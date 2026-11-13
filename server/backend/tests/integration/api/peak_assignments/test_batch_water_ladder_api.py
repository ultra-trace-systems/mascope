"""Integration tests for the batch's water ladders (step 4.2j).

Seeds a TOF batch of a bromide source: 24 samples folded into the ledger,
twelve dry and twelve humid three hours later. Three of them come from in-app
runs of their own (D00, H00, H01), one from an imported run (H02), the rest
from rows no run holds. Every sample shows Br- and its first two hydrates as
its pre-pass claimed them, and Br-.(H2O)3 read as an organic with its 81Br
line; the humid samples alone show the fourth and fifth hydrates, unread,
but for the fifth's 81Br line, read as an organic of its own (by hand in
H01). The dry samples show an organic at the fifth hydrate's mass, where
their ladder, ending at three, does not reach. The ladder's rules are the
library's (``test_water_ladder.py``) and the plan the pure tests'
(``unit/.../test_batch_water_ladder.py``); what is proven here is the
batch-level contract: the rungs claimed per segment, the members and the
in-app runs' rows rewritten and the import's left, a rung's line going with
it, a heavier line owned by its rung, the holds recorded, the consensus
keeping the record through a recompute, a new measurement replacing every
record, the report's unexplained share, and the route running it as a batch
run. See ``batch_water_ladder.py``.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from mascope_backend.api.new.peak_assignments import (
    batch_peaks_controller,
    batch_peaks_routes,
    batch_water_ladder,
)
from mascope_backend.api.new.peak_assignments.batch_known_species import _row_marks
from mascope_backend.api.new.peak_assignments.batch_peaks import role_code
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_WATER_LADDERS,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_water_ladder import (
    run_batch_water_ladders,
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
from mascope_tools.composition.reagents import water_ladders


pytestmark = pytest.mark.asyncio

#: A mechanism no other suite names: the mechanism table's notations are
#: unique, and the integration database is shared by the whole run.
MECH, NOTATION = "mech-ladder-br", "[M+Br]- (water ladder test)"

BROMIDE = water_ladders("BR")[0]
M0, BR81 = BROMIDE.tracks
DRY = [f"D{i:02d}" for i in range(12)]
HUMID = [f"H{i:02d}" for i in range(12)]
RUN_SAMPLES, IMPORT_SAMPLE = ("D00", "H00", "H01"), "H02"
#: The window the TOF's batch peaks are read at once folded: half a 160 ppm
#: line and the fold's drift margin.
TOF_TOL = 82.0
F3, F5, F5B = "C2H8O2", "C4H4O", "C5H2"


def ion(waters):
    return BROMIDE.rung(waters).formula + "-"


#: Peak id -> its m/z.
MZ = {
    "core": BROMIDE.line_mz(0, M0),
    "core81": BROMIDE.line_mz(0, BR81),
    **{f"r{n}": BROMIDE.line_mz(n, M0) for n in range(1, 6)},
    **{f"r{n}b": BROMIDE.line_mz(n, BR81) for n in range(1, 6)},
}


def _reagent(ion_formula, label, owner=None, isotope_label=None):
    return dict(
        role="reagent",
        assigned_formula=None,
        ion_formula=ion_formula,
        ionization_mechanism_id=None,
        source="reagent",
        tier="unassigned",
        fit_score=None,
        isotope_label=isotope_label,
        owner=owner,
        provenance={"reagent": {"ion": label, "kind": "cluster", "family": "reagent"}},
    )


def _analyte(formula, *, source="untargeted", **extra):
    return {
        "role": "M0",
        "assigned_formula": formula,
        "ion_formula": formula + "Br-",
        "ionization_mechanism_id": MECH,
        "source": source,
        "tier": "assigned",
        "fit_score": 0.8,
        "provenance": {"evidence": 0.8},
        **extra,
    }


UNREAD = dict(
    role="unassigned",
    assigned_formula=None,
    ion_formula=None,
    ionization_mechanism_id=None,
    source=None,
    tier="unassigned",
    fit_score=None,
    provenance=None,
)


def _readings(name):
    """Peak id -> what the sample's run read it as; a peak absent is not in
    the sample."""
    humid = name.startswith("H")
    readings = {
        "core": _reagent("Br-", "[Br]-"),
        "core81": _reagent("Br-", "[Br]-", owner="core", isotope_label="81Br"),
        "r1": _reagent(ion(1), "[Br+H2O]-"),
        "r1b": _reagent(ion(1), "[Br+H2O]-", owner="r1", isotope_label="81Br"),
        "r2": _reagent(ion(2), "[Br+2xH2O]-"),
        "r2b": _reagent(ion(2), "[Br+2xH2O]-", owner="r2", isotope_label="81Br"),
        "r3": _analyte(F3),
        "r3b": {
            **_analyte(F3),
            "role": "iso_child",
            "isotope_label": "81Br",
            "owner": "r3",
        },
    }
    if humid:
        readings["r4"] = UNREAD
        readings["r4b"] = UNREAD
        # In one sample the rung's peak is another source ion's claim.
        readings["r5"] = _reagent("NO3-", "[NO3]-") if name == "H03" else UNREAD
        readings["r5b"] = _analyte(
            F5B, source="manual" if name == "H01" else "untargeted"
        )
    else:
        readings["r5"] = _analyte(F5)
    return readings


def _rows(sample_item_id, name, *, run_id=None):
    ids = {peak: gen_id(32) for peak in MZ}
    rows = []
    for i, (peak, reading) in enumerate(_readings(name).items()):
        reading = dict(reading)
        owner = reading.pop("owner", None)
        rows.append(
            {
                "peak_assignment_id": ids[peak],
                "peak_assignment_run_id": run_id or f"fold-{sample_item_id}",
                "sample_item_id": sample_item_id,
                "sample_peak_id": peak,
                "sample_peak_mz": MZ[peak],
                "sample_peak_intensity": 1e6 / (1 + i),
                "mz_error_ppm": 0.1,
                "isotope_label": None,
                "owner_peak_assignment_id": ids[owner] if owner else None,
                **reading,
            }
        )
    return rows


@pytest.fixture
def tof(monkeypatch):
    """Every sample of the batch is a TOF's, and its partition's profile the
    bromide one; which samples the profile was resolved on is kept."""
    monkeypatch.setattr(
        batch_peaks_controller, "get_instrument_type", lambda filename: "tof"
    )
    asked = []

    async def chemistry(sample_item_id, config):
        asked.append(sample_item_id)
        return SimpleNamespace(profile="BR")

    monkeypatch.setattr(batch_water_ladder, "_mode_chemistry", chemistry)
    return asked


async def _seed(async_session_factory, pa_test_data):
    now = datetime.now(timezone.utc)
    start = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
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
                sample_batch_name=f"Water ladders {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, MECH) is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=MECH,
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=NOTATION,
                )
            )
        for i, name in enumerate(DRY + HUMID):
            # Ten minutes apart, three hours between the dry and the humid.
            acquired = start + timedelta(minutes=10 * i)
            if name.startswith("H"):
                acquired += timedelta(hours=3)
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"tof-ladder-{name}-{sample_file_id}.zarr",
                    instrument="tof-test",
                    datetime=acquired.replace(tzinfo=None),
                    datetime_utc=acquired,
                    length=60.0,
                    range=[30.0, 500.0],
                    polarity="-",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Water ladders {name}",
                    sample_item_type="sample",
                    polarity="-",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        await session.flush()
        for name in (*RUN_SAMPLES, IMPORT_SAMPLE):
            run_id = gen_id()
            runs[name] = run_id
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=samples[name],
                    engine="mascope" if name in RUN_SAMPLES else "peaky-test",
                    engine_version="0.1.0-test",
                    status="completed",
                    peak_assignment_run_utc_created=now,
                    peak_assignment_run_utc_completed=now,
                )
            )
            await session.flush()
            for row in _rows(samples[name], name, run_id=run_id):
                session.add(PeakAssignment(**row))
        await session.commit()
    for name in DRY + HUMID:
        if name in runs:
            folded = await fold_sample_into_batch_peaks(samples[name])
        else:
            folded = await fold_sample_into_batch_peaks(
                samples[name],
                rows=[SimpleNamespace(**r) for r in _rows(samples[name], name)],
                persisted=False,
            )
        assert folded is not None
    # The fold minted its windows at its drift margin alone (no resolution
    # function here): a TOF's are its line width's.
    async with async_session_factory() as session:
        for anchor in (
            (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        ):
            anchor.mz_tol_ppm = TOF_TOL
        await session.commit()
    return {
        "batch_id": batch_id,
        "samples": samples,
        "runs": runs,
        "ids": await _ids(async_session_factory, batch_id),
    }


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
    return {peak: by_mz[round(mz, 3)] for peak, mz in MZ.items()}


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
async def ladder_batch(async_session_factory, pa_test_data, tof):
    return await _seed(async_session_factory, pa_test_data)


# The record is read off the stored provenance by the name the pass writes,
# not through the constant: a renamed key orphans every record a server
# already holds.
def _record(anchor):
    return (anchor.provenance or {}).get("water_ladder")


async def test_a_rung_in_every_segment_moves_its_analyte_to_the_reagent(
    async_session_factory, ladder_batch
):
    batch, runs = ladder_batch, ladder_batch["runs"]
    before = await _anchors(async_session_factory, batch)
    assert before["r3"].consensus_formula == F3
    summary = await run_batch_water_ladders(
        batch["batch_id"], batch_peak_run_id="run-1"
    )
    anchors = await _anchors(async_session_factory, batch)

    r3 = anchors["r3"]
    assert r3.consensus_role == "reagent"
    assert r3.consensus_ion_formula == ion(3)
    assert r3.consensus_formula is None
    record = _record(r3)
    assert record["rung"]["ion"] == "[Br+3xH2O]-"
    assert record["passed_in"] == [0, 1]
    assert record["turned"] is True and record["moved_analyte"] is True
    assert record["read_before"]["formula"] == F3
    assert record["members_rewritten"] == 24
    assert record["rows_rewritten"] == 3
    assert record["lines_rewritten"] == 24
    assert record["rows_kept"] == {"imported": 1, "no_row": 20}
    assert record["reads"]["role"] == "reagent"
    assert record["batch_peak_run_id"] == "run-1"
    for member in (await _members(async_session_factory, batch["ids"]["r3"])).values():
        assert member.role == role_code("reagent")
        assert member.fit_score is None

    # The in-app run's rows are the rung now; the line goes with it, owned as
    # it was; the import's rows are another engine's record.
    rows = await _run_rows(async_session_factory, runs["H00"])
    assert rows["r3"].role == "reagent"
    assert rows["r3"].assigned_formula is None
    assert rows["r3"].ion_formula == ion(3)
    reagent = rows["r3"].provenance["reagent"]
    assert reagent["reason"] == "batch_water_ladder"
    assert reagent["batch_water_ladder"]["segment"] == 1
    assert reagent["batch_water_ladder"]["waters"] == 3
    assert reagent["read_as"]["formula"] == F3
    assert rows["r3"].alternatives[0]["assigned_formula"] == F3
    assert rows["r3b"].role == "reagent"
    assert rows["r3b"].isotope_label == "81Br"
    assert rows["r3b"].owner_peak_assignment_id == rows["r3"].peak_assignment_id
    imported = await _run_rows(async_session_factory, runs[IMPORT_SAMPLE])
    assert imported["r3"].role == "M0" and imported["r3"].assigned_formula == F3

    # The 81Br line's batch peak reads the reagent's too, the rung's line.
    r3b = anchors["r3b"]
    assert r3b.consensus_role == "reagent"
    assert r3b.isotopologue_of == batch["ids"]["r3"]

    (part,) = summary["partitions"]
    assert part["profile"] == "BR"
    assert [s["spectra"] for s in part["segments"]] == [12, 12]
    # Two analytes left the ledger (Br-.(H2O)3 and the fifth hydrate's 81Br
    # line); two unread batch peaks of the humid segment read the reagent too.
    assert summary["rows_moved"] == 2
    assert summary["peaks_turned"] == 4
    assert part["unexplained_after"] < part["unexplained_before"]
    assert summary["rungs_passed"] >= 10


async def test_a_rung_is_claimed_only_in_the_segment_it_passed_in(
    async_session_factory, ladder_batch
):
    batch, samples, runs = ladder_batch, ladder_batch["samples"], ladder_batch["runs"]
    await run_batch_water_ladders(batch["batch_id"])
    anchors = await _anchors(async_session_factory, batch)

    record = _record(anchors["r5"])
    assert record["passed_in"] == [1]
    assert [s["segment"] for s in record["segments"]] == [1]
    members = await _members(async_session_factory, batch["ids"]["r5"])
    for name in HUMID:
        assert members[samples[name]].role == role_code("reagent")
    for name in DRY:
        assert members[samples[name]].role == role_code("M0")
    # Twelve claimed, twelve analytes: the vote leaves the batch peak an
    # analyte, and the record says the claim did not turn it.
    assert anchors["r5"].consensus_formula == F5
    assert record["turned"] is False
    rows = await _run_rows(async_session_factory, runs["D00"])
    assert rows["r5"].assigned_formula == F5
    rows = await _run_rows(async_session_factory, runs["H00"])
    assert rows["r5"].role == "reagent"
    # An unread peak at a rung of the humid segment is the reagent's now.
    assert anchors["r4"].consensus_role == "reagent"
    assert _record(anchors["r4"])["moved_analyte"] is False


async def test_a_heavier_line_read_on_its_own_becomes_its_rungs_line(
    async_session_factory, ladder_batch
):
    batch, samples, runs = ladder_batch, ladder_batch["samples"], ladder_batch["runs"]
    await run_batch_water_ladders(batch["batch_id"])
    anchors = await _anchors(async_session_factory, batch)

    record = _record(anchors["r5b"])
    assert record["rung"]["track"] == "81Br"
    assert record["files_by_reason"] == {"hand_assigned": 1}
    assert record["files_recorded"][0]["sample_item_id"] == samples["H01"]
    members = await _members(async_session_factory, batch["ids"]["r5b"])
    assert members[samples["H01"]].role == role_code("M0")
    assert members[samples["H00"]].role == role_code("reagent")
    assert members[samples["H00"]].owner_batch_peak_id == batch["ids"]["r5"]
    rows = await _run_rows(async_session_factory, runs["H00"])
    assert rows["r5b"].role == "reagent"
    assert rows["r5b"].isotope_label == "81Br"
    assert rows["r5b"].owner_peak_assignment_id == rows["r5"].peak_assignment_id
    hand = await _run_rows(async_session_factory, runs["H01"])
    assert hand["r5b"].assigned_formula == F5B
    assert anchors["r5b"].consensus_role == "reagent"
    # Where the rung's peak is another ion's claim, its line is no line of it.
    assert members[samples["H03"]].role == role_code("reagent")
    assert members[samples["H03"]].owner_batch_peak_id is None
    assert _record(anchors["r5"])["files_by_reason"] == {"claimed_otherwise": 1}


async def test_a_files_own_claim_of_a_rung_is_left_as_it_was(
    async_session_factory, ladder_batch
):
    batch, runs = ladder_batch, ladder_batch["runs"]
    await run_batch_water_ladders(batch["batch_id"])
    anchors = await _anchors(async_session_factory, batch)
    record = _record(anchors["r1"])
    assert record["claimed"] is False
    assert record["members_rewritten"] == 0
    rows = await _run_rows(async_session_factory, runs["H00"])
    assert rows["r1"].provenance["reagent"].get("reason") is None
    # The core is no rung, and carries no record.
    assert _record(anchors["core"]) is None


async def test_the_consensus_keeps_the_record_and_a_new_measurement_replaces_it(
    async_session_factory, ladder_batch
):
    batch = ladder_batch
    await run_batch_water_ladders(batch["batch_id"], batch_peak_run_id="first")
    await recompute_batch_consensus(batch["batch_id"], set(batch["ids"].values()))
    anchors = await _anchors(async_session_factory, batch)
    assert _record(anchors["r3"])["batch_peak_run_id"] == "first"

    # A second measurement claims nothing twice, and replaces every record.
    summary = await run_batch_water_ladders(
        batch["batch_id"], batch_peak_run_id="second"
    )
    anchors = await _anchors(async_session_factory, batch)
    assert _record(anchors["r3"])["batch_peak_run_id"] == "second"
    assert _record(anchors["r3"])["members_rewritten"] == 0
    assert summary["members_rewritten"] == 0
    assert summary["rows_moved"] == 0

    # A batch peak no longer at a passing rung loses its record.
    async with async_session_factory() as session:
        stray = await session.get(BatchPeak, batch["ids"]["core"])
        stray.provenance = {
            **(stray.provenance or {}),
            "water_ladder": {"rung": {}, "reads": {}},
        }
        await session.commit()
    summary = await run_batch_water_ladders(batch["batch_id"])
    assert summary["records_cleared"] == 1
    anchors = await _anchors(async_session_factory, batch)
    assert _record(anchors["core"]) is None


async def test_a_hand_assignment_made_since_the_reading_holds_its_sample(
    async_session_factory, ladder_batch
):
    batch, samples, runs = ladder_batch, ladder_batch["samples"], ladder_batch["runs"]
    anchors, members, _ = await batch_water_ladder._load_batch(batch["batch_id"])
    profiles, times = await batch_water_ladder._load_extras(
        batch["batch_id"], anchors, members
    )
    plan = batch_water_ladder.plan_batch(anchors, members, profiles, times)
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.peak_assignment_run_id == runs["H00"],
                PeakAssignment.sample_peak_id == "r3",
            )
            .values(source="manual")
        )
        await session.commit()
    segments = {part.key: part.segments for part in plan.partitions}
    await batch_water_ladder._write_chunk(
        batch["batch_id"], plan.claims, segments, None
    )
    record = _record((await _anchors(async_session_factory, batch))["r3"])
    assert record["files_by_reason"] == {"hand_assigned": 1}
    assert samples["H00"] not in record["displaced_members"]
    rows = await _run_rows(async_session_factory, runs["H00"])
    assert rows["r3"].role == "M0"


async def test_the_profile_is_resolved_on_a_sample_of_the_partition(
    async_session_factory, ladder_batch, tof
):
    batch = ladder_batch
    tof.clear()
    await run_batch_water_ladders(batch["batch_id"])
    assert len(tof) == 1
    assert tof[0] == min(batch["samples"].values())


async def test_a_partition_no_profile_resolves_reads_no_ladder(
    async_session_factory, ladder_batch, monkeypatch
):
    async def unresolved(sample_item_id, config):
        raise KeyError("no profile")

    monkeypatch.setattr(batch_water_ladder, "_mode_chemistry", unresolved)
    summary = await run_batch_water_ladders(ladder_batch["batch_id"])
    (part,) = summary["partitions"]
    assert part["profile"] is None
    assert part["cores"] == []
    assert summary["claimed"] == 0


async def test_the_known_species_decision_reads_a_ladders_claim_as_the_batchs(
    async_session_factory, ladder_batch
):
    batch = ladder_batch
    await run_batch_water_ladders(batch["batch_id"])
    async with async_session_factory() as session:
        marks = await _row_marks(session, batch["batch_id"], [batch["ids"]["r3"]])
    (reading, _, on_the_batchs_word) = marks.rows[
        (batch["ids"]["r3"], batch["samples"]["H00"])
    ]
    assert reading[0] == "reagent"
    assert on_the_batchs_word is True


async def test_the_route_runs_it_as_a_batch_run(
    editor_client, guest_client, async_session_factory, ladder_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = ladder_batch["batch_id"]
    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/measure-water-ladders"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/measure-water-ladders"
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
    [run] = [run for run in runs if run.action == ACTION_WATER_LADDERS]
    assert run.status == "completed"
    assert run.is_current == 1
    assert run.config["presence_share"] == 0.5
    assert run.summary["rows_moved"] == 2
    anchors = await _anchors(async_session_factory, ladder_batch)
    assert _record(anchors["r3"])["batch_peak_run_id"] == run.batch_peak_run_id


async def test_the_route_refuses_while_another_run_rewrites_the_ledger(
    editor_client, ladder_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "measure_batch_water_ladders", fake_task)
    await start_run(ladder_batch["batch_id"], ACTION_WATER_LADDERS)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{ladder_batch['batch_id']}/measure-water-ladders"
    )
    assert response.status_code == 409, response.text
    assert launched == []
