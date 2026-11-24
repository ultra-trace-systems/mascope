- **A registry of typed metadata fields.** One registry for the whole
  deployment names the fields acquisition metadata will be recorded under:
  each with a stable key, a label, a type (text, number, date and time, one
  of a list, or tags), a unit and its allowed values, so "site" is one field
  on every instrument. Editors manage it in Settings > Data > Metadata
  fields and through `/api/metadata/fields`; a guest reads it. A field in
  use is retired, not deleted, and Mascope ships one, `environment`. An
  attribute template can now select registry fields beside its free-form
  labels, which stay batch annotations.
