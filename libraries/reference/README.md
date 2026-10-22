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
  rows; the new load becomes the active version of its source, and `deactivate`
  takes a source's active load out.
- **Source scope** ([`scope.py`](src/mascope_reference/scope.py)) - what a
  source row records about how its compounds may be matched: a window of
  elements, carbon count and mass, a radical allowance and a polarity. A public
  database loads at the atmospheric window and a list someone authored loads
  unbounded.
- **Indexed query** ([`query.py`](src/mascope_reference/query.py)) -
  `by_formula`, `by_mass_window`, and batched `annotate_formulas`, over the
  active version of each source.
- **Cross-source dedup** ([`dedup.py`](src/mascope_reference/dedup.py)) -
  `collapse_by_inchikey` merges the same molecule from multiple sources into one
  identity when a single answer is wanted.

## Sources

Every source is mirrored from a dump the project publishes, never queried
live: fetch the dump out of band and point `mascope reference sync <source>
<file>` at it. All of them are free to use; the licence each record carries is
what the operator's
[licence gate](../../docs/maintaining.md#reference-licence-gating) filters on.

| Source | Content | The dump the adapter reads | Licence |
| --- | --- | --- | --- |
| `pubchem` [PubChem](https://pubchem.ncbi.nlm.nih.gov/) | ~120M compounds; name, SMILES, InChIKey, exact and monoisotopic mass, formula, cross-references | The Compound SDF dumps (`Compound/CURRENT-Full/SDF` on the FTP site) | Public domain |
| `comptox` [EPA CompTox / CCTE](https://comptox.epa.gov/dashboard/) | ~1.2M environment-relevant chemicals, "MS-ready" structures | The CompTox Chemicals Dashboard bulk CSV export (DSSTox) | Public domain |
| `chebi` [ChEBI](https://www.ebi.ac.uk/chebi/) | ~200k curated biologically relevant molecules, with an ontology | `ChEBI_complete.sdf` | CC BY 4.0 |
| `hmdb` [HMDB](https://hmdb.ca/) | ~250k human metabolites | `hmdb_metabolites.xml` | Free with attribution; verify commercial terms first |
| `lipidmaps` [LIPID MAPS (LMSD)](https://www.lipidmaps.org/) | ~48k curated lipids, systematic nomenclature | The LMSD SDF dump | CC BY 4.0 |
| `coconut` [COCONUT](https://coconut.naturalproducts.net/) | ~400k natural products | The bulk CSV export | CC0 / open |
| `norman` [NORMAN Suspect List Exchange](https://www.norman-network.com/nds/SLE/) | Curated suspect lists (PFAS, pesticides, ...) | A suspect list, or the merged SusDat table, as CSV (distributed via CompTox) | Open |

`custom` (a flat CSV or TSV) and `peaklist` (a self-describing JSON list)
read hand-authored lists; the authoring guide
([reference_data_authoring.md](../../docs/dev/reference_data_authoring.md),
moving to the user documentation) has their formats.

**Deliberately excluded**, because they are not free to use: the NIST MS
library (paid), mzCloud (browse-only, not redistributable), METLIN
(restricted), CAS/SciFinder (paid) and ChemSpider (non-commercial key). KEGG
is conditional - its web API is free for academic use, but bulk FTP is a paid
subscription and commercial use is restricted - and is treated as not free.

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
compounds not yet in the public databases), use the `custom` adapter - see the
user guide [Reference compound lists](../../docs/user/guides/reference-lists.md).

## Loading the shipped lists

```sh
mascope reference seed --list    # what ships, and what loads by default
mascope reference seed           # load the default lists
```

Nothing loads them unasked except the local demo. `tests/test_seed_lists.py`
holds every shipped list to the format's checks.
