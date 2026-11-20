- **A bench card on a PR tells the branch's gate misses from develop's.**
  Against a card of develop, the gate line splits the misses into new ones,
  develop's, which develop's card misses too, and the ones the branch fixed,
  and passes with no new miss: `GATE stage3: PASS for this ref - 0 new, 5
  develop's (...), 0 fixed`. A shared miss the branch makes worse counts as
  new. Develop's own card, and one with no baseline, keep the absolute line.
  A PR whose merge base is newer than develop's rolling baseline is compared
  with the newest card of develop behind its head, named by its commit,
  instead of getting no diff (`tooling/assignment_bench/README.md`, "The gate
  against develop"). `compare_runs.py` takes `--absolute-gate` to judge a
  card alone while it diffs.
