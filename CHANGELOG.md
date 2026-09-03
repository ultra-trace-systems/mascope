# Changelog

Notable changes to Mascope are documented here. Versions follow the date-based scheme `YYYY.MM.DD-<hash>` produced by the release workflow, and releases are pinned with a semantic version tag `vX.Y.Z`.

## [Unreleased]

### Added

- Design note: **batch-primary peak assignment**
  (`docs/dev/peak_assignment_batch_primary.md`). Ingest-time assignment writes
  a per-sample ledger for every processed sample - about 0.84 KB per detected
  peak after the index trim, most of it placeholder rows - and derives the
  batch ledger from it afterwards. Measured on a real batch, the batch ledger's
  anchors are about six percent of its member rows while membership itself is
  dense (half the anchors recur in most samples, and most recurring anchors
  are unassigned), so the note proposes inverting the two: the batch peak
  becomes the object that carries the assignment, its evidence, curation and
  verification; ingest folds a sample in as slim member rows without writing a
  per-sample run; the per-sample run stays as an opt-in deep dive and the
  import target; and the untargeted stage runs once per anchor instead of once
  per sample. Estimated at about a fifth of today's growth per peak, below the
  raw data the samples bring. A design document only; no behaviour changes
  until it is signed off and implemented.

- **Batch peaks: one ledger for a whole batch.** A batch peak is a frozen m/z
  anchor shared across a batch's samples, carrying an evidence-weighted
  consensus of the per-sample assignments that folded into it - so a species can
  be read once for the batch instead of sample by sample. Every observed peak of
  every sample folds into exactly one batch peak, assigned or not, as its
  assignment run completes. The **Batch peaks** ledger and the batch
  **Assignments** overview present them, *Compute batch peaks* backfills a batch
  whose samples were assigned before the feature existed, and
  `GET /api/batch-peaks/batch/{sample_batch_id}` plus
  `POST /api/batch-peaks/batch/{sample_batch_id}/backfill` serve them to
  clients. Two new tables, `batch_peak` and `batch_peak_occurrence` (migration
  `f3b9c7a1e2d4`). Note that the occurrences are the larger of the two by far -
  one row per observed peak per sample - and the assignment retention pass does
  not reclaim them.

- Isotope matches now persist their **signal-to-noise ratio**, and the v2 fit
  score uses it. Real per-peak SNR was already computed on the matching path but
  thrown away before it reached the database, so scores read back from a stored
  match were computed without it. The column is now written (migration
  `355643cd265e`, nullable, no backfill), and the score's detectability gate
  keys on it. **What changes for you:** rows written before the upgrade keep a
  NULL, which is scored honestly as "no SNR for this row" rather than guessed at,
  so a targeted match score can move when a sample is re-processed under this
  release - not at migration time, and not for anything left alone. This is the
  one change here that alters already-released behaviour outside the peak
  assignment flag.
- **An imported assignment run can now record the tier its own engine
  reached**, so a run computed outside Mascope no longer has to be flattened
  into Mascope's reading of it before it can be stored. An imported row's
  `tier` is checked against the run's declared bands and must be the tier its
  evidence earns - which is what lets tiers from two engines sort, filter and
  roll up against one yardstick, and is unchanged. But that also refused a
  *demotion*, and demotions are exactly what a second engine contributes: peaky
  tiers on window uniqueness, isotopologue corroboration and a mass-degeneracy
  audit, none of which is a threshold on evidence. Such a run previously either
  lost its judgement or was refused outright. Rows now carry an optional
  `engine_tier` beside `tier`, exempt from the coherence check, served on the
  ledger and the SDK frame, and filterable both directly and through
  `tier_disagrees` - which excludes rows carrying no engine tier from both
  answers, since silence is not agreement. Nothing ranks on it: the batch
  consensus and `TIER_RANK` stay on `tier`.
  The ledger shows the engine's verdict in its own sortable **engine tier**
  column beside Mascope's, on runs that carry one and nowhere else, so the two
  read side by side rather than one behind a hover, and the peak inspector
  shows both as chips.
  Rows the engine did not tier show a dash, which is most of them - an engine
  typically tiers only the peaks it committed a formula to - and they sort last
  rather than under the 'unassigned' rank they never claimed.
  Curation archives the engine's verdict with the winner it judged and restores
  it on an undo, the same rule the engine's other judgement keys already
  follow, so a hand-edited row never keeps a verdict about a formula it no
  longer holds; a copied run carries none, because the engine that judged the
  source peak never saw the destination's.

- **`mascope-tools` 2026.9.2** exports `formula_plausibility` from
  `mascope_tools.composition`. It is the second factor in the evidence a peak
  assignment is tiered on (`fit x plausibility`), so anything reproducing or
  predicting a tier needs it - including an external engine publishing a run
  into Mascope. It has existed since the graded-plausibility work but only
  inside `composition.heuristic_filter`, which is an uncurated path no outside
  caller had a promise about, and no published release carried it at all. Its
  vectorised sibling `chemical_plausibility` stays unexported: that one is a
  filter stage, shaped to slot into `apply_heuristic_rules` beside four other
  rules that are equally unexported, and bulk scoring is a comprehension over
  the memoized scalar. This is also the first tools release since 2026.6.25.

- **An imported row's `tier` is now optional.** It is a pure function of
  `fit_score`, `assigned_formula` and the run's declared bands - all of which
  the server already holds, and all of which it already computed to check a
  supplied value - so when the field is omitted the server derives it. Sending
  it meant reproducing the deployment's chemical-plausibility function exactly,
  a second implementation of one rule whose drift refused the whole import over
  a number the client had no reason to hold. The invariant now holds by
  construction rather than by refusal. A supplied tier is still accepted and
  still checked. A row that names no `assigned_formula` is 'unassigned'
  whatever fit score rides along, and may not claim otherwise: a fit weighs
  nothing without a formula to weigh it against, which is what the in-app
  ledger has always said.

- A curated sample's assignments can now be **copied to the batch's other
  samples** (*Process → Copy assignments to batch…* in the sample's context
  menu). One sample gets the full treatment - an engine run, inspection,
  curation - and the copy propagates that ledger to every eligible sibling
  without another engine run each: the rows are remapped onto each
  destination's own peaks on a calibration-corrected axis, their evidence
  (fit score, mass error, abundance error) is re-measured against each
  destination's data with the engine's own scorer, and the tier is recomputed
  from that re-measured evidence under the source run's thresholds - so a
  sibling whose data supports a formula less shows that honestly instead of
  inheriting the source's confidence. Each destination gets a new,
  append-only run under the dedicated `mascope-copy` engine (the run
  selector renders it as "copy of" the source), published through the same
  validated import pipeline external engines use, complete with one row per
  destination peak, a copy manifest on the run, per-row `copied_from`
  provenance, and the usual batch fold-in. Blanks, opposite-polarity
  samples, and samples with a run already in flight are skipped and
  reported. Verification verdicts do not copy - a verdict is a judgement
  about one sample's evidence. Design note:
  `docs/dev/peak_assignment_copy.md`.

- **The peak inspector now measures the alternatives it used to only list.**
  Beside a peak's assignment the inspector shows its close alternatives, and
  they came from two places that looked alike and were not. The formulas the
  engine actually competed for the peak carry a fit, a mass error and the
  adduct they were found under. The rest are the composition finder's
  shortlist - every formula whose mass fits the peak, listed before the run
  picked a winner - and they reached the card as a formula and a chemical
  plausibility and nothing else, because measuring them during a run means one
  isotope-envelope match per candidate for every peak of a sample. So they sat
  there with no fit to compare them on and a permanently disabled *use this*:
  visible, unrankable, and impossible to act on. For a single peak the same
  measurement is cheap, so it is now made when somebody is actually looking at
  that peak. Each such formula is seeded against every adduct the sample is
  recorded under, and the one whose main peak lands on this peak with the
  strongest evidence - the fit weighted by chemical plausibility, the same
  measure a tier is read off - is reported with its fit, its mass error and
  its adduct. That is enough to assign it, so *use this* works on it. A
  formula no adduct places on the peak stays blocked and now says which of the
  three reasons it is: the sample has no adducts recorded, the formula makes
  no ion at all, or nothing landed within the mass tolerance. The measurement
  belongs to the session, not to the run: it is never written onto the run's
  rows, and committing one of these formulas is recorded as what it is - a
  composition scored against the sample by hand, re-tiered under the run's own
  thresholds - rather than as promoting something the engine had decided.


- The **Batch peaks** ledger now has an **Intensity** column and folds
  isotopologues under the peak they belong to. The intensity is the
  highest the species reaches in any sample of the batch, in the instrument's
  own unit, and it is sortable - which is how you find the largest thing in the
  batch, including the largest thing nothing was assigned to, since an
  unassigned anchor carries an intensity like any other. Peaks with no
  intensity to report sort last rather than as zero.

  The fold is the *Isotopologues* toggle the per-sample assignment ledger
  already had, and it defaults to folded: an isotopologue peak carries its
  compound's formula, so left in the list it read as a second species and the
  ledger's row count, tier chips and formula filter all counted one compound
  twice. Isotopologues now ride under their main peak, counted in the **+N**
  marker beside the formula, and the toggle unfolds them as indented rows.
  A batch peak is an m/z anchor and carries no compound of its own, so the link
  is derived from the per-sample assignments behind it: a peak is folded only
  when most of the samples that assigned it agree it belongs to another
  anchor's compound. One that is an isotopologue in a single sample and a species
  in its own right in the rest keeps its own row. Ticking rows is unchanged -
  folding an isotopologue away also unticks it, so the chart never draws a trace
  with no ticked row behind it.

  Batches folded before this release get both without being recomputed; the
  values are derived from the per-sample peaks already stored. As with the rest
  of a batch peak's consensus, *Compute batch peaks* is what re-derives them
  after samples are added or removed outside an assignment run.
- Switching samples now keeps you on the **same peak**. A batch peak is one m/z
  anchor shared across a batch, so the peak you are looking at in one sample
  usually has a counterpart in the next one - and moving between samples now
  follows it there, leaving the inspector and the spectrum on the same species
  instead of dropping you back to no selection at all. Comparing one compound
  across a batch previously meant finding it again by m/z in every sample. The
  peak it follows is the one that shares a batch peak, not the nearest m/z, so
  it is the same species by the same definition the batch overview draws its
  traces from. A sample where that species was never observed leaves the
  selection empty exactly as before, and so does a batch whose batch peaks have
  not been computed; nothing is announced either way. An explicit choice still
  wins - clicking a peak, or clearing the selection, is never undone by a
  follow arriving late.
- **You can now assign a peak yourself, and it sticks.** Until now the peak
  inspector's close alternatives were a read-only list and a composition found
  by re-searching a peak could only be added to a target collection - getting
  either into the ledger meant a whole new assignment run, which might well
  award the peak to something else again. Two controls close that: **use this**
  on a close alternative, and a **hand button** on each re-search result that
  puts that composition onto the selected peak. One kind of candidate cannot be
  committed: the compositions the finder lists with no adduct, since a formula
  without the adduct it was seen under is half an assignment and could never
  carry a verdict, which is keyed on the peak, the formula and the adduct
  together. Its *use this* is offered but disabled, with that reason - the
  formula is not unassignable, it just has to be re-searched first, because the
  search finds it under the sample's own adducts and the hand button there
  commits it with one.

  The row is edited in place and marked as assigned by hand, with a hand icon
  beside its tier wherever the row appears. A satellite that the override
  unassigned gets a mark of its own rather than the hand: it is the consequence
  of a decision taken on another peak, not a formula anyone chose. The ledger's
  source filter lists both, so the whole footprint of one override reads off in
  one place. The assignment it replaced becomes the peak's first close
  alternative, so the same button undoes the change. The confidence tier is
  recalculated from the new assignment's own fit under the run's own thresholds
  rather than inherited, and the calibrated P(correct) of the assignment that
  was replaced is *not* carried over - that number was the engine's reading of
  a different formula, and stating it beside a hand-picked one would be a
  probability nothing calibrated. It is kept on the record with the assignment
  it describes. Isotopologue satellites of the replaced formula become
  unassigned: a satellite is the same compound seen through one heavy atom, so
  leaving them would let one family show two compounds. They go only when the
  compound really changes - the formula and the adduct together, so
  re-committing the composition a row already carries leaves its family exactly
  where it stood - and putting the original assignment back restores them along
  with it, which is what makes the undo a whole one rather than half of one.
  The exception is a satellite someone has assigned by hand in the meantime:
  that judgment is the newer one, so the restore reports it and leaves it as it
  stands.

  A hand assignment lives in the run it was made in - **re-assigning the sample
  recomputes the ledger from the data and supersedes it** - and nothing is
  confirmed automatically, because choosing a candidate and vouching for one
  are different acts and a verdict needs the evidence level only you can
  supply. To keep the judgment across re-runs, record a verification: those
  attach to the peak and formula, not to a run. Batch views take their snapshot
  when a sample is folded in, so an override reaches them at the batch's next
  *Compute batch peaks*. Editors only, and only where peak assignment is
  enabled.

- Clicking a point on the **batch assignments chart** now opens the peak behind
  it, not just its sample. Each trace is one batch peak drawn across the batch,
  and the series it is drawn from now carries the sample peak every point was
  measured from - so a click focuses that sample, focuses that peak in its
  ledger and spectrum, and brings the Sample tab forward, the way clicking the
  batch overview lands on the matched ion. Spotting a trend and then asking what
  one of its points actually was previously meant switching to the sample view
  and hunting the m/z by hand. A point on a sample where the batch peak was
  never observed still just focuses the sample.

- The **draw style** setting - Markers / Lines / Both - is now on the batch
  assignments chart too, not only the batch overview. Both charts mount the same
  control, so the two sides of the Targets/Assignments switch no longer offer
  different chart settings depending on which one you are looking at. Line modes
  keep each trace's own color rather than falling back to the default colorway,
  and the marker shape keeps encoding the confidence tier.

- The run selector now says **which engine produced the assignment run** you
  are reading, and at which version. A run computed here is chipped
  *Mascope*; one published into the sample from outside carries that engine's
  own name, so a ledger is never anonymous. This matters because the view
  defaults to the newest completed run whatever produced it - without the chip,
  a published run would be indistinguishable from one this server computed.
  A published run also carries a **calibration** chip: it calibrates on its own
  side rather than passing the m/z verification an in-app run must clear, so it
  has to declare what it calibrated against, and hovering the chip shows that
  declaration. Hovering the engine chip shows the fit-score bands the run tiered
  with, since *assigned* only means the same thing on two runs that used the
  same thresholds. The in-app engine name is reserved, so the chip cannot be
  forged. The same fields (`engine`, `engine_version`, `tier_bands`,
  `calibration`) are on `list_runs()` and `df.attrs["run"]` in the Python SDK,
  which is what lets two engines be compared on one sample: read each run by id
  and join on `sample_peak_id`.

- Design note: **temporal-continuity evidence and anchor-scoped verdicts**
  (`docs/dev/peak_assignment_continuity.md`). Over a time-ordered batch, an
  isotopologue or adduct ratio should hold constant however the source strength
  moves, so the note proposes ratio stability as a computed evidence badge on
  each batch peak - display-only, feeding neither P(correct) nor a tier - and,
  separately, a verdict recorded once per species at the batch-peak anchor that
  overlays the per-sample verdicts without replacing them: a per-sample verdict
  still wins wherever one exists. Anchor verdicts stay out of the calibration
  label pool by living in their own table, so one judgment can never become
  many correlated labels. A design document only; no behaviour changes until it
  is signed off and implemented.

- Design note: **copying peak assignments from a curated sample to the rest of
  its batch** (`docs/dev/peak_assignment_copy.md`). Compares a literal copy
  against a seeded per-sample re-score - both remap the source rows onto each
  destination sample's own peaks and publish through the run-import channel -
  and recommends the re-score: formulas, families and manual curation carry
  over; evidence numbers are re-measured per sample. A design document only;
  no behaviour changes until it is signed off and implemented.

- Matches can now be refreshed for a **whole dataset** in one action:
  right-click a dataset in the *Datasets* pane and choose *Process -> Refresh
  matches*. Its batches are refreshed one after another, newest first, with the
  same rules as refreshing each batch by hand - a batch that is already matched
  is skipped, one that is mid-processing is left alone - and a toast reports
  what was done. Keeping a large dataset current previously meant hunting down
  every flagged batch and clicking each one.
- Help mode now covers the peak assignment feature end to end. Hovering the
  peak inspector, the composition search, the Assignments and Batch peaks
  ledgers, the assignment charts, the Targets/Assignments switch and every
  field of the run-configuration dialog shows a card explaining what the
  element means - the confidence tiers, the four evidence numbers (fit,
  plausibility, confidence, P(correct)), verification and batch peaks are
  authored once as docs snippets shared by the popovers and the user guide,
  which also gains "Verifying assignments" and "Batch peaks" sections, so
  every card's Learn more link has a real landing place. The `v-help`
  directive accepts the same docs-sourced card shape as the component hook,
  so plain elements can reuse those snippets too.

- The notifications tab has a **Clear** button. It empties the log and the
  unread badge with it, so no count is left standing for rows that are gone;
  running processes keep their progress bars, and nothing is deleted on the
  server.
- The SDK's peak dataframe now carries `target_collection_names` beside
  `target_collection_ids`, so grouping or plotting by collection no longer
  means resolving the identifiers yourself. The names are for display -
  they are not unique, and a peak can match several collections - so
  `target_collection_id` remains the key to join on.
- Peak matches now carry the name of the target collection they came from,
  not just its identifier, so grouping or plotting by collection no longer
  means resolving the identifiers yourself. It appears as
  `target_collection_names` beside `target_collection_ids` in the SDK's peak
  dataframe. The names are for display - they are not unique, and a peak can
  match several collections - so `target_collection_id` remains the key to
  join on. The names come from the server, so an SDK pointed at an older
  deployment still sees the column, with no values in it.
- Peak assignment can be restricted to reference data under licences you
  have accepted. Reference records carry a licence from the moment they are
  loaded, but nothing applied it when matching, so a mirror whose terms need
  checking before commercial use was matched against in every run with
  nothing to say so. Setting `reference_licenses` under `[backend]` limits
  matching to the licences listed; leaving it unset keeps matching against
  everything loaded, which is what deployments do today. The licences are
  matched as exact tags, so an allowlist has to name every tag it keeps -
  `mascope reference status` prints the whole tag vocabulary beside the
  effective set and which of the loaded sources that set admits, and every
  run records what it was allowed to match. Annotation is unaffected - a
  licence outside the set is still shown on results, it is just not matched
  against.
- `mascope env sync` takes `--from` and `--to` (both `YYYY-MM-DD`, both
  inclusive) to transfer only part of the filestore. The window selects on
  acquisition date - the date the data was measured, which is what the
  filestore is laid out by - rather than on when a file was last written.
  It filters the files only: the database is never filtered, so combining a
  window with a full database sync leaves rows whose files were not
  transferred, and the command warns when you do.

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
  TOTP seeds. The 2.0.0 CLI generates it when missing before starting the stack,
  so an existing deployment picks it up on its next start - once that CLI is
  installed. **Upgrading:** reinstall the CLI *before* `mascope prod update`,
  right after checking out the release. The older CLI knows nothing about the
  secret, and compose refuses to create the backend without the file after
  stopping the running one. This applies to the unattended updater too: it
  never reinstalls itself, so on a server running `mascope-update.timer`,
  reinstall the CLI as soon as the timer reports 2.0.0 applied - otherwise the
  next reboot starts the 2.0.0 compose file with the old CLI. See
  [maintaining.md](docs/maintaining.md#rolling-out-a-release-across-several-servers).
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

- The assignment ledger now says whose peaks it is showing. Its header is a
  breadcrumb - batch, sample, and the number of peaks assigned - instead of the
  word *Assignments*, and the caret at its head drops the sample and returns you
  to the batch's peak ledger. Leaving a sample previously meant a meta-click on
  its row in the sample browser or clearing the filter chip in the far corner of
  the window, neither of them anywhere near the table being read.

- Formulas in the assignment ledger can be copied. Hovering one reveals the same
  copy button the batch-peak ledger has; it copies the bare formula, without the
  superscript count of folded isotopologue peaks beside it, and clicking it does
  not change which row is selected.

- **Contributor License Agreement.** External contributors are now asked to
  accept the Ultra Trace Systems Individual Contributor License Agreement once,
  on their first pull request, by replying to the CLA assistant's comment; the
  `CLA Assistant` check blocks merging until every commit author has. The
  agreement and the signature register live in
  [ultra-trace-systems/cla](https://github.com/ultra-trace-systems/cla) and are
  shared with Peaky, so one acceptance covers both projects. Contributors keep
  their copyright and Mascope stays Apache-2.0. See
  [CONTRIBUTING.md](CONTRIBUTING.md#contributor-license-agreement).

- Design note: **isolated deployments - reaching and updating a server with no
  internet route** (`docs/dev/isolated_deployment_plan.md`). For sites where
  the Mascope server has no route to the internet and only the instrument PC
  next to it goes online, on the customer's say-so. The note inventories every
  place the deployment reaches out today and what breaks without a route, puts
  an egress allow-list first as the option that costs nothing, and then
  proposes: administrator access through a customer-switched subnet route on
  the instrument PC instead of a remote-desktop hop; a signed release bundle
  (images, manifest, git bundle, CLI wheels) dropped into an inbox that the
  existing unattended updater applies with its usual window and grace rules; a
  redacted support bundle travelling the other way; and the host-level hygiene
  (time, TLS, patching, backups) an isolated server needs. A design document
  only; no behaviour changes until it is signed off and implemented.

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

- **Ingest can fold a sample into the batch ledger without writing a run.** A
  new `[meta]` setting, `peak_assignment_ingest_ledger`, chooses what an
  ingest-time assignment writes: `"sample"` (the default, unchanged) writes a
  per-sample run and folds it; `"batch"` runs the same database-first
  assignment and folds the result straight into the batch peaks, writing no
  per-sample run - the Sample view is then served from the batch ledger. That
  removes the per-sample rows, about half of the per-peak database cost and
  mostly placeholders for peaks nothing assigned, while every sample is still
  assigned as it arrives. An explicit run on such a sample still writes one and
  restores what only a run keeps: per-peak alternatives and error figures, and
  hand curation. The ingest ceiling applies to both modes.

- **A sample the batch ledger knows but no run describes still has a Sample
  view.** Deleting or pruning a sample's assignment runs used to leave its
  Sample tab empty - grey spectrum, no ledger - although its peaks were still
  in the batch ledger. The runs listing now carries one derived run per such
  sample (engine `batch`, listed after the real runs), and the ledger and
  inspector reads answer for it from the batch peaks: formula, ion formula,
  mechanism, tier, fit, P(correct), role and isotopologue family come from the
  member rows and their anchor's candidate registry, and the inspector's close
  alternatives are what the rest of the batch saw at that m/z. Derived rows
  carry no mass or abundance error, isotope label, source or target link -
  those are a run's numbers - and cannot be curated (the server answers 409
  and the inspector withholds the controls), but a verdict can be recorded
  against them. An imported run can no longer claim the `batch` engine name.
  This is the read model the next step, ingest folding a sample without
  writing a run, will serve.

- **The batch ledger stands without the per-sample ledger.** A batch peak's
  consensus used to be recomputed by joining every member back to its
  `peak_assignment` row for the ion formula, the mechanism, the calibrated
  probability, the member's role and its isotopologue link - so a run deleted
  or pruned afterwards left members that contributed nothing to those, and a
  later recompute could blank an anchor's ion formula, mechanism and family
  link for good. Each member now carries those fields itself and names its ion
  formula and mechanism by index into its anchor's new append-only candidate
  registry (`batch_peak.candidates`). One migration adds the columns and
  backfills them from the ledger rows still linked; a member whose row is
  already gone keeps its formula and gets an entry with no ion formula behind
  it, which is what a recompute could recover for it before. Nothing changes
  in the API or the UI. First step of the batch-primary design note
  (`docs/dev/peak_assignment_batch_primary.md`).

- **The assignment ledger stores its JSON leaner, without changing what it
  serves.** Two of the ledger's largest costs were repetition rather than
  information. Every database-sourced row repeated the confidence calibration
  its `p_correct` was read off (instrument, provisional flag, source - 90
  bytes a row) although one curve serves a whole run; it is now recorded once
  per run as `confidence_calibration`, served on the runs endpoint, and folded
  back into each row by the assignment detail read, so the inspector and the
  SDK see the same `provenance.calibrated` / `provenance.calibration` they
  always did. And the untargeted finder's formula-only alternatives - 99 % of
  all stored alternatives, about 70 bytes each for 16 bytes of content - are
  stored as `[formula, plausibility]` and expanded on read, so every API and
  SDK consumer keeps receiving the dict. Together about 13 % off the ledger on
  a measured Stage A+B run; one migration rewrites the rows already stored.

- **The assignment tables cost less disk, and ingest-time assignment can be
  bounded.** Assignment at ingest writes one ledger row and one batch-peak
  occurrence per detected peak - about 1 KB per peak, or 2-8 MB per typical
  sample, growing with everything a deployment acquires. Measured on real
  ledgers, a third to a half of that was index, and the batch-peak table stood
  at eighteen times its compacted size because every fold rewrote every anchor
  it touched. This release trims what it can without changing the data:
  `peak_assignment` loses two redundant indexes (the run-id one duplicated the
  unique constraint, the peak-id one was never used) and indexes its four
  nullable references only where they are set, **12 % off the table**;
  `batch_peak_occurrence` is keyed by (batch peak, sample) instead of a random
  surrogate id nothing read, **27 % off that table**; the batch-peak consensus
  is written only when it changed, a backfill recomputes each anchor once at
  the end instead of once per sample, and the three tables get autovacuum
  thresholds sized for their churn (plus a fill factor on `batch_peak` for
  in-place updates). The retention pass now keeps the newest **2** completed
  runs per sample and engine (was 3) - the current result and the one it
  replaced - and the docs say what it reclaims: re-runs only, never a sample's
  ingest run, which is its only run until someone re-assigns it. Two new
  `[meta]` settings bound the ingest-time cost without switching the feature
  off: `peak_assignment_on_ingest = false` assigns on demand only, for
  high-throughput instruments where ingest ledgers run to tens of gigabytes a
  month; `peak_assignment_ingest_max_peaks` (default 100000, 0 disables) skips
  a single very dense acquisition at ingest, logs it, and leaves the sample
  assignable explicitly. One migration, run before the stack serves; the API
  and UI are unchanged.

- **The "no adduct" explanations in the peak inspector are in plain
  language.** Both said "No adduct:" and then described the machinery -
  a candidate "the finder listed", a formula that "cannot be verified" - using
  *verified* for committing an assignment, which in Mascope means something
  else entirely: a verdict a person records about evidence. The first now says
  the formula is not assignable to this peak and why, or, once the peak's
  alternatives have been measured, gives the specific reason no adduct reached
  it. The second is the one that mattered more: it appears on the assignment a
  hand-made override replaced, where *use this* would normally undo the
  change, and it has to explain that this particular undo cannot be done at
  all because the replaced assignment named no adduct. It said so in a single
  clause and then recommended re-searching, which reads like the undo. It now
  says plainly that the undo is not available here, and that re-searching
  assigns the formula again but as a new assignment - so the isotopologues the
  override cleared stay cleared.

- **The assignments browser header is one row again.** It had grown to four
  controls - run selector, *Isotopologues* toggle, verdict filter and *Assign
  peaks* - in a column the user can drag to half a window, so the last of them
  were simply clipped off the edge of the pane and became unreachable at the
  widths people actually work at. The two that describe the run rather than the
  table, the **run selector** and **Assign peaks**, moved up into the switch bar
  beside *Targets / Assignments*, one row above the ledger they used to sit
  inside; the selector takes whatever width the switch leaves it. The two that
  only change how the table reads, the
  **Isotopologues toggle** and the **verdict filter**, are behind a cog at the
  end of the tier-chip row, so everything that narrows the table is on one row
  and the cog is the same table-controls affordance the sample and ion browsers
  already use. Both keep their setting while the menu is closed. The verdict
  filter is now a chip strip rather than a dropdown, which is what makes the
  menu usable from the keyboard at all: a dropdown inside it swallowed the
  Escape key that closes the menu, in a panel Tab cannot leave either. The bar
  wraps rather than clips if the column is narrowed far enough. Nothing changes
  with peak-centric assignment turned off, and there is still exactly one
  *Assign peaks* button on screen at a time.
- **The batch-peak browser's header now matches the sample one.** The batch
  ledger kept the layout the sample ledger had just moved away from, so
  switching between a batch and one of its samples rearranged the header: the
  *Targets / Assignments* switch sat centred over the batch ledger and to the
  left over the sample one, the *Isotopologues* toggle was on the panel header
  in one and behind the tier row's cog in the other, and *Compute batch peaks*
  was in a corner *Assign peaks* had vacated. The two headers are now the same
  shape. The switch is left-aligned in both. **Compute batch peaks** moved up
  into the switch bar, in the corner and the style *Assign peaks* uses one
  focus level down, so the action that fills the ledger is always in the same
  place. The **Isotopologues toggle** is behind a cog at the end of the
  tier-chip row, keeping its setting while the menu is closed and opening with
  the switch focused, as its counterpart does. A refused or failed compute is
  still reported below the table it is about.
- **An assignment's confidence tier now reflects the chemistry as well as the
  fit.** A tier is meant to say how strong the case for a peak's formula is, but
  it was read off the fit score alone - which measures only how well the
  measured isotope pattern matches the prediction. A formula that matched the
  mass beautifully while describing an unlikely molecule therefore took the
  ledger's strongest word. The engine already weighed both when deciding which
  formula wins a contested peak, so the two could disagree on the same row: a
  formula could win on the combined evidence and then be tiered as though it had
  fit cleanly. The tier now comes off that same combined measure - the fit
  weighted by how chemically plausible the formula is - everywhere a tier is
  decided: both engine stages, a hand-edited row, a row copied to another sample,
  the composition search's preview, and the check an imported ledger is held to.
  The fit score itself is untouched and is still shown as the pure measurement;
  what changed is which number decides the band. The percentage on a tier chip is
  now that combined measure rather than the fit, so the chip's number and its
  label can no longer contradict each other. On the batch-peaks ledger the chip
  shows no percentage at all: a batch peak's tier is a vote across the samples it
  appears in, not a threshold on any one number, and pairing it with one sample's
  figure implied an arithmetic that never existed.

  Practically, most rows do not move. Chemical plausibility is 1.0 for the large
  majority of real formulas, so for them the combined measure is just the fit. On
  the published demo dataset - every sample assigned, 77,911 rows carrying a
  formula - 92.8% score the maximum plausibility, and about 7% of rows change
  tier, in both directions. What moves down is the chemically implausible; what
  moves up is a clean, ordinary formula whose fit sat just under the old line.
  The two thresholds keep their names and move onto the new scale (0.8/0.5 ->
  0.75/0.45), chosen against that demo ledger as the pair that stays closest to
  the split it replaces. They remain directional starting points rather than
  calibrated truth, and per-instrument recalibration is still the open follow-up.

  For anyone publishing a ledger into Mascope from another engine: nothing new
  has to be sent. Declare the run's `tier_bands` on the new scale and keep
  sending each row's `fit_score` and `assigned_formula` as before - plausibility
  is computed from the formula, so the server derives each row's evidence itself
  rather than taking a number on trust.

- **Computing batch peaks** now shows how far along it is. The backfill folds a
  batch one sample at a time but reported only once the whole thing was done, so
  all the button could offer was a spinner - for minutes on a large batch, with
  no way to tell a slow run from a stuck one. It now fills the app's progress bar
  as it walks the batch. The button stops spinning on the same packet as before,
  the one that says the run ended, so a run that failed still releases it. The
  bar counts samples looked at rather than samples folded, so on a batch where
  nothing has been assigned yet it fills and then reports that there was nothing
  to compute - that batch's state, not a contradiction.

- Selecting **every** batch peak no longer locks up the app. The *Batch peaks*
  ledger lists every anchor in the batch, singletons included, so on a large
  batch "select all" is tens of thousands of rows - and everything downstream
  was priced per row: a chart trace and a legend entry each, one series request
  per hundred rows issued strictly one after the next, and, in the shared
  selection plumbing, a log line and a scan of the whole selection per record.
  The tab locked up before the first trace appeared. The ledger now selects at
  most 300 batch peaks at a time, and says so where the gesture was made rather
  than leaving it to be inferred from a chart drawing fewer traces than the
  ledger shows ticked. The list itself is unchanged - every batch peak is still
  there to be selected - and the tier chips and the Formula column's filter are
  how you choose which 300: filter first, then select. The chart's series
  requests now go out together instead of in turn, and changing the selection
  while they are still in flight cancels them, so a plot can no longer be
  assembled from two different selections.

- The strongest assignment tier is now called **assigned** rather than
  *identified*. In mass spectrometry an identification is read as MS2- or
  reference-standard-level evidence, and what the engine actually does is
  assign a molecular formula from accurate mass and an isotope pattern - real
  evidence, but not that. The tier now claims what it can support, and matches
  the vocabulary of the external assignment engine the app interoperates with.
  The other three tiers - *candidate*, *below assignability*, *unassigned* -
  are unchanged.

  Nothing published needs to be re-imported. The API still accepts the old
  spelling wherever a client can send one: as a tier on an imported ledger row,
  as a `tier_bands` key, as the `tier` filter on the per-sample assignment
  ledger and on the batch-peak ledger, and as the run-config key
  `identified_threshold` (now `assigned_threshold`). Each is
  normalised on the way in, so a ledger exported before the rename imports
  unchanged and an SDK client pinned to the old vocabulary keeps working - but
  nothing is ever stored under the old name again. **Responses carry the new
  spelling**: a client that matches on the literal `"identified"` in a tier it
  reads back has to be updated, since the compatibility runs inbound only. A data migration rewrites the
  tier wherever it is already stored: on the per-sample ledger, on batch peaks
  and their per-sample occurrences, and in the two JSON columns whose keys carry
  the name. On any deployment it should rewrite nothing, since none has the
  workflow enabled yet; it is there for databases developers already have, where
  an unrecognised tier would otherwise be quietly counted as *unassigned*.


- **The orange accent is quieter, and the selection wash is finally a wash.**
  The interface palette is swept from the brand safety orange into eleven
  shades, and that sweep used to carry the seed's full colourfulness to every
  shade. Almost none of them could be shown that way on a normal screen, so
  each one was quietly flattened on its way to the display, arriving off its
  intended brightness and off its intended hue - the pale end as a vivid peach
  rather than a tint, the dark end as maroon rather than orange. The sweep now
  asks how much colour the screen can actually hold at each shade and stays
  inside that, at 80% strength. What you see: selected rows in the peak and
  match tables are washed in a soft cream instead of banded in orange, and
  buttons, tabs, focus rings and panel labels settle to a warmer, less
  insistent orange in both themes. Nothing moves in the layout, and the brand
  seed is unchanged - only how it is swept. Text contrast is held or improved
  everywhere; text on a selected row improves markedly. Status colours that
  are deliberately their own - the amber for calibration drift, an unsure
  verdict, or a provisional assignment - do not follow this accent and are
  unchanged, so they now stand out a little more against it.
- **One *Targets* / *Assignments* switch.** The switch above the browser is the
  only one, and it decides both what the browser lists and what the *Batch*
  overview plots, so the two can never sit on different sides. That pairing
  matters: the *Assignments* chart plots exactly the batch peaks selected in the
  ledger, and that ledger is only on screen in *Assignments*, so a chart left on
  the other setting was being driven by a ledger you could not see. The choice
  is remembered across reloads. During development the *Batch* tab carried a
  second switch of its own, unsaved and independent of the first; it never
  reached a release. With peak assignment switched off the switch is hidden and
  everything stays on the targeted view, as before.
- **The interface follows the updated brand guidelines.** Three changes land
  together, all of them appearance only - nothing moves and nothing behaves
  differently.

  *The orange accent is quieter, and the selection wash is finally a wash.* The
  accent is swept from the brand safety orange into eleven shades, and that
  sweep used to carry the seed's full colorfulness to every shade. Almost none
  of them could be shown that way on a normal screen, so each was quietly
  flattened on its way to the display, arriving off its intended brightness and
  off its intended hue - the pale end as a vivid peach rather than a tint, the
  dark end as maroon rather than orange. Every shade now takes the most color
  the screen can actually hold at its brightness, which is how the guidelines
  build their own oranges. Selected rows in the peak and match tables are
  washed in a soft cream instead of banded in orange. On a light theme the
  accent also steps down to the deeper orange the guidelines reserve for
  buttons, links and text on light backgrounds - quieter, and the first version
  of it to carry a white label at full WCAG AA. The dark theme keeps safety
  orange itself, the background it was drawn for.

  *Formulas and m/z values are set in IBM Plex Mono.* The guidelines treat the
  mono face as part of the identity rather than a fallback, and the code labels
  throughout the assignment views had been asking for it all along - they had
  nothing to resolve to, so they rendered in whatever monospace face the system
  happened to offer. It is self-hosted alongside Plex Sans; no font is fetched
  from a third-party CDN.

  *Status colors now have a value per theme.* Success, warning, error and
  information each carry their own color on light and on dark, taken from the
  guidelines. One value used to serve both themes, which left the light theme
  badly short - a warning read at 1.9:1 against the page, far under the 4.5:1
  that text needs. Every status color improves, most of them substantially.
  Orange no longer signals anything: the guidelines keep it as the brand
  accent, and the flags that had borrowed it - poor isotope match, tied
  candidates, provisional assignment - are warnings and now say so.

- **Peak assignment is now on by default.** A composition is assigned to every
  peak of a sample as it is processed (the fast database stage only - the
  untargeted search stays something you launch deliberately), the assignment
  views are present, and the `/api/peak-assignments` write routes accept work
  instead of answering 403. Targeted matching is unchanged: assignment is an
  addition, not a replacement. The target collections, ion tables and batch
  overview behave exactly as before, and the *Match* tab stays where it is -
  with the match-parameter drawer and *Rate Match* it carries, which the Sample
  tab does not. The two views answer different questions and are meant to be
  used together: Match reads one target ion across the batch, the Sample tab
  reads every peak of one sample.
  **What an upgrade changes for you.** An env config written before this
  release names no `peak_assignment`, so it takes the new default; a deployment
  that already set it to `false` is unaffected. Processing a sample now also
  writes an assignment run and one row per detected peak, so it takes a little
  longer and uses more database space - permanently. That ledger is the
  baseline cost of the feature and nothing reclaims it: the nightly retention
  timer a `tooling/ubuntu.sh` host runs bounds *re-assignment* of the same
  sample, keeping the newest runs per sample, so on a deployment that only
  assigns at ingest it has nothing to delete. Batch peaks add a second row per
  observed peak per sample, and the retention pass does not touch those at all.
  Budget disk accordingly - see `docs/maintaining.md`. Samples processed before
  the upgrade are not assigned retroactively.

  To stop it, set `peak_assignment = false` under `[meta]` in the env config:

  ```toml
  [meta]
  peak_assignment = false
  ```

  Then `mascope prod up`. That ends the ingest work, closes the write routes,
  and removes the assignment views - one restart is the whole procedure now, see
  the entry below. (`MASCOPE_PEAK_ASSIGNMENT=0` moves the backend only and is a
  development knob: the web app reads the `[meta]` value, so the assignment
  views would stay on screen over write routes answering 403.)

- **The web app reads the config the server is running on, not the one its image
  was built with.** `[meta]` reached the frontend only as a JSON blob compiled
  into the bundle at image build time. That is the wrong moment. A deployment
  provisioned with `mascope init` keeps its own config layers, which no update
  rewrites, while its images come from the registry built against the
  repository's - so the two could be different revisions of the same file, and
  the UI would offer features the backend answered 403 for. The frontend
  container is now handed the resolved runtime and publishes it to the browser
  at start, so both halves of every `[meta]` flag always agree. Flipping
  `peak_assignment` or raising `tus_max_upload_gb` is a stack restart; it no
  longer needs a frontend rebuild, and nothing needs reconciling by hand after
  the upgrade. A stack that passes no runtime (the demo compose) keeps using the
  baked-in copy exactly as before.

- Target compound formulas are now enforced by the database, not just by the
  API. `validate_compound_formula` already refused a bare numeric mass such as
  `"136.1252"` in place of a composition, but only on the Pydantic request
  models - so the rule held for anything arriving over HTTP and for nothing
  else. A db script, hand-written SQL, or a restored dump from an older server
  could still put one in, and a sweep across deployments found rows that had:
  two databases still held mass-based compounds, one of them with tens of
  thousands of `match_ion` rows depending on them. A
  `CHECK` constraint on `target_compound` now refuses one on every INSERT and
  UPDATE. It is added `NOT VALID`, so a server still holding a legacy row
  upgrades cleanly instead of aborting; clean those rows up and run
  `VALIDATE CONSTRAINT` separately where a hard guarantee is wanted. Note that
  a legacy mass row cannot be edited in place afterwards - changing its formula
  works (that path deletes and re-creates the row), but a partial update that
  leaves the formula alone is refused.

- Explicit **bracket isotope notation** in a target compound formula
  (`[13C]C5H12O6`, `C[13]C5H12O6`) is no longer accepted, on the API and in the
  database. Isotope patterns are always generated from the formula, so pinning
  an isotope on the compound asks for a single monoisotopic species where a
  full pattern gets computed regardless. This spelling was accepted until now,
  so a stored compound that uses it can no longer be saved from the target
  collection editor, even for an unrelated edit - give the unlabelled
  composition instead. **Caret isotopes (`^N` = 15N) are unaffected** and stay
  valid: those name a labelled *reagent*, a genuinely different substance, and
  a natural-abundance pattern is still computed around them.
- Blocking filestore work no longer runs on the API's event loop. Reading a
  spectrum, peak list or timeseries, applying an m/z calibration, aggregating a
  batch and exporting peaks all reach zarr synchronously, and since the zarr 3
  migration those paths take a cross-process lock that another process - a
  sibling API worker, or the file converter - can hold for the length of a whole
  write. On the event loop that stalls every other request the worker is
  serving, not just the one doing the work. Those calls now run in a worker
  thread.
  Two details decided the shape of the fix. The loaders return lazy dask
  objects, so each offload has to span the call *and* the materialization that
  follows it - wrapping only the call moves metadata into the thread and leaves
  every chunk read on the loop. And the two calibration `apply` bodies are
  offloaded whole rather than per write, under a lock covering the whole
  recalibration. Each body rewrites several m/z axes and then records the new
  calibration, and a reader must not catch it half-done - nor a second apply
  slip past the already-applied guard, which on the Orbitrap path would apply
  the cumulative factor twice. Running on the event loop used to give that for
  free, since neither body contains an `await`; moving them to a worker thread
  gives it up, because two callers get two threads and run at the same time.
  Hence the explicit lock, which is also the inter-process one, so it holds
  against a sibling worker and the file converter as well.

- A `delete-sync-dirs` filestore action reclaims the `.sync` directories that
  zarr 2's synchronizer left behind. They are inert after the zarr 3 upgrade -
  nothing reads or writes them - so this is a one-shot cleanup rather than a
  migration, and it removes only `*.sync`, never a store or a live lock.

- Dropped the exact `numcodecs` pin and relaxed `zarr` to a `>=3.3,<4` range,
  closing a long-standing TODO. `numcodecs` is not imported anywhere in Mascope;
  it arrives through `zarr`, which declares `numcodecs>=0.14` itself, so pinning
  it in three places only fought that constraint. It is also what blocked the
  routine 0.15 to 0.16 bump: `numcodecs` 0.16 removed two symbols that `zarr` 2
  imported at module scope, so neither package could move until `zarr` 3 landed.
  The chunking failure the pins were introduced for does not reproduce: TOF files
  from three instruments now convert and load cleanly, so dependency updates to
  either package are ordinary bumps again, judged by the test suites.

- Upgraded `zarr` from 2.18.2 to 3.3.0. Existing sample files are unaffected:
  zarr 3 reads and updates the v2 stores already in the filestore in place, and
  Mascope now pins the default on-disk format to v2 so newly written stores
  match the ones beside them, keeping the filestore in a single format and a
  downgrade to zarr 2 possible.
  zarr 3 removed its synchronizers, which is how concurrent writes to a sample
  file used to be serialized across processes. That protection is now explicit:
  writes take a `fasteners` inter-process lock held for the whole
  read-modify-write, so the backend workers and the file converter still cannot
  interleave writes to the same store. The lock is coarser than the per-chunk
  locking it replaces, and covers a case the old one did not - inside
  `_process_chunk_sync` a chunk's read and its matching write are now in the
  same lock, where previously two processes could interleave between them and
  lose an update. It is taken with a timeout rather than `fasteners`' unbounded
  default, because these writes are entered from async request handlers and a
  holder in another process would otherwise pin a worker's event loop
  indefinitely. Note that zarr 3 still accepts a `synchronizer=` argument and
  ignores it, so this had to be replaced rather than merely removed.
  One further behaviour change is handled explicitly: zarr 3 answers "is this
  variable present", `group_keys()` and `groups()` from a store's consolidated
  metadata when it exists, where zarr 2 always listed the store. Every
  membership test and store listing therefore goes through
  `mascope_file.io.open_zarr_store`, which reads the live store. This matters
  most on the write paths, where the difference is silent rather than loud: a
  recalibration would skip a group that a stale `.zmetadata` omits, leaving one
  store's groups on two different m/z axes, and a partial peak update would
  drop a variable's write entirely. A `peak_timeseries.zarr` whose `sparsity`
  array exists on disk but is missing from a stale `.zmetadata` is likewise
  repaired on the next read exactly as before, rather than being treated as
  absent.
  Two smaller consequences of the swap: the lock is a *file* where zarr 2's
  synchronizer left a `.sync` *directory*, so the glob-and-delete sweeps over a
  sample directory now remove either kind rather than failing on the file; and
  the `.sync` directories written by zarr 2 are left orphaned by the upgrade.
  They are inert - nothing reads or writes them - and any sweep that runs over
  a sample directory clears them, but a long-lived filestore can reclaim the
  inodes early with the new `delete-sync-dirs` filestore action, run inside the
  backend container as
  `python -m mascope_backend.db.admin.filestore delete-sync-dirs`.

- Applying an m/z calibration to a sample file that has no `peak_timeseries.zarr`
  now warns and completes instead of failing the whole calibration. The handlers
  always meant to tolerate that case, but the exception they caught was zarr 2's
  `PathNotFoundError`, a `ValueError`, which never covered the `FileNotFoundError`
  that the loader itself raises for a variable that is simply absent. zarr 3
  raises `GroupNotFoundError`, a subclass of `FileNotFoundError`, so one clause
  now covers both.
- The upload size cap now applies to browser uploads too. Web uploads were
  capped in the browser at 2.5 GB whatever `tus_max_upload_gb` said - below
  even its 5 GB default - so raising the cap only ever helped the SDK and
  the instrument agents. **Upgrading:** the setting moves from `[backend]` to
  `[meta]` in the env config toml. A value left under `[backend]` keeps
  working and is moved automatically with a warning, but move it, because
  only the `[meta]` copy reaches the web app. The frontend reads it at build
  time, so raising the browser cap on a deployment running a released image
  needs a frontend rebuild; a backend restart alone lifts it for the SDK and
  agent paths.

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
  editor binding them, and a collection a mode uses can no longer be narrowed
  into a single workspace afterwards - either way round, one workspace's
  private collection would otherwise govern how every other workspace's
  samples are matched. Only a binding the edit actually *changes* is checked:
  the pane sends the mode's current collections back with every save, so a
  mode already pointing at a workspace-scoped collection stays editable by
  anyone who may edit modes, and can be moved off it again.
- Launching a peak assignment now answers with the outcome instead of an
  unconditional acknowledgement. `POST /api/peak-assignments/sample/{id}/assign`
  returns 202 carrying the id of the run it just created - so a caller can follow
  that one run instead of diffing run lists to guess which is its own - 409 with
  the id of the run already in flight for that sample, or 422 naming why the
  sample cannot usefully be assigned (a blank, or an unverified m/z
  calibration). Previously both refusals were decided *after* the response had
  been sent and reached only a socket notification, which reported them as a
  success. `POST /api/peak-assignments/batch/{id}/assign` answers with the
  eligibility partition it will execute: the samples it will assign and the ones
  it will skip with their reasons, so an all-skipped batch is distinguishable
  from a refusal. It deliberately reports no per-sample run ids, because a batch
  creates each run only as it reaches that sample. The app's launchers now report
  a refusal where it was asked for - the sample pane shows the reason inline, the
  batch launcher as a notification carrying the server's own wording - instead of
  a generic error toast over a dialog that would not close.
- Creating an account no longer asks the administrator to invent a temporary
  password. The server generates one and shows it once, the same way a password
  reset already worked. The new user had to replace it at first sign-in either
  way, so the password's only job was to survive being handed over once - and a
  generated one cannot be weak, nor a password the administrator uses somewhere
  else. The first owner still chooses their own, since nobody is handing that
  account over. Callers of the registration API that supply a password keep
  working unchanged.

- **Recalibrating a sample by hand now clears its acquisition-drift badge.**
  The amber "acquisition drift" badge is deliberately sticky: a recalibration
  runs on the axis the previous fit already corrected, so its own pre-fit error
  is near zero and clearing the marker on that basis would erase a real
  finding. But the badge quotes the pre-fit error of the calibration that
  raised it, and that fit is not always right - a mis-calibration inflates the
  number, and the file then carried a drift warning that described the bad fit
  rather than the instrument, with no way to retract it short of resetting the
  calibration entirely. Applying a calibration from the calibration dialog is
  now read as what it is (an operator stating that the previous one was wrong)
  and drops the carried marker, leaving the badge green. It leaves the badge
  green even when the bad fit had shifted the m/z axis, which is the usual
  shape of a mis-calibration: the recalibration's own pre-calibration error is
  then the displacement that fit left behind, so blaming the instrument for it
  would re-raise the badge on the very calibration meant to retract it. An
  instrument that really is drifting is still flagged whenever the fit runs on
  the axis the file was acquired with - a first calibration, or any
  calibration after the m/z calibration has been reset - and automatic
  pipeline recalibrations carry the marker forward exactly as before. Clearing
  is recorded on the calibration record (`drift_cleared_at`,
  `drift_cleared_by`), and it also retires the once-a-day suppression the
  stale warning had opened, so a genuine drift on that instrument is reported
  again instead of waiting out the window.

### Fixed

- The peak inspector's **close alternatives** no longer include the assignment
  the peak was actually given. The list is meant to be the runners-up a peak
  could have gone to instead, so the committed formula appearing in it - with
  the same ion formula, the same mass error and the same plausibility as the
  card above it - read either as a duplicate assignment or as the engine
  competing a formula against itself. It reached the list two ways, and both are
  now closed at the point the list is built. In the targeted stage the winner
  was excluded by position alone, which is not the same as excluding its
  identity: one formula reaches a peak on more than one row whenever a compound
  is both a curated target and a known reference, or when two targets share a
  formula, so the losing twin became an alternative. In the untargeted stage the
  composition finder's shortlist of other candidates was frozen before the
  heuristic filter and the isotope-pattern ranking had chosen the winner, so it
  named that winner whenever the pattern promoted anything but the
  mass-closest composition - the usual case, not an edge case.

  An entry is now dropped when it reaches the winner's formula through the
  winner's ionization mechanism; the same formula seen through a different
  adduct is a real second sighting and still appears. Screening happens before
  the cap on how many alternatives are kept, so a duplicate can no longer take
  a slot from a genuine rival. The finder's shortlist also stops hiding the
  mass-closest composition, which is exactly the runner-up worth seeing when
  the isotope pattern demoted it. Runs already stored keep the duplicate until
  the sample is assigned again, so the inspector screens the list on display as
  well.

- A verification verdict now covers a compound's whole **isotopologue family**
  rather than one peak of it. An M+1 is not a second thing to judge - it is the
  same compound seen through one heavy atom - but a verdict was looked up by the
  peak it was recorded against, so confirming a compound left every isotopologue
  looking unjudged: focusing one opened an empty confirm / reject / unsure form
  beside its own confirmed parent, and an unfolded family in the assignment
  ledger showed a *Confirmed* badge on the M0 row with blank verdict cells on
  every child row beneath it - even under the ledger's *Confirmed* filter, which
  is what had put the family on screen. Confirming a compound now shows the
  verdict on every member of its family, and verifying while an isotopologue is
  focused records a single verdict against the compound rather than another one
  against that peak, so the confidence calibration is fed one label per family
  instead of several correlated ones. The inspector says which compound a
  verdict captured from an isotopologue applies to - except where there is no
  compound to point at: an isotopologue whose main peak was won by a different
  ion in that run has no family, stands for itself, and is judged on its own
  without any claim to a wider scope. A half-written verdict also survives a
  click inside the isotopologue table now, since moving between peaks of one
  compound no longer counts as starting a different judgment.
- Refitting an instrument's confidence calibration no longer counts a verdict
  somebody changed their mind about **twice**. Verification verdicts are stored
  append-only, and every reader was left to work out which row was the current
  one by taking the latest timestamp within an assignment's identity. The user
  interface did that; the recalibration fit did not, so it trained on the whole
  history - a confirm later switched to a reject contributed a positive *and* a
  negative label, at an identical score. That pair is noise to the curve being
  fitted, it drags the held-out quality metric toward chance, and it counts
  toward the minimum-label thresholds that are supposed to be the guardrail
  against fitting on too little.

  The current verdict is now marked rather than re-derived: recording one stamps
  the verdict it replaces in the same transaction, and a partial unique index
  keeps exactly one live verdict per assignment identity, so a second one cannot
  be written even by a double-fired submit. Replaced verdicts are kept, with the
  score snapshot they were judged against - a retracted judgment is still an
  honest observation about the model at that score, and it stays available for
  audit - they are simply no longer counted as current. A migration marks the
  history in databases that already hold verdicts; on any deployment it should
  mark nothing, since none has the workflow enabled yet.

- One compound can no longer end up drawn as **two batch-peak traces** because
  two samples were folded into the batch at the same time. A batch peak is a
  frozen m/z anchor: an arriving sample's peak joins the nearest existing anchor
  or mints a new one, and an anchor never moves again. Nothing kept two folds of
  the same batch apart, though, and two that each read the anchor set before
  either had finished both minted an anchor for a species they shared. Because
  anchors are permanent, the split never healed - the compound stayed split
  across two traces, each present in only some of the samples it was actually
  found in, both support fractions wrong, and re-folding only snapped each
  sample to whichever half was nearer. Folding now takes a lock on the batch for
  the span in which it reads and extends the anchors, so overlapping folds queue
  instead of interleaving; folds of different batches still run in parallel, and
  the fold's preparatory work, including the instrument-configuration lookup,
  happens before the lock is taken. All three paths that can overlap are
  covered: assigning a whole batch, publishing an imported run, and computing
  batch peaks for a batch after the fact. A sample that will not fold during
  that last one is now logged with its traceback rather than one line of
  exception text, since the count alone is all the caller ever sees.

- The peak inspector now names the peak it is showing when that peak has no
  formula. An unassigned peak read as the word *Unassigned* over an empty
  evidence grid - the same card for every unassigned peak in the sample, saying
  nothing about which one was selected. It carries the peak's m/z and intensity
  now, whether it came from an assignment run or from a sample with no run yet.

- An unassigned peak can no longer be *confirmed*. Every peak in a run gets a
  ledger row, so peaks nothing explained are real rows that simply carry no
  formula - and the inspector offered its confirm / reject / unsure form on them
  all the same. A verdict recorded there was a judgment about nothing, kept
  among the hand labels but carrying no evidence for anything to learn from. The
  form and the verdict badge now appear only where there is an assignment to
  judge, in the inspector and in the ledger's verdict column alike; the ledger's
  verdict filter no longer sorts a formula-less row under a verdict its own
  column does not show.

- Clearer wording on the grey *not calibrated* badge in the sample browser: it
  now reads "No calibration collection defined for the ionization mode, or no
  matching peaks found."

- The **"Supported by N adducts"** badge now appears on isotopologue rows too.
  When a compound is seen through several adducts, that co-occurrence is
  independent evidence for the formula, and the badge says so - but it was
  showing only on the main isotopologue. Selecting an M+1 or M+2 peak, or
  unfolding the isotopologues in the ledger, dropped the badge entirely, which read
  as *this peak has no corroboration* when what was true is that corroboration
  is not a property of a single isotopologue at all. The engine records it on
  the main isotopologue by design: a child is the same ion measured at
  another isotope, not a second sighting of the compound. The evidence belongs
  to the formula the whole family shares, so the isotopologues now show their
  family's count, and say on their face that it is borrowed - "via M0" in the
  inspector, a parenthesised count in the ledger. The tooltip is careful about
  one thing in particular: the confidence boost from that corroboration is in
  the main isotopologue's P(correct), *not* in the child's, which stays
  calibrated on its own evidence - so a child never claims the probability
  shown next to it already accounts for the other adducts. Rows whose family was
  never corroborated are unchanged, and a family corroborated by a single adduct
  still shows nothing. In the peak inspector the badge now also appears
  immediately, instead of a moment after the peak's full record arrives.

- Help cards no longer run off the edge of the screen. The popover asked
  Floating UI only to offset itself from the element it points at, with nothing
  to say what should happen when the result did not fit, and its body had a
  maximum width but no maximum height - so a long card opened near an edge was
  simply cut off, with no way to reach the rest of it. The two worst were the
  *Assignment Runs* card, by some way the longest of the help snippets and
  pinned below its selector, and the peak inspector's card, which points up out
  of a panel that already starts near the top of the window. A card that does
  not fit now moves: to the other side of what it points at, or, when its target
  is a whole pane and neither side has the room, beside it instead. It slides
  along that edge to stay clear of the frame, and if it still does not fit it is
  capped at the room actually available and scrolls inside itself. Its arrow
  and the invisible bridge that keeps it reachable for the *Learn more* link
  both follow the side the card ended up on rather than the side it asked for,
  so neither is left pointing at nothing after a flip.
- Unfolded isotopologue rows in the assignment ledger no longer scatter when you
  sort by a column. Each isotopologue stays directly under the parent whose formula
  names it, in every column and in both directions, because the ledger now
  orders its own rows: it sorts the parents and re-attaches each family
  underneath. Sorting by intensity was the worst of it - an isotopologue is a few
  percent of its main peak, so every child sank hundreds of rows below its
  parent and arrived as a bare indented arrow with nothing to say what it was an
  isotopologue of. A third click on a sorted header now clears the column and
  returns the ledger to its confidence order, which previously took a reload.

- Clicking the selected row in the assignment ledger deselects it again. The
  table said so all along, but the pane discarded the message, so the peak
  stayed focused with no row highlighted and the spectrum, inspector and
  timeseries stayed on it.

- The assignment ledger no longer offers two *Assign peaks* buttons at once. With
  no runs yet the toolbar collapsed to a lone copy of the button sitting just
  above the identical one in the empty state; the toolbar copy now appears only
  once there is a run to sit beside.
- Calibrating a sample against an **empty calibration collection** now reports
  a warning instead of quietly calibrating against the whole database. An
  ionization mode only had to *name* a collection for m/z fitting to proceed;
  with no compounds in it, the empty compound list was dropped from the isotope
  query as falsy, so the fit ran against every target isotope on the server -
  other collections and other workspaces included - and any of them that landed
  in the refine window could anchor a fit that was then applied to the sample.
  Fitting now stops before the query with "Calibration collection is empty",
  the same way it stops for a sample file with no peaks: nothing is written,
  the dialog shows the warning with *Save* disabled, and an automatic run
  records its usual failure marker. A collection whose compounds have no
  isotope for the mode's mechanisms at the instrument's resolution is reported
  the same way, where it previously returned a 500. The isotope query's
  compound filter now distinguishes "no compound filter" from "a filter no
  compound satisfies", so an empty compound list can no longer widen the query
  for any caller.
- The **Batch peaks** ledger no longer offers an action that cannot work, and
  no longer reports one as finished before it is. *Compute batch peaks* is now
  disabled with the reason in its tooltip when there is no batch in view, when
  the batch has no samples, or when you are not an editor of its workspace -
  the last of which used to be a button that looked ready and answered 403. It
  also stays in its loading state until the background task actually reports
  back, rather than stopping the moment the server acknowledged the request:
  the spinner used to stop while the work was still running, which invited a
  second click. A launch that is refused or fails puts the server's own reason
  in the pane and gives the button back. And a run that folds nothing - every
  sample in the batch still unassigned - is now announced as a warning saying
  so, instead of a green *Computed batch peaks from 0 assigned sample(s)* that
  reads as done. A run whose folds actually failed says that instead, rather
  than sending you off to assign a batch that already is; one that folded some
  and dropped others now says how many it dropped, which the old message hid
  along with the samples missing from the ledger.

- The **Batch peaks** ledger gains the tier strip the sample ledger has: one
  chip per confidence tier with its count, click to filter the table to it.
  The counts were already being computed and shown nowhere. The chips and the
  tier column's own filter menu are the same filter, so the two cannot
  disagree about what the table is showing.

- Sorting the **Batch peaks** table by tier now orders by confidence rather
  than alphabetically. A plain string sort puts *below_assignability* ahead of
  *candidate*, which is the opposite of what the column is for; it now
  reads assigned, candidate, below, unassigned, with equal tiers ordered by
  the fit percentage shown in the chip. The tier order, ranks and chip labels
  are defined once and shared with the sample ledger, so the two ledgers can
  no longer rank the same four tiers differently.
- The match browser's tables **fit their pane again**. Targets, Assignments and
  Batch peaks were all sized from the browser window, by a formula whose
  constants predated the Targets/Assignments switch bar that now sits above
  them - so every table ran roughly a bar's height too long, and the last row
  and the horizontal scrollbar were cut off. Nothing revealed the loss: the
  panel body has no scrollbar of its own, so the overflow was simply clipped.
  The tables now take their height from the pane itself, which also means the
  switch bar, the confidence-tier strip and an assignment-launch error each
  shorten the table by exactly their own height instead of by a guess, and
  resizing the splitter re-fits the table at any position. The Assignments
  ledger in particular had a fixed 60-pixel allowance that had to cover two of
  those and never covered the error banner at all.

- Several dev instances on one hostname no longer sign each other out. Cookies
  are not scoped by port, so every stack served from `localhost` - each
  worktree's `mascope dev run --instance`, and a local demo stack beside them -
  wrote and read one `mascope_auth` cookie, while each signs its sessions with
  its own secret. Logging into one therefore invalidated the session in the
  others: the app reported a successful login and the next request answered
  401. In dev the session cookie and the half-finished-login cookie now carry
  the runtime env in their names (`mascope_auth_wt-my-feature`), so each
  instance keeps its own session. Production is unchanged - the name stays
  exactly `mascope_auth`, so upgrading signs nobody out - and a dev browser
  holding the old cookie just logs in once more. The resolved name is logged at
  startup, and `MASCOPE_COOKIE_SCOPED` forces the suffix on or off for a host
  whose recorded runtime mode does not match how it is actually being used.
  Because a scoped cookie is one per env rather than one per host, and nothing
  removes a dead env's, the dev cookie now expires after a day rather than the
  week a production session keeps.

- The peak inspector's *abu.* column now shows the correct theoretical
  relative abundance for untargeted isotopologues. The column recovers
  the prediction from the stored abundance error, which untargeted assignment
  saved without its sign - so an isotopologue observed *below* its prediction was
  rendered as if it had been observed above it (a peak predicted at 10% of M0
  but observed at 5% displayed as 3.3% instead of 10%). Untargeted assignments
  now store the same signed error as targeted ones (positive = observed above
  prediction), which also puts the theoretical envelope markers in the
  spectrum chart at the right height. Stored assignments are corrected the
  next time peak assignment runs; scores and tiers are unaffected, since
  everything that scores the error already used its magnitude.

- Untargeted peak assignments now record which side of its prediction a peak
  was measured on. Their m/z error was stored as a distance rather than a
  signed error, so the peak inspector showed every untargeted assignment as if
  its peak were heavy, and the spectrum chart - which recovers the theoretical
  m/z from the stored error - drew the theoretical marker mirrored onto the
  wrong side of the measured peak. The untargeted composition and isotope m/z
  errors are now signed the way the targeted stage's already were (positive =
  measured above prediction) and are taken relative to the prediction, which
  makes that recovery exact. Candidate ranking, deduplication and scoring all
  work on the magnitude, so which composition wins a peak, and its score, are
  unchanged. Stored assignments are corrected the next time peak assignment
  runs.

- Two backend test suites can now run at once on one machine without
  sabotaging each other. The ephemeral test databases were named
  `mascope_test_<category>` with nothing identifying the checkout, and each
  suite starts by dropping the database it is about to create - so a second
  run deleted the first one's schema mid-test, and the failures that surfaced
  were unrelated-looking asyncpg errors rather than anything pointing at the
  cause. The names now carry an env segment: a readable label - `MASCOPE_ENV`
  if exported, else the checkout's own directory name - plus a digest of the
  checkout's absolute path, which is what actually isolates, since neither the
  label nor an exported variable identifies a checkout on its own. This matches
  how `mascope dev run --instance` already namespaces databases, filestores and
  ports. `MASCOPE_TEST_ENV` overrides the segment outright for two runs that
  should share one namespace, or two in the same checkout that should not.
  Session teardown drops only the databases that run created. Two upload tests
  planted fixed-name scratch files in the tus spool, which lives under the
  shared `MASCOPE_PATH` rather than the checkout, and so deleted each other's
  fixtures across concurrent runs; they now name those files per run.

- A raw file whose own m/z spacing is coarser than the peak fitter's window no
  longer fails to process. The instrument-function fit derives a minimum peak
  separation in samples from `dmz / median(diff(mz))`; on a coarse spectrum
  that ratio falls below 1, `int()` floors it to 0, and `find_peaks` rejects
  it, so an otherwise perfectly readable file was quarantined. The separation
  is now clamped to at least one sample, and peaks whose window holds too few
  samples to constrain a fit are dismissed the way the other quality filters
  dismiss theirs - if that leaves too few peaks, the file is treated as a blank
  measurement, which is a defined outcome rather than a hard failure.

- Refreshing matches now repairs samples whose peak data was built by an older
  version of Mascope, instead of failing on them every time. A file's peak
  data is stored against the scans of the acquisition, and which scans a file
  yields is decided each time it is read - so a file recorded before Mascope
  began discarding an abnormal first scan holds one scan more than it now
  reads back, and every sample like that failed to recompute. Such a file's
  stored peak totals were measured over the discarded scan too, so the only
  thing that puts it right is re-running peak detection: a refresh that meets
  one now asks for that itself, once per file however many of its samples are
  affected, and those samples rematch on their own when it finishes. The
  refresh says which files it queued, and reports the samples as failed for
  that run rather than pretending otherwise.

- Exporting a sample item's peak data now says what is wrong with those same
  samples instead of failing obscurely. Every column of the export comes from
  the stored peak data except the total ion current, which is read from the
  sample file, so on such a sample the two disagreed and the export stopped
  with "All arrays must be of the same length" without producing a file. It
  now names the outdated peak data and says that re-running peak detection
  rebuilds it. The export is refused rather than filled in, because the stored
  totals every intensity in it is derived from were measured over the
  discarded scan as well.

- A batch refresh that failed is no longer announced as a success. Refreshing
  a batch isolates a failing sample so one bad file cannot stop the rest, and
  the outcome it reported was ignored when the notification was raised - so a
  refresh whose own message read "rematch failed" arrived as a green success.
  Such a run is now shown as a warning or an error, and it names the reasons
  behind the failures instead of only counting them.

- A tab that loses its connection now says so once, instead of once per
  retry. Socket.IO retries a lost connection indefinitely, and each attempt
  filed its own "Trying to reconnect..." notification - roughly one every
  five seconds, which filled the log's 250-entry limit in about twenty
  minutes and pushed out every real notification. The retries are still
  visible in the browser console.

- Batch sorting in the sample browser now sticks. The sort order was saved
  only while it differed from the default, and switching back to the default
  left the previously saved order in place - so the next visit restored the
  order you had just changed away from. It looked as though sorting only
  persisted after typing something into the batch search box, because a
  search term was what kept the saved state from matching the default.
- Duplicate dataset names are now refused by the database, not only by the
  application, so two requests racing each other can no longer both slip a name
  through. **Upgrading:** the migration that adds the constraint renames any
  duplicates a deployment already holds - within a workspace, the oldest keeps
  its name and the others gain a numbered suffix, and every rename is printed
  during the upgrade. Names are compared ignoring case and surrounding spaces,
  so `Winter run`, `winter run` and `Winter run ` count as one name; datasets
  in different workspaces are unaffected, as are the automatically created
  acquisition datasets. The rename cannot be undone by downgrading, which only
  removes the constraint.

- Two datasets in the same workspace can no longer be given the same name.
  Creating, renaming or moving a dataset into a name the workspace already
  uses is refused with a message saying so, rather than producing a list with
  indistinguishable entries. Names are compared ignoring case and surrounding
  spaces. The edit dialog also used to close as though a rename had succeeded
  when the server had refused it; it now stays open so the name can be
  corrected.
- Registering an account no longer adds it to every instrument workspace on
  the deployment. Guests and editors were enrolled in all of them at their
  matching role, so a guest created today could reach every instrument while
  a guest created before those workspaces existed could reach none. Only
  admins and owners are enrolled now, which is the rule creating an
  instrument workspace already followed and the one the authorization
  documentation already described. Guests and editors are invited to the
  instruments they work on, as they were always meant to be. **Existing
  memberships are left alone**, so anyone already enrolled keeps their
  access; the change only affects accounts registered from now on.

- The workspace members dialog now keeps up with memberships it did not make
  itself. It loaded the roster when it opened and then kept showing that, so
  a member added or re-roled elsewhere - by another administrator, or by
  registering an account, which enrols the new account in the system
  workspaces its role qualifies it for - only turned up once the dialog was
  reopened. It now reloads when the member controller announces a change to
  the workspace it is showing, and ignores announcements about any other
  workspace. Registration went around that controller with a direct database
  write, so it announced nothing and validated nothing; it now adds each
  membership the way the members endpoint does, which checks that the
  workspace and the account exist, treats a membership that is already there
  as done rather than failing on the unique index the direct insert would
  have hit, and puts the granted role through the same role-ceiling check.
- Resumable uploads are refused when the disk is nearly full. The per-upload
  size cap bounds one transfer but not several at once, so enough legitimate
  uploads could still fill the disk. A new upload that would leave less than
  `tus_min_free_disk_gb` free (10 GB by default, `0` disables it) is now
  refused before any bytes move, and clients retry it, so a squeeze that
  clears on its own costs only a delay. Upload fragments abandoned by clients
  are also cleared after 24 hours without progress; previously they survived
  until the next restart.
- A sample whose spectrum will not m/z calibrate now reports once, at the
  end, instead of filing up to fourteen identical warnings. Every level of
  the retry reported the same failure separately, which flooded the
  notification log and the sidebar badge without any of the copies saying
  how many attempts had been spent or what had been skipped as a result.
  The single report names the sample, the attempts, the tolerance it gave up
  at, the reason, and that matching and peak assignment were skipped for it.
  A failure that previously produced no toast at all now produces one per
  affected sample, because the pipeline's own notification still reports
  success for a file whose samples all failed to calibrate.

- Calibrating a set of samples now says which ones failed. The warning at the
  end of the run only counted them - "Failed to calibrate 3 sample(s)." - and
  left the names in a payload nothing reads, so it told the user how many
  samples to go looking for but not which. It now names each failed sample
  with its reason, and once there are more than ten it names the first ten and
  reports the rest as a count.

- Rematching a set of batches now says which ones it could not rematch, and
  why. The summary counted them - "2 failed" - discarded each batch's reason
  where it was caught, and left the ids in a payload nothing renders, so a
  user was told how much had gone wrong and nothing about what to go and look
  at. Each failed batch is now named with its reason, batches skipped because
  they were already being processed are named together with the one remedy
  that applies to them, and a long selection is cut off after ten names so one
  notification stays one notification. The names travel with the error raised
  when nothing rematched at all, not only with the partial-success warning.

- A notification that names things one per line now reads as lines in the
  notification log. The log rendered the whole message as a single paragraph,
  so the summaries above - and the one a file re-processing run writes - came
  out as a run-on with nothing separating one entry from the next, the reasons
  carrying no trailing punctuation to stand in for the break. Toasts already
  showed these correctly.
- `mascope env sync` now leaves files the application can actually use.
  Ownership is not carried across the transfer, so files land owned by
  whoever runs the receiving end; when that differs from the uid the stack
  runs as, Mascope could not read or write what had just been synced. The
  sync now detects the mismatch and prints the exact `chown` to fix it, and
  `--chown` attempts it directly where passwordless sudo is available.
  File permissions are also set explicitly rather than following the
  receiving account's umask - note that this applies to a re-sync as well, so
  permissions tightened by hand on an existing target are reset.
- A TOF file whose spectrum holds no detectable peaks is now ingested as a
  blank measurement instead of failing. Deciding whether a measurement is
  blank compares its largest peak against the spread of the others, and a
  spectrum with no peaks left nothing to compare: the comparison raised, so
  the file was reported as a fault in Mascope and never got its peak
  timeseries, while larger files from the same instrument went through
  normally. A spectrum with no peaks is what a blank measurement is, and it
  is now classified as one. The same comparison was written a second time
  where instrument functions are fitted, and failed there on exactly these
  spectra; both readings now come from one shared measurement, and asking
  for a fit on such a file reports too few quality peaks rather than a
  numerical error.
- Processing a single raw file by hand no longer depends on its filename
  carrying a recognized ionization mode token. "Process selected" asked the
  server to resolve the mode from the filename, and a filename with no
  configured token in it - a file named by an instrument nobody has set tokens
  up for, an unusual acquisition, a renamed file - answered with an error
  toast and left the Ionization Mode dropdown empty, with no way to fill it
  and Save still enabled: the sample was created with no mode at all and
  matched against nothing. The dropdown now always lists every mode configured
  for the sample's polarity and preselects the one whose token the filename
  carries, so recognized filenames behave exactly as before and unrecognized
  ones are a choice the user makes rather than a dead end. Save is blocked
  until a mode is on the field, and the field says why it is empty - no
  polarity picked yet, no mode configured for that polarity, or no token in
  the filename.
  Two narrower cases are fixed with it: a file holding both polarities used to
  preselect whichever matching mode came back first, which for half of them
  was the mode of the polarity the user had not chosen, and it now resolves
  against the chosen polarity and re-resolves when that choice changes; and
  two overlapping tokens matching one filename used to refuse the file
  outright, and now leave the choice to the user, saying which case it is
  rather than blaming the filename for carrying no token.
- `mascope test run` now runs the suite it is standing in, and reports whether
  it passed. It ran pytest from `MASCOPE_PATH` - the shared runtime home,
  which usually points at a different checkout - so from a git worktree it
  collected the *other* tree's tests while importing this one's packages, and
  aborted on an import mismatch. It also asked for doctests across the
  libraries' test directories, where the per-library `conftest.py` files
  collide under pytest's default import mode; that had been failing the whole
  library run for as long as it had been there. Doctests are now a separate
  pass naming the modules that carry one, so nothing collides and the pass no
  longer imports modules that need the runtime secrets to load. And a failing
  suite now fails the command - backend, libraries or frontend - which
  previously reported success whatever pytest answered.
- The SDK's `matching.match_compound()` works again. The endpoint behind it
  builds a throwaway target compound to match against and rolls everything
  back afterwards, but the throwaway compound row itself was never written,
  so the flush of its ions violated the foreign key on
  `target_ion.target_compound_id` - every call has returned HTTP 500 for as
  long as the endpoint has existed, in every release ever cut. The app was
  unaffected (it uses the ion and multi-compound variants), which is why the
  break went unnoticed; the SDK's live contract tests now cover this surface,
  so it cannot break silently again.

- A service token validated once is now reused for a few seconds, so a
  resumable upload's chunks no longer re-check it once per chunk. This is the
  remaining half of the bulk-upload stall; the other half - the check holding
  one database connection instead of three - shipped in 1.7.3. Reusing a
  validation means revoking a credential (unpairing a machine, clearing an
  account's tokens) takes effect within those few seconds rather than
  instantly, and a role change that a service connection reads inside that
  window is kept for the life of that connection rather than the few seconds.
  Deliberately not on the release line: 1.7.3 took the connection fix without
  it, because the entry that earns the reuse there defers no revocation and
  does not exist on that line.

- Pressing Escape no longer throws away two-factor recovery codes. The last
  step of setup deliberately offers no close button, because the codes are
  shown once and two-factor is already switched on by the time they appear -
  but Escape closed the dialog anyway and cleared them. It is now ignored
  until the codes have been acknowledged. An account whose two-factor is
  required by server policy could not turn the factor off to enrol again, so
  the only way back was an administrator reset.

- `mascope logs query` now searches the whole retention window. Rotated days
  are kept as zip archives, which the query silently ignored - however wide
  the requested time range, only the days not yet compressed were read, in
  practice the current day. Archives are now unpacked and included, an
  unreadable archive is skipped with a warning instead of failing the query,
  and a half-written log line no longer aborts the read. Time intervals are
  validated too: `--interval 60d` used to be cast to 60 *seconds*, quietly
  collapsing the window - shorthand like `60d` or `12h` now means what it
  says, spelled-out forms like `60 days` keep working, and anything else is
  rejected with a clear error rather than narrowing the query. The same
  validation guards `mascope logs gc --retain`, which now also collects the
  archives - it swept only the uncompressed files, so it reported a retention
  window it was not enforcing over most of the directory.

- `mascope prod db script list` and `run` now work on a server where only the
  operator CLI is installed (`uv tool install mascope-cli`, the documented
  no-source-checkout path). The runner looked for the maintenance scripts in
  the host's own Python, which only a monorepo install has, so on every other
  server the documented recipes - `require_password_change`,
  `prune_peak_assignment_runs` and the rest - crashed with
  `ModuleNotFoundError`, and the nightly assignment-run retention timer, which
  runs the same command, failed on every firing. The scripts are now
  discovered inside the backend container, where they run anyway, so the list
  always matches the deployed release; the host's copy is consulted only when
  the container cannot be asked. A stopped stack is reported as such, before
  any pre-script backup is taken.

- The file converter keeps converting uploads after a processor thread dies.
  The thread's own recovery handler could raise and take the thread down for
  good, and nothing above it noticed: its queue filled with files nobody would
  convert, and the service went on accepting uploads it would never process
  until someone restarted it. Recovery is now best-effort, and the service
  watches its worker threads - a dead one is reported and replaced, and the
  file it was converting is handed back to the queue, since the folder watcher
  only offers a file once. A slot that keeps dying is retired, but it keeps
  saying so rather than going quiet. The watchers are watched too: they feed
  every upload, so losing one is the same silent stall from the other end,
  and it is reported rather than replaced because a fresh watcher would
  re-offer files that are already being converted. (#1350)

- A day's acquisitions no longer split across duplicate batches. Files
  converted in one sweep share a day and an ionization mode, so they belong in
  one daily batch - but each upload is handled independently, and several
  arriving together could each find no batch yet and each create one. The
  day's samples were then divided between the copies, each carrying its own
  calibration and matching, and the batch list showed the same day twice.
  The daily batch is now unique in the database, so whichever upload gets
  there first creates it and the rest join it, however many arrive at once and
  whichever server process handles them. Existing duplicates are merged on
  upgrade: the oldest batch of each day keeps its samples and receives the
  others', and it is marked for re-matching so its results cover the whole day
  again.
- A file acquired just after midnight on New Year no longer lands in the
  previous year's acquisition dataset under a batch named for the new year.
  The dataset was picked by UTC year while the batch was named for the
  instrument's local date, so on an instrument ahead of UTC one local day
  owned batches in two different years. Both now follow the instrument's own
  clock.
- Two ionization modes that share a display name no longer share a daily
  batch. Only a mode's token has to be unique, so identically named positive
  and negative variants produced one batch name for both, and the second
  polarity's samples were filed under the first one's batch - inheriting its
  polarity and its calibration and diagnostic collections. Each polarity now
  gets its own batch.

- A browser pane whose data fails to load now says so and offers **Try again**,
  instead of going blank or spinning. The shared data-store loader records the
  failure rather than swallowing it, and the sample, batch, dataset, peak, match
  and assignment panes render it in place of their list. The rows and the
  selection are kept across the failure, so a transient 500 no longer costs the
  user their place in the sample they were reading; the pane shows the error
  rather than the stale list, so nothing out of date is presented as current.
  Overlapping loads - a dependency change, a socket reload and the retry button
  can all be in flight at once - now settle by which was started last rather
  than which finished first, so a slow failure can no longer raise an error
  banner over data that has already loaded.

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

- Restarting the server no longer abandons the files it was processing. A
  container stop gave the worker Docker's default ten seconds before killing
  it outright, which is not enough for automatic processing of a freshly
  uploaded file - and a killed worker writes nothing, so the file was left
  with its sample committed, no matched peaks, and no record anywhere of why.
  The stop now allows ninety seconds, and shutdown spends up to sixty of them
  waiting for the pipelines still running. Anything that overruns is stopped
  deliberately rather than abandoned, so it still names the file it was
  working on; a pipeline merely waiting out a retry delay gives up as soon as
  the shutdown begins instead of holding the whole budget doing nothing.
- Re-processing a batch of sample files no longer risks leaving files with no
  samples at all. Every targeted file's samples were deleted up front, before
  the loop that rebuilds them, so a re-processing run that was interrupted -
  by a restart, or by an unhandled failure - left every file it had not
  reached yet empty, which is worse than the state re-processing is meant to
  repair. Each file is now reset, cleared and rebuilt in one step, so an
  interrupted run leaves the files it never reached untouched.
- Re-triggering processing on a file no longer duplicates its samples. A file
  whose earlier processing was cut short keeps the samples that run had
  already created, and processing it again added a second set rather than
  replacing them - so the file ended up with a duplicate sample per
  ionization mode. Existing automatic samples are now cleared before every
  processing attempt, the first included.
- Interrupted processing no longer floods error monitoring. Each affected file
  reported itself as a separate error, and monitoring groups events by
  message, so a restart during a bulk upload could mint an issue per file. A
  shutdown now reports one error naming how many pipelines it stopped, with
  the individual files listed in the server log.
- A raw file that recorded too few scans to measure no longer fails
  ingestion as an unexpected error. An aborted acquisition, or one that wrote
  a file before its first scan, has nothing to convert, so the file still
  fails and moves to `failed_files` - but the Orbitrap path surfaced the
  reader's scan-selection error and the TOF path an index error off an empty
  array, both reaching error monitoring with a traceback as though Mascope had
  broken. Both now report an empty acquisition, which the converter treats
  like a duplicate upload: the user gets a plain explanation in place of the
  raw `IndexError` or `NoScansFoundError` text, and the failure is logged
  below the level error monitoring subscribes to. Seen across several customer
  deployments since 1.7.0.
- A TOF acquisition that was aborted mid-run now ingests with the length it
  actually has. `TimingData/BufTimes` is pre-allocated, so an aborted run ends
  in unwritten rows; the sample length was measured from the last row alone,
  which is what raised the index error above when that row held nothing. It is
  now measured from every scan recorded before the abort, and each of the h5
  readers trims the unwritten tail the same way, so they agree on which scans
  the file holds.
- A TOF file that recorded no usable time axis is refused instead of being
  ingested with a NaN interval and length. Exactly one scan gives no
  inter-scan spacing to average, and an unwritten slot between two recorded
  scans leaves a hole in the axis; either way the resulting NaN was stored and
  only surfaced later, when serializing the sample to JSON failed.
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

## [1.7.3] - 2026.09.01

Hotfix cut from `master`, so its two fixes are listed here rather than
under Unreleased. The connection half of the bulk-upload stall shipped with
them; the validation reuse above it did not.

### Fixed

- A bulk upload run no longer stops a worker answering. Every request from
  an agent, the file converter or the SDK is checked against its access
  token, and that check was opening four database connections and holding
  three - so enough uploads at once used up the connections the rest of the
  server needed, and unrelated requests waited on a pool with nothing left
  to give until they timed out. On one production server that meant a worker
  serving nothing for a minute. The check now holds one connection and reads
  the token row once. This makes the exhaustion far less likely rather than
  impossible; a heavy enough run can still reach the limit.

- **An uploaded file is now announced to the file converter before it is
  published**, closing a race that quarantined finished uploads. Both upload
  paths staged the bytes under a name the converter's watcher cannot match,
  renamed them into place, and only then emitted the socket event carrying the
  uploader's identity. The watcher queues a file once its size is stable across
  two ~1 s polls, and that event is not local - it crosses Redis pub/sub to
  whichever worker holds the converter's socket - so under load it could arrive
  after the converter had already picked the file up. The converter then found
  no context for it, raised, and moved it into `filestreams/failed_files`,
  which nothing retries: the watcher globs `filestreams/*.raw` without
  recursing, so a file that lost the race was ingested by nothing and reported
  to no one. The identity is now registered before any of the bytes are
  written, so the context is always in place before the file can be seen -
  ahead of the staging write rather than between it and the rename, because
  awaiting there would mean awaiting while the staged file is the only copy of
  the upload, where a dropped connection takes it with it.

  This fixes the ordering, not the delivery. The event emitter is
  fire-and-forget - it logs a handler's failure and returns, and it reaches
  nobody when no converter is connected - so a converter that drops between the
  availability check and the emit still leaves a published file with no context
  behind it, and `failed_files` still has nothing retrying it. Closing that
  needs the context to outlive the emit, in Redis rather than in the
  converter's process memory, or the converter to ask for a context it does not
  recognise.

- **Two uploads of the same filename no longer corrupt each other.** The
  staging name was a single `<final>.part` per destination, so a client
  restarting an interrupted transfer could have two uploads writing the same
  path: one overwrote the other's staged bytes, the first rename consumed them,
  and the loser's rename failed with `FileNotFoundError` on a path that had
  already been moved away. Staging names now carry a unique per-upload suffix.

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
