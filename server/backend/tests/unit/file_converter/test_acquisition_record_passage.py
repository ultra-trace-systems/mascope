"""Unit tests: an upload's acquisition record and hash, through the converter.

The server learns what an upload carried when the upload is made, and
registers the file only once the converter has converted it. In between, the
record and the hash ride with the file's context: the event that tells the
converter who uploaded a file, the context it keeps for the file, and the
registration it posts back. Each is one line that passes them on, and a line
that did not would lose every record without a single error.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.file_converter import api as converter_api
from mascope_backend.file_converter import base_processor
from mascope_backend.file_converter.base_processor import BaseFileProcessor
from mascope_backend.file_converter.socket.events import _build_file_context


RECORD = {
    "schema": "mascope-acquisition/1",
    "source_filename": "run_0042.raw",
    "acquisition_id": "0199b6a0-7c00-7000-8000-000000000003",
    "ionization": "NO3",
}
SHA256 = "ab" * 32
USER = SimpleNamespace(id=7, username="machine", role_id=2)


@pytest.mark.asyncio
async def test_the_event_that_registers_a_file_with_the_converter_carries_them(
    monkeypatch,
):
    emitted = AsyncMock()
    monkeypatch.setattr(sample_files_controller.event_emitter, "emit", emitted)

    await sample_files_controller._register_file_with_converter(
        filename="Orbi-Lab2_run_0042.raw",
        user=USER,
        access_token="tok",
        device_id=3,
        instrument_timezone="Europe/Helsinki",
        source_filename="run_0042.raw",
        acquisition=RECORD,
        sha256=SHA256,
    )

    event, payload = emitted.await_args.args
    assert event == "file-converter.auth"
    assert payload["acquisition"] == RECORD
    assert payload["sha256"] == SHA256


def test_the_converters_context_for_a_file_keeps_them():
    context = _build_file_context(
        {
            "filename": "Orbi-Lab2_run_0042.raw",
            "user_id": 7,
            "username": "machine",
            "role_id": 2,
            "access_token": "tok",
            "acquisition": RECORD,
            "sha256": SHA256,
        }
    )

    assert context.acquisition == RECORD
    assert context.sha256 == SHA256


def test_a_context_built_by_an_event_that_carries_neither_has_neither():
    """An event from a backend worker that predates them, mid-update."""
    context = _build_file_context(
        {
            "filename": "x.raw",
            "user_id": 7,
            "username": "machine",
            "role_id": 2,
            "access_token": "tok",
        }
    )

    assert context.acquisition is None
    assert context.sha256 is None


def test_the_processor_hands_them_to_the_registration(monkeypatch):
    registered = MagicMock()
    monkeypatch.setattr(base_processor, "create_sample_file_db_record", registered)
    processor = SimpleNamespace(
        _get_file_context=lambda: SimpleNamespace(
            access_token="tok",
            device_id=3,
            source_filename="run_0042.raw",
            acquisition=RECORD,
            sha256=SHA256,
        )
    )

    BaseFileProcessor._create_db_record(processor, MagicMock(), "ifunc-001")

    assert registered.call_args.kwargs["acquisition"] == RECORD
    assert registered.call_args.kwargs["sha256"] == SHA256


def test_the_registration_the_converter_posts_carries_them(monkeypatch):
    posted = MagicMock(return_value=SimpleNamespace(status_code=201))
    monkeypatch.setattr(converter_api, "_request_with_retry", posted)
    props = SimpleNamespace(
        filename="Orbi-Lab2_2026.01.01-00h00m00s_run_0042",
        utc_offset=0,
        timestamp="2026-01-01T00:00:00",
        length=60.0,
        range=[50.0, 750.0],
        method_file=None,
        mz_calibration=None,
        polarity="-",
        acquisition_timezone=None,
        utc_offset_source="guess",
        instrument_type="orbi",
    )

    converter_api.create_sample_file_db_record(
        props, "ifunc-001", access_token="tok", acquisition=RECORD, sha256=SHA256
    )

    body = posted.call_args.kwargs["json"]
    assert body["acquisition"] == RECORD
    assert body["sha256"] == SHA256
