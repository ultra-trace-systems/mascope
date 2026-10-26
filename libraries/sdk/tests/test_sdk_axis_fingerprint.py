"""
Hermetic unit tests for the axis fingerprint reads,
``SampleFilesResource.get_axis_fingerprint`` and
``SamplesResource.get_axis_fingerprint``: the path each asks the server for,
and that the document comes back as the server sent it. They stub the
resources' GET seam and need no running stack.
"""

from typing import Any

import pytest

from mascope_sdk.resources.sample_files import SampleFilesResource
from mascope_sdk.resources.samples import SamplesResource


#: A document as the routes send it under ``data``: the file's, and the
#: sample's item block beside it.
DOCUMENT = {
    "schema": "mascope.axis_fingerprint",
    "version": 1,
    "status": "no_fingerprint",
    "sample_file_id": "file-1",
    "modes": [{"mode_id": "+|R120000|40.0000-500.0000", "segments": None}],
    "item": {
        "sample_item_id": "sample-1",
        "sample_file_id": "file-1",
        "scans": [1, 2],
        "mode_id": "+|R120000|40.0000-500.0000",
        "mz_calibration_factor": None,
    },
}


def _recording(resource):
    """Stub the resource's GET: record what was asked, answer ``DOCUMENT``."""
    resource.requests = []

    def fake_get(path: str, params: dict[str, Any] | None = None, **kwargs):
        resource.requests.append((path, params, kwargs))
        return DOCUMENT

    resource._get = fake_get
    return resource


@pytest.fixture
def sample_files() -> SampleFilesResource:
    return _recording(SampleFilesResource(client=None))


@pytest.fixture
def samples() -> SamplesResource:
    return _recording(SamplesResource(client=None))


def test_a_files_fingerprint_is_read_from_its_route(sample_files):
    document = sample_files.get_axis_fingerprint("file-1")

    assert sample_files.requests == [("sample/files/file-1/axis_fingerprint", None, {})]
    assert document == DOCUMENT


def test_a_samples_fingerprint_is_read_from_its_route(samples):
    document = samples.get_axis_fingerprint("sample-1")

    assert samples.requests == [("samples/sample-1/axis_fingerprint", None, {})]
    assert document == DOCUMENT
