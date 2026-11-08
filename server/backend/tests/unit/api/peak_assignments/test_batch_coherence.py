"""Unit tests for the co-variation reading's pure parts (step 4.2).

No DB: which anchors are read and in which role, what a partition's reading
records on each anchor and counts, the run's summary and outcome, how the
consensus keeps a record, what the ledger, a snapshot and a sample's row
serve of one. The traces themselves are the library's
(``libraries/tools/tests/test_coherence.py``). See ``batch_coherence.py``.
"""

import math
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_coherence import (
    Channel,
    co_variation_outcome,
    co_variation_record,
    partition_anchors,
    read_batch,
    read_partition,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    CO_VARIATION_KEY,
    co_variation_record_of,
    compute_consensus,
    row_co_variation,
    standing_co_variation_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_tools.composition.coherence import (
    MIN_SPECTRA,
    REASON_FEW_SPECTRA,
    REASON_FLAT,
    VERDICT_CO_VARYING,
    VERDICT_NOT_CO_VARYING,
    VERDICT_UNTESTABLE,
    CoVariation,
)


NOTATIONS = {"mech-a": "[M-H]-", "mech-b": "[M+NO3]-", "mech-c": "[M+Br]-"}
SAMPLES = [f"s{i:02d}" for i in range(16)]


def anchor(key, formula=None, mechanism=None, **extra):
    """A batch peak as the reading reads one."""
    fields = dict(
        batch_peak_id=key,
        ionization_mode_id="mode-1",
        intensity_variable="sum_peak_heights",
        consensus_formula=formula,
        consensus_ion_formula=None,
        ionization_mechanism_id=mechanism,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=None,
        isotopologue_of=None,
        provenance=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


class TestWhichAnchorsAreRead:
    def test_a_neutral_s_channels_are_grouped_on_its_element_counts(self):
        parts = partition_anchors(
            [
                anchor("a", "C3H4O4", "mech-a"),
                # A curated row spells the same neutral explicitly.
                anchor("b", "C3H4O4", "mech-b"),
                anchor("c", "C1H4N2O1", "mech-a"),
                anchor("d", "CH4N2O", "mech-b"),
            ],
            NOTATIONS,
        )
        part = parts[("mode-1", "orbi")]
        assert sorted(len(v) for v in part.neutrals.values()) == [2, 2]
        urea = next(v for v in part.neutrals.values() if v[0].formula == "C1H4N2O1")
        assert [c.batch_peak_id for c in urea] == ["c", "d"]
        assert [c.channel for c in urea] == ["[M-H]-", "[M+NO3]-"]
        assert part.traces == ["a", "b", "c", "d"]

    def test_isotope_lines_claimed_peaks_and_readingless_anchors(self):
        parts = partition_anchors(
            [
                anchor("m0", "C3H4O4", "mech-a"),
                anchor("iso", "C3H4O4", "mech-a", isotopologue_of="m0"),
                anchor(
                    "line", "C3H4O4", "mech-b", consensus_ion_formula="[13C]C2H3O4-"
                ),
                anchor("reagent", None, None, consensus_role="reagent"),
                anchor(
                    "reagent-line",
                    None,
                    None,
                    consensus_role="reagent",
                    isotopologue_of="reagent",
                ),
                anchor("ringing", None, None, consensus_role="artifact"),
                anchor("open"),
                anchor("unread", "C5H8O4", "mech-unknown"),
                anchor(
                    "held-unassigned",
                    "C5H8O4",
                    "mech-a",
                    consensus_tier="unassigned",
                ),
                anchor(
                    "below", "C6H10O4", "mech-a", consensus_tier="below_assignability"
                ),
            ],
            NOTATIONS,
        )
        part = parts[("mode-1", "orbi")]
        # An isotope line repeats its parent's trace, the reagent's included,
        # and ringing reads nothing.
        assert part.reagents == ["reagent"]
        # Every other monoisotopic, unclaimed anchor is a trace of the batch.
        assert part.traces == ["below", "held-unassigned", "m0", "open", "unread"]
        # A channel is a formula through a mechanism the batch can name, at a
        # tier that commits one - below assignability included.
        channels = [c.batch_peak_id for v in part.neutrals.values() for c in v]
        assert sorted(channels) == ["below", "m0"]

    def test_partitions_are_the_mode_and_the_instrument_type(self):
        parts = partition_anchors(
            [
                anchor("orbi-a", "C3H4O4", "mech-a"),
                anchor(
                    "tof-b", "C3H4O4", "mech-b", intensity_variable="sum_peak_areas"
                ),
                anchor("other-mode", "C3H4O4", "mech-b", ionization_mode_id="mode-2"),
            ],
            NOTATIONS,
        )
        assert set(parts) == {("mode-1", "orbi"), ("mode-1", "tof"), ("mode-2", "orbi")}
        assert all(len(v) == 1 for p in parts.values() for v in p.neutrals.values())


def wave(phase=0.0, amplitude=0.4, period=5.0):
    return [amplitude * math.sin(2 * math.pi * i / period + phase) for i in range(16)]


def trace(values, base=4.0, samples=SAMPLES):
    return {s: 10.0 ** (base + v) for s, v in zip(samples, values) if v is not None}


def background(n=6):
    """Traces that move with nothing in particular: the common mode's floor."""
    return {
        f"bg{i}": trace(wave(phase=0.9 * i, amplitude=0.01, period=3.0 + i))
        for i in range(n)
    }


def channels(formula, *mechanisms, prefix=None):
    prefix = prefix or formula
    return [
        Channel(f"{prefix}-{m}", formula, None, m, NOTATIONS[m]) for m in mechanisms
    ]


def part_of(neutrals, traces, reagents=()):
    from mascope_backend.api.new.peak_assignments.batch_coherence import (
        PartitionAnchors,
    )

    return PartitionAnchors(
        traces=sorted(traces), reagents=list(reagents), neutrals=neutrals
    )


class TestAPartitionsReading:
    def test_each_anchor_records_its_partners_and_each_pair_counts_once(self):
        own = wave()
        traces = {
            **background(),
            "P-mech-a": trace(own, base=5.0),
            "P-mech-b": trace(own, base=4.0),
            "P-mech-c": trace([-v for v in own], base=3.0),
        }
        neutrals = {"P": channels("C3H4O4", "mech-a", "mech-b", "mech-c", prefix="P")}
        records, counts = read_partition(
            part_of(neutrals, traces), traces, batch_peak_run_id="run-1"
        )
        assert set(records) == {"P-mech-a", "P-mech-b", "P-mech-c"}
        record = records["P-mech-a"]
        assert record["formula"] == "C3H4O4"
        assert record["ionization_mechanism_id"] == "mech-a"
        assert record["channel"] == "[M-H]-"
        assert record["batch_peak_run_id"] == "run-1"
        assert record["normalised"] is False
        # The strongest partner at the top, every partner strongest first.
        assert record["verdict"] == VERDICT_CO_VARYING
        assert record["r"] == pytest.approx(1.0, abs=1e-3)
        assert record["n_spectra"] == 16
        assert record["partner_batch_peak_id"] == "P-mech-b"
        assert record["partner_channel"] == "[M+NO3]-"
        assert [p["batch_peak_id"] for p in record["partners"]] == [
            "P-mech-b",
            "P-mech-c",
        ]
        assert record["partners"][1]["verdict"] == VERDICT_NOT_CO_VARYING
        assert record["partners"][1]["r"] < -0.9
        assert record["partners"][1]["channel"] == "[M+Br]-"
        assert record["partners"][1]["formula"] == "C3H4O4"
        assert counts["neutrals_read"] == 1
        assert counts["pairs"] == 3
        assert counts["co_varying"] == 1
        assert counts["not_co_varying"] == 2
        assert counts["traces"] == 9
        assert counts["reagent_ions"] == 0

    def test_two_anchors_through_one_channel_are_not_partners(self):
        own = wave()
        traces = {
            **background(),
            "split-1": trace(own, base=5.0),
            "split-2": trace(own, base=4.5),
        }
        neutrals = {
            "S": [
                Channel("split-1", "C3H4O4", None, "mech-a", "[M-H]-"),
                Channel("split-2", "C3H4O4", None, "mech-a", "[M-H]-"),
            ]
        }
        records, counts = read_partition(part_of(neutrals, traces), traces)
        assert records == {}
        assert counts["neutrals_read"] == 0
        assert counts["pairs"] == 0

    def test_a_same_channel_twin_is_left_out_of_the_partners(self):
        own = wave()
        traces = {
            **background(),
            "a1": trace(own, base=5.0),
            "a2": trace(own, base=4.5),
            "b": trace(own, base=4.0),
        }
        neutrals = {
            "N": [
                Channel("a1", "C3H4O4", None, "mech-a", "[M-H]-"),
                Channel("a2", "C3H4O4", None, "mech-a", "[M-H]-"),
                Channel("b", "C3H4O4", None, "mech-b", "[M+NO3]-"),
            ]
        }
        records, counts = read_partition(part_of(neutrals, traces), traces)
        assert [p["batch_peak_id"] for p in records["a1"]["partners"]] == ["b"]
        assert [p["batch_peak_id"] for p in records["b"]["partners"]] == ["a1", "a2"]
        assert counts["pairs"] == 2

    def test_the_untestable_readings_are_counted_by_reason(self):
        own = wave()
        traces = {
            **background(),
            "F-mech-a": trace(own, base=5.0),
            "F-mech-b": trace([0.0] * 16, base=4.0),
            "D-mech-a": trace(own, base=5.5),
            "D-mech-b": trace(own[: MIN_SPECTRA - 1] + [None] * 7, base=3.5),
        }
        neutrals = {
            "F": channels("C3H4O4", "mech-a", "mech-b", prefix="F"),
            "D": channels("C4H6O4", "mech-a", "mech-b", prefix="D"),
        }
        records, counts = read_partition(part_of(neutrals, traces), traces)
        assert records["F-mech-b"]["verdict"] == VERDICT_UNTESTABLE
        assert records["F-mech-b"]["reason"] == REASON_FLAT
        assert records["F-mech-b"]["r"] is None
        assert records["D-mech-a"]["reason"] == REASON_FEW_SPECTRA
        assert records["D-mech-a"]["n_spectra"] == MIN_SPECTRA - 1
        assert counts["untestable_flat"] == 1
        assert counts["untestable_few_spectra"] == 1
        assert counts["co_varying"] == counts["not_co_varying"] == 0

    def test_the_reagent_ions_normalise_the_traces(self):
        own = wave()
        traces = {
            **background(),
            "P-mech-a": trace(own, base=5.0),
            "P-mech-b": trace(own, base=4.0),
            "NO3": trace([0.0] * 16, base=6.0),
        }
        neutrals = {"P": channels("C3H4O4", "mech-a", "mech-b", prefix="P")}
        part = part_of(neutrals, [k for k in traces if k != "NO3"], reagents=["NO3"])
        records, counts = read_partition(part, traces)
        assert records["P-mech-a"]["normalised"] is True
        assert counts["normalised"] is True
        assert counts["reagent_ions"] == 1

    def test_a_trace_with_no_members_reads_as_empty(self):
        traces = background()
        neutrals = {"P": channels("C3H4O4", "mech-a", "mech-b", prefix="P")}
        part = part_of(neutrals, list(traces) + ["P-mech-a", "P-mech-b"])
        records, _ = read_partition(part, traces)
        assert records["P-mech-a"]["n_spectra"] == 0
        assert records["P-mech-a"]["reason"] == REASON_FEW_SPECTRA


class TestTheRecord:
    def test_the_top_is_the_strongest_partner_s_reading_in_full(self):
        own = Channel("x", "C3H4O4", "C3H3O4-", "mech-a", "[M-H]-")
        flat = Channel("y", "C3H4O4", "C3H4O4NO3-", "mech-b", "[M+NO3]-")
        weak = Channel("z", "C3H4O4", "C3H4O4Br-", "mech-c", "[M+Br]-")
        record = co_variation_record(
            own,
            [
                (flat, CoVariation(VERDICT_UNTESTABLE, 40, reason=REASON_FLAT)),
                (
                    weak,
                    CoVariation(
                        VERDICT_NOT_CO_VARYING, 30, r=0.12345, spread=(0.1, 0.2)
                    ),
                ),
            ],
            normalised=True,
            batch_peak_run_id="run-9",
        )
        assert record == {
            "formula": "C3H4O4",
            "ionization_mechanism_id": "mech-a",
            "channel": "[M-H]-",
            "verdict": VERDICT_NOT_CO_VARYING,
            "r": 0.123,
            "n_spectra": 30,
            "reason": None,
            "spread": [0.1, 0.2],
            "partner_batch_peak_id": "z",
            "partner_channel": "[M+Br]-",
            "partners": [
                {
                    "batch_peak_id": "z",
                    "formula": "C3H4O4",
                    "ion_formula": "C3H4O4Br-",
                    "ionization_mechanism_id": "mech-c",
                    "channel": "[M+Br]-",
                    "verdict": VERDICT_NOT_CO_VARYING,
                    "r": 0.123,
                    "n_spectra": 30,
                    "reason": None,
                    "spread": [0.1, 0.2],
                },
                {
                    "batch_peak_id": "y",
                    "formula": "C3H4O4",
                    "ion_formula": "C3H4O4NO3-",
                    "ionization_mechanism_id": "mech-b",
                    "channel": "[M+NO3]-",
                    "verdict": VERDICT_UNTESTABLE,
                    "r": None,
                    "n_spectra": 40,
                    "reason": REASON_FLAT,
                    "spread": None,
                },
            ],
            "normalised": True,
            "batch_peak_run_id": "run-9",
        }


class TestTheRun:
    def test_the_summary_sums_the_partitions_and_names_the_thresholds(self):
        own = wave()
        anchors = [
            anchor("P-a", "C3H4O4", "mech-a"),
            anchor("P-b", "C3H4O4", "mech-b"),
            anchor("Q-a", "C3H4O4", "mech-a", ionization_mode_id="mode-2"),
            anchor("Q-b", "C3H4O4", "mech-b", ionization_mode_id="mode-2"),
        ]
        bg = background()
        anchors += [anchor(k) for k in bg]
        anchors += [anchor(f"{k}-2", ionization_mode_id="mode-2") for k in bg]
        traces = {
            **bg,
            **{f"{k}-2": v for k, v in bg.items()},
            "P-a": trace(own, base=5.0),
            "P-b": trace(own, base=4.0),
            "Q-a": trace(own, base=5.0),
            "Q-b": trace([-v for v in own], base=4.0),
        }
        records, summary = read_batch(
            anchors, NOTATIONS, traces, batch_peak_run_id="run-1"
        )
        assert set(records) == {"P-a", "P-b", "Q-a", "Q-b"}
        assert summary["thresholds"] == {
            "co_varying_r": 0.6,
            "min_spectra": 10,
            "flat_spread": 0.02,
            "persistent_share": 0.5,
            "min_common_mode_traces": 5,
        }
        assert [p["ionization_mode_id"] for p in summary["partitions"]] == [
            "mode-1",
            "mode-2",
        ]
        assert [p["instrument_type"] for p in summary["partitions"]] == ["orbi"] * 2
        assert summary["neutrals_read"] == 2
        assert summary["pairs"] == 2
        assert summary["co_varying"] == 1
        assert summary["not_co_varying"] == 1
        assert summary["untestable_few_spectra"] == summary["untestable_flat"] == 0
        assert summary["anchors_recorded"] == 4
        # A partition's traces are read only against its own.
        assert records["Q-a"]["partner_batch_peak_id"] == "Q-b"

    def test_the_outcome_names_what_it_read_and_that_no_tier_moved(self):
        outcome = co_variation_outcome(
            {
                "neutrals_read": 3,
                "co_varying": 2,
                "not_co_varying": 1,
                "untestable_few_spectra": 4,
                "untestable_flat": 1,
            },
            "sb-1",
        )
        assert outcome["status"] == "success"
        assert outcome["message"] == (
            "Read 3 compounds seen through two channels or more: 2 pairs of "
            "channels co-vary across the batch, 1 does not, and 5 could not be "
            "read (4 on too few spectra, 1 flat). No tier moved."
        )
        assert outcome["_notification_data"] == {"sample_batch_id": "sb-1"}

    def test_a_batch_with_nothing_to_read_says_so(self):
        outcome = co_variation_outcome(
            {
                "neutrals_read": 0,
                "co_varying": 0,
                "not_co_varying": 0,
                "untestable_few_spectra": 0,
                "untestable_flat": 0,
            },
            "sb-1",
        )
        assert outcome["status"] == "partial"
        assert "seen through two ionization channels" in outcome["message"]


# --- the record on the anchor ---------------------------------------------------

_RECORD = {
    "formula": "A",
    "ionization_mechanism_id": "m",
    "verdict": VERDICT_CO_VARYING,
    "r": 0.9,
}


def _fit(formula, mechanism="m", fit=0.9, intensity=1000.0):
    return {
        "assigned_formula": formula,
        "role": "M0",
        "fit_score": fit,
        "intensity": intensity,
        "tier": "assigned",
        "ionization_mechanism_id": mechanism,
    }


class TestTheConsensusKeepsTheRecord:
    def test_while_it_names_the_formula_and_the_mechanism(self):
        c = compute_consensus([_fit("A"), _fit("A")], co_variation=_RECORD)
        assert c.provenance[CO_VARIATION_KEY] == _RECORD

    def test_not_once_it_moves_to_another_formula(self):
        c = compute_consensus([_fit("B"), _fit("B")], co_variation=_RECORD)
        assert CO_VARIATION_KEY not in c.provenance

    def test_not_once_it_reads_the_formula_through_another_mechanism(self):
        c = compute_consensus([_fit("A", "n"), _fit("A", "n")], co_variation=_RECORD)
        assert c.consensus_formula == "A"
        assert CO_VARIATION_KEY not in c.provenance

    def test_not_on_an_anchor_nothing_reads(self):
        bare = {"assigned_formula": None, "role": "unassigned", "tier": "unassigned"}
        c = compute_consensus([bare], co_variation=_RECORD)
        assert CO_VARIATION_KEY not in c.provenance

    def test_beside_a_series_record(self):
        series = {"formula": "A", "unit": "CH2"}
        c = compute_consensus([_fit("A")], series=series, co_variation=_RECORD)
        assert c.provenance[CO_VARIATION_KEY] == _RECORD
        assert c.provenance["series"] == series


@pytest.mark.parametrize(
    "provenance, record",
    [
        ({CO_VARIATION_KEY: _RECORD}, _RECORD),
        ({CO_VARIATION_KEY: {"r": 0.9}}, None),
        ({CO_VARIATION_KEY: "co_varying"}, None),
        (None, None),
    ],
)
def test_a_record_names_a_formula(provenance, record):
    assert co_variation_record_of({"provenance": provenance}) == record


@pytest.mark.parametrize(
    "formula, mechanism, served",
    [("A", "m", True), ("B", "m", False), ("A", "n", False), (None, None, False)],
)
def test_a_record_stands_only_beside_the_reading_it_was_read_for(
    formula, mechanism, served
):
    anchor_ = {
        "provenance": {CO_VARIATION_KEY: _RECORD},
        "consensus_formula": formula,
        "ionization_mechanism_id": mechanism,
    }
    assert standing_co_variation_record(anchor_) == (_RECORD if served else None)


def _ledger_anchor(**over):
    base = dict(
        batch_peak_id="bp-1",
        sample_batch_id="sb-1",
        mz=181.0707,
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
        provenance={CO_VARIATION_KEY: _RECORD},
        engine_tier=None,
        engine_claim=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("mechanism, served", [("m", True), ("n", False)])
def test_the_ledger_and_a_snapshot_serve_a_record_only_beside_its_reading(
    mechanism, served
):
    anchor_ = _ledger_anchor(ionization_mechanism_id=mechanism)
    expected = _RECORD if served else None
    assert _batch_peak_meta(anchor_)["co_variation"] == expected
    row = anchor_snapshot_row("run-1", anchor_, [])
    assert row["co_variation"] == expected
    meta = snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")
    assert meta["co_variation"] == expected


@pytest.mark.parametrize(
    "row, served",
    [
        ({"role": "M0", "assigned_formula": "A", "ionization_mechanism_id": "m"}, True),
        # Another reading of the same peak is not what was read.
        (
            {"role": "M0", "assigned_formula": "B", "ionization_mechanism_id": "m"},
            False,
        ),
        (
            {"role": "M0", "assigned_formula": "A", "ionization_mechanism_id": "n"},
            False,
        ),
        # An isotope line is judged through its owner.
        (
            {
                "role": "iso_child",
                "assigned_formula": "A",
                "ionization_mechanism_id": "m",
            },
            False,
        ),
    ],
)
def test_a_sample_s_row_shows_the_record_where_it_reads_the_same(row, served):
    assert row_co_variation(row, _ledger_anchor()) == (_RECORD if served else None)


def test_a_row_whose_peak_is_in_no_batch_peak_shows_none():
    row = {"role": "M0", "assigned_formula": "A", "ionization_mechanism_id": "m"}
    assert row_co_variation(row, None) is None


def test_a_row_shows_nothing_of_a_record_its_anchor_no_longer_reads():
    row = {"role": "M0", "assigned_formula": "A", "ionization_mechanism_id": "m"}
    moved = _ledger_anchor(consensus_formula="B")
    assert row_co_variation(row, moved) is None
