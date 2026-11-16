- **A batch peak whose trace holds a constant ratio to the source is flagged
  as likely background.** *Read co-variation* now also reads each committed
  batch peak's trace against the batch's reagent ions, or each sample's total
  ion current where the batch holds none (a normaliser that itself moves by
  0.25 or more is not divided by): a coefficient of variation under 0.25, in
  half the batch's samples or more, in a batch where at least a fifth of the
  traces vary, is a **flat series**, and where the source's own list of ions
  names the batch peak's ion the flag names it. The flag says where an ion
  comes from, not whether its formula is right, so it moves no tier. The
  *Batch peaks* ledger marks the formula and offers a **Flat series** switch,
  a sample's inspector shows the reading, and the ledger's CSV carries
  `flat_series`, `flat_series_cv` and `flat_series_source_ion` on every member
  so background can be filtered out. The ledger's records in the API and the
  SDK carry it as `flat_series`, a sample's assignment detail as
  `batch_flat_series`.
