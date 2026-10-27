"""Lightweight Core table handles for the reference mirror.

The physical tables are *defined* by the backend ORM models (the single source
of truth for Alembic migrations and constraint naming). This module gives the
library its own column-name-only handles onto those same tables so the query
and ingest paths can build statements without importing the backend - the CLI
ingests and the backend queries through one shared column vocabulary.

The column names here MUST stay in lockstep with the backend ORM models; a
schema test asserts they do.
"""

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    column,
    table,
)


REFERENCE_SOURCE_TABLE = "reference_source"
REFERENCE_COMPOUND_TABLE = "reference_compound"
REFERENCE_SPECTRUM_TABLE = "reference_spectrum"

#: What a ``reference_source`` row's load holds: compounds, which annotation and
#: Stage A read, or MS/MS spectra, which neither does.
COMPOUND_KIND = "compound"
SPECTRUM_KIND = "spectrum"
SOURCE_KINDS = (COMPOUND_KIND, SPECTRUM_KIND)

# Widths of the bounded varchar columns, mirrored from the backend ORM models
# and enforced by the ingest path before insert. Postgres answers an over-long
# value with StringDataRightTruncation, which aborts the statement - and a
# multi-hour load with it - over one systematic lipid name or one spelled-out
# licence string. Keep these in lockstep with the ORM like the column names.
SOURCE_NAME_LENGTH = 64
SOURCE_VERSION_LENGTH = 128
LICENSE_LENGTH = 64
INCHIKEY_LENGTH = 27
SOURCE_NATIVE_ID_LENGTH = 128
FORMULA_LENGTH = 512
POLARITY_LENGTH = 8
SOURCE_KIND_LENGTH = 16
ADDUCT_LENGTH = 64
ENERGY_KIND_LENGTH = 8
ENERGY_TEXT_LENGTH = 128


#: One row per ingested source + version (provenance, license, active flag).
reference_source = table(
    REFERENCE_SOURCE_TABLE,
    column("reference_source_id", Integer),
    column("name", String(SOURCE_NAME_LENGTH)),
    column("version", String(SOURCE_VERSION_LENGTH)),
    column("license", String(LICENSE_LENGTH)),
    column("record_count", Integer),
    column("is_active", Boolean),
    column("ingested_at", TIMESTAMP(timezone=True)),
    # How the source's compounds may be matched (mascope_reference.scope): the
    # window as a JSON object whose null fields are unbounded, whether its
    # radicals may be matched, and its polarity (NULL for both).
    column("known_window", JSON),
    column("allow_radicals", Boolean),
    column("polarity", String(POLARITY_LENGTH)),
    # What the load holds: COMPOUND_KIND or SPECTRUM_KIND. One name holds loads
    # of one kind, so activating a load never deactivates the other kind's.
    column("kind", String(SOURCE_KIND_LENGTH)),
)

#: One row per (compound, source version). ``xrefs`` is typed JSON so the dict
#: binds correctly on both Postgres (production) and SQLite (tests).
reference_compound = table(
    REFERENCE_COMPOUND_TABLE,
    column("reference_compound_id", Integer),
    column("reference_source_id", Integer),
    column("formula", String(FORMULA_LENGTH)),
    column("monoisotopic_mass", Float),
    # Intrinsic charge; NULL/0 = neutral. Recorded, never matched (issue #1726):
    # ingest still rejects charge-suffixed formulas, so nothing writes it yet,
    # and iter_known_compositions excludes charged rows either way.
    column("charge", Integer),
    column("inchikey", String(INCHIKEY_LENGTH)),
    column("name", Text),
    column("smiles", Text),
    column("inchi", Text),
    column("source_native_id", String(SOURCE_NATIVE_ID_LENGTH)),
    column("xrefs", JSON),
    column("license", String(LICENSE_LENGTH)),
)

#: One row per MS/MS spectrum per source version. The window columns of the
#: source row mean nothing for a spectral load: Stage A never reads spectra.
reference_spectrum = table(
    REFERENCE_SPECTRUM_TABLE,
    column("reference_spectrum_id", Integer),
    column("reference_source_id", Integer),
    column("source_native_id", String(SOURCE_NATIVE_ID_LENGTH)),
    column("name", Text),
    # The compound's canonical neutral formula, NULL where the source gives none
    # Mascope can read.
    column("formula", String(FORMULA_LENGTH)),
    column("inchikey", String(INCHIKEY_LENGTH)),
    column("smiles", Text),
    column("inchi", Text),
    column("precursor_mz", Float),
    column("adduct", String(ADDUCT_LENGTH)),
    column("polarity", String(POLARITY_LENGTH)),
    # The energy as a number with its kind ("nce" or "ev") where the source
    # names one value and its unit, and as the source wrote it always.
    column("collision_energy", Float),
    column("collision_energy_kind", String(ENERGY_KIND_LENGTH)),
    column("collision_energy_text", String(ENERGY_TEXT_LENGTH)),
    # The centroids, [[m/z, intensity], ...] in ascending m/z, intensities as
    # the source gives them.
    column("peaks", JSON),
    column("license", String(LICENSE_LENGTH)),
)

# Column-name lists reused by the ingest path (everything except the
# autoincrement primary key, which the database assigns).
SOURCE_INSERT_COLUMNS = (
    "name",
    "version",
    "license",
    "record_count",
    "is_active",
    "ingested_at",
    "known_window",
    "allow_radicals",
    "polarity",
    "kind",
)
COMPOUND_INSERT_COLUMNS = (
    "reference_source_id",
    "formula",
    "monoisotopic_mass",
    "inchikey",
    "name",
    "smiles",
    "inchi",
    "source_native_id",
    "xrefs",
    "license",
)
SPECTRUM_INSERT_COLUMNS = (
    "reference_source_id",
    "source_native_id",
    "name",
    "formula",
    "inchikey",
    "smiles",
    "inchi",
    "precursor_mz",
    "adduct",
    "polarity",
    "collision_energy",
    "collision_energy_kind",
    "collision_energy_text",
    "peaks",
    "license",
)
