"""
Exactly these routes answer an API token, and the AI toolkit's reads are among
them.

``api_route(token_access=True)`` is what lets a route answer an
``Authorization: Bearer`` request; without it ``get_enabled_backends`` refuses
one with a 401 before the handler runs, whatever the caller's permissions. A
token is a 360-day credential carrying all of its user's rights, so which
routes take one is a security decision made one route at a time
(``docs/dev/ai_layer.md``, section 7, decision 5). The set is therefore pinned
whole: a route that gains the flag, or loses it, fails here until this list
says so.

``TOOLKIT_READS`` is the subset the AI toolkit's ``find``, ``describe``, loaders
and oracle calls need (the note's section 3.2 and 3.3, and its appendix for
what each one is). Every one of them is a read: a GET, or a POST that computes
an answer without writing (a peak's time series, the aggregate match preview,
the m/z query). The write routes are in ``OTHER_TOKEN_ROUTES``, each by the
client that needs it.

Structural, like ``test_api_route_coverage.py``: it walks whatever routes the
app registers. That a real SDK token reaches the toolkit's reads end to end is
``integration/api/workspace/test_toolkit_token_reads.py``.
"""

from fastapi.routing import APIRoute, iter_route_contexts

from mascope_backend.app.fast import fast


#: (method, path) -> the toolkit operation that reads it.
TOOLKIT_READS = {
    # find: one listing per kind
    ("GET", "/api/workspaces"): "find(kind='workspace')",
    ("GET", "/api/workspaces/{workspace_id}/datasets"): "find(kind='dataset')",
    ("GET", "/api/sample/batches"): "find(kind='batch')",
    ("GET", "/api/samples"): "find(kind='sample')",
    ("GET", "/api/target/collections"): (
        "find(kind='collection'), through the SDK's collections.list()"
    ),
    # describe: one object of each kind, and a batch's collections
    ("GET", "/api/workspaces/{workspace_id}"): "describe(workspace)",
    ("GET", "/api/workspaces/{workspace_id}/datasets/{dataset_id}"): (
        "describe(dataset)"
    ),
    ("GET", "/api/sample/batches/{sample_batch_id}"): "describe(batch)",
    ("GET", "/api/samples/{sample_item_id}"): "describe(sample)",
    ("GET", "/api/target/collections/{target_collection_id}"): ("describe(collection)"),
    ("GET", "/api/target/associations/target_collections_in_sample_batch"): (
        "describe(batch): its associated collections"
    ),
    # the four loaders, and the provenance block every Frame cites
    ("GET", "/api/samples/{sample_item_id}/peaks"): (
        "load_peaks, load_peaks_by_stage, and load_timeseries resolving a formula"
    ),
    ("POST", "/api/samples/{sample_item_id}/peaks/timeseries"): "load_timeseries",
    ("GET", "/api/samples/{sample_item_id}/spectrum"): "load_spectrum",
    ("GET", "/api/provenance"): "the provenance record of every Frame",
    # the oracle
    ("POST", "/api/match/aggregate/sample/{sample_item_id}/compounds"): (
        "match_compounds"
    ),
    ("POST", "/api/cheminfo/mz/query"): "query_by_mz",
    ("GET", "/api/ionization_mechanisms"): "query_by_mz: mechanisms by name",
    # the aggregate match preview: computed, never saved (the note's 1.2)
    ("POST", "/api/match/aggregate/sample/{sample_item_id}"): (
        "describe(sample) match status, plan_match(save=False)"
    ),
    ("POST", "/api/match/aggregate/batch/{sample_batch_id}"): (
        "describe(batch) match status, plan_match(save=False)"
    ),
}

#: Every other route that answers a token, by the client that needs it.
OTHER_TOKEN_ROUTES = {
    # The SDK, beyond the toolkit: MS2, centroids, several spectra, the
    # single-compound match, files, their scans and source data, and the
    # version probe.
    ("GET", "/api/samples/centroids"),
    ("GET", "/api/samples/spectra"),
    ("GET", "/api/samples/{sample_item_id}/ms2/centroids"),
    ("GET", "/api/samples/{sample_item_id}/ms2/ms1_centroids"),
    ("GET", "/api/samples/{sample_item_id}/ms2/summary"),
    ("GET", "/api/samples/{sample_item_id}/ms2/timeseries"),
    ("POST", "/api/match/aggregate/sample/{sample_item_id}/compound"),
    ("GET", "/api/sample/files"),
    ("GET", "/api/sample/files/{sample_file_id}/metadata"),
    ("GET", "/api/sample/files/{sample_file_id}/peaks"),
    ("GET", "/api/sample/files/{sample_file_id}/raw"),
    ("POST", "/api/sample/files/{sample_file_id}/peaks/timeseries"),
    ("GET", "/api/sample/files/{sample_file_id}/scans"),
    ("GET", "/api/sample/files/{sample_file_id}/spectrum"),
    ("GET", "/api/version"),
    # A destructive recompute, token-reachable; the note's 1.3 asks for its
    # own review. The toolkit never calls it outside plan/apply.
    ("POST", "/api/match/rematch/sample/{sample_item_id}"),
    # Peak assignment: the SDK's sample-level reads and the engine's writes.
    ("GET", "/api/peak-assignments/batch/{sample_batch_id}/profile-preview"),
    ("POST", "/api/peak-assignments/calibration/{instrument}/recalibrate"),
    ("GET", "/api/peak-assignments/sample/{sample_item_id}"),
    ("POST", "/api/peak-assignments/sample/{sample_item_id}/assign"),
    (
        "GET",
        "/api/peak-assignments/sample/{sample_item_id}/assignment/{peak_assignment_id}",
    ),
    (
        "PATCH",
        "/api/peak-assignments/sample/{sample_item_id}/assignment/{peak_assignment_id}",
    ),
    (
        "GET",
        "/api/peak-assignments/sample/{sample_item_id}/assignment/"
        "{peak_assignment_id}/alternative-scores",
    ),
    ("POST", "/api/peak-assignments/sample/{sample_item_id}/fit/aggregate"),
    ("POST", "/api/peak-assignments/sample/{sample_item_id}/fit/visualize"),
    ("GET", "/api/peak-assignments/sample/{sample_item_id}/profile-preview"),
    ("GET", "/api/peak-assignments/sample/{sample_item_id}/runs"),
    ("POST", "/api/peak-assignments/sample/{sample_item_id}/runs/import"),
    (
        "DELETE",
        "/api/peak-assignments/sample/{sample_item_id}/runs/{peak_assignment_run_id}",
    ),
    ("GET", "/api/peak-assignments/sample/{sample_item_id}/verifications"),
    ("POST", "/api/peak-assignments/sample/{sample_item_id}/verify"),
    # The batch peak ledger: the SDK's reads (test_batch_peaks_routes_token_access)
    # and the engine's writes.
    ("GET", "/api/batch-peaks/batch/{sample_batch_id}"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/backfill"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/curate"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/export"),
    ("GET", "/api/batch-peaks/batch/{sample_batch_id}/members"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/release-curation"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/retract"),
    ("GET", "/api/batch-peaks/batch/{sample_batch_id}/runs"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/runs/import"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/search-untargeted"),
    ("GET", "/api/batch-peaks/batch/{sample_batch_id}/verdicts"),
    ("POST", "/api/batch-peaks/batch/{sample_batch_id}/verify"),
    # The instrument and acquisition agents: upload, instrument configs, the
    # acquisition dataset and the device token they renew.
    ("POST", "/api/auth/devices/token"),
    ("GET", "/api/datasets/acquisition"),
    ("POST", "/api/instrument_configs"),
    ("GET", "/api/instrument_configs/by_filename/{filename}"),
    ("POST", "/api/sample/files"),
    ("POST", "/api/sample/files/delete"),
    ("POST", "/api/sample/files/upload"),
    # The tus upload protocol, stamped by hand in sample_files_routes.
    ("POST", "/api/sample/files/upload/tus"),
    ("OPTIONS", "/api/sample/files/upload/tus/"),
    ("POST", "/api/sample/files/upload/tus/"),
    ("DELETE", "/api/sample/files/upload/tus/{uuid}"),
    ("HEAD", "/api/sample/files/upload/tus/{uuid}"),
    ("PATCH", "/api/sample/files/upload/tus/{uuid}"),
}


def _token_routes() -> set[tuple[str, str]]:
    """(method, path) of every API route whose endpoint is ``token_access``."""
    return {
        (method, context.path_format)
        for context in iter_route_contexts(fast.routes)
        if isinstance(context.route, APIRoute)
        and getattr(context.endpoint, "token_access", False)
        for method in context.methods or ()
    }


def test_the_two_lists_do_not_overlap():
    assert not TOOLKIT_READS.keys() & OTHER_TOKEN_ROUTES


def test_every_toolkit_read_answers_a_token():
    missing = sorted(set(TOOLKIT_READS) - _token_routes())

    assert not missing, "\n".join(
        f"{method} {path} is not token_access, so {TOOLKIT_READS[method, path]} "
        "answers 401 for every SDK caller"
        for method, path in missing
    )


def test_only_the_known_routes_answer_a_token():
    assert _token_routes() == set(TOOLKIT_READS) | OTHER_TOKEN_ROUTES
