"""Integration tests for the batch's merged-isotopologue reading (step 4.2f).

Seeds a batch of six samples folded into the ledger: three parents and, at
their isotope lines, four rows every sample's run committed as a compound of
its own. The first parent's 13C and 18O lines hold their predicted shares; the
second parent's 13C line is three times its share; the third parent's 13C line
holds its share and is pinned by hand. One sample is folded from an in-app run
of its own and one from an imported run, the rest from rows no run holds. The
reading's arithmetic is the library's (``test_merged_isotopologues.py``) and
the plan's the pure tests' (``unit/.../test_batch_merged_isotopologues.py``);
what is proven here is the batch-level contract: the rows stripped and kept,
the members and the in-app run's rows rewritten and the import's left, the
records on both batch peaks and both rows, the consensus keeping them through
a recompute and a re-fold, a hand assignment holding its row, the ledger and
an earlier run's snapshot serving them, and the route running it as a batch
run. See ``batch_merged_isotopologues.py``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY

import pytest
import pytest_asyncio
from pass_runs import step_outcome  # the tests root, on sys.path
from sqlalchemy import delete, select, update
from stage_stubs import (  # the tests root, on sys.path
    frame,
    run_sample,
    stub_known_set,
    stub_sample_reads,
    stub_seeded_scorer,
)
from stage_stubs import mechanism as mechanism_row

from mascope_backend.api.new.peak_assignments import (
    batch_merged_isotopologues,
    batch_peaks_routes,
)
from mascope_backend.api.new.peak_assignments.batch_merged_isotopologues import (
    run_batch_merged_isotopologues,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    is_stale,
    role_code,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ISOTOPOLOGUES,
    ACTION_STRIP_ISOTOPOLOGUES,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    fold_assignment_id,
    fold_run_id,
    member_row,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.merged_isotopologues import (
    predict_isotopologue_lines,
)


pytestmark = pytest.mark.asyncio

#: A mechanism no other suite names: the mechanism table's notations are
#: unique, and the integration database is shared by the whole run.
MECH, NOTATION = "mech-strip-h", "[M-H]- (merged isotopologue test)"
#: Another mechanism to the same ions, for a parent read another way.
MECH_FA, NOTATION_FA = "mech-strip-fa", "[M+HCOO]- (merged isotopologue test)"

N_SAMPLES = 6
#: The sample folded from an in-app run of its own, and the one folded from
#: an imported run.
RUN_SAMPLE, IMPORT_SAMPLE = "S00", "S01"


def _line(ion, label):
    [found] = [x for x in predict_isotopologue_lines(ion) if x.label == label]
    return found.mz_offset, found.ratio


C13_P, R13_P = _line("C10H15O4-", "13C")
O18_P, R18_P = _line("C10H15O4-", "18O")
C13_P2, R13_P2 = _line("C12H19O4-", "13C")
C13_P3, R13_P3 = _line("C8H11O4-", "13C")


#: Peak id -> (m/z, formula, ion formula, intensity of sample i).
def _peaks():
    p = lambda i: 1e6 * (1 + i)  # noqa: E731
    p2 = lambda i: 2e6 * (1 + i)  # noqa: E731
    p3 = lambda i: 3e6 * (1 + i)  # noqa: E731
    return {
        "p": (199.0976, "C10H16O4", "C10H15O4-", p),
        "x": (199.0976 + C13_P, "C9H12N2O2", "C9H11N2O2-", lambda i: p(i) * R13_P),
        "o": (199.0976 + O18_P, "C8H8N2O3", "C8H7N2O3-", lambda i: p(i) * R18_P),
        "p2": (227.1289, "C12H20O4", "C12H19O4-", p2),
        "z": (
            227.1289 + C13_P2,
            "C11H16N2O2",
            "C11H15N2O2-",
            lambda i: p2(i) * R13_P2 * 3,
        ),
        "p3": (171.0663, "C8H12O4", "C8H11O4-", p3),
        "w": (171.0663 + C13_P3, "C7H8N2O2", "C7H7N2O2-", lambda i: p3(i) * R13_P3),
    }


def _rows(sample_item_id, index, *, run_id=None, overrides=None):
    rows = {}
    for peak_id, (mz, formula, ion, intensity) in _peaks().items():
        changes = (overrides or {}).get(peak_id, {})
        rows[peak_id] = dict(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=run_id or fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id=peak_id,
            sample_peak_mz=mz,
            sample_peak_intensity=intensity(index),
            role="M0",
            assigned_formula=formula,
            ion_formula=ion,
            ionization_mechanism_id=MECH,
            source="untargeted",
            tier="assigned",
            fit_score=0.9,
            mz_error_ppm=0.1,
            owner_peak_assignment_id=None,
            provenance={"evidence": 0.8, "plausibility": 0.9},
        )
        if peak_id == "x":
            # The 13C line's own reading, weaker than its parent's, so its
            # rewrite shows in the tier, the fit and the probability.
            rows[peak_id].update(
                tier="candidate",
                fit_score=0.5,
                provenance={"evidence": 0.45, "p_correct": 0.7},
            )
        rows[peak_id].update(changes)
    return list(rows.values())


async def _seed(async_session_factory, pa_test_data, overrides=None):
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    samples, runs = {}, {}
    async with async_session_factory() as session:
        dataset_id = (
            await session.execute(
                select(SampleBatch.dataset_id).where(
                    SampleBatch.sample_batch_id == pa_test_data["sample_batch_id"]
                )
            )
        ).scalar_one()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Merged isotopologues {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        for mechanism, notation in ((MECH, NOTATION), (MECH_FA, NOTATION_FA)):
            if await session.get(IonizationMechanism, mechanism) is None:
                session.add(
                    IonizationMechanism(
                        ionization_mechanism_id=mechanism,
                        ionization_mechanism_polarity="-",
                        ionization_mechanism=notation,
                    )
                )
        for i in range(N_SAMPLES):
            name = f"S{i:02d}"
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-strip-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 12, 0, i, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Merged isotopologues {name}",
                    sample_item_type="sample",
                    polarity="-",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        await session.flush()
        for name, engine in ((RUN_SAMPLE, "mascope"), (IMPORT_SAMPLE, "peaky-test")):
            run_id = gen_id()
            runs[name] = run_id
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=samples[name],
                    engine=engine,
                    engine_version="0.1.0-test",
                    status="completed",
                    peak_assignment_run_utc_created=now,
                    peak_assignment_run_utc_completed=now,
                )
            )
            await session.flush()
            for row in _rows(
                samples[name],
                int(name[1:]),
                run_id=run_id,
                overrides=(overrides or {}).get(name),
            ):
                session.add(PeakAssignment(**row))
        await session.commit()
    for i in range(N_SAMPLES):
        name = f"S{i:02d}"
        if name in runs:
            folded = await fold_sample_into_batch_peaks(samples[name])
        else:
            folded = await fold_sample_into_batch_peaks(
                samples[name],
                rows=[SimpleNamespace(**r) for r in _rows(samples[name], i)],
                persisted=False,
            )
        assert folded is not None
    ids = await _ids(async_session_factory, batch_id)
    # The third parent's line is pinned by hand to the formula its runs read.
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == ids["w"])
            .values(
                provenance={
                    "manual": {
                        "formula": "C7H8N2O2",
                        "ion_formula": "C7H7N2O2-",
                        "ionization_mechanism_id": MECH,
                    }
                }
            )
        )
        await session.commit()
    return {"batch_id": batch_id, "samples": samples, "runs": runs, "ids": ids}


async def _ids(session_factory, batch_id):
    """Peak id -> its batch peak's id, by the m/z each peak was seeded at."""
    async with session_factory() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
    by_mz = {round(anchor.mz, 3): anchor.batch_peak_id for anchor in anchors}
    return {peak_id: by_mz[round(mz, 3)] for peak_id, (mz, *_) in _peaks().items()}


async def _anchors(session_factory, batch):
    async with session_factory() as session:
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == batch["batch_id"]
                    )
                )
            )
            .scalars()
            .all()
        }
    return {key: anchors[batch_peak_id] for key, batch_peak_id in batch["ids"].items()}


async def _members(session_factory, batch_peak_id):
    async with session_factory() as session:
        return {
            member.sample_item_id: member
            for member in (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id == batch_peak_id
                    )
                )
            )
            .scalars()
            .all()
        }


async def _ledger_stamp(session_factory, batch_id):
    """When the batch's current run says its ledger was last written."""
    async with session_factory() as session:
        return (
            await session.execute(
                select(BatchPeakRun.ledger_utc_modified).where(
                    BatchPeakRun.sample_batch_id == batch_id,
                    BatchPeakRun.is_current == 1,
                )
            )
        ).scalar_one()


async def _run_rows(session_factory, run_id):
    """Peak id -> the run's row on it."""
    async with session_factory() as session:
        return {
            row.sample_peak_id: row
            for row in (
                await session.execute(
                    select(PeakAssignment).where(
                        PeakAssignment.peak_assignment_run_id == run_id
                    )
                )
            )
            .scalars()
            .all()
        }


@pytest_asyncio.fixture
async def strip_batch(async_session_factory, pa_test_data):
    return await _seed(async_session_factory, pa_test_data)


# The records are read off the stored provenance by the names the reading
# writes, not through the constants: a renamed key orphans every record a
# server already holds.
def _stripped(anchor):
    record = (anchor.provenance or {}).get("merged_isotopologue_of")
    # A record a consensus kept marked stale stands for nothing.
    return None if is_stale(record) else record


def _lines(anchor):
    record = (anchor.provenance or {}).get("merged_isotopologues")
    # A record a consensus kept marked stale stands for nothing.
    return None if is_stale(record) else record


async def test_a_row_at_its_parents_line_is_stripped_and_the_ledgers_agree(
    async_session_factory, strip_batch
):
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    before = await _anchors(async_session_factory, batch)
    assert before["x"].consensus_tier == "candidate"
    stamped = await _ledger_stamp(async_session_factory, batch["batch_id"])
    summary = await run_batch_merged_isotopologues(
        batch["batch_id"], batch_peak_run_id="run-strip"
    )
    anchors = await _anchors(async_session_factory, batch)
    assert await _ledger_stamp(async_session_factory, batch["batch_id"]) > stamped

    # The two lines of the first parent leave the ledger as rows of their own.
    for key, label in (("x", "13C"), ("o", "18O")):
        line = anchors[key]
        assert line.isotopologue_of == ids["p"]
        assert line.consensus_formula == "C10H16O4"
        assert line.ionization_mechanism_id == MECH
        assert line.consensus_tier == "assigned"
        record = _stripped(line)
        assert record["batch_peak_id"] == ids["p"]
        assert record["label"] == label
        assert record["spectra"] == N_SAMPLES
        assert record["displaced"]["formula"] == _peaks()[key][1]
        assert record["members_rewritten"] == N_SAMPLES
        assert record["rows_rewritten"] == 1
        assert record["rows_kept"] == {"imported": 1, "no_row": N_SAMPLES - 2}
        assert set(record["displaced_members"]) == set(samples.values())
        assert record["batch_peak_run_id"] == "run-strip"
        # Each member it rewrote carries the isotope lines' mark.
        members = await _members(async_session_factory, ids[key])
        assert {m.batch_written["stage"] for m in members.values()} == {"isotope_lines"}
    assert anchors["x"].consensus_ion_formula == "[13C]C9H15O4-"
    # The parent keeps both lines, and reads what it read.
    assert anchors["p"].consensus_formula == "C10H16O4"
    assert anchors["p"].isotopologue_of is None
    parent = _lines(anchors["p"])
    assert parent["formula"] == "C10H16O4"
    assert {entry["batch_peak_id"] for entry in parent["lines"]} == {
        ids["x"],
        ids["o"],
    }
    # Off its share, and pinned by hand: kept as they were.
    for key in ("z", "w"):
        assert anchors[key].isotopologue_of is None
        assert anchors[key].consensus_formula == _peaks()[key][1]
        assert _stripped(anchors[key]) is None
    assert _lines(anchors["p2"]) is None and _lines(anchors["p3"]) is None

    # Every member of the line is the parent's isotopologue in its sample,
    # measured by the parent's fit at the parent's tier.
    for member in (await _members(async_session_factory, ids["x"])).values():
        assert member.role == role_code("iso_child")
        assert member.owner_batch_peak_id == ids["p"]
        assert member.tier == tier_code("assigned")
        assert member.fit_score == pytest.approx(0.9)
        assert member.p_correct is None
    # The in-app run's rows agree, recorded on both.
    rows = await _run_rows(async_session_factory, batch["runs"][RUN_SAMPLE])
    line_row, parent_row = rows["x"], rows["p"]
    assert line_row.role == "iso_child"
    assert line_row.owner_peak_assignment_id == parent_row.peak_assignment_id
    assert line_row.assigned_formula == "C10H16O4"
    assert (line_row.tier, line_row.fit_score) == ("assigned", pytest.approx(0.9))
    assert (line_row.isotope_label, line_row.ion_formula) == ("13C", "[13C]C9H15O4-")
    assert line_row.provenance["batch_isotopologue"]["batch_peak_id"] == ids["x"]
    # Its tier is its parent's, and it says so; no rule capped the parent.
    assert line_row.provenance["tier_reasons"] == [
        {"rule": "inherited_from_owner", "detail": ANY, "caps": False}
    ]
    assert line_row.alternatives[0]["assigned_formula"] == "C9H12N2O2"
    assert line_row.alternatives[0]["displaced_by_claim"] is True
    assert {
        entry["sample_peak_id"]
        for entry in parent_row.provenance["batch_isotopologues"]
    } == {"x", "o"}
    assert rows["z"].role == "M0" and rows["w"].role == "M0"
    # The imported run is another engine's record, and is not rewritten.
    imported = await _run_rows(async_session_factory, batch["runs"][IMPORT_SAMPLE])
    assert imported["x"].role == "M0"
    assert imported["x"].assigned_formula == "C9H12N2O2"
    assert "batch_isotopologues" not in (imported["p"].provenance or {})

    assert summary["merged_rows"] == 7
    assert (summary["stripped"], summary["off_ratio"], summary["held"]) == (2, 1, 1)
    assert summary["held_by_reason"] == {"curated": 1}
    assert summary["members_rewritten"] == 2 * N_SAMPLES
    assert summary["rows_rewritten"] == 2
    assert {pair["batch_peak_id"] for pair in summary["stripped_pairs"]} == {
        ids["x"],
        ids["o"],
    }
    assert [pair["batch_peak_id"] for pair in summary["held_pairs"]] == [ids["w"]]


async def test_a_line_of_a_capped_parent_says_its_tier_is_capped(
    async_session_factory, pa_test_data
):
    """A parent the absent-line rule capped caps its line's reason too: the
    line reads the parent's tier, and its reason says the parent was capped
    (MAS-1325)."""
    capped = {"rule": "absent_line", "detail": "no 13C line", "caps": True}
    batch = await _seed(
        async_session_factory,
        pa_test_data,
        overrides={
            RUN_SAMPLE: {
                "p": {
                    "provenance": {
                        "evidence": 0.8,
                        "plausibility": 0.9,
                        "tier_reasons": [capped],
                    }
                }
            }
        },
    )
    await run_batch_merged_isotopologues(batch["batch_id"], batch_peak_run_id="run")
    rows = await _run_rows(async_session_factory, batch["runs"][RUN_SAMPLE])
    for peak in ("x", "o"):
        assert rows[peak].role == "iso_child"
        assert rows[peak].provenance["tier_reasons"] == [
            {"rule": "inherited_from_owner", "detail": ANY, "caps": True}
        ]
    # The parent's own reasons are left as they were.
    assert rows["p"].provenance["tier_reasons"] == [capped]


async def test_the_samples_view_shows_the_line_as_the_parents_isotopologue(
    async_session_factory, strip_batch
):
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await run_batch_merged_isotopologues(batch["batch_id"])
    anchors = await _anchors(async_session_factory, batch)
    member = (await _members(async_session_factory, ids["x"]))[samples["S04"]]
    row = member_row(member, anchors["x"])
    assert row["role"] == "iso_child"
    assert row["owner_peak_assignment_id"] == fold_assignment_id(ids["p"])
    assert row["assigned_formula"] == "C10H16O4"
    assert row["ion_formula"] == "[13C]C9H15O4-"


async def test_the_consensus_and_a_refold_keep_the_strip(
    async_session_factory, strip_batch
):
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await run_batch_merged_isotopologues(batch["batch_id"])
    stripped = _stripped((await _anchors(async_session_factory, batch))["x"])

    await recompute_batch_consensus(batch["batch_id"], set(ids.values()))
    # The in-app sample folds again from its rewritten rows.
    assert await fold_sample_into_batch_peaks(samples[RUN_SAMPLE]) is not None
    anchors = await _anchors(async_session_factory, batch)
    assert anchors["x"].isotopologue_of == ids["p"]
    assert _stripped(anchors["x"]) == stripped
    assert {entry["batch_peak_id"] for entry in _lines(anchors["p"])["lines"]} == {
        ids["x"],
        ids["o"],
    }
    member = (await _members(async_session_factory, ids["x"]))[samples[RUN_SAMPLE]]
    assert member.role == role_code("iso_child")
    assert member.owner_batch_peak_id == ids["p"]


async def test_a_second_reading_strips_nothing_twice(
    async_session_factory, strip_batch
):
    batch = strip_batch
    await run_batch_merged_isotopologues(batch["batch_id"])
    again = await run_batch_merged_isotopologues(batch["batch_id"])
    assert again["stripped"] == 0
    assert again["members_rewritten"] == 0
    anchors = await _anchors(async_session_factory, batch)
    assert len(_lines(anchors["p"])["lines"]) == 2


async def test_a_row_a_sample_of_which_is_a_hand_assignment_is_held(
    async_session_factory, pa_test_data
):
    # On the imported sample, folded second: the identity was already in the
    # registry, brought by the in-app sample, so only its row says a person
    # made it.
    batch = await _seed(
        async_session_factory,
        pa_test_data,
        overrides={IMPORT_SAMPLE: {"x": {"source": "manual"}}},
    )
    # Held by the reading itself, before anything is written.
    planned = await _planned(batch["batch_id"])
    assert {p.row.batch_peak_id: p.reason for p in planned.held} == {
        batch["ids"]["x"]: "hand_assigned",
        batch["ids"]["w"]: "curated",
    }
    summary = await run_batch_merged_isotopologues(batch["batch_id"])
    anchors = await _anchors(async_session_factory, batch)
    assert anchors["x"].isotopologue_of is None
    assert anchors["x"].consensus_formula == "C9H12N2O2"
    assert _stripped(anchors["x"]) is None
    assert summary["held_by_reason"] == {"curated": 1, "hand_assigned": 1}
    rows = await _run_rows(async_session_factory, batch["runs"][RUN_SAMPLE])
    assert rows["x"].role == "M0"
    # The other line of the same parent is stripped all the same.
    assert anchors["o"].isotopologue_of == batch["ids"]["p"]


async def test_the_ledger_and_an_earlier_run_serve_the_records(
    guest_client, async_session_factory, strip_batch
):
    batch, ids = strip_batch, strip_batch["ids"]
    run_id = await start_run(batch["batch_id"], ACTION_STRIP_ISOTOPOLOGUES)
    await run_batch_merged_isotopologues(batch["batch_id"], batch_peak_run_id=run_id)
    await complete_run(batch["batch_id"], run_id)

    async def ledger(**params):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch['batch_id']}",
            params={"min_n_present": 1, **params},
        )
        assert response.status_code == 200, response.text
        return {row["batch_peak_id"]: row for row in response.json()["data"]}

    live = await ledger()
    served = live[ids["x"]]["merged_isotopologue_of"]
    assert served["batch_peak_id"] == ids["p"]
    assert served["label"] == "13C"
    assert served["batch_peak_run_id"] == run_id
    # The members' earlier states are the run snapshot's to keep.
    assert "displaced_members" not in served
    assert live[ids["x"]]["isotopologue_of"] == ids["p"]
    assert live[ids["p"]]["merged_isotopologues"]["n_lines"] == 2
    assert live[ids["z"]]["merged_isotopologue_of"] is None

    later = await start_run(batch["batch_id"], ACTION_STRIP_ISOTOPOLOGUES)
    await complete_run(batch["batch_id"], later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier[ids["x"]]["merged_isotopologue_of"] == served
    assert (
        earlier[ids["p"]]["merged_isotopologues"]
        == live[ids["p"]]["merged_isotopologues"]
    )


async def test_the_route_runs_it_as_a_batch_run(
    editor_client,
    guest_client,
    async_session_factory,
    strip_batch,
    monkeypatch,
    only_its_own_stage,
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = strip_batch["batch_id"]
    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/strip-isotopologues"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/strip-isotopologues"
    )
    assert accepted.status_code == 202, accepted.text
    assert "please wait" in accepted.json()["message"]
    async with async_session_factory() as session:
        runs = (
            (
                await session.execute(
                    select(BatchPeakRun).where(BatchPeakRun.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
    [run] = [run for run in runs if run.action == ACTION_STRIP_ISOTOPOLOGUES]
    assert run.status == "completed"
    # The strip is the first step of the isotope lines' stage, the stamping
    # the second, both inside the pass's one run, which is the current one.
    assert run.is_current == 1
    assert not [r for r in runs if r.action == ACTION_ISOTOPOLOGUES]
    strip = step_outcome(run, "isotope_lines", 0)
    assert step_outcome(run, "isotope_lines", 1)["data"] is not None
    assert strip["config"]["median_low"] == 0.4
    assert strip["config"]["isotopes"][0] == "13C"
    assert strip["data"]["stripped"] == 2
    anchors = await _anchors(async_session_factory, strip_batch)
    assert _stripped(anchors["x"])["batch_peak_run_id"] == run.batch_peak_run_id


async def test_the_route_refuses_while_another_run_rewrites_the_ledger(
    editor_client, strip_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    await start_run(strip_batch["batch_id"], ACTION_STRIP_ISOTOPOLOGUES)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{strip_batch['batch_id']}/strip-isotopologues"
    )
    assert response.status_code == 409, response.text
    assert launched == []


async def _planned(batch_id):
    anchors, members, hand = await batch_merged_isotopologues._load_batch(batch_id)
    return batch_merged_isotopologues.plan_batch(anchors, members, hand)


async def test_a_row_whose_reading_moved_since_it_was_read_is_left_out(
    async_session_factory, strip_batch
):
    batch, ids = strip_batch, strip_batch["ids"]
    plan = await _planned(batch["batch_id"])
    assert {p.row.batch_peak_id for p in plan.stripped} == {ids["x"], ids["o"]}
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == ids["x"])
            .values(consensus_formula="C8H8N4O")
        )
        await session.commit()
    written = await batch_merged_isotopologues._write_chunk(
        batch["batch_id"], plan.stripped, None
    )
    assert {w.pair.row.batch_peak_id: w.outcome for w in written} == {
        ids["x"]: "moved",
        ids["o"]: "stripped",
    }
    for member in (await _members(async_session_factory, ids["x"])).values():
        assert member.role == role_code("M0")


async def test_a_hand_assignment_made_since_the_reading_holds_its_row(
    async_session_factory, strip_batch
):
    batch, ids = strip_batch, strip_batch["ids"]
    plan = await _planned(batch["batch_id"])
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.peak_assignment_run_id == batch["runs"][RUN_SAMPLE],
                PeakAssignment.sample_peak_id == "x",
            )
            .values(source="manual")
        )
        await session.commit()
    written = await batch_merged_isotopologues._write_chunk(
        batch["batch_id"], plan.stripped, None
    )
    assert {w.pair.row.batch_peak_id: w.outcome for w in written} == {
        ids["x"]: "hand_assigned",
        ids["o"]: "stripped",
    }
    rows = await _run_rows(async_session_factory, batch["runs"][RUN_SAMPLE])
    assert rows["x"].role == "M0"


async def test_a_row_its_samples_read_again_as_a_compound_is_read_again(
    async_session_factory, strip_batch
):
    """Folded again from rows that read the lines as compounds of their own -
    every sample but the in-app one, whose rows the strip rewrote - the rows
    are compounds of the ledger again and their records come off, and so does
    their parent's, which no row folds under any more; the next reading strips
    them again, and their parent lists each once."""
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await run_batch_merged_isotopologues(batch["batch_id"])
    for i in range(N_SAMPLES):
        name = f"S{i:02d}"
        if name == RUN_SAMPLE:
            continue
        if name == IMPORT_SAMPLE:
            assert await fold_sample_into_batch_peaks(samples[name]) is not None
            continue
        assert (
            await fold_sample_into_batch_peaks(
                samples[name],
                rows=[SimpleNamespace(**r) for r in _rows(samples[name], i)],
                persisted=False,
            )
            is not None
        )
    anchors = await _anchors(async_session_factory, batch)
    for key in ("x", "o"):
        assert anchors[key].isotopologue_of is None
        assert _stripped(anchors[key]) is None
    # The parent's mark and the "Stripped rows" filter read this record.
    assert "merged_isotopologues" not in anchors["p"].provenance

    again = await run_batch_merged_isotopologues(batch["batch_id"])
    assert again["stripped"] == 2
    anchors = await _anchors(async_session_factory, batch)
    assert anchors["x"].isotopologue_of == ids["p"]
    lines = _lines(anchors["p"])["lines"]
    assert sorted(entry["batch_peak_id"] for entry in lines) == sorted(
        [ids["x"], ids["o"]]
    )


async def test_a_row_released_from_its_parent_leaves_the_parents_lines(
    async_session_factory, strip_batch
):
    """The samples folded without a run are folded again, their runs reading
    the 13C row as a compound of its own and the 18O row as the parent's
    line: the 13C row is released and comes off the parent's list, though
    the parent's own reading stands, and the 18O row stays on it."""
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await run_batch_merged_isotopologues(batch["batch_id"])
    for i in range(N_SAMPLES):
        name = f"S{i:02d}"
        if name in (RUN_SAMPLE, IMPORT_SAMPLE):
            continue
        rows = {row["sample_peak_id"]: row for row in _rows(samples[name], i)}
        rows["o"].update(
            role="iso_child",
            assigned_formula="C10H16O4",
            isotope_label="18O",
            owner_peak_assignment_id=rows["p"]["peak_assignment_id"],
        )
        assert (
            await fold_sample_into_batch_peaks(
                samples[name],
                rows=[SimpleNamespace(**r) for r in rows.values()],
                persisted=False,
            )
            is not None
        )
    anchors = await _anchors(async_session_factory, batch)
    assert anchors["x"].isotopologue_of is None
    assert _stripped(anchors["x"]) is None
    assert anchors["o"].isotopologue_of == ids["p"]
    assert _stripped(anchors["o"])["batch_peak_id"] == ids["p"]
    parent = _lines(anchors["p"])
    assert [entry["batch_peak_id"] for entry in parent["lines"]] == [ids["o"]]
    assert parent["n_lines"] == 1
    assert parent["formula"] == "C10H16O4"


async def test_a_parent_moving_within_its_ion_keeps_its_lines_renamed(
    async_session_factory, strip_batch
):
    """MAS-1144: the samples folded without a run are folded again reading the
    parent as C9H14O2 through formate, the ion C10H16O4 deprotonated is, and
    both stripped rows as its lines. The parent's list and both rows' records
    stand, naming the reading the parent holds now."""
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await run_batch_merged_isotopologues(batch["batch_id"])
    before = await _anchors(async_session_factory, batch)
    for i in range(N_SAMPLES):
        name = f"S{i:02d}"
        if name in (RUN_SAMPLE, IMPORT_SAMPLE):
            continue
        rows = {row["sample_peak_id"]: row for row in _rows(samples[name], i)}
        rows["p"].update(assigned_formula="C9H14O2", ionization_mechanism_id=MECH_FA)
        for line, label in (("x", "13C"), ("o", "18O")):
            rows[line].update(
                role="iso_child",
                assigned_formula="C9H14O2",
                ionization_mechanism_id=MECH_FA,
                isotope_label=label,
                owner_peak_assignment_id=rows["p"]["peak_assignment_id"],
            )
        assert (
            await fold_sample_into_batch_peaks(
                samples[name],
                rows=[SimpleNamespace(**r) for r in rows.values()],
                persisted=False,
            )
            is not None
        )
    anchors = await _anchors(async_session_factory, batch)
    assert (anchors["p"].consensus_formula, anchors["p"].ionization_mechanism_id) == (
        "C9H14O2",
        MECH_FA,
    )
    named = {"formula": "C9H14O2", "ionization_mechanism_id": MECH_FA}
    assert _lines(anchors["p"]) == {**_lines(before["p"]), **named}
    for line in ("x", "o"):
        assert anchors[line].isotopologue_of == ids["p"]
        assert _stripped(anchors[line]) == {**_stripped(before[line]), **named}


async def _drop(session_factory, batch_peak_id):
    """Take every member off a batch peak, as a re-fold that leaves it does."""
    async with session_factory() as session:
        await session.execute(
            delete(BatchPeakOccurrence).where(
                BatchPeakOccurrence.batch_peak_id == batch_peak_id
            )
        )
        await session.commit()


async def test_a_row_that_goes_leaves_the_parents_lines(
    async_session_factory, strip_batch
):
    """Only the row is recomputed, and it goes: the parent, which the
    recompute does not reach, drops it from its list all the same."""
    batch, ids = strip_batch, strip_batch["ids"]
    await run_batch_merged_isotopologues(batch["batch_id"])
    before = (await _anchors(async_session_factory, batch))["p"]
    await _drop(async_session_factory, ids["o"])
    await recompute_batch_consensus(batch["batch_id"], {ids["o"]})
    async with async_session_factory() as session:
        assert await session.get(BatchPeak, ids["o"]) is None
        parent = await session.get(BatchPeak, ids["p"])
    assert [entry["batch_peak_id"] for entry in _lines(parent)["lines"]] == [ids["x"]]
    assert _lines(parent)["n_lines"] == 1
    assert parent.batch_peak_utc_modified > before.batch_peak_utc_modified

    # The other goes too: the parent, which takes no line any more, carries
    # no record at all.
    await _drop(async_session_factory, ids["x"])
    await recompute_batch_consensus(batch["batch_id"], {ids["x"]})
    async with async_session_factory() as session:
        parent = await session.get(BatchPeak, ids["p"])
    assert "merged_isotopologues" not in parent.provenance


@pytest.mark.parametrize(
    "listed",
    [
        pytest.param(
            lambda record, gone: {
                **record,
                "lines": [e for e in record["lines"] if e["batch_peak_id"] != gone],
                "n_lines": len(record["lines"]) - 1,
            },
            id="lists-other-rows",
        ),
        pytest.param(lambda record, gone: None, id="no-record"),
    ],
)
async def test_a_parent_that_no_longer_lists_a_row_is_left_as_it_is(
    async_session_factory, strip_batch, listed
):
    """A row goes whose parent lists it no more, or lists nothing: the
    parent is left as it is."""
    batch, ids = strip_batch, strip_batch["ids"]
    await run_batch_merged_isotopologues(batch["batch_id"])
    async with async_session_factory() as session:
        parent = await session.get(BatchPeak, ids["p"])
        provenance = dict(parent.provenance)
        record = listed(provenance.pop("merged_isotopologues"), ids["o"])
        if record is not None:
            provenance["merged_isotopologues"] = record
        parent.provenance = provenance
        await session.commit()
    before = (await _anchors(async_session_factory, batch))["p"]
    await _drop(async_session_factory, ids["o"])
    await recompute_batch_consensus(batch["batch_id"], {ids["o"]})
    async with async_session_factory() as session:
        assert await session.get(BatchPeak, ids["o"]) is None
        after = await session.get(BatchPeak, ids["p"])
    assert after.batch_peak_utc_modified == before.batch_peak_utc_modified
    assert after.provenance == before.provenance


async def test_a_13c_line_folded_since_the_reading_holds_its_row(
    async_session_factory, strip_batch
):
    """Two samples folded again between the reading and the write bring the
    13C row's own 13C line, folded under it: the row carries an envelope of
    its own now, and is held, though its other samples still turn its vote.
    The line is found where its members sit, not where its anchor was minted:
    the first sample minted it 1.6 ppm off the line, outside the window, and
    the two sit 0.7 ppm off it."""
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    plan = await _planned(batch["batch_id"])
    [pair] = [p for p in plan.stripped if p.row.batch_peak_id == ids["x"]]
    assert {p.row.batch_peak_id for p in plan.stripped} == {ids["x"], ids["o"]}
    assert pair.window_ppm == pytest.approx(1.0)
    [c13] = [
        line
        for line in batch_merged_isotopologues.lines_of(pair.row)
        if line.isotopologue.label == "13C"
    ]
    for name, off_ppm in (("S02", 1.6), ("S03", -0.2)):
        index = int(name[1:])
        rows = _rows(samples[name], index)
        [own] = [row for row in rows if row["sample_peak_id"] == "x"]
        rows.append(
            {
                **own,
                "peak_assignment_id": gen_id(32),
                "sample_peak_id": "x13",
                "sample_peak_mz": c13.mz * (1 + off_ppm / 1e6),
                "sample_peak_intensity": own["sample_peak_intensity"]
                * c13.isotopologue.ratio,
                "role": "iso_child",
                "ion_formula": "[13C]C8H11N2O2-",
                "isotope_label": "13C",
                "owner_peak_assignment_id": own["peak_assignment_id"],
            }
        )
        assert (
            await fold_sample_into_batch_peaks(
                samples[name],
                rows=[SimpleNamespace(**r) for r in rows],
                persisted=False,
            )
            is not None
        )
    async with async_session_factory() as session:
        [line] = (
            (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.isotopologue_of == ids["x"])
                )
            )
            .scalars()
            .all()
        )
    assert abs(line.mz - c13.mz) / c13.mz * 1e6 > pair.window_ppm
    assert len(await _members(async_session_factory, line.batch_peak_id)) == 2

    written = await batch_merged_isotopologues._write_chunk(
        batch["batch_id"], plan.stripped, None
    )
    assert {w.pair.row.batch_peak_id: w.outcome for w in written} == {
        ids["x"]: "own_13c",
        ids["o"]: "stripped",
    }
    anchors = await _anchors(async_session_factory, batch)
    assert anchors["x"].isotopologue_of is None
    assert _stripped(anchors["x"]) is None
    for member in (await _members(async_session_factory, ids["x"])).values():
        assert member.role == role_code("M0")
    rows = await _run_rows(async_session_factory, batch["runs"][RUN_SAMPLE])
    assert rows["x"].role == "M0"


def _stub_the_stage_list(monkeypatch):
    """What a sample's judging reads after the strip, canned: every peak of
    the fixture, the suite's mechanism, and a seeded scorer that pairs each
    parent's reading to its peak."""
    peaks = _peaks()
    by_formula = {formula: peak for peak, (_, formula, _, _) in peaks.items()}
    ions = {formula: ion for _, (_, formula, ion, _) in peaks.items()}

    def peaks_of(sample_item_id):
        return frame((peak, mz, 1e6) for peak, (mz, *_) in peaks.items())

    async def seeded(sample, seeds, params, **kwargs):
        ion_by_seed, fit_by_ion, errors = {}, {}, {}
        for formula, mechanism in seeds:
            peak = by_formula.get(formula)
            if peak is None:
                continue
            ion = f"ion-{formula}"
            ion_by_seed[(formula, mechanism)] = ion
            fit_by_ion[ion] = 0.9
            errors[(ion, peak)] = {"mz_error_ppm": 0.1}
        return ion_by_seed, fit_by_ion, errors, None

    stub_sample_reads(monkeypatch, peaks_of, [mechanism_row(NOTATION, MECH)])
    stub_seeded_scorer(
        monkeypatch, seeded, peaks_of, ion_formula=lambda formula, _m: ions[formula]
    )


async def test_a_refolded_sample_is_stripped_again_by_the_next_pass(
    async_session_factory, strip_batch, monkeypatch, only_these_stages
):
    """A sample folded in again reads its own rows, the strip's rewrite of its
    line gone: every stage is stale, and the next pass strips the line in it
    again (MAS-983's Verify)."""
    from mascope_backend.api.new.peak_assignments.batch_stages import (
        assign_batch,
        stage_standing,
    )

    only_these_stages("isotope_lines")
    _stub_the_stage_list(monkeypatch)
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await assign_batch(sample_batch_id=batch["batch_id"])
    sample = samples["S03"]
    line = (await _members(async_session_factory, ids["x"]))[sample]
    assert line.role == role_code("iso_child")
    assert line.batch_written["stage"] == "isotope_lines"

    await fold_sample_into_batch_peaks(
        sample, rows=[SimpleNamespace(**r) for r in _rows(sample, 3)], persisted=False
    )
    refolded = (await _members(async_session_factory, ids["x"]))[sample]
    assert refolded.role == role_code("M0")
    assert refolded.batch_written is None
    standing = await stage_standing(batch["batch_id"])
    assert {e["stale_reason"] for e in standing["stages"]} == {"fold"}

    outcome = await assign_batch(sample_batch_id=batch["batch_id"])
    assert outcome["status"] == "success", outcome["message"]
    restored = (await _members(async_session_factory, ids["x"]))[sample]
    assert restored.role == role_code("iso_child")
    assert restored.owner_batch_peak_id == ids["p"]
    assert restored.batch_written["stage"] == "isotope_lines"
    assert (await stage_standing(batch["batch_id"]))["stale_stages"] == []
    # The record keeps what the row read before the first strip and every
    # member either strip rewrote, which a withdrawal would put back.
    record = _stripped((await _anchors(async_session_factory, batch))["x"])
    assert record["displaced"]["formula"] == _peaks()["x"][1]
    assert set(record["displaced_members"]) == set(samples.values())
    assert record["members_rewritten"] == N_SAMPLES
    [stage] = [s for s in outcome["data"]["stages"] if s["stage"] == "isotope_lines"]
    # Both of the first parent's lines, the 13C and the 18O row.
    assert stage["outcomes"][0]["data"]["stripped_again"] == 2
    assert stage["outcomes"][0]["data"]["stripped"] == 0


# --- a sample's run reads the strip (MAS-985) --------------------------------------


async def _strip_through_the_stages(batch, monkeypatch, only_these_stages):
    """The strip run as its batch stage, its records stamped with the stage's
    version; that version."""
    from mascope_backend.api.new.peak_assignments.batch_stages import (
        assign_batch,
        stage_standing,
    )

    only_these_stages("isotope_lines")
    _stub_the_stage_list(monkeypatch)
    await assign_batch(sample_batch_id=batch["batch_id"])
    [stage] = [
        e
        for e in (await stage_standing(batch["batch_id"]))["stages"]
        if e["stage"] == "isotope_lines"
    ]
    return stage["version"]


async def test_a_samples_next_run_reads_the_stripped_row_as_the_parents_line(
    async_session_factory, strip_batch, monkeypatch, only_these_stages
):
    """A sample assigned again reads its peaks afresh, the analyte reading the
    strip displaced among them; its run reads the batch's strip and judges the
    row as its parent's isotope line, so the ledger it folds agrees with the
    batch's (MAS-868's and MAS-985's Verify). The rows the strip held are read
    as the run reads them."""
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    version = await _strip_through_the_stages(batch, monkeypatch, only_these_stages)
    stripped = _stripped((await _anchors(async_session_factory, batch))["x"])
    assert stripped["mz_offset"] == pytest.approx(C13_P, abs=1e-6)
    sample = samples[RUN_SAMPLE]
    stub_known_set(
        monkeypatch,
        lambda sample_item_id, run_id: _rows(sample_item_id, 0, run_id=run_id),
    )

    ctx = await run_sample(sample)

    rows = await _run_rows(async_session_factory, ctx.run_id)
    parent = rows["p"]
    for peak, label in (("x", "13C"), ("o", "18O")):
        line = rows[peak]
        assert line.role == "iso_child"
        assert line.owner_peak_assignment_id == parent.peak_assignment_id
        assert line.assigned_formula == "C10H16O4"
        assert line.isotope_label == label
        record = line.provenance["batch_isotopologue"]
        # Read at the parent's own peak and the line's offset the strip kept.
        offset = {"x": C13_P, "o": O18_P}[peak]
        assert record["predicted_mz"] == pytest.approx(
            parent.sample_peak_mz + offset, abs=1e-5
        )
        assert record["batch_peak_id"] == ids[peak]
        assert record["parent_batch_peak_id"] == ids["p"]
        assert record["version"] == version
        assert record["displaced"]["assigned_formula"] == _peaks()[peak][1]
        assert line.alternatives[0]["assigned_formula"] == _peaks()[peak][1]
        # The run's judging gives the line its parent's tier, and says so.
        reasons = line.provenance["tier_reasons"]
        assert [r["rule"] for r in reasons][-1] == "inherited_from_owner"
    assert {e["sample_peak_id"] for e in parent.provenance["batch_isotopologues"]} == {
        "x",
        "o",
    }
    # The rows the strip held are the run's own to read: no other row reads
    # the batch's strip.
    assert {
        peak
        for peak, row in rows.items()
        if "batch_isotopologue" in (row.provenance or {})
    } == {"x", "o"}
    assert rows["z"].role == "M0"
    # The ledger the run folds reads the line as the batch does.
    member = (await _members(async_session_factory, ids["x"]))[sample]
    assert member.role == role_code("iso_child")
    assert member.owner_batch_peak_id == ids["p"]
    # And the run records the version of the strip it read.
    async with async_session_factory() as session:
        config = (await session.get(PeakAssignmentRun, ctx.run_id)).config
    read = config["batch_decisions"]["stages"]["isotope_lines"]
    assert (read["version"], read["records"]) == (version, 2)
    assert read["record_versions"] == [version]
    assert config["batch_decisions"]["applied"]["strips"] == {"read": 2}


@pytest.mark.parametrize(
    "redetected, applied",
    [("x", {"read": 1, "peak_absent": 1}), ("p", {"peak_absent": 2})],
    ids=["the stripped peak", "the parent's peak"],
)
async def test_a_samples_next_run_reads_on_past_a_peak_it_no_longer_has(
    async_session_factory,
    strip_batch,
    monkeypatch,
    only_these_stages,
    redetected,
    applied,
):
    """A re-detection mints the sample new peak ids, and its members name the
    old ones until its next fold: the run that brings it up to date leaves a
    strip whose peak, or whose parent's, it no longer has, and records it as
    such."""
    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await _strip_through_the_stages(batch, monkeypatch, only_these_stages)
    sample = samples[RUN_SAMPLE]
    stub_known_set(
        monkeypatch,
        lambda sample_item_id, run_id: _rows(sample_item_id, 0, run_id=run_id),
    )
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeakOccurrence)
            .where(
                BatchPeakOccurrence.batch_peak_id == ids[redetected],
                BatchPeakOccurrence.sample_item_id == sample,
            )
            .values(sample_peak_id=f"redetected-{redetected}")
        )
        await session.commit()

    ctx = await run_sample(sample)

    assert ctx.batch_applied["strips"] == applied
    rows = await _run_rows(async_session_factory, ctx.run_id)
    # No row reads a strip the run left; the other strip, on peaks the run
    # holds, is read as the parent's line.
    assert {
        peak
        for peak, row in rows.items()
        if "batch_isotopologue" in (row.provenance or {})
    } == ({"o"} if redetected == "x" else set())


async def test_a_decoy_arm_reads_no_strip(
    async_session_factory, strip_batch, monkeypatch, only_these_stages
):
    """A decoy arm claims what its control claims and reads none of the
    batch's judging: its own lines decide whether a row is a line
    (``pipeline.BATCH_INPUTS``)."""
    from mascope_backend.api.controllers.samples.lib.samples_fetch import (
        fetch_sample,
    )
    from mascope_backend.api.new.peak_assignments import pipeline
    from mascope_backend.api.new.peak_assignments.batch_decisions import (
        read_batch_decisions,
    )

    batch, samples, ids = strip_batch, strip_batch["samples"], strip_batch["ids"]
    await _strip_through_the_stages(batch, monkeypatch, only_these_stages)
    # A record of a decision a decoy does read on the same batch peak - here a
    # known-species decision the strip left behind, marked stale - reads no
    # strip into the arm with it.
    async with async_session_factory() as session:
        row = await session.get(BatchPeak, ids["x"])
        row.provenance = {
            **row.provenance,
            "known_species": {"verdict": "present", "reads": {}, "stale": True},
        }
        await session.commit()
    sample = await fetch_sample(samples["S03"])

    run = await read_batch_decisions(
        sample, profile=None, kinds=pipeline.batch_kinds(pipeline.RUN, decoy=False)
    )
    decoy = await read_batch_decisions(
        sample, profile=None, kinds=pipeline.batch_kinds(pipeline.DECOY, decoy=True)
    )

    assert set(run.strips) == {"x", "o"}
    assert {strip.parent_peak_id for strip in run.strips.values()} == {"p"}
    assert decoy.strips == {}
    assert "isotope_lines" not in decoy.record()["stages"]
