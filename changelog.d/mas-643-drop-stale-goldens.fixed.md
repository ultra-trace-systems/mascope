- **A demo refresh that drops the MS2 or ion goldens deletes their file.**
  When every file the goldens covered has left the raw set,
  `mascope demo snapshot --update` writes the manifest without the block and
  now also removes `expected/ms2.parquet` or `expected/ions.parquet`, so the
  published bundle zip no longer ships a golden its manifest does not name.
