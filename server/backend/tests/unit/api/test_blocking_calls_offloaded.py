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

import ast
import asyncio
import importlib
import inspect
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import mascope_backend
import mascope_signal
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    BaseCalibrationHandler,
)


#: Packages swept for ``asyncio.to_thread`` call sites. Everything that offloads
#: filestore work lives in one of these.
_SWEPT_PACKAGES = (mascope_backend, mascope_signal)


def _modules_using_to_thread():
    """Import every module in the swept packages whose source calls to_thread.

    Walks the source tree rather than pkgutil, which skips a whole subpackage
    silently when importing it raises - exactly the way this sweep could go
    quiet without anyone noticing.
    """
    for package in _SWEPT_PACKAGES:
        root = Path(package.__file__).parent
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "asyncio.to_thread" not in source:
                continue
            parts = path.relative_to(root).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            name = ".".join((package.__name__,) + parts)
            yield importlib.import_module(name), source


def _enclosing_class(tree, node):
    """The ClassDef a node sits in, if any - so `self.x` can be resolved."""
    for candidate in ast.walk(tree):
        if isinstance(candidate, ast.ClassDef) and any(
            inner is node for inner in ast.walk(candidate)
        ):
            return candidate
    return None


def _resolve(module, tree, call):
    """Resolve the callable handed to to_thread, or None if it cannot be read.

    Handles the three shapes that appear: a bare name, a dotted attribute on a
    module alias (``m_io.load_peak_data``), and a bound method (``self._sync``).
    A lambda is inlined and can never be a coroutine function, so it is skipped.
    """
    target = call.args[0]
    if isinstance(target, ast.Lambda):
        return None

    parts = []
    node = target
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    parts.reverse()

    if parts[0] == "self":
        cls = _enclosing_class(tree, call)
        if cls is None:
            return None
        obj = getattr(module, cls.name, None)
        parts = parts[1:]
    else:
        obj = module

    for part in parts:
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _offload_sites():
    """Every ``asyncio.to_thread(callable, ...)`` in the tree, resolved."""
    sites = []
    for module, source in _modules_using_to_thread():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "to_thread"):
                continue
            resolved = _resolve(module, tree, node)
            if resolved is None:
                continue
            where = f"{module.__name__}:{node.lineno}"
            sites.append((where, ast.unparse(node.args[0]), resolved))
    return sites


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


class _StubCalibrationHandler:
    """A handler with the real locking, and a body that only counts itself.

    Subclassing the real base would drag in a sample file and its stores; what
    is under test is the exclusion around ``_apply_sync``, so the body is
    replaced by an already-applied guard plus a concurrency counter, and the
    lock path is a plain temp directory.
    """

    def __init__(self, sample_dir):
        os.makedirs(sample_dir, exist_ok=True)
        self._sample_dir = sample_dir
        self.applied = 0
        self._live = 0
        self.max_concurrent = 0
        self._done = False

    _calibration_lock_path = lambda self: os.path.join(
        self._sample_dir, "mz_calibration"
    )
    _guarded_apply = BaseCalibrationHandler._guarded_apply

    async def apply(self, fit):
        return await asyncio.to_thread(self._guarded_apply, fit)

    def _apply_sync(self, fit):
        self._live += 1
        self.max_concurrent = max(self.max_concurrent, self._live)
        try:
            if self._done:
                # Stands in for _is_calibration_already_applied.
                return None
            # Widen the window the race would need, so an unlocked run fails
            # reliably rather than occasionally.
            time.sleep(0.05)
            self.applied += 1
            self._done = True
        finally:
            self._live -= 1


class TestCalibrationApplyIsOneUnit:
    """``apply`` must be offloaded whole, and under one lock.

    Both bodies recalibrate several arrays, so a reader must not catch them
    half-done. Being on the event loop used to provide that for free - no
    await in the body meant no other coroutine in the worker could interleave
    - and moving the body to a thread gives it up: two callers get two pool
    threads and run at the same time, which is weaker than the yield points
    per-write wrapping would have introduced. On the Orbitrap handler, where
    the factor is cumulative, two applies admitted past
    ``_is_calibration_already_applied`` double-apply it.

    So the exclusion has to be explicit, and these pin both halves: the body
    stays one synchronous unit, and it is reached through the lock.
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

    def test_apply_is_reached_through_the_sample_lock(self):
        """Offloading whole is only safe if something else excludes the race."""
        from mascope_backend.api.controllers.calibration.lib import (
            calibration_mz_fit as mod,
        )

        for handler in (mod.TofCalibrationHandler, mod.OrbiCalibrationHandler):
            assert "_guarded_apply" in inspect.getsource(handler.apply), (
                f"{handler.__name__}.apply must offload _guarded_apply, not "
                "_apply_sync directly - a bare thread is not exclusive"
            )

        guarded = inspect.getsource(mod.BaseCalibrationHandler._guarded_apply)
        assert "zarr_write_lock" in guarded
        assert "self._apply_sync(fit)" in guarded

    @pytest.mark.asyncio
    async def test_two_applies_for_one_sample_do_not_overlap(self, tmp_path):
        """The double-apply the guard exists to stop, driven concurrently.

        Without the lock both bodies observe the "not yet applied" state before
        either records the new one, which on the Orbitrap path scales the axis
        twice.
        """
        handler = _StubCalibrationHandler(str(tmp_path / "sample"))

        await asyncio.gather(*(handler.apply({}) for _ in range(4)))

        assert handler.applied == 1, (
            f"{handler.applied} applies got past the already-applied guard; "
            "the recalibration is not exclusive"
        )
        assert handler.max_concurrent == 1, (
            "two _apply_sync bodies overlapped inside the lock"
        )


class TestNoCoroutineFunctionIsOffloaded:
    """``to_thread`` takes a sync callable; a coroutine function is a silent no-op.

    ``asyncio.to_thread(some_async_def, ...)`` builds a coroutine object in the
    worker thread, never awaits it, and returns it. Nothing raises at that
    point - the failure surfaces later as an AttributeError on the un-awaited
    coroutine. ``mascope_signal.compute.load_peak_timeseries`` is ``async def``,
    so this is a live trap in exactly these handlers.

    Checked statically over every ``asyncio.to_thread`` call site in the tree
    rather than by exercising handlers under the spy. A runtime spy only ever
    sees the sites the test itself happens to drive, so it says nothing about
    the ones it does not - and a new offload added tomorrow is exactly the one
    nobody remembers to drive.
    """

    def test_every_offload_site_hands_over_a_sync_callable(self):
        offloads = _offload_sites()

        # Resolution is best-effort, so a bug that silently resolves nothing
        # would make this pass vacuously - which is the failure mode being
        # fixed here. Pin that it still finds the bulk of the call sites.
        assert len(offloads) >= 25, (
            f"only found {len(offloads)} asyncio.to_thread call sites; the "
            "AST walk has probably stopped matching them"
        )

        offending = [
            f"{where}: {name}"
            for where, name, func in offloads
            if inspect.iscoroutinefunction(func)
        ]
        assert not offending, (
            "coroutine functions handed to asyncio.to_thread (await them "
            "instead): " + ", ".join(offending)
        )
