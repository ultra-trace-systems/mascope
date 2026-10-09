"""A file that fails before its record leaves no sample directory.

``_process_file`` makes the sample directory first and the database record
last, with the instrument functions and the peak detection between them, and
any of those can fail. A directory left behind has no record pointing at it:
the next upload of the file meets it, and the orphan check - which reads any
answer but a 200 as "no record" - sends that upload to remove the directory
through the server, which is refused in turn where what failed was the
converter's own credential, so the upload ends in a failed delete. So the
directory goes with every failure before the record, and stays with the
record that points at it whatever fails after.
"""

import os
import shutil
from queue import Queue
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import mascope_backend.file_converter.base_processor as base_processor
from mascope_signal.instrument_func.fit import InsufficientPeaksError
from mascope_tofwerk.processor import H5Processor


FILENAME = "TOF-1_2026.01.01-00h00m00s_sample"


@pytest.fixture
def processor(monkeypatch, tmp_path):
    """A processor whose steps are stubs that count their calls, over a sample
    directory under tmp_path."""
    sample_dir = tmp_path / "TOF-1" / "2026.01.01" / FILENAME
    monkeypatch.setattr(
        base_processor, "parse_path_from_item_filename", lambda _name: str(sample_dir)
    )
    monkeypatch.setattr(
        base_processor, "write_empty_peak_timeseries", lambda name: None
    )
    p = H5Processor(socket_client=None, file_queue=Queue(), shutdown_event=Event())
    p.file_to_process = "TOF-1_sample.h5"
    p.calls = []

    def _step(name, result=None):
        def _run(*args, **kwargs):
            p.calls.append(name)
            return result

        return _run

    def _make_directory(props, path):
        p.calls.append("directory")
        os.makedirs(sample_dir)

    monkeypatch.setattr(p, "_create_filestore_directory", _make_directory)
    monkeypatch.setattr(p, "_emit_progress_notification", lambda progress: None)
    monkeypatch.setattr(
        H5Processor, "_is_blank_measurement", property(lambda self: False)
    )
    monkeypatch.setattr(
        p,
        "_create_instrument_config",
        _step("functions", ("instrument-function", None, None)),
    )
    monkeypatch.setattr(p, "_compute_peaks", _step("peaks"))

    def _record(props, function_id):
        # as the real one: the record made is what keeps the directory
        p.calls.append("record")
        p._record_created = True

    monkeypatch.setattr(p, "_create_db_record", _record)
    p.sample_dir = sample_dir
    return p


def _props():
    return SimpleNamespace(filename=FILENAME)


def _raising(exc):
    def _raise(*_args, **_kwargs):
        raise exc

    return _raise


@pytest.mark.parametrize(
    ("step", "failure"),
    [
        # a refused instrument-config creation raises a bare Exception (api.py)
        (
            "_create_instrument_config",
            Exception("Failed to create instrument config: HTTP 401"),
        ),
        # peak detection raises its own kinds
        ("_compute_peaks", ValueError("spectrum coarser than the fit window")),
        (
            "_create_db_record",
            RuntimeError("Failed to create database record: HTTP 401"),
        ),
    ],
)
def test_a_failure_before_the_record_removes_the_directory(
    processor, monkeypatch, step, failure
):
    """The instrument functions, the peak detection or the record: whichever
    fails, and whatever it raises, the directory is gone and the failure is
    the one raised."""
    monkeypatch.setattr(processor, step, _raising(failure))

    with pytest.raises(type(failure), match=str(failure)):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert not processor.sample_dir.exists()


def test_a_processed_file_keeps_its_directory(processor):
    processor._process_file(_props(), "TOF-1_sample.h5")

    assert processor.sample_dir.is_dir()
    assert processor.calls == ["directory", "functions", "peaks", "record"]


def test_a_blank_file_whose_record_fails_leaves_no_directory(processor, monkeypatch):
    monkeypatch.setattr(
        H5Processor, "_is_blank_measurement", property(lambda self: True)
    )
    monkeypatch.setattr(
        processor, "_create_db_record", _raising(RuntimeError("refused"))
    )

    with pytest.raises(RuntimeError, match="refused"):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert not processor.sample_dir.exists()
    assert processor.calls == ["directory"]


def test_a_low_signal_file_whose_record_fails_leaves_no_directory(
    processor, monkeypatch
):
    """Too few peaks for the instrument functions: ingested as a blank, and
    the record's failure removes the directory as on any other route."""
    monkeypatch.setattr(
        processor,
        "_create_instrument_config",
        _raising(InsufficientPeaksError("3 peaks")),
    )
    monkeypatch.setattr(
        processor, "_create_db_record", _raising(RuntimeError("refused"))
    )

    with pytest.raises(RuntimeError, match="refused"):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert not processor.sample_dir.exists()


def test_a_failure_after_the_record_keeps_the_directory(processor, monkeypatch):
    """A record once made keeps its directory whatever fails after it, so a
    statement added after the record can never cost the file its store."""

    def _emit(progress):
        if progress == 100:
            raise RuntimeError("the socket is gone")

    monkeypatch.setattr(processor, "_emit_progress_notification", _emit)

    with pytest.raises(RuntimeError, match="socket"):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert processor.sample_dir.is_dir()


def test_the_real_record_step_is_what_keeps_the_directory(processor, monkeypatch):
    """Through the real ``_create_db_record``, with the server call a stub:
    the record it makes is what keeps the directory from a failure after
    it, so the stubbed record step above stands in for the real one."""
    monkeypatch.setattr(
        processor, "_create_db_record", H5Processor._create_db_record.__get__(processor)
    )
    monkeypatch.setattr(
        base_processor, "create_sample_file_db_record", lambda *a, **k: None
    )
    monkeypatch.setattr(
        processor,
        "_get_file_context",
        lambda: SimpleNamespace(access_token="t", device_id=None),
    )

    def _emit(progress):
        if progress == 100:
            raise RuntimeError("the socket is gone")

    monkeypatch.setattr(processor, "_emit_progress_notification", _emit)

    with pytest.raises(RuntimeError, match="socket"):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert processor.sample_dir.is_dir()


def test_the_real_record_step_refused_removes_the_directory(processor, monkeypatch):
    """Through the real ``_create_db_record`` again, with the server refusing:
    the record is not made, so the directory goes - the one failure the
    converter always did clean up, held here against a flag set before the
    record rather than after it."""
    monkeypatch.setattr(
        processor, "_create_db_record", H5Processor._create_db_record.__get__(processor)
    )
    monkeypatch.setattr(
        base_processor,
        "create_sample_file_db_record",
        _raising(Exception("Failed to create database record! Status code: 401")),
    )
    monkeypatch.setattr(
        processor,
        "_get_file_context",
        lambda: SimpleNamespace(access_token="t", device_id=None),
    )

    with pytest.raises(RuntimeError, match="Status code: 401"):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert not processor.sample_dir.exists()


def test_a_recorded_file_does_not_keep_the_next_files_directory(
    processor, monkeypatch, tmp_path
):
    """A processor thread lives as long as the service and processes file
    after file. The record of one file keeps that file's directory and no
    other's: the next file that fails before its record loses its own."""
    directories = {}

    def _path(name):
        return str(tmp_path / "TOF-1" / "2026.01.01" / name)

    def _make_directory(props, path):
        processor.calls.append("directory")
        directories[props.filename] = tmp_path / "TOF-1" / "2026.01.01" / props.filename
        os.makedirs(directories[props.filename])

    monkeypatch.setattr(base_processor, "parse_path_from_item_filename", _path)
    monkeypatch.setattr(processor, "_create_filestore_directory", _make_directory)

    processor._process_file(SimpleNamespace(filename=FILENAME), "TOF-1_sample.h5")
    monkeypatch.setattr(processor, "_compute_peaks", _raising(ValueError("no peaks")))
    second = "TOF-1_2026.01.01-01h00m00s_sample"
    with pytest.raises(ValueError, match="no peaks"):
        processor._process_file(SimpleNamespace(filename=second), "TOF-1_sample2.h5")

    assert directories[FILENAME].is_dir()
    assert not directories[second].exists()


def test_a_removal_that_fails_is_logged_and_the_failure_still_raised(
    processor, monkeypatch
):
    """The loop reports the failure it was handling; the removal that failed
    is one ERROR line saying the directory is still there."""
    monkeypatch.setattr(processor, "_compute_peaks", _raising(ValueError("no peaks")))
    monkeypatch.setattr(
        base_processor.shutil, "rmtree", _raising(PermissionError("busy"))
    )
    logger = MagicMock()
    monkeypatch.setattr(base_processor, "runtime", SimpleNamespace(logger=logger))

    with pytest.raises(ValueError, match="no peaks"):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert processor.sample_dir.is_dir()
    assert logger.exception.call_count == 1
    assert "Could not remove the sample directory" in logger.exception.call_args.args[0]


def test_a_directory_that_was_already_there_is_not_touched(processor, monkeypatch):
    """A file uploaded again meets its own directory: the directory belongs
    to the record it has, and the upload is refused, not the directory removed."""
    os.makedirs(processor.sample_dir)
    (processor.sample_dir / "data.h5").write_bytes(b"kept")
    monkeypatch.setattr(
        processor,
        "_create_filestore_directory",
        _raising(FileExistsError(str(processor.sample_dir))),
    )
    monkeypatch.setattr(
        processor, "_check_orphan_sample_file_filestore", lambda name: False
    )

    with pytest.raises(FileExistsError, match="already exists"):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert (processor.sample_dir / "data.h5").read_bytes() == b"kept"
    assert processor.calls == []


def test_an_orphan_that_was_already_there_is_removed_and_the_file_processed_once(
    processor, monkeypatch
):
    """The orphan path as before: a directory with no record is removed
    through the server and the file processed again - and once only: the
    retry records the file, and the frame that met the orphan does not run
    the steps a second time over the record just made."""
    os.makedirs(processor.sample_dir)

    def _create(props, path):
        processor.calls.append("directory")
        if processor.calls.count("directory") == 1:
            raise FileExistsError(str(processor.sample_dir))
        assert not processor.sample_dir.exists()  # the orphan is gone by now
        os.makedirs(processor.sample_dir)

    monkeypatch.setattr(processor, "_create_filestore_directory", _create)
    monkeypatch.setattr(
        processor, "_check_orphan_sample_file_filestore", lambda name: True
    )
    monkeypatch.setattr(
        processor,
        "_remove_orphaned_filestore",
        lambda name: shutil.rmtree(processor.sample_dir),
    )

    processor._process_file(_props(), "TOF-1_sample.h5")

    assert processor.calls == ["directory", "directory", "functions", "peaks", "record"]
    assert processor.sample_dir.is_dir()


def test_an_orphan_the_server_does_not_remove_is_tried_twice_then_refused(
    processor, monkeypatch
):
    os.makedirs(processor.sample_dir)
    monkeypatch.setattr(
        processor,
        "_create_filestore_directory",
        _raising(FileExistsError(str(processor.sample_dir))),
    )
    monkeypatch.setattr(
        processor, "_check_orphan_sample_file_filestore", lambda name: True
    )
    removals = []
    monkeypatch.setattr(
        processor, "_remove_orphaned_filestore", lambda name: removals.append(name)
    )

    with pytest.raises(FileExistsError):
        processor._process_file(_props(), "TOF-1_sample.h5")

    assert removals == [FILENAME]
    assert processor.sample_dir.is_dir()
