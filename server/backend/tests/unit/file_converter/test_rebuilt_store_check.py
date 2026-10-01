"""A peak store rebuilt for a stale one is checked before it is trusted.

A refresh that meets a stale store queues peak detection and reports the batch
at INFO, on the understanding that the rebuild repairs it. A rebuild that does
not is a fault nothing else on that loop would report: the rematch after it
fails as a client error, and the next refresh queues the same rebuild again.
"""

from mascope_backend.file_converter import peak_recompute_worker
from mascope_backend.file_converter.peak_recompute_worker import PeakRecomputeWorker
from mascope_signal.compute import StalePeakStoreError


def _logged(monkeypatch):
    logged = {"warning": [], "exception": []}
    logger = peak_recompute_worker.runtime.logger
    for level in logged:
        monkeypatch.setattr(logger, level, logged[level].append)
    return logged


def _store_check(monkeypatch, outcome=None):
    async def check(filename):
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(peak_recompute_worker, "check_peak_store", check)


def test_a_store_the_rebuild_repaired_is_not_reported(monkeypatch):
    logged = _logged(monkeypatch)
    _store_check(monkeypatch)

    PeakRecomputeWorker._check_rebuilt_store("ORBI-1_run.raw")

    assert logged == {"warning": [], "exception": []}


def test_a_store_still_stale_after_its_rebuild_is_warned_about(monkeypatch):
    logged = _logged(monkeypatch)
    _store_check(monkeypatch, StalePeakStoreError("their scan counts differ"))

    PeakRecomputeWorker._check_rebuilt_store("ORBI-1_run.raw")

    (warning,) = logged["warning"]
    assert "ORBI-1_run.raw" in warning
    assert "their scan counts differ" in warning


def test_a_check_that_cannot_run_does_not_fail_the_rebuild(monkeypatch):
    """The store is written either way; failing here would report a
    successful detection as failed."""
    logged = _logged(monkeypatch)
    _store_check(monkeypatch, OSError("store unreadable"))

    PeakRecomputeWorker._check_rebuilt_store("ORBI-1_run.raw")

    assert logged["warning"] == []
    assert len(logged["exception"]) == 1
