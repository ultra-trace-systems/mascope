- **The assignment bench can card a run option that is off by default.**
  `bench run <ref> --config '<json>'` (or `--config-file <path>`) asks
  every in-app run of the card with that run config: the control, the
  batch arm's runs and each decoy arm. A run whose recorded config lacks
  an option asked for is let go at once as failed, so a typo never cards
  the defaults under the config's name. The run log, `run.json` and the PR
  comment say the config. Such a card is reused only for the same config,
  and is never develop's baseline. Without `--config` a run and its card
  are as before (`tooling/assignment_bench/README.md`, "A run config").
