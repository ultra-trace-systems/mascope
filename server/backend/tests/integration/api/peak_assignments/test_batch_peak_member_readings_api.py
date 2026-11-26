"""Integration tests for the read of every member of one batch peak, on any
formula, with what the batch's decisions did to each member's run row
(``batch_level_sources.get_batch_peak_member_readings``, MAS-1315).

Eleven samples in a batch of their own, each with one glucose peak, folded
into one batch peak. Then each member's run row is put in one state: its own
reading (a level, another formula, none), no row at all, a row a batch stage
rewrote in place, or a row its run wrote reading a batch decision. The rows'
marks are written by the stages' own row writers, so the read is tested
against the provenance the stages leave.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    with_row_before,
)
from mascope_backend.api.new.peak_assignments.batch_lines import (
    BatchLine,
    read_as_batch_isotopologue,
    stamp_series,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import MARK_SEARCH
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    BatchSpecies,
    batch_species_row,
    water_rung_row,
)
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    User,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.reagents import ReagentCluster


pytestmark = pytest.mark.asyncio

GLUCOSE = "C6H12O6"
ION = ReagentCluster("BrH2O", -1, "BrH2O", "cluster")
LINE = BatchLine(
    label="13C",
    mz_offset=1.00336,
    share=0.066,
    batch_peak_id="bp-line",
    parent_batch_peak_id="bp-parent",
    reading={"share": 0.07},
)

# Sample -> (intensity, the state its member's run row is put in).
_MEMBERS = {
    "own_4a": (3000.0, "level_4a"),
    "own_5a": (9000.0, "level_5a"),
    "other": (8000.0, "other_formula"),
    "none": (7500.0, "no_formula"),
    "no_run": (4000.0, "no_run"),
    "ks": (6000.0, "known_species"),
    "listed": (5800.0, "listed"),
    "water": (5000.0, "water_ladder"),
    "strip": (4500.0, "strip"),
    "run_ks": (2000.0, "run_known_species"),
    "run_reads": (1000.0, "run_strip_and_series"),
}


async def _seed_sample(session, batch_id, name, intensity, at):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-member-readings-{name}-{sample_file_id}.zarr",
            instrument="orbi-test",
            datetime=at.replace(tzinfo=None),
            datetime_utc=at,
            length=60.0,
            range=[50.0, 500.0],
            polarity="+",
        )
    )
    session.add(
        SampleItem(
            sample_item_id=sample_item_id,
            sample_batch_id=batch_id,
            sample_file_id=sample_file_id,
            sample_item_name=f"Member readings {name}",
            sample_item_type="sample",
            polarity="+",
            sample_item_utc_created=at,
        )
    )
    session.add(
        PeakAssignmentRun(
            peak_assignment_run_id=run_id,
            sample_item_id=sample_item_id,
            engine_version="0.1.0-test",
            status="completed",
            peak_assignment_run_utc_created=at,
            peak_assignment_run_utc_completed=at + timedelta(minutes=5),
        )
    )
    session.add(
        PeakAssignment(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=run_id,
            sample_item_id=sample_item_id,
            sample_peak_id="p1",
            sample_peak_mz=181.0707,
            sample_peak_intensity=intensity,
            role="M0",
            assigned_formula=GLUCOSE,
            ion_formula="C6H13O6+",
            source="database",
            fit_score=0.9,
            mz_error_ppm=1.0,
            tier="assigned",
            level="4a",
            provenance={"identification_level": {"level": "4a"}},
        )
    )
    return sample_item_id


def _as_dict(row) -> dict:
    return {c.name: getattr(row, c.name) for c in PeakAssignment.__table__.columns}


def _write(row, values: dict) -> None:
    for key, value in values.items():
        setattr(row, key, value)


def _values(state: str, row) -> dict | None:
    """The values that put a run row in a state, from the stages' writers."""
    own = _as_dict(row)
    if state in ("level_4a", "level_5a"):
        level = state[-2:]
        return {
            "level": level,
            "provenance": {
                "identification_level": {
                    "level": level,
                    "evidence": f"evidence of {level}",
                    "lift": f"lift of {level}",
                }
            },
        }
    if state == "other_formula":
        return {"assigned_formula": "C6H12O5", "ion_formula": "C6H13O5+"}
    if state == "no_formula":
        return {
            "assigned_formula": None,
            "ion_formula": None,
            "tier": "unassigned",
            "level": None,
            "provenance": {},
        }
    if state == "known_species":
        # The batch's claim rewrites the row: its record cites no version.
        return with_row_before(
            row,
            batch_species_row(
                own, BatchSpecies(ION, {"batch_peak_id": "bp", "verdict": "present"})
            ),
        )
    if state == "listed":
        return with_row_before(
            row,
            {
                "tier": "candidate",
                "provenance": {
                    REASON_BATCH_KNOWN_SPECIES: {"batch_peak_id": "bp"},
                    "tier_reasons": [{"rule": REASON_BATCH_KNOWN_SPECIES}],
                },
            },
        )
    if state == "water_ladder":
        return with_row_before(
            row,
            water_rung_row(own, ION, {"core": "[Br]-", "waters": 1}, exact_mz=96.9),
        )
    if state == "strip":
        # Its own row stands in for the owner: the key must name a stored row.
        owner = {**own, "sample_peak_mz": 180.0}
        return read_as_batch_isotopologue(own, owner, LINE)
    if state == "run_known_species":
        # The sample's run claimed the ion on the batch's word: no row
        # before, and the record cites the stage's version.
        return batch_species_row(
            own, BatchSpecies(ION, {"batch_peak_id": "bp", "version": 3})
        )
    if state == "run_strip_and_series":
        # A run read a strip onto one row and, on the same reading, a series.
        # Its own row stands in for the owner: the key must name a stored row.
        owner = {**own, "sample_peak_mz": 180.0}
        values = read_as_batch_isotopologue(own, owner, replace(LINE, version=2))
        stamped = {**own, **values, "role": "M0", "sample_peak_id": "p1"}
        assert stamp_series(
            [stamped],
            {"p1": {"formula": GLUCOSE, "ionization_mechanism_id": None, "version": 1}},
        )
        return {**values, "provenance": stamped["provenance"]}
    return None


@pytest_asyncio.fixture
async def readings(async_session_factory, pa_test_data):
    start = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    batch_id = gen_id()
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
                sample_batch_name=f"Member readings {batch_id}",
                sample_batch_utc_created=start,
            )
        )
        samples = {}
        for index, (name, (intensity, _)) in enumerate(_MEMBERS.items()):
            samples[name] = await _seed_sample(
                session, batch_id, name, intensity, start + timedelta(hours=index)
            )
        await session.commit()
    for sample_item_id in samples.values():
        assert await fold_sample_into_batch_peaks(sample_item_id) == batch_id
    async with async_session_factory() as session:
        for name, (_, state) in _MEMBERS.items():
            if state == "no_run":
                await session.execute(
                    update(BatchPeakOccurrence)
                    .where(BatchPeakOccurrence.sample_item_id == samples[name])
                    .values(peak_assignment_id=None)
                )
                continue
            row = (
                await session.execute(
                    select(PeakAssignment).where(
                        PeakAssignment.sample_item_id == samples[name]
                    )
                )
            ).scalar_one()
            _write(row, _values(state, row))
        # A batch stage's mark on a member, beside its sample's own row.
        await session.execute(
            update(BatchPeakOccurrence)
            .where(BatchPeakOccurrence.sample_item_id == samples["own_5a"])
            .values(batch_written={"stage": MARK_SEARCH, "version": 1, "before": {}})
        )
        await session.commit()
        [batch_peak_id] = (
            (
                await session.execute(
                    select(BatchPeak.batch_peak_id).where(
                        BatchPeak.sample_batch_id == batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return {"batch_id": batch_id, "batch_peak_id": batch_peak_id, "samples": samples}


def _url(readings, route="members", batch_id=None, batch_peak_id=None):
    return (
        f"/api/batch-peaks/batch/{batch_id or readings['batch_id']}/{route}/"
        f"{batch_peak_id or readings['batch_peak_id']}"
    )


async def _by_name(client, readings):
    response = await client.get(_url(readings))
    assert response.status_code == 200, response.text
    body = response.json()
    names = {v: k for k, v in readings["samples"].items()}
    return body, {names[m["sample_item_id"]]: m for m in body["data"]}


async def test_it_lists_every_member_brightest_first(guest_client, readings):
    body, _ = await _by_name(guest_client, readings)
    names = {v: k for k, v in readings["samples"].items()}

    assert [names[m["sample_item_id"]] for m in body["data"]] == sorted(
        _MEMBERS, key=lambda name: -_MEMBERS[name][0]
    )
    assert body["results"] == body["n_members"] == len(_MEMBERS)
    assert body["consensus_formula"] == GLUCOSE


async def test_a_member_on_the_consensus_carries_its_reading_and_level(
    guest_client, readings
):
    _, members = await _by_name(guest_client, readings)
    own = members["own_5a"]

    assert own["sample_item_name"] == "Member readings own_5a"
    assert own["sample_peak_id"] == "p1"
    assert own["peak_assignment_id"]
    assert own["intensity"] == 9000.0
    assert own["datetime_utc"].startswith("2026-07-05T13:00:00")
    assert (
        own["assigned_formula"],
        own["ion_formula"],
        own["tier"],
        own["role"],
        own["level"],
        own["level_evidence"],
        own["level_lift"],
        own["unstated"],
    ) == (
        GLUCOSE,
        "C6H13O6+",
        "assigned",
        "M0",
        "5a",
        "evidence of 5a",
        "lift of 5a",
        None,
    )
    assert own["batch_stage"] is None
    assert own["batch_read"] == []


async def test_a_member_on_another_formula_is_listed_with_its_reading(
    guest_client, readings
):
    _, members = await _by_name(guest_client, readings)

    other = members["other"]
    assert (other["assigned_formula"], other["ion_formula"], other["level"]) == (
        "C6H12O5",
        "C6H13O5+",
        "4a",
    )
    assert other["unstated"] == "other_formula"
    assert members["none"]["unstated"] == "no_formula"
    assert members["none"]["tier"] == "unassigned"


async def test_a_member_with_no_run_row_is_listed_with_its_why(guest_client, readings):
    _, members = await _by_name(guest_client, readings)
    member = members["no_run"]

    assert member["intensity"] == 4000.0
    assert member["peak_assignment_id"] is None
    assert member["unstated"] == "no_run"
    assert {
        key: member[key]
        for key in (
            "assigned_formula",
            "ion_formula",
            "tier",
            "role",
            "level",
            "level_evidence",
            "level_lift",
            "batch_stage",
        )
    } == dict.fromkeys(
        (
            "assigned_formula",
            "ion_formula",
            "tier",
            "role",
            "level",
            "level_evidence",
            "level_lift",
            "batch_stage",
        )
    )
    assert member["batch_read"] == []


async def test_a_row_a_batch_stage_rewrote_carries_the_stage(guest_client, readings):
    body, members = await _by_name(guest_client, readings)

    assert {
        name: members[name]["batch_stage"]
        for name in ("ks", "listed", "water", "strip")
    } == {
        "ks": "known_species",
        "listed": "known_species",
        "water": "water_ladders",
        "strip": "isotope_lines",
    }
    # The rewrite is the stage's own, not a decision the run read.
    assert all(
        members[name]["batch_read"] == [] for name in ("ks", "listed", "water", "strip")
    )
    assert members["ks"]["role"] == "reagent"
    assert members["strip"]["role"] == "iso_child"
    assert body["batch_stages"] == {
        "water_ladders": 1,
        "known_species": 2,
        "isotope_lines": 1,
    }


async def test_a_row_neither_touched_carries_no_stage(guest_client, readings):
    _, members = await _by_name(guest_client, readings)

    assert {
        name: members[name]["batch_stage"]
        for name in ("own_4a", "own_5a", "other", "none")
    } == dict.fromkeys(("own_4a", "own_5a", "other", "none"))


async def test_a_decision_the_run_read_is_served_with_its_version(
    guest_client, readings
):
    _, members = await _by_name(guest_client, readings)

    assert members["run_ks"]["batch_stage"] is None
    assert members["run_ks"]["batch_read"] == [{"stage": "known_species", "version": 3}]
    assert members["run_reads"]["batch_stage"] is None
    assert members["run_reads"]["batch_read"] == [
        {"stage": "isotope_lines", "version": 2},
        {"stage": "series", "version": 1},
    ]


async def test_a_members_own_mark_is_served(guest_client, readings):
    _, members = await _by_name(guest_client, readings)

    assert members["own_5a"]["member_stage"] == "search"
    assert members["own_4a"]["member_stage"] is None


async def test_it_counts_the_members_stating_none_as_the_levels_read(
    guest_client, readings
):
    body, members = await _by_name(guest_client, readings)
    levels = (await guest_client.get(_url(readings, route="levels"))).json()

    assert body["unstated"] == levels["unstated"]
    assert levels["n_members"] == body["n_members"]
    # The levels read lists exactly the members stating a level here.
    assert sorted(m["sample_item_id"] for m in levels["data"]) == sorted(
        m["sample_item_id"] for m in members.values() if m["unstated"] is None
    )


async def test_an_unknown_batch_peak_is_not_found(guest_client, readings):
    response = await guest_client.get(_url(readings, batch_peak_id=gen_id()))

    assert response.status_code == 404, response.text


async def test_a_batch_peak_asked_under_another_batch_is_not_found(
    guest_client, readings, pa_test_data
):
    response = await guest_client.get(
        _url(readings, batch_id=pa_test_data["sample_batch_id"])
    )

    assert response.status_code == 404, response.text


@pytest_asyncio.fixture
async def outsider_client(async_session_factory, roles, create_jwt_auth_token):
    """A signed-in user who is a member of no workspace."""
    async with async_session_factory() as session:
        user = User(
            email=f"outsider-{gen_id()}@test.com",
            username=f"outsider_{gen_id()}",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["editor"].role_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: create_jwt_auth_token(user)},
    ) as client:
        yield client


async def test_a_reader_without_access_to_the_batch_is_refused(
    outsider_client, readings
):
    response = await outsider_client.get(_url(readings))

    assert response.status_code == 403, response.text
