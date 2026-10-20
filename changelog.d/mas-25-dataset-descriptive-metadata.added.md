- **A dataset can say who made it, under which licence, and what it relates
  to.** The dataset dialog has a descriptive-metadata section: a licence
  (suggested CC-BY-4.0, CC0-1.0, CC-BY-NC-4.0 or proprietary, or any other),
  authors in citation order with their ORCID iD and affiliation, keywords,
  related identifiers such as the DOI of the paper the data supplements, and
  funding. Every field is optional. The API reports and accepts the fields on
  datasets, the SDK's dataset listing carries them, and the *Batch* sheet of
  the batch spreadsheet export appends License, Authors, Keywords, Related
  identifiers and Funding rows, empty when the dataset has none.
