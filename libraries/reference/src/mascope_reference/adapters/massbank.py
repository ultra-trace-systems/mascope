"""MassBank adapter.

Reads a MassBank Europe release in MassBank's own record format: the source
archive of a MassBank-data release (``MassBank-data-<tag>.tar.gz``, one record
file per spectrum, read without unpacking) or a text file of records. Not the
release's MSP files, which leave the licence out, nor its JSON, which is a
metadata catalogue without peaks.

Every MassBank record states its licence (``LICENSE: CC BY``), and they differ:
CC0, CC BY, CC BY-SA, CC BY-NC and CC BY-NC-SA all occur, so each spectrum keeps
its own (``CC-BY-NC-SA``).
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._msms_io import peak_pair, read_massbank_records
from mascope_reference.schema import SPECTRUM_KIND
from mascope_reference.spectrum import (
    DEPRECATED,
    NOT_MS2,
    SkippedSpectrum,
    SpectrumRecord,
    licence_tag,
    polarity_of,
    precursor_mz,
)


class MassBankAdapter:
    """Adapter for MassBank record-format releases."""

    name = "massbank"
    kind = SPECTRUM_KIND
    #: For a record that states no licence, which the format does not allow.
    license = "massbank-unstated"

    def parse(self, path: Path) -> Iterator[SpectrumRecord | SkippedSpectrum]:
        for record in read_massbank_records(path):
            accession = record.first("ACCESSION")
            if accession is None:
                continue
            if "DEPRECATED" in record.fields:
                yield SkippedSpectrum(DEPRECATED)
                continue
            if (
                record.subtag("AC$MASS_SPECTROMETRY", "MS_TYPE") or ""
            ).upper() != "MS2":
                yield SkippedSpectrum(NOT_MS2)
                continue
            yield SpectrumRecord(
                source=self.name,
                source_native_id=accession,
                name=record.first("CH$NAME"),
                formula=record.first("CH$FORMULA"),
                inchikey=record.subtag("CH$LINK", "INCHIKEY"),
                smiles=record.first("CH$SMILES"),
                inchi=record.first("CH$IUPAC"),
                precursor_mz=precursor_mz(
                    record.subtag("MS$FOCUSED_ION", "PRECURSOR_M/Z")
                ),
                adduct=record.subtag("MS$FOCUSED_ION", "PRECURSOR_TYPE"),
                polarity=polarity_of(record.subtag("AC$MASS_SPECTROMETRY", "ION_MODE")),
                collision_energy_text=record.subtag(
                    "AC$MASS_SPECTROMETRY", "COLLISION_ENERGY"
                ),
                peaks=[p for line in record.peak_lines if (p := peak_pair(line))],
                license=licence_tag(record.first("LICENSE")) or self.license,
            )
