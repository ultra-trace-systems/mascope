- **A sample's TIC is the mean per scan, no longer the sum.** The TIC grew
  with a sample's length, while its spectrum is averaged over the same
  scans, so a long sample looked brighter than a short one of the same
  signal. It is now the mean TIC per MS1 scan, in the unit of the
  sample's spectrum: counts for an Orbitrap, ions/s for a TOF. New samples
  get it at once. The stored TIC of every existing sample changes when the
  operator runs `db script run fix_sample_item_tic` once after the upgrade
  (`docs/maintaining.md`): the TIC trace of the batch charts, the batch
  export's TIC column and `samples.list()` in the SDK all show the new value.
