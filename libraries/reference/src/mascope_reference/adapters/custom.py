"""Custom reference-list adapter.

For hand-authored reference lists - e.g. a published atmospheric "peak list"
whose compounds are not (yet) in the public databases. Reads a simple, flat
CSV/TSV with a documented column schema; only ``formula`` is required. Everything
else (name, structure, identifiers, citation) is optional and carried through.

Column schema (case-insensitive, common aliases accepted):
    formula     required - neutral molecular formula (e.g. C10H16O3)
    name        preferred compound name
    inchikey    InChIKey (enables cross-source de-duplication)
    smiles      SMILES structure
    inchi       InChI structure
    cas         CAS registry number -> xrefs["cas"]
    reference   citation / DOI of the source list -> xrefs["reference"]
    id          your identifier for the row (else name, else row number)
    license     per-record license (else the source's default)

Pair it with ``mascope reference sync custom <file> --name <list-name>`` so the
list gets its own provenance name and does not collide with other custom lists.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._io import read_delimited
from mascope_reference.record import ReferenceRecord


def _first(row: dict[str, str], *keys: str) -> str | None:
    """First non-empty value among the given column aliases (case-insensitive)."""
    lowered = {k.lower(): v for k, v in row.items() if k}
    for key in keys:
        value = lowered.get(key.lower())
        if value:
            return value
    return None


class CustomAdapter:
    """Adapter for hand-authored reference lists (CSV/TSV)."""

    name = "custom"
    license = "custom"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        # Tab-separated if the file says so, comma-separated otherwise.
        delimiter = "\t" if any(s in (".tsv", ".tab") for s in path.suffixes) else ","
        for index, row in enumerate(read_delimited(path, delimiter=delimiter)):
            formula = _first(row, "formula", "molecular_formula")
            if not formula:
                # No formula -> no usable annotation key; skip the row.
                continue
            name = _first(row, "name", "compound_name", "compound")
            native_id = (
                _first(row, "id", "identifier", "compound_id")
                or name
                or f"{self.name}-{index + 1}"
            )
            xrefs: dict[str, str] = {}
            cas = _first(row, "cas", "cas_number", "casrn")
            if cas:
                xrefs["cas"] = cas
            reference = _first(row, "reference", "citation", "doi", "source_ref")
            if reference:
                xrefs["reference"] = reference
            yield ReferenceRecord(
                formula=formula,
                inchikey=_first(row, "inchikey", "inchi_key"),
                name=name,
                smiles=_first(row, "smiles"),
                inchi=_first(row, "inchi"),
                source=self.name,
                source_native_id=native_id,
                xrefs=xrefs,
                license=_first(row, "license") or self.license,
            )
