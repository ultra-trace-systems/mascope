# File Agent self-update

Goal: make the File Agent installed on an instrument PC able to move to a
newer release without anyone touching that PC, so that the next hands-on
campaign across customer sites is the last one. The server decides which
release its agents should run; the agent fetches it, proves it came from us,
installs it between uploads, and puts the previous release back if the new
one does not come up.

This document records (1) why the timing matters more than the mechanism,
(2) the mechanics the design builds on, (3) goals and non-goals, (4) the
design, (5) how it stays safe, (6) the build phases, (7) the rollout,
(8) the limits accepted, and (9) the questions still open. The code paths are
cited so the proposal can be checked against what runs.

> **Status (2026-09-23): proposal, nothing built.** Written for sign-off
> before any code.

## 1. Why now

Every agent at a customer site gets replaced once more regardless of this
work. Phase E of the [device identity plan](device_identity_plan.md) - pair
each instrument PC as a device, retire shared logins, turn on strict mode -
is a scheduled, remote session per site, and it has not started. Agents that
predate pairing cannot be carried over without a person approving the
pairing, and the TOF agent is not in this repository at all and is replaced
by the File Agent in the same pass. Most sites still run agents old enough
to upload only through the legacy multipart route (resumable uploads first
shipped in the agent in v1.5.0).

That visit is unavoidable, so the design question is only what it installs.
If the agent installed during phase E can update itself, phase E is the last
campaign. If the updater ships afterwards, it takes one more campaign to put
it in place, which is exactly the cost this work exists to avoid. **This has
to ship, and be proven on the internal deployment, before phase E starts.**

The ingest work ([ingest routing and splitting](ingest_routing_and_splitting.md))
lowers the stakes but does not remove them. It deliberately keeps the agent
thin - routing, processing status and parking all live on the server, and
"File Agents need no change" is one of its rules - yet it still has agent
items ahead of it: the sidecar upload of phase 7, and whatever the status
follower (#2169) grows into. Each of those would otherwise wait for a
campaign.

## 2. Current mechanics (reference)

- **Install.** `agents/file/installer.iss` (Inno Setup): a per-user install
  with `PrivilegesRequired=lowest`, into
  `%LocalAppData%\Programs\Mascope File Agent`, with no elevation prompt.
  Settings, the device token, logs and runtime state live in
  `%AppData%\Mascope\FileAgent` and are left in place on uninstall and
  reinstall; `config.py` already migrates older settings forward. Replacing
  the program therefore keeps the pairing and the configuration.
- **Process.** A PyInstaller `--onefile --console` executable
  (`agents/file/build.ps1`), started from the Startup folder at sign-in when
  the installer's "startup" task is chosen. It runs in a console window;
  closing the window is the documented way to stop it
  ([docs/user/instruments/index.md](../user/instruments/index.md)). Nothing
  stops a second copy from running: there is no single-instance guard.
- **Uploads.** `FileUploader` in `agents/file/src/mascope_file_agent/main.py`
  queues a file when the watcher sees it created or moved in, waits until
  its size settles and the file is no longer locked, then uploads it over
  TUS with retries; what fails is copied to `failed_uploads`. The queue lives
  in memory only.
- **Files that arrive while the agent is stopped are never uploaded**
  (#1905). There is no look at the folder on start, deliberately: a
  recursive watch can cover years of acquisitions on a network share, and a
  sweep risks uploading files that were never meant to go. The guided
  setup's own look at the folder was bounded for the same reason (the
  `_folder_evidence` limits in `wizard.py`).
- **Version.** `build.ps1` writes `mascope_file_agent/_version.py` from the
  release tag; the agent sends it on every request as `X-Agent-Version`
  (`libraries/sdk/src/mascope_sdk/_agents.py`), and the server stores it per
  device as `agent_device.last_seen_version`
  (`server/backend/src/mascope_backend/db/models.py`). "Paired machines" in
  the web app already lists it (`server/frontend/src/lib/devices.js`), so an
  upgrade across instrument PCs can already be followed.
- **Capabilities.** `GET /api/version` is readable with a device token and
  returns `SERVER_CAPABILITIES`
  (`server/backend/src/mascope_backend/capabilities.py`); the agent reads it
  at start. This is the pattern for anything an older server does not do.
- **Release.** The `build-file-agent` job in
  `.github/workflows/build-release-images.yaml` builds the agent on a Windows
  runner, signs the payload and the installer through Azure Artifact
  Signing, verifies the signature, and uploads two identical assets to the
  GitHub release: `Mascope-File-Agent-Setup.exe` (what the web app's download
  button points at through `releases/latest`) and
  `Mascope-File-Agent-Setup-<tag>.exe`. A pre-release is never
  `releases/latest`. The documented rollback of clearing
  `AZURE_SIGNING_ACCOUNT` produces an **unsigned** build.
- **Servers already update themselves**: `mascope prod update --auto` on a
  timer follows `releases/latest` where a deployment enables it, and pins
  `MASCOPE_VERSION` otherwise. The agent release that belongs to a server is
  the one with the same tag.

## 3. Goals and non-goals

Goals:

1. An agent paired to a server moves to the release that server asks for,
   without anyone at the instrument PC.
2. Nothing runs on an instrument PC unless it is signed by our publisher
   identity, whatever the server or the network says.
3. An update never loses a file the running agent had already seen or would
   have seen, including on an instrument that acquires without pause.
4. A release that does not come up is rolled back on the PC by itself, and a
   bad release can be pulled back across a deployment from the server.
5. Each deployment's owner decides whether its agents update on their own,
   only report that an update is available, or stay where they are.

Non-goals, each a decision:

- **Catching up after a crash, a reboot or a closed window** (#1905). That
  gap is accepted: the operator fills it by hand, as today. What changes is
  that the agent now *says* there was a gap, and when (4.8). Only the
  restarts the updater itself causes are covered, because nobody knows they
  happened (4.5).
- **Updating agents that predate this work, or the TOF agent.** They cannot
  be reached; phase E replaces them by hand.
- **Delta or background-download optimisations.** The installer is small
  enough to fetch whole.
- **Customer software-distribution tools** (Intune, SCCM, winget). Nothing
  here stops a managed site from using them with `policy = "off"`; a winget
  manifest can be added later if a site asks.
- **Moving the agent to a Windows service.** A separate decision (9), though
  phase E is the natural time to take it, since it needs elevation once.

## 4. The design

### 4.1 The server decides the release, the agent pulls it

The server answers a device-token request `GET /api/auth/devices/update`
(announced by a new capability, `agent_update_offers`) with either nothing
to do or an offer:

```json
{"version": "v1.9.0", "url": "https://github.com/ultra-trace-systems/mascope/releases/download/v1.9.0/Mascope-File-Agent-Setup-v1.9.0.exe", "policy": "auto"}
```

The target is resolved in this order:

1. a version pinned for the device (per-device override, 4.9);
2. a version pinned for the deployment (`file_agent_version`);
3. the server's own release, when `runtime.version` names a release tag -
   agent and server from the same tag are the pair the release was tested as;
4. otherwise no target. A deployment tracking `latest`, or a source checkout
   reporting `unknown`, offers nothing until it pins one.

Following the server (rule 3) only ever moves forward: an agent newer than
its server stays where it is. A pin (rules 1-2) is exact and may go back,
which is how a bad release is withdrawn across a deployment.

The agent asks at start and then every six hours, with jitter, from its own
daemon thread alongside the token renewal and status threads that `run()`
already starts. An older server announces no capability and is never asked.

Pulling rather than pushing needs no new channel into the PC, keeps working
through the same proxy and TLS path the uploads use, and means the server
never needs to know whether a PC is online.

### 4.2 Where the installer comes from

The offer's URL is the release's versioned GitHub asset by default.

Some instrument PCs reach only their Mascope server. For them the server
serves the installer itself, from a copy placed in its runtime home at
update time, and the offer points there instead. That is a later phase:
whether it is needed is a per-site fact to collect during phase E (9). The
opposite case - a server with no internet route and a PC that has one - is
the one the [isolated deployment plan](isolated_deployment_plan.md) describes,
and the GitHub default already covers it.

Where the bytes come from does not affect trust (4.3), so both sources are
treated the same way.

### 4.3 What decides whether it runs

The server chooses a version; the signature decides whether it runs. Before
launching anything it downloaded, the agent checks:

- the Authenticode signature of the setup executable, verified by Windows
  (`WinVerifyTrust` through ctypes), with the chain valid and timestamped;
- the signer's subject pinned to the publisher identity the release job
  signs with, compiled into the agent;
- the version resource of the setup executable equal to the offered version,
  so a genuine but different signed release cannot be substituted for the
  one offered.

Authenticode already covers the file's integrity, so no separate hash is
needed for that; publishing a hash as well is an open question (9). A
compromised or misconfigured customer server can therefore make an agent
install another genuine release, and nothing else. An unsigned release -
the documented signing rollback - is refused everywhere, which is the
intended failure mode: auto-update stops fleet-wide until a signed release
exists, and manual installs continue to work.

### 4.4 When it installs

An instrument can acquire without pause: a TOF writing one long file after
another, or an Orbitrap sequence of two-minute files, always has a file in
the queue or still being written. "Wait until idle" would never update such
an agent. The update instead proceeds as soon as no upload is in flight:

1. stop taking files from the queue;
2. let the uploads already running finish (their retries included);
3. write the handoff (4.5), naming every file the agent had seen but not yet
   uploaded;
4. hand over to the update helper (4.6) and exit.

A deployment may set a maintenance window (local hours on the PC) outside
which step 1 does not start. By default there is none.

### 4.5 The restart handoff

This is the one place the agent looks at its folder on start, and it is
bounded so that neither objection in #1905 applies to it.

The old agent writes `%AppData%\Mascope\FileAgent\update-handoff.json`:

- `stopped_at` (UTC);
- `from` and `to` versions;
- `pending`: every path in the queue or still being waited on;
- `recent_dirs`: the watched folder, plus every folder in which the agent
  saw a file arrive in the last 24 hours.

The new agent, started with `--after-update`:

1. queues every `pending` path that still exists. These are files the agent
   had already chosen, so the normal size-settled and lock checks apply and
   nothing new is decided;
2. lists each of `recent_dirs` **non-recursively**, plus any subfolder of
   them whose creation time is after `stopped_at` (a new day's folder), and
   queues files matching the mask whose creation time (`st_birthtime`,
   available on Windows from Python 3.12) is after `stopped_at` minus a
   minute. `failed_uploads` is never looked at;
3. deletes the handoff.

What this does *not* do is the reason it is safe:

- It never runs without a handoff, so a first start, a crash, a reboot or a
  closed window uploads nothing from the folder.
- It never walks the tree. It lists a handful of folders the old agent named.
- It only picks up files created in the seconds the agent was not running,
  which the running agent would have uploaded anyway.
- More than 50 candidates means something other than a restart happened.
  Then none are uploaded and the count is reported as a gap (4.8).

The server refuses a filename it already holds, so a file that reaches both
the old and the new agent is not stored twice.

A file moved into the folder during the gap keeps its old creation time and
is missed. That is accepted (8).

### 4.6 The update helper

The agent cannot replace its own executable while it runs, and the code that
decides whether to roll back must not be the code being rolled out. A copy
of the **running** executable, placed in `%AppData%\Mascope\FileAgent\update\`,
does the work when started as `--apply-update <handoff>`:

1. wait for the old agent's process to exit (by PID);
2. keep the currently installed release's setup executable, downloaded or
   kept from the last update, as the rollback target;
3. run the new installer with
   `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /LOG=<file>`;
4. start the new agent with `--after-update`;
5. wait for its health mark (4.7), up to ten minutes;
6. if none comes, stop the new agent, reinstall the kept release, start it,
   and record the failed version so it is not tried again until the server's
   target changes.

The installer's post-install launch stays `skipifsilent`, so a silent
install does not start a second copy; the helper starts the agent. A named
mutex taken by the agent at start is the single-instance guard it lacks
today. The helper waits on it too, which covers a copy that the Startup
folder or a user launched in the meantime.

The helper runs the old release's code, so the rollback logic that runs is
always from a version that has already worked on that PC.

### 4.7 The health mark

The new agent writes `{version, healthy_at}` to its state file once:

- its configuration loaded and the watcher is running; and
- the start-up credential check (`_check_credential_at_start`) either passed
  or failed at the transport level (server unreachable).

A server that is down during the update is not the new release's fault, and
must not trigger a rollback. A crash, a configuration error, or the server
refusing the credential after an update, is.

### 4.8 Reporting, and the gap made visible

- **Updates.** Each step is logged, and `X-Agent-Version` shows the result
  in Paired machines. A rollback, or a refused signature, is reported to the
  server and becomes a notification for the device's sponsor and the
  instrument workspace's owners: a new kind in the phase 1 notifications
  (#2166).
- **Gaps (#1905).** The agent records a `last_alive` time in its state file
  every minute. On any start without an update handoff, if the agent was
  gone for more than five minutes, it logs a warning naming the interval:

  > The agent was not running from 02:10 to 08:45. Files that arrived in
  > that time were not uploaded: upload them in Raw files, or move them out
  > of the watched folder and back.

  The same interval goes to the server as a notification. This costs no
  folder scan and uploads nothing. It turns today's silent loss into one
  the operator is told about, which is what #1905 found worst about the gap.
  The user guide gets the sentence #1905 asks for.

### 4.9 Server side

- `[backend]` settings: `file_agent_updates = "auto" | "notify" | "off"`,
  optional `file_agent_version` (pin), optional `file_agent_update_window`.
- `agent_device` gains `update_policy` and `target_version` (nullable,
  per-device overrides), so one instrument PC can be a canary or be held
  back.
- `notify`: the offer carries `policy: "notify"`, and the agent logs that a
  release is available without installing it. Paired machines shows
  "update available" next to the version it already lists.
- Paired machines gets an "update now" action, which sets that device's
  `target_version` for one update. This is also how the first canary is
  driven.

## 5. Security

The updater makes the release pipeline a way to run code on every
instrument PC that follows it. That is a larger responsibility than
distributing an installer that people choose to run, and the design treats
it as one:

- **The trust anchor is the signing identity**, not GitHub, the network or
  the customer server (4.3). The `release-signing` environment is
  deliberately unprotected today so releases stay automatic, and the
  workflow comment says why. Whether a File Agent release that auto-updates
  customers should wait for a required reviewer is a decision to take with
  this work, not after it (9).
- **The server can only choose among genuine releases**, including older
  ones. Pinning an old release with a known defect is the residual risk. The
  agent refuses anything older than a floor compiled into each release,
  raised when a release fixes something that must not come back.
- **TLS verification off** (`verify_tls = false`, for self-signed servers)
  does not weaken the update: the signature check does not depend on the
  connection.
- **Security assessment checks** (`security/pentest/`): an installer signed
  by another subject is refused; an unsigned one is refused; a genuine
  installer offered under another version is refused; a pin below the floor
  is refused.

## 6. Build phases

- **A - agent prerequisites, useful on their own.** Single-instance mutex;
  state file with `last_alive`; the gap warning (4.8, log only). Closes the
  "silent" half of #1905.
- **B - updater.** Server offer endpoint, capability and settings (4.1,
  4.9), default `off`; agent offer loop, download, verification (4.3),
  drain and handoff (4.4, 4.5), update helper and health mark (4.6, 4.7);
  "update now" in Paired machines. Proven by updating the internal
  deployment's agent between two consecutive releases, including a
  deliberately broken one that must roll back.
- **C - reporting.** Update-failure and gap notifications on the server
  (4.8); `notify` shown in Paired machines.
- **D - phase E of the device identity plan** installs the agent from B.
  Each site's owner chooses a policy during the same session.
- **E - later, as sites ask.** Server-hosted installers (4.2); per-device
  policy editing in the UI; maintenance windows beyond the setting.

B must be released before phase E's first site visit. A and C can land
either side of it.

## 7. Rollout

- The internal deployment's agent first, `auto`, across at least two
  releases, one of them a planted failure.
- Then one customer site that agrees to `auto`. Its server's own update
  moves its agents, so agents are staged per deployment for free: a site's
  agents update when its server does, not when a release is published.
- The rest per phase E. Paired machines is the progress view, and
  `last_seen_version` gives the same answer in SQL.

## 8. Accepted limits

- Agents installed before this work, and every TOF agent, are updated by
  hand one last time.
- An agent that is not running is not updated. A PC left at the sign-in
  screen after a Windows reboot runs no agent at all, and is updated on its
  next start. That is the service question in 9.
- Files that arrive while the agent is stopped for any reason other than an
  update are still not uploaded. They are reported instead (4.8).
- A file moved into the watched folder during an update's restart keeps its
  old creation time and is missed by the handoff.
- A release that installs, starts and passes the health mark but misbehaves
  later is not rolled back automatically. A deployment pin undoes it
  (4.1).

## 9. Open questions

- **Default policy** for a deployment that has not chosen: `off` (safe,
  but the campaign's benefit depends on sites opting in) or `auto` agreed
  during phase E.
- **Required reviewers on `release-signing`** once agents follow releases
  unattended (5).
- **Publishing a SHA-256** for each installer as well as signing it: it
  would pin the exact build, at the cost of a second value to carry from the
  Windows job to the server.
- **Which instrument PCs cannot reach GitHub.** Collect this during phase E;
  it decides whether 4.2's server-hosted path is needed at all.
- **Service or scheduled task instead of the Startup folder**, so the agent
  runs without a signed-in user. It needs elevation once, which phase E
  provides, and it changes how the helper starts and stops the agent.
- **The version floor**'s first value, and who raises it.

## 10. Picking this up

| Area | Where | Section |
|---|---|---|
| Agent run loop, queue, watcher | `agents/file/src/mascope_file_agent/main.py` (`run`, `FileUploader`, `FileSystemWatcher`) | 4.4, 4.5 |
| Agent settings and state location | `agents/file/src/mascope_file_agent/config.py` | 4.5, 4.8 |
| Bounded folder look (precedent) | `agents/file/src/mascope_file_agent/wizard.py` (`_folder_evidence`) | 4.5 |
| Installer | `agents/file/installer.iss`, `agents/file/build.ps1` | 4.6 |
| Release build and signing | `.github/workflows/build-release-images.yaml` (`build-file-agent`) | 4.3, 5 |
| Version header | `libraries/sdk/src/mascope_sdk/_agents.py` | 2 |
| Capabilities | `server/backend/src/mascope_backend/capabilities.py`, `api/new/version/routes.py` | 4.1 |
| Devices | `server/backend/src/mascope_backend/db/models.py` (`AgentDevice`), `api/new/auth/devices/` | 4.1, 4.9 |
| Paired machines UI | `server/frontend/src/lib/devices.js` | 4.9 |
| Notifications | the phase 1 notification store (#2166) | 4.8 |
| Missed-files issue | #1905 | 3, 4.5, 4.8 |
