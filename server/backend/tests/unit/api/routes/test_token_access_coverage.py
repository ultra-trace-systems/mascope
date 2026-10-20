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
the m/z query). ``SERIES_READS`` are the two compact per-batch series the
visual digests chart from (``docs/dev/visual_digest.md``, phase 0 and
appendix A), POSTs that compute and write nothing. ``SDK_WRITES`` are the data-organisation writes opened for the
SDK's write wrappers (phase 3 of the note), each after reading its ACL and its
side effects; the destructive ones are marked. Every other route is in
``OTHER_TOKEN_ROUTES``, by the client that needs it.

``NARROWED`` pins the routes whose token access is narrowed to some services
(``api_route(token_access=("file-converter",))``), and to which.

The writes among them, every POST, PUT, PATCH and DELETE, are also declared in
``security/pentest/token_writes.json``, which the pen-test suite's TOKEN-01
compares with what a deployment publishes. The two lists are kept equal here,
so a write opened to tokens is declared to the assessment in the same change.

Structural, like ``test_api_route_coverage.py``: it walks whatever routes the
app registers. That a real SDK token reaches the toolkit's reads end to end is
``integration/api/workspace/test_toolkit_token_reads.py``, and the series reads
``integration/api/workspace/test_series_token_reads.py``.
"""

import json
from itertools import combinations
from pathlib import Path

from fastapi.routing import APIRoute, iter_route_contexts

from mascope_backend.app.fast import fast


#: The token writes the pen-test suite holds every deployment to (TOKEN-01).
PENTEST_TOKEN_WRITES = (
    Path(__file__).resolve().parents[6] / "security/pentest/token_writes.json"
)

#: The methods that change state; the pen-test list declares exactly these.
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


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

#: (method, path) -> the SDK wrapper that reads it: one call per batch for the
#: per-sample series the app's two batch charts draw, instead of one per sample.
SERIES_READS = {
    ("POST", "/api/match/records/ion/series"): "batches.ion_series",
    ("POST", "/api/batch-peaks/records/series"): "batch_peaks.series",
}

#: (method, path) -> the SDK write wrapper that calls it. Each keeps its own
#: ACL, which a token passes as its user's session would.
SDK_WRITES = {
    # datasets
    ("POST", "/api/workspaces/{workspace_id}/datasets"): "datasets.create",
    ("PATCH", "/api/workspaces/{workspace_id}/datasets/{dataset_id}"): (
        "datasets.update"
    ),
    ("POST", "/api/workspaces/{workspace_id}/datasets/{dataset_id}/move"): (
        "datasets.move"
    ),
    # batches
    ("PATCH", "/api/sample/batches/{sample_batch_id}"): "batches.update",
    ("POST", "/api/sample/batches/{sample_batch_id}/copy"): "batches.copy",
    # samples
    ("PATCH", "/api/sample/items/{sample_item_id}"): "samples.update",
    ("POST", "/api/sample/items/move"): "samples.move",
    ("POST", "/api/sample/items/copy"): "samples.copy",
    # collections, and their association with batches (a batches-only update)
    ("POST", "/api/target/collections"): "collections.create",
    ("PATCH", "/api/target/collections/{target_collection_id}"): (
        "collections.update, collections.associate"
    ),
    # the deletes, destructive: each removes the object and what it holds
    ("DELETE", "/api/workspaces/{workspace_id}/datasets/{dataset_id}"): (
        "datasets.delete"
    ),
    ("DELETE", "/api/sample/batches/{sample_batch_id}"): "batches.delete",
    ("DELETE", "/api/target/collections/{target_collection_id}"): (
        "collections.delete"
    ),
    # compounds: the shared list, guarded by the global editor role alone
    ("POST", "/api/target/compounds"): "compounds.add",
    ("PATCH", "/api/target/compounds"): "compounds.update",
    ("DELETE", "/api/target/compounds/{target_compound_id}"): "compounds.remove",
    # saving matches, last: save upserts the aggregate, resave rebuilds it
    ("POST", "/api/match/aggregate/sample/{sample_item_id}/save"): "matching.save",
    ("POST", "/api/match/aggregate/batch/{sample_batch_id}/save"): "matching.save",
    ("POST", "/api/match/aggregate/batch/{sample_batch_id}/resave"): (
        "matching.save(rebuild=True)"
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
    # The file converter's peak-recompute worker, after every recompute: a
    # destructive recompute, so its token alone (NARROWED below).
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


#: (method, path) -> the only services whose token the route answers, for the
#: routes narrowed with ``api_route(token_access=("...",))``. Every route in the
#: lists above and not here answers every allowed service's token.
NARROWED = {
    # Opened for the file converter alone (GitHub #2354, MAS-142): an SDK or
    # agent token holder could otherwise delete a sample's matches and
    # recompute them.
    ("POST", "/api/match/rematch/sample/{sample_item_id}"): frozenset(
        {"file-converter"}
    ),
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


def test_the_lists_do_not_overlap():
    lists = (TOOLKIT_READS, SERIES_READS, SDK_WRITES, OTHER_TOKEN_ROUTES)
    for one, other in combinations(lists, 2):
        assert not set(one) & set(other)


def test_every_toolkit_read_answers_a_token():
    missing = sorted(set(TOOLKIT_READS) - _token_routes())

    assert not missing, "\n".join(
        f"{method} {path} is not token_access, so {TOOLKIT_READS[method, path]} "
        "answers 401 for every SDK caller"
        for method, path in missing
    )


def test_every_series_read_answers_a_token():
    missing = sorted(set(SERIES_READS) - _token_routes())

    assert not missing, "\n".join(
        f"{method} {path} is not token_access, so {SERIES_READS[method, path]} "
        "answers 401 for every SDK caller"
        for method, path in missing
    )


def test_only_the_known_routes_answer_a_token():
    assert _token_routes() == (
        set(TOOLKIT_READS) | set(SERIES_READS) | set(SDK_WRITES) | OTHER_TOKEN_ROUTES
    )


def _narrowed_routes() -> dict[tuple[str, str], frozenset[str]]:
    """(method, path) -> ``token_services`` of every narrowed token route."""
    return {
        (method, context.path_format): context.endpoint.token_services
        for context in iter_route_contexts(fast.routes)
        if isinstance(context.route, APIRoute)
        and getattr(context.endpoint, "token_access", False)
        and getattr(context.endpoint, "token_services", None) is not None
        for method in context.methods or ()
    }


def test_the_narrowed_routes_are_token_routes():
    assert NARROWED.keys() <= set(TOOLKIT_READS) | OTHER_TOKEN_ROUTES


def test_only_the_known_routes_are_narrowed_and_to_their_services():
    # A route widened back to every service, narrowed to another one, or
    # narrowed without a word here fails, as a route gaining the flag does.
    assert _narrowed_routes() == NARROWED


def test_the_pentest_suite_declares_every_token_write():
    declared = {
        (method, path)
        for method, path in json.loads(PENTEST_TOKEN_WRITES.read_text("utf-8"))[
            "writes"
        ]
    }
    writes = {(m, p) for m, p in _token_routes() if m in STATE_CHANGING}

    assert writes == declared, (
        "security/pentest/token_writes.json and the token routes disagree; "
        f"undeclared: {sorted(writes - declared)}, "
        f"declared but closed: {sorted(declared - writes)}"
    )
