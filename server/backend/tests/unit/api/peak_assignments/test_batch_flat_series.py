"""Unit tests for the flat-series reading's pure parts (step 4.2e).

No DB: which batch peaks are read and on which reading, what a partition's
reading records and counts - the source ion named only from the batch's own
profiles -, that the consensus carries the record and moves no tier on it,
and what the ledger, a snapshot, a sample's row and the export read of it.
The traces themselves are the library's
(``libraries/tools/tests/test_coherence.py``). See ``batch_coherence.py``.
"""

import math
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_coherence import (
    PartitionAnchors,
    Reading,
    co_variation_outcome,
    partition_anchors,
    read_batch_flatness,
    read_partition_flatness,
)
from mascope_backend.api.new.peak_assignments.batch_export import (
    COLUMNS,
    member_export_row,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    FLAT_SERIES_KEY,
    TIER_REASONS_KEY,
    compute_consensus,
    flat_series_record_of,
    is_stale,
    row_flat_series,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import library_ion
from mascope_tools.composition.coherence import (
    FLAT_CV,
    FLAT_MIN_SHARE,
    REASON_RARE,
    REASON_STEADY,
    VERDICT_FLAT,
    VERDICT_UNTESTABLE,
    VERDICT_VARYING,
)


SAMPLES = [f"s{i:02d}" for i in range(24)]
TIC = {s: 1e6 for s in SAMPLES}


def anchor(key, formula=None, mechanism="m", **extra):
    """A batch peak as the reading reads one."""
    fields = dict(
        batch_peak_id=key,
        ionization_mode_id="mode-1",
        intensity_variable="sum_peak_heights",
        consensus_formula=formula,
        consensus_ion_formula=f"{formula}-" if formula else None,
        ionization_mechanism_id=mechanism if formula else None,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=None,
        isotopologue_of=None,
        provenance=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def flat(level=1000.0):
    return {s: level for s in SAMPLES}


def diel(amplitude=0.6, level=3.0, phase=0.0):
    return {
        s: 10.0 ** (level + amplitude * math.sin(2 * math.pi * i / 12.0 + phase))
        for i, s in enumerate(SAMPLES)
    }


def varying_batch(**more):
    """Four varying anchors without a formula, so the partition varies, and
    the anchors given."""
    anchors = [anchor(f"v{i}") for i in range(4)]
    traces = {f"v{i}": diel(phase=i) for i in range(4)}
    for key, (bp, trace) in more.items():
        anchors.append(bp)
        traces[key] = trace
    return anchors, traces


# --- which batch peaks are read -------------------------------------------------


class TestWhichReadingsAreRecorded:
    def test_every_trace_that_commits_a_formula_at_any_tier(self):
        anchors = [
            anchor("a", "A"),
            anchor("c", "C", consensus_tier="candidate"),
            anchor("b", "B", consensus_tier="below_assignability"),
            # A formula no tier commits (an import's row that names one) is
            # a trace, not a reading.
            anchor("n", "N", consensus_tier="unassigned"),
            anchor("u"),
            anchor("line", "A", isotopologue_of="a"),
            anchor("r", consensus_role="reagent", consensus_ion_formula="NO3-"),
        ]
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        assert sorted(part.readings) == ["a", "b", "c"]
        assert part.readings["c"] == Reading("C", "C-", "m", "candidate")
        assert sorted(part.traces) == ["a", "b", "c", "n", "u"]
        assert part.reagents == ["r"]


# --- the partition's reading ------------------------------------------------------


class TestAPartitionsFlatness:
    def test_a_flat_trace_is_recorded_flat_and_a_diel_one_varying(self):
        anchors, traces = varying_batch(
            bg=(anchor("bg", "C2H3N3"), flat()),
            day=(anchor("day", "C10H16O3"), diel(amplitude=0.8)),
        )
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        records, counts = read_partition_flatness(
            part, traces, TIC, batch_peak_run_id="run-1"
        )
        assert sorted(records) == ["bg", "day"]
        bg = records["bg"]
        assert bg["verdict"] == VERDICT_FLAT and bg["cv"] == 0.0
        assert bg["formula"] == "C2H3N3"
        assert bg["ion_formula"] == "C2H3N3-"
        assert bg["ionization_mechanism_id"] == "m"
        assert bg["n_present"] == bg["n_spectra"] == len(SAMPLES)
        assert bg["share"] == 1.0
        assert bg["normaliser"] == "tic" and bg["normalised"] is True
        assert bg["flat_cv"] == FLAT_CV and bg["min_share"] == FLAT_MIN_SHARE
        assert bg["source_ion"] is None
        assert bg["batch_peak_run_id"] == "run-1"
        assert records["day"]["verdict"] == VERDICT_VARYING
        assert counts["readings"] == 2 and counts["flat"] == 1
        assert counts["varying"] == 1 and counts["flat_assigned"] == 1
        assert counts["flat_source_ions"] == 0
        assert counts["varying_share"] == round(5 / 6, 4)

    def test_a_flat_reading_below_assigned_is_not_counted_at_assigned(self):
        anchors, traces = varying_batch(
            c=(anchor("c", "C", consensus_tier="candidate"), flat())
        )
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        _, counts = read_partition_flatness(part, traces, TIC)
        assert counts["flat"] == 1 and counts["flat_assigned"] == 0

    def test_a_flat_reading_whose_ion_the_batch_s_list_names_carries_it(self):
        anchors, traces = varying_batch(
            no2=(anchor("no2", "NO2", consensus_ion_formula="NO2+"), flat()),
            day=(anchor("day", "N2O", consensus_ion_formula="NO2+"), diel()),
        )
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        records, counts = read_partition_flatness(
            part, traces, TIC, profiles=["EASYIC_POS"]
        )
        assert records["no2"]["source_ion"] == {
            "ion_formula": "NO2+",
            "label": "[NO2]+",
            "family": "air",
            "kind": "cluster",
            "mz": round(library_ion("NO2+", ["EASYIC_POS"]).mz, 5),
        }
        # Only a flat trace is flagged the source's: a varying one with the
        # same ion names none.
        assert records["day"]["source_ion"] is None
        assert counts["flat_source_ions"] == 1

    @pytest.mark.parametrize("profiles", [[], ["BR"]])
    def test_no_source_ion_from_a_list_the_batch_did_not_run(self, profiles):
        # The batch's runs resolved no profile, or one whose list holds no
        # such ion (a bromide source's air ions are anions).
        anchors, traces = varying_batch(
            no2=(anchor("no2", "NO2", consensus_ion_formula="NO2+"), flat())
        )
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        records, counts = read_partition_flatness(part, traces, TIC, profiles=profiles)
        assert records["no2"]["verdict"] == VERDICT_FLAT
        assert records["no2"]["source_ion"] is None
        assert counts["flat_source_ions"] == 0

    def test_the_reagent_ions_are_the_normaliser(self):
        reagent = {s: 1e5 * (1 - 0.01 * i) for i, s in enumerate(SAMPLES)}
        cluster = {s: v * 0.02 for s, v in reagent.items()}
        anchors, traces = varying_batch(
            cl=(anchor("cl", "C2H2N2O"), cluster),
            r=(
                anchor("r", consensus_role="reagent", consensus_ion_formula="X-"),
                reagent,
            ),
        )
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        records, counts = read_partition_flatness(part, traces, TIC)
        assert counts["normaliser"] == "reagent"
        assert records["cl"]["normaliser"] == "reagent"
        assert records["cl"]["cv"] == pytest.approx(0.0, abs=1e-6)

    def test_the_tic_where_the_partition_holds_no_reagent_ion(self):
        tic = {s: 1e6 * (1 + 0.01 * i) for i, s in enumerate(SAMPLES)}
        follows = {s: v * 1e-3 for s, v in tic.items()}
        anchors, traces = varying_batch(f=(anchor("f", "F"), follows))
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        records, _ = read_partition_flatness(part, traces, tic)
        assert records["f"]["normaliser"] == "tic"
        assert records["f"]["cv"] == pytest.approx(0.0, abs=1e-6)

    def test_a_partition_that_holds_still_reads_none_flat(self):
        anchors = [anchor(f"b{i}", f"C{i + 1}H4") for i in range(6)]
        traces = {f"b{i}": flat(100.0 * (i + 1)) for i in range(6)}
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        records, counts = read_partition_flatness(part, traces, TIC)
        assert counts["steady"] is True and counts["flat"] == 0
        assert {r["reason"] for r in records.values()} == {REASON_STEADY}

    def test_a_trace_in_under_half_the_spectra_is_untestable(self):
        rare = {s: 1000.0 for s in SAMPLES[:11]}
        anchors, traces = varying_batch(rare=(anchor("rare", "R"), rare))
        part = partition_anchors(anchors, {})[("mode-1", "orbi")]
        records, _ = read_partition_flatness(part, traces, TIC)
        assert records["rare"]["verdict"] == VERDICT_UNTESTABLE
        assert records["rare"]["reason"] == REASON_RARE

    def test_the_batch_sums_its_partitions_and_names_the_thresholds(self):
        anchors, traces = varying_batch(bg=(anchor("bg", "B"), flat()))
        other = anchor("o", "O", ionization_mode_id="mode-2")
        records, summary = read_batch_flatness(
            [*anchors, other],
            {},
            {**traces, "o": flat()},
            TIC,
            profile_by_sample={SAMPLES[0]: "EASYIC_POS", "elsewhere": "BR"},
        )
        assert sorted(records) == ["bg", "o"]
        assert summary["thresholds"] == {
            "flat_cv": FLAT_CV,
            "min_share": FLAT_MIN_SHARE,
            "min_spectra": 10,
            "min_varying_share": 0.2,
        }
        assert [p["ionization_mode_id"] for p in summary["partitions"]] == [
            "mode-1",
            "mode-2",
        ]
        # A partition's lists are its samples' runs' profiles.
        assert [p["profiles"] for p in summary["partitions"]] == [
            ["EASYIC_POS"],
            ["EASYIC_POS"],
        ]
        assert summary["readings"] == 2 and summary["flat"] == 1
        assert summary["flat_assigned"] == 1
        # The second partition is one flat trace: nothing there varies.
        assert summary["untestable"] == 1


# --- the rule in the consensus ----------------------------------------------------

_FLAT = {
    "formula": "A",
    "ion_formula": "A-",
    "ionization_mechanism_id": "m",
    "verdict": VERDICT_FLAT,
    "cv": 0.08,
    "n_present": 22,
    "n_spectra": 24,
    "share": 0.9167,
    "normaliser": "tic",
    "normaliser_cv": 0.05,
    "normalised": True,
    "flat_cv": FLAT_CV,
    "min_share": FLAT_MIN_SHARE,
    "source_ion": None,
}
_SOURCE = {
    **_FLAT,
    "formula": "NO2",
    "ion_formula": "NO2+",
    "source_ion": {
        "ion_formula": "NO2+",
        "label": "[NO2]+",
        "family": "air",
        "kind": "cluster",
        "mz": 45.99238,
    },
}


def _fit(formula="A", mechanism="m", fit=0.9, tier="assigned", sample="s1", ion=None):
    return {
        "sample_item_id": sample,
        "assigned_formula": formula,
        "ion_formula": ion or f"{formula}-",
        "role": "M0",
        "fit_score": fit,
        "intensity": 1000.0,
        "tier": tier,
        "ionization_mechanism_id": mechanism,
    }


def _consensus(record=_FLAT, members=None, **extra):
    members = members or [_fit(sample="s1"), _fit(sample="s2")]
    return compute_consensus(members, flat_series=record, **extra)


class TestTheConsensusKeepsTheRecord:
    def test_while_it_names_the_reading_it_was_read_on(self):
        assert _consensus().provenance[FLAT_SERIES_KEY] == _FLAT

    def test_not_on_another_formula_or_mechanism(self):
        other = [_fit("B", sample="s1"), _fit("B", sample="s2")]
        assert is_stale(_consensus(members=other).provenance[FLAT_SERIES_KEY])
        through = [_fit(mechanism="n", sample="s1")]
        assert is_stale(_consensus(members=through).provenance[FLAT_SERIES_KEY])

    @pytest.mark.parametrize(
        "provenance, record",
        [
            ({FLAT_SERIES_KEY: _FLAT}, _FLAT),
            ({FLAT_SERIES_KEY: {"cv": 0.1}}, None),
            ({FLAT_SERIES_KEY: "flat"}, None),
            (None, None),
        ],
    )
    def test_a_record_names_its_verdict(self, provenance, record):
        assert flat_series_record_of({"provenance": provenance}) == record


class TestItMovesNothing:
    @pytest.mark.parametrize("record", [_FLAT, _SOURCE])
    def test_a_flat_reading_keeps_its_tier_role_and_formula(self, record):
        members = [
            _fit(record["formula"], ion=record["ion_formula"], sample="s1"),
            _fit(record["formula"], ion=record["ion_formula"], sample="s2"),
        ]
        bare = compute_consensus(members)
        c = compute_consensus(members, flat_series=record)
        assert c.provenance[FLAT_SERIES_KEY] == record
        assert c.consensus_tier == bare.consensus_tier == "assigned"
        assert c.consensus_role is None
        assert c.consensus_formula == record["formula"]
        assert c.ionization_mechanism_id == "m"
        assert TIER_REASONS_KEY not in c.provenance


# --- what readers are served ------------------------------------------------------


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
        provenance={FLAT_SERIES_KEY: _FLAT},
        engine_tier=None,
        engine_claim=None,
        engine_level=None,
        engine_evidence=None,
        engine_lift=None,
        seeded=False,
        continuity_grade=None,
        continuity=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("formula, served", [("A", True), ("B", False)])
def test_the_ledger_and_a_snapshot_serve_the_record_beside_its_reading(formula, served):
    bp = _ledger_anchor(consensus_formula=formula)
    expected = _FLAT if served else None
    assert _batch_peak_meta(bp)["flat_series"] == expected
    row = anchor_snapshot_row("run-1", bp, [])
    assert row["flat_series"] == expected
    meta = snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")
    assert meta["flat_series"] == expected


@pytest.mark.parametrize(
    "row, served",
    [
        ({"role": "M0", "assigned_formula": "A", "ionization_mechanism_id": "m"}, True),
        (
            {"role": "M0", "assigned_formula": "B", "ionization_mechanism_id": "m"},
            False,
        ),
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
    assert row_flat_series(row, _ledger_anchor()) == (_FLAT if served else None)


def test_a_row_whose_peak_is_in_no_batch_peak_shows_none():
    row = {"role": "M0", "assigned_formula": "A", "ionization_mechanism_id": "m"}
    assert row_flat_series(row, None) is None


def _member_row(**over):
    base = dict(
        sample_item_id="s1",
        sample_peak_id="p1",
        candidate=0,
        mz_delta_ppm=0.1,
        intensity=1000.0,
        tier=tier_code("assigned"),
        role=1,
        fit_score=0.9,
        p_correct=None,
        owner_batch_peak_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_the_export_carries_the_flag_on_every_member():
    assert COLUMNS[-3:] == ("flat_series", "flat_series_cv", "flat_series_source_ion")
    row = member_export_row(_member_row(), _ledger_anchor(), "S1")
    assert row["flat_series"] == VERDICT_FLAT
    assert row["flat_series_cv"] == 0.08
    assert row["flat_series_source_ion"] is None
    source = member_export_row(
        _member_row(),
        _ledger_anchor(
            consensus_formula="NO2",
            consensus_ion_formula="NO2+",
            provenance={FLAT_SERIES_KEY: _SOURCE},
        ),
        "S1",
    )
    assert source["flat_series_source_ion"] == "[NO2]+"
    # Where the anchor no longer reads what was read, the flag is not its.
    moved = member_export_row(
        _member_row(), _ledger_anchor(consensus_formula="B"), "S1"
    )
    assert (moved["flat_series"], moved["flat_series_cv"]) == (None, None)


class TestTheList:
    @pytest.mark.parametrize(
        "ion, profiles, label",
        [
            ("NO2+", ["EASYIC_POS"], "[NO2]+"),
            ("H2NO2+", ["EASYIC_POS"], "[NO+H2O]+"),
            # Two spellings of one composition name one ion.
            ("O2NH2+", ["EASYIC_POS"], "[NO+H2O]+"),
            ("C14H10+", ["EASYIC_POS"], "[C14H10]+."),
            ("NO2-", ["BR"], "[NO2]-"),
            # The first profile naming it wins.
            ("NO2-", ["EASYIC_POS", "BR"], "[NO2]-"),
        ],
    )
    def test_an_ion_a_profile_s_list_names(self, ion, profiles, label):
        assert library_ion(ion, profiles).label == label

    @pytest.mark.parametrize(
        "ion, profiles",
        [
            ("C3H6O+", ["EASYIC_POS"]),
            ("NO2", ["EASYIC_POS"]),
            ("", ["EASYIC_POS"]),
            (None, ["EASYIC_POS"]),
            # Another profile's list names it; the batch's does not.
            ("C14H10+", ["BR"]),
            ("NO2+", []),
            ("NO2+", ["NO_SUCH_PROFILE"]),
        ],
    )
    def test_an_ion_it_does_not(self, ion, profiles):
        assert library_ion(ion, profiles) is None


# --- the run's outcome --------------------------------------------------------------


def _counts(flat=None):
    counts = {
        "neutrals_read": 1,
        "co_varying": 1,
        "not_co_varying": 0,
        "untestable_few_spectra": 0,
        "untestable_flat": 0,
    }
    if flat is not None:
        counts["flat_series"] = flat
    return counts


def test_the_outcome_names_what_the_flat_reading_found_and_moves_no_tier():
    outcome = co_variation_outcome(
        _counts({"readings": 10, "flat": 4, "flat_source_ions": 1}), "sb-1"
    )
    assert outcome["message"].endswith(
        "flat). 4 of 10 batch peaks read flat against the batch's normaliser, 1 "
        "of them an ion the source's list names. No tier moved."
    )
    assert co_variation_outcome(_counts(), "sb-1")["message"].endswith(
        "flat). No tier moved."
    )


def test_a_batch_with_no_two_channels_still_says_what_the_flat_reading_found():
    counts = _counts({"readings": 1, "flat": 1, "flat_source_ions": 0})
    counts["neutrals_read"] = 0
    outcome = co_variation_outcome(counts, "sb-1")
    assert outcome["status"] == "partial"
    assert outcome["message"].endswith(
        "1 of 1 batch peak read flat against the batch's normaliser, 0 of them "
        "an ion the source's list names. No tier moved."
    )


def test_a_partition_with_no_anchor_reads_nothing():
    records, counts = read_partition_flatness(PartitionAnchors(), {}, {})
    assert records == {} and counts["readings"] == 0
