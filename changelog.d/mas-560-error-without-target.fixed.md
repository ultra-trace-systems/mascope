- **`mascope-ai` no longer adds a stray "it" to an error without an object.**
  When the server could not be reached, `mascope-ai setup`, `mascope-ai list`
  and the MCP server said "Could not connect to Mascope it: ...", and a failed
  listing read "Could not list the workspaces it: ...". The message now ends
  the phrase where the operation does, and names the object only when there
  is one.
