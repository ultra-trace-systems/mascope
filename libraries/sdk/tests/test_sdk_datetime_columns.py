"""
Hermetic tests of the listings' datetime coercion
(``resources/_base.py``: ``_coerce_datetime_columns`` and
``_coerce_utc_columns``).

The server writes a time whose microseconds are 0 without its fraction
(``10:45:57+00:00``) and every other time with it (``10:45:57.708105+00:00``).
A column that holds both must still come out as datetimes: pinned here on the
helpers and through a listing that uses them.
"""

import json
from typing import Any

import pandas as pd
import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.resources._base import (
    _coerce_datetime_columns,
    _coerce_utc_columns,
)
from mascope_sdk.resources.workspaces import WorkspacesResource


#: Whole second first: the value pandas would infer the format from.
MIXED_UTC = ["2026-08-28T10:45:57+00:00", "2026-08-28T10:45:57.708105+00:00"]
MIXED_LOCAL = ["2026-08-28T10:45:57", "2026-08-28T10:45:57.708105"]
WHOLE_UTC = ["2026-08-28T10:45:57+00:00", "2026-08-28T11:00:00+00:00"]


def test_a_mixed_utc_column_becomes_utc_datetimes():
    df = _coerce_datetime_columns(pd.DataFrame({"x_datetime_utc": MIXED_UTC}))

    assert str(df["x_datetime_utc"].dtype).startswith("datetime64")
    assert str(df["x_datetime_utc"].dt.tz) == "UTC"
    assert df["x_datetime_utc"].tolist() == [
        pd.Timestamp("2026-08-28T10:45:57", tz="UTC"),
        pd.Timestamp("2026-08-28T10:45:57.708105", tz="UTC"),
    ]


def test_a_mixed_local_column_becomes_naive_datetimes():
    df = _coerce_datetime_columns(pd.DataFrame({"datetime": MIXED_LOCAL}))

    assert str(df["datetime"].dtype).startswith("datetime64")
    assert df["datetime"].dt.tz is None
    assert df["datetime"].iloc[1] == pd.Timestamp("2026-08-28T10:45:57.708105")


def test_a_whole_second_column_still_parses():
    df = _coerce_datetime_columns(pd.DataFrame({"datetime_utc": WHOLE_UTC}))

    assert df["datetime_utc"].iloc[1] == pd.Timestamp("2026-08-28T11:00", tz="UTC")


def test_a_mixed_audit_column_becomes_utc_datetimes():
    df = _coerce_utc_columns(
        pd.DataFrame({"peak_assignment_run_utc_created": MIXED_UTC})
    )

    column = df["peak_assignment_run_utc_created"]
    assert str(column.dtype).startswith("datetime64")
    assert column.iloc[1] == pd.Timestamp("2026-08-28T10:45:57.708105", tz="UTC")


class _StubClient:
    url = "http://server"
    access_token = "token"
    workspace_id = "ws-1"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, Any] = {}


@pytest.fixture
def wire(monkeypatch):
    def install(body):
        def fake(url, **kwargs):
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(body).encode()
            return response

        monkeypatch.setattr(_http.requests, "get", fake)

    return install


def test_a_listing_hands_over_a_mixed_column_as_datetimes(wire):
    rows = [
        {"workspace_id": f"ws-{i}", "workspace_name": f"W{i}", "datetime_utc": t}
        for i, t in enumerate(MIXED_UTC)
    ]
    wire({"data": rows, "total": len(rows)})

    workspaces = WorkspacesResource(_StubClient()).list()

    column = workspaces["datetime_utc"]
    assert str(column.dtype).startswith("datetime64")
    assert column.iloc[0] < pd.Timestamp("2026-08-28T10:45:57.5", tz="UTC")
