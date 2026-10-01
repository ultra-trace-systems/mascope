"""
Tests for the provenance block (``mascope_backend.provenance``).

The block is a contract with whatever reads an export long after it was
written - a notebook, a reviewer, a deposit package - so its keys are pinned
here exactly: a key renamed or dropped fails a test rather than quietly
breaking a reader. Each version it reports must come from the one place the
server itself takes it from.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from mascope_backend.api.new.peak_assignments.config import (
    PEAK_ASSIGNMENT_ENGINE_VERSION,
)
from mascope_backend.provenance import (
    PROVENANCE_VERSION,
    build_provenance,
    flatten_provenance,
)
from mascope_backend.runtime import runtime


@pytest.fixture(autouse=True)
def configured_deployment(monkeypatch):
    """A configured deployment id, so no test reads a real filestore."""
    monkeypatch.setattr(runtime.config, "deployment_id", "example-lab")


def test_the_block_carries_exactly_the_keys_of_version_1():
    block = build_provenance()

    assert list(block) == [
        "provenance_version",
        "generated_utc",
        "deployment_id",
        "produced_with",
    ]
    assert list(block["produced_with"]) == [
        "mascope_version",
        "match_score_version",
        "peak_assignment_engine_version",
    ]
    assert block["provenance_version"] == PROVENANCE_VERSION == 1


def test_the_deployment_is_named():
    assert build_provenance()["deployment_id"] == "example-lab"


def test_a_deployment_without_an_id_says_so(monkeypatch, tmp_path):
    """No id is reported as null, not as an invented one."""
    monkeypatch.setattr(runtime.config, "deployment_id", None)
    monkeypatch.setattr(runtime.meta, "filestore", str(tmp_path))

    assert build_provenance()["deployment_id"] is None


def test_the_mascope_version_is_the_deployed_one(monkeypatch):
    """The same value ``GET /api/version`` reports: MASCOPE_VERSION, which
    compose also uses to pick the image tag."""
    monkeypatch.setattr(runtime, "_version", "v9.9.9-test", raising=False)

    assert build_provenance()["produced_with"]["mascope_version"] == "v9.9.9-test"


def test_an_unset_version_reports_unknown(monkeypatch):
    monkeypatch.setattr(runtime, "_version", None, raising=False)

    assert build_provenance()["produced_with"]["mascope_version"] == "unknown"


@pytest.mark.parametrize("setting, expected", [("1", 1), ("2", 2), (None, 1)])
def test_the_match_score_version_is_the_process_switch(monkeypatch, setting, expected):
    if setting is None:
        monkeypatch.delenv("MASCOPE_MATCH_SCORE_VERSION", raising=False)
    else:
        monkeypatch.setenv("MASCOPE_MATCH_SCORE_VERSION", setting)

    assert build_provenance()["produced_with"]["match_score_version"] == expected


def test_the_engine_version_is_the_one_this_build_runs():
    assert (
        build_provenance()["produced_with"]["peak_assignment_engine_version"]
        == PEAK_ASSIGNMENT_ENGINE_VERSION
    )


def test_generated_utc_is_now_in_utc_to_the_second():
    before = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = build_provenance()["generated_utc"]
    after = datetime.now(timezone.utc)

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp)
    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before <= moment <= after + timedelta(seconds=1)


def test_inputs_are_left_out_unless_given():
    assert "inputs" not in build_provenance()


def test_inputs_are_recorded_as_given():
    inputs = {"dataset_id": "d1", "sample_batch_id": "b1"}

    block = build_provenance(inputs=inputs)
    inputs["dataset_id"] = "changed later"

    assert block["inputs"] == {"dataset_id": "d1", "sample_batch_id": "b1"}


def test_the_block_is_json():
    block = build_provenance(inputs={"target_collection_ids": ["c1", "c2"]})

    assert json.loads(json.dumps(block)) == block


def test_flattening_joins_nested_keys_and_lists_in_order(monkeypatch):
    monkeypatch.setattr(runtime, "_version", "v9.9.9-test", raising=False)
    block = build_provenance(
        inputs={"sample_batch_id": "b1", "target_collection_ids": ["c1", "c2"]}
    )

    rows = flatten_provenance(block)

    assert [key for key, _ in rows] == [
        "provenance_version",
        "generated_utc",
        "deployment_id",
        "produced_with.mascope_version",
        "produced_with.match_score_version",
        "produced_with.peak_assignment_engine_version",
        "inputs.sample_batch_id",
        "inputs.target_collection_ids",
    ]
    values = dict(rows)
    assert values["produced_with.mascope_version"] == "v9.9.9-test"
    assert values["inputs.target_collection_ids"] == "c1, c2"
    assert values["deployment_id"] == "example-lab"


def test_an_empty_list_flattens_to_an_empty_value():
    assert flatten_provenance({"inputs": {"target_collection_ids": []}}) == [
        ("inputs.target_collection_ids", "")
    ]
