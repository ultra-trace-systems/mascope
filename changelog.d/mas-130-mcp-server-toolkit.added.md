- **An MCP server over `mascope_ai`, for coding agents.** `mascope-ai mcp`
  gives a coding agent such as Claude Code Mascope's data through the
  toolkit's tools: `find` and `describe`, the four loaders, the mass query and
  the match scoring, and `answer` for the provenance manifest. It runs with
  the user's own AI Agent token. A loader answers with a Frame's handle, and
  the rows are read in Python on the same machine from the server's session
  directory: the local-Python path for the heavy work. No tool result passes
  4 KB, and every name Mascope returns comes back as quoted data with its
  unprintable characters escaped. `mascope-ai setup` checks the install, the
  configuration and the token, `mascope-ai list` shows what there is, and
  `mascope-ai skill` prints the skill text for the agent. `chart` and
  `table` are not served yet.
