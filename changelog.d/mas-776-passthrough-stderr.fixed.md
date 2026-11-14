- **The `mascope` commands that hand off to another program now keep its log
  off stdout.** `mascope prod docker`, `mascope backend`, `mascope agent`,
  `mascope cert` and the other passthrough commands sent the program's
  stderr, where its log lines go, to stdout with its output. Redirecting one
  of them to a file, such as a script's `--json` report run through
  `mascope prod docker exec`, saved the log lines too. The program's stderr
  now goes to stderr, as it does for the CLI's own commands.
