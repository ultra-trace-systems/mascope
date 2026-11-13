"""
Unit tests for the on-demand scoring of a row's formula-only alternatives.

Stage B stores two populations in ``alternatives``: the contenders it competed
for the peak, which carry a fit and the adduct they were found under, and the
composition finder's ``other_candidates`` shortlist, which carries a formula and
a chemical plausibility and nothing else. The run does not measure the second
population - one isotope-envelope match per candidate per peak is a whole-sample
cost - so those entries reach the inspector with no fit to compare and no adduct
to commit them under.

``score_row_alternatives`` measures them for one peak when somebody is looking at
it. The heavy edges are faked (the database session, the sample fetch, the
mechanism list and the seeded scoring chain itself), but the selection, the
M0-only pairing rule, the evidence ranking and the blocked reasons are the
production ones.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.peak_assignments.alternatives_scoring import (
    MAX_SCORED_FORMULAS,
    _blocked_reason,
    _m0_by_ion,
    _paired_to,
    ion_identity,
    measure_winner_confidence,
    recorded_rivals,
    score_row_alternatives,
    stage_b_winners,
    unscorable_alternatives,
    winner_arbitration,
)
from mascope_backend.api.new.peak_assignments.config import (
    MAX_UNTARGETED_PEAKS_CEILING,
    PeakAssignmentConfig,
)
from mascope_tools.composition.arbitration import arbitrate_candidates


_MOD = "mascope_backend.api.new.peak_assignments.alternatives_scoring"

#: The peak the row under test sits on.
PEAK_ID = "sp-1"


def _mechanism(mechanism_id, notation):
    return SimpleNamespace(
        ionization_mechanism_id=mechanism_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity="+",
    )


PROTON = _mechanism("im-h", "[M+H]+")
AMMONIUM = _mechanism("im-nh4", "[M+NH4]+")
SODIUM = _mechanism("im-na", "[M+Na]+")


def _scored_row(ion_id, mz, peak_id, mz_error=0.5, abundance_error=0.02):
    """One row of a gated, fit-scored seeded frame."""
    return {
        "target_ion_id": ion_id,
        "target_ion_formula": f"{ion_id}-ion",
        "mz": mz,
        "relative_abundance": 1.0,
        "sample_peak_id": peak_id,
        "match_mz_error": mz_error,
        "match_abundance_error": abundance_error,
        "match_score": 0.9,
    }


def _assignment(alternatives):
    return SimpleNamespace(
        peak_assignment_id="pa-1",
        sample_item_id="si-1",
        sample_peak_id=PEAK_ID,
        alternatives=alternatives,
    )


def _session_returning(assignment):
    """An ``async_session()`` context whose ``get()`` yields ``assignment``."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=assignment)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


async def _run(
    alternatives,
    *,
    mechanisms=(PROTON, AMMONIUM),
    ion_by_seed=None,
    fit_by_ion=None,
    scored_rows=(),
):
    """Call the controller with every heavy edge faked.

    The seeded chain is replaced by whatever this call declares it found, so a
    test states the measurement outcome directly rather than through a peak
    file.
    """
    scored_df = pd.DataFrame(list(scored_rows))
    score_seeds = AsyncMock(
        return_value=(ion_by_seed or {}, fit_by_ion or {}, {}, scored_df)
    )
    with (
        patch(
            f"{_MOD}.async_session",
            return_value=_session_returning(_assignment(alternatives)),
        ),
        patch(
            f"{_MOD}.fetch_sample",
            AsyncMock(
                return_value=SimpleNamespace(
                    sample_item_id="si-1", filename="s.raw", polarity="+"
                )
            ),
        ),
        patch(
            f"{_MOD}.fetch_sample_mechanisms",
            AsyncMock(
                return_value=(
                    [m.ionization_mechanism_id for m in mechanisms],
                    list(mechanisms),
                )
            ),
        ),
        patch(
            f"{_MOD}.default_match_params",
            AsyncMock(return_value=SimpleNamespace(isotope_abundance_threshold=0.01)),
        ),
        patch(f"{_MOD}.score_seeds", score_seeds),
    ):
        result = await score_row_alternatives(
            sample_item_id="si-1", peak_assignment_id="pa-1"
        )
    return result, score_seeds


class TestSelection:
    """Which entries are worth measuring at all."""

    def test_picks_the_entries_with_a_formula_and_no_adduct(self):
        alternatives = [
            # Competed by the run: it already has both halves.
            {"assigned_formula": "C7H16O5", "ionization_mechanism_id": "im-h"},
            # Written before alternatives carried the mechanism; its target ion
            # still resolves to one, so it needs no measurement either.
            {"assigned_formula": "C9H8", "target_ion_id": "ti-9"},
            {"assigned_formula": "C4H8N2O3", "plausibility": 0.44},
            # No formula at all: there is nothing to measure.
            {"ion_formula": "C3H7+"},
        ]

        assert unscorable_alternatives(alternatives) == [(2, "C4H8N2O3")]

    def test_survives_the_shapes_an_imported_run_can_send(self):
        # `alternatives` is untyped JSON that an external engine wrote, so an
        # entry may be anything at all rather than the dict this expects.
        assert unscorable_alternatives(None) == []
        assert unscorable_alternatives([]) == []
        assert unscorable_alternatives(["C6H12O6", None, 7]) == []
        assert unscorable_alternatives([{"assigned_formula": 42}]) == []
        assert unscorable_alternatives([{"assigned_formula": ""}]) == []

    def test_keeps_the_stored_index_not_the_position_among_the_selected(self):
        alternatives = [
            {"assigned_formula": "C7H16O5", "ionization_mechanism_id": "im-h"},
            {"assigned_formula": "C4H8N2O3"},
            {"assigned_formula": "C9H8", "ionization_mechanism_id": "im-h"},
            {"assigned_formula": "C5H10O2"},
        ]

        assert unscorable_alternatives(alternatives) == [
            (1, "C4H8N2O3"),
            (3, "C5H10O2"),
        ]


class TestM0Pairing:
    """The claim being measured is that the peak IS the ion's M0."""

    def test_takes_the_lightest_isotopologue_of_each_ion(self):
        # Deliberately out of m/z order: reading the frame positionally would
        # take the M+1 of ion-a as its monoisotopic peak.
        frame = pd.DataFrame(
            [
                _scored_row("ion-a", 201.1, "sp-2"),
                _scored_row("ion-a", 200.1, PEAK_ID),
                _scored_row("ion-b", 200.2, PEAK_ID),
            ]
        )

        m0 = _m0_by_ion(frame)

        assert m0["ion-a"]["mz"] == pytest.approx(200.1)
        assert m0["ion-a"]["sample_peak_id"] == PEAK_ID
        assert m0["ion-b"]["mz"] == pytest.approx(200.2)

    def test_an_empty_or_unscored_frame_pairs_nothing(self):
        assert _m0_by_ion(pd.DataFrame()) == {}
        assert _m0_by_ion(pd.DataFrame([{"mz": 1.0}])) == {}

    def test_a_pairing_needs_this_peak_and_not_merely_some_peak(self):
        assert _paired_to(pd.Series({"sample_peak_id": PEAK_ID}), PEAK_ID) is True
        # The peak id is a string on the row and may arrive numeric from the
        # matcher, so the comparison is on the string form of both.
        assert _paired_to(pd.Series({"sample_peak_id": 7}), "7") is True
        assert _paired_to(pd.Series({"sample_peak_id": "sp-9"}), PEAK_ID) is False
        assert _paired_to(pd.Series({"sample_peak_id": float("nan")}), PEAK_ID) is False
        assert _paired_to(None, PEAK_ID) is False


class TestBlockedReason:
    """The three ways a formula can fail to be measured, said apart."""

    def test_names_the_case_it_is_actually_in(self):
        no_adducts = _blocked_reason(0, generated=True)
        no_ion = _blocked_reason(3, generated=False)
        no_match = _blocked_reason(3, generated=True)

        assert "no adducts recorded" in no_adducts
        assert "could not be turned into an ion" in no_ion
        assert "3 adducts" in no_match
        assert "mass tolerance" in no_match
        assert len({no_adducts, no_ion, no_match}) == 3

    def test_counts_a_single_adduct_in_the_singular(self):
        assert "1 adduct " in _blocked_reason(1, generated=True)
        assert "1 adducts" not in _blocked_reason(1, generated=True)


class TestScoring:
    """The controller over a faked seeded chain."""

    @pytest.mark.asyncio
    async def test_reports_nothing_and_scores_nothing_without_such_entries(self):
        result, score_seeds = await _run(
            [{"assigned_formula": "C7H16O5", "ionization_mechanism_id": "im-h"}]
        )

        assert result["results"] == 0
        assert result["data"] == []
        # The point of the check: the peak read and the isotope generation are
        # not paid for on a row with nothing to gain from them.
        score_seeds.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_seeds_every_formula_against_every_adduct_of_the_sample(self):
        _result, score_seeds = await _run(
            [{"assigned_formula": "C4H8N2O3"}, {"assigned_formula": "C9H8"}],
            mechanisms=(PROTON, AMMONIUM, SODIUM),
        )

        seeds = score_seeds.await_args.args[1]
        assert seeds == {
            ("C4H8N2O3", "im-h"),
            ("C4H8N2O3", "im-nh4"),
            ("C4H8N2O3", "im-na"),
            ("C9H8", "im-h"),
            ("C9H8", "im-nh4"),
            ("C9H8", "im-na"),
        }

    @pytest.mark.asyncio
    async def test_reports_the_adduct_whose_m0_lands_on_this_peak(self):
        result, _ = await _run(
            [{"assigned_formula": "C4H8N2O3"}],
            ion_by_seed={
                ("C4H8N2O3", "im-h"): "ion-h",
                ("C4H8N2O3", "im-nh4"): "ion-n",
            },
            fit_by_ion={"ion-h": 0.9, "ion-n": 0.8},
            scored_rows=[
                # The protonated form explains a different peak entirely.
                _scored_row("ion-h", 200.0, "sp-9"),
                _scored_row("ion-n", 200.1, PEAK_ID, mz_error=-0.6),
            ],
        )

        [entry] = result["data"]
        assert entry["ionization_mechanism_id"] == "im-nh4"
        assert entry["ionization_mechanism"] == "[M+NH4]+"
        assert entry["fit_score"] == pytest.approx(0.8)
        assert entry["mz_error_ppm"] == pytest.approx(-0.6)
        assert entry["isotope_label"] == "M0"
        assert entry["adducts_tried"] == 2
        assert entry["adducts_matched"] == 1
        assert "blocked_reason" not in entry

    @pytest.mark.asyncio
    async def test_ranks_the_adducts_on_evidence_not_on_the_fit_alone(self):
        # Both adducts land on the peak. The formula is the same for both, so
        # plausibility cannot separate them and the stronger fit wins - the
        # check is that evidence is what is compared, and it is monotonic in
        # the fit when the formula is held fixed.
        result, _ = await _run(
            [{"assigned_formula": "C4H8N2O3"}],
            ion_by_seed={
                ("C4H8N2O3", "im-h"): "ion-h",
                ("C4H8N2O3", "im-nh4"): "ion-n",
            },
            fit_by_ion={"ion-h": 0.4, "ion-n": 0.95},
            scored_rows=[
                _scored_row("ion-h", 200.0, PEAK_ID),
                _scored_row("ion-n", 200.1, PEAK_ID),
            ],
        )

        [entry] = result["data"]
        assert entry["ionization_mechanism_id"] == "im-nh4"
        assert entry["adducts_matched"] == 2
        # Evidence is fit x plausibility, so it never exceeds the fit.
        assert entry["evidence"] <= entry["fit_score"]

    @pytest.mark.asyncio
    async def test_refuses_an_adduct_that_explains_the_peak_as_an_isotopologue(self):
        # ion-h's M0 sits elsewhere and only its M+1 reaches this peak. That is
        # not the claim the shortlist makes about this peak, so it does not
        # count as a way to assign the formula here.
        result, _ = await _run(
            [{"assigned_formula": "C4H8N2O3"}],
            ion_by_seed={("C4H8N2O3", "im-h"): "ion-h"},
            fit_by_ion={"ion-h": 0.95},
            scored_rows=[
                _scored_row("ion-h", 200.0, "sp-9"),
                _scored_row("ion-h", 201.0, PEAK_ID),
            ],
        )

        [entry] = result["data"]
        assert "fit_score" not in entry
        assert "mass tolerance" in entry["blocked_reason"]

    @pytest.mark.asyncio
    async def test_blocks_a_formula_that_makes_no_ion_with_its_own_reason(self):
        result, _ = await _run(
            [{"assigned_formula": "C4H8N2O3"}, {"assigned_formula": "NotAFormula"}],
            ion_by_seed={("C4H8N2O3", "im-h"): "ion-h"},
            fit_by_ion={"ion-h": 0.9},
            scored_rows=[_scored_row("ion-h", 200.0, PEAK_ID)],
        )

        scored, unscorable = result["data"]
        assert scored["fit_score"] == pytest.approx(0.9)
        # No seed generated an ion for it, which is a different failure from
        # "no adduct reached the peak" and is reported as one.
        assert "could not be turned into an ion" in unscorable["blocked_reason"]

    @pytest.mark.asyncio
    async def test_keeps_the_stored_index_and_the_plausibility_on_every_entry(self):
        result, _ = await _run(
            [
                {"assigned_formula": "C7H16O5", "ionization_mechanism_id": "im-h"},
                {"assigned_formula": "C4H8N2O3"},
            ],
            ion_by_seed={("C4H8N2O3", "im-h"): "ion-h"},
            fit_by_ion={"ion-h": 0.9},
            scored_rows=[_scored_row("ion-h", 200.0, PEAK_ID)],
        )

        [entry] = result["data"]
        # The stored list's index, not the position among the measured ones:
        # the client renders a filtered list and joins on the formula, but the
        # index is what names the entry in the list the server stores.
        assert entry["alternative_index"] == 1
        assert entry["assigned_formula"] == "C4H8N2O3"
        # Recomputed from the formula rather than read off the entry, which is
        # a number a publishing client could have made up.
        assert 0.0 <= entry["plausibility"] <= 1.0

    @pytest.mark.asyncio
    async def test_a_sample_with_no_adducts_blocks_rather_than_scoring(self):
        result, score_seeds = await _run(
            [{"assigned_formula": "C4H8N2O3"}], mechanisms=()
        )

        [entry] = result["data"]
        assert "no adducts recorded" in entry["blocked_reason"]
        assert entry["adducts_tried"] == 0
        # Nothing to seed, so the chain short-circuits rather than reading peaks.
        assert score_seeds.await_args.args[1] == set()

    @pytest.mark.asyncio
    async def test_reports_what_it_did_not_reach_rather_than_dropping_it(self):
        # A run may be configured up to MAX_ALTERNATIVES_CEILING (50), and an
        # imported run's alternatives are whatever the publishing client sent,
        # so the cap is reachable without anything being malformed. What it
        # drops must not read as "no adduct fits", which is a claim about the
        # chemistry that nothing measured.
        formulas = [f"C{n}H{n}O2" for n in range(1, MAX_SCORED_FORMULAS + 4)]
        result, score_seeds = await _run(
            [{"assigned_formula": formula} for formula in formulas],
            ion_by_seed={},
            fit_by_ion={},
        )

        assert len(result["data"]) == len(formulas)
        seeded = {formula for formula, _ in score_seeds.await_args.args[1]}
        assert len(seeded) == MAX_SCORED_FORMULAS

        # The two populations say different things about themselves: one was
        # measured and found nothing, the other was never looked at.
        measured = result["data"][:MAX_SCORED_FORMULAS]
        unreached = result["data"][MAX_SCORED_FORMULAS:]
        assert all("was not reached" not in e["blocked_reason"] for e in measured)
        assert all("was not reached" in e["blocked_reason"] for e in unreached)
        # Every entry still names its own position in the stored list, so a
        # client can line the answer up with what it is showing.
        assert [e["alternative_index"] for e in result["data"]] == list(
            range(len(formulas))
        )

    @pytest.mark.asyncio
    async def test_refuses_an_assignment_that_is_not_this_samples(self):
        with (
            patch(
                f"{_MOD}.async_session",
                return_value=_session_returning(
                    SimpleNamespace(
                        peak_assignment_id="pa-1",
                        sample_item_id="si-OTHER",
                        sample_peak_id=PEAK_ID,
                        alternatives=[],
                    )
                ),
            ),
            pytest.raises(ApiException),
        ):
            await score_row_alternatives(
                sample_item_id="si-1", peak_assignment_id="pa-1"
            )


# -- The run-level pass (step 4.15): the same measurement over a run's winners.


def _winner(peak_id, intensity, alternatives=None, *, formula="C5H10O2", fit=0.9):
    """One committed untargeted M0 row, shaped as the engine writes it."""
    return {
        "peak_assignment_id": f"pa-{peak_id}",
        "sample_peak_id": peak_id,
        "sample_peak_intensity": intensity,
        "role": "M0",
        "source": "untargeted",
        "assigned_formula": formula,
        "fit_score": fit,
        "tier": "assigned",
        "alternatives": alternatives,
        "provenance": {"evidence": fit, "plausibility": 1.0},
    }


#: The run's own measurement, which the pass is scored at; any object will do,
#: since the test reads back that it was handed on.
ACCURACY = SimpleNamespace(sigma_ppm=0.4, scoring_mu_ppm=0.1)
CENTRE = SimpleNamespace(mu_ppm=0.1)
TOLERANCE = SimpleNamespace(share=0.05)


async def _measure(
    rows,
    *,
    peak_budget=10,
    mechanisms=(PROTON, AMMONIUM),
    ion_by_seed=None,
    fit_by_ion=None,
    scored_rows=(),
):
    """Run the pass with the seeded chain faked, as :func:`_run` does."""
    score_seeds = AsyncMock(
        return_value=(
            ion_by_seed or {},
            fit_by_ion or {},
            {},
            pd.DataFrame(list(scored_rows)),
        )
    )
    with patch(f"{_MOD}.score_seeds", score_seeds):
        summary = await measure_winner_confidence(
            SimpleNamespace(sample_item_id="si-1", filename="s.raw", polarity="+"),
            rows,
            list(mechanisms),
            SimpleNamespace(isotope_abundance_threshold=0.01),
            peak_budget=peak_budget,
            reagent_offset=CENTRE,
            envelope_tolerance=TOLERANCE,
            accuracy=ACCURACY,
        )
    return summary, score_seeds


class TestWhatAWinnerCompetesWith:
    """The field a Stage B winner's share is taken of."""

    def test_only_the_untargeted_monoisotopic_rows_are_winners(self):
        rows = [
            _winner("p1", 10.0),
            {**_winner("p2", 10.0), "role": "iso_child"},
            {**_winner("p3", 10.0), "source": "database"},
            {**_winner("p4", 10.0), "assigned_formula": None},
        ]

        assert [row["sample_peak_id"] for row in stage_b_winners(rows)] == ["p1"]

    def test_the_rivals_are_the_alternatives_with_a_fit_not_the_same_ion(self):
        row = _winner(
            "p1",
            10.0,
            [
                # A losing contender, measured as the winner was.
                {"assigned_formula": "C4H8N2O3", "fit_score": 0.7},
                # The winner's own ion split another way: no competitor.
                {"assigned_formula": "C3H6O3", "fit_score": 0.9, "same_ion": True},
                # The finder's shortlist: nothing measured it yet.
                {"assigned_formula": "C6H14N"},
                # A list reading the search displaced, with the fit it had.
                {"assigned_formula": "C4H6O3", "fit_score": 0.5, "displaced": True},
                "C7H6O2",
            ],
        )

        assert recorded_rivals(row) == [("C4H8N2O3", 0.7, None), ("C4H6O3", 0.5, None)]

    def test_a_lone_winner_won_by_default(self):
        assert winner_arbitration(_winner("p1", 10.0), []) == {
            "confidence": 1.0,
            "n_candidates": 1,
            "is_tie": False,
        }

    def test_a_measured_entry_within_the_gap_is_a_tie(self):
        arbitration = winner_arbitration(
            _winner("p1", 10.0), [("C4H8N2O3", 0.88, None)]
        )

        assert arbitration["is_tie"] is True
        assert arbitration["n_candidates"] == 2
        assert arbitration["confidence"] == pytest.approx(0.9 / 1.78, abs=1e-4)

    def test_a_clearly_weaker_entry_is_no_tie(self):
        arbitration = winner_arbitration(_winner("p1", 10.0), [("C4H8N2O3", 0.3, None)])

        assert arbitration["is_tie"] is False
        assert arbitration["confidence"] == pytest.approx(0.75)

    def test_one_neutral_spelled_two_ways_is_one_hypothesis(self):
        # Hill order against the order a finder or a list may write: one
        # composition, so no self-tie and no share split with itself.
        arbitration = winner_arbitration(_winner("p1", 10.0), [("O2C5H10", 0.9, None)])

        assert arbitration == {"confidence": 1.0, "n_candidates": 1, "is_tie": False}

    def test_so_is_a_winner_spelled_the_other_way(self):
        arbitration = winner_arbitration(
            _winner("p1", 10.0, formula="O2C5H10"), [("C5H10O2", 0.9, None)]
        )

        assert arbitration == {"confidence": 1.0, "n_candidates": 1, "is_tie": False}


class TestOneIonIsOneCandidate:
    """Readings of one ion are one hypothesis about the peak, as to the finder."""

    def test_an_ion_reads_alike_however_its_neutral_and_adduct_split(self):
        assert ion_identity("C10H18BrO3-") == ion_identity("C10H18O3Br-")
        assert ion_identity("C10H18BrO3-") == ("C10H18BrO3", "-")
        # The charge is the signs, one per charge: the last count stays the
        # element's.
        assert ion_identity("C6H11O2-") == ("C6H11O2", "-")
        assert ion_identity("C2H8N2++") == ("C2H8N2", "++")
        assert ion_identity("C6H11O2-") != ion_identity("C6H11O2+")
        assert ion_identity(None) is None
        assert ion_identity("") is None

    def test_a_reading_of_the_winners_own_ion_is_the_winner(self):
        # The bromide adduct of C10H18O3 and deprotonated C10H19BrO3 are one
        # ion: the shortlist lists both, and the second is no rival.
        row = _winner("p1", 10.0, formula="C10H18O3", fit=0.87)
        row["ion_formula"] = "C10H18BrO3-"

        arbitration = winner_arbitration(row, [("C10H19BrO3", 0.87, "C10H18BrO3-")])

        assert arbitration == {"confidence": 1.0, "n_candidates": 1, "is_tie": False}

    def test_two_readings_of_another_ion_are_one_rival(self):
        # Two splits of one ion with one fit neither share its weight nor
        # count twice: the winner's share is taken against the ion once.
        row = _winner("p1", 10.0, formula="C10H18O3", fit=0.9)
        row["ion_formula"] = "C10H18BrO3-"

        arbitration = winner_arbitration(
            row,
            [
                ("C8H16Cl2N3O", 0.3, "C8H16BrCl2N3O-"),
                ("C8H17BrCl2N3O", 0.3, "C8H16BrCl2N3O-"),
            ],
        )

        assert arbitration["n_candidates"] == 2
        assert arbitration["confidence"] == pytest.approx(0.75)

    def test_the_best_reading_stands_for_its_ion(self):
        row = _winner("p1", 10.0, fit=0.9)
        row["ion_formula"] = "C5H11O2+"
        # One ion, two readings with different fits: the stronger one is the
        # ion's candidate, whichever order they arrive in.
        readings = [("C4H8N2O3", 0.2, "C4H9N2O3+"), ("C4H6O3", 0.9, "C4H9N2O3+")]

        forward = winner_arbitration(row, readings)
        backward = winner_arbitration(row, readings[::-1])

        assert forward == backward
        assert forward["is_tie"] is True
        assert forward["confidence"] == pytest.approx(0.5)

    def test_a_recorded_rival_of_the_winners_ion_does_not_compete(self):
        row = _winner(
            "p1",
            10.0,
            [
                {
                    "assigned_formula": "C10H19BrO3",
                    "fit_score": 0.95,
                    "ion_formula": "C10H18BrO3-",
                }
            ],
            formula="C10H18O3",
        )
        row["ion_formula"] = "C10H18BrO3-"

        assert winner_arbitration(row, [])["n_candidates"] == 1


class TestTheRunMeasuresItsWinners:
    """`measure_winner_confidence` over a faked seeded chain."""

    @pytest.mark.asyncio
    async def test_records_the_share_and_the_tie_and_nothing_else(self):
        alternatives = [{"assigned_formula": "C4H8N2O3"}]
        row = _winner("p1", 10.0, alternatives)

        summary, _ = await _measure(
            [row],
            ion_by_seed={("C4H8N2O3", "im-h"): "ion-h"},
            fit_by_ion={"ion-h": 0.88},
            scored_rows=[_scored_row("ion-h", 200.0, "p1")],
        )

        assert row["provenance"] == {
            "evidence": 0.9,
            "plausibility": 1.0,
            "confidence": pytest.approx(0.9 / 1.78, abs=1e-4),
            "n_candidates": 2,
            "is_tie": True,
        }
        # Reported, not acted on: the row commits what the run decided.
        assert row["tier"] == "assigned"
        assert row["fit_score"] == 0.9
        assert row["alternatives"] == [{"assigned_formula": "C4H8N2O3"}]
        assert summary["placed"] == 1
        assert summary["ties"] == 1

    @pytest.mark.asyncio
    async def test_a_formula_no_adduct_puts_on_the_peak_is_no_candidate(self):
        # The inspector's rule: only an ion whose M0 paired to this peak counts.
        row = _winner(
            "p1",
            10.0,
            [{"assigned_formula": "C4H8N2O3"}, {"assigned_formula": "C6H14N"}],
        )

        summary, _ = await _measure(
            [row],
            ion_by_seed={
                ("C4H8N2O3", "im-h"): "ion-a",
                ("C6H14N", "im-h"): "ion-b",
            },
            fit_by_ion={"ion-a": 0.95, "ion-b": 0.95},
            scored_rows=[
                _scored_row("ion-a", 200.0, "p-elsewhere"),
                _scored_row("ion-a", 201.0, "p1"),
                _scored_row("ion-b", 200.0, "p1"),
            ],
        )

        assert row["provenance"]["n_candidates"] == 2
        assert summary["formulas"] == 2
        assert summary["placed"] == 1

    @pytest.mark.asyncio
    async def test_an_entry_placed_as_the_winners_own_ion_is_the_winner(self):
        # The shortlist lists the winner's ion split the other way; the pass
        # places it as that ion, and it does not compete.
        row = _winner("p1", 10.0, [{"assigned_formula": "C10H19BrO3"}])
        row["assigned_formula"] = "C10H18O3"
        row["ion_formula"] = "C10H18BrO3-"
        placed = {
            **_scored_row("ion-h", 200.0, "p1"),
            "target_ion_formula": "C10H18BrO3-",
        }

        summary, _ = await _measure(
            [row],
            ion_by_seed={("C10H19BrO3", "im-h"): "ion-h"},
            fit_by_ion={"ion-h": 0.9},
            scored_rows=[placed],
        )

        assert summary["placed"] == 1
        assert row["provenance"]["n_candidates"] == 1
        assert row["provenance"]["is_tie"] is False

    @pytest.mark.asyncio
    async def test_the_best_adduct_is_the_formulas_measurement(self):
        row = _winner("p1", 10.0, [{"assigned_formula": "C4H8N2O3"}])

        await _measure(
            [row],
            ion_by_seed={
                ("C4H8N2O3", "im-h"): "ion-h",
                ("C4H8N2O3", "im-nh4"): "ion-n",
            },
            fit_by_ion={"ion-h": 0.2, "ion-n": 0.6},
            scored_rows=[
                _scored_row("ion-h", 200.0, "p1"),
                _scored_row("ion-n", 200.1, "p1"),
            ],
        )

        assert row["provenance"]["confidence"] == pytest.approx(0.6, abs=1e-4)

    @pytest.mark.asyncio
    async def test_seeds_every_shortlist_formula_under_every_adduct_in_one_pass(self):
        rows = [
            _winner("p1", 10.0, [{"assigned_formula": "C4H8N2O3"}]),
            _winner(
                "p2",
                5.0,
                [{"assigned_formula": "C6H14N"}, {"assigned_formula": "C4H8N2O3"}],
                formula="C7H6O2",
            ),
        ]

        _summary, score_seeds = await _measure(rows)

        score_seeds.assert_awaited_once()
        assert score_seeds.await_args.args[1] == {
            ("C4H8N2O3", "im-h"),
            ("C4H8N2O3", "im-nh4"),
            ("C6H14N", "im-h"),
            ("C6H14N", "im-nh4"),
        }

    @pytest.mark.asyncio
    async def test_scores_at_the_runs_measurement_not_one_fitted_off_the_seeds(self):
        _summary, score_seeds = await _measure(
            [_winner("p1", 10.0, [{"assigned_formula": "C4H8N2O3"}])]
        )

        kwargs = score_seeds.await_args.kwargs
        assert kwargs["inherited_accuracy"] is ACCURACY
        assert kwargs["reagent_offset"] is CENTRE
        assert kwargs["envelope_tolerance"] is TOLERANCE

    @pytest.mark.asyncio
    async def test_the_budget_goes_to_the_brightest_winners_first(self):
        dim = _winner("p-dim", 5.0, [{"assigned_formula": "C6H14N"}])
        bright = _winner("p-bright", 50.0, [{"assigned_formula": "C4H8N2O3"}])
        middle = _winner("p-mid", 20.0, [{"assigned_formula": "C4H6O3"}])

        summary, score_seeds = await _measure([dim, bright, middle], peak_budget=2)

        seeded = {formula for formula, _ in score_seeds.await_args.args[1]}
        assert seeded == {"C4H8N2O3", "C4H6O3"}
        assert "confidence" in bright["provenance"]
        assert "confidence" in middle["provenance"]
        # A share taken of part of the field would read as a share of all of it.
        assert "confidence" not in dim["provenance"]
        assert "is_tie" not in dim["provenance"]
        assert summary == {
            "peak_budget": 2,
            "winners": 3,
            "measured": 2,
            "unreached": 1,
            "formulas": 2,
            "placed": 0,
            "ties": 0,
            "seconds": summary["seconds"],
        }

    @pytest.mark.asyncio
    async def test_a_winner_with_no_shortlist_costs_no_budget(self):
        lone = _winner(
            "p-lone", 1.0, [{"assigned_formula": "C4H8N2O3", "fit_score": 0.3}]
        )
        bright = _winner("p-bright", 50.0, [{"assigned_formula": "C6H14N"}])
        dimmer = _winner("p-dimmer", 20.0, [{"assigned_formula": "C4H6O3"}])

        summary, _ = await _measure([lone, bright, dimmer], peak_budget=1)

        # Its rivals were measured by the run already; nothing is spent on it.
        assert lone["provenance"]["confidence"] == pytest.approx(0.75)
        assert lone["provenance"]["n_candidates"] == 2
        assert "confidence" in bright["provenance"]
        assert "confidence" not in dimmer["provenance"]
        assert summary["measured"] == 1
        assert summary["unreached"] == 1

    @pytest.mark.asyncio
    async def test_ties_are_flagged_where_the_inspector_shows_them(self):
        # One seeded measurement, read by both: the inspector's best entry for
        # each formula, arbitrated with the winner, says what the run records.
        alternatives = [
            {"assigned_formula": "C4H8N2O3"},
            {"assigned_formula": "C6H14N"},
        ]
        measurement = {
            "ion_by_seed": {
                ("C4H8N2O3", "im-h"): "ion-a",
                ("C4H8N2O3", "im-nh4"): "ion-b",
                ("C6H14N", "im-h"): "ion-c",
            },
            "fit_by_ion": {"ion-a": 0.5, "ion-b": 0.86, "ion-c": 0.4},
            "scored_rows": [
                _scored_row("ion-a", 200.0, PEAK_ID),
                _scored_row("ion-b", 200.1, PEAK_ID),
                _scored_row("ion-c", 200.2, PEAK_ID),
            ],
        }
        inspected, _ = await _run(alternatives, **measurement)
        row = _winner(PEAK_ID, 10.0, alternatives)

        await _measure([row], **measurement)

        shown = arbitrate_candidates(
            [("C5H10O2", 0.9)]
            + [(e["assigned_formula"], e["fit_score"]) for e in inspected["data"]]
        )
        winner = next(c for c in shown if c.formula == "C5H10O2")
        assert row["provenance"]["is_tie"] is winner.is_tie is True
        assert row["provenance"]["confidence"] == pytest.approx(
            winner.confidence, abs=1e-4
        )


class TestTheOption:
    """Off by default; the budget bounded."""

    def test_off_by_default_with_a_budget(self):
        config = PeakAssignmentConfig()

        assert config.stage_b_confidence is False
        assert config.stage_b_confidence_peaks == 200

    @pytest.mark.parametrize("budget", [0, MAX_UNTARGETED_PEAKS_CEILING + 1])
    def test_a_budget_outside_its_bounds_is_refused(self, budget):
        with pytest.raises(ValueError):
            PeakAssignmentConfig(stage_b_confidence_peaks=budget)

    def test_the_ceiling_itself_is_a_budget(self):
        config = PeakAssignmentConfig(
            stage_b_confidence=True,
            stage_b_confidence_peaks=MAX_UNTARGETED_PEAKS_CEILING,
        )

        assert config.stage_b_confidence_peaks == MAX_UNTARGETED_PEAKS_CEILING
