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
    score_row_alternatives,
    unscorable_alternatives,
)


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
