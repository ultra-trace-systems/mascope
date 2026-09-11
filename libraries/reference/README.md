# Mascope Reference

Ingestion and query for mirrored **free-to-use public chemistry databases**,
adding formula -> identity annotation on top of Mascope's de novo composition
engine. See [`docs/dev/public_database_integration.md`](../../docs/dev/public_database_integration.md)
for the design.

## What it does

- **ETL adapters** ([`adapters/`](src/mascope_reference/adapters)) - one per
  source (PubChem, EPA CompTox, ChEBI, HMDB, LIPID MAPS, COCONUT, NORMAN), plus a
  generic `custom` adapter for hand-authored / paper-derived peak lists (CSV/TSV)
  and a `peaklist` adapter for self-describing JSON lists. Each streams a dump
  into normalized [`ReferenceRecord`](src/mascope_reference/record.py)
  instances. Pure transforms - no database, no chemistry engine.
- **Shipped lists** ([`lists/`](src/mascope_reference/lists)) - Mascope's own
  curated atmospheric CIMS lists, one schema 2 file per list.
  [`peaklist.py`](src/mascope_reference/peaklist.py) defines the format and the
  checks a list must pass, and [`seed.py`](src/mascope_reference/seed.py) loads
  each list as its own versioned source.
- **Normalization** ([`normalize.py`](src/mascope_reference/normalize.py)) -
  canonicalizes formulas to the *same* Hill order as the de novo path (reusing
  `mascope_tools.composition`) and computes monoisotopic mass on ingest, so
  reference and assigned formulas compare identically.
- **Versioned ingest** ([`ingest.py`](src/mascope_reference/ingest.py)) - each
  load records a `reference_source` row and bulk-inserts `reference_compound`
  rows; the new load becomes the active version of its source.
- **Indexed query** ([`query.py`](src/mascope_reference/query.py)) -
  `by_formula`, `by_mass_window`, and batched `annotate_formulas`, over the
  active version of each source.
- **Cross-source dedup** ([`dedup.py`](src/mascope_reference/dedup.py)) -
  `collapse_by_inchikey` merges the same molecule from multiple sources into one
  identity when a single answer is wanted.

## Ownership boundary

The `reference_source` / `reference_compound` tables are defined by the backend
ORM models (single source of truth for Alembic). This library addresses them by
column name through lightweight Core handles ([`schema.py`](src/mascope_reference/schema.py))
so the CLI can ingest and the backend can query without either importing the
other. `tests/test_schema.py` asserts the two stay in lockstep.

## Ingesting a source

Fetch a dump out of band, then:

```sh
mascope reference sync pubchem /path/to/Compound.sdf.gz --version 2026-07
mascope reference status
```

To author your own reference data from a published peak list (e.g. atmospheric
compounds not yet in the public databases), use the `custom` adapter - see
[docs/dev/reference_data_authoring.md](../../docs/dev/reference_data_authoring.md).

## Loading the shipped lists

```sh
mascope reference seed --list    # what ships, and what loads by default
mascope reference seed           # load the default lists
```

Nothing loads them unasked except the local demo. `tests/test_seed_lists.py`
holds every shipped list to the format's checks.
