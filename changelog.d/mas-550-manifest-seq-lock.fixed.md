- **Two processes writing one AI session no longer give two manifest
  records the same `seq`.** `mascope_ai` counts and appends each record of
  `manifest.jsonl` under a lock on `manifest.lock` beside it, so an
  answer's `calls` always name one call each, also when the MCP server and a
  `Toolkit(session=...)` in another Python write the session at once.
