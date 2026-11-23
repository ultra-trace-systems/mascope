"""
Integration tests: ``GET /api/calibration/trend`` and its CSV export.

Against the real routes and a real database: which files of an instrument a
trend reads (one polarity, TOF, a fit or a failed calibration, inside the
window), what the files acquired before the window say of the collection
changes in it, which collections are named to whom, who may read a trend at
all, a window of too many files served in bins summed up in the database, and
the export. How one record is read, and how the records line up, is
the unit tests' (``unit/api/calibration/test_calibration_trend.py``).
"""

import copy
import csv
import io
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.controllers.calibration import calibration_trend
from mascope_backend.api.models.dataset.config import acquisition_workspace_name
from mascope_backend.db import (
    SampleFile,
    TargetCollection,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


#: The window the tests read, and the hour each file is acquired in it.
_W0 = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
_W1 = _W0 + timedelta(days=2)

_V1 = "a" * 64
_V2 = "b" * 64


def _record(collection_id, sha, offset_ppm=-80.0, residual=1.0):
    """A TOF fit's record with two lines, fitted with one collection version."""

    def point(ion, target, retained):
        return {
            "ion": ion,
            "target_mz": target,
            "observed_mz": target * (1 + offset_ppm * 1e-6),
            "residual_ppm": residual if retained else 25.0,
            "snr": 120.0,
            "retained": retained,
        }

    return {
        "mode": 0,
        "par": [2800.0, -0.5],
        "status": "ok",
        "verified": True,
        "quality": {
            "n_points": 1,
            "n_ions": 1,
            "pre_fit_mz_error_ppm": abs(offset_ppm),
            "post_fit_mz_error_ppm": abs(residual),
            "points": [point("NO3-", 61.9884, True), point("Br3-", 236.7536, False)],
            "span": [61.9884, 61.9884],
            "collection": {"id": collection_id, "isotopes_sha256": sha},
            "model": {"mode": 0, "equation": "i = p1*sqrt(m) + p2"},
            "node_version": 2,
        },
    }


@pytest_asyncio.fixture
async def instrument(async_session_factory, test_users):
    """A TOF instrument whose acquisition workspace the guest is a guest of.

    Its negative files: one fitted before records named their collection,
    five days before the window, and one with the global collection's first
    version two days before it; in the window, that version, then its second
    (an edit), then a private collection taking over, a failed calibration,
    and three files no trend reads - one still on its acquisition axis, one
    fitted before records had a quality block and one whose quality block is
    null; and one after the window. Beside them, files no trend of it reads: a
    positive one, an Orbitrap one and another instrument's, in the window and
    the day before it, the latter fitted with the private collection.
    """
    # A space in it, which the export's file name does not keep.
    name = f"trend tof {gen_id(6).lower()}"
    other = f"trend-other-{gen_id(6).lower()}"
    ids = {key: gen_id(16) for key in ("acq", "private_ws", "global", "private")}
    files: dict[str, str] = {}
    async with async_session_factory() as session:
        session.add_all(
            [
                Workspace(
                    workspace_id=ids["acq"],
                    workspace_name=acquisition_workspace_name(name),
                    workspace_status="active",
                    is_system=True,
                ),
                Workspace(
                    workspace_id=ids["private_ws"],
                    workspace_name=f"Trend private {ids['private_ws']}",
                    workspace_status="active",
                ),
            ]
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=ids["acq"],
                user_id=test_users["guest"].id,
                workspace_role="guest",
            )
        )
        session.add_all(
            [
                TargetCollection(
                    target_collection_id=ids["global"],
                    target_collection_name="Trend global set",
                ),
                TargetCollection(
                    target_collection_id=ids["private"],
                    target_collection_name="Trend private set",
                    workspace_id=ids["private_ws"],
                ),
            ]
        )

        def add(key, at, record, polarity="-", kind="tof", instrument_name=name):
            files[key] = gen_id(16)
            session.add(
                SampleFile(
                    sample_file_id=files[key],
                    filename=f"{instrument_name}_{key}_{files[key]}.h5",
                    instrument=instrument_name,
                    instrument_type=kind,
                    datetime=at.replace(tzinfo=None),
                    datetime_utc=at,
                    length=60.0,
                    range=[10.0, 600.0],
                    polarity=polarity,
                    mz_calibration=copy.deepcopy(record),
                )
            )

        older = _record(ids["global"], _V1)
        for key in ("points", "span", "collection", "model", "node_version"):
            del older["quality"][key]
        add("older", _W0 - timedelta(days=5), older)
        add("before", _W0 - timedelta(days=2), _record(ids["global"], _V1))
        add("v1", _W0 + timedelta(hours=1), _record(ids["global"], _V1, -81.0))
        add("v2", _W0 + timedelta(hours=2), _record(ids["global"], _V2, -82.0, 0.5))
        add("private", _W0 + timedelta(hours=3), _record(ids["private"], _V1))
        add(
            "failed",
            _W0 + timedelta(hours=4),
            {"mode": 0, "par": [1.0, 2.0], "status": "failed", "verified": False},
        )
        add(
            "unfitted",
            _W0 + timedelta(hours=5),
            {"mode": 0, "par": [1.0, 2.0], "status": "unfitted", "verified": False},
        )
        add(
            "bare",
            _W0 + timedelta(hours=5, minutes=20),
            {"mode": 0, "par": [1.0, 2.0], "status": "ok", "verified": True},
        )
        add(
            "quality_null",
            _W0 + timedelta(hours=5, minutes=40),
            {"mode": 0, "par": [1.0, 2.0], "status": "ok", "quality": None},
        )
        add("positive", _W0 + timedelta(hours=6), _record(ids["global"], _V1), "+")
        add("orbi", _W0 + timedelta(hours=7), _record(ids["global"], _V1), kind="orbi")
        add(
            "other",
            _W0 + timedelta(hours=8),
            _record(ids["global"], _V1),
            instrument_name=other,
        )
        # Fitted with the private set before the window, on files whose trend
        # is not this one: the private set is still new in it.
        day_before = _W0 - timedelta(days=1)
        add("positive_before", day_before, _record(ids["private"], _V1), "+")
        add("orbi_before", day_before, _record(ids["private"], _V1), kind="orbi")
        add(
            "other_before",
            day_before,
            _record(ids["private"], _V1),
            instrument_name=other,
        )
        add("after", _W1 + timedelta(hours=1), _record(ids["global"], _V2))
        await session.commit()
    yield {"name": name, "files": files, **ids}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.execute(
            delete(TargetCollection).where(
                TargetCollection.target_collection_id.in_(
                    [ids["global"], ids["private"]]
                )
            )
        )
        await session.execute(
            delete(WorkspaceMember).where(WorkspaceMember.workspace_id == ids["acq"])
        )
        await session.execute(
            delete(Workspace).where(
                Workspace.workspace_id.in_([ids["acq"], ids["private_ws"]])
            )
        )
        await session.commit()


_V3 = "c" * 64


@pytest_asyncio.fixture
async def edits(async_session_factory):
    """A TOF instrument whose one collection was edited twice: its negative
    files are fitted with the collection's first, second and third version,
    an hour apart, from the window's first hour."""
    name = f"trend-edits-{gen_id(6).lower()}"
    collection_id = gen_id(16)
    files: dict[str, str] = {}
    async with async_session_factory() as session:
        for hour, (key, sha) in enumerate(
            (("e1", _V1), ("e2", _V2), ("e3", _V3)), start=1
        ):
            files[key] = gen_id(16)
            at = _W0 + timedelta(hours=hour)
            session.add(
                SampleFile(
                    sample_file_id=files[key],
                    filename=f"{name}_{key}.h5",
                    instrument=name,
                    instrument_type="tof",
                    datetime=at.replace(tzinfo=None),
                    datetime_utc=at,
                    length=60.0,
                    range=[10.0, 600.0],
                    polarity="-",
                    mz_calibration=_record(collection_id, sha),
                )
            )
        await session.commit()
    yield {"name": name, "files": files, "collection": collection_id}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.commit()


@pytest_asyncio.fixture
async def busy(async_session_factory):
    """A TOF instrument with five negative files in one hour, fitted with one
    collection, their axis 84 to 76 ppm off and their residuals 0.2 to
    1.0 ppm, the second line always left out and read 40 ppm further off;
    the last three write their count of kept lines as no whole number (a
    string, a fraction, a float), and the last of them writes the first
    line's m/z to a fifth decimal. Two hours later a file fitted before
    records listed their lines, and an hour after it one whose collection
    names no isotope hash."""
    name = f"trend-busy-{gen_id(6).lower()}"
    collection_id = gen_id(16)
    files: dict[str, str] = {}
    async with async_session_factory() as session:

        def add(key, at, record):
            files[key] = gen_id(16)
            session.add(
                SampleFile(
                    sample_file_id=files[key],
                    filename=f"{name}_{key}.h5",
                    instrument=name,
                    instrument_type="tof",
                    datetime=at.replace(tzinfo=None),
                    datetime_utc=at,
                    length=60.0,
                    range=[10.0, 600.0],
                    polarity="-",
                    mz_calibration=record,
                )
            )

        for i, offset in enumerate((-84.0, -82.0, -80.0, -78.0, -76.0)):
            record = _record(collection_id, _V1, offset, 0.2 * (i + 1))
            nitrate, bromide = record["quality"]["points"]
            bromide["observed_mz"] = bromide["target_mz"] * (1 + (offset + 40) * 1e-6)
            if i >= 2:
                record["quality"]["n_points"] = ("9", 2.5, 4.0)[i - 2]
            if i == 4:
                nitrate["target_mz"] = 61.98841
            add(f"b{i}", _W0 + timedelta(hours=1, minutes=5 * i), record)
        older = _record(collection_id, _V1, -80.0, 1.5)
        for key in ("points", "collection"):
            del older["quality"][key]
        add("older", _W0 + timedelta(hours=3, minutes=30), older)
        unhashed = _record(collection_id, _V1)
        unhashed["quality"]["collection"]["isotopes_sha256"] = None
        add("unhashed", _W0 + timedelta(hours=4, minutes=10), unhashed)
        await session.commit()
    yield {"name": name, "files": files, "collection": collection_id}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.commit()


@pytest_asyncio.fixture
async def copies(async_session_factory):
    """A TOF instrument whose files were fitted with two collections of the
    same isotopes, so of one hash: two files with the first, then two with
    the second, an hour apart. The last file's record names one line's ion
    with a number."""
    name = f"trend-copies-{gen_id(6).lower()}"
    first, second = gen_id(16), gen_id(16)
    files: dict[str, str] = {}
    async with async_session_factory() as session:
        for hour, (key, collection_id) in enumerate(
            (("a1", first), ("a2", first), ("b1", second), ("b2", second)), start=1
        ):
            record = _record(collection_id, _V1)
            if key == "b2":
                record["quality"]["points"][1]["ion"] = 42
            files[key] = gen_id(16)
            at = _W0 + timedelta(hours=hour)
            session.add(
                SampleFile(
                    sample_file_id=files[key],
                    filename=f"{name}_{key}.h5",
                    instrument=name,
                    instrument_type="tof",
                    datetime=at.replace(tzinfo=None),
                    datetime_utc=at,
                    length=60.0,
                    range=[10.0, 600.0],
                    polarity="-",
                    mz_calibration=record,
                )
            )
        await session.commit()
    yield {"name": name, "files": files, "first": first, "second": second}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.commit()


@pytest.fixture
def binned(monkeypatch):
    """Any window of a file or more is served in bins."""
    monkeypatch.setattr(calibration_trend, "TREND_MAX_FILES", 0)


def _edits(data):
    return [
        (change["kind"], change["isotopes_sha256"], change["previous_isotopes_sha256"])
        for change in data["changes"]
    ]


def _lines(data):
    """A trend's calibrant lines, by ion and exact m/z."""
    return [(line["ion"], line["target_mz"]) for line in data["calibrants"]]


def _window(instrument, start=_W0, end=_W1, **more):
    return {
        "instrument": instrument["name"],
        "polarity": "-",
        "datetime_min": start.isoformat(),
        "datetime_max": end.isoformat(),
        **more,
    }


async def _trend(client, params) -> dict:
    response = await client.get("/api/calibration/trend", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _ids(instrument, data, key="files"):
    by_id = {value: key for key, value in instrument["files"].items()}
    return [by_id[entry["sample_file_id"]] for entry in data[key]]


@pytest.mark.asyncio
async def test_reads_the_instruments_fits_of_one_polarity_in_the_window(
    guest_client, instrument
):
    data = await _trend(guest_client, _window(instrument))

    assert _ids(instrument, data) == ["v1", "v2", "private", "failed"]
    assert [entry["status"] for entry in data["files"]] == [
        "ok",
        "ok",
        "ok",
        "failed",
    ]
    assert data["truncated"] is False
    assert (data["binned"], data["bin"], data["n_files"]) == (False, "hour", 4)
    assert data["bins"] == []


@pytest.mark.asyncio
async def test_serves_each_lines_residuals_and_the_axis_offset(
    guest_client, instrument
):
    data = await _trend(guest_client, _window(instrument))

    lines = {line["ion"]: line for line in data["calibrants"]}
    assert lines["NO3-"]["residual_ppm"] == [1.0, 0.5, 1.0, None]
    assert lines["Br3-"]["retained"] == [False, False, False, None]
    assert [round(entry["axis_offset_ppm"] or 0, 6) for entry in data["files"]] == [
        -81.0,
        -82.0,
        -80.0,
        0,
    ]
    assert data["files"][0]["n_points"] == 1
    assert data["files"][0]["n_candidates"] == 2


@pytest.mark.asyncio
async def test_marks_the_edit_and_the_collection_that_took_over(
    guest_client, instrument
):
    """V1 was in use before the window: only V2 and the private set are new.

    The private set fitted files the day before the window too, but of the
    other polarity, an Orbitrap and another instrument: none of this trend's.
    """
    data = await _trend(guest_client, _window(instrument))

    changes = [
        (
            change["kind"],
            change["collection_id"],
            change["isotopes_sha256"],
            change["previous_isotopes_sha256"],
        )
        for change in data["changes"]
    ]
    assert changes == [
        ("edited", instrument["global"], _V2, _V1),
        ("first_used", instrument["private"], _V1, None),
    ]
    assert _ids(instrument, data, "changes") == ["v2", "private"]


@pytest.mark.asyncio
async def test_an_edit_before_the_windows_first_file_is_read_from_before_it(
    guest_client, instrument
):
    """The window opens on V2; the file before it, on V1, says it is an edit."""
    data = await _trend(
        guest_client,
        _window(instrument, start=_W0 + timedelta(hours=1, minutes=30)),
    )

    assert _ids(instrument, data) == ["v2", "private", "failed"]
    assert [
        (change["kind"], change["previous_isotopes_sha256"])
        for change in data["changes"]
    ] == [("edited", _V1), ("first_used", None)]


@pytest.mark.asyncio
async def test_nothing_before_the_first_records_is_a_change(guest_client, instrument):
    """A window from before the instrument's first file: its records begin
    with V1, which no earlier file had, and that is no change."""
    data = await _trend(
        guest_client,
        _window(instrument, start=_W0 - timedelta(days=10)),
    )

    assert _ids(instrument, data)[:2] == ["older", "before"]
    assert data["files"][0]["axis_offset_ppm"] is None
    assert data["files"][0]["collection"] is None
    assert [change["kind"] for change in data["changes"]] == ["edited", "first_used"]


@pytest.mark.asyncio
async def test_files_before_the_window_that_name_no_collection_begin_no_change(
    guest_client, instrument
):
    """Only a record from before collections were named precedes the window:
    the window's first collection is where the records of them begin."""
    data = await _trend(
        guest_client, _window(instrument, start=_W0 - timedelta(days=3))
    )

    assert _ids(instrument, data)[0] == "before"
    assert [change["kind"] for change in data["changes"]] == ["edited", "first_used"]


@pytest.mark.asyncio
async def test_a_collection_is_named_only_to_who_can_read_it(
    guest_client, owner_client, instrument
):
    guest = await _trend(guest_client, _window(instrument))
    owner = await _trend(owner_client, _window(instrument))

    def names(data):
        return {version["id"]: version["name"] for version in data["collections"]}

    assert names(guest) == {
        instrument["global"]: "Trend global set",
        instrument["private"]: None,
    }
    assert names(owner)[instrument["private"]] == "Trend private set"


@pytest.mark.asyncio
async def test_a_window_of_more_files_than_the_trend_draws_is_served_in_bins(
    guest_client, instrument, monkeypatch
):
    """Four files on the trend, one more than it draws one by one: a bin per
    hour and collection, the failed calibration in one of no collection. The
    files no trend reads are not summed up."""
    monkeypatch.setattr(calibration_trend, "TREND_MAX_FILES", 3)

    data = await _trend(guest_client, _window(instrument))

    assert (data["binned"], data["bin"], data["n_files"]) == (True, "hour", 4)
    assert data["files"] == []
    assert data["truncated"] is False
    hour = lambda h: (_W0 + timedelta(hours=h)).isoformat()  # noqa: E731
    assert [
        (b["start_utc"], b["collection_id"], b["n_files"], b["n_failed"])
        for b in data["bins"]
    ] == [
        (hour(1), instrument["global"], 1, 0),
        (hour(2), instrument["global"], 1, 0),
        (hour(3), instrument["private"], 1, 0),
        (hour(4), None, 1, 1),
    ]
    # A failed calibration lists no lines, and is no fit that does not.
    assert data["bins"][3]["n_without_lines"] == 0
    assert data["bins"][3]["post_fit_ppm"] is None
    assert data["bins"][0]["axis_offset_ppm"] == pytest.approx([-81.0] * 3)
    lines = {line["ion"]: line for line in data["calibrants"]}
    assert [line["target_mz"] for line in data["calibrants"]] == [61.9884, 236.7536]
    assert lines["NO3-"]["residual_ppm"] == [[1.0] * 3, [0.5] * 3, [1.0] * 3, None]
    assert lines["Br3-"]["n_kept"] == [0, 0, 0, 0]
    assert lines["Br3-"]["n_listed"] == [1, 1, 1, 0]
    assert [(v["isotopes_sha256"], v["n_files"]) for v in data["collections"]] == [
        (_V1, 1),
        (_V2, 1),
        (_V1, 1),
    ]
    assert data["collections"][2]["name"] is None


@pytest.mark.asyncio
async def test_a_window_of_as_many_files_as_the_trend_draws_is_served_whole(
    guest_client, instrument, monkeypatch
):
    """Four files on the trend, and three in the window no trend reads: they
    are not counted."""
    monkeypatch.setattr(calibration_trend, "TREND_MAX_FILES", 4)

    data = await _trend(guest_client, _window(instrument))

    assert (data["binned"], data["n_files"], len(data["files"])) == (False, 4, 4)


@pytest.mark.asyncio
async def test_a_bin_serves_the_quartiles_of_its_files(owner_client, busy, binned):
    data = await _trend(owner_client, _window(busy))

    first, older, unhashed = data["bins"]
    assert first["start_utc"] == (_W0 + timedelta(hours=1)).isoformat()
    assert (first["collection_id"], first["n_files"]) == (busy["collection"], 5)
    assert first["axis_offset_ppm"] == pytest.approx([-82.0, -80.0, -78.0])
    assert first["post_fit_ppm"] == pytest.approx([0.4, 0.6, 0.8])
    # Of the counts of kept lines, only the two whole numbers.
    assert first["n_points"] == [1, 1, 1]
    assert first["n_candidates"] == [2, 2, 2]
    assert first["n_without_lines"] == 0
    # Fitted before records listed their lines or named their collection.
    assert older["start_utc"] == (_W0 + timedelta(hours=3)).isoformat()
    assert (older["collection_id"], older["n_without_lines"]) == (None, 1)
    assert older["axis_offset_ppm"] is None
    assert older["n_candidates"] is None
    assert older["post_fit_ppm"] == pytest.approx([1.5] * 3)
    # A collection named without its hash is none, as on the files.
    assert unhashed["collection_id"] is None
    # The m/z written to a fifth decimal is the same line.
    nitrate, bromide = data["calibrants"]
    assert nitrate["residual_ppm"][0] == pytest.approx([0.4, 0.6, 0.8])
    assert (nitrate["n_listed"], nitrate["n_kept"]) == ([5, 0, 1], [5, 0, 1])
    assert bromide["residual_ppm"] == [[25.0] * 3, None, [25.0] * 3]
    assert (bromide["n_listed"], bromide["n_kept"]) == ([5, 0, 1], [0, 0, 0])


@pytest.mark.asyncio
async def test_a_count_of_kept_lines_that_is_no_whole_number_is_none(
    owner_client, busy
):
    """One by one, as in its bin."""
    data = await _trend(owner_client, _window(busy))

    counts = dict(zip(_ids(busy, data), (f["n_points"] for f in data["files"])))
    assert [counts[f"b{i}"] for i in range(5)] == [1, 1, None, None, None]


@pytest.mark.asyncio
async def test_a_window_longer_than_a_month_is_binned_per_day(
    guest_client, instrument, binned
):
    data = await _trend(
        guest_client, _window(instrument, start=_W0 - timedelta(days=40))
    )

    assert data["bin"] == "day"
    day = lambda d: (_W0 + timedelta(days=d)).isoformat()  # noqa: E731
    files = {(b["start_utc"], b["collection_id"]): b["n_files"] for b in data["bins"]}
    assert files == {
        (day(-5), None): 1,
        (day(-2), instrument["global"]): 1,
        (day(0), instrument["global"]): 2,
        (day(0), instrument["private"]): 1,
        (day(0), None): 1,
    }


@pytest.mark.asyncio
async def test_a_window_with_too_many_files_bins_its_latest(
    guest_client, instrument, binned, monkeypatch
):
    """Its latest files on the trend: the three later files no trend reads
    take none of the places. And it reads its changes from the files before
    the latest it serves: from the start of the window, nothing before V2
    would say it is an edit."""
    monkeypatch.setattr(calibration_trend, "TREND_MAX_BINNED_FILES", 3)

    data = await _trend(
        guest_client, _window(instrument, start=_W0 - timedelta(days=3))
    )

    assert (data["truncated"], data["n_files"]) == (True, 3)
    assert [b["start_utc"] for b in data["bins"]] == [
        (_W0 + timedelta(hours=h)).isoformat() for h in (2, 3, 4)
    ]
    assert [
        (change["kind"], change["previous_isotopes_sha256"])
        for change in data["changes"]
    ] == [("edited", _V1), ("first_used", None)]


@pytest.mark.asyncio
async def test_a_binned_window_of_as_many_files_as_it_reads_is_whole(
    guest_client, instrument, binned, monkeypatch
):
    monkeypatch.setattr(calibration_trend, "TREND_MAX_BINNED_FILES", 5)

    data = await _trend(
        guest_client, _window(instrument, start=_W0 - timedelta(days=3))
    )

    assert (data["truncated"], data["n_files"]) == (False, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start", [timedelta(hours=h) for h in (0, 1.5, 2.5, -72, -240)], ids=str
)
async def test_bins_mark_the_changes_the_files_do(
    guest_client, instrument, monkeypatch, start
):
    """The changes of a binned trend are placed from the runs of files of one
    version, and must be those its files one by one give: every case the
    tests above pin on the files."""
    params = _window(instrument, start=_W0 + start)
    files = await _trend(guest_client, params)
    monkeypatch.setattr(calibration_trend, "TREND_MAX_FILES", 0)

    bins = await _trend(guest_client, params)

    assert (files["binned"], bins["binned"]) == (False, True)
    assert bins["changes"] == files["changes"]
    assert bins["collections"] == files["collections"]
    assert _lines(bins) == _lines(files)


@pytest.mark.asyncio
@pytest.mark.parametrize("start", [timedelta(0), timedelta(hours=2, minutes=30)])
async def test_bins_mark_each_edit_the_files_do(
    owner_client, edits, monkeypatch, start
):
    params = _window(edits, start=_W0 + start)
    files = await _trend(owner_client, params)
    monkeypatch.setattr(calibration_trend, "TREND_MAX_FILES", 0)

    bins = await _trend(owner_client, params)

    assert bins["binned"] is True
    assert bins["changes"] == files["changes"]
    assert bins["collections"] == files["collections"]


@pytest.mark.asyncio
async def test_bins_mark_a_collection_of_the_same_isotopes_taking_over(
    owner_client, copies, monkeypatch
):
    """The second collection takes over with the first's hash: a run of
    files begins where the collection changes, not only the hash; and the
    mark is on the run's first file, not its last. A line whose ion is no
    text is the same line without an ion, in bins as on the files."""
    files = await _trend(owner_client, _window(copies))
    monkeypatch.setattr(calibration_trend, "TREND_MAX_FILES", 0)

    bins = await _trend(owner_client, _window(copies))

    assert [(c["kind"], c["collection_id"]) for c in files["changes"]] == [
        ("first_used", copies["second"])
    ]
    assert _ids(copies, files, "changes") == ["b1"]
    assert bins["binned"] is True
    assert bins["changes"] == files["changes"]
    assert bins["collections"] == files["collections"]
    assert _lines(files) == [("NO3-", 61.9884), ("Br3-", 236.7536), (None, 236.7536)]
    assert _lines(bins) == _lines(files)


@pytest.mark.asyncio
async def test_a_collection_first_in_the_window_is_new_after_files_before_it(
    guest_client, instrument
):
    """The window opens on the private set; files before it named another."""
    data = await _trend(
        guest_client,
        _window(instrument, start=_W0 + timedelta(hours=2, minutes=30)),
    )

    assert _ids(instrument, data) == ["private", "failed"]
    assert [change["kind"] for change in data["changes"]] == ["first_used"]


@pytest.mark.asyncio
async def test_each_edit_names_the_version_just_before_it(owner_client, edits):
    """Three versions in the window: the third was edited from the second,
    not from the first."""
    data = await _trend(owner_client, _window(edits))

    assert _ids(edits, data) == ["e1", "e2", "e3"]
    assert _edits(data) == [("edited", _V2, _V1), ("edited", _V3, _V2)]
    assert _ids(edits, data, "changes") == ["e2", "e3"]


@pytest.mark.asyncio
async def test_an_edit_names_the_latest_version_before_the_window(owner_client, edits):
    """Two versions before the window, the third in it: it was edited from
    the second, the later of the two."""
    data = await _trend(
        owner_client, _window(edits, start=_W0 + timedelta(hours=2, minutes=30))
    )

    assert _ids(edits, data) == ["e3"]
    assert _edits(data) == [("edited", _V3, _V2)]


@pytest.mark.asyncio
async def test_no_start_reads_ninety_days_before_the_end(guest_client, instrument):
    data = await _trend(
        guest_client,
        {"instrument": instrument["name"], "polarity": "-", "datetime_max": _W1},
    )

    start = datetime.fromisoformat(data["datetime_min"])
    assert start == _W1 - timedelta(days=calibration_trend.TREND_DEFAULT_DAYS)
    assert _ids(instrument, data)[0] == "older"


@pytest.mark.asyncio
async def test_a_plus_sign_in_the_query_is_the_positive_polarity(
    guest_client, instrument
):
    """A bare ``+`` in a query string reads as a space; it is the polarity."""
    response = await guest_client.get(
        f"/api/calibration/trend?instrument={quote(instrument['name'])}&polarity=+"
        f"&datetime_min=2026-10-18T00:00:00Z&datetime_max=2026-10-20T00:00:00Z"
    )

    assert response.status_code == 200, response.text
    assert _ids(instrument, response.json()["data"]) == ["positive"]


@pytest.mark.asyncio
async def test_a_window_that_ends_before_it_starts_is_refused(guest_client, instrument):
    response = await guest_client.get(
        "/api/calibration/trend",
        params=_window(instrument, start=_W1, end=_W0),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_user_outside_the_instruments_workspace_is_refused(
    editor_client, instrument
):
    for path in ("/api/calibration/trend", "/api/calibration/trend/export"):
        response = await editor_client.get(path, params=_window(instrument))

        assert response.status_code == 403, path


@pytest.mark.asyncio
async def test_the_export_lists_each_files_lines(guest_client, instrument):
    response = await guest_client.get(
        "/api/calibration/trend/export", params=_window(instrument)
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    safe = instrument["name"].replace(" ", "_")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="calibration-trend-{safe}-neg.csv"'
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [(row["sample_file_id"], row["ion"]) for row in rows] == [
        (instrument["files"]["v1"], "NO3-"),
        (instrument["files"]["v1"], "Br3-"),
        (instrument["files"]["v2"], "NO3-"),
        (instrument["files"]["v2"], "Br3-"),
        (instrument["files"]["private"], "NO3-"),
        (instrument["files"]["private"], "Br3-"),
        (instrument["files"]["failed"], ""),
    ]
    assert rows[0]["collection_name"] == "Trend global set"
    assert rows[4]["collection_name"] == ""
    assert rows[2]["collection_isotopes_sha256"] == _V2


@pytest.mark.asyncio
async def test_the_export_lists_the_files_where_the_trend_bins_them(
    guest_client, instrument, binned
):
    response = await guest_client.get(
        "/api/calibration/trend/export", params=_window(instrument)
    )

    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    # Two lines a fit, and one row for the failed calibration.
    assert [row["sample_file_id"] for row in rows[::2]] == [
        instrument["files"][key] for key in ("v1", "v2", "private", "failed")
    ]
    assert rows[0]["collection_name"] == "Trend global set"


@pytest.mark.asyncio
async def test_the_export_lists_the_latest_files_of_a_window_with_more(
    guest_client, instrument, monkeypatch
):
    monkeypatch.setattr(calibration_trend, "EXPORT_MAX_FILES", 2)

    response = await guest_client.get(
        "/api/calibration/trend/export", params=_window(instrument)
    )

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["sample_file_id"] for row in rows] == [
        instrument["files"]["private"],
        instrument["files"]["private"],
        instrument["files"]["failed"],
    ]


# --- The acquisition axis a record keeps (MAS-937) ---

#: The acquisition axis of :func:`_record`'s fit, read 80 ppm low: a mode-0
#: m/z goes with ``1 / p1 ** 2``.
_LOW = 2800.0 / (1 - 80e-6) ** 0.5


def _refit(collection_id, sha, **keys):
    """A refit's record: its lines observed near 0 ppm on the axis the fit
    before set, and ``keys`` of what it kept of the acquisition axis."""
    record = _record(collection_id, sha, offset_ppm=0.2)
    record.update(keys)
    return record


@pytest_asyncio.fixture
async def axes(async_session_factory):
    """A TOF instrument's files an hour apart, each a way a record says how its
    acquisition axis read the lines: a first fit from before the keys, read
    80 ppm low; refits that kept the axis, in mode 0 and, counted from 500
    samples into the raw axis, in mode 2; a mode-1 fit's that kept a mode-1
    axis, and one that kept its axis as mode 5; one that kept the first
    fit's offsets; three whose axis gives no m/z (a zero p1, a fractional
    power of a negative number, a mode with no closed form) and one whose
    fit time is not one; a new version of the collection, refitted onto the
    first file acquired and, a week earlier, onto the last; and a fit that
    chose from no lines."""
    name = f"trend-axes-{gen_id(6).lower()}"
    cid = gen_id(16)
    axis0 = {"mode": 0, "par": [_LOW, -0.5], "raw_index_offset": 0}
    mode2_fit = {"mode": 2, "par": [2800.0, -500.5, 0.5]}
    axis2 = {"mode": 2, "par": [_LOW, -0.5, 0.5], "raw_index_offset": 500}
    # i = p1 / sqrt(m) + p2, and the axis's m/z (p1 / (i - p2)) ** 2
    mode1_fit = {"mode": 1, "par": [2800.0, -50.0]}
    axis1 = {
        "mode": 1,
        "par": [2800.0 * (1 - 80e-6) ** 0.5, -50.0],
        "raw_index_offset": 0,
    }
    # The mode-0 axis ((i + 500.5) / _LOW) ** 2 of a fit counting from -500.5,
    # written out as p1 * i ** 2 + p2 * i + p3
    mode5_fit = {"mode": 0, "par": [2800.0, -500.5]}
    axis5 = {
        "mode": 5,
        "par": [1 / _LOW**2, 1001.0 / _LOW**2, 500.5**2 / _LOW**2],
        "raw_index_offset": 0,
    }
    kept = [
        {"ion": "NO3-", "target_mz": 61.9884, "offset_ppm": -79.0},
        {"ion": "Br3-", "target_mz": "236.7536", "offset_ppm": 1.0},
        {"ion": "Br3-", "target_mz": 236.7536, "offset_ppm": -60.0},
    ]
    records = {
        "late": _refit(
            cid, _V2, acquisition_axis=axis0, fitted_at="2026-11-17T08:00:00+00:00"
        ),
        "first": _record(cid, _V1),
        "axis": _refit(cid, _V1, acquisition_axis=axis0),
        "mode2": {**_refit(cid, _V1, acquisition_axis=axis2), **mode2_fit},
        "mode1": {**_refit(cid, _V1, acquisition_axis=axis1), **mode1_fit},
        "mode5": {**_refit(cid, _V1, acquisition_axis=axis5), **mode5_fit},
        "kept": _refit(cid, _V1, acquisition_offsets=kept),
        "zero": _refit(
            cid,
            _V1,
            acquisition_axis={**axis0, "par": [0.0, -0.5]},
            fitted_at="yesterday",
        ),
        "negative": _refit(
            cid,
            _V1,
            acquisition_axis={
                "mode": 2,
                "par": [_LOW, 1e9, 0.5],
                "raw_index_offset": 0,
            },
        ),
        "mode3": _refit(
            cid,
            _V1,
            acquisition_axis={"mode": 3, "par": [1, 2, 3, 4], "raw_index_offset": 0},
        ),
        "early": _refit(
            cid, _V2, acquisition_axis=axis0, fitted_at="2026-11-10T08:00:00+00:00"
        ),
        "nolines": _refit(cid, _V1, acquisition_axis=axis0),
    }
    records["nolines"]["quality"]["points"] = []
    files: dict[str, str] = {}
    async with async_session_factory() as session:
        for hour, (key, record) in enumerate(records.items(), start=1):
            files[key] = gen_id(16)
            at = _W0 + timedelta(hours=hour)
            session.add(
                SampleFile(
                    sample_file_id=files[key],
                    filename=f"{name}_{key}.h5",
                    instrument=name,
                    instrument_type="tof",
                    datetime=at.replace(tzinfo=None),
                    datetime_utc=at,
                    length=60.0,
                    range=[10.0, 600.0],
                    polarity="-",
                    mz_calibration=record,
                )
            )
        await session.commit()
    yield {"name": name, "files": files, "collection": cid}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.commit()


def _rounded(values):
    return [None if value is None else round(value, 6) for value in values]


def _medians(quartiles):
    return _rounded([None if q is None else q[1] for q in quartiles])


#: What each file of :func:`axes` reads, its kept line and its other line.
_AXES_NITRATE = [*[-80.0] * 6, -79.0, None, None, None, -80.0, None]
_AXES_BROMIDE = [*[-80.0] * 6, -60.0, None, None, None, -80.0, None]


@pytest.mark.asyncio
async def test_a_refit_reads_the_acquisition_axis_its_record_kept(owner_client, axes):
    data = await _trend(owner_client, _window(axes))

    assert _ids(axes, data) == list(axes["files"])
    lines = {line["ion"]: line for line in data["calibrants"]}
    assert _rounded(lines["NO3-"]["offset_ppm"]) == _AXES_NITRATE
    assert _rounded(lines["Br3-"]["offset_ppm"]) == _AXES_BROMIDE
    assert _rounded([f["axis_offset_ppm"] for f in data["files"]]) == _AXES_NITRATE
    assert [f["fitted_utc"] for f in data["files"]] == [
        "2026-11-17T08:00:00+00:00",
        *[None] * 9,
        "2026-11-10T08:00:00+00:00",
        None,
    ]
    assert data["files"][-1]["n_candidates"] == 0


@pytest.mark.asyncio
async def test_bins_read_the_acquisition_axis_as_the_files_do(
    owner_client, axes, binned
):
    """One file a bin: each bin's median is its file's reading."""
    data = await _trend(owner_client, _window(axes))

    assert data["binned"] is True
    assert _medians([b["axis_offset_ppm"] for b in data["bins"]]) == _AXES_NITRATE
    # A fit that chose from no lines chose from none, not from unknown ones
    assert data["bins"][-1]["n_candidates"] == [0.0, 0.0, 0.0]
    assert data["bins"][-1]["n_without_lines"] == 0
    lines = {line["ion"]: line for line in data["calibrants"]}
    assert _medians(lines["NO3-"]["offset_ppm"]) == _AXES_NITRATE
    assert _medians(lines["Br3-"]["offset_ppm"]) == _AXES_BROMIDE


@pytest.mark.asyncio
async def test_a_change_is_placed_when_its_version_was_fitted(
    owner_client, axes, monkeypatch
):
    """V2 was refitted onto the first file acquired and, earlier, onto the
    last: V2 is the edit, made when its first record was written, on the
    last file, served alike in bins."""
    files = await _trend(owner_client, _window(axes))
    monkeypatch.setattr(calibration_trend, "TREND_MAX_FILES", 0)
    bins = await _trend(owner_client, _window(axes))

    assert files["changes"] == [
        {
            "datetime_utc": "2026-11-10T08:00:00+00:00",
            "sample_file_id": axes["files"]["early"],
            "kind": "edited",
            "collection": 0,
            "collection_id": axes["collection"],
            "isotopes_sha256": _V2,
            "previous_isotopes_sha256": _V1,
        }
    ]
    assert bins["changes"] == files["changes"]
    assert bins["collections"] == files["collections"]
