- **The assignment bench can card what a batch decides.** `bench run
  <ref> --batch` adds the batch arm: after each set's control runs, one pass
  of the batch stages runs on the set's batch, every sample runs again, and
  the card reads those runs beside the control. It shows every reading in
  both and the rows that moved, counted by the batch decision each cites
  (the known species, a water rung, a listed compound, an isotope strip, a
  series). The gate still judges the control. A set whose pass fails or ends
  with a stage stale has no arm, named with why. The run dumps the bench
  database first and puts it back after, so no later run reads a decision a
  pass left. Without `--batch` a run and its card are as before
  (`tooling/assignment_bench/README.md`, "The batch arm").
