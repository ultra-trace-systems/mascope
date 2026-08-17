"""
Pin the HTTP verb of the enqueueing/export routes.

These routes change state (spawn background work, mint service tokens,
write temp files), so they must not be reachable via GET: the
SameSite=lax auth cookie rides on cross-site top-level GET navigations,
which would let a crafted link trigger the work with a logged-in user's
ambient credentials.

The app registers routers lazily, so the routes are read from the
generated OpenAPI schema rather than by walking ``fast.routes``.
"""

from mascope_backend.app.fast import fast


SIDE_EFFECT_PATHS = {
    "/api/sample/files/{sample_file_id}/peaks/compute",
    "/api/sample/items/{sample_item_id}/export_peak_data",
    "/api/sample/batches/{sample_batch_id}/export_peaks",
    "/api/sample/batches/{sample_batch_id}/export/spreadsheet",
    "/api/sample/batches/{sample_batch_id}/peaks",
    "/api/visualization/ion_focus",
}


def test_side_effect_routes_accept_post_only():
    paths = fast.openapi()["paths"]

    missing = SIDE_EFFECT_PATHS - set(paths)
    assert not missing, f"routes not found in the app (path drift?): {missing}"

    for path in SIDE_EFFECT_PATHS:
        methods = set(paths[path])
        assert methods == {"post"}, f"{path} accepts {methods}, expected POST only"


def test_no_get_route_enqueues_background_work():
    """No GET operation may declare a 202 response.

    202 Accepted is the app's convention for a route that enqueues background
    work, so a GET returning it is a state-changing route a cross-site GET
    navigation could trigger with the SameSite=lax cookie. Deriving the check
    from the schema catches such a route even when nobody remembers to add it
    to SIDE_EFFECT_PATHS above - the allowlist alone cannot self-expand.
    """
    paths = fast.openapi()["paths"]
    offenders = [
        path
        for path, operations in paths.items()
        if "get" in operations and "202" in (operations["get"].get("responses") or {})
    ]
    assert not offenders, f"GET routes enqueueing work (should be POST): {offenders}"
