- **The MCP server plans and applies writes.** `mascope-ai mcp` serves
  `plan_organize`, `plan_collection`, `plan_associate` and `plan_match`,
  which write nothing and answer with the plan, and `apply`, which writes a
  plan of that server once. Calling `apply` is the user's approval: Claude
  Code's permission prompt shows the plans waiting for it, and the server
  mints the confirm token only when the call runs, so the model never holds
  one. Never allow `apply` always. A repeated call returns the same receipt;
  an expired, consumed, drifted or part-way plan comes back as an error
  result that says why.
