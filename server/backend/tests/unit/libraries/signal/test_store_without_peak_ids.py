"""A peak store that keeps no peak ids is a stale peak store (MAS-795).

Peak ids were added to peak stores with a migration, and a store written
before them that the migration missed has no ``peak_id`` coordinate. Every
read that names a peak names it by its id, so such a store is refused as
stale (``mascope_signal.compute.check_peak_ids``): the peaks read answers as
it does for any stale store, the matcher raises the class so a batch refresh
counts the sample under the stale-store reason and queues its rebuild, and
``rebuild_stale_peak_stores`` counts and rebuilds it.

The stores here are real: detected from a scripted acquisition and written
again by the library's own writer with the coordinate dropped.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from scripted_acquisition import SAMPLE_FILENAME
from test_utils import captured_logs

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_backend.api.controllers.sample.batches.lib import util as batch_util
from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.lib.stale_peak_store import is_stale_peak_store
from mascope_backend.api.models.samples.sample_pydantic_model import (
    GetSamplePeaksQueryParams,
    GetSamplePeakTimeseriesBody,
)
from mascope_backend.api.routes.samples.samples_routes import (
    get_sample_peak_timeseries_route,
    get_sample_peaks_route,
)
from mascope_backend.db.scripts import rebuild_stale_peak_stores as script
from mascope_match import compute_match_isotopes


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
SCANS = [(NEG, 1, {62.0: 100.0, 125.0: 10.0, 188.0: 5.0})] * 6
USER = SimpleNamespace(to_dict=lambda: {"id": "user_1"})
NO_IDS = "keeps no peak ids"


def _store():
    return m_io.load_array(SAMPLE_FILENAME, var="peak_timeseries")


@pytest.fixture
def detected(acquire, instrument_functions):
    """The scripted file, its peaks detected: a store with ids."""
    acquire(SCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert "peak_id" in _store().coords


@pytest.fixture
def without_ids(detected):
    """The same store written again without its peak ids, as one written
    before they were kept reads."""
    store = _store().load()
    asyncio.run(
        m_io.write_peaks(store.drop_vars("peak_id"), SAMPLE_FILENAME, overwrite=True)
    )
    assert "peak_id" not in _store().coords


@pytest.fixture
def a_scan_long(detected, acquire):
    """A store with ids that holds a scan its file no longer reads: what the
    reads already refuse as stale."""
    acquire(SCANS[1:])


# --- the check ----------------------------------------------------------------


def test_a_store_with_its_ids_passes(detected):
    data = m_io.load_peak_data(SAMPLE_FILENAME)

    assert m_compute.check_peak_ids(data) is None
    assert m_compute.check_peak_ids(data.peak_heights) is None


def test_a_store_without_them_is_stale(without_ids):
    data = m_io.load_peak_data(SAMPLE_FILENAME)

    for peaks in (data, data.peak_heights):
        with pytest.raises(m_compute.StalePeakStoreError, match=NO_IDS):
            m_compute.check_peak_ids(peaks)


# --- the peaks read -----------------------------------------------------------


def _sample():
    time = _store().time.values
    return SimpleNamespace(
        sample_item_id="si_1",
        sample_item_name="Sample 1",
        filename=SAMPLE_FILENAME,
        t0=float(time[0]),
        t1=float(time[-1]),
        polarity="-",
    )


async def _peaks(time_range: bool):
    sample = _sample()
    query = {"t_min": sample.t0, "t_max": sample.t1} if time_range else {}
    with (
        patch.object(
            samples_controller, "fetch_sample", AsyncMock(return_value=sample)
        ),
        patch.object(samples_controller, "_segments_of", AsyncMock(return_value=None)),
    ):
        response = await get_sample_peaks_route(
            sample_item_id="si_1",
            query_params=GetSamplePeaksQueryParams(**query),
            user=USER,
            membership=None,
        )
    return response.status_code, json.loads(response.body)


@pytest.mark.parametrize("time_range", [False, True], ids=["whole", "time-range"])
def test_the_peaks_read_answers_as_for_any_stale_store(time_range, request, detected):
    """The answer a store the reads already refuse as stale gets, a 400 with
    the stale-store message, never a 500 with an AttributeError."""
    status, body = asyncio.run(_peaks(time_range))
    assert status == 200
    # A time range lists the peaks whose timeseries are computed: none yet
    assert body["results"] == (0 if time_range else 3)

    request.getfixturevalue("without_ids")
    status, body = asyncio.run(_peaks(time_range))
    assert status == 400
    assert NO_IDS in body["error"]
    assert "Re-run peak detection" in body["error"]


def test_a_store_a_scan_long_gets_the_same_answer(a_scan_long):
    """What the answer above is held to: the one the peaks read already
    gives a store whose sums its file no longer reads back."""
    status, body = asyncio.run(_peaks(time_range=False))

    assert status == 400
    assert "Re-run peak detection" in body["error"]


def test_the_timeseries_of_a_peak_by_its_id_answers_the_same(without_ids):
    sample = _sample()
    with patch.object(
        samples_controller, "fetch_sample", AsyncMock(return_value=sample)
    ):
        response = asyncio.run(
            get_sample_peak_timeseries_route(
                sample_item_id="si_1",
                body=GetSamplePeakTimeseriesBody(peak_id="peak_1"),
                user=USER,
                membership=None,
            )
        )

    assert response.status_code == 400
    assert NO_IDS in json.loads(response.body)["error"]


def test_the_file_peaks_read_refuses_it(without_ids, monkeypatch):
    """Every peak of the file, from its stored sums: named by their ids."""

    async def _get_sample_file(_sample_file_id):
        return {"data": {"filename": SAMPLE_FILENAME}}

    monkeypatch.setattr(sample_files_controller, "get_sample_file", _get_sample_file)
    with pytest.raises(ApiException) as raised:
        asyncio.run(
            sample_files_controller.get_sample_file_peaks(
                "file-1", areas=True, heights=True
            )
        )

    assert raised.value.status_code == 400
    assert is_stale_peak_store(raised.value)


def test_a_batch_alignment_refuses_it(without_ids):
    """A batch's aligned peaks read each sample's store by its ids."""
    with pytest.raises(m_compute.StalePeakStoreError, match=NO_IDS):
        batch_util._sync_load_peak_data(SAMPLE_FILENAME, "-", "sum_peak_heights")


def test_a_fill_of_its_timeseries_refuses_it(without_ids):
    """A fill reads the ids of the peaks it computes, to find them again on
    a rewritten axis; a read of computed timeseries alone needs none."""
    with pytest.raises(m_compute.StalePeakStoreError, match=NO_IDS):
        asyncio.run(m_compute.load_peak_timeseries(SAMPLE_FILENAME, [62.0]))


# --- the matcher --------------------------------------------------------------


def _match():
    targets = pd.DataFrame(
        {
            "target_ion_id": ["ion"] * 2,
            "target_isotope_id": ["m0", "m1"],
            "mz": [62.0, 125.0],
            "relative_abundance": [1.0, 0.1],
        }
    )
    return asyncio.run(compute_match_isotopes(SAMPLE_FILENAME, targets, polarity="-"))


def test_the_matcher_matches_a_store_with_its_ids(detected):
    frame = _match()

    assert frame["sample_peak_id"].notna().all()


def test_the_matcher_refuses_it_as_stale(without_ids, monkeypatch):
    """Before its timeseries are loaded, so that nothing past the load meets
    peaks without ids; and on the chain a batch refresh reads its failures
    by (``is_stale_peak_store``)."""

    async def unreached(*_args, **_kwargs):
        raise AssertionError("the timeseries of a store without ids were loaded")

    monkeypatch.setattr(m_compute, "load_peak_timeseries", unreached)

    with pytest.raises(ValueError) as raised:
        _match()

    assert is_stale_peak_store(raised.value)
    assert NO_IDS in str(raised.value)


# --- the script ---------------------------------------------------------------


def test_the_script_finds_it_stale(without_ids):
    assert script.check(SAMPLE_FILENAME) == script.Checked(
        script.STALE, why=script.NO_PEAK_IDS
    )


def test_an_empty_store_without_ids_is_stale_too(acquire):
    """A blank's store is no less refused by the reads."""
    acquire(SCANS)
    m_peak.write_empty_peak_timeseries(SAMPLE_FILENAME)
    store = _store().load()
    asyncio.run(
        m_io.write_peaks(store.drop_vars("peak_id"), SAMPLE_FILENAME, overwrite=True)
    )

    assert script.check(SAMPLE_FILENAME).state == script.STALE


def test_its_dry_run_counts_it_and_a_run_rebuilds_it(without_ids, instrument_functions):
    candidates = [
        {"sample_file_id": "sf-1", "filename": SAMPLE_FILENAME, "instrument": "X"}
    ]
    stale, counts, _ = asyncio.run(
        script.survey(candidates, lambda _filename: True, script.check)
    )

    assert counts["X"] == {script.STALE: 1}
    assert [(file["state"], file["why"]) for file in stale] == [
        (script.STALE, script.NO_PEAK_IDS)
    ]

    async def no_samples(_sample_file_id):
        return [], []

    with captured_logs() as logs:
        outcome = asyncio.run(
            script.rebuild_all(stale, script.Steps(samples_of=no_samples))
        )
    assert outcome.found == 1
    assert any(f"{SAMPLE_FILENAME}: {NO_IDS};" in record["message"] for record in logs)
    assert "peak_id" not in _store().coords  # a dry run writes nothing

    # What a run's rebuild does: a detection, which gives the peaks ids
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert script.check(SAMPLE_FILENAME) == script.Checked(script.CURRENT)
