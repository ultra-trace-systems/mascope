"""
Packaging smoke test for the standalone mascope-cli wheel.

Run inside an isolated environment where the built mascope_cli wheel (and its
mascope_runtime dependency) are installed and the platform default home is
redirected to a throwaway directory, e.g.:

    uv build --package mascope_cli --out-dir dist
    uv build --package mascope_runtime --out-dir dist
    HOME=$(mktemp -d) uv run --isolated --no-project \
        --with dist/mascope_cli-*.whl --find-links dist \
        python .github/scripts/wheel_smoke.py

Asserts the operator install contract: no source checkout detected, no
developer commands or dependencies, and the fresh-machine flow
(`mascope init` -> a runtime-backed command) works end to end.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from mascope_cli.checkout import source_checkout
from mascope_cli.main import app


# A wheel install must not look like a source checkout.
assert source_checkout() is None, f"unexpected checkout: {source_checkout()}"

# Dev-only dependencies must not be present in the operator install.
for module in ("alembic", "psycopg2", "pandas"):
    assert importlib.util.find_spec(module) is None, f"{module} leaked in"

runner = CliRunner()

result = runner.invoke(app, ["--help"])
assert result.exit_code == 0, result.output
for command in ("init", "prod", "env", "demo", "logs", "cert"):
    assert command in result.output, f"operator command {command} missing"

# Developer commands must not be registered.
for command in ("dev", "test", "agent", "backend"):
    probe = runner.invoke(app, [command, "--help"])
    assert probe.exit_code != 0, f"developer command {command} is registered"

# The fresh-machine flow: init the default home, then run a command that
# needs the runtime (config loading, state, version resolution).
init = runner.invoke(app, ["init"])
assert init.exit_code == 0, init.output

modules = runner.invoke(app, ["modules"])
assert modules.exit_code == 0, modules.output
assert "backend" in modules.output

# `demo` keeps only what works without the source tree: `fetch` and `info`.
# Seeding, `snapshot` and `verify` import the dev extra and the backend.
demo_help = runner.invoke(app, ["demo", "--help"])
assert demo_help.exit_code == 0, demo_help.output
for command in ("fetch", "info"):
    assert command in demo_help.output, f"demo {command} missing"
for command in ("verify", "snapshot"):
    probe = runner.invoke(app, ["demo", command, "--help"])
    assert probe.exit_code != 0, f"demo {command} is registered"
for option in ("--rebuild", "--fresh", "--no-launch"):
    probe = runner.invoke(app, ["demo", option])
    assert probe.exit_code == 2, f"demo {option} is registered: {probe.output}"

# The bare command points at the compose demo and exits non-zero, rather than
# failing on an import (psycopg2) the operator install does not have. Run the
# console script itself: the runtime's log sink writes the message to the real
# terminal streams, which CliRunner swaps out and closes.
mascope = Path(sys.executable).parent / (
    "mascope.exe" if os.name == "nt" else "mascope"
)
assert mascope.is_file(), f"console script missing: {mascope}"
demo = subprocess.run([str(mascope), "demo"], capture_output=True, text=True)
demo_output = demo.stdout + demo.stderr
assert demo.returncode == 1, (demo.returncode, demo_output)
assert "Traceback" not in demo_output, demo_output
assert "docker compose -f docker-compose.demo.yaml up -d" in demo_output, demo_output

print(
    "wheel smoke OK: operator surface only, dev deps absent, init flow works, "
    "demo points at the compose stack"
)
