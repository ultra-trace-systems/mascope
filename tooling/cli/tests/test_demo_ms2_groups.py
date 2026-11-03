"""
``mascope demo`` lists the MS2 groups of a restored snapshot.

The group inventory (``ms2_group``) is written when a file's MS2 store is, so
a demo snapshot taken before the table existed restores stores the inventory
does not list, and the demo shows no MS2 groups. Each start runs the
backfill that lists them, as ``tooling/demo-init.sh`` does for the demo stack.
"""

import importlib
import subprocess
import sys

import pytest

from mascope_cli.cmd.demo import _fetch, _seed
from mascope_cli.cmd.demo import main as demo_main


BACKFILL = "mascope_backend.db.scripts.backfill_ms2_store"


class _Ran:
    """A stand-in for ``subprocess.run`` that records what it was given."""

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, self.returncode)


def test_the_backfill_runs_in_run_mode_with_no_pause(monkeypatch):
    ran = _Ran()
    monkeypatch.setattr(subprocess, "run", ran)
    monkeypatch.setenv("MASCOPE_ENV", "demo")

    demo_main._list_ms2_groups()

    [(args, kwargs)] = ran.calls
    assert args == [sys.executable, "-m", BACKFILL, "--run"]
    assert kwargs["env"]["MS2_STORE_PAUSE"] == "0"
    # The demo env, which the demo callback pins, reaches the script
    assert kwargs["env"]["MASCOPE_ENV"] == "demo"


def test_a_failed_backfill_warns_and_the_demo_goes_on(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _Ran(returncode=1))
    warnings = []
    monkeypatch.setattr(
        demo_main.runtime.logger, "warning", lambda message: warnings.append(message)
    )

    demo_main._list_ms2_groups()

    assert len(warnings) == 1
    assert "exited with code 1" in warnings[0]


@pytest.fixture
def snapshot_run(monkeypatch):
    """``_run`` on the snapshot path with everything around the seed stubbed.

    :return: The order in which the seed steps ran.
    """
    # By module: the dev package's ``main`` attribute is its Typer callback
    dev_db, dev_docker, dev_main, dev_migrate, dev_redis, pg_utils = (
        importlib.import_module(name)
        for name in (
            "mascope_cli.cmd.dev.db",
            "mascope_cli.cmd.dev.docker",
            "mascope_cli.cmd.dev.main",
            "mascope_cli.cmd.dev.migrate",
            "mascope_cli.cmd.dev.redis",
            "mascope_cli.pg.utils",
        )
    )

    steps = []
    nothing = lambda *args, **kwargs: None  # noqa: E731
    monkeypatch.setattr(_fetch, "fetch", nothing)
    monkeypatch.setattr(demo_main, "_ensure_dev_secrets", nothing)
    monkeypatch.setattr(dev_docker, "check_and_start_docker", nothing)
    monkeypatch.setattr(pg_utils, "check_data_dirs", nothing)
    monkeypatch.setattr(dev_main, "_run_dev_compose", nothing)
    monkeypatch.setattr(dev_redis, "wait_for_redis", lambda **kwargs: True)
    monkeypatch.setattr(dev_db, "wait_for_server", lambda **kwargs: True)
    monkeypatch.setattr(dev_db, "create_database", lambda env: True)
    monkeypatch.setattr(
        _seed, "restore_snapshot", lambda *a, **k: steps.append("restore")
    )
    monkeypatch.setattr(_seed, "restore_filestore", nothing)
    monkeypatch.setattr(dev_migrate, "check_pending_migrations", lambda: True)
    monkeypatch.setattr(
        dev_migrate, "run_migrations", lambda: steps.append("migrate") or True
    )
    for name in ("_seed_credentials", "_seed_reference_demo", "_list_ms2_groups"):
        monkeypatch.setattr(demo_main, name, lambda name=name: steps.append(name))
    monkeypatch.setattr(demo_main, "_print_access", nothing)
    return steps


def test_a_restored_snapshot_has_its_ms2_groups_listed_after_migrating(snapshot_run):
    demo_main._run(rebuild=False, no_launch=True, version=None, force=False)

    # After the migration that makes the table, and once
    assert snapshot_run.index("_list_ms2_groups") > snapshot_run.index("migrate")
    assert snapshot_run.count("_list_ms2_groups") == 1
