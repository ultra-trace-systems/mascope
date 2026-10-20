"""
Failures as structured, actionable results: the SDK's exceptions mapped to
``ToolkitError``s whose message says what to do and whose ``to_dict()`` a
transport hands back in place of a stack trace. A 403 names the object, the
role it needs and the role the user has; transient failures are left to the
SDK's retries, with none added.

Hermetic: the workspace of ``conftest.py`` behind a real SDK client.
"""

import json

import pytest

from mascope_ai import (
    AccessDenied,
    InvalidRequest,
    NotAuthenticated,
    NotFound,
    RowCapExceeded,
    ToolkitError,
    Unavailable,
)
from mascope_ai.errors import LISTED
from mascope_sdk._http import RETRY_MAX_ATTEMPTS


class TestMapping:
    def test_a_403_names_the_object_the_role_needed_and_the_role_held(self, kit, api):
        api.fail("GET", "samples/s21/peaks", 403)

        with pytest.raises(AccessDenied) as caught:
            kit.load_peaks_by_stage("s21", [(0, 5)])

        error = caught.value
        assert error.target.id == "s21"
        assert error.required_role == "guest"
        assert error.current_role == "editor"
        assert error.workspace == "Workspace A"
        message = str(error)
        assert "Sample 1" in message and "s21" in message
        assert "You have editor on workspace 'Workspace A'" in message
        assert "it needs guest" in message
        data = error.to_dict()
        assert data["error"] == "access_denied"
        assert data["target"]["id"] == "s21"
        json.dumps(data)

    def test_a_load_with_some_reads_refused_is_an_incomplete_answer(self, kit, api):
        api.fail("GET", "samples/s22/peaks", 403)

        frame = kit.load_peaks(batches="Spring 2026")

        assert frame.complete is False
        assert frame.n_rows > 0
        (note,) = frame.failures
        assert note.where["sample_item_id"] == "s22"
        assert note.error_type == "PermissionDeniedError"

    def test_a_load_with_every_read_refused_is_a_403(self, kit, api):
        api.fail("GET", r"samples/\w+/peaks", 403)

        with pytest.raises(AccessDenied) as caught:
            kit.load_peaks(batches="Spring 2026")

        assert caught.value.target.id == "b2"
        assert kit.session.frames() == []

    def test_a_403_on_one_read_names_the_role_held(self, kit, api):
        api.fail("GET", r"samples/s21/spectrum", 403)

        with pytest.raises(AccessDenied) as caught:
            kit.load_spectrum("s21")

        assert caught.value.target.id == "s21"
        assert caught.value.current_role == "editor"
        assert "read the spectrum of sample 'Sample 1' (s21)" in str(caught.value)

    def test_a_403_on_a_listing(self, kit, api):
        api.fail("GET", "target/collections", 403)

        with pytest.raises(AccessDenied, match="list the target collections"):
            kit.find("x", kind="collection")

    def test_a_401(self, kit, api):
        api.fail("GET", "target/collections", 401)

        with pytest.raises(NotAuthenticated, match="ai-agent") as caught:
            kit.find("x", kind="collection")

        assert caught.value.to_dict()["error"] == "not_authenticated"

    def test_a_404(self, kit, api):
        from mascope_ai import Ref

        # The server's 404 for a route with nothing behind it (an unknown
        # sample ID is a 403 there, as conftest answers it)
        api.fail("GET", "samples/gone", 404)

        with pytest.raises(NotFound) as caught:
            kit.describe(Ref("gone", "sample", "Gone", parent="b1"))

        assert caught.value.to_dict() == {
            "error": "not_found",
            "message": caught.value.message,
            "query": "gone",
            "kind": "sample",
        }

    def test_a_422(self, kit, api):
        api.fail("GET", r"samples/\w+/spectrum", 422)

        with pytest.raises(InvalidRequest, match="refused"):
            kit.load_spectrum("s21")

    @pytest.mark.parametrize("status", [503, 429])
    def test_a_transient_failure_is_retried_by_the_sdk_alone(self, kit, api, status):
        api.fail("GET", r"samples/\w+/spectrum", status)

        with pytest.raises(Unavailable, match="already retried"):
            kit.load_spectrum("s21")

        assert len(api.requests_to(r"samples/s21/spectrum")) == RETRY_MAX_ATTEMPTS

    def test_a_server_error_mid_load_is_a_failure_of_that_sample_not_the_load(
        self, kit, api
    ):
        api.fail("GET", r"samples/s23/peaks", 500)

        frame = kit.load_peaks(batches="Spring 2026")

        assert frame.complete is False
        assert frame.failures[0].where["sample_item_id"] == "s23"

    def test_every_error_is_a_toolkit_error(self):
        for cls in (
            AccessDenied,
            InvalidRequest,
            NotAuthenticated,
            NotFound,
            Unavailable,
        ):
            assert issubclass(cls, ToolkitError)


class TestSdkErrors:
    def test_the_role_a_write_names_is_kept(self):
        from mascope_ai.errors import sdk_errors
        from mascope_sdk import PermissionDeniedError

        with pytest.raises(AccessDenied) as caught:
            with sdk_errors("move", None, current_role=lambda: ("guest", "WS")):
                raise PermissionDeniedError("no", required_role="editor on both")

        assert caught.value.required_role == "editor on both"
        assert caught.value.current_role == "guest"

    def test_a_read_needs_guest(self):
        from mascope_ai.errors import sdk_errors
        from mascope_sdk import PermissionDeniedError

        with pytest.raises(AccessDenied) as caught:
            with sdk_errors("read", None):
                raise PermissionDeniedError("no")

        assert caught.value.required_role == "guest"
        assert caught.value.current_role is None
        assert "You have no role that allows it" in str(caught.value)


class TestToDict:
    def test_long_lists_are_cut_with_the_count_beside_them(self):
        left = [{"sample_item_id": f"s{i}"} for i in range(50)]

        error = RowCapExceeded(
            900, 500, estimated=False, unit="sample", done=3, left=left, hint="Narrow."
        )

        data = error.to_dict()
        assert data["n_left"] == 50 and len(data["left"]) == LISTED
        assert data["rows"] == 900 and data["cap"] == 500 and data["done"] == 3
        assert "passed 900 rows after 3 sample(s)" in data["message"]
        assert "50 sample(s) were left unread" in data["message"]
        json.dumps(data)
