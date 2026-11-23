- **`mascope prod doctor` flags secret files that group or others can
  access.** Compose mounts each file in `.runtime/secrets/` into the
  containers with its host mode, so a secret left at the umask can be read by
  every user on the server and every uid in those containers. That includes
  the app's database password. Since 1.15.0 the CLI creates new secrets 0600,
  but files created before that were left open. The doctor now lists each
  open file, and a secrets directory that group or others can write to. It
  exits `1` for them and prints the exact `chmod` that closes them. When a
  file the backend reads is owned by another uid than the one the backend
  runs as, the printed fix starts with a `chown` to that uid. A fleet rollout
  stops at the first server that has open secrets, and its Kuma monitor goes
  down until they are closed. See "Secret file modes" in
  `docs/maintaining.md`.
