- **The batch export takes the instrument model from the instrument.** The
  Samples sheet's *Instrument model* columns, and a deposit package's model
  terms, now read the model recorded on the sample's instrument, so a sample
  whose raw file is no longer kept still gets its PSI-MS model term. An
  instrument with no model recorded yet falls back to the raw file's header,
  as before. Registering a file also fills the instrument's `cv_term` with
  its model's PSI-MS accession, empty for a model the vocabulary has no term
  for.
