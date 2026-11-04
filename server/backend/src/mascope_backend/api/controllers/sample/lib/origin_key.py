"""The origin key: which part of the acquisition record a copy was made from.

A batch built from a filter is refreshed by running its filter again and
appending what it does not hold yet, and what it holds is told by each
copy's ``sample_item.origin_key`` (``docs/dev/sample_filters_and_metadata.md``,
section 4.3). The key is a digest of the natural key of the part the copy
was made from, taken when the copy is made and never edited: its file, its
stream and epoch within the file, and its window.

- **The file** is ``sample_file_id``. A file's row outlives its
  re-processing, which makes the file's acquisition samples again under new
  ids, so the source's own id would not find them.
- **The stream** is named by its polarity. An item reads the whole of its
  polarity's MS1 scans, however it reads them: with no stream row, through
  the polarity's one stream, or through the composite its streams are
  stitched into (``process/streams.py``). A file processed before stream rows
  existed, and stitched or given rows when it is processed again, keeps the
  key of each of its parts. A stream row's ``stream_key`` is not the name: it
  is a name within its file that the reader of the day gives, the filter of a
  single-stream file's scans rather than its polarity, and it would give the
  same part another key once the file is processed again under rows.
- **The epoch** is left out: a stream has exactly one until a recipe cuts
  epochs, and nothing cuts them yet. A key written now is what the part of a
  stream with one epoch keeps reading as.
- **The window**, ``t0`` and ``t1``, as stored. The copy's own are editable,
  which is why the key is a column of its own rather than read off them.

The ionization mode is deliberately not part of it (the note's decision 10):
the key says which measurement a copy is, not how it was read, so a copy
whose file's chemistry is corrected afterwards keeps its key.

When items can read part of a polarity - one of its streams apart, or an
epoch of one - the step that makes such items adds what tells the parts
apart to their keys, and to theirs alone, so every key written before keeps
its meaning.
"""

import hashlib
import json
from typing import Protocol


#: Names the layout of what is digested, so that a key of another layout can
#: never equal one of this.
_LAYOUT = "mascope-origin/1"


class _Part(Protocol):
    """What the key is taken from: a sample, as ``sample_view`` gives it."""

    sample_file_id: str
    polarity: str | None
    t0: float | None
    t1: float | None
    origin_key: str | None


def _seconds(value: float | None) -> float | None:
    """A window's end as one number: 60 and 60.0 are one window, and so are
    0.0 and -0.0, which would otherwise be written apart."""
    return None if value is None else float(value) + 0.0


def origin_key(
    sample_file_id: str,
    polarity: str | None,
    t0: float | None,
    t1: float | None,
) -> str:
    """The origin key of one part of the acquisition record.

    :param sample_file_id: The part's file.
    :param polarity: Its polarity, which names its stream (module docstring).
    :param t0: The start of its window [s].
    :param t1: The end of its window [s].
    :return: The digest, 64 hexadecimal characters.
    """
    natural = [_LAYOUT, sample_file_id, polarity, _seconds(t0), _seconds(t1)]
    text = json.dumps(natural, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def copy_origin_key(source: _Part) -> str:
    """The origin key a copy of ``source`` is made with.

    The source's own where it has one: a copy of a copy came from the part
    its source came from, even where its source's window was edited since,
    so a batch copied whole holds what its original holds. Else the digest
    of the source's part - an acquisition sample's, which is what a refresh
    compares with, or that of a sample made by hand, whose part a refresh
    finds only where its window is an acquisition sample's.

    :param source: The sample copied, as ``sample_view`` gives it.
    :return: The key.
    """
    if source.origin_key is not None:
        return source.origin_key
    return origin_key(source.sample_file_id, source.polarity, source.t0, source.t1)
