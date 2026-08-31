# Handoff — Fit Score & Assignment Confidence (peak-centric integration)

*Start here if you are picking up the "fit score + identification confidence" workstream.
Everything is landed on the `epic/peak-centric-assignment` branch.*

## 1. What this workstream is

Two things, kept deliberately separate:

- **Fit score** — a pure, reproducible *measurement* of how well a peak's data fits a
  candidate composition (mass, intensity, SNR-detectability, isotopes). Bounded `[0,1]`,
  `1.0` = perfect. **Competitor-blind**; makes no probability claim.
- **Identification confidence** — a *layered* system on top of the fit score (chemistry,
  spectral neighbourhood, instrument/context, calibration, arbitration) that decides
  *which* of several well-fitting candidates is real, and reports a confidence + level.

This is the science layer of the **peak-centric assignment** paradigm: assign a
composition to every peak — Stage A (known/database), Stage B (untargeted), then
arbitration into confidence tiers. The fit score is the **scoring engine** for both
stages; the confidence layers are the paradigm's **Phase 3** (tiers/arbitration).

## 2. Read these, in order

1. [`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) — the peak-centric engine
   (Stage A/B, `PeakAssignment` tables, phased plan). The frame everything sits in.
2. [`reference_peak_assignment_convergence.md`](reference_peak_assignment_convergence.md) —
   how the reference compound DB feeds Stage A (formula-based match, one-to-many identity).
3. [`../../libraries/tools/docs/fit_score.md`](../../libraries/tools/docs/fit_score.md) —
   the fit score: the exact model (`score_pattern_v2`), math, parameters, references.
4. [`assignment_confidence.md`](assignment_confidence.md) — the confidence-layer **study +
   phased plan** (L0–L5, Schymanski/MSI levels, target-decoy calibration, references).
5. [`../../libraries/tools/docs/composition_assignment.md`](../../libraries/tools/docs/composition_assignment.md) —
   the composition enumeration + heuristic filtering pipeline.
6. `../../tooling/score_eval/DESIGN.md` — untracked scratch design with the detailed
   validation numbers/metrics (kept in the worktree, not committed).

## 3. Decisions already made — do not relitigate (rationale + where documented)

- **`match_score` is the FIT, not a probability.** Displaying the Platt-calibrated
  probability made a perfect match read ~0.87 and looked "unsure"; the raw fit is
  median ~0.92 / max 1.0 and matches intuition. The calibration is retained but belongs
  to the confidence layer. (`fit_score.md` §1; `assignment_confidence.md`.)
- **Rename in flight: `match_score` → `fit_score`** to say plainly it measures fit.
- **Legacy targeted path defaults to v1** (`MASCOPE_MATCH_SCORE_VERSION=1`). The fit score
  (`=2`) is adopted *deliberately* in the peak-centric engine, not by silently flipping the
  legacy default — per the epic's "coexist, don't replace" principle. v2 also degrades to
  v1 where a lighter aggregation path lacks per-isotopologue columns.
- **`rule_senior` (Golden Rule 2) fails open on radicals.** It rejects only the impossible
  (over-saturated / disconnectable) neutrals; odd-electron species can be genuine
  (APCI/APPI). Applies to NEUTRAL formulas only. It is also **opt-in**
  (`HeuristicFilterConfig.use_senior`): it replaced a no-op placeholder, so enabling it for
  every caller would silently narrow the pre-existing composition search. Stage B sets it.
- **The whole feature is off by default**, behind `peak_assignment` in the runtime
  *(historical: the flag stayed, but its default flipped to **on** when the
  feature became generally available - see `peak_assignment_paradigm.md`)*
  `[meta]` config (env override `MASCOPE_PEAK_ASSIGNMENT`), read by backend
  (`peak_assignment_enabled()`) and frontend (`runtime.meta`) alike. "Coexist, don't
  replace" turned out to need enforcing: ingest-time assignment, the rescored composition
  search, the reference annotation on that same search, and the Sample-view rework all
  changed behaviour for users who never opened the feature. On the API the flag gates the
  **writes**: assign / verify / recalibrate return 403 with it off
  (`require_peak_assignment_enabled` in `routes.py`), while the read routes stay open so
  ledgers from opted-in periods remain inspectable — the full reasoning in
  [`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) §5.1.
- **The on-demand composition search takes its mass sigma from the instrument, not from
  the candidates.** `api/new/cheminfo/service.py` rescores each candidate with
  `ion_score_v2`, and the fit score's mass term needs the instrument's mass accuracy.
  Stage A can *fit* it (`fit_sample_mass_accuracy` over every isotopologue the library
  matched across the spectrum), but the search cannot: its rows are the candidates for a
  single m/z, spread across `±mz_precision` by construction, so a fitted sigma would
  measure the search window and flatten the mass term to near-uniform. It uses the
  sample's resolved `match_params.mz_tolerance` read as a ~3σ window (≈1.7 ppm Orbitrap,
  ≈5 ppm TOF) instead, and **reports no fit score at all** when no tolerance is
  resolvable — `score_pattern_v2`'s own `FALLBACK_SIGMA_PPM = 2.0` is Orbitrap-only and
  would collapse every candidate on a TOF sample.

## 4. Data-quality findings to revisit (chemistry review)

Validated `rule_senior` against the 92 demo target compounds:
- **`C6H17NO4`** — over-saturated (17 H on a C6NO4 skeleton, max 15): impossible for any
  neutral. Almost certainly a target-list **data error**; fix at source.
- **`C9H15O6`, `C10H15O5`, `C10H17O7`, `Br`** — odd-electron radicals as neutrals (they now
  pass, fail-open). Confirm whether legitimate radical species or off-by-one-H entries.

## 5. Current state

*Keep this section honest — it is the first thing the next person reads. Describe the
branch's shape, not a commit hash that goes stale within a day.*

- **Branch:** `epic/peak-centric-assignment`, the integration branch. It was **rebased
  onto `develop`** (2026-07-27), which rewrote every commit on it, so any branch cut from
  the old history has to be rebased too — `feat/reference-stage-a` (PR #1633) already was.
  The rebase re-parented the branch's Alembic chain onto develop's head; the migration
  chain is linear with a single head.
- **Migrations:** two, not five. The branch's four peak-assignment revisions were squashed
  into `a1f8c25d9e47` before merge, since one of them renamed a column on a table the
  branch itself had created two revisions earlier - every deployment would have created
  `match_score` and immediately renamed it to `fit_score`. The reference tables keep their
  own revision (`c4f7a2e9b1d8`): independent subsystem, no foreign key either way.
  Equivalence was proved by diffing a schema dump built from the old chain against one
  built from the new (identical), and the stairway/single-head/model-drift tests pass.
- **Opt-in:** the feature was **off by default** (§3) at the time of writing; it
  is on by default now. Work stacked on
  `feat/peak-assignment-opt-in`.
- **Tests:** the full suite is green — libraries, CLI, backend unit + integration +
  migrations, frontend unit, lint/format. One gotcha when running them from a worktree:
  the backend suite is gated on Postgres at *import* time
  (`server/backend/tests/conftest.py`), so even the pure-unit tests need `mascope dev up`.
  (The migration tests used to resolve Alembic from `$MASCOPE_PATH` and silently test the
  *main* checkout's migrations against this tree's models — a bogus model-drift failure.
  Fixed with roadmap item E8; they now resolve from the checkout they live in, so no
  `MASCOPE_PATH` juggling is needed.)
- **Code map:**
  - Fit score: `libraries/tools/src/mascope_tools/composition/heuristic_filter.py`
    (`score_pattern_v2`, `calibrate_score`, `rule_senior`).
  - Backend adapter: `.../api/controllers/match/lib/match_score_v2.py` (`ion_score_v2`,
    `match_score_version`) and the dispatch in `.../match/lib/match_aggregate.py`.
  - Peak-centric engine (epic): `server/backend/src/mascope_backend/api/new/peak_assignments/`.
  - Feature flag: `.../peak_assignments/config.py` (`peak_assignment_enabled`) and
    `server/frontend/src/lib/features.js`.
  - Eval harness: `tooling/score_eval/` (`make_candidates.py`, `score_eval.py`).

## 6. Next steps (priority order)

**Landed since this handoff (all on `epic/peak-centric-assignment`):**

1. ✅ **Phase 3 chemistry — graded plausibility (P1).** `chemical_plausibility` /
   `formula_plausibility` in `heuristic_filter.py`: a per-candidate plausibility in
   `[0,1]` = Senior/RDBE (Rule 2) × element-ratio (Rules 4–5, Table 2) × heteroatom
   co-occurrence (Rule 6, Table 3), numbers verbatim from Kind & Fiehn 2007. Grades, does
   not gate; fail-open. Unit-tested + validated on the 91 demo formulas (only the
   over-saturated `C6H17NO4` scores 0). See `assignment_confidence.md` §4.
2. ✅ **Wired the fit score into the peak-centric engine's Stage A/B scoring.** Stage A:
   `engine.score_ions_by_fit` (deliberate ion-level `score_pattern_v2` per `target_ion_id`,
   post-gating). Stage B: uses the isotope-pattern fit score `assign_compositions` already
   computes (v1 degradation, no SNR). Both replace the crude `abundance_term·mz_term`.
   See `fit_score.md` §1a.
3. ✅ **Renamed `match_score` → `fit_score`** on the `PeakAssignment` surface: model column
   + range check constraint, `PeakAssignmentRecord` schema, engine output dicts,
   read-model, tests, and the peak-assignment migration (`a1f8c25d9e47`; the rename
   was later squashed into it, so the column is created as `fit_score`), chained from the
   peak-assignment-tables head). Legacy `match_ion` / `match_isotope.match_score`
   deliberately untouched. **Applied to `mascope_demo` (dev postgres) end-to-end**; the
   live API serves `fit_score`.
4. ✅ **Engine-owned tier bands (0.75 / 0.45).** `PeakAssignmentConfig.assigned_threshold`
   (0.75) / `candidate_threshold` (0.45); Stage A/B tier against them instead of the legacy
   `match_params` thresholds. Persisted on the run config; provisional (see below). The two
   key names are the original ones — only the scale they sit on moved, from the bare fit to
   the evidence (item 10), which is why the numbers came down from 0.8 / 0.5.
5. ✅ **Phase 3 P2 — candidate arbitration (core).**
   `mascope_tools.composition.arbitration.arbitrate_candidates`: competes a peak's
   candidates by **fit × plausibility**, emits a normalised confidence, flags ties
   (Schymanski L5). Unit-tested; `assignment_confidence.md` §4 (P2 progress).
6. ✅ **Live end-to-end on real demo spectra.** Migrated `mascope_demo` to head and ran
   `assign_sample_peaks` over all 161 demo samples. `fit_score` median ≈ 0.95; tiers band
   cleanly; Stage A winners chosen by fit × plausibility with confidence/tie in
   `provenance`. Data sits in `mascope_demo.peak_assignment` for the UI. *(The banding was
   measured under fit-scale bands at 0.8 / 0.5, the split in force when the run was made;
   tiers are read off the evidence now — item 10.)*
7. ✅ **P2 confidence calibration (pipeline; data provisional).**
   `mascope_tools.composition.calibration`: `Calibration` (provenance-carrying),
   `fit_calibration` (Platt + held-out ECE, refuses too-little data), `apply_calibration`,
   `calibration_error`, per-instrument `calibration_for`. **Honest fallback:** no curve for
   an instrument → `p_correct=null, calibrated=false` (TOF today); one **provisional
   Orbitrap** curve (a=5.74, b=-3.36, held-out ECE 0.029) fit from the demo bundle via
   `arbitration_eval.py --fit-calibration` (untracked scratch alongside the
   `tooling/score_eval` harness). Wired into the engine → `provenance.p_correct /
   calibrated / calibration`. Labels = reference-confirmed identities (Schymanski L1) vs
   decoys — the reference-dataset link + basis for future user self-calibration. See
   `assignment_confidence.md` §4 + `how-it-works/peak-assignment.md`.
8. ✅ **How-it-works docs** — new user-facing `how-it-works/peak-assignment.md` (fit score,
   plausibility, arbitration, calibration, tiers) with citations; `matching.md` TODO
   resolved.
9. ✅ **Peak-ownership tolerance fix** — a peak is only OWNED by an isotopologue whose
   pairing is within tolerance (`invert_matches_to_peak_assignments` requires a positive
   gated intensity). The targeted matcher pairs within a wide 0.5 Da window; without this a
   trace isotopologue grabbed an out-of-tolerance peak (up to ~1500 ppm off on the demo,
   ~71% of Stage A rows), inherited its ion's tier, and blocked that peak's correct
   assignment. Out-of-tolerance pairings now fall through to Stage B / unassigned.
   Unit-tested.
10. ✅ **Tiers derive from the evidence (fit × plausibility), not the fit alone.**
    `engine.evidence_for(fit_score, formula)` weighs the fit by the formula's chemical
    plausibility, and `engine.tier_for_evidence` — renamed from `tier_for_score`, bands now
    **keyword-only** because they read in the opposite order to their names and a positional
    call written in band order silently inverted them — buckets that product. The rationale
    is that this is already the currency both stages arbitrate a contested peak in (item 5),
    so the tier now agrees with the quantity that picked the winner, and a chemically
    implausible formula can no longer hold the ledger's strongest word on mass accuracy.
    Every derivation site moved together: both engine stages, manual curation and its
    demote-restore fallback, the copy service's re-tier, the composition-search preview, and
    the import tier-coherence check (`tier_coherence_error` gained a `formula` parameter).
    Plausibility is always **recomputed from the formula**, never trusted from a payload —
    it is a pure function of the formula, so an imported row can be checked without asking
    its author to declare one; it fails open to the bare fit when the formula is absent or
    unparseable. `fit_score` is **unchanged**: still stored and displayed as the pure
    measurement, just no longer what buckets the row. `PEAK_ASSIGNMENT_ENGINE_VERSION`
    0.2.0 → 0.3.0. The bands were re-fit by sweeping the pair over a real ledger — all 161
    demo samples assigned, 213,146 rows, 77,911 of them tiered. Plausibility turned out to
    be a spike at 1.0 with a thin tail (92.8% of tiered rows score exactly 1.0; Stage A
    98.3%, Stage B 88.4%), so only 7.2% of rows move at all: 0.75/0.45 gives assigned
    85.38% / candidate 11.73% / below_assignability 2.89% against 84.08% / 12.41% / 3.51%
    under fit-tiering at 0.8/0.5, the closest pair in the sweep to the split it replaces,
    with the 6.93% of tiered rows that change tier moving in both directions (2,717 up,
    1,710 down) rather than draining one band. Known and deliberately not solved: Stage A's
    fit is `ion_score_v2` and Stage B's is `score_pattern` (v1, no per-peak SNR), so one
    band means slightly different things to each — on the sweep, holding the upper band at
    0.80 would cost Stage B 5.3% of its assigned rows and Stage A only 0.5%. That
    heterogeneity **predates** this binding (it was equally true under fit-tiering), and
    per-stage bands were not introduced. This does **not** supersede the end state: binding
    the tier to a calibrated P(correct) remains the documented destination, still gated on
    universal calibration coverage (untargeted + all instruments) and still deferred. Item
    4's bands are directional, not calibrated — see D7.

## 6a. Roadmap / next steps (priority order)

**A — correctness / verification (near-term)**
- **A1. Verify the ownership fix (#9).** ✅ *Verified read-only on the demo* (no DB writes,
  no interference): running the real matcher → gating → `invert` on 8 samples (both
  polarities), Stage A ownership drops **5078 → 1519 peaks — 70.1% were out-of-tolerance
  steals**, now released to Stage B / unassigned (matches the 71% dataset-wide finding).
  Remaining: a **persisted** re-run so the corrected data is UI-browsable — needs the
  `isotope_formula` migration applied to the run DB; prefer an isolated env
  (`mascope dev run --instance`; note the per-env filestore) over the shared demo stack.
- **A2. (optional) Two-tier claim tolerance** — a looser "claim" tolerance (~3–5 ppm) so
  genuinely borderline real isotopes are not released while the strict tolerance still gates
  the score. Only if borderline isotopes are seen dropping.

**B — fit-score model**
- **B3. Mass-term over-penalty — investigated (golden set).** Conclusion: **no
  distribution-level fix is warranted for `Br3-`.** Empirically the mass-error core is
  m/z-flat (~0.13–0.18 ppm), with **no m/z σ growth**, **no m/z offset** (Br3-'s region
  signed-mean ~0), and only a mild high-intensity (space-charge) uptick; a **Student-t** term
  scores Br3- *lower* (its peaks are at the ~1.4 σ shoulder, not the deep tail). Br3- is a
  genuine **mass-accuracy outlier** and ~0.6 is honest (calibrated confidence ~0.57 agrees).
  ✅ **SNR-aware mass σ implemented** (the one data-supported refinement; does not lift
  Br3-). Per-peak mass width `σ_i = sqrt(σ² + (MASS_SNR_K/SNR)²)`, `MASS_SNR_K=2.36` fit on
  the goldens (σ_mass 0.63 ppm @ SNR≈4 → 0.10 @ SNR>1000). Clean golden-set A/B (K=0 vs
  2.36): top-1 contested **0.706→0.723**, true-score **p10 0.45→0.50** (the over-penalised
  weak isotopologues), ROC-AUC/calibrated-ECE flat. Br3- accepted as an honest outlier
  (option 1). See `fit_score.md` §3.1/§Limitations. *(A follow-up: the stored score_eval
  baseline JSONs were stale — regenerate after a clean golden re-export.)*

**C — finish Phase 3 P2 (science)**
- **C4. Curated calibration data** — a proper Orbitrap reference set to replace the
  provisional curve, and a **TOF golden set** so TOF stops being uncalibrated. Positives =
  reference-standard identities (Schymanski L1); negatives = decoys — tightly tied to the
  reference dataset.
- **C5. Extend arbitration + calibration to Stage B** once untargeted candidates carry
  comparable per-candidate fits.

**D — product / data-gated decisions**
- **D6. Persisted per-instrument calibration store — DONE.** `assignment_calibration` table
  (created by migration `a1f8c25d9e47`) holds the Platt curve + per-adduct corroboration log-odds;
  `calibration_store.load_calibration` reads the active row and falls back to the in-code
  provisional curve. The service loads it and passes it to the engine, which folds adduct
  corroboration into `p_correct`. The refit path (fit a new active row from labels) is **built** as
  D9/V2 (`recalibrate_instrument`); *remaining:* the front-end "calibrate my instrument" trigger +
  the verification capture UI that feeds it — designed in
  [`verification_calibration_loop.md`](verification_calibration_loop.md).
- **D7. Recalibrate the tier bands** (currently the 0.75/0.45 estimates) per instrument — a
  "what users see" decision. Still open, and now a recalibration on the **evidence** scale:
  because plausibility is ≤ 1, evidence ≤ fit for every row, so a given number is stricter
  here than the same number was on the fit scale.
- **D9. Interactive verification → calibration golden set.** Human-in-the-loop confirm/reject in
  the UI feeding `fit_calibration` per instrument. Designed in
  [`verification_calibration_loop.md`](verification_calibration_loop.md); the central risk is the
  confirmation-bias loop (guardrails recorded there). **V1 capture backend shipped** (the
  `assignment_verification` table, created by migration `a1f8c25d9e47`, + verify/verifications API); the V1
  UI is handed to the frontend ([`verification_capture_frontend.md`](verification_capture_frontend.md)).
  **V2 built:** `recalibrate_instrument` + `POST /calibration/{instrument}/recalibrate` (superuser)
  refit the curve from labels (provisional until enough reference-grade positives), writing a new
  active store row — verified on synthetic + demo; waits on real V1-UI labels to switch on. **V3
  open:** active-learning queue, evidence-level weighting in the fit, Schymanski surfacing.

**E — ops**
- **E8. Resolve the worktree/main alembic split. — DONE.** The CLI resolved alembic from
  `$MASCOPE_PATH` — the *main* checkout — so from a worktree it applied **develop's**
  chain and then reported "Database up to date". Consequences, both observed: the
  migration tests silently verified the wrong migrations, and `mascope dev run --instance`
  could not create this feature's tables at all, so a worktree stack came up without them.
  Both now resolve from the running source tree instead — `checkout.backend_path()` for
  the CLI, `Path(__file__)` for the test suite — with `MASCOPE_PATH` left to its documented
  job (database, secrets, `.runtime`). Regression coverage in
  `tooling/cli/tests/test_dev_migrate.py`. (The startup reaper still tolerates a
  missing-table state rather than failing the boot; that remains damage control, and is
  now a backstop rather than the only line of defence.)
- **E8b. Schedule the retention prune.** `prune_peak_assignment_runs` exists and is
  documented for operators, but nothing runs it — there is no timer or cron entry, so
  unattended growth is unchanged until one exists.
- **E8c. Cross-process admission control.** The batch semaphore and in-flight set are
  per-worker; prod runs several. A real bound (and cancellation, and resume) needs batch
  run state in the database.

**F — later phases**
- **F9. P3 — spectral-neighbourhood corroboration.** ✅ *First increment landed:*
  `mascope_tools.composition.corroboration.adduct_corroboration` — a compound seen via
  multiple co-occurring adducts is corroborated (`1 - 2^-(n_adducts-1)`); validated on the
  demo (~66% of confident compounds are multi-adduct). Unit-tested. *Remaining:* fold it into
  the reported confidence (a run-level post-pass; modeling/product decision), then
  intensity-consistency across adducts + in-source-fragment grouping (fuller CAMERA/IPA).
- **F10. P4 — context & levels** — retention-time / ionization priors + a reported
  Schymanski/MSI identification **level** alongside the confidence.

*Doc discipline:* record the plan here as it evolves; document each **implemented** feature
in `docs/user/how-it-works/` with its literature citations.

## 7. Environment / ops notes

- **Running the worktree's code:** the `.venv` is an editable install pointing at the
  *main* checkout, so to import THIS worktree's code you must put its `src` dirs on
  `PYTHONPATH` ahead of the `.pth` entries. Test invocation pattern used here:
  `PYTHONPATH="<worktree>/server/backend/src;<worktree>/libraries/*/src;…" python -m pytest …`.
- **Demo DB:** Postgres in the `mascope_dev_postgres` docker container (`mascope_demo`
  database) backs the golden-dataset validation.
- **Deploying this branch — do not tell operators to reinstall the CLI for
  `mascope reference`.** That group is registered only under `source_checkout()`
  (`tooling/cli/src/mascope_cli/main.py`), so a wheel install will never expose it no
  matter how often it is reinstalled; it is developer-only on purpose, because it pulls
  the chemistry dependencies kept out of the operator CLI. A deployment loads reference
  data by running the same ingest inside the backend container
  (`docker compose exec backend python -m mascope_backend.db.scripts.reference_sync ...`);
  `mascope prod db script run` forwards no arguments, so it covers the argument-free
  scripts only. See [`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) §7 and
  [`reference_data_authoring.md`](reference_data_authoring.md).
- The fit score was validated end-to-end on the demo (median fit 0.92, max 1.0; scores
  scale monotonically with isotopic corroboration) — the numbers live in
  [`fit_score.md`](../../libraries/tools/docs/fit_score.md) §5 (originally in the
  untracked `DESIGN.md` scratch).
