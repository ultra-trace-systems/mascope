- **The assignment comparison prints the plan's gate card.**
  `tooling/assignment_compare/compare_runs.py --card <dir> --sets <manifest>`
  reads both engines' stored runs over fixed sets of samples, named by letter,
  and writes one card per set and one pooled, as markdown and JSON: every
  metric of the assignment quality plan's table, G1 to G14, the mass error,
  the tier-reason coverage and, with `--decoy`, the decoy arms, each a function
  in `metrics.py`. `--gate` marks each reading against the stage targets of
  `thresholds.json` (the plan's, shipped beside it) and prints a pass-or-fail
  line; `--baseline <card.json>` lists the readings that moved since a previous
  card. Before anything is written the card's text is checked against a deny
  list (the manifest's workspace, batch and dataset names, the sample ids, the
  hosts, `--deny`), and a card that holds one is refused, naming the token
  (`tooling/assignment_compare/README.md`, *The gate card*).
