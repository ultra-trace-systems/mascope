- **The model matrix has its driver, and its first row.** One command runs
  every golden question through Goose against the demo stack, the toolkit's
  MCP server as the recipe's extension, scores each answer with the fixture's
  own checker and keeps the record (`libraries/ai/tests/golden_transcripts/
  matrix/`, recipe in `golden_transcripts/README.md`). The first row, an
  open-weight model over the MCP server alone, is recorded under
  `golden_transcripts/`: the metadata, interpretation and decline classes
  clean, the analysis class failing for want of a computation path, which the
  side arms (`reduce`, a Python shell) confirm.
