- **The assignment bench posts its card on a pull request.**
  `tooling/assignment_bench/bench.py run <ref> --post <pr>` posts the card
  as one comment on the PR: the gate line, the headline table and what moved
  since develop's baseline, with the whole card collapsed. A re-run edits
  that comment rather than adding one. A card the scrub refuses posts the
  refusal and the kind of name it held, never the name; a run that stops on
  an error posts the error's tail. A PR from a fork is refused, and a
  baseline older than the PR's merge base is not diffed against. The user
  units `tooling/systemd/bench-baseline.{service,timer}` make develop's
  baseline card every night (`tooling/assignment_bench/README.md`).
