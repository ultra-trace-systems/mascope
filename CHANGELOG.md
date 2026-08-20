# Changelog

Notable changes to Mascope are documented here. Versions follow the date-based scheme `YYYY.MM.DD-<hash>` produced by the release workflow, and releases are pinned with a semantic version tag `vX.Y.Z`.

## [Unreleased]

### Added

- Pairing a machine again is now just starting the agent. On every start it
  asks the server whether this machine's credential is still accepted, and
  offers to pair right there when it is not - so a machine that was revoked,
  or left off long enough for its credential to lapse, is fixed before the
  first acquisition needs it rather than after an upload has already failed.
  The same offer appears if a credential is refused mid-session, and
  approving the code in Mascope resumes the upload the agent was holding.
  Recovery previously meant relaunching the agent from a terminal with a
  flag, which is a lot to ask of whoever is running the instrument.
  A credential that cannot be checked - a machine that just booted with no
  network yet, a server being restarted - is not treated as a refusal: the
  agent says so and carries on, since pairing would not fix it.

- Instrument-agent credentials are now short-lived and renew themselves. A
  paired device's token expires in 30 days instead of 360, and the File Agent
  rotates it in the background well before then, so a token copied off an
  instrument PC is useful for far less time. Renewal issues a fresh token and
  reaps the superseded one once its own lifetime lapses; a token that has
  outlived its lifetime is refused with a message to re-pair. A new endpoint,
  `POST /api/auth/devices/token`, performs the rotation for the agent. The
  machine account's own converter token is issued on demand, so a short device
  lifetime never strands an upload.
- The File Agent now **verifies the server's TLS certificate by default** and
  obtains its credential **only by pairing**. Setup asks whether to verify TLS
  (answer No only for a self-signed or development server, recorded as
  `verify_tls` in the agent config), and the manual token-paste path is gone -
  pairing is the single way in, which is also what keeps the credential a
  revocable, self-renewing per-machine device token.
  **Upgrading an existing agent:** verification was previously always off, and
  a `config.toml` written before this release names no `verify_tls`, so it
  takes the new default and an agent pointed at a self-signed or internal-CA
  server will stop uploading with a TLS error. Add `verify_tls = false` to
  `[file-agent]` before upgrading those machines, or re-run setup and answer No.
- Instrument agents now authenticate as their own **machine accounts**, not as
  the person who paired them. Approving a pairing provisions a dedicated
  account the device authenticates as - capped at the editor role, with no
  usable password - so acquisition no longer depends on a human's credential
  and a person leaving never revokes an agent. The person who approved the
  pairing is recorded as the device's sponsor, and is added as owner of the
  acquisition workspaces their agent's uploads create, so they keep seeing the
  data. The machine account itself joins the acquisition workspaces as an
  editor when it is provisioned - the same reach the approver's token had
  before - so a re-paired agent keeps uploading to instruments that were
  already in use. Machine accounts never sign in interactively, are excluded from the
  deployment-wide forced-password-change sweep and the two-factor requirement
  (both meaningless without a browser), do not appear in the user list, and
  cannot be renamed, re-roled or deleted through user management - they are
  managed entirely through Paired machines. Revoking a device now also
  deactivates its machine account and clears all its tokens.
- Paired instrument agents are now first-class devices. Approving a pairing
  creates a registered device - named after the machine's hostname, sponsored
  by the approving user - and binds the issued token to it, so a deployment can
  finally answer "which machine holds this credential, and who vouched for it".
  A new **Paired machines** list under Settings shows each device with its
  service and when it was last seen, and lets you rename or **revoke one
  machine on its own**. Revoking a machine is now the only thing that stops
  that machine: its credential belongs to its own machine account, so
  Regenerate - which replaces the tokens issued to *you* - no longer affects a
  paired agent. Admins and owners can review and revoke any deployment's
  devices within the usual user-management role ceiling. Uploaded files now
  record who sent them (the device for agent uploads, the user for interactive
  ones); rows created before this land as unattributed rather than guessed.
  A deployment flag `require_device_tokens` under `[backend]` (default off)
  refuses agent tokens that are not bound to a paired device - left off during
  the transition so agents paired before this keep working, and turned on once
  every agent machine has been re-paired. The pen-test suite gained checks for
  per-device revocation isolation and the strict-mode gate.
- Acquisition timestamps now use the instrument PC's timezone. The File Agent
  reports its IANA zone with each upload, and the converter resolves the UTC
  offset from it at the file's own acquisition time - correct for an instrument
  in a different zone from the server, across DST, and for backlogged uploads.
  An offset embedded in the raw file still wins where present (TOF); the old
  "assume the converter host's timezone" behaviour remains only as a last resort
  for uploads that carry no zone, and no longer mis-signs a west-of-UTC offset.
  Each sample file records the zone applied and whether it came from the file,
  the agent, or the fallback.
  The agent detects its zone from the operating system. Windows names a *group*
  of zones rather than a city, so a machine can resolve to a neighbouring city
  whose historical DST rules differ; set `timezone` in the agent configuration
  (e.g. `timezone = 'Europe/Helsinki'`) to report it exactly. A machine that
  cannot name its zone simply reports none and the server falls back as before.
  One case stays inherently uncertain: a file whose acquisition time falls in
  the hour the clocks repeat names two instants, and one in the hour they skip
  names none. Vendors like Thermo record a bare wall clock with no offset, so
  the file cannot say which. The converter resolves those deliberately (as the
  pre-transition reading) and logs a warning naming the file and both candidate
  offsets, so an affected timestamp is explainable rather than silently an hour
  out.
- Assignment runs computed outside Mascope can now be published into a sample's
  run history. `POST /api/peak-assignments/sample/{id}/runs/import` accepts a
  finished ledger from an external engine and stores it as a first-class run -
  same tables, same read model, same batch fold-in as a run the app computed -
  stamped with the producing engine, so a reader always knows whose judgement a
  ledger carries. A dense sample's ledger is too large for one request, so an
  import assembles across several: the first creates the run and returns its id,
  follow-ups carry the next row offset, and the last one finalizes. Offsets are
  server-checked and the client names the import with an id of its own -
  required, since a row offset cannot make the request that *creates* a run
  idempotent - so a request retried after a timeout is an idempotent no-op
  rather than duplicated rows, a second run, or a second batch fold-in. `DELETE
  /api/peak-assignments/sample/{id}/runs/{run_id}` releases an upload that will
  never finish, which would otherwise block new assignment work on that sample
  until the nightly retention pass.
  Every id a row carries into a reference column - ionization mechanism, target
  compound, target ion - is checked before anything is stored, so a stale
  reference is a 422 that names it rather than a constraint violation reported
  as something else entirely, and a refused import never leaves a half-open run
  holding the sample.
  What an import may assert stops short of what the server presents as its own:
  it declares the fit-score bands it tiered with and every row's tier is checked
  against them, it discloses what it calibrated against (an import bypasses the
  m/z verification gate because it calibrates client-side), and the calibrated
  P(correct) columns stay empty on imported rows. The in-app engine name is
  reserved, so the provenance badge cannot be forged. Only one run - imported or
  in-app - can be in flight for a sample at a time; a second is refused with 409
  naming the one already running. Retention now budgets kept runs per sample
  *and* engine, so publishing can never evict a sample's in-app history, and
  imported verdicts are excluded from the instrument-wide confidence
  calibration, whose labels stay to runs this server computed. Tune the new
  grace on unfinished uploads with `MASCOPE_PRUNE_KEEP_IMPORTING_HOURS`
  (default 24) and the ceiling on imported runs kept per sample across all
  engines with `MASCOPE_PRUNE_KEEP_PER_SAMPLE_TOTAL` (default 12; runs this
  server computed are exempt from it). Launching runs from the SDK and the run selector's engine badge
  come next.
- The Python SDK can now read persisted peak-assignment results (read-only
  v1). A new `mascope.peak_assignments` resource reads the peak-centric
  assignment runs launched from the app: `list_runs(sample_id)` returns the
  run history, `get(sample_id)` returns the whole ledger of the latest
  completed run (or a given `run_id`) as one DataFrame - one row per observed
  peak, paged through the API internally, with the run metadata attached on
  `df.attrs["run"]` and server-side `tier`/`role`/`source` filters - and
  `detail(sample_id, peak_assignment_id)` fetches a single assignment in full
  (the `alternatives`/`provenance` inspector JSON the slim list rows omit). A
  high-level `mascope.load_assignments(dataset, batches=...)` loader
  concatenates assignments across batches with batch/sample metadata, in
  parity with `load_peaks`; samples without a completed run are skipped and
  logged. Ships with a new tutorial notebook, `10_peak_assignment.ipynb`
  (tier distribution, database-vs-untargeted source split, tier-colored
  mass-defect map, Van Krevelen, drilling into alternatives). Launching runs
  and verifying assignments stay app-side. (#1737)
- Two-factor authentication (TOTP). Any account can turn it on from its
  settings: scan a QR code with an authenticator app, confirm one code, and
  save the ten single-use recovery codes shown once at that point. A deployment
  can also require it - `mfa_required_min_role` under `[backend]` names the
  lowest role covered (`admin` for admins and owners, `guest` for everyone);
  unset, the default, requires it of nobody and leaves existing deployments
  exactly as they were. A covered account is held at a setup screen after
  signing in until it enrols, and cannot turn the factor off again. A name that
  is not a role stops the backend at startup rather than resolving to "nobody",
  so a typo cannot leave a requirement that looks configured and asks nobody
  for anything.
  The session cookie is minted only after both steps pass: credentials for an
  enrolled account return a short-lived, single-use pending token instead of a
  session, and a second request exchanges it plus a code for the session. Every
  surface that trusts the session - the role checks, the realtime channel - is
  therefore unchanged, because none of them ever sees a half-finished sign-in.
  Generating an API access token and approving an agent pairing now ask for a
  current code as well as a session. Both hand out credentials valid for a year
  that are not tied to the browser they were requested from, so a stolen session
  alone must not be able to obtain one; signing in or enrolling counts as
  presenting a code for the next five minutes, so this rarely means entering one
  twice. Changing a password is deliberately not affected - it already requires
  the current password.
  **Recovery, in escalating order**: a recovery code; an administrator clearing
  the factor for a guest or editor, or an owner for anyone but themselves; and
  `mascope prod mfa reset <email>` on the host for when nobody who could do that
  can sign in either. None of them reveals or changes a password.
  **New secret**: `.runtime/secrets/mfa_encryption_key.txt` encrypts the stored
  TOTP seeds. `mascope prod up` generates it when missing before starting, so
  an existing deployment picks it up on its next start.
  Back it up with the others and do not rotate it casually: replacing it makes
  every enrolled seed undecryptable, and each of those users has to sign in with
  a recovery code and enrol again. It is deliberately separate from
  `jwt_secret_key.txt`, which can be rotated freely. A deployment without the
  key starts normally and refuses only enrolment. See
  [Authorization](docs/authorization.md) and `docs/dev/mfa_totp_plan.md`.
- `GET /api/workspaces` now reports `my_role` on every record: the caller's own
  role in that workspace, plus `instrument` on an acquisition workspace, naming
  the instrument whose raw files it holds. The app uses them to disable an
  action the backend would refuse rather than offering it and surfacing a 403 on
  click - the Recalibrate entries in the sample and batch menus, and the Save
  button in the calibration dialog, are now disabled with an explanation for
  anyone without admin in the relevant instrument workspace. Superusers report
  `owner` everywhere, matching what the workspace checks grant them regardless
  of membership.

### Removed

- SDK: the `09_composition_assignment.ipynb` tutorial is retired. It rolled
  its own untargeted composition assignment client-side, predating the
  server-side assignment engine; next to the engine's persisted, arbitrated,
  calibrated runs it taught a workflow that produces different scores and no
  ledger. The remaining notebooks close the gap: `10_batch_stages.ipynb` is
  now `09_batch_stages.ipynb`, and the supported path is
  `10_peak_assignment.ipynb`. **Upgrading:** `copy_examples` never overwrites or
  removes anything, so an existing `mascope_examples/` folder keeps the old
  files. Delete `09_composition_assignment.ipynb` and `10_batch_stages.ipynb`
  from it before re-running `copy_examples`, or you keep the retired notebook
  and end up with batch-stages twice under two numbers.

### Changed

- Running an m/z calibration no longer requires the global `admin` role. It now
  requires `admin` in the *instrument* workspace holding the raw file - the
  right scope, because a calibration is written onto the file and every sample
  item referencing it, in any workspace, sees the change, which is the same
  reason deleting and reprocessing that file are governed there. Fitting a
  calibration (`POST /api/calibration/mz_fit`) computes without writing anything
  and is scoped instead to `editor` in the workspace holding the sample, or
  `admin` in its instrument workspace - a caller who may write the calibration
  can preview it. The practical effect is that an analyst who needs to calibrate
  their own instrument's data no longer has to be promoted to a role that also
  carries user administration. Membership of the workspace holding the sample
  does not grant a calibration - the file is shared, so the instrument workspace
  governs it, and unlike deleting a file there is no fallback through the
  workspace an item happens to sit in. Because the instrument role can reach a
  batch or sample the caller could not have listed, the confirmation the route
  returns names it only when a guest-level read would have returned the name.
- Editing and deleting an ionization mode dropped from the global `admin` role
  to `editor`, matching what creating one already required, what the whole
  instrument-config surface requires, and what `docs/authorization.md` states
  for shared reference data. The edit and delete controls in the Ionization
  Modes pane follow, so the widened permission is actually reachable. Guests
  remain read-only. Note that such an edit is retroactive: it changes how
  samples already processed under the mode are calibrated and matched, and
  flags every affected batch - in every workspace, not only the editor's own -
  for recalibration or rematching. Because the mode is read instance-wide, the
  calibration and diagnostic collections it names must now be readable by the
  editor setting them, and a collection a mode uses can no longer be narrowed
  into a single workspace afterwards - either way round, one workspace's
  private collection would otherwise govern how every other workspace's
  samples are matched.
- With the opt-in peak-centric assignment feature enabled, the legacy Match
  tab (briefly renamed "Fit" under the flag) is retired: the Sample view
  already carries the spectrum-envelope and time-series duties it duplicated,
  so the tab and every navigation into it (ion-table visualize, batch-chart
  click-through, shared links) are hidden behind the flag. Deployments that
  have not opted in see no change - the Match tab and its targeted
  visualization work exactly as before. The composition-fit endpoints
  (`POST /api/peak-assignments/sample/{id}/fit/aggregate` and
  `.../fit/visualize`) remain available as API/SDK surface.
- Creating an account no longer asks the administrator to invent a temporary
  password. The server generates one and shows it once, the same way a password
  reset already worked. The new user had to replace it at first sign-in either
  way, so the password's only job was to survive being handed over once - and a
  generated one cannot be weak, nor a password the administrator uses somewhere
  else. The first owner still chooses their own, since nobody is handing that
  account over. Callers of the registration API that supply a password keep
  working unchanged.

### Fixed

- A file the server rejects outright is no longer retried ten times over five
  minutes before being set aside. Anything the server understood and refused -
  most often a name that does not identify an instrument, but also an upload
  over the size cap - now fails on the first attempt, and the agent explains
  the cause and the fix instead of repeating a request that cannot succeed:
  Mascope reads the instrument from the start of the file name up to the first
  underscore, so either the acquisition software names files that way or
  `filename_prefix` in the agent configuration adds it. Rate limits, request
  timeouts and server errors are still retried, since those do clear. The
  agent also now says where it put a file it gave up on, and how to make it
  try again.

- A refused agent credential now says so, instead of telling an unattended
  machine to sign in. When a deployment accepts only paired machines, or a
  device token expired while the agent was offline, the refusal names
  re-pairing as the fix and the agent reports it; previously every such 401
  was rewritten to "Please sign in to the Mascope", which no agent can act
  on and which pointed operators at the wrong thing entirely. Ordinary
  sign-in failures are unchanged.
- Revoking a paired machine now reports that machine the same way the device
  list does. The revoke response named no sponsor at all, so a client
  refreshing its row from that payload blanked the sponsor out.
- The settings pane no longer offers a **File Agent** token to generate by
  hand. The agent takes a paired credential and cannot accept a pasted one,
  so that token had no use; machines are connected under **Paired machines**.
  The TOF and CSV export agents keep theirs, which is still how they are
  issued a credential.

- The File Agent no longer falls back to the legacy single-request upload
  endpoint, and no longer reports a rejected credential as an out-of-date
  server. Every supported server accepts resumable agent uploads, so a
  refusal when an upload is created means the credential was refused - a
  revoked machine, a token that expired while the agent was offline, or a
  deployment that accepts only paired machines - and the agent now says so
  and stops, instead of concluding the server was too old and quietly
  moving that process to the single-request endpoint for the rest of its
  life, where uploads are capped at 100 MB and no longer resume after a
  network drop. Servers keep accepting single-request uploads, so agents
  that have not been re-paired yet continue to work unchanged.

- A successful `mascope prod update` - unattended or `--version` - now also
  moves the deployment checkout to the release it deployed. The checkout is
  what a boot redeploys, so an unattended update used to leave the server one
  reboot away from silently downgrading to the previous release (and
  `mascope prod doctor` reporting DRIFT) until the next timer window. The
  move never discards local changes: when the checkout cannot be moved, the
  update still succeeds with a warning naming the manual
  `git checkout <tag>` step.
- `mascope dev run --instance` now applies the migrations of the checkout it
  runs from. The Alembic directory was derived from `MASCOPE_PATH`, the shared
  runtime home, which normally points at the main checkout - so an instance
  launched from a git worktree migrated its database to the main checkout's
  head, silently skipping the branch's own revisions. Nothing warned: the
  pending-migration check compared the wrong head, reported "up to date", and
  the stack started without the new schema, failing later at runtime on the
  first use of the missing columns. Any branch adding a migration needed a
  manual `alembic upgrade` plus `--skip-migrations` to be exercised at all,
  which defeated the purpose of per-worktree instances for exactly the changes
  most worth testing live.
- The migration test suite hit the same conflation: it built its Alembic path
  from `MASCOPE_PATH`, so running a worktree's tests tested the *main*
  checkout's migrations against the worktree's models and produced a
  convincing but bogus model-drift failure. It now resolves the path from its
  own location. `mascope prod manifest`, which records "the Alembic head baked
  into the current source tree", was reading it from the same wrong place.
- `MASCOPE_PATH` keeps its documented job throughout - database volumes,
  secrets, `.runtime` - and is no longer treated as a source location by any
  of these paths. It is still the fallback for an operator install, where the
  deploy directory legitimately is both.
- An upload token that expires mid-transfer is answered with a clean 401
  instead of being recorded as an unhandled server fault. The tus upload
  routes are generated by tuspyserver, so they cannot carry the `@api_route`
  decorator that normally turns an `ApiException` into a response; nothing
  converted it, and Starlette re-raises after the catch-all handler replies,
  so error monitoring captured a routine re-authentication as a crash. The
  application now handles `ApiException` directly, which also covers any
  future route Mascope does not own.
- Acquisition-drift warnings are reported at most once a day per instrument.
  Drift is a standing condition until someone retunes the instrument, not an
  event per file, so a busy instrument repeated the same warning on every
  acquisition - one production Orbitrap logged it 172 times in 19 hours,
  burying unrelated errors. The per-file magnitude still follows in an INFO
  log line and in the persisted calibration record. The window is
  per-process, so a backend running several workers can still emit one
  warning per worker per day.
- Running a database maintenance script unattended no longer reports its
  skipped pre-script backup as a warning. `--skip-backup` is a real warning
  for an operator at a terminal, where it prints beside a confirmation
  prompt they can still abort at; under `--yes` there is nobody to react and
  the flag is a settled configuration choice. The nightly assignment-prune
  timer passes both by design, so every server in a fleet was minting an
  error-monitoring event a night that no one could act on.

## [1.7.2] - 2026.08.19

### Added

- Publishing a GitHub Release now verifies the Zenodo archive: a
  `verify-zenodo` job in the release workflow polls the Zenodo API and fails
  the run if the new version has not appeared under the concept record within
  20 minutes. The GitHub-Zenodo sync fails silently when its credentials
  lapse - v1.6.2 through v1.7.1 went unarchived for up to a week before
  anyone noticed - so a broken sync now turns the release run red, with
  recovery steps documented in the developer guide's release section.

### Fixed

- Acquisition-drift warnings no longer flood error monitoring with an issue
  per ppm value. The warning message embedded the observed drift rounded to
  whole ppm, and monitoring groups events by message - so ongoing drift,
  whose magnitude wanders file-to-file, split one episode into dozens of
  single-event issues that buried real errors (a production deployment's
  TOF instruments minted over fifty issues within a day of updating to
  1.7.0). The message now names the instrument and the threshold but not
  the magnitude, grouping an episode into one issue per instrument; the
  exact per-file magnitude still follows in an INFO log line, in the
  persisted calibration record, and in the sample browser's badge tooltip.
- TOF instruments get their own acquisition-drift threshold, 50 ppm, in
  place of the global 10 ppm. A TOF mass axis legitimately wanders tens of
  ppm between retunings, so the Orbitrap-grade threshold kept every file of
  a normally-behaving TOF warning permanently. A sample flagged under the
  old threshold sheds its drift badge on its next recalibration when its
  recorded magnitude is within its class threshold.

## [1.7.1] - 2026.08.18

### Changed

- The demo dataset is now bundle **v1.2.1**
  ([10.5281/zenodo.21994087](https://doi.org/10.5281/zenodo.21994087)),
  regenerated so its manifest pins the `opentfraw` 1.4.0 raw reader this
  release moves to. The reproducibility test asserts the running reader matches
  the version that produced the goldens, so the bundle has to be recut whenever
  the reader is bumped. Nothing about the measurement changed: the raw files are
  byte-identical to v1.2.0 and so are the goldens, because 1.4.0's additions
  (per-scan DIA/wideband flags, `sample_info` headings, an acquisition-end
  timestamp) touch no decoded spectrum. Verified before the bump: reading all
  161 raw files under 1.3.7 and 1.4.0 gives identical scans, centroid labels,
  profiles and trailer parameters, and a full pipeline rebuild reproduces all
  42,510 golden peaks with zero deviation in m/z, intensity and match score.
  Pinned runs against older bundles keep working - v1.0.0 through v1.2.0 stay
  registered.

### Fixed

- Signing in outside the browser (SDK scripts, automation) now issues the
  file-converter access token exactly like a browser sign-in does. The token
  is resolved server-side for every upload, but it was only minted when the
  sign-in carried a web-app socket id - so an editor account provisioned and
  agent-paired entirely through the API had every File Agent upload refused
  with a misleading "check your API token" error until its first browser
  sign-in.
- `tooling/smoke-test.sh` can now verify a production deployment serving the
  self-signed `mascope cert gen` certificate: set `SMOKE_INSECURE=1` to skip
  TLS verification. Its plain curls previously failed on the certificate
  before reaching any check.
- A demo bundle can no longer be published with goldens that cover only part of
  its raw files. `mascope demo --rebuild` could quietly ingest fewer files than
  the bundle contains and still finish with every sample batch `ready`, and
  `mascope demo snapshot --update` would then capture goldens from that partial
  run - which is how bundle v1.0.0 shipped with 152 of its 161 files. The
  goldens export now compares the demo database against the manifest's raw set
  and refuses to write `expected/` when they disagree, naming the files that
  are missing and distinguishing "never ingested" from "ingested but matched
  nothing". The comparison keys on the compact acquisition stamp, the one
  component shared by the published filename and the filename the converter
  reconstructs from the file's own metadata. The ingestion half of the check
  runs even when a run matched nothing, since the snapshot is captured from
  that same database either way.
- The rebuild uploader no longer races the file-converter's startup. Uploads
  began as soon as the backend answered HTTP, but the upload endpoint hands each
  file's processing context to the converter over a socket, and a converter that
  has not connected yet never receives it - so the first files uploaded failed
  deep in processing with "not registered in file converter service" and were
  quarantined, with nothing surfacing the shortfall. The uploader now holds the
  first upload until a converter has connected (distinguishing a fresh connect
  from a presence record left behind by a killed run or another worktree), and
  then keeps sweeping the converter's quarantine folder for the rest of the run,
  re-uploading its own files and reporting by name any that never made it. An
  upload that fails outright is retried rather than dropped, quarantined copies
  left by an earlier rebuild are cleared before the run starts instead of being
  re-fed as duplicates, and the closing report covers every stage a file can
  fall out at, not just the quarantine folder.
- The reproducibility test now fails on quarantined files as soon as the
  pipeline stops moving, naming them and the usual cause, instead of waiting out
  its stall window to report only that the pipeline stalled.

- The "Learn more" links in help-mode popovers are now reachable. Help cards
  are matched to the pointer purely by geometry, with nothing accounting for
  what covers what, so a popover that overhangs a neighbouring panel - the
  "Raw files" tab card hangs over the sample browser - counted as hovering that
  panel as well. Reaching for the link swapped the popover for the covered
  panel's card partway there, and the click landed on the wrong documentation
  page. A popover now holds its card for as long as the pointer is on it, and
  extends its hover area across the gap to whatever opened it, so the link
  stays where it was aimed at.

- The "new version of Mascope is available" banner no longer reappears on
  every tab refocus after a release. nginx served `index.html` with no cache
  policy, so browsers applied heuristic caching and kept booting the previous
  build's page for hours after a deploy - which the update check then
  (correctly) flagged, over and over. `index.html` is now served with
  `Cache-Control: no-cache` (revalidated with a cheap 304 while unchanged) and
  the hashed `/assets/` bundles with a one-year lifetime, and the banner's
  Reload button now refreshes the browser's cached copy of `index.html` before
  reloading, so it always lands on the new build.
- The penetration-test suite no longer reports a baseline entry as stale when its
  check never ran. An accepted finding that produced no finding was taken to
  mean the control now passes - true of a full run, but not of a single module
  or a `-k` filter, where the check is not collected at all. A narrow run therefore
  advised removing entries that were still doing their job, and removing one
  leaves nothing to catch that finding's return. Entries whose checks a run did
  not collect are now reported as unevaluated, distinct from both accepted and
  stale.
- A stale baseline entry now fails the run on its own. Previously staleness could
  only withhold a pass from a run that had already failed, so a run whose controls
  all passed printed the warning and still exited zero - meaning a scheduled job
  would never have surfaced the one state in which the baseline silently absorbs a
  returning finding.
- Re-running `./tooling/ubuntu.sh install` from a non-login shell (ssh, cron,
  ansible) no longer exits before writing the systemd units. The PATH export
  added to fix the first-install failure landed after the `uv tool
  update-shell` call it was meant to precede, and that call errors when uv's
  bin directory is absent from PATH while the shell profile already names it
  - the state of every re-run over ssh - so under errexit the script died
  with the binaries linked but no boot service or disk-check timer installed.
  This matters because re-running the installer is the documented way to pick
  up corrected units, and the v1.7.0 release notes ask every server to do
  exactly that. The export now comes first, so both the first run and every
  re-run complete.
- The docs no longer load the mermaid diagram library from a CDN. The docs are
  built to be self-contained - fonts self-hosted, KaTeX vendored - but the
  theme's diagram loader fetched mermaid itself from unpkg.com at view time,
  leaking visitor addresses and breaking diagrams in air-gapped deployments.
  mermaid is now vendored into the docs assets like KaTeX.
- Thirteen display equations in the "How it works" documentation pages now
  render as display blocks instead of inline math flanked by literal dollar
  signs; they were indented or glued to the preceding paragraph, which keeps
  Arithmatex from promoting `$$...$$` to a display block. A bare `%` KaTeX
  silently dropped as a TeX comment is now escaped.
- `CITATION.cff` tracks the released version again: it had sat at
  1.0.0 / 2026-06-29 since the first release, so GitHub's "Cite this
  repository" widget and the copy shipped in every archive since cited the
  wrong version. Its `doi` field now carries the concept DOI - always the
  latest archived release; Zenodo mints per-version DOIs only after
  publishing - and the version bump is folded into the standard release-prep
  commit alongside this changelog so it can no longer be skipped.

### Security

- The file-converter's realtime channel no longer hands out user tokens. The
  `/file-converter` Socket.IO namespace authenticated a connection on the
  public `x-service-name` header alone, then broadcast every upload and
  peak-detection payload - each carrying the acting user's access token - to
  the whole namespace, so any client able to reach the backend could
  subscribe and harvest live tokens, then replay them against the REST API as
  those users. Connecting now requires a service token derived per deployment
  from the JWT secret via the same domain separation as the reset and
  verification secrets - no new secret file, the backend and the converter
  already mount the same `jwt_secret_key` - and the token-bearing payloads
  are delivered only to the authenticated converter's own room, whose
  membership survives reconnects and multi-worker backends, instead of being
  broadcast to the namespace. Uploads are refused up front with 503 while no
  converter is connected, rather than accepted and stranded unprocessed in
  the filestore. **If your file converter runs on another host**, provision
  it the same `jwt_secret_key` and rotate the two together - see the hosting
  guide; a converter presenting a stale secret is rejected, and the log
  distinguishes it from an arbitrary unauthenticated client.
- State-changing API requests that declare a foreign origin are now refused
  (HTTP 403). Cross-site protection for writes previously rested entirely on
  the auth cookie's `SameSite=lax` attribute - a browser-side control that
  would vanish silently if the cookie ever needed `SameSite=None`. The backend
  now validates `Origin` (falling back to `Referer` where absent) on POST /
  PUT / PATCH / DELETE against the deployment's own origin, reconstructed per
  request from the proxy headers, so any hostname served through the shipped
  nginx works with no configuration; the Socket.IO handshake now consumes the
  same reconstruction from the shared policy module, one implementation for
  both surfaces. Behind the proxy only the browser-visible origin counts as
  the deployment's own - the internal upstream name is no longer accepted.
  Requests that declare no origin at all - the instrument agents'
  token-authenticated uploads, service calls, `curl` - pass unchanged, and
  the named dev-server origins stay accepted in development. **If you front
  Mascope with your own proxy**, it must preserve the browser's `Host` (or
  send `X-Forwarded-Host`) and, when it terminates TLS, send
  `X-Forwarded-Proto: https` - see the hosting guide - or every browser
  write will be refused. Pentest `CSRF-01` verifies the check.
- The Content-Security-Policy is now **enforced**; it had shipped as
  Report-Only pending a browser QA pass, which found the app itself clean
  (login, dashboard, batch overview, sample spectrum with Plotly, chart PNG
  export, and live Socket.IO produce no violations). The app policy is
  unchanged from the one previously reported. The bundled docs get their own
  policy: the Material theme boots through inline scripts, so `/docs/` allows
  same-origin inline scripts while dropping the app's `unsafe-eval` and blob:
  worker allowances it never needed. A `Permissions-Policy` header now also
  denies camera, microphone and geolocation outright.

## [1.7.0] - 2026.08.17

### Added

- An owner can now require every account to set a new password, from Manage
  users or with `mascope prod db script run require_password_change`. The
  minimum password length was introduced after many accounts already existed
  and nothing ever re-validated them, so a deployment could carry passwords
  weaker than its own policy with no way to find out. The requirement is soft:
  everyone keeps signing in with their existing password and is held at a
  password screen until they replace it. No account is excluded, the acting owner
  included. The requirement closes the API and the realtime channel alike: a
  pending account can still reach its own profile and the password form, and
  still receives its own notifications, but cannot read records over either
  surface. Existing API access tokens (SDK, notebooks, instrument agents) keep
  working, because their strength does not depend on the account's password and
  their holders cannot present a password screen; changing the password revokes
  them, and they must then be regenerated or re-paired. Withdraw the requirement
  with `mascope prod db script run clear_password_change_requirement`; there is
  deliberately no way to do that over the API.
- Passwords issued by an administrator are now temporary. Resetting another
  user's password, and creating an account, both leave that account required to
  choose its own password at next sign-in, so only its holder ever knows the
  password in use. The first owner, who chooses their own password during setup,
  is not asked to change it.
- The password policy now rejects the most commonly used and breached
  passwords. The bundled list is filtered to the lengths the 12-character
  minimum could otherwise accept, so it catches `qwerty123456` and
  `passwordpassword` without adding character-class rules.
- New black-box security suite in `security/pentest/`: 41 checks against a
  running deployment, each carrying its OWASP category, CWE and the SOC 2
  Trust Services Criteria it evidences, producing a severity-ranked report in
  Markdown and JSON. It covers recon, security headers, authentication,
  session and token handling, authorization, injection and traversal, error
  hygiene, uploads, transport, the Socket.IO surface, and rate limiting. It
  is standalone - its own dependencies and virtualenv, no imports from the
  app - so it can be pointed at any deployment. `security/pentest/README.md`
  covers how to run it and what a run does to its target.
- The suite refuses to produce a report it cannot attribute: it resolves the
  build under test before any check runs, from `GET /api/version` or
  `MASCOPE_PENTEST_BUILD`, and aborts otherwise. Every report also records an
  observable fingerprint of the served frontend, so a stale image reported as
  current is visible rather than assumed.
- Tenant isolation and the realtime access checks are automated rather than
  verified by hand: the suite provisions two peer accounts, confirms neither
  can read, modify or subscribe to the other's workspace - over REST and over
  Socket.IO - and deletes them afterwards. Also covered: whether the client
  address can be spoofed past the rate limiter, whether a state-changing
  request is accepted from another origin, and whether the published demo
  credentials open a deployment that is not the demo stack.

- New `GET /api/version` reports the version of the running deployment, so an
  operator or an audit can tie a deployment to an artifact without shell access
  to the host. It reports `MASCOPE_VERSION`, the same value that selects the
  image tag, so it always names the tag that was deployed. Admin-gated as least
  privilege for an operational endpoint; the login screen already shows the
  version, so this is not a confidentiality boundary.
- Sample browser: new m/z calibration status column. Every sample shows a
  color-coded badge - green: calibrated, with the fit quality in the tooltip
  (calibration point count, mean |m/z error| before/after the fit); red:
  automatic calibration failed, with the reason and attempt count, and match
  computation skipped until the sample is recalibrated; orange: calibrated,
  but the file arrived with acquisition-side m/z drift (the instrument likely
  needs retuning); grey: not calibrated (no calibration collection for the
  ionization mode, or a blank file). Clicking a badge opens the calibration
  dialog. (#1765, #1773)
- Calibration outcomes are now persisted on the sample file record: a
  given-up automatic calibration writes an explicit failed marker (error,
  attempts, final tolerance) instead of leaving the record empty, and applied
  fits store a quality block (calibration point count, pre/post-fit mean
  |m/z error| in ppm, calibrant-to-TIC fraction, parameters used) so the
  quality of any applied fit stays inspectable after the fact. Samples whose
  automatic calibration failed skip matching and peak assignment instead of
  silently matching on an uncalibrated m/z axis. (#1765)
- Acquisition drift alerting: when an applied fit's pre-calibration error
  exceeds 10 ppm, a warning is exported to error monitoring (grouped per
  instrument and drift magnitude) and the sample is badged in the browser.
  The software corrects the drift, but a drifting instrument needs operator
  attention; the marker (with the originally observed magnitude) survives
  re-calibration and is cleared only when re-processing restores the
  acquisition axis. (#1773)
- New `GET /api/calibration/default_params` returns a sample's
  instrument-appropriate default calibration parameters; the calibration
  dialog seeds its fields from it. (#1774)

### Fixed

- A production server no longer switches release channel when it restarts. The
  boot service (`mascope.service`) had no `WorkingDirectory`, so systemd ran it
  from `/`, where the CLI cannot read the release tag the deployment has
  checked out - the version resolution fell back to the rolling `latest` build,
  and a reboot silently replaced the pinned release with an unreleased master
  build (which, carrying newer migrations, can migrate the production database
  forward on startup). The units now run from the deployment checkout, the
  deploy version is resolved from `MASCOPE_PATH` rather than the process
  working directory, and a deploy that cannot resolve a version says so instead
  of quietly deploying `latest`. Existing servers need one
  `./tooling/ubuntu.sh install` to pick up the corrected units.
- `mascope prod doctor` reports the deployed version and flags drift between
  the images the containers are running and the release the deployment would
  deploy - the state above was invisible until someone read the version in the
  web UI. Drift makes the report exit non-zero, so a monitor catches it.
- Containerized deployments now receive `MASCOPE_VERSION`, which they never
  did: compose interpolated it into the `image:` tag but never passed it into
  the container environment. Every error reported to GlitchTip therefore
  carried no release, so events could not be grouped or attributed to a
  version, and the runtime had no version to report. Both compose files now
  set it from the same value that selects the image tag, so the reported
  version cannot drift from the image actually running.
- FTMS satellite ("sidelobe") peak flagging now catches real sidelobe
  patterns; previously it flagged nothing on production Orbitrap data. Real
  sidelobe mirror pairs are intensity-asymmetric and failed the old
  similarity gate, and unpaired shoulders were only flagged within 3 ppm of
  the parent. New defaults: mirror-pair similarity 0.25, single-sided window
  8 ppm (about one peak width), symmetric search window 100 ppm, base-peak
  pool widened to the top 20. Flagged sidelobes are excluded from matching,
  so results change where sidelobes were previously matched as weak
  isotopologues - the demo dataset goldens are regenerated as bundle v1.2
  accordingly. (#1772)
- Re-processing a sample file now recalibrates it from scratch. Orbitrap
  calibration rescales the file's stored m/z axes in place and tracks the
  running factor, so a re-processed file silently kept its previous
  calibration; the acquisition axis is now restored (exact inverse of the
  stored factor) and both calibration records cleared before the pipeline
  runs. (#1765)
- The calibration dialog no longer runs Orbitrap refits with TOF-shaped
  parameters (refine window 100 ppm, SNR threshold 10 - an order of magnitude
  looser than the automatic pipeline); it now uses the same instrument
  defaults the pipeline uses, preserving any user-edited values. (#1774)
- A tab left open no longer fills up with notification toasts. Each toast is
  dismissed on its own timer, and a browser throttles those timers in a
  background tab while notifications keep arriving over the socket, so coming
  back to a tab that had been processing a batch could mean a wall of toasts
  at once - enough, in the worst case, to make the view unusable. At most
  five are shown now; anything beyond that collapses into one summary
  carrying the highest severity it covers. Nothing is lost - the full history
  stays in the notification drawer and the sidebar badge still counts every
  warning and error. (#1809)
- The notification log no longer retains operation payloads. It kept the last
  250 notifications in full, including the results attached to them, so a
  session that ran composition searches held on to their entire result sets
  for as long as the tab stayed open: 16.6 MB for a representative search
  history, against 34 KB for the same history now. Log entries keep only what
  the drawer displays. (#1809)
- Notification watchers are released when the pane that registered them
  closes. The cleanup handle a component received registered itself too late
  to remove anything, so every open/close of the peak search pane left behind
  a callback that kept running on every matching notification and held that
  pane's search results with it. Cleanup now follows the component's
  lifetime. (#1809)

### Security

- The Socket.IO handshake now rejects cross-site origins. It ran with
  `cors_allowed_origins="*"`, which makes Engine.IO skip the origin check
  entirely and reflect any `Origin` back with `Allow-Credentials: true`, so a
  hostile page could open a *credentialed* realtime connection and only the
  browser's `SameSite=lax` cookie default stood in the way. Production now
  accepts only the deployment's own origin, reconstructed per request from
  `X-Forwarded-Proto` + `X-Forwarded-Host` so any hostname works without
  configuration; development names the Vite dev-server origins. The file
  converter now suppresses the `Origin` its websocket library synthesises
  from the connect URL, so it stays outside the check as a service client
  rather than depending on that value happening to match. nginx also
  forwards the browser's scheme rather than its own listener's, so a
  deployment behind someone else's TLS terminator reports the origin the
  browser actually used. The REST and realtime policies now come from one
  module so they cannot drift apart.
- The edge rate limits apply on every host, not only the TLS one. The
  per-client `limit_req`/`limit_conn` tier - including the stricter bucket in
  front of `/api/auth/` - lived only in the HTTPS config, so a deployment
  serving this image with `MASCOPE_TLS=off` behind its own TLS terminator
  silently lost the whole edge tier and fell back to the backend's Redis
  limiter, which fails open when Redis is unavailable. Nothing in the limiter
  needs TLS: it keys on `$remote_addr`, so it now lives in the body both
  configs share.
- The real client address now survives the Cloudflare proxy: nginx trusts
  `CF-Connecting-IP` from Cloudflare's published ranges only, so the
  backend's per-IP login rate limits, the new edge limits below, and the
  access log all key on the actual client instead of a shared edge address.
  The backend access log records that address too, instead of the nginx
  container's internal IP, so requests and failed logins can be tied to
  their source. (#1783, #1787)
- The edge bounds what one client can do: per-client request and connection
  limits (answering 429, so throttling is distinguishable from an outage),
  a stricter budget on the authentication endpoints, and a 10-minute cap on
  API requests (uploads get an hour; only Socket.IO keeps its 24-hour
  timeout, which previously applied to every proxied route). The nginx
  version is no longer advertised. (#1783)
- Every resumable-upload (tus) route requires authentication, enforced before
  the request body is handled. Previously the generated metadata routes (HEAD,
  OPTIONS, DELETE) accepted anonymous callers - a leaked upload id let anyone
  read upload metadata (filename, size, progress) or delete an upload in
  flight - and a chunk upload (PATCH) began writing to disk before the auth
  check ran. (#1784)
- A single resumable upload is capped - 5 GB by default, configurable with
  `tus_max_upload_gb` (see docs/maintaining.md). This lowers the effective
  ceiling from the tus library's 120 GiB default (the nginx body limit only
  bounds one chunk), so one runaway transfer cannot fill the disk. An upload
  declaring a larger size is refused up front with 413. The cap is per upload:
  how many files an instrument agent transfers per day is unaffected. Separately,
  nginx now caps a single request body at 100 MB (previously 2.5 GB), so
  whole-file legacy uploads above that go through the chunked tus route. (#1784)
- Resetting another user's password, and the enqueueing/export routes (peak
  recomputation, batch peak aggregation, peak and spreadsheet exports, the
  ion-focus visualization), are POST instead of GET. The auth cookie is SameSite=lax,
  which is sent on cross-site top-level GET navigations - so a crafted link
  could reset a user's password (account lockout, not credential theft) or
  spawn heavy background work with a signed-in admin's ambient credentials.
  Only the bundled frontend calls these routes, so no external client is
  affected. (#1785, #1786)
- The deployment env example no longer carries `MASCOPE_COOKIE_SECURE`. The
  production compose does not read it, and the demo compose already defaults it
  to false for its localhost HTTP, so the line did nothing in the example; the
  production cookie's Secure flag follows the runtime mode and is on in prod
  regardless. Removed so it cannot imply it governs the production cookie. (#1788)
- The HTTP/localhost frontend config now also suppresses the nginx version
  banner (`server_tokens off`), which previously applied only to the production
  config. The two configs share a host-agnostic body, and a build check now
  keeps that body from drifting so a future hardening cannot reach only one of
  them. (#1796)

## [1.6.2] - 2026.08.12

### Added

- SDK: new `examples` optional extra (`pip install "mascope_sdk[examples]"`)
  installs everything the tutorial notebooks import beyond the SDK core:
  plotly (with nbformat for notebook rendering), matplotlib, numpy, scipy,
  and ipykernel. Previously a fresh install following the getting-started
  guide hit missing-import errors in the notebooks (plotly, nbformat,
  matplotlib, ...); the guide and README now install the SDK with this
  extra.
- SDK: new example notebook `10_batch_stages.ipynb` - splits a batch timeline
  into time-range stages (e.g. background / exposure / recovery), averages
  ion- and compound-level intensities across the samples of each stage,
  normalises by each sample's TIC, and compares stages with grouped bar charts
  and a log2 fold-change heatmap against a reference stage. The batch-level
  counterpart of `05_peaks_by_stage.ipynb`.

### Changed

- SDK: name matching now follows a single contract everywhere. A plain string
  passed to name-based lookups (`resolve_id`-backed resolution in
  `datasets`/`batches`/`samples`/workspace selection, `samples.list(batch=...,
batches=...)`, and the `load_peaks` / `load_peak_timeseries` filters) is
  matched as a case-insensitive **literal substring** - regex metacharacters
  carry no special meaning, so names like `"Sample (A)"` match as-is. To match
  by regular expression, pass a compiled `re.Pattern` (case-sensitivity from
  its flags); compiled patterns previously crashed the resolution paths
  ("cannot set case for compiled regex"). **Behavior change**: raw-regex
  strings such as `"2026-01|2026-02"` were previously interpreted as regexes
  by `samples.list` and ID resolution. For now they keep working through a
  fallback that emits a `DeprecationWarning` when a string only matches as a
  regex - switch such calls to `re.compile(...)`; the fallback will be removed
  in a future release.

### Fixed

- Automatic Orbitrap m/z calibration no longer anchors to FTMS sidelobe
  ("satellite") peaks. When a file arrives with an instrument-side
  calibration offset beyond the old 10 ppm refine window, the true calibrant
  fell outside the window while its weak sidelobes (SNR well above the
  threshold) remained inside, and the one-point fit anchored to a sidelobe -
  applying a wrong calibration with no warning (observed as ~12 ppm
  miscalibrations on a customer Orbitrap instrument). A local-dominance guard
  now rejects candidates with a >=10x stronger peak within 100 ppm, and the
  default Orbitrap refine window is widened from 10 to 50 ppm so the true
  centroid is found on the first attempt. (#1762)

## [1.6.1] - 2026.08.11

### Fixed

- Match result tables no longer grow without bound under match-refresh churn.
  Match refresh removes and re-persists whole per-sample match sets, so
  `match_isotope` / `match_ion` / `match_compound` see delete+insert churn far
  above their live size; with PostgreSQL's default autovacuum settings the
  freed space was never reused between refresh cycles and the tables bloated
  (observed ~20x, hundreds of GB of dead space in production). A migration now
  sets per-table autovacuum thresholds (scale factor 0.01, base threshold
  100k) so vacuum keeps up with the churn and steady-state size stays a small
  multiple of the live data. This bounds future growth only - space already
  leaked must be reclaimed once per server with `VACUUM FULL`.

## [1.6.0] - 2026.08.11

### Added

- Public chemistry database integration, phases 0-2 (foundations through suspect-screening sources). A new `mascope_reference` library mirrors free-to-use public databases (PubChem, EPA CompTox, ChEBI, HMDB, LIPID MAPS, COCONUT, NORMAN) into `reference_source` / `reference_compound` tables, normalizing every source formula to the same canonical Hill order the de novo engine uses. Ingest a versioned snapshot with `mascope reference sync <source> <dump> --version <tag>` (see `mascope reference sources` / `status`) - a source-checkout command, because it pulls the chemistry dependencies deliberately kept out of the operator CLI; a wheel-installed deployment runs the same ingest inside the backend container instead (`docker compose exec backend python -m mascope_backend.db.scripts.reference_sync ...`, see `docs/dev/reference_data_authoring.md`). Composition results can be annotated with known reference compounds sharing each formula (name, structure, cross-references, source, license), collapsed one-per-compound on InChIKey - either by passing `known_only: true` for a suspect-screening prior that keeps only formulas backed by a known compound, or by opting into peak-centric assignment. Without one of those the composition search is not annotated at all, so its response keeps exactly the shape existing SDK clients already parse. The peak-assignment table surfaces the matched identity, and `mascope demo` seeds a small illustrative reference set so the annotation is visible without downloading a public-database dump. Custom reference data - e.g. published atmospheric peak lists not yet in the public databases - can be authored as a flat CSV/TSV and loaded with `mascope reference sync custom <file> --name <list>` (see `docs/dev/reference_data_authoring.md`). De novo scoring is untouched. `reference_compound` also carries a nullable `charge` column so permanently charged species (choline, quaternary ammoniums) can later be represented - recorded only: ingest still rejects charge-suffixed formulas and Stage A matches neutral formulas exclusively. Design: `docs/dev/public_database_integration.md`.
- Peak-centric assignment (first iteration): a sample's peaks can now each be assigned a chemical composition, inverting the target-first workflow. A new background engine matches every peak against the known target library (Stage A) and runs an untargeted composition search over the unexplained remainder (Stage B), then persists one row per observed peak with formula, adduct, isotope role, evidence, and a confidence tier (`identified` / `candidate` / `below_assignability` / `unassigned`). Results are stored in the new `peak_assignment_run` / `peak_assignment` tables (single-owner-per-peak enforced per run, full config recorded for reproducibility) and served by `/api/peak-assignments/sample/{id}` endpoints. **Off by default**: set `peak_assignment = true` under `[meta]` in your env toml (or `MASCOPE_PEAK_ASSIGNMENT=1`) to enable it. Until you do, the targeted workflow is unchanged - no assignment runs on sample ingest, the composition search keeps reporting the legacy match score and its previous response shape, and the Sample tab keeps its peak ledger. The flag also gates the API writes: with it off, the `/api/peak-assignments` write routes (assign, verify, recalibrate) return 403, while the read routes stay open so ledgers written while the feature was on remain inspectable after opting out. The frontend bakes the flag in at build time, so changing it on a deployment means rebuilding the frontend image. Assignment can be launched per sample or per batch, both with a configurable run (untargeted stage on/off, m/z precision, formula range, peak and alternative caps); a **batch** run defaults to the database stage only, since its cost scales with the number of samples, and skips blank and uncalibrated samples. Each run writes one row per observed peak, so re-running assignment accumulates rows: `mascope prod db script run prune_peak_assignment_runs` reclaims superseded runs (see `docs/maintaining.md`). Design and phased plan in `docs/dev/peak_assignment_paradigm.md`.
- Peak assignment Stage A now also matches against the reference mirror
  (curated and public chemistry databases loaded via `mascope_reference`),
  not just the curated target library. Reference formulas are bounded to
  atmospheric compositions (elements in C/H/N/O/S, C <= 40, monoisotopic mass
  <= 700 Da) and expanded through the existing ion / isotopologue path, so a
  peak can be assigned a known composition even when no curated target covers
  it. When a curated target wins a peak the reference identities ride alongside
  it in `provenance.reference_identities`; when only the reference set matches,
  the assignment carries the reference identities and null target linkage. One
  formula can carry many identities (isomers), preserved as a one-to-many list.
- `mascope_reference.iter_known_compositions` - a bulk provider that yields the
  active, deduplicated known compositions (formula -> identities) for Stage A,
  with license, element, carbon, mass, and per-formula identity-count bounds.
- An example curated reference database,
  `libraries/reference/examples/atmospheric_organics.csv` (79 atmospheric
  organics with 17 shared-formula isomer sets), for exercising the reference
  path end to end and as a template for hand-authored lists. Referenced from the
  reference-data authoring guide.

### Changed

- Rebranded from Karsa to Ultra Trace: Mascope is now maintained and developed
  by Ultra Trace Systems Oy.
  The app carries the new Ultra Trace logo, favicon, and color theme (Safety
  Orange primary on charcoal / off-white surfaces, per the UTS brand
  guidelines). GitHub URLs moved to the `ultra-trace-systems` organization,
  Docker images to `ghcr.io/ultra-trace-systems/mascope/...` (the old
  `ghcr.io/karsa-oy` namespace no longer serves images, so older checkouts
  should update their compose files), and contact emails to `@ultratrace.eu`.
  The user docs carry the same branding (charcoal / Safety Orange Material
  theme, UT logo and favicon), and both the app and the docs now use the
  brand typeface IBM Plex Sans (replacing Inter), self-hosted as before so
  nothing is fetched from third-party CDNs.
- Focusing a peak in the sample spectrum now zooms to an instrument-aware
  window: +/- 0.05 m/z on high-resolution instruments (Orbitrap), keeping the
  previous +/- 0.3 m/z on TOF. Applies regardless of the peak assignment flag.

### Fixed

- LaTeX math in the user docs now actually renders (previously the raw
  `$...$` / `$$...$$` source showed on the peak-detection, calibration,
  matching, and instrument-function pages). Math is rendered by KaTeX,
  vendored into the docs assets so the docs remain self-contained and
  air-gapped - no CDN requests.
- Removing a frontend data store's socket listener no longer detaches every
  other store listening on the same event name; each store now unsubscribes
  only its own handler. Cross-store reload events (e.g. shared batch reloads)
  survive pane teardown.
- Computed isotope match records serialize their signal-to-noise column as
  null instead of NaN when the sample file carries no SNR data, keeping the
  composition-search match response valid JSON.

## [1.5.0] - 2026.08.02

### Added

- The File Agent now uploads with the resumable TUS protocol - the same
  endpoint the web app uses - removing the 100 MB upload size limit.
  Files travel in 50 MB chunks (staying under reverse-proxy body
  limits), and an interrupted transfer resumes from the last
  server-confirmed byte instead of restarting. The backend's TUS routes
  now accept agent access tokens for this. Already-deployed agents are
  unaffected: the legacy single-request endpoint (capped at 100 MB)
  stays in place, so older File Agent and TOF Agent installations keep
  uploading as before - upgrading the agent is what lifts the size
  limit. (A new agent pointed at a not-yet-updated server likewise
  falls back to the legacy endpoint automatically.)

- File Agent uploads adapt to unreliable upload paths: a chunk that
  repeatedly dies mid-transfer is halved (50 -> 25 -> ... -> 5 MB)
  before resuming. Verified against a real Cloudflare tunnel, where a
  tuspyserver 4.1.3 server aborts PATCH bodies over ~25 MB (see the
  upgrade below) - the halving lets a new agent keep uploading through
  Cloudflare even against a not-yet-upgraded server, and guards
  against genuine proxy body caps in general. An abandoned upload now
  also deletes its partial data from the server instead of leaving it
  there until the next restart.

- `tuspyserver` upgraded 4.1.3 -> 4.2.0: fixes uploads behind
  Cloudflare, where 4.1.3 aborted PATCH request bodies over ~25 MB
  (A/B-verified through a real Cloudflare tunnel: 30-40 MB chunks fail
  on 4.1.3 and pass on 4.2.0), and picks up the upstream fix for the
  upload-breaking `file_dep` regression (issue #1159) plus
  offset/resume handling improvements. Newer versions stay blocked:
  4.2.1 ships a path traversal fixed only later, and 4.2.2+ cannot
  import on Windows (unconditional `fcntl`), which would break native
  dev runs. Web-app uploads now use 20 MiB chunks instead of the 5 MiB
  that worked around the 4.1.3 bug, cutting per-request overhead 4x on
  multi-GB files.

- The File Agent can now watch subfolders of the watched folder: answer
  yes to the new "Also watch subfolders?" question in the guided setup, or
  set `recursive = true` in its `config.toml`. The agent's own
  `failed_uploads` folder is always excluded, so failed files are never
  re-uploaded in a loop. The installer's finish page now also explains
  that the watched folder is chosen in the guided setup on first start,
  and the user docs cover disabling or removing the agent.

- `mascope fleet` (source checkouts only): `fleet list` shows the production
  servers from the fleet's Ansible inventory, and `fleet logs <host> ...`
  runs `mascope logs query --prod` on that server over SSH (tailnet),
  passing all query flags through — one command for agents and scripts to
  pull filtered production logs, with the inventory as the single source of
  truth for addresses and credentials.
- `mascope logs query` is now agent/script-friendly: `--json` prints the raw
  NDJSON records (no ANSI colors, no summary line), and `--service`/`-s`
  narrows the query to one service's log files (e.g. `-s backend`).
- Opt-in backend performance tracing: with the GlitchTip DSN set,
  `MASCOPE_SENTRY_TRACES_RATE` (0-1, default 0) samples that fraction of
  requests as transactions, surfacing per-endpoint latency in GlitchTip's
  Performance tab. Invalid values log a warning and keep tracing off.
- The user documentation is now built into every deployment and served at
  `/docs/` on the app's own origin, and the in-app help cards link to the
  relevant page. The docs gained first-steps orientation, concepts and
  data-hierarchy pages, guides for file import and matching, and a Python
  SDK getting-started guide.
- The batch overview chart has a draw style setting (Markers / Lines /
  Both) in its chart settings menu; line modes keep each series' assigned
  color instead of falling back to the default colorway.

### Changed

- The "Download File Agent installer" button in the sidebar's Settings tab
  is now always visible to editors, like the "Pair an agent" button,
  instead of appearing only while "File Agent" is the selected token type.
- `mascope logs query --max N` now returns the N _most recent_ matching
  lines (still printed oldest-first) instead of the N oldest, matching the
  "show me the last N errors" intent.
- The interactive API docs and OpenAPI schema (`/docs`, `/redoc`,
  `/openapi.json`) are served in dev mode only (#1675). They were never
  proxied to end users and only offered recon value on a directly
  reachable backend.
- `seed_demo` refuses to run unless `MASCOPE_ALLOW_DEMO_SEED` is set to a
  truthy value, so the public demo credentials (a well-known
  owner+superuser with fixed API tokens) can no longer be seeded into a
  real deployment by accident (#1675). The demo compose stack and
  `mascope demo` set the flag automatically.

### Fixed

- The file converter no longer floods the logs (and GlitchTip, when error
  reporting is on) with `ConnectionError` events while the backend is still
  starting up. It now waits for the backend quietly - retrying with backoff
  and logging each attempt at INFO - and starts its watcher and worker
  threads only after the socket is connected, which also removes the
  startup `BadNamespaceError` emit failures. A single WARNING fires if the
  backend stays unreachable for over two minutes, so a genuinely down
  backend still surfaces as one monitored event.
- Two `mascope` commands starting at the same moment no longer crash with
  `json.JSONDecodeError: Expecting value`. Every invocation rewrites
  `.runtime/state.json` during startup (the entrypoint clears the env
  override), and the write truncated the file in place, so a concurrent
  reader could parse an empty or half-written file - a backup cron and a
  monitoring cron firing the same second was enough. Runtime state is now
  written to a temp file and moved into place atomically, so a reader sees
  either the old file or the new one; an unreadable file is retried once and
  then falls back to defaults with a warning instead of raising. The
  auto-update state file is written atomically for the same reason: a
  partial read there is reported as "nothing pending", which would silently
  hide a pending migration.
- `mascope logs query --grep` no longer crashes on patterns containing
  quotes; user-provided filter values are bound as query parameters instead
  of being interpolated into the SQL.
- The pretty log printout now decodes JSON escape sequences in messages
  (previously quotes inside messages rendered as `\"`).
- Concurrent auto-processing can no longer create duplicate ACQUISITION
  year-datasets - a race that, once hit, made every subsequent file of
  that instrument/year fail with `MultipleResultsFound`. The natural key
  is now enforced with a partial unique index; the migration first merges
  existing duplicates by repointing their sample batches to the oldest
  dataset, and the get-or-create recovers from the constraint violation
  instead of racing.
- A transient infrastructure error (backend 502/503/504, database pool
  timeout) no longer permanently drops a file from auto-processing. Such
  failures are retried with growing delays, and the partial results of
  the failed attempt are cleaned up before each retry so a rerun cannot
  duplicate sample items.
- Switching batches no longer leaves the batch overview chart empty when
  the new batch shares the focused target collection with the previous
  one.

### Security

- Temp-file downloads (`GET /api/temp/{name}`) are scoped to the
  requesting user's own directory (#1675). Previously any authenticated
  user could fetch any temp file - spreadsheet exports, peak CSVs,
  download bundles - by guessing its name, which is derived from sample
  and batch names; crafted filenames could also traverse outside the temp
  directory. In-flight TUS uploads now live in their own `temp/tus/`
  subdirectory, separate from the per-user download directories.
- Socket.IO room subscriptions are authorized against the same workspace
  read ACL as the REST API (#1675). Previously any authenticated user
  could subscribe to an arbitrary room id and receive record-data
  broadcasts for workspaces they are not a member of. `user-<id>`
  channels are private to their user, global target collections stay
  readable to any authenticated user, and unknown rooms are denied.
- Login attempts are rate-limited per account in addition to per client
  IP, blunting distributed password guessing against a single account
  from rotating addresses (#1675). Only failed attempts count - a
  successful login clears the account's counter - so bogus attempts
  cannot lock the real user out; the Redis keys and log lines carry a
  SHA-256 digest of the identifier, never the submitted string.
- Password-reset and email-verification token secrets are derived per
  deployment from the JWT secret instead of hardcoded constants, and the
  raw reset/verification tokens are no longer written to the server log
  (#1675).
- `@api_route` now refuses at import time to register a non-public route
  whose `user` parameter does not bind an auth dependency (either
  `user=Depends(...)` or `Annotated[User, Depends(...)]`), closing a
  footgun where such a route was silently unauthenticated (#1675).
- The frontend ships a Content-Security-Policy in Report-Only mode
  covering the SPA's actual needs (#1675). It blocks nothing yet: after
  a browser QA pass (exercise a Plotly spectrum view and a live
  Socket.IO connection with the console open), enforce it by renaming
  the header in `server/frontend/nginx.conf` and `nginx.http.conf`.

## [1.4.6] - 2026.07.27

### Fixed

- Socket payloads no longer kill the frontend socket with a "parse error"
  disconnect. Two defects produced the identical symptom: a NaN/Infinity
  float anywhere in an emitted payload was serialized as a bare literal
  (invalid JSON the browser's parser rejects), e.g. a NaN isotope height
  in an ion-focus visualization trace; and a large ion-focus payload could
  exceed the client decoder's 200-attachment cap ("too many attachments"),
  since target ions store all isotopes above 0.001% abundance and each
  isotope contributes up to four binary arrays. The socket server now
  renders non-finite floats as null in every emitted payload (mirroring
  the REST-side sanitation), and the client decoder no longer caps binary
  attachments from Mascope's own backend.
- A socket payload that fails to decode client-side no longer reloads the
  whole app. The automatic reload-on-reconnect could not recover the lost
  packet and used to loop: reload -> auto-fired request -> same bad
  payload -> parse error -> reload. After a parse-error disconnect the
  client now just reconnects and re-subscribes its rooms; network-level
  reconnects keep the reload as the stale-data safety net.

## [1.4.5] - 2026.07.27

### Added

- Fleet release rollouts as a playbook: `tooling/fleet/update.yml` deploys a
  release across the fleet one server at a time (fail-fast, verified with
  `mascope prod doctor`, and reinstalls the CLI so it cannot drift behind the
  checkout). `docs/maintaining.md` gains the matching canary-first rollout
  runbook with a per-server verification checklist.

- Fleet Ansible now supports per-host sudo passwords via Ansible Vault
  (`tooling/fleet/group_vars/fleet/`): the encrypted vault (gitignored, kept
  out of this public repo) holds each server's password, unlocked with a single
  `--ask-vault-pass` prompt so whole-fleet runs no longer need per-host `-K`.

- Stack-health push monitoring: `tooling/monitoring/doctor-push.sh` reports
  `mascope prod doctor` results to an Uptime Kuma push monitor per server
  (dead-man's switch), carrying disk usage in each heartbeat; runbook section
  in `tooling/monitoring/README.md`. The systemd README now also documents the
  disk-space monitor units (`mascope-disk-check.service`/`.timer`), which its
  unit table previously omitted.

- Fleet configuration as code under `tooling/fleet/` (Ansible): roles for the
  sshd hardening drop-in, the ufw ruleset (tailnet-only SSH, Cloudflare-only
  443, canonical container-NAT block), the load-bearing Docker
  `iptables: false` setting, and unattended security upgrades - with a
  check-first drift workflow (`ansible-playbook site.yml --check --diff`).
  The inventory is deliberately an example file: real addresses stay out of
  this public repository.

### Fixed

- Acquisition ingest bursts no longer exhaust the backend's database
  connection pool. Auto-processing pipelines (batch creation, calibration,
  matching for each converted file) and the follow-up rematch tasks they
  spawn now run at most three at a time per worker; an unbounded burst - a
  whole folder of raw files converted back to back - stacked enough
  concurrent sessions that everything waiting longer than `pool_timeout`
  died with "QueuePool limit reached, connection timed out": the converter's
  API calls failed with 400 (raw files quarantined in `failed_files`),
  service-token validations failed, and pipelines died between creating the
  sample file and its sample items. `pool_timeout` is also raised 30s -> 120s
  so residual congestion queues instead of failing. Together with the upload
  race below, this caused the nightly reproducibility workflow's
  nondeterministic "144..155/161 files processed" failures.
- The file converter no longer quarantines a raw file because the backend
  was momentarily too busy to answer. Connection-pool starvation now answers
  503 Service Unavailable (previously a misleading 400 "Database operation
  failed"), and the converter's backend API calls retry transport errors and
  5xx responses with backoff (15/30/60s) before giving up. Client-side
  request timeouts were also raised above the server's pool patience so a
  slow-but-successful call is no longer killed from the client side.
- Uploaded sample files can no longer be silently lost to a race with the
  file converter. The upload endpoints wrote bytes directly under the final
  filename inside the watched filestreams folder, so a write that stalled
  for a couple of seconds (I/O contention) looked size-stable to the
  converter's watcher and was ingested truncated - the upload still returned
  success, the sample just never appeared. Uploads now land under a
  non-watched temp name and are published with an atomic rename. This was
  the cause of the nightly reproducibility workflow's nondeterministic
  "pipeline did not settle: 147..155/161 files processed" failures.
- The file converter's watcher now queues stream files already present when
  it starts (e.g. left behind by a converter restart) instead of silently
  ignoring them forever.
- The reproducibility test now fails fast when the pipeline stalls (10 min
  with no counter movement instead of idling out the full 45-min timeout)
  and reports which stage lost files: streams never picked up, streams
  quarantined in failed_files, or files converted without sample items. The
  workflow now uploads full stack logs and container restart/OOM states as
  an artifact on failure, instead of dumping the last 300 log lines.
- GlitchTip events now carry a friendly per-server identity: `server_name` is
  the runtime env name (e.g. `site1`) instead of the opaque Docker container
  id the SDK falls back to under containers.

## [1.4.4] - 2026.07.26

### Added

- Agent device pairing: instrument agents can now be connected without
  copy-pasting an access token. The File Agent's guided setup offers
  pairing as the default - the agent shows a short code (e.g. `BCD-234`),
  an editor approves it in the web app under API Access Tokens > "Pair an
  agent", and the agent picks up its token automatically. Each pairing
  creates its own token without revoking existing ones, so pairing a new
  instrument PC never disconnects another (the manual Regenerate button
  still replaces all of the user's tokens for the service). Backend:
  `/api/auth/pairing/start|poll|approve` - start/poll are unauthenticated
  and rate-limited per client IP, codes live in Redis with a 10-minute
  TTL, and only the authenticated editor-role approval mints a token,
  which is handed to the agent exactly once. Access tokens gain an
  optional description stamped with the paired machine's hostname
  (migration `f2d8b5c3a9e1`).

### Fixed

- The File Agent setup wizard no longer reports a working connection when
  the configured address is a web server that is not the Mascope API. Any
  single-page-app server (such as the Vite frontend dev server in
  development setups, which cannot receive uploads) answers a GET with the
  app page and HTTP 200; verification now requires a JSON API response and
  explains which address to use instead. Uploads rejected with 404/422 also
  fail fast with a pointed error instead of burning ten 30-second retries
  on a response that cannot change.
- Error reporting no longer floods GlitchTip with routine events. Expected
  client errors (failed logins, expired sessions, validation errors) and
  routine conditions (locked-file retries during acquisition, per-sample
  failures in batch loops, transient retries, deprecation notices) now log
  at INFO or below, per-item loops aggregate into a single summary warning,
  and each incident is reported exactly once - previously one failed login
  produced two events and an unhandled server error up to three. Failures
  caught in except blocks now carry their traceback (logger.exception), so
  GlitchTip groups them as real exceptions instead of one issue per
  interpolated file name. The CLI no longer reports at all: its warnings
  and errors are user-facing terminal output, and hosts that export
  MASCOPE_SENTRY_DSN in the shell no longer get a per-invocation
  "sentry-sdk not installed" nag. The level policy (WARNING+ is exported
  to error monitoring, so levels express operator relevance) is documented
  in the developer guide under "Choosing a level" and in
  mascope_runtime.logging.
- Failures that previously died silently are now visible: stdlib logging
  records (zarr, the Thermo reader) are bridged into loguru and reach the
  log files and the monitoring sink; the file converter's rematch call
  checks the HTTP status, so a failed rematch after a peak recompute is
  reported instead of ignored; socket event-handler bugs are logged with
  their traceback instead of being swallowed as auth errors; background
  rematch tasks are kept referenced and observed (previously they could be
  garbage-collected mid-run or die as unretrieved exceptions); and corrupt
  CLI state files (auto-update state, instance registry) log a warning
  instead of being silently treated as empty. Also fixes tofwerk's
  open_h5_file relabeling exceptions raised inside the caller's with-body
  as "Failed to open the file".

## [1.4.3] - 2026.07.25

### Fixed

- GlitchTip error reporting is now actually deployable: backend images ship
  `sentry-sdk` (the runtime's `[sentry]` extra), and `docker compose` passes
  `MASCOPE_SENTRY_DSN` from the host into the backend and file-converter
  containers. Previously the DSN never reached the container and the SDK was
  missing from the image, so enabling reporting per the runbook had no effect.
  Reporting remains off unless the DSN is set.

### Added

- Self-hosted monitoring stack under `tooling/monitoring/` (GlitchTip error
  tracking + Uptime Kuma uptime/TLS-expiry monitoring), deployable to an
  internal monitoring box with a copy-paste runbook: compose files, DOCKER-USER
  firewall rules restricting the published ports to LAN + tailnet (plain ufw
  cannot filter Docker-published ports), a restic backup script for the new
  volumes, and Uptime Kuma monitor guidance including inverted port-22/443
  tripwires on the fleet's public IPs.
- Optional error reporting from the backend to a self-hosted GlitchTip/Sentry
  instance. A loguru sink forwards `WARNING`/`ERROR` records (with tracebacks
  and request context) as events. It is **off by default** and gated entirely on
  `MASCOPE_SENTRY_DSN`: unset means no SDK import and no behavior change; set it
  on the backend service (with the `mascope_runtime[sentry]` extra installed) to
  turn reporting on. Events carry the runtime mode as `environment` and
  `MASCOPE_VERSION` as `release`. See `docs/maintaining.md` -> Monitoring.
- File Agent Windows installer (Inno Setup): per-user install with no admin
  rights, Start Menu entry, an optional run-at-login startup task so the
  agent survives reboots, and an uninstaller that leaves the configuration
  in `%AppData%` untouched. Built and attached to every GitHub release by
  CI as `Mascope-File-Agent-Setup.exe` (fixed name, so
  `releases/latest/download/...` always serves the newest version) plus a
  versioned copy; the exe is stamped with the release version and reports
  it at startup. A "Download File Agent installer" button in the web app's
  user settings links to the latest release. File Agent unit tests now run
  in CI on every PR.

- File Agent guided setup: on first start (or with `--setup`) the bundled
  agent asks for the server address, access token and watched folder in the
  console, verifies the token against the server right away, and starts
  watching - no more editing TOML files in `.runtime` or `state.json` by
  hand. A user-facing installation guide was added to
  `docs/user/instruments/index.md`.

### Changed

- File Agent settings now live in a single flat file,
  `%AppData%\Mascope\FileAgent\config.toml`; the nested runtime config is
  regenerated from it on every start. Logs moved to
  `%AppData%\Mascope\FileAgent\logs`. Existing installs are migrated
  automatically, and config-schema changes no longer require deleting the
  configuration on upgrade (missing keys fall back to defaults). A host
  configured with an explicit `http://` scheme is now respected for
  plain-HTTP servers.
- Batch match aggregation is roughly twice as fast on large batches (a full
  re-create of a 2268-sample stress batch drops from ~9 to ~5 minutes, with
  bit-identical results). The match create funnels now write each level as a
  single bulk `INSERT .. ON CONFLICT DO UPDATE .. WHERE row IS DISTINCT FROM
excluded` statement instead of a per-row ORM read-then-diff loop; the ion
  and compound aggregations group on id columns instead of up to 15 label
  strings; and the aggregation chunk size adapts to the batch's target-chain
  shape instead of a fixed sample count. Per-chunk aggregate/create timings
  are logged at debug level so production refreshes show where aggregation
  time goes.

## [1.4.2] - 2026.07.25

### Fixed

- A target collection without a description no longer silently loses all of
  its match aggregates. The aggregation pipeline groups on label columns, and
  pandas drops groups whose key is missing - so a NULL
  `target_collection_description` (legal in the API, and since the v1.4.0
  PATCH-semantics change the stored value for a collection created without a
  description) erased every row of that collection from all aggregate levels
  with no error. Nullable label columns are now normalized before
  aggregation; the labels are display-only, so the change is lossless.

## [1.4.1] - 2026.07.25

### Fixed

- Backend/file-converter images are now built with dependencies constrained to
  `uv.lock` instead of re-resolving from PyPI at build time. Unconstrained
  resolution let transitive pins drift within their specifiers - the opentfraw
  raw reader floated from the locked 1.2.0 to 1.3.x in freshly built images,
  which made the nightly golden-dataset reproducibility workflow fail its
  reader-version pin on every run since it was introduced.
- Batch match aggregation no longer exhausts process memory on large batches.
  The full-batch aggregation built one reconstructed isotope frame for the
  whole batch (samples x isotopes x collection memberships, with a dozen
  string columns); on a 2306-sample production batch this OOM-killed the
  backend worker mid-refresh, severing its database connections and leaving
  the batch stuck in "processing". Batch aggregation now runs in bounded
  sample chunks (200 samples per pass) - every aggregate level is per-sample,
  so chunking bounds peak memory without changing any persisted value - and
  the frame assembly runs in a worker thread so a large aggregation can no
  longer starve health checks and other requests on the same worker.
- The aggregation-completeness signal is now crash-safe: the scope's
  sample-level aggregates are cleared when an aggregation starts and restored
  chunk by chunk, so any death mid-aggregation - an exception, but also an
  OOM kill or restart that no error handler ever sees - leaves the next
  refresh re-aggregating instead of skipping over stale aggregates that sit
  next to freshly stored match isotopes.

### Changed

- Updated several frontend dependencies minor versions
- Updated several backend dependencies minor versions

## [1.4.0] - 2026.07.24

### Added

- Target compounds can be pasted from a spreadsheet as a single formula
  column; the name column is no longer required. The paste infers the layout
  from the column count (1 = formula, 2 = name + formula, 3 = name + formula +
  CAS), tolerates a header cell on formula-only pastes, and rejects columns
  that do not contain valid formulas with a clear message. The target
  collection dialog also gains help-mode popovers (paste layouts, add-compounds
  panel, collection types); previously help mode showed nothing there.
- PostgreSQL `max_connections` is now configurable via `[backend.database]` in
  the `.mascope.toml` layers and passed to the postgres container like the
  other tuning flags (`MASCOPE_DB_MAX_CONNECTIONS`). Default stays 100;
  production sets 200. `mascope prod db status` / `mascope dev db status` now
  display the server cap next to the pool settings.

### Fixed

- Creating or updating a large target collection no longer shows a spurious
  "Request timed out" error while the backend is still working: the frontend
  gives target collection create/update/delete a 5-minute request timeout
  (the global default is 20 s, but generating ion and isotope patterns for
  hundreds of pasted compounds legitimately takes longer). The backend also
  fetches ionization mechanisms once per request instead of once per created
  compound.
- Collection-batch associations now respect workspace scoping from both
  directions: a workspace-scoped collection can no longer be assigned batches
  from another workspace (409), and a batch can no longer be assigned another
  workspace's collection - previously only scope _changes_ were validated, so
  the invariant could be silently violated at assignment time. Changing a
  collection's batch associations now also requires editor access to the
  workspaces of the batches being added or removed (associations the request
  preserves are exempt, keeping cross-workspace global collections manageable
  by admins).
- Workspace editors can now use "Edit batches" on a global target collection
  to bulk-assign it to their own workspaces' batches: a batches-only update
  no longer requires the admin rights reserved for mutating the collection
  itself (name, type, scope, compounds), only editor access in the workspaces
  of the batches being added or removed. The Manage batches dialog
  accordingly sends a batches-only payload, and the collection update API
  gained true PATCH semantics: omitted fields are left unchanged, whereas
  previously an omitted collection type was read as the default TARGETS and
  an omitted description as empty, wrongly flagging changes (triggering
  needless full-batch rematches) and validating batch types against the wrong
  collection type.
- Production backend pool exhaustion during acquisition ingest
  (`QueuePool limit of size 3 overflow 2 reached, connection timed out`): a
  burst of converted files stacks several concurrent calibrate/match pipelines
  on a single uvicorn worker, exhausting its 5-connection pool and failing
  unrelated requests (including auth) on that worker for 30 s at a time.
  `max_overflow` is raised 2 → 7 in prod (per-worker ceiling 5 → 10;
  12-worker peak 120, under the new `max_connections = 200`). The developer
  guide's connection-pool section now documents the two sizing constraints
  (global budget and per-worker burst ceiling).
- "Refresh matches" is incremental again. Since v1.3.0 stopped storing
  non-matching (score-0) `match_isotope` rows, every refresh re-fetched and
  re-scored every previously non-matching isotope of every sample - adding a
  few targets to a large batch redid the whole batch's matching work. Matching
  now persists one zero-score **sentinel** row per fully non-matching ion (the
  main isotope), and the unmatched-isotope fetch skips every isotope of an ion
  that has any stored row, so a refresh computes only ions never evaluated for
  the sample (or invalidated since). The sentinel adds back roughly one row per
  non-matching ion - a small fraction of the rows the optimization removed.
  The `remove_unmatched_match_isotopes` maintenance script now converts legacy
  score-0 rows into sentinel form instead of deleting them all, so cleaned
  databases keep their evaluated markers.
- Concurrent match writes no longer deadlock. All match create/delete funnels
  serialize per sample batch on transaction-scoped Postgres advisory locks and
  process rows in stable natural-key order, eliminating the
  `DeadlockDetectedError` seen when a batch refresh overlapped another refresh
  or the upload pipeline's per-sample aggregation. The match tables also gain
  unique constraints on their natural keys (a migration dedupes any existing
  duplicates first, keeping the newest row per key), so a race now fails loudly
  instead of silently duplicating rows.
- Editing a target ion's match parameters now also deletes the ion's stored
  match isotopes (previously only its match ions), so the recompute after the
  edit actually applies the new parameters instead of skipping the stored
  isotopes.
- Changing a target compound's formula (or deleting a compound) now flags the
  affected batches for rematch. Previously the edit cascade-deleted the
  compound's match rows across all batches but left the batches marked "ready",
  where a plain refresh is refused.
- User-facing error messages no longer contain duplicated wording. Nested
  controller layers each prepended their own "Failed to ..." context
  ("Failed to Update Workspace. Failed to Update Workspace. ..."), and the
  global HTTP exception handler repeated the error detail twice while leaking
  internal request wording ("HTTPException on POST /path | detail=...") to the
  client. The most specific message now wins; full context still reaches the
  server logs.
- The File Agent reports the specific upload failure cause (rejected token,
  timeout, connection refused, server error message) instead of a generic
  "File upload failed", and no longer retries on a rejected access token - it
  fails fast with a hint to fix the configured `access_token`. Its 401 log
  line previously printed "None Please check your API token.".
- File converter error notifications no longer surface bare exception reprs
  (a malformed h5 file showed as "Failed to process X: 'Configuration File'")
  or relabel known causes as "Unexpected error"; cryptic messages are prefixed
  with the exception type instead.
- The SDK surfaces the backend's human-readable error message; previously
  every API error rendered as the opaque `{'error_id': '...'}` dict.
- Frontend error toasts always carry a message (some failure paths showed a
  blank toast or the literal text "undefined").
- CLI: `mascope demo` prints a clean one-line error instead of a Python
  traceback for ordinary fetch/restore failures; `mascope env sync`/`create`
  no longer double their error wording; database script discovery logs
  skipped (broken) script modules at debug level instead of hiding them
  silently.

### Changed

- The batch refresh skips the full-batch higher-level aggregation when provably
  nothing changed (nothing computed or removed, stored aggregates complete), so
  a no-op refresh is near-instant. Partial (orphan-only) match removal now
  deletes sample-level aggregates only for the affected samples instead of the
  whole batch.
- Genericized error messages ("Unexpected error.", "Database operation
  failed.") now include a short reference to the server-side log entry, e.g.
  "(ref: 3f9a1b2c)", so users can quote it to support for correlation.

## [v1.3.2] - 2026.07.12

### Fixed

- Give nightly system-test jobs a postgres-password secret

### Added

- `mascope prod doctor` - a read-only, network-free command that reports the
  deployment's status at a glance: container health, free disk on the state and
  docker filesystems, the recorded pending update, local backup freshness, and
  the docker image footprint. Exits 0 when healthy and 1 when a container is
  down or a filesystem is below the free-space floor, so it doubles as a
  monitoring probe; `--json` emits the same data for scripting.
- Disk-space monitor (`tooling/disk-check.sh`) with a systemd timer
  (`mascope-disk-check.timer`, installed **and enabled** by `tooling/ubuntu.sh`,
  runs every 15 minutes). It measures free space on the `.runtime` and docker
  filesystems and, when either drops below a floor (`MIN_FREE_GB` default 10, or
  `MIN_FREE_PCT` default 10), pings a healthchecks.io-style `HEALTHCHECK_URL` so
  an operator is alerted before a full disk wedges Postgres and takes the stack
  down. Read-only - it never deletes anything. Configure in
  `/etc/mascope/disk-check.env` (template `tooling/disk-check.env.example`); see
  the "Disk space" section of the maintainer runbook.

### Changed

- `mascope prod update` (and unattended `--auto`) now refuse to pull new images
  when free space on the docker image store is below `MASCOPE_UPDATE_MIN_FREE_GB`
  (default 5 GiB), so a pull cannot fill the disk mid-flight. Under `--auto` the
  shortfall is recorded to the update `status.log` and returns the error exit
  code.
- After a successful update the tooling prunes unused images
  (`docker image prune -af`), reclaiming the superseded release's images that
  were previously left behind on every update - a slow disk leak that unattended
  updates would otherwise accumulate. The running stack's images are kept; a
  rollback re-pulls the previous release as before.
- `db_init` now prunes old pre-migration dumps, keeping the most recent
  `MASCOPE_PREMIGRATION_KEEP` (default 5). Each migration update writes a full
  pre-migration dump into the backups directory; previously these were pruned
  only by the optional backup cron, so a server with auto-updates but no cron
  slowly filled its disk with old dumps. Only `*_pre-migration.dump` files are
  touched - cron/manual dumps are left alone.
- Rotated application log files are now compressed (loguru `compression="zip"`),
  roughly a 10x reduction on the two weeks of retained logs.

## [v1.3.1] - 2026.07.11

### Fixed

- Add write permission to Build release images workflow job
- Fix match visualization for unmatched isotopes

## [v1.3.0] - 2026.07.10

### Changed

- `match_isotope` no longer stores non-matching isotopes, cutting the largest
  table in the database by the majority of its rows (on one production instance
  it was 209 GB / 93% of the database, ~80%+ of it placeholder rows). Matching
  scores every candidate isotopologue against the sample peaks, but only those
  that score above zero are a real match; the rest - no peak within the match
  window, or a peak whose m/z error (>= 100 ppm) or abundance error (>= 100%) is
  so large it can never become a match at any read-time tolerance - are now
  dropped on write and **reconstructed on read** from their target isotope. The
  Match-tab isotope table still lists every expected isotope, and all
  higher-level aggregates (`match_ion` / `match_compound` / `match_collection` /
  `match_sample`) are unchanged because a non-matching isotope contributes zero
  to every aggregate. Read-time tolerance loosening is preserved in full: the
  persist threshold coincides exactly with the UI slider ceilings (m/z tolerance
  100 ppm, isotope ratio tolerance 1.0), so every record reachable as a nonzero
  match is still stored. Going forward, matching also writes far fewer rows,
  bounding the growth rate rather than only reclaiming once.
- The Match-tab isotope table shows a match tag only for actual matches
  (possible/probable). Isotopes that are not a match under the current
  tolerances - never detected, or scored zero - now show no tag instead of a
  misleading 0%.

### Added

- `remove_unmatched_match_isotopes` maintenance script (`mascope prod db script
run remove_unmatched_match_isotopes`) reclaims the historical non-matching
  rows from existing databases. It deletes `match_isotope` rows with
  `match_score = 0` in bounded batches (configurable `BATCH_SIZE`, `DRY_RUN=1`
  to preview) so a multi-hundred-million-row table can be cleaned without one
  giant transaction; the delete is lossless for aggregates. Run `VACUUM FULL
match_isotope` (or pg_repack) afterwards to return the freed space to the OS.

### Fixed

- `mascope prod db script run` no longer fails with exit 127 on images built
  from source. It resolved the in-container Python from a single hardcoded path
  (`/root/.local/share/uv/tools/...`) that only matched older published images;
  current images install the tool under `/opt/uv/tools`. The runner now probes
  the known tool locations (and falls back to a `python` on `PATH` that can
  import `mascope_backend`), so it works regardless of how the image was built.

## [2026.07.08]

### Added

- Read-path performance benchmark suite (`server/backend/tests/system/benchmark/`, opt-in with `MASCOPE_BENCH_TEST=1`): clones the demo dataset up to thousands of samples and collection ions, then exercises the hot batch-overview and sample-browser endpoints, asserting a per-request latency budget (default 20 s, the frontend timeout) and a response-size budget. A nightly workflow (`.github/workflows/benchmark.yaml`) runs it against a freshly built demo stack and publishes the timings, so a latency or payload-shape regression at scale surfaces before a user hits it.
- Unattended, self-classifying updates for pinned deployments (`mascope-cli`
  2026.7.8). `mascope prod update --check` classifies a pending update as
  up-to-date, a _fast_ update (new images, no database migration, near-zero
  downtime) or a _migration_ update (a schema migration will run and cause
  downtime) by reading the Alembic head the target release carries and comparing
  it to the live database, so a maintenance window is only scheduled when one is
  actually needed. Releases now publish a small `mascope-manifest.json` (a GitHub
  Release asset) recording that head, which `--check --manifest` reads without
  inspecting the image. `mascope prod update --auto` (driven by the systemd
  `mascope-update.timer` in `tooling/systemd/`) applies fast updates inside a
  configurable maintenance window (`MASCOPE_UPDATE_WINDOW`) with a post-apply
  health check, and applies a migration update once its grace period elapses
  (`MASCOPE_UPDATE_GRACE_DAYS`, default 7) or an operator runs `mascope prod
update --confirm`; `mascope prod update --snooze N` postpones it. A failed
  health check alerts and stops without rolling back automatically. Release
  discovery uses the public GitHub API over plain HTTPS, so no token is needed.
  `tooling/ubuntu.sh` installs the systemd units (the update timer left disabled
  until you opt in), and `docs/maintaining.md` is the operator runbook covering
  provisioning, updates, backups, and troubleshooting.
- The web UI now survives a full page reload. The active selection chain
  (workspace -> dataset -> batch -> sample -> collection -> ion) is persisted to
  browser storage and restored on load, so a reload - whether from an
  auto-update restarting the backend, a transient network failure, or pressing
  F5 - lands you back where you were instead of near the top of the navigation.
- You can now share a link to a specific view. A "Copy link to this view" action
  in the toolbar copies a URL that reopens Mascope at your current selection
  (workspace -> dataset -> batch -> sample, plus the focused peak or match ion);
  opening the link restores that view for the recipient, provided they can access
  the same data. The address bar stays clean during normal use - sharing is
  explicit - and if part of a shared view can't be opened (for example no access
  to a workspace), the app opens as much as it can and says what it could not.
  When a newer build has been deployed, a dismissible banner offers to reload.

### Changed

- SDK (`mascope-sdk` 2026.7.7): the `load_peaks` / `load_peak_timeseries`
  `batches` and `samples` filters now treat a string as a case-insensitive
  **literal substring** instead of a regex, so values with metacharacters (e.g.
  `"Sample (A)"`) match literally. Pass `exact=True` to match a whole name, or a
  compiled `re.Pattern` (e.g. `re.compile("2025|2026", re.IGNORECASE)`) to filter
  by regex. Callers relying on regex/alternation in a plain string must switch to
  a compiled pattern. The dead `**kwargs` on `MascopeClient.load_peaks` /
  `load_peak_timeseries` was removed, so an unknown keyword such as `batch=...`
  (singular) now raises `TypeError` instead of being silently ignored.

### Fixed

- SDK (`mascope-sdk` 2026.7.7): `POST` requests now send their body as
  `application/json`. Previously the body was serialized with
  `data=json.dumps(...)` and no `Content-Type` header, so the backend received it
  as opaque bytes and rejected every SDK `POST` carrying a body with a 422
  validation error (surfaced by `load_peak_timeseries` on
  `POST /api/samples/{id}/peaks/timeseries`).

## [v1.2.0] - 2026.07.07

### Changed

- The batch overview chart loads its per-sample datapoints through a new columnar endpoint (`POST /api/match/records/ion/series`) that sends each ion's metadata once with parallel per-sample value arrays, scoped by batch ID instead of an explicit list of every sample ID. On a 5,000-sample batch this cuts a full large-collection chart load from minutes of ~25 MB chunk responses to seconds, and the chart no longer rebuilds every Plotly trace from deep clones when toggling the average/sum scale.
- The spectrum, match-spectra and match-timeseries charts no longer deep-clone every Plotly trace when the intensity scale is toggled; they build shallow copies that share the unchanged data arrays. Noticeable on long acquisitions (thousands of scans per trace).
- API responses now carry a `Server-Timing` header and request logs include `duration_ms`, so slow endpoints are visible in browser devtools and server logs without extra tooling.

### Added

- The Mascope CLI is now a standalone PyPI package: `pip install mascope-cli`
  on a machine with Docker, then `mascope init` (creates a runtime home with
  editable config, compose files and generated secrets), `mascope cert gen`
  and `mascope prod up` bring up a deployment — no source checkout needed.
  Importing the CLI is side-effect free, `mascope --help` works before any
  environment exists, and commands without a configured home fail with a
  pointer to `mascope init` instead of a traceback. The standalone install
  ships the operator surface (`init`, `prod`, `env`, `demo`, `logs`, `cert`);
  developer commands (`dev`, `test`, `agent`, `backend`) remain available in
  the monorepo checkout, which is unchanged. The shared runtime library is
  published alongside as `mascope-runtime`. A hermetic CLI test suite and a
  packaging smoke test (wheel installed into an isolated environment) run in
  CI on every PR. Without a `MASCOPE_VERSION` pin, a pip-installed CLI
  deploys the `latest` release images.
- `mascope prod update`: update a deployment in one step — pulls the target
  release images (`--version vX.Y.Z`, or `latest` without a pin), restarts
  the stack with them, and shows container status. Database migrations run
  automatically on startup, preceded by a pre-migration dump; a failed pull
  aborts before the running stack is touched.

- Frontend unit test layer (Vitest): fast, backend-free tests covering formatters, chemistry helpers, batch import validation and API utilities. Run with `npm run test:unit` or `mascope test run frontend`.
- Hermetic end-to-end test suite (Playwright) that runs against the demo stack with API-seeded state, covering login, the app shell, and dataset / batch / target collection management. Both frontend suites now run in CI on every PR, with traces and reports uploaded on failure.
- SDK contract tests: `MascopeClient` is exercised end-to-end against the demo
  stack (workspace resolution, dataset/batch/sample listings, matched peak
  retrieval), doubling as a breaking-change detector for the public REST API.
  They run in CI inside the demo-stack e2e job and locally with
  `MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/sdk/tests/`.
- Upload-to-browse e2e test: a real demo raw file is uploaded through the web
  UI (Uppy/tus), the file-converter runs the real conversion -> peak detection
  -> matching pipeline, and the result must appear in the Raw files browser.
  Unit tests for the converter's building blocks: the peak-detection
  concurrency guard, the upload-context registry whose filename normalization
  decides whether an uploaded file is "registered", and the filestream watcher
  that must queue a file only once it has stopped growing.
- Golden-dataset reproducibility test: the demo bundle's raw files are ingested
  through the real upload -> convert -> match pipeline and the produced peaks
  must reproduce the bundle's golden outputs within the manifest tolerances
  (sub-0.1 ppm m/z). The demo stack gained a rebuild mode
  (`MASCOPE_DEMO_REBUILD=1`) that restores only the reference seed so ingestion
  starts from scratch; CI runs the test nightly and on manual dispatch
  (`.github/workflows/reproducibility.yaml`).
- The `libraries/` test suites (chem, file, match, molmass, signal, thermo, tools)
  now run in CI on every PR; previously they only ran when invoked locally.
- Frontend unit tests for the notification hub (process tracking, badges,
  watcher dispatch, log retention) and the spreadsheet-paste table parser.
- Unit tests for the core matching pipeline: the isotope-to-peak assignment
  rules (closest-in-window, per-ion peak uniqueness, abundance priority, m/z
  ordering) and the match statistics (abundance/mz error and score formulas)
  in `mascope_match`, plus the ion -> compound -> collection/sample aggregation
  rules in the backend match controllers.
- Releases are gated on a smoke test (`tooling/smoke-test.sh`): the demo stack is booted from the freshly built images and must serve the frontend, authenticate the demo login and answer seeded API reads before any image is pushed or tagged `latest`. The script also works against any running deployment.

### Changed

- Documented the test layers in the developer guide and added a repository `CLAUDE.md` runbook for coding agents.
- The matching pipeline is substantially faster: match isotopes are bulk-inserted, aggregation runs once per batch instead of per sample, the row-wise pandas hot paths are vectorized, and candidate peaks are found with binary-search windows instead of dense difference matrices. Behaviour is preserved; the profiled bottleneck (database round-trips and row-wise pandas, ~90% of match time) is what was cut.
- Chemical-formula handling (formula parsing, ion arithmetic, isotope prediction, and the labelled `^N` custom element) is consolidated into `mascope_tools.composition`, and the vendored `mascope_molmass` fork is retired (net -7.2k lines).

### Removed

- The unmaintained instrument-bound Playwright suite. Its batch, dataset and target collection scenarios now live in the hermetic e2e suite; the truly instrument-dependent specs (sample processing, Orbitrap acquisition) were dropped and remain available in git history.
- The vendored `mascope_molmass` fork; its only unique capability (the labelled `^N` custom element) now lives in `mascope_tools.composition`.

### Fixed

- `mascope prod` compose commands (`build`, `up`, ...) now exit with docker
  compose's exit code instead of always reporting success. CI builds release
  images via `mascope prod build` and trusts its exit status, so a swallowed
  build failure previously let jobs continue against stale images.
- Web UI file uploads work again on deployments served from a non-standard
  port (e.g. the demo stack on `:8080`). nginx forwarded `X-Forwarded-Host`
  without the port, the tus upload endpoint built its upload URL from it, and
  every upload chunk was then sent to port 80 and refused. Found by the new
  upload e2e test.
- Frontend linting works again: migrated to the ESLint 9 flat config format (the legacy config had been silently ignored). The revived linter surfaced dormant chart bugs that are now fixed: the batch overview chart's log-scale zoom reset never fired, and two match spectra comparisons were always false.
- The dashboard no longer renders a duplicate `id="app"` element inside the Vue mount point.
- The axios error handlers no longer throw a `TypeError` (masking the real error) when a request fails before a response exists, e.g. a request-setup or network failure; the request/response `config` and body are now guarded before destructuring.

### Security

- API error responses no longer include Python tracebacks, internal filesystem paths, or raw messages of unexpected exceptions (including `AttributeError` and `RuntimeError`, which previously echoed their raw message). Clients receive the user-facing message plus an opaque `error_id`; the full traceback is logged server-side under the same `error_id` for correlation. The same applies to error payloads emitted over Socket.IO notifications.
- Request validation errors no longer echo the raw request body (which can contain credentials) back to the client, and the offending input values are now kept out of the server logs as well (the validation error is logged without its traceback, whose final line rendered the raw input).
- After a batch rematch, an open sample's peak list now refreshes its match/formula annotations. The batch aggregation path emitted only a batch-level event and skipped the per-sample `peak_reload`, so peak annotations went stale until a manual reload.
- The batch overview chart no longer leaks a socket listener on every mount: the match-event handlers are now removed on unmount (they were registered as inline callbacks that `socket.off` could not match by identity).
- Invalid target-compound formulas are now handled safely. Since the replacement parser silently skips characters it does not recognise, a formula that is garbage, a leftover numeric mass, or an unknown custom element previously produced bogus adduct-only ions or an unhandled 500 during isotope prediction; such compounds are now skipped with a warning. The batch match endpoint rejects bare numeric masses like single-compound creation does, and the valid formula `NaN` (sodium nitride) is no longer misclassified as a numeric mass.

## 2026.07.07

### Fixed

- The batch overview no longer times out when loading a large target collection on a large batch (#1584). The batch-level match ion aggregation ranked every match row of every sample in the batch before keeping the best row per ion; it now probes the best in-batch match per requested ion via a new `match_ion (target_ion_id, match_score)` index, so its cost scales with the collection instead of the batch's match volume. Measured on a 5,103-sample batch with a 3,000-ion collection: 13-21 s -> 0.3-1.3 s. Requires a database migration (`alembic upgrade head`).

### Changed

- nginx now serves JSON, JavaScript and CSS responses gzip-compressed (#1585). The batch overview's chart data chunks shrink from ~25 MB to ~1.6 MB on the wire for a 5,000-sample batch; uploads and raw-file downloads are unaffected.

## [v1.1.1] - 2026.07.03

### Fixed

- Runtime now parses release semver tag correctly, enabling to checkout, pull and run a specific release (e.g. `git checkout v1.1.1`, `mascope prod docker pull`, `mascope prod up`)

## [v1.1.0] - 2026.07.03

### Security

- Authentication endpoints (login, first-owner registration, credential change) are now rate-limited per client IP, backed by Redis so limits hold across all workers. This blunts password brute-forcing and credential stuffing.
- New and changed passwords must be at least 12 characters and may not contain the account's email or username. The web UI validates the same policy inline so users get immediate feedback.
- The web session lifetime (auth cookie / JWT) is reduced from 360 days to 7 days, bounding how long a stolen token stays valid.
- The server now warns at startup if the JWT signing secret is shorter than the 32-byte minimum recommended for HS256.
- The backend API is no longer published to a host port. nginx reaches it over the internal Docker network, so the plaintext HTTP API is no longer exposed on a host interface where it could bypass the frontend's TLS termination.
- nginx now sends `Strict-Transport-Security` (HTTPS only), `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`, and no longer sends a wildcard `Access-Control-Allow-Origin` (the frontend and API share an origin).
- The authentication cookie's `SameSite` policy is set explicitly (`lax`) rather than relying on the library default.

### Fixed

- The file-converter service now connects to the backend over the websocket transport only. With a multi-worker backend it previously failed to establish its Socket.IO session (the polling handshake was load-balanced across workers), resulting in intermittent errors.
- Version tags now use a stable 7-character commit hash, so the image tag a deploy derives matches the one CI published. A full clone previously abbreviated the hash to a longer length than CI's shallow clone, so `mascope prod docker pull` failed with `manifest unknown`.
- Production `docker pull`/`up` now deploy `latest` (or a pinned release / semver tag at HEAD) independent of the checked-out branch, instead of a branch-derived tag that is never published. Local `prod build`/`up --build` still tag and display the current branch's version.

## 2026.07.02

### Added

- Version-pinned releases: a deployment can pin `MASCOPE_VERSION` to a release, and the web UI reports the running version.
- Citation metadata (`CITATION.cff`) and a software DOI for archived releases.
- Community health documents: contributing guide, code of conduct, and security policy.
- SDK example notebooks `06-08`, SDK version `2026.7.2`.

### Changed

- Rewrote the hosting documentation into a step-by-step production deploy and update guide.
- Removed the redundant release compose stack; local trials now use the demo stack.
- Updated backend dependencies, including a `python-socketio` security update.

### Fixed

- Corrected the demo quickstart download URL after the `develop` branch was retired.
- Fixed default values in the instrument parameter test.
- Fixed the Ubuntu installation script `tooling/ubuntu.sh` to work on Ubuntu >= 26.
- Run tests on PR to `master`, make workflow permissions read-only explicitly.

## [v1.0.0] - 2026.06.29

- First public release

[Unreleased]: https://github.com/ultra-trace-systems/mascope/compare/v1.3.2...master
[v1.0.0]: https://github.com/ultra-trace-systems/mascope/releases/tag/v1.0.0
[v1.1.0]: https://github.com/ultra-trace-systems/mascope/releases/tag/v1.1.0
[v1.1.1]: https://github.com/ultra-trace-systems/mascope/releases/tag/v1.1.1
[v1.2.0]: https://github.com/ultra-trace-systems/mascope/releases/tag/v1.2.0
[v1.3.0]: https://github.com/ultra-trace-systems/mascope/releases/tag/v1.3.0
[v1.3.1]: https://github.com/ultra-trace-systems/mascope/releases/tag/v1.3.1
[v1.3.2]: https://github.com/ultra-trace-systems/mascope/releases/tag/v1.3.2
