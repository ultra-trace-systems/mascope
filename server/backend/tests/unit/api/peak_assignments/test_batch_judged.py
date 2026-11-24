"""The state a sample's series judging is recorded at (``batch_judged``,
MAS-1188): every field a judging reads moves the digest, and what a stage
stamps on every run (its version, its run id, its times) does not."""

import copy
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import batch_judged
from mascope_backend.api.new.peak_assignments.batch_judged import (
    JUDGED_STATES,
    SeriesJudging,
    judged_state,
    outcome_of,
    pass_key,
    settled,
    states_of,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig


KEY = "engine:settings"


def _member(**over):
    fields = dict(
        batch_peak_id="A",
        sample_item_id="S",
        sample_peak_id="p1",
        peak_assignment_id="row-1",
        candidate=0,
        tier=4,
        fit_score=0.9,
        role=0,
        owner_batch_peak_id=None,
        p_correct=0.8,
        intensity=1000.0,
        mz_delta_ppm=0.4,
        batch_written=None,
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def _anchor(**over):
    fields = dict(
        batch_peak_id="A",
        mz=181.0707,
        consensus_formula="C6H12O6",
        consensus_role="M0",
        consensus_ion_formula="C6H13O6+",
        ionization_mechanism_id="m1",
        isotopologue_of=None,
        ionization_mode_id="mode-1",
        intensity_variable="i",
        candidates=[
            {
                "formula": "C6H12O6",
                "ion_formula": "C6H13O6+",
                "ionization_mechanism_id": "m1",
            },
            {
                "formula": "C7H16O5",
                "ion_formula": "C7H17O5+",
                "ionization_mechanism_id": "m1",
            },
        ],
        provenance={
            "series": {
                "formula": "C6H12O6",
                "ionization_mechanism_id": "m1",
                "has_anchor": True,
                "depth": 1,
                "version": 3,
                "batch_peak_run_id": "run-1",
            }
        },
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def _ladder_anchor(**over):
    """A water-ladder rung's batch peak in the sample's partition, in which the
    sample has no member."""
    fields = dict(
        batch_peak_id="W",
        mz=37.0284,
        consensus_formula="H5O2",
        consensus_role="reagent",
        consensus_ion_formula="H5O2+",
        ionization_mechanism_id="m0",
        isotopologue_of=None,
        ionization_mode_id="mode-1",
        intensity_variable="i",
        candidates=[],
        provenance={
            "water_ladder": {
                "rung": {"n": 2},
                "reads": {
                    "role": "reagent",
                    "formula": "H5O2",
                    "ion_formula": "H5O2+",
                    "ionization_mechanism_id": "m0",
                },
                "presence": 0.9,
                "version": 2,
                "batch_peak_run_id": "run-1",
                "utc_decided": "2026-11-24T00:00:00",
            }
        },
    )
    fields.update(over)
    return SimpleNamespace(**fields)


def _state(members=None, anchors=None, partition=None, key=KEY):
    members = members if members is not None else [_member()]
    anchors = anchors if anchors is not None else [_anchor()]
    partition = partition if partition is not None else [_ladder_anchor()]
    return judged_state(members, {a.batch_peak_id: a for a in anchors}, partition, key)


def test_the_same_state_digests_the_same():
    assert _state() == _state()


@pytest.mark.parametrize(
    "field, value",
    [
        ("sample_peak_id", "p2"),
        ("peak_assignment_id", "row-2"),
        ("candidate", 1),
        ("tier", 3),
        ("fit_score", 0.5),
        ("role", 1),
        ("owner_batch_peak_id", "B"),
        ("p_correct", 0.1),
        ("intensity", 10.0),
        ("mz_delta_ppm", 1.2),
        ("batch_written", {"stage": "search", "version": 1}),
    ],
)
def test_every_field_of_a_member_moves_the_whole_state_alone(field, value):
    decisions, whole = _state()
    moved_decisions, moved_whole = _state(members=[_member(**{field: value})])
    assert moved_decisions == decisions
    assert moved_whole != whole


def test_a_candidate_that_names_another_reading_moves_the_state():
    """The registry entry the member's index names, not the index alone: a
    re-fold that rebuilt the registry reads another formula at the same
    index."""
    registry = copy.deepcopy(_anchor().candidates)
    registry[0]["formula"] = "C6H14O6"
    assert _state(anchors=[_anchor(candidates=registry)])[1] != _state()[1]


def test_a_mark_moves_the_state_by_its_stage_not_its_version():
    one = _state(members=[_member(batch_written={"stage": "search", "version": 1})])
    two = _state(
        members=[
            _member(
                batch_written={
                    "stage": "search",
                    "version": 2,
                    "before": {"tier": 2},
                }
            )
        ]
    )
    other = _state(
        members=[_member(batch_written={"stage": "isotope_lines", "version": 1})]
    )
    assert one == two
    assert other != one


@pytest.mark.parametrize(
    "field, value",
    [
        ("mz", 181.0),
        ("consensus_formula", "C7H16O5"),
        ("consensus_role", "reagent"),
    ],
)
def test_every_field_of_a_batch_peak_moves_the_decisions(field, value):
    decisions, _ = _state()
    assert _state(anchors=[_anchor(**{field: value})])[0] != decisions


def test_a_series_record_moves_the_decisions_but_not_by_its_version_or_run():
    decisions, whole = _state()
    record = dict(_anchor().provenance["series"])
    restamped = {**record, "version": 9, "batch_peak_run_id": "run-9"}
    assert _state(anchors=[_anchor(provenance={"series": restamped})]) == (
        decisions,
        whole,
    )
    assert _state(anchors=[_anchor(provenance={})])[0] != decisions


def test_a_stripped_line_moves_the_decisions_by_what_a_judging_reads():
    decisions, _ = _state()
    line = {
        "batch_peak_id": "P",
        "formula": "C6H12O6",
        "label": "13C",
        "ratio": 0.1,
        "mz_offset": 1.00335,
        "spectra": 12,
        "median_factor": 1.1,
        "scatter_ppm": 0.4,
    }

    def stripped(**over):
        return _anchor(
            isotopologue_of="P",
            provenance={
                **_anchor().provenance,
                "merged_isotopologue_of": {**line, **over},
            },
        )

    with_line = _state(anchors=[stripped()])[0]
    assert with_line != decisions
    for field, value in (
        ("batch_peak_id", "Q"),
        ("formula", "C6H14O6"),
        ("label", "18O"),
        ("ratio", 0.2),
        ("mz_offset", 2.0),
        ("above", True),
        ("verdict", "held"),
    ):
        moved = stripped(**{field: value})
        if field == "batch_peak_id":
            moved.isotopologue_of = "Q"
        assert _state(anchors=[moved])[0] != with_line, field
    # What the strip read over the spectra the row only cites.
    cited = stripped(spectra=40, median_factor=0.9, scatter_ppm=1.0, version=4)
    assert _state(anchors=[cited])[0] == with_line
    # A record from before the offset was kept: its m/z is what is read.
    no_offset = stripped(mz_offset=None, mz=182.074)
    other_mz = stripped(mz_offset=None, mz=182.08)
    assert _state(anchors=[no_offset])[0] != _state(anchors=[other_mz])[0]


def test_a_series_record_moves_the_decisions_by_what_it_vouches_for_alone():
    decisions, _ = _state()
    record = dict(_anchor().provenance["series"])
    for field, value in (
        ("formula", "C7H16O5"),
        ("ionization_mechanism_id", "m2"),
        ("has_anchor", False),
    ):
        moved = _anchor(provenance={"series": {**record, field: value}})
        if field == "formula":
            moved.consensus_formula = "C7H16O5"
            assert (
                _state(anchors=[moved])[0]
                != _state(anchors=[_anchor(consensus_formula="C7H16O5")])[0]
            )
            continue
        assert _state(anchors=[moved])[0] != decisions, field
    # The unit, the root and the depth name it in the row's reasons alone.
    named = {**record, "unit": "C2H4", "root_formula": "C4H8O4", "depth": 3}
    assert _state(anchors=[_anchor(provenance={"series": named})])[0] == decisions


def _reagent_anchor(batch_peak_id="R", present=5, **species):
    reads = {
        "role": "reagent",
        "formula": "H2O",
        "ion_formula": "H3O+",
        "ionization_mechanism_id": None,
    }
    return SimpleNamespace(
        batch_peak_id=batch_peak_id,
        mz=19.0178,
        consensus_formula="H2O",
        consensus_role="reagent",
        consensus_ion_formula="H3O+",
        ionization_mechanism_id=None,
        isotopologue_of=None,
        ionization_mode_id="mode-1",
        intensity_variable="i",
        candidates=[],
        provenance={
            "known_species": {
                "verdict": "present",
                "reads": reads,
                "species": {
                    "kind": "library",
                    "ion_formula": "H3O+",
                    "label": "hydronium",
                    **species,
                },
                "files": 9,
                "present": present,
                "test_held": 1,
                "version": 2,
                "batch_peak_run_id": "run-2",
            }
        },
    )


def test_a_library_ion_of_the_partition_moves_the_decisions_by_the_ion():
    alone = _state(partition=[])[0]
    one = _state(partition=[_reagent_anchor()])[0]
    assert one != alone
    # The files and counts it was decided on the row only cites.
    assert _state(partition=[_reagent_anchor(present=6)])[0] == one
    assert _state(partition=[_reagent_anchor(label="other")])[0] != one
    # Two batch peaks of one ion: the one in the most files speaks for it.
    twins = [_reagent_anchor("R", present=5), _reagent_anchor("S", present=3)]
    flipped = [_reagent_anchor("R", present=3), _reagent_anchor("S", present=5)]
    assert _state(partition=twins)[0] == one
    assert _state(partition=flipped)[0] != one
    # A batch peak that no longer reads as the reagent decides nothing.
    off = _reagent_anchor()
    off.consensus_role = "M0"
    assert _state(partition=[off])[0] == alone


def test_a_known_species_record_on_the_samples_own_batch_peak_is_not_read():
    """A listed compound decided present is read only by a path that proposes
    the known set, which a judging again does not; a library ion is read as
    its partition's (above)."""
    reads = {
        "role": "M0",
        "formula": "C6H12O6",
        "ion_formula": "C6H13O6+",
        "ionization_mechanism_id": "m1",
    }
    known = _anchor(
        provenance={
            **_anchor().provenance,
            "known_species": {"verdict": "present", "reads": reads, "files": 3},
        }
    )
    assert _state(anchors=[known])[0] == _state()[0]


def test_a_record_of_the_samples_partition_moves_the_decisions_and_another_not():
    decisions, _ = _state()
    ladder = _ladder_anchor().provenance["water_ladder"]
    moved = _ladder_anchor(provenance={"water_ladder": {**ladder, "presence": 0.5}})
    assert _state(partition=[moved])[0] != decisions
    restamped = _ladder_anchor(
        provenance={
            "water_ladder": {
                **ladder,
                "version": 7,
                "batch_peak_run_id": "run-7",
                "utc_decided": "2026-11-25T00:00:00",
            }
        }
    )
    assert _state(partition=[restamped])[0] == decisions
    elsewhere = _ladder_anchor(
        ionization_mode_id="mode-2",
        provenance={"water_ladder": {**ladder, "presence": 0.5}},
    )
    assert _state(partition=[elsewhere])[0] == _state(partition=[])[0]
    other_variable = _ladder_anchor(intensity_variable="j")
    assert _state(partition=[other_variable])[0] == _state(partition=[])[0]


def test_another_engine_or_other_settings_move_the_decisions():
    assert _state(key="other")[0] != _state()[0]
    assert pass_key(PeakAssignmentConfig()) == pass_key(None)
    assert pass_key(PeakAssignmentConfig(max_alternatives=7)) != pass_key(None)
    assert pass_key(None).startswith(batch_judged.PEAK_ASSIGNMENT_ENGINE_VERSION)


def test_a_sample_is_settled_only_where_its_state_is_the_one_recorded():
    now = {"S1": ("d", "w1"), "S2": ("d", "w2"), "S3": ("d", "w3")}
    last = {"S1": "w1", "S2": "other"}
    named = {"S1": {"A"}, "S2": {"A"}, "S3": {"A"}, "S4": {"A"}}
    assert settled(named, now, last) == {"S1"}


def test_the_states_ride_on_the_outcome_and_the_summary_keeps_them_apart():
    judging = SeriesJudging("batch", KEY, states={"S2": "w2", "S1": "w1"}, left=1)
    outcome = outcome_of(
        judging, {"status": "success", "message": "Judged 1 sample.", "data": {}}
    )
    assert outcome["data"]["samples_left"] == 1
    assert outcome["message"] == (
        "Judged 1 sample. 1 sample read as it did after its last judging and "
        "was left as it was."
    )
    assert list(outcome[JUDGED_STATES]) == ["S1", "S2"]
    assert states_of([{"status": "success"}, outcome]) == {
        JUDGED_STATES: {"S1": "w1", "S2": "w2"}
    }
    assert states_of([{"status": "success"}]) == {}
    plain = {"status": "success", "message": "m", "data": {}}
    assert outcome_of(None, dict(plain)) == plain
    none_left = outcome_of(SeriesJudging("batch", KEY), dict(plain))
    assert none_left["message"] == "m" and "samples_left" not in none_left["data"]


@pytest.mark.asyncio
async def test_a_state_is_recorded_only_where_the_decisions_held(monkeypatch):
    """Right after its judging: the whole state as it reads then, where its
    decisions read as before the judging; else none."""
    reads = {"S1": ("d", "w1-after"), "S2": ("moved", "w2-after")}

    async def read_states(sample_batch_id, samples, key):
        return {s: reads[s] for s in samples if s in reads}

    monkeypatch.setattr(batch_judged, "read_states", read_states)
    judging = SeriesJudging(
        "batch",
        KEY,
        before={"S1": ("d", "w1"), "S2": ("d", "w2")},
        states={"S2": "old"},
    )
    await judging.judged("S1")
    await judging.judged("S2")
    await judging.judged("S3")
    assert judging.states == {"S1": "w1-after"}


@pytest.mark.asyncio
async def test_narrowing_leaves_the_settled_samples_but_never_a_written_one(
    monkeypatch,
):
    now = {"S1": ("d", "w1"), "S2": ("d", "w2"), "S3": ("d", "w3")}
    seen = {}

    async def read_states(sample_batch_id, samples, key):
        seen["samples"] = set(samples)
        seen["key"] = key
        return {s: now[s] for s in samples if s in now}

    async def last_states(sample_batch_id):
        return {"S1": "w1", "S2": "w2", "S3": "old"}

    monkeypatch.setattr(batch_judged, "read_states", read_states)
    monkeypatch.setattr(batch_judged, "last_states", last_states)
    corroborated = {"S1": {"A"}, "S2": {"A"}, "S3": {"A"}}
    judging, left = await SeriesJudging.narrow(
        "batch", PeakAssignmentConfig(max_alternatives=7), {"S2"}, corroborated
    )
    assert left == {"S2": {"A"}, "S3": {"A"}}
    assert judging.left == 1 and judging.states == {"S1": "w1"}
    assert seen["samples"] == {"S1", "S2", "S3"}
    assert seen["key"] == pass_key(PeakAssignmentConfig(max_alternatives=7))
    assert judging.before == now


@pytest.mark.asyncio
async def test_narrowing_that_cannot_read_judges_every_sample(monkeypatch):
    async def fails(*args, **kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr(batch_judged, "read_states", fails)
    corroborated = {"S1": {"A"}}
    judging, left = await SeriesJudging.narrow("batch", None, set(), corroborated)
    assert left == corroborated and judging.left == 0 and judging.before == {}
