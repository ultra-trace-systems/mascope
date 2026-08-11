"""EPA CompTox / DSSTox adapter.

Reads the CompTox Chemicals Dashboard bulk CSV export (DSSTox), keyed by
DTXSID. Environment-relevant chemistry - the direct PFAS / contaminant
suspect-screening use case. Public domain.

Column headers vary slightly between DSSTox exports, so lookups go through a
small alias map rather than hard-coding one spelling.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._io import read_delimited
from mascope_reference.record import ReferenceRecord


def _first(row: dict[str, str], *keys: str) -> str | None:
    """Return the first non-empty value among the given column aliases."""
    for key in keys:
        value = row.get(key)
        if value:
            return value
    return None


class CompToxAdapter:
    """Adapter for EPA CompTox / DSSTox bulk CSV exports."""

    name = "comptox"
    license = "public-domain"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        for row in read_delimited(path, delimiter=","):
            dtxsid = _first(row, "DTXSID", "dtxsid")
            formula = _first(row, "MOLECULAR_FORMULA", "Molecular Formula", "formula")
            if not dtxsid or not formula:
                continue
            casrn = _first(row, "CASRN", "casrn", "CAS-RN")
            xrefs: dict[str, str] = {"dtxsid": dtxsid}
            if casrn:
                xrefs["casrn"] = casrn
            yield ReferenceRecord(
                formula=formula,
                inchikey=_first(row, "INCHIKEY", "InChIKey", "inchikey"),
                name=_first(row, "PREFERRED_NAME", "Preferred Name", "IUPAC_NAME"),
                smiles=_first(row, "SMILES", "smiles"),
                inchi=_first(row, "INCHI_STRING", "InChI", "inchi"),
                source=self.name,
                source_native_id=dtxsid,
                xrefs=xrefs,
                license=self.license,
            )
