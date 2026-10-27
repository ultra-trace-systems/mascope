- **The deposit package is an RO-Crate.** `mascope export package` now also
  writes `ro-crate-metadata.json`, an RO-Crate 1.2 description of the
  package that data repositories and RO-Crate viewers read. It names the
  dataset's licence, its authors by ORCID iD, funding, related identifiers
  and PSI-MS terms, every file with its SHA-256, and the Mascope version that
  wrote the package. `manifest.json` is unchanged beside it.
