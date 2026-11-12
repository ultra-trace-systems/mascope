- **The assignment bench runs a ref's in-app engine and cards it.**
  `tooling/assignment_bench/bench.py run <ref>` builds the ref into the
  bench's worktree, migrates the bench database forward with the ref's own
  migrations, and refuses a ref older than the database. It starts the ref's
  backend on the bench slot with four uvicorn workers (`--workers`), runs
  the in-app engine on every sample whose reference run is the pinned one,
  and with `--decoys` the decoy arms. It writes the gate card per set and
  pooled, scrubbed of every name and id the instance holds, under
  `cards/<ref>/<timestamp>/`. `--baseline` diffs the card against develop's
  rolling baseline, which a run of develop's head replaces; `bench baseline`
  shows it. One run at a time, with a time budget per set; the ref's backend
  and worktree are gone after the run (`tooling/assignment_bench/README.md`).
  A card now reads a decoy arm only where its control is the run the card
  reads.
