# CLI decoupling plan

Status: **the first plan's phases 0 to 4 shipped in v1.2.0 (2026-07-07):
`mascope-cli` and `mascope-runtime` are on PyPI, a wheel install runs the
operator surface from a runtime home `mascope init` creates, and CI installs
the wheel on every pull request. What is open is narrower: every server
install in the repository's own tooling is still the checkout install, and a
wheel install is not yet a deployment that follows a release. Phases 5 to 8
below are that work; phase 6 is a decision and gates 7 and 8** (2026-10-14).
Code anchors were re-read on `develop` on that date and are named where a
claim rests on them; paths under `cmd/` are in `tooling/cli/src/mascope_cli/`.

## Picking this up

The goal is unchanged: an operator installs the CLI on a fresh machine and
uses it to install, launch, update and back up a Mascope deployment without a
source checkout. Two installs exist today, and they behave differently:

- **The checkout install** is what `docs/hosting.md`, `tooling/ubuntu.sh`
  and the Ansible playbooks under `tooling/fleet/` set up. The server holds a
  clone of the repository, the checked-out release tag selects the images,
  and the CLI is a uv tool built from that clone.
- **The wheel install** is `pip install mascope-cli` (or `uv tool install
  mascope-cli`) followed by `mascope init`, as `tooling/cli/README.md`
  describes. It runs, CI proves it, and some operator commands were made to
  work on it specifically (section 2.5). But nothing records which release it
  deploys, its compose file is the one its CLI version shipped, and it has no
  boot or update units (section 3).

Read section 3 first, then the phases in section 4. Section 1 is the record
of what the first plan delivered; section 2 is its inventory, re-read.

## 1. What the first plan delivered

The plan was written for v1.2.0 (2026-07-07), and that release shipped it:
every item of phases 0 to 4 except the two marked below.

| Phase | Item | State | Where |
|---|---|---|---|
| 0 | Hermetic CLI suite | shipped | `tooling/cli/tests/conftest.py` builds a throwaway runtime home |
| 0 | CI runs it on every PR | shipped | `.github/workflows/tests.yaml`, job `cli` |
| 1 | Lazy runtime, side-effect-free import | shipped | `runtime.py` (`get_runtime`, `_LazyRuntime`) |
| 1 | Compose paths resolved per call | shipped | `cmd/prod/main.py` `_compose_path`; no module-level reads of `MASCOPE_PATH` remain |
| 1 | `pg` / `cmd.dev` circular import untangled | shipped | `docker.py` holds `is_docker_running`; `cmd/__init__.py` has no ordering hack |
| 1 | Shared state globals fixed | shipped | `mascope_runtime/state.py` keeps state per instance; `libraries/runtime/tests/test_state.py` pins it |
| 2 | Platform default home, `mascope init` | shipped | `home.py` (`default_home`), `cmd/init.py` |
| 2 | Config layers and compose files as package data | shipped | `mascope_cli/data/`; `tests/test_package_data.py` fails on drift from the repo root copies |
| 2 | Version from the installed package off a checkout | shipped | `version.py` (`resolve_version`) |
| 3 | Developer commands registered only in a checkout | shipped | `main.py` gates `dev`, `backend`, `agent`, `instance`, `test`, `fleet`, `reference` on `checkout.source_checkout()` |
| 3 | `mascope-runtime` published, `duckdb` an extra | shipped | `libraries/runtime/pyproject.toml` extra `logs`; the CLI requires `mascope_runtime[logs]` |
| 3 | `pandas` / `requests` in a `demo` extra | shipped | `tooling/cli/pyproject.toml` |
| 3 | Dependency set verified with `deptry` | **open** | `deptry` is in the root `dev` group (`pyproject.toml`) but nothing runs it; section 2.2 |
| 4 | Trusted publishing on version bump | shipped | `.github/workflows/publish-pypi.yaml`; ten CLI releases since 2026.7.7 |
| 4 | Wheel install smoke | partly | `tests.yaml` job `cli-package` and `.github/scripts/wheel_smoke.py` stop at `init` then `modules`; nothing runs `prod up` from a wheel (phase 8) |

## 2. The entanglement inventory, re-read

### 2.1 Import-time coupling: gone

- Importing the CLI needs no environment. `runtime.py` creates the
  `Runtime` on first attribute access, and `_ensure_mascope_path` falls back
  to the default home only when `mascope init` has initialized it.
  `tests/test_import_side_effects.py` runs `import`, `--help`, a command
  without a home, and `init` then a command, each in a clean subprocess.
- `cmd/__init__.py` imports only the operator groups; `main.py` imports the
  developer groups inside its `source_checkout()` branch.
- One stale description of the old shape remains: the docstring of
  `tooling/cli/tests/conftest.py` still says the singleton initializes at
  import time.

### 2.2 Packaging coupling: mostly resolved

- `mascope-runtime` is on PyPI, published from the same workflow, with the
  CLI pinning a floor (`mascope_runtime[logs]>=...` in
  `tooling/cli/pyproject.toml`). Two published packages bring a coupling the
  first plan did not have: version skew. A CLI module that imports a runtime
  name newer than the floor breaks every command for an operator left on an
  older runtime. CI's `cli-package` job imports the wheel against the oldest
  runtime the floor admits (`tooling/check-cli-runtime-floor.py`), and
  `tests/test_runtime_mirror.py` pins the one mirrored name
  (`runtime.DEPLOYMENT_FILE`). The developer guide's "Importing from the
  runtime" explains the two ways out.
- The **published `dev` extra cannot be installed**. It names
  `mascope_reference`, which is not on PyPI, so
  `pip install "mascope-cli[dev]"` does not resolve. Nothing needs it: the
  commands it serves are never registered off a checkout, and the workspace
  takes it through the root `pyproject.toml` (`mascope_cli[dev,demo]`).
- Undeclared imports, all in paths that need a checkout anyway: `deptry`
  run over `tooling/cli/src` (2026-10-14) reports `mascope_backend`
  (`cmd/demo/main.py`, `cmd/demo/build_bundle.py`), `mascope_sdk`
  (`cmd/demo/_rebuild.py`), `numpy` (`cmd/demo/verify.py`, present through
  the `demo` extra's `pandas`), and `mascope_thermo` (`cmd/file.py`, which
  has a fallback, section 2.5). The rest it reports are `loguru` and
  `typing_extensions`, which arrive through declared dependencies.
- The compose files and config layers ship in the wheel (`mascope_cli/data/`)
  and `mascope init` copies them into the home. That closed the first plan's
  gap and opened the one section 3 describes: the copies belong to the CLI
  version, not to the release being deployed.

### 2.3 Checkout-assuming commands: gated, with one leak

- `dev`, `test`, `backend`, `agent`, `instance`, `fleet` and `reference` are
  registered only in a checkout (`main.py`). `.github/scripts/wheel_smoke.py`
  asserts `dev`, `test`, `agent` and `backend` are absent from a wheel.
- **`demo` leaks.** It is registered everywhere, and the wheel smoke lists
  it as an operator command, but most of it drives the source tree. The
  callback pins dev mode (`cmd/demo/main.py`, `main`); the bare command and
  `--fresh` import `cmd.dev.*`, whose `cmd/dev/db/main.py` imports
  `psycopg2`, from the `dev` extra; seeding imports `mascope_backend`;
  `snapshot` runs `build_bundle`, which imports `mascope_backend`; `verify`
  reads goldens through `mascope_backend`. Installed from the wheel in an
  isolated environment (2026-10-14), `mascope demo --no-launch` fails with
  `ModuleNotFoundError: No module named 'psycopg2'`. Only `fetch` and `info`
  work there. The operator's demo is the compose file `mascope init` writes
  (`docker compose -f docker-compose.demo.yaml up -d`, as the README says).
- `prod manifest` needs alembic and a source tree. It runs at release build
  time (`.github/workflows/build-release-images.yaml`, "Publish the release
  manifest"), and on a wheel install it stops with a message rather than a
  traceback (`cmd/prod/release_manifest.py`, `read_alembic_head`). It can stay
  where it is.
- `Runtime.parse_version()` takes a `cwd`, and the deploy path asks git
  in `MASCOPE_PATH`, not the working directory (`cmd/prod/main.py`,
  `_deploy_version`). Off a checkout it answers `unknown-version`, which is
  where section 3 starts.

### 2.4 The latent bug: fixed

`mascope_runtime/state.py` stores state per instance, and its module
docstring says why. `libraries/runtime/tests/test_state.py` covers it
(`test_json_states_do_not_share_paths`, `test_temp_states_are_independent`,
`test_temp_state_is_isolated_from_json_state`).

### 2.5 What changed around the CLI since the first plan

- **The CLI's version is date-based** (`tooling/cli/pyproject.toml`,
  `2026.10.9`), set at release prep with the runtime's, as the developer
  guide's "Publish" section describes. It is in a different series from the
  app's `vX.Y.Z` tags. `version.py` reports it as `v<calver>` off a
  checkout, and `_deploy_version` refuses to use it as an image tag. So each
  app release has a CLI release cut with it, but nothing machine-readable
  says which: the release manifest records `app_version` and
  `alembic_head` (`cmd/prod/release_manifest.py`, `build_manifest`), not the
  CLI version.
- **The per-worktree instance work** (`instance.py`, `cmd/instance.py`,
  `.runtime/instances.json`) made "the source tree is not the runtime home"
  explicit on the developer side: `checkout.backend_path()` takes the
  migration chain from the running source, never from `MASCOPE_PATH`, and
  `cmd/dev/main.py` (`_source_root`) launches the worktree's code. It is the
  seam phase 2 drew for operators, now drawn for developers too, and it adds
  no coupling to the operator surface: all of it sits behind the checkout
  gate. The one operator-side trace is `backend_path()`'s fallback, which
  assumes a checkout install (`MASCOPE_PATH/server/backend`) and is reached
  only by `prod manifest`.
- **Operator commands that need backend code run it in the backend
  container.** `prod db script` discovers and runs the scripts there
  (`cmd/prod/db/scripts.py`), and `file scans` copies the file in and reads
  it there when `mascope_thermo` is not installed (`cmd/file.py`). This is
  the pattern for anything else a wheel install has to do with backend code.
- **The update machinery is built on the checkout.** `prod update` and the
  unattended `--auto` path (`cmd/prod/main.py`, `update` and `_auto`),
  `prod doctor`'s DRIFT line (`cmd/prod/doctor.py`), `_align_checkout`, and
  the stale-CLI warning (`cmd/prod/cli_drift.py`) all take the checkout's tag
  as the deployed release, and the checkout's `tooling/cli` as what the CLI
  should be.

## 3. The two installs

| | Checkout install | Wheel install |
|---|---|---|
| Set up by | `git clone`, `git checkout vX.Y.Z`, `tooling/ubuntu.sh install` (`docs/hosting.md`) | `pip install mascope-cli`, `mascope init` (`tooling/cli/README.md`) |
| `MASCOPE_PATH` | the clone | `~/.mascope` or `%LOCALAPPDATA%\Mascope` (`home.py`), or the env var |
| Which release runs | the checked-out tag (`_deploy_version`) | a `MASCOPE_VERSION` pin, otherwise `latest`, with a warning on every command that resolves it |
| After `prod update --version vX.Y.Z` | the checkout moves to the tag (`_align_checkout`), so the next `prod up` and a reboot stay on it | the pin lasts for that process (`update` sets it in `os.environ`); the next `prod up` deploys `latest` again |
| Compose file and config layers | the release's own, from the checkout | the CLI's bundled copies, written at `init`; `prod update` never refreshes them, and `init --force` overwrites the base and mode layers wholesale |
| How the CLI is updated | `uv tool install --force --reinstall --python 3.12 . --with-executables-from mascope-cli` in the clone (builds the whole workspace, backend included, from source) | `pip install -U mascope-cli` |
| CLI behind the release | detected (`cli_drift.py`), reinstall printed | not detectable: there is no checkout to compare with |
| Boot, unattended update, retention, disk check | systemd units from `tooling/systemd/`, installed by `tooling/ubuntu.sh` with `@@MASCOPE_PATH@@` and `@@MASCOPE_BIN@@` filled in | none |
| Fleet automation | `tooling/fleet/update.yml` checks out the tag and reinstalls the tool; `roles/deploy_env` refuses a home that is not a checkout | none |
| Demo | the demo compose file (`mascope demo` is the developer's flow) | the demo compose file; `mascope demo` itself fails (section 2.3) |

The fallback warning in `_deploy_version` tells a wheel operator to "deploy
from a checkout at the release tag", which is the one remedy their install
cannot take.

So the wheel install is a working way to start a stack, and not yet a way to
run one across releases. Closing that is phases 7 and 8. Whether to close it,
or to keep the checkout install as the server install and the wheel as a
trial path, is phase 6.

## 4. The plan from here

Each step is one pull request into `develop`, as everywhere else. Phase 5 is
independent of the decision and can start now.

### Phase 5 - the wheel install's sharp edges

1. **`demo` on a wheel install** (#2350). Register the subcommands that
   drive the source tree (the bare command with `--rebuild` and `--fresh`,
   `snapshot`, `verify`) only in a checkout, the way `main.py` gates `dev`,
   and have the group on a wheel point at the demo compose file. Keep
   `fetch` and `info`. `wheel_smoke.py` asserts the result.
2. **Say what a wheel install does.** The `_deploy_version` warning names
   the `MASCOPE_VERSION` pin as the remedy when there is no checkout, and
   `prod update --version` on a wheel install says the pin does not outlive
   the command. The README states the limits in section 3 until phase 7
   removes them.
3. **Published metadata.** Move the `dev` extra's packages to the root
   `pyproject.toml`, which is the only place that installs them, and add a
   `deptry` check of `tooling/cli` to CI with the checkout-only imports of
   section 2.2 listed as known. Fix the stale texts on the way: the
   `conftest.py` docstring, the comment in `tooling/ubuntu.sh` that says every
   Mascope package is versioned `0.0.0` (the CLI, the runtime, the SDK and
   the tools are calver), and the developer guide's path to `main.py`.

### Phase 6 - decision: what a server installs

A call for the plan owner. The options:

- **(a) The checkout stays the server install.** The wheel is documented as
  the single-machine and trial path with the limits of section 3, and
  phases 7 and 8 reduce to that documentation plus the end-to-end smoke
  (step 8.2).
- **(b) The wheel becomes the server install.** Phases 7 and 8 in full, then
  the servers move one at a time. A server then needs no git and no source
  tree, the CLI install no longer compiles the workspace, the release and the
  CLI become one install step, and the isolated-deployment bundle can drop
  its git bundle (`isolated_deployment_plan.md`, section 5.1). It costs a
  move of every server and a rewrite of the update machinery's assumptions
  (section 2.5).

Recommendation: (b), sequenced after the 2.0 rollout so the servers move once
and not during it. Phase 7 can be rehearsed on a testbed in prod mode
without touching a site.

### Phase 7 - a wheel deployment follows its release (if 6 is b)

1. **Record the deployed release** in the runtime home, written by
   `prod update` and `prod up` when a release is named and read by
   `_deploy_version` before the `latest` fallback; `prod doctor` compares the
   running images with it. Not the backend's `deployment.json`, which holds
   the deployment's id.
2. **Decision: where a release's compose file and config layers come from.**
   Either the CLI deploys only the release it was cut with, and moving to
   another release means installing that CLI first; or each release publishes
   its compose file and config layers beside `mascope-manifest.json`, and
   `prod update` refreshes the home from them. Recommendation: the first. It
   keeps one source for both and makes `uv tool install mascope-cli==<calver>`
   the update, with the unattended path installing the CLI and applying the
   release in two separate processes, so the rule in `cli_drift.py` (never
   reinstall the running tool from inside its own run) holds.
3. **Implement step 2**, with the release manifest naming its CLI version,
   and operator settings kept in the env overlay
   (`.runtime/env/<env>/<mode>.mascope.toml`), which no refresh touches.

### Phase 8 - boot, timers and the move (if 6 is b; 8.2 either way)

1. **Units from the CLI.** Ship `tooling/systemd/` as package data and a
   command that installs the units with the paths filled in, as
   `tooling/ubuntu.sh` does now; `ubuntu.sh` keeps only the prerequisites
   and the wheel install.
2. **Packaged end-to-end smoke.** Install the wheel in a clean container,
   `mascope init`, `mascope prod up` against published release images, then
   `tooling/smoke-test.sh`. This is the first plan's open phase 4 item.
3. **Docs and fleet.** `docs/hosting.md` and `docs/maintaining.md` describe
   the wheel install for servers; the Ansible roles learn it; the move is
   rehearsed on a testbed, then made one server at a time, with the old
   checkout left in place until the server has run one release from the
   wheel.

### The CLI's open GitHub issues

Six are attached to this plan's Linear project, re-read against `develop` on
2026-10-14. None depends on the decoupling, and each can be fixed on its
own.

- **#1735** (worktree instances apply the main checkout's alembic chain):
  fixed by #1854, which added `checkout.backend_path()`; the regression
  tests are in `tests/test_dev_migrate.py`. The issue can be closed.
- **#1858** (`prod update --check` reports up to date when a newer release
  exists): partly fixed. The revert after a reboot it also describes is gone
  since #1862 (`_align_checkout`). The main defect stands: `--check`
  classifies `_deploy_version()`, which is the checkout's own tag, and only
  `--auto` asks for the newest release (`cmd/prod/auto_update.py`,
  `latest_release_tag`). Operator-facing, small.
- **#1898** (log history unreachable): partly fixed. `logs query` reads the
  rotated archives since #1915 (`mascope_runtime/logging.py`). What remains
  is on the backend side: every uvicorn worker rotates its own sink on the
  same path, so one day ends up split across several archives, some of them
  empty. The fix is in the runtime, and the CLI's floor moves only if the CLI
  starts using something new from it.
- **#1907** (CLI warnings reach error monitoring): the trigger is gone, since
  `prod db script` discovers scripts in the container (#1914) and the nightly
  unit no longer imports the backend on the host. The gap is not. The
  carve-out is decided for each `Runtime`, while logging is configured for
  the whole process, so a host import of `mascope_backend` from a CLI process
  installs the monitoring sink again. Two such imports remain: the host
  fallback in `cmd/prod/db/scripts.py` and the demo seeding. A runtime fix,
  plus a test of a CLI `Runtime` followed by a backend one.
- **#1970** (`dev migrate` discards alembic's output): still reproduces.
  `cmd/dev/migrate.py` runs `upgrade` and `downgrade` with `capture=True`
  and without `show_output`, and on failure it logs only stderr.
  Developer-only, and a one-line fix with a test.
- **#1006** (a command for orphaned sample files): not built, and the
  command group it names no longer exists. It fits the pattern in section
  2.5: a report script and a removal script under `mascope_backend.db.scripts`,
  which `prod db script run` runs in the container.

Filed during this review: **#2350**, the `demo` leak of section 2.3, which is
step 5.1. Also open on the operator surface: #1473 (`env` has no delete, and
`env create` only makes the directory) and #1908 (the `--skip-backup` log
level is tested only as a helper).

## 5. Test strategy

The rule stands: the regression net for a seam lands before the seam moves.
Unit tests need no Docker, Postgres or network; anything that shells out is
tested through mocked `lib.run` / `subprocess.run` with argument assertions.

What exists in `tooling/cli/tests/` for the seams above:

- the import and no-checkout flow: `test_import_side_effects.py`,
  `test_checkout.py`, `test_init.py`, `test_package_data.py`;
- the entrypoint and versions: `test_main.py`, `test_runtime_version.py`;
- the runtime floor: `test_runtime_mirror.py`,
  `test_check_cli_runtime_floor.py`;
- prod compose and the update path: `test_prod_compose.py`,
  `test_prod_update.py`, `test_prod_auto.py`, `test_prod_cli_drift.py`,
  `test_prod_doctor.py`, `test_prod_manifest.py`, `test_prod_preflight.py`;
- `lib.run`, `pg.utils`, the test command: `test_lib_run.py`,
  `test_pg_utils.py`, `test_test_cmd.py`;
- the demo: `test_demo_deid.py`, `test_demo_verify.py`, `test_demo_fetch.py`,
  `test_demo_rebuild.py` and the rest of `test_demo_*`.

Outside the suite, CI's `cli-package` job installs the built wheels in
isolation and runs `wheel_smoke.py`, installs the workspace as a uv tool the
way a server does, and imports the wheel against the oldest runtime its
floor admits.

What the open phases need first:

- phase 5: `wheel_smoke.py` asserting which `demo` subcommands a wheel has,
  and a test that the fallback warning differs with and without a checkout;
- phase 7: tests of the deployed-release record through `_deploy_version`,
  `update` and `doctor`, written against today's behaviour before it
  changes; and, for the refresh, a test that the env overlay survives it;
- phase 8: the packaged smoke of step 8.2, which needs Docker and so runs in
  CI, not in the hermetic suite.

## Related

- [isolated_deployment_plan.md](isolated_deployment_plan.md) - the update
  design for a server with no internet route, written against the checkout
  install.
- `tooling/cli/README.md` - the wheel install as an operator meets it.
- The developer guide: "Importing from the runtime" and "Publish".
- The Linear project "CLI decoupling" (team Mascope): the steps of phases 5
  to 8 as issues, read beside this note.
