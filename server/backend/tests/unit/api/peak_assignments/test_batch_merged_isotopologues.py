"""Unit tests for the batch's merged-isotopologue reading, its pure parts
(step 4.2f).

No DB: which rows are read against which parents' lines, which are stripped,
kept or held and why, what each sample of a stripped row becomes, whether the
members' vote turns, what the records and the run's summary say, how the
consensus keeps the records, what the ledger and a snapshot serve, and how a
sample's row is rewritten as its owner's line. The lines and the share's
reading are the library's (``libraries/tools/tests/test_merged_isotopologues.py``).
See ``batch_merged_isotopologues.py``.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments.batch_merged_isotopologues import (
    ALREADY,
    HELD_CURATED,
    HELD_HAND_ASSIGNED,
    HELD_OWN_13C,
    HELD_PARENT_ABSENT,
    KEPT_CARRIES_LINES,
    KEPT_CLAIMED,
    KEPT_PARENT_ABSENT,
    KEPT_PARENT_READS_OTHERWISE,
    MAX_LISTED,
    Member,
    Plan,
    Row,
    Written,
    _still_planned,
    carries_own_13c,
    lines_of,
    parent_entry,
    plan_batch,
    row_record,
    row_rewrite,
    sample_decision,
    stripping_outcome,
    summarise,
    vote_turns,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    MERGED_ISOTOPOLOGUE_OF_KEY,
    MERGED_ISOTOPOLOGUES_KEY,
    compute_consensus,
    merged_isotopologue_of_record,
    merged_isotopologue_of_summary,
    merged_isotopologues_record,
    resolution_adaptive_tol_ppm,
    standing_merged_isotopologue_of_record,
    standing_merged_isotopologues_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_backend.api.new.peak_assignments.engine import (
    DISPLACED_BY_CLAIM,
    SOURCE_DATABASE,
    SOURCE_MANUAL,
    SOURCE_UNTARGETED,
)
from mascope_tools.composition.merged_isotopologues import (
    MEDIAN_HIGH,
    VERDICT_ISOTOPOLOGUE,
)


SAMPLES = [f"s{i}" for i in range(6)]
MECH = "mech-h"
#: An Orbitrap anchor's frozen tolerance: a 2 ppm half FWHM and the margin.
ORBI_TOL = resolution_adaptive_tol_ppm(200.0, 250000.0)
PARENT_ION = "C10H15O4-"
PARENT_MZ = 199.097583


def anchor(key, mz, formula=None, ion=None, *, brightest=None, **extra):
    """A batch peak as the reading reads one: a merged row where it names a
    formula."""
    fields = dict(
        batch_peak_id=key,
        mz=mz,
        mz_tol_ppm=ORBI_TOL,
        ionization_mode_id="mode-1",
        intensity_variable="sum_peak_heights",
        consensus_formula=formula,
        consensus_ion_formula=ion,
        ionization_mechanism_id=MECH if formula else None,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=None,
        isotopologue_of=None,
        max_intensity=brightest,
        support_fraction=1.0 if formula else None,
        provenance=None,
        candidates=[],
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def members(
    mz, intensities, formula=None, *, role="M0", owner=None, source=None, spread=None
):
    """Sample -> member, one intensity per sample (None for absent), each at
    ``mz`` moved by its sample's ``spread`` in ppm."""
    out = {}
    for i, (sample, value) in enumerate(zip(SAMPLES, intensities)):
        if value is None:
            continue
        out[sample] = Member(
            sample_item_id=sample,
            sample_peak_id=f"p-{mz:.4f}-{i}",
            intensity=value,
            mz=mz * (1 + (spread[i] if spread else 0.0) / 1e6),
            role=role if formula or owner else "unassigned",
            owner_batch_peak_id=owner,
            formula=formula,
            ionization_mechanism_id=MECH if formula else None,
            source=source or (SOURCE_UNTARGETED if formula else None),
        )
    return out


PARENT = [1e6 * (1 + i) for i in range(6)]


def the_parent(key="P", mz=PARENT_MZ, intensities=PARENT, **extra):
    a = anchor(
        key,
        mz,
        "C10H16O4",
        PARENT_ION,
        brightest=max(v for v in intensities if v is not None),
        **extra,
    )
    return a, members(mz, intensities, "C10H16O4")


def line(parent_anchor, label):
    """Where a parent's line sits, and its share."""
    row = Row(parent_anchor, {}, parent_anchor.mz)
    [found] = [x for x in lines_of(row) if x.isotopologue.label == label]
    return found.mz, found.isotopologue.ratio


def the_row(key, mz, intensities, formula="C9H12N2O2", **extra):
    a = anchor(
        key,
        mz,
        formula,
        "C9H11N2O2-",
        brightest=max(v for v in intensities if v is not None),
        **extra,
    )
    return a, members(mz, intensities, formula)


def plan(*pairs, hand_assigned=()):
    anchors = [a for a, _ in pairs]
    by_id = {a.batch_peak_id: m for a, m in pairs}
    return plan_batch(anchors, by_id, hand_assigned)


def at_share(intensities, share, factor=1.0):
    return [None if v is None else v * share * factor for v in intensities]


class TestTheReading:
    def test_a_13c_row_is_stripped_to_its_parent(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        row = the_row("X", mz, at_share(PARENT, share))
        found = plan(parent, row)
        [pair] = found.stripped
        assert (pair.row.batch_peak_id, pair.parent.batch_peak_id) == ("X", "P")
        assert pair.line.isotopologue.label == "13C"
        assert pair.reading.verdict == VERDICT_ISOTOPOLOGUE
        assert pair.reading.spectra == 6
        assert pair.reading.median_factor == pytest.approx(1.0)
        counts = found.counts[("mode-1", "orbi")]
        assert counts["merged_rows"] == 2
        assert (counts["at_a_line"], counts["stripped"]) == (1, 1)

    def test_the_diagnostic_lines_are_read_too(self):
        parent = the_parent()
        mz, share = line(parent[0], "18O")
        row = the_row("X", mz, at_share(PARENT, share, 0.6))
        [pair] = plan(parent, row).stripped
        assert pair.line.isotopologue.label == "18O"

    def test_a_row_at_the_line_off_its_share_is_kept(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        row = the_row("X", mz, at_share(PARENT, share, MEDIAN_HIGH * 1.2))
        found = plan(parent, row)
        assert found.stripped == [] and found.held == []
        counts = found.counts[("mode-1", "orbi")]
        assert (counts["at_a_line"], counts["off_ratio"]) == (1, 1)

    def test_a_row_often_twice_its_share_is_kept(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        # A median of one, with one sample of six at three times the share.
        factors = [1.0, 1.0, 1.0, 1.0, 1.0, 3.0]
        row = the_row("X", mz, [v * share * f for v, f in zip(PARENT, factors)])
        assert plan(parent, row).stripped == []

    def test_a_row_on_too_few_samples_is_untestable(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        row = the_row(
            "X", mz, at_share(PARENT[:2], share) + [None] * 4, formula="C9H12N2O2"
        )
        found = plan(parent, row)
        assert found.stripped == []
        assert found.counts[("mode-1", "orbi")]["untestable"] == 1

    def test_a_row_outside_the_window_is_not_read(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        row = the_row("X", mz * (1 + 1.5e-6), at_share(PARENT, share))
        found = plan(parent, row)
        assert found.stripped == []
        assert found.counts[("mode-1", "orbi")]["at_a_line"] == 0

    def test_the_window_widens_with_the_batchs_mass_scatter(self):
        """Members scattered by one ppm around their rows read four sigma,
        about six ppm, so a row 1.5 ppm off the line is at it."""
        spread = [-1.0, 1.0, -1.0, 1.0, -1.0, 1.0]
        parent_anchor, _ = the_parent()
        parent = (
            parent_anchor,
            members(PARENT_MZ, PARENT, "C10H16O4", spread=spread),
        )
        mz, share = line(parent_anchor, "13C")
        row_anchor, _ = the_row("X", mz * (1 + 1.5e-6), at_share(PARENT, share))
        row = (
            row_anchor,
            members(row_anchor.mz, at_share(PARENT, share), "C9H12N2O2", spread=spread),
        )
        found = plan(parent, row)
        [pair] = found.stripped
        assert pair.window_ppm == pytest.approx(4 * 1.4826, rel=1e-3)
        assert pair.scatter_ppm == pytest.approx(1.4826, rel=1e-3)
        counts = found.counts[("mode-1", "orbi")]
        assert counts["window_ppm"] == pytest.approx(4 * 1.4826, rel=1e-3)
        assert counts["scatter_ppm"] == pytest.approx(1.4826, rel=1e-3)

    def test_a_dimmer_parent_is_no_parent(self):
        parent = the_parent(intensities=[1e4] * 6)
        mz, share = line(parent[0], "13C")
        row = the_row("X", mz, [2e4] * 6)
        assert plan(parent, row).counts[("mode-1", "orbi")]["at_a_line"] == 0

    def test_a_row_above_is_read_against_the_parent_below_only(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        # The same pair the other way round: the parent is no line of its
        # own isotope line.
        found = plan(parent, the_row("X", mz, at_share(PARENT, share)))
        assert [p.row.batch_peak_id for p in found.stripped] == ["X"]

    def test_a_stripped_row_is_no_parent_of_a_row_above_it(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        row = the_row("X", mz, at_share(PARENT, share))
        # A row at the stripped row's own 13C line, at its share of it.
        above_mz, above_share = line(row[0], "13C")
        above = the_row(
            "Y",
            above_mz,
            at_share(at_share(PARENT, share), above_share),
            formula="C8H8N4O",
        )
        found = plan(parent, row, above)
        assert [p.row.batch_peak_id for p in found.stripped] == ["X"]

    def test_two_parents_the_share_nearest_one_wins(self):
        first = the_parent("P1")
        mz, share = line(first[0], "13C")
        # A second parent whose 18O line falls where the first's 13C line is.
        o18_offset = line(first[0], "18O")[0] - first[0].mz
        second = the_parent("P2", mz=mz - o18_offset, intensities=PARENT)
        _, share18 = line(second[0], "18O")
        row = the_row("X", mz, at_share(PARENT, share))
        [pair] = plan(first, second, row).stripped
        # Read as the 18O line its height is about six times too tall for;
        # as the 13C line, exactly.
        assert share / share18 > MEDIAN_HIGH
        assert pair.parent.batch_peak_id == "P1"

    def test_a_line_one_peak_cannot_show_is_not_read(self):
        """At a TOF's resolving power a nitrogen ion's 15N line sits inside
        its 13C peak: the 13C line is read, at the peak's merged share, and
        the 15N line is not."""
        ion = "C10H15N2O4-"
        sharp = anchor("P", PARENT_MZ, "C10H16N2O4", ion)
        blunt = anchor(
            "P",
            PARENT_MZ,
            "C10H16N2O4",
            ion,
            mz_tol_ppm=resolution_adaptive_tol_ppm(200.0, 5000.0),
        )
        labels = lambda a: {x.isotopologue.label for x in lines_of(Row(a))}  # noqa: E731
        assert {"13C", "15N"} <= labels(sharp)
        assert "15N" not in labels(blunt)
        assert "13C" in labels(blunt)

    def test_a_row_that_is_no_merged_ion_is_not_read(self):
        """An unassigned batch peak at a parent's line, at its share: no
        compound of the ledger, so nothing to strip."""
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        bare = (
            anchor("X", mz, brightest=1.0),
            members(mz, at_share(PARENT, share)),
        )
        found = plan(parent, bare)
        assert found.stripped == []
        assert found.counts[("mode-1", "orbi")]["merged_rows"] == 1

    def test_a_sample_whose_parent_peak_has_no_intensity_is_not_read(self):
        parent_anchor, parent_members = the_parent()
        for sample, value in (("s0", 0.0), ("s1", None)):
            parent_members[sample] = Member(
                **{**vars(parent_members[sample]), "intensity": value}
            )
        mz, share = line(parent_anchor, "13C")
        [pair] = plan(
            (parent_anchor, parent_members), the_row("X", mz, at_share(PARENT, share))
        ).stripped
        assert pair.reading.spectra == 4

    def test_two_parents_as_near_the_more_samples_win(self):
        """Both read the row at exactly its share; the one read over more
        samples wins, though the other is brighter and its id sorts first."""
        first = the_parent("P1")
        mz, share = line(first[0], "13C")
        o18_offset = line(first[0], "18O")[0] - first[0].mz
        second_anchor, _ = the_parent("P0", mz=mz - o18_offset)
        _, share18 = line(second_anchor, "18O")
        brighter_parent = [v * share / share18 for v in PARENT[:4]] + [None, None]
        second = the_parent("P0", mz=mz - o18_offset, intensities=brighter_parent)
        row = the_row("X", mz, at_share(PARENT, share))
        [pair] = plan(first, second, row).stripped
        assert pair.parent.batch_peak_id == "P1"
        assert pair.reading.spectra == 6

    def test_a_partition_reads_its_own_rows(self):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        row = the_row(
            "X", mz, at_share(PARENT, share), intensity_variable="sum_peak_areas"
        )
        found = plan(parent, row)
        assert found.stripped == []
        assert set(found.counts) == {("mode-1", "orbi"), ("mode-1", "tof")}


class TestWhatIsNeverStripped:
    def _pair(self, **extra):
        parent = the_parent()
        mz, share = line(parent[0], "13C")
        return parent, the_row("X", mz, at_share(PARENT, share), **extra)

    def test_a_curated_row_is_held(self):
        parent, row = self._pair(provenance={"manual": {"formula": "C9H12N2O2"}})
        found = plan(parent, row)
        assert found.stripped == []
        [held] = found.held
        assert held.reason == HELD_CURATED
        assert found.counts[("mode-1", "orbi")]["held"] == 1

    def test_a_row_a_sample_of_which_is_a_hand_assignment_is_held(self):
        parent, row = self._pair()
        [held] = plan(parent, row, hand_assigned={"X"}).held
        assert held.reason == HELD_HAND_ASSIGNED

    def test_a_row_whose_identity_a_person_brought_is_held(self):
        parent, (a, m) = self._pair()
        m = {
            sample: Member(**{**vars(member), "source": SOURCE_MANUAL})
            if sample == "s3"
            else member
            for sample, member in m.items()
        }
        [held] = plan(parent, (a, m)).held
        assert held.reason == HELD_HAND_ASSIGNED

    def test_a_row_carrying_its_own_13c_line_is_held(self):
        parent, row = self._pair()
        own_mz, own_share = line(row[0], "13C")
        own = (
            anchor(
                "Y",
                own_mz,
                "C9H12N2O2",
                "[13C]C8H11N2O2-",
                brightest=1.0,
                isotopologue_of="X",
            ),
            members(own_mz, [1.0] * 6, "C9H12N2O2", role="iso_child", owner="X"),
        )
        found = plan(parent, row, own)
        assert [h.reason for h in found.held] == [HELD_OWN_13C]
        assert carries_own_13c(
            [Row(a, m, a.mz) for a, m in (parent, row, own)], 1.0
        ) == {"X"}

    def test_a_line_of_its_own_elsewhere_holds_no_row(self):
        """The batch peak the row owns is its 18O line, not its 13C line,
        and one sample's run read it so: the row is stripped all the same,
        that sample left as it was."""
        parent, row = self._pair()
        o18_mz, _ = line(row[0], "18O")
        own = (
            anchor(
                "Y",
                o18_mz,
                "C9H12N2O2",
                "C9H11N2O2-",
                brightest=1.0,
                isotopologue_of="X",
            ),
            members(
                o18_mz, [1.0] + [None] * 5, "C9H12N2O2", role="iso_child", owner="X"
            ),
        )
        found = plan(parent, row, own)
        assert [p.row.batch_peak_id for p in found.stripped] == ["X"]

    def test_a_row_too_few_of_whose_samples_hold_the_parent_is_held(self):
        parent = the_parent(intensities=PARENT[:3] + [None] * 3)
        mz, share = line(parent[0], "13C")
        row = the_row("X", mz, at_share(PARENT, share))
        [held] = plan(parent, row).held
        assert held.reason == HELD_PARENT_ABSENT


class TestEachSample:
    def setup_method(self):
        parent_anchor, parent_members = the_parent()
        self.parent = Row(parent_anchor, parent_members, PARENT_MZ)
        mz, share = line(parent_anchor, "13C")
        row_anchor, row_members = the_row("X", mz, at_share(PARENT, share))
        self.row = Row(row_anchor, row_members, mz)

    def _set(self, row, sample, **changes):
        row.members[sample] = Member(**{**vars(row.members[sample]), **changes})

    def test_a_sample_holding_the_parents_reading_is_rewritten(self):
        assert sample_decision(self.row, self.parent, "s0", set()) is None

    def test_a_sample_already_holding_the_line_is_left(self):
        self._set(self.row, "s0", role="iso_child", owner_batch_peak_id="P")
        assert sample_decision(self.row, self.parent, "s0", set()) == ALREADY

    def test_a_sample_without_the_parent_is_kept(self):
        del self.parent.members["s0"]
        assert sample_decision(self.row, self.parent, "s0", set()) == (
            KEPT_PARENT_ABSENT
        )

    @pytest.mark.parametrize(
        "changes",
        [
            {"formula": "C11H20O3"},
            {"ionization_mechanism_id": "mech-other"},
            {"role": "iso_child", "owner_batch_peak_id": "Q"},
        ],
    )
    def test_a_sample_whose_parent_reads_otherwise_is_kept(self, changes):
        self._set(self.parent, "s0", **changes)
        assert sample_decision(self.row, self.parent, "s0", set()) == (
            KEPT_PARENT_READS_OTHERWISE
        )

    def test_a_sample_whose_row_a_pre_pass_claimed_is_kept(self):
        self._set(self.row, "s0", role="reagent", formula=None)
        assert sample_decision(self.row, self.parent, "s0", set()) == KEPT_CLAIMED

    def test_a_sample_whose_row_carries_lines_is_kept(self):
        assert sample_decision(self.row, self.parent, "s0", {("s0", "X")}) == (
            KEPT_CARRIES_LINES
        )
        assert sample_decision(self.row, self.parent, "s1", {("s0", "X")}) is None

    def test_an_unassigned_member_is_the_line_there_too(self):
        self._set(self.row, "s0", role="unassigned", formula=None)
        assert sample_decision(self.row, self.parent, "s0", set()) is None


class TestTheWrite:
    def test_a_pair_is_written_while_both_read_what_it_was_read_for(self):
        pair = _stripped_pair()
        row, parent = pair.row.anchor, pair.parent.anchor
        assert _still_planned(pair, {"X": row, "P": parent})
        moved = [
            {
                "X": SimpleNamespace(**{**vars(row), "consensus_formula": "C8H8N4O"}),
                "P": parent,
            },
            {
                "X": SimpleNamespace(**{**vars(row), "ionization_mechanism_id": "m2"}),
                "P": parent,
            },
            {
                "X": SimpleNamespace(**{**vars(row), "isotopologue_of": "P"}),
                "P": parent,
            },
            {
                "X": row,
                "P": SimpleNamespace(**{**vars(parent), "consensus_formula": "C9H8"}),
            },
            {
                "X": row,
                "P": SimpleNamespace(
                    **{**vars(parent), "consensus_ion_formula": "C10H15O4+"}
                ),
            },
            {
                "X": row,
                "P": SimpleNamespace(
                    **{**vars(parent), "ionization_mechanism_id": "m2"}
                ),
            },
            {"X": row},
            {"P": parent},
        ]
        for anchors in moved:
            assert not _still_planned(pair, anchors)

    def test_a_samples_row_is_rewritten_beside_an_in_app_parent_row_of_its_run(self):
        own = SimpleNamespace(peak_assignment_run_id="r1")
        assert (
            row_rewrite(own, SimpleNamespace(peak_assignment_run_id="r1"), {"r1"})
            is None
        )
        assert row_rewrite(None, own, {"r1"}) == "no_row"
        assert row_rewrite(own, None, {"r1"}) == "no_parent_row"
        other_run = SimpleNamespace(peak_assignment_run_id="r2")
        assert row_rewrite(own, other_run, {"r1", "r2"}) == "no_parent_row"
        assert row_rewrite(
            own, SimpleNamespace(peak_assignment_run_id="r1"), set()
        ) == ("imported")


class TestTheVote:
    def _row(self, formulas):
        return Row(
            anchor("X", 200.0, "C9H12N2O2", "C9H11N2O2-"),
            {
                f"s{i}": Member(
                    f"s{i}",
                    f"x-{i}",
                    1.0,
                    200.0,
                    role="M0" if formula else ("reagent" if formula == "" else None),
                    formula=formula or None,
                )
                for i, formula in enumerate(formulas)
            },
        )

    def test_a_strict_majority_turns_it(self):
        row = self._row(["Y"] * 5)
        decisions = {"s0": None, "s1": None, "s2": ALREADY, "s3": "x", "s4": "x"}
        assert vote_turns(row, decisions)

    def test_a_tie_does_not(self):
        row = self._row(["Y"] * 4)
        assert not vote_turns(row, {"s0": None, "s1": None, "s2": "x", "s3": "x"})

    def test_an_unassigned_member_left_as_it_is_casts_no_vote(self):
        decisions = {"s0": None, "s1": None, "s2": "x", "s3": "x"}
        assert vote_turns(self._row(["Y", "Y", None, None]), decisions)
        assert not vote_turns(self._row(["Y", "Y", "Y", "Y"]), decisions)

    def test_claimed_members_outnumbering_the_rest_hold_the_row(self):
        row = self._row(["Y", "", "", ""])
        assert not vote_turns(row, {"s0": None, "s1": "x", "s2": "x", "s3": "x"})


def _stripped_pair():
    parent = the_parent()
    mz, share = line(parent[0], "13C")
    [pair] = plan(parent, the_row("X", mz, at_share(PARENT, share))).stripped
    return pair


class TestTheRecords:
    def _records(self):
        pair = _stripped_pair()
        record = row_record(
            pair,
            line_ion_formula="[13C]C9H15O4-",
            displaced_members={"s0": {"sample_item_id": "s0", "role": 1}},
            kept={KEPT_PARENT_ABSENT: 1},
            rows_rewritten=2,
            rows_kept={"no_row": 4},
            batch_peak_run_id="run-strip",
        )
        return pair, record

    def test_the_row_records_its_parent_the_line_and_what_it_read(self):
        pair, record = self._records()
        assert record["batch_peak_id"] == "P"
        assert record["formula"] == "C10H16O4"
        assert record["ion_formula"] == PARENT_ION
        assert record["ionization_mechanism_id"] == MECH
        assert record["label"] == "13C"
        assert record["line_ion_formula"] == "[13C]C9H15O4-"
        assert record["spectra"] == 6
        assert record["verdict"] == VERDICT_ISOTOPOLOGUE
        assert record["displaced"] == {
            "formula": "C9H12N2O2",
            "ion_formula": "C9H11N2O2-",
            "ionization_mechanism_id": MECH,
            "tier": "assigned",
            "support_fraction": 1.0,
        }
        assert (record["members_rewritten"], record["rows_rewritten"]) == (1, 2)
        assert record["rows_kept"] == {"no_row": 4}
        assert record["samples_kept"] == {KEPT_PARENT_ABSENT: 1}
        assert record["batch_peak_run_id"] == "run-strip"

    def test_the_parent_records_the_line(self):
        pair, record = self._records()
        entry = parent_entry(pair, record)
        assert entry["batch_peak_id"] == "X"
        assert entry["label"] == "13C"
        assert entry["displaced_formula"] == "C9H12N2O2"
        assert entry["batch_peak_run_id"] == "run-strip"

    def test_the_consensus_keeps_the_rows_record_while_its_members_name_the_parent(
        self,
    ):
        _, record = self._records()
        line_members = [
            {
                "assigned_formula": "C10H16O4",
                "role": "iso_child",
                "tier": "assigned",
                "fit_score": 0.9,
                "intensity": 1.0,
                "owner_batch_peak_id": "P",
            }
        ] * 3
        kept = compute_consensus(line_members, "X", merged_isotopologue_of=record)
        assert kept.isotopologue_of == "P"
        assert kept.provenance[MERGED_ISOTOPOLOGUE_OF_KEY] is record
        moved = [{**m, "role": "M0", "owner_batch_peak_id": None} for m in line_members]
        assert (
            MERGED_ISOTOPOLOGUE_OF_KEY
            not in compute_consensus(
                moved, "X", merged_isotopologue_of=record
            ).provenance
        )

    def test_the_consensus_keeps_the_parents_record_while_it_reads_the_same(self):
        record = {"formula": "C10H16O4", "ionization_mechanism_id": MECH, "lines": []}
        parent = [
            {
                "assigned_formula": "C10H16O4",
                "ionization_mechanism_id": MECH,
                "role": "M0",
                "tier": "assigned",
                "fit_score": 0.9,
                "intensity": 1.0,
            }
        ] * 3
        kept = compute_consensus(parent, "P", merged_isotopologues=record)
        assert kept.provenance[MERGED_ISOTOPOLOGUES_KEY] is record
        moved = [{**m, "assigned_formula": "C11H20O3"} for m in parent]
        assert (
            MERGED_ISOTOPOLOGUES_KEY
            not in compute_consensus(moved, "P", merged_isotopologues=record).provenance
        )
        other_mechanism = [
            {**m, "ionization_mechanism_id": "mech-other"} for m in parent
        ]
        assert (
            MERGED_ISOTOPOLOGUES_KEY
            not in compute_consensus(
                other_mechanism, "P", merged_isotopologues=record
            ).provenance
        )

    def test_the_ledger_and_a_snapshot_serve_them_without_the_member_states(self):
        _, record = self._records()
        stripped = anchor(
            "X",
            200.1,
            "C10H16O4",
            "[13C]C9H15O4-",
            isotopologue_of="P",
            provenance={MERGED_ISOTOPOLOGUE_OF_KEY: record},
        )
        parent_record = {
            "formula": "C10H16O4",
            "ionization_mechanism_id": MECH,
            "lines": [{"batch_peak_id": "X"}],
            "n_lines": 1,
        }
        parent = anchor(
            "P",
            PARENT_MZ,
            "C10H16O4",
            PARENT_ION,
            provenance={MERGED_ISOTOPOLOGUES_KEY: parent_record},
        )
        for bp in (stripped, parent):
            bp.engine_tier = bp.engine_claim = None
            bp.continuity_grade = bp.continuity = None
            bp.sample_batch_id = "b"
            bp.best_fit_score = None
            bp.n_present = 6
            bp.is_ambiguous = 0
        meta = _batch_peak_meta(stripped)
        served = meta["merged_isotopologue_of"]
        assert served["batch_peak_id"] == "P"
        assert "displaced_members" not in served
        assert merged_isotopologue_of_record(stripped)["displaced_members"]
        assert _batch_peak_meta(parent)["merged_isotopologues"] == parent_record
        assert _batch_peak_meta(parent)["merged_isotopologue_of"] is None

        snapshot = SimpleNamespace(**anchor_snapshot_row("run-1", stripped, []))
        assert snapshot_anchor_meta(snapshot, "b")["merged_isotopologue_of"] == served
        parent_snapshot = SimpleNamespace(**anchor_snapshot_row("run-1", parent, []))
        assert (
            snapshot_anchor_meta(parent_snapshot, "b")["merged_isotopologues"]
            == parent_record
        )

    def test_a_record_the_members_left_or_a_reading_moved_off_is_not_served(self):
        _, record = self._records()
        moved = anchor(
            "X",
            200.1,
            "C9H12N2O2",
            "C9H11N2O2-",
            provenance={MERGED_ISOTOPOLOGUE_OF_KEY: record},
        )
        assert merged_isotopologue_of_record(moved) is record
        assert standing_merged_isotopologue_of_record(moved) is None
        assert merged_isotopologue_of_summary(moved) is None
        parent = anchor(
            "P",
            PARENT_MZ,
            "C11H20O3",
            "C11H19O3-",
            provenance={
                MERGED_ISOTOPOLOGUES_KEY: {
                    "formula": "C10H16O4",
                    "ionization_mechanism_id": MECH,
                }
            },
        )
        assert merged_isotopologues_record(parent) is not None
        assert standing_merged_isotopologues_record(parent) is None


class TestTheSummary:
    def _plan(self):
        pair = _stripped_pair()
        found = Plan(stripped=[pair], held=[], counts={})
        found.counts[("mode-1", "orbi")] = {
            "merged_rows": 2,
            "at_a_line": 1,
            "off_ratio": 0,
            "untestable": 0,
            "held": 0,
            "stripped": 1,
        }
        return found, pair

    def test_it_lists_the_pairs_and_sums_the_counts(self):
        found, pair = self._plan()
        summary = summarise(
            found,
            [
                Written(
                    pair,
                    "stripped",
                    members_rewritten=6,
                    rows_rewritten=1,
                    kept={},
                    rows_kept={"no_row": 5},
                )
            ],
        )
        assert summary["stripped"] == 1
        assert summary["members_rewritten"] == 6
        assert summary["rows_rewritten"] == 1
        assert summary["rows_kept"] == {"no_row": 5}
        [listed] = summary["stripped_pairs"]
        assert (listed["batch_peak_id"], listed["parent_batch_peak_id"]) == ("X", "P")
        assert listed["label"] == "13C"
        assert listed["formula"] == "C9H12N2O2"
        assert summary["held_pairs"] == []
        assert summary["thresholds"]["median_low"] == 0.4
        assert summary["partitions"][0]["instrument_type"] == "orbi"
        assert "stripped" in stripping_outcome(summary, "b")["message"]

    def test_a_pair_moved_or_held_at_the_write_is_counted_so(self):
        found, pair = self._plan()
        moved = summarise(found, [Written(pair, "moved")])
        assert (moved["stripped"], moved["moved"]) == (0, 1)
        assert moved["stripped_pairs"] == []
        held = summarise(found, [Written(pair, HELD_PARENT_ABSENT)])
        assert (held["stripped"], held["held"]) == (0, 1)
        assert held["held_by_reason"] == {HELD_PARENT_ABSENT: 1}
        assert held["held_pairs"][0]["reason"] == HELD_PARENT_ABSENT

    def test_the_lists_are_capped_and_the_counts_are_not(self):
        found, pair = self._plan()
        many = [Written(pair, "stripped") for _ in range(MAX_LISTED + 3)]
        found.counts[("mode-1", "orbi")]["stripped"] = len(many)
        summary = summarise(found, many)
        assert summary["stripped"] == MAX_LISTED + 3
        assert len(summary["stripped_pairs"]) == MAX_LISTED

    def test_an_empty_batch_says_why(self):
        summary = summarise(Plan(), [])
        assert stripping_outcome(summary, "b")["status"] == "partial"


class TestTheSamplesRow:
    """The per-sample rewrite (``service.read_as_batch_isotopologue``)."""

    LINE = service.BatchLine(
        label="13C",
        mz_offset=1.00336,
        share=0.1,
        batch_peak_id="X",
        parent_batch_peak_id="P",
        reading={"spectra": 6, "median_factor": 1.0, "above": 0},
        batch_peak_run_id="run-strip",
    )

    def _rows(self, owner_source=SOURCE_UNTARGETED):
        owner = {
            "peak_assignment_id": "row-p",
            "sample_peak_id": "peak-p",
            "sample_peak_mz": PARENT_MZ,
            "sample_peak_intensity": 1e6,
            "role": "M0",
            "assigned_formula": "C10H16O4",
            "ion_formula": PARENT_ION,
            "ionization_mechanism_id": MECH,
            "source": owner_source,
            "fit_score": 0.8,
            "tier": "candidate",
            "target_compound_id": None,
            "target_ion_id": None,
            "provenance": {"plausibility": 0.9, "evidence": 0.72, "p_correct": 0.6},
        }
        row = {
            "peak_assignment_id": "row-x",
            "sample_peak_id": "peak-x",
            "sample_peak_mz": PARENT_MZ + 1.00336 + 0.0001,
            "sample_peak_intensity": 1.2e5,
            "role": "M0",
            "assigned_formula": "C9H12N2O2",
            "ion_formula": "C9H11N2O2-",
            "ionization_mechanism_id": MECH,
            "isotope_label": None,
            "source": SOURCE_UNTARGETED,
            "fit_score": 0.95,
            "tier": "assigned",
            "alternatives": [{"assigned_formula": "C8H8N4O"}],
            "provenance": {"evidence": 0.9},
        }
        return row, owner

    def test_the_row_becomes_its_owners_isotopologue(self):
        row, owner = self._rows()
        values = service.read_as_batch_isotopologue(row, owner, self.LINE)
        assert values["role"] == "iso_child"
        assert values["owner_peak_assignment_id"] == "row-p"
        assert values["assigned_formula"] == "C10H16O4"
        assert values["ionization_mechanism_id"] == MECH
        assert (values["isotope_label"], values["ion_formula"]) == (
            "13C",
            "[13C]C9H15O4-",
        )
        assert values["fit_score"] == 0.8
        assert values["tier"] == "candidate"
        assert values["source"] == SOURCE_UNTARGETED
        assert values["mz_error_ppm"] == pytest.approx(
            0.0001 / (PARENT_MZ + 1.00336) * 1e6
        )
        assert values["abundance_error"] == pytest.approx(0.12 / 0.1 - 1.0)
        # The row is left as it was.
        assert row["role"] == "M0"

    def test_what_it_said_is_kept_first_among_its_alternatives(self):
        row, owner = self._rows()
        values = service.read_as_batch_isotopologue(row, owner, self.LINE)
        first = values["alternatives"][0]
        assert first[DISPLACED_BY_CLAIM] is True
        assert first["assigned_formula"] == "C9H12N2O2"
        assert values["alternatives"][1] == {"assigned_formula": "C8H8N4O"}

    def test_an_unassigned_peak_puts_no_reading_back(self):
        row, owner = self._rows()
        row.update(
            role="unassigned",
            assigned_formula=None,
            ion_formula=None,
            alternatives=None,
        )
        assert (
            service.read_as_batch_isotopologue(row, owner, self.LINE)["alternatives"]
            is None
        )

    def test_the_rewrite_is_recorded_on_the_row(self):
        row, owner = self._rows()
        provenance = service.read_as_batch_isotopologue(row, owner, self.LINE)[
            "provenance"
        ]
        # The owner's measurement, and none of its arbitration.
        assert provenance["plausibility"] == 0.9
        assert provenance["evidence"] == 0.72
        assert "p_correct" not in provenance
        record = provenance[service.BATCH_ISOTOPOLOGUE]
        assert record["line"] == "13C"
        assert (record["batch_peak_id"], record["parent_batch_peak_id"]) == ("X", "P")
        assert record["batch_peak_run_id"] == "run-strip"
        assert record["observed_share"] == pytest.approx(0.12)
        assert record["reading"]["spectra"] == 6
        assert record["displaced"]["assigned_formula"] == "C9H12N2O2"
        assert record["displaced"]["tier"] == "assigned"
        assert record["displaced"]["evidence"] == 0.9

    def test_and_on_its_owner(self):
        row, owner = self._rows()
        provenance = service.with_batch_isotopologue_line(owner, row, self.LINE)
        assert provenance["evidence"] == 0.72
        [entry] = provenance[service.BATCH_ISOTOPOLOGUES]
        assert entry["peak_assignment_id"] == "row-x"
        assert entry["sample_peak_id"] == "peak-x"
        assert (entry["line"], entry["batch_peak_id"]) == ("13C", "X")
        assert service.BATCH_ISOTOPOLOGUES not in owner["provenance"]
        # A second rewrite of the same peak replaces its entry.
        owner["provenance"] = provenance
        again = service.with_batch_isotopologue_line(owner, row, self.LINE)
        assert len(again[service.BATCH_ISOTOPOLOGUES]) == 1

    def test_the_row_takes_its_owners_mechanism(self):
        row, owner = self._rows()
        row["ionization_mechanism_id"] = "mech-other"
        values = service.read_as_batch_isotopologue(row, owner, self.LINE)
        assert values["ionization_mechanism_id"] == MECH

    def test_a_target_lists_owner_names_the_line_by_its_offset(self):
        row, owner = self._rows(owner_source=SOURCE_DATABASE)
        values = service.read_as_batch_isotopologue(row, owner, self.LINE)
        assert values["isotope_label"] == "M+1"
        assert values["ion_formula"] == PARENT_ION
        assert values["isotope_formula"]
        assert values["source"] == SOURCE_DATABASE
