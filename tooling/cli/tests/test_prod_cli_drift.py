"""
Tests for the installed-CLI staleness check (`mascope_cli.cmd.prod.cli_drift`)
and its wiring into `prod update`.

The pure detection (tree hashing, applicability, drift verdict) is tested
directly against constructed package trees; the `update` wiring is tested
through main.py with `cli_drift.detect` stubbed, asserting that a stale install
warns (and, under --auto, is recorded) while never failing the update.
"""

import importlib
from pathlib import Path

import mascope_cli.cmd.prod.cli_drift as cli_drift


prod_main = importlib.import_module("mascope_cli.cmd.prod.main")


def _make_pkg(root: Path, files: dict[str, str]) -> Path:
    """Materialize a package tree of {relative-path: text} under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


_PKG = {
    "__init__.py": "",
    "main.py": "def app():\n    pass\n",
    "cmd/prod/main.py": "def update():\n    pass\n",
}


# --- _tree_hash ---


def test_tree_hash_empty_is_none(tmp_path):
    (tmp_path / "empty").mkdir()
    assert cli_drift._tree_hash(tmp_path / "empty") is None


def test_tree_hash_identical_trees_match(tmp_path):
    a = _make_pkg(tmp_path / "a", _PKG)
    b = _make_pkg(tmp_path / "b", _PKG)
    assert cli_drift._tree_hash(a) == cli_drift._tree_hash(b)


def test_tree_hash_content_change_differs(tmp_path):
    a = _make_pkg(tmp_path / "a", _PKG)
    changed = dict(_PKG, **{"main.py": "def app():\n    return 1\n"})
    b = _make_pkg(tmp_path / "b", changed)
    assert cli_drift._tree_hash(a) != cli_drift._tree_hash(b)


def test_tree_hash_added_module_differs(tmp_path):
    """A new command module (the doctor-style drift) changes the digest."""
    a = _make_pkg(tmp_path / "a", _PKG)
    with_new = dict(_PKG, **{"cmd/prod/doctor.py": "def doctor():\n    pass\n"})
    b = _make_pkg(tmp_path / "b", with_new)
    assert cli_drift._tree_hash(a) != cli_drift._tree_hash(b)


def test_tree_hash_ignores_pycache(tmp_path):
    """Byte-compiled caches never match *.py, so they do not perturb the hash."""
    a = _make_pkg(tmp_path / "a", _PKG)
    before = cli_drift._tree_hash(a)
    (a / "__pycache__").mkdir()
    (a / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00\x01")
    assert cli_drift._tree_hash(a) == before


# --- reinstall_command ---


def test_reinstall_command_mirrors_ubuntu_sh():
    cmd = cli_drift.reinstall_command("/opt/mascope")
    assert 'cd "/opt/mascope"' in cmd
    assert "uv tool install --force --reinstall --python 3.12 ." in cmd
    assert "--with-executables-from mascope-cli" in cmd
    assert 'CFLAGS="-std=c17"' in cmd


# --- detect ---


def _checkout(mascope_path: Path, files: dict[str, str]) -> Path:
    return _make_pkg(mascope_path.joinpath(*cli_drift._CLI_SRC_RELPATH), files)


def test_detect_no_checkout_returns_none(tmp_path):
    # A runtime home without a source checkout: drift is impossible.
    assert cli_drift.detect(str(tmp_path)) is None


def test_detect_running_from_checkout_returns_none(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path, _PKG)
    monkeypatch.setattr(cli_drift, "installed_package_dir", lambda: checkout)
    assert cli_drift.detect(str(tmp_path)) is None


def test_detect_in_sync_not_drifted(tmp_path, monkeypatch):
    _checkout(tmp_path, _PKG)
    installed = _make_pkg(tmp_path / "site-packages" / "mascope_cli", _PKG)
    monkeypatch.setattr(cli_drift, "installed_package_dir", lambda: installed)

    result = cli_drift.detect(str(tmp_path))
    assert result is not None
    assert result.drifted is False


def test_detect_stale_install_drifts(tmp_path, monkeypatch):
    # Checkout has a new command the installed tree lacks (the prod incident).
    _checkout(
        tmp_path, dict(_PKG, **{"cmd/prod/doctor.py": "def doctor():\n    pass\n"})
    )
    installed = _make_pkg(tmp_path / "site-packages" / "mascope_cli", _PKG)
    monkeypatch.setattr(cli_drift, "installed_package_dir", lambda: installed)

    result = cli_drift.detect(str(tmp_path))
    assert result is not None
    assert result.drifted is True
    assert result.reinstall is not None
    assert "uv tool install" in result.reinstall
    assert str(tmp_path) in result.reason


# --- _warn_if_cli_stale wiring ---


def _drift(drifted: bool) -> cli_drift.DriftResult:
    return cli_drift.DriftResult(
        drifted=drifted,
        reason="test",
        reinstall="cd . && uv tool install ..." if drifted else None,
    )


def test_warn_records_status_only_under_auto(monkeypatch):
    recorded = []
    monkeypatch.setattr(prod_main.cli_drift, "detect", lambda p: _drift(True))
    monkeypatch.setattr(
        prod_main.auto_update, "record_status", lambda p, m: recorded.append(m)
    )

    prod_main._warn_if_cli_stale(auto=False)
    assert recorded == []  # interactive: warn to the log, no status file write

    prod_main._warn_if_cli_stale(auto=True)
    assert len(recorded) == 1
    assert "reinstall" in recorded[0].lower()


def test_warn_noop_when_not_drifted(monkeypatch):
    recorded = []
    monkeypatch.setattr(prod_main.cli_drift, "detect", lambda p: _drift(False))
    monkeypatch.setattr(
        prod_main.auto_update, "record_status", lambda p, m: recorded.append(m)
    )

    prod_main._warn_if_cli_stale(auto=True)
    assert recorded == []


def test_warn_noop_when_not_applicable(monkeypatch):
    recorded = []
    monkeypatch.setattr(prod_main.cli_drift, "detect", lambda p: None)
    monkeypatch.setattr(
        prod_main.auto_update, "record_status", lambda p, m: recorded.append(m)
    )

    prod_main._warn_if_cli_stale(auto=True)
    assert recorded == []


def test_warn_swallows_detection_error(monkeypatch):
    """A failure while checking must never fail an otherwise-healthy update."""

    def _boom(_path):
        raise OSError("permission denied")

    monkeypatch.setattr(prod_main.cli_drift, "detect", _boom)
    # Must return without raising.
    prod_main._warn_if_cli_stale(auto=False)


# --- update command wiring ---


def test_update_invokes_stale_check(monkeypatch, cli_runner):
    """The interactive update runs the staleness check as its last step."""
    calls = []
    monkeypatch.setattr(prod_main, "check_data_dirs", lambda mode: None)
    monkeypatch.setattr(prod_main, "_abort_if_low_disk", lambda *, auto: None)
    monkeypatch.setattr(prod_main, "_run_compose", lambda *a, **k: None)
    monkeypatch.setattr(prod_main, "_prune_images", lambda: None)
    monkeypatch.setattr(
        prod_main, "_warn_if_cli_stale", lambda *, auto: calls.append(auto)
    )
    # The test home is no checkout, so it is a wheel install's: the release
    # has to be the CLI's own.
    monkeypatch.setattr(prod_main.mascope_release, "RELEASE", "v1.2.0")

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", "v1.2.0"])
    assert result.exit_code == 0
    assert calls == [False]
