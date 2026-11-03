# Mascope CLI

The operations CLI for [Mascope](https://ultratrace.eu/), Ultra Trace's mass-spectrometry
analysis platform. It installs, launches, backs up and manages a Mascope
deployment — the application itself runs as Docker containers pulled from the
project's registry.

## Requirements

- Python 3.12
- Docker (with the compose plugin)

## Quickstart

```sh
pip install mascope-cli

mascope init        # create a runtime home: config, compose files, secrets
mascope cert gen    # self-signed TLS cert (or install real certs)
mascope prod up     # pull and start the production stack
```

`mascope init` creates the runtime home at `~/.mascope`
(`%LOCALAPPDATA%\Mascope` on Windows) — the configuration TOMLs, docker
compose files, and generated application secrets. Set `MASCOPE_PATH`
to place it elsewhere. Re-running `init` is safe: existing files are kept and
secrets are never regenerated. Put your own settings in the env overlay,
`.runtime/env/default/prod.mascope.toml`, not in the TOMLs at the top of the
home: those are the release's, and a CLI upgrade replaces them.

To try Mascope without a deployment, the self-contained demo stack ships in
the runtime home as well:

```sh
docker compose -f docker-compose.demo.yaml up -d
# then open http://localhost:8080 — login demo@mascope.app / mascope-demo
```

## Everyday operations

```sh
mascope prod ps                  # container status
mascope prod logs --follow       # stream logs
mascope prod update              # deploy the release this CLI was cut with
mascope prod db backup           # dump the database
mascope prod db restore --yes    # restore the latest dump
mascope logs query --grep error  # search the application's file logs
mascope env list                 # manage runtime environments
mascope export package <id>      # a dataset's archive, to deposit in a repository
```

Run `mascope --help` or `mascope <command> --help` for the full reference.

`mascope prod update` pulls the target release images, restarts the stack
with them and runs any pending database migrations (preceded by an automatic
pre-migration dump).

## The deployed release

A runtime home made by `pip install mascope-cli` and `mascope init` has no
source checkout to say which release it runs. Its CLI says it: each CLI
version is cut with one Mascope release, and its compose file and config
layers are that release's. So the CLI deploys that release and no other.

- **Moving to another release** is installing its CLI, then `mascope prod up`:

  ```sh
  uv tool install mascope-cli==<calver>   # the CLI cut with the release
  mascope prod up --detach
  ```

  `mascope prod up` and `mascope prod update` refuse any other release, a
  `--version` or a `MASCOPE_VERSION` pin, `latest` included. The refusal
  names the CLI to install, read from the release's manifest
  (`mascope-manifest.json`, whose `cli_version` names it). Going back to an
  earlier release is the same, with its CLI.
- **The first `prod up` after the CLI is upgraded** writes the new release's
  config layers and compose files over the home's
  (`base.mascope.toml`, `prod.mascope.toml`, `dev.mascope.toml` and the three
  `docker-compose*.yaml`). It keeps a copy of what it replaced under
  `.runtime/replaced/`, and does it once per CLI version, recorded in
  `.runtime/config-files.json`. It never touches `.runtime/`: not the env
  overlay `.runtime/env/<env>/prod.mascope.toml`, where your settings belong,
  and never the secrets.
- **The unattended update** (`mascope prod update --auto`) finds the newest
  release and classifies it as before. When it is due - a fast update in the
  maintenance window, or a migration update past its grace or confirmed - and
  its CLI is newer, it exits with code **40** and writes the requirement to
  install (`mascope-cli==<calver>`) to `.runtime/update/cli-to-install`. Install
  that, then run `--auto` again as a new process, which applies it. The
  update unit (below) does both.
- **What ran last** is kept in `.runtime/deployed-release.json`, written once
  the stack is up healthy on a release (`prod update`, `--auto`, and
  `prod up --detach`, which deploys the CLI's release).
  `mascope prod doctor` reports drift between the containers and the CLI's
  release until the `prod up` after an upgrade.
- **On a checkout install** the checked-out tag says which release runs, and
  none of this applies.

## Boot and maintenance units (Linux)

`mascope prod units install` installs the systemd units a server needs. Run
it as the account that runs the deployment; it uses `sudo` for the privileged
steps.

```sh
mascope prod units install --dry-run   # print what it would write and run
mascope prod units install
sudo systemctl enable --now mascope-update.timer   # opt in to unattended updates
```

- **`mascope.service`** starts the stack at boot. It is enabled.
- **`mascope-update.service` and `.timer`** run the unattended update nightly.
  On exit 40 the unit installs the CLI named in
  `.runtime/update/cli-to-install` with `uv tool install`, then runs `--auto`
  again. The timer stays off until you enable it.
- **The disk check and the assignment-run retention** run on timers that are
  enabled.

The units run the `mascope` executable that installed them, on this runtime
home, as this account. The update needs that executable to be a
`uv tool install mascope-cli`. Their settings files are seeded once into
`/etc/mascope`, and `MASCOPE_PATH` is added to `/etc/environment` when it is
missing. Re-run the command after a CLI upgrade that changes the units;
`mascope prod units remove` takes them out again. The full story is in
[docs/maintaining.md](https://github.com/ultra-trace-systems/mascope/blob/develop/docs/maintaining.md).

## Limits of a wheel install

A wheel install starts a stack and moves it across releases with its CLI, but
lacks some of what a server's source checkout gets from `tooling/ubuntu.sh`:

- **The system packages are yours to install.** Docker, and uv for the
  unattended update. `tooling/ubuntu.sh` installs them on a checkout.
- **Only a release cut with a CLI can be deployed.** A pre-release built
  without a CLI of its own, or the rolling `latest`, needs a checkout.

## Development

The Mascope source checkout exposes additional developer commands (`dev`,
`test`, `agent`, `backend`) that operate on the monorepo; they are not part
of the standalone install. See the
[repository](https://github.com/ultra-trace-systems/mascope) for the developer guide.
