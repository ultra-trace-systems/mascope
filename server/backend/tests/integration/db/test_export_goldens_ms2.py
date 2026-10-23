"""
The MS2 golden export (``export_goldens.get_golden_ms2``), against the database.

The export reads which samples hold fragmentation scans off each file's
scan-stream census and the spectra through the same compute path as the MS2
routes; both are stubbed here, since the test database holds no raw file.
What is real is the read of the samples - their file, polarity and window -
and the shape of the rows: one per centroid at or above the peak store's
floor, in a stable order, keyed by the group the MS2 routes key by, once per
file and polarity however many items read the same scans.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import pytest_asyncio

from mascope_backend.db import Dataset, SampleBatch, SampleFile, SampleItem, Workspace
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import export_goldens
from mascope_signal.peak import SIGNAL_TO_NOISE_THRESHOLD


_MS2_FILE = "Orbion_2026.10.15-03h49m40s_MS2_15NO3_manual-C10H18O2_20261015034940"
_MS1_FILE = "Orbion_2025.08.11-14h23m50s_neg_Br_NoRI_20250811142350"

_CENSUS = {
    # MS1 scans and, in the negative polarity, fragmentation scans.
    _MS2_FILE: [
        {"signature": {"polarity": "-", "ms_order": 1}},
        {"signature": {"polarity": "-", "ms_order": 2}},
    ],
    # MS1 only: the demo's own acquisitions.
    _MS1_FILE: [{"signature": {"polarity": "-", "ms_order": 1}}],
}


def _spectrum(*centroids: tuple[float, float, float]):
    """``(masses, intensities, resolutions, signal_to_noise)`` from
    ``(mz, intensity, snr)`` triples, in the order given."""
    masses = np.array([c[0] for c in centroids])
    intensities = np.array([c[1] for c in centroids])
    snr = np.array([c[2] for c in centroids])
    return masses, intensities, np.full(masses.shape, 60000.0), snr


# One precursor at two steps. The hcd15 spectrum is given out of m/z order
# and carries one centroid under the floor; the hcd1 spectrum sorts after it
# by activation text, but is a lower energy.
_GROUPS = {
    (233.116, "hcd15.00"): _spectrum(
        (213.0007, 203.0, 12.0),
        (62.9862, 1512.0, 40.0),
        (112.987, 207.0, SIGNAL_TO_NOISE_THRESHOLD - 0.5),
    ),
    (233.116, "hcd1.00"): _spectrum((62.9862, 1112.0, 30.0)),
}

_SUMMARY = {
    "parent_peaks": [233.116],
    "groups": [
        {
            "parent_peak_mz": 233.116,
            "activation": "hcd1.00",
            "scan_count": 8,
            "isolation_width": 1.0,
        },
        {
            "parent_peak_mz": 233.116,
            "activation": "hcd15.00",
            "scan_count": 13,
            "isolation_width": 1.0,
        },
    ],
    "ms1_scan_count": 19,
    "ms2_scan_count": 33,
}


@pytest_asyncio.fixture(scope="module")
async def demo_rows(async_session_factory) -> None:
    """Two files in one batch: an MS2 acquisition read by two items over the
    same scans, and an MS1-only acquisition."""
    now = datetime.now(timezone.utc)
    workspace_id, dataset_id, batch_id = gen_id(), gen_id(), gen_id()
    ms2_file_id, ms1_file_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name="MS2 golden export test workspace",
                workspace_status="active",
                workspace_utc_created=now,
                workspace_utc_modified=now,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="MS2 golden export test dataset",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name="MS2 golden export test batch",
                sample_batch_utc_created=now,
            )
        )
        for file_id, filename, length in (
            (ms2_file_id, _MS2_FILE, 142.0),
            (ms1_file_id, _MS1_FILE, 10.0),
        ):
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=filename,
                    instrument="Orbion",
                    datetime=datetime(2026, 10, 15, 3, 49, 40),
                    datetime_utc=now,
                    length=length,
                    range=[40.0, 600.0],
                    polarity="-",
                )
            )
        for file_id, name in (
            (ms2_file_id, "MS2 sample"),
            (ms2_file_id, "MS2 sample, a second item over the same scans"),
            (ms1_file_id, "MS1 sample"),
        ):
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=batch_id,
                    sample_file_id=file_id,
                    sample_item_name=name,
                    sample_item_type="sample",
                    polarity="-",
                    t0=0.0,
                    t1=142.0,
                    sample_item_utc_created=now,
                )
            )
        await session.commit()


async def _census(filename: str):
    return _CENSUS.get(filename, [])


@pytest.mark.asyncio
async def test_ms2_rows_are_one_per_centroid_at_the_floor_in_a_stable_order(
    demo_rows,
):
    centroids = AsyncMock(return_value=_GROUPS)
    summary = AsyncMock(return_value=_SUMMARY)
    with (
        # The test database is bound by the conftest; the export's own engine
        # configuration would rebind it to the runtime's.
        patch.object(export_goldens, "configure_database_engine", AsyncMock()),
        patch(
            "mascope_backend.api.controllers.sample.files.process.status.read_scan_streams",
            side_effect=_census,
        ),
        patch("mascope_signal.compute.get_orbi_ms2_centroids_by_parent", centroids),
        patch("mascope_signal.compute.get_ms2_summary", summary),
    ):
        rows = await export_goldens._query_golden_ms2()

    # The MS1-only file opened nothing; the MS2 file was read once for its two
    # items, through the items' window and polarity.
    assert centroids.await_count == 1
    assert centroids.await_args.args == (_MS2_FILE,)
    assert centroids.await_args.kwargs == {
        "t_min": 0.0,
        "t_max": 142.0,
        "polarity": "-",
        "by_activation": True,
    }
    assert summary.await_count == 1

    # Groups in key order, centroids by m/z within a group, the one under the
    # floor left out; the group's scan count and width ride on every row.
    assert [(r["activation"], r["mz"]) for r in rows] == [
        ("hcd1.00", 62.9862),
        ("hcd15.00", 62.9862),
        ("hcd15.00", 213.0007),
    ]
    assert {r["filename"] for r in rows} == {_MS2_FILE}
    assert {r["polarity"] for r in rows} == {"-"}
    assert {r["parent_peak_mz"] for r in rows} == {233.116}
    hcd15 = [r for r in rows if r["activation"] == "hcd15.00"]
    assert {r["scan_count"] for r in hcd15} == {13}
    assert {r["isolation_width"] for r in hcd15} == {1.0}
    assert hcd15[0]["intensity"] == 1512.0
    assert hcd15[0]["signal_to_noise"] == 40.0
    assert hcd15[0]["resolution"] == 60000.0
    assert set(rows[0]) == {
        "filename",
        "polarity",
        "parent_peak_mz",
        "activation",
        "scan_count",
        "isolation_width",
        "mz",
        "intensity",
        "resolution",
        "signal_to_noise",
    }


@pytest.mark.asyncio
async def test_a_centroid_on_the_floor_is_kept(demo_rows):
    """The floor is inclusive, as the peak store's is."""
    on_floor = {
        (233.116, "hcd15.00"): _spectrum(
            (62.9862, 1512.0, float(SIGNAL_TO_NOISE_THRESHOLD))
        )
    }
    with (
        patch.object(export_goldens, "configure_database_engine", AsyncMock()),
        patch(
            "mascope_backend.api.controllers.sample.files.process.status.read_scan_streams",
            side_effect=_census,
        ),
        patch(
            "mascope_signal.compute.get_orbi_ms2_centroids_by_parent",
            AsyncMock(return_value=on_floor),
        ),
        patch(
            "mascope_signal.compute.get_ms2_summary", AsyncMock(return_value=_SUMMARY)
        ),
    ):
        rows = await export_goldens._query_golden_ms2()

    assert [r["mz"] for r in rows] == [62.9862]


@pytest.mark.asyncio
async def test_a_group_the_summary_does_not_describe_has_no_scan_count(demo_rows):
    """A spectrum whose group the summary left out keeps its centroids and
    records no scan count or width, rather than failing the export."""
    with (
        patch.object(export_goldens, "configure_database_engine", AsyncMock()),
        patch(
            "mascope_backend.api.controllers.sample.files.process.status.read_scan_streams",
            side_effect=_census,
        ),
        patch(
            "mascope_signal.compute.get_orbi_ms2_centroids_by_parent",
            AsyncMock(return_value=_GROUPS),
        ),
        patch(
            "mascope_signal.compute.get_ms2_summary",
            AsyncMock(return_value={"groups": []}),
        ),
    ):
        rows = await export_goldens._query_golden_ms2()

    assert len(rows) == 3
    assert {r["scan_count"] for r in rows} == {None}
    assert {r["isolation_width"] for r in rows} == {None}


@pytest.mark.asyncio
async def test_an_ms1_only_database_exports_nothing_and_opens_no_file(demo_rows):
    """With no fragmentation scans in any census, no spectrum is read."""
    centroids = AsyncMock(return_value=_GROUPS)
    with (
        patch.object(export_goldens, "configure_database_engine", AsyncMock()),
        patch(
            "mascope_backend.api.controllers.sample.files.process.status.read_scan_streams",
            AsyncMock(return_value=_CENSUS[_MS1_FILE]),
        ),
        patch("mascope_signal.compute.get_orbi_ms2_centroids_by_parent", centroids),
    ):
        rows = await export_goldens._query_golden_ms2()

    assert rows == []
    assert centroids.await_count == 0
