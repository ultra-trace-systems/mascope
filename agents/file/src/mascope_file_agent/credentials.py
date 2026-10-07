"""This machine's credential: held live, checked at start, renewed, replaced.

The access token is a device token the server issued when the machine was
paired. :class:`Credentials` holds the one in use, so the renewal loop can
rotate it under the uploader without a restart, asks the server about it once
at start, and - when the server refuses it - offers to pair the machine again
through a :class:`Repair`.
"""

import sys
from threading import Lock
from typing import Callable

from mascope_file_agent.wizard import (
    CREDENTIAL_OK,
    CREDENTIAL_REJECTED,
    check_credential,
    run_pairing,
)
from mascope_sdk import api_renew_agent_token
from mascope_sdk.exceptions import AuthenticationError, TusNotSupportedError


# Seconds after startup before the first token renewal, then between renewals
# when the server reports no lifetime, and after a transient renewal failure.
RENEW_INITIAL_DELAY = 60
RENEW_FALLBACK_INTERVAL = 7 * 24 * 60 * 60  # 7 days
RENEW_RETRY_DELAY = 5 * 60  # 5 minutes
RENEW_MIN_INTERVAL = 60 * 60  # never renew more often than hourly

REPAIR_BANNER = """
=== This machine's Mascope credential was refused ===
{reason}

Pairing again takes a few seconds: this window shows a code, and someone
signed in to Mascope approves it under 'Pair an agent'.
"""

REPAIR_DECLINED_NOTE = """
Not pairing. Uploads stay paused until this machine is paired again - start
the Mascope File Agent again when you are ready and it will ask.
"""


class Repair:
    """What the agent does when the server refuses this machine's credential.

    This one does nothing but say so in the log, which is what an agent with
    no console wants: a program that embeds the agent passes its own subclass
    to show the refusal where its operator looks, and to say how the machine
    is paired again there.
    """

    #: Ends the log line written when the credential is refused at start,
    #: after "Uploads will fail until it is paired again - ".
    advice_at_start = "run the agent's setup to pair it."

    #: Ends the log line written when an upload is refused for its credential.
    advice_on_upload = "Run the agent's setup to pair this machine again."

    def offer(self, reason: str, pair: Callable[[], str | None]) -> str | None:
        """Try to replace the refused credential.

        Called at most once at a time, and not again once it has returned
        None: a refused credential stays refused, and the operator is not
        asked once per file.

        :param reason: The server's explanation
        :type reason: str
        :param pair: Pairs this machine with the agent's server, showing the
            code on the console and waiting for its approval; returns the new
            token, or None when pairing did not complete
        :type pair: Callable[[], str | None]
        :return: The new access token, or None to leave the credential as it is
        :rtype: str | None
        """
        return None


class ConsoleRepair(Repair):
    """Ask at the console whether to pair again, and pair there.

    Recovery used to mean re-launching the agent with ``--setup``, which is a
    lot to ask of whoever runs the instrument: the console is already open in
    front of them and the credential is already known to be dead. Pairing needs
    a person with an editor account to approve in the browser either way, so
    asking here changes nothing about who may connect a machine.
    """

    advice_at_start = (
        "start the Mascope File Agent from the Start Menu and answer the prompt."
    )

    advice_on_upload = (
        "Start the Mascope File Agent again from the Start Menu and it will "
        "offer to pair this machine."
    )

    def offer(self, reason: str, pair: Callable[[], str | None]) -> str | None:
        if not (sys.stdin and sys.stdin.isatty()):
            return None  # started without a console; the log line has to do
        print(REPAIR_BANNER.format(reason=reason))
        try:
            answer = input("Pair this machine again now? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if answer in ("n", "no"):
            print(REPAIR_DECLINED_NOTE)
            return None
        return pair()


class Credentials:
    """The access token an agent presents, and what keeps it accepted.

    :param url: The server's base URL, which the renewal is asked at.
    :param host: The configured server address, which the startup check and
        pairing are asked at.
    :param access_token: The token the agent starts with.
    :param logger: Where the lines go.
    :param verify_tls: Whether to verify the server's TLS certificate.
    :param instrument: Name of the instrument this machine watches, reported
        when pairing again.
    :param persist: Saves a new token so that a restart keeps using it; None
        when there is nowhere to save it.
    :param repair: What to do when the server refuses the credential.
    """

    def __init__(
        self,
        url: str,
        host: str,
        access_token: str | None,
        logger,
        verify_tls: bool = True,
        instrument: str | None = None,
        persist: Callable[[str], None] | None = None,
        repair: Repair | None = None,
    ):
        self.url = url
        self.host = host
        self.logger = logger
        self.verify_tls = verify_tls
        self.instrument = instrument
        self.repair = repair if repair is not None else Repair()
        self._persist = persist
        # The live access token. Held apart from the settings the agent was
        # built from so the renewal loop can rotate it under the uploader
        # without a restart.
        self._token_lock = Lock()
        self._access_token = access_token
        # Serializes the re-pair offer: three upload workers hitting a dead
        # credential at once must not each prompt. Declining is remembered so
        # the console does not nag on every file for the rest of the session.
        self._repair_lock = Lock()
        self._repair_declined = False

    def current_access_token(self) -> str | None:
        """The access token uploads should use right now (renewal may rotate it)."""
        with self._token_lock:
            return self._access_token

    def set_access_token(self, token: str) -> None:
        """Update the live access token used by subsequent uploads."""
        with self._token_lock:
            self._access_token = token

    def persist(self, token: str) -> None:
        """Save a rotated token so a restart keeps using it.

        A no-op when the agent was given nowhere to save it.
        """
        if self._persist is None:
            return
        try:
            self._persist(token)
        except OSError as e:
            # The in-memory token still works this session; only restart continuity
            # is at risk, so warn rather than fail.
            self.logger.warning(f"Could not persist the renewed token: {e}")

    def renewal_loop(self, stop_event) -> None:
        """Rotate the agent's device token before it expires, until shutdown.

        Renews shortly after start (to establish a known expiry) and then at about
        half the server-reported lifetime. A server with no renewal endpoint (older
        release), or a credential that is not renewable, backs the loop off to the
        long interval rather than ending it - the condition is often temporary, and
        a loop that stops never renews again for the life of the process. Uploads
        meanwhile continue on the current token and surface their own actionable
        error if it has lapsed.

        :param stop_event: Set on agent shutdown to end the loop.
        """
        delay = RENEW_INITIAL_DELAY
        while not stop_event.wait(delay):
            try:
                try:
                    new_token, expires_in = api_renew_agent_token(
                        self.url, self.current_access_token()
                    )
                except (TusNotSupportedError, AuthenticationError) as e:
                    # 404: this server has no renewal endpoint (older release).
                    # 401: the credential is not a renewable device token. Neither
                    # is worth a tight retry, but neither proves the condition is
                    # permanent - a rolling restart or a proxy blip answers both
                    # the same way - so back off instead of ending the loop. A
                    # thread that returns here never renews again, and the token
                    # then lapses silently 30 days later.
                    self.logger.info(f"Token renewal unavailable, backing off: {e}")
                    delay = RENEW_FALLBACK_INTERVAL
                    continue
                except Exception as e:
                    self.logger.info(f"Token renewal failed, will retry: {e}")
                    delay = RENEW_RETRY_DELAY
                    continue

                self.set_access_token(new_token)
                self.persist(new_token)
                self.logger.info("Renewed the agent access token.")
                delay = (
                    max(RENEW_MIN_INTERVAL, expires_in // 2)
                    if expires_in
                    else RENEW_FALLBACK_INTERVAL
                )
            except Exception:
                # Nothing may kill this thread: it is the only thing keeping the
                # credential alive, and a daemon thread's traceback goes to an
                # excepthook nobody reads. Persisting or rescheduling can still
                # raise (a config dict missing a key, a full disk), so the loop
                # absorbs it and tries again rather than going quietly dead.
                self.logger.exception("Token renewal loop error; continuing")
                delay = RENEW_RETRY_DELAY

    def offer_repair(self, token_used: str | None, reason: str) -> bool:
        """Offer to pair this machine again after the server refused its credential.

        :param token_used: The credential the failed attempt used; a different live
            token means another worker already fixed it.
        :type token_used: str | None
        :param reason: The server's explanation, shown to the operator.
        :type reason: str
        :return: Whether the caller should retry the upload.
        :rtype: bool
        """
        with self._repair_lock:
            if self.current_access_token() != token_used:
                return True  # another worker re-paired while this one waited
            if self._repair_declined:
                return False
            token = self.repair.offer(reason, self._pair)
            if not token:
                self._repair_declined = True
                return False
            self.set_access_token(token)
            self.persist(token)
            self.logger.info("Paired again; resuming uploads.")
            return True

    def _pair(self) -> str | None:
        """Pair this machine at the console; the new token, or None."""
        return run_pairing(
            self.host, verify=self.verify_tls, instrument=self.instrument
        )

    def check_at_start(self) -> None:
        """Ask the server about this machine's credential before any file needs it.

        A credential that lapsed while the machine was off, or was revoked, is
        only discovered when the first acquisition tries to upload - which is the
        worst moment for whoever is running the instrument, and the point at which
        data is already waiting. Asking once at startup moves that discovery to
        where a person is most likely to be looking at the window.

        Only an answered refusal is offered a repair. A machine that just booted
        may have no network yet, and a server may be restarting; pairing fixes
        neither, so those are logged and left to the upload retries.
        """
        outcome, message = check_credential(
            self.host,
            self.current_access_token(),
            verify=self.verify_tls,
        )
        if outcome == CREDENTIAL_OK:
            return
        if outcome == CREDENTIAL_REJECTED:
            if not self.offer_repair(self.current_access_token(), message):
                self.logger.error(
                    f"This machine's Mascope credential was refused: {message} "
                    "Uploads will fail until it is paired again - "
                    f"{self.repair.advice_at_start}"
                )
            return
        self.logger.info(
            f"Could not confirm this machine's credential at startup: {message} "
            "Continuing; uploads and token renewal retry on their own."
        )
