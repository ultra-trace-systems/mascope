"""NORMAN Suspect List Exchange adapter.

Reads NORMAN suspect lists / the merged SusDat table (CSV, distributed via
CompTox). These are the curated suspect sets - PFAS, pesticides, and other
contaminant lists - that make environmental suspect screening concrete. Open.

The SusDat merged export uses ``Molecular_Formula`` / ``Structure_SMILES`` /
``InChIKey`` style headers; individual list exports vary, so lookups go through
an alias map.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._io import read_delimited
from mascope_reference.record import ReferenceRecord


def _first(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return value
    return None


class NormanAdapter:
    """Adapter for NORMAN suspect lists / SusDat CSV exports."""

    name = "norman"
    license = "open"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        for row in read_delimited(path, delimiter=","):
            native_id = _first(
                row, "Norman_SusDat_ID", "NORMAN_SUSDAT_ID", "DTXSID", "dtxsid"
            )
            formula = _first(
                row, "Molecular_Formula", "MOLECULAR_FORMULA", "Molecular Formula"
            )
            if not native_id or not formula:
                continue
            xrefs: dict[str, str] = {}
            dtxsid = _first(row, "DTXSID", "dtxsid")
            if dtxsid:
                xrefs["dtxsid"] = dtxsid
            cas = _first(row, "CAS_RN", "CASRN", "CAS")
            if cas:
                xrefs["casrn"] = cas
            yield ReferenceRecord(
                formula=formula,
                inchikey=_first(row, "InChIKey", "INCHIKEY", "inchikey"),
                name=_first(row, "Name", "PREFERRED_NAME", "name"),
                smiles=_first(row, "StructureSMILES", "SMILES", "smiles"),
                inchi=_first(row, "StructureInChI", "InChI", "inchi"),
                source=self.name,
                source_native_id=native_id,
                xrefs=xrefs,
                license=self.license,
            )
