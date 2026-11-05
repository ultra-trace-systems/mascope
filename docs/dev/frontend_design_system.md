`ULTRA TRACE MASCOPE - DESIGN DOC - FRONTEND DESIGN SYSTEM`

# Frontend design system

- **Status:** building
- **As of:** 2026-11-05
- **Tracked in:** [Dashboard UX improvements](https://linear.app/ultra-trace/project/dashboard-ux-improvements-435aa9db35a9)
  (Linear: MAS-160 the type scale and density, MAS-156 the dialog grammar,
  MAS-153 the chip legend, MAS-712 and MAS-713 the sidebar and main area that
  draw the shell components of section 9; MAS-163 is the decision that asked
  for this note)
- **What:** The frontend's visual vocabulary on one page: type, density, colour
  semantics, buttons, dialogs, empty states and feedback, chips and badges, and
  the shell components, with what exists and what each PR implements against.

Status: **written 2026-11-05** from the code on `develop` and the October UX
review ([`ux_review_2026_10.md`](ux_review_2026_10.md), decision D3). Where
the code already does the right thing, this note names the file so a PR copies
it; where it does not, the note says what to converge on and which issue does
the converging. A PR that takes a visual decision this page does not cover adds
the line here in the same PR. Precedence: the brand guidelines, then this note,
then PrimeVue Aura's defaults.

## Picking this up

The frontend is Vue 3 and PrimeVue 4 with the Aura preset, themed in
[`theme.js`](../../server/frontend/src/theme.js) from the four brand ramps in
[`palette.json`](../../server/frontend/src/palette.json), with the overrides in
[`style.css`](../../server/frontend/src/style.css) (all in `@layer override`).
Reusable pieces live in
[`lib/base/`](../../server/frontend/src/lib/base/). Most of what follows is a
rule and the one file that already keeps it.

## 1. Type

**Faces.** IBM Plex Sans for everything; IBM Plex Mono for what a chemist reads
as a value: formulas, m/z, ids, counts in badges, index labels. Both are
self-hosted through `@fontsource` imports in
[`main.js`](../../server/frontend/src/main.js); nothing loads from a CDN. Use
`var(--font-mono)` (declared on `:root` in `style.css`), never a literal
`monospace` or `'IBM Plex Mono'` in a component. Columns of numbers set
`font-variant-numeric: tabular-nums`, as the ledger panes already do.

**Scale.** The base is `13px` on `html, body`. The code has about forty
distinct font sizes today (`0.85rem` and `0.8rem` most often, then `smaller`,
`13px`, `0.72rem`, `11px`; `PanePeakAssign.vue` alone has twenty-five
declarations). MAS-160 replaces them with four tokens on `:root`:

| Token | Size | For |
|---|---|---|
| `--text-lg` | `1.15rem` (15px) | dialog and pane titles, the inspector's compound line |
| `--text-base` | `1rem` (13px) | everything with no other reason |
| `--text-sm` | `0.85rem` (11px) | tags, chips, table cells in a dense pane, captions |
| `--text-xs` | `0.72rem` (9.5px) | axis labels and section eyebrows only, never running text |

`font-size: small` and `smaller` are not used: they compound through nesting
(the filter bar's `.filters :deep(*)` is finding F13). Headings inside a pane
are weight, not size: `600` for a title, `500` for a label, `400` for text.

## 2. Density

One setting, *Comfortable* or *Compact*, in the user's settings pane, stored
beside dark mode and set as a `data-density` attribute on `html` (MAS-160). It
drives PrimeVue's `size` for tables and inputs and the row height together;
the type scale stays the same in both. Chart heights never encode the density:
they come from the flex layout and a `ResizeObserver` (S6, MAS-317), so the
toolbar height is the only hand-set number in the shell.

## 3. Colour

**Brand.** The accent is Safety Orange, `primary` in `theme.js`. On the light
ground the preset reads one rung darker than Aura's default (`primary.600`,
the guidelines' Orange Deep) so a button label clears WCAG AA; on the dark
ground it reads the damped ramp `safetyorangedim` so a whole interface of
accent is not tiring. The comments in `theme.js` carry the reasoning; do not
pick an orange by hand. The accent means *this is Mascope* and *this is the
one primary action*; it never means a state.

**Grounds.** Off-white (`offwhite` ramp) in light, charcoal (`charcoal` ramp)
in dark, through PrimeVue's `surface` tokens. Dark mode is the `darkmode`
class on `html`, from
[`stores/ui/darkmode.js`](../../server/frontend/src/stores/ui/darkmode.js);
every colour must read on both grounds, and a chart colour is checked on both
(M5, MAS-159).

**States.** Four tokens in `style.css`, darkened on light so they can carry
text, none of them orange:

| Token | Light | Dark | Means |
|---|---|---|---|
| `--state-success` | `#1f7a46` | `#3fa96b` | assigned, confirmed, calibration ok |
| `--state-warning` | `#8a6200` | `#e9b33b` | candidate, unsure, calibration poor or accepted with a warning |
| `--state-error` | `#b0281a` | `#e2503f` | rejected, failed, a destructive action |
| `--state-info` | `#0b7e8e` | `#16b0c4` | drifted, an engine or provenance note |

For text and icons use the `--state-*` token; for a PrimeVue `Tag`, `Button`
or `Message` use the matching severity (`success`, `warn`, `danger`, `info`,
`secondary` for neutral). Neutral is `--p-text-muted-color`, dashed or at
reduced opacity for *not yet known* (the unassigned tier). The same word always
takes the same colour across tiers
([`lib/tiers.js`](../../server/frontend/src/lib/tiers.js)), verdicts
([`lib/verification.js`](../../server/frontend/src/lib/verification.js)) and
calibration badges (`.browser .calibration-*` in `style.css`). Two corrections
are owed: `BaseMatchTag` maps the probable match to `danger`, so the best
matches read as alarms (QW6, MAS-153); and the reagent and artifact role in
`BaseTierTag` is a hard-coded purple `#8a5ed0`, which becomes a `--role-reagent`
token beside the state tokens.

**Token hygiene.** PrimeVue 3 names (`--text-color`, `--surface-border`) are
defined nowhere and always fall through to their fallback; component tokens
such as `--p-message-warn-color` exist only once a component of that kind has
mounted. Use `--p-text-color`, `--p-text-muted-color`,
`--p-content-border-color`, the `--state-*` tokens, and the `--p-panel-*` and
`--p-dialog-*` remaps `style.css` already makes.

## 4. Spacing and shape

Siblings are laid out with flex or grid and `gap`, not margins: `.row` and
`.col` in `style.css` give `0.5rem`, `.fields` gives `1rem` between form
fields, and `lib/base` uses `0.3rem` inside a tag or breadcrumb, `0.5rem`
between a header's controls. Radii: controls take Aura's; a pill (chip, count
badge, update banner) is `999px`; an inline mark is `3px`. Pane headers share
one height through a token (`--header-h`, replacing the `25.15px` literal in
`BaseTabbedPanel.vue` and `BaseBreadcrumb.vue`). A fixed pixel width belongs
to an icon rail or a splitter default, never to a table column that holds
text.

## 5. Buttons

| Role | Recipe | Where it is already right |
|---|---|---|
| The one action that completes a surface (Create, Save, Apply, Refresh) | filled primary, a verb that names the result, no icon | `DialogDatasetOp.vue` footer |
| A pane or row action | `text severity="secondary" size="small"`, Phosphor icon, label | `BaseTabbedPanel.vue`, `BaseBreadcrumb.vue` |
| Destructive | `severity="danger"`, in a confirm dialog whose accept button repeats the verb | the confirm dialogs' `rejectProps` / `acceptProps` |
| A link in text | `link` | the help cards |

An icon-only button always has `label`, `aria-label` and a tooltip, and the
label is shown whenever there is room for it: the create buttons hide their
label with `hiddenlabel` only once the list has rows and show it on the empty
state (QW4, as `DatasetTable.vue` does). One filled primary per surface;
*Logout* is not one and moves to the settings pane (M2). Icons are Phosphor
(`pi ph ph-*`); the remaining PrimeIcons (`pi pi-*`) are replaced as their
files are touched.

## 6. Dialogs

One grammar (M2, MAS-156), a `BaseFormDialog` in `lib/dialogs/`:

- the header is verb and object, "Create batch", "Edit target collection";
- labels are `FloatLabel` with `for`, as all three op dialogs already do;
- width comes from a size prop (`sm` 480px, `md` 720px, `lg` content-sized up
  to 95vw) and the height from the content, with no minimum;
- the footer is the `<menu>` that `style.css` already right-aligns: a
  secondary text *Cancel* and the one primary verb, no icons on either (today
  the batch dialog alone carries `pi-times` and `pi-save`);
- a disabled primary says why in a tooltip on a `pointer-events: auto` span,
  the trick `DialogTargetCollectionOp.vue` uses on its Batches tab;
- a confirm dialog is PrimeVue's `ConfirmDialog` with `rejectProps` of
  secondary *Cancel* and an accept button carrying the verb and `danger` or
  `warn`.

## 7. Empty states, disabled controls, feedback

**Empty states** are
[`BaseEmptyState.vue`](../../server/frontend/src/lib/base/BaseEmptyState.vue)
and nothing else: an icon, a title in the user's words ("No datasets yet"), a
one-line hint on what the thing is for, an optional note, and the action that
fixes it in the `actions` slot ("Create the first dataset"). A search miss is
the same component with the search icon and "No datasets match the search". An
empty chart draws the empty state over the frame, not an instruction in the
title line above empty axes (S3, MAS-315).

**Disabled controls** carry their reason: a tooltip, plus `aria-describedby`
to a visually hidden span, with `pointer-events: auto` so the tooltip fires on
a PrimeVue element that suppresses events (the tabs in `Dashboard.vue` after
QW3). The reason is written as the action that enables it: "Open a batch with
samples", "Select a sample".

**Toasts** follow
[`lib/toastTitles.js`](../../server/frontend/src/lib/toastTitles.js) and
[`lib/toaster.js`](../../server/frontend/src/lib/toaster.js) (QW2): the title
is the event's name ("Calibration", "Upload"), success and info add no status
word, a warning appends "warning" and an error "failed"; life is 3 s, 7 s for a
warning, 10 s for an error; a result that is on screen in the pane that asked
for it is silent, an empty result is `info`, an error is never silent; at most
five toasts at once, the rest folded into one summary. A new event type adds a
title to the table before it ships. Long-running work shows in the progress
strip under the dashboard and in the notifications list, not as a toast per
step.

## 8. Chips, tags and badges

Three things that look alike and mean differently:

- **A `Tag` is a state** and is not removable: `value`, `severity`, a Phosphor
  `icon`, `--text-sm`, a tooltip naming the state in full.
  [`BaseTierTag.vue`](../../server/frontend/src/lib/base/BaseTierTag.vue),
  [`BaseMatchTag.vue`](../../server/frontend/src/lib/base/BaseMatchTag.vue) and
  [`BaseVerdictBadge.vue`](../../server/frontend/src/lib/base/BaseVerdictBadge.vue)
  are the models. Wherever a tag's colour carries meaning, the pane offers a
  legend (QW6).
- **A `Chip` is a filter** and is removable: the app filter chips in
  `ToolbarAppFilters/` and the keyword chips of the dataset metadata. The
  finder's clause chips and the ledger's tier chips are this kind; an active
  chip reads in the accent on `--p-highlight-background`, an inactive one in
  the muted text colour on a hairline border.
- **A count badge is a number**: `--font-mono`, `999px`, `--text-xs`; filled
  with the accent and white text only when it says something changed since the
  user looked (new samples matching a batch's filter, unread notifications);
  otherwise the muted text colour with no fill. There is no PrimeVue `Badge` in
  the code yet; the sidebar of MAS-712 introduces the component.

## 9. The shell components

Drawn in the review of 2026-11-05 that decided D1 (recorded in
[`ux_review_2026_10.md`](ux_review_2026_10.md) section 4.4) and built by
MAS-712 and MAS-713. The measures are the design's; the implementation may
move them, and then this table moves with it.

| Component | Recipe |
|---|---|
| Sidebar | 236px on the second surface (`--p-surface-100` / `-800`), a hairline border on its right; collapses to a 52px icon rail below about 1400px, the outline then a flyout of the same width over the main area |
| Workspace switcher | the sidebar's top row, workspace icon and name at weight 600, a chevron; a popover lists the workspaces with their icons |
| Destination | a row with icon and label, `--text-base`; the active one on `--p-highlight-background` in the accent at weight 600; a count badge at the row's end |
| Section label | uppercase `--text-xs` with `0.08em` letter spacing in the muted colour, the section's "+" at its end |
| Tree node | 16px indent per level, a `0.3rem` gap, the folder and hash icons the app already uses for dataset and batch; the focused node on the first surface with an inset 3px accent bar at its left and weight 600; a 7px status dot in a state colour at the row's end, or a count badge |
| Breadcrumb | the location chain, workspace to the focused row, in one line: ancestors in the muted colour, the current level at weight 600, `›` separators in the border colour, `‹ ›` stepping arrows in `--font-mono` on the batch and sample crumbs |
| Ledger strip | the filter chips (All, Assigned, Candidate, Unassigned, Targets, Reagent) with counts, then a 7px tier bar whose segments take the state colours in the tiers' order |
| Scope switch | a two-option `SelectButton` (*This sample*, *Whole batch*) in the ledger header |
| Sample strip | a 30px vertical tab on the second surface, its text rotated, bold count and the focused sample's name; expands to the full sample table in place |
| Inspector card | the compound line at `--text-lg` with its tier tag, then a four-column grid of label and `--font-mono` value pairs at `--text-sm` |

## 10. Using this note in a PR

Before a frontend PR is opened, its author checks: every colour comes from a
token in section 3 or a PrimeVue severity; every font size is one of the four
tokens; an icon-only button has label, `aria-label` and tooltip; a disabled
control says why; an empty list is a `BaseEmptyState`; a new toast type has a
title in the table; a new chip, tag or badge is one of the three kinds in
section 8; and a visual decision not covered here is added to this page in the
same PR. The review bot's findings on these points are answered by pointing at
the section, or by amending it.
