- **The MS2 store can be written for files converted before it.**
  `mascope prod db script run backfill_ms2_store` counts, per instrument, the
  Orbitrap files whose scan census holds fragmentation scans and no MS2
  store, and the files with no census, which it leaves alone. With
  `MS2_STORE_MODE=run` it writes those stores, one raw file at a time with a
  pause after each (`MS2_STORE_PAUSE`, 1 s), and logs each file's groups. A
  run can be stopped and started again, `MS2_STORE_LIMIT` caps how many files
  one run reads, and a file whose store could not be written is passed over
  until `MS2_STORE_RETRY=1`. The maintaining guide names the step for the
  update that adds the store.
