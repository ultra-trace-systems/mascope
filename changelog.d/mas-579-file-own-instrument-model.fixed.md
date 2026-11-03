- **Exports state each sample file's own instrument model.** The batch
  spreadsheet's Samples sheet and a dataset's deposit package take the model
  from the file's own props, then its kept raw file, and the instrument's
  model only for a file that names none. An instrument that kept its name
  across a hardware change, or an older file uploaded after newer ones, no
  longer gives every sample of the name the same, latest model.
