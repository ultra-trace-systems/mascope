"""The batch's decisions a sample's run reads (``batch_decisions.py``, MAS-985).

The pure parts: where a rung's record places a sample, what each record
decides for it, what the run records of what it read, and how a listed
compound and a strip are read on the run's rows as the batch's own claims
read a member. The stage list's side: which decisions each path reads, the
stage each enters at, and that the elect stage reads them in that order. The
reads over a real ledger are the integration suites' (the water ladder's,
the known species', the strip's).
"""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import batch_decisions, pipeline
from mascope_backend.api.new.peak_assignments.batch_decisions import (
    KIND_ISOTOPOLOGUE_STRIP,
    KIND_KNOWN_SPECIES,
    KIND_LISTED_COMPOUND,
    KIND_SERIES,
    KIND_WATER_RUNG,
    KINDS,
    LISTED_ELECTED,
    LISTED_LIFTED,
    PEAK_ABSENT,
    STRIP_KEPT_CARRIES_LINES,
    STRIP_KEPT_CLAIMED,
    STRIP_KEPT_PARENT,
    STRIP_READ,
    BatchDecisions,
    ListedDecision,
    StripDecision,
    cite_listed,
    elect_listed,
    listed_decision,
    listed_from,
    read_strips,
    rung_from,
    segment_of,
    strip_from,
    twin_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_lines import BatchLine
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.known_species import (
    ALREADY,
    KEPT_CARRIES_LINES,
    KEPT_CLAIMED_OTHERWISE,
    KEPT_HAND_ASSIGNED,
    KEPT_LINE_OF_ANOTHER,
    KEPT_LISTED,
    KEPT_ON_TWIN,
    KEPT_TARGET_LIBRARY,
)
from mascope_backend.api.new.peak_assignments.prepasses import SourceIonHits
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    REASON_BATCH_WATER_LADDER,
    BatchSpecies,
)
from mascope_tools.composition.reagents import (
    ReagentCluster,
    ReagentHit,
    ion_mz,
    water_ladders,
)


BROMIDE = water_ladders("BR")[0]
LADDERS = {BROMIDE.core.label: BROMIDE}


def _rung_record(**extra) -> dict:
    """A water-ladder record of Br-.(H2O)4's monoisotopic line, passed in the
    second of two segments: seconds 0-100 and 500-600."""
    return {
        "rung": {
            "core": BROMIDE.core.label,
            "waters": 4,
            "track": "M0",
            "ion_formula": BROMIDE.rung(4).formula + "-",
        },
        "segments": [
            {"segment": 0, "start": 0.0, "end": 100.0, "presence": 0.2},
            {
                "segment": 1,
                "start": 500.0,
                "end": 600.0,
                "presence": 0.9,
                "decoy_presence": 0.1,
            },
        ],
        "passed_in": [1],
        "claimed_samples": ["claimed"],
        "held": None,
        "batch_peak_run_id": "run-1",
        "version": 3,
        **extra,
    }


class TestTheSegmentASampleIsIn:
    @pytest.mark.parametrize("acquired", [500.0, 550.0, 600.0])
    def test_a_sample_acquired_inside_a_passing_segment_is_in_it(self, acquired):
        placed, entry = segment_of(_rung_record(), "s", acquired)
        assert placed is True
        assert entry["segment"] == 1

    @pytest.mark.parametrize(
        "acquired",
        [
            50.0,  # in a segment the rung did not pass in
            300.0,  # in the gap between two
            700.0,  # after the last
            -10.0,  # before the first
            None,  # at no time
        ],
    )
    def test_a_sample_anywhere_else_is_in_none(self, acquired):
        assert segment_of(_rung_record(), "s", acquired) == (False, None)

    @pytest.mark.parametrize("acquired", [50.0, 300.0, 700.0, -10.0])
    def test_a_claimed_sample_s_own_time_decides_alone(self, acquired):
        # ``claimed_samples`` carries an earlier measurement's samples over,
        # whatever segment this one puts them in.
        assert segment_of(_rung_record(), "claimed", acquired) == (False, None)
        placed, entry = segment_of(_rung_record(), "claimed", 550.0)
        assert (placed, entry["segment"]) == (True, 1)

    def test_a_sample_the_ladder_claimed_is_in_one(self):
        # The untimed segment's samples, and those of a record from before
        # the records kept a segment's times.
        assert segment_of(_rung_record(), "claimed", None) == (True, None)
        old = _rung_record()
        for entry in old["segments"]:
            del entry["start"], entry["end"]
        assert segment_of(old, "claimed", 550.0) == (True, None)
        assert segment_of(old, "s", 550.0) == (False, None)


class TestARungForASample:
    def test_a_rung_its_segment_passed_is_claimed_on_the_ladders_word(self):
        one = rung_from(_rung_record(), "bp-4", "s", 550.0, LADDERS)

        assert one.cluster == BROMIDE.rung(4)
        assert one.reason == REASON_BATCH_WATER_LADDER
        assert one.record == {
            "batch_peak_id": "bp-4",
            "core": BROMIDE.core.label,
            "waters": 4,
            "track": "M0",
            "segment": 1,
            "presence": 0.9,
            "decoy_presence": 0.1,
            "references": one.record["references"],
            "batch_peak_run_id": "run-1",
            "version": 3,
        }
        assert one.record["references"]

    @pytest.mark.parametrize(
        "record, acquired",
        [
            (_rung_record(held="curated"), 550.0),
            (_rung_record(rung={**_rung_record()["rung"], "track": "81Br"}), 550.0),
            (_rung_record(rung={**_rung_record()["rung"], "core": "[I]-"}), 550.0),
            (_rung_record(rung={**_rung_record()["rung"], "waters": 99}), 550.0),
            (_rung_record(), 700.0),
        ],
        ids=["held", "heavier track", "no such core", "past the ladder", "outside"],
    )
    def test_no_rung_is_claimed_otherwise(self, record, acquired):
        assert rung_from(record, "bp-4", "s", acquired, LADDERS) is None


def _species(formula: str, charge: int = -1, **record) -> BatchSpecies:
    return BatchSpecies(ReagentCluster(formula, charge, formula, "cluster"), record)


class TestWhatARunReadsAndRecords:
    def test_a_known_species_speaks_for_its_ion_before_a_rung(self):
        species = _species("BrH2O", version=2)
        rung = _species("BrH2O", reason=REASON_BATCH_WATER_LADDER)
        other = _species("BrH4O2")
        decisions = BatchDecisions(
            sample_batch_id="b", species=(species,), rungs=(rung, other)
        )
        assert decisions.source_ions() == [species, other]

    def test_the_run_records_each_stage_it_read_and_the_versions(self):
        listed = ListedDecision("p", None, {}, {"version": 2})
        strip = StripDecision(
            "x", "p", "C", "m", BatchLine("13C", 1.0, 0.1, "bx", "bp", {}, version=5)
        )
        decisions = BatchDecisions(
            sample_batch_id="b",
            kinds=frozenset(KINDS),
            stages={
                "known_species": {"version": 2, "state": "current"},
                "isotope_lines": {"version": 5, "state": "stale"},
                "series": {"version": 4, "state": "current"},
            },
            species=(_species("A", version=2),),
            listed={"p": listed},
            strips={"x": strip},
            series={"p1": {"version": 4}, "p2": {"version": None}},
        )

        record = decisions.record({"strips": {"read": 1}})

        assert record == {
            "stages": {
                "known_species": {
                    "version": 2,
                    "state": "current",
                    "records": 2,
                    "record_versions": [2],
                },
                "water_ladders": {
                    "version": 0,
                    "state": "not_run",
                    "records": 0,
                    "record_versions": [],
                },
                "isotope_lines": {
                    "version": 5,
                    "state": "stale",
                    "records": 1,
                    "record_versions": [5],
                },
                "series": {
                    "version": 4,
                    "state": "current",
                    "records": 2,
                    "record_versions": [4, None],
                },
            },
            "applied": {"strips": {"read": 1}},
        }

    def test_a_decoy_records_only_what_it_read(self):
        decisions = BatchDecisions(
            sample_batch_id="b",
            kinds=pipeline.batch_kinds(pipeline.DECOY, decoy=True),
        )
        assert set(decisions.record()["stages"]) == {"known_species", "water_ladders"}

    def test_a_sample_in_no_batch_records_nothing(self):
        assert BatchDecisions(kinds=frozenset(KINDS)).record() is None


D4, D4_ION, OTHER = "C8H24O4Si4", "C8H25O4Si4+", "C9H24N2O4Si3"


def _listed_record(**extra) -> dict:
    return {
        "species": {
            "kind": "listed",
            "formula": D4,
            "ion_formula": D4_ION,
            "ionization_mechanism_id": "m-h",
            "listing": {"tags": ["background"]},
        },
        "verdict": "present",
        "held": None,
        "files": 6,
        "present": 5,
        "test_held": 2,
        "batch_peak_run_id": "run-1",
        "version": 4,
        **extra,
    }


ANCHOR = SimpleNamespace(
    batch_peak_id="bp-d",
    candidates=[
        {
            "formula": D4,
            "ion_formula": D4_ION,
            "ionization_mechanism_id": "m-h",
            "source": "database",
            "target_compound_id": "tc-1",
        }
    ],
)


class TestAListedCompoundForASample:
    def test_a_listed_compound_decided_present_is_read_with_its_registry(self):
        one = listed_from(ANCHOR, "p", _listed_record())

        assert one.sample_peak_id == "p"
        assert (one.species.formula, one.species.ion_formula) == (D4, D4_ION)
        assert one.species.ionization_mechanism_id == "m-h"
        assert one.exemplar["source"] == "database"
        assert one.exemplar["target_compound_id"] == "tc-1"
        assert one.exemplar["sample_peak_mz"] == pytest.approx(ion_mz("C8H25O4Si4", 1))
        assert one.record == {
            "batch_peak_id": "bp-d",
            "verdict": "present",
            "files": 6,
            "present": 5,
            "test_held": 2,
            "batch_peak_run_id": "run-1",
            "version": 4,
        }

    @pytest.mark.parametrize(
        "record",
        [
            _listed_record(verdict="absent"),
            _listed_record(held="vote_holds"),
            _listed_record(species={"kind": "library", "ion_formula": "Br-"}),
        ],
        ids=["absent", "held", "a library ion"],
    )
    def test_no_other_record_proposes_a_compound(self, record):
        assert listed_from(ANCHOR, "p", record) is None

    def test_it_names_the_sample_s_peaks_in_its_twins(self):
        one = listed_from(ANCHOR, "p", _listed_record(), ["t2", "t1"])
        assert one.twin_peak_ids == ("t1", "t2")
        assert listed_from(ANCHOR, "p", _listed_record()).twin_peak_ids == ()


def _anchor(batch_peak_id, mode="mode-1", variable="sum_peak_heights"):
    return SimpleNamespace(
        batch_peak_id=batch_peak_id,
        ionization_mode_id=mode,
        intensity_variable=variable,
    )


class TestTheTwinsOfADecision:
    def test_a_twin_is_another_batch_peak_of_the_species_in_its_partition(self):
        # The batch decides each partition's species together, and keeps a
        # sample that reads one on any other batch peak of its trace: one held
        # for the decided one, and one decided otherwise (settle_twins).
        decided = _anchor("bp-d")
        known = [
            ("d", decided, _listed_record()),
            ("held", _anchor("bp-h"), _listed_record(held="twin", twin_of="bp-d")),
            ("absent", _anchor("bp-a"), _listed_record(verdict="absent")),
            (
                "other",
                _anchor("bp-o"),
                _listed_record(
                    species={"kind": "listed", "formula": OTHER, "ion_formula": D4_ION}
                ),
            ),
            (
                "ion",
                _anchor("bp-i"),
                _listed_record(
                    species={
                        "kind": "listed",
                        "formula": D4,
                        "ion_formula": "C8H24O4Si4Na+",
                    }
                ),
            ),
            (
                "library",
                _anchor("bp-l"),
                _listed_record(
                    species={"kind": "library", "formula": D4, "ion_formula": D4_ION}
                ),
            ),
            ("mode", _anchor("bp-m", mode="mode-2"), _listed_record()),
            ("tof", _anchor("bp-t", variable="sum_peak_areas"), _listed_record()),
        ]

        assert twin_peaks(decided, _listed_record(), known) == ["held", "absent"]

    def test_a_record_with_no_species_is_no_twin(self):
        known = [("x", _anchor("bp-x"), {"verdict": "present"})]
        assert twin_peaks(_anchor("bp-d"), _listed_record(), known) == []


STRIP_RECORD = {
    "batch_peak_id": "bp-p",
    "formula": "C10H16O4",
    "ionization_mechanism_id": "m",
    "label": "13C",
    "ratio": 0.11,
    "mz_offset": 1.00336,
    "mz": 200.101,
    "spectra": 6,
    "median_factor": 1.0,
    "above": 0,
    "verdict": "held",
    "batch_peak_run_id": "run-2",
    "version": 7,
}


class TestAStripForASample:
    def test_a_strip_reads_the_sample_s_two_peaks(self):
        one = strip_from(
            SimpleNamespace(batch_peak_id="bp-x"), "x", STRIP_RECORD, "p", 199.0
        )

        assert (one.sample_peak_id, one.parent_peak_id) == ("x", "p")
        assert (one.formula, one.ionization_mechanism_id) == ("C10H16O4", "m")
        assert one.line == BatchLine(
            label="13C",
            mz_offset=1.00336,
            share=0.11,
            batch_peak_id="bp-x",
            parent_batch_peak_id="bp-p",
            reading={"spectra": 6, "median_factor": 1.0, "above": 0, "verdict": "held"},
            batch_peak_run_id="run-2",
            version=7,
        )

    def test_a_record_without_the_offset_reads_it_off_the_parent(self):
        old = {k: v for k, v in STRIP_RECORD.items() if k != "mz_offset"}
        one = strip_from(SimpleNamespace(batch_peak_id="bp-x"), "x", old, "p", 199.0)
        assert one.line.mz_offset == pytest.approx(1.101)
        no_mz = {k: v for k, v in old.items() if k != "mz"}
        assert (
            strip_from(SimpleNamespace(batch_peak_id="bp-x"), "x", no_mz, "p", 1.0)
            is None
        )

    def test_a_sample_without_the_parent_reads_none(self):
        assert (
            strip_from(
                SimpleNamespace(batch_peak_id="bp-x"), "x", STRIP_RECORD, None, None
            )
            is None
        )


def _row(peak, formula=None, *, row_id=None, role="M0", tier="assigned", **extra):
    return {
        "peak_assignment_id": row_id or f"id-{peak}",
        "peak_assignment_run_id": "run",
        "sample_item_id": "s",
        "sample_peak_id": peak,
        "sample_peak_mz": 297.08246,
        "sample_peak_intensity": 1e5,
        "role": role,
        "assigned_formula": formula,
        "ion_formula": None if formula is None else f"{formula}+",
        "ionization_mechanism_id": "m-h",
        "isotope_label": None,
        "source": "untargeted",
        "fit_score": 0.8,
        "tier": tier,
        "target_compound_id": None,
        "owner_peak_assignment_id": None,
        "alternatives": None,
        "provenance": {"evidence": 0.8},
        **extra,
    }


DECIDED = listed_from(ANCHOR, "d", _listed_record())


class TestWhatBecomesOfAListedCompound:
    @pytest.mark.parametrize(
        "rows, claimed, owners, expected",
        [
            ([_row("d", D4, tier="candidate")], False, set(), ALREADY),
            (
                [
                    _row(
                        "d",
                        D4,
                        tier="below_assignability",
                        source="database",
                        target_compound_id="tc-9",
                    )
                ],
                False,
                set(),
                KEPT_TARGET_LIBRARY,
            ),
            (
                [_row("d", OTHER, role="iso_child", owner_peak_assignment_id="id-q")],
                False,
                set(),
                KEPT_LINE_OF_ANOTHER,
            ),
            ([], True, set(), KEPT_CLAIMED_OTHERWISE),
            ([_row("d", OTHER)], False, {"id-d"}, KEPT_CARRIES_LINES),
            (
                [_row("d", D4, tier="below_assignability")],
                False,
                {"id-d"},
                LISTED_LIFTED,
            ),
            ([_row("d", OTHER)], False, set(), None),
            ([], False, set(), None),
        ],
        ids=[
            "committed on its own",
            "the target library's",
            "another's line",
            "claimed",
            "another formula with its lines",
            "its own reading below candidate",
            "another formula",
            "unread",
        ],
    )
    def test_as_the_batch_s_claim_decides_a_member(
        self, rows, claimed, owners, expected
    ):
        assert (
            listed_decision(DECIDED, rows, claimed=claimed, owners=owners) == expected
        )

    @pytest.mark.parametrize(
        "rows, owners, on_twins, expected",
        [
            (
                [_row("d", OTHER)],
                set(),
                [_row("e", D4, tier="candidate")],
                KEPT_ON_TWIN,
            ),
            ([], set(), [_row("e", D4, tier="below_assignability")], KEPT_ON_TWIN),
            (
                [_row("d", D4, tier="below_assignability")],
                set(),
                [_row("e", D4, tier="unassigned")],
                KEPT_ON_TWIN,
            ),
            ([_row("d", OTHER)], set(), [_row("e", OTHER)], None),
            (
                [_row("d", OTHER)],
                set(),
                [_row("e", D4, role="iso_child", owner_peak_assignment_id="id-q")],
                None,
            ),
            ([_row("d", D4, tier="candidate")], set(), [_row("e", D4)], ALREADY),
            ([_row("d", OTHER)], {"id-d"}, [_row("e", D4)], KEPT_CARRIES_LINES),
        ],
        ids=[
            "the compound on a twin",
            "on a twin below candidate",
            "a lift, and on a twin",
            "another formula on a twin",
            "a twin's line of another",
            "committed here on its own",
            "another formula with its lines here",
        ],
    )
    def test_a_sample_that_reads_it_on_a_twin_is_kept(
        self, rows, owners, on_twins, expected
    ):
        # As the batch's own claim keeps one (decide_row's twins): the claim
        # writes no second copy of the compound in the sample.
        assert (
            listed_decision(
                DECIDED, rows, claimed=False, owners=owners, on_twins=on_twins
            )
            == expected
        )


PEAKS = pd.DataFrame(
    {"sample_peak_id": ["d", "e"], "mz": [297.08246, 310.0], "intensity": [1e5, 2e4]}
)


def _elect(stage_a, stage_b, *, decided=None, source_mz=None, claimed=frozenset()):
    return elect_listed(
        stage_a,
        stage_b,
        {"d": DECIDED} if decided is None else decided,
        claimed_peak_ids=set(claimed),
        peaks=PEAKS,
        source_mz=source_mz or {},
        sample_item_id="s",
        run_id="run",
    )


class TestTheListedCompoundElected:
    def test_another_reading_is_displaced_by_the_list_s_at_candidate(self):
        searched = _row("d", OTHER)
        kept = _row("e", "C9H10O5")

        stage_a, stage_b, outcomes = _elect([], [searched, kept])

        assert outcomes == {"d": LISTED_ELECTED}
        # Elected as a list's row, among Stage A's.
        assert stage_a == [searched] and stage_b == [kept]
        assert searched["peak_assignment_id"] == "id-d"
        assert (searched["assigned_formula"], searched["ion_formula"]) == (D4, D4_ION)
        assert searched["ionization_mechanism_id"] == "m-h"
        assert searched["source"] == "database"
        assert searched["target_compound_id"] == "tc-1"
        assert (searched["tier"], searched["fit_score"]) == ("candidate", None)
        assert searched["alternatives"][0]["assigned_formula"] == OTHER
        cited = searched["provenance"][REASON_BATCH_KNOWN_SPECIES]
        assert cited["batch_peak_id"] == "bp-d" and cited["version"] == 4
        assert cited["read_as"]["formula"] == OTHER

    def test_a_peak_no_stage_read_takes_the_reading_too(self):
        stage_a, _, outcomes = _elect([], [])

        assert outcomes == {"d": LISTED_ELECTED}
        [row] = stage_a
        assert (row["sample_peak_id"], row["assigned_formula"]) == ("d", D4)
        assert (row["sample_item_id"], row["peak_assignment_run_id"]) == ("s", "run")
        assert row["sample_peak_intensity"] == 1e5
        assert row["peak_assignment_id"]

    def test_it_is_measured_on_the_axis_as_stored(self):
        # A decoy's shifted axis moves no batch decision: the error is read off
        # the peak where the source put it, which a control reads it at.
        exact = ion_mz("C8H25O4Si4", 1)
        stage_a, _, _ = _elect(
            [], [_row("d", OTHER)], source_mz={"d": exact * (1 + 2e-6)}
        )
        assert stage_a[0]["mz_error_ppm"] == pytest.approx(2.0, abs=1e-3)
        assert stage_a[0]["sample_peak_mz"] == 297.08246

    def test_its_own_reading_below_candidate_is_lifted_and_keeps_its_fit(self):
        own = _row("d", D4, tier="below_assignability", source="database")
        line = _row("d13", D4, role="iso_child", owner_peak_assignment_id="id-d")

        stage_a, _, outcomes = _elect([own, line], [])

        assert outcomes == {"d": LISTED_LIFTED}
        assert stage_a == [own, line]
        assert (own["tier"], own["fit_score"]) == ("candidate", 0.8)
        assert own["provenance"][REASON_BATCH_KNOWN_SPECIES]["read_as"] == {
            "tier": "below_assignability"
        }

    def test_a_decision_on_a_peak_the_run_does_not_hold_is_left(self):
        # The decision names a peak of an earlier detection of the sample: its
        # members name it until the sample's next fold.
        searched = _row("d", OTHER)
        gone = replace(DECIDED, sample_peak_id="gone")

        stage_a, stage_b, outcomes = _elect(
            [], [searched], decided={"d": DECIDED, "gone": gone}
        )

        assert outcomes == {"d": LISTED_ELECTED, "gone": PEAK_ABSENT}
        assert [row["sample_peak_id"] for row in stage_a] == ["d"]
        assert stage_b == []
        assert cite_listed(stage_a, {"d": DECIDED, "gone": gone}, outcomes) == 1

    def test_a_reading_of_it_on_a_twin_keeps_the_run_s_reading(self):
        # The fold split the compound's trace: the sample's peak e is in a
        # twin, and the run's own reading of the compound there stands alone.
        own = _row("e", D4, tier="candidate", source="database")
        searched = _row("d", OTHER)
        decided = replace(DECIDED, twin_peak_ids=("e",))

        stage_a, stage_b, outcomes = _elect([own], [searched], decided={"d": decided})

        assert outcomes == {"d": KEPT_ON_TWIN}
        assert stage_a == [own] and stage_b == [searched]
        assert searched["assigned_formula"] == OTHER
        assert [
            r["sample_peak_id"]
            for r in stage_a + stage_b
            if r["assigned_formula"] == D4
        ] == ["e"]

    def test_of_two_twins_decided_present_one_is_elected(self):
        # Neither claimed in the batch, so both stand present; a run that
        # reads the compound on neither peak elects it on the first alone.
        first = replace(DECIDED, twin_peak_ids=("e",))
        second = replace(DECIDED, sample_peak_id="e", twin_peak_ids=("d",))

        stage_a, _, outcomes = _elect([], [], decided={"d": first, "e": second})

        assert outcomes == {"d": LISTED_ELECTED, "e": KEPT_ON_TWIN}
        assert [(r["sample_peak_id"], r["assigned_formula"]) for r in stage_a] == [
            ("d", D4)
        ]

    def test_a_reading_the_claim_keeps_is_left_as_it_is(self):
        searched = _row("d", OTHER)
        line = _row("d13", OTHER, role="iso_child", owner_peak_assignment_id="id-d")
        stage_a, stage_b, outcomes = _elect([], [searched, line])
        assert outcomes == {"d": KEPT_CARRIES_LINES}
        assert stage_b == [searched, line] and stage_a == []
        assert searched["assigned_formula"] == OTHER

    def test_the_decision_is_cited_once_the_judging_has_written(self):
        searched = _row("d", OTHER)
        stage_a, _, outcomes = _elect([], [searched])
        # The judging rewrites every row's reasons.
        searched["provenance"]["tier_reasons"] = [{"rule": "on_a_list", "caps": False}]

        assert cite_listed(stage_a, {"d": DECIDED}, outcomes) == 1
        assert [r["rule"] for r in searched["provenance"]["tier_reasons"]] == [
            "on_a_list",
            REASON_BATCH_KNOWN_SPECIES,
        ]
        assert cite_listed(stage_a, {"d": DECIDED}, outcomes) == 1
        assert len(searched["provenance"]["tier_reasons"]) == 2
        # A row on the peak that reads the compound with no decision of the
        # batch's on it was not put there by the decision, and cites nothing.
        unread = _row("d", D4, tier="candidate")
        unread["provenance"]["tier_reasons"] = [{"rule": "on_a_list"}]
        assert cite_listed([unread], {"d": DECIDED}, outcomes) == 0
        assert unread["provenance"]["tier_reasons"] == [{"rule": "on_a_list"}]
        # A reading the judging turned to another formula cites nothing.
        searched["assigned_formula"] = OTHER
        assert cite_listed(stage_a, {"d": DECIDED}, outcomes) == 0
        assert cite_listed(stage_a, {"d": DECIDED}, {"d": ALREADY}) == 0


LINE = BatchLine("13C", 1.00336, 0.11, "bp-x", "bp-p", {"spectra": 6}, "run-2", 7)
STRIP = StripDecision("x", "p", "C10H16O4", "m-h", LINE)


def _strip(rows, *, claimed=frozenset(), peaks=("p", "x")):
    held = {"p": (199.0976, 1e6), "x": (200.101, 1.1e5)}
    return read_strips(
        rows,
        {"x": STRIP},
        claimed_peak_ids=set(claimed),
        peaks=pd.DataFrame(
            {
                "sample_peak_id": list(peaks),
                "mz": [held[peak][0] for peak in peaks],
                "intensity": [held[peak][1] for peak in peaks],
            }
        ),
        sample_item_id="s",
        run_id="run",
    )


class TestTheStripReadOnTheRows:
    def test_the_row_is_read_as_the_parent_s_line(self):
        parent = _row("p", "C10H16O4", sample_peak_mz=199.0976)
        analyte = _row("x", "C9H12N2O2", tier="candidate", sample_peak_mz=200.101)

        rows, outcomes = _strip([parent, analyte])

        assert outcomes == {STRIP_READ: 1}
        assert rows == [parent, analyte]
        assert analyte["role"] == "iso_child"
        assert analyte["owner_peak_assignment_id"] == "id-p"
        assert analyte["assigned_formula"] == "C10H16O4"
        assert analyte["tier"] == "assigned"
        assert analyte["isotope_label"]
        record = analyte["provenance"]["batch_isotopologue"]
        assert (record["batch_peak_id"], record["version"]) == ("bp-x", 7)
        assert analyte["alternatives"][0]["assigned_formula"] == "C9H12N2O2"
        [entry] = parent["provenance"]["batch_isotopologues"]
        assert (entry["sample_peak_id"], entry["version"]) == ("x", 7)

    @pytest.mark.parametrize(
        "rule, caps",
        [("absent_line", True), ("no_close_rival", False)],
        ids=["capped by absent_line", "no rule caps"],
    )
    def test_the_line_carries_its_parent_s_tier_as_its_reason(self, rule, caps):
        # As the batch's own strip writes it (MAS-1325); the run's judging
        # writes it again from the judged parent.
        parent = _row("p", "C10H16O4", sample_peak_mz=199.0976)
        parent["provenance"]["tier_reasons"] = [
            {"rule": rule, "detail": "d", "caps": caps}
        ]
        analyte = _row("x", "C9H12N2O2", sample_peak_mz=200.101)

        _strip([parent, analyte])

        assert analyte["provenance"]["tier_reasons"] == [
            {"rule": "inherited_from_owner", "detail": ANY, "caps": caps}
        ]

    def test_an_unread_peak_is_read_as_the_line_too(self):
        parent = _row("p", "C10H16O4", sample_peak_mz=199.0976)
        rows, outcomes = _strip([parent])
        assert outcomes == {STRIP_READ: 1}
        [_, line] = rows
        assert (line["sample_peak_id"], line["role"]) == ("x", "iso_child")
        assert line["owner_peak_assignment_id"] == "id-p"
        assert line["peak_assignment_run_id"] == "run"

    @pytest.mark.parametrize(
        "rows, peaks",
        [
            ([_row("p", "C10H16O4", sample_peak_mz=199.0976)], ("p",)),
            ([], ("x",)),
        ],
        ids=["the stripped peak", "the parent's peak"],
    )
    def test_a_strip_on_a_peak_the_run_does_not_hold_is_left(self, rows, peaks):
        # The strip names a peak of an earlier detection of the sample.
        before = [dict(row) for row in rows]
        after, outcomes = _strip(rows, peaks=peaks)
        assert outcomes == {PEAK_ABSENT: 1}
        assert after == before

    @pytest.mark.parametrize(
        "rows, claimed, expected",
        [
            ([_row("p", "C9H10O5"), _row("x", "C9H12N2O2")], (), STRIP_KEPT_PARENT),
            (
                [
                    _row("p", "C10H16O4", ionization_mechanism_id="m-other"),
                    _row("x", "C9H12N2O2"),
                ],
                (),
                STRIP_KEPT_PARENT,
            ),
            (
                [_row("p", "C10H16O4", role="iso_child"), _row("x", "C9H12N2O2")],
                (),
                STRIP_KEPT_PARENT,
            ),
            ([_row("p", "C10H16O4")], ("x",), STRIP_KEPT_CLAIMED),
            (
                [
                    _row("p", "C10H16O4"),
                    _row("x", "C9H12N2O2"),
                    _row(
                        "x13",
                        "C9H12N2O2",
                        role="iso_child",
                        owner_peak_assignment_id="id-x",
                    ),
                ],
                (),
                STRIP_KEPT_CARRIES_LINES,
            ),
            (
                [
                    _row("p", "C10H16O4"),
                    _row(
                        "x",
                        "C10H16O4",
                        role="iso_child",
                        owner_peak_assignment_id="id-p",
                    ),
                ],
                (),
                ALREADY,
            ),
        ],
        ids=[
            "the parent reads another formula",
            "through another mechanism",
            "the parent's peak is no monoisotopic row",
            "claimed",
            "the row carries its own lines",
            "already the parent's line",
        ],
    )
    def test_a_row_the_batch_s_strip_keeps_is_left(self, rows, claimed, expected):
        before = [dict(row) for row in rows]
        after, outcomes = _strip(rows, claimed=claimed)
        assert outcomes == {expected: 1}
        assert after == before


class TestTheStageListsInputs:
    def test_each_kind_enters_at_its_stage_and_a_decoy_reads_the_claims(self):
        assert {one.kind: (one.stage, one.decoy) for one in pipeline.BATCH_INPUTS} == {
            KIND_KNOWN_SPECIES: (pipeline.Stage.ELECT, True),
            KIND_WATER_RUNG: (pipeline.Stage.ELECT, True),
            KIND_LISTED_COMPOUND: (pipeline.Stage.PROPOSE, True),
            KIND_ISOTOPOLOGUE_STRIP: (pipeline.Stage.JUDGE, False),
            KIND_SERIES: (pipeline.Stage.JUDGE, False),
        }
        assert {one.kind for one in pipeline.BATCH_INPUTS} == set(KINDS)

    @pytest.mark.parametrize(
        "path, decoy, kinds",
        [
            (pipeline.RUN, False, set(KINDS)),
            (pipeline.INGEST_FOLD, False, set(KINDS)),
            (
                pipeline.DECOY,
                True,
                {KIND_KNOWN_SPECIES, KIND_WATER_RUNG, KIND_LISTED_COMPOUND},
            ),
            (
                pipeline.BATCH_SEARCH,
                False,
                {
                    KIND_KNOWN_SPECIES,
                    KIND_WATER_RUNG,
                    KIND_ISOTOPOLOGUE_STRIP,
                    KIND_SERIES,
                },
            ),
            (
                pipeline.PROPAGATION,
                False,
                {
                    KIND_KNOWN_SPECIES,
                    KIND_WATER_RUNG,
                    KIND_ISOTOPOLOGUE_STRIP,
                    KIND_SERIES,
                },
            ),
            (pipeline.PERSONS_CHOICE, False, {KIND_KNOWN_SPECIES, KIND_WATER_RUNG}),
        ],
        ids=lambda value: getattr(value, "name", None),
    )
    def test_a_path_reads_what_its_stages_read(self, path, decoy, kinds):
        assert pipeline.batch_kinds(path, decoy=decoy) == kinds

    @pytest.mark.asyncio
    async def test_a_pass_reads_its_batch_once_in_the_resolve_stage(self):
        read = []

        async def reader(sample, *, profile, kinds):
            read.append((sample.sample_item_id, profile, kinds))
            return BatchDecisions(sample_batch_id="b", kinds=kinds)

        ctx = pipeline.RunContext(
            path=pipeline.RUN,
            sample=SimpleNamespace(
                sample_item_id="si-1",
                sample_item_name="s",
                sample_batch_id="b",
                polarity="-",
                filename="f.zarr",
            ),
            config=PeakAssignmentConfig(profile="BR"),
            run_id="r-1",
        )
        ctx.match_params, ctx.peaks_df = object(), PEAKS
        resolved = SimpleNamespace(
            profile=SimpleNamespace(name="BR"), unavailable_channels=()
        )
        with (
            patch.object(pipeline, "read_batch_decisions", side_effect=reader),
            patch.object(pipeline, "sample_setup", return_value=None),
            patch.object(pipeline, "_mechanisms_of", return_value=([], [])),
            patch.object(pipeline, "_instrument_type", return_value="tof"),
            patch.object(pipeline, "resolve_profile", return_value=resolved),
            patch.object(pipeline, "named_context_for", return_value=None),
            patch.object(
                pipeline.sample_mechanisms,
                "_resolve_secondary_channels",
                return_value=(resolved, []),
            ),
            patch.object(
                pipeline.sample_mechanisms, "_searched_mechanisms", return_value=[]
            ),
            patch.object(pipeline.sample_inputs, "_resolution_of", return_value=None),
        ):
            await pipeline.resolve(ctx)

        assert read == [("si-1", "BR", frozenset(KINDS))]
        assert ctx.batch.kinds == frozenset(KINDS)


def _context(**batch) -> pipeline.RunContext:
    ctx = pipeline.RunContext(
        path=pipeline.RUN,
        sample=SimpleNamespace(sample_item_id="s", sample_item_name="s"),
        config=PeakAssignmentConfig(run_untargeted=False),
        run_id="run",
    )
    ctx.batch = BatchDecisions(sample_batch_id="b", **batch)
    ctx.peaks_df = pd.DataFrame(
        {
            "sample_peak_id": ["d", "p", "x"],
            "mz": [297.08246, 199.0976, 200.101],
            "intensity": [1e5, 1e6, 1.1e5],
        }
    )
    return ctx


class TestTheElectStageReadsThem:
    @pytest.mark.asyncio
    async def test_a_listed_compound_before_the_passes_a_strip_before_the_series(
        self,
    ):
        ctx = _context(
            listed={"d": DECIDED},
            strips={"x": STRIP},
            series={"p": {"formula": "C10H16O4", "ionization_mechanism_id": "m-h"}},
        )
        ctx.stage_a = [
            _row("p", "C10H16O4", sample_peak_mz=199.0976),
            _row("x", "C9H12N2O2", sample_peak_mz=200.101),
        ]
        ctx.decided = dict(ctx.batch.listed)
        same_ion = []

        def families(rows, **_):
            same_ion.append(
                {r["sample_peak_id"]: (r["assigned_formula"], r["role"]) for r in rows}
            )
            return 0

        with patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", side_effect=families
        ):
            await pipeline.elect(ctx)

        # The same-ion readings read the listed compound, and the analyte's
        # reading before the strip...
        assert same_ion == [
            {
                "p": ("C10H16O4", "M0"),
                "x": ("C9H12N2O2", "M0"),
                "d": (D4, "M0"),
            }
        ]
        # ...which the judging then reads as the parent's line, beside the
        # series' record.
        rows = {r["sample_peak_id"]: r for r in ctx.stage_a + ctx.stage_b}
        assert rows["x"]["role"] == "iso_child"
        assert rows["p"]["provenance"]["series"]["formula"] == "C10H16O4"
        assert ctx.listed_outcomes == {"d": LISTED_ELECTED}
        assert ctx.batch_applied == {
            "listed": {LISTED_ELECTED: 1},
            "strips": {STRIP_READ: 1},
        }

    @pytest.mark.asyncio
    async def test_a_decision_on_a_peak_the_run_does_not_hold_is_recorded(self):
        # After a re-detection the sample's members name peaks its run no
        # longer has, until its next fold: the run reads on, and says so.
        gone = replace(STRIP, sample_peak_id="gone-x")
        ctx = _context(
            listed={"gone-d": replace(DECIDED, sample_peak_id="gone-d")},
            strips={"gone-x": gone},
        )
        ctx.decided = dict(ctx.batch.listed)
        ctx.stage_a = [_row("p", "C10H16O4", sample_peak_mz=199.0976)]
        with patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
        ):
            await pipeline.elect(ctx)

        assert [r["sample_peak_id"] for r in ctx.stage_a + ctx.stage_b] == ["p"]
        assert ctx.batch_applied == {
            "listed": {PEAK_ABSENT: 1},
            "strips": {PEAK_ABSENT: 1},
        }

    @pytest.mark.asyncio
    async def test_a_listed_compound_holds_candidate_through_the_re_score(self):
        # The refit's re-score moves a row's evidence and its tier with it
        # (``judging_loop.rescore_rows``). A row the decision put at candidate,
        # elected or lifted, falls no lower: only a cap takes it lower. A
        # reading the decision kept keeps its own tier.
        ctx = _context(
            listed={
                "d": DECIDED,
                "x": replace(DECIDED, sample_peak_id="x"),
                "p": replace(DECIDED, sample_peak_id="p"),
            }
        )
        ctx.decided = dict(ctx.batch.listed)
        own = _row(
            "d",
            D4,
            tier="below_assignability",
            source="database",
            fit_score=0.4,
            provenance={"evidence": 0.4},
        )
        library = _row(
            "p",
            "C10H16O4",
            tier="below_assignability",
            source="database",
            target_compound_id="tc-9",
            fit_score=0.4,
            provenance={"evidence": 0.4},
            sample_peak_mz=199.0976,
        )
        ctx.stage_a = [own, library]
        ctx.stage_b = [_row("x", OTHER, sample_peak_mz=200.101)]
        with patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
        ):
            await pipeline.elect(ctx)
        assert ctx.listed_outcomes == {
            "d": LISTED_LIFTED,
            "p": KEPT_TARGET_LIBRARY,
            "x": LISTED_ELECTED,
        }
        assert ctx.floors == {"d": "candidate", "x": "candidate"}

        fits = {
            "database": {(D4, "m-h"): 0.1, ("C10H16O4", "m-h"): 0.1},
        }
        bands = {"candidate": 0.45, "assigned": 0.75}
        rows = ctx.stage_a + ctx.stage_b
        # The premise: the new fit alone puts the lifted reading below.
        unheld = {r["sample_peak_id"]: r for r in deepcopy(rows)}
        pipeline.judging_loop.rescore_rows(list(unheld.values()), fits, bands)
        assert unheld["d"]["tier"] == "below_assignability"

        pipeline.judging_loop.rescore_rows(rows, fits, bands, floors=ctx.floors)

        tiers = {r["sample_peak_id"]: r["tier"] for r in rows}
        assert own["provenance"]["evidence"] == pytest.approx(0.1)
        assert tiers == {
            "d": "candidate",
            "x": "candidate",
            "p": "below_assignability",
        }

    @pytest.mark.asyncio
    async def test_a_path_that_proposes_no_known_set_proposes_no_listed_compound(self):
        ctx = _context(listed={"d": DECIDED})
        ctx.path = pipeline.PROPAGATION
        with patch.object(pipeline, "_propose_given") as given:
            await pipeline.propose(ctx)
        given.assert_awaited_once()
        assert ctx.decided == {}

    @pytest.mark.asyncio
    async def test_the_known_set_s_paths_propose_it(self):
        ctx = _context(listed={"d": DECIDED})
        with patch.object(pipeline, "_propose_known_set") as known:
            await pipeline.propose(ctx)
        known.assert_awaited_once()
        assert ctx.decided == {"d": DECIDED}


class TestWhatTheStagesTookIsNoReadings:
    @pytest.mark.asyncio
    async def test_a_listed_compound_is_measured_on_the_axis_as_stored(self):
        # On a shifted decoy arm the source's peaks are read where the source
        # put them (``sample_mechanisms._source_axis``): the arm claims what its control
        # claims.
        exact = ion_mz("C8H25O4Si4", 1)
        ctx = _context(listed={"d": DECIDED})
        ctx.decided = dict(ctx.batch.listed)
        stored = ctx.peaks_df.assign(mz=[exact * (1 - 3e-6), 199.0976, 200.101])
        with (
            patch.object(
                pipeline.sample_mechanisms, "_source_axis", return_value=stored
            ),
            patch.object(
                pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
            ),
        ):
            await pipeline.elect(ctx)
        [row] = ctx.stage_a
        assert row["mz_error_ppm"] == pytest.approx(-3.0, abs=1e-3)
        assert row["sample_peak_mz"] == 297.08246

    @pytest.mark.asyncio
    async def test_no_strip_is_read_on_a_peak_the_claim_stage_took(self):
        ctx = _context(strips={"x": STRIP})
        ctx.reagent_peak_ids = {"x"}
        ctx.stage_a = [_row("p", "C10H16O4", sample_peak_mz=199.0976)]
        with (
            patch.object(
                pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
            ),
        ):
            await pipeline.elect(ctx)
        assert [r["sample_peak_id"] for r in ctx.stage_a + ctx.stage_b] == ["p"]
        assert ctx.batch_applied == {"strips": {STRIP_KEPT_CLAIMED: 1}}


class TestTheReaderReadsWhatItIsAsked:
    @pytest.mark.asyncio
    async def test_a_kind_not_asked_for_is_not_read(self):
        species = AsyncMock(return_value=[_species("Br")])
        rungs = AsyncMock(return_value=(_species("BrH2O"),))
        series = AsyncMock(return_value={"p": {"version": 1}})
        members = AsyncMock(return_value=({}, {}))
        with (
            patch.object(batch_decisions.known_species, "batch_species_for", species),
            patch.object(batch_decisions, "rungs_for", rungs),
            patch.object(batch_decisions.batch_lines, "batch_series_by_peak", series),
            patch.object(batch_decisions, "member_decisions", members),
            patch.object(batch_decisions, "stage_versions", AsyncMock(return_value={})),
        ):
            sample = SimpleNamespace(sample_item_id="s", sample_batch_id="b")
            only_series = await batch_decisions.read_batch_decisions(
                sample, profile="BR", kinds={KIND_SERIES}
            )
            only_claims = await batch_decisions.read_batch_decisions(
                sample, profile="BR", kinds={KIND_KNOWN_SPECIES, KIND_WATER_RUNG}
            )

        assert (only_series.species, only_series.rungs) == ((), ())
        assert dict(only_series.series) == {"p": {"version": 1}}
        assert [one.cluster.formula for one in only_claims.source_ions()] == [
            "Br",
            "BrH2O",
        ]
        assert only_claims.series == {}
        species.assert_awaited_once()
        rungs.assert_awaited_once_with(sample, "BR")
        series.assert_awaited_once_with("s")

    @pytest.mark.asyncio
    async def test_the_stage_versions_are_read_before_the_records(self):
        # A stage that hands over while the records are read then shows as
        # running or older than them, never as current with the older records.
        read = []

        def reading(name, value):
            async def one(*args, **kwargs):
                read.append(name)
                return value

            return one

        with (
            patch.object(
                batch_decisions.known_species,
                "batch_species_for",
                reading("species", []),
            ),
            patch.object(batch_decisions, "rungs_for", reading("rungs", ())),
            patch.object(
                batch_decisions.batch_lines,
                "batch_series_by_peak",
                reading("series", {}),
            ),
            patch.object(
                batch_decisions, "member_decisions", reading("members", ({}, {}))
            ),
            patch.object(
                batch_decisions,
                "stage_versions",
                reading("stages", {"series": {"version": 2, "state": "current"}}),
            ),
        ):
            decisions = await batch_decisions.read_batch_decisions(
                SimpleNamespace(sample_item_id="s", sample_batch_id="b"),
                profile="BR",
            )

        assert read[0] == "stages"
        assert sorted(read[1:]) == ["members", "rungs", "series", "species"]
        assert decisions.stages == {"series": {"version": 2, "state": "current"}}

    @pytest.mark.asyncio
    async def test_a_sample_in_no_batch_reads_nothing(self):
        decisions = await batch_decisions.read_batch_decisions(
            SimpleNamespace(sample_item_id="s", sample_batch_id=None), profile="BR"
        )
        assert decisions == BatchDecisions(kinds=frozenset(KINDS))


class TestALineOffAnUnreadPeak:
    @pytest.mark.asyncio
    async def test_the_elect_stage_keeps_the_line_it_read_off_a_peak_no_stage_read(
        self,
    ):
        ctx = _context(strips={"x": STRIP})
        ctx.stage_a = [_row("p", "C10H16O4", sample_peak_mz=199.0976)]
        with (
            patch.object(
                pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
            ),
        ):
            await pipeline.elect(ctx)
        rows = {r["sample_peak_id"]: r for r in ctx.stage_a}
        assert rows["x"]["role"] == "iso_child"
        assert rows["x"]["owner_peak_assignment_id"] == "id-p"


# --- the source ions claimed on the run's rows (MAS-1089) ---------------------------


#: A carbon-bearing ion and a carbon-free one, each decided present by the
#: batch as a known species, and a rung its water ladder passed.
TRIMER = ReagentCluster("C3H13N6O3", 1, "[(CH4N2O)3+H]+", "cluster")
HYDRONIUM = ReagentCluster("H5O2", 1, "[H3O+H2O]+", "cluster")
SPECIES = BatchSpecies(TRIMER, {"batch_peak_id": "T", "verdict": "present"})
WATERLESS = BatchSpecies(HYDRONIUM, {"batch_peak_id": "H", "verdict": "present"})
RUNG = BatchSpecies(
    TRIMER, {"batch_peak_id": "R", "segment": 0}, reason=REASON_BATCH_WATER_LADDER
)


def _ion(index, cluster=TRIMER, **line):
    return ReagentHit(
        cluster=cluster,
        index=index,
        mz=199.0976,
        intensity=1e6,
        mz_error_ppm=0.1,
        **line,
    )


#: The ion on ``p`` and its 13C line on ``x``, in ``_context``'s peaks.
ION, LINE = _ion(1), _ion(2, isotope_label="13C", parent_index=1)


def _claim(stage_a=(), stage_b=(), hits=(ION,), one=SPECIES, **kwargs):
    kwargs.setdefault("claimed_peak_ids", set())
    return batch_decisions.claim_source_ions(
        list(stage_a),
        list(stage_b),
        hits,
        {one.cluster.formula: one},
        peak_ids=["d", "p", "x"],
        **kwargs,
    )


class TestTheSourceIonsClaimedOnTheRows:
    """A run claims its batch's source ions after Stage A and the search, by
    the keep rules the batch's own claims take a member by (MAS-1089)."""

    def test_a_peak_the_target_library_names_is_kept(self):
        target = _row("p", "C3H12N6O3", source="database", target_compound_id="tc")
        claimed, stage_a, _, outcomes, displaced = _claim([target])

        assert (claimed, stage_a, displaced) == ([], [target], {})
        assert outcomes == {"p": KEPT_TARGET_LIBRARY}

    def test_a_searched_reading_is_displaced(self):
        searched = _row("p", "C10H16O4")
        claimed, _, stage_b, outcomes, displaced = _claim(stage_b=[searched])

        assert (claimed, stage_b) == ([ION], [])
        assert outcomes == {"p": batch_decisions.SOURCE_CLAIMED}
        assert displaced == {"p": searched}

    @pytest.mark.parametrize(
        "one, outcome",
        [
            (SPECIES, batch_decisions.SOURCE_CLAIMED),
            (RUNG, KEPT_LISTED),
        ],
        ids=["known species", "water rung"],
    )
    def test_a_reference_lists_reading_keeps_a_rungs_peak_only(self, one, outcome):
        listed = _row("p", "C10H16O4", source="database")
        _, _, _, outcomes, _ = _claim([listed], one=one)

        assert outcomes == {"p": outcome}

    def test_a_peak_a_person_assigned_is_kept(self):
        hand = _row("p", "C10H16O4", source="manual")
        _, _, _, outcomes, _ = _claim([hand])

        assert outcomes == {"p": KEPT_HAND_ASSIGNED}

    def test_another_readings_isotope_line_is_kept(self):
        parent = _row("d", "C10H16O4")
        line = _row(
            "p",
            "C10H16O4",
            role="iso_child",
            isotope_label="13C2",
            owner_peak_assignment_id="id-d",
        )
        claimed, stage_a, _, outcomes, _ = _claim([parent, line])

        assert (claimed, stage_a) == ([], [parent, line])
        assert outcomes == {"p": KEPT_LINE_OF_ANOTHER}

    def test_a_peak_another_claim_took_is_kept(self):
        claimed, _, _, outcomes, _ = _claim(claimed_peak_ids={"p"})

        assert (claimed, outcomes) == ([], {"p": KEPT_CLAIMED_OTHERWISE})

    def test_a_reading_with_a_line_the_ion_cannot_have_is_kept(self):
        m0 = _row("p", "C10H16O4")
        line = _row(
            "x",
            "C10H16O4",
            role="iso_child",
            isotope_label="13C",
            owner_peak_assignment_id="id-p",
        )
        hits = (_ion(1, HYDRONIUM),)
        claimed, stage_a, _, outcomes, _ = _claim([m0, line], hits=hits, one=WATERLESS)

        assert (claimed, stage_a) == ([], [m0, line])
        assert outcomes == {"p": KEPT_CARRIES_LINES}

    def test_a_reading_with_lines_the_ion_can_have_goes_with_them(self):
        m0 = _row("p", "C10H16O4")
        line = _row(
            "x",
            "C10H16O4",
            role="iso_child",
            isotope_label="13C",
            owner_peak_assignment_id="id-p",
        )
        claimed, stage_a, _, outcomes, displaced = _claim([m0, line], hits=(ION, LINE))

        # The reading's line leaves with it, and the ion's line takes its peak.
        assert (claimed, stage_a) == ([ION, LINE], [])
        assert outcomes == {"p": batch_decisions.SOURCE_CLAIMED}
        assert displaced == {"p": m0}

    def test_an_ions_line_is_left_on_a_peak_another_reading_holds(self):
        other = _row("x", "C9H12N2O2")
        claimed, stage_a, _, _, _ = _claim([other], hits=(ION, LINE))

        assert (claimed, stage_a) == ([ION], [other])
        assert _claim(hits=(ION, LINE))[0] == [ION, LINE]

    def test_an_ions_line_is_left_on_a_peak_another_claim_took(self):
        # The artifact pass took the line's peak: one row on a peak.
        claimed, _, _, outcomes, _ = _claim(hits=(ION, LINE), claimed_peak_ids={"x"})

        assert claimed == [ION]
        assert outcomes == {"p": batch_decisions.SOURCE_CLAIMED}

    def test_a_decoy_arm_keeps_a_peak_its_own_claims_took(self):
        # A control's claim kept the peak out of its artifact pass; the arm's
        # artifact pass took it.
        claimed, _, _, outcomes, _ = _claim(
            hits=(ION, LINE), control=frozenset({"p"}), claimed_peak_ids={"p"}
        )
        assert claimed == []
        assert outcomes == {"p": KEPT_CLAIMED_OTHERWISE}

        claimed, _, _, _, _ = _claim(
            hits=(ION, LINE), control=frozenset({"p", "x"}), claimed_peak_ids={"x"}
        )
        assert claimed == [ION]

    def test_a_decoy_arm_claims_what_its_control_claimed(self):
        target = _row("p", "C3H12N6O3", source="database", target_compound_id="tc")
        claimed, stage_a, _, outcomes, _ = _claim([target], control=frozenset({"p"}))
        assert (claimed, stage_a) == ([ION], [])
        assert outcomes == {"p": batch_decisions.SOURCE_CLAIMED}

        claimed, _, _, outcomes, _ = _claim(control=frozenset())
        assert claimed == []
        assert outcomes == {"p": batch_decisions.SOURCE_KEPT_BY_CONTROL}


class TestTheRunClaimsThemInItsElectStage:
    @staticmethod
    def _found(*hits, one=SPECIES):
        return SourceIonHits(tuple(hits), {one.cluster.formula: one})

    @pytest.mark.asyncio
    async def test_a_kept_peak_stays_the_known_sets(self):
        ctx = _context()
        target = _row("p", "C3H12N6O3", source="database", target_compound_id="tc")
        ctx.stage_a = [target]
        ctx.batch_source = self._found(ION)
        with patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
        ):
            await pipeline.elect(ctx)

        assert ctx.stage_a == [target]
        assert (ctx.reagent_rows, ctx.reagent_peak_ids) == ([], set())
        assert ctx.batch_applied == {"source": {KEPT_TARGET_LIBRARY: 1}}

    @pytest.mark.asyncio
    async def test_a_claimed_peak_is_the_sources_and_says_what_it_read(self):
        ctx = _context(listed={"p": replace(DECIDED, sample_peak_id="p")})
        ctx.decided = dict(ctx.batch.listed)
        searched = _row("p", "C10H16O4", sample_peak_mz=199.0976)
        ctx.stage_b = [searched]
        ctx.batch_source = self._found(ION)
        with patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
        ):
            await pipeline.elect(ctx)

        assert ctx.stage_a + ctx.stage_b == []
        [row] = ctx.reagent_rows
        assert (row["sample_peak_id"], row["role"]) == ("p", "reagent")
        assert row["ion_formula"] == "C3H13N6O3+"
        reagent = row["provenance"]["reagent"]
        assert reagent["reason"] == REASON_BATCH_KNOWN_SPECIES
        assert reagent["read_as"]["formula"] == "C10H16O4"
        assert row["alternatives"][0]["assigned_formula"] == "C10H16O4"
        assert row["alternatives"][0]["displaced_by_claim"] is True
        assert ctx.reagent_peak_ids == {"p"}
        # The listed compound the batch decided on the same peak is elected
        # after the source claim, which took it.
        assert ctx.listed_outcomes == {"p": KEPT_CLAIMED_OTHERWISE}
        assert ctx.batch_applied == {
            "claimed": {REASON_BATCH_KNOWN_SPECIES: 1},
            "source": {batch_decisions.SOURCE_CLAIMED: 1},
            "listed": {KEPT_CLAIMED_OTHERWISE: 1},
        }

    @pytest.mark.asyncio
    async def test_a_decoy_arm_reads_its_controls_claims(self):
        ctx = _context()
        ctx.path = pipeline.DECOY
        ctx.decoy_plan = SimpleNamespace(control_source_claims=frozenset())
        ctx.batch_source = self._found(ION)
        with patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
        ):
            await pipeline.elect(ctx)

        assert ctx.reagent_rows == []
        assert ctx.batch_applied["source"] == {
            batch_decisions.SOURCE_KEPT_BY_CONTROL: 1
        }

    @pytest.mark.parametrize(
        "path, control",
        [(pipeline.RUN, None), (pipeline.DECOY, frozenset({"p", "x"}))],
        ids=["run", "decoy"],
    )
    @pytest.mark.asyncio
    async def test_a_peak_the_artifact_pass_took_holds_one_row(self, path, control):
        ctx = _context()
        ctx.path = path
        if control is not None:
            ctx.decoy_plan = SimpleNamespace(control_source_claims=control)
        ctx.artifact_peak_ids = {"x"}
        ctx.artifact_rows = [_row("x", "C9H12N2O2", role="artifact")]
        ctx.batch_source = self._found(ION, LINE)
        with patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
        ):
            await pipeline.elect(ctx)

        on_peaks = Counter(
            row["sample_peak_id"] for row in ctx.reagent_rows + ctx.artifact_rows
        )
        assert on_peaks == Counter({"p": 1, "x": 1})

    @pytest.mark.parametrize(
        "path, deferred",
        [
            (pipeline.RUN, True),
            (pipeline.DECOY, True),
            (pipeline.INGEST_FOLD, True),
            (pipeline.BATCH_SEARCH, False),
            (pipeline.PROPAGATION, False),
            (pipeline.PERSONS_CHOICE, False),
        ],
        ids=lambda value: getattr(value, "name", None),
    )
    @pytest.mark.asyncio
    async def test_the_claim_stage_claims_them_only_on_a_path_with_no_known_set(
        self, path, deferred
    ):
        ctx = _context()
        ctx.path = path
        ctx.resolved_profile = SimpleNamespace(profile=SimpleNamespace(name="UR"))
        found = self._found(ION)
        with (
            patch.object(
                pipeline.prepasses,
                "_reagent_assignments",
                return_value=([], set(), None, found),
            ),
            patch.object(
                pipeline.prepasses, "_artifact_assignments", return_value=([], set())
            ) as artifacts,
        ):
            await pipeline.claim(ctx)

        assert pipeline.claims_after_the_known_set(path) is deferred
        if deferred:
            assert ctx.batch_source == found
            assert ctx.reagent_rows == []
            assert artifacts.call_args.kwargs["claimed_peak_ids"] == set()
        else:
            assert ctx.batch_source == SourceIonHits()
            [row] = ctx.reagent_rows
            assert row["sample_peak_id"] == "p"
            assert row["provenance"]["reagent"]["reason"] == REASON_BATCH_KNOWN_SPECIES
            assert ctx.batch_applied == {"claimed": {REASON_BATCH_KNOWN_SPECIES: 1}}
            # The artifact pass leaves what the batch's word claimed.
            assert artifacts.call_args.kwargs["claimed_peak_ids"] == {"p"}
