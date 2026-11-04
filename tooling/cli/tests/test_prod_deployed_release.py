"""
Tests for the deployed-release record a wheel deployment keeps.

The wiring - which commands write it, and `_deploy_version` reading it - is
covered in test_prod_update.py, test_prod_env.py, test_prod_auto.py and
test_prod_doctor.py; here the file itself.
"""

import json

import pytest

from mascope_cli.cmd.prod import deployed_release


def test_save_then_load(tmp_path):
    deployed_release.save(str(tmp_path), "v1.12.0-rc.1")

    assert deployed_release.load(str(tmp_path)) == "v1.12.0-rc.1"
    data = json.loads(deployed_release.path(str(tmp_path)).read_text("utf-8"))
    assert data["version"] == "v1.12.0-rc.1" and data["deployed_at"]


def test_the_record_lives_in_the_runtime_dir_not_the_filestore(tmp_path):
    # Not the backend's deployment.json: that one names the deployment and
    # travels with the data; this one names the images the CLI starts.
    path = deployed_release.path(str(tmp_path))

    assert path == tmp_path / ".runtime" / "deployed-release.json"
    assert path.name != "deployment.json"


def test_load_without_a_record_is_none(tmp_path):
    assert deployed_release.load(str(tmp_path)) is None


@pytest.mark.parametrize("version", ["latest", "v2026.09.01-9b9e54d", "1.2.0"])
def test_save_refuses_what_is_not_a_release(tmp_path, version):
    with pytest.raises(ValueError):
        deployed_release.save(str(tmp_path), version)

    assert not deployed_release.path(str(tmp_path)).exists()


def test_a_failed_write_keeps_the_previous_record(tmp_path, monkeypatch):
    deployed_release.save(str(tmp_path), "v1.11.0")

    def _crash(fd):
        raise OSError("disk full")  # the new content is written, not yet durable

    # Only around the save: os.fsync is the os module's, which pytest's own
    # teardown uses too.
    with monkeypatch.context() as m, pytest.raises(OSError):
        m.setattr("mascope_runtime.atomic.os.fsync", _crash)
        deployed_release.save(str(tmp_path), "v1.12.0")

    assert deployed_release.load(str(tmp_path)) == "v1.11.0"
    assert list((tmp_path / ".runtime").iterdir()) == [
        deployed_release.path(str(tmp_path))
    ]


def test_clear_removes_the_record_and_tolerates_none(tmp_path):
    deployed_release.save(str(tmp_path), "v1.12.0")

    deployed_release.clear(str(tmp_path))
    deployed_release.clear(str(tmp_path))  # already gone: no error

    assert deployed_release.load(str(tmp_path)) is None
