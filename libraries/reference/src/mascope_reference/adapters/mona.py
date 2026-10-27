"""MoNA (MassBank of North America) adapter.

Reads a MoNA MSP export (``MoNA-export-<library>.msp``, unzipped from the
download), whichever library or query it was cut for. The export puts what
the NIST layout has no field for into one ``Comments:`` line of quoted
``"key=value"`` items: the licence, the SMILES and the InChI are read there.

MoNA mixes licences per record and most of its libraries state none: the
records it took from MassBank carry ``license=CC BY`` and the like, and keep
it; a record that states none is tagged ``mona-unstated`` rather than given
the site's general terms, so a gate decides about them knowingly.

Not every MoNA spectrum is a measurement: its in-silico libraries (LipidBlast
and others, most of the "All Spectra" export) are predicted, and the export
marks them only in the instrument type (``in-silico QTOF``). They are skipped,
as are the GC-MS and MS1 records, which have no precursor to look up.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._msms_io import (
    msp_comments,
    read_msp_records,
    value_or_none,
)
from mascope_reference.schema import SPECTRUM_KIND
from mascope_reference.spectrum import (
    NOT_MS2,
    PREDICTED,
    SkippedSpectrum,
    SpectrumRecord,
    licence_tag,
    polarity_of,
    precursor_mz,
)


class MonaAdapter:
    """Adapter for MoNA MSP exports."""

    name = "mona"
    kind = SPECTRUM_KIND
    license = "mona-unstated"

    def parse(self, path: Path) -> Iterator[SpectrumRecord | SkippedSpectrum]:
        for record in read_msp_records(path):
            comments = msp_comments(record.first("comments"))
            native_id = record.first("db#")
            if native_id is None:
                continue
            if "in-silico" in (record.first("instrument_type") or "").lower():
                yield SkippedSpectrum(PREDICTED)
                continue
            if (record.first("spectrum_type") or "").upper() != "MS2":
                yield SkippedSpectrum(NOT_MS2)
                continue
            yield SpectrumRecord(
                source=self.name,
                source_native_id=native_id,
                name=record.first("name"),
                formula=record.first("formula"),
                inchikey=record.first("inchikey"),
                smiles=value_or_none(
                    comments.get("SMILES") or comments.get("computed SMILES")
                ),
                inchi=value_or_none(
                    comments.get("InChI") or comments.get("computed InChI")
                ),
                precursor_mz=precursor_mz(record.first("precursormz")),
                adduct=record.first("precursor_type"),
                polarity=polarity_of(record.first("ion_mode")),
                collision_energy_text=record.first("collision_energy"),
                peaks=record.peaks,
                license=licence_tag(comments.get("license")) or self.license,
            )
