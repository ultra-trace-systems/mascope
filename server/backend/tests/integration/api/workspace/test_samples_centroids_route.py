"""
Tests: the per-scan centroids of sample items, ``GET /api/samples/centroids``.

Each scan's entry carries its scan number and each centroid its flags, and
``exclude`` names the flags whose centroids are left out. The route is driven
the way a client calls it - the query string parsed, the permission check
run on the real sample item, the body encoded - over a scripted read: the
sample row the controller selects and the centroids
``mascope_signal.compute`` would hand back for it. That the reader leaves the
named centroids out is pinned in ``mascope_thermo``'s own tests.

Fixtures used:
- ``alpha_item`` - a sample item in ws_alpha, of which every user is a member
"""

from types import SimpleNamespace

import numpy as np
import pytest

from mascope_backend.api.controllers.samples import samples_controller


#: Two scans of the scripted read: the second was the file's last, at
#: retention time 0.0, which only its number tells apart from the first.
_PER_SCAN = [
    {
        "scan": 7,
        "masses": np.array([61.9884, 100.0]),
        "intensities": np.array([5.0, 6.0]),
        "resolutions": np.array([1.0, 2.0]),
        "signal_to_noise": np.array([3.0, 4.0]),
        "flags": np.array([16, 0]),
        "timestamp": 0.0,
    },
    {
        "scan": 8,
        "masses": np.array([100.0]),
        "intensities": np.array([7.0]),
        "resolutions": np.array([2.0]),
        "signal_to_noise": np.array([5.0]),
        "flags": np.array([10]),
        "timestamp": 0.0,
    },
]


class _Session:
    """An ``async_session()`` whose one query answers with the given sample
    rows: the controller reads them from a view the test database lacks."""

    def __init__(self, samples):
        self.samples = samples

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: self.samples)
        )


@pytest.fixture
def per_scan(monkeypatch, alpha_item):
    """Answer every sample's per-scan read with the scripted scans, and
    record what each read was asked for."""
    asked = []
    sample = SimpleNamespace(
        sample_item_id=alpha_item, filename="ORBI_a", t0=0.0, t1=60.0, polarity="+"
    )

    def read(**kwargs):
        asked.append(kwargs)
        return [dict(scan) for scan in _PER_SCAN]

    monkeypatch.setattr(samples_controller, "async_session", lambda: _Session([sample]))
    monkeypatch.setattr(
        samples_controller, "get_sample_file_type", lambda _filename: "orbi_raw"
    )
    monkeypatch.setattr(
        samples_controller.m_compute, "get_orbi_centroids_per_scan", read
    )
    return asked


@pytest.mark.asyncio
async def test_each_scan_carries_its_number_and_each_centroid_its_flags(
    guest_client, alpha_item, per_scan
):
    resp = await guest_client.get(
        "/api/samples/centroids", params={"sample_item_ids": [alpha_item]}
    )

    assert resp.status_code == 200, resp.text
    centroids = resp.json()["data"][alpha_item]
    assert centroids["scan"] == [7, 8]
    assert centroids["flags"] == [[16, 0], [10]]
    assert centroids["masses"] == [[61.9884, 100.0], [100.0]]
    assert centroids["timestamp"] == [0.0, 0.0]
    assert [asked["exclude"] for asked in per_scan] == [()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exclude",
    [["reference,exception"], ["Reference", "exception"]],
    ids=["comma-separated", "repeated"],
)
async def test_the_excluded_flags_are_handed_to_the_read(
    guest_client, alpha_item, per_scan, exclude
):
    resp = await guest_client.get(
        "/api/samples/centroids",
        params={"sample_item_ids": [alpha_item], "exclude": exclude},
    )

    assert resp.status_code == 200, resp.text
    assert [asked["exclude"] for asked in per_scan] == [("reference", "exception")]


@pytest.mark.asyncio
async def test_an_exclusion_that_names_no_flag_is_refused_before_anything_is_read(
    guest_client, alpha_item, per_scan
):
    resp = await guest_client.get(
        "/api/samples/centroids",
        params={"sample_item_ids": [alpha_item], "exclude": "lock"},
    )

    assert resp.status_code == 422, resp.text
    assert "lock" in resp.text
    assert per_scan == []
