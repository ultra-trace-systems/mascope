"""
Tests for the deployment id (``mascope_backend.deployment``).

The invariant is one id per deployment, stable across restarts and updates:
generated on the first start, found on every later one, and never quietly
replaced - a replaced id would split one deployment's exports across two
names. A configured id wins over the generated one. Reading never writes, so
only the startup step can create the file.

Every test points the filestore at a temporary directory, so nothing here
touches the runtime home of whoever runs the suite.
"""

import json
import os

import pytest
from test_utils import captured_logs

from mascope_backend import deployment
from mascope_backend.runtime import runtime
from mascope_runtime.config import DEPLOYMENT_ID_PATTERN


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """An empty filestore of its own, and no configured id."""
    root = tmp_path / "filestore"
    root.mkdir()
    monkeypatch.setattr(runtime.meta, "filestore", str(root))
    monkeypatch.setattr(runtime.config, "deployment_id", None)
    return root


def _kept(filestore) -> dict:
    with open(filestore / "deployment.json", encoding="utf-8") as f:
        return json.load(f)


def test_the_id_is_kept_at_the_root_of_the_filestore(filestore):
    """The filestore is what the off-site backup copies with the database
    dumps, and it is mounted into the containers; the secrets directory is
    neither, so an id kept there would not survive a container."""
    assert deployment.deployment_file() == os.path.join(
        str(filestore), "deployment.json"
    )


def test_the_first_start_generates_an_id_and_keeps_it(filestore):
    generated = deployment.ensure_deployment_id()

    assert generated and DEPLOYMENT_ID_PATTERN.fullmatch(generated)
    record = _kept(filestore)
    assert record["deployment_id"] == generated
    assert record["generated_utc"].endswith("Z")
    assert deployment.deployment_id() == generated


def test_every_later_start_finds_the_same_id(filestore):
    first = deployment.ensure_deployment_id()
    before = (filestore / "deployment.json").read_bytes()

    assert deployment.ensure_deployment_id() == first
    assert deployment.ensure_deployment_id() == first
    assert (filestore / "deployment.json").read_bytes() == before


def test_reading_never_writes(filestore):
    """Only the startup step creates the id. A process that reads before it -
    or one that never runs it - reports no id rather than minting a second."""
    assert deployment.deployment_id() is None
    assert not (filestore / "deployment.json").exists()


def test_a_configured_id_wins_and_needs_no_file(filestore, monkeypatch):
    monkeypatch.setattr(runtime.config, "deployment_id", "example-lab")

    assert deployment.ensure_deployment_id() == "example-lab"
    assert deployment.deployment_id() == "example-lab"
    assert not (filestore / "deployment.json").exists()


def test_a_configured_id_wins_over_a_generated_one(filestore, monkeypatch):
    """Configuring an id on a deployment that already generated one renames
    it from then on - the operator's word is the one exported."""
    deployment.ensure_deployment_id()
    monkeypatch.setattr(runtime.config, "deployment_id", "example-lab")

    assert deployment.deployment_id() == "example-lab"


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "",
        json.dumps(["not", "an", "object"]),
        json.dumps({"generated_utc": "2026-10-01T12:00:00Z"}),
        json.dumps({"deployment_id": "not an id"}),
        json.dumps({"deployment_id": 42}),
    ],
)
def test_an_unreadable_file_is_reported_and_left_alone(filestore, content):
    """A replaced id would quietly split the deployment's exports across two
    names, so a file that cannot be read is an operator's to repair - the
    startup says so and writes nothing."""
    path = filestore / "deployment.json"
    path.write_text(content, encoding="utf-8")

    with captured_logs("WARNING") as records:
        assert deployment.ensure_deployment_id() is None

    assert path.read_text(encoding="utf-8") == content
    assert any(
        record["level"].name == "WARNING" and "No deployment id" in record["message"]
        for record in records
    )
    assert deployment.deployment_id() is None


def test_the_generated_id_is_written_whole(filestore, monkeypatch):
    """Written beside the file and renamed over it, so a start that dies part
    way leaves no file - and the next start generates one - rather than a
    truncated file the deployment would then refuse forever."""
    calls = []
    real_write_json = deployment.write_json

    def spy(path, data, **kwargs):
        calls.append((path, data))
        return real_write_json(path, data, **kwargs)

    monkeypatch.setattr(deployment, "write_json", spy)

    generated = deployment.ensure_deployment_id()

    assert calls == [(deployment.deployment_file(), _kept(filestore))]
    assert calls[0][1]["deployment_id"] == generated
