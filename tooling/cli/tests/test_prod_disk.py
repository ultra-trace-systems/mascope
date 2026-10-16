"""
Tests for the disk-space guard and post-deploy image prune.

The guard (`auto_update.disk_precheck` + `main._abort_if_low_disk`) refuses to
pull update images when the docker image store is low on space, and
`main._prune_images` reclaims the superseded release's images after a healthy
deploy. Docker/subprocess and the real filesystem are stubbed so the suite stays
hermetic.
"""

import importlib
import subprocess
from pathlib import Path

import pytest
import typer

import mascope_cli.cmd.prod.auto_update as au


prod_main = importlib.import_module("mascope_cli.cmd.prod.main")


# --- free_gb / min_free_gb ---


def test_free_gb_real_path_is_positive(tmp_path):
    free = au.free_gb(tmp_path)
    assert free is not None and free > 0


def test_free_gb_walks_up_to_existing_ancestor(tmp_path):
    # A path that does not exist yet resolves to its nearest existing parent.
    missing = tmp_path / "does" / "not" / "exist"
    assert au.free_gb(missing) == au.free_gb(tmp_path)


def test_min_free_gb_default(monkeypatch):
    monkeypatch.delenv("MASCOPE_UPDATE_MIN_FREE_GB", raising=False)
    assert au.min_free_gb() == au.DEFAULT_MIN_FREE_GB


def test_min_free_gb_env_override(monkeypatch):
    monkeypatch.setenv("MASCOPE_UPDATE_MIN_FREE_GB", "20")
    assert au.min_free_gb() == 20.0


@pytest.mark.parametrize("raw", ["lots", "nan", "inf", "-5", ""])
def test_min_free_gb_invalid_falls_back(monkeypatch, raw):
    # float() accepts "nan" and "inf": nan would never compare below the
    # floor and inf would refuse every update.
    monkeypatch.setenv("MASCOPE_UPDATE_MIN_FREE_GB", raw)
    assert au.min_free_gb() == au.DEFAULT_MIN_FREE_GB


def test_env_float_accepts_zero_and_fractions(monkeypatch):
    monkeypatch.setenv("MASCOPE_TEST_FLOAT", "0")
    assert au.env_float("MASCOPE_TEST_FLOAT", 7.0) == 0.0
    monkeypatch.setenv("MASCOPE_TEST_FLOAT", "2.5")
    assert au.env_float("MASCOPE_TEST_FLOAT", 7.0) == 2.5


# --- disk_precheck ---


def test_disk_precheck_ok_when_above_threshold(monkeypatch):
    monkeypatch.setattr(au, "docker_root", lambda: Path("/var/lib/docker"))
    monkeypatch.setattr(au, "free_gb", lambda p: 50.0)
    monkeypatch.setattr(au, "min_free_gb", lambda: 5.0)
    assert au.disk_precheck() is None


def test_disk_precheck_message_when_below_threshold(monkeypatch):
    monkeypatch.setattr(au, "docker_root", lambda: Path("/var/lib/docker"))
    monkeypatch.setattr(au, "free_gb", lambda p: 1.0)
    monkeypatch.setattr(au, "min_free_gb", lambda: 5.0)
    message = au.disk_precheck()
    assert message is not None
    assert "1.0 GiB free" in message


def test_disk_precheck_unmeasurable_never_blocks(monkeypatch):
    monkeypatch.setattr(au, "docker_root", lambda: Path("/var/lib/docker"))
    monkeypatch.setattr(au, "free_gb", lambda p: None)
    assert au.disk_precheck() is None


def test_docker_root_falls_back_when_docker_unreachable(monkeypatch):
    monkeypatch.setattr(
        au, "_run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "err")
    )
    assert au.docker_root() == Path("/var/lib/docker")


# --- _abort_if_low_disk ---


def test_abort_auto_records_and_exits_error(monkeypatch):
    monkeypatch.setattr(prod_main.auto_update, "disk_precheck", lambda: "low disk")
    recorded = []
    monkeypatch.setattr(
        prod_main.auto_update, "record_status", lambda p, m: recorded.append(m)
    )

    with pytest.raises(typer.Exit) as e:
        prod_main._abort_if_low_disk(auto=True)
    assert e.value.exit_code == prod_main.auto_update.AUTO_ERROR
    assert recorded and "low disk" in recorded[0]


def test_abort_interactive_exits_one(monkeypatch):
    monkeypatch.setattr(prod_main.auto_update, "disk_precheck", lambda: "low disk")

    with pytest.raises(typer.Exit) as e:
        prod_main._abort_if_low_disk(auto=False)
    assert e.value.exit_code == 1


def test_abort_noop_when_enough_disk(monkeypatch):
    monkeypatch.setattr(prod_main.auto_update, "disk_precheck", lambda: None)
    # Returns normally (no raise) when there is room.
    prod_main._abort_if_low_disk(auto=True)
    prod_main._abort_if_low_disk(auto=False)


# --- _prune_images ---


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        ["docker"], returncode, stdout=stdout, stderr=stderr
    )


def test_prune_images_success(monkeypatch):
    captured = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _completed(stdout="deleted: sha256:abc\nTotal reclaimed space: 1.2GB\n")

    monkeypatch.setattr(prod_main.subprocess, "run", _run)
    prod_main._prune_images()
    assert captured["cmd"] == ["docker", "image", "prune", "-a", "-f"]


def test_prune_images_nonzero_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        prod_main.subprocess, "run", lambda cmd, **k: _completed(1, stderr="boom")
    )
    # Must not raise - a prune failure never fails a healthy update.
    prod_main._prune_images()


def test_prune_images_subprocess_error_is_non_fatal(monkeypatch):
    def _boom(cmd, **kwargs):
        raise OSError("docker missing")

    monkeypatch.setattr(prod_main.subprocess, "run", _boom)
    prod_main._prune_images()


# --- manual `update` integration: guard before pull, prune after deploy ---


def test_manual_update_aborts_on_low_disk(monkeypatch, cli_runner):
    monkeypatch.setattr(prod_main.auto_update, "disk_precheck", lambda: "low disk")
    monkeypatch.setattr(prod_main, "check_data_dirs", lambda mode: None)

    def _fail(*a, **k):
        raise AssertionError("must not pull when disk is low")

    monkeypatch.setattr(prod_main, "_run_compose", _fail)

    result = cli_runner.invoke(prod_main.prod_app, ["update"])
    assert result.exit_code == 1


def test_manual_update_prunes_after_deploy(monkeypatch, cli_runner):
    monkeypatch.setattr(prod_main.auto_update, "disk_precheck", lambda: None)
    monkeypatch.setattr(prod_main, "check_data_dirs", lambda mode: None)
    monkeypatch.setattr(prod_main, "_run_compose", lambda args: None)
    pruned = []
    monkeypatch.setattr(prod_main, "_prune_images", lambda: pruned.append(True))

    result = cli_runner.invoke(prod_main.prod_app, ["update"])
    assert result.exit_code == 0
    assert pruned  # the superseded images were pruned
