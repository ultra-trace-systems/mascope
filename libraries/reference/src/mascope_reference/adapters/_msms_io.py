"""Streaming readers for the three MS/MS library dump formats.

- **MassBank's record format**: ``TAG: value`` lines, the peaks indented under
  ``PK$PEAK:``, each record closed by ``//``. A MassBank release is the
  MassBank-data repository at a tag, whose source archive (``.tar.gz``) holds
  one record file per spectrum; it is read as it stands, without unpacking. A
  text file of one or more records reads the same.
- **MSP** (MoNA's export, NIST's layout): ``Key: value`` lines, then
  ``Num Peaks: N`` and the peaks, one record per blank-line-separated block.
- **MGF** (GNPS): ``KEY=value`` lines and the peaks between ``BEGIN IONS`` and
  ``END IONS``.

All three stream, record by record, and read ``.gz`` as they read plain text:
the MoNA and GNPS dumps run to gigabytes.
"""

import re
import tarfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from mascope_reference.adapters._io import _open_text


#: A number in a peak line.
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
#: A quoted annotation after a peak, which may hold numbers of its own.
_QUOTED = re.compile(r'"[^"]*"')
#: One quoted ``key=value`` item of a MoNA ``Comments:`` line.
_COMMENT_ITEM = re.compile(r'"([^"]*)"')
#: The record files of a MassBank-data archive, by name.
_MASSBANK_FILE = re.compile(r"MSBNK-.+\.txt")

#: The value a field holds when it holds nothing.
_EMPTY = frozenset({"", "N/A", "NA", "NONE", "NULL", "UNKNOWN"})


def value_or_none(text: str | None) -> str | None:
    """A field's value stripped, or None for an empty or placeholder one."""
    value = (text or "").strip()
    return None if value.upper() in _EMPTY else value


def peak_pair(line: str) -> tuple[float, float] | None:
    """The (m/z, intensity) of one peak line, or None when it holds none.

    Reads the first two numbers: ``107.0491 43``, ``107.0491\\t43`` and
    MassBank's ``107.0491 340449.8 43`` (m/z, intensity, relative intensity)
    give (107.0491, 43 or 340449.8). An MSP line's quoted annotation is taken
    off before (:func:`_peak_pairs`).
    """
    numbers = _NUMBER.findall(line)
    if len(numbers) < 2:
        return None
    return float(numbers[0]), float(numbers[1])


def _peak_pairs(line: str) -> list[tuple[float, float]]:
    """Every peak of an MSP peak line, which may hold several split by ';'.

    The annotations go first: one may hold a ';' of its own.
    """
    unquoted = _QUOTED.sub(" ", line)
    return [pair for chunk in unquoted.split(";") if (pair := peak_pair(chunk))]


# --- MassBank -----------------------------------------------------------------


@dataclass
class MassBankRecord:
    """One MassBank record: each tag's values, and its peak lines.

    :param fields: Tag -> its values in order; an iterative tag (``CH$NAME``,
        ``CH$LINK``, ``AC$MASS_SPECTROMETRY``) has several.
    :param peak_lines: The lines under ``PK$PEAK:``, stripped.
    """

    fields: dict[str, list[str]] = field(default_factory=dict)
    peak_lines: list[str] = field(default_factory=list)

    def first(self, tag: str) -> str | None:
        """The tag's first value, None when absent or a placeholder."""
        values = self.fields.get(tag)
        return value_or_none(values[0]) if values else None

    def subtag(self, tag: str, name: str) -> str | None:
        """The value of a ``TAG: SUBTAG value`` line, e.g. ION_MODE.

        :param tag: ``AC$MASS_SPECTROMETRY``, ``MS$FOCUSED_ION``, ``CH$LINK``.
        :param name: ``MS_TYPE``, ``PRECURSOR_M/Z``, ``INCHIKEY``.
        """
        for value in self.fields.get(tag, ()):
            head, _, rest = value.partition(" ")
            if head == name:
                return value_or_none(rest)
        return None


def _massbank_records(lines: Iterable[str]) -> Iterator[MassBankRecord]:
    """Split MassBank record-format lines into records."""
    record = MassBankRecord()
    block: str | None = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line.strip() == "//":
            if record.fields:
                yield record
            record, block = MassBankRecord(), None
            continue
        if line.startswith(" "):
            # A multi-line tag's continuation; only the peaks are read.
            if block == "PK$PEAK" and line.strip():
                record.peak_lines.append(line.strip())
            continue
        tag, sep, value = line.partition(":")
        if not sep:
            continue
        block = tag.strip()
        record.fields.setdefault(block, []).append(value.strip())
    # A file whose last record has lost its '//' still holds that record.
    if record.fields:
        yield record


def read_massbank_records(path: Path) -> Iterator[MassBankRecord]:
    """Stream the records of a MassBank release archive or record file.

    :param path: The source archive of a MassBank-data release (``.tar.gz``,
        any compression ``tarfile`` reads), or a text file of one or more
        records, optionally ``.gz``. In an archive only the record files,
        ``MSBNK-*.txt``, are read; nothing is written to disk.
    :yield: One record per spectrum.
    """
    if tarfile.is_tarfile(path):
        # Stream mode reads the archive front to back once, a member at a time.
        with tarfile.open(path, mode="r|*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                if not _MASSBANK_FILE.fullmatch(PurePosixPath(member.name).name):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", errors="replace")
                yield from _massbank_records(text.splitlines())
        return
    with _open_text(path) as handle:
        yield from _massbank_records(handle)


# --- MSP ----------------------------------------------------------------------


@dataclass
class MspRecord:
    """One MSP record: its fields, keyed lower case, and its peaks.

    :param fields: Lower-case key -> values in order (``synon`` repeats).
    :param peaks: (m/z, intensity) as listed.
    """

    fields: dict[str, list[str]] = field(default_factory=dict)
    peaks: list[tuple[float, float]] = field(default_factory=list)

    def first(self, key: str) -> str | None:
        """The key's first value, None when absent or a placeholder."""
        values = self.fields.get(key.lower())
        return value_or_none(values[0]) if values else None


def msp_comments(text: str | None) -> dict[str, str]:
    """The ``"key=value"`` items of a MoNA ``Comments:`` line.

    Split at the first ``=``, since a value may hold more (``"InChI=InChI=1S/..."``).
    A key that repeats keeps its first value.

    :param text: The line's value.
    :return: Key -> value, keys as written.
    """
    items: dict[str, str] = {}
    for item in _COMMENT_ITEM.findall(text or ""):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            items.setdefault(key.strip(), value.strip())
    return items


def read_msp_records(path: Path) -> Iterator[MspRecord]:
    """Stream the records of an MSP file.

    A record ends at a blank line, or at a ``Name:`` line when the record
    already has a name. Its peaks are the lines after ``Num Peaks:``.

    :param path: An ``.msp`` file, optionally ``.gz``.
    :yield: One record per spectrum.
    """
    with _open_text(path) as handle:
        record = MspRecord()
        in_peaks = False
        for raw in handle:
            line = raw.strip()
            if not line:
                if record.fields:
                    yield record
                record, in_peaks = MspRecord(), False
                continue
            if in_peaks and not line[0].isalpha():
                record.peaks.extend(_peak_pairs(line))
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key = key.strip().lower()
            if key == "name" and "name" in record.fields:
                yield record
                record = MspRecord()
            in_peaks = key == "num peaks"
            if not in_peaks:
                record.fields.setdefault(key, []).append(value.strip())
        if record.fields:
            yield record


# --- MGF ----------------------------------------------------------------------


@dataclass
class MgfRecord:
    """One MGF ion block: its fields, keyed upper case, and its peaks."""

    fields: dict[str, str] = field(default_factory=dict)
    peaks: list[tuple[float, float]] = field(default_factory=list)

    def get(self, key: str) -> str | None:
        """The key's value, None when absent or a placeholder."""
        return value_or_none(self.fields.get(key.upper()))


def read_mgf_records(path: Path) -> Iterator[MgfRecord]:
    """Stream the ion blocks of an MGF file.

    :param path: An ``.mgf`` file, optionally ``.gz``.
    :yield: One record per ``BEGIN IONS`` ... ``END IONS`` block.
    """
    with _open_text(path) as handle:
        record: MgfRecord | None = None
        for raw in handle:
            line = raw.strip()
            upper = line.upper()
            if upper == "BEGIN IONS":
                record = MgfRecord()
                continue
            if record is None:
                continue
            if upper == "END IONS":
                yield record
                record = None
                continue
            if line[:1].isalpha():
                key, sep, value = line.partition("=")
                if sep:
                    record.fields.setdefault(key.strip().upper(), value.strip())
                continue
            pair = peak_pair(line)
            if pair is not None:
                record.peaks.append(pair)
