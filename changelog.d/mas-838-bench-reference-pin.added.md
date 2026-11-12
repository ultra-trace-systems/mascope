- **The assignment bench publishes the reference engine at a pinned release.**
  `tooling/assignment_bench/bench.py reference --pin vX.Y.Z` installs peaky
  at the release tag into a tool environment of the bench's own. It runs
  `peaky assign` on every sample of every set with the set's profile and
  window, and publishes each run into the bench instance beside the in-app
  runs, under the engine badge `peaky`. A published sample is kept by pin,
  set, sample and `mascope-tools` version, so a second call publishes
  nothing. A new pin publishes every set again and lists the sets whose
  reference changed. A set whose profile the pinned peaky lacks is reported.
  `bench status` shows the pin and peaky's version string
  (`tooling/assignment_bench/README.md`).
