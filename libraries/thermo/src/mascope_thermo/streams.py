"""Scan streams: the census of what an acquisition measured.

A scan stream is the scans of one experiment of the acquisition method. A
method is built of experiments, each defining its scan parameters, and every
scan records the one that produced it: its scan event, numbered within its
segment of the method. So the survey scans of one segment and event are one
stream whatever sets two experiments apart: a scan range, a polarity, or a
microscan count that no scan filter shows. A file acquired with no method
loaded records no event, and there a stream is the scans that share one scan
signature: the fields of the scan filter that say what was measured
(:mod:`mascope_thermo.scan_filter`), plus the FT resolution from the scan's
trailer.

**What a stream is, and what it is called, are two things.** A stream's
identity is its signature and its experiment: ``signature_key``,
``scan_segment`` and ``scan_event``. Those say the same thing in every file
of one method. Its ``key`` is a name, unique within its file and no further:
it carries the experiment only where another experiment of the same file
shares the signature, so a run stopped before a repeated experiment came
round again names the first one by its signature alone. Never compare
streams across files by their keys, and compare the part of the identity the
comparison is about: the method binding's signature class reads
``signature_key`` alone, so that it does not move with a method's layout.

Most files hold one stream per polarity. A method can also alternate scan
ranges or scan modes within one polarity, repeat an experiment later in the
run, interleave fragmentation scans, or switch settings part way through.

Fragmentation scans are grouped by signature alone. What their scan event
counts - the experiment, or a dependent scan's place in its cycle - is not
measured on any file in reach, and nothing reads their streams yet.

Peak detection pools every MS1 scan of a polarity unless a deployment asks
for more, and then a file whose method measures more than one thing in a
polarity has its peaks detected per stream (:func:`peak_streams`). Streams
on the same scan ranges at the same microscans are not another thing
measured (:func:`measured`): an experiment the method only runs again, or
one whose resolution changes part way through. Their runs stay pooled. The
census records what each file holds, so that the pooling can be seen, and
so that what is built on streams rests on evidence
(``docs/dev/ingest_routing_and_splitting.md``, section 4). The reader selects
one stream's scans by its key (:func:`scan_stream_keys`, and the ``stream``
argument of ``ReaderBackend``'s selecting methods).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from typing import NamedTuple

import numpy as np

from mascope_thermo.backend import (
    ReaderBackend,
    _sample_evenly,
    open_backend,
    trailer_float,
)
from mascope_thermo.scan_filter import ScanFilter, parse_scan_filter


FT_RESOLUTION = "FT Resolution:"

#: How many transients each scan of an experiment averages, as a scan's
#: trailer names it: a setting a method changes from one experiment to the
#: next without its scan filter showing it, and that never varies among the
#: scans of one experiment.
MICROSCANS = "Micro Scan Count:"

# Trailers sampled per stream for its acquisition parameters, as many as the
# whole-file capture samples.
_PARAMETER_SCANS = 5

# Scans averaged for a stream's top peaks in stream_report. The leading peaks
# of an evenly spread sample are those of the whole stream, and a long file
# holds tens of thousands of scans.
_TOP_PEAK_SCANS = 200


def _resolution(trailer: dict) -> int | str | None:
    """The trailer's FT resolution, as a number where it is one.

    The Thermo backend reports trailer values as text and OpenTFRaw as typed
    scalars, so ``"120000"`` and ``120000`` must give the same key. A value
    that is not a number is kept as text rather than dropped.
    """
    value = trailer.get(FT_RESOLUTION)
    if value is None:
        return None
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() else number


def _scan_resolution(
    backend: ReaderBackend, scan_number: int, numbers: dict[str, float]
) -> int | str | None:
    """One scan's FT resolution, as :func:`_resolution` reads its trailer.

    ``numbers`` are the scan's :meth:`ReaderBackend.trailer_numbers`, read
    with ``FT_RESOLUTION`` among the labels. A whole stored number is what
    the trailer's text says too, however many decimals the Thermo library
    shows, so it gives the same key without the text: under that library
    one stored value costs a fraction of the scan's whole trailer as text.
    Any other resolution - none stored, text, or a fraction the text may
    round - is read from the trailer as before, so that no key changes.
    """
    number = numbers.get(FT_RESOLUTION)
    if number is not None and float(number).is_integer():
        return int(number)
    return _resolution(backend.scan_trailer(scan_number))


def scan_streams(backend: ReaderBackend) -> list[dict]:
    """The file's scan streams, in the order they first appear.

    Each stream is a dict:

    - ``key``: the stream's name in this file: the signature as one line of
      text (:meth:`~mascope_thermo.scan_filter.ScanFilter.stream_key`),
      closed by the experiment where the signature is shared by more than
      one in this file: ``... R=120000 event=2``, with the segment before
      the event for an experiment outside the method's first segment. A file
      whose signatures already separate its experiments keeps the keys it
      would have without events, which is nearly every file. Unique within
      the file, and **not** stable across files of one method (see the
      module docstring);
    - ``signature_key``: the signature and resolution as one line of text,
      with no experiment: what was measured, the same text in every file.
      Equal to ``key`` wherever the key names no experiment;
    - ``signature``: the same fields, parsed. Two experiments under one
      signature share it;
    - ``scan_segment``, ``scan_event``: for an MS1 stream, the experiment
      that produced its scans, as the method counts them from 1; both
      ``None`` where the file records none. A fragmentation stream carries
      neither key, because what its scans' events count is not measured
      (see the module docstring). A census taken before streams followed
      the experiment carries neither on any stream;
    - ``scans``: how many scans the stream holds;
    - ``blocks``: how many runs its scans form among the scans of the same
      polarity and MS order. A stream that runs throughout the file is one
      block. Two scan ranges alternating in one polarity are one block per
      scan each; a method that switches ranges every few minutes gives a few
      long blocks. Scans of the other polarity or other MS orders in between
      do not break a run, so polarity switching and fragmentation scans leave
      a survey stream in one block;
    - ``t_first``, ``t_last``: start times of its first and last scan [s];
    - ``filters``: how many distinct filters it folds together. More than one
      only for data-dependent fragmentation, whose filter names each
      precursor. Flags that report one scan's outcome (``lock``) do not make
      a filter distinct, so both backends count alike;
    - ``acquisition_params``: for an MS1 stream, the trailers of up to five of
      its own scans, summarised as ``ReaderBackend.acquisition_parameters``
      does. An MSn stream carries ``{}``: a targeted method has one stream per
      precursor, and ``.props`` is read too often to carry a summary for each.

    Every scan counts, including an outlier first scan that scan selection
    leaves out: this describes the file rather than selecting from it.

    :param backend: An open reader backend.
    :return: One dict per stream, JSON-safe.
    """
    return [stream for stream, _scan_numbers in _census(backend)]


def scan_stream_keys(backend: ReaderBackend) -> list[str]:
    """The stream key of every scan of the file, in acquisition order.

    What scan selection compares a ``stream`` against. They are the keys
    :func:`scan_streams` reports, from the function it groups by, so the scans
    selected for a stream are exactly the ones the census counted into it -
    all but an outlier first scan, which selection leaves out and the census
    does not.

    :param backend: An open reader backend.
    :return: One key per scan, as ``ReaderBackend.scan_filters`` orders them.
    """
    return [scan.key for scan in _keyed_scans(backend)]


def file_scan_stream_keys(datafile_path: str) -> dict[int, str]:
    """The stream key of every scan of a raw file, by scan number.

    :func:`scan_stream_keys` for a caller that holds scans read from several
    streams at once, each by its scan number, and has to tell which stream
    each one is of. Taken from the file as it reads now, as
    :func:`file_scan_streams` is.

    :param datafile_path: Path to a Thermo ``.raw`` file.
    :return: ``{scan number: stream key}``, for every scan of the file.
    """
    with open_backend(datafile_path) as backend:
        return {int(scan.row["scan"]): scan.key for scan in _keyed_scans(backend)}


def scan_stream_numbers(
    backend: ReaderBackend, labels: Iterable[str]
) -> list[tuple[dict, str, dict[str, float]]]:
    """Every scan's stream key, with the numbers its trailer stores under
    ``labels``.

    :func:`scan_stream_keys` for a caller that computes with trailer values
    beside the keys, in the same pass over the file's scans. The numbers
    are as stored (``ReaderBackend.trailer_numbers``), not read from the
    trailer's text, which under the Thermo library is rounded.

    :param backend: An open reader backend.
    :param labels: Trailer labels, spelled as the trailer spells them.
    :return: ``(row, key, {label: number})`` per scan, the row as
        ``ReaderBackend.scan_filters`` gives it and in its order. A label
        under which the scan's trailer stores no number is left out of its
        numbers.
    """
    return [
        (scan.row, scan.key, scan.numbers) for scan in _keyed_scans(backend, labels)
    ]


class _KeyedScan(NamedTuple):
    """One scan, with the stream it belongs to.

    ``segment`` and ``event`` are the experiment the scan is grouped by:
    ``None`` for a survey scan that records none, and for every
    fragmentation scan, which is not grouped by it. ``numbers`` are the
    trailer numbers its caller asked :func:`_keyed_scans` for.
    """

    row: dict
    parsed: ScanFilter
    resolution: int | str | None
    key: str
    signature_key: str
    segment: int | None
    event: int | None
    numbers: dict[str, float]


def _keyed_scans(
    backend: ReaderBackend, labels: Iterable[str] = ()
) -> list[_KeyedScan]:
    """Every scan of the file with its stream key, in acquisition order.

    The one place that decides which stream a scan belongs to, so that the
    census and anything that later selects a stream's scans cannot disagree.
    Each scan also keeps the numbers its trailer stores under ``labels``,
    where it stores them, so that nothing passes over the scans a second
    time.

    A survey scan belongs to its experiment: its signature together with
    its segment and scan event. The experiment closes the key only where one
    signature is shared by more than one, so the key of every other stream
    is the signature alone; and it names the segment only outside the
    method's first, so a method of one segment reads ``event=N``. Survey
    scans that record no event are one stream per signature, also beside
    scans of that signature that do record one. A fragmentation scan is
    keyed by its signature whatever it records (see the module docstring).
    """
    labels = tuple(labels)
    stored = tuple(dict.fromkeys((FT_RESOLUTION, *labels)))
    scans = []
    # signature key -> the experiments its survey scans come from
    experiments: dict[str, set[tuple[int | None, int | None]]] = {}
    for row in backend.scan_filters():
        parsed = parse_scan_filter(row["filter"])
        read = backend.trailer_numbers(row["scan"], stored)
        resolution = _scan_resolution(backend, row["scan"], read)
        numbers = {label: read[label] for label in labels if label in read}
        experiment = (None, None)
        if parsed.ms_order == 1 and row.get("event") is not None:
            # An event with no segment is in the method's first, as the
            # reader also has it (backend._method_experiment).
            experiment = (row.get("segment") or 1, row["event"])
        signature_key = parsed.stream_key(resolution)
        experiments.setdefault(signature_key, set()).add(experiment)
        scans.append((row, parsed, resolution, experiment, signature_key, numbers))
    return [
        _KeyedScan(
            row,
            parsed,
            resolution,
            parsed.stream_key(
                resolution,
                event=event,
                segment=None if segment == 1 else segment,
            )
            if len(experiments[signature_key]) > 1
            else signature_key,
            signature_key,
            segment,
            event,
            numbers,
        )
        for row, parsed, resolution, (segment, event), signature_key, numbers in scans
    ]


def _census(backend: ReaderBackend) -> list[tuple[dict, list[int]]]:
    """:func:`scan_streams`, with each stream's scan numbers beside it."""
    streams: dict[str, dict] = {}
    # (polarity, MS order) -> key of the last scan seen in that sequence
    last_key: dict[tuple, str] = {}
    for scan in _keyed_scans(backend):
        row, parsed, key = scan.row, scan.parsed, scan.key
        stream = streams.get(key)
        if stream is None:
            stream = streams[key] = {
                "key": key,
                "signature_key": scan.signature_key,
                "signature": parsed.signature(scan.resolution),
                # The experiment, on the streams grouped by it and only on
                # those: a key this dict lacks says "not grouped by it",
                # where None says "the file records none".
                **(
                    {"scan_segment": scan.segment, "scan_event": scan.event}
                    if parsed.ms_order == 1
                    else {}
                ),
                "scans": 0,
                "blocks": 0,
                "t_first": row["time_s"],
                "t_last": row["time_s"],
                "_filters": set(),
                "_scan_numbers": [],
            }
        sequence = (parsed.polarity, parsed.ms_order)
        if last_key.get(sequence) != key:
            stream["blocks"] += 1
        last_key[sequence] = key
        stream["scans"] += 1
        stream["t_last"] = row["time_s"]
        stream["_filters"].add((parsed.precursors, parsed.scan_ranges))
        stream["_scan_numbers"].append(row["scan"])

    census = []
    for stream in streams.values():
        scan_numbers = stream.pop("_scan_numbers")
        stream["filters"] = len(stream.pop("_filters"))
        stream["t_first"] = float(stream["t_first"])
        stream["t_last"] = float(stream["t_last"])
        stream["acquisition_params"] = (
            backend.acquisition_parameters(
                max_scans=_PARAMETER_SCANS, scan_numbers=scan_numbers
            )
            if stream["signature"]["ms_order"] == 1
            else {}
        )
        census.append((stream, scan_numbers))
    return census


def pooled_ms1_streams(streams: list[dict]) -> dict[str, list[str]]:
    """The polarities whose MS1 scans come from more than one stream.

    Peak detection pools every MS1 scan of a polarity into one averaged
    spectrum and one peak list, so these are the files whose streams are
    mixed today.

    :param streams: A census from :func:`scan_streams`.
    :return: ``{polarity: [stream keys]}``, only for the polarities pooled.
    """
    by_polarity: dict[str, list[str]] = {}
    for stream in streams:
        signature = stream["signature"]
        if signature.get("ms_order") == 1:
            by_polarity.setdefault(signature.get("polarity"), []).append(stream["key"])
    return {polarity: keys for polarity, keys in by_polarity.items() if len(keys) > 1}


def _setting(stream: dict, name: str) -> float | None:
    """One setting of a stream's experiment, or ``None`` where its census
    does not say.

    The value the stream's sampled scans agree on (``acquisition_params``).
    A reader reports it as a number or as text, by backend, and it is a
    number here either way, also in text a server captured in its own
    regional format (:func:`~mascope_thermo.backend.trailer_float`). A
    setting that varied among the scans sampled is listed by name only, and
    reads as unknown like one no trailer carried.
    """
    constant = (stream.get("acquisition_params") or {}).get("constant") or {}
    return trailer_float(constant.get(name))


def microscans(stream: dict) -> float | None:
    """How many transients each scan of a stream averaged, or ``None`` where
    its census does not say.

    :param stream: A stream of a census from :func:`scan_streams`.
    :return: The count, or ``None``.
    """
    return _setting(stream, MICROSCANS)


def measured(stream: dict) -> tuple[tuple[tuple[str, str], ...], float | None]:
    """What a stream's experiment measured, to tell the MS1 streams of one
    polarity apart by: its scan ranges and its microscan count.

    Those two are all the stitch map is drawn from
    (``mascope_signal.stitch``): a stream claims its ranges, and where two
    claim one m/z the one with more microscans owns it. Two streams that
    agree on both tie wherever they claim, and the stitch can only give one
    of them all of it, by their order in the file. So they are one thing
    measured, whatever else sets them apart: a resolution changed part way
    through a run, a scan mode, an AGC target, or where the experiment sits
    in the method, as an experiment the method runs again does. A method
    that settles the source with a short scan and then measures defines two
    experiments under one filter, and its microscans keep them two: the
    measurement owns the range (``docs/dev/ingest_routing_and_splitting.md``,
    4.1 and 4.5).

    What a scan reports of itself, its injection time for one, varies within
    an experiment and says nothing of what the method asked for.

    A range is read at the four decimals a stream key states it to, as the
    stitch reads it, and the ranges of a multiplexed scan in m/z order. A
    microscan count the census does not hold is ``None`` and compares as a
    value like any other, so streams of one range whose counts cannot be
    read count as one thing measured.

    :param stream: A stream of a census from :func:`scan_streams`.
    :return: ``(scan ranges, microscan count)``, each range ``(lower,
        upper)`` as text.
    """
    ranges = sorted(
        (float(lower), float(upper))
        for lower, upper in (stream.get("signature") or {}).get("scan_ranges") or []
    )
    return (
        tuple((f"{lower:.4f}", f"{upper:.4f}") for lower, upper in ranges),
        microscans(stream),
    )


def peak_streams(streams: list[dict]) -> list[dict]:
    """The MS1 streams a file's peaks are detected per, or ``[]``.

    A file is detected per stream when some polarity holds MS1 streams that
    measured more than one thing (:func:`measured`): two scan ranges, or one
    range at two microscan counts. Every MS1 stream of the file then gets a
    peak list of its own, the only stream of another polarity included, so
    that each peak of such a file belongs to exactly one stream.

    A polarity whose streams all measured the same thing has nothing in it
    to detect apart. Detected apart, its streams would tie in the stitch
    and the first of them would be its composite, the others left out
    whole. Pooled, they are averaged together, as they are with per-stream
    detection off. So a file of such polarities is detected whole, like one
    with a single MS1 stream in each polarity, where the polarity already
    is the stream. That holds whether the streams follow each other or
    interleave: a composite method runs each window's scans as one block
    too, so time order tells no composite from a repeat.

    A group of such streams beside another experiment of its polarity is
    not reached by this: its file is detected per stream for the other
    experiment's sake, and each stream of the group is a stream of its own.
    So is a group in a polarity of a file detected per stream for another
    polarity's sake.

    :param streams: A census from :func:`scan_streams`.
    :return: The MS1 streams, in the order they first appear, or ``[]``.
    """
    kinds: dict[str | None, set[tuple]] = {}
    for stream in streams:
        signature = stream["signature"]
        if signature.get("ms_order") == 1:
            kinds.setdefault(signature.get("polarity"), set()).add(measured(stream))
    if not any(len(found) > 1 for found in kinds.values()):
        return []
    return [stream for stream in streams if stream["signature"].get("ms_order") == 1]


def file_scan_streams(datafile_path: str) -> list[dict]:
    """A raw file's scan streams, taken from the file as it reads now.

    What per-stream peak detection goes by. The census in a file's
    ``.props`` was taken when the file was converted, by the reader of that
    day, and a stream is selected by a key the reader of today computes;
    asking the reader for both is what keeps them one answer.

    :param datafile_path: Path to a Thermo ``.raw`` file.
    :return: The census, as :func:`scan_streams` gives it.
    """
    with open_backend(datafile_path) as backend:
        return scan_streams(backend)


def stream_report(datafile_path: str, top: int = 10) -> dict:
    """A raw file's census, as ``mascope file scans`` shows it.

    The file's instrument model, method and scan count, then its streams from
    :func:`scan_streams`. Each MS1 stream also gets ``top_peaks``: the
    ``top`` strongest centroids averaged over up to 200 of that stream's own
    scans, spread evenly, as ``[m/z, intensity]`` pairs. The reagent ions of a chemistry are usually
    among them, which is what an operator looks for first.

    :param datafile_path: Path to a Thermo ``.raw`` file.
    :param top: How many centroids to report per MS1 stream; 0 for none.
    :return: The report, JSON-safe.
    """
    with open_backend(datafile_path) as backend:
        streams = []
        for stream, scan_numbers in _census(backend):
            if stream["signature"].get("ms_order") == 1 and top > 0:
                stream["top_peaks"] = _top_peaks(backend, scan_numbers, top)
            streams.append(stream)
        return {
            "file": os.path.basename(datafile_path),
            "model": backend.instrument_details().get("Model"),
            "method_file": backend.method_file(),
            "scans": int(backend.num_scans()),
            "streams": streams,
        }


def _top_peaks(backend: ReaderBackend, scan_numbers: list[int], top: int) -> list:
    """The ``top`` strongest centroids averaged over an even sample of these scans."""
    masses, intensities, *_ = backend.average_centroids(
        _sample_evenly(scan_numbers, _TOP_PEAK_SCANS), ppm=1, average=True
    )
    masses = np.asarray(masses, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    strongest = np.argsort(intensities)[::-1][:top]
    return [
        [round(float(masses[i]), 5), round(float(intensities[i]), 1)] for i in strongest
    ]


def main(argv: list[str] | None = None) -> int:
    """``python -m mascope_thermo.streams PATH``: print the report as JSON.

    The runtime logs to stdout too, and a caller reads the report from
    stdout, so logging is shut down before the file is opened. Anything
    logged earlier is flushed first, and the report is the last line.
    ``mascope file scans`` runs this inside the backend container when the
    operator CLI has no reader of its own.
    """
    parser = argparse.ArgumentParser(
        prog="python -m mascope_thermo.streams",
        description="Print a Thermo raw file's scan streams as JSON.",
    )
    parser.add_argument("path", help="Path to a Thermo .raw file")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Strongest centroids to report per MS1 stream (default 10)",
    )
    args = parser.parse_args(argv)

    from loguru import logger

    logger.remove()
    report = stream_report(args.path, top=args.top)
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
