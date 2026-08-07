`ULTRA TRACE MASCOPE - EXAMPLE REFERENCE DATA`

# Example reference database: atmospheric organics

`atmospheric_organics.csv` is a small, curated reference list of organic
compounds routinely observed in atmospheric mass spectrometry - dicarboxylic
acids, monoterpene and isoprene oxidation products, biomass-burning tracers, and
a few organosulfur / inorganic acids. It exists so the peak-assignment engine
has **real, non-trivial reference data** to run against without depending on a
large public-database mirror.

It is deliberately small and illustrative, not exhaustive. Use it to exercise
the reference path end to end, as the worked example for
[reference_data_authoring.md](../../../docs/dev/reference_data_authoring.md), and
as a template for your own hand-authored lists.

## What it demonstrates

- **The `custom` adapter format** - a flat `name,formula,reference` CSV (only
  `formula` is required); see the authoring guide for the full schema.
- **The atmospheric bound** - every compound is within the window Stage A of
  peak assignment expands (elements in C/H/N/O/S, C <= 40, monoisotopic mass
  <= 700 Da), so the whole list is matchable.
- **One formula, many identities** - 17 formulas in this list are shared by two
  or three isomers (e.g. `C4H6O4` = succinic + methylmalonic; `C6H10O5` =
  levoglucosan + mannosan + galactosan; `C8H6O4` = phthalic + isophthalic +
  terephthalic). Peak assignment attaches all matching identities to the peak,
  so these exercise the one-to-many annotation path.

## Load it

From a monorepo checkout:

```sh
mascope reference sync custom libraries/reference/examples/atmospheric_organics.csv \
    --name atmospheric-organics --version 2025.07
```

or, in a deployment, inside the backend container (see the authoring guide):

```sh
docker compose exec backend python -m mascope_backend.db.scripts.reference_sync \
    custom /data/atmospheric_organics.csv --name atmospheric-organics --version 2025.07
```

Formulas are canonicalized to Hill order and their monoisotopic masses computed
on ingest; you do not supply a mass column.

## Provenance

Compounds and their roles as atmospheric tracers are drawn from the standard
literature, e.g. Kawamura and Ikushima (1993) for the aliphatic dicarboxylic
acid series, Yu et al. (1999) and Claeys et al. (2009) for alpha-pinene
oxidation products, Szmigielski et al. (2007) for MBTCA, Claeys et al. (2004),
Wang et al. (2005) and Paulot et al. (2009) for isoprene SOA tracers, Simoneit
et al. (1999) for biomass-burning tracers, and Saltzman et al. (1983) for
methanesulfonic acid. The per-row `reference` column carries an author-year
label where a standard primary reference exists; it is provenance, not an
exhaustive citation. Molecular formulas are the verifiable data - each was
parsed and mass-checked on authoring.
