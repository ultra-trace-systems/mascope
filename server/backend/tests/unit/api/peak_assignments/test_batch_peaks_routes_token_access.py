"""Every batch-ledger route the SDK calls must accept a bearer token.

``api_route(token_access=True)`` is what lets a route answer an
``Authorization: Bearer`` request. Without it the auth layer refuses one with a
401 before the handler runs, whatever the caller's permissions - so a route the
SDK reads has to carry the flag, and the writes beside it already do.

Pinned here, on the attribute the decorator stamps, rather than left to an
integration test, because the failure is invisible from the browser: the app
authenticates with a cookie and takes the same routes happily, and only a token
client ever sees the 401. The SDK's own suite cannot see it either - it stubs
the HTTP seam - and this is the second time the shape has bitten: see
``libraries/sdk/tests/test_samples_peaks.py``, where a listing that was not
declared ``token_access`` is the reason peak names are resolved server-side.

Routes the SDK does not call are deliberately absent: the series, counterpart,
anchor-context and sample-status reads are the app's, and stay cookie-only
until something needs otherwise.
"""

import pytest

from mascope_backend.api.new.peak_assignments import batch_peaks_routes


#: Route function -> the SDK surface that reaches it, for the failure message.
_SDK_ROUTES = {
    "get_batch_peak_ledger_route": "mascope.batch_peaks.list()",
    "get_batch_peak_verdicts_route": "mascope.batch_peaks.verdicts()",
    "get_batch_peak_members_route": (
        "mascope.batch_peaks.members() and mascope.load_batch_ledger()"
    ),
    "get_batch_peak_runs_route": (
        "mascope.batch_peaks.runs(), and the poll import_run(wait=True) makes"
    ),
    "import_batch_run_route": "mascope.batch_peaks.import_run()",
}


@pytest.mark.parametrize(("route_name", "sdk_call"), sorted(_SDK_ROUTES.items()))
def test_a_route_the_sdk_calls_accepts_a_bearer_token(route_name, sdk_call):
    route = getattr(batch_peaks_routes, route_name)

    assert getattr(route, "token_access", False) is True, (
        f"{route_name} is not declared token_access, so {sdk_call} answers 401 "
        "for every caller no matter their permissions."
    )
