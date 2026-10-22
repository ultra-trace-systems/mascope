# Acquisition records

For the people who write or configure the program that controls an
instrument. A raw file says what the instrument measured. It does not say
which step of which sequence asked for it, in which mode, with which settings,
on which installation. The control program knows, and only while the
acquisition runs. It writes that down as a small JSON document beside the raw
file, an **acquisition record**, and the [File Agent](index.md#the-file-agent)
sends the document with the file's upload.

Mascope uses the record for two things:

- **Chemistry.** Where the record names the file's chemistry, the file is bound
  to the ionization mode with that token before the server looks for a token
  in the file's name. [Instruments & acquisition](index.md#acquisition-records)
  says what the agent and the server do with a record, and what Raw files
  shows.
- **Provenance.** The record carries identifiers that let somebody walk from a
  sample file back to the installation, the run, the step, the mode as applied
  and the configuration as loaded, by identifiers alone. The identifiers go
  into [the batch export](../reference/exports.md#samples) with every sample.

## The sidecar

| | |
|---|---|
| Where | Beside the data file, under the file's whole name and `.mascope.json`: `run_0042.raw` has `run_0042.raw.mascope.json` |
| What | One JSON object, UTF-8, 16 KB at most |
| When | Before the agent uploads the file. A program that embeds the agent writes it in an `Agent.on_ready` step, which runs before the uploader has the file. A program beside a standalone agent writes it before the file has been left alone for the agent's `timeout` |
| How often | Once. A retried upload sends the same document, so the record of an acquisition never changes its identifiers |

The agent never uploads a sidecar as a file, never changes one and never
deletes one. A sidecar that appears after its file was uploaded is not sent.

The schema is `mascope-acquisition/1`, and the Python module
`mascope_sdk.acquisition` is its definition: `AcquisitionRecord` is the model,
`parse()` reads a document, `dump()` writes one and `read_sidecar()` finds a
file's. Both ends of an upload use them, so there is one answer to whether a
document is a record, and a program written in Python can validate what it
writes with the same code the server reads it with.

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

## Fields

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
| `ionization` | The chemistry, as the token of an ionization mode on the server: the string that would otherwise have to be in the file's name. The whole token and nothing else |
| `triggered_at`, `acknowledged_at` | When the program asked the instrument to acquire, and when the instrument answered |
| `step_started_at`, `step_finished_at` | The step's own start and end |
| `settle_time` | Seconds the step waited for the mode to settle |
| `setpoints`, `heaters` | What the mode set, as a flat mapping of `device.setting` to a value |
| `events` | What happened to the step while it ran, each with `at` and `event`: a pause, a resume, a device set up again |
| `channel_csv` | The file of channel readings the program wrote for the step: `name`, `sha256`. Named, not uploaded |
| `clock` | The clock the times were read off: `source`, `timezone` (IANA), `utc_offset` (`+03:00`) |

Three rules hold for every field:

- **Identifiers are UUIDs** ([RFC 9562](https://www.rfc-editor.org/rfc/rfc9562)),
  so they are unique without a registry and read the same on every server. A
  version 7 UUID sorts by when it was made, which suits runs, steps and
  acquisitions. The schema does not ask for a version. It does ask for the
  UUID's own spelling, lowercase with its hyphens
  (`0199b6a0-7c00-7000-8000-000000000001`): uppercase, braces, `urn:uuid:` and
  bare digits are refused, so that the id in the document is spelled as the
  column made from it.
- **Content is identified by its SHA-256**, as 64 lowercase hex digits: a
  configuration file, a mode's definition, a sequence's definition, the
  channel file. The raw file's own hash is not in the record: the agent
  computes it from the file on disk and sends it beside the record.
- **A time is an instant, written one way**: an
  [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339) date-time with its
  offset, UTC by convention (`2026-10-07T12:00:00.000Z`). That is the date,
  `T`, the time to the second or finer, and `Z` or `+03:00`. A time without an
  offset is refused, and so is one written any other way, a count of seconds
  among them.

## Compatibility

A field the schema does not name is kept as it came, at every level, so a
program can write more than a reader knows of. Adding a field is therefore not
a new schema. Anything else - a field that changes its type or its meaning, a
field that becomes required - is `mascope-acquisition/2`, which a reader of
`/1` refuses by name.

Reading is strict otherwise. A UUID and a time are strings, a number is not
read out of a string, and a record with one field that is not what the schema
says is not a record. That is deliberate: a control program's tests can
validate what it writes against the same model, so a fault shows there and not
as a record that half arrived.

It is as strict as it will ever be. A document this version reads is a record
for good, since refusing it later would be the change that needs a new schema.
So the document also has to be JSON and nothing more:

- **No `NaN`, `Infinity` or `-Infinity`**, anywhere, in a field of the schema
  or outside it. They are not numbers JSON has, and a database that stores JSON
  refuses them. Python's `json.dumps` writes them unasked, so a failed reading
  passed straight to it makes one: write `null`, or leave the setting out. A
  number too large to be read as anything but infinity, `1e999`, is refused
  with them.
- **No key twice** in one object. Two readers need not pick the same one.
- **32 levels of nesting at most**, the record itself being the first.

`parse()` is where all of this is held, the spellings of an identifier and a
time included, so a document is a record when `parse()` says so; the model
alone reads more. `dump()` writes nothing `parse()` would not read back, and
refuses a record that holds a number that is not finite. A field that was read
as `null` is written as `null`.

## What the server keeps

A record that is refused at the upload is refused with the reason, and the
agent logs the reason where the instrument's operator sees it and sends the
file without the record. A record the server keeps is stored on the sample
file as it was sent, fields this version does not know included, together
with:

- the four identifiers, `acquisition_id`, `step_id`, `sequence_run_id` and
  `agent_id`. An acquisition is one file: a second file naming the same
  `acquisition_id` is stored without its record, with a warning;
- the file's SHA-256, where the agent reported one and the bytes the server
  received had it. The hash says the file the server holds is the file that
  was sent.

All of them are empty for a file that came with neither, and for every file
uploaded before records existed. The identifiers and the hash are part of
every sample file the API lists, and the batch export writes them beside each
sample as *Acquisition ID*, *Step ID*, *Sequence run ID*, *Agent ID* and *File
SHA-256* ([Export files](../reference/exports.md#samples)). The record itself
is returned only when one file is asked for (`GET /api/sample/files/{id}`).

Each sample made from the file says what bound it: `bound_by` is `declared`
for a sample its record bound, `explicit` for a chemistry a person chose,
`token` for a file-name token and `method` for a binding learnt from the
file's acquisition method. The field is reported and never taken: a client
cannot set it.
