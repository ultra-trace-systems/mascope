"""
Tests for the release manifest: generation, loading/validation, the
`mascope prod manifest` command, and its consumption by `update --check`.
"""

import importlib
import json
from importlib import metadata
from pathlib import Path

import pytest

import mascope_cli.cmd.prod.release_manifest as rm


prod_main = importlib.import_module("mascope_cli.cmd.prod.main")


_HEAD = "abc123def456"


def _write_alembic_tree(root: Path, revision: str) -> Path:
    """Build a minimal Alembic layout (ini + one revision) under ``root``."""
    backend = root / "server" / "backend"
    versions = backend / "migrations" / "versions"
    versions.mkdir(parents=True)
    # Absolute script_location so resolution does not depend on the cwd.
    (backend / "alembic.ini").write_text(
        f"[alembic]\nscript_location = {backend / 'migrations'}\n", encoding="utf-8"
    )
    (versions / "0001_init.py").write_text(
        f'revision = "{revision}"\n'
        "down_revision = None\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "def upgrade():\n    pass\n"
        "def downgrade():\n    pass\n",
        encoding="utf-8",
    )
    return backend


# --- read_alembic_head / build_manifest ---


def test_read_alembic_head(tmp_path):
    backend = _write_alembic_tree(tmp_path, _HEAD)
    assert rm.read_alembic_head(backend) == _HEAD


def test_read_alembic_head_missing_ini(tmp_path):
    with pytest.raises(rm.ManifestError):
        rm.read_alembic_head(tmp_path / "nope")


def test_build_manifest_shape(monkeypatch):
    monkeypatch.setattr(rm, "read_alembic_head", lambda backend_path: _HEAD)
    data = rm.build_manifest("v1.3.0", Path("unused"))
    assert data["schema_version"] == rm.SCHEMA_VERSION
    assert data["app_version"] == "v1.3.0"
    assert data["alembic_head"] == _HEAD
    assert data["generated_at"].endswith("Z")


def test_the_manifest_of_the_clis_own_release_names_the_cli(monkeypatch):
    monkeypatch.setattr(rm, "read_alembic_head", lambda backend_path: _HEAD)
    monkeypatch.setattr(rm.release, "RELEASE", "v1.3.0")

    data = rm.build_manifest("v1.3.0", Path("unused"))

    assert data["cli_version"] == metadata.version("mascope_cli")


@pytest.mark.parametrize("version", ["v1.3.1", "v1.4.0-rc.1", "latest"])
def test_the_manifest_of_another_release_names_no_cli(monkeypatch, version):
    """The checkout's CLI deploys only its own release: it is not this one's."""
    monkeypatch.setattr(rm, "read_alembic_head", lambda backend_path: _HEAD)
    monkeypatch.setattr(rm.release, "RELEASE", "v1.3.0")

    assert rm.build_manifest(version, Path("unused"))["cli_version"] is None


def test_the_release_workflow_manifest_names_this_cli(
    monkeypatch, cli_runner, tmp_path
):
    """`prod manifest --version <tag> --output ...`, as build-release-images runs it.

    The workflow passes the release tag the checkout was built from; at a
    release, the CLI in that checkout is the one cut with it.
    """
    monkeypatch.setattr(rm, "read_alembic_head", lambda backend_path: _HEAD)
    out = tmp_path / rm.MANIFEST_FILENAME

    result = cli_runner.invoke(
        prod_main.prod_app,
        ["manifest", "--version", rm.release.RELEASE, "--output", str(out)],
    )

    assert result.exit_code == 0, result.output
    data = rm.load_manifest(out)
    assert data["app_version"] == rm.release.RELEASE
    assert data["cli_version"] == metadata.version("mascope_cli")


def test_a_manifest_naming_no_cli_warns(monkeypatch, cli_runner):
    monkeypatch.setattr(rm, "read_alembic_head", lambda backend_path: _HEAD)
    warnings = []
    monkeypatch.setattr(prod_main.runtime.logger, "warning", warnings.append)

    result = cli_runner.invoke(prod_main.prod_app, ["manifest", "--version", "v0.0.1"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["cli_version"] is None
    assert any("names no CLI" in w for w in warnings), warnings


# --- load_manifest validation ---


def _valid_manifest() -> dict:
    return {
        "schema_version": rm.SCHEMA_VERSION,
        "app_version": "v1.3.0",
        "alembic_head": _HEAD,
        "generated_at": "2026-07-07T00:00:00Z",
    }


def test_load_manifest_roundtrip(tmp_path):
    path = tmp_path / rm.MANIFEST_FILENAME
    path.write_text(json.dumps(_valid_manifest()), encoding="utf-8")
    assert rm.load_manifest(path)["alembic_head"] == _HEAD


def test_load_manifest_missing_file(tmp_path):
    with pytest.raises(rm.ManifestError):
        rm.load_manifest(tmp_path / "absent.json")


def test_load_manifest_bad_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(rm.ManifestError):
        rm.load_manifest(path)


def test_load_manifest_wrong_schema(tmp_path):
    path = tmp_path / "m.json"
    bad = _valid_manifest()
    bad["schema_version"] = 999
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(rm.ManifestError):
        rm.load_manifest(path)


@pytest.mark.parametrize("cli_version", ["2026.11.2", "2026.11.2.post1", None])
def test_load_manifest_accepts_a_cli_version_or_none(tmp_path, cli_version):
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({**_valid_manifest(), "cli_version": cli_version}), encoding="utf-8"
    )

    assert rm.load_manifest(path)["cli_version"] == cli_version


@pytest.mark.parametrize(
    "cli_version", ["2026.11.2; curl evil", "latest", "", "v2026.11.2", 2026]
)
def test_load_manifest_rejects_a_cli_version_that_is_no_version(tmp_path, cli_version):
    """It goes into an install command, so it must be a version and nothing else."""
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({**_valid_manifest(), "cli_version": cli_version}), encoding="utf-8"
    )

    with pytest.raises(rm.ManifestError):
        rm.load_manifest(path)


def test_load_manifest_missing_field(tmp_path):
    path = tmp_path / "m.json"
    bad = _valid_manifest()
    del bad["alembic_head"]
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(rm.ManifestError):
        rm.load_manifest(path)


# --- `mascope prod manifest` command ---


def test_manifest_command_stdout(monkeypatch, cli_runner):
    monkeypatch.setattr(
        prod_main.release_manifest,
        "build_manifest",
        lambda version, backend_path: {
            "schema_version": 1,
            "app_version": version,
            "alembic_head": _HEAD,
            "generated_at": "2026-07-07T00:00:00Z",
        },
    )
    result = cli_runner.invoke(prod_main.prod_app, ["manifest", "--version", "v1.3.0"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["app_version"] == "v1.3.0"
    assert payload["alembic_head"] == _HEAD


def test_manifest_command_output_file(monkeypatch, cli_runner, tmp_path):
    monkeypatch.setattr(
        prod_main.release_manifest,
        "build_manifest",
        lambda version, backend_path: {"schema_version": 1, "alembic_head": _HEAD},
    )
    out = tmp_path / "mascope-manifest.json"
    result = cli_runner.invoke(
        prod_main.prod_app, ["manifest", "--version", "v1.3.0", "--output", str(out)]
    )
    assert result.exit_code == 0
    assert json.loads(out.read_text())["alembic_head"] == _HEAD


# --- update --check --manifest consumption ---


def test_check_uses_manifest_head(monkeypatch, cli_runner, tmp_path):
    """The manifest supplies target head + version; no image head read occurs."""
    manifest = tmp_path / rm.MANIFEST_FILENAME
    manifest.write_text(json.dumps(_valid_manifest()), encoding="utf-8")

    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)
    # DB is behind the manifest head -> migration update.
    monkeypatch.setattr(
        prod_main.preflight, "db_current_revision", lambda *a, **k: "000000aaaaaa"
    )
    monkeypatch.setattr(prod_main.preflight, "_image_changed", lambda image, c: True)
    # If the image head were read, this would raise; the manifest must prevent it.
    monkeypatch.setattr(
        prod_main.preflight,
        "image_alembic_head",
        lambda image: (_ for _ in ()).throw(AssertionError("should not inspect image")),
    )
    monkeypatch.setattr(prod_main, "_run_compose", lambda *a, **k: None)

    result = cli_runner.invoke(
        prod_main.prod_app,
        ["update", "--check", "--no-pull", "--json", "--manifest", str(manifest)],
    )
    assert result.exit_code == 20
    payload = json.loads(result.stdout)
    assert payload["target"] == "v1.3.0"
    assert payload["target_revision"] == _HEAD
    assert payload["classification"] == "migration-update"


def test_check_invalid_manifest_errors(monkeypatch, cli_runner, tmp_path):
    manifest = tmp_path / "bad.json"
    manifest.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)

    result = cli_runner.invoke(
        prod_main.prod_app,
        ["update", "--check", "--no-pull", "--manifest", str(manifest)],
    )
    assert result.exit_code == prod_main.preflight.ERROR_EXIT_CODE
