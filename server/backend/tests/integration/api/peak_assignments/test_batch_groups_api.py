"""Integration tests for the batch's readings and groups on the reads (MAS-1066).

Seeds one batch straight into the ledger, the fixture batch the groups' lens
(MAS-1069) shares: a CH2 series from an assigned root through one mechanism,
with members one step up, three steps up and one step down and no batch peak
two steps up; the neutral one step up read through a second mechanism too
(two channels), with an isotope line of the first; a co-variation record on
each channel naming the other; a label twin the root's batch checks name; and
on the member one step up a stale, partial ratio grade, a known-species
decision, a water-ladder rung and a blend flag. A sample holds the root, the
member one step up and the second channel; another holds the member three
steps up alone.

What is proven: a sample's detail row serves the four readings the ledger
serves and every group its batch peak is in, each member with the sample's own
peak where it holds it; a step with no batch peak is no member; the read takes
the same number of queries whatever the groups hold; and the batch-peaks read,
live and off a run's snapshot, serves the same groups. See
``batch_series.batch_groups``.
"""

import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, select

from mascope_backend.api.new.peak_assignments.batch_peaks import tier_code
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SERIES,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_assignment_id
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

#: The two mechanisms the batch reads its neutrals through. A notation is
#: unique across the table, so these are ones no other suite makes: a suite
#: that makes a common one after this would collide with it.
NITRATE, NITRATE_NOTATION = "mech-grp-no3", "[M+C3H5O3]-"
DEPROTONATED, DEPROTONATED_NOTATION = "mech-grp-mh", "[M+C4H7O2]-"

#: The series' root and unit.
ROOT_FORMULA, UNIT = "C5H8O4", "CH2"


def _series(formula, step):
    return {
        "formula": formula,
        "ionization_mechanism_id": NITRATE,
        "has_anchor": True,
        "unit": UNIT,
        "units": [UNIT],
        "step": step,
        "depth": abs(step),
        "anchor_batch_peak_id": None,
        "root_batch_peak_id": None,
        "root_formula": ROOT_FORMULA,
        "ppm": 0.4,
        "batch_peak_run_id": "series-run",
        "version": 3,
    }


def _co_variation(formula, mechanism_id, partner_id, partner_mechanism_id):
    return {
        "formula": formula,
        "ionization_mechanism_id": mechanism_id,
        "channel": "own",
        "verdict": "co_varying",
        "r": 0.93,
        "partner_batch_peak_id": partner_id,
        "partners": [
            {
                "batch_peak_id": partner_id,
                "formula": formula,
                "ionization_mechanism_id": partner_mechanism_id,
                "verdict": "co_varying",
                "r": 0.93,
            }
        ],
        "batch_peak_run_id": "co-variation-run",
        "version": 2,
    }


def _reads(formula, ion, mechanism_id):
    return {
        "role": None,
        "formula": formula,
        "ion_formula": ion,
        "ionization_mechanism_id": mechanism_id,
    }


#: The anchors, by name: formula, ion, mechanism, m/z.
ANCHORS = {
    "down": ("C4H6O4", "C4H6NO7-", NITRATE, 180.0),
    "root": (ROOT_FORMULA, "C5H8NO7-", NITRATE, 194.0),
    "up": ("C6H10O4", "C6H10NO7-", NITRATE, 208.0),
    "up_line": ("C6H10O4", "[13C]C5H10NO7-", NITRATE, 209.0),
    "up_other": ("C6H10O4", "C6H9O4-", DEPROTONATED, 145.05),
    # Two steps up, but through the other mechanism: no member of the series.
    "two_other": ("C7H12O4", "C7H11O4-", DEPROTONATED, 159.07),
    "three": ("C8H14O4", "C8H14NO7-", NITRATE, 236.0),
    "twin": ("C5H9NO4", "C5H8NO4-", DEPROTONATED, 146.05),
    "root_line": (ROOT_FORMULA, "[13C]C4H8NO7-", NITRATE, 195.0),
}


async def seed_group_batch(async_session_factory, pa_test_data, *, extra_steps=()):
    """The fixture batch (module docstring); ``extra_steps`` adds series
    members that many CH2 steps from the root, which the second sample holds."""
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    ids = {name: gen_id() for name in ANCHORS}
    anchors = dict(ANCHORS)
    for step in extra_steps:
        name = f"extra_{step}"
        anchors[name] = (
            f"C{5 + step}H{8 + 2 * step}O4",
            f"C{5 + step}H{8 + 2 * step}NO7-",
            NITRATE,
            194.0 + 14.0 * step,
        )
        ids[name] = gen_id()
    samples = {"x": gen_id(), "y": gen_id()}
    rows = {}
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
                sample_batch_name=f"Groups {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        for mechanism_id, notation in (
            (NITRATE, NITRATE_NOTATION),
            (DEPROTONATED, DEPROTONATED_NOTATION),
        ):
            if await session.get(IonizationMechanism, mechanism_id) is None:
                session.add(
                    IonizationMechanism(
                        ionization_mechanism_id=mechanism_id,
                        ionization_mechanism_polarity="-",
                        ionization_mechanism=notation,
                    )
                )
        for name, sample_item_id in samples.items():
            sample_file_id = gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"groups-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 1, 12, 0, 0),
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
                    sample_item_name=f"Groups {name}",
                    sample_item_type="sample",
                    polarity="-",
                    sample_item_utc_created=now,
                )
            )
        await session.flush()

        provenance = {name: {} for name in anchors}
        for name, step in (("up", 1), ("three", 3)):
            record = _series(anchors[name][0], step)
            record["root_batch_peak_id"] = ids["root"]
            record["anchor_batch_peak_id"] = ids["root"]
            provenance[name]["series"] = record
        for name in anchors:
            if name.startswith("extra_"):
                record = _series(anchors[name][0], int(name.split("_")[1]))
                record["root_batch_peak_id"] = ids["root"]
                provenance[name]["series"] = record
        provenance["up"]["co_variation"] = _co_variation(
            "C6H10O4", NITRATE, ids["up_other"], DEPROTONATED
        )
        provenance["up_other"]["co_variation"] = _co_variation(
            "C6H10O4", DEPROTONATED, ids["up"], NITRATE
        )
        provenance["root"]["batch_checks"] = {
            "formula": ROOT_FORMULA,
            "ion_formula": anchors["root"][1],
            "ionization_mechanism_id": NITRATE,
            "channel": NITRATE_NOTATION,
            "batch_peak_run_id": "checks-run",
            "version": 1,
            "label_twin": {"batch_peak_id": ids["twin"], "verdict": "consistent"},
            # Stands while the stamping record it read does.
            "halogen_lock": {
                "batch_peak_id": ids["root_line"],
                "stamping_run_id": "stamp-run",
            },
        }
        provenance["root"]["diagnostic_isotopologues"] = {
            "formula": ROOT_FORMULA,
            "ionization_mechanism_id": NITRATE,
            "batch_peak_run_id": "stamp-run",
            "lines": [],
        }
        up_reads = _reads("C6H10O4", anchors["up"][1], NITRATE)
        provenance["up"]["known_species"] = {
            "species": "a known species",
            "verdict": "present",
            "reads": up_reads,
            "displaced_members": {"s": {}},
            "version": 1,
        }
        provenance["up"]["water_ladder"] = {
            "rung": {"n_water": 1},
            "reads": up_reads,
            "claimed_samples": ["s"],
            "version": 1,
        }
        provenance["up"]["blend"] = {
            "formulas": ["C6H10O4", "C5H6O5"],
            "axis": "intensity",
        }
        for name, (formula, ion, mechanism_id, mz) in anchors.items():
            session.add(
                BatchPeak(
                    batch_peak_id=ids[name],
                    sample_batch_id=batch_id,
                    mz=mz,
                    mz_tol_ppm=3.0,
                    intensity_variable="sum_peak_heights",
                    consensus_formula=formula,
                    consensus_ion_formula=ion,
                    ionization_mechanism_id=mechanism_id,
                    consensus_tier="assigned",
                    # One sample each: the ledger's default floor hides them,
                    # and the groups still count them.
                    n_present=1,
                    isotopologue_of=(
                        ids["up"]
                        if name == "up_line"
                        else ids["root"]
                        if name == "root_line"
                        else None
                    ),
                    provenance=provenance[name] or None,
                    continuity_grade="stable" if name == "up" else None,
                    continuity=(
                        {
                            "eligible": True,
                            "formula": formula,
                            "ionization_mechanism_id": mechanism_id,
                            "n_present_at_compute": 4,
                            "warnings": ["partial"],
                            "pairs": [],
                        }
                        if name == "up"
                        else None
                    ),
                )
            )
        await session.flush()

        run_id = gen_id()
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=samples["x"],
                engine_version="0.1.0-test",
                status="completed",
                config={},
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        held = {
            "x": ("root", "up", "up_other"),
            "y": ("three", *(f"extra_{step}" for step in extra_steps)),
        }
        for sample, names in held.items():
            for name in names:
                formula, ion, mechanism_id, mz = anchors[name]
                peak_id = f"p-{name}"
                assignment_id = None
                if sample == "x":
                    assignment_id = gen_id(32)
                    session.add(
                        PeakAssignment(
                            peak_assignment_id=assignment_id,
                            peak_assignment_run_id=run_id,
                            sample_item_id=samples[sample],
                            sample_peak_id=peak_id,
                            sample_peak_mz=mz,
                            sample_peak_intensity=1e5,
                            role="M0",
                            assigned_formula=formula,
                            ion_formula=ion,
                            ionization_mechanism_id=mechanism_id,
                            isotope_label="M0",
                            source="untargeted",
                            fit_score=0.9,
                            mz_error_ppm=0.3,
                            tier="assigned",
                        )
                    )
                    rows[name] = assignment_id
                    await session.flush()
                session.add(
                    BatchPeakOccurrence(
                        batch_peak_id=ids[name],
                        sample_item_id=samples[sample],
                        sample_peak_id=peak_id,
                        peak_assignment_id=assignment_id,
                        mz_delta_ppm=0.0,
                        intensity=1e5,
                        tier=tier_code("assigned"),
                        fit_score=0.9,
                    )
                )
        await session.commit()
    return SimpleNamespace(batch_id=batch_id, ids=ids, samples=samples, rows=rows)


@pytest_asyncio.fixture
async def group_batch(async_session_factory, pa_test_data):
    return await seed_group_batch(async_session_factory, pa_test_data)


async def _detail(client, sample_item_id, peak_assignment_id):
    response = await client.get(
        f"/api/peak-assignments/sample/{sample_item_id}/assignment/{peak_assignment_id}"
    )
    assert response.status_code == 200, response.text
    [row] = response.json()["data"]
    return row


def _by_kind(groups):
    found = {}
    for group in groups:
        assert group["kind"] not in found, groups
        found[group["kind"]] = group
    return found


def _members(group):
    return [member["batch_peak_id"] for member in group["members"]]


async def test_a_member_s_detail_serves_each_reading_and_group(
    guest_client, group_batch
):
    ids, samples = group_batch.ids, group_batch.samples
    row = await _detail(guest_client, samples["x"], group_batch.rows["up"])

    series = row["batch_series"]
    assert series["formula"] == "C6H10O4"
    assert series["root_batch_peak_id"] == ids["root"]
    assert series["version"] == 3
    assert series["root"]["batch_peak_id"] == ids["root"]
    assert series["root"]["formula"] == ROOT_FORMULA
    assert series["root"]["sample_peak_id"] == "p-root"

    assert row["batch_continuity"]["grade"] == "stable"
    assert row["batch_continuity"]["partial"] is True
    assert row["batch_continuity"]["stale"] is True
    assert row["batch_continuity"]["detail"]["n_present_at_compute"] == 4

    known = row["batch_known_species"]
    assert known["verdict"] == "present"
    assert "displaced_members" not in known
    ladder = row["batch_water_ladder"]
    assert ladder["rung"] == {"n_water": 1}
    assert "claimed_samples" not in ladder
    assert row["batch_blend"]["axis"] == "intensity"

    groups = _by_kind(row["batch_groups"])
    assert set(groups) == {"series", "channels", "co_variation"}
    steps = {
        member["batch_peak_id"]: member["step"]
        for member in groups["series"]["members"]
    }
    assert steps == {ids["down"]: -1, ids["root"]: 0, ids["up"]: 1, ids["three"]: 3}
    assert _members(groups["series"]) == [
        ids["down"],
        ids["root"],
        ids["up"],
        ids["three"],
    ]
    assert groups["series"]["unit"] == UNIT
    assert groups["series"]["root_formula"] == ROOT_FORMULA
    # The second channel and the isotope line carry the neutral, and only the
    # channel is one.
    assert set(_members(groups["channels"])) == {ids["up"], ids["up_other"]}
    assert set(_members(groups["co_variation"])) == {ids["up"], ids["up_other"]}

    members = {
        member["batch_peak_id"]: member
        for group in row["batch_groups"]
        for member in group["members"]
    }
    assert {key: member["sample_peak_id"] for key, member in members.items()} == {
        ids["down"]: None,
        ids["root"]: "p-root",
        ids["up"]: "p-up",
        ids["three"]: None,
        ids["up_other"]: "p-up_other",
    }
    assert members[ids["up_other"]]["mechanism"] == DEPROTONATED_NOTATION
    assert members[ids["up"]]["mechanism"] == NITRATE_NOTATION
    assert members[ids["up_other"]]["ionization_mechanism_id"] == DEPROTONATED
    assert members[ids["three"]]["mz"] == 236.0
    assert members[ids["three"]]["consensus_tier"] == "assigned"
    assert members[ids["three"]]["formula"] == "C8H14O4"
    assert members[ids["three"]]["ion_formula"] == "C8H14NO7-"

    # The root is in the series and the check pair; it has no ratio grade.
    root = await _detail(guest_client, samples["x"], group_batch.rows["root"])
    assert [group["kind"] for group in root["batch_groups"]] == [
        "series",
        "check_pair",
        "check_pair",
    ]
    series_group, lock, twin = root["batch_groups"]
    assert series_group == groups["series"]
    assert lock["check"] == "halogen_lock"
    assert _members(lock) == [ids["root"], ids["root_line"]]
    assert twin["check"] == "label_twin"
    assert _members(twin) == [ids["root"], ids["twin"]]
    assert root["batch_series"] is None
    assert root["batch_continuity"] is None
    assert root["batch_known_species"] is None
    assert root["batch_water_ladder"] is None
    assert root["batch_blend"] is None


async def test_a_series_step_with_no_batch_peak_is_no_member(guest_client, group_batch):
    ids = group_batch.ids
    row = await _detail(guest_client, group_batch.samples["x"], group_batch.rows["up"])
    [series] = [group for group in row["batch_groups"] if group["kind"] == "series"]
    assert 2 not in [member["step"] for member in series["members"]]
    # A batch peak of the missing step's formula through another mechanism is
    # none either.
    assert ids["two_other"] not in _members(series)
    assert ids["up_line"] not in _members(series)


async def test_a_derived_row_serves_the_groups_with_its_own_sample_s_peaks(
    guest_client, group_batch
):
    ids = group_batch.ids
    row = await _detail(
        guest_client, group_batch.samples["y"], fold_assignment_id(ids["three"])
    )
    [series] = row["batch_groups"]
    assert series["kind"] == "series"
    peaks = {m["batch_peak_id"]: m["sample_peak_id"] for m in series["members"]}
    assert peaks[ids["three"]] == "p-three"
    assert peaks[ids["root"]] is None
    assert row["batch_series"]["formula"] == "C8H14O4"
    assert row["batch_series"]["root"]["sample_peak_id"] is None


async def test_a_row_in_no_batch_peak_serves_none(guest_client, pa_test_data):
    row = await _detail(
        guest_client, pa_test_data["sample_item_id"], pa_test_data["m0_assignment_id"]
    )
    for key in (
        "batch_series",
        "batch_continuity",
        "batch_known_species",
        "batch_water_ladder",
        "batch_blend",
        "batch_groups",
    ):
        assert row[key] is None, key


@contextlib.contextmanager
def _statements(async_engine):
    """The SQL statements run while entered."""
    statements: list[str] = []

    def before(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(async_engine.sync_engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", before)


async def test_the_detail_takes_a_fixed_number_of_queries(
    guest_client, async_session_factory, async_engine, pa_test_data
):
    async def count(batch):
        with _statements(async_engine) as statements:
            row = await _detail(guest_client, batch.samples["x"], batch.rows["up"])
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        return row, len(selects)

    small = await seed_group_batch(async_session_factory, pa_test_data)
    large = await seed_group_batch(
        async_session_factory, pa_test_data, extra_steps=(4, 5, 6, 7, 8, 9)
    )
    small_row, small_count = await count(small)
    large_row, large_count = await count(large)
    [small_series] = [g for g in small_row["batch_groups"] if g["kind"] == "series"]
    [large_series] = [g for g in large_row["batch_groups"] if g["kind"] == "series"]
    assert len(large_series["members"]) == len(small_series["members"]) + 6
    assert large_count == small_count


async def test_the_batch_peaks_read_serves_the_same_groups(guest_client, group_batch):
    ids, samples, batch_id = group_batch.ids, group_batch.samples, group_batch.batch_id

    async def ledger(**params):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch_id}", params=params
        )
        assert response.status_code == 200, response.text
        return response.json()

    live = await ledger()
    # The default floor hides every row of one sample; the groups span them.
    assert live["data"] == []
    groups = {group["group_id"]: group for group in live["batch_groups"]}
    assert [group["kind"] for group in live["batch_groups"]] == [
        "series",
        "channels",
        "co_variation",
        "check_pair",
        "check_pair",
    ]

    def without_sample(group):
        return {
            **group,
            "members": [
                {**member, "sample_peak_id": None} for member in group["members"]
            ],
        }

    for name in ("up", "root"):
        row = await _detail(guest_client, samples["x"], group_batch.rows[name])
        for group in row["batch_groups"]:
            assert without_sample(group) == groups[group["group_id"]]
    for group in live["batch_groups"]:
        assert all(member["sample_peak_id"] is None for member in group["members"])
    assert _members(groups[f"check_pair:label_twin:{ids['root']}"]) == [
        ids["root"],
        ids["twin"],
    ]

    run_id = await start_run(batch_id, ACTION_SERIES)
    await complete_run(batch_id, run_id)
    later = await start_run(batch_id, ACTION_SERIES)
    await complete_run(batch_id, later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier["batch_groups"] == live["batch_groups"]
    whole = await ledger(batch_peak_run_id=run_id, min_n_present=1)
    assert len(whole["data"]) == len(ANCHORS)
    assert whole["batch_groups"] == live["batch_groups"]
