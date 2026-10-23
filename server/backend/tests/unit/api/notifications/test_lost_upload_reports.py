"""
Uploads the file converter could not store: the parts that need no database.

Which file in ``failed_files`` has a record, read off the names a reader
stores files under; the converter's ``upload_lost`` event, which acts for no
user; and the server start that looks through the folder. The digests
themselves are covered against a real database in
``tests/integration/api/notifications/test_lost_uploads.py``.
"""

from unittest.mock import AsyncMock

import pytest

from mascope_backend.api.new.notifications import lost_uploads
from mascope_backend.app import startup
from mascope_backend.db.admin.sample_file import report_unstored_failed_files as scan
from mascope_backend.socket.events.handlers.file_converter import conversion


class TestHasRecord:
    @pytest.mark.parametrize(
        ("name", "stored"),
        [
            # A TOF file whose name carries its time is stored as it is
            ("TOF-1_2026.09.01-12h00m00s_a.h5", "TOF-1_2026.09.01-12h00m00s_a"),
            # The Thermo reader puts the time after the instrument segment
            ("Orbi-1_a.raw", "Orbi-1_2026.09.01-12h00m00s_a"),
            # and reads spaces as underscores
            ("Orbi-1_a b.raw", "Orbi-1_2026.09.01-12h00m00s_a_b"),
            # The TOF reader adds the time to a name with no underscore
            ("TOF1.h5", "TOF1_2026.09.01-12h00m00s"),
            # and stores a name that has its own time as it is
            (
                "Orbi-1_2026.09.01-12h00m00s_a.raw",
                "Orbi-1_2026.09.01-13h00m00s_2026.09.01-12h00m00s_a",
            ),
        ],
    )
    def test_a_stored_name_of_the_file_is_its_record(self, name, stored):
        assert scan.has_record(name, scan.record_keys({"Orbi-1_other", stored}))

    @pytest.mark.parametrize(
        ("name", "stored"),
        [
            ("Orbi-1_a.raw", "Orbi-1_2026.09.01-12h00m00s_ab"),
            ("Orbi-1_a.raw", "Orbi-1_a_2"),
            ("Orbi-1_a.raw", "Orbi-2_2026.09.01-12h00m00s_a"),
            # A time is taken out only where it is a segment of its own
            ("Orbi-1a.raw", "Orbi-1_2026.09.01-12h00m00sa"),
        ],
    )
    def test_another_files_name_is_not(self, name, stored):
        assert not scan.has_record(name, scan.record_keys({stored}))


def test_only_the_raw_files_in_the_folder_are_listed(tmp_path):
    for name in ("b.RAW", "a.h5", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "c.raw").mkdir()

    assert scan.list_failed_files(str(tmp_path)) == ["a.h5", "b.RAW"]
    assert scan.list_failed_files(str(tmp_path / "missing")) == []


@pytest.mark.asyncio
async def test_a_scan_that_fails_is_logged_not_raised(monkeypatch, tmp_path):
    (tmp_path / "Orbi-1_a.raw").write_bytes(b"x")

    def _boom():
        raise RuntimeError("database down")

    monkeypatch.setattr(scan, "async_session", _boom)

    result = await scan.report_unstored_failed_files(str(tmp_path))

    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_a_start_looks_through_failed_files(monkeypatch, tmp_path):
    for step in ("gc_filestore", "ensure_deployment_id"):
        monkeypatch.setattr(startup, step, lambda: None)
    for step in (
        "configure_database_engine",
        "dispose_engine",
        "reset_stuck_processing_batches",
        "reset_running_peak_assignment_runs",
        "reset_running_batch_peak_runs",
        "reset_interrupted_processing",
        "purge_read_notifications",
        "create_acquisition_datasets",
        "ensure_system_ionization",
    ):
        monkeypatch.setattr(startup, step, AsyncMock())
    report = AsyncMock()
    monkeypatch.setattr(startup, "report_unstored_failed_files", report)
    monkeypatch.setattr(startup.runtime.env, "path", lambda *_: str(tmp_path / "t"))

    await startup.init_main_process()

    report.assert_awaited_once_with()


class TestUploadLostEvent:
    @pytest.fixture
    def notify(self, monkeypatch) -> AsyncMock:
        notify = AsyncMock()
        monkeypatch.setattr(conversion, "notify_lost_uploads", notify)
        return notify

    @pytest.mark.asyncio
    async def test_it_names_the_file_to_the_admins(self, notify):
        await conversion.upload_lost("sid", {"filename": "a.raw", "instrument": "X"})

        notify.assert_awaited_once_with(
            [("a.raw", "X", lost_uploads.UNREGISTERED_DETAIL)]
        )

    @pytest.mark.asyncio
    async def test_it_needs_no_uploaders_credential(self, notify):
        # The very thing the converter lacks for such a file
        await conversion.upload_lost("sid", {"filename": "a.raw"})

        notify.assert_awaited_once_with(
            [("a.raw", None, lost_uploads.UNREGISTERED_DETAIL)]
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [None, [], {}, {"filename": ""}, {"filename": 3}])
    async def test_an_event_naming_no_file_is_ignored(self, notify, data):
        await conversion.upload_lost("sid", data)

        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_instrument_that_is_not_a_name_is_left_out(self, notify):
        await conversion.upload_lost("sid", {"filename": "a.raw", "instrument": {}})

        assert notify.await_args.args[0][0][1] is None
