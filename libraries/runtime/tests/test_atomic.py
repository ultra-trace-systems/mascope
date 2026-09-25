"""
Tests for the atomic JSON write shared by the runtime state and ``.props``.

Three details are the whole reason this is one helper rather than a line in
each writer, so each has a test: the temporary is unique per call and not per
process, the content reaches the disk before the rename, and a Windows
sharing lock on the destination is waited out instead of losing the write.
"""

import json
import os
import stat
import threading
import time
from unittest.mock import patch

import pytest

from mascope_runtime.atomic import replace_with_retry, write_json


def _read(path):
    with open(path, "r") as f:
        return json.load(f)


def _temps(directory, target=".props"):
    return [n for n in os.listdir(directory) if n != os.path.basename(target)]


@pytest.fixture
def props(tmp_path):
    """An existing file with something worth not losing in it."""
    path = tmp_path / ".props"
    path.write_text(json.dumps({"mz_calibration": {"a": 1}}), encoding="utf-8")
    return str(path)


def test_it_writes_the_content(props):
    write_json(props, {"mz_calibration": {"a": 2}}, indent=4)

    assert _read(props) == {"mz_calibration": {"a": 2}}
    assert _temps(os.path.dirname(props)) == []


def test_a_failed_write_leaves_the_previous_file_intact(props):
    """The reason for the rename: an in-place write truncates first."""
    before = _read(props)

    with patch("mascope_runtime.atomic.json.dump", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            write_json(props, {"mz_calibration": {"a": 2}})

    assert _read(props) == before
    assert _temps(os.path.dirname(props)) == []


def test_a_failed_rename_leaves_the_previous_file_and_no_temporary(props):
    before = _read(props)

    with patch("mascope_runtime.atomic.replace_with_retry", side_effect=OSError("no")):
        with pytest.raises(OSError):
            write_json(props, {"mz_calibration": {"a": 2}})

    assert _read(props) == before
    assert _temps(os.path.dirname(props)) == []


def test_each_write_gets_its_own_temporary(props):
    """Per call, not per process.

    Two threads of one process naming the temporary after the pid would share
    it, and one thread's rename would publish a file the other was still
    filling - the torn file the rename exists to prevent.
    """
    seen = []

    def capture(temp_path, path, timeout=None):
        seen.append(temp_path)
        os.replace(temp_path, path)

    with patch("mascope_runtime.atomic.replace_with_retry", side_effect=capture):
        write_json(props, {"n": 1})
        write_json(props, {"n": 2})

    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_the_content_is_flushed_to_disk_before_the_rename(props):
    """Without the fsync the rename can land before the bytes do."""
    order = []

    real_fsync = os.fsync

    def note_fsync(fd):
        order.append("fsync")
        return real_fsync(fd)

    def note_replace(temp_path, path, timeout=None):
        order.append("replace")
        os.replace(temp_path, path)

    with patch("mascope_runtime.atomic.os.fsync", side_effect=note_fsync):
        with patch(
            "mascope_runtime.atomic.replace_with_retry", side_effect=note_replace
        ):
            write_json(props, {"n": 1})

    assert order == ["fsync", "replace"]


def test_a_reader_holding_the_destination_does_not_lose_the_write(props):
    """Windows: os.replace raises PermissionError while a handle is open.

    An in-place write succeeded in that state, so the rename must wait the
    reader out rather than turn a working write into a lost calibration fit.
    On POSIX the rename succeeds immediately and this passes trivially.
    """
    holder = open(props, "r")
    threading.Timer(0.05, holder.close).start()
    try:
        write_json(props, {"mz_calibration": {"a": 2}}, indent=4)
    finally:
        if not holder.closed:
            holder.close()

    assert _read(props) == {"mz_calibration": {"a": 2}}


def test_a_sharing_violation_is_waited_out_rather_than_losing_the_write(props):
    """The same as the test above, without needing to be on Windows.

    That one depends on the platform raising PermissionError for an open
    destination, which only Windows does - so on the Linux test host it
    passes whatever write_json does with the error. This one makes the first
    rename fail and asserts the write still lands, which pins the retry
    everywhere.
    """
    real_replace = os.replace
    attempts = []

    def replace_once_held(temp_path, path):
        attempts.append(temp_path)
        if len(attempts) == 1:
            raise PermissionError("[WinError 5] Access is denied")
        return real_replace(temp_path, path)

    with patch("mascope_runtime.atomic.os.replace", side_effect=replace_once_held):
        write_json(props, {"mz_calibration": {"a": 2}}, indent=4)

    assert len(attempts) == 2, "the first rename failed and was retried"
    assert _read(props) == {"mz_calibration": {"a": 2}}
    assert _temps(os.path.dirname(props)) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX file modes")
def test_the_file_keeps_the_permissions_it_had(props):
    """A temporary is private; the rename would carry that onto the file.

    .props at 0644 silently becoming 0600 is invisible to the app and breaks
    anything reading the filestore as another user.
    """
    os.chmod(props, 0o644)

    write_json(props, {"n": 1})

    assert stat.S_IMODE(os.stat(props).st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX file modes")
def test_a_new_file_gets_what_an_ordinary_write_would(tmp_path):
    written = tmp_path / "new.json"
    control = tmp_path / "control.json"

    write_json(str(written), {"n": 1})
    with open(control, "w") as f:
        json.dump({"n": 1}, f)

    assert stat.S_IMODE(os.stat(written).st_mode) == stat.S_IMODE(
        os.stat(control).st_mode
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX file modes")
def test_an_explicit_mode_is_applied(tmp_path):
    """What the runtime state file asks for, so this change cannot loosen it."""
    target = tmp_path / "state.json"

    write_json(str(target), {"n": 1}, mode=0o600)

    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_the_retry_gives_up_rather_than_spinning_forever(tmp_path):
    target = tmp_path / ".props"
    target.write_text("{}", encoding="utf-8")
    temp = tmp_path / "temp.tmp"
    temp.write_text("{}", encoding="utf-8")

    with patch(
        "mascope_runtime.atomic.os.replace", side_effect=PermissionError("held")
    ):
        started = time.monotonic()
        with pytest.raises(PermissionError):
            replace_with_retry(str(temp), str(target), timeout=0.05)
        assert time.monotonic() - started < 2.0
