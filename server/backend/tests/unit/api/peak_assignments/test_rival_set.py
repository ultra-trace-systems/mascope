"""One rival set per peak, measured once at the run's measurement model.

Every reader of a peak's rivals reads the one set: the arbitration's
confidence and tie, the density the tiering pass caps on and its
``no_close_rival``, the 4.15 share and the identification level's exclusions
(MAS-1072). The frames are stated here rather than measured; what is read
off them, and at which model, is the production code's.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import judging_loop, rival_set
from mascope_backend.api.new.peak_assignments.engine import (
    GRID_RIVALS,
    MEASURED_SHORTLIST,
    invert_matches_to_peak_assignments,
)
from mascope_backend.api.new.peak_assignments.identification_level import shortlist
from mascope_backend.api.new.peak_assignments.rival_set import (
    FRAME_KNOWN,
    FRAME_SEARCH,
    RIVAL_SET,
    RivalFrames,
    SeededFrame,
    measure_rival_sets,
    named_rival_seeds,
)
from mascope_backend.api.new.peak_assignments.tiering import (
    REASON_NO_CLOSE_RIVAL,
    density_reason,
    standing_reasons,
)
from mascope_tools.composition.arbitration import arbitrate_candidates, unseparated
from mascope_tools.composition.finder import CANDIDATE_RIVALS


#: The model the frames were scored at, and the one a refit took.
V1 = SimpleNamespace(version=1)
V2 = SimpleNamespace(version=2)


def _line(ion, formula, peak, fit, *, mechanism="im-h", ion_formula=None, mz=200.0):
    """One gated line of a seeded frame."""
    return {
        "target_ion_id": ion,
        "target_isotope_id": f"{ion}-{mz}",
        "target_ion_formula": ion_formula or f"{formula}H+",
        "target_compound_formula": formula,
        "ionization_mechanism_id": mechanism,
        "mz": mz,
        "sample_peak_id": peak,
        "sample_peak_intensity": 100.0,
        "match_mz_error": 0.2,
        "match_score": fit,
    }


def _winner(peak="p1", formula="C5H10O2", fit=0.9, source="untargeted"):
    return {
        "peak_assignment_id": f"pa-{peak}",
        "sample_peak_id": peak,
        "role": "M0",
        "source": source,
        "assigned_formula": formula,
        "ion_formula": f"{formula}H+",
        "ionization_mechanism_id": "im-h",
        "fit_score": fit,
        "tier": "assigned",
        "provenance": {"evidence": fit, "plausibility": 1.0},
    }


def _frames(*, rival_fit=0.86, shortlist_fit=0.4, at=None) -> tuple[RivalFrames, dict]:
    """The search's winner on p1, a reading the search named against it, and
    two shortlist formulas the 4.15 pass measured for it, one placed."""
    search = SeededFrame(
        pd.DataFrame(
            [
                _line("w", "C5H10O2", "p1", 0.9),
                _line("r", "C4H6O3", "p1", rival_fit),
            ]
        ),
        {("C5H10O2", "im-h"): "w", ("C4H6O3", "im-h"): "r"},
        {("C4H6O3", "im-h"): frozenset({"p1"})},
    )
    short = SeededFrame(
        pd.DataFrame(
            [
                _line("s", "C6H14O", "p1", shortlist_fit),
                _line("x", "C3H6N2O2", "p-other", 0.99),
            ]
        ),
        {("C6H14O", "im-h"): "s", ("C3H6N2O2", "im-h"): "x"},
        {
            ("C6H14O", "im-h"): frozenset({"p1"}),
            ("C3H6N2O2", "im-h"): frozenset({"p1"}),
        },
    )
    frames = RivalFrames(
        None, (search, short), base=V1, shortlists={"p1": ("C6H14O", "C3H6N2O2")}
    )
    return frames, {"search": search, "short": short}


class TestOnePeakOneSet:
    """Verify 1: one peak's density, ``no_close_rival``, 4.15 share and level
    exclusions read the same rivals, at the same centre and width."""

    def test_every_reader_reads_the_one_set_at_one_model(self, monkeypatch):
        # At V2 the frames are scored again; every number below comes from
        # that one call per frame, at that one model.
        frames, _ = _frames()
        handed = []

        def score_at(frame, model):
            handed.append(model)
            moved = {"w": 0.8, "r": 0.78, "s": 0.3, "x": 0.95}
            return frame["target_ion_id"].map(moved)

        monkeypatch.setattr(rival_set, "score_frame_at", score_at)
        row = _winner()

        summary = measure_rival_sets([row], frames, V2)

        assert set(map(id, handed)) == {id(V2)}
        provenance = row["provenance"]
        record = provenance[RIVAL_SET]
        assert record["model_version"] == 2
        assert record["fit_score"] == 0.8
        fits = {entry["formula"]: entry["fit_score"] for entry in record["rivals"]}
        assert fits == {"C4H6O3": 0.78, "C6H14O": 0.3}
        assert {entry["formula"]: entry["frame"] for entry in record["rivals"]} == {
            "C4H6O3": FRAME_SEARCH,
            "C6H14O": FRAME_SEARCH,
        }
        candidates = [("C5H10O2", 0.8), ("C4H6O3", 0.78), ("C6H14O", 0.3)]
        # The density, counted by the arbitration's tie rule over the set.
        counted = unseparated(candidates, around="C5H10O2")
        assert provenance["candidate_density"] == len(counted) == 2
        # The tiering pass caps on it, and names the count.
        assert density_reason(row)["detail"].startswith("2 formulas")
        # The 4.15 share and tie: the same arbitration over the same set.
        arbitrated = arbitrate_candidates(candidates)
        mine = next(c for c in arbitrated if c.formula == "C5H10O2")
        assert provenance["confidence"] == round(mine.confidence, 4)
        assert provenance["is_tie"] is mine.is_tie is True
        assert provenance["n_candidates"] == 3
        assert provenance[MEASURED_SHORTLIST] == [
            {
                "formula": "C6H14O",
                "ion_formula": "C6H14OH+",
                "fit_score": 0.3,
                "mz_error_ppm": 0.2,
            },
            {"formula": "C3H6N2O2", "placed": False},
        ]
        # The level's exclusions: the same rivals, beside the same own fit,
        # the unplaced formula out by mass.
        rivals, unlisted = shortlist(row)
        assert {r.formula: (r.fit, r.against, r.placed) for r in rivals} == {
            "C4H6O3": (0.78, 0.8, True),
            "C6H14O": (0.3, 0.8, True),
            "C3H6N2O2": (None, 0.8, False),
        }
        assert unlisted == 0
        assert summary == {
            "model_version": 2,
            "measured": 1,
            "unmeasured": 0,
            "shortlist_placed": 1,
            "shortlist_ties": 1,
        }

    def test_a_separated_rival_leaves_no_close_rival(self):
        frames, _ = _frames(rival_fit=0.3)
        row = _winner()

        measure_rival_sets([row], frames, V1)

        assert row["provenance"]["candidate_density"] == 1
        assert density_reason(row) is None
        reasons = {reason["rule"] for reason in standing_reasons(row, [])}
        assert REASON_NO_CLOSE_RIVAL in reasons

    def test_at_the_model_the_frames_were_scored_at_their_fits_are_read(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            rival_set, "score_frame_at", lambda *_: pytest.fail("scored again")
        )
        frames, _ = _frames()
        row = _winner()

        measure_rival_sets([row], frames, V1)

        assert row["provenance"][RIVAL_SET]["fit_score"] == 0.9

    def test_a_reading_named_for_another_peak_is_no_rival_here(self):
        frames, _ = _frames()
        row = _winner()

        measure_rival_sets([row], frames, V1)

        # x's own line sits on p-other: it was named for p1 and is not on it.
        names = {entry["formula"] for entry in row["provenance"][RIVAL_SET]["rivals"]}
        assert "C3H6N2O2" not in names

    def test_a_reading_named_for_one_peak_is_no_rival_on_another(self):
        # Its own line sits on p2, where the search did not name it.
        search = SeededFrame(
            pd.DataFrame(
                [
                    _line("w", "C5H10O2", "p2", 0.9),
                    _line("r", "C4H6O3", "p2", 0.9),
                ]
            ),
            {("C5H10O2", "im-h"): "w", ("C4H6O3", "im-h"): "r"},
            {("C4H6O3", "im-h"): frozenset({"p1"})},
        )
        row = _winner(peak="p2")

        measure_rival_sets([row], RivalFrames(None, (search,), base=V1), V1)

        assert row["provenance"][RIVAL_SET]["rivals"] == []

    def test_a_named_readings_isotope_line_is_no_rival(self):
        # Named as a reading of p1's own mass: only its monoisotopic line on
        # p1 makes it one, as the inspector places a shortlist formula.
        search = SeededFrame(
            pd.DataFrame(
                [
                    _line("w", "C5H10O2", "p1", 0.9),
                    _line("r", "C4H6O3", "p0", 0.9, mz=199.0),
                    _line("r", "C4H6O3", "p1", 0.9, mz=200.0),
                ]
            ),
            {("C5H10O2", "im-h"): "w", ("C4H6O3", "im-h"): "r"},
            {("C4H6O3", "im-h"): frozenset({"p1"})},
        )
        row = _winner()

        measure_rival_sets([row], RivalFrames(None, (search,), base=V1), V1)

        assert row["provenance"][RIVAL_SET]["rivals"] == []

    def test_a_committed_readings_isotope_line_on_the_peak_is_a_rival(self):
        # Another winner's 13C line on p1: the peak may be that line, as
        # Stage A's arbitration counts any gated line on its peak.
        search = SeededFrame(
            pd.DataFrame(
                [
                    _line("w", "C5H10O2", "p1", 0.9),
                    _line("o", "C6H8O4", "p0", 0.88, mz=199.0),
                    _line("o", "C6H8O4", "p1", 0.88, mz=200.0),
                ]
            ),
            {("C5H10O2", "im-h"): "w", ("C6H8O4", "im-h"): "o"},
        )
        row = _winner()

        measure_rival_sets([row], RivalFrames(None, (search,), base=V1), V1)

        [entry] = row["provenance"][RIVAL_SET]["rivals"]
        assert (entry["formula"], entry["monoisotopic"]) == ("C6H8O4", False)
        assert row["provenance"]["candidate_density"] == 2

    def test_the_rows_own_neutral_through_another_channel_is_no_rival(self):
        search = SeededFrame(
            pd.DataFrame(
                [
                    _line("w", "C5H10O2", "p1", 0.9),
                    _line(
                        "n",
                        "C5H10O2",
                        "p1",
                        0.95,
                        mechanism="im-nh4",
                        ion_formula="C5H14NO2+",
                    ),
                ]
            ),
            {("C5H10O2", "im-h"): "w", ("C5H10O2", "im-nh4"): "n"},
        )
        row = _winner()

        measure_rival_sets([row], RivalFrames(None, (search,), base=V1), V1)

        assert row["provenance"][RIVAL_SET]["rivals"] == []
        assert row["provenance"]["candidate_density"] == 1

    def test_a_search_winner_the_shortlist_did_not_reach_states_no_share(self):
        # Its set is the named rivals alone, part of its field: the density
        # reads it, a share of all of it would not be one.
        frames, parts = _frames()
        frames = RivalFrames(None, (parts["search"],), base=V1)
        row = _winner()

        measure_rival_sets([row], frames, V1)

        provenance = row["provenance"]
        assert provenance["candidate_density"] == 2
        assert "confidence" not in provenance and "is_tie" not in provenance
        assert MEASURED_SHORTLIST not in provenance

    def test_a_row_no_frame_measured_keeps_what_its_stage_recorded(self):
        frames, _ = _frames()
        row = _winner(peak="p-unseen")
        row["provenance"]["candidate_density"] = 3

        summary = measure_rival_sets([row], frames, V1)

        assert row["provenance"]["candidate_density"] == 3
        assert RIVAL_SET not in row["provenance"]
        assert summary["unmeasured"] == 1


def _stage_a_frame(fits: dict[str, float]) -> pd.DataFrame:
    """Two list compounds on one peak, as Stage A's gated frame holds them."""
    rows = []
    for index, (formula, fit) in enumerate(fits.items()):
        rows.append(
            {
                "target_isotope_id": f"iso-{index}",
                "target_ion_id": f"ion-{index}",
                "target_compound_id": f"tc-{index}",
                "target_compound_formula": formula,
                "target_ion_formula": f"{formula}H+",
                "target_isotope_formula": f"{formula}H+",
                "ionization_mechanism_id": "im-h",
                "ionization_mechanism": "[M+H]+",
                "mz": 181.07,
                "sample_peak_id": "p1",
                "sample_peak_mz": 181.07,
                "sample_peak_intensity": 1000.0,
                "match_score": fit,
                "match_mz_error": 0.1 * (index + 1),
                "match_abundance_error": 0.0,
                "relative_abundance": 1.0,
            }
        )
    return pd.DataFrame(rows)


class TestAStageAPeakAtTheModelItsCapsRead:
    """#3366: on a run that takes v2, a Stage A peak's confidence, tie and
    density are read at v2."""

    FITS = {"C6H12O6": 0.9, "C7H16O5": 0.6}

    def _row(self):
        frame = _stage_a_frame(self.FITS)
        [row] = [
            row
            for row in invert_matches_to_peak_assignments(
                frame,
                sample_item_id="si",
                peak_assignment_run_id="run",
                candidate_threshold=0.45,
                assigned_threshold=0.75,
                calibration=None,
            )
            if row["role"] == "M0"
        ]
        return frame, row

    def test_at_v1_the_set_reads_what_stage_a_arbitrated(self):
        frame, row = self._row()
        stage_a = {
            key: row["provenance"][key]
            for key in ("confidence", "n_candidates", "is_tie", "candidate_density")
        }

        measure_rival_sets([row], RivalFrames(frame, (), base=V1), V1)

        assert {key: row["provenance"][key] for key in stage_a} == stage_a
        [entry] = row["provenance"][RIVAL_SET]["rivals"]
        assert (entry["formula"], entry["frame"]) == ("C7H16O5", FRAME_KNOWN)

    def test_at_v2_they_move_with_the_fits(self, monkeypatch):
        frame, row = self._row()
        assert row["provenance"]["candidate_density"] == 1
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            lambda scored, _model: scored["target_compound_formula"].map(
                {"C6H12O6": 0.8, "C7H16O5": 0.78}
            ),
        )
        row["fit_score"] = 0.8

        measure_rival_sets([row], RivalFrames(frame, (), base=V1), V2)

        provenance = row["provenance"]
        assert provenance["candidate_density"] == 2
        assert provenance["is_tie"] is True
        assert provenance["confidence"] == round(0.8 / 1.58, 4)
        assert provenance[RIVAL_SET]["model_version"] == 2

    def test_the_round_measures_the_sets_at_the_model_it_caps_at(self, monkeypatch):
        # Before any cap, at the refit's model where the round took it.
        order = []
        handed = {}

        def measure(rows, frames, model):
            order.append("sets")
            handed["model"] = model
            return {"model_version": model.version}

        monkeypatch.setattr(judging_loop, "measure_rival_sets", measure)
        for name in ("cap_second_channel", "cap_mass_gate", "cap_cross_channel"):
            monkeypatch.setattr(
                judging_loop, name, lambda *a, _n=name, **k: order.append(_n) or {}
            )
        monkeypatch.setattr(
            judging_loop,
            "apply_tiering",
            lambda *a, **k: order.append("tiering") or {},
        )
        monkeypatch.setattr(
            judging_loop, "find_envelope_claims", lambda *a, **k: ([], {})
        )
        judging = judging_loop.Judging(
            [],
            model=SimpleNamespace(version=1, envelope_tolerance=None),
            fallback_sigma_ppm=0.5,
            notation_by_id={},
            mz_tolerance_ppm=3.0,
            abundance_floor=0.01,
            max_alternatives=5,
            rival_frames=RivalFrames(None, (), base=V1),
        )
        judging.judged_at = V2

        judging.cap()
        judging.another_round()

        assert handed["model"] is V2
        assert order[0] == "sets" and "tiering" in order
        assert judging.tiering["rival_sets"] == {"model_version": 2}


class TestAListRowsGridRivals:
    """Verify 2: a list row's grid rivals are scored in the frame its own
    rivals are: the seeded pass the search's readings are measured in, at the
    run's model, and read off it at the model the caps read."""

    @pytest.mark.asyncio
    async def test_they_are_measured_in_the_pass_the_searchs_readings_are(self):
        named = {
            ("C7H16O5", "im-h"): frozenset({"p1"}),
            # A seed the search also commits is measured as its reading.
            ("C5H10O2", "im-h"): frozenset({"p2"}),
        }
        found = pd.DataFrame([_line("w", "C5H10O2", "p2", 0.9)])
        score_seeds = AsyncMock(
            return_value=(
                {("C5H10O2", "im-h"): "w", ("C7H16O5", "im-h"): "g"},
                {"w": 0.9, "g": 0.7},
                {},
                found,
            )
        )
        with patch.object(judging_loop, "score_seeds", score_seeds):
            fits, frame = await judging_loop._seeded_rescore(
                SimpleNamespace(sample_item_name="s"),
                None,
                {("C5H10O2", "im-h")},
                model=V1,
                resolution=None,
                named=named,
            )

        assert score_seeds.await_args.args[1] == set(named)
        assert score_seeds.await_args.kwargs["model"] is V1
        # Fits reach only the search's own rows; the named are rivals alone.
        assert fits == {("C5H10O2", "im-h"): 0.9}
        assert frame.placed_for == {("C7H16O5", "im-h"): frozenset({"p1"})}

    def test_the_names_come_from_the_search_and_the_list_election(self):
        matches = pd.DataFrame(
            [
                {
                    "mz": 200.0,
                    "formula": "C5H10O2",
                    "isotope_label": "M0",
                    CANDIDATE_RIVALS: [
                        {"formula": "C4H6O3", "ionization_mechanism": "[M+H]+"}
                    ],
                },
                {"mz": 201.0, "formula": "C5H10O2", "isotope_label": "13C"},
            ]
        )
        frame = pd.DataFrame(
            {"sample_peak_id": ["p1", "p2", "p9"], "mz": [181.0, 200.0, 201.0]}
        )

        named = named_rival_seeds(
            matches,
            frame,
            mechanism_id_by_notation={"[M+H]+": "im-h"},
            list_rivals={
                "p1": (
                    {"formula": "C7H16O5", "ionization_mechanism": "[M+H]+"},
                    {"formula": "C4H6O3", "ionization_mechanism": "[M+H]+"},
                )
            },
        )

        assert named == {
            ("C4H6O3", "im-h"): frozenset({"p1", "p2"}),
            ("C7H16O5", "im-h"): frozenset({"p1"}),
        }

    def test_read_off_the_set_the_rule_names_them_as_the_searchs(self):
        # A list row on p1: its known rival in Stage A's frame and the grid's,
        # measured in the seeded frame; both count, the grid's is named.
        frame = _stage_a_frame({"C6H12O6": 0.9, "C7H16O5": 0.88})
        seeded = SeededFrame(
            pd.DataFrame([_line("g", "C8H8O4", "p1", 0.87)]),
            {("C8H8O4", "im-h"): "g"},
            {("C8H8O4", "im-h"): frozenset({"p1"})},
        )
        row = {
            **_winner(formula="C6H12O6", source="database"),
            "provenance": {GRID_RIVALS: {"rivals": []}},
        }

        measure_rival_sets([row], RivalFrames(frame, (seeded,), base=V1), V1)

        assert row["provenance"]["candidate_density"] == 3
        detail = density_reason(row)["detail"]
        assert "1 of them from the formula search (C8H8O4)" in detail


def _isotope_frame() -> pd.DataFrame:
    """Ions whose monoisotopic line is not their first row: a bromine cluster
    listed heaviest first, a labelled ion whose bracketed line is its M0 and
    whose unlabelled remainder sits below it, an M0 line of no m/z, a merged
    low-resolution line, and two ions with no bracket-free formula at all,
    one with a line of no m/z."""
    rows = [
        ("br", "[81Br]2C2H3-", 173.9, "C2H3Br2-"),
        ("br", "C2H3Br2-", 169.9, "C2H3Br2-"),
        ("br", "[81Br]C2H3Br-", 171.9, "C2H3Br2-"),
        ("lab", "C9H16O7-", 235.0, "C9H16O7^N-"),
        ("lab", "[15N][13C]C8H16O7-", 237.0, "C9H16O7^N-"),
        ("lab", "[15N]C9H16O7-", 236.0, "C9H16O7^N-"),
        ("nan", "C5H9O2+", float("nan"), "C5H9O2+"),
        ("nan", "[13C]C4H9O2+", 102.1, "C5H9O2+"),
        ("lowres", "[13C]C5H11O2+/C6H11O2+", 115.1, "C6H11O2+"),
        ("lowres", "[18O]C6H11O+", 117.1, "C6H11O2+"),
        ("heavy", "[13C]C3H7+", 44.1, "C4H7+"),
        ("heavy", "[13C]2C2H7+", 45.1, "C4H7+"),
        ("unread", "[13C]C3H5+", float("nan"), "C4H5+"),
        ("unread", "[13C]2C2H5+", 43.1, "C4H5+"),
    ]
    frame = pd.DataFrame(
        [
            {
                "target_ion_id": ion,
                "target_isotope_formula": isotope,
                "target_isotope_id": f"{ion}:{isotope}",
                "mz": mz,
                "target_ion_formula": ion_formula,
                "position": position,
            }
            for position, (ion, isotope, mz, ion_formula) in enumerate(rows)
        ]
    )
    # Shuffled, and with the duplicated index a gated, scored frame can carry.
    frame = frame.sample(frac=1.0, random_state=7)
    frame.index = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    return frame


class TestTheMonoisotopicLineIsTheEnginesPick:
    """The set reads each ion's monoisotopic line as the engine does
    (``monoisotopic.monoisotopic_positions``, the row ``monoisotopic_row``
    picks from the ion's rows)."""

    def test_m0_by_ion_reads_the_same_rows(self):
        # The cases the frame states, by name: the lightest bromine line, the
        # labelled line over the remainder below it, the M0 by its formula
        # though its m/z is missing and so sorts last, the merged line that
        # names the M0, and an ion of no M0 line its lightest, a line of no
        # m/z after every line of one.
        frame = _isotope_frame()

        rows = rival_set.m0_by_ion(frame)

        assert {ion: row["position"] for ion, row in rows.items()} == {
            "br": 1,
            "lab": 5,
            "nan": 6,
            "lowres": 8,
            "heavy": 10,
            "unread": 13,
        }

    def test_a_list_rows_own_reading_is_its_ions_own_line(self):
        # On p1: the row's M0 line, its own 13C line at a better fit, a rival's
        # M0 line, and the 13C line of an ion whose M0 is on another peak.
        frame = _stage_a_frame({"C6H12O6": 0.7, "C7H16O5": 0.8, "C5H10O5": 0.5})
        frame.loc[2, "sample_peak_id"] = "p2"
        extra = frame.iloc[[0, 2]].copy()
        extra["target_isotope_id"] = ["iso-0-13c", "iso-2-13c"]
        extra["target_isotope_formula"] = ["[13C]C5H13O6+", "[13C]C4H11O5+"]
        extra["mz"] = [182.07, 152.06]
        extra["sample_peak_id"] = "p1"
        extra["match_score"] = [0.95, 0.75]
        frame = pd.concat([frame, extra], ignore_index=True)
        row = _winner(formula="C6H12O6", fit=0.7, source="database")

        measure_rival_sets([row], RivalFrames(frame, (), base=V1), V1)

        record = row["provenance"][RIVAL_SET]
        assert record["fit_score"] == 0.7
        assert {
            r["formula"]: (r["monoisotopic"], r["mz_error_ppm"])
            for r in record["rivals"]
        } == {"C7H16O5": (True, 0.2), "C5H10O5": (False, 0.3)}


class TestASearchReadingsLines:
    """A committed reading's lines count on a peak only where the gate paired
    them to it, as Stage A's lines do."""

    def test_a_line_the_gate_did_not_pair_to_the_peak_is_no_rival(self):
        ungated = _line("c", "C6H8O3", "p1", 0.88)
        ungated["sample_peak_intensity"] = 0.0
        gated = _line("g", "C7H8O2", "p1", 0.6)
        gated["match_mz_error"] = -0.4
        seeded = SeededFrame(
            pd.DataFrame([_line("w", "C5H10O2", "p1", 0.9), ungated, gated]),
            {
                ("C5H10O2", "im-h"): "w",
                ("C6H8O3", "im-h"): "c",
                ("C7H8O2", "im-h"): "g",
            },
        )
        row = _winner()

        measure_rival_sets([row], RivalFrames(None, (seeded,), base=V1), V1)

        assert row["provenance"]["candidate_density"] == 1
        [rival] = row["provenance"][RIVAL_SET]["rivals"]
        assert (rival["formula"], rival["mz_error_ppm"]) == ("C7H8O2", -0.4)
