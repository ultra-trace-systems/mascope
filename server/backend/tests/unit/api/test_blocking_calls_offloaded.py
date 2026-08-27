"""
Guards that blocking filestore work stays off the asyncio event loop.

The zarr 3 migration replaced zarr's ProcessSynchronizer with an explicit
``fasteners`` inter-process lock. That lock is held for a whole
read-modify-write and can be held by a *different* process (a sibling uvicorn
worker, or the file converter), so taking it on the event loop stalls every
other request that worker is serving. The handlers therefore hand this work to
``asyncio.to_thread``.

Two regressions are worth pinning, and neither is visible at runtime:

- someone "simplifies" an ``await asyncio.to_thread(lambda: ...)`` back into a
  direct call. Nothing fails; the loop just starts blocking again.
- someone wraps the *call* but not the ``.compute()``. These loaders return
  lazy dask-backed objects, so the wrapper moves only metadata into the thread
  and every chunk read stays on the loop. This looks correct in review and is
  the more likely of the two mistakes.

The first is caught by recording what is passed to ``to_thread``; the second
needs the thread-identity check in ``TestWorkHappensOffTheLoop``, which is the
only assertion here that can tell a real offload from a decorative one.

Asserting "the loop was not blocked" directly is not attempted - it is
timing-dependent and flaky. These are proxies, deliberately.
"""

import asyncio
import inspect
import threading

import numpy as np
import pytest
import xarray as xr


@pytest.fixture
def to_thread_spy(monkeypatch):
    """Record every callable handed to asyncio.to_thread, and still run it."""
    recorded = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(func, /, *args, **kwargs):
        recorded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", recording_to_thread)
    return recorded


def _lazy_dataset(record_thread_into: list) -> xr.Dataset:
    """A dask-backed Dataset that records which thread reads its chunks.

    The recording happens in the dask graph, so it fires when the data is
    actually materialized - not when the lazy object is constructed. That is
    exactly the distinction between a real offload and a decorative one.

    Callers must pin dask's synchronous scheduler around the ``compute()``.
    The default threaded scheduler runs chunk reads in its own pool, so the
    recorded thread would never be the main thread even when ``compute()`` was
    called on the loop and blocked it - which is precisely the case this helper
    exists to detect.
    """
    dask = pytest.importorskip("dask")
    dask_array = pytest.importorskip("dask.array")

    def _chunk():
        record_thread_into.append(threading.current_thread())
        return np.arange(8, dtype=np.float64).reshape(4, 2)

    data = dask_array.from_delayed(
        dask.delayed(_chunk)(), shape=(4, 2), dtype=np.float64
    )
    return xr.Dataset(
        {"peak_heights": (("mz", "time"), data)},
        coords={"mz": np.linspace(100.0, 110.0, 4), "time": np.arange(2)},
    )


class TestToThreadContract:
    """Properties every offload in these handlers must satisfy."""

    @pytest.mark.asyncio
    async def test_spy_records_and_delegates(self, to_thread_spy):
        """The spy itself must not change behaviour - it is used by the rest."""
        result = await asyncio.to_thread(len, [1, 2, 3])

        assert result == 3
        assert to_thread_spy == [len]

    @pytest.mark.asyncio
    async def test_offloaded_callable_is_never_a_coroutine_function(
        self, to_thread_spy
    ):
        """to_thread takes a sync callable; a coroutine function is a silent no-op.

        ``asyncio.to_thread(some_async_def, ...)`` builds a coroutine object in
        the worker thread, never awaits it, and returns it. No exception is
        raised at that point - the failure surfaces later as an AttributeError
        on the un-awaited coroutine. ``mascope_signal.compute.load_peak_timeseries``
        is ``async def``, so this is a live trap in exactly these handlers.
        """
        await asyncio.to_thread(len, [])

        for func in to_thread_spy:
            assert not inspect.iscoroutinefunction(func), (
                f"{func!r} is a coroutine function and must not be passed to "
                "asyncio.to_thread - await it instead"
            )


class TestWorkHappensOffTheLoop:
    """The chunk reads themselves must happen in the worker thread.

    This is the assertion that distinguishes wrapping the whole lazy chain
    from wrapping only its head.
    """

    @pytest.mark.asyncio
    async def test_wrapping_the_whole_chain_reads_off_the_loop(self):
        """The shape the handlers use: call + .compute() inside one thread."""
        dask = pytest.importorskip("dask")
        read_threads: list[threading.Thread] = []
        dataset = _lazy_dataset(read_threads)
        main_thread = threading.current_thread()

        with dask.config.set(scheduler="synchronous"):
            await asyncio.to_thread(lambda: dataset["peak_heights"].compute())

        assert read_threads, "the lazy data was never materialized"
        assert all(t is not main_thread for t in read_threads), (
            "chunks were read on the event loop thread"
        )

    @pytest.mark.asyncio
    async def test_wrapping_only_the_head_still_reads_on_the_loop(self):
        """The mistake this suite exists to catch, pinned as a counter-example.

        Offloading the loader but leaving the materialization behind reads the
        chunks on the loop. If this ever stops being true, the guard above has
        stopped meaning anything.
        """
        dask = pytest.importorskip("dask")
        read_threads: list[threading.Thread] = []
        dataset = _lazy_dataset(read_threads)
        main_thread = threading.current_thread()

        with dask.config.set(scheduler="synchronous"):
            lazy = await asyncio.to_thread(lambda: dataset["peak_heights"])
            lazy.compute()

        assert read_threads and all(t is main_thread for t in read_threads)


class TestHandlersOffloadTheirBlockingWork:
    """Each edited handler must actually reach asyncio.to_thread.

    Deliberately narrow: the callee is stubbed, so this asserts the offload is
    present and not what the handler computes. The point is that deleting a
    wrapper turns a green test red.
    """

    @pytest.mark.asyncio
    async def test_sample_file_spectrum_offloads(self, to_thread_spy, monkeypatch):
        """get_sample_file_spectrum takes the zarr write lock on a cache miss."""
        from mascope_backend.api.controllers.sample.files import (
            sample_files_controller as controller,
        )

        monkeypatch.setattr(
            controller,
            "_sync_get_sum_spectrum",
            lambda *a, **k: ([100.0, 101.0], [5.0, 6.0]),
        )

        async def fake_get_sample_file(sample_file_id):
            return {"data": {"filename": "OrbiTest_1001.01.01_12h00m00s_TestFile"}}

        monkeypatch.setattr(controller, "get_sample_file", fake_get_sample_file)

        result = await controller.get_sample_file_spectrum("sf_1")

        assert result["data"]["mz"] == [100.0, 101.0]
        assert controller._sync_get_sum_spectrum in to_thread_spy

    @pytest.mark.asyncio
    async def test_sample_file_peaks_offloads(self, to_thread_spy, monkeypatch):
        from mascope_backend.api.controllers.sample.files import (
            sample_files_controller as controller,
        )

        monkeypatch.setattr(
            controller,
            "_sync_load_sample_file_peaks",
            lambda *a, **k: {"mz": [100.0], "height": [1.0], "sparsity": [0.0]},
        )

        async def fake_get_sample_file(sample_file_id):
            return {"data": {"filename": "OrbiTest_1001.01.01_12h00m00s_TestFile"}}

        monkeypatch.setattr(controller, "get_sample_file", fake_get_sample_file)

        await controller.get_sample_file_peaks("sf_1", areas=False, heights=True)

        assert controller._sync_load_sample_file_peaks in to_thread_spy


class TestCalibrationApplyIsOneUnit:
    """``apply`` must be offloaded whole, not per zarr call.

    Both bodies recalibrate several arrays and contain no await, so they are
    atomic against every other coroutine in the worker. Wrapping each write
    separately would insert yield points into the middle of a multi-array
    recalibration; on the Orbitrap handler, where the factor is cumulative, a
    second apply admitted through one of those could double-apply it.
    """

    def test_apply_delegates_to_a_single_sync_body(self):
        from mascope_backend.api.controllers.calibration.lib import (
            calibration_mz_fit as mod,
        )

        for handler in (mod.TofCalibrationHandler, mod.OrbiCalibrationHandler):
            assert inspect.iscoroutinefunction(handler.apply)
            assert not inspect.iscoroutinefunction(handler._apply_sync), (
                f"{handler.__name__}._apply_sync must stay synchronous so the "
                "whole recalibration is one atomic unit in one thread"
            )
            source = inspect.getsource(handler._apply_sync)
            assert "await " not in source, (
                f"{handler.__name__}._apply_sync gained an await - the body is "
                "no longer atomic, and it can no longer run in a worker thread"
            )
