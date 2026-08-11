`ULTRA TRACE MASCOPE - DESIGN DOC - UI STATE PERSISTENCE & SHAREABLE LOCATIONS`

# Persisting UI State and Sharing Locations

## Purpose

Two related capabilities for the Mascope web UI:

1. **Refresh survival.** A full page reload - whether from an auto-update
   restarting the backend, a transient network failure, or the user pressing
   F5 - currently drops the user most of the way back to the top of the
   navigation. This makes the auto-update rollout (systemd + `mascope` CLI)
   more disruptive than it needs to be. We want a reload to land the user back
   where they were.

2. **Shareable locations.** Being able to hand another user a link that opens
   Mascope at a specific place - this sample, this collection, this ion in the
   match view - so they see what you see.

Both are the same underlying problem: **serialize the app's current "location"
into a portable token, and drive the stores back to it.** The two features
differ only in where the token lives (browser storage vs. the URL) and who
consumes it. This document records the current state, the shared abstraction,
and a phased plan.

---

## 1. Current architecture (as-is)

The frontend is effectively a single-page app. `src/routes/index.js` registers
one meaningful route (`/`); Vue Router is present but does no navigation. All
"where am I" information lives in **Pinia stores**, not in the URL.

### The location is a chain of focused records

The data stores ([src/stores/data/modules/](../../server/frontend/src/stores/data/modules/))
are wired into a dependency chain. Each store declares a `deps()` on its
parent's `focusedId`; when the parent's focus changes, the child reloads
([lib/store/data.js](../../server/frontend/src/lib/store/data.js)):

```
workspace  (workspace_id)
  -> dataset  (dataset_id)          deps: workspace_id
    -> batch  (sample_batch_id)     deps: dataset_id
      -> sample  (sample_item_id)   deps: sample_batch_id    [multi-select]
        -> peak  (peak_id)          deps: sample_item_id
        -> match.collection  (target_collection_id)   deps: sample|batch
          -> match.ion  (target_ion_id)               deps: sample|batch+collection  [multi-select]
            -> match.visualized  (imperative: ion/isotope/collection cache)
```

Alongside the chain, a handful of **view-state** stores describe how the
current location is presented:

- `ui.tab.active` - which pane tab is showing (`raw files` / `batch` /
  `sample` / `match`), today derived by watchers in
  [ui/tab.js](../../server/frontend/src/stores/ui/tab.js).
- `ui.split` - pane split ratios.
- `ui.darkmode` - theme.
- `match.params` - match filter parameters for the visualization.
- chart zoom (`ui.chart`, transient).

### What already persists across a reload

There is already a persistence primitive. `useSelection` accepts a
`persist: true` option
([lib/store/selection.js:200-256](../../server/frontend/src/lib/store/selection.js)):

- on every focus change it writes the focused record's id to
  `localStorage["module[<name>]"]`;
- after a store's records load, `prepRefocus -> restoreState` reads that id
  back, **validates it exists in the freshly loaded records**, and focuses it
  (cleaning up the entry if the record is gone).

Because focus cascades, restoring one level triggers the next level's load,
which restores itself, and so on. It is already a working, self-healing chain
restore - it is just **only enabled on three stores**: `workspace`, `dataset`,
`instrument`. A few UI bits persist ad hoc via their own `localStorage` keys
(`mascope-dashboard-split`, `mascope-browser-split`, `mascope-darkmode`, table
sort configs).

So today a reload restores you to your **dataset**, then stops. Batch, sample,
the match view, and the active tab all reset.

### Two facts that shape the design

1. **Restore is asynchronous and streaming.** A level can only be restored
   after its parent has focused and its own data has arrived. The right model
   is not "await each level" but "publish the intended chain up front and let
   each store claim its own piece as its data lands." The existing
   `restoreState` + `lazyFocus` machinery already works this way.

2. **IDs are server-side and access-controlled.** Every id in the chain is a
   backend UUID gated by workspace membership / dataset ACLs. Persisting for
   the *same* user is safe. **Sharing a link to another user only resolves the
   levels that user can actually see** - so restore must degrade gracefully,
   landing at the deepest level it can resolve and telling the user why it
   stopped. The existing "validate against loaded records, else clean up"
   behaviour is exactly the right primitive to build on.

---

## 2. Core abstraction: a single Location model

Introduce one canonical description of "where the app is," independent of the
sink it is stored in.

```js
// lib/location/schema.js
// A location is a sparse, ordered chain plus view state. Absent levels = "not focused".
{
  v: 1,                       // schema version, for URL stability across releases
  workspace: "<uuid>|null",
  dataset:   "<uuid>|null",
  batch:     "<uuid>|null",
  samples:   ["<uuid>", ...], // multi-select
  peak:      "<uuid>|null",
  collection:"<uuid>|null",
  ions:      ["<uuid>", ...], // multi-select
  isotope:   "<uuid>|null",
  tab:       "match",
  // view-only, NOT part of a shareable location by default (see 5.3):
  // params, splits, darkmode
}
```

Two pure functions and one store bind it to the app:

- **`readLocation()`** - a computed that derives the current `Location` from
  the live stores (reads each store's `focusedId` / `selectedIds` and
  `ui.tab.active`). One place that knows the chain order.
- **`applyLocation(loc)`** - publishes `loc` as the **target** the stores
  should converge to. It does *not* imperatively await; it seeds each store's
  desired focus (reusing `lazyFocus` / the persisted-id path) so that as data
  streams in, each level claims its piece and cascades the next.
- **`useLocation` store** - owns the target location, exposes `readLocation`,
  `applyLocation`, and coordinates the two sinks below.

Everything else is two thin adapters over this model.

---

## 3. Feature 1 - Refresh survival (localStorage sink)

**Goal:** a reload restores the full chain + active tab + view state, for the
same user, automatically.

### 3.1 Minimal version (small, high value for auto-update)

Extend the existing primitive down the chain. Concretely:

- Turn on `persist: true` for `batch`, `sample`, `peak`, `match.collection`,
  `match.ion`.
- Teach `persistState`/`restoreState` to handle **multi-select** stores
  (`sample`, `match.ion`): store a JSON array of ids, restore all that still
  exist. Today `persistState` only writes the single `focused` record.
- Persist `ui.tab.active` to its own key and restore it after the panes that
  can host it are populated (guard against restoring the `match` tab when
  there is no visualized ion - the existing tab watchers already encode these
  invariants and should own the guard).

This alone makes an auto-update reload land the user back on their sample and
tab. It is a few lines per store plus the multi-select change in
`selection.js`, and it reuses the self-healing/validation logic already in
place.

### 3.2 Full version (consolidated Location)

Replace the scattered `module[<name>]` keys with a single serialized
`Location` written to one key (`localStorage["mascope.location.<userId>"]`)
whenever `readLocation()` changes (debounced). On boot, `applyLocation()` seeds
the chain. Advantages:

- one atomic snapshot instead of N keys that can drift out of sync;
- naturally **user-scoped** (see 3.3);
- the exact same serializer feeds the URL sink (Feature 2).

`match.visualized` needs a bespoke restore step: it is imperative state, not a
`useData` store. After `sample` + `collection` + `ion` have focused, call
`match.visualized.set({ sampleId, collectionId, ionId, isotopeId })`. Hang this
off the location applier as the chain's terminal step.

### 3.3 User scoping

`localStorage` is shared across users on one browser. Today `module[dataset]`
is global, so user B on a shared machine can inherit user A's focus (harmless -
it fails the access check and cleans up - but confusing). Key the consolidated
snapshot by `auth.user.id` and clear/ignore snapshots from a different user on
login. The `auth.onLogin` hook is the place to do this.

### 3.4 Interaction with the auto-update flow

The app already blocks the UI and shows "No connection to the server" while the
socket is down ([App.vue:81-87](../../server/frontend/src/App.vue)), and the
data stores re-sync on reconnect via `auth.onLogin`. So for an in-place backend
restart where the tab is *not* reloaded, state already survives in memory - the
overlay lifts and stores re-sync. Persistence matters specifically for the
**hard reload** case (the user refreshes, or the update ships new frontend
assets and we force a reload). Persisting to `localStorage` on every focus
change - which the primitive already does - is what makes that safe.

> Optional polish, tie-in with the update PR: when the update manifest reports
> a new frontend build, show a non-blocking "New version available - reload"
> prompt rather than forcing it, so the reload happens at a moment the user
> chooses. State restoration then makes that reload cheap.

---

## 4. Feature 2 - Shareable locations (URL sink)

**Goal:** a user can copy a link that reopens Mascope at the current location;
another user opening it lands there (subject to access).

### 4.1 Encoding

Serialize the same `Location` into the URL. Options:

- **Query string** (`/?w=<id>&d=<id>&b=<id>&s=<id>,<id>&c=<id>&i=<id>&tab=match`)
  - human-inspectable, easy to build with `URLSearchParams`, plays well with
  `createWebHistory`. Recommended.
- Compact hash token (base64url of the JSON) - opaque but shorter and hides
  ids from shoulder-surfing; harder to debug. A `v` field lets us switch
  encodings later.

Prefer the query string, versioned via the `v` field. Keep it to the semantic
chain + tab; leave view-only state out (see 5.3).

### 4.2 Two directions

- **Export (copy link).** A "Copy link to this view" action reads
  `readLocation()`, encodes it, and writes it to `window.location` /
  clipboard. Start with an explicit button (predictable, no history spam);
  optionally later mirror the location into the URL continuously (debounced,
  `router.replace` so we don't flood browser history).
- **Import (open link).** On boot, if the URL carries a location, parse it and
  `applyLocation()`. **URL wins over the localStorage snapshot** when present.
  After applying, optionally strip the query back out with `router.replace` so
  a subsequent manual reload falls through to the personal snapshot.

### 4.3 Access control and graceful degradation

This is the crux of cross-user sharing. Each level is validated as its data
loads (the existing `restoreState` check). When a level's id is **not present**
in the loaded records - because the record was deleted, or the recipient lacks
access - the chain **stops at the last resolved level** and surfaces a toast:
"Couldn't open the shared sample - you may not have access to it." No partial,
inconsistent state; the user lands somewhere coherent.

Sharing across users therefore only works when the target lives in a workspace
both users belong to. That is the correct security boundary and needs no new
backend endpoint - it falls out of the existing per-request ACLs. (If we later
want share-by-link to *grant* access, that is a separate, larger feature: a
signed share token redeemed for a scoped grant. Out of scope here.)

---

## 5. Design decisions and edge cases

### 5.1 Async convergence, not imperative navigation

`applyLocation` must be declarative: set the whole target chain, let each store
restore its slice when its data arrives. Trying to `await` level by level
fights the reactive `deps -> sync` machinery and races socket-driven reloads.
The `lazyFocus`/`restoreState`/`prepRefocus` trio already implements
convergence - the applier should feed it, not bypass it.

### 5.2 Stale / deleted ids

Already handled by design: `restoreState` validates existence and cleans up.
The one place to be careful is "records not yet loaded" vs. "record gone" -
the current code correctly keeps the persisted id while records are empty
(deps unmet) and only discards it once a non-empty record set proves it stale.
Preserve that distinction in the multi-select extension.

### 5.3 What belongs in a shareable location vs. only in local persistence

| State | localStorage (personal) | URL (shareable) |
|---|---|---|
| chain (workspace..ion, isotope) | yes | yes |
| active tab | yes | yes |
| match params | yes | maybe (they change what's shown) |
| split ratios, dark mode | yes | no (personal presentation) |
| chart zoom | no (transient) | no |

Match params are the judgment call: they alter what the recipient sees in the
match view, so including them makes a link reproduce the view faithfully;
excluding them keeps links short and lets each user apply their own defaults.
Recommend **including params in the URL only when non-default**, behind the
`v` version so we can revisit.

### 5.4 Versioning

The `v` field guards both sinks against schema drift across releases (relevant
precisely because auto-update will push new frontends). On mismatch: ignore a
too-new snapshot, best-effort migrate a known-older one.

### 5.5 Multi-select restore order

`sample` and `match.ion` are multi-select. Restoring a set is fine, but the
downstream imperative `match.visualized` assumes a single focused ion/sample.
Define restore as: restore the full selected set, and treat the first (or a
stored "focused" marker) as the visualization anchor.

---

## 6. Phased plan

**Phase 1 - Refresh survival, minimal (ships with / after the auto-update PR).**
Extend `persist: true` down the chain; add multi-select persistence to
`selection.js`; persist and guard `ui.tab.active`. Reuses all existing restore
logic. Small, testable, directly de-risks the auto-update UX.

**Phase 2 - Consolidated Location model.**
Introduce `lib/location/` (`schema`, `readLocation`, `applyLocation`,
`useLocation`), migrate the per-module keys to one user-scoped snapshot, add
the `match.visualized` terminal restore step. Behaviour-preserving refactor
that unlocks Phase 3.

**Phase 3 - Shareable links.**
URL adapter over the Location model: "Copy link" action, boot-time import with
URL-wins precedence, graceful degradation with toasts, decide params-in-URL
policy. No backend change.

**Phase 4 (optional) - Continuous URL mirroring and update-reload prompt.**
Debounced `router.replace` mirroring; "new version - reload" prompt wired to
the update manifest.

### Testing

- Unit (Vitest): `readLocation`/`applyLocation` round-trip; encode/decode with
  version handling; multi-select persist/restore; stale-id cleanup vs.
  deps-unmet retention.
- e2e (Playwright, demo stack): focus a sample + ion, reload, assert the same
  location; open a crafted URL and assert it lands; open a URL to an
  inaccessible id and assert graceful stop + toast.

---

## 7. Summary

Mascope already has a working, self-healing, chain-aware persistence primitive
(`persist: true` in `useSelection`) - it is simply scoped to three stores and
only knows single-select. Both requested features are reachable by (a)
extending that primitive down the full chain for refresh survival, then (b)
lifting it into one canonical `Location` model that can be serialized to either
`localStorage` (personal, automatic) or the URL (shareable, explicit), with
graceful degradation falling out of the existing existence-validation. No new
backend endpoints are required for the common case; cross-user sharing rides
the existing workspace ACLs.
