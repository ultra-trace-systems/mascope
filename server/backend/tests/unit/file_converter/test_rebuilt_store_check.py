"""A peak store rebuilt for a stale one is checked before it is trusted.

A refresh that meets a stale store queues peak detection and reports the batch
at INFO, on the understanding that the rebuild repairs it. A rebuild that does
not is a fault nothing else on that loop would report: the rematch after it
fails as a client error, and the next refresh queues the same rebuild again.
"""

from test_utils import captured_logs

from mascope_backend.file_converter import peak_recompute_worker
from mascope_backend.file_converter.peak_recompute_worker import PeakRecomputeWorker
from mascope_runtime.logging import SENTRY_FINGERPRINT
from mascope_signal.compute import StalePeakStoreError


# The error as check_stored_scan_axis words it, advice and all
STALE = StalePeakStoreError(
    "The peak store holds 24 scan(s) and the sample file now reads back 23 "
    "(their scan counts differ). Re-run peak detection for this sample file to "
    "rebuild the store."
)


def _store_check(monkeypatch, outcome=None):
    async def check(filename):
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(peak_recompute_worker, "check_peak_store", check)


def _reported(filename="ORBI-1_run.raw"):
    """The WARNING-and-above records the check emits - what monitoring sees."""
    with captured_logs(level="WARNING") as records:
        PeakRecomputeWorker._check_rebuilt_store(filename)
    return records


def test_a_store_the_rebuild_repaired_is_not_reported(monkeypatch):
    _store_check(monkeypatch)

    assert _reported() == []


def test_a_store_still_stale_after_its_rebuild_is_warned_about(monkeypatch):
    _store_check(monkeypatch, STALE)

    (record,) = _reported()

    assert record["level"].name == "WARNING"
    assert "ORBI-1_run.raw" in record["message"]


def test_the_warning_carries_the_error_so_files_group_as_one_issue(monkeypatch):
    """The files one fault reaches share an issue, and the file still travels
    with the event in the log line. The error's message carries the scan
    counts, and monitoring would group an exception event by its message's
    first line - close to an issue per file - so the call site pins the
    grouping."""
    _store_check(monkeypatch, STALE)

    (record,) = _reported()

    assert record["exception"] is not None
    assert isinstance(record["exception"].value, StalePeakStoreError)
    assert record["extra"][SENTRY_FINGERPRINT] == ["peak-store-stale-after-rebuild"]
    # The error's own advice is to re-run peak detection, which has just run.
    assert "Re-run peak detection" not in record["message"]


def test_a_check_that_cannot_run_does_not_fail_the_rebuild(monkeypatch):
    """The store is written either way; failing here would report a
    successful detection as failed."""
    _store_check(monkeypatch, OSError("store unreadable"))

    (record,) = _reported()

    assert record["level"].name == "ERROR"
    assert isinstance(record["exception"].value, OSError)
