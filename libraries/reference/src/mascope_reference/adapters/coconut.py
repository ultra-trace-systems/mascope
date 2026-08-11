"""COCONUT adapter.

Reads the COCONUT (COlleCtion of Open Natural prodUcts) bulk CSV export. Open
natural-product structures. Released CC0.

Column spellings have shifted across COCONUT releases, so lookups go through an
alias map.
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


class CoconutAdapter:
    """Adapter for COCONUT bulk CSV exports."""

    name = "coconut"
    license = "CC0"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        for row in read_delimited(path, delimiter=","):
            identifier = _first(row, "identifier", "coconut_id", "id")
            formula = _first(row, "molecular_formula", "molecular formula", "formula")
            if not identifier or not formula:
                continue
            yield ReferenceRecord(
                formula=formula,
                inchikey=_first(row, "inchikey", "inchi_key"),
                name=_first(row, "name", "preferred_name"),
                smiles=_first(row, "canonical_smiles", "smiles"),
                inchi=_first(row, "inchi"),
                source=self.name,
                source_native_id=identifier,
                xrefs={"coconut_id": identifier},
                license=self.license,
            )
