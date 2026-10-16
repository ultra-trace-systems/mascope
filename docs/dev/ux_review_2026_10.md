`ULTRA TRACE MASCOPE - DESIGN DOC - DASHBOARD UX REVIEW, OCTOBER 2026`

# Dashboard UX review, October 2026

Status: **review recorded, nothing built** (2026-10-16). The findings below
were taken from a walk through the dashboard against a running instance and
then re-verified against the frontend code on `develop` the same day; every
finding names the file it rests on. The plan at the end is in three tiers:
quick wins, written as self-contained briefs and filed as issues in the Linear
project [Dashboard UX improvements](https://linear.app/ultra-trace/project/dashboard-ux-improvements-435aa9db35a9)
(team Mascope); medium items, on the roadmap and filed under one parent issue
in the same project; and decisions, which only the plan owner can take and
which are filed there as *Decision* issues, not tasks.

## Picking this up

The dashboard is the whole app for most users: a top bar, a left column with
two stacked browser panes, a right column with four tabs. Nothing found here is
a blocker. The pattern is a steady tax: the user is told where they are three
times and nowhere clearly, actions hide behind icon buttons and right-clicks,
empty panels do not say what would fill them, and feedback arrives as toasts
named after the event that produced them. Each item is small; together they
are what makes the app feel harder than its data model is.

Read section 2 for the shell as it is, section 3 for the findings, section 4
for the plan. The briefs in 4.1 are meant to be implementable from this
document alone.

---

## 1. How the review was done

- A running instance from the `develop` branch, the **targeted workflow**
  (`peak_assignment` flag off, so the Targets/Assignments switch is hidden and
  the lower-left browser shows collections and ions). The peak-centric layout
  was looked at only where the two share code.
- About sixty Playwright screenshots at 1280x720 and 1920x1080, in light and
  dark mode, walking workspace to dataset to batch to sample to collection to
  ion, the dialogs, the context menus, the sidebar drawer and the toasts that
  each action produced.
- The shell code read beside the captures, and re-read on 2026-10-16 to verify
  each finding. Paths below are relative to `server/frontend/src` unless they
  say otherwise.

| Area | Files |
|---|---|
| Shell and tabs | [routes/Dashboard.vue](../../server/frontend/src/routes/Dashboard.vue), [stores/ui/tab.js](../../server/frontend/src/stores/ui/tab.js), [stores/ui/split.js](../../server/frontend/src/stores/ui/split.js), [App.vue](../../server/frontend/src/App.vue) |
| Top bar | [lib/toolbars/ToolbarAppFilters/ToolbarAppFilters.vue](../../server/frontend/src/lib/toolbars/ToolbarAppFilters/ToolbarAppFilters.vue), [SidebarMenu.vue](../../server/frontend/src/lib/toolbars/ToolbarAppFilters/SidebarMenu/SidebarMenu.vue), [NotificationPane.vue](../../server/frontend/src/lib/toolbars/ToolbarAppFilters/SidebarMenu/NotificationPane.vue) |
| Browser panes | [lib/base/BaseTabbedPanel.vue](../../server/frontend/src/lib/base/BaseTabbedPanel.vue), [lib/base/BaseBreadcrumb.vue](../../server/frontend/src/lib/base/BaseBreadcrumb.vue), [lib/panes/PaneBrowserSample/](../../server/frontend/src/lib/panes/PaneBrowserSample/), [lib/panes/PaneBrowserMatch/](../../server/frontend/src/lib/panes/PaneBrowserMatch/) |
| Tabs' content | [lib/panes/PaneTabAcquisitions.vue](../../server/frontend/src/lib/panes/PaneTabAcquisitions.vue), [lib/panes/PaneTabSample.vue](../../server/frontend/src/lib/panes/PaneTabSample.vue), [lib/charts/ChartBatchOverview/](../../server/frontend/src/lib/charts/ChartBatchOverview/), [lib/panes/PanePeakAssign/PanePeakSearch.vue](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakSearch.vue) |
| Dialogs | [lib/dialogs/DialogDatasetOp.vue](../../server/frontend/src/lib/dialogs/DialogDatasetOp.vue), [lib/dialogs/DialogBatchOp/DialogBatchOp.vue](../../server/frontend/src/lib/dialogs/DialogBatchOp/DialogBatchOp.vue), [lib/dialogs/DialogTargetCollectionOp.vue](../../server/frontend/src/lib/dialogs/DialogTargetCollectionOp.vue), [lib/dialogs/DialogCalibration.vue](../../server/frontend/src/lib/dialogs/DialogCalibration.vue) |
| Visual language | [lib/base/BaseMatchTag.vue](../../server/frontend/src/lib/base/BaseMatchTag.vue), [style.css](../../server/frontend/src/style.css), [theme.js](../../server/frontend/src/theme.js), [lib/charts/BaseChartPlotly.vue](../../server/frontend/src/lib/charts/BaseChartPlotly.vue) |

---

## 2. The shell as it is

`Dashboard.vue` is a CSS grid of three rows: the filter toolbar (60px), the
dashboard, and a 5px progress strip. The dashboard row is a PrimeVue
`Splitter`: a left column and a right column, the left column itself split
top and bottom. Both splits persist in `localStorage` through
`stores/ui/split.js`, with defaults of 40/60 (columns) and 50/50 (left rows).

**The top bar** (`ToolbarAppFilters.vue`, which renders `SidebarMenu.vue`) is
the home button and a chain of text buttons: workspace, then the focused
dataset, then the focused batch with previous/next arrows. Clicking a crumb
unfocuses the level below it; right-clicking opens that level's context menu.
Everything else about the account lives in a left `Drawer` with tabs for
workspaces, notifications, settings and about.

**The upper-left pane** (`PaneBrowserSample.vue`) is one box that swaps three
tables by focus: `DatasetTable` while no dataset is focused, `BatchTable` while
no batch is, `SampleTable` otherwise. Each is a `BaseTabbedPanel` with a label
("Batches (12)"), a search field and a "+" create button.

**The lower-left pane** (`PaneBrowserMatch.vue`) does the same for the targeted
workflow: `MatchCollectionTable` while no collection is focused,
`MatchIonTable` otherwise. Both pass a `breadcrumb` to `BaseTabbedPanel`
instead of a label. With the peak-assignment flag on, a Targets/Assignments
`SelectButton` above them switches to the batch-peaks and assignment ledgers.

**The right column** is a `Tabs` with Raw files, Batch, Sample and Match. Which
tab is active is `stores/ui/tab.js`, and that store watches the data chain:
visualizing an ion switches to Match, focusing a batch whose samples load
switches to Batch, unfocusing walks back. A tab is disabled by a condition in
`Dashboard.vue`'s `tabs` computed; the user is not told which.

**Feedback** is one toast per completed notification, built in `App.vue` from
the notification's `type` and `status`, plus the same list in the drawer's
notifications tab.

---

## 3. Findings

Each finding gives what was seen, where it comes from in the code, and what to
do about it. The numbering (F1 to F24) is used by the plan in section 4.

### 3.1 Orientation

**F1. The location is spelled three times.** The top bar shows workspace >
dataset > batch (`SidebarMenu.vue`, the `menu.breadcrum`). The lower-left
pane repeats the batch or sample as the first item of its own breadcrumb
(`MatchCollectionTable.vue` and `MatchIonTable.vue`, the `breadcrumb`
computed, rendered by `BaseBreadcrumb.vue` through `BaseTabbedPanel`'s
`breadcrumb` prop) and continues it to "Target collections" > the collection >
"N ions" - five items at ion level. At the default 40 % left column that chain
does not fit one line and its labels wrap onto a second (the capture shows it;
`BaseBreadcrumb.vue` fixes the menu at 25.15px with `nowrap`, so what wraps is
the buttons' own label text). The Batch chart then repeats the batch name a
third time in its title and the collection in its subtitle
(`ChartBatchOverview.vue`, `chartTitle` and `chartSubtitle`).
*Proposal:* one breadcrumb owns the whole chain, workspace to ion, in the top
bar; the pane headers show only their own level's count and actions; the chart
title names only what the breadcrumb does not (the selected-ion count, the
axis). Medium item M1 and decision D1.

**F2. Drilling down swaps tables in one box with a faint back caret.**
`PaneBrowserSample.vue` is three `v-if`/`v-else-if` branches on focus, so the
dataset list is replaced by the batch list, which is replaced by the sample
list, in the same frame. Only the sample table passes a `clear` to
`BaseTabbedPanel` (`SampleTable.vue`, `:clear="app.data.batch.unfocus"`,
`back-label` "Back to batches"); `BaseTabbedPanel.vue` renders it as a
`ph-caret-left` icon button at `opacity: 0.4`. The batch list has no back
control in its own header at all: the way back to the datasets is the
workspace crumb in the top bar, which the tooltip does not say.
*Proposal:* a visible, labelled back control on every drilled level ("< Datasets",
"< Batches") or a persistent outline of the three levels. Decision D1 chooses
between them; M1 implements.

**F3. The Match tab enables only after an icon button.** The tab's condition is
`disabled: !app.data.match.visualized.ion` (`Dashboard.vue`). The only thing
that sets a visualized ion from the table is the expander column of
`MatchIonTable.vue`: a `Button` whose icon is `ph-seal-question` with a sample
focused (tooltip "Visualize ion match") and `pi-tag` without one (tooltip
"Click to select sample with the best match"). Selecting the row does not do
it - rows are multi-selectable (`selectionMode="multiple"`) because the
selection drives the Batch chart. A user who clicks rows sees the Match tab
stay grey. *Proposal:* an explicit "Open match" action - a context-menu entry,
Enter on the focused row, and a hover button with that label - and a tooltip
on the disabled tab that names it. Quick win QW3.

**F4. Disabled tabs give no reason.** The three conditions in `Dashboard.vue`
(`!app.data.batch.focused || app.data.sample.list.length === 0`,
`!app.data.sample.focused`, `!app.data.match.visualized.ion`) are rendered as
a bare `:disabled`; the `pt` on the tab carries only the help popover. PrimeVue
suppresses pointer events on a disabled tab, so a plain `v-tooltip` would not
fire; `DialogTargetCollectionOp.vue` already solves this for its Batches tab
by wrapping the label in a span with `pointer-events: auto` and putting the
tooltip there ("Please select a collection type first"). *Proposal:* a
`disabledReason` per tab rendered the same way. Quick win QW3.

**F5. Automatic tab switching is invisible.** `stores/ui/tab.js` has four
watchers: a visualized ion moves to Match (unless Sample is active) and its
unfocus moves back to Batch or the default; an unfocused sample leaves Sample
for Batch; samples loading from zero with a batch focused moves to Batch, and
samples emptying leaves it; an unfocused batch leaves Batch. The store already
has a `hydrating` flag that stands the watchers down during a location restore.
A user who picks a tab and then clicks in a browser pane can be moved off it
with no cue. *Proposal:* record the time of the user's last manual pick (the
`Tabs` v-model update in `Dashboard.vue`) and have the watchers skip a switch
within a few seconds of it unless the active tab has just become disabled;
flash the tab header when a switch does happen. Follow-up S1.

### 3.2 Empty states

**F6. Raw files opens on "Last 24 hours".** `PaneTabAcquisitions.vue` offers a
`Select` with `['Last 24 hours', 'Last 7 days', 'Last 30 days', 'Last 90 days']`
plus two date pickers for a custom range; there is no all-time option. The
default is `mode: 'Last 24 hours'` in `stores/data/modules/acquisition.js`
(`initTime()`, and the fallback when a range is cleared). A server whose last
upload is older than a day opens on an empty table. The only empty-result hint
in the pane (`filterActive`) covers the client-side search and polarity
narrowing, not the time window. *Proposal:* default to the newest files
regardless of age, add an all-time option, and give the empty table a message
with one-click "Show last 90 days" / "Show all" actions. Quick win QW1.

**F7. The lower-left pane is blank above batch level.** `MatchCollectionTable.vue`
renders its `DataTable` only `v-if="app.data.batch.focused"`, and its
`breadcrumb` returns no items when nothing is focused (the comment says this is
to keep the "+" right-aligned). At workspace or dataset level the panel is
therefore an empty frame with a lone "+" in the corner. *Proposal:* a titled
empty state - "Target collections" with "Select a batch to see its target
collections" and the create button with its label. Quick win QW4.

**F8. Create dataset, Create batch and Create target collection are icon-only.**
All three buttons carry `class="hiddenlabel"` (`DatasetTable.vue`,
`BatchTable.vue`, `MatchCollectionTable.vue`), which `style.css` turns into
`.hiddenlabel .p-button-label { display: none }`; the label survives only as a
tooltip. On an empty list the "+" is the only thing in the panel. (The same
class hides "Edit ionizations" in `PaneTabAcquisitions.vue`.) *Proposal:* show
the label whenever the list is empty, and put the create action in the empty
state's body. Quick win QW4.

**F9. The notifications pane has no empty state.** `NotificationPane.vue`
renders a heading, a clear button (disabled when the log is empty), the inbox
(which hides itself entirely when it has no items,
`NotificationInbox.vue` `v-if="app.ui.inbox.items.length"`), a search field,
and a `ScrollPanel` of `Message`s; with no log there is nothing under the
search field. *Proposal:* one sentence - what appears here and how long it
stays. Folded into QW4's empty-state component; follow-up S2 for the pane.

### 3.3 Visual language

**F10. The best matches are red, at half opacity, with no legend.**
`BaseMatchTag.vue` maps `matchCategory` 2 to severity `danger`, 1 to `warn`,
0 to `success`. Category 2 is the *probable* match - a score above the
probable threshold (`libraries/match/src/mascope_match/params.py`, and the
`uiCategory` recomputation in `stores/data/modules/match/params.js`), so the
chip reads as an alarm exactly where the result is best. The chip is also
`pale` (`opacity: 0.5`) unless `nofade` or `alarming`, which weakens whatever
colour it has. `ChartBatchOverview/data.js` encodes the same category as a
filled square (2) or an open square (otherwise), with no key. No legend exists
anywhere: the tabs' help cards in `Dashboard.vue` and the panes' help text do
not mention the chips. *Proposal:* semantic colours (probable = success,
possible = warn, below = neutral), no default fade, the chart symbols aligned,
and a legend row in the help cards. Quick win QW6.

**F11. The batch chart is a grey plot area until a collection is selected.**
`ChartBatchOverview.vue` sets `chartSubtitle` to "Select a target collection
to visualize matches" when none is focused, but the traces are empty and
`BaseChartPlotly` draws the axes and grid anyway (`gridcolor: '#33333399'`),
so the instruction sits in the title line above an empty frame.
*Proposal:* an overlay in the figure with the instruction and a button that
focuses the collection table, in place of the empty axes; the pattern exists
in `PanePeakSearch.vue` (`search-placeholder`). Follow-up S3.

**F12. In dark mode the TIC markers and legend glyph were not visible in one
capture.** The TIC trace's marker is `app.ui.darkmode.active ? '#888' : '#222'`
with symbol `diamond-open` (`ChartBatchOverview/data.js`); ion traces take the
`glasbey` palette by theme (`lib/charts/colors.js`); `BaseChartPlotly.vue`
makes the paper and legend backgrounds transparent and recolours legend text
with `--p-panel-color`. One of the two dark captures of the Batch tab showed
neither the TIC diamonds nor the legend glyph; it was not reproduced in the
other. *Proposal:* an audit of every Plotly marker and legend colour against
the dark panel background, with a dark-mode screenshot test. Medium item M5.

**F13. Thirteen-pixel type, a toolbar forced small, no density setting.**
`style.css` sets `html, body { font-size: 13px }`; `ToolbarAppFilters.vue`
forces `font-size: small` on everything in the filter bar; the browser
headers' menu links are `small` too; `BaseMatchTag` defaults to 11px. There is
no density setting (`UserSettingsPane.vue` has none; the only "compact" in the
tree is a per-component prop). At 1280x720 the composition search's parameter
inputs (`PanePeakSearch.vue`: m/z precision at `flex: 0 0 80px`, formula range
beside it) truncated their placeholders. *Proposal:* a type scale as CSS
variables, a comfortable/compact density setting that drives row height and
type size together, and an audit of fixed widths. Medium item M6.

### 3.4 Feedback

**F14. Toast titles are the event name plus its status.** `App.vue` subscribes
to every notification and shows a toast whose summary is
``${beautifySnakeCase(type)} ${status}``, severity from status,
life 3 s (10 s for errors, 7 s for warnings). So a share link produces "Shared
link success", a composition search "Match compositions by mz success", a
calibration fit "Calibration mz fit success". A composition search that finds
nothing completes with `status: success` and `data.results: 0`, so the toast
is green while `PanePeakSearch.vue` is already showing "No results found" in
place. `NotificationPane.vue` builds its log headings the same way.
*Proposal:* a title per notification type (fallback to the beautified name)
shared by the toast and the log; `info` severity for a success with an empty
result; and no toast at all when the pane that launched the request renders
its result, with the entry kept in the log. Quick win QW2.

**F15. Clicking the calibration badge runs a server fit and toasts before the
dialog is read.** `SampleTable.vue`'s badge click calls `openCalibration`,
which sets `contextMenu.dialog.calibration = true`; `DialogCalibration.vue`
watches `visible` and its `init` ends in `refit()`, which is `mzFit.compute()`
- a `POST /calibration/mz_fit` as a process (`lib/mzFit.js`). The backend's
completion notification is toasted as "Calibration mz fit success" while the
user is still reading the dialog that opened. *Proposal:* the dialog opens on
the stored calibration and previews the fit silently (QW2's suppression), and
the toast fires on Apply only. Follow-up S4, enabled by QW2.

**F16. In the targeted layout a ledger click starts a composition search.**
The legacy branch of `PaneTabSample.vue` mounts `PanePeakSearch` `embedded`
beside the peak browser. `PanePeakSearch.vue`'s `watchDebounced` runs
`immediate` on the focused peak's m/z, the sample, the precision, the formula
range and the mechanisms, debounced by the server-set delay (800 ms fallback,
`lib/peakAssignParams.js`), and posts `/cheminfo/mz/match/sample/{id}`. So
browsing peaks is a server search per stop, each with a toast (F14).
*Proposal:* in the embedded layout search on demand (a "Search compositions"
button, or an opt-in auto-follow toggle persisted with the pane); keep
auto-follow in the Re-search takeover, where the user asked for it. Follow-up
S5.

**F17. "Refresh matches", "Rematch" and "Recalibrate" are not explained.** The
Process submenu (`stores/batchContextMenu.js`, and the same three plus "Detect
peaks" in `sampleContextMenu.js`, "Refresh matches" alone in
`datasetContextMenu.js`) offers Recalibrate (opens the calibration dialog),
Refresh matches (`rematch(row)`, which is `full_remove=false, force=false`),
and Rematch (`full_remove: true, force: true`). Nothing in the menu says the
first keeps existing matches and only adds, while the second removes and
recomputes. *Proposal:* labels that say what happens ("Refresh matches - add
new targets only", "Rematch all - remove and recompute"), a description line
per entry, and the same words in the user guide. Folded into M3, which
rebuilds these menu models.

### 3.5 Consistency

**F18. Three dialog grammars.** `DialogDatasetOp.vue` is a 720px modal with
float labels for Name and Description and a footer of "Cancel" / "Create" or
"Save" without icons. `DialogBatchOp.vue` is `min-width: 800px; min-height:
600px` with Info/Targets tabs, float labels centred inside a bordered `Panel`
(65ch inputs), and a footer of "Cancel" with `pi-times` / "Save" with
`pi-save` - "Save" even when creating - leaving a large empty area under two
fields. `DialogTargetCollectionOp.vue` is `max-width: 95vw` with a row of name,
description and a type `SelectButton`, a workspace `Select`, Compounds/Batches
tabs and a two-pane selector at `min-width: 1072px; height: 450px`.
*Proposal:* one form style (float labels), one footer (secondary "Cancel", a
primary verb naming the action, no icons), sizes from content, and a shared
dialog base the three are rebuilt on. Medium item M2.

**F19. Internal id fields are offered as columns.** `SampleTableCustomizer.vue`
builds `availableColumns` from `Object.keys` of the sample records, filtered
only by type. The sample view (`server/backend/alembic/versions/20261009_67479416fc78_sample_view_binding_provenance.py`)
exposes `sample_item_id`, `sample_file_id`, `instrument_function_id`,
`sample_batch_id`, `ionization_mode_id`, `acquisition_id`, `step_id`,
`sequence_run_id`, `agent_id` and more, so the picker lists "Sample item id",
"Instrument function id" and the rest beside Name and Polarity.
*Proposal:* hide `*_id` fields from the default list and show them under an
Advanced tab of the customizer. Quick win QW5.

**F20. Right-click is the only route to edit, delete, members, rematch and
download.** The dataset, batch, sample, collection and ion actions live in
`ContextMenu` models (`stores/datasetContextMenu.js`, `batchContextMenu.js`,
`sampleContextMenu.js`, `MatchCollectionContextMenu.vue`,
`MatchIonContextMenu.vue`), reached by right-clicking a row, a crumb (the
tooltips say "(right click for options)") or the panel. There is no visible
affordance; a trackpad or touch user has to know. *Proposal:* a hover-revealed
overflow button at the row end and a "..." in each pane header that open the
same models; right-click stays. Medium item M3.

**F21. Logout is a primary button on every drawer tab.** The `Drawer`'s
`#footer` in `SidebarMenu.vue` is "Logged in as {username}" and a
`<Button icon="pi pi-sign-out" label="Logout">` with the default (primary)
severity; the footer is the drawer's, so it shows under workspaces,
notifications, settings and about alike. *Proposal:* secondary and text, or
moved into the settings pane. Folded into M2's button vocabulary.

### 3.6 Layout and paradigms

**F22. At 1920px the left column is mostly whitespace.** The default split is
`[40, 60]` in percent (`stores/ui/split.js`), so the left column is 768px wide
at 1920 for tables that need about 500, and 512px at 1280, where the ion
breadcrumb wraps (F1). *Proposal:* viewport-dependent defaults - a pixel range
clamped around a fraction, converted to percent at mount and recomputed on
resize until the user drags. Medium item M4.

**F23. Heights are hand-tuned constants.** `Dashboard.vue`:
`grid-template-rows: 60px calc(100vh - 65px - 2rem) 5px`.
`ChartBatchOverview.vue`: `<figure style="height: calc(100vh - 200px)">`.
`PaneTabSample.vue`: `const padding = 180` feeding the two chart heights.
`SampleTable.vue`: `const padding = 100` and a further `- 50`. Each encodes
the others' heights; changing the toolbar or a tab header misaligns them
silently. *Proposal:* flex columns with `min-height: 0` and `height: 100%`,
and a `ResizeObserver` in `BaseChartPlotly`, so the toolbar height is the only
number. Follow-up S6.

**F24. Two paradigms share the screen, and the switch is far from its effect.**
With the flag on, the Targets/Assignments `SelectButton` in
`PaneBrowserMatch.vue` sets `app.ui.matchMode.mode` (`stores/ui/matchMode.js`,
persisted), which swaps the lower-left browser *and* decides what the Batch
tab plots - the Batch help card in `Dashboard.vue` says so. The control is in
the left column; its most visible effect is in the right. The component
comment says the targeted view is "on a retire path
(docs/dev/peak_assignment_frontend.md)", but when and what survives (the
match-parameter drawer and Rate Match, which only the Match tab carries) is
not recorded. *Proposal:* show the mode where its effect is (a chip in the
Batch chart's title that is also the switch), and record the retirement path.
Decision D2.

---

## 4. Plan

### 4.1 Quick wins

Six briefs, each self-contained, each filed as an issue in the Linear project.
They are independent of one another and of the medium items; any can go first.
Conventions for all of them: one PR into `develop`, rebased, Conventional
Commits, ASCII-only, frontend unit tests (Vitest) for the logic and an e2e
check on the demo stack where the behaviour is visible, anonymized.

#### QW1. Raw files: newest files by default, and a way out of an empty table

**Context.** F6. `PaneTabAcquisitions.vue` offers four recent windows and a
custom range; the default is `'Last 24 hours'` (`stores/data/modules/acquisition.js`,
`initTime()`); `loadRecent(days)` calls `GET /sample/files/recent` with
`days`, `loadRange` calls `GET /sample/files`. A server whose last upload is
older than a day opens on an empty table with no hint.

**Task.** (a) Add an all-time option and make it the default: the newest page
of files regardless of age. Check which route takes it - `/sample/files/recent`
without `days`, or `/sample/files` with an open range (the controllers under
`server/backend/src/mascope_backend/api/controllers/sample/files/`); add the
parameter if neither does. (b) Give the table an `#empty` template: when a
window is active and the page is empty, "No files in the last N days" with
"Show last 90 days" and "Show all files" buttons that set
`app.data.acquisition.time.mode`; when all-time is empty, "No files have been
uploaded to this server yet" with a link to the import guide. (c) Keep a stored
user choice if the mode is persisted; only the initial default changes.

**Where.** `lib/panes/PaneTabAcquisitions.vue` (the `Select` options, the
`#empty` slot); `stores/data/modules/acquisition.js` (`initTime`, `days`, the
load branch); the files controller if the route needs the parameter;
`docs/user` import-files guide if it names the default window.

**Verify.** Vitest on the store's mode-to-days mapping and the empty-state
branch (a component test with a mocked store). e2e on the demo stack: the Raw
files tab lists the demo files on first load (they are older than 24 hours, so
today's default shows an empty table there); picking "Last 24 hours" shows the
empty state and "Show all files" fills the table.

**Size.** S (M if the route needs the parameter).

#### QW2. Toasts: titles per event, info for empty results, silence when the result is on screen

**Context.** F14, F15. `App.vue` toasts every completed notification as
`${beautifySnakeCase(type)} ${status}`; `NotificationPane.vue` heads its log
entries the same way. A zero-result composition search is a green "Match
compositions by mz success" beside the pane's own "No results found"; opening
the calibration dialog toasts "Calibration mz fit success" for a preview.

**Task.** (a) A title map keyed by notification type in `lib/toaster.js` (or a
new `lib/notificationTitles.js`), falling back to the beautified name, used by
the toast and the log heading so the two agree; start with the types the
dashboard produces (composition search, share link, calibration fit and apply,
rematch, export, upload, process). (b) A severity hook per type:
`match_compositions_by_mz` with `data.results === 0` is `info`, not `success`.
(c) A claim: a pane that launches a process and renders its result registers
the process id (`app.ui.notification.claim(processId)`; `PanePeakSearch.vue`
already tracks `pendingProcessId`, `DialogCalibration.vue`'s preview fit is
the second caller), and the toaster skips a claimed notification while the log
keeps it. Errors are never skipped.

**Where.** `App.vue` (the subscriber); `lib/toaster.js`;
`stores/ui/notification.js` (the bus: the claim set); `NotificationPane.vue`
(headings); `lib/panes/PanePeakAssign/PanePeakSearch.vue` and
`lib/mzFit.js` or `lib/dialogs/DialogCalibration.vue` (claims);
`tests/unit` beside each.

**Verify.** Vitest: the title map falls back; the severity hook returns `info`
for an empty result; a claimed process id suppresses the toast and not the log
entry; an error toasts regardless. Manual on the demo stack: a composition
search with nothing found shows the placeholder and no toast; opening the
calibration dialog shows no toast; Apply does.

**Size.** M.

#### QW3. Disabled tabs say why, and "Open match" is an action

**Context.** F3, F4. `Dashboard.vue`'s `tabs` computed disables Batch, Sample
and Match on data conditions with no reason shown; `MatchIonTable.vue`
visualizes an ion only from the expander column's icon button.

**Task.** (a) Add `disabledReason` to each tab entry ("Select a batch that has
samples", "Select a sample", "Open a match from the ion table") and render it
as a tooltip on the disabled tab; PrimeVue blocks pointer events on a disabled
tab, so wrap the label in a span with `pointer-events: auto` as
`DialogTargetCollectionOp.vue` does for its Batches tab. (b) Add "Open match"
to the ion context menu (`MatchIonContextMenu.vue` and its store), make Enter
on the focused row do the same (`onKeyDown` exists), and give the expander
button that label in its tooltip; leave row click as selection, which the
Batch chart depends on.

**Where.** `routes/Dashboard.vue`; `lib/panes/PaneBrowserMatch/MatchIonTable.vue`,
`MatchIonContextMenu.vue`, `stores/ionContextMenu.js`; `tests/unit/routes`
and `tests/unit/lib/panes`.

**Verify.** Vitest: the reason mapping is a pure function of the store state
(three cases); the context-menu model contains "Open match" when a sample is
focused. e2e on the demo stack: hovering the disabled Match tab shows its
reason; "Open match" from the context menu enables and activates the tab.

**Size.** S.

#### QW4. Labelled create buttons and titled empty states

**Context.** F7, F8, F9. Three create buttons hide their label with
`hiddenlabel`; the lower-left pane is a bare frame with a "+" until a batch
is focused; the notifications pane shows nothing when empty.

**Task.** (a) A `BaseEmptyState` component (icon, title, one-line hint, an
actions slot) in `lib/base`. (b) `DatasetTable.vue`, `BatchTable.vue`,
`MatchCollectionTable.vue`: bind the class as
`:class="{ hiddenlabel: list.length > 0 }"` so the label shows on an empty
list, and render `BaseEmptyState` in the table's `#empty` slot with the create
action. (c) `MatchCollectionTable.vue`: when no batch is focused, pass
`label="Target collections"` and `icon` to `BaseTabbedPanel` instead of an
empty breadcrumb, and render `BaseEmptyState` ("Select a batch to see its
target collections") in the body. (d) `NotificationPane.vue`: `BaseEmptyState`
under the search field when the log is empty ("Nothing yet. Results and
warnings of what you run appear here and stay until you clear them").

**Where.** `lib/base/BaseEmptyState.vue` (new) and `lib/base/index.js`;
`lib/panes/PaneBrowserSample/DatasetTable.vue`, `BatchTable.vue`;
`lib/panes/PaneBrowserMatch/MatchCollectionTable.vue`;
`lib/toolbars/ToolbarAppFilters/SidebarMenu/NotificationPane.vue`;
`tests/unit/lib/base`.

**Verify.** Vitest: `BaseEmptyState` renders title, hint and slot; the three
tables show the label when their list is empty and hide it otherwise. e2e on
the demo stack with the `scratch` fixture (a fresh workspace and dataset): the
dataset list's empty state and the labelled "Create batch" are visible.

**Size.** S.

#### QW5. Hide internal id columns from the sample table customizer

**Context.** F19. `SampleTableCustomizer.vue` lists every key of a sample
record as a column; the sample view carries nine or more `*_id` fields, so
"Sample item id", "Instrument function id" and the rest sit beside Name.

**Task.** An `INTERNAL_FIELDS` rule (`/_id$/` plus any named exception) in
`columnOrder.js`; the customizer's "All" tab excludes them and a new
"Advanced" tab lists them for support use (the `tab` ref exists). A stored
configuration that already names one keeps rendering it. `filter_id` keeps
its "Filter" label and stays where it is only if the plan owner says it is
user-facing; the default is to treat it as internal.

**Where.** `lib/panes/PaneBrowserSample/SampleTableCustomizer.vue`,
`columnOrder.js`; `tests/unit/lib/panes/PaneBrowserSample`.

**Verify.** Vitest with a fixture record: `availableColumns` for "All" has no
`*_id` field; "Advanced" has them all; a stored config with `sample_file_id`
still resolves. The server's `sample_table_defaults` need no change.

**Size.** S.

#### QW6. Match chips mean what they say, with a legend

**Context.** F10. `BaseMatchTag.vue` colours the probable match red, the
possible amber, the rest green, at half opacity by default; the batch chart
encodes the same category as filled/open squares; no legend anywhere.

**Task.** (a) Map category 2 to `success`, 1 to `warn`, 0 to `secondary`
(neutral); drop the default `pale` and keep `alarming` as the only reason to
emphasise. (b) Align `ChartBatchOverview/data.js`: filled square = probable,
open = otherwise, and say so in the Batch help card. (c) A legend snippet -
three chips with their meaning and the thresholds from the match parameters -
in the help text of `MatchCollectionTable.vue` and `MatchIonTable.vue`
(`app.ui.help.right`) and in `docs/user/_help/matching.md`, which the Match
tab's help card renders.

**Where.** `lib/base/BaseMatchTag.vue`; `lib/charts/ChartBatchOverview/data.js`;
the two tables' help strings; `routes/Dashboard.vue` (Batch help card);
`docs/user/_help/matching.md`; `tests/unit/lib/base`.

**Verify.** Vitest on the severity mapping (three cases, plus `alarming`). A
light and a dark screenshot of the ion table and the Batch chart with a
collection focused on the demo stack; the legend appears in the help popover.

**Size.** S.

### 4.2 Medium items

On the roadmap, filed as issues under one parent in the Linear project, not
scheduled. Each changes the shape of a component the quick wins only touch,
so they follow them; M1 and M3 wait on decision D1.

- **M1. One breadcrumb owns the chain** (F1, F2). The top bar's chain extends
  to sample, collection and ion; `BaseTabbedPanel` loses its `breadcrumb`
  prop and shows a label with a count and a labelled back control; the chart
  title stops repeating the batch. Files: `SidebarMenu.vue`,
  `BaseTabbedPanel.vue`, `BaseBreadcrumb.vue`, `MatchCollectionTable.vue`,
  `MatchIonTable.vue`, `PaneBrowserAssignment.vue`, `PaneBrowserBatchPeaks.vue`
  (they pass breadcrumbs too), `ChartBatchOverview.vue`. Builds on the chain
  in `docs/dev/ui_state_persistence.md`.
- **M2. One dialog grammar and one button vocabulary** (F18, F21). A
  `BaseFormDialog` with float labels, a fixed footer (secondary "Cancel", a
  primary verb that names the action, no icons), size from content; the three
  dialogs rebuilt on it; Logout demoted. Files: the three `DialogXOp` files,
  `SidebarMenu.vue`, a new `lib/dialogs/BaseFormDialog.vue`.
- **M3. Overflow buttons beside the context menus** (F17, F20). A row-end
  hover button and a pane-header "..." open the existing menu models; the
  Process entries renamed and described. Files: the five context-menu stores
  and components, `BaseTabbedPanel.vue`, the four tables.
- **M4. Viewport-dependent default splits** (F22). `stores/ui/split.js`
  derives defaults from the viewport (a clamped pixel width for the left
  column) and recomputes on resize until the user drags; stored values win.
- **M5. Dark-mode chart audit** (F12). Every Plotly marker, line and legend
  colour in `lib/charts` checked against the dark panel background; the TIC
  marker fixed; a dark-mode screenshot e2e of the Batch and Sample tabs.
- **M6. A type scale and a density setting** (F13). Font sizes as CSS
  variables; a comfortable/compact setting in `UserSettingsPane.vue` that
  drives table row height and type size; the fixed widths in
  `PanePeakSearch.vue` and the toolbar audited at 1280px.

### 4.3 Smaller follow-ups not yet filed

Each is a small change that fits a quick-win PR but did not make the first
six. File them when picked up.

- **S1.** A grace period after a manual tab pick before the watchers in
  `stores/ui/tab.js` may switch (F5).
- **S2.** The notifications pane's empty state if QW4 does not reach it (F9).
- **S3.** An overlay in the batch chart in place of empty axes (F11).
- **S4.** The calibration dialog previews silently and toasts on Apply (F15;
  needs QW2's claim).
- **S5.** On-demand composition search in the embedded targeted layout (F16).
- **S6.** Replace the hand-tuned height constants with flex and a
  `ResizeObserver` (F23).

### 4.4 Decisions for the plan owner

Filed as *Decision* issues in the Linear project. Each is a call, not a task;
the medium items named wait on it.

- **D1. The navigation outline.** Keep the one-box drill-down
  (`PaneBrowserSample.vue`) with a labelled back control and the breadcrumb of
  M1, or move to a persistent outline of workspace, dataset and batch in the
  left column with the tables beside it. The first is cheaper and keeps the
  column narrow; the second removes F2 entirely and makes F22 worse. Decides
  M1 and M3.
- **D2. One paradigm.** When does the targeted workflow (Targets mode, the
  Match tab, `MatchIonTable`) retire, and what of it survives - the
  match-parameter drawer and Rate Match are only there (the comment in
  `Dashboard.vue` records that retiring the tab once removed both). Until this
  is written down, polishing the targeted view competes with retiring it.
  Decides how far F3, F10 and F24 are worth taking.
- **D3. A design-system note.** Whether the frontend gets a short note of its
  own - type scale, density, colour semantics, dialog grammar, button
  vocabulary, the empty-state pattern - that M2, M6 and QW6 implement against,
  and who owns it. Without one, each PR decides these alone and the three
  grammars of F18 return.

---

## 5. Verifying UI changes

- **Logic in Vitest.** Store watchers (`tab.js`), mappings (tab reasons,
  chip severities, title maps, column filters) and small components
  (`BaseEmptyState`) are unit-tested in `server/frontend/tests/unit`,
  mirroring the `src` path. This is the default and is what CI runs on every
  PR.
- **Behaviour in e2e.** Where a change is only visible with the real backend
  (the Raw files default, the Match tab enabling, the empty states of a fresh
  workspace), a Playwright spec on the demo stack, seeded through the `api`
  and `scratch` fixtures, not by clicking. Prefer `getByRole` and
  `getByLabel`; the labelled buttons of QW4 make that easier.
- **Appearance by capture.** Colour and dark-mode work (QW6, M5) is checked
  with a light and a dark screenshot of the affected tab on the demo stack,
  attached to the PR; M5 adds a screenshot assertion.
- **Nothing here moves the pipeline's output**, so the reproducibility check
  is not needed on these branches.

## 6. Out of scope

- The peak-centric layout with the flag on (assignment ledgers, inspector,
  verification capture) beyond the code the two paradigms share; it has its
  own notes (`peak_assignment_frontend.md`, `verification_capture_frontend.md`).
- The sidebar drawer's workspace and user management dialogs, the import
  dialogs and the user guide text.
- Accessibility beyond labels and tooltips (focus order, screen-reader
  announcements of the automatic tab switch) - worth its own pass once D3 is
  taken.
- Mobile and narrow viewports below 1280px.
