#!/usr/bin/env bash
# Packaged end-to-end smoke: start a production stack from a wheel install.
#
# The wheel smoke (wheel_smoke.py) stops at `mascope init`. This goes on to
# what an operator does next on a fresh machine, with nothing but the built
# CLI and runtime wheels: `mascope init`, `mascope cert gen`, `mascope prod
# up` on published release images, then tooling/smoke-test.sh against the
# stack and `mascope prod down`. A compose file, secret or config layer that
# the wheel's bundled copies get wrong fails here rather than on a site.
#
# It needs Docker and the network (it pulls the release images), so it is no
# part of the hermetic suites. It binds port 443 and the `mascope_prod_*`
# container names: run it on a CI runner, never beside a production stack.
#
#   uv build --package mascope_cli --out-dir dist
#   uv build --package mascope_runtime --out-dir dist
#   bash .github/scripts/packaged_smoke.sh dist
#
# The release deployed is the one the CLI was cut with
# (`mascope_cli.release.RELEASE`), pinned with MASCOPE_VERSION: on develop
# that is the newest release. A wheel install deploys that release alone, so
# another pin is refused by `prod up`. A release candidate never bumps
# RELEASE (only `tooling/changelog.py compile` does), so a candidate's run
# starts the previous release's images, not the candidate's: on a release
# event whose tag is not RELEASE the run says so with a warning. When that
# release's images are not published yet (release prep has landed, the
# release has not), the smoke is skipped with a warning: no image of it
# exists to start. A tag pinned by hand must exist.
#
# The published images run as uid 1000 and read the secrets `mascope init`
# writes owner-only, so the install has to be that uid's, as on a server
# (docs/hosting.md). Run as another user, the script stops, unless
# SMOKE_SWITCH_USER=1 lets it run itself again as the user with uid 1000
# (created when there is none, and put in the docker group) through sudo, or
# runuser when it is root. On GitHub Actions (GITHUB_ACTIONS=true) it does so
# by default: a runner's own user is uid 1001. SMOKE_SWITCH_USER=0 stops it.
#
# Environment:
#   MASCOPE_VERSION    image tag to deploy (default: the wheel's own release)
#   SMOKE_WORKDIR      scratch directory (default: a new temporary directory;
#                      given, it must be writable by uid 1000 when switching)
#   SMOKE_TIMEOUT      seconds tooling/smoke-test.sh waits (default 900)
#   SMOKE_SWITCH_USER  1 to run as uid 1000 when started as another user
#                      (default 1 on GitHub Actions, else 0)
set -euo pipefail

dist=$(cd "${1:-dist}" && pwd)
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

say() { echo "[packaged-smoke] $*"; }

switch_default=0
[ "${GITHUB_ACTIONS:-}" != true ] || switch_default=1
if [ "$(id -u)" != 1000 ]; then
  if [ "${SMOKE_SWITCH_USER:-$switch_default}" != 1 ]; then
    say "FAIL: run as uid 1000, the published images' user, or set SMOKE_SWITCH_USER=1" >&2
    exit 1
  fi
  as_root=()
  [ "$(id -u)" = 0 ] || as_root=(sudo -n)
  user=$(getent passwd 1000 | cut -d: -f1 || true)
  if [ -z "$user" ]; then
    user=mascope
    "${as_root[@]}" useradd --uid 1000 --create-home "$user"
  fi
  "${as_root[@]}" usermod -aG docker "$user"
  # What the other user runs, where it can read it: the wheels and the two
  # scripts, laid out as in the checkout.
  stage=$(mktemp -d)
  mkdir -p "$stage/dist" "$stage/.github/scripts" "$stage/tooling"
  cp "$dist"/*.whl "$stage/dist/"
  cp "${BASH_SOURCE[0]}" "$stage/.github/scripts/packaged_smoke.sh"
  cp "$repo/tooling/smoke-test.sh" "$stage/tooling/smoke-test.sh"
  # chmod first: once the directory is uid 1000's, a runner's user may not.
  chmod 755 "$stage"
  "${as_root[@]}" chown -R 1000 "$stage"
  say "running again as $user (uid 1000)"
  run_as=(sudo -n -u "$user")
  [ "$(id -u)" != 0 ] || run_as=(runuser -u "$user" --)
  exec "${run_as[@]}" env PATH="$(dirname "$(command -v uv)"):/usr/local/bin:/usr/bin:/bin" \
    MASCOPE_VERSION="${MASCOPE_VERSION:-}" SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-}" \
    SMOKE_WORKDIR="${SMOKE_WORKDIR:-}" GITHUB_ACTIONS="${GITHUB_ACTIONS:-}" \
    GITHUB_EVENT_NAME="${GITHUB_EVENT_NAME:-}" GITHUB_REF_NAME="${GITHUB_REF_NAME:-}" \
    bash "$stage/.github/scripts/packaged_smoke.sh" "$stage/dist"
fi
work=${SMOKE_WORKDIR:-$(mktemp -d)}
mkdir -p "$work"
work=$(cd "$work" && pwd)
base_url=https://localhost

# A clean environment with no checkout on the path: its own venv, its own
# HOME, a runtime home of its own, and a working directory outside the
# checkout, so nothing can resolve to the source tree.
cd "$work"
export HOME="$work/user"
export MASCOPE_PATH="$work/home"
mkdir -p "$HOME"
unset MASCOPE_ENV MASCOPE_INSTANCE PYTHONPATH VIRTUAL_ENV

say "installing the CLI and runtime wheels from $dist"
uv venv --quiet --python 3.12 "$work/venv"
cli_wheel=$(find "$dist" -maxdepth 1 -name 'mascope_cli-*.whl' | head -n1)
[ -n "$cli_wheel" ] || { say "FAIL: no mascope_cli wheel in $dist" >&2; exit 1; }
uv pip install --quiet --python "$work/venv/bin/python" \
  --find-links "$dist" "$cli_wheel"
export PATH="$work/venv/bin:$PATH"
mascope=$work/venv/bin/mascope

"$work/venv/bin/python" - <<'EOF'
from mascope_cli.checkout import source_checkout

found = source_checkout()
assert found is None, f"the wheel install found a checkout: {found}"
EOF

release=$("$work/venv/bin/python" -c \
  'from mascope_cli.release import RELEASE; print(RELEASE)')
pinned=${MASCOPE_VERSION:-}
export MASCOPE_VERSION=${pinned:-$release}
say "the wheel deploys $release; deploying $MASCOPE_VERSION"
if [ "${GITHUB_EVENT_NAME:-}" = release ] && [ "${GITHUB_REF_NAME:-}" != "$release" ]; then
  # A candidate: its wheel still carries the previous release.
  msg="release $GITHUB_REF_NAME is not the wheel's release $release, so its images are not smoked; $release's are"
  say "$msg"
  echo "::warning::$msg"
fi

for service in backend frontend; do
  image=ghcr.io/ultra-trace-systems/mascope/$service:$MASCOPE_VERSION
  if ! docker manifest inspect "$image" > /dev/null 2>&1; then
    # A tag asked for by name has to exist.
    if [ -n "$pinned" ]; then
      say "FAIL: $image is not published" >&2
      exit 1
    fi
    msg="$image is not published yet; skipping the packaged smoke"
    say "$msg"
    [ -z "${GITHUB_ACTIONS:-}" ] || echo "::warning::$msg"
    exit 0
  fi
done

say "mascope init"
"$mascope" init
say "mascope cert gen"
"$mascope" cert gen

# The prod layer sizes Postgres for a 64 GB server. A CI runner has 16 GB, so
# the env overlay, where an operator's own settings go, scales it down.
cat > "$MASCOPE_PATH/.runtime/env/default/prod.mascope.toml" <<'EOF'
[backend]
workers = 2

[backend.database]
shm_size = "1g"
max_connections = 60
shared_buffers = "512MB"
effective_cache_size = "2GB"
maintenance_work_mem = "256MB"
autovacuum_work_mem = "128MB"
EOF

stopped=0
finish() {
  status=$?
  if [ "$status" -ne 0 ]; then
    say "FAILED (exit $status); the stack's logs follow"
    "$mascope" prod docker ps -a || true
    "$mascope" prod docker logs --no-color --tail 200 || true
  fi
  if [ "$stopped" -eq 0 ]; then
    "$mascope" prod down || true
  fi
  exit "$status"
}
trap finish EXIT

say "mascope prod up --detach"
"$mascope" prod up --detach

# A fresh deployment has no account: register the first owner with the
# server's owner secret, as an operator does in the browser, then give it a
# workspace for the smoke's authenticated reads to find.
secret=$(head -n1 "$MASCOPE_PATH/.runtime/secrets/server_owner_secret_key.txt" | tr -d '\r\n')
email=owner@mascope.app
# Random, and free of the account's own name, which the password policy refuses.
password="Pw-$(openssl rand -hex 16)"
jar="$work/cookies"

# One API call; on a refusal, says which and what the API answered.
api() {
  local what=$1
  shift
  local code
  code=$(curl -sSk -o "$work/response" -w '%{http_code}' -b "$jar" -c "$jar" "$@")
  case "$code" in
    2??) ;;
    *)
      say "FAIL: $what answered $code: $(head -c 2000 "$work/response")" >&2
      return 1
      ;;
  esac
}

say "waiting for the API"
elapsed=0
until curl -fsSk -o /dev/null "$base_url/api/users/first-owner/status"; do
  if [ "$elapsed" -ge 600 ]; then
    say "FAIL: the API did not answer within 600s" >&2
    exit 1
  fi
  sleep 10
  elapsed=$((elapsed + 10))
done

say "registering the first owner"
api "first-owner registration" -X POST "$base_url/api/users/first-owner" \
  -H 'Content-Type: application/json' \
  --data "{\"email\": \"$email\", \"username\": \"owner\", \"password\": \"$password\", \"server_secret\": \"$secret\"}"
api "login" -X POST "$base_url/api/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=password' \
  --data-urlencode "username=$email" \
  --data-urlencode "password=$password"
api "workspace creation" -X POST "$base_url/api/workspaces" \
  -H 'Content-Type: application/json' \
  --data '{"workspace_name": "Packaged smoke"}'

say "tooling/smoke-test.sh"
SMOKE_BASE_URL=$base_url SMOKE_INSECURE=1 SMOKE_EMAIL=$email \
  SMOKE_PASSWORD=$password SMOKE_TIMEOUT=${SMOKE_TIMEOUT:-900} \
  bash "$repo/tooling/smoke-test.sh"

say "mascope prod down"
stopped=1
"$mascope" prod down
say "PASS"
