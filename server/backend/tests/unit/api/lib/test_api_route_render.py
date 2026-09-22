"""
How ``@api_route`` renders a handler's result, and what it does when it cannot.

Starlette renders JSON with ``allow_nan=False``, so one NaN or +/-Infinity
anywhere in a payload raises ValueError at render time. Left to
``process_exception``, that ValueError reads as a bad request: the client is
told a sound request was a 400, and the failure is logged at INFO, below the
WARNING floor of error monitoring, where nobody hears of it. So a non-finite
float is sent as null with a WARNING naming the route, and a body that cannot
be rendered at all is a 500 logged at ERROR.
"""

import json

import numpy as np
import pytest
from fastapi.responses import FileResponse, JSONResponse
from test_utils import captured_logs

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException


def strict_loads(body: bytes):
    """Parse like a browser's ``JSON.parse``: reject NaN/Infinity literals."""

    def reject(literal):
        raise ValueError(f"non-strict JSON literal: {literal}")

    return json.loads(body, parse_constant=reject)


def route_returning(payload, status_code: int = 200):
    """A public ``@api_route`` handler that returns ``payload``."""

    @api_route(status_code=status_code, public=True)
    async def handler():
        return payload

    return handler


def route_raising(exc: Exception):
    """A public ``@api_route`` handler that raises ``exc``."""

    @api_route(public=True)
    async def handler():
        raise exc

    return handler


class TestNonFiniteFloats:
    @pytest.mark.asyncio
    async def test_nan_and_infinity_render_as_null(self):
        handler = route_returning(
            {
                "height": [1.5, float("nan"), float("inf"), float("-inf")],
                # np.float64 subclasses float and is what xarray/pandas hand out
                "nested": {"score": np.float64("nan"), "rows": [(2.0, np.nan)]},
            }
        )

        response = await handler()

        assert response.status_code == 200
        assert strict_loads(response.body) == {
            "height": [1.5, None, None, None],
            "nested": {"score": None, "rows": [[2.0, None]]},
        }

    @pytest.mark.asyncio
    async def test_text_nan_is_not_a_float_and_survives(self):
        # Only floats are mapped: a formula or label spelled "NaN" is data.
        handler = route_returning(
            {"formula": "NaN", "labels": ["NaN", "Infinity"], "value": float("nan")}
        )

        response = await handler()

        assert strict_loads(response.body) == {
            "formula": "NaN",
            "labels": ["NaN", "Infinity"],
            "value": None,
        }

    @pytest.mark.asyncio
    async def test_one_warning_names_the_route(self):
        handler = route_returning({"a": float("nan"), "b": [float("inf")] * 3})

        # WARNING and up is what error monitoring receives.
        with captured_logs("WARNING") as records:
            await handler()

        assert [r["level"].name for r in records] == ["WARNING"]
        # Qualified by module: handler names repeat across route modules.
        assert f"{__name__}.route_returning.<locals>.handler" in records[0]["message"]

    @pytest.mark.asyncio
    async def test_finite_payload_renders_strictly_and_logs_nothing(self):
        payload = {"height": [1.5, 0.0, -2.0], "formula": "NaN", "count": 3}
        handler = route_returning(payload)

        with captured_logs("WARNING") as records:
            response = await handler()

        assert records == []
        # Byte for byte what Starlette's own strict render produces.
        assert response.body == JSONResponse(content=payload).body

    @pytest.mark.asyncio
    async def test_error_payload_of_a_warning_is_rendered_the_same_way(self):
        # A 200/207 ApiException answers with data the frontend reads, e.g.
        # a calibration fit's numbers; it goes through the same render.
        handler = route_raising(
            ApiException("Fit warning", {"data": {"error_ppm": float("nan")}}, 200)
        )

        with captured_logs("WARNING") as records:
            response = await handler()

        assert response.status_code == 200
        assert strict_loads(response.body) == {
            "error": "Fit warning",
            "detail": {"data": {"error_ppm": None}},
        }
        assert [r["level"].name for r in records] == ["WARNING"]


class TestRenderFailure:
    """A body that cannot be rendered is the route's fault, not the client's."""

    @pytest.mark.parametrize(
        "payload",
        [
            # jsonable_encoder raises ValueError on a type it does not know
            pytest.param({"count": np.int64(3)}, id="numpy-int"),
            # these fail the non-finite fallback as well: nothing to map in them
            pytest.param({"name": "file\udcff"}, id="lone-surrogate"),
            pytest.param({float("nan"): 1.0}, id="nan-key"),
        ],
    )
    @pytest.mark.asyncio
    async def test_it_is_a_500_logged_once_at_error(self, payload):
        handler = route_returning(payload)

        with captured_logs("INFO") as records:
            response = await handler()

        assert response.status_code == 500
        body = strict_loads(response.body)
        assert "Unexpected error" in body["error"]
        assert set(body["detail"]) == {"error_id"}
        # One ERROR with its traceback; no WARNING claiming it was rendered,
        # and no INFO record taking it for a client error.
        assert [r["level"].name for r in records] == ["ERROR"]
        assert records[0]["exception"] is not None

    @pytest.mark.asyncio
    async def test_a_value_error_from_the_handler_stays_a_client_error(self):
        # The handler's own ValueError still means a bad value in the request.
        handler = route_raising(ValueError("mz must be positive"))

        with captured_logs("INFO") as records:
            response = await handler()

        assert response.status_code == 400
        assert "mz must be positive" in strict_loads(response.body)["error"]
        assert [r["level"].name for r in records] == ["INFO"]


class TestResponseShape:
    @pytest.mark.asyncio
    async def test_process_id_moves_to_a_header(self):
        handler = route_returning(
            {"message": "Queued", "process_id": "abc123"}, status_code=202
        )

        response = await handler()

        assert response.status_code == 202
        assert response.headers["Process-ID"] == "abc123"
        assert strict_loads(response.body) == {"message": "Queued"}

    @pytest.mark.asyncio
    async def test_a_file_response_is_returned_as_is(self, tmp_path):
        path = tmp_path / "export.csv"
        path.write_text("mz,height\n")
        file_response = FileResponse(path)

        response = await route_returning(file_response)()

        assert response is file_response
