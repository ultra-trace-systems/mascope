# Mascope Reference

Ingestion and query for mirrored **free-to-use public chemistry databases**,
adding formula -> identity annotation on top of Mascope's de novo composition
engine, and for the **open MS/MS spectral libraries** a spectral-similarity
scorer reads. See [`docs/dev/public_database_integration.md`](../../docs/dev/public_database_integration.md)
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
- **MS/MS reference spectra** - adapters for MassBank, MoNA and GNPS
  ([`adapters/massbank.py`](src/mascope_reference/adapters/massbank.py),
  [`mona.py`](src/mascope_reference/adapters/mona.py),
  [`gnps.py`](src/mascope_reference/adapters/gnps.py)) stream a library dump
  into [`SpectrumRecord`](src/mascope_reference/spectrum.py) instances, each
  with its own licence; `ingest_spectra` loads them into `reference_spectrum`
  as a versioned load, and
  [`spectral_query.py`](src/mascope_reference/spectral_query.py) reads them
  back `by_inchikey` or `by_precursor`, licence-filtered. Nothing scores them
  yet: that is MAS-360, the candidate ranking by spectral similarity.

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

### MS/MS spectral libraries

Loaded with the same `sync`, as spectra rather than compounds. Stage A never
matches a spectrum, so a library has no window, and the licence gate of
[`reference_licenses`](../../docs/maintaining.md#reference-licence-gating),
which is Stage A's, does not apply to it: the read path takes the licences to
keep from its caller. Only MS2 spectra that were measured, with a precursor
m/z and at least one peak, are kept; a load counts what it left out by reason.

| Source | Content | The dump the adapter reads | Licence |
| --- | --- | --- | --- |
| `massbank` [MassBank Europe](https://massbank.eu/) | ~110k MS2 spectra, vendor-neutral | The source archive of a [MassBank-data release](https://github.com/MassBank/MassBank-data/releases) (`MassBank-data-<tag>.tar.gz`), read without unpacking, or a file of records. Not the release's MSP files, which leave the licence out | Per record: CC0, CC BY, CC BY-SA, CC BY-NC, CC BY-NC-SA and others, stored as `CC-BY-NC-SA` and so on |
| `mona` [MoNA](https://mona.fiehnlab.ucdavis.edu/downloads) | Metadata-rich community and imported libraries | An MSP export (`MoNA-export-<library>.msp`, unzipped) | Per record where one is stated (`license=` in `Comments`); `mona-unstated` where none is, which is most of its libraries. In-silico spectra are skipped |
| `gnps` [GNPS](https://external.gnps2.org/gnpslibrary) | Community MS/MS, natural products | A library MGF (`GNPS-LIBRARY.mgf`, `ALL_GNPS_NO_PROPOGATED.mgf`) | `CC0` for GNPS's own libraries (`ORGANISM=GNPS-...`); `gnps-unstated` for the ones it imports. Library classes other than 1, 2 and 3 are skipped |

Each spectrum records its precursor m/z, adduct (in Mascope's standard
notation where it can be written in it), polarity, the collision energy as a
number with its kind (`nce` or `ev`) where the library names one value and its
unit and as written always, the centroid list, the compound's name, canonical
formula, structure and InChIKey where the library gives them, and its licence.

**Deliberately excluded**, because they are not free to use: the NIST MS
library (paid), mzCloud (browse-only, not redistributable), METLIN
(restricted), CAS/SciFinder (paid) and ChemSpider (non-commercial key). KEGG
is conditional - its web API is free for academic use, but bulk FTP is a paid
subscription and commercial use is restricted - and is treated as not free.

## Ownership boundary

The `reference_source` / `reference_compound` / `reference_spectrum` tables are
defined by the backend ORM models (single source of truth for Alembic). This library addresses them by
column name through lightweight Core handles ([`schema.py`](src/mascope_reference/schema.py))
so the CLI can ingest and the backend can query without either importing the
other. The backend's `tests/migrations/test_reference_schema_parity.py` asserts the two
stay in lockstep. A `reference_source` row says which kind of load it is
(`compound` or `spectrum`), and a name holds loads of one kind only, so a
library loaded under a compound source's name is refused rather than allowed
to deactivate it.

## Ingesting a source

Fetch a dump out of band, then:

```sh
mascope reference sync pubchem /path/to/Compound.sdf.gz --version 2026-07
mascope reference sync massbank /path/to/MassBank-data-2026.03.tar.gz --version 2026.03
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
