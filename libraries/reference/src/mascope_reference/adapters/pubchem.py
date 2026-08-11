"""PubChem adapter.

Reads the PubChem Compound SDF dumps (the ``Compound/CURRENT-Full/SDF`` FTP
files). Every field this adapter needs is a tagged SDF data field, so no
chemistry toolkit is required to parse the connection table. PubChem is public
domain.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.adapters._io import read_sdf_records
from mascope_reference.record import ReferenceRecord


class PubChemAdapter:
    """Adapter for PubChem Compound SDF dumps."""

    name = "pubchem"
    license = "public-domain"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        for fields in read_sdf_records(path):
            cid = fields.get("PUBCHEM_COMPOUND_CID")
            formula = fields.get("PUBCHEM_MOLECULAR_FORMULA")
            if not cid or not formula:
                # A record with no id or no formula carries no usable annotation.
                continue
            name = (
                fields.get("PUBCHEM_IUPAC_NAME")
                or fields.get("PUBCHEM_IUPAC_TRADITIONAL_NAME")
                or None
            )
            smiles = (
                fields.get("PUBCHEM_OPENEYE_CAN_SMILES")
                or fields.get("PUBCHEM_SMILES")
                or None
            )
            yield ReferenceRecord(
                formula=formula,
                inchikey=fields.get("PUBCHEM_IUPAC_INCHIKEY") or None,
                name=name,
                smiles=smiles,
                inchi=fields.get("PUBCHEM_IUPAC_INCHI") or None,
                source=self.name,
                source_native_id=cid,
                xrefs={"pubchem_cid": cid},
                license=self.license,
            )
