"""What Ctrl+C does to the console program, with interrupts that are real.

`Agent.run_until_complete()` promises three things of an interrupt: the first
stops the agent, which then waits for the uploads it has handed to a worker;
a second during that wait is answered with a line and more waiting; a third
stops without them. Whether an interrupt can reach the wait at all is the part
a stubbed `stop()` cannot show, and it is where the platforms differ:

- On Windows a wait for a lock cannot be interrupted, so a `Thread.join()`
  with no timeout is deaf to Ctrl+C until the thread ends by itself.
- Elsewhere the interrupt reaches the join, and CPython 3.12 then marks the
  thread as stopped while it runs (python/cpython#90882), so the next wait
  finds nothing to wait for and the process exits with the upload in flight.

So the agent runs in a process of its own (`interrupted_console.py`), with an
upload held and the interrupts raised the way the operating system raises
them, and the test reads when each line was written. It means most where the
program ships, which is why CI runs this suite on Windows as well.
"""

import re
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("interrupted_console.py")
LINE = re.compile(r"^\s*(\d+\.\d\d) (.*)$")


def _console(tmp_path, hold, *interrupts):
    """Run the console agent; ``(seconds since the upload began, line)`` pairs."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), str(hold), *map(str, interrupts)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [
        (float(match[1]), match[2])
        for match in map(LINE.match, result.stdout.splitlines())
        if match
    ]


def _when(lines, text):
    """When the first line holding ``text`` was written, or None if none was."""
    return next((seconds for seconds, line in lines if text in line), None)


def test_one_interrupt_stops_the_agent_and_waits_for_the_upload(tmp_path):
    lines = _console(tmp_path, 3, 1)

    asked = _when(lines, "Shutdown requested by user.")
    uploaded = _when(lines, "File upload of file x.raw succeeded!")
    returned = _when(lines, "returned")
    # Answered when it came, and the agent gone only once the upload was in.
    assert 1 <= asked < 2.5
    assert 3 <= uploaded <= returned
    assert _when(lines, "Still waiting") is None
    assert _when(lines, "had not ended") is None


def test_a_second_interrupt_keeps_waiting_and_a_third_stops_without_the_upload(
    tmp_path,
):
    lines = _console(tmp_path, 15, 1.5, 4, 6.5)

    first = _when(lines, "Shutdown requested by user.")
    second = _when(lines, "Still waiting for the uploads under way to end.")
    third = _when(lines, "x.raw: its upload had not ended when the agent stopped.")
    returned = _when(lines, "returned")
    # Each is answered when it comes - not, as a join would have it, once the
    # upload is over, and not all at once at the second.
    assert 1.5 <= first < 4
    assert 4 <= second < 6.5
    assert 6.5 <= third <= returned < 12
    # The upload was held for 15 s: the process left it behind.
    assert _when(lines, "upload ended") is None
