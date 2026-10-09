"""What the server says it can do.

A server announces each thing an older one did not do in ``GET /api/version``,
and the agent asks before it relies on one: what became of an upload, whether
an acquisition record is kept. Every part of the agent that needs the answer
shares the one asking.
"""

import threading
import time
from typing import Callable

import requests

from mascope_sdk import agent_headers


#: Seconds to wait for the answer.
REQUEST_TIMEOUT = 30

#: Statuses that are no answer to the question, because they clear on their
#: own: the server, or a proxy in front of it, is asking for another try. The
#: SDK counts the same three as worth sending an upload request again for.
#: ``/api/version`` is rate-limited by the address the request comes from, and
#: an instrument computer shares its address with every browser beside it.
ASK_AGAIN_FOR = frozenset({408, 425, 429})

#: Seconds an answer is kept before the server is asked again. An agent runs
#: for months and its server is updated under it, so what the server could
#: not do when the agent started is not what it cannot do.
ASK_AGAIN_AFTER = 60 * 60


class ServerCapabilities:
    """Ask the server what it can do, and keep the answer for a while.

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
        self._announced: dict | None = None
        self._asked_at = 0.0
        # The token a refusal was given to; None when the server answered.
        self._refused: str | None = None

    @property
    def refused(self) -> bool:
        """Whether what is known of the server is a refusal of the question.

        A server that refused is taken to announce nothing, and that is all
        :meth:`has` says of it. Whoever tells a person why something was not
        done reads this too: "the server is too old" is not what a refused
        credential means.
        """
        return self._refused is not None

    def ask(self) -> dict | None:
        """What the server announces, or None while it has not answered.

        A server that could not be reached, that failed, or that asked for
        another try (:data:`ASK_AGAIN_FOR`) is asked again the next time. One
        that answered is asked again after :data:`ASK_AGAIN_AFTER`.

        A server that refuses the question predates it, and announces nothing
        - or it refused this machine's credential. The two cannot be told
        apart, so a refusal stands only for the token it was given to: once
        the credential has been replaced, the question is asked again.

        :return: The capabilities, by name; empty when it announces none
        :rtype: dict | None
        """
        with self._lock:
            token = self._access_token()
            if (
                self._announced is not None
                and self._clock() - self._asked_at < ASK_AGAIN_AFTER
                and self._refused in (None, token)
            ):
                return self._announced
            answer = self._request(token)
            if answer is None:
                # Not what it said an hour ago either: the caller is told the
                # server did not answer, and decides what that means.
                return None
            self._announced, refused = answer
            self._asked_at = self._clock()
            self._refused = token if refused else None
            return self._announced

    def has(self, capability: str) -> bool | None:
        """Whether the server announces a capability; None while it has not
        answered.

        :param capability: The capability's name
        :type capability: str
        :rtype: bool | None
        """
        announced = self.ask()
        return None if announced is None else announced.get(capability) is True

    def _request(self, token: str | None) -> tuple[dict, bool] | None:
        """Ask. The capabilities and whether the question was refused, or None
        when the server did not answer."""
        try:
            resp = requests.get(
                f"{self._url}/api/version",
                headers=agent_headers(token),
                verify=self._verify,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            self._logger.debug(f"Could not ask the server what it can do: {e}")
            return None
        if resp.status_code >= 500 or resp.status_code in ASK_AGAIN_FOR:
            self._logger.debug(
                f"Could not ask the server what it can do: HTTP {resp.status_code}"
            )
            return None
        if resp.status_code != 200:
            return {}, True
        try:
            capabilities = (resp.json().get("data") or {}).get("capabilities") or {}
        except (ValueError, AttributeError):
            capabilities = {}
        return (capabilities if isinstance(capabilities, dict) else {}), False
