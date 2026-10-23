"""
Tests for holding a release whose unattended apply failed its health check.

Without the hold, `mascope prod update --auto` applies the same release again
at every window and undoes the operator's manual rollback (#2427). These run
the real state file in a temporary MASCOPE_PATH, so a failure recorded by one
`_auto` call is what the next one reads.
"""

import datetime
import importlib

import pytest
import typer

import mascope_cli.cmd.prod.auto_update as au


prod_main = importlib.import_module("mascope_cli.cmd.prod.main")
# The package re-exports a `doctor` command function under the module's name.
doctor = importlib.import_module("mascope_cli.cmd.prod.doctor")

_HEAD = "abc123def456"
_NOW = datetime.datetime(2026, 7, 20, 3, 0, 0)


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
def root(tmp_path):
    return str(tmp_path)


@pytest.fixture
def auto_env(monkeypatch, root):
    """`_auto` against a temporary state dir, with docker and GitHub stubbed."""
    # Build the runtime singleton first: built from the empty temp dir, its
    # config would not load.
    prod_main.runtime.full_config  # noqa: B018
    monkeypatch.setenv("MASCOPE_PATH", root)
    monkeypatch.delenv("MASCOPE_UPDATE_WINDOW", raising=False)
    monkeypatch.delenv("MASCOPE_UPDATE_GRACE_DAYS", raising=False)
    monkeypatch.setattr(au, "_now", lambda: _NOW)
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)
    monkeypatch.setattr(prod_main, "_deploy_version", lambda: "v1.3.0")
    monkeypatch.setattr(
        prod_main.auto_update, "latest_release_tag", lambda repo: "v1.4.0"
    )
    monkeypatch.setattr(
        prod_main.auto_update, "download_manifest", lambda *a, **k: None
    )
    monkeypatch.setattr(prod_main.auto_update, "disk_precheck", lambda: None)
    monkeypatch.setattr(prod_main, "check_data_dirs", lambda mode: None)
    monkeypatch.setattr(prod_main, "_prune_images", lambda: None)
    monkeypatch.setattr(prod_main, "_align_checkout", lambda *a: None)
    monkeypatch.setattr(prod_main, "_warn_if_cli_stale", lambda **k: None)
    return monkeypatch


def _run_auto(env, *, classification, healthy=True, failing=None):
    """
    One `_auto` call; returns (exit code, the compose calls it made).

    :param failing: A compose subcommand ("pull", "up") that exits 1, as
        `_run_compose` does when compose fails.
    """
    env.setattr(prod_main.preflight, "build_plan", lambda **k: _plan(classification))
    env.setattr(prod_main.auto_update, "wait_healthy", lambda c: healthy)
    compose = []

    def run_compose(args):
        compose.append(args)
        if args[0] == failing:
            raise typer.Exit(1)

    env.setattr(prod_main, "_run_compose", run_compose)
    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    return e.value.exit_code, compose


def _confirm():
    with pytest.raises(typer.Exit) as e:
        prod_main._manage_pending(confirm=True, snooze=None)
    assert e.value.exit_code == 0


def _status_log(root):
    return (au.update_dir(root) / "status.log").read_text(encoding="utf-8")


# --- state helpers ---


def test_record_failure_marks_the_pending_record(root, monkeypatch):
    monkeypatch.setattr(au, "_now", lambda: _NOW)
    au.save_pending(
        root,
        au.PendingUpdate("v1.4.0", _HEAD, "2026-07-01T00:00:00", confirmed=True),
    )

    au.record_failure(root, "v1.4.0", _HEAD, "migration")

    pending = au.load_pending(root)
    assert pending.failed_at == "2026-07-20T03:00:00"
    assert pending.first_seen_at == "2026-07-01T00:00:00"  # grace clock kept
    assert pending.confirmed is False  # the confirmation is spent
    assert pending.kind == "migration"


def test_record_failure_of_a_fast_update_makes_its_own_record(root, monkeypatch):
    monkeypatch.setattr(au, "_now", lambda: _NOW)
    au.save_pending(root, au.PendingUpdate("v1.3.0", _HEAD, "2026-07-01T00:00:00"))

    au.record_failure(root, "v1.4.0", _HEAD, "fast")

    pending = au.load_pending(root)
    assert (pending.version, pending.kind) == ("v1.4.0", "fast")
    assert pending.failed_at == "2026-07-20T03:00:00"
    assert pending.alembic_head == _HEAD


def test_record_pending_keeps_a_failure(root):
    au.record_failure(root, "v1.4.0", _HEAD, "migration")
    assert au.record_pending(root, "v1.4.0", _HEAD).failed_at is not None


def test_should_apply_migration_holds_a_failed_release():
    pending = au.PendingUpdate(
        "v1.4.0", _HEAD, "2026-07-01T00:00:00", confirmed=True, failed_at="t"
    )
    assert au.should_apply_migration(pending, _NOW, 7, None) is False


def test_confirm_clears_a_failure(root):
    au.record_failure(root, "v1.4.0", _HEAD, "fast")

    confirmed = au.confirm_pending(root)

    assert confirmed.failed_at is None and confirmed.confirmed is True
    assert au.load_pending(root).failed_at is None


def test_state_from_before_the_fields_loads(root):
    au.update_dir(root).mkdir(parents=True)
    (au.update_dir(root) / "state.json").write_text(
        '{"pending": {"version": "v1.4.0", "alembic_head": "abc", '
        '"first_seen_at": "2026-07-01T00:00:00", "snooze_until": null, '
        '"confirmed": false}}',
        encoding="utf-8",
    )
    pending = au.load_pending(root)
    assert pending.failed_at is None and pending.kind == "migration"


# --- _auto: the failing case of #2427 ---


def test_failed_migration_update_is_not_applied_again(auto_env, root):
    au.save_pending(
        root,
        au.PendingUpdate("v1.4.0", _HEAD, "2026-07-01T00:00:00", confirmed=True),
    )

    code, compose = _run_auto(
        auto_env, classification="migration-update", healthy=False
    )
    assert code == au.AUTO_ERROR
    assert ["up", "--detach"] in compose
    assert au.load_pending(root).failed_at == "2026-07-20T03:00:00"

    # The operator rolls back; the next window classifies the same release.
    code, compose = _run_auto(auto_env, classification="migration-update")
    assert code == au.AUTO_ERROR
    assert compose == []
    assert "failed to apply at 2026-07-20T03:00:00" in _status_log(root)

    # A confirm retries it once.
    _confirm()
    code, compose = _run_auto(auto_env, classification="migration-update")
    assert code == au.AUTO_OK
    assert ["up", "--detach"] in compose
    assert au.load_pending(root) is None


def test_failed_fast_update_is_not_applied_again(auto_env, root):
    code, _ = _run_auto(auto_env, classification="fast-update", healthy=False)
    assert code == au.AUTO_ERROR
    pending = au.load_pending(root)
    assert (pending.version, pending.kind) == ("v1.4.0", "fast")
    assert pending.alembic_head == _HEAD

    code, compose = _run_auto(auto_env, classification="fast-update")
    assert code == au.AUTO_ERROR
    assert compose == []

    _confirm()
    code, compose = _run_auto(auto_env, classification="fast-update")
    assert code == au.AUTO_OK
    assert ["up", "--detach"] in compose


@pytest.mark.parametrize(
    "classification, kind",
    [("migration-update", "migration"), ("fast-update", "fast")],
)
def test_a_failed_compose_up_is_held(auto_env, root, classification, kind):
    # The prod compose file starts the backend only after db_init and the rest
    # only after a healthy backend, so a failed migration or an unhealthy
    # backend fails `up` itself, before the health check.
    if classification == "migration-update":
        au.save_pending(
            root,
            au.PendingUpdate("v1.4.0", _HEAD, "2026-07-01T00:00:00", confirmed=True),
        )
    code, compose = _run_auto(auto_env, classification=classification, failing="up")
    assert code == au.AUTO_ERROR
    assert compose == [["pull"], ["up", "--detach"]]
    pending = au.load_pending(root)
    assert (pending.version, pending.kind) == ("v1.4.0", kind)
    assert pending.failed_at == "2026-07-20T03:00:00"
    assert "did not come up healthy" in _status_log(root)

    # The operator rolls back; the next night must not apply it again.
    code, compose = _run_auto(auto_env, classification=classification)
    assert code == au.AUTO_ERROR
    assert compose == []


def test_a_failed_pull_is_not_held(auto_env, root):
    # A pull that fails leaves the stack as it was: the next night retries it.
    code, compose = _run_auto(auto_env, classification="fast-update", failing="pull")
    assert code == 1
    assert compose == [["pull"]]
    assert au.load_pending(root) is None

    code, compose = _run_auto(auto_env, classification="fast-update")
    assert code == au.AUTO_OK
    assert ["up", "--detach"] in compose


@pytest.mark.parametrize(
    "classification, kind, quiet",
    [
        ("migration-update", "migration", au.AUTO_MIGRATION_PENDING),
        ("fast-update", "fast", au.AUTO_OK),
    ],
)
def test_failure_survives_a_night_on_the_failed_release(
    auto_env, root, classification, kind, quiet
):
    if classification == "migration-update":
        au.save_pending(
            root,
            au.PendingUpdate("v1.4.0", _HEAD, "2026-07-01T00:00:00", confirmed=True),
        )
    code, _ = _run_auto(auto_env, classification=classification, healthy=False)
    assert code == au.AUTO_ERROR

    # The failed apply left the stack on v1.4.0, so the next night sees it up
    # to date. The hold must outlive that night.
    code, compose = _run_auto(auto_env, classification="up-to-date")
    assert code == au.AUTO_ERROR
    assert compose == []
    assert au.load_pending(root).failed_at == "2026-07-20T03:00:00"
    assert "held while this deployment still runs it" in _status_log(root)

    # Snoozed, it stays held but quiet, with the code of its kind.
    au.snooze_pending(root, 3)
    code, _ = _run_auto(auto_env, classification="up-to-date")
    assert code == quiet
    assert au.load_pending(root).failed_at == "2026-07-20T03:00:00"

    # The operator rolls back a day later: the release is still not applied.
    auto_env.setattr(au, "_now", lambda: _NOW + datetime.timedelta(days=4))
    code, compose = _run_auto(auto_env, classification=classification)
    assert code == au.AUTO_ERROR
    assert compose == []
    assert au.load_pending(root).kind == kind


def test_up_to_date_after_a_confirmed_retry_clears_the_record(auto_env, root):
    au.record_failure(root, "v1.4.0", _HEAD, "fast")
    _confirm()
    code, _ = _run_auto(auto_env, classification="up-to-date")
    assert code == au.AUTO_OK
    assert au.load_pending(root) is None


def test_snooze_message_names_the_kind(auto_env, root):
    au.record_failure(root, "v1.4.0", _HEAD, "fast")
    with pytest.raises(typer.Exit):
        prod_main._manage_pending(confirm=False, snooze=3)
    assert "Snoozed fast update v1.4.0 until" in _status_log(root)


@pytest.mark.parametrize(
    "classification, quiet",
    [("migration-update", au.AUTO_MIGRATION_PENDING), ("fast-update", au.AUTO_OK)],
)
def test_snoozed_failure_stays_held_but_quiet(auto_env, root, classification, quiet):
    au.record_failure(root, "v1.4.0", _HEAD, "fast")
    au.snooze_pending(root, 3)

    code, compose = _run_auto(auto_env, classification=classification)
    assert code == quiet
    assert compose == []

    # Once the snooze runs out the release is still held, and loud again.
    auto_env.setattr(au, "_now", lambda: _NOW + datetime.timedelta(days=4))
    code, compose = _run_auto(auto_env, classification=classification)
    assert code == au.AUTO_ERROR
    assert compose == []


@pytest.mark.parametrize("classification", ["migration-update", "fast-update"])
def test_a_newer_release_than_the_failed_one_is_tried(auto_env, root, classification):
    au.save_pending(
        root,
        au.PendingUpdate(
            "v1.3.5", _HEAD, "2026-07-01T00:00:00", failed_at="2026-07-10T03:00:00"
        ),
    )
    # A newer migration update is a new record, so it waits out its own grace
    # period; confirm it the way an operator would.
    if classification == "migration-update":
        au.record_pending(root, "v1.4.0", _HEAD)
        _confirm()

    code, compose = _run_auto(auto_env, classification=classification)
    assert code == au.AUTO_OK
    assert ["up", "--detach"] in compose


def test_unrecordable_failure_still_errors(auto_env, root):
    def _broken(*a):
        raise OSError("read-only filesystem")

    auto_env.setattr(prod_main.auto_update, "record_failure", _broken)
    code, _ = _run_auto(auto_env, classification="fast-update", healthy=False)
    assert code == au.AUTO_ERROR


def test_confirm_says_it_retries_a_failed_release(auto_env, root):
    au.record_failure(root, "v1.4.0", _HEAD, "fast")
    _confirm()
    assert "Confirmed a retry of fast update v1.4.0" in _status_log(root)


# --- doctor ---


def test_doctor_reports_a_failed_update(root):
    au.save_pending(
        root,
        au.PendingUpdate(
            "v1.4.0", _HEAD, "2026-07-01T00:00:00", failed_at="2026-07-20T03:00:00"
        ),
    )
    u = doctor.update_status(root)
    assert (u.pending_failed_at, u.pending_kind) == ("2026-07-20T03:00:00", "migration")


def test_doctor_text_says_failed_and_how_to_retry():
    report = doctor.Report(
        containers=[],
        versions=doctor.VersionStatus(expected=None, running={}),
        disks=[],
        updates=doctor.UpdateStatus(
            "v1.4.0",
            "2026-07-01T00:00:00",
            None,
            pending_failed_at="2026-07-20T03:00:00",
            pending_kind="fast",
        ),
        backups=doctor.BackupStatus(0, None),
        images=doctor.ImageFootprint(None, None, None),
    )
    text = doctor.format_text(report)
    assert (
        "fast update v1.4.0 failed to apply at 2026-07-20T03:00:00; held - "
        "confirm to retry" in text
    )
    assert "pending (first seen" not in text


def test_doctor_text_names_a_confirmed_fast_update():
    report = doctor.Report(
        containers=[],
        versions=doctor.VersionStatus(expected=None, running={}),
        disks=[],
        updates=doctor.UpdateStatus(
            "v1.4.0", "2026-07-01T00:00:00", None, pending_kind="fast"
        ),
        backups=doctor.BackupStatus(0, None),
        images=doctor.ImageFootprint(None, None, None),
    )
    assert "fast update v1.4.0 pending (first seen" in doctor.format_text(report)
