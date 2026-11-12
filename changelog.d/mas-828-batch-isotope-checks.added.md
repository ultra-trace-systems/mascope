- **Read batch checks on the batch ledger.** A new *Read batch checks* button
  beside *Strip isotopologue rows* records three facts only a batch can state
  of a batch peak's reading, in a new *Facts* column of the *Batch peaks*
  ledger and on the inspector. A **halogen lock**: a chlorine or bromine
  compound whose 37Cl or 81Br line *Stamp isotopologues* stamped, sitting at
  the exact spacing from it (within four times the batch's own mass scatter,
  at least 1 ppm); where a 30Si line could sit there instead, the 29Si line
  decides whether the line is silicon's. A **14N twin**: a labelled ion's
  impurity line one unit down, at the share the reagent's own ions show (the
  setup's label purity where the batch holds none), present or missing in
  the samples whose run would notice it absent. A
  **urea pair**: a uronium compound's protonated and urea-clustered peaks,
  established where *Read co-variation* reads them co-varying. The facts are
  display-only: no tier moves on them. Runs as a batch run
  (`POST /api/batch-peaks/batch/{id}/read-batch-checks`); the ledger, in the
  API and the SDK, carries them as `batch_checks`, and the ledger export and
  `batch_peaks.members` close each row with `halogen_lock`,
  `halogen_element`, `label_twin`, `neutral_pair` and `neutral_pair_r`.
