"""
End-to-end reproducibility tests for the published demo dataset.

Two layers:

1. **Bundle integrity** (`test_cached_bundle_matches_manifest`): asserts the
   cached demo bundle matches its manifest checksums (skips if no bundle is
   cached).
2. **Full pipeline** (`test_pipeline_reproduces_goldens`): uploads the bundle's
   raw files into a rebuild-mode demo stack, waits for the real conversion +
   peak detection + matching pipeline to finish, exports the produced peaks and
   asserts they reproduce the golden outputs within the manifest tolerances.

The pipeline test is opt-in (it boots nothing itself and takes tens of
minutes): start a rebuild-mode demo stack first, then enable it with
``MASCOPE_REPRO_TEST=1``:

    MASCOPE_DEMO_REBUILD=1 docker compose -f docker-compose.demo.yaml up -d
    MASCOPE_REPRO_TEST=1 uv run pytest server/backend/tests/system/reproducibility/ -v

CI runs it from ``.github/workflows/reproducibility.yaml`` (nightly + manual
dispatch). Configuration (all optional):

- ``MASCOPE_REPRO_APP_URL``: app origin (default ``http://127.0.0.1:8080``).
- ``MASCOPE_REPRO_BACKEND_CONTAINER`` / ``MASCOPE_REPRO_POSTGRES_CONTAINER``:
  container names (defaults match ``docker-compose.demo.yaml``).
- ``MASCOPE_REPRO_TIMEOUT``: max seconds to wait for ingestion + matching
  (default 2700).

The pure comparison logic (``compare_peaks``) is shared with ``mascope demo
verify`` and tested without any infrastructure in
``tooling/cli/tests/test_demo_verify.py``; the export seam is
``mascope_backend.db.scripts.export_goldens`` (run inside the backend
container, so capture and verification can never drift). See
``docs/demo_dataset.md`` for the full design.
"""

import json
import os
import subprocess
import time

import pytest

from mascope_cli.cmd.demo import _rebuild, bundles
from mascope_cli.cmd.demo.verify import compare_peaks


APP_URL = os.environ.get("MASCOPE_REPRO_APP_URL", "http://127.0.0.1:8080")
BACKEND_CONTAINER = os.environ.get(
    "MASCOPE_REPRO_BACKEND_CONTAINER", "mascope_demo_backend"
)
POSTGRES_CONTAINER = os.environ.get(
    "MASCOPE_REPRO_POSTGRES_CONTAINER", "mascope_demo_postgres"
)
DB_NAME = os.environ.get("MASCOPE_DB_NAME", "mascope_demo")
DB_USER = os.environ.get("MASCOPE_DB_USER", "mascope_user")
TIMEOUT_S = int(os.environ.get("MASCOPE_REPRO_TIMEOUT", "2700"))
POLL_S = 15
# The matched-peak count must be unchanged across this many consecutive polls
# after all batches settle, so matching that trails the batch status flip
# cannot produce a premature export.
STABLE_POLLS = 3
# Fail early when every counter has been frozen this long without the settle
# condition being met - dropped files never finish, and waiting out the full
# timeout only delays the diagnosis. Generous (10 min) so a slow but live
# aggregation is never mistaken for a stall.
STALL_POLLS = 40
# A quarantined file never comes back on its own, so once the counters stop
# moving there is no point waiting out STALL_POLLS to say so: check the
# converter's failed_files folder after this many frozen polls and fail with the
# names right away.
QUARANTINE_CHECK_POLLS = 2

# Python of the in-image mascope uv tool (same path tooling/demo-init.sh uses).
CONTAINER_PYTHON = "/opt/uv/tools/mascope/bin/python"

requires_repro_stack = pytest.mark.skipif(
    os.environ.get("MASCOPE_REPRO_TEST") != "1",
    reason=(
        "reproducibility run not requested; boot a rebuild-mode demo stack "
        "(MASCOPE_DEMO_REBUILD=1 docker compose -f docker-compose.demo.yaml up -d) "
        "and set MASCOPE_REPRO_TEST=1"
    ),
)


def test_cached_bundle_matches_manifest():
    """If a demo bundle is cached locally, it must match its manifest checksums."""
    if not bundles.is_cached():
        pytest.skip("no demo bundle cached; run 'mascope demo fetch' first")
    problems = bundles.verify_manifest()
    assert problems == [], f"bundle integrity issues: {problems}"


@requires_repro_stack
def test_pipeline_reproduces_goldens():
    """
    Re-run the full ingestion + matching pipeline from the bundle's raw files
    and assert the produced peaks reproduce the golden outputs within the
    manifest tolerances.

    Steps: fetch + integrity-check the bundle, pin the raw reader version,
    upload every ``raw/`` file through the real upload endpoint (as the File
    Agent does), wait for conversion + peak detection + matching to settle,
    export the produced peaks via the shared golden-export seam, and compare
    against ``expected/peaks.parquet`` on the stable key
    ``(filename, target_isotope_id)``.
    """
    import pandas as pd

    # --- Resolve the bundle (raw inputs + goldens); fetch it if needed ------
    if not bundles.is_cached():
        from mascope_cli.cmd.demo import _fetch

        _fetch.fetch()
    problems = bundles.verify_manifest()
    assert problems == [], f"bundle integrity issues: {problems}"

    bundle_root = bundles.bundle_dir()
    manifest = bundles.load_manifest()

    expected_rel = manifest.get("expected", {}).get("peaks")
    assert expected_rel, (
        "bundle manifest has no expected/peaks goldens; build them with "
        "'mascope demo snapshot --update'"
    )

    _assert_raw_reader_pin(manifest)

    # --- Upload the raw files through the real upload endpoint --------------
    raws = sorted((bundle_root / "raw").glob("*.raw"))
    assert raws, f"bundle has no raw files at {bundle_root / 'raw'}"
    _upload_raws(raws)

    # --- Wait for conversion + peak detection + matching to finish ----------
    _wait_for_pipeline(n_files=len(raws))

    # --- Export the produced peaks and compare against the goldens ----------
    actual = pd.DataFrame(_export_actual_peaks())
    expected = pd.read_parquet(bundle_root / expected_rel)
    diffs = compare_peaks(
        expected=expected, actual=actual, tolerances=manifest.get("tolerances")
    )

    shown = "\n".join(f"  - {d}" for d in diffs[:50])
    if len(diffs) > 50:
        shown += f"\n  ... and {len(diffs) - 50} more"
    assert diffs == [], (
        f"pipeline did not reproduce the goldens ({len(diffs)} difference(s), "
        f"{len(actual)} produced vs {len(expected)} expected peaks):\n{shown}"
    )


# --- Helpers -----------------------------------------------------------------


def _docker(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """
    Run a docker CLI command, captured; the caller owns returncode checks.

    Container output is UTF-8 (loguru emoji included) regardless of the host
    locale, so decode explicitly - Windows' default charmap codec chokes on it.
    """
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _psql(sql: str) -> str:
    """
    Run one SQL statement in the demo Postgres container, return trimmed stdout.

    Uses the container-local socket (trust auth in the official postgres
    image), so no password or published port is needed.
    """
    res = _docker(
        "exec", POSTGRES_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-tAc", sql
    )
    assert res.returncode == 0, (
        f"psql failed in {POSTGRES_CONTAINER} for {sql!r}:\n{res.stderr.strip()}"
    )
    return res.stdout.strip()


def _upload_raws(raws: list) -> None:
    """
    Upload raw files to the real upload endpoint, as the File Agent does
    (same fixed file-agent token + service headers as ``mascope demo
    --rebuild``), robustly against a server saturated by ingestion.

    Uploads run while the converter is already crunching earlier files, so
    individual requests can be slow or even time out client-side after the
    server has stored the file. Failed files are retried in later passes, and
    a 400 "already exists" answer counts as success (the earlier attempt won).
    """
    import requests

    url = f"{APP_URL}/api/{_rebuild._UPLOAD_PATH}"
    headers = {
        "Authorization": f"Bearer {_rebuild._UPLOAD_TOKEN}",
        "X-Service-Name": _rebuild._UPLOAD_SERVICE,
    }

    def _post_once(raw) -> tuple[bool, str]:
        try:
            with open(raw, "rb") as fh:
                resp = requests.post(
                    url, files=[("files", fh)], headers=headers, timeout=(10, 300)
                )
        except requests.RequestException as e:
            return False, f"{type(e).__name__}: {e}"
        if resp.ok:
            return True, ""
        if resp.status_code == 400 and "exist" in resp.text.lower():
            return True, ""  # an earlier (timed-out) attempt landed server-side
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"

    failed: dict = {raw: "" for raw in raws}
    for attempt in range(3):
        if not failed:
            break
        if attempt:
            time.sleep(30)  # let the converter catch up before retrying
        for raw in list(failed):
            ok, why = _post_once(raw)
            if ok:
                failed.pop(raw)
            else:
                failed[raw] = why

    assert not failed, (
        f"failed to upload {len(failed)}/{len(raws)} raw file(s) to {url} "
        f"after 3 attempts: "
        + "; ".join(f"{raw.name}: {why}" for raw, why in failed.items())
    )


def _wait_for_pipeline(n_files: int) -> None:
    """
    Poll the demo database until ingestion + matching settle, or fail.

    Done when: every uploaded file has produced sample items, at least one
    batch exists, no batch is processing, any batch failure aborts
    immediately, and the matched-peak count has been stable for
    ``STABLE_POLLS`` consecutive polls (matching can trail the batch status).
    """
    deadline = time.monotonic() + TIMEOUT_S
    last_matched = -1
    stable = 0
    last_state: tuple | None = None
    stalled = 0

    while time.monotonic() < deadline:
        failed = int(_psql("SELECT count(*) FROM sample_batch WHERE status = 'failed'"))
        if failed:
            statuses = _psql(
                "SELECT status || ': ' || count(*) FROM sample_batch GROUP BY status"
            )
            pytest.fail(
                f"{failed} sample batch(es) failed during ingestion; "
                f"batch statuses:\n{statuses}\n"
                f"(inspect: docker logs mascope_demo_file_converter)"
            )

        files_done = int(
            _psql("SELECT count(DISTINCT sample_file_id) FROM sample_item")
        )
        batches = int(_psql("SELECT count(*) FROM sample_batch"))
        processing = int(
            _psql("SELECT count(*) FROM sample_batch WHERE status = 'processing'")
        )
        matched = int(_psql("SELECT count(*) FROM match_isotope WHERE match_score > 0"))

        if files_done >= n_files and batches > 0 and processing == 0 and matched > 0:
            stable = stable + 1 if matched == last_matched else 1
            if stable >= STABLE_POLLS:
                return
        else:
            stable = 0

        # An unfinished pipeline whose counters have all stopped moving will
        # not finish by waiting: files were dropped somewhere. Fail early with
        # the stage breakdown instead of idling to the full timeout.
        state = (files_done, batches, processing, matched)
        stalled = stalled + 1 if state == last_state else 0
        if stalled == QUARANTINE_CHECK_POLLS:
            _fail_on_quarantined_files(n_files, files_done, batches, matched)
        if stalled >= STALL_POLLS:
            pytest.fail(
                f"pipeline stalled with no progress for {STALL_POLLS * POLL_S}s: "
                f"{files_done}/{n_files} files processed, {batches} batch(es), "
                f"{processing} still processing, {matched} matched peak(s)\n"
                + _diagnose_missing_files()
            )
        last_state = state
        last_matched = matched
        time.sleep(POLL_S)

    pytest.fail(
        f"pipeline did not settle within {TIMEOUT_S}s: "
        f"{files_done}/{n_files} files processed, {batches} batch(es), "
        f"{processing} still processing, {matched} matched peak(s)\n"
        + _diagnose_missing_files()
    )


def _fail_on_quarantined_files(
    n_files: int, files_done: int, batches: int, matched: int
) -> None:
    """
    Fail the test if the converter quarantined any of the uploaded files.

    Quarantined files produce no sample items, ever, so a pipeline that has
    stopped moving with files in ``failed_files`` is finished - it just cannot
    say so. Naming them (and the usual cause) beats the generic stall message
    ten minutes later.

    :param n_files: Number of raw files uploaded.
    :param files_done: Files that have produced sample items so far.
    :param batches: Sample batches created so far.
    :param matched: Matched peaks so far.
    """
    quarantined = _stream_folders().get("failed", [])
    if not quarantined:
        return
    shown = ", ".join(quarantined[:20]) + (" ..." if len(quarantined) > 20 else "")
    pytest.fail(
        f"the file converter quarantined {len(quarantined)} of {n_files} uploaded "
        f"file(s), which will never produce sample items: {shown}\n"
        "A file quarantined shortly after the stack came up usually means its "
        "upload raced the converter's socket connection: the file context the "
        "upload endpoint emitted reached nobody, and processing failed with "
        "'not registered in file converter service'. Re-upload those files once "
        "the stack is idle.\n"
        f"{files_done}/{n_files} files processed, {batches} batch(es), "
        f"{matched} matched peak(s)\n" + _diagnose_missing_files()
    )


def _stream_folders() -> dict[str, list[str]]:
    """
    List the converter's pending and quarantined stream files.

    The filestreams volume is shared with the backend container, so the listing
    runs there. Diagnostic only - an unreadable volume yields empty lists rather
    than an error, so a probe failure never masks the real assertion.

    :return: ``{"pending": [...], "failed": [...]}`` - stream files still
             waiting in ``filestreams``, and those moved to ``failed_files``.
    """
    probe = (
        "import json, os\n"
        "from mascope_backend.runtime import runtime\n"
        "d = runtime.config.filestreams\n"
        "def ls(p):\n"
        "    if not os.path.isdir(p):\n"
        "        return []\n"
        "    # files only - subfolders like failed_files are not streams\n"
        "    return sorted(\n"
        "        f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))\n"
        "    )\n"
        "print(json.dumps({'pending': ls(d),"
        " 'failed': ls(os.path.join(d, 'failed_files'))}))\n"
    )
    try:
        res = _docker("exec", BACKEND_CONTAINER, CONTAINER_PYTHON, "-c", probe)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001 - diagnostics must never mask the failure
        pass
    return {}


def _diagnose_missing_files() -> str:
    """
    Best-effort breakdown of where files fell out of the pipeline.

    Attributes the shortfall to a stage: raw streams never picked up (still in
    the filestreams folder), streams quarantined by the converter
    (``failed_files``), or files converted whose auto-processing never
    produced sample items. Diagnostic only - never raises.
    """
    lines = []
    try:
        n_sample_files = _psql("SELECT count(*) FROM sample_file")
        lines.append(f"sample_file rows (files converted): {n_sample_files}")
        no_items = _psql(
            "SELECT sf.filename FROM sample_file sf "
            "LEFT JOIN sample_item si ON si.sample_file_id = sf.sample_file_id "
            "WHERE si.sample_file_id IS NULL ORDER BY sf.filename"
        )
        if no_items:
            names = no_items.splitlines()
            shown = ", ".join(names[:20]) + (" ..." if len(names) > 20 else "")
            lines.append(
                f"{len(names)} file(s) converted but auto-processing produced "
                f"no sample items: {shown}"
            )
        folders = _stream_folders()
        if not folders:
            lines.append("(filestreams listing unavailable)")
        for key, label in (
            ("pending", "stream file(s) never picked up by the converter"),
            ("failed", "stream file(s) quarantined in failed_files"),
        ):
            if folders.get(key):
                lines.append(
                    f"{len(folders[key])} {label}: " + ", ".join(folders[key][:20])
                )
    except Exception as e:  # noqa: BLE001 - diagnostics must never mask the failure
        lines.append(f"(diagnostics unavailable: {e})")
    return "\n".join(lines)


def _export_actual_peaks() -> list[dict]:
    """
    Export the produced peaks from inside the backend container.

    Runs the shared golden-export seam (`export_goldens --json`) in the
    backend container - the same query `mascope demo snapshot --update` uses
    to capture the goldens - and reads the JSON back out.
    """
    remote_path = "/tmp/repro_actual_peaks.json"
    res = _docker(
        "exec",
        BACKEND_CONTAINER,
        CONTAINER_PYTHON,
        "-m",
        "mascope_backend.db.scripts.export_goldens",
        "--json",
        remote_path,
    )
    assert res.returncode == 0, (
        f"golden export failed in {BACKEND_CONTAINER}:\n{res.stderr[-2000:]}"
    )
    res = _docker("exec", BACKEND_CONTAINER, "cat", remote_path)
    assert res.returncode == 0, f"could not read {remote_path}: {res.stderr.strip()}"
    rows = json.loads(res.stdout)
    assert rows, "golden export returned no matched peaks"
    return rows


def _assert_raw_reader_pin(manifest: dict) -> None:
    """
    Pin the raw reader to the version that produced the goldens.

    The manifest records ``produced_with.opentfraw_version``; a stack running a
    different reader version must fail loudly rather than produce a confusing
    peak diff. Skipped when the manifest does not declare a version.
    """
    want = (manifest.get("produced_with") or {}).get("opentfraw_version")
    if not want:
        return
    # The manifest records a requirement-style pin ("opentfraw==1.2.0");
    # compare on the version part only.
    want = want.split("==")[-1].strip()

    probe = (
        "import importlib.metadata as m\n"
        "for d in ('mascope-opentfraw', 'opentfraw'):\n"
        "    try:\n"
        "        print(m.version(d))\n"
        "        break\n"
        "    except m.PackageNotFoundError:\n"
        "        pass\n"
    )
    res = _docker("exec", BACKEND_CONTAINER, CONTAINER_PYTHON, "-c", probe)
    assert res.returncode == 0, f"reader version probe failed: {res.stderr.strip()}"
    have = res.stdout.strip()
    assert have == want, (
        f"raw reader version mismatch: goldens were produced with opentfraw "
        f"{want}, the stack runs {have or '(not installed)'} - regenerate the "
        f"goldens ('mascope demo snapshot --update') or align the reader version"
    )
