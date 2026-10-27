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

A spectral library's adapter (:class:`SpectrumAdapter`) is the same kind of
transform over an MS/MS library dump: it yields
:class:`~mascope_reference.spectrum.SpectrumRecord` instances, or a
:class:`~mascope_reference.spectrum.SkippedSpectrum` for a record that is not
a reference spectrum, and has no window, since Stage A never matches spectra.
The two kinds are registered apart (:mod:`mascope_reference.sources`): their
protocols look alike, so a registry, not ``isinstance``, says which an adapter
is.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from mascope_reference.record import ReferenceRecord
from mascope_reference.scope import KnownWindow
from mascope_reference.spectrum import SkippedSpectrum, SpectrumRecord


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


@runtime_checkable
class SpectrumAdapter(Protocol):
    """A spectral library's dump reader."""

    #: Stable source name, used as the ``reference_source`` key and record tag.
    name: str
    #: ``spectrum``, which a load writes on its source row.
    kind: str
    #: Default licence tag for a record that states none of its own.
    license: str

    def parse(self, path: Path) -> Iterator[SpectrumRecord | SkippedSpectrum]:
        """Yield the dump's spectra, or why a record is not one."""
        ...
