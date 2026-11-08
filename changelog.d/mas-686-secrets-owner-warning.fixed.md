- **A runtime home whose secrets the published images cannot read is now
  named before the stack stops on it.** `mascope init` writes the secrets
  readable by their owner alone, and the published images read them as uid
  1000, so a home made by any other user stopped in `db_init` with a bare
  `Permission denied`. `mascope init` and every `mascope prod up` now warn
  when a secret the backend mounts is closed to that uid, naming the files,
  their owner and the `chown` that fixes them, and `mascope prod doctor`
  flags them `UNREADABLE` on a row of its own. It warns without refusing,
  since an image built with its own `MASCOPE_UID` reads them as that uid
  (`docs/maintaining.md`, "Health at a glance").
