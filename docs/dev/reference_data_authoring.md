`ULTRA TRACE MASCOPE - GUIDE - AUTHORING REFERENCE DATA`

# Authoring a custom reference compound database

Mascope annotates assigned formulas with **known compounds** that share the same
formula, pulled from mirrored reference databases (see
[public_database_integration.md](public_database_integration.md)). The public
sources (PubChem, EPA CompTox, ChEBI, ...) cover a lot, but not everything: in
atmospheric science in particular, whole families of compounds have been
characterized in the literature - published as **peak lists** in papers and
supplements - long before they land in a public database. HOMs (highly
oxygenated organic molecules), specific oxidation schemes, tracer lists, and
in-house standards are common examples.

This guide shows how to turn such a list into Mascope reference data, so those
compounds light up in peak assignment just like public-database entries.

> This is an authoring guide. For the architecture (tables, query path, the
> public-database adapters) see
> [public_database_integration.md](public_database_integration.md).

---

## 1. What you need

A table of compounds with, at minimum, a **neutral molecular formula** per row.
Anything else - name, structure, identifiers, the citation - is optional and
carried through to the annotation if present.

Mascope reads it through the built-in **`custom`** adapter, which takes a flat
CSV or TSV with this column schema (headers are case-insensitive; common aliases
are accepted):

| Column | Required | Meaning |
| --- | --- | --- |
| `formula` | **yes** | Neutral molecular formula, e.g. `C10H16O3`. Alias: `molecular_formula`. |
| `name` | no | Preferred compound name (shown in the assignment UI). Aliases: `compound`, `compound_name`. |
| `inchikey` | no | InChIKey. Enables cross-source de-duplication. |
| `smiles` | no | SMILES structure. |
| `inchi` | no | InChI structure. |
| `cas` | no | CAS number. Stored under `xrefs.cas`. Aliases: `cas_number`, `casrn`. |
| `reference` | no | Citation / DOI of the source list. Stored under `xrefs.reference`. Aliases: `citation`, `doi`. |
| `id` | no | Your identifier for the row. Falls back to `name`, then a row number. Aliases: `identifier`, `compound_id`. |
| `license` | no | Per-record license (defaults to `custom`). |

Only `formula` is needed. Rows without a formula are skipped.

### Formula notes

- **Neutral formula**, not the detected ion. Mascope compares against the neutral
  formula the composition engine assigns, so give `C10H16O3`, not `C10H15O3-`
  or the reagent adduct `C10H16O3.NO3`.
- **Any element order** works - formulas are canonicalized to Hill order on
  ingest (`C10H16O3`, `O3C10H16`, and `H16C10O3` all become `C10H16O3`), the same
  canonicalization the assignment engine uses, so keys always match.
- **Isotopes** are collapsed to the base element (`[13C]` -> `C`), matching how
  assigned neutral formulas are stored.
- The **monoisotopic mass** is computed for you on ingest - you do not need a
  mass column.

---

## 2. Example: an atmospheric HOM peak list

Say a paper reports α-pinene highly oxygenated products. Put them in a CSV
(`apinene_hom.csv`):

```csv
name,formula,reference,inchikey
Pinonic acid,C10H16O3,10.5194/acp-example-2019,BFKGXSVWFIQNJS-UHFFFAOYSA-N
Pinic acid,C9H14O4,10.5194/acp-example-2019,
C10 HOM (C10H16O5),C10H16O5,10.5194/acp-example-2019,
C10 HOM (C10H16O7),C10H16O7,10.5194/acp-example-2019,
C10 dimer (C20H32O9),C20H32O9,10.5194/acp-example-2019,
```

Names are free-form - use the accepted trivial name where one exists, or a
descriptive label (`C10 HOM (C10H16O5)`) where the exact structure is unresolved.
Every row still becomes a queryable identity keyed on its formula.

> A larger, ready-to-load example ships in
> [`libraries/reference/examples/atmospheric_organics.csv`](../../libraries/reference/examples/atmospheric_organics.csv)
> (79 atmospheric organics - dicarboxylic acids, terpene and isoprene oxidation
> products, biomass-burning tracers) with a
> [README](../../libraries/reference/examples/README.md) documenting its
> provenance. It is a good template and a quick way to see the reference path
> light up peaks.

> **Watch for commas in names.** A plain CSV splits on every comma, so a name
> like `2-methyl-1,3,4-trihydroxybutene` would break the row. Quote such fields
> (`"2-methyl-1,3,4-..."`) or use a comma-free label.

---

## 3. Ingest it

From a monorepo checkout (the `reference` command is a developer command):

```sh
mascope reference sync custom apinene_hom.csv --name apinene-hom-2019 --version 2019
```

- `custom` selects the flat-file adapter (`mascope reference sources` lists all).
- `--name` gives the load its **own provenance name**. Always set it for custom
  lists: it is what appears as the source of an annotation, and it keeps several
  lists from colliding (see below).
- `--version` tags the load for reproducibility (a DOI, a date, `v1` - your
  choice).

Check what is loaded:

```sh
mascope reference status
```

That is it. The compounds are now annotated wherever the assignment engine
produces a matching formula - the **Identity** column in peak assignment, and the
`known_only` suspect-screening prior on the composition query.

### In a deployment (production)

`mascope reference` is a developer command: it pulls the chemistry dependencies
(via `mascope_reference` -> `mascope_tools`) that are deliberately kept out of the
lightweight operator CLI. Two ways to load reference data into a running
deployment, depending on how it was installed:

- **Deployment from a source checkout** - run the CLI command against the prod
  env directly (it connects to the deployment's database):

  ```sh
  mascope --env prod reference sync custom /path/to/list.csv --name my-list -v 2024
  ```

- **Any deployment (incl. a pip-installed operator CLI with no checkout)** - the
  backend image already ships the chemistry dependencies, so run the ingest
  **inside the backend container**. Mount your dump where the container can read
  it, then:

  ```sh
  docker compose exec backend \
      python -m mascope_backend.db.scripts.reference_sync \
      custom /data/list.csv --name my-list --version 2024
  ```

  It takes the same arguments as `mascope reference sync` (`source`, `file`,
  `--version`, `--name`, `--batch-size`, `--prune`, `--stage`) and runs the
  identical versioned ingest - just where the dependencies live. The database is
  the one the backend is already configured for, so no connection flags are
  needed.

A polished operator-facing wrapper (`mascope prod reference ...`) is planned as
part of productization (Phase 5 in [public_database_integration.md](public_database_integration.md)).

---

## 4. Managing lists over time

**Several lists coexist.** Each `--name` is an independent source, so you can
load as many peak lists as you like and they all annotate together:

```sh
mascope reference sync custom apinene_hom.csv    --name apinene-hom-2019 -v 2019
mascope reference sync custom pfas_suspects.csv  --name norman-pfas       -v 2023
mascope reference sync custom lab_standards.csv  --name inhouse-standards -v 2024
```

**Re-loading replaces, versioned.** Syncing the same `--name` again creates a new
active version and deactivates the previous one; annotations immediately use the
new load. The old version stays on disk for reproducibility unless you pass
`--prune` (which deletes it):

```sh
mascope reference sync custom apinene_hom.csv --name apinene-hom-2019 -v 2020 --prune
```

**Stage without exposing.** `--stage` ingests a load without activating it (it
does not replace the current version) - useful to prepare an update and flip it
in later by re-syncing without `--stage`.

**License / attribution.** Set a per-record `license` column if the list carries
one; it is carried through to every annotation, so results stay attributable.
Put the paper's DOI in a `reference` column and it travels with each compound.

---

## 5. When to use a public-source adapter instead

If your data already comes as a public-database dump (a PubChem SDF, a CompTox
CSV, a ChEBI SDF, ...), use that source's adapter directly - it understands the
native format and identifiers:

```sh
mascope reference sync comptox DSSTox.csv --version 2024
```

Reach for `custom` when the compounds are **hand-authored or paper-derived** and
do not fit a public source's schema. The two live side by side; a formula matched
by both a public source and your custom list shows both identities (collapsed on
InChIKey where available).

See [public_database_integration.md](public_database_integration.md) §2 for the
public sources and where to obtain their dumps.
