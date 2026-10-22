- **A script run on stdin no longer reports to GlitchTip.** Code piped to
  `python -` in a server's container, or typed at its prompt, imported Mascope
  with the server's `MASCOPE_SENTRY_DSN` and filed its own errors, a typo
  included, as production errors of that server. Such code now leaves error
  reporting off; the backend and its workers report as before.
