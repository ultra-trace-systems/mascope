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
from mascope_backend.api.new.peak_assignments.cut_contenders import (
    CONTENDERS_PAST_CAP,
    FORMULA_ONLY_PAST_CAP,
)
from mascope_backend.api.new.peak_assignments.engine import (
    GRID_RIVALS,
    MEASURED_SHORTLIST,
    RIVALS_PAST_CAP,
    invert_matches_to_peak_assignments,
)
from mascope_backend.api.new.peak_assignments.identification_level import (
    LevelContext,
    level_of,
    shortlist,
)
from mascope_backend.api.new.peak_assignments.rival_set import (
    CONTENDERS_NOT_HELD,
    FRAME_KNOWN,
    FRAME_SEARCH,
    RIVAL_SET,
    RIVALS_KEPT,
    RivalFrames,
    SeededFrame,
    fit_tied_seeds,
    measure_rival_sets,
    named_rival_seeds,
)
from mascope_backend.api.new.peak_assignments.row_terms import REFERENCE_IDENTITIES_COL
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


class TestARadicalIsNoRivalToAMolecule:
    """Decision MAS-979's radical rule: the tiering pass holds no radical at
    assigned, whoever names it, so a molecule's set leaves radicals out, as a
    list hit's rivals and the cross-channel pass do."""

    @staticmethod
    def _frames(winner: str, rival: str) -> RivalFrames:
        search = SeededFrame(
            pd.DataFrame(
                [_line("w", winner, "p1", 0.9), _line("r", rival, "p1", 0.89)]
            ),
            {(winner, "im-h"): "w", (rival, "im-h"): "r"},
            {(rival, "im-h"): frozenset({"p1"})},
        )
        return RivalFrames(None, (search,), base=V1)

    def test_a_molecules_set_leaves_a_radical_out(self):
        # C4H7O3 reads DBE 1.5: an unpaired electron.
        row = _winner()
        measure_rival_sets([row], self._frames("C5H10O2", "C4H7O3"), V1)
        assert row["provenance"]["candidate_density"] == 1
        assert row["provenance"][RIVAL_SET]["rivals"] == []

    def test_a_molecule_rival_is_counted(self):
        row = _winner()
        measure_rival_sets([row], self._frames("C5H10O2", "C4H6O3"), V1)
        assert row["provenance"]["candidate_density"] == 2

    def test_a_radicals_set_holds_every_other_ion(self):
        row = _winner(formula="C5H9O2")
        measure_rival_sets([row], self._frames("C5H9O2", "C4H6O3"), V1)
        assert row["provenance"]["candidate_density"] == 2


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
        # Which of them the evidence could not separate, as the set counted.
        assert {
            entry["formula"]: entry["unseparated"] for entry in record["rivals"]
        } == {"C4H6O3": True, "C6H14O": False}
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
        # Both rivals stay in the set, each marked as one the evidence parted.
        assert {
            entry["formula"]: entry["unseparated"]
            for entry in row["provenance"][RIVAL_SET]["rivals"]
        } == {"C4H6O3": False, "C6H14O": False}
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

    def test_the_set_names_ten_rivals_and_counts_the_rest_for_the_level(self):
        # Twelve other ions on the winner's peak, each fitting less well than
        # the one before it, all of them parted from the winner by mass.
        others = [
            "C4H6O3", "C6H14O", "C7H16O5", "C8H8O4", "C5H10O5", "C3H6O3",
            "C7H8O3", "C6H10O5", "C4H8O4", "C5H8O4", "C9H10O3", "C4H8O2",
        ]  # fmt: skip
        fits = {formula: round(0.6 - 0.04 * n, 2) for n, formula in enumerate(others)}
        search = SeededFrame(
            pd.DataFrame(
                [_line("w", "C5H10O2", "p1", 0.9)]
                + [_line(f"o{n}", f, "p1", fits[f]) for n, f in enumerate(others)]
            ),
            {("C5H10O2", "im-h"): "w"}
            | {(f, "im-h"): f"o{n}" for n, f in enumerate(others)},
        )
        frames = RivalFrames(None, (search,), base=V1, shortlists={"p1": ()})
        row = _winner()

        measure_rival_sets([row], frames, V1)

        record = row["provenance"][RIVAL_SET]
        assert RIVALS_KEPT == 10
        # Best evidence first, the ten it names; the two past them counted.
        assert [entry["formula"] for entry in record["rivals"]] == others[:10]
        assert record["rivals_unlisted"] == 2
        # The level tests the ten and leaves the two it was not given open.
        rivals, unlisted = shortlist(row)
        assert len(rivals) == 10 and unlisted == 2
        level = level_of(
            row,
            [],
            LevelContext(neutral_channels={}, notation_by_id={"im-h": "[M+H]+"}),
        )
        assert level["level"] == "5a"
        assert level["rivals_unlisted"] == 2
        assert "two rivals past the stored shortlist, not tested" in level["evidence"]


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

    def _row(self, frame=None, role="M0"):
        frame = _stage_a_frame(self.FITS) if frame is None else frame
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
            if row["role"] == role
        ]
        return frame, row

    @staticmethod
    def _with_mirror_copies(frame: pd.DataFrame, fits: list[float]) -> pd.DataFrame:
        """The frame with a reference mirror's copy of each library row on its
        peak, each fitting as ``fits`` says: the same neutral through the same
        channel, from an ion of the mirror's own."""
        mirror = frame.copy()
        mirror["target_ion_id"] = "mirror-" + mirror["target_ion_id"]
        mirror["target_isotope_id"] = "mirror-" + mirror["target_isotope_id"]
        mirror["target_compound_id"] = None
        mirror[REFERENCE_IDENTITIES_COL] = [[{"name": "ref"}] for _ in fits]
        mirror["match_score"] = fits
        frame = frame.assign(**{REFERENCE_IDENTITIES_COL: None})
        return pd.concat([frame, mirror], ignore_index=True)

    def test_a_mirror_copy_stands_in_for_no_library_rows_reading(self):
        # Each copy fits better than the library row it restates. Stage A
        # arbitrates the library rows, and so does the set: the row's own
        # reading and its rival are the library's, at the library's fits.
        frame = self._with_mirror_copies(_stage_a_frame(self.FITS), [0.95, 0.85])
        frame, row = self._row(frame)
        stage_a = {
            key: row["provenance"][key]
            for key in ("confidence", "n_candidates", "is_tie", "candidate_density")
        }
        assert stage_a["confidence"] == round(0.9 / 1.5, 4)

        measure_rival_sets([row], RivalFrames(frame, (), base=V1), V1)

        assert {key: row["provenance"][key] for key in stage_a} == stage_a
        record = row["provenance"][RIVAL_SET]
        assert record["fit_score"] == 0.9
        assert [(r["formula"], r["fit_score"]) for r in record["rivals"]] == [
            ("C7H16O5", 0.6)
        ]

    def _isotopologue_frame(self) -> pd.DataFrame:
        """The list compound's M0 alone on p1, and on p2 its 13C line beside
        another compound's M0 line, which it outfits."""
        frame = _stage_a_frame(self.FITS)
        frame.loc[1, ["sample_peak_id", "mz", "sample_peak_mz"]] = [
            "p2",
            182.07,
            182.07,
        ]
        line = frame.iloc[[0]].copy()
        line["target_isotope_id"] = "iso-0-13c"
        line["target_isotope_formula"] = "[13C]C5H13O6+"
        line[["sample_peak_id", "mz", "sample_peak_mz"]] = ["p2", 182.07, 182.07]
        line["match_score"] = 0.85
        line["relative_abundance"] = 0.07
        return pd.concat([frame, line], ignore_index=True)

    def test_an_isotopologue_row_reads_its_peaks_share_at_v1_as_stage_a(self):
        frame, row = self._row(self._isotopologue_frame(), role="iso_child")
        assert row["sample_peak_id"] == "p2"
        stage_a = {
            key: row["provenance"][key]
            for key in ("confidence", "n_candidates", "is_tie")
        }
        assert stage_a == {
            "confidence": round(0.85 / 1.45, 4),
            "n_candidates": 2,
            "is_tie": False,
        }

        measure_rival_sets([row], RivalFrames(frame, (), base=V1), V1)

        assert {key: row["provenance"][key] for key in stage_a} == stage_a
        # A line predicted from its ion was never searched: no density, no set.
        assert "candidate_density" not in row["provenance"]
        assert RIVAL_SET not in row["provenance"]

    def test_an_isotopologue_rows_share_and_tie_move_at_v2(self, monkeypatch):
        frame, row = self._row(self._isotopologue_frame(), role="iso_child")
        monkeypatch.setattr(
            rival_set,
            "score_frame_at",
            lambda scored, _model: scored["target_isotope_id"].map(
                {"iso-0": 0.8, "iso-0-13c": 0.7, "iso-1": 0.68}
            ),
        )

        summary = measure_rival_sets([row], RivalFrames(frame, (), base=V1), V2)

        provenance = row["provenance"]
        assert provenance["confidence"] == round(0.7 / 1.38, 4)
        assert provenance["is_tie"] is True
        assert provenance["n_candidates"] == 2
        assert "candidate_density" not in provenance
        assert RIVAL_SET not in provenance
        # The run counts the monoisotopic rows it measured, not this one.
        assert summary["measured"] == 0 and summary["unmeasured"] == 0

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


#: A formula whose fit can tie a list row's while its plausibility (0.5) puts
#: its evidence far under the row's: named by the search by its fit alone.
TWIN = "C2H4N8O2"


def _list_row_with_twin(*, fit_only: bool = True, twin_fit: float = 0.9):
    """A list row on p1 with a known rival, the grid's evidence rival and the
    grid's fit-tied twin, each measured as the run measures it."""
    frame = _stage_a_frame({"C6H12O6": 0.9, "C7H16O5": 0.88})
    named = {
        ("C8H8O4", "im-h"): frozenset({"p1"}),
        (TWIN, "im-h"): frozenset({"p1"}),
    }
    seeded = SeededFrame(
        pd.DataFrame(
            [
                _line("g", "C8H8O4", "p1", 0.87),
                _line("t", TWIN, "p1", twin_fit, ion_formula="C2H5N8O2+"),
            ]
        ),
        {("C8H8O4", "im-h"): "g", (TWIN, "im-h"): "t"},
        named,
        {(TWIN, "im-h"): frozenset({"p1"})} if fit_only else {},
    )
    row = {
        **_winner(formula="C6H12O6", source="database"),
        "provenance": {GRID_RIVALS: {"rivals": []}},
    }
    return row, RivalFrames(frame, (seeded,), base=V1)


def _the_set_without_the_twin():
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
    return row


class TestAListRowsFitTiedRivals:
    """MAS-975 (#3285): the grid's formulas a list reading's fit alone could
    not separate are measured in the frame its other rivals are and recorded
    apart, for the level; the arbitration and the density read as before."""

    def test_they_are_recorded_apart_and_read_by_no_count(self):
        row, frames = _list_row_with_twin()
        before = _the_set_without_the_twin()

        measure_rival_sets([row], frames, V1)

        provenance, expected = row["provenance"], before["provenance"]
        for key in ("candidate_density", "confidence", "n_candidates", "is_tie"):
            assert provenance[key] == expected[key], key
        record = provenance[RIVAL_SET]
        assert record["rivals"] == expected[RIVAL_SET]["rivals"]
        assert record["rivals_unlisted"] == expected[RIVAL_SET]["rivals_unlisted"]
        assert record["fit_tied"] == [
            {
                "formula": TWIN,
                "ion_formula": "C2H5N8O2+",
                "ionization_mechanism_id": "im-h",
                "fit_score": 0.9,
                "mz_error_ppm": 0.2,
                "monoisotopic": True,
                "frame": FRAME_SEARCH,
                "unseparated": False,
            }
        ]
        assert record["fit_tied_unlisted"] == 0
        # And the tier reason that names the search's rivals names the same.
        assert density_reason(row) == density_reason(before)

    def test_the_level_lists_one_open(self):
        row, frames = _list_row_with_twin()
        measure_rival_sets([row], frames, V1)

        rivals, _ = shortlist(row)

        assert TWIN in {rival.formula for rival in rivals}
        record = level_of(row, [], LevelContext({}, {"im-h": "[M+H]+"}))
        [entry] = [e for e in record["rivals"] if e["formula"] == TWIN]
        assert entry["test"] == "open"
        assert entry["held_by"] == ["plausibility"]

    def test_named_by_the_evidence_it_is_a_rival_of_the_arbitration(self):
        # The same reading, not marked fit-only: it competes, as before.
        row, frames = _list_row_with_twin(fit_only=False)

        measure_rival_sets([row], frames, V1)

        record = row["provenance"][RIVAL_SET]
        assert TWIN in {entry["formula"] for entry in record["rivals"]}
        assert "fit_tied" not in record
        before = _the_set_without_the_twin()["provenance"]["n_candidates"]
        assert row["provenance"]["n_candidates"] == before + 1

    def test_the_set_names_ten_and_counts_the_rest(self):
        frame = _stage_a_frame({"C6H12O6": 0.9})
        twins = [f"C{n}H4N8O2" for n in range(2, 15)]
        seeded = SeededFrame(
            pd.DataFrame(
                [_line(f"t{n}", twin, "p1", 0.9) for n, twin in enumerate(twins)]
            ),
            {(twin, "im-h"): f"t{n}" for n, twin in enumerate(twins)},
            {(twin, "im-h"): frozenset({"p1"}) for twin in twins},
            {(twin, "im-h"): frozenset({"p1"}) for twin in twins},
        )
        row = {
            **_winner(formula="C6H12O6", source="database"),
            "provenance": {GRID_RIVALS: {"rivals": []}},
        }

        measure_rival_sets([row], RivalFrames(frame, (seeded,), base=V1), V1)

        record = row["provenance"][RIVAL_SET]
        assert len(record["fit_tied"]) == RIVALS_KEPT
        assert record["fit_tied_unlisted"] == len(twins) - RIVALS_KEPT
        assert row["provenance"]["candidate_density"] == 1

    def test_one_ion_read_twice_is_listed_once_at_its_best_fit(self):
        # Two splits of one ion, both named by their fit alone: one rival.
        frame = _stage_a_frame({"C6H12O6": 0.9})
        split = ("C2H2N7O2", "im-nh4")
        seeded = SeededFrame(
            pd.DataFrame(
                [
                    _line(
                        "b",
                        split[0],
                        "p1",
                        0.9,
                        mechanism="im-nh4",
                        ion_formula="C2H5N8O2+",
                    ),
                    _line("a", TWIN, "p1", 0.6, ion_formula="C2H5N8O2+"),
                ]
            ),
            {(TWIN, "im-h"): "a", split: "b"},
            {(TWIN, "im-h"): frozenset({"p1"}), split: frozenset({"p1"})},
            {(TWIN, "im-h"): frozenset({"p1"}), split: frozenset({"p1"})},
        )
        row = {
            **_winner(formula="C6H12O6", source="database"),
            "provenance": {GRID_RIVALS: {"rivals": []}},
        }

        measure_rival_sets([row], RivalFrames(frame, (seeded,), base=V1), V1)

        [entry] = row["provenance"][RIVAL_SET]["fit_tied"]
        assert (entry["formula"], entry["fit_score"]) == (split[0], 0.9)

    def test_a_fit_only_reading_of_a_rivals_ion_is_not_listed_twice(self):
        # The known set already measures C7H16O5 on the peak; the search's
        # reading of the same neutral is no second rival.
        frame = _stage_a_frame({"C6H12O6": 0.9, "C7H16O5": 0.88})
        seeded = SeededFrame(
            pd.DataFrame([_line("t", "C7H16O5", "p1", 0.95)]),
            {("C7H16O5", "im-h"): "t"},
            {("C7H16O5", "im-h"): frozenset({"p1"})},
            {("C7H16O5", "im-h"): frozenset({"p1"})},
        )
        row = {
            **_winner(formula="C6H12O6", source="database"),
            "provenance": {GRID_RIVALS: {"rivals": []}},
        }

        measure_rival_sets([row], RivalFrames(frame, (seeded,), base=V1), V1)

        assert "fit_tied" not in row["provenance"][RIVAL_SET]

    def test_the_seeds_named_by_fit_alone_carry_their_peaks(self):
        search = SimpleNamespace(
            frame=pd.DataFrame({"sample_peak_id": ["p1", "p2"], "mz": [181.0, 200.0]}),
            mechanism_id_by_notation={"[M+H]+": "im-h"},
            election=SimpleNamespace(
                fit_tied_by_peak={
                    "p1": ({"formula": TWIN, "ionization_mechanism": "[M+H]+"},),
                    "p2": (
                        {"formula": TWIN, "ionization_mechanism": "[M+H]+"},
                        {"formula": "C8H8O4", "ionization_mechanism": "[M+H]+"},
                    ),
                }
            ),
        )

        found = fit_tied_seeds(search)

        assert found == {
            (TWIN, "im-h"): frozenset({"p1", "p2"}),
            ("C8H8O4", "im-h"): frozenset({"p2"}),
        }

    def test_a_search_without_an_election_names_none(self):
        search = SimpleNamespace(
            frame=pd.DataFrame({"sample_peak_id": [], "mz": []}),
            mechanism_id_by_notation={},
            election=None,
        )

        assert fit_tied_seeds(search) == {}

    @pytest.mark.asyncio
    async def test_they_are_measured_in_the_seeded_pass_and_marked(self):
        named = {("C8H8O4", "im-h"): frozenset({"p1"})}
        fit_only = {
            (TWIN, "im-h"): frozenset({"p1"}),
            # A seed the search commits is measured as its reading.
            ("C5H10O2", "im-h"): frozenset({"p1"}),
        }
        score_seeds = AsyncMock(
            return_value=(
                {
                    ("C5H10O2", "im-h"): "w",
                    ("C8H8O4", "im-h"): "g",
                    (TWIN, "im-h"): "t",
                },
                {"w": 0.9, "g": 0.7, "t": 0.6},
                {},
                pd.DataFrame([_line("w", "C5H10O2", "p2", 0.9)]),
            )
        )
        with patch.object(judging_loop, "score_seeds", score_seeds):
            _fits, frame = await judging_loop._seeded_rescore(
                SimpleNamespace(sample_item_name="s"),
                None,
                {("C5H10O2", "im-h")},
                model=V1,
                resolution=None,
                named=named,
                fit_only=fit_only,
            )

        assert score_seeds.await_args.args[1] == {
            ("C5H10O2", "im-h"),
            ("C8H8O4", "im-h"),
            (TWIN, "im-h"),
        }
        assert frame.placed_for == {
            ("C8H8O4", "im-h"): frozenset({"p1"}),
            (TWIN, "im-h"): frozenset({"p1"}),
        }
        assert frame.fit_only == {(TWIN, "im-h"): frozenset({"p1"})}


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


def _level(row: dict) -> dict:
    return level_of(
        row, [], LevelContext(neutral_channels={}, notation_by_id={"im-h": "[M+H]+"})
    )


class TestTheContendersTheCapCut:
    """An untargeted winner's losing contenders the cap on its stored
    alternatives cut (``cut_contenders.CONTENDERS_PAST_CAP``) are rivals of its set
    where a frame measured them on the peak: the level tests them there, or
    counts them once past the set's ten. One no frame measured the set cannot
    vouch for, and it records how many (MAS-1185)."""

    def test_cut_contenders_the_set_measured_are_tested_and_read_4(self):
        search = SeededFrame(
            pd.DataFrame(
                [
                    _line("w", "C5H10O2", "p1", 0.9),
                    _line("a", "C4H6O3", "p1", 0.3),
                    _line("b", "C7H16O5", "p1", 0.2, mz=200.0),
                ]
            ),
            {
                ("C5H10O2", "im-h"): "w",
                ("C4H6O3", "im-h"): "a",
                ("C7H16O5", "im-h"): "b",
            },
        )
        row = _winner()
        row["alternatives"] = None
        row["provenance"] |= {
            RIVALS_PAST_CAP: 2,
            CONTENDERS_PAST_CAP: ["C4H6O3", "C7H16O5"],
        }
        frames = RivalFrames(None, (search,), base=V1, shortlists={"p1": ()})

        measure_rival_sets([row], frames, V1)

        record = row["provenance"][RIVAL_SET]
        assert CONTENDERS_NOT_HELD not in record
        assert shortlist(row)[1] == 0
        assert _level(row)["level"].startswith("4")
        assert row["provenance"][RIVALS_PAST_CAP] == 2

    def test_a_cut_contender_whose_line_the_gate_dropped_is_not_held(self):
        # The finder put C6H8O3's line on p1, the seeded re-score paired it
        # to no peak: the set does not hold it, and the level counts it.
        ungated = _line("c", "C6H8O3", "p1", 0.88)
        ungated["sample_peak_intensity"] = 0.0
        search = SeededFrame(
            pd.DataFrame([_line("w", "C5H10O2", "p1", 0.9), ungated]),
            {("C5H10O2", "im-h"): "w", ("C6H8O3", "im-h"): "c"},
        )
        row = _winner()
        row["provenance"] |= {RIVALS_PAST_CAP: 1, CONTENDERS_PAST_CAP: ["C6H8O3"]}
        frames = RivalFrames(None, (search,), base=V1, shortlists={"p1": ()})

        measure_rival_sets([row], frames, V1)

        assert row["provenance"][RIVAL_SET][CONTENDERS_NOT_HELD] == 1
        level = _level(row)
        assert level["level"] == "5a"
        assert level["rivals_unlisted"] == 1
        assert "one rival past the stored shortlist, not tested" in level["evidence"]

    def test_cut_contenders_past_the_sets_ten_are_counted_once(self):
        # Twelve measured rivals, the cap cut the two the set counts past its
        # ten and one formula-only entry: three unlisted, not five.
        others = [
            "C4H6O3", "C6H14O", "C7H16O5", "C8H8O4", "C5H10O5", "C3H6O3",
            "C7H8O3", "C6H10O5", "C4H8O4", "C5H8O4", "C9H10O3", "C4H8O2",
        ]  # fmt: skip
        fits = {formula: round(0.6 - 0.04 * n, 2) for n, formula in enumerate(others)}
        search = SeededFrame(
            pd.DataFrame(
                [_line("w", "C5H10O2", "p1", 0.9)]
                + [_line(f"o{n}", f, "p1", fits[f]) for n, f in enumerate(others)]
            ),
            {("C5H10O2", "im-h"): "w"}
            | {(f, "im-h"): f"o{n}" for n, f in enumerate(others)},
        )
        row = _winner()
        row["provenance"] |= {
            RIVALS_PAST_CAP: 3,
            FORMULA_ONLY_PAST_CAP: 1,
            CONTENDERS_PAST_CAP: sorted(others[10:]),
        }
        frames = RivalFrames(None, (search,), base=V1, shortlists={"p1": ()})

        measure_rival_sets([row], frames, V1)

        record = row["provenance"][RIVAL_SET]
        assert record["rivals_unlisted"] == 2
        assert CONTENDERS_NOT_HELD not in record
        rivals, unlisted = shortlist(row)
        assert len(rivals) == 10 and unlisted == 3
        assert _level(row)["rivals_unlisted"] == 3
