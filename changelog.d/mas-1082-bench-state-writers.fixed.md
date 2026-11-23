- **The assignment bench keeps a reused card's record.** A card reused while
  `bench.py init`, `reference` or `baseline <card>` ran was dropped from the
  bench's state when that command saved, so `status` and `post` no longer
  found it. Each command now writes only its own part of the state, and two
  writes at once no longer share one temporary file.
