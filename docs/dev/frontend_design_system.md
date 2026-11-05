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
the line here in the same PR.

Precedence: the brand guidelines (the *UTS Brand Guidelines* page in Notion,
September 2026 revision), then this note, then PrimeVue Aura's defaults. The
guidelines were drafted with a language model and have had no designer's
review, so they are the reference, not scripture: they settle what they settle
well (the faces, the accent and its light rung, the semantic states), and where
the product's needs differ this note records the departure with the
measurement behind it, in section 10. A PR may add a departure the same way;
what is not recorded there follows the guide.

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
declarations). MAS-160 replaces them with three tokens on `:root`:

| Token | Size | For |
|---|---|---|
| `--text-lg` | `1.15rem` (15px) | dialog and pane titles, the inspector's compound line |
| `--text-base` | `1rem` (13px) | everything with no other reason |
| `--text-sm` | `0.85rem` (11px) | tags, chips, table cells in a dense pane, captions, axis labels, section eyebrows |

Eleven pixels is the floor: the guide's smallest size is the 11px code label,
and nothing in the app reads below it. The guide's desktop scale (16px body,
12px captions) is written for the website; a data UI that shows a thousand
ledger rows at once runs smaller, and 13px is what the app has always used
(departure 1 in section 10). `font-size: small` and `smaller` are not used:
they compound through nesting (the filter bar's `.filters :deep(*)` is finding
F13). Headings inside a pane are weight, not size: `700` for a title, which is
the guide's heading weight, `500` for a label, `400` for text. Plex Sans is
loaded at 400, 500 and 700 only, so the `600` that fifteen components set today
is synthesised by the browser; it becomes `700` as those files are touched, or
the SemiBold file is added to `main.js` if the synthesised weight is what
people have come to read.

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
`#a23f00`, the generated sweep's nearest rung to the guidelines' Orange Deep
`#B84500`, about dE 5 from it) so a button label clears WCAG AA; on the dark
ground it reads the damped ramp `safetyorangedim` so a whole interface of
accent is not tiring (departure 5). The comments in `theme.js` carry the
reasoning; do not pick an orange by hand. The accent means *this is Mascope*
and *this is the one primary action*; it never means a state. Text on the
accent takes the theme's contrast colour: white on light (6.5:1 on Orange
Deep), the dark ground's near-black on dark (5.4:1 on the damped accent, where
white measures 3.2:1 and fails).

**Grounds.** Off-white (`offwhite` ramp) in light, charcoal (`charcoal` ramp)
in dark, through PrimeVue's `surface` tokens. The two ramps are generated
sweeps (`scripts/palette.js`), not the guide's Grey 050 to Grey 900 steps:
eight of the ten steps agree within dE 2, the dark raised surface and the
light body copy drift further, and adopting the guide's ramp was deferred when
the accent was aligned because it recolours every surface in both themes and
wants its own swatches and sign-off (departure 2). Dark mode is the `darkmode`
class on `html`, from
[`stores/ui/darkmode.js`](../../server/frontend/src/stores/ui/darkmode.js);
every colour must read on both grounds, and a chart colour is checked on both
(M5, MAS-159).

**States.** Four tokens in `style.css`, the guide's semantic states as
written, none of them orange:

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
`BaseTierTag` is a hard-coded purple `#8a5ed0` that appears in no palette,
which becomes a `--role-reagent` token beside the state tokens, taking the
guide's Series 3 purple `#8266EA`, the approved colour nearest to it, unless a
swatch on both grounds says it cannot carry the role. Two of the guide's state
pairs miss AA on the app's grounds (info on light at 4.2:1, error on dark at
4.4:1); they are kept as the guide's values and the shortfall is fed back to
the guide rather than fixed by hand here.

**Charts.** Mascope colours dozens of categories at once and keeps Colorcet's
glasbey palette for that, in its light and dark variants; the guide's
five-series palette cannot replace it (departure 3). Where a chart has five
series or fewer, the series take the guide's palette in order, Series 1 the
theme's accent, Series 2 the info teal, then `#8266EA`, `#A6C33C`, `#C93A82`:
the ion time series, the progress strip's five process types. Baseline and
noise take the guide's Grey 600, `#5C5D5A`.

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
between a header's controls. Those are rem on a 13px base, so 4, 6.5 and 13
pixels; the guide's scale is 8/16/24/40/64/96px with 24px card padding,
written for pages. A pane of a thousand rows cannot take 8px everywhere, so
the app keeps a 4px half step and otherwise lands on the guide's steps: 4px
inside a tag or between an icon and its label, 8px between a header's
controls and a row's cells, 16px between form fields and as pane padding, 24px
as dialog content padding (departure 4). MAS-160 writes these as `--space-1`
to `--space-4` beside the type tokens and replaces the rem literals. Radii: controls take Aura's; a pill (chip, count
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
- **A count badge is a number**: `--font-mono`, `999px`, `--text-sm`; filled
  with the accent, and the theme's contrast colour on it (section 3: white on
  light, the dark ground's near-black on dark), only when it says something
  changed since the user looked (new samples matching a batch's filter, unread
  notifications); otherwise the muted text colour with no fill. There is no PrimeVue `Badge` in
  the code yet; the sidebar of MAS-712 introduces the component.

## 9. The shell components

Drawn in the review of 2026-11-05 that decided D1 (recorded in
[`ux_review_2026_10.md`](ux_review_2026_10.md) section 4.4) and built by
MAS-712 and MAS-713. The measures are the design's; the implementation may
move them, and then this table moves with it.

| Component | Recipe |
|---|---|
| Sidebar | 236px on the second surface (`--p-surface-100` / `-800`), a hairline border on its right; collapses to a 52px icon rail below about 1400px, the outline then a flyout of the same width over the main area |
| Workspace switcher | the sidebar's top row, workspace icon and name at weight 700, a chevron; a popover lists the workspaces with their icons |
| Destination | a row with icon and label, `--text-base`; the active one on `--p-highlight-background` in the accent at weight 700; a count badge at the row's end |
| Section label | uppercase `--text-sm` at weight 500 with `0.08em` letter spacing in the muted colour, the guide's caption row; the section's "+" at its end |
| Tree node | 16px indent per level, a `0.3rem` gap, the folder and hash icons the app already uses for dataset and batch; the focused node on the first surface with an inset 3px accent bar at its left and weight 700; a 7px status dot in a state colour at the row's end, or a count badge |
| Breadcrumb | the location chain, workspace to the focused row, in one line: ancestors in the muted colour, the current level at weight 700, `›` separators in the border colour, `‹ ›` stepping arrows in `--font-mono` on the batch and sample crumbs |
| Ledger strip | the filter chips (All, Assigned, Candidate, Unassigned, Targets, Reagent) with counts, then a 7px tier bar whose segments take the state colours in the tiers' order |
| Scope switch | a two-option `SelectButton` (*This sample*, *Whole batch*) in the ledger header |
| Sample strip | a 30px vertical tab on the second surface, its text rotated, bold count and the focused sample's name; expands to the full sample table in place |
| Inspector card | the compound line at `--text-lg` with its tier tag, then a four-column grid of label and `--font-mono` value pairs at `--text-sm` |

## 10. Departures from the brand guidelines

Each is a measured choice, recorded so that nobody re-derives it and so that
the guide can be amended; the figures come from the PRs that made them
(the accent and state alignment of September 2026, and the dark-accent
damping that followed it) or from this note's own reading of the code.

| # | The guide says | Mascope does | Why |
|---|---|---|---|
| 1 | Body 16px, captions 12px, code labels 11px | Base 13px, small 11px, large 15px | A data UI shows a thousand ledger rows at once; the guide's scale is the website's. The 11px floor is kept. |
| 2 | Neutrals are the Grey 050 to 900 ramp; do not invent greys | Generated off-white and charcoal sweeps | Eight of ten steps agree within dE 2; adopting the ramp recolours every surface in both themes and waits for its own swatches and sign-off. |
| 3 | Charts follow the five-series palette in order | Glasbey for many-category charts; the five series where a chart has five or fewer | Dozens of simultaneous categories cannot be told apart with five colours. |
| 4 | An 8px spacing scale with 24px card padding | 4/8/16/24px, the 4px half step inside tags and between icon and label | Dense tables need the half step; everything else lands on the guide's steps. |
| 5 | Safety Orange `#FF6700` is the accent on dark | The damped sweep of the same hue, `#dd743b` | Full-chroma orange over a whole interface on near-black tires over a session; damping at a fixed lightness is contrast-neutral (5.4:1 either way). dE 8 from Safety Orange is the cost. |

Two more things are not departures but gaps in the guide, fed back with the
September alignment: it has no product-UI row in its Themes table (its dark
theme is scoped to web and social, its light theme to print and decks), and
its semantic states were never contrast-checked against the themes' grounds.

## 11. Using this note in a PR

Before a frontend PR is opened, its author checks: every colour comes from a
token in section 3 or a PrimeVue severity; every font size is one of the four
tokens; an icon-only button has label, `aria-label` and tooltip; a disabled
control says why; an empty list is a `BaseEmptyState`; a new toast type has a
title in the table; a new chip, tag or badge is one of the three kinds in
section 8; a departure from the brand guide is a row in section 10 with its
measurement; and a visual decision not covered here is added to this page in
the same PR. The review bot's findings on these points are answered by pointing
at the section, or by amending it.
