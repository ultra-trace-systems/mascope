# Reference

## Glossary

Quick definitions of the terms used throughout Mascope and these docs. Each
links to the section that explains it in depth.

| Term | Meaning |
| --- | --- |
| **Workspace** | The access-control and sharing boundary; contains datasets and members. See [the data hierarchy](../concepts/index.md#the-data-hierarchy). |
| **Dataset** | A study or campaign — a named grouping of related sample batches. See [the data hierarchy](../concepts/index.md#the-data-hierarchy). |
| **Sample batch** | A set of samples analysed and visualised together; matching and calibration happen at this level. See [the data hierarchy](../concepts/index.md#the-data-hierarchy). |
| **Sample** | One analysed measurement: a time window of a sample file, peak-detected, calibrated, and matched. See [sample files vs. samples](../concepts/index.md#sample-files-vs-samples). |
| **Sample file** | The raw, unmodified file an instrument produced (`.raw` or `.h5`), stored once and shared. See [sample files vs. samples](../concepts/index.md#sample-files-vs-samples). |
| **Acquisition dataset / batch** | System-managed, read-only record of uploaded raw files, grouped by instrument and polarity. See [acquisition and analysis](../concepts/index.md#acquisition-and-analysis). |
| **Analysis dataset / batch** | The editable datasets and batches you create to do science. See [acquisition and analysis](../concepts/index.md#acquisition-and-analysis). |
| **Workspace role** | What a member can do inside one workspace: guest, editor, admin, or owner. See [workspaces and sharing](../concepts/index.md#workspaces-and-sharing). |
| **Target collection** | A named list of compounds attached to a batch for analysis; typed as targets, calibrants, or diagnostics. See [targeted analysis](../concepts/index.md#targeted-analysis). |
| **Target compound** | A molecule (formula; optional name, CAS number, InChIKey, InChI and SMILES) in a collection; shared reference data. See [targeted analysis](../concepts/index.md#targeted-analysis). |
| **Target ion** | A compound combined with one ionization mechanism — the form that actually appears in a spectrum. See [targeted analysis](../concepts/index.md#targeted-analysis). |
| **Target isotope** | One line of an ion's theoretical isotopic pattern, with exact *m/z* and relative abundance. See [targeted analysis](../concepts/index.md#targeted-analysis). |
| **Ionization mechanism** | A single charge-forming reaction (protonation, adduct formation, ...). See [ionization](../concepts/index.md#ionization-modes-and-mechanisms). |
| **Ionization mode** | The measurement-level scheme: a set of mechanisms plus calibrant and diagnostic collections, recognised in filenames by its token. See [ionization](../concepts/index.md#ionization-modes-and-mechanisms). |
| **Matching** | Linking the targets attached to a batch to the peaks detected in each sample, and scoring every assignment. See [matching and match scores](../concepts/index.md#matching-and-match-scores). |
| **Match score** | A 0–1 score per assignment, built from *m/z* error, isotope-pattern fit, and intensity; aggregated bottom-up. See [matching and match scores](../concepts/index.md#matching-and-match-scores). |
| **Match category** | *No match*, *possible*, or *probable* — thresholds on the match score. See [matching and match scores](../concepts/index.md#matching-and-match-scores). |
| **Calibration** | Correcting the mass axis by aligning calibrant peaks to their theoretical masses. See [calibration](../concepts/index.md#calibration). |
| **File Agent** | The small program on the instrument PC that watches a folder and uploads new raw files automatically. See [Instruments & acquisition](../instruments/index.md#the-file-agent). |

## FAQ and troubleshooting

Import problems (rejected files, unrecognised filename tokens, greyed-out
processing) are covered in the
[import guide's troubleshooting section](../guides/import-files.md#troubleshooting),
and File Agent upload issues in
[Instruments & acquisition](../instruments/index.md#troubleshooting-uploads).

<!-- TODO Phase 4. Outline:
- FAQ (built from real support questions)
- General troubleshooting (login, common errors)
-->
