- **An MS2 group's isolation window, and its located precursor.**
  `GET /api/samples/{id}/ms2/window` lists, for each MS2 group of a sample,
  the sample's MS1 centroids within half the isolation width plus 5 mDa of
  the group's parent key. Each comes with its offset from the key, intensity,
  share of the window, resolution, S/N and `is_parent`, the one that is the
  precursor. `width` draws every window at one width; a group whose scans
  store none lists its target alone and says so. A sample with no MS1 scans
  answers empty windows with the reason `no_ms1_scans`.

  Beside the window, each group carries its precursor as its MS2 spectra
  locate it, by peaky's rule: once per parent peak, the peak nearest the key
  among those holding at least 10 % of the strongest within max(10 ppm,
  5 mDa), in the lowest-energy spectrum that holds one, then followed within
  max(5 ppm, 0.5 mDa) across the energies, null where it is spent. A key
  typed with two decimals still finds the precursor and not a satellite
  nearer to it. The MS2 store records it when it is written, and the group
  inventory lists it, so `GET /api/sample/batches/{id}/ms2/groups` serves it
  too (`located_precursor_mz`, `located_precursor_intensity`,
  `located_precursor_activation`, `precursor_mz`, `precursor_intensity`).

  The MS2 store's version moves to 3. **After updating, run the MS2 store
  backfill again** (`backfill_ms2_store`, count first, then
  `MS2_STORE_MODE=run`): until a file's store is written again, its MS2
  routes read its raw file and its groups list no precursor.
