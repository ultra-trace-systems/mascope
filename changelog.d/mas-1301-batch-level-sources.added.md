- **From a batch row's level to the samples it comes from.** A click on the level chip
  of a row in the *Batch peaks* ledger opens a popover: how the samples split over the
  levels, why the shown level holds and what would lift it, the best level any sample
  states, and how many samples state none and why. **Open a sample** opens the brightest
  sample at a level with the peak in focus and the inspector card stepping through that
  level's samples. The row's own arrow still opens the brightest sample of all, and its
  tooltip now says so. The samples come from a new read,
  `GET /api/batch-peaks/batch/{id}/levels/{batch_peak_id}`, asked on the click.
