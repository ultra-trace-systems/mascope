"""The ETL adapter contract.

One adapter per source. An adapter is a pure transform: given a path to a
downloaded dump, it yields raw :class:`ReferenceRecord` instances (source
formula, identity fields, per-record license). Canonicalization and mass
computation happen afterwards in :func:`mascope_reference.normalize.finalize`,
so adapters never depend on the chemistry engine and stay trivially testable
against small fixtures.

Adapters open files lazily and yield row by row: source dumps are routinely
multi-gigabyte, so nothing here is allowed to materialize a whole file.

An adapter also says what a load of its source writes on the source row about
how the compounds may be matched (:mod:`mascope_reference.scope`): its
``known_window``, and, where the dump itself says more - a list file's header
names its radical allowance and its polarity - a ``scope(path)`` method that
reads it.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from mascope_reference.record import ReferenceRecord
from mascope_reference.scope import KnownWindow


@runtime_checkable
class Adapter(Protocol):
    """A source-specific dump reader."""

    #: Stable source name, used as the ``reference_source`` key and record tag.
    name: str
    #: Default license tag applied to records that carry no per-record license.
    license: str
    #: The window a load writes: the mirror window for a database, unbounded for
    #: a list someone authored.
    known_window: KnownWindow

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        """Yield raw (pre-canonicalization) records from a downloaded dump."""
        ...
