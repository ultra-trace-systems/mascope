- **A copied batch keeps its assignment settings and its seeding.** Copying
  a batch dropped the assignment settings saved on it and its
  `seed_batch_peaks`, so the copy's passes ran on the engine's defaults and
  the copy of a seeded batch was unseeded. The copy now keeps both, the
  settings exactly as the original saved them.
