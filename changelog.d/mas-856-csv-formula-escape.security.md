- **A name typed as a formula opens as text in an export.** A sample, batch
  or collection name or an imported engine's claim that begins with `=`, `+`,
  `-` or `@` used to run as a formula when an exported file was opened in a
  spreadsheet program. The peak data, batch ledger and calibration trend CSVs
  now write such a cell behind one apostrophe, and the batch data spreadsheet
  stores it as text. The SDK's new `read_export_csv` reads a CSV export back
  with the apostrophe taken off
  ([#3148](https://github.com/ultra-trace-systems/mascope/issues/3148)).
