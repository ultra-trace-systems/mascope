"""
Tests for grace-period application of pending migration updates and the
operator snooze/confirm surface (`mascope prod update --confirm/--snooze`).
"""

import datetime
import importlib

import pytest
import typer

import mascope_cli.cmd.prod.auto_update as au


prod_main = importlib.import_module("mascope_cli.cmd.prod.main")

_HEAD = "abc123def456"
_WINDOW = (2, 5)


def _pending(first_seen, *, snooze_until=None, confirmed=False):
    return au.PendingUpdate(
        version="v1.4.0",
        alembic_head=_HEAD,
        first_seen_at=first_seen,
        snooze_until=snooze_until,
        confirmed=confirmed,
    )


def _in_window():
    return datetime.datetime(2026, 7, 20, 3, 0, 0)  # hour 3 -> inside (2,5)


def _out_of_window():
    return datetime.datetime(2026, 7, 20, 12, 0, 0)


# --- should_apply_migration ---


def test_grace_elapsed_in_window_applies():
    pending = _pending("2026-07-01T03:00:00")  # 19 days ago
    assert au.should_apply_migration(pending, _in_window(), 7, _WINDOW) is True


def test_grace_not_elapsed_waits():
    now = datetime.datetime(2026, 7, 20, 3, 0, 0)
    pending = _pending("2026-07-19T03:00:00")  # 1 day ago, grace 7
    assert au.should_apply_migration(pending, now, 7, _WINDOW) is False


def test_out_of_window_never_applies_even_if_grace_elapsed():
    pending = _pending("2026-07-01T03:00:00")
    assert au.should_apply_migration(pending, _out_of_window(), 7, _WINDOW) is False


def test_confirmed_applies_before_grace():
    pending = _pending("2026-07-19T03:00:00", confirmed=True)
    assert au.should_apply_migration(pending, _in_window(), 7, _WINDOW) is True


def test_snoozed_blocks_even_when_confirmed_and_grace_elapsed():
    pending = _pending(
        "2026-07-01T03:00:00", snooze_until="2026-08-01T00:00:00", confirmed=True
    )
    assert au.should_apply_migration(pending, _in_window(), 7, _WINDOW) is False


def test_snooze_expired_then_applies():
    pending = _pending("2026-07-01T03:00:00", snooze_until="2026-07-10T00:00:00")
    assert au.should_apply_migration(pending, _in_window(), 7, _WINDOW) is True


# --- snooze_pending / confirm_pending ---


def test_snooze_pending_sets_until_and_clears_confirm(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(au, "_now", lambda: datetime.datetime(2026, 7, 20, 0, 0, 0))
    au.record_pending(root, "v1.4.0", _HEAD)
    au.confirm_pending(root)

    updated = au.snooze_pending(root, 7)
    assert updated.snooze_until == "2026-07-27T00:00:00"
    assert updated.confirmed is False


def test_confirm_pending_sets_flag_and_clears_snooze(tmp_path):
    root = str(tmp_path)
    au.record_pending(root, "v1.4.0", _HEAD)
    au.snooze_pending(root, 3)

    updated = au.confirm_pending(root)
    assert updated.confirmed is True
    assert updated.snooze_until is None


def test_snooze_confirm_no_pending(tmp_path):
    root = str(tmp_path)
    assert au.snooze_pending(root, 7) is None
    assert au.confirm_pending(root) is None


# --- _manage_pending (CLI) ---


def test_update_confirm_no_pending(monkeypatch, cli_runner):
    monkeypatch.setattr(prod_main.auto_update, "confirm_pending", lambda p: None)
    result = cli_runner.invoke(prod_main.prod_app, ["update", "--confirm"])
    assert result.exit_code == 0


def test_update_confirm_applies(monkeypatch, cli_runner):
    monkeypatch.setattr(
        prod_main.auto_update,
        "confirm_pending",
        lambda p: _pending("t0", confirmed=True),
    )
    monkeypatch.setattr(prod_main.auto_update, "record_status", lambda *a, **k: None)
    result = cli_runner.invoke(prod_main.prod_app, ["update", "--confirm"])
    assert result.exit_code == 0


def test_update_snooze_applies(monkeypatch, cli_runner):
    monkeypatch.setattr(
        prod_main.auto_update,
        "snooze_pending",
        lambda p, days: _pending("t0", snooze_until="2026-08-01T00:00:00"),
    )
    monkeypatch.setattr(prod_main.auto_update, "record_status", lambda *a, **k: None)
    result = cli_runner.invoke(prod_main.prod_app, ["update", "--snooze", "5"])
    assert result.exit_code == 0


def test_update_snooze_rejects_non_positive(cli_runner):
    result = cli_runner.invoke(prod_main.prod_app, ["update", "--snooze", "0"])
    assert result.exit_code == 1


# --- _auto migration branch: grace-driven application ---


@pytest.fixture
def auto_env(monkeypatch, tmp_path):
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)
    monkeypatch.setattr(
        prod_main.auto_update, "latest_release_tag", lambda repo: "v1.4.0"
    )
    monkeypatch.setattr(
        prod_main.auto_update, "download_manifest", lambda *a, **k: None
    )
    monkeypatch.setattr(prod_main.auto_update, "record_status", lambda *a, **k: None)
    monkeypatch.setattr(
        prod_main.preflight,
        "build_plan",
        lambda **k: prod_main.preflight.UpdatePlan(
            target="v1.4.0",
            classification="migration-update",
            image_changed=True,
            migration_pending=True,
            current_revision="000000aaaaaa",
            target_revision=_HEAD,
        ),
    )
    # The updater's state lands under MASCOPE_PATH: keep it out of the shared
    # test home, where one test's recorded failure would hold the next's update.
    # Build the runtime singleton first: built from the empty temp dir, its
    # config would not load.
    prod_main.runtime.full_config  # noqa: B018
    monkeypatch.setenv("MASCOPE_PATH", str(tmp_path))
    monkeypatch.delenv("MASCOPE_UPDATE_WINDOW", raising=False)
    monkeypatch.delenv("MASCOPE_UPDATE_GRACE_DAYS", raising=False)
    return monkeypatch


def test_auto_migration_applies_when_should(auto_env):
    auto_env.setattr(prod_main.auto_update, "should_apply_migration", lambda *a: True)
    auto_env.setattr(prod_main.auto_update, "record_pending", lambda *a: _pending("t0"))
    auto_env.setattr(prod_main.auto_update, "clear_pending", lambda p: None)
    auto_env.setattr(prod_main.auto_update, "wait_healthy", lambda c: True)
    auto_env.setattr(prod_main, "check_data_dirs", lambda mode: None)
    applied = []
    auto_env.setattr(prod_main, "_run_compose", lambda args: applied.append(args))

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_OK
    assert ["up", "--detach"] in applied


def test_auto_migration_waits_when_not_should(auto_env):
    auto_env.setattr(prod_main.auto_update, "should_apply_migration", lambda *a: False)
    auto_env.setattr(prod_main.auto_update, "record_pending", lambda *a: _pending("t0"))

    def _fail(args):
        raise AssertionError("must not apply when should_apply is False")

    auto_env.setattr(prod_main, "_run_compose", _fail)

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_MIGRATION_PENDING


def test_auto_migration_bad_grace_env_errors(auto_env):
    auto_env.setenv("MASCOPE_UPDATE_GRACE_DAYS", "soon")
    auto_env.setattr(prod_main.auto_update, "record_pending", lambda *a: _pending("t0"))

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_ERROR
