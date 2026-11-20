- **The assignment bench gives a head its card in seconds when its engine
  inputs did not change.** `bench.py run <ref>` first hashes what a card can
  depend on: the ref's tree but for the paths that cannot move it (the
  frontend, docs, Markdown, changelog fragments, CI, the File Agent, tests and
  tooling other than the bench and the comparison), the caller's bench and
  comparison, and what the bench holds. A complete card of the same digest,
  sets and decoy arms is reused without the lock, a backend or a run, and
  `--post` posts it with a line naming the commit it was made at; a develop
  card serves a frontend-only branch. `--fresh` runs regardless, and the
  nightly baseline always does. `bench.py status` shows each card's digest
  and whether it was run or reused (`tooling/assignment_bench/README.md`,
  "A card without a run").
