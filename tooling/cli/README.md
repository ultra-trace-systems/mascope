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
(`%LOCALAPPDATA%\Mascope` on Windows) — configuration TOMLs you can edit,
docker compose files, and generated application secrets. Set `MASCOPE_PATH`
to place it elsewhere. Re-running `init` is safe: existing files are kept and
secrets are never regenerated.

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
mascope prod update              # update the stack to the latest release
mascope prod db backup           # dump the database
mascope prod db restore --yes    # restore the latest dump
mascope logs query --grep error  # search the application's file logs
mascope env list                 # manage runtime environments
mascope export package <id>      # a dataset's archive, to deposit in a repository
```

Run `mascope --help` or `mascope <command> --help` for the full reference.

`mascope prod update` pulls the target release images, restarts the stack
with them and runs any pending database migrations (preceded by an automatic
pre-migration dump). Move to a specific release with
`mascope prod update --version v1.2.0`, or pin the deployed version for all
commands with `MASCOPE_VERSION`.

## The deployed release

A runtime home made by `pip install mascope-cli` and `mascope init` has no
source checkout to say which release it runs, so it keeps a record of its own
in `.runtime/deployed-release.json`:

- **What writes it.** `mascope prod update --version v1.2.0`, the unattended
  `mascope prod update --auto`, and `mascope prod up --detach` with a
  `MASCOPE_VERSION` pin to a release tag, each once the stack is up healthy.
  Any other `up` leaves the record as it is.
- **What reads it.** `mascope prod up`, a plain `mascope prod update`,
  `mascope prod doctor` and `--auto` use the recorded release when no
  `MASCOPE_VERSION` pin is set. `--auto` never moves the stack back past it.
- **Back to `latest`.** `mascope prod update --version latest` removes the
  record. With no record and no pin, the stack follows the `latest` images.
- **On a checkout install** the checked-out tag says which release runs, and
  the record is neither written nor read.

## Limits of a wheel install

A wheel install starts a stack and keeps its release, but does not yet carry
one across releases the way a server's source checkout does:

- **The compose file and config layers are the CLI's own.** `mascope init`
  writes the copies this CLI version ships, and `prod update` never refreshes
  them. `mascope init --force` overwrites the base and mode layers wholesale.
- **There are no boot or update units.** Nothing starts the stack at boot or
  runs the unattended update, backup retention or disk check. A checkout
  install gets these as systemd units from `tooling/ubuntu.sh`.
- **A CLI behind the release goes unnoticed.** There is no checkout to
  compare it with; upgrade it with `pip install -U mascope-cli`.

## Development

The Mascope source checkout exposes additional developer commands (`dev`,
`test`, `agent`, `backend`) that operate on the monorepo; they are not part
of the standalone install. See the
[repository](https://github.com/ultra-trace-systems/mascope) for the developer guide.
