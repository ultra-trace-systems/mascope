"""ChEBI adapter.

Reads the ChEBI complete SDF dump (``ChEBI_complete.sdf``). Curated
biologically-relevant molecules. Licensed CC BY 4.0, so attribution is carried
per record.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._io import read_sdf_records
from mascope_reference.record import ReferenceRecord


class ChebiAdapter:
    """Adapter for ChEBI complete SDF dumps."""

    name = "chebi"
    license = "CC-BY-4.0"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        for fields in read_sdf_records(path):
            chebi_id = fields.get("ChEBI ID")
            # ChEBI publishes formulae as a possibly multi-line "Formulae" field;
            # the first entry is the canonical neutral formula.
            formulae = fields.get("Formulae", "")
            formula = formulae.splitlines()[0].strip() if formulae else ""
            if not chebi_id or not formula:
                continue
            yield ReferenceRecord(
                formula=formula,
                inchikey=fields.get("InChIKey") or None,
                name=fields.get("ChEBI Name") or None,
                smiles=fields.get("SMILES") or None,
                inchi=fields.get("InChI") or None,
                source=self.name,
                source_native_id=chebi_id,
                xrefs={"chebi_id": chebi_id},
                license=self.license,
            )
