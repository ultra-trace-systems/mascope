"""The count of the rivals a row's set leaves out (MAS-1184, #3497).

A row's rival set holds the candidates the search could not separate from it
at the model it ranked at, measured at the model the caps read. With
``count_scored_rivals`` on, the run counts the other candidates the search
scored that would tie the reading at the caps' model, and changes nothing.
The frames are stated here; what is read off them, at which model, is the
production code's.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import rival_count, rival_set
from mascope_backend.api.new.peak_assignments.rival_count import (
    KIND_LIST,
    KIND_SEARCH,
    SCORED_RIVALS_KEY,
    Handed,
    count,
    gap_band,
    handed_by_peak,
    search_config,
    tally,
)
from mascope_backend.api.new.peak_assignments.rival_set import (
    RIVAL_SET,
    RivalFrames,
    SeededFrame,
    measure_rival_sets,
)
from mascope_tools.composition.finder import SCORED_RIVALS


#: The model the frames were scored at, and the one a refit took.
V1 = SimpleNamespace(version=1)
V2 = SimpleNamespace(version=2)


def _line(ion, formula, peak, fit, *, mechanism="im-h"):
    return {
        "target_ion_id": ion,
        "target_isotope_id": f"{ion}-0",
        "target_ion_formula": f"{formula}H+",
        "target_compound_formula": formula,
        "ionization_mechanism_id": mechanism,
        "mz": 200.0,
        "sample_peak_id": peak,
        "sample_peak_intensity": 100.0,
        "match_mz_error": 0.2,
        "match_score": fit,
    }


def _winner(density_fit=0.3):
    """The search's winner on p1 and the rival it named there."""
    return SeededFrame(
        pd.DataFrame(
            [_line("w", "C5H10O2", "p1", 0.9), _line("r", "C4H6O3", "p1", density_fit)]
        ),
        {("C5H10O2", "im-h"): "w", ("C4H6O3", "im-h"): "r"},
        {("C4H6O3", "im-h"): frozenset({"p1"})},
    )


def _extra():
    """The extra pass: one candidate whose line sits on p1, one whose sits
    elsewhere."""
    return SeededFrame(
        pd.DataFrame(
            [
                _line("c", "C6H14O", "p1", 0.5),
                _line("x", "C3H6N2O2", "p-other", 0.99),
            ]
        ),
        {("C6H14O", "im-h"): "c", ("C3H6N2O2", "im-h"): "x"},
        {
            ("C6H14O", "im-h"): frozenset({"p1"}),
            ("C3H6N2O2", "im-h"): frozenset({"p1"}),
        },
    )


def _row():
    return {
        "peak_assignment_id": "pa-p1",
        "sample_peak_id": "p1",
        "role": "M0",
        "source": "untargeted",
        "assigned_formula": "C5H10O2",
        "ion_formula": "C5H10O2H+",
        "ionization_mechanism_id": "im-h",
        "tier": "assigned",
        "provenance": {"evidence": 0.9, "plausibility": 1.0},
    }


#: What the search handed over for the winner: its named rival, a candidate it
#: had 1.5 tie gaps off, and one 7 gaps off whose line is on another peak.
HANDED = {
    ("p1", KIND_SEARCH): [
        Handed(("C4H6O3", "im-h"), "C4H6O3", 0.4),
        Handed(("C6H14O", "im-h"), "C6H14O", 1.5),
        Handed(("C3H6N2O2", "im-h"), "C3H6N2O2", 7.0),
    ]
}


def _at_v2(fits):
    """``score_frame_at`` as a refit to V2 moves the lines: the fits by ion."""

    def score_at(frame, model):
        assert model is V2
        return frame["target_ion_id"].map(fits)

    return score_at


class TestTheBands:
    @pytest.mark.parametrize(
        "gaps, band",
        [
            (0.5, "within_1"),
            (1.0, "within_1"),
            (1.01, "1_to_2"),
            (2.0, "1_to_2"),
            (2.5, "2_to_3"),
            (4.0, "3_to_5"),
            (5.0, "3_to_5"),
            (5.1, "over_5"),
        ],
    )
    def test_a_distance_falls_in_one_band(self, gaps, band):
        assert gap_band(gaps) == band


class TestACandidateSeparatedAtV1:
    """Verify 3: one candidate is separated at the first model and ties at the
    refitted one; it is counted once, in its band, and is in no set."""

    def test_it_ties_at_v2_and_is_counted_once_in_its_band(self, monkeypatch):
        # At V2 the winner reads 0.8, its named rival is separated (0.3),
        # and the candidate the search had 1.5 gaps off ties it (0.78).
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "c": 0.78, "x": 0.99}),
        )
        frames = RivalFrames(None, (_winner(),), base=V1)
        extra = RivalFrames(None, (_extra(),), base=V1)
        row = _row()
        measure_rival_sets([row], frames, V2)
        before = copy.deepcopy(row)

        record = tally([row], HANDED, frames, extra, V2)

        search = record[KIND_SEARCH]
        assert search["readings"] == 1
        assert search["scored"] == 3
        assert (search["held"], search["outside"], search["not_placed"]) == (1, 1, 1)
        assert search["ties"] == {
            "within_1": 0,
            "1_to_2": 1,
            "2_to_3": 0,
            "3_to_5": 0,
            "over_5": 0,
        }
        assert record[KIND_LIST]["readings"] == 0
        # Today the winner stands alone; with the candidate it would not.
        assert row["provenance"]["candidate_density"] == 1
        assert record["density_rises"] == 1
        assert record["reach_limit"] == {"assigned": 1}
        assert record["would_cap"] == 1
        # The count reads the row and writes nothing: the candidate is in no
        # set, and the density is what it was.
        assert row == before
        formulas = {r["formula"] for r in row["provenance"][RIVAL_SET]["rivals"]}
        assert "C6H14O" not in formulas

    def test_at_v1_it_stays_separated(self):
        frames = RivalFrames(None, (_winner(),), base=V1)
        extra = RivalFrames(None, (_extra(),), base=V1)
        row = _row()
        measure_rival_sets([row], frames, V1)

        record = tally([row], HANDED, frames, extra, V1)

        assert record[KIND_SEARCH]["outside"] == 1
        assert sum(record[KIND_SEARCH]["ties"].values()) == 0
        assert record["density_rises"] == 0
        assert record["reach_limit"] == {}

    def test_a_row_already_at_the_limit_rises_without_reaching_it(self, monkeypatch):
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.79, "c": 0.78, "x": 0.99}),
        )
        frames = RivalFrames(None, (_winner(),), base=V1)
        extra = RivalFrames(None, (_extra(),), base=V1)
        row = _row()
        measure_rival_sets([row], frames, V2)
        assert row["provenance"]["candidate_density"] == 2

        record = tally([row], HANDED, frames, extra, V2)

        assert record["density_rises"] == 1
        assert record["reach_limit"] == {}
        assert record["would_cap"] == 0

    def test_a_list_reading_is_counted_apart(self, monkeypatch):
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "c": 0.78, "x": 0.99}),
        )
        frames = RivalFrames(None, (_winner(),), base=V1)
        extra = RivalFrames(None, (_extra(),), base=V1)
        row = {**_row(), "source": "database"}
        measure_rival_sets([row], frames, V2)

        record = tally(
            [row], {("p1", KIND_LIST): HANDED[("p1", KIND_SEARCH)]}, frames, extra, V2
        )

        assert record[KIND_LIST]["ties"]["1_to_2"] == 1
        assert record[KIND_SEARCH]["readings"] == 0

    def test_a_corroborated_row_reaches_the_limit_and_would_not_be_capped(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "c": 0.78, "x": 0.99}),
        )
        frames = RivalFrames(None, (_winner(),), base=V1)
        extra = RivalFrames(None, (_extra(),), base=V1)
        row = _row()
        row["provenance"]["cross_channel"] = {"channels": ["im-h", "im-na"]}
        measure_rival_sets([row], frames, V2)

        record = tally([row], HANDED, frames, extra, V2)

        assert record["reach_limit"] == {"assigned": 1}
        assert record["would_cap"] == 0

    def test_a_formula_handed_through_two_channels_ties_at_its_best(self, monkeypatch):
        # The same neutral through a second channel, placed at a worse fit
        # after its better reading: the neutral is read once, at its best.
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "c": 0.78, "c2": 0.2}),
        )
        extra = SeededFrame(
            pd.DataFrame(
                [
                    _line("c", "C6H14O", "p1", 0.5),
                    _line("c2", "C6H14O", "p1", 0.5, mechanism="im-na"),
                ]
            ),
            {("C6H14O", "im-h"): "c", ("C6H14O", "im-na"): "c2"},
            {
                ("C6H14O", "im-h"): frozenset({"p1"}),
                ("C6H14O", "im-na"): frozenset({"p1"}),
            },
        )
        frames = RivalFrames(None, (_winner(),), base=V1)
        row = _row()
        measure_rival_sets([row], frames, V2)
        handed = {
            ("p1", KIND_SEARCH): [
                Handed(("C6H14O", "im-h"), "C6H14O", 1.5),
                Handed(("C6H14O", "im-na"), "C6H14O", 6.0),
            ]
        }

        record = tally([row], handed, frames, RivalFrames(None, (extra,), base=V1), V2)

        assert record[KIND_SEARCH]["outside"] == 2
        assert record[KIND_SEARCH]["ties"]["1_to_2"] == 1
        assert sum(record[KIND_SEARCH]["ties"].values()) == 1

    def test_a_formula_ties_in_the_band_of_the_channel_that_ties(self, monkeypatch):
        # As above, but the second channel's line is the one that ties: the
        # band is the one the search had that channel in.
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "c": 0.2, "c2": 0.78}),
        )
        extra = SeededFrame(
            pd.DataFrame(
                [
                    _line("c", "C6H14O", "p1", 0.5),
                    dict(
                        _line("c2", "C6H14O", "p1", 0.5, mechanism="im-na"),
                        target_ion_formula="C6H14ONa+",
                    ),
                ]
            ),
            {("C6H14O", "im-h"): "c", ("C6H14O", "im-na"): "c2"},
            {
                ("C6H14O", "im-h"): frozenset({"p1"}),
                ("C6H14O", "im-na"): frozenset({"p1"}),
            },
        )
        frames = RivalFrames(None, (_winner(),), base=V1)
        row = _row()
        measure_rival_sets([row], frames, V2)
        handed = {
            ("p1", KIND_SEARCH): [
                Handed(("C6H14O", "im-h"), "C6H14O", 1.5),
                Handed(("C6H14O", "im-na"), "C6H14O", 6.0),
            ]
        }

        record = tally([row], handed, frames, RivalFrames(None, (extra,), base=V1), V2)

        assert record[KIND_SEARCH]["ties"]["over_5"] == 1
        assert sum(record[KIND_SEARCH]["ties"].values()) == 1

    def test_two_neutrals_of_one_ion_tie_once_as_a_seeded_set_reads_them(
        self, monkeypatch
    ):
        # Two outside neutrals read as one ion through two channels: the set
        # holds an ion once, at its best evidence, so seeding both would add
        # one rival and raise the density to 2, not 3.
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "a": 0.78, "b": 0.77}),
        )
        a = _line("a", "C6H14O", "p1", 0.5)
        b = dict(
            _line("b", "C6H12", "p1", 0.5, mechanism="im-h2o"),
            target_ion_formula=a["target_ion_formula"],
        )

        def extra():
            return SeededFrame(
                pd.DataFrame([a, b]),
                {("C6H14O", "im-h"): "a", ("C6H12", "im-h2o"): "b"},
                {
                    ("C6H14O", "im-h"): frozenset({"p1"}),
                    ("C6H12", "im-h2o"): frozenset({"p1"}),
                },
            )

        frames = RivalFrames(None, (_winner(),), base=V1)
        row = _row()
        measure_rival_sets([row], frames, V2)
        handed = {
            ("p1", KIND_SEARCH): [
                Handed(("C6H14O", "im-h"), "C6H14O", 1.5),
                Handed(("C6H12", "im-h2o"), "C6H12", 2.5),
            ]
        }

        record = tally(
            [row], handed, frames, RivalFrames(None, (extra(),), base=V1), V2
        )

        search = record[KIND_SEARCH]
        assert search["outside"] == 2
        assert search["ties"]["1_to_2"] == 1
        assert sum(search["ties"].values()) == 1
        assert record["density_rises"] == 1
        # What seeding both into the set would read.
        seeded = _row()
        measure_rival_sets(
            [seeded], RivalFrames(None, (_winner(), extra()), base=V1), V2
        )
        assert seeded["provenance"]["candidate_density"] == 2
        assert [r["formula"] for r in seeded["provenance"][RIVAL_SET]["rivals"]] == [
            "C6H14O",
            "C4H6O3",
        ]

    def test_a_candidate_reading_a_held_ion_is_held(self, monkeypatch):
        # Another neutral read as the named rival's own ion: the set holds
        # that hypothesis already.
        monkeypatch.setattr(
            rival_set, "score_frame_at", _at_v2({"w": 0.8, "r": 0.3, "s": 0.79})
        )
        split = dict(_line("s", "C5H4O2", "p1", 0.5), target_ion_formula="C4H6O3H+")
        extra = SeededFrame(
            pd.DataFrame([split]),
            {("C5H4O2", "im-h"): "s"},
            {("C5H4O2", "im-h"): frozenset({"p1"})},
        )
        frames = RivalFrames(None, (_winner(),), base=V1)
        row = _row()
        measure_rival_sets([row], frames, V2)
        handed = {("p1", KIND_SEARCH): [Handed(("C5H4O2", "im-h"), "C5H4O2", 1.5)]}

        record = tally([row], handed, frames, RivalFrames(None, (extra,), base=V1), V2)

        assert record[KIND_SEARCH]["held"] == 1
        assert record[KIND_SEARCH]["outside"] == 0

    def test_a_rival_the_set_holds_by_its_fit_alone_is_held(self, monkeypatch):
        # A list reading's grid formula only plausibility separated is in its
        # set under ``fit_tied``: held, though no density reads it.
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "t": 0.79, "c": 0.79}),
        )
        winner = _winner()
        tied = SeededFrame(
            pd.DataFrame([_line("t", "C6H14O", "p1", 0.5)]),
            {("C6H14O", "im-h"): "t"},
            {("C6H14O", "im-h"): frozenset({"p1"})},
            {("C6H14O", "im-h"): frozenset({"p1"})},
        )
        frames = RivalFrames(None, (winner, tied), base=V1)
        extra = RivalFrames(None, (_extra(),), base=V1)
        row = {**_row(), "source": "database"}
        measure_rival_sets([row], frames, V2)
        assert row["provenance"][RIVAL_SET]["fit_tied"]
        handed = {("p1", KIND_LIST): [Handed(("C6H14O", "im-h"), "C6H14O", 1.5)]}

        record = tally([row], handed, frames, extra, V2)

        assert record[KIND_LIST]["held"] == 1
        assert sum(record[KIND_LIST]["ties"].values()) == 0

    def test_a_row_without_a_set_is_not_counted(self):
        frames = RivalFrames(None, (_winner(),), base=V1)
        extra = RivalFrames(None, (_extra(),), base=V1)

        record = tally([_row()], HANDED, frames, extra, V1)

        assert record[KIND_SEARCH]["readings"] == 0


class TestWhatTheSearchHandedOver:
    def test_each_committed_reading_and_list_reading_by_its_peak(self):
        frame = pd.DataFrame({"mz": [200.0, 300.0], "sample_peak_id": ["p1", "p2"]})
        matches = pd.DataFrame(
            [
                {
                    "mz": 200.0,
                    "formula": "C5H10O2",
                    SCORED_RIVALS: [
                        {
                            "formula": "C6H14O",
                            "ionization_mechanism": "[M+H]+",
                            "tie_gaps": 1.5,
                        },
                        # A channel the run did not search is no seed.
                        {
                            "formula": "C3H6",
                            "ionization_mechanism": "[M+K]+",
                            "tie_gaps": 2.0,
                        },
                    ],
                },
                {"mz": 300.0, "formula": "C2H4", SCORED_RIVALS: None},
            ]
        )

        handed = handed_by_peak(
            matches,
            frame,
            mechanism_id_by_notation={"[M+H]+": "im-h"},
            list_scored={
                "p2": (
                    {
                        "formula": "C4H6O3",
                        "ionization_mechanism": "[M+H]+",
                        "tie_gaps": 3.0,
                    },
                )
            },
        )

        assert handed == {
            ("p1", KIND_SEARCH): [Handed(("C6H14O", "im-h"), "C6H14O", 1.5)],
            ("p2", KIND_LIST): [Handed(("C4H6O3", "im-h"), "C4H6O3", 3.0)],
        }

    def test_a_search_with_the_option_off_hands_over_nothing(self):
        frame = pd.DataFrame({"mz": [200.0], "sample_peak_id": ["p1"]})
        matches = pd.DataFrame([{"mz": 200.0, "formula": "C5H10O2"}])

        assert handed_by_peak(matches, frame, mechanism_id_by_notation={}) == {}


class TestTheSearchConfig:
    def _ctx(self, on):
        profile = SimpleNamespace(
            search_config=lambda notations: rival_count_config(notations)
        )
        return SimpleNamespace(
            resolved_profile=profile,
            config=SimpleNamespace(count_scored_rivals=on),
        )

    def test_off_the_profile_search_is_unchanged(self):
        assert search_config(self._ctx(False), ["[M+H]+"]).scored_rivals is False

    def test_on_it_hands_over_every_candidate(self):
        config = search_config(self._ctx(True), ["[M+H]+"])
        assert config.scored_rivals is True
        assert config.ionizations == "[M+H]+"


def rival_count_config(notations):
    from mascope_tools.composition.models import CompositionSearchConfig

    return CompositionSearchConfig(ionizations=",".join(notations))


def _ctx(*, on=True, model=V2):
    frames = RivalFrames(None, (_winner(),), base=V1)
    row = _row()
    measure_rival_sets([row], frames, model)
    matches = pd.DataFrame(
        [
            {
                "mz": 200.0,
                "formula": "C5H10O2",
                "ionization_mechanism": "[M+H]+",
                "isotope_label": "M0",
                SCORED_RIVALS: [
                    {
                        "formula": "C4H6O3",
                        "ionization_mechanism": "[M+H]+",
                        "tie_gaps": 0.4,
                    },
                    {
                        "formula": "C6H14O",
                        "ionization_mechanism": "[M+H]+",
                        "tie_gaps": 1.5,
                    },
                    # The committed reading is measured already: not again.
                    {
                        "formula": "C5H10O2",
                        "ionization_mechanism": "[M+H]+",
                        "tie_gaps": 0.0,
                    },
                ],
            }
        ]
    )
    search = SimpleNamespace(
        ran=True,
        matches=matches,
        frame=pd.DataFrame({"mz": [200.0], "sample_peak_id": ["p1"]}),
        mechanism_id_by_notation={"[M+H]+": "im-h"},
        election=None,
        scope={},
    )
    return SimpleNamespace(
        config=SimpleNamespace(count_scored_rivals=on),
        search=search,
        judging=SimpleNamespace(rival_frames=frames),
        judged=SimpleNamespace(rows=[row], model=model),
        model=V1,
        sample=SimpleNamespace(sample_item_name="s"),
        match_params=object(),
        resolution=None,
        seeded_frame=SimpleNamespace(seconds=1.25),
    )


def _score_seeds():
    extra = _extra()

    async def score(_sample, seeds, _params, **_kwargs):
        ion_by_seed = {s: i for s, i in extra.ion_by_seed.items() if s in seeds}
        scored = extra.scored[extra.scored["target_ion_id"].isin(ion_by_seed.values())]
        return ion_by_seed, {}, {}, scored

    return AsyncMock(side_effect=score)


class TestTheRunsCount:
    """Verify 2, at the stage: off, nothing is measured and the record carries
    no count; on, the count is recorded and the rows are what they were."""

    @pytest.mark.asyncio
    async def test_off_it_measures_and_records_nothing(self):
        ctx = _ctx(on=False, model=V1)
        scored = _score_seeds()
        with patch.object(rival_count, "score_seeds", scored):
            await count(ctx)
        scored.assert_not_awaited()
        assert SCORED_RIVALS_KEY not in ctx.search.scope

    @pytest.mark.asyncio
    async def test_on_it_records_the_count_and_changes_no_row(self, monkeypatch):
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "c": 0.78, "x": 0.99}),
        )
        ctx = _ctx()
        rows = copy.deepcopy(ctx.judged.rows)
        scored = _score_seeds()
        with patch.object(rival_count, "score_seeds", scored):
            await count(ctx)

        # Only what the run did not seed already, at the model its frames were
        # first scored at.
        assert scored.await_args.args[1] == {("C4H6O3", "im-h"), ("C6H14O", "im-h")}
        assert scored.await_args.kwargs["model"] is V1
        record = ctx.search.scope[SCORED_RIVALS_KEY]
        assert record[KIND_SEARCH]["ties"]["1_to_2"] == 1
        # The committed formula's own entry is held, as the set holds it.
        assert record[KIND_SEARCH]["scored"] == 3
        assert record[KIND_SEARCH]["held"] == 2
        assert record["refit_taken"] is True
        assert (record["named_at_version"], record["measured_at_version"]) == (1, 2)
        assert record["seeds_measured"] == 2
        assert record["seeded_seconds"] == 1.25
        assert record["seconds"] >= 0
        assert ctx.judged.rows == rows

    @pytest.mark.asyncio
    async def test_a_list_reading_s_candidates_come_from_the_election(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            _at_v2({"w": 0.8, "r": 0.3, "c": 0.78, "x": 0.99}),
        )
        ctx = _ctx()
        ctx.judged.rows[0]["source"] = "database"
        ctx.search.matches = ctx.search.matches.drop(columns=[SCORED_RIVALS])
        ctx.search.election = SimpleNamespace(
            scored_by_peak={
                "p1": (
                    {
                        "formula": "C6H14O",
                        "ionization_mechanism": "[M+H]+",
                        "tie_gaps": 2.5,
                    },
                )
            }
        )
        with patch.object(rival_count, "score_seeds", _score_seeds()):
            await count(ctx)
        record = ctx.search.scope[SCORED_RIVALS_KEY]
        assert record[KIND_LIST]["readings"] == 1
        assert record[KIND_LIST]["ties"]["2_to_3"] == 1
        assert record[KIND_SEARCH]["readings"] == 0

    @pytest.mark.asyncio
    async def test_a_run_that_kept_its_first_model_says_so(self):
        ctx = _ctx(model=V1)
        with patch.object(rival_count, "score_seeds", _score_seeds()):
            await count(ctx)
        record = ctx.search.scope[SCORED_RIVALS_KEY]
        assert record["refit_taken"] is False
        assert (record["named_at_version"], record["measured_at_version"]) == (1, 1)

    @pytest.mark.asyncio
    async def test_a_run_whose_search_did_not_run_counts_nothing(self):
        ctx = _ctx(model=V1)
        ctx.search.ran = False
        scored = _score_seeds()
        with patch.object(rival_count, "score_seeds", scored):
            await count(ctx)
        scored.assert_not_awaited()
        assert ctx.search.scope == {}
