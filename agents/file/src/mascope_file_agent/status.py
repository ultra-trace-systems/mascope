"""
What became of each file the agent uploaded.

"Upload succeeded" used to be the last the agent said about a file, so it
could not tell a file the server processed from one it could not. A server
that announces ``files_listed_by_source_filename`` records each file's
processing status, and finds a file by the name it had on this machine among
the files this agent uploaded and it registered lately. After each upload the
agent asks about the file at a widening interval, and logs a line for each
stage it sees the file at: an error when processing failed, a warning when
the file waits for a chemistry or its calibration failed. A stage the file
passes through between two questions is not seen. A server that does not
announce it is not asked.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

from mascope_sdk import agent_headers


#: What a server announces when it can say what became of an upload.
CAPABILITY = "files_listed_by_source_filename"

#: Seconds before each question about a file: the first after the upload, the
#: next after each answer, the last repeated until the file settles.
#: Conversion takes seconds to minutes, calibration and matching a little
#: longer.
POLL_DELAYS = (20, 40, 60, 120, 240, 480, 900)

#: How long a file is followed before the agent stops asking about it.
FOLLOW_FOR = 3 * 60 * 60

#: Seconds to wait for each answer.
REQUEST_TIMEOUT = 30

#: How far before the upload finished a registration of the file counts. The
#: look-back is counted on the server's clock, which need not agree with this
#: machine's; the margin only has to cover the time the question takes.
REGISTRATION_MARGIN = 120

#: What each status reads as in the log, and at which level.
STAGES = {
    "converted": ("converted", "info"),
    "queued": ("queued for processing", "info"),
    "bound": ("bound to its ionization modes", "info"),
    "calibrated": ("m/z calibrated", "info"),
    "done": ("processed", "info"),
    "needs_chemistry": ("needs a chemistry", "warning"),
    "calibration_failed": (
        "m/z calibration failed, so not all of it was matched",
        "warning",
    ),
    "failed": ("processing failed", "error"),
}

#: The statuses a file does not leave on its own.
SETTLED = frozenset({"done", "needs_chemistry", "calibration_failed", "failed"})


@dataclass
class _Followed:
    """A file the agent is waiting to hear the outcome of."""

    #: The file's name on this machine, which the server keeps as its
    #: ``source_filename``.
    name: str
    #: When the upload finished, by this agent's clock.
    uploaded: float
    due: float
    polls: int = 0
    status: str | None = None
    #: When the server last answered a question about it; None if never.
    answered_at: float | None = None
    #: Whether the latest question about it was answered.
    latest_answered: bool = False


class StatusFollower:
    """Follow uploaded files until the server settles them.

    Upload workers hand each uploaded file to :meth:`follow`; :meth:`run`, on
    a thread of its own, asks about the files whose turn has come. Nothing
    here may stop an upload: a question that fails is asked again later, and
    an error with one file does not keep the others waiting.

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
        self._url = url
        self._access_token = access_token
        self._logger = logger
        self._verify = verify
        self._clock = clock
        self._lock = threading.Lock()
        self._followed: dict[str, _Followed] = {}
        #: None until the server says whether it can be asked; then whether
        #: it can.
        self.enabled: bool | None = None
        #: Set once :meth:`run` has returned: nothing asks about files then.
        self._stopped = False

    def follow(self, name: str) -> None:
        """Start following a file that was just uploaded.

        A file uploaded again under the same name before the server settled
        the earlier upload is followed as the new upload: the server's answer
        for the name cannot tell the two apart. A file whose upload finishes
        after the agent stopped following files - an upload in flight when it
        was told to stop - is not followed, and the line says so.

        :param name: The file's name on this machine.
        """
        if self.enabled is False:
            return
        now = self._clock()
        with self._lock:
            stopped = self._stopped
            earlier = None if stopped else self._followed.get(name)
            if not stopped:
                self._followed[name] = _Followed(
                    name=name, uploaded=now, due=now + POLL_DELAYS[0]
                )
        if stopped:
            self._logger.info(
                f"{name}: not followed, as the agent is stopping. What became of "
                "it shows in Raw files on the server."
            )
            return
        if earlier is not None:
            self._logger.info(
                f"{name}: uploaded again before the server settled the earlier "
                "upload; following the new one."
            )

    def following(self) -> list[str]:
        """The names of the files being followed."""
        with self._lock:
            return list(self._followed)

    def poll_due(self) -> None:
        """Ask about every followed file whose turn has come.

        Stops at the first question the server does not answer: the rest wait
        for the next pass rather than time out one after another.
        """
        if self.enabled is None and not self._ask_server():
            return
        if not self.enabled:
            return
        with self._lock:
            now = self._clock()
            due = [
                followed for followed in self._followed.values() if followed.due <= now
            ]
        for followed in due:
            try:
                answered = self._poll(followed)
            except Exception:
                self._logger.exception(
                    f"Error following {followed.name}; asking again later"
                )
                self._schedule(followed)
                continue
            if not answered:
                return

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
        with self._lock:
            self._stopped = True
            left = len(self._followed)
        if left:
            self._logger.info(
                f"No longer following {left} uploaded file{'' if left == 1 else 's'}: "
                "the agent is stopping. What became of them shows in Raw files on "
                "the server."
            )

    def _ask_server(self) -> bool:
        """Ask the server whether it can say what became of an upload.

        :return: Whether it answered; :attr:`enabled` then says what.
        """
        try:
            resp = requests.get(
                f"{self._url}/api/version",
                headers=agent_headers(self._access_token()),
                verify=self._verify,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            self._logger.debug(f"Could not ask the server what it can do: {e}")
            return False
        if resp.status_code >= 500:
            self._logger.debug(
                f"Could not ask the server what it can do: HTTP {resp.status_code}"
            )
            return False
        capabilities = {}
        if resp.status_code == 200:
            try:
                capabilities = (resp.json().get("data") or {}).get("capabilities") or {}
            except (ValueError, AttributeError):
                capabilities = {}
        # A server that refuses the question predates it: it cannot be asked
        # with a device token, and cannot answer the questions either.
        self.enabled = capabilities.get(CAPABILITY) is True
        if not self.enabled:
            self._logger.info(
                "The server does not report what becomes of uploaded files, so the "
                "agent will not follow them."
            )
            with self._lock:
                self._followed.clear()
        return True

    def _poll(self, followed: _Followed) -> bool:
        """Ask about one file, and schedule its next question.

        :return: Whether the server answered.
        """
        answered, row = self._ask(followed)
        followed.latest_answered = answered
        if answered:
            followed.answered_at = self._clock()
        if row is not None:
            self._report(followed, row)
            if row.get("processing_status") in SETTLED:
                self._forget(followed)
                return answered
        if self._clock() - followed.uploaded >= FOLLOW_FOR:
            self._give_up(followed)
            return answered
        self._schedule(followed)
        return answered

    def _schedule(self, followed: _Followed) -> None:
        """Set the file's next question, counted from now."""
        followed.polls += 1
        delay = POLL_DELAYS[min(followed.polls, len(POLL_DELAYS) - 1)]
        followed.due = self._clock() + delay

    def _ask(self, followed: _Followed) -> tuple[bool, dict | None]:
        """The server's newest row for the file registered since its upload.

        :return: Whether the server answered, and the row, or None when it
            has none yet. A question that fails is logged quietly and is not
            an answer: the file is asked about again at its next turn.
        """
        registered_within = int(self._clock() - followed.uploaded) + REGISTRATION_MARGIN
        try:
            resp = requests.get(
                f"{self._url}/api/sample/files",
                params={
                    "source_filename": followed.name,
                    "registered_within": registered_within,
                    # Not another agent's upload of a file of the same name.
                    "uploaded_by_me": "true",
                    "sort": "sample_file_utc_created",
                    "order": "desc",
                    "page": 0,
                    "limit": 1,
                },
                headers=agent_headers(self._access_token()),
                verify=self._verify,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            self._logger.debug(f"Could not ask about {followed.name}: {e}")
            return False, None
        if resp.status_code != 200:
            self._logger.debug(
                f"Could not ask about {followed.name}: HTTP {resp.status_code}"
            )
            return False, None
        try:
            rows = resp.json().get("data")
        except (ValueError, AttributeError):
            rows = None
        if not isinstance(rows, list):
            self._logger.debug(
                f"Could not ask about {followed.name}: unexpected answer"
            )
            return False, None
        row = rows[0] if rows else None
        return True, row if isinstance(row, dict) else None

    def _report(self, followed: _Followed, row: dict) -> None:
        """Log the file's status if it moved on since it was last seen."""
        status = row.get("processing_status")
        if not status or status == followed.status:
            return
        followed.status = status
        label, level = STAGES.get(status, (status, "info"))
        detail = (row.get("processing_detail") or "").strip()
        getattr(self._logger, level)(
            f"{followed.name}: {label}" + (f". {detail}" if detail else "")
        )

    def _give_up(self, followed: _Followed) -> None:
        """Say what the agent last knew of the file, and stop following it.

        Worded from the latest answer: an answer that has not come for a while
        says nothing of how the file is now.
        """
        hours = FOLLOW_FOR // 3600
        if followed.answered_at is None:
            self._logger.warning(
                f"{followed.name}: the server could not be asked about it in the "
                f"{hours} hours after the upload; look for it in Raw files on the "
                "server."
            )
        elif not followed.latest_answered:
            silent = _duration(self._clock() - followed.answered_at)
            last = (
                "the server had no record of it"
                if followed.status is None
                else f"it was {STAGES.get(followed.status, (followed.status,))[0]}"
            )
            self._logger.warning(
                f"{followed.name}: the server could not be asked about it for the "
                f"last {silent}; before that, {last}. Look for it in Raw files on "
                "the server."
            )
        elif followed.status is None:
            self._logger.warning(
                f"{followed.name}: the server has no record of it {hours} hours "
                "after the upload. Converting it may have failed; look for it in "
                "Raw files on the server."
            )
        else:
            self._logger.warning(
                f"{followed.name}: still {STAGES.get(followed.status, (followed.status,))[0]} "
                f"{hours} hours after the upload; no longer following it."
            )
        self._forget(followed)

    def _forget(self, followed: _Followed) -> None:
        """Stop following the file - unless it was uploaded again meanwhile."""
        with self._lock:
            if self._followed.get(followed.name) is followed:
                del self._followed[followed.name]


def _duration(seconds: float) -> str:
    """A stretch of time as a person reads it: minutes, or whole hours."""
    minutes = round(seconds / 60)
    if minutes < 120:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    return f"{round(minutes / 60)} hours"
