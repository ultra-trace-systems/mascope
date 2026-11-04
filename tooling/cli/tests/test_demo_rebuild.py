"""
Unit tests for the demo rebuild uploader's startup wait + quarantine sweep.

No stack, no network: the upload seam (``_post_raw``, or the SDK's
``api_post_file_tus`` behind it), the presence probe (``converter_presence``),
the database probe (``_ingested_keys``) and the on-disk locations are all
substituted. What is under test is the ordering logic that keeps a rebuild from
silently ingesting fewer files than the bundle contains - or from reporting a
file it did ingest as missing.
"""

import subprocess

import pytest
from loguru import logger

from mascope_cli.cmd.demo import _rebuild


@pytest.fixture
def no_sleep(monkeypatch):
    """Make the polling loops spin instantly."""
    monkeypatch.setattr(_rebuild.time, "sleep", lambda _: None)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A two-file bundle whose filestreams folder the test drives by hand."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raws = []
    for name in ("Orbion_neg_Br_NoRI_20250811142350.raw", "Orbion_pos_Ur_NoRI_1.raw"):
        path = raw_dir / name
        path.write_bytes(b"raw")
        raws.append(path)

    streams = tmp_path / "filestreams"
    (streams / "failed_files").mkdir(parents=True)

    monkeypatch.setattr(_rebuild, "_raw_files", lambda *a, **kw: raws)
    monkeypatch.setattr(_rebuild, "_filestreams_dir", lambda: streams)
    # An empty demo database and a connected converter, unless a test says
    # otherwise: neither probe may reach for docker.
    monkeypatch.setattr(_rebuild, "_ingested_keys", lambda: set())
    monkeypatch.setattr(_rebuild, "converter_presence", lambda: "fresh-sid")
    return streams, raws


# The demo database stores the name the converter rebuilds from the file's own
# metadata; the acquisition stamp is what it shares with the bundle's name.
_NEG_INGESTED_AS = "Orbion_2025.08.11-14h23m50s_neg_Br_NoRI_20250811142350"


# --- waiting for the file-converter ------------------------------------------


def test_wait_returns_when_the_presence_value_changes(monkeypatch, no_sleep):
    """A converter that connects rewrites the recorded socket id."""
    values = iter(["stale-sid", "stale-sid", "fresh-sid"])
    monkeypatch.setattr(_rebuild, "converter_presence", lambda: next(values))

    assert _rebuild._wait_for_converter("stale-sid") is True


def test_wait_ignores_a_stale_key_until_it_times_out(monkeypatch, no_sleep):
    """A key left behind by a killed run (or another worktree) is not a connect."""
    monkeypatch.setattr(_rebuild, "converter_presence", lambda: "stale-sid")
    monkeypatch.setattr(_rebuild, "_CONVERTER_WAIT_S", 0.01)

    assert _rebuild._wait_for_converter("stale-sid") is False


def test_wait_accepts_a_first_connect_from_an_empty_key(monkeypatch, no_sleep):
    monkeypatch.setattr(_rebuild, "converter_presence", lambda: "fresh-sid")

    assert _rebuild._wait_for_converter("") is True


def test_wait_falls_back_to_a_blind_delay_without_a_probe(monkeypatch):
    """An unreadable presence key still buys the converter some time."""
    slept = []
    monkeypatch.setattr(_rebuild.time, "sleep", slept.append)

    assert _rebuild._wait_for_converter(None) is False
    assert slept == [_rebuild._CONVERTER_BLIND_WAIT_S]


def test_upload_waits_before_the_first_upload(monkeypatch, bundle):
    """The wait happens first - that is the whole point of it."""
    events = []
    monkeypatch.setattr(
        _rebuild,
        "_wait_for_converter",
        lambda baseline: events.append(f"wait:{baseline}") or True,
    )
    monkeypatch.setattr(
        _rebuild, "_post_raw", lambda url, raw: events.append(f"post:{raw.name}")
    )

    missing = _rebuild.upload_raw(
        converter_baseline="stale-sid", wait_for_converter=True
    )

    assert missing == []
    assert events[0] == "wait:stale-sid"


def test_upload_does_not_wait_by_default(monkeypatch, bundle):
    """A manual re-upload against a running stack has nothing to wait for."""
    monkeypatch.setattr(
        _rebuild,
        "_wait_for_converter",
        lambda baseline: pytest.fail("should not wait"),
    )
    monkeypatch.setattr(_rebuild, "_post_raw", lambda url, raw: None)

    assert _rebuild.upload_raw() == []


def test_upload_reports_a_file_it_could_never_post(monkeypatch, bundle, no_sleep):
    """A file the server never received is named, not counted as uploaded."""
    monkeypatch.setattr(
        _rebuild,
        "_post_raw",
        lambda url, raw: None if raw.name.startswith("Orbion_neg") else "HTTP 500",
    )

    assert _rebuild.upload_raw() == ["Orbion_pos_Ur_NoRI_1.raw"]


def test_upload_retries_a_failed_post(monkeypatch, bundle, no_sleep):
    """A timeout against a saturated server costs a retry, not the file."""
    attempts = []

    def _flaky(url, raw):
        attempts.append(raw.name)
        # Fails once, then succeeds - the shape of an upload that timed out
        # client-side while the converter was busy.
        return "timeout" if attempts.count(raw.name) == 1 else None

    monkeypatch.setattr(_rebuild, "_post_raw", _flaky)

    assert _rebuild.upload_raw() == []
    assert len(attempts) == 4  # two files, each posted twice


def test_upload_retry_skips_a_file_that_did_arrive(monkeypatch, bundle, no_sleep):
    """A client-side timeout after the server stored the file is not re-posted."""
    streams, raws = bundle
    posted = []

    def _times_out_but_lands(url, raw):
        posted.append(raw.name)
        # The server stored it despite the client giving up on the response.
        (streams / raw.name).write_bytes(b"raw")
        return "timeout"

    monkeypatch.setattr(_rebuild, "_post_raw", _times_out_but_lands)

    missing = _rebuild.upload_raw()

    assert posted == [raws[0].name, raws[1].name]  # one pass only, no re-post
    assert missing == []


def test_upload_does_not_repost_a_file_the_converter_already_consumed(
    monkeypatch, bundle
):
    """
    Gone from filestreams by the time it is looked for, but in the database.

    The file landed despite the client's error, and the converter ingested and
    deleted it during the pause before the retry. A listing of filestreams no
    longer shows it; re-posting it would have the duplicate quarantined and the
    run report a file it ingested as missing.
    """
    _, raws = bundle
    ingested: set[str] = set()
    posted = []

    def _lands_then_gets_consumed(url, raw):
        posted.append(raw.name)
        return "timeout"

    def _pause(_):
        # The converter works through the landed file while the uploader waits.
        ingested.add(_rebuild.build_bundle._acquisition_key(_NEG_INGESTED_AS))

    monkeypatch.setattr(_rebuild, "_post_raw", _lands_then_gets_consumed)
    monkeypatch.setattr(_rebuild, "_ingested_keys", lambda: set(ingested))
    monkeypatch.setattr(_rebuild.time, "sleep", _pause)

    messages = []
    sink = logger.add(messages.append, level="ERROR", format="{message}")
    try:
        missing = _rebuild.upload_raw()
    finally:
        logger.remove(sink)

    # Only the file that never landed is re-posted, on every later pass.
    assert posted == [raws[0].name, raws[1].name, raws[1].name, raws[1].name]
    assert missing == [raws[1].name]
    # The file that landed counts as uploaded in the summary.
    assert messages[-1].startswith("Uploaded 1/2 raw file(s); 1 never reached")


def test_upload_checks_the_last_pass_for_files_that_landed(
    monkeypatch, bundle, no_sleep
):
    """A file that landed on the last pass is not reported as never arriving."""
    _, raws = bundle
    ingested: set[str] = set()

    def _times_out_but_is_ingested(url, raw):
        ingested.add(_rebuild.build_bundle._acquisition_key(raw.name))
        return "timeout"

    monkeypatch.setattr(_rebuild, "_post_raw", _times_out_but_is_ingested)
    monkeypatch.setattr(_rebuild, "_ingested_keys", lambda: set(ingested))

    assert _rebuild.upload_raw(max_passes=1) == []


def test_upload_holds_a_retry_until_a_converter_is_connected(
    monkeypatch, bundle, no_sleep
):
    """
    The server refuses every upload while no converter is connected (503).

    Nothing of a refused file reaches filestreams, so nothing but the uploader
    can retry it - and retrying before a converter is back is refused again.
    """
    events = []
    presence = iter(["", "", "fresh-sid"])

    def _probe():
        value = next(presence)
        events.append(f"probe:{value}")
        return value

    def _post(url, raw):
        events.append(f"post:{raw.name}")
        return None if any(e == "probe:fresh-sid" for e in events) else "HTTP 503"

    monkeypatch.setattr(_rebuild, "converter_presence", _probe)
    monkeypatch.setattr(_rebuild, "_post_raw", _post)

    assert _rebuild.upload_raw() == []
    first_retry = events.index("probe:fresh-sid") + 1
    assert all(e.startswith("post:") for e in events[first_retry:])
    assert events[:2] == [f"post:{raw.name}" for raw in bundle[1]]
    assert events[2:5] == ["probe:", "probe:", "probe:fresh-sid"]


def test_waiting_for_a_connected_converter_returns_at_once_when_one_is(
    monkeypatch, no_sleep
):
    probes = []
    monkeypatch.setattr(
        _rebuild, "converter_presence", lambda: probes.append(1) or "old-sid"
    )

    _rebuild._await_connected_converter()

    # Any connected converter will do: the server's gate asks no more.
    assert probes == [1]


def test_waiting_for_a_connected_converter_gives_up(monkeypatch, no_sleep):
    """A converter that never connects costs the wait, not the run."""
    monkeypatch.setattr(_rebuild, "converter_presence", lambda: "")
    monkeypatch.setattr(_rebuild, "_CONVERTER_WAIT_S", 0.01)

    _rebuild._await_connected_converter()  # returns


def test_waiting_for_a_connected_converter_skips_without_a_probe(monkeypatch):
    monkeypatch.setattr(_rebuild, "converter_presence", lambda: None)
    monkeypatch.setattr(
        _rebuild.time, "sleep", lambda _: pytest.fail("should not wait blind")
    )

    _rebuild._await_connected_converter()


# --- looking a file up on the server ------------------------------------------


def test_landed_finds_a_pending_or_quarantined_file_without_the_database(
    monkeypatch, bundle
):
    streams, raws = bundle
    monkeypatch.setattr(
        _rebuild, "_ingested_keys", lambda: pytest.fail("folders answer first")
    )
    (streams / raws[0].name).write_bytes(b"raw")
    (streams / "failed_files" / raws[1].name).write_bytes(b"raw")

    assert _rebuild._landed(raws[0].name)
    assert _rebuild._landed(raws[1].name)


def test_landed_matches_the_database_on_the_acquisition_stamp(monkeypatch, bundle):
    """The row carries the converter's rebuilt name, not the uploaded one."""
    _, raws = bundle
    monkeypatch.setattr(
        _rebuild,
        "_ingested_keys",
        lambda: {_rebuild.build_bundle._acquisition_key(_NEG_INGESTED_AS)},
    )

    assert _rebuild._landed(raws[0].name)
    assert not _rebuild._landed(raws[1].name)


def test_landed_without_a_database_says_no(monkeypatch, bundle):
    """An unreadable database is no evidence of arrival; the file is re-posted."""
    _, raws = bundle
    monkeypatch.setattr(_rebuild, "_ingested_keys", lambda: None)

    assert not _rebuild._landed(raws[0].name)


class _Completed:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


class _DbConfig:
    user = "mascope"

    def get_postgres_container_name(self, mode):
        return f"mascope_{mode}_postgres"

    def get_postgres_database_name(self, env):
        return f"mascope_{env}"


@pytest.fixture
def demo_db(monkeypatch):
    """Stand-in for the dev Postgres container; records the psql it is given."""
    calls = []
    result = {"value": _Completed(0, "")}

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return result["value"]

    monkeypatch.setattr(
        _rebuild.runtime,
        "full_config",
        type("C", (), {"backend": type("B", (), {"database": _DbConfig()})()})(),
        raising=False,
    )
    monkeypatch.setattr(_rebuild.subprocess, "run", _run)
    return calls, result


def test_ingested_keys_reads_the_demo_database(demo_db):
    calls, result = demo_db
    result["value"] = _Completed(0, f"{_NEG_INGESTED_AS}\n\n")

    assert _rebuild._ingested_keys() == {"20250811142350"}
    cmd = calls[0]
    assert cmd[:3] == ["docker", "exec", "mascope_dev_postgres"]
    assert cmd[cmd.index("-d") + 1] == "mascope_demo"
    assert cmd[-1] == "SELECT filename FROM sample_file"


def test_ingested_keys_is_none_when_the_query_fails(demo_db):
    _, result = demo_db
    result["value"] = _Completed(1, "")

    assert _rebuild._ingested_keys() is None


def test_ingested_keys_is_none_without_docker(monkeypatch, demo_db):
    def _missing(*a, **kw):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(_rebuild.subprocess, "run", _missing)

    assert _rebuild._ingested_keys() is None


def test_ingested_keys_gives_up_on_a_hung_container(monkeypatch, demo_db):
    def _hangs(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(_rebuild.subprocess, "run", _hangs)

    assert _rebuild._ingested_keys() is None


# --- the upload itself -----------------------------------------------------------


def test_post_raw_uploads_resumably_as_the_file_agent(monkeypatch, tmp_path):
    """The File Agent's own path: TUS, its token, its service name."""
    import mascope_sdk

    raw = tmp_path / "Orbion_neg_Br_NoRI_20250811142350.raw"
    raw.write_bytes(b"raw")
    seen = []
    monkeypatch.setattr(mascope_sdk, "SERVICE_NAME", "mascope_sdk")
    monkeypatch.setattr(
        mascope_sdk,
        "api_post_file_tus",
        lambda **kw: seen.append((kw, mascope_sdk.SERVICE_NAME)),
    )
    monkeypatch.setattr(
        mascope_sdk,
        "api_post_file",
        lambda **kw: pytest.fail("the legacy multipart route is not used"),
    )

    assert _rebuild._post_raw("http://localhost:8090", raw) is None
    assert seen == [
        (
            {
                "url": "http://localhost:8090",
                "access_token": _rebuild._UPLOAD_TOKEN,
                "filepath": str(raw),
            },
            "file-agent",
        )
    ]


def test_post_raw_reports_a_refusal(monkeypatch, tmp_path):
    import mascope_sdk
    from mascope_sdk.exceptions import ServerError

    def _refuse(**kw):
        raise ServerError(
            "File converter service is not available or not running.",
            status_code=503,
        )

    monkeypatch.setattr(mascope_sdk, "api_post_file_tus", _refuse)

    error = _rebuild._post_raw("http://localhost:8090", tmp_path / "a.raw")
    assert "File converter service is not available" in error


# --- clearing a previous run's quarantine -------------------------------------


def test_stale_quarantine_is_cleared_before_uploading(bundle):
    """Leftovers from an earlier rebuild would be re-fed as duplicates."""
    streams, raws = bundle
    (streams / "failed_files" / raws[0].name).write_bytes(b"raw")

    assert _rebuild.clear_stale_streams() == [raws[0].name]
    assert not (streams / "failed_files" / raws[0].name).exists()


def test_stale_pending_files_are_cleared_before_uploading(bundle):
    """A killed run's unconsumed streams would be ingested a second time."""
    streams, raws = bundle
    (streams / raws[1].name).write_bytes(b"raw")

    assert _rebuild.clear_stale_streams() == [raws[1].name]
    assert not (streams / raws[1].name).exists()


def test_clearing_leaves_files_from_another_bundle_alone(bundle):
    streams, _ = bundle
    foreign = streams / "failed_files" / "SomethingElse_20240101000000.raw"
    foreign.write_bytes(b"raw")
    pending = streams / "SomethingElse_20240101000001.raw"
    pending.write_bytes(b"raw")

    assert _rebuild.clear_stale_streams() == []
    assert foreign.exists()
    assert pending.exists()


def test_deferred_upload_clears_stale_streams_before_the_stack_starts(
    monkeypatch, bundle
):
    """Cleared synchronously, while no converter can be ingesting them."""
    streams, raws = bundle
    (streams / raws[0].name).write_bytes(b"raw")
    started = []

    class _Thread:
        def __init__(self, target, name, daemon):
            pass

        def start(self):
            started.append(not (streams / raws[0].name).exists())

    monkeypatch.setattr(_rebuild.threading, "Thread", _Thread)

    _rebuild.upload_raw_deferred()

    assert started == [True]


# --- sweeping the converter's quarantine folder -------------------------------


def test_sweep_reuploads_a_quarantined_file(bundle, monkeypatch, no_sleep):
    """The startup race, self-healed: the file goes back through the uploader."""
    streams, raws = bundle
    (streams / "failed_files" / raws[0].name).write_bytes(b"raw")
    posted = []
    monkeypatch.setattr(_rebuild, "_post_raw", lambda url, raw: posted.append(raw.name))

    remaining = _rebuild.sweep_failed_uploads(poll=0)

    assert posted == [raws[0].name]
    assert remaining == []
    # The quarantined copy is cleared once its replacement is in flight, so the
    # next sweep does not upload the same file again.
    assert not (streams / "failed_files" / raws[0].name).exists()


def test_sweep_gives_up_and_names_a_file_that_keeps_failing(
    bundle, monkeypatch, no_sleep
):
    """A genuinely broken file is reported, not retried forever."""
    streams, raws = bundle
    (streams / "failed_files" / raws[0].name).write_bytes(b"raw")
    attempts = []
    monkeypatch.setattr(
        _rebuild,
        "_post_raw",
        lambda url, raw: attempts.append(raw.name) or "HTTP 500",
    )

    remaining = _rebuild.sweep_failed_uploads(poll=0, max_retries=2)

    assert attempts == [raws[0].name] * 2
    assert remaining == [raws[0].name]


def test_sweep_drops_a_quarantined_duplicate_of_an_ingested_file(
    bundle, monkeypatch, no_sleep
):
    """A second copy failed, but the first was ingested: nothing is missing."""
    streams, raws = bundle
    (streams / "failed_files" / raws[0].name).write_bytes(b"raw")
    monkeypatch.setattr(
        _rebuild,
        "_ingested_keys",
        lambda: {_rebuild.build_bundle._acquisition_key(_NEG_INGESTED_AS)},
    )
    monkeypatch.setattr(
        _rebuild, "_post_raw", lambda url, raw: pytest.fail("should not re-post")
    )

    assert _rebuild.sweep_failed_uploads(poll=0) == []
    assert not (streams / "failed_files" / raws[0].name).exists()


def test_sweep_leaves_files_from_another_run_alone(bundle, monkeypatch, no_sleep):
    """Only the bundle's own files are re-fed, and they are not reported as ours."""
    streams, _ = bundle
    (streams / "failed_files" / "SomethingElse_20240101000000.raw").write_bytes(b"raw")
    monkeypatch.setattr(
        _rebuild, "_post_raw", lambda url, raw: pytest.fail("should not upload")
    )

    assert _rebuild.sweep_failed_uploads(poll=0) == []
    assert (streams / "failed_files" / "SomethingElse_20240101000000.raw").exists()


def test_sweep_waits_out_files_still_being_converted(bundle, monkeypatch, no_sleep):
    """Pending streams keep the sweep alive; it ends when the folder drains."""
    streams, raws = bundle
    pending = streams / raws[0].name
    pending.write_bytes(b"raw")
    monkeypatch.setattr(
        _rebuild, "_post_raw", lambda url, raw: pytest.fail("nothing to retry")
    )

    # Stand in for the converter finishing the file: the stream disappears on
    # the sweep's second look, which is what ends the loop.
    def _drain(_):
        pending.unlink(missing_ok=True)

    monkeypatch.setattr(_rebuild.time, "sleep", _drain)

    assert _rebuild.sweep_failed_uploads(poll=0) == []


def test_sweep_stops_at_its_deadline(bundle, monkeypatch, no_sleep):
    """A converter that stopped making progress does not hang the thread."""
    streams, raws = bundle
    (streams / raws[0].name).write_bytes(b"raw")  # never consumed

    # Giving up on the backstop is not a clean drain: the file is still sitting
    # in filestreams, so it has to be reported rather than counted as ingested.
    assert _rebuild.sweep_failed_uploads(poll=0, deadline=0.05) == [raws[0].name]


def test_sweep_reports_files_that_never_reached_the_server(bundle, no_sleep):
    """An upload that never landed leaves no trace in either folder."""
    _, raws = bundle

    remaining = _rebuild.sweep_failed_uploads(poll=0, never_uploaded=[raws[1].name])

    assert remaining == [raws[1].name]
