"""
Tests for the unattended updater (`mascope prod update --auto`).

Pure helpers (window, pending-state, health polling, discovery seams) are
tested directly; the `_auto` orchestration is tested through main.py with the
seams stubbed, asserting the control flow and exit codes for each
classification and window state.
"""

import datetime
import importlib
import json
import os
import urllib.error

import pytest
import typer

import mascope_cli.cmd.prod.auto_update as au


prod_main = importlib.import_module("mascope_cli.cmd.prod.main")

_HEAD = "abc123def456"


# --- parse_window / in_window ---


@pytest.mark.parametrize(
    "spec, expected",
    [(None, None), ("", None), ("2-5", (2, 5)), ("22-3", (22, 3))],
)
def test_parse_window(spec, expected):
    assert au.parse_window(spec) == expected


@pytest.mark.parametrize("spec", ["nope", "2-99", "-1-5", "2:5"])
def test_parse_window_invalid(spec):
    with pytest.raises(ValueError):
        au.parse_window(spec)


def _at(hour):
    return datetime.datetime(2026, 7, 7, hour, 0, 0)


def test_in_window_none_always_true():
    assert au.in_window(_at(4), None) is True


def test_in_window_normal():
    assert au.in_window(_at(3), (2, 5)) is True
    assert au.in_window(_at(5), (2, 5)) is False  # end exclusive
    assert au.in_window(_at(1), (2, 5)) is False


def test_in_window_wraps_midnight():
    win = (22, 3)
    assert au.in_window(_at(23), win) is True
    assert au.in_window(_at(1), win) is True
    assert au.in_window(_at(12), win) is False


# --- pending state ---


def test_record_load_clear_pending(tmp_path):
    root = str(tmp_path)
    assert au.load_pending(root) is None

    au.record_pending(root, "v1.3.0", _HEAD)
    loaded = au.load_pending(root)
    assert loaded.version == "v1.3.0"
    assert loaded.alembic_head == _HEAD

    au.clear_pending(root)
    assert au.load_pending(root) is None


def test_save_pending_is_atomic(tmp_path, monkeypatch):
    """
    A reader must never catch this write half-done.

    `load_pending` reports an unparseable file as "nothing pending", so a
    partial write would make a doctor cron overlapping an update silently
    forget a pending migration.
    """
    root = str(tmp_path)
    au.record_pending(root, "v1.3.0", _HEAD)
    state_path = au.update_dir(root) / "state.json"
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        # What a concurrent reader would find just before the swap.
        seen.append(au.load_pending(root))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    au.record_pending(root, "v1.4.0", "def456abc123")
    monkeypatch.undo()

    assert [p.version for p in seen] == ["v1.3.0"]  # old file intact until swap
    assert au.load_pending(root).version == "v1.4.0"
    assert [p.name for p in state_path.parent.iterdir()] == ["state.json"]


def test_record_pending_preserves_first_seen(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(au, "_now", lambda: datetime.datetime(2026, 7, 1, 0, 0, 0))
    first = au.record_pending(root, "v1.3.0", _HEAD)

    # Same version seen again later must keep the original first_seen_at.
    monkeypatch.setattr(au, "_now", lambda: datetime.datetime(2026, 7, 5, 0, 0, 0))
    again = au.record_pending(root, "v1.3.0", _HEAD)
    assert again.first_seen_at == first.first_seen_at

    # A different version resets the clock.
    newer = au.record_pending(root, "v1.4.0", "def456abc123")
    assert newer.first_seen_at != first.first_seen_at


# --- health polling ---


def test_wait_healthy_becomes_healthy(monkeypatch):
    statuses = iter(["starting", "starting", "healthy"])
    monkeypatch.setattr(au, "health_status", lambda c: next(statuses))
    monkeypatch.setattr(au, "_sleep", lambda s: None)
    assert au.wait_healthy("backend", timeout=60, interval=1) is True


def test_wait_healthy_times_out(monkeypatch):
    monkeypatch.setattr(au, "health_status", lambda c: "starting")
    monkeypatch.setattr(au, "_sleep", lambda s: None)
    # _now advances past the deadline immediately on the second read.
    times = iter(
        [datetime.datetime(2026, 7, 7, 0, 0, 0)] * 2
        + [datetime.datetime(2026, 7, 7, 1, 0, 0)] * 3
    )
    monkeypatch.setattr(au, "_now", lambda: next(times))
    assert au.wait_healthy("backend", timeout=1, interval=1) is False


# --- discovery seams (tokenless HTTPS) ---


def test_latest_release_tag(monkeypatch):
    monkeypatch.setattr(au, "_http_get_json", lambda url, **k: {"tag_name": "v1.4.0"})
    assert au.latest_release_tag("ultra-trace-systems/mascope") == "v1.4.0"


def test_latest_release_tag_failure(monkeypatch):
    def _boom(url, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(au, "_http_get_json", _boom)
    assert au.latest_release_tag("ultra-trace-systems/mascope") is None


def test_download_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        au,
        "_http_get_json",
        lambda url, **k: {
            "assets": [
                {
                    "name": au.MANIFEST_FILENAME,
                    "browser_download_url": "https://example.test/m.json",
                }
            ]
        },
    )

    def _fake_download(url, dest, **k):
        dest.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(au, "_http_download", _fake_download)
    path = au.download_manifest("ultra-trace-systems/mascope", "v1.4.0", tmp_path)
    assert path == tmp_path / au.MANIFEST_FILENAME


def test_download_manifest_no_asset(monkeypatch, tmp_path):
    # A release predating the manifest has no matching asset -> None (fallback).
    monkeypatch.setattr(au, "_http_get_json", lambda url, **k: {"assets": []})
    assert (
        au.download_manifest("ultra-trace-systems/mascope", "v1.2.0", tmp_path) is None
    )


# --- _auto orchestration ---


def _plan(classification, target="v1.4.0"):
    return prod_main.preflight.UpdatePlan(
        target=target,
        classification=classification,
        image_changed=True,
        migration_pending=classification == "migration-update",
        current_revision="000000aaaaaa",
        target_revision=_HEAD,
    )


@pytest.fixture
def auto_env(monkeypatch):
    """Baseline stubs: stack up, latest release resolvable, no manifest."""
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)
    monkeypatch.setattr(
        prod_main.auto_update, "latest_release_tag", lambda repo: "v1.4.0"
    )
    monkeypatch.setattr(
        prod_main.auto_update, "download_manifest", lambda *a, **k: None
    )
    monkeypatch.setattr(prod_main.auto_update, "record_status", lambda *a, **k: None)
    # Disk guard and post-deploy prune shell out to docker; keep them inert here
    # (they have their own tests) so the orchestration tests stay hermetic.
    monkeypatch.setattr(prod_main.auto_update, "disk_precheck", lambda: None)
    monkeypatch.setattr(prod_main, "_prune_images", lambda: None)
    monkeypatch.delenv("MASCOPE_UPDATE_WINDOW", raising=False)
    return monkeypatch


def test_auto_up_to_date_clears_pending(auto_env):
    cleared = []
    auto_env.setattr(
        prod_main.auto_update, "clear_pending", lambda p: cleared.append(p)
    )
    auto_env.setattr(prod_main.preflight, "build_plan", lambda **k: _plan("up-to-date"))

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_OK
    assert cleared


def test_auto_migration_records_and_exits_pending(auto_env):
    recorded = {}

    def _record(path, version, head):
        recorded["v"] = version
        # first_seen_at must be "now" so the grace period can never have
        # elapsed, whatever the real date is when the test runs.
        return au.PendingUpdate(version, head, au._now().isoformat())

    auto_env.setattr(prod_main.auto_update, "record_pending", _record)
    auto_env.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("migration-update")
    )

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_MIGRATION_PENDING
    assert recorded["v"] == "v1.4.0"


def test_auto_fast_update_in_window_applies(auto_env):
    auto_env.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("fast-update")
    )
    auto_env.setattr(prod_main.auto_update, "in_window", lambda now, window: True)
    auto_env.setattr(prod_main.auto_update, "wait_healthy", lambda c: True)
    auto_env.setattr(prod_main.auto_update, "clear_pending", lambda p: None)
    auto_env.setattr(prod_main, "check_data_dirs", lambda mode: None)
    applied = []
    auto_env.setattr(prod_main, "_run_compose", lambda args: applied.append(args))

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_OK
    assert ["up", "--detach"] in applied  # the stack was restarted


def test_auto_fast_update_out_of_window_does_not_apply(auto_env):
    auto_env.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("fast-update")
    )
    auto_env.setattr(prod_main.auto_update, "in_window", lambda now, window: False)

    def _fail(args):
        raise AssertionError("must not apply outside the window")

    auto_env.setattr(prod_main, "_run_compose", _fail)

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_OK


def test_auto_fast_update_unhealthy_errors(auto_env):
    auto_env.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("fast-update")
    )
    auto_env.setattr(prod_main.auto_update, "in_window", lambda now, window: True)
    auto_env.setattr(prod_main.auto_update, "wait_healthy", lambda c: False)
    auto_env.setattr(prod_main, "check_data_dirs", lambda mode: None)
    auto_env.setattr(prod_main, "_run_compose", lambda args: None)

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_ERROR


def test_auto_discovery_failure_errors(auto_env):
    auto_env.setattr(prod_main.auto_update, "latest_release_tag", lambda repo: None)

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_ERROR


def test_auto_requires_running_postgres(auto_env):
    auto_env.setattr(prod_main, "is_container_running", lambda mode: False)

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_ERROR


def test_auto_bad_window_errors(auto_env):
    auto_env.setenv("MASCOPE_UPDATE_WINDOW", "not-a-window")

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_ERROR


def test_auto_low_disk_aborts_before_pull(auto_env):
    """A low disk stops --auto before build_plan pulls anything."""
    auto_env.setattr(
        prod_main.auto_update, "disk_precheck", lambda: "only 1.0 GiB free"
    )

    def _fail(**k):
        raise AssertionError("must not pull/classify when disk is low")

    auto_env.setattr(prod_main.preflight, "build_plan", _fail)

    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    assert e.value.exit_code == au.AUTO_ERROR


def test_auto_uses_manifest_head_when_available(auto_env, tmp_path):
    manifest = tmp_path / au.MANIFEST_FILENAME
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_version": "v1.4.0",
                "alembic_head": _HEAD,
                "generated_at": "2026-07-07T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    auto_env.setattr(
        prod_main.auto_update, "download_manifest", lambda *a, **k: manifest
    )

    seen = {}

    def _build_plan(**kwargs):
        seen["target_head"] = kwargs.get("target_head")
        return _plan("up-to-date")

    auto_env.setattr(prod_main.preflight, "build_plan", _build_plan)
    auto_env.setattr(prod_main.auto_update, "clear_pending", lambda p: None)

    with pytest.raises(typer.Exit):
        prod_main._auto(pull=True)
    assert seen["target_head"] == _HEAD
