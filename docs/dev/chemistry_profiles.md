`ULTRA TRACE MASCOPE - DESIGN DOC - CHEMISTRY PROFILES FOR PEAK ASSIGNMENT`

# Reagent-Ion Chemistry Profiles & Chemistry Context: Design & Plan

## Purpose

The peak-centric engine currently treats every spectrum as generic small-molecule
chemistry: one universal element grid, one universal plausibility model, one
hand-typed adduct panel. But a CIMS measurement is not generic — the **reagent
ion system** (Br⁻, NO₃⁻, I⁻, uronium, …) determines which adducts exist, which
elements are reachable, and which bright peaks are reagent clusters rather than
analytes; and the **sampled matrix** (ambient air, chamber, indoor air, water, …)
determines which formulas are plausible at all. Today none of that knowledge
reaches the engine, so it wastes candidate space on impossible chemistry and
cannot use the strong priors an analyst applies instinctively.

This document designs **chemistry profiles**: first-class, versioned objects
that carry reagent-system and matrix knowledge into every stage of assignment.
It is the concrete plan for the confidence architecture's L3 evidence layer
("ionization/reagent priors",
[assignment_confidence.md](assignment_confidence.md) §2), the chemistry half of
the acquisition-method redesign
([ionization_method_config.md](ionization_method_config.md) §4.3), and the
"reuse peaky's context presets" item of the paradigm plan
([peak_assignment_paradigm.md](peak_assignment_paradigm.md) Phase 2). The
profile system itself is a **harvest of peaky**
([github.com/ultra-trace-systems/peaky](https://github.com/ultra-trace-systems/peaky)),
whose two-axis profile model has been proven on real Br⁻/uronium/NO₃⁻/I⁻
campaigns; per the settled decision in the paradigm doc (§5.2), we port the
logic and data, not the runtime.

---

## 1. Current state

### 1.1 The channels chemistry flows through today

| Channel | Home | Limitation |
|---|---|---|
| Adduct panel | `IonizationMode.ionization_mechanism_ids` → notation strings ([models.py](../../server/backend/src/mascope_backend/db/models.py)) | Free-text, filename-token routed, zero seeded rows — a fresh instance cannot ingest until someone hand-types `+H+`. No priors, no reagent semantics. |
| Element grid (Stage B) | `PeakAssignmentConfig.formula_ranges`, default `DEFAULT_FORMULA_RANGE = "C0-100 H0-100 O0-100 N0-100"` ([cheminfo/config.py](../../server/backend/src/mascope_backend/api/new/cheminfo/config.py)) | One generic box for all chemistry. No S/halogens by default, yet 100 O — neither CIMS-correct nor cheap. Per-run string, nothing supplies a chemistry-aware default. |
| Chemical plausibility | `formula_plausibility` ([heuristic_filter.py](../../libraries/tools/src/mascope_tools/composition/heuristic_filter.py)) — Seven Golden Rules, universal Kind & Fiehn bands | Matrix-agnostic by design. Ambient-air HOM chemistry, indoor siloxanes, and PFAS all get the same bands. |
| Known-compound set (Stage A) | curated targets + reference mirror via `iter_known_compositions` ([known.py](../../libraries/reference/src/mascope_reference/known.py)) | The mirror window is hardcoded: `DEFAULT_ELEMENTS = {C,H,N,O,S}`, `DEFAULT_MAX_CARBON = 40`, `DEFAULT_MAX_MASS = 700`. Every active source applies to every run — no notion of "this list is for monoterpene oxidation studies". |
| Calibration + adduct weights | `assignment_calibration` keyed `(instrument, score_version)` ([calibration_store.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/calibration_store.py)) | Its own docstring says a curve is valid "per instrument, per reagent chemistry", but the key cannot express the reagent chemistry ([ionization_method_config.md](ionization_method_config.md) §2.3, §8.2). |
| Labelled reagents | `CUSTOM_ELEMENTS` ([custom_elements.py](../../libraries/tools/src/mascope_tools/composition/custom_elements.py)) — `^N` only, purity 0.98 as a module constant | Isotope-labelled reagent chemistry (¹⁵N-nitrate) is code, not configuration. |

### 1.2 The seams already reserved for this work

The codebase has been leaving hooks exactly where profiles land:

- **`role='reagent'` is declared and never written.** The model and
  `AssignmentRole` vocabulary reserve it;
  [engine.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py)
  explicitly defers it ("a dedicated reagent role is a later phase"). In a CIMS
  spectrum the reagent clusters are the *brightest* peaks; today they are
  assigned as analytes or left unexplained.
- **`rule_known_chemical_space` returns all-True** with a TODO ("requires
  access to some chemical database") — the reference mirror now exists but is
  wired only as a Stage A match source, not as a Stage B prior.
- **`resolve_ionization_modes_by_peaks` is a `NotImplementedError` stub**
  ([ionization/modes/util.py](../../server/backend/src/mascope_backend/api/new/ionization/modes/util.py))
  — data-driven routing needs exactly the reagent fingerprints a profile
  carries.
- **Per-adduct corroboration weights are hand-fit provisional data**
  (`PROVISIONAL_ORBITRAP`,
  [calibration.py](../../libraries/tools/src/mascope_tools/composition/calibration.py)):
  `{"+Br-": 2.28, "+NH4+": 0.83, "+(CH4N2O)H+": 0.70, …}` — measured per
  reagent chemistry, stored per instrument.
- **`acquisition_params` are captured on ingest and consumed by nothing**
  (Phase 0 item 1 of the ionization redesign, PR #1723) — the observed-physics
  half of routing is already in `.props`, waiting for a consumer.

### 1.3 What peaky has proven

peaky's chemistry knowledge factors into **two orthogonal axes**, both frozen
dataclasses (`peaky/chem/profiles.py`, `peaky/chem/contexts.py`):

- **`ReagentProfile`** — the measurement chemistry. Five built-ins (BR, UR,
  NO3, NO3_15N, IODIDE), each: polarity, analyte adduct channels, an element
  grid (`"C0-40 H0-80 N0-3 O0-18 S0-2 Cl0-2 Br0-2"` for BR), a reagent-ion
  regex, a detection adduct for auto-selection, a default context, and for
  labelled reagents an isotope + purity (98% ¹⁵N).
- **`ContextProfile`** — the matrix prior. Nine built-ins (ambient-air,
  chamber, indoor-air, object-headspace, combustion, water, food, uronium,
  none), each: Van Krevelen windows (ambient: H/C 0.7–2.75, O/C 0–1.5,
  N/C 0–0.4, DBE/C 0–0.75, computed on `Heff = H+F+Cl+Br+I`, `Ceff = C+Si`),
  heteroatom caps (max N/S/P/F/Si/Cl/Br/I), minimum-carbon scaffolds
  (`min_C_for = {"Br": 5, "Cl": 5, "F": 3}`), an explicit carbon-free
  inorganic allowlist (HNO₃, H₂SO₄, HOBr, IO₃⁻ …), and the contaminant
  families that may be opened (organosulfate, siloxane/PDMS, glycol/PEG,
  phthalate, fluorinated, …) — each family an *additive element budget*, e.g.
  `pdms: {Si: (4,12), O: (3,14), C: (8,26), H: (18,78)}`.

Around the axes sits supporting machinery, all of it relevant here: a
reagent-cluster library builder (Rₙ⁻ series, hydrates, oxides, isotopologues)
that labels and locks reagent peaks; curated reference peaklists as
self-describing JSON with provenance (an 830-species α-pinene HOM list gated to
oxidation contexts; a 59-species always-active instrument-contaminant list); a
per-heteroatom complexity prior waived when the element's diagnostic isotope
(³⁷Cl, ⁸¹Br, ³⁴S, ²⁹/³⁰Si) is confirmed — never waived for the reagent element;
decoy-controlled homolog-series detection that must beat random-offset decoys
(≥12 links, ≥3× enrichment) before a contaminant family opens; and
certified-neutral discovery (two independent ion channels back-calculating to
one neutral certify a core mass before an expanded element box is searched).

The load-bearing observation for porting: **the profile axes and most of the
chemistry are pure** (`peaky/chem/*` imports nothing beyond stdlib/pandas, has
its own tests, and the element-range strings use the same grammar as
`element_count_ranges`), while the pass pipeline is coupled to peaky's ledger
frame. So we port the *knowledge* (axes, presets, cluster grammar, series
units, peaklists) and the *ideas* (locking, waivers, decoy gates), and leave
the pass orchestration behind — Mascope already has its own arbitration, tiers,
calibration, and verification loop.

---

## 2. The design: two orthogonal profile axes

A **chemistry profile** is the pair (reagent profile, chemistry context). They
stay separate objects because they answer different questions, attach at
different scopes, and change at different cadences:

| | **ReagentProfile** | **ChemistryContext** |
|---|---|---|
| Answers | "How was this measured?" | "What was sampled?" |
| Owns | polarity, adduct channels (+ priors), reagent-ion cluster grammar, detection fingerprint, isotope labelling/purity, element grid | ratio windows, heteroatom caps, min-C scaffolds, inorganic allowlist, contaminant families, reference-list activation, grid refinements |
| Attaches to | the sample, via its ionization mode (routing already exists) | the run, with a batch/workspace default (an interpretation choice, re-runnable) |
| Changes when | the instrument's ion source chemistry changes | the analyst reinterprets the campaign |
| Ionization-redesign fate | absorbed into `IonizationSetup` ([ionization_method_config.md](ionization_method_config.md) §4.3) | stays its own object, referenced by run config |

Keeping the axes separate avoids the combinatorial explosion (5 reagents × 9
contexts as 45 flat profiles) and matches how the science composes: Br⁻ CIMS on
ambient air and Br⁻ CIMS in a chamber share every reagent fact and differ only
in matrix priors.

**Relationship to the ionization redesign.** This is deliberately *not* a third
competing model of "ionization mode". The ReagentProfile is the chemistry
payload that `IonizationSetup` was always going to need — adducts with priors,
reagents with purity as configuration, overrides — plus the CIMS-specific
grammar (cluster series, detection fingerprints) that §4.3 did not yet specify.
Until the ionization redesign's Phase 1 lands (versioned setups, structured
adducts), the ReagentProfile ships as its own small table FK'd from
`ionization_mode`; when `IonizationSetup` arrives, the profile's columns move
into it and the FK collapses. The ChemistryContext is independent of that
migration entirely.

---

## 3. Data model

### 3.1 Tables

**`reagent_profile`** — append-only versioned (same pattern as
`assignment_calibration`: `is_active` + supersession, stamped by version):

| column | meaning |
|---|---|
| `reagent_profile_id`, `version`, `is_active`, `supersedes_id` | identity + versioning |
| `name`, `label`, `description` | `"BR"`, "Bromide CIMS" |
| `polarity` | `+` / `-` |
| `adducts` (JSON) | ordered notation list with optional per-adduct prior weight, e.g. `[{"notation": "+Br-", "prior": null}, …]` |
| `element_ranges` | grid string, existing grammar: `"C0-40 H0-80 N0-3 O0-18 S0-2 Cl0-2 Br0-2"` |
| `reagent_formula`, `reagent_charge` | the reagent ion, e.g. `Br`, −1 |
| `cluster_grammar` (JSON) | which cluster series to enumerate: homomultimers Rₙ, hydrates R·(H₂O)ₖ, oxides ROₙ, acid adducts R·(HX)ₖ, positive `[Rₙ+H]+` — bounds per series |
| `detection` (JSON) | fingerprint for auto-selection: mechanism notations and/or cluster m/z whose presence implies this profile |
| `label_isotope`, `label_purity`, `label_max` | labelled reagents (¹⁵N-nitrate: `^N`, 0.98, 2) — hoists the `CUSTOM_ELEMENTS` constant into configuration |
| `default_context_id` | the context this reagent is typically used with |

**`chemistry_context`** — append-only versioned, same mechanics:

| column | meaning |
|---|---|
| `chemistry_context_id`, `version`, `is_active`, `supersedes_id`, `name`, `label`, `description` | identity |
| `ratio_windows` (JSON) | `{h_to_c: [0.7, 2.75], o_to_c: [0, 1.5], n_to_c: [0, 0.4], dbe_to_c: [0, 0.75]}`, evaluated on Heff/Ceff |
| `element_caps` (JSON) | `{N: 3, S: 1, P: 0, F: 0, Si: 1, Cl: 2, Br: 2, I: 0}` |
| `min_c_for` (JSON) | `{Br: 5, Cl: 5, F: 3}` + `enforce: "grade" \| "gate"` (see §4.2) |
| `inorganic_allowlist` (JSON) | explicit carbon-free formulas that bypass ratio logic |
| `contaminant_families` (JSON) | named additive element budgets + the evidence required to open each (see §4.6) |
| `reference_tags` (JSON) | which reference-source tags this context activates (§4.4) |
| `known_window` (JSON) | Stage A mirror window: elements, max carbon, max mass — replacing the `known.py` constants |

**Presets ship in the library, instances live in the database.** The built-in
profiles/contexts are frozen dataclasses + data in
`mascope_tools.composition.profiles` (ported from peaky with their tests), and
a seed script (`db/scripts/seed_chemistry_profiles.py`, same pattern as
`seed_reference_demo`) installs them as DB rows. The DB rows are what runs
reference and what users later edit (editing mints a new version); the library
data is the seed and the test fixture, not a runtime lookup. This follows the
established split: `mascope_tools` stays DB-free, the backend owns persistence.

**Routing:** `ionization_mode.reagent_profile_id` (nullable FK) binds the
existing per-sample routing to a profile with zero new routing machinery. A
mode without a profile behaves exactly as today.

### 3.2 Run stamping and reproducibility

`PeakAssignmentConfig` gains `reagent_profile` and `chemistry_context`
references; the *resolved* profile content is snapshotted into
`PeakAssignmentRun.config` JSON, exactly as the rest of the config already is.
This closes the reproducibility hole of
[ionization_method_config.md](ionization_method_config.md) §2.3 for the
chemistry half: two runs with identical recorded config can no longer differ
because an admin edited the adduct panel in between — the panel is in the
snapshot.

### 3.3 Calibration keying

`assignment_calibration` gains a nullable `reagent_profile` column. First step:
**record** it on every fit and surface a mismatch warning when a run's profile
differs from the active calibration's (cheap, per §8.2 of the ionization doc).
Second step (once per-profile data exists): include it in the active-row lookup
key, so Br⁻-CIMS weights are never applied to a uronium run. The corroboration
weights inside the calibration are already per-adduct; profile keying makes the
store's own "per instrument, per reagent chemistry" docstring true.

---

## 4. Engine integration

Each profile field maps to a specific consumer. The confidence architecture's
design rule holds throughout: **the fit score stays pure** — profiles touch
candidate generation, plausibility, arbitration and priors, never the
measurement.

### 4.1 Stage B candidate generation

`_run_sample_assignment` currently builds `CompositionSearchConfig` from the
sample's mechanisms and the per-run `formula_ranges` string. With profiles:

- element grid default = the reagent profile's `element_ranges` intersected
  with the context's `element_caps` (the per-run override survives, bounded by
  the existing `MAX_FORMULA_RANGE_SPECIES` ceiling);
- adduct channels = the profile's ordered panel (same mechanisms as today, but
  seeded and ordered rather than free-typed);
- labelled-reagent handling (`^N`, purity) comes from the profile instead of
  `CUSTOM_ELEMENTS`.

This is simultaneously a *quality* change (Cl/Br/S reachable under BR, as they
should be) and a *cost* change (`C0-40 H0-80 N0-3 O0-18` is a vastly smaller
search than `C0-100 H0-100 O0-100 N0-100`; enumeration depth is the number of
element species, so tight per-element bounds are the real pruning lever).

### 4.2 Plausibility: the context layer grades, it does not gate

A new graded factor `context_plausibility ∈ [0,1]` multiplies into
`formula_plausibility` alongside the existing universal factors:

- ratio-window factor: 1.0 inside the context window, tapering outside (same
  shape as `element_ratio_plausibility`'s common/extended/extreme scheme),
  floored — never zero;
- min-C scaffold factor: graded penalty below the scaffold, with the peaky-style
  hard gate available as an explicit per-context opt-in
  (`min_c_for.enforce = "gate"`). Default is **grade**: peaky gates C<5
  organobromines in ambient air, but genuine C1–C4 halocarbons (bromoform) exist,
  and the house rule is fail-open;
- the carbon-free allowlist bypasses ratio logic entirely (as peaky's
  `_inorganic_allowed` does — HNO₃ must not be ratio-scored).

The universal Kind & Fiehn factors stay as the base: with `context = none`
nothing changes at all. Because context windows sit *inside* the universal
bands, the two factors overlap; the decoy harness decides whether the composed
factor helps or double-counts (§7). Everything remains deterministic, fail-open,
and inspectable per the layer rules in
[assignment_confidence.md](assignment_confidence.md) §3.

### 4.3 The reagent-ion pre-pass (activating `role='reagent'`)

Port peaky's `build_library(reagent)` (pure) into
`mascope_tools.composition`: enumerate the reagent cluster ions the profile's
`cluster_grammar` declares — Rₙ⁻ with halogen isotopologue combinations,
hydrates, oxides, acid adducts, `[Rₙ+H]+`/`[Rₙ+NH₄]+` for positive reagents —
with exact masses. Before Stage A, match the library against the peak list
(tight ppm window) and write the hits as `role='reagent'` rows, **locked**:
excluded from Stage A/B candidate peaks, their isotopologue satellites claimed
(intensity-gated) as reagent children.

This activates the reserved role, removes the brightest non-analyte peaks from
arbitration (fewer false analyte winners, faster Stage B), gives the ledger an
honest account of the reagent system, and produces the input for detection
(§5). Peaky's arbitration insight carries over as a rule: **the reagent
element's complexity prior is never waived by its own isotope evidence** — in a
Br⁻ spectrum, a ⁸¹Br satellite proves bromine in the *ion*, not in the neutral.

### 4.4 Stage A known set and reference-list gating

Two changes to the mirror path:

- the `iter_known_compositions` window (elements / max carbon / max mass) comes
  from the context's `known_window` instead of module constants;
- `reference_source` gains `tags` (JSON), and the context's `reference_tags`
  select which active sources join Stage A for this run. An always-active tag
  (instrument contaminants — the Keller-style list) applies regardless of
  context; a `monoterpene_ox` tag joins only when the context activates it.

peaky's curated peaklists (the 830-species HOM list, the 59-species
contaminant list) then flow in through the **existing** `custom` adapter and
`reference_sync` CLI as tagged sources with provenance — no new ingestion
machinery, and the lists become available to every deployment rather than
living inside one research tool.

### 4.5 Filling `rule_known_chemical_space`: the known-compound prior

With the mirror tagged and windowed, Stage B candidates get a small,
inspectable evidence bonus when their formula exists in the active reference
set (peaky's `reflist_prior` is 0.04 on its score scale; ours is measured on
the decoy harness first, §7). It biases ties toward known chemistry without
letting the database veto de-novo discovery — grade, not gate, so the stub's
all-True behaviour remains the floor. Winners already inherit
`reference_identities` into provenance; the prior makes the same knowledge act
*before* arbitration instead of only annotating after it.

### 4.6 Contaminant families and series evidence (batch-level, later)

peaky's families are additive element budgets that **open only on evidence** —
a homolog series (CF₂, C₂H₆OSi, SO₃ …) detected against decoy offsets with
≥12 links and ≥3× enrichment. Mascope's counterpart substrate is the batch-peak
layer (`docs/dev/peak_assignment_batch.md`, PR #1841): series detection belongs
on the frozen cross-sample anchors, not on single samples. Phase 5 ports the
series units + decoy gate and lets a confirmed series (a) open the family's
element budget for targeted Stage B re-search of member peaks and (b) count as
corroboration (`has_anchor`) in tiering. Until then, families exist in the
context schema but only the always-active contaminant *lists* (§4.4) act.

### 4.7 Deliberately out of scope here

Retention-time consistency and MS2 spectral matching are the other two L3/L4
evidence streams; they have their own designs
([public_database_integration.md](public_database_integration.md) §3.6) and are
untouched by profiles. The certified-neutral discovery pass (two channels
certify a core mass before an expanded box opens) is noted as the natural
Stage B v2 extension but not designed here.

---

## 5. Profile selection and routing

Resolution order, mirroring peaky's proven `--reagent auto` and finally
implementing the `resolve_ionization_modes_by_peaks` stub:

1. **Explicit per-run choice** — the assign dialog can always override.
2. **The sample's binding** — `ionization_mode.reagent_profile_id` via the
   existing token routing; context defaults from the profile, overridable per
   batch/workspace.
3. **Data-driven detection** — match every registered profile's `detection`
   fingerprint (reagent mechanisms present; reagent-cluster m/z found by §4.3's
   library) against the sample; polarity from the file narrows the field. This
   is also a *validation* channel even when routing is explicit: "mode says
   Br⁻-CIMS but no Br⁻/Br₂⁻ clusters found" is a warning worth surfacing.

Detection uses the file's own polarity and peaks — never the filename — which
is exactly the demotion path the ionization redesign wants for the token.

---

## 6. Shipped presets

Seeded, versioned, editable-by-supersession. Reagent presets (from peaky, with
their campaign provenance):

| preset | polarity | adduct panel | element grid | notes |
|---|---|---|---|---|
| `BR` | − | `[M+Br]-`, `[M-H]-`, `[M+HBr+Br]-` | `C0-40 H0-80 N0-3 O0-18 S0-2 Cl0-2 Br0-2` | corroboration weight already measured (+Br⁻ LR ≈ 9.8×) |
| `UR` | + | `[M+H]+`, `[M+(CH4N2O)H]+` | `C0-40 H0-90 N0-8 O0-15 S0-2` | urea/uronium |
| `NO3` | − | `[M+NO3]-`, `[M-H]-` | `C0-40 H0-60 N0-3 O0-25 S0-2` | provisional in peaky — ship marked provisional |
| `NO3_15N` | − | `[M+^NO3]-`, `[M-H]-` | as NO3 | `^N` purity 0.98 as data |
| `IODIDE` | − | `[M+I]-`, `[M-H]-`, `[M+I2]-`, `[M-H+I2]-` | `C0-40 H0-80 N0-3 O0-20 S0-2 Cl0-1` | I deliberately off the neutral grid |
| `ESI_POS` / `ESI_NEG` | ± | the generic panels from [ionization_method_config.md](ionization_method_config.md) Phase 0 item 6 | wide default | no reagent grammar; gives non-CIMS users seeded modes |

Context presets: the nine peaky contexts with their windows/caps/families
verbatim as v1 data (ambient-air, chamber, indoor-air, object-headspace,
combustion, water, food, uronium, `none`). `none` is the identity context —
selecting it reproduces today's behaviour exactly.

Reference peaklists (via the custom adapter, tagged): the α-pinene HOM list
(tags: `monoterpene_ox`, `biogenic_soa`) and the instrument-contaminant list
(tag: `always`), both with their citation/provenance blocks carried into
`reference_source`.

Seeding these presets also finally seeds ionization mechanisms/modes — closing
the "fresh instance cannot ingest a single file" gap as a side effect.

---

## 7. Validation requirements

House rule: every graded factor is measured before it ships, on the existing
benches.

- **Decoy/arbitration harness** (`tooling/score_eval/arbitration_eval.py`):
  context plausibility and the known-compound prior must improve contested
  top-1 / FDR-kept counts the way graded plausibility did (0.706 → 0.789
  contested top-1), and must not degrade calibration ECE. Run with the matched
  context vs `none` to quantify the layer, and with a *mismatched* context to
  bound the harm of a wrong user choice.
- **Reagent pre-pass**: on the demo Br/Ur bundles, every peak the library
  claims must be re-checked against its current assignment — reagent rows
  should absorb previously-unassigned bright peaks and known reagent-cluster
  mis-assignments, never confident analyte identifications.
- **Golden reproducibility**: with `context = none` and no profile bound,
  ledgers must be byte-identical to today's (the identity path is a regression
  test, not a hope).
- Campaign-tuned constants inherited from peaky (O-cap 11, series thresholds,
  scaffold minimums) are **re-derived on our decoy sets**, not trusted.

---

## 8. Harvest map

| peaky module | fate | Mascope landing |
|---|---|---|
| `chem/profiles.py`, `chem/contexts.py` | port (pure, tested) | `mascope_tools.composition.profiles` dataclasses + preset data |
| `chem/chemistry.py` (masses, adduct shifts, DBE/Senior/O-cap, grid, complexity prior) | reconcile — Mascope has equivalents for most; port the complexity prior + diagnostic-isotope waiver after decoy A/B against Rule-6 plausibility (overlap risk) | `mascope_tools.composition` |
| `chem/reagents.py` `build_library` | port (pure) | cluster-library module feeding §4.3 |
| `chem/reagents.py` label/reclaim/strip | reimplement — coupled to peaky's ledger | the pre-pass in the backend engine |
| `data/peaklists/*.json` + schema | ingest as data | `reference_source` rows via the custom adapter, tagged |
| `assignment/series_gka.py`, `series_detect.py` (units, GKA math, decoy gate) | port (pure), Phase 5 | batch-peak series detection |
| `assignment/certified_neutral.py` | later (Stage B v2) | — |
| `assignment/directors.py` known-species lists | convert to tagged reference sources — no hardcoded Python lists in the backend | reference mirror |
| pass pipeline, tiers/degeneracy frames, Excel/PDF reporting, SDK client, self-calibration z-gates | **do not port** — Mascope has its own arbitration/tier/calibration/verification stack and UI | — |

peaky itself stays what it is: the research surface and SDK power-user path. A
later follow-up can teach it to *read* profiles from Mascope instead of its
built-ins, so one profile edit serves both — but Mascope takes no runtime
dependency on peaky, and peaky loses nothing.

---

## 9. Phased plan

Each phase lands independently and shippable; everything engine-facing stays
behind the existing `peak_assignment` flag.

### Phase 0 — reproducibility + seeds (days, no schema change beyond tags)
- Stamp resolved mechanism notations into `PeakAssignmentRun.config`
  (ionization doc Phase 0 item 9 — closes the panel-edit hole at
  mitigation level).
- Record the reagent-profile name on new `assignment_calibration` rows
  (§3.3 first step).
- Seed script for mechanisms + ESI±/CIMS modes (item 6 of the ionization
  Phase 0 list, executed as data here).

### Phase 1 — ChemistryContext + Stage B grid (the quality core)
- `chemistry_context` table + presets + seed; `context_plausibility` in
  `mascope_tools`; profile-derived element grid; run stamping.
- Measured on the decoy harness before merge (§7).

### Phase 2 — ReagentProfile + the reagent pre-pass
- `reagent_profile` table + presets + `ionization_mode` FK; cluster-library
  port; `role='reagent'` writing + locking; ledger/UI already render the role.

### Phase 3 — Reference gating + known-compound prior
- `reference_source.tags`, context activation, `known_window` from context;
  peaklist ingestion; the measured Stage B reference prior (fills
  `rule_known_chemical_space`).

### Phase 4 — Calibration keying + the learning loop
- Profile-aware calibration lookup; per-profile corroboration refits via the
  existing recalibration route; ledger-driven suggestions ("`+Br-` won 12% of
  peaks but is not in your panel") per ionization doc §6.5; context-window
  audit ("18% of confident assignments fall outside the ambient H/C window —
  widen?").

### Phase 5 — Batch-level chemistry (after the batch-peak layer merges)
- Series units + decoy-controlled detection on batch-peak anchors; family
  opening + series corroboration in consensus/tiering; detection-based profile
  validation surfaced in the batch view.

### Convergence
When the ionization redesign's Phase 1 lands, `reagent_profile` columns fold
into `IonizationSetup` (§2); nothing else here moves.

### Sequencing relative to in-flight work
The four open engine PRs land first: #1834 (shared arbitration — the seam any
new evidence term goes through), #1836 (persisted SNR — makes stored-row fits
honest before we tune priors against them), #1837 (ledger slimming — profile
provenance rides the new detail route for free), #1841 (batch peaks — the
Phase 5 substrate). This document's phases start after those merge and are
designed not to touch the same files until then.

---

## 10. Key design decisions

1. **Two axes, not one profile object.** *Recommended and assumed above.*
   Reagent and matrix compose (5 + 9 objects, not 45), attach at different
   scopes, and the reagent axis has a designated future home
   (`IonizationSetup`) while the context axis does not need one.
2. **DB rows are the runtime truth; library data is the seed.** Matches
   `assignment_calibration` and the reference mirror; keeps `mascope_tools`
   DB-free; avoids adding chemistry to the runtime TOMLs (which are
   deployment config, exist in two copies, and are not versioned per run).
3. **Grade, don't gate — except the enumeration grid.** Element ranges/caps
   prune enumeration (they already do today; that is what bounds cost); every
   ratio/scaffold/known-space signal is a floored, fail-open factor. peaky's
   hard filters become opt-in per context.
4. **Interim `reagent_profile` table now, `IonizationSetup` later.** The
   alternative — blocking on the full ionization Phase 1 (Instrument,
   AcquisitionMethod, structured Adduct) — serializes two large workstreams for
   no Phase 1–3 benefit. The interim table is small, its columns are a strict
   subset of §4.3's target, and the migration is mechanical.
5. **Calibration: record the profile first, key by it later.** Keying
   immediately would orphan the existing provisional rows and block runs on
   missing per-profile fits; recording + mismatch warning gets the safety
   without the availability cliff.
6. **peaky is harvested, never depended on.** Reaffirms paradigm §5.2; the
   port carries tests with it, and pass-pipeline machinery Mascope already has
   is explicitly not ported.

## 11. Decisions needed before Phase 1

1. **Context scope.** Per-run with a batch default, a workspace default, or
   both? (Same open question as ionization §10.1 — settle them together.)
2. **Preset opinionation.** Ship the NO3 preset marked provisional, or hold it
   back until a NO₃⁻ campaign validates it? (BR/UR are demo-validated; NO3 is
   peaky-provisional.)
3. **UI surface order.** Profiles first appear in the assign dialog (cheap) or
   as a full settings page with versioning UI (the eventual home)? Suggest
   dialog-first.
4. **Mismatched-context harm bound.** §7 measures it; decide the acceptable
   degradation before defaulting any context ≠ `none` for a mode.
5. **Naming.** "Chemistry profile" (umbrella), `ReagentProfile`,
   `ChemistryContext` are used here; the ionization doc's user-facing rename
   ("IonizationSetup") should win for the reagent axis when they merge —
   confirm users see one concept, not three.
