"""GNPS adapter.

Reads a GNPS spectral library as MGF, one library (``GNPS-LIBRARY.mgf``) or
the combined export (``ALL_GNPS_NO_PROPOGATED.mgf``), from the GNPS library
downloads. The MGF names no adduct of its own: GNPS writes it after the
compound's name (``NAME=Hoiamide B M+H``), and it is read from there. It names
no collision energy either, and an InChIKey only now and then, in
``INCHIAUX``; the formula is read from the InChI where there is one.

The MGF states no licence. Spectra contributed to GNPS's own libraries (the
``GNPS-`` ones, ``ORGANISM=GNPS-LIBRARY``) are CC0 by default; the libraries it
imports from elsewhere (``MONA``, ``MASSBANK``, ``BMDMS-NP``, ...) keep their
source's terms, which the MGF does not say, so their spectra are tagged
``gnps-unstated``.

Only the library classes GNPS documents for a submission are read: 1, 2 and 3
(gold, silver, bronze). The export also carries classes 4, 5 and 10, which it
does not describe; they are skipped rather than taken for reference spectra.
"""

import re
from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._msms_io import MgfRecord, read_mgf_records
from mascope_reference.schema import SPECTRUM_KIND
from mascope_reference.spectrum import (
    LIBRARY_CLASS,
    NOT_MS2,
    SkippedSpectrum,
    SpectrumRecord,
    polarity_of,
    precursor_mz,
)


#: The library classes GNPS documents: gold, silver, bronze.
DOCUMENTED_CLASSES = frozenset({"1", "2", "3"})

#: The licence of a spectrum contributed to one of GNPS's own libraries.
GNPS_OWN_LICENSE = "CC0"
#: The tag of a spectrum GNPS imported from another library.
IMPORTED_LICENSE = "gnps-unstated"

#: The ion type GNPS appends to a name: M+H, [M+H]+, [M-H2O+H]+, 2M+Na, M+.
_ADDUCT_TOKEN = re.compile(r"\[?\d*M(?:[+-][A-Za-z0-9()]+)*\]?\d*[+-]?[.*]?")
#: An InChI's formula layer, and the layers that say the structure is charged.
_INCHI_FORMULA = re.compile(r"InChI=1S?/([^/]+)")
_INCHI_CHARGED = re.compile(r"/[qp][+-]")


def _name_and_adduct(name: str | None) -> tuple[str | None, str | None]:
    """Split ``Hoiamide B M+H`` into the compound's name and its ion type."""
    if not name:
        return None, None
    head, _, last = name.rpartition(" ")
    if head and _ADDUCT_TOKEN.fullmatch(last):
        return head.strip(), last
    return name, None


def _bracketed(
    adduct: str | None, charge: str | None, polarity: str | None
) -> str | None:
    """Bracket a bare ion type (``M+H``) with its charge, as ``[M+H]+``.

    The charge's sign is the one written after it (``M+``), else the ion mode's;
    its size is ``CHARGE`` when above one. A type with no sign to give it is
    left as it is.
    """
    if adduct is None or adduct.startswith("["):
        return adduct
    core, sign = adduct, {"positive": "+", "negative": "-"}.get(polarity or "")
    if core[-1] in "+-" and len(core) > 1:
        core, sign = core[:-1], core[-1]
    if sign is None:
        return adduct
    try:
        size = abs(int(float(charge or "0")))
    except ValueError:
        size = 0
    return f"[{core}]{size if size > 1 else ''}{sign}"


def _formula_from_inchi(inchi: str | None) -> str | None:
    """The formula layer of a neutral structure's InChI, or None."""
    if not inchi or _INCHI_CHARGED.search(inchi):
        return None
    match = _INCHI_FORMULA.match(inchi)
    return match.group(1) if match else None


def _licence(record: MgfRecord) -> str:
    library = (record.get("ORGANISM") or "").upper()
    if library == "GNPS" or library.startswith("GNPS-"):
        return GNPS_OWN_LICENSE
    return IMPORTED_LICENSE


class GnpsAdapter:
    """Adapter for GNPS library MGF downloads."""

    name = "gnps"
    kind = SPECTRUM_KIND
    # What a load's source row, `sources` and `status` show. Every spectrum
    # carries its own tag, and a load can hold imported libraries' spectra
    # whose terms are unknown, so the row claims none.
    license = IMPORTED_LICENSE

    def parse(self, path: Path) -> Iterator[SpectrumRecord | SkippedSpectrum]:
        for record in read_mgf_records(path):
            native_id = record.get("SPECTRUMID")
            if native_id is None:
                continue
            if record.get("MSLEVEL") != "2":
                yield SkippedSpectrum(NOT_MS2)
                continue
            quality = record.get("LIBRARYQUALITY")
            if quality is not None and quality not in DOCUMENTED_CLASSES:
                yield SkippedSpectrum(LIBRARY_CLASS)
                continue
            polarity = polarity_of(record.get("IONMODE"))
            name, adduct = _name_and_adduct(record.get("NAME"))
            pepmass = (record.get("PEPMASS") or "").split()
            inchi = record.get("INCHI")
            yield SpectrumRecord(
                source=self.name,
                source_native_id=native_id,
                name=name,
                formula=_formula_from_inchi(inchi),
                inchikey=record.get("INCHIAUX"),
                smiles=record.get("SMILES"),
                inchi=inchi,
                precursor_mz=precursor_mz(pepmass[0] if pepmass else None),
                adduct=_bracketed(adduct, record.get("CHARGE"), polarity),
                polarity=polarity,
                peaks=record.peaks,
                license=_licence(record),
            )
