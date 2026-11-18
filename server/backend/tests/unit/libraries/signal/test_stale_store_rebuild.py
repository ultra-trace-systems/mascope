"""The check and the rebuild of ``rebuild_stale_peak_stores``, on real stores.

A store goes stale when the scans its file reads change under it: here, the
scripted acquisition is handed one scan more or one fewer after its peaks
were detected, as a change of the first-scan rule hands the reader of a real
file. The script's survey and its steps against the database are tested with
stand-ins in ``tests/unit/db/test_rebuild_stale_peak_stores.py``.
"""

import asyncio

import pytest
from composite_acquisition import COMPOSITE, MICROSCANS
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_backend.db.scripts import rebuild_stale_peak_stores as script


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"

SCANS = [(NEG, 1, {62.0: 100.0, 125.0: 10.0, 188.0: 5.0})] * 6


def _store():
    return m_io.load_array(SAMPLE_FILENAME, var="peak_timeseries")


def test_a_store_its_file_reads_back_is_current(acquire, instrument_functions):
    acquire(SCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert script.check(SAMPLE_FILENAME) == script.Checked(script.CURRENT)


def test_a_store_without_scan_counts_is_uncounted_and_its_rebuild_counts(
    acquire, instrument_functions
):
    """A store detected before the scan counts were kept: current but for
    them, and a rebuild fills them."""
    acquire(SCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    store = _store().load()
    asyncio.run(
        m_io.write_peaks(
            store.drop_vars([m_peak.N_SCANS, m_peak.N_SCANS_TOTAL]),
            SAMPLE_FILENAME,
            overwrite=True,
        )
    )
    assert m_peak.N_SCANS not in _store().data_vars

    assert script.check(SAMPLE_FILENAME) == script.Checked(script.UNCOUNTED)

    asyncio.run(_rebuilt(instrument_functions))
    assert script.check(SAMPLE_FILENAME) == script.Checked(script.CURRENT)
    assert _store()[m_peak.N_SCANS].values.tolist() == [len(SCANS)] * 3


def test_a_file_with_no_store_or_no_peaks_is_not_rebuilt(acquire):
    acquire(SCANS)
    assert script.check(SAMPLE_FILENAME) == script.Checked(script.NO_STORE)

    m_peak.write_empty_peak_timeseries(SAMPLE_FILENAME)
    assert script.check(SAMPLE_FILENAME) == script.Checked(script.NO_PEAKS)


def test_a_store_a_scan_long_is_stale_and_its_rebuild_is_not(
    acquire, instrument_functions
):
    """What this release does to a file whose first scan the reader now
    leaves out: the store holds a scan the file no longer reads."""
    acquire(SCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    ids = set(_store().peak_id.values.tolist())

    acquire(SCANS[1:])
    assert script.check(SAMPLE_FILENAME) == script.Checked(
        script.STALE, len(SCANS), len(SCANS) - 1
    )

    asyncio.run(_rebuilt(instrument_functions))

    assert script.check(SAMPLE_FILENAME) == script.Checked(script.CURRENT)
    assert _store().time.size == len(SCANS) - 1
    # A new peak list, with ids nothing made on the old one finds
    assert not ids & set(_store().peak_id.values.tolist())


def test_a_store_a_scan_short_is_stale(acquire, instrument_functions):
    """The other way, which 1.15.0 did to files with MS2 scans: the file
    reads a scan the store lacks."""
    acquire(SCANS[1:])
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    acquire(SCANS)
    assert script.check(SAMPLE_FILENAME) == script.Checked(
        script.STALE, len(SCANS) - 1, len(SCANS)
    )


def test_a_per_stream_store_is_checked_stream_by_stream(
    composite, acquire, instrument_functions
):
    """Each stream against its own scans; and a rebuild keeps the store per
    stream, by the decision the file records."""
    assert script.check(SAMPLE_FILENAME) == script.Checked(script.CURRENT)

    # One scan of the low window fewer: that stream alone disagrees
    acquire(COMPOSITE[:5] + COMPOSITE[6:], MICROSCANS)
    found = script.check(SAMPLE_FILENAME)
    assert found.state == script.STALE
    assert (found.stored, found.reads) == (len(COMPOSITE), len(COMPOSITE) - 1)

    asyncio.run(_rebuilt(instrument_functions))

    assert script.check(SAMPLE_FILENAME) == script.Checked(script.CURRENT)
    assert m_compute.peak_store_streams(_store())


def test_a_per_stream_store_naming_a_stream_the_file_lacks_is_stale(composite, acquire):
    """A stored key that names no stream of the file any more is the same
    condition by another road: the stream has no scans to be read over."""
    acquire(COMPOSITE[:-2], MICROSCANS)

    assert script.check(SAMPLE_FILENAME).state == script.STALE


def test_a_run_rebuilds_the_stale_file_and_reports_it(acquire, instrument_functions):
    """The steps against the database are stand-ins; the rebuild and the
    check after it are the script's own."""
    acquire(SCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    acquire(SCANS[1:])
    stale = [
        {
            "sample_file_id": "file-1",
            "filename": SAMPLE_FILENAME,
            "instrument": "A",
            "stored": len(SCANS),
            "reads": len(SCANS) - 1,
        }
    ]
    done = []

    async def samples_of(sample_file_id):
        return ["item-1", "item-2"], ["batch-1"]

    async def rebuild(filename, before_write):
        """The script's rebuild but for the instrument functions, which the
        database would hold."""
        await script._detect_and_write(filename, instrument_functions, before_write)
        done.append("written")

    async def note(name, *args):
        # Between the detection and the write: the store is the stale one
        assert script.check(SAMPLE_FILENAME).state == script.STALE
        done.append(name)
        return 0

    steps = script.Steps(
        samples_of=samples_of,
        queue_rematch=lambda batches: note("queue", batches),
        remove_matches=lambda samples: note("remove", samples),
        refresh_tic=lambda *args: note("tic", *args),
        refresh_ms2=lambda *args: note("ms2", *args),
        rebuild=rebuild,
        check_one=script.check,
    )
    outcome = asyncio.run(script.rebuild_all(stale, steps))

    assert done == ["tic", "ms2", "queue", "remove", "written"]
    assert outcome.counts["rebuilt"] == 1
    assert not outcome.failed
    assert outcome.batches == {"batch-1"}
    assert outcome.samples == {"item-1", "item-2"}
    assert script.check(SAMPLE_FILENAME) == script.Checked(script.CURRENT)


async def _rebuilt(instrument_functions):
    """What the script's rebuild runs, with the test's instrument functions
    in the place of those the database holds."""
    await asyncio.to_thread(m_peak.compute_peaks, SAMPLE_FILENAME, instrument_functions)


def test_a_reset_that_fails_leaves_the_stale_store_and_its_record(
    acquire, instrument_functions
):
    """``before_write`` raising: nothing of the detection is recorded or
    written, so the file stays stale for the next run."""
    acquire(SCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    acquire(SCANS[1:])
    props = m_io.read_props(SAMPLE_FILENAME)

    async def reset():
        raise RuntimeError("the batches could not be set to rematch")

    with pytest.raises(RuntimeError, match="rematch"):
        asyncio.run(
            script._detect_and_write(SAMPLE_FILENAME, instrument_functions, reset)
        )

    assert script.check(SAMPLE_FILENAME).state == script.STALE
    assert m_io.read_props(SAMPLE_FILENAME) == props


def test_a_detection_that_fails_resets_nothing(
    acquire, instrument_functions, monkeypatch
):
    acquire(SCANS)

    class Failing:
        async def detect_peaks(self, progress_callback=None):
            raise ValueError("no peaks can be detected on this file")

    monkeypatch.setattr(m_peak, "get_peak_detector", lambda *_: Failing())
    reset = []

    async def before_write():
        reset.append(True)

    with pytest.raises(ValueError, match="no peaks"):
        asyncio.run(
            script._detect_and_write(
                SAMPLE_FILENAME, instrument_functions, before_write
            )
        )
    assert reset == []


def test_a_file_without_an_instrument_configuration_resets_nothing(monkeypatch):
    import mascope_backend.api.new.instrument_configs.lib as lib

    async def unreadable(filename):
        raise LookupError(f"no instrument configuration for {filename}")

    monkeypatch.setattr(lib, "read_instrument_functions", unreadable)
    reset = []

    async def before_write():
        reset.append(True)

    with pytest.raises(LookupError, match="instrument configuration"):
        asyncio.run(script._rebuild(SAMPLE_FILENAME, before_write))
    assert reset == []


def test_a_before_write_that_fails_records_no_new_decision(
    acquire, instrument_functions
):
    """Called before the decision is recorded: a detection handed another
    decision than the file's, whose ``before_write`` raises, leaves the
    file's record as it was, not only its store."""
    acquire(SCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    m_io.update_props(SAMPLE_FILENAME, {m_peak.PER_STREAM_PROP: True})

    def refuse():
        raise RuntimeError("refused")

    with pytest.raises(RuntimeError, match="refused"):
        m_peak.compute_peaks(
            SAMPLE_FILENAME, instrument_functions, per_stream=False, before_write=refuse
        )

    assert m_io.read_props(SAMPLE_FILENAME)[m_peak.PER_STREAM_PROP] is True
