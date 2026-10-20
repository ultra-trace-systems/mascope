# SDK & API

Load and analyse Mascope data from Python (notebooks or scripts).

```sh
pip install mascope_sdk
```

```python
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium")
```

!!! tip "New to Python?"
    Follow the step-by-step [getting started guide](getting-started.md) — it
    walks you from installing an editor to running your first tutorial
    notebook in about 15 minutes.

Full reference, configuration, and tutorial notebooks: see the
[SDK readme on GitHub](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/sdk/README.md).

Every frame a loader returns names the server it came from in
`peaks.attrs["provenance"]`: the deployment's id and the Mascope version it
runs, among others. Keep it beside a figure you publish, and the figure names
the build its numbers came from. Saving the frame as CSV or Excel drops it;
the SDK readme shows how to keep it.

A load that could not read every sample returns what it read, and says so:
`peaks.attrs["complete"]` is False and `peaks.attrs["failures"]` names each
sample that failed and why. A script that loads more samples than the
`confirm_above` threshold (100 by default) cannot answer the confirmation
prompt, so the load raises `ConfirmationRequiredError` instead of starting;
pass `confirm_above=None` to load without asking.

## Built on the SDK: Peaky

Want the analysis without writing the notebook code?
[**Peaky**](https://github.com/ultra-trace-systems/peaky) is an AI-native
analysis toolbox built on the Mascope SDK — untargeted chemical-formula
assignment, time-series clustering, Van Krevelen plots, and PDF reports,
driven in plain language through a coding agent such as
[Claude Code](https://claude.com/claude-code). It is the SDK-powered,
AI-driven power-user path: a complement to the web app, not a replacement —
Mascope stays the system of record, and Mascope's scoring is the only scorer
in the loop, so results are reproducible and auditable.

## Calling the API directly

The SDK is a client for Mascope's HTTP API, which any language or tool can call
as well. Each Mascope server describes that API in an
[OpenAPI](https://www.openapis.org/) document served next to these docs,
<a href="../openapi.json"><code>openapi.json</code></a>: import it into an API
client such as Postman or Insomnia, or generate a client library from it. Each
server serves the copy built with it, so the document matches the version that
server runs and names that version in its `info.version`.

- Paths are relative to your server's address: `/api/workspaces` is
  `https://your-instance.mascope.app/api/workspaces`.
- Authenticate the way the SDK does, with an
  [API token](getting-started.md#generate-an-api-token) sent as
  `Authorization: Bearer <token>` together with the header
  `X-Service-Name: mascope_sdk`, which names the service the token was
  generated for (`ai-agent` for a token generated for an
  [AI agent](getting-started.md#generate-an-api-token)). A token sent without
  that header is refused with 401.
- Tokens are accepted by the operations the SDK uses, which the document marks
  with the `APIToken` scheme; the rest of the API answers only to the web app's
  signed-in session.

### What a token may change

A token acts as you: it can do what your role allows, and no more. Most of
the API it reaches only reads, but these writes accept a token too, each with
the role it needs in the web app:

| Change | SDK | Operations | Role needed |
|---|---|---|---|
| Datasets: create, rename or describe, move to another workspace, delete | `mascope.datasets.create`, `.update`, `.move`, `.delete` | `POST /api/workspaces/{ws}/datasets`, `PATCH` and `DELETE /api/workspaces/{ws}/datasets/{id}`, `POST .../{id}/move` | editor in the workspace (a move: in both); a locked dataset needs the global admin role |
| Batches: update, copy, delete | `mascope.batches.update`, `.copy`, `.delete` | `PATCH` and `DELETE /api/sample/batches/{id}`, `POST /api/sample/batches/{id}/copy` | editor on the batch, and for a copy on the destination dataset; a locked batch needs the global admin role to update and the owner role to delete. An update sets the batch's target collections to the list it sends, so send the current list to keep them; an empty one detaches them all |
| Samples: update, move, copy | `mascope.samples.update`, `.move`, `.copy` | `PATCH /api/sample/items/{id}`, `POST /api/sample/items/move`, `POST /api/sample/items/copy` | editor on the samples (a copy: guest is enough) and on the destination batch; a locked sample needs the owner role to update and cannot be moved |
| Target collections: create, update, associate with batches, delete | `mascope.collections.create`, `.update`, `.associate`, `.delete` | `POST /api/target/collections`, `PATCH` and `DELETE /api/target/collections/{id}` | editor in the collection's workspace (a global collection: global admin); to only associate batches, guest on the collection and editor on the batches |
| Target compounds: add, update, remove | `mascope.compounds.add`, `.update`, `.remove` | `POST` and `PATCH /api/target/compounds`, `DELETE /api/target/compounds/{id}` | the global editor role; the compound list is shared by every workspace. Deleting a collection with `delete_orphan_compounds=true` also removes the compounds no other collection uses, with the collection's role alone |
| Matches: save a sample's or a batch's, or rebuild a batch's | `mascope.matching.save_sample`, `.save_batch`, `.resave_batch` | `POST /api/match/aggregate/sample/{id}/save`, `POST /api/match/aggregate/batch/{id}/save`, `POST /api/match/aggregate/batch/{id}/resave` | editor on the sample or batch |

A write your role does not allow is refused with a 403, which says only
that it was refused. The SDK raises it as `PermissionDeniedError`, naming
what the call acted on (`object_type`, `object_id`) and the role this table
gives for it (`required_role`). Running a sample's or a batch's matching
without saving it (`mascope.matching.aggregate_sample`, `.aggregate_batch`)
changes nothing and needs the guest role. The
[SDK readme](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/sdk/README.md#changing-data)
shows the write methods at work.

Besides these, a token can:

- upload raw files, and delete the raw files no sample uses
  (`POST /api/sample/files/delete`, admin in the instrument's workspace);
- add an instrument function (`POST /api/instrument_configs`, the global
  editor role);
- run and review peak assignments and the batch peak ledger, and refit an
  instrument's peak-assignment confidence calibration (a superuser).

Everything else, such as workspaces and their members, m/z calibration,
ionization modes, and rematching or removing matches, needs the web app.

!!! warning
    Deleting a dataset, a batch, a collection or raw files, removing a
    compound and rebuilding a batch's matches cannot be undone. Keep your token as secret
    as your password: anyone holding it can make these changes as you.
    Generating a new token replaces the old one.

<!-- TODO Phase 3: publish the SDK README content into this section as the single
source, or keep this page thin and deep-link. See the roadmap. -->
