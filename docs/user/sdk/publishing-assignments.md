# Publishing an assignment run

An engine outside Mascope can read a sample's peaks through the SDK, assign
them, and publish the result back as an assignment run of its own: the same
tables, the same ledger view and the same batch fold-in as a run Mascope
computed, always stamped with the engine that produced it. This page is the
contract for that import. How a run is read, and what the ledger shows of an
imported one, is under
[Assignment runs](../how-it-works/peak-assignment.md#assignment-runs); an
engine that works on the batch as a whole publishes through the batch-level
route instead
([Importing an engine's batch result](../how-it-works/peak-assignment.md#importing-an-engines-batch-result)).

The route is `POST /api/peak-assignments/sample/{sample_item_id}/runs/import`.
The SDK does not wrap it yet; call it with an API token the way
[Calling the API directly](index.md#calling-the-api-directly) describes. It
needs the **editor** role in the sample's workspace, and a server with peak
assignment switched on (403 otherwise). A blank sample is refused, as it is
for an in-app run, and so is a sample with another run in flight (409, naming
the run).

## The request

| Field | Meaning |
|---|---|
| `engine` | The engine's name, stamped on the run and shown in the run selector. `mascope`, `mascope-copy` and `batch` are reserved for the server's own runs and refused in any capitalisation (422) |
| `engine_version` | The engine's version string |
| `tier_bands` | The `assigned` and `candidate` thresholds the engine tiered with, on the evidence scale (below). Required on the request that creates the run; the legacy key `identified` is read as `assigned` |
| `calibration` | What the engine calibrated against, as JSON of its own. Required on the request that creates the run: an import bypasses the sample's m/z verification gate, so what it calibrated against goes on the record, and the run selector badges the run from it |
| `config` | The engine's run configuration, as JSON. Opaque: stored verbatim, never read by the server |
| `rows` | The assignment rows, below |
| `chunk` | Assembly control, below. Required even for a single-request import |

`config` and `calibration` may be 64 KB each at most. One request may carry
1000 rows and 5 MB at most; a request over either is refused (413 for the
bytes, 422 for the rows), and every response reports the row cap as
`max_rows_per_request`, so size chunks from the server rather than from a
guess.

### A row

A row is a ledger record as the API serves one, minus the fields the server
owns: it mints `peak_assignment_id`, `peak_assignment_run_id` and
`sample_item_id` itself, and leaves the calibrated confidence columns empty.

| Field | Meaning |
|---|---|
| `sample_peak_id` | The peak, which must exist in the sample. At most one row per peak, in the whole import |
| `sample_peak_mz`, `sample_peak_intensity`, `sample_peak_tof` | The peak as the engine observed it. The intensity must be the quantity Mascope's own engine uses for the instrument: **peak height for an Orbitrap file, peak area for a TOF file**. It scales the member's vote in the batch ledger, and nothing can detect the wrong quantity |
| `role` | `M0`, `iso_child`, `reagent`, `artifact` or `unassigned` |
| `assigned_formula`, `ion_formula`, `isotope_label`, `isotope_formula` | What the engine assigned |
| `ionization_mechanism_id` | Optional: an engine names adducts by notation. A supplied id must exist on this server and carry the sample's polarity (422 otherwise); it need not belong to the sample's ionization mode |
| `source` | `database`, `untargeted` or `manual` |
| `fit_score`, `mz_error_ppm`, `abundance_error` | The engine's measurement. `fit_score` is stored and served exactly as supplied |
| `tier` | Optional, and better omitted (below) |
| `engine_tier` | Optional: the tier the engine itself concluded, where its own rules put the peak somewhere the bands would not. Stored as supplied, checked only against the tier vocabulary, and read by nothing that ranks |
| `target_compound_id`, `target_ion_id` | For a `database` row, the library compound and ion it names; a supplied id must exist |
| `owner_sample_peak_id` | For an `iso_child` row, the `sample_peak_id` of its owner row in this import. Resolved to the owner's assignment id when the import finalizes; an owner that is not in the import is a 422. The link is one level deep: an `iso_child` cannot own a row |
| `alternatives`, `provenance` | The inspector's detail: the alternatives the engine considered, and its scoring provenance, subject to the reserved keys below |

### Tiers

A row's tier is read off its **evidence**, the `fit_score` weighted by the
chemical plausibility of `assigned_formula`, under the run's `tier_bands`. The
server derives the evidence itself from those two fields, so a row that states
no `tier` gets the one the bands give it; a row that states one is checked
against that, and a disagreement is a 422 naming the evidence and the fit it
came from. Stating the tier therefore buys nothing and reproduces this server's
plausibility rules at the risk of drifting from them, which is why the field
is better left out. A row with no `fit_score` is not banded: it may be
`unassigned` or `below_assignability`, the two tiers Mascope's own engine
writes with a null score.

An engine whose own rules demote a peak below what its evidence alone earns
keeps that verdict in `engine_tier`. The ledger's *tier disagrees* filter
shows the rows where the two differ; nothing ranks on `engine_tier`.

### Reserved provenance keys

The app reads a few keys out of a row's `provenance` and presents them as its
own judgement, so at the top level of an imported blob these are **stripped**
before the row is stored: `p_correct`, `calibration` and `corroboration`. Two
more are **overwritten** with the server's own derivation: `evidence` and
`plausibility`, the two factors the tier was validated on. A payload carrying
them is not malformed, just not authoritative about them. Keep the engine's own
numbers under a name of its own; `provenance.engine_provenance` is the
convention, and the server stores it verbatim.

The consequence shows in the ledger: an imported row's calibrated P(correct)
is always empty, and a batch peak's P(correct) reflects only members Mascope
calibrated, so it is absent where imported members alone support the peak.

## One import, one or more requests

A dense sample's ledger with alternatives and provenance is tens of thousands
of rows, so an import assembles across requests:

1. The first request carries no `chunk.run_id`, `chunk.index` 0 and a
   `chunk.import_id` of the client's choosing, unique per sample. It creates
   the run in status `importing` and returns its id.
2. Each later request carries that `run_id` and sets `chunk.index` to the
   number of rows the server has accepted so far, which the previous response
   reported as `rows`. The index is a row **offset**, not a sequence counter.
3. The request with `chunk.complete: true` finalizes: payload-wide validation,
   owner resolution, status `completed`, and the batch fold-in. For a slim
   ledger that may be the first and only request.

Every accepted request answers with the standard list envelope, and the state
is the one record in `data[0]`: `peak_assignment_run_id`, `run_status`
(`importing` or `completed`), `rows` and `max_rows_per_request`. Read the
run's state from `data[0].run_status`: the envelope's own `status` is the
literal `"success"` on every accepted request.

**Retries are safe by construction, and have to be**, because the SDK retries
a POST on a timeout and a timeout is exactly the case where the body was
delivered. Re-sending the chunk that produced the current row count is an
idempotent no-op that reports that count again; an index ahead of the count, or
inside an applied chunk, is a 409, and the client resynchronises from the last
`rows` it saw. A retried create is byte-identical to the original, which is why
`import_id` is required: it resolves to the run already made for it instead of
minting a second one. Re-sending the finalizing chunk returns the original
success without folding the batch a second time.

A 409's body is prose, not data. Recover state rather than parse it: the
in-flight run and its id are in the run listing
(`GET /api/peak-assignments/sample/{id}/runs`, which lists runs of every
status), and the accepted row count is in the last response the client saw.

**An abandoned assembly** blocks every later import and in-app run on its
sample until it ends. The client that still holds the run id can carry on
appending; one that cannot deletes it:
`DELETE /api/peak-assignments/sample/{sample_item_id}/runs/{run_id}` removes
an `importing` run with its staged rows, for an editor of the workspace. A
`completed` run is ledger data and cannot be deleted this way; retention is
what ages it out. Without either, the nightly retention pass reclaims an
`importing` run older than 24 hours.

## What an import replaces, and what it keeps

**A run is appended, never written over.** An import always creates a new run
and touches neither an in-app run nor an earlier import. Reads default to the
latest completed run whatever its engine, so a fresh import is what a reader
sees unless they pick another run, and the run selector names the engine.

**Retention is per engine.** The nightly pass keeps the newest two completed
runs per sample *and engine*, so an engine's republished runs age out of its
own quota and never evict the in-app history; across all engines a sample
keeps at most twelve, the in-app engine exempt. A published run that must stay
current is republished, at the cost of its own quota.

**A partial import is allowed, and it replaces the sample in the batch.** A run
may cover a subset of the sample's peaks; the ledger read serves whatever rows
exist. The batch ledger, though, takes a sample's whole contribution from its
latest completed run: publishing twenty rows of interest on a thirty-thousand
peak sample withdraws that sample's other peaks from the batch overview, and
can remove batch peaks it alone supported. Publish the whole ledger where the
batch view matters.

**Calibration is disclosed, not checked.** Mascope refuses an in-app run on a
sample whose m/z calibration is unverified; an import bypasses that, since the
engine calibrated on its own, and the `calibration` it declared is what the
run selector badges. The per-sample offset the batch ledger folds the sample
in with is read from the run's own mass errors, so it is relative to the
engine's calibration.

**Verdicts on imported rows stay out of calibration.** Verifying an imported
assignment is fine, and the verdict is kept, listed and shown. It does not feed
the instrument-wide confidence calibration, which fits on in-app runs only, so
an importer's evidence figures cannot move the P(correct) every assignment
reads from.
