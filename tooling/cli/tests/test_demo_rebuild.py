"""
Unit tests for the demo rebuild uploader's startup wait + quarantine sweep.

No stack, no network: the upload seam (``_post_raw``), the presence probe
(``converter_presence``) and the on-disk locations are all substituted. What is
under test is the ordering logic that keeps a rebuild from silently ingesting
fewer files than the bundle contains.
"""

import pytest

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
    return streams, raws


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

    uploaded = _rebuild.upload_raw(
        converter_baseline="stale-sid", wait_for_converter=True
    )

    assert uploaded == 2
    assert events[0] == "wait:stale-sid"


def test_upload_does_not_wait_by_default(monkeypatch, bundle):
    """A manual re-upload against a running stack has nothing to wait for."""
    monkeypatch.setattr(
        _rebuild,
        "_wait_for_converter",
        lambda baseline: pytest.fail("should not wait"),
    )
    monkeypatch.setattr(_rebuild, "_post_raw", lambda url, raw: None)

    assert _rebuild.upload_raw() == 2


def test_upload_counts_only_successes(monkeypatch, bundle):
    monkeypatch.setattr(
        _rebuild,
        "_post_raw",
        lambda url, raw: None if raw.name.startswith("Orbion_neg") else "HTTP 500",
    )

    assert _rebuild.upload_raw() == 1


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

    assert _rebuild.sweep_failed_uploads(poll=0, deadline=0.05) == []
