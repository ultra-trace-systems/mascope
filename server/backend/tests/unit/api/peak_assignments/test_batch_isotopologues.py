"""Unit tests for the batch's isotopologue stamping, its pure parts (step 4.2c).

No DB: which anchors are parents, where an isotope line is looked for and in
which samples, how each is judged and stamped and a line shared out, what the
records and the run's summary say, how the consensus keeps them, and what the
ledger, a snapshot and a member row serve. The diagnostic isotopologues and
their reading are the library's
(``libraries/tools/tests/test_diagnostic_isotopologues.py``). See
``batch_isotopologues.py``.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import batch_isotopologues
from mascope_backend.api.new.peak_assignments.batch_export import member_export_row
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    REASON_NOT_FOUND,
    REASON_TAKEN,
    CentreIndex,
    Member,
    Trace,
    is_parent,
    needed_peaks,
    plan_batch,
    read_batch,
    stamping_outcome,
    still_read,
    trace_centre,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    DIAGNOSTIC_ISOTOPOLOGUES_KEY,
    STAMP_PICKER,
    STAMP_PREDICTED,
    STAMPED_ISOTOPOLOGUE_OF_KEY,
    compute_consensus,
    diagnostic_isotopologues_record_of,
    member_stamp,
    resolution_adaptive_tol_ppm,
    resolution_from_tol_ppm,
    stamped_isotopologue_of_record,
    stamped_isotopologue_of_summary,
    stamped_lines,
    standing_diagnostic_isotopologues_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_tools.composition.diagnostic_isotopologues import (
    MAX_SPAN,
    MIN_JUDGED,
    REASON_FEW_SPECTRA,
    REASON_UNRESOLVED,
    TRACK_SHARE,
    VERDICT_NOT_STAMPED,
    VERDICT_STAMPED,
    VERDICT_UNTESTABLE,
)
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance


SAMPLES = [f"s{i}" for i in range(6)]
BR81 = 0.4931 / 0.5069
S34 = 0.0425 / 0.9499
BR_OFFSET = 1.997953
S_OFFSET = 1.995796
#: An Orbitrap anchor's frozen tolerance: a 2 ppm half FWHM and the margin.
ORBI_TOL = resolution_adaptive_tol_ppm(200.0, 250000.0)


def anchor(key, mz, formula=None, ion=None, **extra):
    """A batch peak as the stamping reads one."""
    fields = dict(
        batch_peak_id=key,
        mz=mz,
        mz_tol_ppm=ORBI_TOL,
        ionization_mode_id="mode-1",
        intensity_variable="sum_peak_heights",
        consensus_formula=formula,
        consensus_ion_formula=ion,
        ionization_mechanism_id="mech-br" if formula else None,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=None,
        isotopologue_of=None,
        provenance=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def trace(mz, intensities, prefix, roles=None, owner=None):
    """Sample -> member, from one intensity per sample (None for absent)."""
    out = {}
    for i, (sample, value) in enumerate(zip(SAMPLES, intensities)):
        if value is None:
            continue
        role = (roles or {}).get(i)
        out[sample] = Member(
            sample_peak_id=f"{prefix}-{i}",
            intensity=value,
            mz=mz,
            role=role,
            owner_batch_peak_id=owner if role else None,
        )
    return out


PARENT_MZ = 170.9556
PARENT = [1e5 * (1 + i) for i in range(6)]


def bromide_batch(line_ratios=None, roles=None, line_samples=None):
    """A bromide parent, its 81Br line, and a peak of another compound."""
    ratios = line_ratios or [BR81] * 6
    line = [
        PARENT[i] * ratios[i] if line_samples is None or i in line_samples else None
        for i in range(6)
    ]
    anchors = [
        anchor("p", PARENT_MZ, "C2H5O", "C2H5BrO-"),
        anchor("line", PARENT_MZ + BR_OFFSET),
        anchor("other", 250.0, "C9H14O4", "C9H13O4-"),
    ]
    members = {
        "p": trace(PARENT_MZ, PARENT, "p"),
        "line": trace(PARENT_MZ + BR_OFFSET, line, "line", roles=roles, owner="p"),
        "other": trace(250.0, [1e4] * 6, "o"),
    }
    return anchors, members


def stamp(anchors, members, **kwargs):
    return read_batch(
        plan_batch(anchors, members),
        kwargs.pop("tolerances", {}),
        kwargs.pop("snr", {}),
        **kwargs,
    )


def entry(parents, parent_id, label):
    return next(e for e in parents[parent_id]["predicted"] if e["label"] == label)


class TestWhichAnchorsAreParents:
    @pytest.mark.parametrize(
        "extra, parent",
        [
            ({}, True),
            ({"consensus_tier": "candidate"}, True),
            ({"consensus_tier": "below_assignability"}, True),
            ({"consensus_tier": "unassigned"}, False),
            ({"isotopologue_of": "x"}, False),
            ({"consensus_role": "reagent"}, False),
            ({"consensus_ion_formula": "[13C]CH5BrO-"}, False),
            ({"consensus_ion_formula": None}, False),
            ({"consensus_formula": None}, False),
        ],
    )
    def test_a_committed_monoisotopic_reading(self, extra, parent):
        assert is_parent(anchor("p", 171.0, "C2H5O", "C2H5BrO-", **extra)) is parent

    def test_a_parent_whose_ion_holds_no_diagnostic_element_predicts_nothing(self):
        anchors = [anchor("p", 225.2, "C16H32", "C16H33+")]
        assert plan_batch(anchors, {"p": trace(225.2, PARENT, "p")}) == []


class TestWhereAnIsotopologueIsLookedFor:
    def test_at_the_parents_trace_centre(self):
        # The members sit 3 ppm above the frozen anchor, and so does the line.
        shift = PARENT_MZ * 3e-6
        anchors = [
            anchor("p", PARENT_MZ, "C2H5O", "C2H5BrO-"),
            anchor("line", PARENT_MZ + BR_OFFSET + shift),
        ]
        members = {
            "p": trace(PARENT_MZ + shift, PARENT, "p"),
            "line": trace(PARENT_MZ + BR_OFFSET + shift, PARENT, "l"),
        }
        found = {p.isotopologue.label: p for p in plan_batch(anchors, members)}
        assert found["81Br"].mz == pytest.approx(
            PARENT_MZ + shift + BR_OFFSET, abs=1e-5
        )
        assert found["81Br"].line.anchor.batch_peak_id == "line"
        assert found["18O"].line is None

    def test_the_trace_centre_is_the_members_median(self):
        members = [Member("a", 1.0, mz) for mz in (100.0, 100.1, 100.5)]
        assert trace_centre(99.0, members) == 100.1
        assert trace_centre(99.0, []) == 99.0

    def test_a_line_outside_its_own_tolerance_is_not_found(self):
        far = anchor("line", PARENT_MZ + BR_OFFSET + PARENT_MZ * 10e-6)
        index = CentreIndex([Trace(far, {}, far.mz)])
        assert index.find(PARENT_MZ + BR_OFFSET) is None
        near = anchor("near", PARENT_MZ + BR_OFFSET + PARENT_MZ * 1e-6)
        index = CentreIndex([Trace(far, {}, far.mz), Trace(near, {}, near.mz)])
        assert index.find(PARENT_MZ + BR_OFFSET).anchor.batch_peak_id == "near"
        assert index.find(PARENT_MZ + BR_OFFSET, exclude="near") is None

    def test_in_the_parents_partition_only(self):
        anchors, members = bromide_batch()
        anchors[1].intensity_variable = "sum_peak_areas"
        found = {p.isotopologue.label: p for p in plan_batch(anchors, members)}
        assert found["81Br"].line is None

    def test_judged_where_both_lines_stand(self):
        anchors, members = bromide_batch(line_samples={0, 2, 3, 4})
        members["p"].pop("s2")
        # A peak of no intensity holds no ratio.
        members["line"]["s4"] = Member("line-4", 0.0, PARENT_MZ + BR_OFFSET)
        found = {p.isotopologue.label: p for p in plan_batch(anchors, members)}
        assert sorted(found["81Br"].judged) == ["s0", "s3"]
        assert needed_peaks(found.values()) == {
            "s0": {"p-0", "line-0"},
            "s3": {"p-3", "line-3"},
        }

    def test_an_unresolved_isotopologue_is_never_looked_for(self):
        # A TOF's width: the 15N line sits inside the 13C peak, whatever
        # stands at its m/z.
        tof = resolution_adaptive_tol_ppm(150.0, 3000.0)
        anchors = [
            anchor("p", 150.0, "C5H9NO4", "C5H10NO4+", mz_tol_ppm=tof),
            anchor("c13", 151.0034, mz_tol_ppm=tof),
        ]
        members = {"p": trace(150.0, PARENT, "p"), "c13": trace(151.0034, PARENT, "c")}
        found = {p.isotopologue.label: p for p in plan_batch(anchors, members)}
        assert not found["15N"].isotopologue.resolved
        assert found["15N"].line is None
        parents, lines, summary = read_batch(list(found.values()), {}, {})
        assert entry(parents, "p", "15N")["reason"] == REASON_UNRESOLVED
        assert entry(parents, "p", "15N")["verdict"] == VERDICT_UNTESTABLE
        assert summary["unresolved"] >= 1


class TestStamping:
    def test_a_line_holding_its_ratio_across_the_track_is_stamped(self):
        anchors, members = bromide_batch(roles={0: "iso_child", 1: "iso_child"})
        parents, lines, summary = stamp(anchors, members, batch_peak_run_id="run-1")
        br = entry(parents, "p", "81Br")
        assert br["stamped"] is True
        assert br["verdict"] == VERDICT_STAMPED
        assert br["batch_peak_id"] == "line"
        assert (br["judged"], br["holds"], br["share"]) == (6, 6, 1.0)
        assert (br["n_predicted"], br["n_picker"]) == (4, 2)
        assert br["ratio"] == pytest.approx(BR81, rel=1e-3)
        record = lines["line"]
        assert record["batch_peak_id"] == "p"
        assert record["formula"] == "C2H5O"
        assert record["ionization_mechanism_id"] == "mech-br"
        assert record["label"] == "81Br"
        assert record["batch_peak_run_id"] == "run-1"
        # The sample's own run read two of them as the parent's isotopologue.
        assert record["stamps"] == {
            "s0": STAMP_PICKER,
            "s1": STAMP_PICKER,
            **{f"s{i}": STAMP_PREDICTED for i in range(2, 6)},
        }
        assert set(lines) == {"line"}
        assert summary["lines_stamped"] == 1
        assert (summary["stamps_predicted"], summary["stamps_picker"]) == (4, 2)

    @pytest.mark.parametrize("role", ["iso_child", "reagent"])
    def test_a_line_the_run_read_as_the_parents_is_a_picker_stamp(self, role):
        # A reagent's isotopologue is read as its reagent role, as the fold's
        # isotopologue_of reads it.
        anchors, members = bromide_batch(roles={0: role})
        _, lines, _ = stamp(anchors, members)
        assert lines["line"]["stamps"]["s0"] == STAMP_PICKER
        assert set(lines["line"]["stamps"].values()) == {STAMP_PICKER, STAMP_PREDICTED}

    def test_a_line_another_parent_owns_is_a_predicted_stamp(self):
        anchors, members = bromide_batch(roles={0: "iso_child"})
        for member in members["line"].values():
            object.__setattr__(member, "owner_batch_peak_id", "someone-else")
        _, lines, _ = stamp(anchors, members)
        assert set(lines["line"]["stamps"].values()) == {STAMP_PREDICTED}

    def test_a_stamp_is_only_where_the_ratio_held(self):
        ratios = [BR81] * 5 + [BR81 * 1.5]
        anchors, members = bromide_batch(line_ratios=ratios)
        parents, lines, _ = stamp(anchors, members)
        assert entry(parents, "p", "81Br")["share"] == pytest.approx(5 / 6, abs=1e-4)
        assert entry(parents, "p", "81Br")["stamped"] is True
        assert "s5" not in lines["line"]["stamps"]

    def test_a_line_whose_ratio_does_not_hold_is_not_stamped(self):
        held = int(TRACK_SHARE * 6)  # under the bar: one short of it
        ratios = [BR81] * (held - 1) + [BR81 * 3] * (6 - held + 1)
        anchors, members = bromide_batch(line_ratios=ratios)
        parents, lines, summary = stamp(anchors, members)
        br = entry(parents, "p", "81Br")
        assert br["verdict"] == VERDICT_NOT_STAMPED
        assert br["stamped"] is False
        assert lines == {}
        assert summary["not_stamped"] == 1

    def test_too_few_samples_are_untestable(self):
        anchors, members = bromide_batch(line_samples=set(range(MIN_JUDGED - 1)))
        parents, lines, summary = stamp(anchors, members)
        br = entry(parents, "p", "81Br")
        assert br["verdict"] == VERDICT_UNTESTABLE
        assert br["reason"] == REASON_FEW_SPECTRA
        assert lines == {}
        assert summary["untestable"] == 1

    def test_an_isotopologue_nothing_stands_at_is_not_found(self):
        anchors, members = bromide_batch()
        parents, _, summary = stamp(anchors, members)
        o18 = entry(parents, "p", "18O")
        assert o18["batch_peak_id"] is None
        assert o18["reason"] == REASON_NOT_FOUND
        # The other compound's 18O line stands nowhere either.
        assert summary["not_found"] == 2
        assert parents["p"]["n_predicted"] == 2
        assert parents["p"]["n_found"] == 1
        assert parents["p"]["n_stamped"] == 1

    def test_each_samples_run_tolerance_is_the_one_read(self):
        # Every line reads 20% high: today's tolerance refuses it, a TOF run's
        # fitted centre of +20% admits it, and only where that sample's run
        # fitted it.
        anchors, members = bromide_batch(line_ratios=[BR81 * 1.2] * 6)
        _, lines, _ = stamp(anchors, members)
        assert lines == {}
        centre = EnvelopeTolerance(centre=0.2)
        _, lines, _ = stamp(
            anchors, members, tolerances={sample: centre for sample in SAMPLES[:5]}
        )
        assert sorted(lines["line"]["stamps"]) == SAMPLES[:5]

    def test_each_peaks_noise_is_the_one_read(self):
        # 30% high holds only at the two peaks' noise.
        anchors, members = bromide_batch(line_ratios=[BR81 * 1.3] * 6)
        noise = {
            sample: {f"p-{i}": 10.0, f"line-{i}": 10.0}
            for i, sample in enumerate(SAMPLES)
        }
        _, lines, _ = stamp(anchors, members)
        assert lines == {}
        _, lines, _ = stamp(anchors, members, snr=noise)
        assert set(lines["line"]["stamps"]) == set(SAMPLES)

    def test_a_sample_that_cannot_tell_gets_no_stamp(self):
        # Five samples at an S/N of a hundred, one at three: the track is
        # stamped, but not in the sample whose reading could not tell, though
        # its ratio is right.
        anchors, members = bromide_batch()
        noise = {
            sample: {f"p-{i}": 100.0 if i else 3.0, f"line-{i}": 100.0 if i else 3.0}
            for i, sample in enumerate(SAMPLES)
        }
        parents, lines, _ = stamp(anchors, members, snr=noise)
        br = entry(parents, "p", "81Br")
        assert (br["judged"], br["holds"], br["uninformative"]) == (5, 5, 1)
        assert br["stamped"] is True
        assert sorted(lines["line"]["stamps"]) == SAMPLES[1:]

    def test_a_reading_that_cannot_tell_stamps_nothing(self):
        # At an S/N of three the 81Br line's widths reach past half of it.
        assert 2 * BR81 * (2**0.5 / 3) > MAX_SPAN * BR81
        anchors, members = bromide_batch()
        noise = {
            sample: {f"p-{i}": 3.0, f"line-{i}": 3.0}
            for i, sample in enumerate(SAMPLES)
        }
        parents, lines, _ = stamp(anchors, members, snr=noise)
        br = entry(parents, "p", "81Br")
        assert (br["judged"], br["uninformative"]) == (0, 6)
        assert br["verdict"] == VERDICT_UNTESTABLE
        assert lines == {}


def two_parents(p2_fails=0):
    """A line both a bromide's 81Br and a sulfur compound's 34S fall on."""
    anchors, members = bromide_batch()
    line = members["line"]
    p2_mz = PARENT_MZ + BR_OFFSET - S_OFFSET
    anchors.append(anchor("q", p2_mz, "C3H8OS", "C3H7OS-"))
    intensities = [
        line[s].intensity / S34 * (3.0 if i < p2_fails else 1.0)
        for i, s in enumerate(SAMPLES)
    ]
    members["q"] = trace(p2_mz, intensities, "q")
    return anchors, members


class TestALineTwoParentsStamp:
    def test_goes_to_the_parent_whose_ratio_holds_more_widely(self):
        anchors, members = two_parents(p2_fails=1)
        parents, lines, summary = stamp(anchors, members)
        assert lines["line"]["batch_peak_id"] == "p"
        taken = entry(parents, "q", "34S")
        assert taken["verdict"] == VERDICT_STAMPED
        assert taken["stamped"] is False
        assert taken["taken_by"] == "p"
        assert taken["reason"] == REASON_TAKEN
        assert summary["taken"] == 1
        assert summary["lines_stamped"] == 1

    def test_a_parent_whose_ratio_did_not_hold_does_not_lose_the_line(self):
        # The sulfur compound's 34S holds in two of six: it was never a claim.
        anchors, members = two_parents(p2_fails=4)
        parents, lines, summary = stamp(anchors, members)
        assert lines["line"]["batch_peak_id"] == "p"
        lost = entry(parents, "q", "34S")
        assert lost["verdict"] == VERDICT_NOT_STAMPED
        assert lost["stamped"] is False
        assert lost["reason"] is None
        assert "taken_by" not in lost
        assert summary["taken"] == 0
        assert summary["not_stamped"] == 1

    def test_by_share_before_by_samples(self):
        # The bromide holds in all four samples it shares with the line, the
        # sulfur compound in five of six: more samples, a smaller share.
        anchors, members = two_parents(p2_fails=1)
        members["p"].pop("s4")
        members["p"].pop("s5")
        parents, lines, _ = stamp(anchors, members)
        assert (
            entry(parents, "p", "81Br")["holds"],
            entry(parents, "q", "34S")["holds"],
        ) == (
            4,
            5,
        )
        assert lines["line"]["batch_peak_id"] == "p"

    def test_on_an_equal_share_to_the_parent_holding_in_more_samples(self):
        # The sulfur compound holds in all five samples it shares with the
        # line, the bromide in all six: the same share, one more sample.
        anchors, members = two_parents()
        members["q"].pop("s5")
        for anchor_ in anchors:
            if anchor_.batch_peak_id == "p":
                anchor_.batch_peak_id = "z"
        members["z"] = members.pop("p")
        _, lines, _ = stamp(anchors, members)
        assert lines["line"]["batch_peak_id"] == "z"

    def test_on_a_tie_to_the_lower_id(self):
        anchors, members = two_parents()
        parents, lines, _ = stamp(anchors, members)
        assert lines["line"]["batch_peak_id"] == "p"
        assert entry(parents, "q", "34S")["taken_by"] == "p"


class TestTheRun:
    def test_the_summary_names_the_thresholds_and_sums_the_partitions(self):
        anchors, members = bromide_batch()
        anchors.append(
            anchor(
                "tof", 300.0, "C2H5O", "C2H5BrO-", intensity_variable="sum_peak_areas"
            )
        )
        members["tof"] = trace(300.0, PARENT, "t")
        _, _, summary = stamp(anchors, members)
        assert summary["thresholds"] == {
            "hold_widths": 2.0,
            "max_span": MAX_SPAN,
            "min_judged": MIN_JUDGED,
            "track_share": TRACK_SHARE,
        }
        assert [
            (p["instrument_type"], p["parents"]) for p in summary["partitions"]
        ] == [
            ("orbi", 2),
            ("tof", 1),
        ]
        assert summary["parents"] == 3
        assert summary["parents_recorded"] == 3

    def test_the_outcome_names_what_it_did_and_that_no_tier_moved(self):
        anchors, members = bromide_batch()
        _, _, summary = stamp(anchors, members)
        outcome = stamping_outcome(summary, "sb-1")
        assert outcome["status"] == "success"
        assert "1 stamped" in outcome["message"]
        assert "No tier moved" in outcome["message"]
        assert outcome["_notification_data"] == {"sample_batch_id": "sb-1"}

    def test_a_batch_with_nothing_to_predict_says_so(self):
        _, _, summary = read_batch([], {}, {})
        outcome = stamping_outcome(summary, "sb-1")
        assert outcome["status"] == "partial"

    @pytest.mark.parametrize(
        "moved",
        [
            {"consensus_formula": "C9H8"},
            {"ionization_mechanism_id": "mech-cl"},
            None,  # the parent went
        ],
    )
    def test_a_parent_that_moved_since_the_read_writes_no_record(self, moved):
        anchors, members = bromide_batch()
        parents, lines, _ = stamp(anchors, members)
        assert set(parents) == {"p", "other"} and set(lines) == {"line"}
        # The ledger as it was read: everything stands.
        assert still_read(anchors, parents, lines) == (parents, lines)
        now = [a for a in anchors if a.batch_peak_id != "p"]
        if moved is not None:
            now.append(SimpleNamespace(**{**vars(anchors[0]), **moved}))
        kept, kept_lines = still_read(now, parents, lines)
        assert kept == {"other": parents["other"]}
        assert kept_lines == {}


@pytest.mark.asyncio
class TestTheNoise:
    """A sample's peaks' S/N, read off its peak list."""

    @pytest.fixture
    def peak_list(self, monkeypatch):
        frames = {}

        async def fetch(sample_item_id):
            if sample_item_id not in frames:
                raise RuntimeError("no such sample")
            return sample_item_id

        monkeypatch.setattr(batch_isotopologues, "fetch_sample", fetch)
        monkeypatch.setattr(
            batch_isotopologues, "load_sample_peaks", lambda s: frames[s]
        )
        return frames

    async def test_reads_the_peaks_asked_for(self, peak_list):
        import pandas as pd

        peak_list["s0"] = pd.DataFrame(
            {
                "sample_peak_id": ["1", "2", "3"],
                "signal_to_noise": [12.0, float("nan"), 40.0],
            }
        )
        # A peak the list has no estimate for reads as none, not as zero.
        assert await batch_isotopologues._sample_noise("s0", {"1", "2"}) == {"1": 12.0}

    async def test_a_list_with_no_estimate_reads_none(self, peak_list):
        import pandas as pd

        peak_list["s0"] = pd.DataFrame({"sample_peak_id": ["1"]})
        assert await batch_isotopologues._sample_noise("s0", {"1"}) == {}

    async def test_a_list_that_cannot_be_read_reads_none(self, peak_list):
        assert await batch_isotopologues._sample_noise("gone", {"1"}) == {}
        assert await batch_isotopologues.read_noise({"gone": {"1"}}) == {"gone": {}}


# --- the records ------------------------------------------------------------------

_PARENT_RECORD = {
    "formula": "A",
    "ionization_mechanism_id": "m",
    "predicted": [
        {"label": "81Br", "batch_peak_id": "line", "stamped": True},
        {"label": "18O", "batch_peak_id": "o18", "stamped": False},
        {"label": "34S", "batch_peak_id": None, "stamped": False},
    ],
}
_LINE_RECORD = {
    "batch_peak_id": "p",
    "formula": "A",
    "label": "81Br",
    "share": 1.0,
    "stamps": {"s0": STAMP_PICKER, "s1": STAMP_PREDICTED},
}


def _fit(formula, mechanism="m"):
    return {
        "assigned_formula": formula,
        "role": "M0",
        "fit_score": 0.9,
        "intensity": 1000.0,
        "tier": "assigned",
        "ionization_mechanism_id": mechanism,
    }


class TestTheConsensusKeepsTheRecords:
    def test_a_parents_while_it_names_the_reading(self):
        c = compute_consensus([_fit("A")], diagnostic_isotopologues=_PARENT_RECORD)
        assert c.provenance[DIAGNOSTIC_ISOTOPOLOGUES_KEY] == _PARENT_RECORD

    @pytest.mark.parametrize("formula, mechanism", [("B", "m"), ("A", "n")])
    def test_not_a_parents_once_the_reading_moves(self, formula, mechanism):
        c = compute_consensus(
            [_fit(formula, mechanism)], diagnostic_isotopologues=_PARENT_RECORD
        )
        assert DIAGNOSTIC_ISOTOPOLOGUES_KEY not in c.provenance

    def test_a_lines_whatever_the_line_reads(self):
        for members in (
            [_fit("B")],
            [{"assigned_formula": None, "role": "unassigned"}],
        ):
            c = compute_consensus(members, stamped_isotopologue_of=_LINE_RECORD)
            assert c.provenance[STAMPED_ISOTOPOLOGUE_OF_KEY] == _LINE_RECORD

    def test_not_a_record_naming_no_parent(self):
        c = compute_consensus([_fit("B")], stamped_isotopologue_of={"label": "81Br"})
        assert STAMPED_ISOTOPOLOGUE_OF_KEY not in c.provenance


@pytest.mark.parametrize(
    "provenance, parent, line",
    [
        ({DIAGNOSTIC_ISOTOPOLOGUES_KEY: _PARENT_RECORD}, _PARENT_RECORD, None),
        ({STAMPED_ISOTOPOLOGUE_OF_KEY: _LINE_RECORD}, None, _LINE_RECORD),
        (
            {
                DIAGNOSTIC_ISOTOPOLOGUES_KEY: {"predicted": []},
                STAMPED_ISOTOPOLOGUE_OF_KEY: {"label": "x"},
            },
            None,
            None,
        ),
        (
            {DIAGNOSTIC_ISOTOPOLOGUES_KEY: "x", STAMPED_ISOTOPOLOGUE_OF_KEY: "y"},
            None,
            None,
        ),
        (None, None, None),
    ],
)
def test_a_record_names_its_formula_or_its_parent(provenance, parent, line):
    holder = {"provenance": provenance}
    assert diagnostic_isotopologues_record_of(holder) == parent
    assert stamped_isotopologue_of_record(holder) == line


def test_a_parents_record_names_the_lines_it_stamped():
    assert stamped_lines(_PARENT_RECORD) == {"line"}
    assert stamped_lines(None) == set()


@pytest.mark.parametrize(
    "formula, mechanism, served",
    [("A", "m", True), ("B", "m", False), ("A", "n", False)],
)
def test_a_parents_record_stands_beside_its_reading(formula, mechanism, served):
    holder = {
        "provenance": {DIAGNOSTIC_ISOTOPOLOGUES_KEY: _PARENT_RECORD},
        "consensus_formula": formula,
        "ionization_mechanism_id": mechanism,
    }
    assert standing_diagnostic_isotopologues_record(holder) == (
        _PARENT_RECORD if served else None
    )


def test_a_member_carries_the_stamp_of_its_sample():
    holder = {"provenance": {STAMPED_ISOTOPOLOGUE_OF_KEY: _LINE_RECORD}}
    assert member_stamp(holder, "s0") == {
        "batch_peak_id": "p",
        "label": "81Br",
        "stamp_source": STAMP_PICKER,
    }
    assert member_stamp(holder, "s1")["stamp_source"] == STAMP_PREDICTED
    assert member_stamp(holder, "s9") is None
    assert member_stamp({"provenance": None}, "s0") is None


def test_the_summary_leaves_the_stamps_to_the_members():
    summary = stamped_isotopologue_of_summary(
        {"provenance": {STAMPED_ISOTOPOLOGUE_OF_KEY: _LINE_RECORD}}
    )
    assert "stamps" not in summary
    assert summary["batch_peak_id"] == "p"
    assert summary["share"] == 1.0


@pytest.mark.parametrize("resolution", [3000.0, 60000.0, 240000.0])
def test_an_anchors_tolerance_gives_back_its_resolution(resolution):
    tol = resolution_adaptive_tol_ppm(200.0, resolution)
    assert resolution_from_tol_ppm(tol) == pytest.approx(resolution)
    # Minted without one: the margin alone.
    assert resolution_from_tol_ppm(resolution_adaptive_tol_ppm(200.0, None)) is None
    assert resolution_from_tol_ppm(None) is None


def _ledger_anchor(**over):
    base = dict(
        batch_peak_id="line",
        sample_batch_id="sb-1",
        mz=172.95,
        ionization_mode_id="mode-1",
        consensus_formula="A",
        consensus_ion_formula="A-",
        ionization_mechanism_id="m",
        consensus_tier="assigned",
        consensus_role=None,
        best_fit_score=0.95,
        support_fraction=1.0,
        n_present=2,
        is_ambiguous=0,
        intensity_variable="sum_peak_heights",
        max_intensity=5000.0,
        isotopologue_of=None,
        candidates=[{"formula": "A"}],
        provenance={
            DIAGNOSTIC_ISOTOPOLOGUES_KEY: _PARENT_RECORD,
            STAMPED_ISOTOPOLOGUE_OF_KEY: _LINE_RECORD,
        },
        engine_tier=None,
        engine_claim=None,
        seeded=False,
        continuity_grade=None,
        continuity=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("mechanism, served", [("m", True), ("n", False)])
def test_the_ledger_and_a_snapshot_serve_the_records(mechanism, served):
    anchor_ = _ledger_anchor(ionization_mechanism_id=mechanism)
    summary = {k: v for k, v in _LINE_RECORD.items() if k != "stamps"}
    parent = _PARENT_RECORD if served else None
    meta = _batch_peak_meta(anchor_)
    assert (meta["diagnostic_isotopologues"], meta["stamped_isotopologue_of"]) == (
        parent,
        summary,
    )
    row = anchor_snapshot_row("run-1", anchor_, [])
    assert (row["diagnostic_isotopologues"], row["stamped_isotopologue_of"]) == (
        parent,
        summary,
    )
    meta = snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")
    assert (meta["diagnostic_isotopologues"], meta["stamped_isotopologue_of"]) == (
        parent,
        summary,
    )


@pytest.mark.parametrize(
    "sample, parent, source",
    [("s0", "p", STAMP_PICKER), ("s1", "p", STAMP_PREDICTED), ("s5", None, None)],
)
def test_a_member_row_carries_its_stamp(sample, parent, source):
    member = SimpleNamespace(
        sample_item_id=sample,
        sample_peak_id="1",
        mz_delta_ppm=0.0,
        intensity=1.0,
        candidate=None,
        tier=None,
        role=None,
        fit_score=None,
        p_correct=None,
        owner_batch_peak_id=None,
    )
    row = member_export_row(member, _ledger_anchor(), "sample")
    assert (row["stamped_isotopologue_of"], row["stamp_source"]) == (parent, source)
