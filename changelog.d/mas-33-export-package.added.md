- **A dataset's deposit package, for a data repository.** `mascope export
  package <dataset id>` writes one zip archive of a dataset to upload to a
  repository such as Zenodo: its raw files, beside each the acquisition
  record it came with if there was one, its batch spreadsheets, the target collections matched and
  calibrated against, its instrument functions, a README that says what was
  measured, how, under what licence, by whom and with which software, a
  `DATA_LICENSE`, and a `manifest.json` modelled on the demo dataset's, with
  the SHA-256 of every file and a `SHA256SUMS` that `sha256sum -c` checks.
  The standalone CLI builds it in the prod backend container and copies it
  out. *Export files* in the user guide lists what it holds, and
  `docs/maintaining.md` how to write one.
