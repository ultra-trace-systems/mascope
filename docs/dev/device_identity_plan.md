# Device identity for instrument agents

Goal: give instrument agents their own first-class identity, so acquisition
uploads stop borrowing a human's credential. Every uploaded file becomes
attributable to the machine that sent it and to the named person who vouched
for that machine; revocation works per machine; and a second factor can be
required of every human account without breaking unattended acquisition.

This document records (1) the problem and the mechanics it grows out of,
(2) the target design, (3) the build phases, (4) the shape of the rollout,
and (5) what the design deliberately does not attempt. The reader-facing
authorization model this extends is described in
[docs/authorization.md](../authorization.md).

> **Status (2026-08-20): phases A-D are built and merged**, in #1851 (TOTP)
> and #1877 (registry, attribution, machine accounts, token lifecycle, the
> new agent). Section 4 has been reconciled with what shipped and now
> describes running code, noting where the build departed from the design
> and what it added on top; sections 5, 6 and 10 say what is left. Phases E
> (the per-deployment rollout) and F (cleanup) have not started, so
> everything about them is still plan. Nothing here is enabled by default:
> `require_device_tokens` ships off, and existing agents keep working until
> a deployment is cut over.

## 1. Problem

Instrument PCs are unattended, frequently shared machines. The File Agent
that runs on them already authenticates well at the transport level: an
opaque, database-backed access token obtained through device pairing, bound
to a service scope by the `X-Service-Name` header, valid only on routes that
opt into token access — it cannot drive the web UI. The credential is fine.
Its *subject* is not: the token belongs to the human who approved the
pairing (`access_token.user_id`), and four problems follow from that.

- **Attribution is discarded.** `sample_file` has no uploader column. The
  identity available at upload time travels only through a transient
  Socket.IO payload for live UI notifications and is never persisted, so
  "who uploaded this file" cannot be answered afterwards for any file.
- **Continuity is hostage to offboarding.** Deactivating the approving user
  revokes their tokens — including the ones unattended agents depend on.
  Sites work around this rationally by pairing agents under shared,
  non-personal logins. Those are exactly what a security review flags:
  shared credentials, no per-person audit trail, no named owner.
- **A second factor cannot be mandated.** While agents ride human
  credentials, enforcing MFA for humans risks stopping acquisition; and a
  shared login cannot meaningfully enroll a second factor at all. (TOTP
  itself ships in PR #1851; this plan is what makes *enforcing* it safe.)
- **Revocation is all-or-nothing.** Tokens are managed per user+service:
  regenerating removes every machine's token for that user, which
  [docs/user/instruments/index.md](../user/instruments/index.md) has to warn
  about today. There is no way to cut loose a single machine.

## 2. Current mechanics (reference)

- Pairing: `POST /api/auth/pairing/{start,poll}` (unauthenticated,
  rate-limited) rendezvous in Redis; `POST /api/auth/pairing/approve`
  (editor+) mints the token with `description="Paired: <hostname>"` —
  `server/backend/src/mascope_backend/api/new/auth/pairing/service.py`.
  The hostname in the free-text description is the only record of which
  machine holds the token.
- Tokens: `access_token` table (`db/models.py`), 360-day lifetime, service
  scopes in `auth/access_token/config.py` (`mascope_sdk`, `file-converter`,
  `tof-agent`, `file-agent`, `export-agent`), validation in
  `auth/access_token/validation.py`, per-request backend selection in
  `auth/backend.py` (bearer tokens work only on `token_access=True` routes).
- Upload paths: resumable tus (primary) and a legacy multipart fallback for
  pre-tus agents, both in `api/routes/sample/files/sample_files_routes.py`.
- Routing: the instrument name is parsed client-side from the uploaded
  filename (`libraries/file/src/mascope_file/name.py`) and selects — or
  auto-creates — the `Acquisitions <instrument>` system workspace
  (`check_instrument_workspace_access(..., allow_new=True)` in
  `api/new/workspaces/dependencies.py`).
- Agent: `agents/file/` (Windows installer built on release). Token stored
  in plaintext `%APPDATA%\Mascope\FileAgent\config.toml`; HTTP calls run
  with `verify=False`.
- The TOF agent predates pairing, is not part of this repository, and is
  **deprecated by this work**: its sites move to the File Agent during the
  rollout, and the server-side `tof-agent` scope, socket namespace, and
  acquisition event handlers are removed in the cleanup phase.

## 3. Goals and non-goals

Goals:

1. Every agent credential belongs to a **registered device** with a
   lifecycle: visible, renameable, individually revocable.
2. Every uploaded file **persists** who sent it — the device for agent
   uploads, the user for interactive/SDK uploads.
3. Every device records a **sponsor**: the named human who approved its
   pairing. Every machine principal has a named accountable person.
4. No human's departure stops ingestion.
5. MFA enforceable for all human accounts with zero effect on acquisition.

Non-goals — each of these is a decision, not an omission:

- **Per-operator attribution of unattended acquisitions.** Which person ran
  the instrument is a fact that lives outside the server's trust boundary
  (a shared OS session on the instrument PC). See section 8.
- **Restricting instrument-name routing.** Deployments deliberately use
  file naming to scope acquisitions into separate `Acquisitions <name>`
  workspaces by campaign or project, even from one physical instrument.
  The name is a customer-controlled routing key, not an identity claim,
  and it keeps working exactly as described in
  [docs/authorization.md](../authorization.md#instrument-workspaces).
  Provenance comes from device attribution, not from constraining names.
- **Zero-touch migration.** Rejected: most installed agents predate pairing
  and get reinstalled during the rollout anyway, and other pending changes
  (password refresh, MFA enrollment) need the same coordination. The
  rollout is one deliberate campaign per deployment (section 6).
- **External identity providers.** TOTP (PR #1851) covers the requirement
  without adding per-deployment infrastructure. An OIDC layer can be
  revisited independently later.

## 4. The design, and what shipped

### 4.1 Device registry

A new `agent_device` table: `device_id`, `name` (defaults to the hostname
reported at pairing, renameable), `service_name`, `sponsor_user_id`
(FK `user`, `ON DELETE SET NULL` — the approver; same pattern as
`workspace_member.granted_by`), `created_at`, `last_seen_at` (updated on
authentication, throttled), `revoked_at` (nullable). `access_token` gains a
nullable `device_id` FK, `ON DELETE CASCADE` so a credential can never
outlive its device row; personal tokens (`mascope_sdk`) leave it null.

Pairing approval turned out to need two stages rather than one: the device
row and its machine account commit together, so `machine_user_id` is never
briefly null, and the tokens are minted afterwards because the token
strategy owns its own session. A failure between the two discards both and
leaves the pending code approvable again.

**Re-pairing a machine adds a device; it never replaces one.** There is no
uniqueness on name and service, and no lookup for an existing device, so a
machine paired twice has two device rows, two machine accounts, and an old
credential that stays valid until its own lifetime lapses. That is the
right default for attribution - history keeps pointing at the device that
actually uploaded - but it means the rollout has to revoke the old device
explicitly, per machine. Section 6 carries that step.

### 4.2 Persisted attribution

`sample_file` gains `uploaded_by_user_id` and `uploaded_by_device_id`
(nullable FKs, `ON DELETE SET NULL` so history survives account removal).
Both upload paths set them at file creation and the values are carried
through the converter chain. Existing rows stay null — pre-feature history
is genuinely unattributable.

**The read half has not shipped.** Listings return the raw column values
and nothing joins them to a person or a machine name; the frontend does not
read them at all, so no "unknown (pre-dates attribution)" wording exists
either. Attribution is recorded and queryable in the database, not yet
visible in the product.

Two mechanics worth knowing before building on this. The device id travels
in the create request body rather than being taken from the caller, because
the converter writes the record back later under its own credential; the
controller honours it only when the caller is the machine account that
device authenticates as, and degrades to unattributed with a warning rather
than failing an ingest. And `uploaded_by_user_id` is always the
authenticated account, which for an agent upload is the **machine account**,
not the sponsor - so an agent upload populates both columns, and neither
names a human.

The migration that adds these columns also adds section 4.8's
`acquisition_timezone` and `utc_offset_source`, so attribution and
timestamp provenance arrived together.

### 4.3 Per-device management

New endpoints under `/api/auth/devices`: list the devices you sponsor,
rename one, revoke **one**, and an admin-only view of every device.

Revocation does more than the one line this section originally gave it, and
the extra work is the point: it deletes the device's tokens *and* every
token the device's machine account holds — including the machine account's
own `file-converter` token, which is not device-bound and would otherwise
keep working — then deactivates the machine account, then stamps
`revoked_at` (idempotently, so the first revocation time survives). The
device row is kept on purpose, so files it uploaded stay explainable.

Two limits that differ from the original sketch. Revoking someone else's
device is bounded by the same role ceiling as user management, so an admin
cannot revoke a device sponsored by another admin or by an owner — not
"admins/owners may revoke any device". And rename stayed sponsor-only:
admins have no rename path. The admin-wide list has no consumer in the UI
yet.

The settings pane's raw per-service "Regenerate" flow is replaced, for the
File Agent, by a "Paired machines" list with per-row rename and revoke;
`Regenerate` remains for `mascope_sdk` personal tokens, whose semantics are
unchanged, and for `tof-agent` and `export-agent`, whose clients cannot
pair and for which it is the only way to issue a credential until phase F
retires them.

### 4.4 Machine accounts

`user` gains `account_type`: `person` (default) or `machine`. A machine
account cannot obtain an interactive session (login refused), has no
password flow, is exempt from the password-change gate and MFA policy by
construction, and is capped at the editor role. Pairing approval
auto-provisions one machine account per device and makes it the token's
subject, which is what breaks the offboarding coupling: sponsors vouch for
devices, they do not own their credentials.

A machine account is addressed as `device-<device_id>@agents.mascope.app`
and named `<machine name> (agent #<device_id>)`. The domain is a
deliberately mail-less subdomain rather than a reserved one, because the
user schema validates the field as an email address; the device id keys
both, so they are unique per device and obviously non-human in a listing.

Workspace behavior follows the existing auto-creation rules
([authorization.md](../authorization.md#instrument-workspaces)): the
machine account takes the "uploading user becomes owner" slot, and global
admins/owners are auto-added as today. One addition: the device's sponsor
is also added as workspace owner, so a plain-editor sponsor keeps seeing
what their machine ingests without an admin's help. Beyond the design, a
new machine account is also mirrored into the system workspaces its sponsor
already belongs to — at exactly editor, and only where the sponsor holds at
least editor — so an agent can ingest for the instruments its sponsor can
see, and no others.

Machine accounts are additionally fenced off from user management: they are
excluded from the user listing, and a shared guard refuses updating,
deleting, resetting MFA on, or stripping tokens from one through the human
user routes. They are managed only through Paired machines.

**Not built:** converting the non-personal accounts deployments already
created as workarounds. There is no admin script and no API path for it, so
the rollout currently leaves those accounts as ordinary person accounts,
still carrying their unusable password-reset paths. Either phase E does it
by hand per deployment, or phase F needs a conversion step; it should not
stay implicit.

### 4.5 Token lifecycle

Device tokens drop from 360 days to a short lifetime with automatic
renewal: the agent rotates its token via an authenticated renewal endpoint,
with a bounded overlap window so an upload in flight never dies mid-rotation.
The short lifetime is enforced only for device-bound tokens, on top of the
unchanged 360-day database-strategy cap; a token past the device lifetime is
refused (renew or re-pair) rather than deleted. Renewal issues a fresh token
whose clock restarts and reaps all but the two newest tokens for the device:
the fresh one and the token it supersedes, which stays usable only until its
own (unchanged) lifetime elapses — the overlap that lets an upload in flight
during the switch finish, without extending any token's life. Implemented
values: a 30-day device lifetime, two tokens kept. The agent renews once a
minute after start (to establish a known expiry), then at half the
server-reported lifetime, never more often than hourly; a server with no
renewal endpoint, or a credential that is not renewable, backs the loop off
to seven days rather than ending it, and a transient failure retries in
five minutes. The lifetime is a server constant, not a deployment
setting.
The machine account's own file-converter token (server-side, not
device-bound) is minted on demand if missing, so a short device lifetime
never strands uploads.

**At-rest digest is deferred to phase F, deliberately.** The token is the
`access_token` primary key and the client holds the raw value, so a digest
cannot coexist with raw tokens in the same column: hashing the existing rows
would invalidate every live credential at once. It becomes clean only once
the campaign (section 6) has re-paired every agent — at which point no raw
device token remains to preserve — so it lands with the phase-F cleanup that
also drops legacy non-device token acceptance, as one re-issue rather than a
mid-transition dual scheme. Recorded in section 10.

### 4.6 Strict mode

A per-deployment config flag, `require_device_tokens` under `[backend]`
(not the `[backend.auth]` working name this section first used): when
enabled, a bearer token for an agent service scope that has no device
binding is refused with an actionable error the caller actually receives —
see 4.7. It ships default-off, is flipped per deployment at
campaign cutover, becomes default-on in a later release, and eventually the
non-device code path is deleted outright. Flipping this flag is the
auditable per-deployment closure event for the whole migration.

### 4.7 Agent

The new File Agent release: pairing is the only way to obtain a credential
(manual token paste is removed — pairing works on LAN and self-hosted
deployments alike); the renewal loop from 4.5; TLS verification on by
default, with an explicit config opt-out for self-signed deployments
instead of today's unconditional `verify=False`. It also becomes the
migration target for TOF-agent sites: file-drop ingestion replaces the
deprecated agent.

"No compatibility shim" turned out to need splitting. The *client* no
longer falls back from resumable to legacy multipart uploads — a 401 at
upload creation used to be read as "this server predates token-accessible
resumable uploads", which latched the process onto the capped,
non-resumable endpoint and blamed the server's version for what was usually
a revoked credential. The *server* still serves the legacy route, because
agents already installed at customer sites use it; it goes in phase F,
after phase E has replaced them.

Four behaviours were added during review, after this section was written,
all of them about the moment a credential stops working — which is when a
non-technical person at an instrument is involved:

- **The agent checks its credential when it starts** and offers to pair
  there and then if the server refuses it, rather than discovering it on
  the first upload. Only an answered refusal prompts: a machine that boots
  before its network is up logs and carries on, since pairing cannot fix
  that. The check runs after the watcher starts, so a prompt nobody answers
  cannot stop the agent collecting files.
- **A refusal mid-session offers the same thing in the console** the agent
  already has open, and resumes the upload it was holding once approved.
  Both paths mean recovery is "start the agent", not "run it with a flag";
  there is no separate pairing entry point.
- **Refusals carry their own message to the caller.** A 401 is otherwise
  genericized to "please sign in", which an unattended agent cannot act on;
  the strict-mode and expired-credential refusals opt out of that and name
  re-pairing instead, while ordinary sign-in failures are unchanged.
- **Permanently refused uploads are not retried.** A file the server
  understood and rejected — most often a name that does not identify an
  instrument — fails once, with guidance on the two ways to fix it, instead
  of re-transferring the whole file ten times.

### 4.8 Acquisition timestamps: the instrument PC's timezone

A correctness fix riding the same vehicle, because it needs exactly the
piece this plan adds — an agent that can state facts about the machine it
runs on.

`sample_file.datetime_utc` is derived from a local timestamp plus a
guessed UTC offset: `mascope_thermo`'s `RawProcessor` always computes the
offset from the *converter host's* current clock
(`libraries/thermo/src/mascope_thermo/processor.py`), and
`mascope_tofwerk`'s processor does the same whenever the HDF5 file lacks
its own offset attribute. The guess is wrong whenever the instrument PC's
timezone differs from the converter host's, or when DST shifted between
acquisition and conversion — and the current arithmetic cannot even
represent a west-of-UTC offset correctly (`timedelta.seconds` on a
negative interval). The `fix_helsinki_datetime_utc` admin scripts exist
because one variant of this has already bitten. Everything downstream of
the guess is sound: processors emit `utc_offset`
(`file_converter/schema.py`), and the backend applies it to produce
`datetime_utc`.

The fix replaces the guess with a fact:

- The agent sends the instrument PC's IANA timezone (e.g.
  `Europe/Helsinki`) in the tus creation metadata, next to the filename —
  a zone identifier rather than a numeric offset, so the server can
  resolve the offset *at the file's own acquisition timestamp* and stay
  correct across DST for backlogged or re-uploaded files.
- The backend carries the zone into the converter's file context, and the
  processors resolve `utc_offset` from it. Precedence: an offset the file
  itself carries (the TOF HDF5 attribute, written by the acquisition
  software) wins; the agent-supplied zone is next; the host-clock guess
  remains only as the last resort for old agents and manual uploads.
- `sample_file` records the zone applied and its source (file, agent, or
  guess), so every timestamp is auditable and recomputable later. The
  existing fix scripts are the argument for never applying an offset
  without recording where it came from.

Resolving a zone at an acquisition time raised a case the design did not
anticipate: a bare wall clock is not always one instant. When clocks go
back the hour repeats, and when they go forward it does not exist at all.
Rather than let the library pick silently, the resolution reports which of
those it hit, records both candidate offsets, and settles on the
pre-transition reading — deliberate and greppable, instead of an hour that
is quietly wrong twice a year.

Old agents send nothing and get today's behavior — their uploads are
recorded with `utc_offset_source = 'guess'`, which is also how a deployment
can tell how far the campaign has got: when no new file arrives with
`guess`, or with a null `uploaded_by_device_id`, every agent on it has been
re-paired. Nothing breaks in the transition, and the campaign (section 6)
is what retires the guess in practice.

## 5. Build phases

Phases A-D are **done and merged**; E and F have not started.

- **A — prerequisite (DONE, #1851):** TOTP. It already places pairing
  approval and token regeneration behind MFA re-auth and touches the same
  pairing routes and dialog this plan extends, so it went first and this
  work rebased on top.
- **B — registry and attribution (DONE, #1877):** 4.1, 4.2, 4.3, the 4.6
  flag (default off), the server and converter half of 4.8 (zone accepted,
  applied, and recorded when present), and the assessment checks from
  section 9. Safe to release at any time: existing agents are unaffected
  while the flag is off and send no zone.
- **C — machine accounts (DONE, #1877):** 4.4, including converting the token subject for
  device-bound tokens and the workspace-membership addition.
- **D — new agent (DONE, #1877):** 4.5, 4.7, and the agent half of 4.8 (the machine's
  IANA timezone sent with every upload), plus the installer and
  [docs/user/instruments/index.md](../user/instruments/index.md) rewrite.
- **E — rollout campaign (NOT STARTED):** section 6, per deployment.
- **F — cleanup release (NOT STARTED):** remove the `tof-agent` service scope, socket
  namespace and acquisition event handlers, and non-device token acceptance
  for agent scopes; shrink pairing eligibility accordingly.

  The agent's *client-side* fallback to the legacy multipart endpoint is
  already gone in phase D: every supported server accepts agent TUS
  uploads, so a refusal at upload creation is a rejected credential and is
  reported as one. The legacy route itself stays until this phase, because
  agents already installed at customer sites still use it and phase E is
  what replaces them - removing it earlier would break acquisition at every
  site the moment the server updated.

  Its other consumers are small and must be handled with it: the demo
  bundle rebuild uploads through `mascope_sdk.api_post_file`
  (`tooling/cli/.../demo/_rebuild.py`), which keeps that function public
  API, and `stores/data/modules/sample.js` still carries an `upload` action
  posting to the route with no callers left (the browser uploads through
  Uppy/TUS) - delete it with the route.

  Progress towards this is measurable on a live deployment rather than
  assumed: uploads from an agent that has not been re-paired have
  `sample_file.uploaded_by_device_id IS NULL`, and their acquisition times
  are stored with `utc_offset_source = 'guess'`. When neither appears for
  new files, every agent on that deployment has been replaced.

## 6. Rollout campaign

Per deployment, one coordinated pass — scheduled with the customer, driven
remotely:

1. Server updated to the release carrying phases A–D (normal rollout).
2. Inventory confirmed. Which machines hold agent tokens is derivable
   server-side before any customer contact: `access_token` rows carry
   service, hostname description, and age.
3. Each instrument PC, in a remote session: install the new agent, pair it
   (device row and machine account created; sponsor is the person
   approving). If a machine ends up paired more than once — a retry, or a
   re-pair later — revoke the superseded device: pairing adds, it does not
   replace, so the older credential stays valid for up to its 30-day
   lifetime otherwise (4.1).
4. Personal accounts created with the site; shared operator logins retired.
   Note there is no tooling for converting an existing shared account into
   a machine account (4.4), so this step is retire-and-recreate by hand
   unless phase F builds one.
5. Deployment-wide password refresh triggered (the existing owner-level
   sweep; every account re-chooses a policy-conformant password on next
   sign-in).
6. MFA enrollment per the deployment's policy (PR #1851).
7. Strict mode enabled; remaining legacy agent tokens revoked.
8. Closure recorded: date, devices paired, accounts converted or retired.

Step 8 is the audit evidence. The campaign is finished when every
deployment enforces strict mode — completion is tracked per deployment, not
per release, because a migration that ships but is not enforced anywhere
has not actually happened.

## 7. Compatibility

- Until a deployment reaches step 7, its existing agents work unchanged;
  there is no fleet-wide flag day.
- The demo stack and e2e fixtures pair a device during seeding so the suite
  exercises the real credential path; assessment checks skip against
  targets predating the feature (the suite's existing capability-skip
  pattern).
- `mascope_sdk` personal tokens are deliberately untouched: a notebook acts
  as its person, which is correct attribution already.
- Self-hosting docs ([hosting.md](../hosting.md),
  [maintaining.md](../maintaining.md)) update in the phase that changes
  each behavior they describe.

## 8. Accepted limits

Unattended acquisitions attribute to *device plus sponsor*, not to the
operator standing at the instrument. That is honest: the operator's
identity lives in a shared OS session outside the server's trust boundary,
and any scheme that pretends otherwise (per-operator agent logins on a
kiosk PC) would be worked around within a week. Compensating facts: every
curation action (batches, workspace organization) happens under a personal
login and is attributable; raw-file operator metadata, where the format
carries it, can be surfaced as informational. Deployments that need
badge-level lab attribution should handle it in the lab's own systems.
This limit is documented as an accepted risk with a review date rather
than left implicit.

## 9. Security-assessment additions

New checks in `security/pentest/` (ids indicative): an agent-scope token
without device binding is refused when strict mode is on; revoking one
device invalidates only that device's token while a second device keeps
working; an uploaded file's record carries its device attribution; a
machine account cannot obtain an interactive session. Phase F extends the
upload checks to assert the legacy path is gone.

## 10. Open implementation questions

Settled by the phase A-D build, recorded here so they are not reopened:
token lifetime, cadence and overlap (4.5); the machine-account identifier
convention (4.4); and the token digest, which landed in neither B nor C and
stays deferred to F for the reason 4.5 gives.

Still open:

- **`export-agent`.** No client for it exists in this repository, and
  nothing here can show whether any deployment pairs one — the question the
  design asked is still unanswered, and its phase-F fate follows from the
  answer. It currently keeps its manual-token path because removing that
  would leave no way to issue it a credential.
- **Converting the non-personal accounts deployments created as
  workarounds** (4.4). The design assumed the rollout would do it; nothing
  was built, so it is either a manual step in E or a tool in F.
- Whether the web upload path should pass the browser's timezone as a
  weaker hint for 4.8, and how its provenance is labeled if so. Nothing was
  built; the server-side hook is generic enough to accept one, and the
  `utc_offset_source` vocabulary would need a fourth value.
- Whether to ship a zone-aware backfill tool for historical
  `datetime_utc` rows (the existing fixed-offset admin script, upgraded
  to resolve a zone per timestamp), applied per deployment where the
  instrument PC's zone is known. Untouched.
- **Whether attribution should be visible in the product** (4.2). It is
  recorded but not surfaced anywhere; leaving it query-only is a defensible
  answer, but it should be a decision rather than an oversight.
