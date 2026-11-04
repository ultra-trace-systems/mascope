"""
Tests for how the converter stops (``service.run`` and ``install_stop_signal``).

Docker stops the converter's container with SIGTERM and SIGKILLs it once its
stop grace period is up. The converter is the container's PID 1, and the kernel
delivers no signal to a PID 1 that has no handler for it, so a converter that
does not handle SIGTERM sits out the whole grace period on every stop, and
every stop of the boot unit times out waiting for it (#2903).

Each test runs the real ``run()`` in a process of its own. The stop tests send
it a real SIGTERM. That process is not a PID 1, so without the handler the
signal's default action would kill it at once instead of being dropped. The
tests therefore read how it ended, not only how fast: a converter that handled
the stop returns from ``run()`` and exits 0, while one the signal killed exits
with -SIGTERM.

Ctrl+C ends the supervision loop another way, with a KeyboardInterrupt, and
must stop the threads as well: ``run()`` joins every one of them on its way
out, and a join on a thread nobody told to stop never returns.

Every way out of ``run()`` disconnects the socket. The process reports whether
it did on its last line, after ``run()`` has returned.
"""

import signal
import subprocess
import sys
import threading
import time

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows has no SIGTERM to send: os.kill() ends the process outright",
)

#: How long the converter may take to import and start its threads.
STARTUP_TIMEOUT_S = 120
#: How soon an idle converter must be gone after the stop: far inside the 90 s
#: grace period, with room for a slow runner.
IDLE_STOP_WITHIN_S = 15
#: How long to wait for any exit at all before calling the stop hung.
EXIT_TIMEOUT_S = 60
#: What the converter prints once supervision has begun.
SUPERVISING = "CONVERTER-SUPERVISING"
#: What it prints once run() has returned with the socket disconnected.
DISCONNECTED = "CONVERTER-DISCONNECTED True"

# The converter with no backend: the socket "connects" at once and goes
# nowhere, and the watchers poll a scratch folder. The real main() is wrapped
# only to say when supervision has begun - every thread started, the stop
# handler installed. Modes:
# - "converting": the Orbitrap processor slot holds a conversion that is in
#   flight when the stop comes and takes another second to finish, and records
#   that it did.
# - "interrupted": supervision ends at once in the KeyboardInterrupt Ctrl+C
#   raises in the main thread. Raised rather than sent: a child of a
#   non-interactive shell can start with SIGINT ignored.
_CONVERTER = r"""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from mascope_backend.file_converter import service

source, mode, marker = sys.argv[1:]

service.SOCKET_CLIENT = MagicMock()
service.runtime = SimpleNamespace(
    config=SimpleNamespace(source=source, h5_threads=1, raw_threads=1, interval=1),
    logger=service.runtime.logger,
)

if mode == "converting":

    class Converting(threading.Thread):
        def __init__(self, *, shutdown_event, **_):
            super().__init__()
            self.shutdown_event = shutdown_event

        def run(self):
            self.shutdown_event.wait()
            time.sleep(1)
            Path(marker).write_text("converted")

    service.RawProcessor = Converting

supervise = service.main


def main(supervised):
    print("CONVERTER-SUPERVISING", flush=True)
    if mode == "interrupted":
        raise KeyboardInterrupt
    supervise(supervised)


service.main = main
try:
    service.run()
except KeyboardInterrupt:
    pass
print(f"CONVERTER-DISCONNECTED {service.SOCKET_CLIENT.sio.disconnect.called}", flush=True)
"""


class _Converter:
    """One converter process, read line by line as it runs."""

    def __init__(self, tmp_path, mode: str):
        self.marker = tmp_path / "converted"
        self.output: list[str] = []
        self._supervising = threading.Event()
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CONVERTER,
                str(tmp_path / "streams"),
                mode,
                str(self.marker),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in self.process.stdout:
            self.output.append(line)
            # Contained, not equal: the colour reset ending the log line
            # before it can land at the start of this one.
            if SUPERVISING in line:
                self._supervising.set()

    def await_supervising(self) -> None:
        if not self._supervising.wait(STARTUP_TIMEOUT_S):
            pytest.fail(f"the converter never began supervising:\n{self.log}")

    def wait_exit(self, after: str) -> None:
        """Wait for the process to end; `after` names what it was waiting on."""
        try:
            self.process.wait(EXIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"the converter was still running {EXIT_TIMEOUT_S} s after "
                f"{after}:\n{self.log}"
            )
        self._reader.join(5)

    def stop(self) -> float:
        """Send SIGTERM once supervision has begun; seconds until it exited."""
        self.await_supervising()
        sent = time.monotonic()
        self.process.send_signal(signal.SIGTERM)
        self.wait_exit("SIGTERM")
        return time.monotonic() - sent

    @property
    def disconnected(self) -> bool:
        return any(DISCONNECTED in line for line in self.output)

    @property
    def log(self) -> str:
        return "".join(self.output)

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()


@pytest.fixture()
def converter(tmp_path):
    started: list[_Converter] = []

    def _start(mode: str = "idle") -> _Converter:
        started.append(_Converter(tmp_path, mode))
        return started[-1]

    yield _start
    for process in started:
        process.kill()


def test_an_idle_converter_stops_promptly_on_sigterm(converter):
    process = converter()

    elapsed = process.stop()

    assert process.process.returncode == 0, (
        f"the converter did not stop cleanly (exit {process.process.returncode}); "
        f"-{signal.SIGTERM.value} means the signal killed it unhandled:\n"
        f"{process.log}"
    )
    assert elapsed < IDLE_STOP_WITHIN_S, process.log
    assert process.disconnected, (
        f"run() returned without disconnecting the socket:\n{process.log}"
    )


def test_a_conversion_in_flight_is_finished_before_the_converter_exits(converter):
    process = converter("converting")

    process.stop()

    assert process.process.returncode == 0, process.log
    assert process.marker.exists(), (
        f"the converter exited without finishing the conversion it held:\n{process.log}"
    )


def test_ctrl_c_stops_the_threads_and_disconnects(converter):
    process = converter("interrupted")

    process.await_supervising()
    process.wait_exit("the KeyboardInterrupt")

    assert process.process.returncode == 0, process.log
    assert process.disconnected, (
        f"run() returned without disconnecting the socket:\n{process.log}"
    )
