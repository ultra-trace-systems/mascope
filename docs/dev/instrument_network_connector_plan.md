# Keeping the instrument PC off the internet: the connector, and which deployment answers which objection

- **Status:** design
- **As of:** 2026-11-18
- **Tracked in:** [Isolated deployments](https://linear.app/ultra-trace/project/isolated-deployments-7585d72b2a80)
  (Linear, P-MAS-10, beside `isolated_deployment_plan.md`; filed 2026-11-17:
  phases A to E are milestones "Connector A" to "Connector E", MAS-957 to
  MAS-972, with the decisions MAS-964 and MAS-969; phase F is MAS-973 in the
  isolated plan's held milestone)
- **What:** Keeping the instrument PC off the internet while Mascope stays in
  the cloud, through a small connector host at the site, and which deployment
  answers which customer objection.

Goal: when a customer says Mascope cannot be in the cloud, find out which of
two questions they are answering, answer the network one with network design
rather than with an on-premises server, and keep on-premises for the customers
whose objection is where the data lives. The instrument PC never needs a route
to the internet in any of the deployments below.

This document records (1) the setting, (2) the two questions a "no cloud"
objection mixes, (3) what the File Agent does on the network today, verified
against the code, (4) the ladder of answers that keep the instrument PC
offline with Mascope in the cloud, (5) the connector, (6) on-premises split
into a managed and an isolated form, and why a site in mainland China takes
the isolated one, (7) where this meets
[isolated_deployment_plan.md](isolated_deployment_plan.md), (8) the four
offerings that follow, and (9) the build phases.

> **Status (2026-11-17): proposal, nothing built; the phases are filed.**
> Sections 4.1 and 4.2 need no code, only documentation and a test; the
> connector of section 5 is fleet configuration plus one role; the drop
> folder of 4.4 needs agent changes first.

## 1. The setting

An Orbion site has three parts: the instrument, the acquisition PC next to it
(Windows, running OrbionICS, which drives the instrument and uploads its
files), and Mascope. A control program uploads through the File Agent library,
pairing as `instrument-agent` (the library's README, `agents/file/README.md`);
that OrbionICS does so in every respect section 3 describes is to be
confirmed. Mascope is preferably the cloud
instance Ultra Trace runs. Some customers refuse that, and an on-premises
server is then the usual answer - one that is costly to maintain as a service
(the isolated deployment plan exists because of it).

OrbionICS is not in this repository. Everything this note says about its
behaviour is marked as an external dependency to confirm, not as a fact.

## 2. Two questions in one objection

"Mascope cannot be in the cloud" usually stands for one of these:

| What the customer says | What it usually means | What answers it |
|---|---|---|
| "The instrument PC cannot be on the internet" | Their IT treats it as plant equipment: a vendor-validated Windows image patched on the vendor's schedule, not IT's, driving an expensive instrument. The fear is ransomware on that machine. | Network design (section 4). Mascope stays in the cloud. |
| "Our data cannot leave the site" | Residency or confidentiality: proprietary samples, regulated work, export control. | An on-premises server (section 6), or a cloud instance in a region or account the customer chooses, which is a hosting decision outside this note. |
| "The lab cannot stop when the internet does" | Availability. | Acquisition never stops: the instrument PC writes its files locally. Uploads catch up afterwards, but today only after outages of minutes (section 3, last rows); phase C fixes that. |

The first row is the common one, and an on-premises server is the wrong answer
to it: the instrument PC talks to an on-premises server over the LAN, and it
can talk to a cloud instance without a route of its own just as well (section
4). Ask which row a prospect is in before offering anything; section 11 asks
for it to be recorded per deal.

## 3. What the agent does on the network today

Everything the ladder below relies on, with where it is in the code. The agent
here is the File Agent library (`agents/file`) and the SDK calls it makes
(`libraries/sdk`), which a control program such as OrbionICS embeds.

| Behaviour | Where | What it means here |
|---|---|---|
| Every request goes through the module-level `requests` functions, which open a default `Session`: `trust_env` is on and no call passes `proxies=`. `HTTPS_PROXY` and `NO_PROXY` are read, and on Windows, when no variable is set, the proxy in the Internet Settings of the account the process runs as. | `mascope_sdk/_agents.py`, `mascope_sdk/_http.py`, `mascope_file_agent/capabilities.py`, `status.py`, `wizard.py` | A static proxy works with no agent change. A proxy set only by an auto-configuration script (PAC, WPAD) does not: `requests` does not evaluate those. |
| TLS is verified against the `certifi` bundle that `requests` ships. Verification is a boolean (`VERIFY_TLS` in `mascope_sdk/_agents.py`, `verify_tls` in the agent's configuration); the Windows certificate store is not read. `REQUESTS_CA_BUNDLE` in the process environment replaces the bundle. | `requests.adapters.HTTPAdapter.cert_verify`, `requests.sessions.Session.merge_environment_settings` | Anything that terminates TLS in front of the agent - a TLS-inspecting corporate proxy, the connector's reverse form (4.3) - needs a publicly trusted certificate, or `REQUESTS_CA_BUNDLE` set for the agent's process. An internal CA installed in Windows is not enough. |
| The resumable upload takes only the upload id from the creation response's `Location` and addresses the chunks through its own configured host. | `api_post_file_tus` in `mascope_sdk/_agents.py` | A reverse proxy does not have to rewrite `Location`. |
| Pairing is a device-code flow: `POST /api/auth/pairing/start` returns a code, the agent polls `/api/auth/pairing/poll`, and a person approves the code in the web app from any browser. | `start_pairing`, `run_pairing`, `_await_approval` in `mascope_file_agent/wizard.py`; `api/new/auth/pairing/routes.py` | No URL comes back to rewrite, and pairing needs no browser on the instrument PC. People who use the web UI there are another client with needs of its own (4.6). |
| The origin guard on state-changing requests acts only on a request that carries `Origin` or `Referer`. | the origin middleware in `app/fast.py`, `origins.py` | `requests` sends neither, so agent writes pass whatever `Host` a proxy forwards. |
| The acquisition record rides in the `Upload-Metadata` header of the creation request: up to 16 KB of JSON, about 22 KB encoded. | `api_post_file_tus`; `large_client_header_buffers 4 32k` in `server/frontend/nginx.conf`; the `ValidationError` branch of `FileUploader.upload_sample_file` | A reverse proxy left at nginx's default 8 KB header line refuses the creation request with a 400. The agent then sends the file again without its record, so the upload goes through and the record is lost, with only a warning in the agent's log. The risk is records lost quietly, not uploads refused. |
| Chunks are 50 MB, halved down to 5 MB when connections fail; a chunk's read timeout is 600 s. | `TUS_CHUNK_SIZE`, `TUS_MIN_CHUNK_SIZE`, `_transfer_chunks` in `mascope_sdk/_agents.py` | A reverse proxy needs a body limit of at least 50 MB (the shipped nginx allows 100 MB on the tus location) and a read timeout of at least the agent's 600 s per chunk. The shipped nginx has 3600 s on the tus location, because finalizing a large upload takes time. |
| The watcher uses watchdog's native `Observer`, with no polling fallback. | `FileSystemWatcher` in `mascope_file_agent/watcher.py` | On Linux that is inotify, which sees files written through a Samba server on the same host but not files another machine writes to a share mounted from elsewhere. |
| A file counts as complete when its size holds over a 1 s look and `os.rename(f, f)` succeeds; the uploader then waits until the file's `st_atime` is `timeout` seconds old (default 3). | `FileSystemWatcher._wait_until_complete`, `FileUploader.seconds_since_last_access`, `DEFAULT_SETTINGS` in `config.py` | On Windows the rename fails while a writer holds the file open, which is the real "still being written" signal. On Linux it always succeeds, and the default `relatime` mount option does not move `st_atime` on writes. On a Linux host only the writer's own protocol says a file is whole. |
| The record beside a file, `<file>.mascope.json`, is read when the file is uploaded, and a record never triggers an upload of its own. | `UploadProvenance._record`, `acquisition.read_sidecar`; the handler's `ignore_patterns` in `watcher.py` | A record that lands after the upload has started is not sent. |
| A file that fails to upload is tried 10 times, 30 s apart, then copied to `failed_uploads` for a person to put back. The queue is in memory, and nothing scans the watched folder at start: watchdog reports only new events. | `FileUploader.process_file_upload` (`max_retries=10`), `RETRY_DELAY`, `FileUploader.jobs` in `uploader.py` | An outage longer than roughly 5 to 15 minutes, depending on how fast a failed connection fails, leaves files set aside, and a file that arrives while the agent is stopped is never uploaded. This holds for every deployment today, not only behind a connector. |
| The library's suite runs on Linux as well as Windows. | the "File Agent tests" and "File Agent tests (Windows)" jobs in `.github/workflows/tests.yaml` | Running the agent as a library on a Linux connector (4.4) is tested ground; the Windows program is not needed there. |

## 4. Keeping the instrument PC offline with Mascope in the cloud

A ladder, cheapest first. Each rung keeps the previous one's agent behaviour
and adds isolation.

The agent is not the only thing on the instrument PC that may reach out.
People may use the Mascope web UI there, and the site may want remote support
on the PC on request. Each needs more than the agent's one connection: 4.6
says what a browser needs on each rung, and 4.7 what TeamViewer needs. Where
this section says "the instance's name and nothing else", it means for the
agent; those two add named destinations of their own.

### 4.1 (a) A firewall rule by name

The instrument PC keeps its network, and the customer's firewall lets it reach
the Mascope instance's name on 443 - plus the names of 4.6 and 4.7 where those
are wanted - and nothing else. Nothing to build. The rule has to be by name, not
address: the fleet's servers admit 443 from Cloudflare's ranges only (the
`firewall` role in `tooling/fleet`), so the instance's public addresses are
Cloudflare's, and an address rule for it is a rule for every site Cloudflare
fronts. That needs a firewall that filters on SNI or DNS, which many sites
have.

### 4.2 (b) The customer's proxy

Many sites already run an outbound proxy. Set it for the agent - `HTTPS_PROXY`
as a machine-wide variable, or the Windows proxy setting of the account the
agent runs as - and allow the instance's name on it. Section 3 has the
conditions: a static setting, not a PAC script, and a proxy that does not
inspect TLS for that name (or `REQUESTS_CA_BUNDLE` naming a bundle with the
proxy's CA). The instrument PC then needs no internet DNS either: with a proxy,
the proxy resolves the target name. TLS stays end to end and the agent
verifies the instance's real certificate. A browser on the PC takes the same
Windows proxy setting (4.6).

External dependency: whether the OrbionICS process sees the setting - inherits
a machine-wide variable, or runs as an account with the Windows proxy set.

### 4.3 (c) The Mascope Connector

A small Linux host Ultra Trace runs at the site, in the customer's IT zone or
DMZ (section 5). The instrument PC reaches only the connector; the connector
alone reaches out, to named hosts. It forwards in one of two forms.

**Forward proxy, the default.** The connector runs a forward proxy that admits
`CONNECT` to a short list of names and nothing else. The list is the
instance's name on 443, plus, where the site wants them, the sign-in check's
host for a browser on the PC (4.6) and TeamViewer's servers for remote support
(4.7). On the instrument PC this is rung (b) with the connector as the proxy,
so the agent needs no change. TLS is end to end: the connector holds no
certificate and no token, sees only names and byte counts, and has nothing
worth stealing. Nothing has to be rewritten.

Some IT departments count a `CONNECT` tunnel as internet access by another
name, because the instrument PC still holds a TLS session with a host outside.
For them, the next form.

**Reverse proxy.** The agent's `host` is the connector's name; the connector
terminates TLS and opens its own connection to the instance. The instrument
PC's session ends at the connector, and the connector can admit the agent's
routes and nothing else: `GET /api/version`, `GET /api/sample/files`,
`POST /api/auth/pairing/start`, `POST /api/auth/pairing/poll`,
`POST /api/auth/devices/token`, and `/api/sample/files/upload/tus` and below
(`POST`, `HEAD`, `PATCH`, `DELETE`), plus whatever route the self-update offer
uses once it is built. From section 3, it must:

- present a publicly trusted certificate, because the agent reads only
  `certifi`'s bundle. ACME DNS-01 for a name under a domain Ultra Trace
  controls works, renewed from the connector, which has egress; the DNS
  credential is then a secret on a customer-site host and should be scoped to
  that one record;
- send `Host` and SNI for the instance's name upstream;
- allow 32 KB header lines and a body of at least 50 MB, as the shipped
  `nginx.conf` does, and a read timeout of at least the agent's 600 s per
  chunk (the shipped `nginx.conf` has 3600 s on the tus location).

It does not have to rewrite `Location`, pairing responses or origins. The cost
of this form: the connector sees tokens and data in the clear, which makes it a
host worth attacking, where the forward form is not.

The reverse form serves the agent only. The web UI does not work through it
on the instrument PC (4.6), and TeamViewer cannot use it (4.7).

Neither form changes outage behaviour: the files wait on the instrument PC,
within the limits of section 3's last rows.

### 4.4 (d) The drop folder

For sites that allow the instrument PC no HTTP session with anything. The
connector exports an SMB share per instrument; the instrument software writes
its files there instead of uploading; a File Agent - the library, as a service
- on the connector uploads them. This is store-and-forward, and it carries
rules that section 3 dictates:

- **The connector hosts the share; it does not mount one.** inotify sees files
  written through the local Samba server and misses a remote share's changes.
- **The writer finishes a file before the agent sees it.** Write the record,
  `<file>.mascope.json`, first; then write the raw file under a name the
  agent's mask does not match and rename it into place. The watcher takes a
  rename to a matching name as a new file (`on_moved` in `watcher.py`), and
  the file is whole the moment it appears. Without the rename, a Linux host has
  no signal that the writer is done.
- **One agent per instrument folder**, with `instrument` set, each paired as a
  machine of its own. Pairing reports the connector's host name as the
  machine's name (`platform.node()` in `start_pairing`), so the instrument name
  is what tells them apart under Paired machines.
- **Only once the agent survives outages and restarts** (phase C). Without
  that, files dropped while the connector's agent is stopped are never
  uploaded, and an outage of more than a few minutes sets files aside on the
  connector, where nobody looks. After a successful upload the agent moves the
  file into an `uploaded/` folder pruned by age, which gives the restart
  catch-up its record of what is done and the site a short local copy.
- **The share is narrow.** One share per instrument, writable by that
  instrument PC's account alone, and nothing else on the connector reachable
  over SMB: a writable share on another host is the path ransomware takes
  sideways.

External dependency: OrbionICS needs a mode that writes to a share instead of
uploading, in the order above.

The drop folder gives the instrument PC no path to Mascope at all, so nobody
uses the web UI there; people use Mascope from another PC (4.6). Remote
support needs the forward proxy beside the share (4.7).

### 4.5 One-way links: out of scope

A data diode cannot carry the agent's protocol, which needs answers: the
upload's `HEAD` for the server's offset, the creation response's `Location`,
the pairing poll, token renewal. A one-way site needs a fire-and-forget
transport and a receiver on the far side, which is a product of its own.
Design it only when a customer requires it.

### 4.6 The Mascope web UI on the instrument PC

People often use Mascope in a browser on the instrument PC itself. A browser
is a second client, and it needs more than the agent does:

- **The whole site**, not the handful of routes the agent uses (4.3).
- **A WebSocket.** The app opens socket.io with `transports: ['websocket']`
  and no polling fallback (`server/frontend/src/api/socket.js`). Its API
  module waits at load for the socket's first connection (`export const api =
  await initApi()` in `api/client.js`), so without the socket the app does not
  start. Once it runs, it blocks itself behind "No connection to the server"
  whenever the socket drops (`App.vue`). The spectrum and time-series plots
  receive their data over the socket (`lib/charts/ChartMatchSpectra/data.js`,
  `lib/charts/ChartMatchTimeseries/data.js`).
- **The sign-in check, where the instance turns it on.** With
  `MASCOPE_TURNSTILE_SITE_KEY` set (`docs/maintaining.md`, "Turning Turnstile
  on"), the sign-in form loads Cloudflare's script and its frame from
  `challenges.cloudflare.com` (`server/frontend/src/lib/turnstile.js`; the
  Content-Security-Policy in `nginx.conf` admits that host). **Login** stays
  greyed out until the check returns a token (`lib/panes/PaneLogin.vue`). The
  agent never meets the check: pairing, uploads and token calls skip it.

What that means on each rung:

| Rung | The web UI on the instrument PC |
|---|---|
| (a) firewall rule, (b) customer's proxy, (c) connector, forward form | Works. On (a) the browser goes direct, and the firewall rule admits the names below. On (b) and (c) it works if the browser uses the proxy; browsers take the Windows proxy setting, PAC scripts included, unlike the agent. Allow `challenges.cloudflare.com` beside the instance's name when the instance has Turnstile on, or leave Turnstile off for that instance. A plain `CONNECT` tunnel carries the WebSocket as it is. A TLS-inspecting proxy that does not pass WebSocket upgrades lets the page load and the app never start, so exempt the instance's name from inspection. |
| (c) connector, reverse form | Does not work. The form admits the agent's routes only. Opening it to the whole site, `/socket.io/` upgrades included, would still fail: the browser's requests carry the connector's name as `Origin` while the connector sends the instance's `Host` upstream, so the origin guard (`own_request_origins` in `server/backend/src/mascope_backend/origins.py`) refuses every state-changing request. The Turnstile widget is also registered for the instance's hostname. The reverse form serves the agent only. |
| (d) drop folder | No browser access from the instrument PC; people use Mascope from another PC. |
| Managed on-premises, isolated appliance (section 6) | The browser reaches the server over the LAN. With Turnstile off, nothing else is needed. With it on, the server checks each token with Cloudflare's siteverify on `challenges.cloudflare.com`, and refuses the sign-in when it cannot (`api/new/auth/turnstile.py`). So both the server and its users' browsers must reach that host. A managed server needs it on its egress allow-list. An isolated appliance never has it, so Turnstile stays off there. |

### 4.7 Remote support on the instrument PC, on request

Sites often want remote support on the instrument PC, through TeamViewer,
started when someone asks for it. This note gives that a network path. What
is done in a session, supporting the instrument software, stays outside it
(section 10).

TeamViewer makes outbound connections only and needs no inbound port:
TCP/UDP 5938 first, TCP 443 when 5938 is blocked, and TCP 80 as a last resort,
to TeamViewer's servers under `teamviewer.com`
([Ports and URLs used by TeamViewer](https://www.teamviewer.com/en/global/support/knowledge-base/teamviewer-remote/troubleshooting/ports-used-by-teamviewer/)).
The full Windows client takes an HTTP proxy, in its options or with the
`--ProxyIP` parameter
([Command line parameters](https://www.teamviewer.com/en/global/support/knowledge-base/teamviewer-remote/for-developers/command-line-parameters/)).
That it works through the connector's proxy is still to be tested (phase A).

- **(a), (b) and managed on-premises:** through the customer's firewall or
  proxy, which admits TeamViewer's names.
- **(c) forward form:** the connector's proxy admits TeamViewer's names on 443
  as a second named destination, with 5938 only where the customer also opens
  it. The proxy log records every session.
- **Reverse form and drop folder:** neither gives the PC a proxy, so the
  connector runs the forward proxy beside them, for TeamViewer's names alone.
- **Isolated appliance:** only through a path the customer opens for the
  session. Remote desktop through the instrument PC is today's path there
  ([isolated_deployment_plan.md](isolated_deployment_plan.md) section 1).

"On request" means two things. Someone at the PC starts or accepts each
session, and no unattended access is configured on it. And the customer's
firewall can keep the connector's TeamViewer egress closed between sessions,
opening it when a session is wanted.

## 5. The connector

### 5.1 What it is

An Ubuntu LTS virtual machine on the customer's hypervisor - preferred, since
their IT then owns the hardware - or a small host Ultra Trace ships. It is
small: an Orbitrap instrument uploads files of 0.2 to 1.2 MB every 20 to 40 s,
about 50 to 175 MB per acquisition hour and a sustained 0.1 to 0.4 Mbit/s
(production filestores over 60 days, measured November 2026). Disk matters
only for the drop folder, where it holds the `uploaded/` retention.

It runs Tailscale and the forward proxy (Squid, or any proxy that can restrict
`CONNECT` to named hosts on 443). The drop folder adds Samba and one agent
service per instrument; the reverse form adds nginx and an ACME client. In the
proxy forms it holds no customer data.

### 5.2 Provisioned and maintained as a fleet host

- **Tailscale on the connector itself**, tagged per site, with an ACL that
  lets the admin devices reach port 22 and nothing else. No subnet route is
  advertised: the tailnet reaches the connector, not the site's network.
- **A `connectors` inventory group** beside `fleet` in `tooling/fleet`, with
  its own play. `sshd_hardening` and `unattended_upgrades` apply as they are
  (the connector has egress for its mirrors). `firewall` needs a connector
  variant. It keeps default deny in, allow out and SSH on `tailscale0` as now.
  It leaves out the Cloudflare 443 rule and the container NAT block. It leaves
  forwarding at ufw's `DROP`, where the role sets `DEFAULT_FORWARD_POLICY` to
  `ACCEPT` for Docker: the connector runs no containers, and section 10
  promises it gives the instrument PC no route. (Tailscale's own forwarding
  rules still carry the subnet route of section 7 under that policy.) And it
  adds the forwarding port, or 445 for the drop folder, from the instrument
  PCs' addresses, as a per-host variable. `docker_daemon`, `deploy_env` and
  `postgres_readonly` do not apply, and neither does `update.yml`: there is no
  Mascope stack. A new `connector` role installs the proxy and, for the drop
  folder, Samba and the agent at a pinned version.
- **Health** is a push monitor per connector, the way
  `tooling/monitoring/doctor-push.sh` pushes from servers. The proxy's own log
  shows every destination an instrument PC asked for, which is also how the
  OrbionICS questions of section 11 get answered at a pilot site.
- Rate limits keyed on the client address, such as the one on `/api/version`
  that `capabilities.py` notes, see a site's instruments from one address
  behind a connector. They already do behind the site's NAT, so nothing
  changes.

### 5.3 What customer IT allows

| Direction | From | To | What for |
|---|---|---|---|
| In | the instrument PCs' addresses | the connector, one port | the proxy port (forward form), 443 (reverse form) or 445 (drop folder) |
| Out | the connector | the Mascope instance's name, 443 | the uploads, and the web UI where people use it on the instrument PC (4.6) |
| Out, optional | the connector | `challenges.cloudflare.com`, 443 | the sign-in check, for a browser on the instrument PC when the instance has Turnstile on (4.6) |
| Out, on request | the connector | TeamViewer's servers under `teamviewer.com`, 443; 5938 only if the customer opens it | remote support sessions on the instrument PC, opened per session by the customer's firewall if it wants (4.7) |
| Out | the connector | Tailscale's coordination server and relays, which Tailscale's documentation lists; it relays over 443 when UDP is blocked | administration |
| Out | the connector | the Ubuntu mirrors or the customer's own; an NTP source | patching and time |
| Out, optional | the connector | GitHub's release download host | installers, if they come from GitHub (5.4) |

The instrument PC has no default route and no internet DNS. Its agent, its
browser and TeamViewer reach outside only through the connector's proxy, and
only to the names above.

### 5.4 Installers for PCs that cannot reach GitHub

[file_agent_self_update_plan.md](file_agent_self_update_plan.md) section 4.2
offers the File Agent's installer from its GitHub release by default, and for
PCs that reach only their Mascope server it plans a later phase in which the
server serves the installer itself. Its section 4.3 makes the signature, not
the source, decide whether an installer runs.

Behind a connector in the forward form the PC reaches the instance, so the
server-served copy covers it once that phase is built, with no change to the
connector. Until then the connector can admit GitHub's release download host
for the PC: the signature check makes the source irrelevant to trust. In the
reverse form the connector has to admit the installer route as well. A cache
on the connector is the last resort, if neither works. How OrbionICS itself is
updated is an external dependency to confirm.

## 6. On-premises, two ways

### 6.1 Managed on-premises

For customers whose objection is the data. The server sits where the connector
would - the customer's IT zone or DMZ - with the egress allow-list in
[hosting.md](../hosting.md#isolated-and-restricted-sites) and Tailscale on the
server itself. It is then an ordinary fleet host: `update.yml`, unattended
upgrades, monitoring and error reporting work as they do for a cloud server.
The instrument PC reaches it over the LAN and never the internet, which
hosting.md already says the agent is indifferent to; so does a browser there
(4.6). TeamViewer, if the site wants it, goes through the customer's firewall
or proxy (4.7). The `firewall` role needs a per-host source for 443, the LAN,
in place of the Cloudflare rule.
[isolated_deployment_plan.md](isolated_deployment_plan.md) section 4.1 needs
the same. It says the role should skip the Cloudflare rule for isolated
hosts, and the only variable it names is the SSH source. Data stays on site,
and maintenance costs about what a cloud server's does.

### 6.2 The isolated appliance

No route at all, for the server or anything else at the site:
[isolated_deployment_plan.md](isolated_deployment_plan.md), with its signed
bundles, inbox, support bundle and host-hygiene checklist. Every release needs
a carrier and every diagnosis a session or a bundle. A browser on the
instrument PC reaches the server over the LAN (4.6). Remote support goes only
through a path the customer opens for the session (4.7). It is the expensive
offering and should be priced as one.

### 6.3 A site in mainland China

Managed on-premises, as 6.1 draws it, does not fit a site in mainland China.
Both of its outbound paths fail there: the updates for technical reasons, the
tunnel because of the rules.

**Updates.** A managed server pulls from the hosts on
[hosting.md's allow-list](../hosting.md#isolated-and-restricted-sites).
From the mainland:

- Docker Hub, where the Postgres and Redis images come from, is slow to the
  point of timing out. Most domestic mirrors of it closed in June 2024
  ([a December 2025 survey](https://eastondev.com/blog/en/posts/dev/20251217-docker-mirror-guide-2025/)).
- GitHub and `ghcr.io`, for release discovery and the release images, are
  not blocked, but how reliably they cross the border has not been measured.
- PyPI and the Ubuntu archive work through domestic mirrors.

So a Chinese site updates by the isolated plan's signed bundle
([isolated_deployment_plan.md](isolated_deployment_plan.md) section 5), which
already carries the third-party images. It therefore waits on the bundle
phases: MAS-303, and the issues it files. The alternative is to mirror our
images into a registry inside China, run by a Chinese cloud provider. That
adds an account there and a publishing step to every release.

**The tunnel, technically.** Tailscale often connects:

- Its control plane is on TCP 443.
- Its relays include a Hong Kong region (`hkg` in the
  [default DERP map](https://login.tailscale.com/derpmap/default)).
- It relays over TCP 443 when UDP is blocked.

How well it connects depends on the carrier, the province and the time of
day, and nothing guarantees it
([SSD Nodes, 2026](https://www.ssdnodes.com/learn/lang/zh-hans/does-tailscale-work-in-china)).
A self-hosted relay or Headscale mends the layer that fails; neither changes
the border. The isolated plan's alternatives in its section 4.3, Cloudflare
Tunnel and a reverse SSH tunnel, cross the same border.

**The tunnel, by the rules.** This is the real blocker.

- The Ministry of Industry and Information Technology's notice of January
  2017 bars anyone from setting up or renting cross-border dedicated lines or
  other channels, VPNs included, without the telecom authorities' approval
  ([translation](https://www.chinalawtranslate.com/%e5%b7%a5%e4%b8%9a%e5%92%8c%e4%bf%a1%e6%81%af%e5%8c%96%e9%83%a8%e5%85%b3%e4%ba%8e%e6%b8%85%e7%90%86%e8%a7%84%e8%8c%83%e4%ba%92%e8%81%94%e7%bd%91%e7%bd%91%e7%bb%9c%e6%8e%a5%e5%85%a5%e6%9c%8d%e5%8a%a1/?lang=en)).
- The ministry later said that foreign and multinational firms needing such
  links should rent them from operators with international gateways
  ([Yicai Global, July 2017](https://www.yicaiglobal.com/news/new-vpn-regulation-will-not-affect-lawful-users-miit-says)).

A standing tunnel from the customer's server to Ultra Trace's hosts is a
self-built cross-border channel of exactly that kind. Expect the customer's
IT to refuse it.

**Atmospheric data.**

- The Measures for the Administration of Foreign-related Meteorological
  Observation and Data
  ([InforMEA](https://informea.org/en/content/legislation/measures-administration-foreign-related-meteorological-observation-and-data))
  require a permit for a foreign-related observation station, and govern
  meteorological data provided to foreign parties.
- In October 2023 the Ministry of State Security reported that it had
  checked more than 3,000 foreign-related stations. It found several hundred
  sending data abroad in real time without approval
  ([CNA](https://www.cna.com.tw/news/acn/202310310031.aspx)).
- Whether an Orbion measuring atmospheric composition falls within the
  measures is unconfirmed (section 11). The question bears hardest on the
  cloud offerings, which send every measurement abroad as it is made.
- The Cyberspace Administration of China's cross-border data provisions of
  March 2024 exempt data from academic cooperation from their assessment and
  contract requirements. The exemption holds only when the data contains no
  personal information and no important data
  ([Hunton](https://www.hunton.com/privacy-and-cybersecurity-law-blog/2024/03/27/new-era-of-regulation-for-cross-border-transfers-in-china/)).

**What fits:**

- The server on site, with no standing tunnel, updated by bundle.
- Support through attended sessions that the customer opens. TeamViewer is
  reported to work across the border, though slowly. Sunlogin and ToDesk are
  common locally but are reported to need their paid overseas tiers to cross
  well. Neither report has been tested.
- The isolated plan's support bundle (its section 6), for diagnosis without
  a session.
- A standing tunnel only over a cross-border line the customer already rents
  from a licensed operator, and only with their written approval.
- A local partner for hands-on work.

That is the isolated appliance of 6.2. Its gateway (section 7), whichever
machine plays that role, crosses the border only in sessions the customer
opens.

## 7. Where this meets the isolated deployment plan

The isolated plan's setting (its section 1) has the server offline and the
instrument PC online, permanently or in windows the customer opens. It uses
that PC as the site's gateway: the Tailscale subnet router of its section 4,
the File Agent carrier of section 5.5 (carrier 3), and the heartbeat and
support-bundle relay of section 6. For a site whose objection is the instrument
PC, each of those puts an outbound path on the one machine the customer wants
offline.

The connector takes that role instead. It advertises the subnet route to the
server's address (it has egress; the server stays without a route), so the
isolated plan's section 4 holds with the connector in the instrument PC's
place. Bundles reach the server's inbox from the connector, by the `scp` over
the tunnel that the isolated plan's section 5.5 already describes for the
case where the tunnel is open, or by a job on the connector that fetches and
pushes them. Support bundles and heartbeats leave the same way. Which machine
is a site's gateway - the instrument PC, or a connector - is a fact to record
per site with its inventory entry; both settings stay valid.

## 8. Four offerings

| Offering | Where the data lives | What the instrument PC reaches | What the customer provides | Maintenance |
|---|---|---|---|---|
| Cloud | Ultra Trace's hosting | the instance, by 4.1 or 4.2 | outbound 443 to the instance's name, plus the names of 4.6 and 4.7 where wanted | the fleet's |
| Cloud + Connector | Ultra Trace's hosting | the connector only | a small VM and the rules of 5.3 | the fleet's, plus one small host per site |
| Managed on-premises | the customer's server | the server, over the LAN | a server in the IT zone and the hosting.md allow-list | about a cloud server's |
| Isolated appliance | the customer's server | the server, over the LAN | nothing outbound | a carrier per release; the isolated plan |

In every row, a browser on the instrument PC reaches Mascope by the agent's
path, and TeamViewer by a path of its own (4.6, 4.7). The exceptions are two
forms of the connector row, the reverse form and the drop folder, which give
the browser no path at all.

In mainland China, managed on-premises does not fit, and the cloud rows wait
on the meteorological-data question. As things stand, the isolated appliance
with attended support is the row that fits (6.3).

Prices are a business decision outside this note. The point of the table is
that each offering's cost follows from its last column, so the costly one is
priced as costly and most "no cloud" customers land in the second row.

## 9. Build phases

**Phase A - answers with no code.** Put 4.1 and 4.2 in front of every
prospect: a section in `docs/hosting.md`, "Keeping the instrument PC offline",
and a paragraph in the instrument guide (`docs/user/instruments/index.md`) on
proxies - a static setting through `HTTPS_PROXY` or the account's Windows
proxy, no PAC, and TLS inspection exempted for the instance's name or
`REQUESTS_CA_BUNDLE` set. The same pages say what a browser on the PC needs
(4.6) and what TeamViewer needs (4.7). Test the agent through a `CONNECT`
proxy on the LAN test host with the PC's own route closed, and on Windows a
browser and TeamViewer through the same proxy. Ask the OrbionICS questions of
section 11. (MAS-957, MAS-958, MAS-959.)

**Phase B - the connector, forward form.** The `connector` role, its proxy's
list of names (the instance's, and the names of 4.6 and 4.7 per site), the
`connectors` group and play, the firewall variant, the Tailscale tag and ACL,
the push monitor, and a runbook chapter in `docs/maintaining.md`. Pilot at one
site. (MAS-960, MAS-961, MAS-962, MAS-965, MAS-970.)

**Phase C - the agent survives outages.** Retry connection failures without a
count (keeping the count for refusals), and catch up at start on files not yet
uploaded. This matters for every deployment, not only connector sites; how the
agent knows what it has uploaded is the decision MAS-964. (MAS-963,
MAS-968.)

**Phase D - the drop folder.** Samba shares and agent services in the
`connector` role, the `uploaded/` retention, and the write protocol of 4.4 in
the instrument guide. Needs phase C and the OrbionICS write mode. (MAS-966,
MAS-967, MAS-972.)

**Phase E - the reverse form**, only when a site asks for it. (MAS-971; the
decision MAS-969 bears on it.)

**Phase F - the gateway role for isolated sites** (section 7), filed with the
isolated plan's phases. (MAS-973.)

## 10. Deliberately not in scope

- Giving the instrument PC any route through the connector: no NAT, no exit
  node. The forward proxy carries named destinations only.
- One-way links (4.5).
- The connector holding data beyond the drop folder's retention. It is not a
  cache of the instance.
- Running Mascope's stack on the connector. That is managed on-premises (6.1).
- Supporting the instrument software itself. Section 4.7 gives remote support
  on the instrument PC a network path; what is done in a session is a
  separate matter, as the isolated plan also says.

## 11. Open questions

- **Which objection each prospect has.** Record it per deal; the offerings of
  section 8 follow from it.
- **Whether customer IT accepts a `CONNECT` tunnel**, or wants the session
  terminated (the reverse form) or no HTTP at all (the drop folder). The pilot
  will tell how often each comes up.
- **OrbionICS (external, to confirm; MAS-959):** whether its process picks up
  a proxy setting; whether it makes outbound calls of its own - its updates,
  licensing, the instrument vendor's software - that a connector would have to
  admit or the site decide on; whether it can write record-then-rename to a
  share instead of uploading; and under which service name the drop folder's
  uploads should appear, given that the connector's agent pairs as
  `file-agent` where a control program pairs as `instrument-agent`.
- **How the agent knows what it has uploaded** (the decision MAS-964), for
  phase C's restart catch-up: a local ledger; the `uploaded/` move, which fits
  the drop folder but not the instrument PC, whose acquisition folder belongs
  to the instrument software; or asking the server by the file's hash, which
  the agent already sends with each upload (`sha256` in `api_post_file_tus`).
- **The agent's trust store** (the decision MAS-969). A CA-bundle setting, or
  reading the operating system's store, would make the reverse form and
  TLS-inspecting proxies work without an environment variable. It also bears
  on the internal-CA option in `docs/hosting.md`, whose "install the CA on
  client machines once" reaches browsers but not the agent (GitHub #3286).
- **Remote support on the instrument PC** (4.7): whether a site's IT accepts
  TeamViewer through the connector, opened per session; whether supporting
  OrbionICS needs unattended access rather than a session someone at the PC
  accepts; and whether TeamViewer works through the connector's proxy on 443
  as its documentation says (phase A's test).
- **A site in mainland China** (6.3), for the customer and Chinese counsel:
  - whether an Orbion measuring atmospheric composition is a foreign-related
    meteorological observation station in the measures' sense, and so
    whether its data may reach Ultra Trace's hosting at all;
  - whether its data counts as important data under the Cyberspace
    Administration's provisions;
  - whether the customer already rents a cross-border line from a licensed
    operator.

  For us: which remote-support tool crosses the border well enough, tested
  at the site.
- **The connector's form:** a customer VM or a host Ultra Trace ships, and who
  patches the hypervisor under it.
- **Tailscale on a customer-site host:** the same acceptance question the
  isolated plan's section 12 asks. The reverse-SSH alternative of its section
  4.3 applies to the connector equally.
