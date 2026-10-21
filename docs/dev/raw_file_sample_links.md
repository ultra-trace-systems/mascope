`ULTRA TRACE MASCOPE - DESIGN DOC - RAW FILES: LINKS TO SAMPLES`

# Raw files: links from a file to its samples

Status: **design, nothing built** (2026-10-21). Code anchors were read at
v1.13.0 on 2026-10-20 (`develop` differed from it only in calibration code)
and are named where a claim rests on them. Section 7 lists what is open, each
item with a recommendation. Section 8 is the plan, two briefs filed as MAS-245
and MAS-246 in the Linear project
[Dashboard UX improvements](https://linear.app/ultra-trace/project/dashboard-ux-improvements-435aa9db35a9)
(team Mascope).

## Picking this up

The feature, as a person meets it:

- **A *Samples* column in Raw files.** Each row shows how many samples were
  made from the file that the person can open. The number is the link.
- **Clicking it opens them.** With one sample, the app moves to its
  workspace, dataset and batch, selects the sample and shows the Sample tab.
  With several samples in one batch, it selects them all in the Batch tab.
  With samples in more than one batch, a short picker asks which.
- **A zero says why.** "Still being processed", "choose its chemistry
  first", or "none that you can open", as a tooltip on the 0.
- **Only samples you can open.** A sample in a workspace the person is not a
  member of is never offered, named or counted.

The design in one paragraph. Each row of the Raw files list carries a count
of the file's sample items, scoped to the workspaces the workspace list shows
the caller, so every number can be followed. Clicking the number fetches
those items with their whole chain (workspace, dataset, batch, item) from one
read route, and the frontend turns the answer into a location and drives it
through `useLocation().apply`, the same path a shared link and a restored
session already take. Nothing new is stored; the items are fetched when the
link is followed, so a re-processed file's new samples are found.

---

## 1. Why

Raw files is where a person checks that ingest worked: the Status column says
*Done*, *Calibration failed*, *Needs a chemistry*. The next question is "show
me", and today the answer is a walk: open `Acquisitions <instrument>`, then
the year's dataset, then the day's batch for the right ionization mode (named
`"<date> <mode name> acquisition"`, `acquisition_batch_name` in
`controllers/sample/files/process/service.py`), then search the sample table
for the file's name. For a file a person also imported into a batch of their
own, nothing points there at all, and nothing says that it was imported
anywhere.

The count answers the second question on sight: a 3 on a file acquired in
one polarity says it is in two batches besides its acquisition batch.

The sample finder (`docs/dev/sample_filters_and_metadata.md`, Linear project
*Sample finder and acquisition metadata*) replaces the walk for finding
samples by filter. This is the other direction: from one file you are already
looking at, straight to what was made from it. The two do not overlap, and
the finder's results can reuse the navigation built here (section 6).

---

## 2. Current state

### 2.1 One file, many samples, many batches

`SampleItem.sample_file_id` (`db/models.py`, indexed, `ON DELETE CASCADE`)
ties an item to its file; an item belongs to one batch, a batch to one
dataset, a dataset to one workspace. A file reaches several items in several
places:

- **Auto-processing** makes one item per bound ionization mode, each in that
  mode's daily `ACQUISITION` batch of the instrument's year dataset in the
  instrument's system workspace (`create_acquisition_batches_and_items` in
  `process/service.py`). A file acquired in both polarities therefore has
  at least two items in two batches. MS2 parts go to a batch of their own
  (`process/ms2_parts.py`).
- **Import, copy and manual processing** (`DialogBatchImport`,
  `DialogSampleOp`, `POST /api/sample/items/copy`) add items of the same file
  to analysis batches, in any workspace the person can edit, and a person can
  cut several time windows of one file into one batch.
- **Re-processing** deletes the file's acquisition items and makes new ones
  with new ids. A link stored anywhere would go stale; a link resolved when
  it is followed does not.
- **No items yet, or none at all**: a file that is `converted` or `queued`
  has not been bound; `needs_chemistry` was never bound; `failed` may have
  stopped before or after (`lib/processingStatus.js`).

### 2.2 No route answers "the items of a file"

`GET /api/sample/items` (`routes/sample/items/sample_items_routes.py`)
requires `sample_batch_id` and checks access to that batch; its
`sample_file_id` parameter only narrows within the batch. The samples routes
(`/api/samples`) are per batch too. Nothing returns a file's items across
batches, nothing counts them, and nothing returns an item together with its
dataset and workspace.

### 2.3 Who sees a file is not who can open its samples

The Raw files list (`get_sample_files` in
`controllers/sample/files/sample_files_controller.py`) shows a file when the
caller is a member of its instrument's acquisition workspace **or** of any
workspace holding one of its items, and shows every file to a superuser or a
global admin (`accessible_acquisition_instruments` returns `None` for both,
`new/workspaces/dependencies.py`).

The workspace list (`get_workspaces` in `new/workspaces/service.py`) shows
member workspaces only, and every workspace to a superuser. The workspace ACL
(`_enforce`) bypasses for superusers only - a global admin is refused in a
workspace they are not a member of, which is the case `lib/permissions.js`
already documents for calibration.

So a global admin who is not a member of an instrument's workspace sees the
instrument's files in Raw files, and cannot open the samples made from them.
A count or a link computed from the file-visibility rule would promise them
samples the app cannot load.

### 2.4 The frontend already knows how to go anywhere

`useLocation().apply(loc)` (`lib/location/index.js`) takes a location -
workspace, dataset, batch, the selected samples, and the tab - and drives each
level's store toward it, queueing a level whose records have not loaded
(`lazyFocus`, `lazySelect` in `lib/store/selection.js`) so the chain converges
as each parent's children arrive. It restores the tab through
`ui.tab.hydrate`, which holds off the automatic tab switching of
`stores/ui/tab.js` until the chain settles. `verifyApplied` warns when a level
has loaded and still lacks its target. It is used today for shared links at
login; it is exactly the navigation this needs.

The Raw files table (`lib/panes/PaneTabAcquisitions.vue`) is a multi-select
`DataTable` with fixed column widths (Instrument, Filename, Status, Polarity,
Datetime) whose selection feeds *Process selected*, *Choose chemistry*,
*Download*, *Delete* and *Re-process*. A row click selects. *Process
selected* processes into the **focused batch**, so moving the focus elsewhere
changes where it would process to (section 7, question 5).

The list's rows are `SampleFile.to_dict()`, and so is the record of every
`acquisition_updated` socket event, which the store applies in place
(`applyUpdate` in `stores/data/modules/acquisition.js`). Creates and deletes
already trigger a debounced background reload of the page (`reload`, 300 ms
wait, 2 s ceiling).

---

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| 1 | The server says where a file's samples are; the client does not derive it | Batch names and the acquisition layout are ingest plumbing (the ingest design keeps changing them); the item rows are the truth |
| 2 | "Can open" is "in a workspace `GET /api/workspaces` lists to the caller": member workspaces, every workspace for a superuser | Every number can be followed (2.3). The file-visibility rule would show global admins counts that lead nowhere |
| 3 | Items the caller cannot open are not returned, counted or named | Matches `access_granted`'s rule of naming an object only to a caller who could read it. A zero says "none that you can open", never "none" |
| 4 | The count is the link: one column, one gesture | Visible on every row without being told about it; the number says before the click whether there is anywhere to go |
| 5 | Counted with the page; the items themselves fetched when the link is followed | The count is cheap per page; the chain is needed only for the one file clicked, and fetching it then survives re-processing (new ids) and imports made elsewhere |
| 6 | One sample lands on the Sample tab; several of one batch on the Batch tab; several batches ask | The Sample tab is the spectrum the person came for; a set of samples is what the Batch chart is for |
| 7 | Clicking the count does not select the row | The selection drives the processing actions; following a link must not change what *Process selected* would process |
| 8 | Navigation goes through `useLocation().apply` | One path for shared links, restore and this; no second chain driver to keep in step |

---

## 4. The design

### 4.1 The count on each row

Each row of `GET /api/sample/files` and `GET /api/sample/files/recent` gains
`sample_item_count`, scoped by decision 2: the file's items in workspaces the
caller is a member of, every item for a superuser.

Computed in `get_sample_files` by one grouped query over the page's
`sample_file_id`s, after the page query and outside it:

```sql
SELECT si.sample_file_id, count(*)
FROM sample_item si
JOIN sample_batch sb ON sb.sample_batch_id = si.sample_batch_id
JOIN dataset d ON d.dataset_id = sb.dataset_id
JOIN workspace_member wm ON wm.workspace_id = d.workspace_id
                        AND wm.user_id = :caller        -- not for a superuser
WHERE si.sample_file_id IN (:page_ids)
GROUP BY si.sample_file_id
```

Kept out of the page statement so the count, sort and pagination query, and
the index plan `tests/integration/api/sample/test_sample_file_list_plan.py`
pins, are untouched. The existing index on `sample_item.sample_file_id`
carries it; a page is at most 500 ids. The access predicate is one helper
shared with 4.2, so the count and the list it opens cannot disagree.

`get_sample_files` needs the caller for this, where today it takes
`allowed_instruments` and a `user_id` that is `None` for privileged callers.
It takes the caller's id and superuser flag explicitly, since a global admin
has `allowed_instruments=None` (sees every file) but is still scoped here.

`SampleFile.to_dict()` is not changed: a socket event is broadcast to an
instrument's room, and a count scoped per caller cannot ride in it.

### 4.2 The items of one file

`GET /api/sample/files/{sample_file_id}/items`, beside
`/{sample_file_id}/peaks` and `/{sample_file_id}/metadata`, behind the same
`check_sample_file_instrument_access(..., "guest")`, so a file the caller
cannot see is refused as its siblings refuse it. Controller
`get_sample_file_items(sample_file_id, user)` in `sample_files_controller.py`.
`@api_route()` like its siblings, not token-accessible to begin with; opening
it to the SDK is the one-flag change the AI layer makes route by route.

The answer is one flat row per item, so the client groups it as it needs:

```json
{
  "message": "Sample items of the file retrieved successfully.",
  "results": 2,
  "data": [
    {
      "sample_file_id": "f1...",
      "sample_item_id": "i1...",
      "sample_item_name": "...",
      "sample_item_type": "...",
      "polarity": "+",
      "t0": 0.0,
      "t1": 1800.0,
      "sample_batch_id": "b1...",
      "sample_batch_name": "2026-10-19 <mode> acquisition",
      "sample_batch_type": "ACQUISITION",
      "dataset_id": "d1...",
      "dataset_name": "2026",
      "workspace_id": "w1...",
      "workspace_name": "Acquisitions <instrument>",
      "workspace_is_system": true,
      "workspace_status": "active"
    }
  ]
}
```

One `SELECT` over the joins of 4.1, with the same predicate. Ordered so the
person's own work comes first: non-system workspaces before system ones, then
workspace, dataset and batch name, then `t0`. Workspaces are not filtered by
status: the workspace list returns archived ones too, so the frontend can
open them.

### 4.3 The column

A *Samples* column in `PaneTabAcquisitions.vue`, after *Status*, about 80 px,
not sortable (sorting by an aggregate would move it into the page query and
off the index). The cell is one of:

- **A number of one or more**: a small link-styled `Button` with the count,
  `aria-label` "Open the 2 samples of <filename>", tooltip "Open its sample"
  or "Open its 2 samples". Its click calls `openSamples(row)` and stops
  propagation, so the row is not selected (decision 7).
- **Zero**: a muted "0", not a button, its tooltip `emptyReason(row)`.
- **Busy**: while the click's request is out, the button shows a spinner and
  is disabled, so a second click does not race the first.

### 4.4 Following the link

A frontend module, `lib/sampleLinks.js`, holds the decisions as pure
functions, so they are unit-tested without a store:

- `groupByBatch(rows)` - the rows grouped by `sample_batch_id`, each group
  carrying its chain.
- `locationFor(group, items)` - the location to apply: `workspace`,
  `dataset`, `batch`, `samples` (the item ids), and `tab`, `'sample'` for one
  item and `'batch'` for several (decision 6).
- `emptyReason(file)` - what a zero says, by status: "Still being processed"
  for `converted` and `queued`, "Choose its chemistry first" for
  `needs_chemistry`, otherwise "No samples that you can open" (decision 3).

`useLocation` gains `open(loc)`: `apply(loc)` followed by the same
convergence check as a shared link, with in-app wording ("Could not open the
sample; it may have been deleted or re-processed"). `verifyApplied` takes the
message, rather than a second copy of the poll being written.

`openSamples(file)` (in `lib/sampleLinks.js`, using the store and the API):

1. Fetch the file's items (4.2).
2. None came back - the count was stale: an info notification with
   `emptyReason`, and the row's count is set to 0. Nothing moves.
3. Every item in one batch: `open(locationFor(...))`.
4. Items in several batches: the picker.

The **picker** (`lib/dialogs/DialogOpenSamples.vue`) lists the groups, own
workspaces first: a header line `workspace > dataset > batch` with an
*Acquisition* or *Analysis* tag (and *Archived* where it applies), and under
it the items (name, polarity, the time window in minutes). A group's *Open in
this batch* button opens all of its items; an item opens alone. Buttons, not
row clicks, so it works from the keyboard and reads to a screen reader.

What the jump leaves behind: the peak, target collection, ion selection and
match visualization are cleared, as a shared link clears them. Raw files keeps
its page, filters and selection, so the way back is one click on its tab.

### 4.5 Keeping the count current

- **A status update** arrives as a bare `SampleFile.to_dict()` with no count.
  `applyUpdate` keeps the row's previous `sample_item_count`, and when the
  update changes `processing_status` it schedules the store's existing
  debounced background `reload`. Binding is what makes acquisition items and
  re-processing is what deletes them, and both change the status, so during
  ingest a count is at most the reload ceiling (2 s) behind. The reload is the
  one creates and deletes already trigger, so a busy fleet costs no more
  requests than it does today.
- **An import or copy into a batch elsewhere** emits no acquisition event, so
  that count stays low until the next load of the page.
- The number is a hint; the click always fetches, so it never opens the wrong
  place, and step 2 of 4.4 corrects a count that promised too much.

### 4.6 Words

The Raw files help popover in `routes/Dashboard.vue` gains one line ("The
Samples column counts the samples made from each file; click the number to
open them"), and `docs/user/guides/import-files.md` a short section. Each PR
carries its own `changelog.d/` fragment.

---

## 5. Costs and risks

- **One more query per page load.** A grouped count over at most 500 ids on
  an indexed column, alongside the page query. Measured against the list's
  own query in the backend tests, not assumed.
- **More background reloads during ingest.** A status change now reloads the
  page as a create already does; the debounce ceiling bounds it to one reload
  every 2 s while Raw files is on screen (the store stops listening when the
  tab is parked).
- **The jump clears match state.** Someone who opened a file's sample from
  Raw files in the middle of a match review loses the visualized ion. The same
  is true of opening a shared link, and the Raw files tab is a different task;
  accepted.
- **Processing into the focused batch.** *Process selected* targets the
  focused batch, and following a link changes it. A person who opens a file's
  acquisition sample and then comes back to process files would process into
  the acquisition batch, if they may edit it. The multi-file import dialog
  names its target batch in its title (`DialogBatchImport.vue`); the
  single-file one says only "Create a new sample item" (`DialogSampleOp.vue`).
  Section 7, question 5.
- **Global admins see zeros.** By decision 2 a global admin who is not a
  member of an instrument's workspace sees 0 on its files, with "No samples
  that you can open". That is the honest answer and the fix is a membership;
  it is also what the calibration gate already tells them.

---

## 6. How this touches the related designs

- **Sample finder** (`sample_filters_and_metadata.md`): the finder's result
  rows are items with a chain; `open()` and `locationFor` are what its "go to
  this sample" uses. Its decision on where the finder lives (Linear MAS-105,
  "a pane beside Raw files") is unaffected.
- **Dashboard UX review** (`ux_review_2026_10.md`): M1 (one breadcrumb owns
  the chain, MAS-155) shows where a jump landed; not needed first. Finding F5
  (automatic tab switching is invisible) does not bite: `open()` holds the
  watchers off through `hydrate`.
- **UI state persistence** (`ui_state_persistence.md`): no new location
  field; a jump is persisted like any other focus change.
- **Ingest routing and splitting**: when a file is split into windows and
  streams, it simply has more items; the count grows and the picker lists
  them with their time windows. Nothing here depends on the batch layout.

Not in this design:

- **Opening the samples of several selected files at once** (a day's files
  into their daily batch). It would want a context-menu entry and a bulk form
  of the 4.2 route; the column is per row. Worth doing if people ask for it.
- **The reverse link**, from a sample to its row in Raw files. It needs the
  Raw files list to take a server-side filename filter from a store action
  (the route already accepts `filename`).

---

## 7. Open questions

| # | Question | Recommendation |
|---|---|---|
| 1 | One sample: land on the Sample tab or on the Batch tab with it selected? | Sample tab (decision 6): the spectrum is what the person came to see, and the batch is one crumb away |
| 2 | Should the column be sortable ("files in no batch first")? | No: it would move the aggregate into the page query and off the index the list is planned on. A status filter covers the "not processed" case already |
| 3 | Offer samples in archived workspaces? | Yes, as the workspace list does; the picker tags them *Archived* |
| 4 | Should the count split acquisition from analysis samples ("1 + 2")? | No: one number reads at a glance, and the picker shows the split |
| 5 | Should following a link keep *Process selected*'s target batch from moving? | No: name the target instead. The single-file dialog's title names the batch as the import dialog's already does (in 8.2). If that still misleads, *Process selected* asks for its target rather than reading the focus - a change of its own |

---

## 8. Plan

Two issues in the Linear project *Dashboard UX improvements* (team Mascope),
one after the other: MAS-246 is blocked by MAS-245. Paths in the frontend
brief are relative to `server/frontend/src`, in the backend brief to
`server/backend/src/mascope_backend`, unless they say otherwise.

### 8.1 Backend: sample counts on Raw files rows, and the samples of a file with their chain (MAS-245)

**Context.** `docs/dev/raw_file_sample_links.md`, sections 2.2, 2.3, 4.1 and
4.2. No route counts or returns a file's sample items across batches:
`GET /api/sample/items` requires `sample_batch_id`. Raw files shows a file to
anyone who can see its instrument or any of its items, and every file to a
global admin, but the workspace ACL lets only members (and superusers) into a
workspace, so "who can see the file" is the wrong scope for its samples.

**Task.** (a) An access helper in
`api/controllers/sample/files/sample_files_controller.py`: the `sample_item`
to `sample_batch` to `dataset` join, plus `workspace_member` on the caller
unless they are a superuser. (b) `sample_item_count` on every row of
`get_sample_files`, by one grouped query over the page's ids after the page
query, outside the count/sort/pagination statement (section 4.1);
`get_sample_files` takes the caller's id and superuser flag from both list
routes, since a global admin lists every file but is still scoped here.
`SampleFile.to_dict()` unchanged. (c) `get_sample_file_items(sample_file_id,
user)` and `GET /{sample_file_id}/items` in
`api/routes/sample/files/sample_files_routes.py`, behind
`check_sample_file_instrument_access(..., "guest")`: the flat rows of section
4.2, workspace status included for the picker's *Archived* tag, ordered
non-system workspaces first, then workspace, dataset and batch name, then
`t0`. Not token-accessible.

**Where.** The controller and routes above; tests in
`server/backend/tests/integration/api/workspace/` beside
`test_sample_file_filtering.py`, whose fixtures already build the
instrument-member, item-member and admin cases.

**Verify.** Backend pytest, for the count and the route alike: a member of
the instrument workspace gets its acquisition items; a user who is a member
only of an analysis workspace holding one item gets that item and not the
acquisition ones; a global admin who is a member of neither sees the file in
`GET /api/sample/files` with a count of 0 and an empty list from the route; a
superuser gets everything; a file acquired in both polarities counts 2 and
returns two items in two batches; each row's count equals the length of the
route's answer for it; the order holds; a page with no items reports zeros.
`tests/integration/api/sample/test_sample_file_list_plan.py` passes
unchanged. `uv run ruff check . && uv run ruff format --check .`.

**Size.** M. Blocks 8.2.

### 8.2 Frontend: the Samples column, and opening a file's samples from it (MAS-246)

**Context.** `docs/dev/raw_file_sample_links.md`, sections 2.4, 3, 4.3 to
4.6. Needs 8.1. `useLocation().apply` in `lib/location/index.js` already
drives workspace, dataset, batch, samples and tab, and is used for shared
links. The Raw files table (`lib/panes/PaneTabAcquisitions.vue`) selects on
row click and its selection drives the processing actions; following a link
must not change it.

**Task.** (a) `lib/sampleLinks.js`: pure `groupByBatch`, `locationFor` (one
item: tab `'sample'`; several: `'batch'`) and `emptyReason` (by processing
status), and the `openSamples(file)` action of section 4.4: fetch
`GET /sample/files/{id}/items`; nothing back, an info notification and the
row's count set to 0; one batch, navigate; several, the picker. (b)
`useLocation().open(loc)`: `apply` plus the convergence check, with
`verifyApplied` taking its message so the in-app wording ("Could not open the
sample; it may have been deleted or re-processed") does not need a second
poll. (c) `lib/dialogs/DialogOpenSamples.vue`, exported from
`lib/dialogs/index.js`: groups as `workspace > dataset > batch` with an
Acquisition/Analysis (and Archived) tag, items with name, polarity and time
window, a group button and an item button. (d) The *Samples* column of
section 4.3 in `PaneTabAcquisitions.vue`, after *Status*, not sortable: the
count as a link-styled button that stops propagation and shows a spinner
while its request is out; a muted 0 with `emptyReason` as its tooltip. (e)
In `stores/data/modules/acquisition.js`, `applyUpdate` keeps the row's
previous `sample_item_count` and, when the update changes
`processing_status`, schedules the existing debounced `reload` (section 4.5).
(f) `lib/dialogs/DialogSampleOp.vue`'s create title names the focused batch,
as `DialogBatchImport.vue`'s does: this makes moving the focus a click away
from Raw files. (g) The Raw files help in `routes/Dashboard.vue` and a section
in `docs/user/guides/import-files.md`. A `changelog.d/` fragment under
*added*.

**Where.** The files above; tests in `server/frontend/tests/unit/lib/`
(`sampleLinks.spec.js`), `tests/unit/lib/location/location.spec.js` (`open`),
`tests/unit/lib/dialogs/`, and the existing
`tests/unit/stores/data/modules/acquisition.spec.js` and
`tests/unit/lib/panes/paneTabAcquisitions.spec.js`; an e2e spec in
`tests/e2e/`.

**Verify.** Vitest: grouping; the location for one and several items; each
empty reason; `open` warning with the in-app message when the sample never
arrives; the dialog's buttons emitting the right location; the store keeping
the count across an update with the same status and reloading on a status
change; the cell's three states, and a click on the count leaving the
selection as it was. e2e on the demo stack: take the newest demo file through
the `api` fixture (so it is on the first Raw files page) and import it into
two batches of the `scratch` dataset, so the case does not rest on the demo's
acquisition layout. Its row's count includes both; clicking it opens the
picker with both scratch batches (and the acquisition batch, if the demo has
one); *Open in this batch* on one lands there on the Sample tab, which *Copy
link to this view* confirms (`b=` and `s=`).

**Size.** M. Needs 8.1.
