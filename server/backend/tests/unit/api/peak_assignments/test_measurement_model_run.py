"""A run scores and caps at one measurement model.

Stage A fits the run's model at its first named point (v1) and every row is
scored there. The refit stage fits it again on the run's corroborated commits
(v2); where that moves the centre or the width beyond the bound, every row is
scored again at v2 off the frames it was scored on (``judging_loop.fits_at``,
``judging_loop.rescore_rows``) before any cap, and the mass gate reads a row's
distance at the same model. So a row's fit score and its ``mass_z`` are read
at one centre and one width, whichever version the run ends at.
"""

import math

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import judging_loop, stage_a_pass
from mascope_backend.api.new.peak_assignments.engine import (
    score_frame_at,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.envelope_claims import (
    ENVELOPE_CLAIM,
    ClaimedLine,
    EnvelopeClaim,
)
from mascope_backend.api.new.peak_assignments.mass_gate import SpectrumLines
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
)
from mascope_tools.composition.calibration import (
    Calibration,
    apply_calibration,
    apply_corroboration,
)
from mascope_tools.composition.mass_accuracy import SampleMassAccuracy
from mascope_tools.composition.measurement_model import (
    MODEL_V1,
    MODEL_V2,
    MeasurementModel,
)
from mascope_tools.composition.reagents import FragmentIon, FragmentLadder


BANDS = {TIER_CANDIDATE: 0.45, TIER_ASSIGNED: 0.75}
ORBI_WIDTH = 0.3
#: Every library line sits here, 1.2 ppm from the v1 centre: the commits move
#: the model there, four of v1's widths away.
AXIS_PPM = -1.2

V1 = MeasurementModel.v1(
    SampleMassAccuracy(mu_ppm=0.0, sigma_ppm=0.2, anchors=40, anchor_ions=12),
    ORBI_WIDTH,
)
#: A confidence curve, and the adduct its compounds' second channel is
#: corroborated by.
CALIBRATION = Calibration(a=6.0, b=-3.0, corroboration_weights={"[M+Na]+": 1.5})
CURVE = stage_a_pass.ConfidenceCurve(CALIBRATION, {"im-1": "[M+H]+", "im-2": "[M+Na]+"})


def _p(evidence: float) -> float:
    """The curve's P(correct) at an evidence, as Stage A rounds it."""
    return round(float(apply_calibration(evidence, CALIBRATION)), 4)


def _frame(ions: int = 12, error_ppm: float = AXIS_PPM) -> pd.DataFrame:
    """Stage A's gated, scored frame: ``ions`` library ions, each an M0 and a
    13C line ``error_ppm`` off, as the targeted matcher pairs them."""
    rows = []
    for index in range(ions):
        mz = 150.0 + 10.0 * index
        for label, rel, offset in (("M0", 1.0, 0.0), ("13C", 0.1, 1.00336)):
            rows.append(
                {
                    "target_ion_id": f"ion-{index}",
                    "target_compound_formula": f"C{6 + index}H12O6",
                    "ionization_mechanism_id": "im-1",
                    "relative_abundance": rel,
                    "mz": mz + offset,
                    "match_mz_error": error_ppm,
                    "sample_peak_intensity": 1e6 * rel,
                    "signal_to_noise": 500.0 * rel,
                    "match_score": 1.0,
                    "sample_peak_id": f"p{index}-{label}",
                    "isotope_label": label,
                }
            )
    return pd.DataFrame(rows)


def _rows(frame: pd.DataFrame, model: MeasurementModel) -> list[dict]:
    """The rows Stage A built off the frame, each ion's fit at ``model``."""
    fits = score_frame_at(frame, model)
    rows = []
    for (ion, label), line in frame.groupby(["target_ion_id", "isotope_label"]):
        index = line.index[0]
        fit = round(float(fits.loc[index]), 4)
        row_id = f"{ion}-{label}"
        rows.append(
            {
                "peak_assignment_id": row_id,
                "sample_peak_id": line["sample_peak_id"].iloc[0],
                "sample_peak_mz": float(line["mz"].iloc[0]),
                "sample_peak_intensity": float(line["sample_peak_intensity"].iloc[0]),
                "role": ROLE_M0 if label == "M0" else ROLE_ISO_CHILD,
                "owner_peak_assignment_id": None if label == "M0" else f"{ion}-M0",
                "assigned_formula": line["target_compound_formula"].iloc[0],
                "ion_formula": None,
                "ionization_mechanism_id": "im-1",
                "isotope_label": label,
                "source": SOURCE_DATABASE,
                "target_compound_id": f"tc-{ion}",
                "fit_score": fit,
                "mz_error_ppm": float(line["match_mz_error"].iloc[0]),
                "tier": tier_for_evidence(
                    fit, candidate_threshold=0.45, assigned_threshold=0.75
                ),
                "provenance": {"evidence": fit, "plausibility": 1.0},
            }
        )
    return rows


def _refit(
    rows: list[dict],
    frame: pd.DataFrame,
    curve: stage_a_pass.ConfidenceCurve | None = None,
    *,
    floors: dict[str, str] | None = None,
    fragment_ladders: tuple[FragmentLadder, ...] = (),
) -> judging_loop.Judging:
    judging = judging_loop.Judging(
        rows,
        model=V1,
        fallback_sigma_ppm=ORBI_WIDTH,
        notation_by_id={"im-1": "[M+H]+"},
        mz_tolerance_ppm=3.0,
        abundance_floor=0.001,
        max_alternatives=5,
        lines=SpectrumLines([], [], []),
        tier_bands=BANDS,
        rescore=lambda model: judging_loop.fits_at(model, frame, None),
        curve=curve,
        floors=floors or {},
        fragment_ladders=fragment_ladders,
    )
    judging.refit()
    return judging


def _judged(judging: judging_loop.Judging) -> judging_loop.JudgedCommits:
    """The judging rounds run on from the first refit to their fixed point."""
    judging.judge()
    judging.cap()
    while judging.another_round():
        judging.refit()
        judging.judge()
        judging.cap()
    return judging.result()


class TestOneModelForTheScoreAndTheCap:
    def test_a_rows_fit_score_and_its_mass_z_are_read_at_one_model(self):
        frame = _frame()
        rows = _rows(frame, V1)
        # The premise: at v1 every library line is four widths off, and its
        # fit is poor; the refit's commits put the centre on them.
        assert all(row["fit_score"] < 0.75 for row in rows)

        judging = _refit(rows, frame)
        model = judging.judged_at

        assert model.version == MODEL_V2
        assert model.scoring_mu_ppm == pytest.approx(AXIS_PPM)
        assert judging.mass_calibration["refit"]["adopted"] is True
        fits = score_frame_at(frame, model)
        for row in judging.judged:
            ion = row["peak_assignment_id"].rsplit("-", 1)[0]
            at = fits[frame["target_ion_id"] == ion].iloc[0]
            assert row["fit_score"] == pytest.approx(round(float(at), 4))
            if row["role"] == ROLE_M0:
                z = model.z_of(row["mz_error_ppm"], row["sample_peak_mz"])
                assert row["provenance"]["mass_z"] == pytest.approx(round(z, 2))
                assert row["provenance"]["mass_z"] == pytest.approx(0.0, abs=0.01)
        # Scored again at v2, the lines sit on the centre and read well.
        assert all(row["fit_score"] > 0.75 for row in judging.judged)
        assert judging.mass_calibration["refit"]["rescored"] == len(rows)
        # The rows the stages built are not touched; the round's copies are.
        assert all(row["fit_score"] < 0.75 for row in rows)

    def test_with_fragment_ladders_the_rounds_own_ledger_is_scored_at_v2(self):
        # A profile with fragment ladders judges a copy of the round's rows,
        # so that a fragment claim can measure them again from the rows as the
        # round began (``Judging.judge``). The re-score sets that copy.
        frame = _frame()
        rows = _rows(frame, V1)
        ladder = FragmentLadder(
            parent="C20H40O2",
            label="a parent the sample does not commit",
            fragments=(FragmentIon("C2H5O", 1, "[C2H5O]+", literature_ratio=1.0),),
            references=("nist",),
        )

        judging = _refit(rows, frame, fragment_ladders=(ladder,))

        assert judging.judged is not judging.applied
        assert judging.judged_at.version == MODEL_V2
        assert all(row["fit_score"] > 0.75 for row in judging.judged)

    def test_a_run_that_takes_v2_is_judged_capped_and_reported_at_v2(self):
        frame = _frame()
        rows = _rows(frame, V1)

        judging = _refit(rows, frame)
        judging.judge()
        judging.cap()
        assert not judging.another_round()
        result = judging.result()

        # What the run records and every later reader reads is the model the
        # rows were scored and capped at, not the v1 the round began from.
        assert result.model is judging.judged_at
        assert result.model.version == MODEL_V2
        assert result.model.scoring_mu_ppm == pytest.approx(AXIS_PPM)
        assert result.mass_calibration["applied"] is True

    def test_a_batch_decision_s_reading_falls_no_lower_than_it_at_v2(self):
        # A batch decision proposes a listed compound's reading at candidate,
        # above the band its evidence earned, and only a cap may take it lower
        # (MAS-983). A pass with no recorded model refits v2 on its commits,
        # which lowers this reading's evidence further: 0.3 ppm off v1's
        # centre, it is 1.5 ppm off v2's.
        frame = _frame()
        frame.loc[frame["target_ion_id"] == "ion-0", "match_mz_error"] = 0.3
        rows = _rows(frame, V1)
        decided = next(row for row in rows if row["peak_assignment_id"] == "ion-0-M0")
        evidence = round(decided["fit_score"] * 0.4, 4)
        decided["provenance"].update(plausibility=0.4, evidence=evidence)
        decided["tier"] = TIER_BELOW_ASSIGNABILITY
        floors = {decided["sample_peak_id"]: TIER_CANDIDATE}
        # Proposed as the elect stage proposes it.
        assert judging_loop.raise_to_floors(rows, floors) == 1

        def judged(**kwargs) -> dict:
            result = _judged(_refit(rows, frame, **kwargs))
            assert result.model.version == MODEL_V2
            return next(
                row for row in result.rows if row["peak_assignment_id"] == "ion-0-M0"
            )

        held = judged(floors=floors)
        assert held["provenance"]["evidence"] < BANDS[TIER_CANDIDATE]
        assert held["provenance"]["mass_z"] < 3
        assert held["tier"] == TIER_CANDIDATE
        # The premise: v2's evidence alone puts the reading below.
        assert judged()["tier"] == TIER_BELOW_ASSIGNABILITY

    def test_a_re_scored_row_reads_its_p_correct_again_at_v2(self):
        frame = _frame()
        rows = _rows(frame, V1)
        for row in rows:
            row["provenance"]["p_correct"] = _p(row["provenance"]["evidence"])

        judging = _refit(rows, frame, CURVE)

        for row in judging.judged:
            evidence = row["provenance"]["evidence"]
            assert evidence > 0.75
            assert row["provenance"]["p_correct"] == _p(evidence)
        # Without the curve the run reads none again: the number stays the v1
        # one beside a v2 evidence.
        stale = _refit(rows, frame)
        assert any(
            row["provenance"]["p_correct"] != _p(row["provenance"]["evidence"])
            for row in stale.judged
        )

    def test_a_run_whose_refit_stays_inside_the_bound_keeps_v1_for_both(self):
        frame = _frame(error_ppm=0.02)
        rows = _rows(frame, V1)
        before = {row["peak_assignment_id"]: row["fit_score"] for row in rows}

        judging = _refit(rows, frame)

        assert judging.judged_at == V1
        assert judging.judged_at.version == MODEL_V1
        assert judging.mass_calibration["refit"]["adopted"] is False
        for row in judging.judged:
            assert row["fit_score"] == before[row["peak_assignment_id"]]
            if row["role"] == ROLE_M0:
                assert row["provenance"]["mass_z"] == pytest.approx(
                    round(V1.z_of(0.02, row["sample_peak_mz"]), 2)
                )


def _row(
    row_id: str,
    *,
    fit: float,
    tier: str,
    evidence: float | None = None,
    source: str = SOURCE_UNTARGETED,
    role: str = ROLE_M0,
    formula: str = "C6H12O6",
) -> dict:
    return {
        "peak_assignment_id": row_id,
        "role": role,
        "assigned_formula": formula,
        "ionization_mechanism_id": "im-1",
        "source": source,
        "fit_score": fit,
        "tier": tier,
        "provenance": {"evidence": fit if evidence is None else evidence},
    }


class TestRescoreRows:
    def test_a_row_takes_its_new_fit_and_the_band_its_evidence_earns(self):
        rows = [
            _row("up", fit=0.5, tier=TIER_CANDIDATE),
            _row("child", fit=0.5, tier=TIER_CANDIDATE, role=ROLE_ISO_CHILD),
        ]
        fits = {SOURCE_UNTARGETED: {("C6H12O6", "im-1"): 0.9}}

        assert judging_loop.rescore_rows(rows, fits, BANDS) == 2

        for row in rows:
            assert row["fit_score"] == 0.9
            assert row["provenance"]["evidence"] == 0.9
            assert row["tier"] == TIER_ASSIGNED

    def test_its_evidence_moves_by_the_fit_s_ratio_so_a_weight_stands(self):
        # A list's prior or a plausibility under one: the weight on the fit
        # carries over to the new fit.
        row = _row("weighted", fit=0.8, evidence=0.4, tier=TIER_BELOW_ASSIGNABILITY)
        fits = {SOURCE_UNTARGETED: {("C6H12O6", "im-1"): 0.4}}

        judging_loop.rescore_rows([row], fits, BANDS)

        assert row["provenance"]["evidence"] == pytest.approx(0.2)
        assert row["tier"] == TIER_BELOW_ASSIGNABILITY

    def test_a_row_an_earlier_stage_held_is_not_lifted_but_may_fall(self):
        # Evidence 0.9 earns assigned, and a stage held the row at candidate.
        held = _row("held", fit=0.9, tier=TIER_CANDIDATE)
        falls = _row("falls", fit=0.9, tier=TIER_CANDIDATE, formula="C7H14O6")
        fits = {
            SOURCE_UNTARGETED: {
                ("C6H12O6", "im-1"): 0.95,
                ("C7H14O6", "im-1"): 0.2,
            }
        }

        judging_loop.rescore_rows([held, falls], fits, BANDS)

        assert held["tier"] == TIER_CANDIDATE
        assert falls["tier"] == TIER_BELOW_ASSIGNABILITY

    def test_a_reading_a_batch_decision_proposed_falls_no_lower_than_it(self):
        # Proposed at candidate by a batch decision over the band evidence 0.4
        # earns. A new fit may lift it, but its evidence alone does not take it
        # below the decision; a reading with no decision falls.
        decided = _row("decided", fit=0.4, tier=TIER_CANDIDATE)
        lifted = _row("lifted", fit=0.4, tier=TIER_CANDIDATE, formula="C8H16O6")
        free = _row("free", fit=0.4, tier=TIER_CANDIDATE, formula="C7H14O6")
        for row in (decided, lifted, free):
            row["sample_peak_id"] = f"p-{row['peak_assignment_id']}"
        fits = {
            SOURCE_UNTARGETED: {
                ("C6H12O6", "im-1"): 0.1,
                ("C8H16O6", "im-1"): 0.9,
                ("C7H14O6", "im-1"): 0.1,
            }
        }
        floors = {"p-decided": TIER_CANDIDATE, "p-lifted": TIER_CANDIDATE}

        judging_loop.rescore_rows([decided, lifted, free], fits, BANDS, floors=floors)

        assert decided["provenance"]["evidence"] == 0.1
        assert decided["tier"] == TIER_CANDIDATE
        assert lifted["tier"] == TIER_ASSIGNED
        assert free["tier"] == TIER_BELOW_ASSIGNABILITY

    def test_a_row_reads_its_own_stage_s_frame_first(self):
        listed = _row("listed", fit=0.5, tier=TIER_CANDIDATE, source=SOURCE_DATABASE)
        searched = _row("searched", fit=0.5, tier=TIER_CANDIDATE)
        fits = {
            SOURCE_DATABASE: {("C6H12O6", "im-1"): 0.8},
            SOURCE_UNTARGETED: {("C6H12O6", "im-1"): 0.6},
        }

        judging_loop.rescore_rows([listed, searched], fits, BANDS)

        assert (listed["fit_score"], searched["fit_score"]) == (0.8, 0.6)

    def test_a_given_reading_read_as_a_list_s_is_scored_in_the_seeded_frame(self):
        # A propagated reading a list holds is read as the list's row, and was
        # scored in the seeded frame, the only one its path has.
        given = _row(
            "given",
            fit=0.5,
            tier=TIER_CANDIDATE,
            source=SOURCE_DATABASE,
            formula="C7H14O6",
        )
        fits = {SOURCE_DATABASE: {}, SOURCE_UNTARGETED: {("C7H14O6", "im-1"): 0.7}}

        judging_loop.rescore_rows([given], fits, BANDS)

        assert given["fit_score"] == 0.7

    def test_a_row_no_frame_scored_keeps_its_fit(self):
        row = _row("finder", fit=0.66, tier=TIER_CANDIDATE)
        reagent = _row("reagent", fit=0.5, tier=TIER_CANDIDATE)
        reagent["role"] = "reagent"

        rescored = judging_loop.rescore_rows(
            [row, reagent], {SOURCE_UNTARGETED: {("C6H12O6", "x"): 0.9}}, BANDS
        )

        assert rescored == 0
        assert (row["fit_score"], row["tier"]) == (0.66, TIER_CANDIDATE)
        assert reagent["fit_score"] == 0.5


def _listed(
    row_id: str,
    *,
    fit: float,
    compound: str = "tc-1",
    mechanism: str = "im-1",
    formula: str = "C6H12O6",
    alternatives: list[dict] | None = None,
) -> dict:
    """A target library's monoisotopic row, its P(correct) read off the curve
    at its evidence as Stage A reads it."""
    tier = tier_for_evidence(fit, candidate_threshold=0.45, assigned_threshold=0.75)
    return {
        "peak_assignment_id": row_id,
        "role": ROLE_M0,
        "assigned_formula": formula,
        "ionization_mechanism_id": mechanism,
        "source": SOURCE_DATABASE,
        "target_compound_id": compound,
        "fit_score": fit,
        "tier": tier,
        "alternatives": alternatives,
        "provenance": {"evidence": fit, "plausibility": 1.0, "p_correct": _p(fit)},
    }


def _alternative(formula: str, fit: float, **extra) -> dict:
    return {
        "assigned_formula": formula,
        "ionization_mechanism_id": "im-1",
        "fit_score": fit,
        "source": SOURCE_DATABASE,
        **extra,
    }


class TestTheRescoreMovesWhatTheRowRecordsOfItsFit:
    """A re-scored row's P(correct) and its stored alternatives move with its
    fit, so nothing a reader serves of the row is read at v1."""

    def test_the_reviewed_case_moves_p_correct_and_the_alternative(self):
        # A row at fit 0.9, P(correct) on its curve, one alternative at 0.6;
        # at v2 the row fits 0.5 and the alternative 0.85.
        row = _listed("r", fit=0.9, alternatives=[_alternative("C5H10O5", 0.6)])
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-1"): 0.5, ("C5H10O5", "im-1"): 0.85}}

        judging_loop.rescore_rows([row], fits, BANDS, CURVE)

        assert (row["fit_score"], row["tier"]) == (0.5, TIER_CANDIDATE)
        assert row["provenance"]["evidence"] == 0.5
        assert row["provenance"]["p_correct"] == _p(0.5)
        assert row["alternatives"][0]["fit_score"] == 0.85

    def test_p_correct_is_read_at_the_moved_evidence_not_the_fit(self):
        # A formula of plausibility 0.7: its evidence is the fit times that,
        # and Stage A read P(correct) at the evidence.
        row = _listed("r", fit=0.9)
        row["provenance"].update(evidence=0.63, plausibility=0.7, p_correct=_p(0.63))
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-1"): 0.5}}

        judging_loop.rescore_rows([row], fits, BANDS, CURVE)

        evidence = row["provenance"]["evidence"]
        assert evidence == pytest.approx(0.35)
        assert evidence != row["fit_score"]
        assert row["provenance"]["p_correct"] == _p(evidence)
        assert row["provenance"]["p_correct"] != _p(row["fit_score"])

    def test_an_alternative_moves_though_its_winner_did_not(self):
        row = _listed("r", fit=0.9, alternatives=[_alternative("C5H10O5", 0.6)])
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-1"): 0.9, ("C5H10O5", "im-1"): 0.3}}

        assert judging_loop.rescore_rows([row], fits, BANDS, CURVE) == 0

        assert row["alternatives"][0]["fit_score"] == 0.3
        assert row["provenance"]["p_correct"] == _p(0.9)

    def test_a_reading_of_the_rows_own_ion_follows_the_row(self):
        # Written at the row's fit (``engine.record_stage_a_same_ion_readings``),
        # and no frame scored it: the same ion is the same lines.
        same_ion = _alternative("C4H8O3", 0.9, same_ion=True)
        # A reading of the ion a frame scored on its own, at another fit.
        restated = _alternative("C3H6O2", 0.7, same_ion=True)
        searched = _alternative("C9H8", 0.4, source=SOURCE_UNTARGETED)
        row = _listed("r", fit=0.9, alternatives=[same_ion, restated, searched])
        fits = {
            SOURCE_DATABASE: {
                ("C6H12O6", "im-1"): 0.6,
                # The search's reading, named in the library's frame too.
                ("C9H8", "im-1"): 0.95,
            }
        }

        judging_loop.rescore_rows([row], fits, BANDS, CURVE)

        assert same_ion["fit_score"] == 0.6
        assert restated["fit_score"] == 0.7
        # The search's reading is on the finder's scale, not the frame's.
        assert searched["fit_score"] == 0.4

    def test_a_search_rows_reading_of_its_own_ion_keeps_the_finders_fit(self):
        same_ion = _alternative("C4H8O3", 0.9, same_ion=True)
        row = _row("s", fit=0.9, tier=TIER_ASSIGNED)
        row["alternatives"] = [same_ion]
        fits = {SOURCE_UNTARGETED: {("C6H12O6", "im-1"): 0.6}}

        judging_loop.rescore_rows([row], fits, BANDS, CURVE)

        assert row["fit_score"] == 0.6
        assert same_ion["fit_score"] == 0.9

    def test_a_sibling_that_falls_no_longer_corroborates(self):
        # One compound through two channels, each confident at v1, so each
        # carries the other's boost; at v2 the second falls below candidate.
        first = _listed("h", fit=0.8)
        second = _listed("na", fit=0.8, mechanism="im-2")
        other = _listed("o", fit=0.8, compound="tc-2", formula="C7H14O6")
        boosted = apply_corroboration(_p(0.8), ["[M+Na]+"], {"[M+Na]+": 1.5})
        first["provenance"]["p_correct"] = round(boosted, 4)
        first["provenance"]["corroboration"] = {"adducts": ["[M+H]+", "[M+Na]+"]}
        other["provenance"]["p_correct"] = 0.4242
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-2"): 0.2}}

        judging_loop.rescore_rows([first, second, other], fits, BANDS, CURVE)

        assert second["provenance"]["p_correct"] == _p(0.2)
        # The first row's own fit did not move; its sibling's boost is gone.
        assert first["provenance"]["p_correct"] == _p(0.8)
        assert "corroboration" not in first["provenance"]
        # A compound no re-scored row names keeps the number Stage A wrote.
        assert other["provenance"]["p_correct"] == 0.4242

    def test_a_sibling_that_rises_corroborates(self):
        first = _listed("h", fit=0.8)
        second = _listed("na", fit=0.2, mechanism="im-2")
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-2"): 0.8}}

        judging_loop.rescore_rows([first, second], fits, BANDS, CURVE)

        lifted = round(apply_corroboration(_p(0.8), ["[M+Na]+"], {"[M+Na]+": 1.5}), 4)
        assert first["provenance"]["p_correct"] == lifted
        assert first["provenance"]["corroboration"]["adducts"] == [
            "[M+H]+",
            "[M+Na]+",
        ]
        assert second["provenance"]["p_correct"] == _p(0.8)

    def test_a_sibling_an_earlier_stage_held_still_corroborates(self):
        # Stage A counts a sibling at the band its evidence earned, before any
        # stage held it; read again, it counts the same way.
        first = _listed("h", fit=0.8)
        second = _listed("na", fit=0.8, mechanism="im-2")
        second["tier"] = TIER_BELOW_ASSIGNABILITY
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-1"): 0.85}}

        judging_loop.rescore_rows([first, second], fits, BANDS, CURVE)

        lifted = round(apply_corroboration(_p(0.85), ["[M+Na]+"], {"[M+Na]+": 1.5}), 4)
        assert first["provenance"]["p_correct"] == lifted

    def test_an_isotopologue_reads_its_p_correct_again(self):
        child = _listed("c", fit=0.9)
        child["role"] = ROLE_ISO_CHILD
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-1"): 0.5}}

        judging_loop.rescore_rows([child], fits, BANDS, CURVE)

        assert child["provenance"]["p_correct"] == _p(0.5)

    def test_without_a_curve_no_number_is_read(self):
        # An instrument with no curve has no P(correct) to read again.
        row = _listed("r", fit=0.9)
        row["provenance"]["p_correct"] = None
        fits = {SOURCE_DATABASE: {("C6H12O6", "im-1"): 0.5}}

        judging_loop.rescore_rows([row], fits, BANDS, None)

        assert row["provenance"]["p_correct"] is None
        assert row["fit_score"] == 0.5

    def test_the_curve_reads_the_notations_of_stage_a_s_frame(self):
        frame = pd.DataFrame(
            {
                "ionization_mechanism_id": ["im-1", "im-2", "im-1"],
                "ionization_mechanism": ["[M+H]+", "[M+Na]+", "[M+H]+"],
            }
        )

        curve = stage_a_pass.ConfidenceCurve.of(CALIBRATION, frame)

        assert curve.calibration is CALIBRATION
        assert curve.notation_by_mechanism == {"im-1": "[M+H]+", "im-2": "[M+Na]+"}
        assert stage_a_pass.ConfidenceCurve.of(None, frame) is None


class TestTheSecondChannelCapAtTheNewModel:
    """The second-channel cap judges a reading through a secondary channel
    after the re-score, on the ledger the reading changes settled
    (``engine.cap_second_channel``): it caps an uncorroborated reading where
    it is assigned there, so it reads the tier the reading's v2 evidence
    earns, not the one v1 earned."""

    def test_a_run_holds_a_secondary_channel_reading_its_v2_score_lifts(self):
        # The reviewed case: one of the library's ions read by the search
        # through a secondary channel, with no list and no second channel.
        # At v1 it is below assignability; at v2 its fit is the best there is.
        frame = _frame()
        frame.loc[frame["target_ion_id"] == "ion-0", "ionization_mechanism_id"] = "im-9"
        rows = _rows(frame, V1)
        minor = {"[M+Cl]-": "im-9"}
        for row in rows:
            if row["peak_assignment_id"].startswith("ion-0-"):
                row.update(
                    ionization_mechanism_id="im-9",
                    source=SOURCE_UNTARGETED,
                    target_compound_id=None,
                )
        reading = next(row for row in rows if row["peak_assignment_id"] == "ion-0-M0")
        assert reading["tier"] == TIER_BELOW_ASSIGNABILITY

        judging = judging_loop.Judging(
            rows,
            model=V1,
            fallback_sigma_ppm=ORBI_WIDTH,
            notation_by_id={"im-1": "[M+H]+", "im-9": "[M+Cl]-"},
            mz_tolerance_ppm=3.0,
            abundance_floor=0.001,
            max_alternatives=5,
            lines=SpectrumLines([], [], []),
            tier_bands=BANDS,
            minor_channels=frozenset(minor),
            rescore=lambda model: judging_loop.fits_at(model, frame, None),
        )
        judging.refit()
        judging.judge()
        judging.cap()
        while judging.another_round():
            judging.refit()
            judging.judge()
            judging.cap()
        result = judging.result()

        assert result.model.version == MODEL_V2
        held = next(
            row for row in result.rows if row["peak_assignment_id"] == "ion-0-M0"
        )
        assert held["provenance"]["evidence"] >= BANDS[TIER_ASSIGNED]
        assert held["tier"] == TIER_CANDIDATE
        assert held["provenance"]["minor_channel"]["capped"] is True
        assert "minor_channel" in {
            reason["rule"] for reason in held["provenance"]["tier_reasons"]
        }
        # The mode's own readings take the band v2 earns them.
        assert all(
            row["tier"] == TIER_ASSIGNED
            for row in result.rows
            if row["role"] == ROLE_M0 and row["ionization_mechanism_id"] == "im-1"
        )


def _claimed(row: dict) -> dict:
    """The row, read by an envelope claim as its owner's line: it takes its
    owner's fit and evidence, and the claim reads it no higher than
    candidate (``envelope_claims.apply_claims``)."""
    row["role"] = ROLE_ISO_CHILD
    row["provenance"][ENVELOPE_CLAIM] = {"line": "13C2", "tracking": "tracks"}
    return row


class TestAClaimedLineAtTheNewModel:
    """An envelope claim reads a line no higher than candidate whatever its
    owner's evidence earns, and it is applied each round before the re-score.
    The line takes its owner's evidence, so the band that evidence earned
    before says nothing of the claim's ceiling: a re-score at v2 keeps it."""

    def test_a_claimed_line_its_owner_s_new_fit_lifts_stays_at_candidate(self):
        # The owner's fit earned candidate, as the claimed line stands; the
        # new fit earns assigned. An unclaimed line of the same fit takes it.
        claimed = _claimed(_row("claimed", fit=0.5, tier=TIER_CANDIDATE))
        free = _row("free", fit=0.5, tier=TIER_CANDIDATE, role=ROLE_ISO_CHILD)
        fits = {SOURCE_UNTARGETED: {("C6H12O6", "im-1"): 0.9}}

        judging_loop.rescore_rows([claimed, free], fits, BANDS)

        assert claimed["provenance"]["evidence"] == 0.9
        assert claimed["tier"] == TIER_CANDIDATE
        assert free["tier"] == TIER_ASSIGNED

    def test_a_claimed_line_falls_with_its_owner_s_new_fit(self):
        row = _claimed(_row("claimed", fit=0.5, tier=TIER_CANDIDATE))
        fits = {SOURCE_UNTARGETED: {("C6H12O6", "im-1"): 0.2}}

        judging_loop.rescore_rows([row], fits, BANDS)

        assert row["tier"] == TIER_BELOW_ASSIGNABILITY

    def test_a_run_keeps_a_claimed_line_at_candidate_where_v2_lifts_its_owner(self):
        # The reviewed case: the owner and the line earn candidate at v1, and
        # v2 lifts the owner to assigned. The claim the run found then is
        # applied at the start of the next round, to the rows at v1, and that
        # round refits v2 and scores them again.
        frame = _frame()
        rows = _rows(frame, V1)
        by_id = {row["peak_assignment_id"]: row for row in rows}
        for row in (by_id["ion-0-M0"], by_id["ion-1-M0"]):
            row.update(fit_score=0.5, tier=TIER_CANDIDATE)
            row["provenance"]["evidence"] = 0.5
        judging = judging_loop.Judging(
            rows,
            model=V1,
            fallback_sigma_ppm=ORBI_WIDTH,
            notation_by_id={"im-1": "[M+H]+"},
            mz_tolerance_ppm=3.0,
            abundance_floor=0.001,
            max_alternatives=5,
            lines=SpectrumLines([], [], []),
            tier_bands=BANDS,
            rescore=lambda model: judging_loop.fits_at(model, frame, None),
        )
        judging.claims = {
            "ion-1-M0": EnvelopeClaim(
                owner_id="ion-0-M0",
                line=ClaimedLine(
                    row_id="ion-1-M0",
                    label="13C2",
                    line_mz=160.0 / (1 + AXIS_PPM * 1e-6),
                    share=0.02,
                    tracking="tracks",
                    held_tier=TIER_CANDIDATE,
                ),
            )
        }

        judging.refit()
        judging.judge()
        judging.cap()

        assert judging.judged_at.version == MODEL_V2
        judged = {row["peak_assignment_id"]: row for row in judging.judged}
        owner, line = judged["ion-0-M0"], judged["ion-1-M0"]
        assert owner["tier"] == TIER_ASSIGNED
        assert line["role"] == ROLE_ISO_CHILD
        assert line["provenance"]["evidence"] == owner["provenance"]["evidence"]
        assert line["provenance"]["evidence"] >= BANDS[TIER_ASSIGNED]
        assert line["tier"] == TIER_CANDIDATE
        assert "envelope_claim" in {
            reason["rule"] for reason in line["provenance"]["tier_reasons"]
        }


def _ctx(path, *, recorded=False):
    """A pass at its judging, its stages' fields set."""
    from types import SimpleNamespace

    from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig

    return SimpleNamespace(
        path=path,
        model=V1,
        model_recorded=recorded,
        stage_a_scored=_frame(2),
        stage_a_absent=pd.DataFrame(),
        seeded_frame=judging_loop.SeededFrame(_frame(1), {}),
        shortlist=None,
        confidence_curve=CURVE,
        decoy_plan=None,
        stage_a=[],
        stage_b=[],
        resolved_profile=SimpleNamespace(
            fallback_sigma_ppm=ORBI_WIDTH,
            minor_channels=frozenset(),
            partner_gated_channels=frozenset(),
            added_channels=frozenset(),
            given_channels=(),
            context=None,
            nox_skeleton=False,
            profile=SimpleNamespace(name="no ladders"),
        ),
        searched_mechanisms=[],
        judged_mechanisms=[],
        scoring=SimpleNamespace(mz_tolerance_ppm=3.0, abundance_floor=0.001),
        config=PeakAssignmentConfig(),
        lines=None,
        reagent_rows=[],
        resolution=None,
        artifact_peak_ids=set(),
        instrument_type="orbi",
        listed_formulas=frozenset(),
        source_line_cap=None,
        floors={},
    )


class TestWhichPathsRefit:
    """A path fits its own model where it measured one; one that reads its
    control's or its sample's recorded model judges there unchanged."""

    @pytest.mark.parametrize(
        "path_name, recorded, refits",
        [
            ("RUN", False, True),
            ("INGEST_FOLD", False, True),
            ("DECOY", False, False),
            ("BATCH_SEARCH", True, False),
            ("PROPAGATION", True, False),
            # No run recorded a model: the path's own commits fit one.
            ("BATCH_SEARCH", False, True),
        ],
    )
    def test_the_refit_and_the_rescore(self, monkeypatch, path_name, recorded, refits):
        from mascope_backend.api.new.peak_assignments import pipeline

        ctx = _ctx(getattr(pipeline, path_name), recorded=recorded)
        handed = []
        monkeypatch.setattr(
            judging_loop, "fits_at", lambda *args: handed.append(args) or {"fits": 1}
        )

        judging = pipeline._judging_for(ctx)

        assert judging.model is V1
        assert judging.refits is refits
        # The curve Stage A read P(correct) off, which a re-score reads again.
        assert judging.curve is CURVE
        if refits:
            assert judging.rescore(V1) == {"fits": 1}
            ((model, stage_a, seeded, absent),) = handed
            assert model is V1
            assert stage_a is ctx.stage_a_scored
            assert seeded is ctx.seeded_frame
            assert absent is ctx.stage_a_absent
        else:
            assert judging.rescore is None
        # Every path reads its rows' rival sets out of the frames it scored
        # them on, read at the model the round caps at.
        assert judging.rival_frames is not None
        assert judging.rival_frames.shortlists == {}

    def test_a_batch_decision_s_floors_reach_the_re_score(self):
        # The batch stages' judging of a sample no run recorded a model for:
        # it refits, and its re-score keeps each decision's floor.
        from mascope_backend.api.new.peak_assignments import pipeline

        ctx = _ctx(pipeline.PROPAGATION)
        ctx.floors = {"p-decided": TIER_CANDIDATE}

        judging = pipeline._judging_for(ctx)

        assert judging.refits is True
        assert judging.floors == {"p-decided": TIER_CANDIDATE}


@pytest.mark.asyncio
async def test_a_given_readings_frame_is_kept_to_score_again(monkeypatch):
    # The propagation's given readings are scored in one seeded pass; the
    # frame is kept, as the search's re-score's is, so a refit can score the
    # rows again off it.
    from types import SimpleNamespace

    from mascope_backend.api.new.peak_assignments import pipeline

    scored = _frame(1)
    seen = {}

    async def score_seeds(sample, seeds, params, **kwargs):
        seen.update(kwargs)
        return {("C6H12O6", "im-1"): "ion-0"}, {"ion-0": 0.9}, {}, scored

    monkeypatch.setattr(pipeline, "score_seeds", score_seeds)
    ctx = SimpleNamespace(
        path=pipeline.PROPAGATION,
        seeds={("C6H12O6", "im-1")},
        sample=None,
        match_params=None,
        model=V1,
        resolution=None,
        seeded_frame=None,
        listed_ions={},
        claimed_peak_ids=set(),
        kept=None,
    )

    await pipeline.score(ctx)

    assert seen["model"] is V1
    assert ctx.seeded_frame.scored is scored
    assert ctx.seeded_frame.ion_by_seed == {("C6H12O6", "im-1"): "ion-0"}


def test_the_record_keeps_the_model_the_search_ranked_at_where_the_run_moved():
    from mascope_backend.api.new.peak_assignments.engine import measurement_model_record

    v2 = V1.v2(mu_ppm=-1.2, sigma_ppm=0.1, commits=40, source_lines=3)

    moved = measurement_model_record(v2, ranked_at=V1, scoring={}, gate={})
    stayed = measurement_model_record(V1, ranked_at=V1, scoring={}, gate={})

    assert moved["version"] == MODEL_V2
    assert moved["ranked_at"] == V1.record()
    assert "ranked_at" not in stayed
    assert MeasurementModel.from_record(moved).scoring_mu_ppm == pytest.approx(-1.2)


def test_a_frame_is_scored_at_the_centre_its_model_reads_at_each_line():
    # The lines of ions at low m/z sit lower, as a 1/mz trend has them: at the
    # trend every ion reads as centred, each line from the centre at its own
    # m/z; at the trend's constant part alone, the low ions read off it.
    from mascope_tools.composition.mass_accuracy import MassTrend

    trend = MassTrend(
        intercept_ppm=0.0,
        offset_mda=-0.3,
        sigma_ppm=0.1,
        points=40,
        mz_lo=100.0,
        mz_hi=400.0,
    )
    model = V1.v2(mu_ppm=0.0, sigma_ppm=0.1, commits=40, source_lines=0, trend=trend)
    frame = _frame(12)
    frame["match_mz_error"] = model.centres_ppm(frame["mz"])
    flat = V1.v2(mu_ppm=0.0, sigma_ppm=0.1, commits=40, source_lines=0)
    # The premise: the centre moves by more than a width over the frame.
    centres = model.centres_ppm(frame["mz"])
    assert centres.max() - centres.min() > model.width_ppm

    at_trend = score_frame_at(frame, model)
    at_constant = score_frame_at(frame, flat)

    assert at_trend.min() > 0.95
    low = frame["target_ion_id"] == "ion-0"
    assert at_constant[low].iloc[0] < at_trend[low].iloc[0] - 0.1


def _trend_model() -> MeasurementModel:
    """A run whose centre follows a 1/mz trend fitted over m/z 100-400."""
    from mascope_tools.composition.mass_accuracy import MassTrend

    trend = MassTrend(
        intercept_ppm=0.0,
        offset_mda=-0.3,
        sigma_ppm=0.1,
        points=40,
        mz_lo=100.0,
        mz_hi=400.0,
    )
    return V1.v2(mu_ppm=0.0, sigma_ppm=0.1, commits=40, source_lines=0, trend=trend)


def _lone_line(ion: str, mz: float, error_ppm: float, rel: float = 1.0) -> dict:
    return {
        "target_ion_id": ion,
        "relative_abundance": rel,
        "mz": mz,
        "match_mz_error": error_ppm,
        "sample_peak_intensity": 1e6 * rel,
    }


@pytest.mark.parametrize("widths", [0.5, 1.0, 1.5])
def test_a_line_below_the_trends_coverage_scores_at_the_width_it_is_capped_at(
    widths,
):
    # Below the lowest commit the trend's absolute floor passes the model's
    # width, and the mass gate reads the row's distance in the floored width
    # (``z_of``). A lone line's fit is its mass term, exp(-z^2 / 2): it is
    # scored at that same distance, not at the model's one width.
    model = _trend_model()
    mz = 30.0
    width = float(model.widths_ppm([mz])[0])
    assert width > model.width_ppm  # the premise: the floor passes the width
    error = model.centre_ppm(mz) + widths * width
    frame = pd.DataFrame([_lone_line("ion-0", mz, error)])

    fit = score_frame_at(frame, model).iloc[0]

    assert model.z_of(error, mz) == pytest.approx(widths)
    assert fit == pytest.approx(math.exp(-0.5 * widths**2))


def test_each_line_is_scored_at_the_width_at_its_own_mz():
    # Two ions, one below the trend's coverage and one inside it, their lines
    # listed out of order: each scores in the frame as it does alone.
    model = _trend_model()
    lines = [
        _lone_line("high", 301.0, 0.4, rel=0.3),
        _lone_line("low", 31.0, -4.0, rel=0.3),
        _lone_line("high", 300.0, 0.2),
        _lone_line("low", 30.0, -3.5),
    ]
    frame = pd.DataFrame(lines)

    together = score_frame_at(frame, model)

    for ion in ("high", "low"):
        alone = score_frame_at(frame[frame["target_ion_id"] == ion], model)
        assert together[alone.index].to_list() == pytest.approx(alone.to_list())
