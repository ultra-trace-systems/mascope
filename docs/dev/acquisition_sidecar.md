# The acquisition record: what an instrument's control program says of a file

Status: **the schema and the File Agent's half are built; the server's half
is not** (2026-10-07). A control program can write a record and an agent
sends it to a server that announces it keeps one. No server announces that
yet: storing the record, routing by it and exporting its identifiers are the
next change, and [The server](#the-server) says what that will be.

## What it is for

A raw file says what the instrument measured. It does not say which step of
which sequence asked for it, in which mode, with which settings, on which
installation. The program that controls the instrument knows, and only while
the acquisition runs. It writes that down as a small JSON document beside the
raw file, a **sidecar**, and the File Agent sends the document with the
file's upload.

Two things in Mascope want it:

- **Chemistry routing.** The record names the file's chemistry outright, which
  is rung 0, "declared", of the binding ladder
  ([ingest_routing_and_splitting.md](ingest_routing_and_splitting.md), section
  5.2): the acquisition's own word, ahead of a filename token.
- **Provenance.** The FAIR roadmap ([fair_roadmap.md](fair_roadmap.md)) finds
  user data short of globally unique identifiers (F1) and of detailed
  provenance (R1.2). Mascope is the only place the acquisition context still
  exists when a dataset is deposited, and the control program is the only
  place the instrument's configuration and the step's timing exist at all.
  The record carries identifiers that let somebody walk from a sample file
  back to the installation, the run, the step, the mode as applied and the
  configuration as loaded, by identifiers alone.

## The sidecar

| | |
|---|---|
| Where | Beside the data file, under the file's whole name and `.mascope.json`: `run_0042.raw` has `run_0042.raw.mascope.json` |
| What | One JSON object, UTF-8, 16 KB at most |
| When | Before the agent uploads the file. A program that embeds the agent writes it in an `Agent.on_ready` step, which runs before the uploader has the file. A program beside a standalone agent writes it before the file has been left alone for the agent's `timeout` |
| How often | Once. A retried upload sends the same document, so the record of an acquisition never changes its identifiers |

The agent never uploads a sidecar as a file, never changes one and never
deletes one. A sidecar that appears after its file was uploaded is not sent.

The schema is `mascope-acquisition/1`, and
`mascope_sdk.acquisition` is its definition: `AcquisitionRecord` is the model,
`parse()` reads a document, `dump()` writes one and `read_sidecar()` finds a
file's. Both ends of an upload use them, so there is one answer to whether a
document is a record.

```json
{
  "schema": "mascope-acquisition/1",
  "source_filename": "run_0042.raw",
  "agent_id": "3f0e8f0c-5d0b-4c7e-9a43-0d8f6c1b2a10",
  "sequence_run_id": "0199b6a0-7c00-7000-8000-000000000001",
  "step_id": "0199b6a0-7c00-7000-8000-000000000002",
  "acquisition_id": "0199b6a0-7c00-7000-8000-000000000003",
  "ionization": "NO3",
  "triggered_at": "2026-10-07T12:00:00.000Z"
}
```

### Fields

Only the first six are required. A program writes what it knows.

| Field | Content |
|---|---|
| `schema` | `mascope-acquisition/1` |
| `source_filename` | The data file's name where it was written, with no folder. The record of another file is not used |
| `agent_id` | The installation that wrote the record. A UUID, made once and kept across upgrades and pairings |
| `sequence_run_id` | One run of a sequence, with all its cycles |
| `step_id` | One step of one cycle. A step that goes on after a pause keeps it; one triggered again is another step |
| `acquisition_id` | This file's acquisition. Normally one to a step |
| `machine` | The computer: `name` (its host name, as the agent reports it when pairing), `windows_machine_guid` |
| `control_program` | `name`, `version`, `brand` |
| `instrument` | The instrument, by the name the server files its data under |
| `kecu` | The unit between the program and the instrument's hardware: `fw_version` |
| `nodes` | The configured devices that were online, each with `name`, `device_name`, `hw_version`, `fw_version` |
| `sequence` | `name`, `cycle`, `step_index`, `loop`, `steps` (each a `mode` and a `duration` in seconds) and the `hash` of the definition as run |
| `configuration` | The configuration files as loaded when the run started, each a `path` relative to the program's configuration folder and a `sha256` |
| `mode` | The mode the step put the instrument in: `name`, its `definition` in full, and the definition's `hash` |
| `ionization` | The chemistry, as the token of an ionization mode on the server: the string that would otherwise have to be in the file's name |
| `triggered_at`, `acknowledged_at` | When the program asked the instrument to acquire, and when the instrument answered |
| `step_started_at`, `step_finished_at` | The step's own start and end |
| `settle_time` | Seconds the step waited for the mode to settle |
| `setpoints`, `heaters` | What the mode set, as a flat mapping of `device.setting` to a value |
| `events` | What happened to the step while it ran, each with `at` and `event`: a pause, a resume, a device set up again |
| `channel_csv` | The file of channel readings the program wrote for the step: `name`, `sha256`. Named, not uploaded |
| `clock` | The clock the times were read off: `source`, `timezone` (IANA), `utc_offset` (`+03:00`) |

Three rules hold for every field:

- **Identifiers are UUIDs** (RFC 9562), so they are unique without a registry
  and read the same on every server. A version 7 UUID sorts by when it was
  made, which suits runs, steps and acquisitions. The schema does not ask for
  a version. It does ask for the UUID's own spelling, lowercase with its
  hyphens (`0199b6a0-7c00-7000-8000-000000000001`): uppercase, braces,
  `urn:uuid:` and bare digits are refused, so that the id in the document is
  spelled as the column made from it.
- **Content is identified by its SHA-256**, as 64 lowercase hex digits: a
  configuration file, a mode's definition, a sequence's definition, the
  channel file. The raw file's own hash is not in the record. The agent
  computes it and sends it beside the record ([How it travels](#how-it-travels)).
- **A time is an instant, written one way**: an RFC 3339 date-time with its
  offset, UTC by convention (`2026-10-07T12:00:00.000Z`). That is the date,
  `T`, the time to the second or finer, and `Z` or `+03:00`. A time without
  an offset is refused, and so is one written any other way, a count of
  seconds among them.

### Compatibility

A field the schema does not name is kept as it came, at every level, so a
program can write more than a reader knows of. Adding a field is therefore
not a new schema. Anything else - a field that changes its type or its
meaning, a field that becomes required - is `mascope-acquisition/2`, which a
reader of `/1` refuses by name.

Reading is strict otherwise. A UUID and a time are strings, a number is not
read out of a string, and a record with one field that is not what the schema
says is not a record. That is deliberate: the control program's tests validate
what it writes against the same model, so a fault shows there and not as a
record that half arrived.

It is as strict as it will ever be. A document this version reads is a record
for good, since refusing it later would be the change that needs a new
schema. So the document also has to be JSON and nothing more:

- **No `NaN`, `Infinity` or `-Infinity`**, anywhere, in a field of the schema
  or outside it. They are not numbers JSON has, and a database that stores
  JSON refuses them. Python's `json.dumps` writes them unasked, so a failed
  reading passed straight to it makes one: write `null`, or leave the
  setting out. A number too large to be read as anything but infinity,
  `1e999`, is refused with them.
- **No key twice** in one object. Two readers need not pick the same one.
- **32 levels of nesting at most**, the record itself being the first.

`parse()` is where all of this is held, the spellings of an identifier and a
time included, so a document is a record when `parse()` says so; the model
alone reads more. `dump()` writes nothing `parse()` would not read back, and
refuses a record that holds a number that is not finite. A field that was
read as `null` is written as `null`.

## How it travels

With the upload's creation request, as two keys of TUS `Upload-Metadata`:

| Key | Value |
|---|---|
| `acquisition` | The sidecar's document, as it was written |
| `sha256` | The data file's SHA-256, as lowercase hex |

The hash is computed by the agent from the file on disk, before the upload is
created, because the metadata goes with the request that creates it. A file
is therefore read twice: once to hash and once to send. It is hashed once
for an upload however many attempts the upload takes, and again only if its
size or its time of last change has moved in between.

Both are sent only to a server that announces
`files_accept_acquisition_metadata` in `GET /api/version`. The record is large
for a request header - up to 22 KB once encoded - and a server that does not
expect it would refuse the whole upload. `mascope_file_agent/provenance.py`
decides, per upload:

| The server | The file has a usable sidecar | What is sent |
|---|---|---|
| keeps them | yes | the record and the hash |
| keeps them | no | the hash |
| does not keep them | either | neither; the log says so once for the first file that had a sidecar |
| refuses to say | either | neither; the log says once that the server is too old to be asked or refused this machine's credential |
| has not answered | yes | nothing: the attempt fails as an unreachable server's does, and is retried |
| has not answered | no | the file, with neither |

A server "has not answered" when it could not be reached, when it failed
(5xx), and when it asked for another try (408, 425, 429): `/api/version` is
rate-limited by the address a request comes from, and one such answer taken
for "keeps nothing" would leave an hour of records behind. Any other status
but 200 is a refusal to say. It stands only for the token it was given to,
since a server that predates the question and one that refused this
machine's credential answer alike.

**Neither may cost a file its upload.** A sidecar that cannot be used - not
JSON, another schema, too large, the record of another file - is left behind
with a warning, and the file goes without it; so is one whose reading failed
in any other way. An upload refused together with its record is made once
more without the record: a proxy in front of the server can refuse the
request for the size of its headers, and that must not set an acquisition
aside. A file set aside in `failed_uploads` has its sidecar copied with it.

One refusal is not answered that way. A 409 is, to the agent, a conflict to
try again and not a request that was rejected for what it is, so the file is
not sent again without its record: it is tried again as it was, and set aside
after the tenth attempt. That leaves a server one way to refuse an upload
outright, record or no record, without being sent the file anyway.

The agent asks what the server can do once and keeps the answer for an hour
(`mascope_file_agent/capabilities.py`), so a server updated under a running
agent starts receiving records without the agent being restarted. The
following of what became of each upload reads the same answer on every pass,
and starts with the same update.

## The server

Not built. What the next change does:

- announces `files_accept_acquisition_metadata`, and accepts request headers
  of the size the record needs, in the backend and in the nginx in front of
  it;
- validates `acquisition` with `mascope_sdk.acquisition.parse()` and keeps the
  document on the sample file, with `acquisition_id`, `step_id`,
  `sequence_run_id` and `agent_id` as indexed columns of their own and
  `acquisition_id` unique, so that every file of a run is one query and a
  repeated upload makes no second record;
- keeps `sha256` beside them;
- binds the file to the mode `ionization` names before it looks at the file's
  name, as rung 0 of the ladder;
- shows all of it in the file's API model, and adds the identifiers to the
  exports.

## Reading the record as provenance

For the deposit-ready package of the FAIR roadmap's phase 3, which is JSON-LD
on schema.org and PROV. Nothing builds the package yet; this is the reading
both sides hold to, so that the package will have what it needs from the
instrument side. The identifiers export as `urn:uuid:<id>`.

| In the record | In PROV |
|---|---|
| the raw file (the sample file) | `prov:Entity`, `prov:wasGeneratedBy` the step |
| the step (`step_id`, its start and end, the trigger and its acknowledgement) | `prov:Activity` with `startedAtTime` and `endedAtTime`, `prov:used` the mode, `prov:wasInformedBy` the run |
| the sequence run (`sequence_run_id`) | `prov:Activity`, `prov:used` the sequence's definition and the configuration |
| the mode and the sequence's definition, by hash | `prov:Entity`, a `prov:Plan` |
| the installation (`agent_id`, `control_program`) | `prov:SoftwareAgent`; the step `prov:wasAssociatedWith` it |
| the instrument (`instrument`, `kecu.fw_version`) | `prov:Agent`; the step `prov:wasAssociatedWith` it |
