- **The assignment bench no longer waits two hours on a run the server never
  ends.** A run that has not ended 20 minutes after it started, or 60 with
  the decoy arms, is left off the card as `stalled` and the set goes on
  without it. Nothing more is launched on that sample, which the server
  holds busy; `bench.py run --run-limit-min` sets the limit. A sample whose
  assign request keeps failing is left out after six tries 10 s apart,
  and `run.json`, the console and the PR comment say why, with the server's
  status and message (without the path or the error's reference), instead
  of reading as over budget.
