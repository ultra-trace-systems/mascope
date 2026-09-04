# Instruments & acquisition

Operator-facing: connecting instruments, running acquisitions, and getting data
into Mascope.

## The File Agent

The File Agent is a small Windows program that runs on the instrument PC. It
watches a folder for new data files (for example Orbitrap `.raw` files) and
uploads them to your Mascope server automatically.

### Installing

1. Download the installer on the instrument PC. In the Mascope web app, open
   the **Home menu** (house icon, top-left), go to the **Settings** tab, and
   click **Download File Agent
   installer** under **API Access Tokens** (or download
   `Mascope-File-Agent-Setup.exe` from the latest [Mascope release on
   GitHub](https://github.com/ultra-trace-systems/mascope/releases/latest)).
2. Run the installer. It needs no administrator rights and offers a
   **Start the File Agent automatically when you sign in to Windows**
   checkbox — leave it enabled so the agent survives reboots.

   > The installer is not yet code-signed, so Windows SmartScreen may warn
   > about an unrecognized app. Click **More info** → **Run anyway**.

3. When the agent first starts, a guided setup runs in the console window.
   It asks for the **Mascope server address** (for example
   `mascope.example.com`) and whether to verify the server's TLS
   certificate (answer *no* only for a self-signed test server), then for
   the **folder to watch** for new data files (any folder on the PC — where
   the agent is installed does not matter), whether to **also watch its
   subfolders**, and the **file pattern** to upload (default `*.raw`).

4. Next it asks for the **instrument name** this machine watches, for
   example `Orbi-Lab2` (letters, digits and hyphens). The agent reports it
   when pairing and with every upload, so the server can file uploads under
   it. If the watched folder already holds files, the name the server files
   them under is offered as the default; setup looks at a sample of the
   newest files, at most two folders deep, and takes at most a few seconds
   whatever the size of the folder. Leave the name empty to skip this,
   or answer `-` to remove a name that was set before.

   Setup then checks what those uploads would be filed under as things
   stand — taking any `filename_prefix` already configured into account —
   and offers to put `<instrument>_` in front of every uploaded name when
   the server could not read the current one. If the folder is still empty
   it asks for one example file name rather than guessing.

5. Finally the setup pairs the agent with your account:
   1. The agent shows a short pairing code, for example `BCD-234`.
   2. Log in to Mascope in your browser (*editor* role or higher), open
      the **Home menu** (house icon, top-left) **Settings** tab, and under
      **API Access Tokens** click **Pair an agent**.
   3. Enter the code and approve — the agent picks up its access token
      automatically within a few seconds.

The setup checks the server connection and the token immediately, so a typo
is caught before any data acquisition depends on it. After setup completes,
the agent starts watching the folder right away.

Each paired machine gets its own token, so pairing a new instrument PC
never disconnects an existing one. The token is **short-lived and the agent
renews it automatically** in the background — you never copy or paste one,
and there is nothing to rotate by hand. If an instrument PC is left off for
long enough that its token lapses, just start the agent: it checks with the
server as it starts, and offers to pair again when the credential is no
longer accepted. It makes the same offer if a credential is refused while it
is running.

Under **Paired machines** (Home menu → Settings → API Access Tokens) each
machine shows the instrument its agent reports watching and the agent release
it last connected with, next to when it was last seen — so an upgrade across
several instrument PCs can be followed from the web app. A machine paired by an
older agent shows neither until it runs one that reports them. Both follow
what the agent reports now, so changing `instrument` in a machine's
configuration and restarting it updates the list on its next upload.

Setup also asks whether to **verify the server's TLS certificate**. Leave
this on for a normal Mascope server; answer No only for a self-signed or
development server (recorded as `verify_tls` in the configuration).

Leave the console window open while acquiring — closing it stops the agent
until the next sign-in (or until you start it again from the Start Menu).

### Changing the configuration

All settings live in one file on the instrument PC:

```
%APPDATA%\Mascope\FileAgent\config.toml
```

| Setting           | Meaning                                                            |
| ----------------- | ------------------------------------------------------------------ |
| `host`            | Mascope server address, e.g. `mascope.example.com`                 |
| `access_token`    | Device token (filled by pairing, renewed automatically)            |
| `source`          | Full path of the folder watched for new data files                 |
| `recursive`       | `true` to also watch subfolders of `source` (default `false`)      |
| `verify_tls`      | `true` to verify the server's TLS certificate (default `true`)     |
| `timezone`        | IANA timezone of this machine, e.g. `Europe/Helsinki` (auto-detected when empty) |
| `instrument`      | Name of the instrument this machine watches, e.g. `Orbi-Lab2` (letters, digits and hyphens); reported when pairing and with each upload. The agent refuses to start on a name the server would not accept |
| `mask`            | Pattern of the files to upload, e.g. `*.raw`                       |
| `timeout`         | Seconds a file must be idle before it is uploaded                  |
| `filename_prefix` | Optional prefix added to the filename on upload                    |
| `filename_suffix` | Optional suffix added to the filename on upload (before extension) |

Restart the agent after editing the file (close its console window, then
start it again from the Start Menu). Alternatively, run the guided setup
again — it walks through all the settings above, offering the current
values as defaults — by starting the agent with the `--setup` flag:

```
%LocalAppData%\Programs\Mascope File Agent\Mascope-File-Agent.exe --setup
```

!!! tip "When acquisition times look shifted by an hour"

    Raw files record the acquisition time in this machine's local time, so the
    agent reports its timezone with every upload and the server converts from
    it. Detection reads the Windows setting, which names a *group* of zones
    rather than a city — a machine in Helsinki can resolve to another city in
    the same group, and the two can disagree about historical daylight-saving
    changes. If timestamps look wrong, set the zone exactly:

    ```toml
    [file-agent]
    timezone = 'Europe/Helsinki'
    ```

    The agent logs the zone it reports at startup, so the console shows which
    one is in use.

### Stopping or disabling the agent

- **Stop until the next sign-in**: close the agent's console window.
- **Stop starting automatically**: open Task Manager → **Startup apps**,
  right-click **Mascope File Agent** and choose **Disable** (enable it
  again the same way). This keeps the agent installed and configured; you
  can still start it manually from the Start Menu.
- **Remove it completely**: uninstall via Windows **Settings → Apps**.
  Your configuration is kept, so reinstalling later picks up where you
  left off.

### Upgrading

Download and run the newest installer — it replaces the previous version in
place and your settings are kept. Installs made with older agent versions
(before the installer existed) are migrated automatically on first start.
The agent prints its version when it starts, and uninstalling (Windows
**Settings → Apps**) never removes your configuration.

### Troubleshooting uploads

- Logs are written to `%APPDATA%\Mascope\FileAgent\logs\prod\`.
- If an upload keeps failing, the file is copied to a `failed_uploads`
  subfolder inside the watched folder. After fixing the cause (network,
  token), copy the file back into the watched folder to retry. The
  `failed_uploads` folder itself is never watched, even with `recursive`
  enabled.
- *"Failed to get instrument type"*: the file name does not identify an
  instrument. Mascope takes the instrument from the start of the name, up to
  the first underscore — `Orbion_2026.05.03…` is instrument `Orbion` — and it
  must be one this deployment knows. Either name the files that way in the
  acquisition software, or set `filename_prefix` in the configuration to add
  it, including the underscore (`filename_prefix = 'Orbion_'`). The guided
  setup offers to set that prefix for you when the files in the watched
  folder do not start with an instrument name: start the agent with
  `--setup`. The agent does not retry these: the server has understood the
  name and refused it, so the file is set aside in `failed_uploads`
  immediately.
- *"The server rejected the access token"* or *"This agent credential has
  expired"*: the machine's token has lapsed or its device was revoked.
  Answer the prompt the agent shows in its window, or close it and start
  the agent again — it offers to pair on start. Pairing a machine never
  affects the others. (The agent renews its token on its own while it
  is running, so this only happens after a long offline period or a
  deliberate revocation.)
- *Uploads fail with HTTP 404*: the configured `host` is answering but is
  not the Mascope API. In a production deployment, use the normal Mascope
  web app address. In a development setup, use the backend address (e.g.
  `http://localhost:8090`) — the frontend dev server (port 5173) cannot
  receive uploads.
- The agent uploads files of any size in resumable chunks, so a network
  drop mid-file costs at most one chunk. Agent versions older than the
  device-pairing release instead upload each file in a single request
  capped at 100 MB - larger files
  are rejected, logged and copied to `failed_uploads`. Download the
  newest installer to remove the limit.

## After files arrive

Files uploaded by the File Agent land in the instrument's
`Acquisitions <instrument>` workspace and show up in the **Raw files** tab, exactly
like files uploaded by hand. From there they are the same as any other raw file:
Mascope processes them automatically into acquisition batches, which you **copy**
into your own workspace. The
[Import data files](../guides/import-files.md#build-your-batch-from-the-acquisition-samples)
guide covers this, and its [prerequisites](../guides/import-files.md#prerequisites)
(filename rules and ionization modes) apply to File Agent uploads too.

<!-- TODO Phase 3. Outline:
- Acquisition workflow specifics (Orbitrap, TOF)
Cross-reference the developer agent docs in docs/dev/.
-->
