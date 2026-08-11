"""The normalized reference-compound record.

Every ETL adapter emits :class:`ReferenceRecord` instances so that source
quirks never leak past the ingestion boundary. A record is the common shape a
single compound takes in a single source: its canonical formula and
monoisotopic mass (both computed on ingest by :mod:`mascope_reference.normalize`
so reference and de novo formulas compare identically), its identity fields,
its source-native id, cross-references, and a per-record license tag.
"""

from pydantic import BaseModel, Field


class ReferenceRecord(BaseModel):
    """One compound as it appears in one source.

    Adapters populate the identity fields plus the source's raw ``formula``;
    :func:`mascope_reference.normalize.finalize` then overwrites ``formula``
    with its canonical Hill form and fills ``monoisotopic_mass``. Keeping one
    record per (compound, source) preserves provenance and the per-record
    license; cross-source collapse on ``inchikey`` is a separate view.
    """

    formula: str = Field(
        description="Neutral formula. Canonical Hill order after finalize()."
    )
    monoisotopic_mass: float | None = Field(
        default=None,
        description="Monoisotopic mass of the neutral formula, computed on ingest.",
    )
    inchikey: str | None = Field(
        default=None, description="InChIKey - the cross-source dedup key."
    )
    name: str | None = Field(default=None, description="Preferred compound name.")
    smiles: str | None = Field(default=None)
    inchi: str | None = Field(default=None)
    source: str = Field(
        description="Source name, e.g. 'pubchem', 'comptox'. Set by the adapter."
    )
    source_native_id: str = Field(
        description="Source-native identifier, e.g. PubChem CID or DTXSID."
    )
    xrefs: dict = Field(
        default_factory=dict,
        description="Cross-references to other sources, keyed by source name.",
    )
    license: str = Field(
        description="Per-record license tag, carried from ingest through to results."
    )
