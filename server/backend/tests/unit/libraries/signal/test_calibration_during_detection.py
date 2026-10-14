"""An m/z calibration applied to a file while its peaks are detected.

A detection reads the calibration the file records when it begins, places
every peak by it, and writes the store it built when it ends
(``mascope_signal.peak.compute_peaks``). An apply moves the file's store by
the new calibration and records it (``calibration_mz_fit``). One that landed
between the detection's read and its write would move the store about to be
replaced, and record a calibration the store that replaces it was never
placed by: every read and match of the file after it off by the factor.

The two run in different processes as a rule - the file converter detects,
a backend worker applies - and the lock between them is a lock of processes,
so the apply is made from another process here (``apply_elsewhere``).
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading

import pytest
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.mz_factor as m_factor
import mascope_signal.peak as m_peak


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"

# Two experiments under one filter, told apart by their microscans, so the
# file can be detected per stream as well as whole
SCANS = [
    (NEG, 1, {62.0: 100.0, 125.0: 10.0}),
    (NEG, 1, {62.0: 100.0, 125.0: 10.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
]
MICROSCANS = {1: 1, 2: 10}

#: The calibration applied: one factor for the whole file, every stream's
FACTOR = 1 + 50e-6
FIT = {
    "mode": "one-point",
    "par": {
        "old_factor": 1.0,
        "old_factor_scaling": FACTOR,
        "calibration_factor": FACTOR,
    },
}

#: Longest the test waits for the other process to say anything, its imports
#: included
WAIT = 120.0

#: A terminal colour code
_COLOUR = re.compile(r"\x1b\[[0-9;]*m")


class _ApplyElsewhere:
    """The apply, run in a process of its own, and what it says.

    The backend logs to stdout as well, and colours it, so a line is read
    without its colour codes.
    """

    def __init__(self, filestore: str):
        self.process = subprocess.Popen(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "apply_elsewhere.py"),
                filestore,
                json.dumps(FIT),
                repr(SCANS),
                repr(MICROSCANS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.said: list[str] = []
        self._over = False
        self._lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            self._lines.put(_COLOUR.sub("", line).rstrip("\n"))
        self._lines.put(None)

    def _next(self) -> str | None:
        """The next line it says, or None once it has said all it will."""
        if self._over:
            return None
        line = self._lines.get(timeout=WAIT)
        if line is None:
            self._over = True
        else:
            self.said.append(line)
        return line

    def asks_for_or_ends(self, lock_path: str) -> None:
        """Return once the apply waits for this lock, or is over."""
        while (line := self._next()) is not None:
            if line == f"LOCK {lock_path}":
                return

    def end(self) -> None:
        """Wait for the apply to be over, and for it to have gone through."""
        while self._next() is not None:
            pass
        self.process.wait(timeout=WAIT)
        output = "\n".join(self.said)
        assert self.process.returncode == 0, output
        assert "APPLIED" in self.said, output


def _store_mz():
    return m_io.load_peak_data(
        SAMPLE_FILENAME, drop_bad_peaks=False, composite=False
    ).mz.values


@pytest.mark.parametrize("per_stream", [False, True], ids=["whole", "per-stream"])
def test_a_calibration_applied_during_a_detection_moves_the_store_it_writes(
    acquire, instrument_functions, temp_filestore, monkeypatch, per_stream
):
    """The file's peaks rebuilt while a calibration is applied to it from
    another process, which asks to apply it after the rebuild has read the
    file's calibration and before it has written its store. Either order of
    the two leaves the store where the record puts it; the apply cannot be
    had in the middle."""
    acquire(SCANS, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=per_stream)
    before = _store_mz()
    detection = m_io.peak_detection_lock_path(SAMPLE_FILENAME)
    write_peaks = m_io.write_peaks
    elsewhere = []

    async def writing(*args, **kwargs):
        # The rebuild has read the record and placed its peaks by it
        if not elsewhere:
            elsewhere.append(_ApplyElsewhere(temp_filestore))
            elsewhere[0].asks_for_or_ends(detection)
        await write_peaks(*args, **kwargs)

    monkeypatch.setattr(m_io, "write_peaks", writing)
    # A rebuild, by what the file records
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    elsewhere[0].end()

    record = m_io.read_props(SAMPLE_FILENAME)["mz_calibration"]
    assert m_factor.file_factor(record) == FACTOR
    assert _store_mz() == pytest.approx(before * FACTOR, rel=1e-9)
