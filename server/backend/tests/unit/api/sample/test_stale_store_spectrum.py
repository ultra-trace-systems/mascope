"""A spectrum route that meets a stale peak store asks for its rebuild, and
one stale sample does not cost the others their spectra.

A per-stream store the file no longer reads back is refused by the spectrum
read (``get_sample_sum_signal``). A match that meets such a store asks the
file converter to detect the file's peaks again; so does a spectrum now,
for whoever may rematch the sample, and the answer says so. The route for
several samples answers the ones it can and lists the stale ones
(``spectrum_service``; ``docs/dev/ingest_routing_and_splitting.md``,
section 10, step 6).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import xarray as xr

from mascope_backend.api.controllers.samples import (
    samples_controller,
    spectrum_service,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_signal.compute import StalePeakStoreError


STALE = (
    "The peak store holds the peaks of scan stream 'low window', and the "
    "sample file now reads back no stream under that key. Re-run peak "
    "detection for this sample file to rebuild the store."
)
USER = SimpleNamespace(id=7, username="editor")


def _sample(item_id: str, file_id: str | None = None):
    file_id = file_id or f"file-of-{item_id}"
    return SimpleNamespace(
        sample_item_id=item_id,
        sample_item_name=f"sample {item_id}",
        sample_file_id=file_id,
        filename=f"{file_id}.raw",
        t0=0.0,
        t1=10.0,
        polarity="-",
    )


def _signal() -> xr.DataArray:
    return xr.DataArray(
        np.array([1.0, 2.0]), dims=("mz",), coords={"mz": [100.0, 100.5]}
    )


class _Files:
    """The samples a request reads, some of whose files have a stale store,
    and what was asked of the file converter for them."""

    def __init__(self, samples, stale=(), failing=()):
        self.samples = {sample.sample_item_id: sample for sample in samples}
        self.stale = {f"{file_id}.raw" for file_id in stale}
        self.failing = {f"{file_id}.raw" for file_id in failing}
        self.forbidden: set[str] = set()
        self.rebuilds = AsyncMock(side_effect=lambda files, user, pid: len(files))

    async def fetch_sample(self, sample_item_id):
        return self.samples[sample_item_id]

    def signal(self, filename, polarity, t_min, t_max, average):
        if filename in self.stale:
            raise StalePeakStoreError(STALE)
        if filename in self.failing:
            raise OSError("the filestore is gone")
        return _signal()

    async def check_access(self, sample_item_id, user, min_role):
        assert min_role == "editor"
        if sample_item_id in self.forbidden:
            raise ForbiddenAccessException()

    def __enter__(self):
        self._patches = [
            patch.object(samples_controller, "fetch_sample", self.fetch_sample),
            patch.object(spectrum_service, "fetch_sample", self.fetch_sample),
            patch.object(
                samples_controller.m_compute, "get_sample_sum_signal", self.signal
            ),
            patch.object(spectrum_service, "check_sample_access", self.check_access),
            patch.object(
                spectrum_service, "request_stale_peak_store_rebuilds", self.rebuilds
            ),
        ]
        for patched in self._patches:
            patched.start()
        return self

    def __exit__(self, *exc_info):
        for patched in self._patches:
            patched.stop()
        return False

    def queued(self) -> list[dict]:
        """The files each request to the converter named."""
        return [call.args[0] for call in self.rebuilds.await_args_list]


# -- several samples -------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_stale_sample_does_not_cost_the_others_their_spectra():
    samples = [_sample("a"), _sample("b"), _sample("c")]
    with _Files(samples, stale=["file-of-b"]):
        body = await samples_controller.get_samples_spectra(["a", "b", "c"])

    # One entry per sample asked for, in the order asked
    assert [entry["mz"] for entry in body["data"]] == [
        [100.0, 100.5],
        [],
        [100.0, 100.5],
    ]
    assert body["results"] == 3
    stale = body["data"][1]
    assert (stale["intensity"], stale["intensity_unit"]) == ([], "counts/s")
    assert "Re-run peak detection" in stale["message"]
    assert body["stale"] == [
        {
            "sample_item_id": "b",
            "sample_file_id": "file-of-b",
            "filename": "file-of-b.raw",
        }
    ]
    assert body["message"].startswith("Spectra retrieved for 2 of 3 samples.")
    assert "file-of-b.raw" in body["message"]


@pytest.mark.asyncio
async def test_every_sample_answered_reads_as_before():
    with _Files([_sample("a"), _sample("b")]):
        body = await samples_controller.get_samples_spectra(["a", "b"])

    assert body["message"] == "Spectra retrieved successfully."
    assert body["stale"] == []
    assert all("message" not in entry for entry in body["data"])


@pytest.mark.asyncio
async def test_any_other_failure_still_fails_the_request():
    """Only a stale store is one sample's own condition to answer around."""
    with _Files([_sample("a"), _sample("b")], failing=["file-of-b"]):
        with pytest.raises(ApiException):
            await samples_controller.get_samples_spectra(["a", "b"])


@pytest.mark.asyncio
async def test_the_stale_files_of_several_samples_are_asked_rebuilt_once_each():
    samples = [
        _sample("a", "shared"),
        _sample("b", "shared"),
        _sample("c"),
        _sample("d"),
    ]
    with _Files(samples, stale=["shared", "file-of-c"]) as files:
        body = await spectrum_service.get_samples_spectra_for(
            USER, sample_item_ids=["a", "b", "c", "d"]
        )

    assert files.queued() == [
        {"shared": "shared.raw", "file-of-c": "file-of-c.raw"},
    ]
    assert files.rebuilds.await_args.args[1] is USER
    assert body["message"].startswith("Spectra retrieved for 1 of 4 samples.")
    assert body["message"].endswith(
        "Peak detection has been queued for 2 of them: ask again once it has finished."
    )


@pytest.mark.asyncio
async def test_a_file_is_asked_rebuilt_only_for_someone_who_may_rematch_its_sample():
    """The rebuild rematches the file's samples as whoever asked: an editor's
    refresh of the batch, which a guest of the workspace cannot do."""
    samples = [_sample("mine"), _sample("theirs")]
    with _Files(samples, stale=["file-of-mine", "file-of-theirs"]) as files:
        files.forbidden = {"theirs"}
        body = await spectrum_service.get_samples_spectra_for(
            USER, sample_item_ids=["mine", "theirs"]
        )

    assert files.queued() == [{"file-of-mine": "file-of-mine.raw"}]
    assert "queued for 1 of them" in body["message"]


@pytest.mark.asyncio
async def test_nothing_is_said_of_a_rebuild_that_was_not_queued():
    """No converter to ask, or nobody who may: the answer names the stale
    files and promises nothing."""
    with _Files([_sample("a"), _sample("b")], stale=["file-of-b"]) as files:
        files.rebuilds.side_effect = lambda files_, user, pid: 0
        body = await spectrum_service.get_samples_spectra_for(
            USER, sample_item_ids=["a", "b"]
        )

    assert "queued" not in body["message"]
    assert body["stale"]


@pytest.mark.asyncio
async def test_a_request_that_met_no_stale_store_asks_for_nothing():
    with _Files([_sample("a")]) as files:
        body = await spectrum_service.get_samples_spectra_for(
            USER, sample_item_ids=["a"]
        )

    files.rebuilds.assert_not_awaited()
    assert body["message"] == "Spectra retrieved successfully."


# -- one sample ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_spectrum_that_meets_a_stale_store_asks_for_its_rebuild():
    """The spectrum does not exist until the detection has run, so the
    request still fails, with the store's own sentence and what was done
    about it."""
    with _Files([_sample("a")], stale=["file-of-a"]) as files:
        with pytest.raises(ApiException) as refused:
            await spectrum_service.get_sample_spectrum_for(USER, sample_item_id="a")

    assert files.queued() == [{"file-of-a": "file-of-a.raw"}]
    message = refused.value.user_message
    assert "Re-run peak detection" in message
    assert message.endswith(
        "Peak detection has been queued for the file: open the sample again "
        "once it has finished."
    )
    assert refused.value.status_code == 400


@pytest.mark.asyncio
async def test_a_guest_is_told_what_the_store_says_and_nothing_is_queued():
    with _Files([_sample("a")], stale=["file-of-a"]) as files:
        files.forbidden = {"a"}
        with pytest.raises(ApiException) as refused:
            await spectrum_service.get_sample_spectrum_for(USER, sample_item_id="a")

    files.rebuilds.assert_not_awaited()
    assert "queued" not in refused.value.user_message
    assert "Re-run peak detection" in refused.value.user_message


@pytest.mark.asyncio
async def test_a_spectrum_that_fails_for_another_reason_asks_for_nothing():
    with _Files([_sample("a")], failing=["file-of-a"]) as files:
        with pytest.raises(ApiException):
            await spectrum_service.get_sample_spectrum_for(USER, sample_item_id="a")

    files.rebuilds.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_spectrum_that_reads_is_returned_as_the_controller_returns_it():
    with _Files([_sample("a")]) as files:
        body = await spectrum_service.get_sample_spectrum_for(
            USER, sample_item_id="a", mz_min=None, mz_max=None
        )

    files.rebuilds.assert_not_awaited()
    assert body["data"]["mz"] == [100.0, 100.5]
