- **The batch's untargeted search takes a faint peak that recurs across the
  batch.** *Search untargeted* now also searches every batch peak present in
  more of the batch's spectra than the batch's own split of their occurrence
  distribution (Otsu's threshold, read per ionization mode and instrument
  type, held between 25 and 75 %; none under 10 spectra), however faint, beside
  the peaks the intensity threshold and the peak ceiling admit. Persistence
  says an ion is there, not which formula it is, so a batch peak taken on
  persistence alone holds the formula the search found at candidate at most
  (**persistence only**) until a line of its isotope pattern, the same neutral
  through another channel or a series of the batch sees it; a better fit does
  not lift it. A repeat mark beside the formula in the *Batch peaks* ledger
  gives the share of the spectra the peak is in and the split it cleared, a
  down arrow beside the tier names what holds it, and a sample's inspector
  shows the share. At the default settings (no threshold, no ceiling) every
  batch peak is taken on its height. The ledger's records in the API and the
  SDK carry it as `persistence` and `tier_reasons`, a sample's assignment
  detail as `batch_persistence`.
