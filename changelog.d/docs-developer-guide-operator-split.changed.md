- **The operator sections of the developer guide are in the maintainer
  runbook.** The nightly backup's full setup (including a backup server of
  your own), restoring and the notes on large databases, the PostgreSQL tuning
  reference, and `mascope env sync` (copying an environment's filestore and
  database between hosts) moved from `docs/dev/developer_guide.md` to
  `docs/maintaining.md`, which now holds every operator procedure; the guide
  points there, and its Deploying section defers the host setup and release
  walkthrough to `docs/hosting.md` and keeps the image pipeline and how a
  release is cut. The guide's frontend section gains an as-built summary of
  `lib/location`, the reload restore and shareable links.
