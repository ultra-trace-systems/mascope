"""Mascope reference database library.

Ingestion and query for mirrored public chemistry databases. The library owns
source-specific ETL (:mod:`mascope_reference.adapters`), the normalized record
model (:class:`ReferenceRecord`), formula/mass canonicalization
(:mod:`mascope_reference.normalize`), and the indexed read path
(:mod:`mascope_reference.query`). The mirror's tables are owned by the backend;
the query functions take the ORM model classes as arguments so this library
never imports the backend.
"""

from mascope_reference.dedup import collapse_by_inchikey
from mascope_reference.ingest import IngestResult, ingest
from mascope_reference.known import (
    KnownComposition,
    KnownIdentity,
    iter_known_compositions,
)
from mascope_reference.normalize import (
    canonical_formula,
    finalize,
    monoisotopic_mass,
)
from mascope_reference.query import (
    annotate_formulas,
    by_formula,
    by_mass_window,
)
from mascope_reference.record import ReferenceRecord
from mascope_reference.sources import available_sources, get_adapter


__all__ = [
    "ReferenceRecord",
    "canonical_formula",
    "monoisotopic_mass",
    "finalize",
    "by_formula",
    "by_mass_window",
    "annotate_formulas",
    "collapse_by_inchikey",
    "iter_known_compositions",
    "KnownComposition",
    "KnownIdentity",
    "available_sources",
    "get_adapter",
    "ingest",
    "IngestResult",
]
