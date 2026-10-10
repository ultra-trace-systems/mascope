"""A re-processed file's peaks, detected again under the rule of the day.

Whether a raw Orbitrap file's peaks are detected per scan stream is decided
when the file is first converted and recorded beside it, and every rebuild
of its peak store goes by that record and leaves it as it is
(``mascope_signal.peak``; ``docs/dev/ingest_routing_and_splitting.md``,
section 4.3). A file's samples are defined against its store as it was
decided, so switching the deployment's setting changes nothing under them.

An explicit re-processing is the one place that decides again. It refuses a
file a person made a sample from and makes every other sample of the file
anew, so nothing is left that was defined against the store as it was. It
detects the file's peaks under ``[backend] composite_scan_streams`` as it is
set at that moment and records that: a file converted before the setting was
switched on is stitched, and a stitched one is pooled again where it was
switched off.

**Only where the answer can differ from the store the file has.** A file
that holds one experiment in each polarity and that nobody decided anything
for - nearly every file - has the store any decision gives it, so its peaks
are left alone, and it is read for its streams only where the setting is on.
A file whose store or whose record says per stream is detected again
whichever way the setting is, since the map its streams are stitched by is
drawn by the rule of the day as well.

**Peaks are detected here, in the backend.** Every other detection runs in
the file converter, which is asked over a socket and answers whenever it is
done. A re-processing needs the store before it makes the file's samples,
so it runs the detection itself and waits for it. Two detections of one file
cannot overlap whichever process runs them
(``mascope_file.io.peak_detection_lock_path``).
"""

import asyncio

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.db import SampleFile
from mascope_backend.runtime import runtime
from mascope_signal.peak import PER_STREAM_PROP, compute_peaks


def composites_scan_streams() -> bool:
    """Whether this deployment detects a multi-experiment file per experiment.

    ``composite_scan_streams`` in the runtime ``[backend]`` config
    (``mascope_runtime.config.BackendConfig``), read where a decision is
    made: here for a re-processing, and by the file converter for a file's
    first conversion (``mascope_backend.file_converter.base_processor``).

    :return: True when a file whose method measures more than one thing in a
        polarity is to get a peak list per stream
    :rtype: bool
    """
    backend = runtime.full_config.backend
    return bool(backend is not None and backend.composite_scan_streams)


def _decide(filename: str) -> bool | None:
    """Synchronous body of :func:`redetection_decision`: it reads the filestore."""
    if not m_compute.has_scan_streams(filename):
        return None
    setting = composites_scan_streams()
    per_stream_now = bool(m_io.read_props(filename).get(PER_STREAM_PROP))
    if not per_stream_now:
        try:
            store = m_io.load_array(filename, var="peak_timeseries")
        except FileNotFoundError:
            store = None
        if store is not None:
            try:
                per_stream_now = bool(m_compute.peak_store_streams(store))
            except ValueError:
                # Labelled in part, which nothing reads: detecting writes all
                # of it
                per_stream_now = True
    if not (per_stream_now or setting):
        # Pooled, and to stay so: nothing is detected, and the file is not
        # read for its streams
        return None
    # Its peaks are to be detected again, or may be: the streams are read
    # here, whatever the answer, because here nothing of the file has been
    # touched yet. A file whose streams cannot be read fails this, and is
    # refused as it stands rather than after its calibration is reset.
    streams = m_compute.get_peak_streams(filename)
    if per_stream_now:
        return setting
    # Pooled, with no decision recorded for per stream: only a file whose
    # method measures more than one thing in a polarity is detected otherwise
    return True if streams else None


async def redetection_decision(sample_file: SampleFile) -> bool | None:
    """The decision a re-processing hands peak detection for this file.

    :param sample_file: The file about to be re-processed.
    :type sample_file: SampleFile
    :raises Exception: If the file's ``.props``, its peak store or its scan
        streams cannot be read. Nothing of the file has been touched then.
    :return: Whether its peaks are to be detected per scan stream, as the
        deployment is set now; None where its store stands as it is - any
        file but a raw Orbitrap one, a blank measurement, and a file nothing
        says per stream of: not its record, not its store, and not the
        setting together with its own streams.
    :rtype: bool | None
    """
    if sample_file.instrument_function_id is None:
        # A blank measurement: stored without an instrument config, with an
        # empty peak store that no detection writes
        return None
    return await asyncio.to_thread(_decide, sample_file.filename)


async def redetect_peaks(sample_file: SampleFile, per_stream: bool) -> None:
    """Detect a file's peaks again, by an explicit decision, and record it.

    Awaited to its end: when it returns, the file's peak store and the
    decision its ``.props`` records are the ones a first conversion under
    this decision writes.

    :param sample_file: The file being re-processed.
    :type sample_file: SampleFile
    :param per_stream: The decision, as :func:`redetection_decision` gives it.
    :type per_stream: bool
    :raises Exception: If the file's instrument functions cannot be read, or
        its peaks cannot be detected or written
    :return: None
    """
    filename = sample_file.filename
    instrument_functions = await read_instrument_functions(filename=filename)
    await asyncio.to_thread(
        compute_peaks, filename, instrument_functions, None, per_stream
    )
    runtime.logger.info(
        f"Detected the peaks of '{filename}' again for its re-processing, "
        f"{'per scan stream' if per_stream else 'whole'} as this deployment "
        "is set"
    )
