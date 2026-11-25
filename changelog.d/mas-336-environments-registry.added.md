- **A registry of environments, and where each reference list applies.** An
  environment says what a sample was drawn from: a key, a label, the
  chemistry context it reads under, and the regimes whose reference lists
  it reads, from the vocabulary the lists declare theirs in. The registry's
  keys are the values of the shipped `environment` field. Mascope ships a
  starting set, seeded at each start where a server lacks it; editors
  manage the registry through `/api/environments`, and a guest reads it. A
  list's regimes (`applies_to_contexts`) and `always_active` are now
  recorded on its source row by `reference seed` and `reference sync`, and
  the list checks hold the regimes to the vocabulary. Nothing selects lists
  by them yet.
