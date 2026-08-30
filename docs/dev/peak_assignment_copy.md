# Copying Assignments From One Sample to a Batch's Other Samples — Design Note

*One sample of a batch gets the full treatment — an engine run, inspection,
and human overrides — the manual-curation write path has since shipped (§6).
The batch's other samples usually carry closely related chemistry, yet their
only route to a ledger today is another full engine run each, which re-derives
everything and knows nothing about the curation. This note compares two ways to
propagate the curated sample's assignments instead — **B1**, a literal copy, and **B2**,
a seeded copy with per-sample re-score — both publishing through the
run-import channel ([`sdk_peak_assignment.md`](sdk_peak_assignment.md) §8.2),
and recommends B2. The target-collection detour is rejected (§8).*

> **Status: proposal (2026-08-29, triage item WP12).** A design comparison for
> sign-off — no code until agreed. User steer on record: use the source
> sample's assignments directly as the starting point for the other samples —
> do **not** route the copy through target collections, and do not recompute
> everything from scratch. Code anchors re-verified against `develop` on the
> date above.

---

## 1. The shape both variants share: remap, then import

A copy is two steps: **remap** the source run's current rows (curation
included) onto each destination sample's *own* peaks, then **publish** through
the import channel (`POST /peak-assignments/sample/{id}/runs/import`,
[`routes.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/routes.py))
— a fan-out of one new first-class run per destination, same tables, read
model, and batch fold-in as an engine run. The channel already provides what a
copy must not reinvent: append-only publishing, admission control, strict-lite
validation (single owner per peak, peak existence, tier↔fit coherence under
declared `tier_bands`), mandatory attribution, and per-(sample, engine)
retention. A server-side copy calls the import service directly rather than
looping HTTP requests.

The remap is the one part the channel deliberately does not do — the importer
validates every `sample_peak_id` against the destination's peak file and never
re-matches by m/z
([`import_service.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/import_service.py)).
Per destination, the copy:

- loads the destination's peaks through the same read path the engine uses
  (which also yields the instrument-correct intensity quantity), and matches
  source peaks onto them on a mu-corrected axis — predicted destination m/z is
  `src_mz * (1 - mu_src/1e6) / (1 - mu_dst/1e6)` — within the
  resolution-adaptive tolerance the batch anchors use. Each `mu` is the median
  `mz_error_ppm` of that sample's run, recomputed because the fold stores it
  nowhere
  ([`batch_peaks_controller.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_controller.py));
  a destination with no run yet falls back to the targeted-path estimate
  (`fit_sample_mass_accuracy`) or 0.
- uses `BatchPeakOccurrence` as a fast-path where it exists: for an
  already-folded destination the mapping is one join (source occurrence →
  `batch_peak_id` → destination occurrence). It cannot be the primary
  mechanism — occurrences exist only once a completed run has folded, and the
  common destination has no run at all.
- drops source rows with no destination peak and `iso_child` rows whose owner
  was dropped, keeps the better-scoring row when two land on one peak, and
  rewrites `sample_peak_id`/`mz`/`intensity` to the destination's observed
  values.
- **publishes a complete run**: every unmapped destination peak gets an
  `unassigned` row, as the engine writes for unexplained peaks. The batch fold
  takes a sample's whole contribution from its latest completed run, so a
  partial run would silently shrink the sample in the batch view (the import
  route's documented replace semantics); one row per destination peak makes
  that footgun unreachable by construction.

Eligibility per destination: same batch and polarity as the source (a
mechanism id of the wrong polarity is a 422), not a blank, no run in flight
(skipped and reported, not failed).

---

## 2. B1 — literal copy

Carry the source's formulas *and* its numbers — `fit_score`, `mz_error_ppm`,
`abundance_error`, tier — verbatim, with the source run's `tier_bands`
declared. Cheapest, and preserves curation to the digit. But the inherited
numbers describe the *source* sample's peaks, and every consumer treats them
as the destination's evidence: the ledger and inspector present them per row,
and the batch consensus weights each member's vote by
`fit_score * (1 + log1p(intensity))` — a source fit scaling a destination
intensity. Worst is `mz_error_ppm`: the fold recomputes the per-sample axis
offset as the median of the run's `mz_error_ppm`, so a literally copied run
shifts the destination's peaks by the *source's* calibration error before
anchor snapping — mechanical mis-binning, not a presentation issue. Patching
that means recomputing mass errors against destination peaks, which already
concedes that per-sample numbers must come from the sample they describe. And
a destination whose data would not support a formula still displays the
source's confident tier — B1 satisfies the coherence check's letter while
defeating its purpose.

---

## 3. B2 — seeded copy with per-sample re-score

Carry formulas, roles, families, alternatives, and curation markers; re-derive
`fit_score`, `mz_error_ppm`, and `abundance_error` from the destination's own
peaks; re-tier via `tier_for_score` under the source run's declared
`tier_bands` — coherence then holds because the tier is computed from the
imported fit, not asserted beside it. This is a bounded re-score of the seeded
list: no candidate enumeration, no engine search, no re-arbitration. Winners
stay the curated winners; only their evidence is re-measured.

**Which scorer is honest.** Not `POST /sample/{id}/fit/aggregate`: despite its
placement, it computes the legacy v1 aggregate and never calls the engine's
scorer
([`visualization.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/visualization.py)).
The persisted Stage-A (targeted-stage) `fit_score` — the scale the run's
`tier_bands` (default 0.8/0.5) are declared on — is `score_pattern_v2`, so the
honest source is the engine's own chain run server-side over a seeded frame:
generate ions for the copied formula×mechanism list, one
`compute_match_isotopes` pass (a single peak-file load covers every formula),
`apply_match_params` to gate out-of-tolerance pairings exactly as Stage A
does, then `score_ions_by_fit`
([`engine.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py)).
After the peak-file load these are DataFrame transforms already invoked with
in-memory seeded frames by other callers — a new internal call path, not new
science. Rows whose source was the untargeted stage carry a v1-scale fit on
the source; re-scoring moves them onto the v2 scale, deliberately: after
curation the seed list *is* a curated candidate library and is scored like
one. Copied `alternatives` travel verbatim — they are the curation context and
the override undo-trail — with their embedded source-sample fits labeled by
provenance, not re-scored.

---

## 4. Recommendation: B2

B1's inherited numbers mis-weight batch consensus and mis-shift the fold's
mass axis (§2), and fixing even the worst of that already requires
per-destination computation — at which point B2 costs one seeded scoring pass
more (roughly a Stage-A run minus candidate assembly and minus the untargeted
stage). Build one pipeline — remap → seeded re-score → publish complete run —
in which B1 is the degenerate skip-the-scoring mode, kept internal and not
exposed in the UI. This matches the steer: the source's assignments are the
starting point, their evidence is re-measured per sample, and nothing is
re-searched or re-decided.

---

## 5. Publishing: attribution, overwrite semantics, retention

- **Engine label.** Copied runs publish under a fixed engine name, proposed
  `mascope-copy`, reserved like `mascope` so external imports cannot forge it.
  Reservation is enforced inside the shared import validation
  (`normalize_engine`), so the copy service needs a trusted internal entry — a
  server-side flag past that check, or reservation enforced only at the HTTP
  boundary. `BaseRunProvenance` renders any non-`mascope` engine as external;
  teach it this one value as a first-party *copied* presentation ("copy of
  \<source sample\>", source run and date in the tooltip).
- **Disclosure as manifest.** The mandatory `calibration` object carries the
  copy manifest — source sample/run ids, the offsets and tolerance used,
  mapped/dropped counts — and `config` the full copy parameters. Per-row
  `provenance` gains `copied_from` (source sample/peak/assignment ids) plus
  the source fit for reference. The server strips its reserved keys as for any
  import, so copied rows render **no calibrated P(correct)** — and a batch
  peak supported only by copied members shows none either. An honest
  consequence, not a defect.
- **Overwrite semantics: append-only.** Each copy creates a new run per
  destination; nothing edits existing runs. The new run is the latest
  completed, so it is what the ledger opens and what the fold reads —
  replacing that sample's batch contribution (safe, §1). Earlier runs stay in
  the run selector. Copies age out of their own per-(sample, engine) quota (3
  by default) and count against the cross-engine per-sample total (12; in-app
  runs exempt — whether the copy engine shares that exemption is an
  implementation decision); they can never evict in-app runs. Re-copying after
  further curation is simply another publish with a fresh `import_id`.
- **Tell the UI.** The import channel emits no `peak_assignment_reload` today
  (the batch-peaks pane's compute button even documents "refresh after an
  import"); the copy flow must emit it — or run under the background-task
  decorator — so ledgers and batch views refresh when the fan-out lands.

---

## 6. Manual overrides and verifications

The manual-curation write path **has shipped** (`PATCH …/assignment/{id}`,
`peak_assignments/curation.py`), and it records an override in place much as
this section anticipated: the row's winner becomes the chosen formula, the
previous winner moves to the head of `alternatives`, provenance gains a
`manual: {action, scored_by, user_id, at, previous_formula, previous, demoted,
restored, restore_skipped, restore_failed}` block, and the row is marked
`source: "manual"`.
The sequencing dependency named here is discharged — `AssignmentSource` is now
`database | untargeted | manual`, so a copied override row is a legal import
rather than a 422. Because the copy reads the source run's *current* rows,
overrides propagate mechanically.

Three details of the shipped shape the copy has to respect:

- **A curated row carries no calibrated fields.** The engine's judgement of the
  displaced winner is archived whole inside
  `provenance.manual.previous.engine_judgement` — all nine
  `_ENGINE_JUDGEMENT_KEYS`, not only the calibrated `p_correct` / `calibrated` /
  `calibration` / `corroboration` — because every one of them describes the
  arbitration that produced the *displaced* winner. The curated row's own
  provenance is rebuilt from the candidate being committed rather than edited,
  so nothing of that blob is inherited; only what is honest for the new winner
  comes back (`evidence` recomputed from the committed fit and plausibility,
  `reference_identities` taken from the committed candidate). So a copied
  override needs no stripping of those keys beyond what the import path already
  does, and a destination reader sees "no calibrated probability" rather than
  one belonging to another formula.
- **An override demotes the satellites of the formula it replaced** to
  `unassigned`, marked `source: "manual"` with their own previous winner kept
  in `alternatives`. Satellites go only when the committed (formula, mechanism)
  pair actually differs from the one the row held — a family belongs to a
  compound, and a compound is a formula under an adduct. A copy of a curated
  run therefore carries demoted rows too; under B2 they are re-scored like any
  other row, and an `unassigned` source-row simply has no formula to re-score.
- **The undo trail would travel as data, but not as an undo — and would not
  survive being tried.** Each stripped satellite is archived on the M0's
  `provenance.manual.demoted`, and committing the same (formula, mechanism)
  back onto that M0 restores the satellites onto their own rows — a real undo
  rather than a message that the change was reversed. The archive would ride
  along on a copy like the rest of the manual block (import strips three
  top-level keys and never descends into it). But each entry names a
  `peak_assignment_id`, and a restore may only ever write rows of the run it is
  curating — the ids come out of a JSON blob an imported run's publisher could
  have put anything in, so that guard is deliberate and right. On a destination
  those ids name the *source* run's rows: the importer mints fresh ids for
  every published row, so nothing the copy writes can ever answer to them.
  What that would cost is worth stating plainly, because the entries would not
  simply lie dormant as audit. They are read only by the edit that commits
  their compound back onto the M0 — which is the undo itself — and that edit
  reports them under `manual.restore_failed` and **consumes** them, on the
  correct reasoning that an id belonging to another run never becomes this
  one's and a kept entry would hold one of the archive's 32 slots to offer an
  undo that can only fail again. So the first attempt to undo a copied override
  on a destination would put the M0 back to its formula, put none of its family
  back, say so in the response, and leave that compound's entries gone from the
  archive; the copied demotions would stay `unassigned` with nothing pointing
  at them any more. For the copy design the consequence is one line: **an
  override survives a copy; its undo does not.** A destination is put right by
  undoing on the source and re-copying, or by an engine run there — both of
  which rebuild the family from data rather than from an archive.

**How an override would travel: as data.** It is not a side channel. `source:
"manual"` is a third value of the same `AssignmentSource` literal the importer
validates against and the ledger's `source=` filter reads, and everything about
the act — action, user, time, the displaced winner verbatim, the archived
engine judgement, the demotion archive — is in the row's own `provenance.manual`
JSON. So the copy would need no override-specific transport: the remap rewrites
`sample_peak_id` / `mz` / `intensity`, and the rest of the row goes across as it
stands (import strips only the three server-owned provenance keys, all
top-level, so the nested manual block survives whole). The same fact is what
lets the re-score behave itself: B2 can tell a person's choice from the
engine's by reading a column, and it re-measures a curated row's evidence
exactly as it does any other row's while leaving the winner alone — §3
re-scores, it does not re-arbitrate, so a curated formula cannot lose its peak
to a runner-up on a destination sample. That is the difference from the
rejected target-collection route (§8), where curated winners re-compete. What
does change is the tier: under B2 an override may legitimately land in a lower
tier on a sample whose data supports it less, which is the point of re-scoring.

**But an override is run-scoped, and a verdict is not.** An override lives in
the run it edits; a later engine run on that sample rebuilds the ledger from
the data and supersedes it. A verification is the durable memory — append-only,
keyed on (`sample_peak_id`, `assigned_formula`, `ionization_mechanism_id`)
rather than on a run, and surviving re-runs by design. Two consequences for a
copy. First, what a copy carries is the source run's rows as they stand at that
moment, not a standing instruction: re-assign the source sample and its
overrides are gone, while copies already published keep theirs. Second, a
destination's copied override has exactly the same lifetime it had at home — it
is that sample's latest completed run until someone runs the engine there, and
then it is not. Neither is a defect introduced by copying; it is the lifetime
the override already has. A judgement meant to outlive a re-run is recorded as
a verdict rather than as an override.

Verifications do **not** copy. A verdict attaches to the judged sample's
stable identity (`sample_item_id` + `sample_peak_id` + formula + mechanism)
precisely so it survives re-runs on *that* sample; a copied run on another
sample starts with zero verdicts — a verdict is human judgment about one
sample's evidence and is never fabricated for another.

---

## 7. UI entry point

One entry point in v1: the sample context menu in the Datasets pane —
*Process → Copy assignments to batch…* on the curated source sample, following
the existing Process-submenu precedent (*Assign peaks* on batches, *Refresh
matches* on datasets), gated like the other assignment surfaces
(`peakAssignmentEnabled` + editor role) and enabled only when the sample has a
completed run. It opens a confirmation dialog (precedent:
`DialogPeakAssignBatch`) listing the other samples with their eligibility,
runs the fan-out in the background, and reports per-destination outcomes in a
toast. A batch-menu variant and a pre-commit coverage preview are natural
later additions, not v1.

---

## 8. Rejected alternatives

**Routing the copy through target collections** — building a collection from
the source's formulas and running targeted assignment on the other samples —
is rejected, per the explicit user steer and on the merits. It recomputes
rather than copies: a full engine pass per sample, in which curated winners
re-compete and can lose, and roles, alternatives, and manual provenance are
regenerated rather than carried. And it abuses a shared, deployment-visible
layer as scratch space: target collections are the curated targeted-paradigm
library, kept as a coexisting *view* under the peak-centric paradigm
([`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) §5.1), not a
transport for per-batch copy state — manual overrides and untargeted
discoveries do not round-trip through library membership at all. Copying
`PeakAssignment` rows directly in the database is rejected for one sentence's
worth of reason: it bypasses exactly the validation, attribution, admission,
fold-in, and retention guarantees the import channel exists to provide.

---

## 9. Open questions

1. **Deferred consensus for the fan-out.** N destinations mean N sequential
   fold-ins, superlinear over shared anchors. Fine at typical batch sizes; if
   whole-batch copies become routine, the known fix is a `fold_in: false`
   import option plus one deferred consensus pass
   ([`sdk_peak_assignment.md`](sdk_peak_assignment.md) §8.2).
2. **Sigma for thin copies.** Below 8 matched anchors the seeded re-score
   falls back to a fixed 2 ppm sigma (wrong for TOF). Fit `mu`/`sigma` from a
   wider frame, or accept the fallback and surface it in the run's disclosure?
3. **Copy-of-a-copy.** Nothing prevents copying from a sample whose latest run
   is itself a copy. Provenance chains via `copied_from`; should the UI steer
   users toward the original curated source?
