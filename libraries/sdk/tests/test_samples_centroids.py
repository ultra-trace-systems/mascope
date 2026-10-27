"""
Hermetic unit tests for ``SamplesResource.get_centroids``: what it asks the
server for, and that the per-scan answer reaches the caller as sent, scan
numbers and centroid flags included. They stub the resource's GET seam and
need no running stack.
"""

from typing import Any

import pytest

from mascope_sdk.exceptions import MascopeAPIError
from mascope_sdk.resources.samples import SamplesResource


#: One sample's per-scan centroids as the route sends them.
CENTROIDS = {
    "sample-1": {
        "scan": [7, 8],
        "masses": [[61.9884, 100.0], [100.0]],
        "intensities": [[5.0, 6.0], [7.0]],
        "resolutions": [[1.0, 2.0], [2.0]],
        "signal_to_noise": [[3.0, 4.0], [5.0]],
        "flags": [[16, 0], [10]],
        "timestamp": [0.0, 0.0],
    }
}


@pytest.fixture
def samples(monkeypatch) -> SamplesResource:
    """A resource that records its GETs in ``requests`` and answers
    ``CENTROIDS``."""
    resource = SamplesResource(client=None)
    resource.requests = []

    def fake_get(path: str, params: dict[str, Any] | None = None, **kwargs):
        resource.requests.append((path, params or {}))
        return CENTROIDS

    monkeypatch.setattr(resource, "_get", fake_get)
    return resource


def test_every_centroid_is_asked_for_by_default(samples):
    centroids = samples.get_centroids(["sample-1"])

    assert samples.requests == [
        ("samples/centroids", {"sample_item_ids": ["sample-1"]})
    ]
    assert centroids["sample-1"]["scan"] == [7, 8]
    assert centroids["sample-1"]["flags"] == [[16, 0], [10]]


@pytest.mark.parametrize(
    "exclude",
    [["reference", "exception"], ("reference", "exception")],
    ids=["list", "tuple"],
)
def test_the_excluded_flags_are_sent_comma_separated(samples, exclude):
    samples.get_centroids(["sample-1"], exclude=exclude)

    assert samples.requests == [
        (
            "samples/centroids",
            {"sample_item_ids": ["sample-1"], "exclude": "reference,exception"},
        )
    ]


def test_one_flag_may_be_named_alone(samples):
    samples.get_centroids(["sample-1"], exclude="reference")

    assert samples.requests[0][1]["exclude"] == "reference"


def test_an_empty_exclusion_leaves_nothing_out(samples):
    samples.get_centroids(["sample-1"], exclude=[])

    assert "exclude" not in samples.requests[0][1]


#: The same sample as a server older than the flags sends it: it ignores
#: ``exclude`` and serves every centroid, with neither ``scan`` nor ``flags``.
OLD_SERVER_CENTROIDS = {
    "sample-1": {
        key: value
        for key, value in CENTROIDS["sample-1"].items()
        if key not in ("scan", "flags")
    }
}


def test_an_exclusion_an_older_server_ignored_raises(samples, monkeypatch):
    monkeypatch.setattr(
        samples, "_get", lambda path, params=None, **kw: OLD_SERVER_CENTROIDS
    )

    with pytest.raises(MascopeAPIError, match="predates centroid flags"):
        samples.get_centroids(["sample-1"], exclude=["reference"])


def test_an_older_server_answers_as_before_without_an_exclusion(samples, monkeypatch):
    monkeypatch.setattr(
        samples, "_get", lambda path, params=None, **kw: OLD_SERVER_CENTROIDS
    )

    assert samples.get_centroids(["sample-1"]) == OLD_SERVER_CENTROIDS


def test_an_exclusion_a_flagged_answer_honours_is_returned(samples):
    centroids = samples.get_centroids(["sample-1"], exclude=["reference"])

    assert centroids == CENTROIDS
