"""The ETL adapter contract.

One adapter per source. An adapter is a pure transform: given a path to a
downloaded dump, it yields raw :class:`ReferenceRecord` instances (source
formula, identity fields, per-record license). Canonicalization and mass
computation happen afterwards in :func:`mascope_reference.normalize.finalize`,
so adapters never depend on the chemistry engine and stay trivially testable
against small fixtures.

Adapters open files lazily and yield row by row: source dumps are routinely
multi-gigabyte, so nothing here is allowed to materialize a whole file.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from mascope_reference.record import ReferenceRecord


@runtime_checkable
class Adapter(Protocol):
    """A source-specific dump reader."""

    #: Stable source name, used as the ``reference_source`` key and record tag.
    name: str
    #: Default license tag applied to records that carry no per-record license.
    license: str

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        """Yield raw (pre-canonicalization) records from a downloaded dump."""
        ...
