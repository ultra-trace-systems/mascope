"""
Drift guard for the bundled package data.

`mascope init` materializes config/compose files from `mascope_cli/data/`,
which mirror the canonical copies at the repo root (used directly by the
monorepo workflows). The two sets must stay identical; when this test fails,
re-copy the repo-root file into `tooling/cli/src/mascope_cli/data/`.
"""

from pathlib import Path

import pytest

import mascope_cli
from mascope_cli.cmd.init import CONFIG_FILES
from mascope_cli.cmd.prod import units


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(mascope_cli.__file__).resolve().parent / "data"


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_bundled_file_matches_repo_root(name):
    bundled = (DATA_DIR / name).read_bytes()
    canonical = (REPO_ROOT / name).read_bytes()

    assert bundled == canonical, (
        f"{name} drifted from the repo root — update the bundled copy: "
        f"cp {REPO_ROOT / name} {DATA_DIR / name}"
    )


@pytest.mark.parametrize("name", units.SOURCE_FILES)
def test_bundled_systemd_file_matches_the_checkout(name):
    """
    The units a wheel install gets are the ones `tooling/ubuntu.sh` installs
    from a checkout, where `mascope prod units install` reads the checkout's.
    """
    bundled = (units.bundled_dir() / name).read_bytes()
    canonical = REPO_ROOT / units._CHECKOUT_PATHS[name]
    assert bundled == canonical.read_bytes(), (
        f"{name} drifted from the checkout - update the bundled copy: "
        f"cp {canonical} {units.bundled_dir() / name}"
    )


def test_every_bundled_systemd_file_has_a_checkout_copy():
    assert {p.name for p in units.bundled_dir().iterdir()} == set(units.SOURCE_FILES)
