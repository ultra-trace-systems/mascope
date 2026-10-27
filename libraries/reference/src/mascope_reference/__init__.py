"""Mascope reference database library.

Ingestion and query for mirrored public chemistry databases and MS/MS spectral
libraries. The library owns source-specific ETL (:mod:`mascope_reference.adapters`),
the normalized record models (:class:`ReferenceRecord`, :class:`SpectrumRecord`),
formula/mass canonicalization (:mod:`mascope_reference.normalize`), and the indexed
read paths (:mod:`mascope_reference.query` for compounds,
:mod:`mascope_reference.spectral_query` for spectra). The mirror's tables are owned by the backend;
the query functions take the ORM model classes as arguments so this library
never imports the backend.
"""

from mascope_reference.dedup import collapse_by_inchikey
from mascope_reference.ingest import (
    IngestResult,
    SpectrumIngestResult,
    ingest,
    ingest_spectra,
)
from mascope_reference.known import (
    KnownComposition,
    KnownIdentity,
    KnownListing,
    iter_known_compositions,
    known_listings,
    known_state_fingerprint,
)
from mascope_reference.normalize import (
    canonical_formula,
    finalize,
    monoisotopic_mass,
)
from mascope_reference.peaklist import (
    PeakList,
    PeakListError,
    list_problems,
    read_peak_list,
)
from mascope_reference.query import (
    SourcePage,
    SourceRow,
    StructureCandidate,
    StructurePage,
    active_sources,
    annotate_formulas,
    by_formula,
    by_mass_window,
    source_compounds,
    structures_by_formula,
    xref_cas_number,
)
from mascope_reference.record import ReferenceRecord
from mascope_reference.seed import SeedOutcome, catalogue, seed
from mascope_reference.sources import (
    available_sources,
    available_spectrum_sources,
    get_adapter,
    get_spectrum_adapter,
    is_spectrum_source,
)
from mascope_reference.spectral_query import by_inchikey, by_precursor
from mascope_reference.spectrum import SkippedSpectrum, SpectrumRecord


__all__ = [
    "PeakList",
    "PeakListError",
    "list_problems",
    "read_peak_list",
    "SeedOutcome",
    "catalogue",
    "seed",
    "ReferenceRecord",
    "canonical_formula",
    "monoisotopic_mass",
    "finalize",
    "by_formula",
    "by_mass_window",
    "annotate_formulas",
    "structures_by_formula",
    "active_sources",
    "source_compounds",
    "SourceRow",
    "SourcePage",
    "StructureCandidate",
    "StructurePage",
    "xref_cas_number",
    "collapse_by_inchikey",
    "iter_known_compositions",
    "known_listings",
    "known_state_fingerprint",
    "KnownComposition",
    "KnownIdentity",
    "KnownListing",
    "available_sources",
    "get_adapter",
    "ingest",
    "IngestResult",
    "SpectrumRecord",
    "SkippedSpectrum",
    "available_spectrum_sources",
    "get_spectrum_adapter",
    "is_spectrum_source",
    "ingest_spectra",
    "SpectrumIngestResult",
    "by_inchikey",
    "by_precursor",
]
