"""
Guards for the systemd units and the installer that templates them.

A unit is only as good as its installation: `tooling/ubuntu.sh` substitutes the
`@@TOKEN@@` placeholders, so a placeholder without a matching sed expression
reaches the server verbatim and systemd silently ignores the directive.

`WorkingDirectory` is asserted by name because losing it is not visible on the
server: the units run the CLI, which reads the deployment checkout to resolve
which release is deployed, and systemd's default working directory is `/`.
"""

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEMD_DIR = REPO_ROOT / "tooling" / "systemd"
INSTALLER = REPO_ROOT / "tooling" / "ubuntu.sh"

# The units that invoke the `mascope` CLI, which resolves the deployed release
# from the checkout it runs in.
CLI_UNITS = ["mascope.service", "mascope-update.service"]


pytestmark = pytest.mark.skipif(
    not SYSTEMD_DIR.is_dir(), reason="repo-root tooling/systemd not available"
)


def _units() -> list[Path]:
    return sorted(SYSTEMD_DIR.glob("*.service"))


def test_every_placeholder_is_substituted_by_the_installer():
    installer = INSTALLER.read_text(encoding="utf-8")
    for unit in _units():
        for token in set(re.findall(r"@@\w+@@", unit.read_text(encoding="utf-8"))):
            assert f"s|{token}|" in installer, (
                f"{unit.name} uses {token} but tooling/ubuntu.sh never "
                "substitutes it - it would be installed verbatim"
            )


def test_installing_does_not_clear_the_runtime_state():
    """
    Re-running `ubuntu.sh install` is the documented way to pick up a change to
    the unit templates, so it must leave `.runtime/state.json` alone: that file
    carries the active environment, and every deployed server runs a named env
    rather than `default`. Clearing it points the boot service at a different
    database, which only shows up on the next restart.
    """
    installer = INSTALLER.read_text(encoding="utf-8")
    body = installer[installer.index("function main()") :]
    main_body = body[: body.index("\n}")]

    for block in re.findall(
        r"if \[ \"\$\(action_in ([^)]*)\)\" \][^\n]*\n(.*?)\n    fi",
        main_body,
        re.DOTALL,
    ):
        actions, statements = block
        if "clear_state" in statements:
            assert "'uninstall'" in actions, (
                "clear_state runs in a block reachable by a bare `install`; it "
                "deletes .runtime/state.json and would reset the server's "
                "active environment to `default`"
            )


@pytest.mark.parametrize("name", CLI_UNITS)
def test_cli_units_run_from_the_deployment_checkout(name):
    unit = (SYSTEMD_DIR / name).read_text(encoding="utf-8")
    assert "WorkingDirectory=@@MASCOPE_PATH@@" in unit, (
        f"{name} must run from the checkout; without it systemd starts the "
        "unit in '/', where the CLI cannot resolve the checked-out release "
        "and falls back to deploying `latest`"
    )
