- **Log lines go to stderr, so a command's stdout holds only its output.**
  A `--dry-run` plan saved with `> plan.txt`, or a `--json` report piped
  into `jq`, no longer starts with the coloured `Running at env` line. The
  backend, the file converter and the File Agent log to stderr too; their
  container logs, journals and consoles show both streams as before.
