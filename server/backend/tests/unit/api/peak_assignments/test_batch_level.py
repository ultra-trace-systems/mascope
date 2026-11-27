"""The batch's identification level (step 5.8c, ``batch_level.py``).

Each lift on a member's record, the refutation, the facts read only while the
record they came from stands, the batch peak's level over its members (3b and
2b kept), and the per-sample overlay.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_level import (
    BY_LINE,
    BY_LOCK,
    BY_STAMP,
    BY_TWIN,
    RECORD_LINES,
    RECORD_LOCK,
    BatchFacts,
    MemberLevel,
    batch_level,
    batch_level_fields,
    batch_level_record_of,
    level_columns,
    levels_outcome,
    overlay,
    read_facts,
    read_levels,
    row_batch_level,
    standing_batch_level_record,
    still_read,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    BATCH_LEVEL_KEY,
    compute_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import (
    STAGE_OF_KEY,
    BatchStage,
)
from mascope_backend.api.new.peak_assignments.engine import GRID_RIVALS
from mascope_backend.api.new.peak_assignments.identification_level import (
    LIFT_4A,
    LIFT_RIVALS_UNMEASURED,
    LIFT_RIVALS_UNMEASURED_BEFORE,
    SPLIT,
    LevelContext,
    level_of,
)
from mascope_backend.api.new.peak_assignments.rival_set import RIVAL_SET
from mascope_backend.api.new.peak_assignments.tiering import PREDICTED_ENVELOPE


FORMULA = "C6H5BrO"
ION = "C6H4BrO-"
MECH = "im-mh"
#: A rival of the bromophenol reading that holds no bromine, and one that does.
NO_BR = ("C4H9N2O3P", "C4H8N2O3P-")
WITH_BR = ("C5H4BrN", "C5H3BrN-")


def reading(**extra) -> dict:
    return {
        "formula": FORMULA,
        "ion_formula": ION,
        "ionization_mechanism_id": MECH,
        **extra,
    }


def open_rival(formula: str, ion: str | None, **extra) -> dict:
    return {
        "formula": formula,
        "ion_formula": ion,
        "record": "rival_set",
        "fit": 0.88,
        "test": "open",
        "detail": "its fit at the run's width is within the tie margin",
        "held_by": ["plausibility"],
        **extra,
    }


def five_a(*rivals: dict, split: str = "4a", **extra) -> dict:
    """A run's record at 5a over a measured shortlist, with its split."""
    return {
        "level": "5a",
        "evidence": "rivals left open",
        "lift": "exclude them",
        "facts": [{"fact": "shortlist", "record": "rival_set"}],
        "rivals": [
            {"formula": "C7H7O2", "ion_formula": "C7H6O2-", "test": "mass"},
            *rivals,
        ],
        SPLIT: {
            "level": split,
            "evidence": f"the split reads {split}",
            "lift": LIFT_4A if split == "4a" else "settle the split",
            "fact": {"fact": "split", "record": "cross_channel"},
        },
        **extra,
    }


def lock(run: str = "run-1", **extra) -> dict:
    return {
        "element": "Br",
        "label": "81Br",
        "batch_peak_id": "bp-line",
        "holds": 9,
        "judged": 10,
        "verdict": "locked",
        "stamping_run_id": run,
        **extra,
    }


def stamping(*entries: dict, run: str = "run-1", **extra) -> dict:
    return {**reading(), "batch_peak_run_id": run, "predicted": list(entries), **extra}


def line(label: str, *, stamped: bool = True, peak: str = "bp-line") -> dict:
    return {
        "label": label,
        "batch_peak_id": peak,
        "stamped": stamped,
        "holds": 8,
        "judged": 10,
    }


def anchor(peak_id: str = "bp-1", **provenance) -> SimpleNamespace:
    return SimpleNamespace(
        batch_peak_id=peak_id,
        consensus_formula=provenance.pop("formula", FORMULA),
        consensus_ion_formula=provenance.pop("ion", ION),
        ionization_mechanism_id=provenance.pop("mechanism", MECH),
        consensus_role=None,
        isotopologue_of=None,
        provenance=provenance,
    )


def locked_anchor(run: str = "run-1", stamped_run: str = "run-1") -> SimpleNamespace:
    """A bromine parent whose 81Br line the stamping stamped and the checks
    locked, the lock read off stamping run ``run``."""
    return anchor(
        batch_checks=reading(batch_peak_run_id="run-2", halogen_lock=lock(run)),
        diagnostic_isotopologues=stamping(line("81Br"), run=stamped_run),
    )


LOCK = BatchFacts(lock={**lock(), "batch_peak_run_id": "run-2"})


def run_record(*rivals: tuple, tof: bool = False, quiet: bool = False) -> dict:
    """The bromophenol row's record as its run states it
    (``identification_level.level_of``), ``rivals`` measured in the tie;
    ``quiet``, its isotope lines under the file's detectability threshold."""
    row = {
        "peak_assignment_id": "pa-1",
        "assigned_formula": FORMULA,
        "ion_formula": ION,
        "role": "M0",
        "tier": "assigned",
        "source": "database",
        "fit_score": 0.9,
        "ionization_mechanism_id": MECH,
        "sample_peak_id": "p-1",
        "sample_peak_intensity": 1000.0,
        "mz_error_ppm": 0.0,
        "owner_peak_assignment_id": None,
        "alternatives": None,
        "provenance": {
            "cross_channel": {"channels": ["[M-H]-"]},
            "plausibility": 1.0,
            GRID_RIVALS: {"fit_score": 0.9, "added": 0, "rivals": []},
            RIVAL_SET: {
                "fit_score": 0.9,
                "rivals": [
                    {
                        "formula": formula,
                        "ion_formula": ion,
                        "fit_score": 0.88,
                        "mz_error_ppm": 0.1,
                        "monoisotopic": True,
                    }
                    for formula, ion in rivals
                ],
                "rivals_unlisted": 0,
            },
        },
    }
    if quiet:
        row["provenance"][PREDICTED_ENVELOPE] = {"lines": []}
    context = LevelContext(
        neutral_channels={}, notation_by_id={MECH: "[M-H]-"}, tof=tof
    )
    return level_of(row, [], context)


class TestTheHalogenLock:
    def test_it_puts_out_the_rivals_without_its_halogen_5a_to_4(self):
        record = overlay(five_a(open_rival(*NO_BR)), ION, LOCK)

        assert record["level"] == "4a"
        assert record["stated"] == "5a"
        assert record["moved_by"] == [BY_LOCK]
        [kept, out] = record["rivals"]
        assert kept["test"] == "mass"
        assert out["test"] == "isotopes"
        assert out["record"] == RECORD_LOCK
        assert "held_by" not in out
        assert "81Br line is stamped in 9 of 10 samples" in out["detail"]
        assert "carries no bromine" in out["detail"]
        assert record["evidence"].startswith(
            "two rivals out: one by mass, one by the batch's isotope checks"
        )
        assert record["evidence"].endswith("the split reads 4a")
        assert record["lift"] == LIFT_4A
        assert {"fact": "isotopes", "record": RECORD_LOCK, "detail": NO_BR[0]} in (
            record["facts"]
        )
        assert SPLIT not in record

    def test_the_letter_of_4_is_the_split_its_run_read(self):
        record = overlay(five_a(open_rival(*NO_BR), split="4b"), ION, LOCK)

        assert record["level"] == "4b"
        assert record["lift"] == "settle the split"

    def test_a_rival_holding_the_halogen_stays_open(self):
        record = overlay(five_a(open_rival(*NO_BR), open_rival(*WITH_BR)), ION, LOCK)

        assert record["level"] == "5a"
        assert record["evidence"].startswith("one rival left open: C5H4BrN")
        tests = {entry["formula"]: entry["test"] for entry in record["rivals"]}
        assert tests == {"C7H7O2": "mass", NO_BR[0]: "isotopes", WITH_BR[0]: "open"}

    @pytest.mark.parametrize(
        "kind, test",
        [
            ("orbitrap", ": an isotope line one of them cannot make"),
            ("tof", ": a heavy-isotope twin one of them cannot make"),
            ("quiet", ""),
        ],
    )
    def test_its_lift_asks_only_of_the_rivals_left_open(self, kind, test):
        own = run_record(NO_BR, WITH_BR, tof=kind == "tof", quiet=kind == "quiet")
        assert f"exclude {NO_BR[0]}, {WITH_BR[0]}{test}" in own["lift"]

        record = overlay(own, ION, LOCK)

        assert record["level"] == "5a"
        assert record["lift"] == own["lift"].replace(
            f"exclude {NO_BR[0]}, {WITH_BR[0]}", f"exclude {WITH_BR[0]}"
        )

    def test_its_lift_names_the_first_three_left_open_as_its_run_names_them(self):
        brominated = [(f"C{n}H4BrN", f"C{n}H3BrN-") for n in (5, 6, 7)]
        own = run_record(NO_BR, *brominated)
        listed = [rival["formula"] for rival in own["rivals"]]
        assert NO_BR[0] in listed[:3]
        assert own["lift"].startswith(f"exclude {', '.join(listed[:3])}:")

        record = overlay(own, ION, LOCK)

        left = [formula for formula in listed if formula != NO_BR[0]]
        assert record["lift"].startswith(f"exclude {', '.join(left)}:")

    def test_its_lift_names_no_rival_its_run_put_out(self):
        by_mass = {**open_rival("C8H9NO4", "C8H8NO4-"), "test": "mass"}
        own = five_a(
            by_mass,
            open_rival(*NO_BR),
            open_rival(*WITH_BR),
            lift=f"exclude {NO_BR[0]}, {WITH_BR[0]}: an isotope line",
        )

        record = overlay(own, ION, LOCK)

        assert record["lift"] == f"exclude {WITH_BR[0]}: an isotope line"

    def test_its_lift_keeps_what_it_asks_of_the_rivals_the_row_does_not_list(self):
        own = five_a(
            open_rival(*NO_BR),
            rivals_unlisted=2,
            lift=f"exclude {NO_BR[0]}: an isotope line; measure the rivals past the "
            "stored shortlist",
        )

        record = overlay(own, ION, LOCK)

        assert record["lift"] == "measure the rivals past the stored shortlist"

    # A member's lift is read as its run stored it: a run from before MAS-1336
    # asked to "measure the shortlist", one since says the rivals were not.
    @pytest.mark.parametrize(
        "unmeasured", [LIFT_RIVALS_UNMEASURED, LIFT_RIVALS_UNMEASURED_BEFORE]
    )
    def test_its_lift_asks_for_the_shortlist_while_an_unmeasured_rival_is_open(
        self, unmeasured
    ):
        unmeasured_br = {
            "formula": WITH_BR[0],
            "ion_formula": WITH_BR[1],
            "test": "open",
        }
        unmeasured_no_br = {**unmeasured_br, "formula": "C7H8O3", "ion_formula": None}
        # The run names its measured open rivals, its unmeasured ones not.
        lift = unmeasured + "; exclude {}: an isotope line"

        kept = overlay(
            five_a(open_rival(*NO_BR), unmeasured_br, lift=lift.format(NO_BR[0])),
            ION,
            LOCK,
        )
        gone = overlay(
            five_a(
                open_rival(*NO_BR),
                unmeasured_no_br,
                open_rival(*WITH_BR),
                lift=lift.format(f"{NO_BR[0]}, {WITH_BR[0]}"),
            ),
            ION,
            LOCK,
        )

        assert kept["lift"] == unmeasured
        assert gone["level"] == "5a"
        assert gone["lift"] == f"exclude {WITH_BR[0]}: an isotope line"

    def test_it_puts_out_nothing_where_every_open_rival_holds_it(self):
        assert overlay(five_a(open_rival(*WITH_BR)), ION, LOCK) is None

    def test_a_rival_of_the_ion_read_off_its_neutral_where_it_names_no_ion(self):
        record = overlay(five_a(open_rival(NO_BR[0], None)), ION, LOCK)

        assert record["level"] == "4a"

    def test_a_rival_reading_another_ions_line_is_not_asked(self):
        rival = open_rival(*NO_BR, monoisotopic=False)

        assert overlay(five_a(rival), ION, LOCK) is None

    def test_a_rival_the_row_does_not_list_holds_it_at_5a(self):
        record = overlay(five_a(open_rival(*NO_BR), rivals_unlisted=2), ION, LOCK)

        assert record["level"] == "5a"
        assert "two rivals not listed, not tested" in record["evidence"]

    def test_a_row_whose_shortlist_was_not_measured_is_not_lifted(self):
        unmeasured = {"level": "5a", "evidence": "rivals not measured", "rivals": []}

        assert overlay(unmeasured, ION, LOCK) is None

    def test_a_row_whose_run_recorded_no_split_stays_at_5a(self):
        record = five_a(open_rival(*NO_BR), lift=f"exclude {NO_BR[0]}: a line")
        record.pop(SPLIT)

        lifted = overlay(record, ION, LOCK)

        assert lifted["level"] == "5a"
        assert "did not record the split" in lifted["evidence"]
        assert lifted["lift"] == "assign the sample again, so its run records the split"

    @pytest.mark.parametrize("fact", ["lock", "34S"])
    def test_a_rival_whose_ion_cannot_be_read_stays_open(self, fact):
        facts = (
            LOCK
            if fact == "lock"
            else read_facts(anchor(diagnostic_isotopologues=stamping(line(fact))), {})
        )

        assert overlay(five_a(open_rival(None, None)), ION, facts) is None

    def test_a_lock_that_did_not_lock_is_no_fact(self):
        facts = read_facts(
            anchor(
                batch_checks=reading(
                    batch_peak_run_id="run-2", halogen_lock=lock(verdict="silicon")
                ),
                diagnostic_isotopologues=stamping(line("81Br")),
            ),
            {},
        )

        assert facts.lock is None


class TestStampedLines:
    @pytest.mark.parametrize(
        "label, element, word, rival",
        [
            ("34S", "S", "sulfur", ("C7H8O3", "C7H7O3-")),
            ("29Si", "Si", "silicon", ("C7H8O3", "C7H7O3-")),
            ("30Si", "Si", "silicon", ("C7H8O3", "C7H7O3-")),
        ],
    )
    def test_a_stamped_line_puts_out_the_rivals_that_cannot_make_it(
        self, label, element, word, rival
    ):
        facts = read_facts(anchor(diagnostic_isotopologues=stamping(line(label))), {})

        assert facts.lines[0]["element"] == element
        record = overlay(five_a(open_rival(*rival)), ION, facts)

        assert record["level"] == "4a"
        assert record["moved_by"] == [BY_LINE]
        out = record["rivals"][1]
        assert out["record"] == RECORD_LINES
        assert f"stamped the reading's {label} line in 8 of 10 samples" in out["detail"]
        assert f"carries no {word}" in out["detail"]

    def test_a_line_not_stamped_is_no_fact(self):
        facts = read_facts(
            anchor(diagnostic_isotopologues=stamping(line("34S", stamped=False))), {}
        )

        assert facts.lines == ()

    def test_a_rival_holding_the_element_stays_open(self):
        facts = read_facts(anchor(diagnostic_isotopologues=stamping(line("34S"))), {})

        assert overlay(five_a(open_rival("C6H6OS", "C6H5OS-")), ION, facts) is None


class TestTheLabelTwin:
    LABELLED = "C3H5O5[15N]-"
    TWIN = BatchFacts(twin={"present": 7, "judged": 9, "batch_peak_id": "bp-twin"})

    def test_a_present_twin_pins_a_labelled_ions_split_4b_to_4a(self):
        record = overlay(
            {"level": "4b", "evidence": "the split is open", "lift": "settle"},
            self.LABELLED,
            self.TWIN,
        )

        assert record["level"] == "4a"
        assert record["stated"] == "4b"
        assert record["moved_by"] == [BY_TWIN]
        assert record["lift"] == LIFT_4A
        assert "14N twin present in 7 of 9 samples" in record["evidence"]
        assert record["evidence"].endswith("its run stated 4b: the split is open")
        assert record["facts"][-1] == {
            "fact": "label_twin",
            "record": "batch_checks.label_twin",
        }

    def test_an_ion_carrying_no_label_is_not_pinned(self):
        assert overlay({"level": "4b", "evidence": "x"}, ION, self.TWIN) is None

    def test_a_twin_not_read_present_is_no_fact(self):
        facts = read_facts(
            anchor(
                ion=self.LABELLED,
                batch_checks={
                    **reading(ion_formula=self.LABELLED),
                    "label_twin": {"verdict": "absent", "present": 0, "judged": 9},
                },
            ),
            {},
        )

        assert facts.twin is None

    def test_it_pins_the_split_of_a_row_the_lock_lifts(self):
        facts = BatchFacts(lock=LOCK.lock, twin=self.TWIN.twin)

        record = overlay(five_a(open_rival(*NO_BR), split="4b"), self.LABELLED, facts)

        assert record["level"] == "4a"
        assert record["moved_by"] == [BY_LOCK, BY_TWIN]

    def test_it_pins_nothing_of_a_row_the_lock_lifts_to_a_split_of_4a(self):
        facts = BatchFacts(lock=LOCK.lock, twin=self.TWIN.twin)

        record = overlay(five_a(open_rival(*NO_BR), split="4a"), self.LABELLED, facts)

        assert record["level"] == "4a"
        assert record["moved_by"] == [BY_LOCK]
        assert record["evidence"].endswith("the split reads 4a")
        assert record["lift"] == LIFT_4A
        assert {"fact": "label_twin", "record": "batch_checks.label_twin"} not in (
            record["facts"]
        )


class TestStampingStands:
    def test_a_lock_whose_stamping_record_no_longer_stands_lifts_nothing(self):
        # A later stamping replaced the record the lock was read off.
        stale = locked_anchor(run="run-1", stamped_run="run-3")

        facts = read_facts(stale, {})

        assert facts.lock is None
        assert overlay(five_a(open_rival(*NO_BR)), ION, facts) is None

    def test_a_lock_whose_stamping_record_stands_is_read(self):
        facts = read_facts(locked_anchor(), {})

        assert facts.lock["stamping_run_id"] == "run-1"
        assert facts.lock["batch_peak_run_id"] == "run-2"
        assert facts.lines == ()

    def test_a_record_read_for_another_reading_gives_no_fact(self):
        moved = locked_anchor()
        moved.consensus_formula = "C6H6O"

        assert not read_facts(moved, {})


#: A halogen-free reading of a peak 4.2c stamped as the bromine parent's line.
LINE_READING = {"formula": "C7H8NO4", "ion": "C7H7NO4-", "mechanism": MECH}


def stamped_line(run: str = "run-1", label: str = "81Br", **reading_) -> tuple:
    """A parent and the line it stamped as its ``label`` line, which the
    line's own members read as a monoisotopic formula."""
    parent = anchor(diagnostic_isotopologues=stamping(line(label), run=run))
    the_line = anchor(
        "bp-line",
        **{**LINE_READING, **reading_},
        stamped_isotopologue_of={
            "batch_peak_id": "bp-1",
            "label": label,
            "stamps": {"s-1": "predicted", "s-2": "picker"},
            "batch_peak_run_id": "run-1",
        },
    )
    return parent, the_line


class TestTheRefutation:
    def test_a_stamped_halogen_line_refutes_a_halogen_free_reading(self):
        parent, the_line = stamped_line()
        facts = read_facts(the_line, {"bp-1": parent})

        record = overlay(
            {
                "level": "4a",
                "evidence": "every rival out",
                "rivals": [{"formula": "C8H12O3", "test": "mass"}],
            },
            LINE_READING["ion"],
            facts,
        )

        assert record["level"] == "5b"
        assert record["stated"] == "4a"
        assert record["moved_by"] == [BY_STAMP]
        assert record["evidence"] == (
            "refuted by the batch's isotopes: the batch stamped this peak as the "
            "81Br line of C6H4BrO- in 2 samples, a line C7H7NO4- cannot make, "
            "holding no bromine; its run stated 4a: every rival out"
        )
        assert "the 81Br line of C6H4BrO-" in record["lift"]
        rival = record["rivals"][0]
        assert rival["formula"] == FORMULA
        assert rival["line"] == "81Br"
        assert rival["test"] == "open"
        assert rival["monoisotopic"] is False
        assert record["facts"][-1]["fact"] == "stamped_halogen_line"

    def test_a_reading_holding_the_halogen_is_not_refuted(self):
        parent, the_line = stamped_line(formula="C5H6BrNO", ion="C5H5BrNO-")
        facts = read_facts(the_line, {"bp-1": parent})

        assert overlay({"level": "4a", "evidence": "x"}, "C5H5BrNO-", facts) is None

    def test_it_goes_before_a_lift(self):
        parent, the_line = stamped_line()
        facts = read_facts(the_line, {"bp-1": parent})
        facts = BatchFacts(lock=LOCK.lock, stamp=facts.stamp)

        record = overlay(five_a(open_rival(*NO_BR)), LINE_READING["ion"], facts)

        assert record["level"] == "5b"
        assert SPLIT not in record

    def test_a_stamp_of_an_earlier_stamping_refutes_nothing(self):
        parent, the_line = stamped_line(run="run-3")

        assert read_facts(the_line, {"bp-1": parent}).stamp is None

    def test_a_stamp_its_parent_no_longer_names_refutes_nothing(self):
        parent, the_line = stamped_line()
        parent.provenance["diagnostic_isotopologues"] = stamping(
            line("81Br", peak="bp-other")
        )

        assert read_facts(the_line, {"bp-1": parent}).stamp is None
        assert read_facts(the_line, {}).stamp is None

    def test_a_stamp_of_another_line_refutes_nothing(self):
        parent, the_line = stamped_line(label="34S")

        assert read_facts(the_line, {"bp-1": parent}).stamp is None

    def test_a_chlorine_stamp_reads_chlorine(self):
        parent, the_line = stamped_line(label="37Cl")
        parent.provenance["diagnostic_isotopologues"] = stamping(line("37Cl"))

        stamp = read_facts(the_line, {"bp-1": parent}).stamp

        assert stamp["element"] == "Cl"
        record = overlay(
            {"level": "5a", "evidence": "x"}, "C7H7NO4-", BatchFacts(stamp=stamp)
        )
        assert record["level"] == "5b"
        assert "holding no chlorine" in record["evidence"]

    def test_a_5b_row_stays_5b_with_the_refutation_named(self):
        parent, the_line = stamped_line()
        facts = read_facts(the_line, {"bp-1": parent})

        record = overlay({"level": "5b", "evidence": "implausible"}, "C7H7NO4-", facts)

        assert record["level"] == "5b"
        assert record["evidence"].endswith("holding no bromine; implausible")


def member(sample: str, level: str, record: dict | None = None, intensity=1.0, ion=ION):
    return MemberLevel(sample, level, record, ion, intensity)


class TestTheBatchPeaksLevel:
    def test_with_no_batch_fact_it_is_the_level_most_members_state(self):
        members = [
            member("s-1", "5a", five_a(open_rival(*NO_BR))),
            member(
                "s-2", "4a", {"level": "4a", "evidence": "pinned", "lift": "L"}, 5.0
            ),
            member(
                "s-3", "4a", {"level": "4a", "evidence": "pinned", "lift": "L"}, 2.0
            ),
        ]

        record = batch_level(anchor(), members, BatchFacts(), "run-9")

        assert record["level"] == "4a"
        assert record["evidence"] == "pinned; 2 of 3 members' runs state 4a"
        assert record["lift"] == "L"
        assert record["members"] == {"4a": 2, "5a": 1}
        assert record["members_moved"] == 0
        assert record["read_from"] == "s-2"
        assert "members_under_batch" not in record
        assert record["batch_facts"] == {}
        assert record["batch_peak_run_id"] == "run-9"
        assert {key: record[key] for key in ("formula", "ion_formula")} == {
            "formula": FORMULA,
            "ion_formula": ION,
        }

    def test_the_lock_lifts_the_members_and_the_batch_reads_most_of_them(self):
        members = [
            member("s-1", "5a", five_a(open_rival(*NO_BR)), 3.0),
            member("s-2", "5a", five_a(open_rival(*NO_BR)), 1.0),
            member("s-3", "5a", five_a(open_rival(*WITH_BR)), 9.0),
        ]

        record = batch_level(anchor(), members, LOCK)

        assert record["level"] == "4a"
        assert record["members"] == {"5a": 3}
        assert record["members_under_batch"] == {"4a": 2, "5a": 1}
        assert record["members_moved"] == 2
        assert record["read_from"] == "s-1"
        assert record["evidence"].endswith(
            "2 of 3 members read 4a under the batch's isotope checks"
        )
        assert record["moved_by"] == [BY_LOCK]
        assert "stated" not in record
        assert record["batch_facts"]["halogen_lock"]["element"] == "Br"

    def test_it_shows_a_member_the_batch_moved_so_its_lines_name_the_fact(self):
        # The brightest member at 4a was there by its own run; the batch's level
        # is 4a because the lock moved the others, and its lines say so.
        own = {"level": "4a", "evidence": "pinned by its run", "lift": "L"}
        members = [
            member("s-1", "4a", own, 9.0),
            member("s-2", "5a", five_a(open_rival(*NO_BR)), 2.0),
            member("s-3", "5a", five_a(open_rival(*NO_BR)), 1.0),
            member("s-4", "5a", five_a(open_rival(*WITH_BR)), 5.0),
        ]

        record = batch_level(anchor(), members, LOCK)

        assert record["level"] == "4a"
        assert record["members"] == {"4a": 1, "5a": 3}
        assert record["read_from"] == "s-2"
        assert record["moved_by"] == [BY_LOCK]
        assert "the batch's isotope checks" in record["evidence"]
        assert record["rivals"][1]["record"] == RECORD_LOCK

    def test_no_fact_is_named_where_the_members_it_moved_left_the_level_as_it_was(self):
        members = [
            member("s-1", "5a", five_a(open_rival(*NO_BR))),
            member("s-2", "5a", five_a(open_rival(*WITH_BR))),
            member("s-3", "5a", five_a(open_rival(*WITH_BR))),
        ]

        record = batch_level(anchor(), members, LOCK)

        assert record["level"] == "5a"
        assert record["members_moved"] == 1
        assert "moved_by" not in record

    def test_a_tie_reads_as_the_level_further_down_the_scale(self):
        members = [
            member("s-1", "5a", five_a(open_rival(*NO_BR))),
            member("s-2", "5a", five_a(open_rival(*WITH_BR))),
        ]

        assert batch_level(anchor(), members, LOCK)["level"] == "5a"

    def test_members_at_3b_keep_their_level_and_so_does_the_batch(self):
        three_b = {"level": "3b", "evidence": "an acid of this formula", "lift": "list"}
        members = [
            member("s-1", "3b", three_b),
            member("s-2", "3b", three_b),
            member("s-3", "5a", five_a(open_rival(*NO_BR))),
        ]

        record = batch_level(anchor(), members, LOCK)

        assert record["level"] == "3b"
        assert record["members_under_batch"] == {"3b": 2, "4a": 1}

    def test_a_member_at_2b_is_never_brought_down_by_a_lift(self):
        two_b = {"level": "2b", "evidence": "one plausible structure", "lift": "L1"}

        assert overlay(two_b, ION, LOCK) is None
        record = batch_level(anchor(), [member("s-1", "2b", two_b)], LOCK)
        assert record["level"] == "2b"
        assert record["evidence"].startswith("one plausible structure")

    def test_a_record_that_is_not_the_members_level_is_not_read(self):
        # The column says 4a; a record left from another statement is not it.
        record = batch_level(
            anchor(), [member("s-1", "4a", five_a(open_rival(*NO_BR)))], LOCK
        )

        assert record["level"] == "4a"
        assert record["evidence"] == "1 of 1 members' runs state 4a"

    def test_a_member_whose_record_is_missing_counts_at_its_column(self):
        record = batch_level(anchor(), [member("s-1", "4b", None)], LOCK)

        assert record["level"] == "4b"
        assert record["evidence"] == "1 of 1 members' runs state 4b"

    def test_no_member_stating_a_level_states_none(self):
        assert batch_level(anchor(), [member("s-1", None)], LOCK) is None
        assert batch_level(anchor(), [], LOCK) is None


class TestTheStage:
    def test_it_reads_every_anchor_that_commits_a_formula(self):
        locked = locked_anchor()
        role = anchor("bp-reagent", formula=None, ion="NO3-")
        members = {"bp-1": [member("s-1", "5a", five_a(open_rival(*NO_BR)))]}

        records, summary = read_levels([locked, role], members, batch_peak_run_id="r")

        assert set(records) == {"bp-1"}
        assert records["bp-1"]["level"] == "4a"
        assert summary["anchors_read"] == 1
        assert summary["levels"] == {"4a": 1}
        assert summary["lifted"] == 1
        assert summary["refuted"] == 0
        assert summary["by_halogen_lock"] == 1
        assert summary["members_moved"] == 1
        assert (
            "1 lifted by the batch's isotope checks (1 by a halogen lock)"
            in (levels_outcome(summary, "b")["message"])
        )

    def test_a_source_roles_reading_states_no_level(self):
        reagent = locked_anchor()
        reagent.consensus_role = "reagent"
        members = {"bp-1": [member("s-1", "5a", five_a(open_rival(*NO_BR)))]}

        records, summary = read_levels([reagent], members)

        assert records == {}
        assert summary["anchors_read"] == 0

    def test_an_isotope_line_states_no_level_of_its_own(self):
        line = anchor("bp-13c")
        line.isotopologue_of = "bp-1"
        members = {"bp-13c": [member("s-1", "4a", {"level": "4a", "evidence": "x"})]}

        records, summary = read_levels([line], members)

        assert records == {}
        assert summary["anchors_read"] == 0

    def test_it_counts_a_refutation(self):
        parent, the_line = stamped_line()
        members = {
            "bp-line": [
                member("s-1", "4a", {"level": "4a", "evidence": "x"}, ion="C7H7NO4-")
            ]
        }

        records, summary = read_levels([parent, the_line], members)

        assert records["bp-line"]["level"] == "5b"
        assert summary["refuted"] == 1
        assert summary["by_stamped_halogen_line"] == 1

    def test_the_write_keeps_only_records_whose_reading_stands(self):
        moved = anchor()
        moved.consensus_formula = "C6H6O"
        records = {"bp-1": batch_level(anchor(), [member("s-1", "4a", None)], LOCK)}

        assert still_read([anchor()], records) == records
        assert still_read([moved], records) == {}
        assert still_read([], records) == {}

    def test_the_stage_is_the_passs_last_and_owns_its_record(self):
        assert list(BatchStage)[-1] is BatchStage.LEVEL
        assert STAGE_OF_KEY[BATCH_LEVEL_KEY] is BatchStage.LEVEL


def with_level(**reading_) -> SimpleNamespace:
    record = batch_level(
        anchor(), [member("s-1", "5a", five_a(open_rival(*NO_BR)))], LOCK, "run-9"
    )
    return anchor(**reading_, identification_level=record)


class TestTheRecordStands:
    def test_it_stands_while_the_consensus_names_its_reading(self):
        standing = with_level()

        assert standing_batch_level_record(standing)["level"] == "4a"
        assert level_columns(standing_batch_level_record(standing))["level"] == "4a"
        assert batch_level_fields(standing)["batch_level"] == "4a"

    def test_not_once_the_reading_moved(self):
        moved = with_level(formula="C6H6O")

        assert batch_level_record_of(moved) is not None
        assert standing_batch_level_record(moved) is None
        assert level_columns(None) == {
            "level": None,
            "level_evidence": None,
            "level_lift": None,
            "identification_level": None,
        }
        assert batch_level_fields(moved) == {
            "batch_level": None,
            "batch_level_evidence": None,
            "batch_level_lift": None,
        }

    def test_the_consensus_keeps_it_and_marks_it_stale_where_the_reading_moves(self):
        record = with_level().provenance[BATCH_LEVEL_KEY]
        members = [
            {
                "sample_item_id": f"s-{i}",
                "assigned_formula": FORMULA,
                "ion_formula": ION,
                "ionization_mechanism_id": MECH,
                "tier": "assigned",
                "fit_score": 0.9,
                "intensity": 1.0,
                "role": "M0",
            }
            for i in range(3)
        ]

        kept = compute_consensus(members, batch_peak_id="bp-1", batch_level=record)
        moved = compute_consensus(
            [{**m, "assigned_formula": "C6H6O"} for m in members],
            batch_peak_id="bp-1",
            batch_level=record,
        )

        assert kept.provenance[BATCH_LEVEL_KEY] == record
        assert moved.provenance[BATCH_LEVEL_KEY] == {**record, "stale": True}
        assert (
            batch_level_record_of(SimpleNamespace(provenance=moved.provenance)) is None
        )


def sample_row(level: str = "5a", record: dict | None = None, **extra) -> dict:
    record = record if record is not None else five_a(open_rival(*NO_BR))
    return {
        "role": "M0",
        "assigned_formula": FORMULA,
        "ion_formula": ION,
        "level": level,
        "provenance": {"identification_level": record},
        **extra,
    }


class TestASamplesRow:
    def test_the_batchs_level_beside_its_own_where_the_batch_lifts_it(self):
        found = row_batch_level(sample_row(), with_level())

        assert found["level"] == "4a"
        assert found["stated"] == "5a"
        assert found["borrowed"] is True
        assert found["batch_peak_id"] == "bp-1"
        assert found["batch_level"] == "4a"
        assert found["rivals"][1]["record"] == RECORD_LOCK

    def test_none_where_the_batch_moves_nothing(self):
        record = five_a(open_rival(*WITH_BR))

        assert row_batch_level(sample_row(record=record), with_level()) is None
        pinned = {"level": "4a", "evidence": "x"}
        assert row_batch_level(sample_row("4a", pinned), with_level()) is None
        # The lock puts one rival out and leaves one: the row is still at 5a.
        partly = five_a(open_rival(*NO_BR), open_rival(*WITH_BR))
        assert row_batch_level(sample_row(record=partly), with_level()) is None

    def test_none_on_a_row_reading_another_formula_or_role(self):
        assert (
            row_batch_level(sample_row(assigned_formula="C6H6O"), with_level()) is None
        )
        assert row_batch_level(sample_row(role="iso_child"), with_level()) is None
        assert row_batch_level(sample_row(), None) is None

    def test_none_where_no_level_of_the_batch_stands(self):
        assert row_batch_level(sample_row(), with_level(formula="C6H6O")) is None
        assert row_batch_level(sample_row(), anchor()) is None

    def test_none_where_the_rows_record_is_not_its_level(self):
        assert row_batch_level(sample_row(level="4b"), with_level()) is None
        assert row_batch_level({**sample_row(), "provenance": {}}, with_level()) is None
