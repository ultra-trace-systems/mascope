# Mascope — agent guide

Mascope is a mass-spectrometry analysis web app: FastAPI backend (`server/backend`),
Vue 3 + PrimeVue frontend (`server/frontend`), shared Python libraries (`libraries/`),
Typer CLI (`tooling/cli`, entry point `mascope`). Postgres + Redis run via docker
compose. See `docs/dev/developer_guide.md` for the full picture.

The repository is **public** (github.com/ultra-trace-systems/mascope, Apache-2.0) - do not
assume it is private. Linking end users to GitHub files, raw URLs, or the issue
tracker is fine.

## Running your own instance (agents in worktrees)

Several worktrees on one machine can each run the full app at once against a
single shared Postgres/Redis. Use this when you need the stack up (backend
tests, e2e, manual checks) without colliding with other worktrees on the ports
or database.

The shared runtime home (`MASCOPE_PATH`) holds the infra, database volumes, and
secrets, and is shared by every worktree; `mascope dev run` still launches the
app from *your* worktree's source. Leave `MASCOPE_PATH` at the machine's shared
home — do not point it at your worktree, or you would spin up a second Postgres.

```sh
mascope dev up                 # shared Postgres + Redis (once per machine; idempotent)
mascope dev run --instance     # YOUR isolated stack for this worktree
```

`--instance` (or `-i`, or `MASCOPE_INSTANCE=1`) binds this worktree to a slot and
derives everything from it: a dedicated env with its own `mascope_<env>`
database and filestore, backend on `8090 + slot`, frontend on `5173 + slot`. The
binding is stable and recorded in `.runtime/instances.json`.

```sh
mascope instance list                  # all slots: env, ports, worktree
mascope instance show [--export]       # this worktree's instance (allocates if needed)
mascope instance rm <env> --purge      # release the slot + delete its filestore when done
mascope dev db drop --env <env> --yes  # then drop its database
```

Gotcha (Windows): mascope's terminal logs contain a glyph that crashes when
stdout is captured or piped under the default cp1252 encoding. When you capture
CLI output (as agents do), run with `PYTHONUTF8=1`; interactive terminals are
unaffected. See `docs/dev/developer_guide.md` →
"Running multiple instances on one machine" for the full picture.

## Running tests

| Suite | Command | Needs | Speed |
|---|---|---|---|
| Backend (pytest) | `mascope test run` or `uv run pytest server/backend/tests/` | Postgres (`mascope dev up`) | minutes |
| Libraries (pytest) | `mascope test run libraries` | nothing | fast |
| CLI (pytest) | `uv run pytest tooling/cli/tests/` | nothing (hermetic conftest) | seconds |
| Frontend unit (Vitest) | `npm run test:unit` in `server/frontend` | nothing | ~1 s |
| Frontend e2e (Playwright) | `npm run test:e2e` in `server/frontend` | a running stack, see below | minutes |
| Deployment smoke | `bash tooling/smoke-test.sh` | a running stack | seconds |
| API benchmark | `MASCOPE_BENCH_TEST=1 uv run pytest server/backend/tests/system/benchmark/` | a running demo stack | minutes (scales the data) |

Run the suite that covers what you changed before finishing. Frontend unit tests are
the default place for new frontend tests; only reach for e2e when the behavior spans
the real backend.

### The e2e stack

The hermetic e2e suite (`server/frontend/tests/e2e/`) targets the demo stack:

```sh
docker compose -f docker-compose.demo.yaml up -d   # frontend at http://localhost:8080
```

It comes preloaded with the published demo dataset and login `demo@mascope.app` /
`mascope-demo` (first start downloads ~150 MB). Point the suite elsewhere with
`MASCOPE_E2E_BASE_URL` / `MASCOPE_E2E_API_URL` / `MASCOPE_E2E_EMAIL` /
`MASCOPE_E2E_PASSWORD`. The upload spec needs a demo raw file
(`MASCOPE_E2E_RAW_FILE`; defaults to the local bundle cache under
`.runtime/demo/`, skips if none found).

### Writing and debugging tests

- Auth is handled once in `tests/e2e/setup/auth.setup.js` (API login → storage state).
  Seed further state through the `api` and `scratch` fixtures in
  `tests/e2e/fixtures/index.js`, not by clicking through the UI. The `scratch`
  fixture provides a per-test workspace + dataset and cleans up after itself.
- Prefer `getByLabel` / `getByRole` locators; add `data-testid` only where no
  accessible handle exists.
- e2e runs keep a trace on failure: `npx playwright show-trace test-results/<...>/trace.zip`.
  Debug a single test with `npm run test:only -- "<name>"` or `test:headed` / `test:trace`.
- Unit tests live in `server/frontend/tests/unit/`, mirroring `src/` paths
  (`tests/unit/lib/chem.spec.js` covers `src/lib/chem.js`).

## Conventions

- Conventional Commits (`type(scope): description`); ASCII-only commit messages,
  no Co-Authored-By trailers.
- **Rebase PR branches, never merge into them** - a PR should stay a linear
  series of commits on top of `develop`. When a branch conflicts or falls
  behind, `git fetch origin develop && git rebase origin/develop`, resolve the
  conflicts, and force-push with `--force-with-lease`. Do not merge `develop`
  into a PR branch - no "Merge branch 'develop' into ..." commits. If one was
  already made, `git reset --hard <pre-merge-commit>` and rebase instead;
  comparing `git rev-parse HEAD^{tree}` against the merge's tree confirms the
  conflict resolution survived unchanged.
- **Anonymize commit messages and PR descriptions** - this repository is public,
  so text pushed to GitHub must never contain real instrument names or IDs,
  customer names, internal or customer server hostnames, tailnet/LAN addresses,
  or raw-data file names that identify a site. Describe them generically
  instead ("a customer Orbitrap instrument", "a production server", "the LAN
  agent host") or use neutral placeholders ("server A", "instrument X"). The
  same applies to issue comments, code comments, and committed test data.
- **Lint Python before committing** - CI's "Lint and format" job runs
  `ruff check .` and `ruff format --check .` and fails the PR on any violation.
  Run `uv run ruff check --fix . && uv run ruff format .` before you commit.
- CI (`.github/workflows/tests.yaml`) runs the "Lint and format" (ruff) job plus
  backend pytest, library pytest, CLI pytest, frontend unit, and the demo-stack
  e2e suite on every PR; releases are gated on `tooling/smoke-test.sh`.
