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

## 4. Target design

### 4.1 Device registry

A new `agent_device` table: `device_id`, `name` (defaults to the hostname
reported at pairing, renameable), `service_name`, `sponsor_user_id`
(FK `user`, `ON DELETE SET NULL` — the approver; same pattern as
`workspace_member.granted_by`), `created_at`, `last_seen_at` (updated on
authentication, throttled), `revoked_at` (nullable). `access_token` gains a
nullable `device_id` FK; personal tokens (`mascope_sdk`) leave it null.
Pairing approval creates the device row and its token in one step.

### 4.2 Persisted attribution

`sample_file` gains `uploaded_by_user_id` and `uploaded_by_device_id`
(nullable FKs, `ON DELETE SET NULL` so history survives account removal).
Both upload paths set them at file creation, and the values are carried
through the converter chain and surfaced in file listings. Existing rows
stay null — pre-feature history is genuinely unattributable, and the UI
should say "unknown (pre-dates attribution)" rather than guess.

### 4.3 Per-device management

New endpoints: list own devices, rename, revoke **one** (removes its tokens,
sets `revoked_at`); admins/owners additionally list and revoke any device.
The settings pane's raw per-service "Regenerate" flow is replaced, for agent
services, by a "Paired machines" list; `Regenerate` remains for
`mascope_sdk` personal tokens, whose semantics are unchanged.

### 4.4 Machine accounts

`user` gains `account_type`: `person` (default) or `machine`. A machine
account cannot obtain an interactive session (login refused), has no
password flow, is exempt from the password-change gate and MFA policy by
construction, and is capped at the editor role. Pairing approval
auto-provisions one machine account per device and makes it the token's
subject, which is what breaks the offboarding coupling: sponsors vouch for
devices, they do not own their credentials.

Workspace behavior follows the existing auto-creation rules
([authorization.md](../authorization.md#instrument-workspaces)): the
machine account takes the "uploading user becomes owner" slot, and global
admins/owners are auto-added as today. One addition: the device's sponsor
is also added as workspace owner, so a plain-editor sponsor keeps seeing
what their machine ingests without an admin's help.

Existing non-personal accounts that deployments created as workarounds are
converted to machine accounts during the rollout, which also retires their
unusable password-reset paths.

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
values: a 30-day device lifetime, two tokens kept; the agent renews at
roughly half its lifetime.
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

A per-deployment config flag (working name
`[backend.auth] require_device_tokens`): when enabled, a bearer token for an
agent service scope that has no device binding is refused with an
actionable error. It ships default-off, is flipped per deployment at
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
deprecated agent, and no compatibility shim is carried.

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

Old agents send nothing and get today's behavior; nothing breaks in the
transition, and the campaign (section 6) is what retires the guess in
practice.

## 5. Build phases

- **A — prerequisite:** land PR #1851 (TOTP). It already places pairing
  approval and token regeneration behind MFA re-auth and touches the same
  pairing routes and dialog this plan extends, so it goes first and this
  work rebases on top.
- **B — registry and attribution (server only):** 4.1, 4.2, 4.3, the 4.6
  flag (default off), the server and converter half of 4.8 (zone accepted,
  applied, and recorded when present), and the assessment checks from
  section 9. Safe to release at any time: existing agents are unaffected
  while the flag is off and send no zone.
- **C — machine accounts:** 4.4, including converting the token subject for
  device-bound tokens and the workspace-membership addition.
- **D — new agent:** 4.5, 4.7, and the agent half of 4.8 (the machine's
  IANA timezone sent with every upload), plus the installer and
  [docs/user/instruments/index.md](../user/instruments/index.md) rewrite.
- **E — rollout campaign:** section 6, per deployment.
- **F — cleanup release:** remove the `tof-agent` service scope, socket
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
   approving).
4. Personal accounts created with the site; shared operator logins retired.
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

- Device-token lifetime and renewal cadence, and the rotation overlap
  window.
- Machine-account identifier convention (synthetic, non-routable, and
  obviously non-human in user listings).
- Whether `export-agent` pairing is in use anywhere; its phase-F fate
  follows from that.
- Whether the token-digest migration lands with phase B or C.
- Whether the web upload path should pass the browser's timezone as a
  weaker hint for 4.8, and how its provenance is labeled if so.
- Whether to ship a zone-aware backfill tool for historical
  `datetime_utc` rows (the existing fixed-offset admin script, upgraded
  to resolve a zone per timestamp), applied per deployment where the
  instrument PC's zone is known.
