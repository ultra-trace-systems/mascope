- **The first sample a backend worker assigns no longer pays for expanding the
  reference lists.** With the shipped lists loaded, that expansion took some
  16 to 20 s per ionization mode and polarity, in every worker and again after
  every restart. Each worker now builds those entries in the background once it
  has started, from the newest sample of each mode and polarity in use; the
  start and `/ready` do not wait for it, and a sample that arrives first waits
  for its own entry's build rather than making a second. `[backend]
  reference_warmup = false` switches it off (`docs/maintaining.md`, "The
  reference lists' warm-up").
