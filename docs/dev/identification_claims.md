# Identification claims: identifiers, candidates and levels

- **Status:** design
- **As of:** 2026-10-22
- **Tracked in:** [FAIR data roadmap](https://linear.app/ultra-trace/project/fair-data-roadmap-0484070a43ea)
  (Linear: MAS-357 the structure lookup, MAS-363 the exports, MAS-361 the
  decision on the classic sheet; MAS-31 is the level's FAIR export) and
  [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c)
  (MAS-358 the target as a candidate, MAS-359 the claim, MAS-360 the MS/MS
  ranking, MAS-362 the decision on the targeted path; MAS-73 carries the
  engine's level); filed 2026-10-22
- **What:** Identifiers as candidates and an identification as a separate, evidenced claim with a Schymanski level: where each lives, who may state what.

## 1. Purpose

A mass spectrometer measures m/z. With mass accuracy and an isotope pattern a
formula follows, to some confidence; from a formula follows a set of compounds
that share it. Which of them was measured does not follow. MS/MS, retention
time, or a reference standard run on the same instrument narrow the set, down
to one compound in the best case, and the field states how far that went as a
level: Schymanski et al. (2014) for high-resolution MS, the Metabolomics
Standards Initiative (Sumner et al. 2007) more broadly. Mascope's confidence
design adopts that scale ([`assignment_confidence.md`](assignment_confidence.md)
section 1; decision 30 of the assignment quality plan, MAS-73).

Mascope stores chemical identifiers in two places that say different things.
A reference row says what a public list calls a formula. A target compound
says what the user's own record stands for. Neither says what a peak is, yet a
target compound's name and CAS number ride into every match of its formula as
if they did, and MAS-28 (PR #2475) adds InChIKey, InChI and SMILES to the same
spot. The peak-centric path already reads a reference hit the honest way:
[`referenceListings.js`](../../server/frontend/src/lib/referenceListings.js)
says in its own words that a formula match names candidate compounds, not an
identification, and the inspector shows it as *reference list · potential*.
The targeted path is the odd one out.

This note separates three layers, says where identifiers live, who may claim
an identification, and what a reader may state of a row. It records the
decision the owner took on 2026-10-22 while reviewing PR #2475:

> Identifiers are stored on the compound records and read as hints about what
> a formula could be. Assigning a formula to a peak claims no identity. The
> quick paths for adding a compound ask for a formula and a name; CAS and the
> structure live in the edit dialog's *Structure* group. An identification is
> a separate, evidenced claim that carries a level.

Revised by the owner later the same day, in the PR's review: the collection
dialog's *Create new* form gets the same collapsed *Structure* group, and a
spreadsheet paste takes InChIKey, InChI and SMILES as columns 4 to 6. The
add-compound popover opened from a peak still asks for a formula and a name.
A pasted or entered structure is not applied to a compound that already
exists; the row says so, and *Edit compound* stays the place that changes a
stored compound's structure.

## 2. What exists

### 2.1 Two records of identity

**Reference compounds** (`reference_compound`,
[`public_database_integration.md`](public_database_integration.md) section 3.3):
one row per compound per source, with the Hill formula, `inchikey` as the
cross-source key, `name`, `smiles`, `inchi`, the source's own id, `xrefs` (the
CAS number is `casrn` for CompTox and `cas` for custom lists) and a per-record
licence. A run reads them through
[`known.py`](../../libraries/reference/src/mascope_reference/known.py) as one
`KnownComposition` per formula carrying up to a capped number of
`KnownIdentity` records (name, source, licence, InChIKey, native id, xrefs).

**Target compounds** (`target_compound`): what users author. A name, a
formula, a CAS number and, from MAS-28, `inchikey`, `inchi` and `smiles`. An
InChI whose formula layer disagrees with the compound formula is refused, a
formula change clears the identity, and there is deliberately no foreign key
to a reference row, since every ingest replaces those rows
([`handoff_fair_phase1.md`](handoff_fair_phase1.md) section 3, decisions 1, 3
and 4). The InChIKey is the link, resolved against active reference rows when
read.

### 2.2 How each reaches a peak

**The peak-centric path.** Stage A matches the known set, targets and
reference rows alike, by formula. A winner that is a reference row gets
`source=database`, and its formula's identities ride in
`provenance.reference_identities`; a target keeps its `target_compound_id`
while the identities that share its formula ride alongside
([`engine.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py),
"target owns the FK, reference identity rides alongside"). The detail read
adds `known_compounds`, the compounds any loaded list holds for the formula.
The inspector
([`PanePeakAssign.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakAssign.vue))
renders *reference list · Urea +2*, with *potential* when the run did not
match the list itself. Every row carries a tier (assigned, candidate, below
assignability, unassigned), a fit score, a plausibility, a tie flag
(`provenance.is_tie`) and, when calibrated, P(correct). The batch ledger CSV
([`batch_export.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_export.py),
`COLUMNS`) carries the formula, ion formula, mechanism, tier, fit score and
P(correct), and no name, identifier or level.

**The targeted path.** `match_compound` and `match_ion` join a sample to a
target compound and its ions. The compound's name and CAS number show on
every match, and the *Match compounds* sheet of the batch spreadsheet carries
*Compound name*, *Compound formula*, *Match score* and, from PR #2475, *CAS
number* and *InChIKey*. No tier, no level. The human judgement here is
`match_rating` (0 to 2 with a checklist), which names no evidence and no
structure.

### 2.3 Verdicts

`assignment_verification` (per sample,
[`verification_calibration_loop.md`](verification_calibration_loop.md)
section 4.2) and `batch_peak_verification` (per anchor,
[`peak_assignment_batch_primary.md`](peak_assignment_batch_primary.md) section
6.3) record a person's verdict on an assignment: `confirmed`, `rejected` or
`unsure`; an `evidence_level` in `reference_standard`, `msms`, `orthogonal`,
`pattern`, `visual`, required when confirming; a note; a context snapshot;
who and when. Both are append-only with the live row marked, keyed to the
stable identity of what was judged: the peak (or anchor), the formula and the
mechanism. An anchor verdict overlays the members; a per-sample verdict wins
where one exists. The verification note already reads the evidence level as
"roughly the Schymanski ladder" (its section 4.1). What a verdict judges is a
formula and a mechanism. It has nowhere to name a structure.

### 2.4 The level

MAS-73 (assignment quality plan step 5.8, decision 30, taken 2026-10-14) puts
a Schymanski-style level on every committed row, derived in the tiering pass
from the facts the engine records (a second channel, a second line, a list, the
mass gate, candidate density), stored in provenance with the facts that earned
it, versioned, shown in the inspector and the ledgers, and carried in the
exports and the import contract. MAS-31 is its FAIR mirror: the level belongs
in the batch export and the deposit package. Everything the engine records is
evidence about a formula, so that level reaches 4 at best. Levels 1 to 3 are
statements about a structure, and nothing in the system can make one today.

## 3. The three layers

| Layer | What it is | Who writes it | What it carries | Level it can reach |
|---|---|---|---|---|
| **1 Composition** | a neutral formula and an ionization mechanism, with the isotopologue role | the engine (peak-centric), the matcher (targeted) | formula, ion formula, mechanism, tier, fit score, plausibility, P(correct), tie flag | 5, or 4 when the formula is unequivocal |
| **2 Candidates** | the compounds that share the formula | reference lists at ingest; the user, on a target compound | InChIKey, InChI, SMILES, CAS, name, source, licence, list tags | none: a candidate is not a claim |
| **3 Claim** | one candidate stated for one peak or anchor, with evidence | a person, through a verdict; later an MS/MS library match | the claimed InChIKey and name, the level, the evidence level, a note, who, when | 1 to 3 with a structure; 4 without |

Four rules hold the layers apart:

1. **An identifier is never a claim.** On a target compound it says what the
   target stands for; on a reference row, what a list calls the formula. Both
   are layer 2, and both are shown as candidates, never as the answer.
2. **Layer 1 alone never exceeds 4.** A target match raises nothing. A
   reference listing raises nothing. Suspect screening on exact mass and
   isotope pattern is level 4 or 5 in Schymanski's own scheme, whatever the
   list calls the hit.
3. **A claim names a structure or it does not.** Without one it confirms the
   formula reading and stays at 4. With one it is 1 to 3 by its evidence.
4. **The level a reader states** is the live claim's where there is one, else
   the engine's (MAS-73), else the formula floor.

## 4. Layer 2: candidates

### 4.1 Reference identities (built)

As in section 2.2. The SDK side, reference identities in frames, is MAS-332
([`sdk_peak_assignment.md`](sdk_peak_assignment.md)). Nothing here changes
them.

### 4.2 The target's declared structure (MAS-28)

A target compound's name, CAS and structure join the candidate set, labelled
as the target's declaration beside the reference listing: *target · Urea* and
*reference list · Urea +2 (CompTox)*. When the target declares an InChIKey a
reference row also carries, that is one candidate shown once with both
labels; when it declares one no list carries, it is a candidate the lists do
not know, and the inspector says so. The ledger's flat `reference_listing`
field gains the same reading for the target. A claim-less target match reads
*target · Urea* and a level of 4 or 5, which is what it is.

### 4.3 The compound editor

The add-compound popover opened from a peak asks for a formula and a name.
The collection dialog's *Create new* form and its spreadsheet paste take the
structure too, for a compound they create (section 1, the revision). The
*Edit compound* dialog gets a collapsed *Structure* group holding the CAS
number, InChIKey, InChI and SMILES, and a *Find structure* action: the active
reference rows whose Hill formula equals the compound's, CAS and name matches
first, each with its source and licence; picking one fills all four fields
from the row (the CAS from `xrefs`). Pasting stays possible, with the shape
and formula-layer checks MAS-28 built. The backfill script of MAS-28 is the
batch form of the same lookup, keyed on CAS. The collection tables show no
InChIKey column; the search by InChIKey stays. The handoff scoped the lookup
out of MAS-28 (its section 8); it is the half that makes the fields usable,
and the one that keeps a user from pasting an identifier for the wrong
isomer.

## 5. Layer 3: the claim

### 5.1 Definition

A claim is a confirmed verdict that names a structure: what was judged (the
peak or anchor, the formula, the mechanism), the claimed InChIKey and the name
that goes with it, the evidence level, the level that follows from it, a note,
who and when. Append-only with the live row marked, like a verdict, and keyed
the same way. Rejecting or retracting a claim is a verdict like any other.

### 5.2 Home: extend the two verification tables

**Recommendation.** Add three nullable columns to both `assignment_verification`
and `batch_peak_verification`: `claimed_inchikey` (`String(27)`, indexed),
`claimed_name` (`Text`) and `level` (a small integer, 1 to 5). Nothing else
moves.

Why there and not a new table: a claim *is* a verdict with a structure. The
stable identity key, the append-only semantics with the live row marked, the
overlay precedence (anchor over members, per-sample where one exists), the
capture popovers, the badge, the ledger filter and the SDK readers all exist
for verdicts and would all have to be duplicated, with a second precedence
rule between a verdict and a claim on the same row, for a separate table. The
calibration label pool is untouched: it selects on verdict and the score
snapshot and ignores a structure column.

Why the level is stored and not derived at read: a claim is a person's
statement and must read the same in five years. The engine's level (MAS-73)
is recomputed per run under a versioned mapping; a claim's is fixed at the
moment it is made, with the mapping version in `context`.

Constraints, in the schema and the pydantic layer: `level` 1 to 3 requires
`claimed_inchikey`; `claimed_inchikey` requires `verdict = 'confirmed'`; the
InChIKey has MAS-28's shape. When the InChIKey resolves to a candidate of the
row (a reference identity or the target's declaration) its formula agreement
is already known; a free InChIKey no candidate carries is accepted with its
name and shown as *unlisted*.

**Call for the plan owner:** a separate `identification_claim` table is the
right answer only if a claim must exist without a verdict, which is the case
if an MS/MS engine is to *propose* a structure without *confirming* it (5.5).
The recommendation covers that with `verified_by` null and the engine named
in `context`, as an imported run names its engine today.

### 5.3 The engine's level and the floor

The engine's level is MAS-73's and stays formula-level: 4 when the row is
assigned and its formula is unequivocal (`is_tie` false, plus whatever the
tier reasons add under MAS-73's mapping), else 5. The classic matcher has no
tier and no tie flag; its rows have no level today.

**Call for the plan owner:** whether the classic path derives a level of its
own (5, or 4 where the isotope pattern matched) for the *Match compounds*
sheet, or whether that sheet waits for the fold of targeted matching into the
peak-centric path (MAS-162, MAS-80). The recommendation is to wait: a second
derivation for a path being retired is work that outlives nothing.

### 5.4 Mapping evidence to a level

| `evidence_level` | structure named | `level` | Schymanski 2014 |
|---|---|---|---|
| `reference_standard` | required | 1 | confirmed structure: standard matched on MS, MS/MS and retention |
| `msms` | required | 2 | probable structure: 2a a library spectrum match, 2b diagnostic evidence |
| `orthogonal` | required | 3 | tentative candidate: retention, in-source fragments, class evidence |
| `pattern` | no | 4 | unequivocal formula |
| `visual` | no | the engine's | no raise |
| any, no structure named | - | the engine's, at most 4 | - |

`reference_standard`, `msms` and `orthogonal` are statements about a
structure, so a confirmed verdict with one of them and no structure is refused
once the columns exist. The capture popover pre-fills the structure from the
row's candidates, the target's declaration first, then a single reference
identity, so the requirement costs one look rather than a paste. Rows recorded
before the columns exist keep their evidence level and read as level 4, with
no structure.

### 5.5 MS/MS as evidence

Phase 4 of the public database plan (a `reference_spectrum` table and a
spectral-similarity scorer over the extracted MS/MS centroids) is where
formula narrows to structure by machine. Its natural output under this note is
a ranking of the row's candidates: the reference identities and the target's
declaration whose InChIKey the library holds spectra for, each with a
similarity. A match above a threshold is a level-2 claim with `msms`
evidence, `verified_by` null and the scorer named in `context`; a person
confirms or rejects it as any verdict.

**Call for the plan owner:** whether the scorer may write a level-2 claim on
its own, or only rank the candidates and leave the claim to a person. The
recommendation is rank-only until the scorer has been measured on the decoy
harness the confidence design uses for every layer
([`assignment_confidence.md`](assignment_confidence.md) section 3, principle
3), then promote. Without MS/MS the candidates stay unordered beyond "the run
matched this one".

### 5.6 The targeted path

The targeted path has no verdict table, only `match_rating`. A claim on a
match ion would need a key the verification tables can use (an observed peak
id), which the classic match records do not carry. The recommendation is not
to build one: the targeted path is being folded into the peak-centric one
(MAS-162 retires the Targets/Assignments switch, MAS-80 the rest), and once a
target match is a ledger row with a tier, it takes claims like any other row.
Until then the targeted surfaces show the target's declaration as what it is
(4.2) and make no claim.

## 6. Surfaces

### 6.1 Inspector and ledgers

The identity block of the inspector grows one line: *target · Urea* above or
beside *reference list · Urea +2*, with the tooltip saying which is which.
The verdict badge grows the structure and the level where a claim is live:
*confirmed · level 1 · Urea (reference standard)*. The ledgers' level column
(MAS-73) reads the claim's level where one is live, the engine's otherwise,
and the tooltip says which. The batch ledger does the same off the anchor's
verdict.

### 6.2 Exports

The batch ledger CSV appends `level`, `level_source` (`claim` or `engine`),
`claimed_inchikey` and `claimed_name` after its Phase 0 columns. The *Match
compounds* sheet keeps *CAS number* and *InChIKey* (they are the target's
declaration, on a sheet that already names the compound) and gains a level
column when 5.3's call is taken.

The standard shape for processed results is mzTab-M 2.0 (MAS-32), and its
tables are the three layers of this note: the small-molecule summary (SML)
carries `chemical_formula`, `smiles`, `inchi`, `chemical_name`,
`database_identifier` and `reliability`, the small-molecule feature table
(SMF) the measured features, and the small-molecule evidence table (SME) one
row per piece of evidence with `identification_method`, `ms_level`, `rank` and
its own identifiers. Under this note an SML row carries the claimed structure's
identifiers and the claim's level as `reliability` where a claim is live, else
the formula and the engine's level with the structure columns empty; the
candidates go to SME rows, ranked, which is what SME exists for. The
`reliability` column is the MSI level, which is why identifiers and a level
belong side by side in every export and never one without the other.

### 6.3 The package

The deposit package (MAS-33) carries what 6.2 writes. Nothing further.

## 7. What this means for PR #2475

Kept as built: the columns and their migration (MAS-24), the API fields and
their validation, the InChI-versus-formula check in the controllers, the
fill-on-create for SDK callers, the backfill script, the SDK, the two export
columns.

Changed before merge:

- The three identity inputs leave the add-compound popover. They go to the
  *Edit compound* dialog, in a collapsed *Structure* group, and the CAS
  number moves there with them. The popover asks for a formula and a name.
  The collection dialog's *Create new* form has the same collapsed group, and
  its spreadsheet paste takes the three as columns 4 to 6 (section 1, the
  revision).
- The InChIKey column leaves the collection dialog's tables; the search by
  InChIKey stays.
- The user docs say a structure is what the target stands for, and that a
  formula match does not confirm it.

Everything else in this note is follow-up work, filed as section 9 proposes.

## 8. Decisions for the plan owner

1. **The claim's home** (5.2): extend the verification tables, or a table of
   its own. Recommended: extend. Answered by starting MAS-359.
2. **A level for the classic sheet** (5.3): derive one, or wait for the fold.
   Recommended: wait. Linear MAS-361.
3. **The MS/MS scorer's authority** (5.5): write level-2 claims, or rank only.
   Recommended: rank only until measured. Answered by starting MAS-360.
4. **Claims on the targeted path** (5.6): build a key for them, or wait for
   the fold. Recommended: wait. Linear MAS-362.

## 9. Proposed issues

Filed in Linear on 2026-10-22, each a self-contained brief in the MAS
convention (Context, Task, Where, Verify, Size); the summaries below are the
short form.

**A. Find structure in the compound editor** (MAS-357; FAIR data roadmap,
after MAS-28). Context: MAS-28 gave target compounds a structure and three paste
fields; the handoff scoped the lookup out. Task: in
`DialogTargetCompoundUpdate.vue` a collapsed *Structure* group (CAS, InChIKey,
InChI, SMILES) with a *Find structure* action backed by a new route that lists
active reference rows by Hill formula (`mascope_reference.query.active_join`,
CAS and name matches first, source and licence shown); picking one fills the
four fields. Verify: a compound with a CAS number and no structure gets its
InChIKey from the lookup in two clicks; an InChI of another formula is still
refused; the quick paths send no identity. Size: M.

**B. The target's declaration as a candidate** (MAS-358; Assignment quality
plan, beside MAS-73). Context: section 4.2. Task: the detail read and the ledger's
`reference_listing` carry the target's name and InChIKey as a candidate
labelled `target`; `referenceListings.js` and the inspector render it beside
the reference listing, merged with a reference identity of the same InChIKey.
Verify: a target match reads *target · name*, a reference-only match reads as
today, a target whose InChIKey a list carries shows once. Size: S.

**C. Claims on the verification tables** (MAS-359; Assignment quality plan,
beside MAS-73). Context: sections 5.1, 5.2
and 5.4. Task: one migration adding `claimed_inchikey`, `claimed_name` and
`level` to `assignment_verification` and `batch_peak_verification`; the
schemas in `peak_assignments/schemas.py` and the two services
(`service.py`, `batch_peak_verification.py`) take and return them, with the
constraints of 5.2 and the mapping of 5.4 versioned in `context`; the
capture popovers (`AssignmentVerdictPopover.vue`, `BatchPeakVerdictPopover.vue`,
`PanePeakAssign.vue`) offer the row's candidates and pre-fill the first; the
badge shows structure and level; the SDK readers (`batch_peaks.py`,
`peak_assignments.py`) return the new fields. Verify: a reference-standard
confirmation without a structure is refused; a level-1 claim reads back with
its InChIKey from both tables; legacy rows read as level 4; the calibration
pool's count is unchanged. Size: M.

**D. Level and claim in the exports** (MAS-363; FAIR data roadmap, extends
MAS-31; blocked by MAS-359, MAS-73 and MAS-361).
Context: section 6.2. Task: `batch_export.py` appends `level`, `level_source`,
`claimed_inchikey`, `claimed_name`; the batch spreadsheet's Provenance sheet
names the mapping version; the *Match compounds* sheet is left as decision 2
says. Verify: the traceability test reads the four columns back; a claimed row
exports its claim's level, an unclaimed one the engine's. Size: S.

**E. MS/MS candidate ranking** (MAS-360; in the Assignment quality plan until
the public-database phase 4 project, which the owner has not opened, exists). Context: section 5.5. Task: the scorer ranks the row's
candidates by spectral similarity and writes the ranking to provenance; a
claim is written only if decision 3 says so. Verify: on the decoy harness the
true structure ranks first at a measured rate. Size: L.

Decisions 2 and 4 are MAS-361 (FAIR data roadmap, since it blocks D) and
MAS-362 (Assignment quality plan), labelled *Decision*; decisions 1 and 3 are
answered by starting MAS-359 and MAS-360.

## References

- Schymanski, E. L., Jeon, J., Gulde, R., Fenner, K., Ruff, M., Singer, H. P.,
  Hollender, J. (2014). Identifying small molecules via high resolution mass
  spectrometry: communicating confidence. *Environmental Science & Technology*
  48(4), 2097-2098. <https://doi.org/10.1021/es5002105>
- Sumner, L. W. et al. (2007). Proposed minimum reporting standards for
  chemical analysis. *Metabolomics* 3, 211-221.
  <https://doi.org/10.1007/s11306-007-0082-2>
- Hoffmann, N. et al. (2019). mzTab-M: a data standard for sharing
  quantitative results in mass spectrometry metabolomics. *Analytical
  Chemistry* 91(5), 3302-3310. <https://doi.org/10.1021/acs.analchem.8b04310>;
  the specification:
  <https://hupo-psi.github.io/mzTab/2_0-metabolomics-release/mzTab_format_specification_2_0-M_release.html>
- Wilkinson, M. D. et al. (2016). The FAIR Guiding Principles for scientific
  data management and stewardship. *Scientific Data* 3, 160018.
  <https://doi.org/10.1038/sdata.2016.18>
- Internal: [`assignment_confidence.md`](assignment_confidence.md) (the layered
  confidence design and P4), [`assignment_quality_plan.md`](assignment_quality_plan.md)
  step 5.8 and decision 30, [`verification_calibration_loop.md`](verification_calibration_loop.md)
  section 4, [`peak_assignment_batch_primary.md`](peak_assignment_batch_primary.md)
  section 6.3, [`public_database_integration.md`](public_database_integration.md)
  sections 3.3, 3.5 and 3.6, [`handoff_fair_phase1.md`](handoff_fair_phase1.md)
  sections 3 and 8, [`fair_roadmap.md`](fair_roadmap.md) items 9 and 12.
