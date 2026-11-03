- **`mascope demo --rebuild` no longer reports a complete rebuild as failed.**
  The rebuild now uploads its raw files resumably, as the File Agent does. It
  looks up a failed upload in the demo database and its folders before posting
  it again, after the last pass too. It holds a retry until a file-converter is
  connected, since the server refuses uploads without one. And it clears the
  copies a killed earlier run left pending, not only the quarantined ones.
  Before, any of these could end a complete rebuild with "do not capture
  goldens". (#1855)
