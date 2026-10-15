"""
What a file's scan-stream census says about the scans it recorded.

The census is what the converter recorded in ``.props``
(``SampleFileProps.scan_streams``), read with
:func:`~mascope_backend.api.controllers.sample.files.process.status.read_scan_streams`.
Everything here reads it and nothing else: no raw file is opened.

**No census says nothing.** A file converted before the census existed, or
by a reader that takes none, has an empty one, and is not taken for a file
with no MS1 scans - a blank stored without peaks would otherwise read as an
acquisition of fragmentation scans alone.

Its own module, below the routes that read a sample: the pipeline files an
acquisition of fragmentation scans alone by it (``process/ms2_parts.py``),
and the MS1 reads of a sample answer such an acquisition empty by it, rather
than failing on the reader's "no scans found".
"""

from mascope_backend.api.controllers.sample.files.process.status import (
    read_scan_streams,
)


def _ms_order(stream: dict) -> int | None:
    """A census stream's MS order, or None when it is not a whole number."""
    order = stream["signature"].get("ms_order")
    # bool is an int, and True would read as MS1.
    return order if isinstance(order, int) and not isinstance(order, bool) else None


def records_ms1_scans(streams: list[dict] | None) -> bool:
    """Whether the census shows any MS1 scan.

    :param streams: The file's scan-stream census, as
        :func:`~...process.status.read_scan_streams` returns it.
    :return: True when a stream of MS order 1 is recorded. False for a file
        with no census too, so read :func:`records_only_fragmentation_scans`
        where "no census" must not be taken for "no MS1 scans".
    :rtype: bool
    """
    return any(_ms_order(stream) == 1 for stream in streams or [])


def records_only_fragmentation_scans(streams: list[dict] | None) -> bool:
    """Whether the census shows scans, and no MS1 scan among them.

    What tells an acquisition of fragmentation scans alone from a blank
    measurement. Both are stored with no peaks and no instrument config, and
    only the first has data that peaks are simply not detected in.

    :param streams: The file's scan-stream census.
    :return: True when it holds streams and none is of MS order 1.
    :rtype: bool
    """
    return bool(streams) and not records_ms1_scans(streams)


async def holds_only_fragmentation_scans(filename: str) -> bool:
    """:func:`records_only_fragmentation_scans` for a stored file.

    For a read that found no MS1 scans, to tell the sample of an acquisition
    of fragmentation scans alone - which has none, and answers empty - from
    a selection of an MS1 sample that holds none, which is the request's to
    fix and fails as it did.

    :param filename: The sample file's stored name.
    :return: True when its census shows scans and no MS1 scan; False when it
        shows MS1 scans, shows none, or cannot be read.
    :rtype: bool
    """
    return records_only_fragmentation_scans(await read_scan_streams(filename))


def fragmentation_polarities(
    streams: list[dict] | None, polarities: str | None
) -> list[str]:
    """The polarities of a file whose scans include fragmentation scans.

    :param streams: The file's scan-stream census.
    :param polarities: ``sample_file.polarity``: the polarities the file
        holds, which also gives the order the answer comes in. A polarity the
        census names and the file does not is left out, because an item is
        made per polarity of the file.
    :return: Those of the file's polarities that hold a stream of MS order 2
        or higher; empty when there is no census.
    :rtype: list[str]
    """
    # Compared as text, one stream at a time: a census whose polarity is not a
    # string names no polarity of the file, and must not fail the file either.
    fragmented = [
        stream["signature"].get("polarity")
        for stream in streams or []
        if (_ms_order(stream) or 0) > 1
    ]
    return [polarity for polarity in polarities or "" if polarity in fragmented]
