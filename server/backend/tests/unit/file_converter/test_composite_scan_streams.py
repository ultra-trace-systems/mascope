"""Who decides whether a file's peaks are detected per scan stream.

A raw Orbitrap file whose method runs more than one experiment in a polarity
can have its peaks detected per experiment (``mascope_signal.peak``). The
deployment's ``[backend] composite_scan_streams`` decides that, once, when
the file is first converted. A rebuild of the store decides nothing: it
goes by the decision the file's ``.props`` records, because the file's
samples are defined against the store as it was decided.

So the converter hands the setting to peak detection, and the worker that
rebuilds a store hands it nothing.
"""

from queue import Queue
from threading import Event
from unittest.mock import MagicMock

import pytest

from mascope_backend.file_converter import base_processor, peak_recompute_worker
from mascope_backend.file_converter.base_processor import composites_scan_streams
from mascope_backend.file_converter.peak_recompute_worker import PeakRecomputeWorker
from mascope_runtime.config import BackendConfig
from mascope_thermo.processor import RawProcessor


FILENAME = "ORBI-1_2026.10.05-12h00m00s_run"


@pytest.fixture
def detected(monkeypatch):
    """Record how each module asks for peak detection, without running it."""
    calls = []

    def compute_peaks(filename, instrument_functions, *args, **kwargs):
        calls.append({"filename": filename, "args": args, **kwargs})

    monkeypatch.setattr(base_processor, "compute_peaks", compute_peaks)
    monkeypatch.setattr(peak_recompute_worker, "compute_peaks", compute_peaks)
    return calls


def _setting(monkeypatch, value):
    monkeypatch.setattr(
        base_processor.runtime.full_config.backend, "composite_scan_streams", value
    )


def test_no_deployment_detects_per_stream_unless_it_says_so():
    """What keeps every file processed as before: the shipped default. Read
    off the model, not off this machine's config, which a developer may have
    set either way."""
    assert BackendConfig.model_fields["composite_scan_streams"].default is False


def test_the_setting_is_read_from_the_backend_config(monkeypatch):
    _setting(monkeypatch, True)
    assert composites_scan_streams() is True

    _setting(monkeypatch, False)
    assert composites_scan_streams() is False


def test_a_config_with_no_backend_section_asks_for_nothing(monkeypatch):
    """The converter reads another module's section, which a pared-down
    config may not carry."""
    monkeypatch.setattr(base_processor.runtime._full_config, "backend", None)

    assert composites_scan_streams() is False


@pytest.mark.parametrize("setting", [True, False])
def test_the_converter_hands_the_setting_to_peak_detection(
    monkeypatch, detected, setting
):
    _setting(monkeypatch, setting)
    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )

    processor._compute_peaks(FILENAME, ("peak shape", "resolution function"))

    (call,) = detected
    assert call["filename"] == FILENAME
    # Explicit either way: the first conversion is what decides, so it never
    # leaves the question to what a .props it has just written happens to say.
    assert call["per_stream"] is setting


@pytest.mark.parametrize("setting", [True, False])
def test_a_rebuild_does_not_decide_what_a_store_is(monkeypatch, detected, setting):
    """Whatever the deployment is set to now. Passing the setting here would
    take a pooled store apart by stream, or pool a per-stream one, under
    samples that already exist."""
    _setting(monkeypatch, setting)
    monkeypatch.setattr(
        peak_recompute_worker, "is_blank_sample_file", lambda *args: False
    )
    monkeypatch.setattr(
        peak_recompute_worker,
        "fetch_instrument_functions",
        lambda *args: ("peak shape", "resolution function"),
    )
    monkeypatch.setattr(
        PeakRecomputeWorker, "_check_rebuilt_store", staticmethod(lambda filename: None)
    )
    worker = PeakRecomputeWorker(
        socket_client=MagicMock(),
        peak_recompute_queue=Queue(),
        peak_guard=MagicMock(),
        shutdown_event=Event(),
    )

    worker._process_request(
        {"filename": FILENAME, "access_token": "token", "sample_file_id": "file"}
    )

    (call,) = detected
    assert call["filename"] == FILENAME
    assert call.get("per_stream") is None
    assert call["args"] == ()
