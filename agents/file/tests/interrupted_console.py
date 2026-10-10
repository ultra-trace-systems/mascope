"""A console agent with one upload held, interrupted three times.

Not a test: `test_console_interrupts.py` runs this in a process of its own,
because the interrupts are real ones, raised the way the operating system
raises a Ctrl+C, and a real interrupt in the test process would end the test
run. It prints one line per log line and one when the agent has returned,
each with the seconds since the upload began.

Usage: python interrupted_console.py <watched folder> <hold seconds> <interrupt at>...
"""

import os
import signal
import sys
import threading
import time

from mascope_file_agent import (
    Agent,
    AgentSettings,
    capabilities,
    credentials,
    identity,
    status,
    uploader,
)
from mascope_file_agent.wizard import CREDENTIAL_OK


FOLDER = sys.argv[1]
HOLD = float(sys.argv[2])
INTERRUPT_AT = [float(seconds) for seconds in sys.argv[3:]]

began = None


def say(text: str) -> None:
    since = 0.0 if began is None else time.monotonic() - began
    print(f"{since:6.2f} {text}", flush=True)


class Logger:
    def __getattr__(self, level):
        return lambda message: say(f"{level}: {message}")


def interrupt() -> None:
    """A Ctrl+C, as the process gets one.

    On Windows the console runs a handler on a thread of its own, which flags
    the interrupt for the main thread; elsewhere the terminal's signal is
    delivered to the main thread itself, cutting short what it is waiting in.
    """
    if os.name == "nt":
        signal.raise_signal(signal.SIGINT)
    else:
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)


def held_upload(**kwargs) -> None:
    global began
    began = time.monotonic()
    say("upload began")
    # On the dot after the upload loop last woke, which it did to hand this
    # file over. On Windows a wait runs out on a timer tick and so does a
    # timer, so each interrupt tends to arrive just as the loop wakes again,
    # into its few instructions of work and not into its wait. That is where
    # an interrupt raised on arrival left a lock held (`_threads.Interrupts`),
    # and the times stay on the dot to keep arriving there.
    for seconds in INTERRUPT_AT:
        threading.Timer(seconds, interrupt).start()
    time.sleep(HOLD)
    say("upload ended")


uploader.api_post_file_tus = held_upload
uploader.POLL_INTERVAL = 0.05
credentials.check_credential = lambda host, token, verify: (CREDENTIAL_OK, "")
status.StatusFollower.poll_due = lambda self: None
capabilities.ServerCapabilities.ask = lambda self: {}

# A process started with SIGINT ignored keeps it ignored, and Python then
# installs no handler of its own, so the interrupts would do nothing. A shell
# without job control starts its background commands that way.
signal.signal(signal.SIGINT, signal.default_int_handler)

identity()
agent = Agent(
    AgentSettings(
        host="mascope.example.com",
        access_token="tok",
        source=FOLDER,
        instrument="Orbi-Lab2",
        timeout=0,
    ),
    logger=Logger(),
)
sample = os.path.join(FOLDER, "x.raw")
with open(sample, "wb") as file:
    file.write(b"data")
agent.uploader.enqueue(sample)

agent.run_until_complete()
say("returned")
