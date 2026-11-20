- **The assignment bench queues its runs and ends cleanly when it is
  stopped.** Runs waiting for the bench's lock now take it in the order they
  asked, and each says its place and who is ahead. SIGTERM, SIGHUP, Ctrl-C,
  or a starter that goes away end a run through its cleanup: the backend
  stopped, the database put back, the lock freed, and a `stopped` `run.json`.
  A backend that an earlier, killed run left on the bench's port is stopped
  at the next start instead of refusing it. A run with no card adds one line
  under the PR's bench comment instead of replacing its card, and
  `bench.py post <ref> --pr N` posts a card that a run made but could not
  post (`tooling/assignment_bench/README.md`, "One run at a time" and
  "Asking for a card on a PR").
