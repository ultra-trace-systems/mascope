- **Editing an SDK listing no longer edits the SDK's cache.** Workspace,
  dataset, batch, sample and ionization-mechanism listings were handed out
  as the cached frames themselves, so a caller that changed one changed what
  every later call, and the SDK's own name resolution, saw. Each call now
  returns a copy.
