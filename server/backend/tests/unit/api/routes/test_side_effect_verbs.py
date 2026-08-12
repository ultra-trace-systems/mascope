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
    "/api/visualization/ion_focus",
}


def test_side_effect_routes_accept_post_only():
    paths = fast.openapi()["paths"]

    missing = SIDE_EFFECT_PATHS - set(paths)
    assert not missing, f"routes not found in the app (path drift?): {missing}"

    for path in SIDE_EFFECT_PATHS:
        methods = set(paths[path])
        assert methods == {"post"}, f"{path} accepts {methods}, expected POST only"
