#!/usr/bin/env bash
# Build and publish one workspace package to PyPI, skipping when the local
# version is already published. Runs inside the publish-pypi workflow, where
# `uv publish` authenticates via PyPI Trusted Publishing (OIDC).
#
# Usage: publish-package.sh <project-dir> <pypi-name> <module-name>
#   e.g. publish-package.sh libraries/sdk mascope-sdk mascope_sdk
set -euo pipefail

DIR=$1
PYPI_NAME=$2
MODULE=$3

# Wheels published earlier in this workflow run accumulate here, so a package
# whose workspace dependency was uploaded seconds ago resolves it locally
# instead of waiting for PyPI's index to propagate the new release.
WHEELHOUSE="${WHEELHOUSE:-$PWD/.wheelhouse}"
mkdir -p "$WHEELHOUSE"

LOCAL=$(uv version --project "$DIR" --short)

# `has` against .releases (not .info.version) so republishing an older version
# by accident is also caught. An empty/failed response (new project) publishes.
ALREADY_PUBLISHED=$(
  curl -fsS "https://pypi.org/pypi/$PYPI_NAME/json" \
    | jq -r --arg v "$LOCAL" '.releases | has($v)' || echo "false"
)
echo "$PYPI_NAME: local version $LOCAL, already on PyPI: $ALREADY_PUBLISHED"

if [ "$ALREADY_PUBLISHED" = "true" ]; then
  echo "Nothing to publish."
  exit 0
fi

rm -rf dist
uv build --package "$MODULE"

# The wheel must install and import cleanly (with its dependencies) before
# anything is uploaded. Dependencies resolve from PyPI, except workspace
# siblings published moments ago in this same run, which come from the local
# wheelhouse. A dependency uploaded outside this run can still briefly be
# missing from PyPI's index, so resolution failures are retried with backoff;
# a genuinely broken wheel fails every attempt and still fails the job.
check_install() {
  uv run --isolated --no-project \
    --find-links "$WHEELHOUSE" --with dist/*.whl \
    python -c "import $MODULE"
}

for delay in 10 20 40 60 0; do
  if check_install; then
    break
  fi
  if [ "$delay" -eq 0 ]; then
    echo "$PYPI_NAME $LOCAL failed the install check after all retries." >&2
    exit 1
  fi
  echo "Install check failed (PyPI index still propagating?); retrying in ${delay}s..."
  sleep "$delay"
done

uv publish
cp dist/*.whl "$WHEELHOUSE"/
echo "Published $PYPI_NAME $LOCAL."
