"""LIPID MAPS (LMSD) adapter.

Reads the LIPID MAPS Structure Database SDF dump. Curated lipids with
systematic nomenclature. Licensed CC BY 4.0.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._io import read_sdf_records
from mascope_reference.record import ReferenceRecord


class LipidMapsAdapter:
    """Adapter for LIPID MAPS (LMSD) SDF dumps."""

    name = "lipidmaps"
    license = "CC-BY-4.0"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        for fields in read_sdf_records(path):
            lm_id = fields.get("LM_ID")
            formula = fields.get("FORMULA")
            if not lm_id or not formula:
                continue
            name = (
                fields.get("NAME")
                or fields.get("COMMON_NAME")
                or fields.get("SYSTEMATIC_NAME")
                or None
            )
            yield ReferenceRecord(
                formula=formula,
                inchikey=fields.get("INCHI_KEY") or fields.get("INCHIKEY") or None,
                name=name,
                smiles=fields.get("SMILES") or None,
                inchi=fields.get("INCHI") or None,
                source=self.name,
                source_native_id=lm_id,
                xrefs={"lm_id": lm_id},
                license=self.license,
            )
