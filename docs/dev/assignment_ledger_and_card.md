# The assignment ledger and the inspector card

- **Status:** design
- **As of:** 2026-11-20
- **Tracked in:** [Assignment ledger and inspector card](https://linear.app/ultra-trace/project/assignment-ledger-and-inspector-card-8caa997cc3da)
  (Linear, P-MAS-23: milestone 1 "One ledger", MAS-1062 to MAS-1065 and MAS-1069;
  milestone 2 "The card", MAS-1066 to MAS-1068)
- **What:** One ledger for the sample and the batch, one vocabulary of marks for every
  reading, a way to see the rows a multi-row reading spans, and an inspector card that
  answers "is this right, and what do I do" before it explains.

*The peak-assignment views grew one step at a time: each step of the assignment quality
plan added a column, a glyph or a section where its reading would show. This note reviews
what that left and proposes the overhaul. It changes how assignments are shown and judged,
never what the engine writes.*

## 1. Where it stands

Three components carry the assignment UI:
[`PaneBrowserAssignment.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue)
(the sample ledger, 1,941 lines),
[`PaneBrowserBatchPeaks.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue)
(the batch ledger, 2,442) and
[`PanePeakAssign.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakAssign.vue)
(the inspector card, 3,262). They were reviewed on 2026-11-20 on a running instance with
peak assignment on, a nine-sample nitrate CIMS batch of 1,024 batch peaks, and against
the code.

1. **Two ledgers, built twice.** Both show a formula at an m/z with a tier, and they
   disagree in about twenty details:
   - header case;
   - the formula font, monospace in one and body text in the other;
   - an empty formula, "—" against "unassigned";
   - isotopologue labels, `[13C]` against `M+1`;
   - the hand mark: inside the tier chip in the accent in one, after the formula and muted
     in the other;
   - the tier chips: multi-select in one; single-select in the other, bound to a filter
     menu that lists raw keys such as `below_assignability`;
   - verdict sorting, which only one has;
   - error handling.

   The listing column, the tier strip, the view menu, the sort helpers and the two verdict
   popovers (about 80 % the same) are copies.
2. **Every batch step added a column or a glyph.** The batch ledger reaches 14 columns.
   Nine glyphs at 0.8 opacity share the formula cell, whose meaning lives in tooltips:
   series, stamp, scissors, known species, blend, flat series, persistence, water ladder
   and the hand. The two ledgers hold about 50 kinds of mark and 32 icons between them.
   Several icons carry three or four meanings:
   - the seal-check is the assigned tier, the verdict header, the unjudged verdict and a
     batch step;
   - the clock is the history banner, a stale ratio grade and "judge again";
   - the flask is the listing column and the reagent role;
   - the hand is manual, curated, an empty state and "use this";
   - the wave is the artifact role and co-variation.

   In the card a dashed border means seven different things.
3. **Rows carry chrome instead of content.** Every row has:
   - a chart toggle or checkbox;
   - a jump icon;
   - an empty verdict seal;
   - dashes in empty cells.

   Isotopologue lines repeat the whole row, checkbox, role tag, sample count and verdict
   included. Intensities read `6.751E2`. The batch ledger's default sort, by samples, puts
   a screen of identical "9" rows on top.
4. **Useful controls hide in a cog and reset.** The cog holds the verdict filter,
   grouping by formula, the stripped, blend and flat-series filters, the engine tier and
   the CSV export. Every toggle except the listing's resets when the pane swaps between
   sample and batch. The ledger repeats the shell's breadcrumb, truncated.
5. **The card answers its own question last.** It is up to 21 blocks stacked; in its
   column of about 300 px that is three screens:
   - eight uppercase numbers, four of them percentages that read alike (fit, plausibility,
     evidence, confidence);
   - six batch lines;
   - "Why this tier" as the server's full sentences;
   - same-ion readings;
   - the identification level and its rivals;
   - an isotopologue table that clips its columns and scrolls sideways;
   - manual notes of 50 to 65 words;
   - the verify buttons, in the middle of the card;
   - alternatives, whose "use this" shows only on hover.
6. **The card misses states.**
   - Viewing an earlier run shows no notice, and verify and curate stay live.
   - A decoy run is not marked.
   - The viewer role is offered controls until a 403 arrives.
   - Sections pop in as the detail loads.
   - The batch's series, ratio stability, known species and blends reach the batch ledger
     but not the card.
7. **The design system drifts.** The card alone sets 14 font sizes and 9 opacities, the
   reagent purple is hard-coded, and a synthesised 600 weight is used. All are things
   [`frontend_design_system.md`](frontend_design_system.md) already rules on.

## 2. The design in one paragraph

One ledger component serves both scopes, *This sample* and *Whole batch*, the scope
switch MAS-713 decided. Where a concept is the same in both scopes, it has the same
column, mark and words. Its filters are in the open and remembered. Every reading is one
mark from one shared vocabulary, in which colour says what the reading does to confidence
and the glyph says which reading it is. Some readings span several rows: a homologous
series, the channels of one neutral, a co-variation partner, a pair from the batch
checks, an isotopologue family. Each of these opens a lens on its rows, and *Group by*
shows them all as groups. The card leads with the answer and the reason for its tier,
draws the evidence against the run's own bands, keeps the server's prose one click away,
folds its sections, and pins the verdict to its foot.

## 3. The ledger

### 3.1 One component, two scopes

A shared `AssignmentLedger` component and its composables live in `lib/ledger/`: sort,
filters, listing, the tier strip, the verdict cell and one verdict popover with a scope
prop. The two panes become scope configurations of it. The differences of section 1.1
converge on one answer each:

- sentence-case headers;
- formulas in Plex Mono;
- a blank empty cell;
- an isotopologue named by its substitution (`[13C]`, `[34S]`) wherever that is known,
  `M+n` only where it is not;
- one empty state (`BaseEmptyState`) and one inline error.

The ledger's own breadcrumb goes, because the shell's says the same.

### 3.2 Columns

| Column | This sample | Whole batch | Cell |
|---|---|---|---|
| Plot | yes | yes | a checkbox; it plots the species in the batch chart, the same in both scopes |
| m/z | yes | yes | Plex Mono, tabular |
| Compound | yes | yes | the formula, with the listed name and its list under it when a list or target names it. This replaces the *Listed as* column |
| Ion | yes | no | the mechanism, with a `×N` pill when the neutral is seen through N channels |
| Intensity | yes | yes | a compact number (`8.31k`, `1.20M`) and a log-scale bar |
| Tier | yes | yes | the tier chip; a capping reason as a mark on the chip |
| Level | when any row has one | when any row has one | the level chip |
| Samples | no | yes | `n/N` |
| Across the batch | no | yes | up to three badges (section 5), holding-back badges first, then `+n` |
| Verdict | yes | yes | an icon only once someone has judged |

Imported engine tiers and levels show in the Tier and Level cells as a second, outlined
chip when *Show › Engine readings* is on. They do not get columns of their own.

### 3.3 Rows

- A row is 40 px; an isotopologue line is a 28 px sub-row under its parent. The sub-row
  shows the line's label, m/z and intensity; it inherits the rest and repeats none of it.
  A caret with the line count folds and unfolds a family.
- Row actions show on hover and on the selected row only: open the brightest sample in
  the batch scope, curate in the sample scope.
- Clicking an m/z focuses the peak in the inspector, the same in both scopes. Plotting is
  the checkbox.
- The default sort is intensity, descending, in both scopes. A sort the user picked is
  remembered per scope, as `ledgerSort.js` does now.
- Narrow columns drop cells in order, by container query: Level, then Samples, then
  Across the batch, then Intensity's bar.

### 3.4 The filter bar

- A search box: a formula, a listed name, or an m/z within the run's tolerance.
- The tier bar of the design system's ledger strip: a 7 px proportion bar over the tier
  chips with their counts.
  - It is multi-select in both scopes.
  - A chip with nothing in it is hidden.
  - The roles (reagent, artifact) sit apart from the tiers.
  - The sample scope keeps its *to verify* queue chip at the right.
- **Show**, a menu in place of the cog:
  - row kinds: isotopologue lines, roles, stripped rows;
  - the batch's flags: blends, flat series, one badge kind;
  - engine readings;
  - **Group by**: none, compound, series (3.5).
- **…**, an overflow menu: export the ledger as CSV, and write listed names in full.

Every choice persists per scope, in the pattern of `ledgerSort.js` and `ledgerListing.js`.

### 3.5 Groups: the rows a reading spans

Several readings are about more than one row. The user has to be able to see those rows
together, not read about them in a tooltip.

| Group | Its rows | Where it is derived |
|---|---|---|
| Channels of one neutral | the batch peaks, or the sample's rows, that read one neutral formula through different mechanisms | the batch scope: same consensus neutral formula, different mechanism. The sample scope: the rows `channelRows` already groups |
| Homologous series | the root and every row whose formula is the root's plus or minus whole repeat units, through the same mechanism | the series record's `root_batch_peak_id`, `unit` and `root_formula`, plus formula arithmetic over the rows the ledger holds |
| Co-variation partner | the batch peaks a co-variation record pairs | the record's partners |
| Batch-check pair | the 14N twin, the halogen line, the urea pair | the check's record |
| Isotopologue family | the main peak and its lines | the fold the ledger already does |

Both ledgers hold all of their rows in the browser (the sample ledger pages until
complete), so every group is derived client-side. No new ledger endpoint is needed.

**The lens.** Any badge or pill that names other rows applies a lens when clicked:

- the series badge;
- the channel pill (`×N`);
- the co-variation and batch-check badges;
- the isotopologue caret.

Under the lens:

- The ledger narrows to the group's rows, in the group's own order. A bar above the table
  names the group ("CH2 series from C8H13NO9, through [M+NO3]⁻, 4 batch peaks, one step
  with no peak"), offers *Plot the group*, and closes with ×.
- A Step or Channel column labels each row: root, `+1 CH2`, `−1 CH2`, or the ion.
- A series step that has no batch peak stays in the list, greyed, so the gap shows.

**Group by** shows every group at once, as collapsible group lines over their members. A
group line names the compound or series and the member count, and shows the tier of its
best member.

**From the card.** The card's reasons and its *Across the batch* section list the same
members, with m/z links that focus each row and *Show in the ledger*, which applies the
lens.

## 4. The card

### 4.1 Layout

1. **The answer.** The listed name and its list, if any; the formula, large; the ion line
   ("[M+HCOO]⁻ → C3H3O4⁻ · m/z 103.0035 · M0"); and one chip row with the tier, the level
   and the verdict. The provisional marker becomes a small caption ("tier rules
   provisional"), not a chip.
2. **Why the tier.** A box titled for the tier ("Why below", "Why assigned"). It holds:
   - the evidence (fit × plausibility) drawn against the run's own bands (`tier_bands`;
     45 and 75 by default);
   - the product line, "fit 30 % × plausibility 100 %";
   - one line per reason: the rule's label from `lib/tierReasons.js`, with the deciding
     number or formula inline. A reason expands to the server's sentence. Holding-back
     reasons come first, in the warning colour. Supporting reasons ("Seen through 2
     channels", "In the batch's O series") are in the support colour and list their rows
     (3.5).
3. **The measurements.** Four tiles, each a value with a caption in words:
   - the mass error, in ppm and in widths from the run's centre;
   - the isotope lines, as found of predicted, with the abundance error;
   - the confidence, with the tie flag spelled out;
   - the batch: the samples the peak is in, and how many batch readings it has.
4. **The sections, folded by default.** Each shows a summary on its folded line:
   - **Isotope pattern**: a stick plot of the predicted lines against the measured ones,
     the absent and untested lines drawn hollow, and a four-column list under it;
   - **Alternatives**: rivals with fit, ppm and kind, and a visible *Use*;
   - **Across the batch**: the shared badges of section 5, each with its line and its
     group;
   - **Identification level**: the level, why, what would lift it, and the rivals.
5. **The verdict bar, pinned at the foot.** *Confirm*, *Reject* and *Unsure*, or the
   recorded verdict with *Change*. Under it, *Find other formulas for this peak*, today's
   *Find more*, renamed to say what it does.

Manual and batch-pin notes shrink to one line with their action ("Assigned by hand in
place of C3H4O4 · Undo"); their full sentence is the tooltip.

### 4.2 States

- **Viewing an earlier run:** a read-only notice under the answer, the verdict bar
  disabled with its reason, *Use* hidden.
- **A decoy run:** a notice naming the arm. The decoy run selector already keeps decoys
  out unless asked.
- **The viewer role:** checked before offering, with `canEditWorkspace` as the ledger does,
  not discovered from a 403.
- **Loading:** skeleton lines in each section until the detail arrives.
- **A focused peak with no ledger row:** the answer area says so, and *Find other
  formulas* is offered.

### 4.3 What the card needs from the server

The sample detail row (`GET /peak-assignments/sample/{id}/assignment/{paid}`) already
serves the batch's isotopologue stamp, co-variation, batch checks, persistence and flat
series. It adds:

- the series record and its root;
- the ratio-stability grade;
- the known-species and water-ladder decisions;
- the blend flag;
- each group's members (batch peak id, formula, m/z, tier), so the card can list rows
  the sample scope has not loaded.

MAS-983 turns batch decisions into versioned records. If it has merged when this is
built, the detail reads those records.

## 5. One mark, one meaning

**Colour says what a reading does to confidence:**

- **teal** (`--state-info`) supports it;
- **amber** (`--state-warning`) holds it back;
- **grey** is read and moves nothing;
- **a white outline** is a person's act.

**The glyph says which reading it is.** One registry, `lib/evidence/marks.js`, holds each
reading's id, glyph, tone rule, label and tooltip. One component,
`BaseEvidenceBadge.vue`, draws it, the same in the ledger and in the card.

| Reading | Glyph | Tone |
|---|---|---|
| Homologous series | stairs | supports |
| Channels co-vary | wave | supports |
| Isotope lines stamped | three bars | supports |
| Ratio stable | pulse | supports; holds back when it drifts or is unstable |
| Batch facts | check | supports; holds back when a fact refutes |
| Persistence only | repeat | holds back |
| Probable blend | two circles | holds back |
| Flat series | equals | moves nothing |
| Reagent decided (water ladder, known species) | drop | moves nothing |
| Changed by hand | hand | a person's act |

**Tiers and the other chips:**

- The tier chips keep their colours and lose their glyphs for a dot. Filled is assigned,
  half is candidate, a ring is below, a dashed ring is unassigned. That frees the
  seal-check, half circle and minus circle.
- The reagent and artifact roles take the `--role-reagent` token the design-system note
  names.
- The level keeps its three tokens.
- A verdict shows nothing until someone judges. A batch-level verdict reaching a sample
  is the same icon, dashed.

**Single meanings, enforced:**

- A dashed border means *not yet known*, and nothing else.
- The clock means history.
- The hand means a person changed this.
- The wave means co-variation.
- The flask disappears: the listed name is written out instead.

The design-system note's section 8 gains this grammar, and the ledger's tier help card
gains its legend.

## 6. What does not change

- **The tier vocabulary and what moves a tier.** Assigned, candidate, below assignability
  and unassigned. Decisions 18, 25, 35 to 37 of the assignment quality plan hold; this
  note only shows them.
- **The identification level's scale and words** (decision 38).
- **P(correct) stays hidden** while its curve is provisional.
- **The verification and curation semantics and their endpoints.** One verdict per
  family on its M0; a per-sample verdict wins over a batch-level one; curation keeps its
  undo.
- **The bench card.** Nothing here changes what the engine writes.

## 7. Calls for the plan owner

1. **The reagent role's name.** The mockup says *source ion*. The word is one of the
   naming decisions in the peak-assignment glossary kept with Peaky's author. The build
   keeps *reagent* until that decision is taken.
2. **The default sort:** intensity, descending, in both scopes. The alternative is tier,
   which the sample ledger uses now.
3. **Engine readings in the Tier and Level cells.** They become a second chip behind
   *Show › Engine readings*, not columns. The alternative keeps today's columns.

Each is built as written here and raised on its PR.

## 8. Docs

- [`peak-assignment.md`](../user/how-it-works/peak-assignment.md): the ledger and card
  sections, the batch ledger's columns, and a new *Seeing a group* section. Each issue
  updates the part it changes.
- `_help/assignment-tiers.md`, `_help/batch-peaks.md` and `_help/assignment-evidence.md`
  match the new layout. The tier help card carries the mark legend.
- [`frontend_design_system.md`](frontend_design_system.md) section 8: the mark grammar
  of section 5.
- [`peak_assignment_frontend.md`](peak_assignment_frontend.md): its ledger and inspector
  paragraphs point here once the steps land.

## 9. Steps

The Linear project *Assignment ledger and inspector card* (P-MAS-23). The `blockedBy`
relations hold the order.

| Issue | Milestone | Step | Blocked by | Size |
|---|---|---|---|---|
| MAS-1062 | 1 · One ledger | One ledger component for both scopes; the differences converge (3.1) | none | L |
| MAS-1063 | 1 · One ledger | One mark, one meaning: the evidence badge and its registry (5) | none | M |
| MAS-1064 | 1 · One ledger | The columns and rows (3.2, 3.3) | MAS-1062, MAS-1063 | M |
| MAS-1065 | 1 · One ledger | The filter bar (3.4) | MAS-1062 | M |
| MAS-1069 | 1 · One ledger | Groups: the lens and Group by (3.5) | MAS-1064, MAS-1065, MAS-1067 | M |
| MAS-1066 | 2 · The card | The card's batch readings and group members on the detail row (4.3) | none | M |
| MAS-1067 | 2 · The card | The card: answer, why, measurements, sections, verdict bar (4.1) | MAS-1063, MAS-1066 | L |
| MAS-1068 | 2 · The card | The card's states (4.2) | MAS-1067 | S |

MAS-1062, MAS-1063 and MAS-1066 can start at once. Groups come last because they join
the ledger and the card. The issues relate to MAS-713 (the ledger as the first content
column), MAS-1015 and MAS-1016 (the batch control in the ledger's header), MAS-160 (the
type scale), MAS-153 (the chip legend), MAS-73 (the identification level) and MAS-983 (the
batch's versioned records); none of those blocks them.
