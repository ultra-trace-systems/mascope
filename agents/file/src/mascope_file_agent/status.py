"""
What became of each file the agent uploaded.

"Upload succeeded" used to be the last the agent said about a file, so it
could not tell a file the server processed from one it could not. A server
that records each file's processing status - ``processing_status`` on the
file list, which a device token may read - is asked after each upload, at a
widening interval, and the agent logs one line for each stage the file
reaches: an error when processing failed, a warning when the file waits for
a chemistry or its calibration failed. An older server reports no status, and
the agent stops asking.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

from mascope_sdk import agent_headers


#: Seconds from an upload to each question about it, the last repeated until
#: the file settles. Conversion takes seconds to minutes, calibration and
#: matching a little longer.
POLL_DELAYS = (20, 40, 60, 120, 240, 480, 900)

#: How long a file is followed before the agent stops asking about it.
FOLLOW_FOR = 3 * 60 * 60

#: Seconds to wait for each answer.
REQUEST_TIMEOUT = 30

#: What each status reads as in the log, and at which level.
STAGES = {
    "converted": ("converted", "info"),
    "bound": ("bound to its ionization modes", "info"),
    "calibrated": ("m/z calibrated", "info"),
    "done": ("processed", "info"),
    "needs_chemistry": ("needs a chemistry", "warning"),
    "calibration_failed": ("m/z calibration failed, so it was not matched", "warning"),
    "failed": ("processing failed", "error"),
}

#: The statuses a file does not leave on its own.
SETTLED = frozenset({"done", "needs_chemistry", "calibration_failed", "failed"})


def stored_name(upload_name: str, instrument: str | None) -> str:
    """The name the server stores an upload under.

    The server files an upload under the instrument the agent reports with
    it, and puts ``<instrument>_`` in front of the name unless the name
    already starts with that instrument (``file_upload_name`` on the server).
    Without a reported instrument the name is stored as it was uploaded.

    :param upload_name: The name the file was uploaded as.
    :param instrument: The instrument reported with it, if any.
    :return: The name to look the file up by.
    """
    if not instrument or upload_name.split("_", 1)[0] == instrument:
        return upload_name
    return f"{instrument}_{upload_name}"


@dataclass
class _Followed:
    """A file the agent is waiting to hear the outcome of."""

    name: str  # the server's name for it
    shown: str  # the name the operator knows it by
    started: float
    due: float
    polls: int = 0
    status: str | None = None


class StatusFollower:
    """Follow uploaded files until the server settles them.

    Upload workers hand each uploaded file to :meth:`follow`; :meth:`run`, on
    a thread of its own, asks about the files whose turn has come. Nothing
    here may stop an upload: a question that fails is asked again later.

    :param url: The server's base URL.
    :param access_token: Returns the live access token; renewal rotates it.
    :param logger: Where the lines go.
    :param verify: Whether to verify the server's TLS certificate.
    :param clock: Monotonic seconds, replaceable in tests.
    """

    def __init__(
        self,
        url: str,
        access_token: Callable[[], str | None],
        logger,
        verify: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._url = f"{url}/api/sample/files"
        self._access_token = access_token
        self._logger = logger
        self._verify = verify
        self._clock = clock
        self._lock = threading.Lock()
        self._followed: dict[str, _Followed] = {}
        #: Cleared for good once the server shows it reports no status.
        self.enabled = True

    def follow(self, upload_name: str, instrument: str | None, shown: str) -> None:
        """Start following a file that was just uploaded.

        :param upload_name: The name it was uploaded as.
        :param instrument: The instrument reported with it, if any.
        :param shown: The name to log it by.
        """
        if not self.enabled:
            return
        now = self._clock()
        name = stored_name(upload_name, instrument)
        with self._lock:
            self._followed[name] = _Followed(
                name=name, shown=shown, started=now, due=now + POLL_DELAYS[0]
            )

    def following(self) -> list[str]:
        """The server names of the files being followed."""
        with self._lock:
            return list(self._followed)

    def poll_due(self) -> None:
        """Ask about every followed file whose turn has come."""
        now = self._clock()
        with self._lock:
            due = [
                followed for followed in self._followed.values() if followed.due <= now
            ]
        for followed in due:
            if not self.enabled:
                return
            self._poll(followed, now)

    def run(self, stop_event, tick: float = 5) -> None:
        """Ask about due files every ``tick`` seconds until shutdown.

        :param stop_event: Set when the agent shuts down.
        :param tick: Seconds between checks for due files.
        """
        while not stop_event.wait(tick):
            try:
                self.poll_due()
            except Exception:
                # A daemon thread's traceback goes to an excepthook nobody
                # reads, and this thread is the only one following files.
                self._logger.exception("Error following uploaded files; continuing")

    def _poll(self, followed: _Followed, now: float) -> None:
        row = self._ask(followed)
        if row is not None:
            if "processing_status" not in row:
                # An older server records no processing status.
                self._logger.info(
                    "The server does not report what becomes of uploaded files, "
                    "so the agent will not follow them."
                )
                self.enabled = False
                with self._lock:
                    self._followed.clear()
                return
            self._report(followed, row)
            if row.get("processing_status") in SETTLED:
                self._forget(followed)
                return
        if now - followed.started >= FOLLOW_FOR:
            self._give_up(followed)
            return
        followed.polls += 1
        followed.due = now + POLL_DELAYS[min(followed.polls, len(POLL_DELAYS) - 1)]

    def _ask(self, followed: _Followed) -> dict | None:
        """The server's row for the file, or None when there is none yet.

        A question that fails is logged quietly and answered with None: the
        file is asked about again at its next turn.
        """
        try:
            resp = requests.get(
                self._url,
                params={"filename": followed.name, "page": 0, "limit": 1},
                headers=agent_headers(self._access_token()),
                verify=self._verify,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            self._logger.debug(f"Could not ask about {followed.shown}: {e}")
            return None
        if resp.status_code != 200:
            self._logger.debug(
                f"Could not ask about {followed.shown}: HTTP {resp.status_code}"
            )
            return None
        try:
            rows = resp.json().get("data") or []
        except (ValueError, AttributeError):
            return None
        return rows[0] if rows else None

    def _report(self, followed: _Followed, row: dict) -> None:
        """Log the file's status if it moved on since it was last seen."""
        status = row.get("processing_status")
        if not status or status == followed.status:
            return
        followed.status = status
        label, level = STAGES.get(status, (status, "info"))
        detail = (row.get("processing_detail") or "").strip()
        getattr(self._logger, level)(
            f"{followed.shown}: {label}" + (f". {detail}" if detail else "")
        )

    def _give_up(self, followed: _Followed) -> None:
        hours = FOLLOW_FOR // 3600
        if followed.status is None:
            self._logger.warning(
                f"{followed.shown}: the server has no record of it {hours} hours "
                "after the upload. Converting it may have failed; look for it "
                "in Raw files on the server."
            )
        else:
            self._logger.warning(
                f"{followed.shown}: still {STAGES.get(followed.status, (followed.status,))[0]} "
                f"{hours} hours after the upload; no longer following it."
            )
        self._forget(followed)

    def _forget(self, followed: _Followed) -> None:
        with self._lock:
            self._followed.pop(followed.name, None)
