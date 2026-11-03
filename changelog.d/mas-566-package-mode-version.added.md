- **A dataset package tells the versions of an ionization mode apart.**
  Each entry under `ionization_modes` in the package's manifest now carries
  its `version`, the version it replaced (`supersedes_id`) and the labelled
  reagent's purity it ran at (`label_purity`). The package's README and the
  **Prepare for publication** dialog name an edited mode with its version, so
  samples on two versions of one mode no longer show two identical entries.
