"""The File Agent as an object: build it, start it, stop it.

:class:`Agent` is what ``mascope-file-agent`` runs, and what a program that
embeds the agent builds for itself::

    from mascope_file_agent import Agent, AgentSettings, identity

    settings = AgentSettings.from_file(config_path)
    identity("file-agent", version="1.2.3", verify_tls=settings.verify_tls)
    agent = Agent(settings, persist_token=settings.token_writer(config_path))
    agent.start()
    ...
    agent.stop()
"""

import logging
import time
from threading import Event, RLock
from typing import Callable

import mascope_sdk
from mascope_file_agent import __version__
from mascope_file_agent import config as agent_config
from mascope_file_agent._threads import Task
from mascope_file_agent.capabilities import ServerCapabilities
from mascope_file_agent.config import ConfigError
from mascope_file_agent.credentials import Credentials, Repair
from mascope_file_agent.provenance import UploadProvenance
from mascope_file_agent.settings import AgentSettings
from mascope_file_agent.status import StatusFollower
from mascope_file_agent.uploader import FAILED_UPLOADS_DIR, FileUploader
from mascope_file_agent.watcher import FileSystemWatcher


#: The service a File Agent pairs and uploads as.
SERVICE_NAME = "file-agent"

#: The name the SDK reports for a process that has not said who it is.
SDK_SERVICE_NAME = "mascope_sdk"

#: Seconds the renewal and the status threads are given to end when stop() is
#: given no deadline of its own. They end at once unless one is in the middle
#: of a request, and a server that does not answer is no reason to hold up a
#: stop for the length of that request's timeout.
HELPER_STOP_TIME = 5


def identity(
    service_name: str = SERVICE_NAME,
    version: str | None = None,
    verify_tls: bool = True,
) -> None:
    """Say who this process is on every request it makes to the server.

    The SDK the agent talks through keeps one identity for the whole process:
    the service name a device token is scoped to, the version shown for the
    paired machine, and whether the server's TLS certificate is verified. A
    process that runs an agent sets it once, before pairing and before the
    agent starts; nothing sets it on import. Every call sets all three: one
    that leaves an argument out sets its default.

    :param service_name: The service the machine is paired as
    :type service_name: str
    :param version: The version to report; None for this package's own
    :type version: str | None
    :param verify_tls: Whether uploads verify the server's TLS certificate
    :type verify_tls: bool
    """
    mascope_sdk.SERVICE_NAME = service_name
    # Sent as a header on every request, so the server can show which release each
    # paired machine runs; a server that does not know the header ignores it.
    mascope_sdk.AGENT_VERSION = __version__ if version is None else version
    mascope_sdk.VERIFY_TLS = verify_tls


def resolve_timezone(configured: str | None) -> str | None:
    """The IANA timezone to report with uploads, or None if it is unknown.

    A raw file records the acquisition time in the instrument PC's local time
    and (for most vendors) no offset, so the converter can only turn it into
    UTC if it knows which zone that was. Reporting it here is what makes the
    stored timestamp right for an instrument in a different zone from the
    server, and right across a DST boundary for a backlogged file.

    An explicitly configured zone wins over detection. Detection is a
    best-effort read of the operating system's setting: on Windows that names
    a *group* of zones rather than a city, so a machine can resolve to a
    neighbouring city whose historical DST rules differ - hence the override.
    Returning None is fine and simply leaves the server on its own zone, the
    behaviour from before any zone was reported.

    :param configured: The ``timezone`` setting, empty when unset.
    :type configured: str | None
    :return: An IANA zone name, or None when it could not be determined.
    :rtype: str | None
    """
    if configured and configured.strip():
        name = configured.strip()
        # Checked here rather than left for the converter: a typo would
        # otherwise be accepted in silence, reported on every upload, and
        # rejected only in the server's log, where the operator who set it
        # never looks. Falling through to detection beats reporting a name
        # nothing can load.
        if _is_known_timezone(name):
            return name
        return _detected_timezone()
    return _detected_timezone()


def _detected_timezone() -> str | None:
    """This machine's IANA zone as the operating system reports it, or None."""
    try:
        import tzlocal

        return tzlocal.get_localzone_name()
    except Exception:
        # Never fatal - the upload matters more than its provenance - and kept
        # free of the logger so this stays a pure function; the caller reports
        # the outcome either way.
        return None


def _is_known_timezone(name: str) -> bool:
    """Whether ``name`` is a zone this machine can resolve.

    A machine without the zone database cannot tell, and says yes: the
    converter validates again, so a false yes costs a log line, while a false
    no would discard a setting that is very likely correct.

    :param name: The candidate IANA zone name.
    :type name: str
    :return: True when the name loads, or when it cannot be checked here.
    :rtype: bool
    """
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(name)
            return True
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return False
    except Exception:
        return True


class Agent:
    """Watch a folder and upload each new file in it to a Mascope server.

    Owns everything a running agent consists of: the folder watcher, the
    uploader and its workers, the token renewal, the follower that logs what
    the server made of each upload, and the one event that stops them all.

    An agent runs once. :meth:`run_until_complete` runs it on the calling
    thread until it is interrupted, which is what the console program does;
    :meth:`start` runs it on threads of its own and returns, for a program
    that has other work on its main thread, and :meth:`stop` ends it either
    way. To run again, build another.

    The process says who it is with :func:`identity` before starting one.

    :param settings: What to watch, where to upload, and as which instrument.
    :param url: The server's base URL; None to take it from ``settings.host``,
        with https unless the host names a scheme. Given, it is where
        everything the agent asks goes - uploads, the renewal, what became of
        each upload, the check at start and a pairing - and ``settings.host``
        is not asked anything.
    :param logger: Where the agent's lines go: anything with the methods of a
        standard :class:`logging.Logger`. None for the ``mascope_file_agent``
        logger.
    :param repair: What to do when the server refuses this machine's
        credential. None to only say so in the log.
    :param persist_token: Called with each new access token, so that a restart
        keeps using it; see :meth:`AgentSettings.token_writer`. None to keep it
        in memory only.
    :raises ConfigError: When the settings name no server, no valid
        instrument, or a watched folder that does not exist.
    """

    def __init__(
        self,
        settings: AgentSettings,
        url: str | None = None,
        logger=None,
        repair: Repair | None = None,
        persist_token: Callable[[str], None] | None = None,
    ):
        if not url and not settings.host:
            raise ConfigError(
                "No Mascope server is configured. Set 'host' in the agent "
                "configuration."
            )
        settings.validate()
        self.settings = settings
        self.url = url or agent_config.base_url(settings.host)
        self.logger = logger or logging.getLogger("mascope_file_agent")
        #: Set when the agent is stopping or has stopped.
        self.shutdown_event = Event()
        #: IANA timezone reported with each upload; None when this machine
        #: could not name its zone.
        self.timezone = resolve_timezone(settings.timezone)
        #: Name of the instrument reported when pairing and with each upload.
        self.instrument = settings.instrument.strip()
        self.credentials = Credentials(
            self.url,
            settings.access_token,
            self.logger,
            verify_tls=settings.verify_tls,
            instrument=self.instrument,
            persist=persist_token,
            repair=repair,
        )
        #: Asked what the server can do, by whatever needs to know.
        self.server = ServerCapabilities(
            self.url,
            self.credentials.current_access_token,
            self.logger,
            verify=settings.verify_tls,
        )
        self.status_follower = StatusFollower(
            self.url,
            self.credentials.current_access_token,
            self.logger,
            verify=settings.verify_tls,
            server=self.server,
        )
        self.uploader = FileUploader(
            settings,
            self.url,
            self.credentials,
            logger=self.logger,
            shutdown_event=self.shutdown_event,
            timezone=self.timezone,
            instrument=self.instrument,
            status_follower=self.status_follower,
            provenance=UploadProvenance(self.server, self.logger),
        )
        self.watcher = FileSystemWatcher(
            settings.source,
            settings.mask,
            recursive=settings.recursive,
            on_ready=[self.uploader.enqueue],
            on_dropped=self.uploader.not_uploaded,
            logger=self.logger,
            shutdown_event=self.shutdown_event,
            ignored_folders=(FAILED_UPLOADS_DIR,),
        )
        self._lock = RLock()
        self._started = False
        self._stop_asked = False
        self._thread: Task | None = None
        self._helpers: list[Task] = []

    @property
    def running(self) -> bool:
        """Whether the agent was started and has not been told to stop."""
        return self._started and not self.shutdown_event.is_set()

    def on_ready(self, callback: Callable[[str], None]) -> None:
        """Have ``callback`` called with each complete file before its upload.

        The callbacks run on the watcher's thread in the order they were
        added, and the file is handed to the uploader after the last of them:
        whatever one writes beside the file is there when it is uploaded. That
        is where a program writes the file's acquisition record,
        ``<file>.mascope.json``, which then goes with the upload
        (:mod:`mascope_sdk.acquisition`). An error in one is logged, and the
        file goes on to the rest.

        :param callback: Takes the file's full path
        :type callback: Callable[[str], None]
        """
        # The uploader is always last.
        self.watcher.on_ready.insert(len(self.watcher.on_ready) - 1, callback)

    def start(self) -> None:
        """Start watching and uploading on the agent's own threads, and return.

        :raises RuntimeError: If this agent was started before, or the process
            has not said who it is with :func:`identity`
        """
        # Under the lock stop() takes, so that a stop from another thread
        # finds the agent either not started or started, never in between.
        with self._lock:
            self._begin()
            self._thread = Task(self._run_on_thread, "file-agent")
            self._thread.start()

    def run_until_complete(self) -> None:
        """Watch and upload on the calling thread until interrupted or stopped.

        An interrupt (Ctrl+C) stops the agent, which then waits for every
        upload it has handed to a worker. A second one during that wait is
        answered with a line in the log and more waiting; a third stops
        without them.

        :raises RuntimeError: If this agent was started before, or the process
            has not said who it is with :func:`identity`
        """
        with self._lock:
            self._begin()
        try:
            self._run()
        finally:
            self._stop_patiently()

    def _stop_patiently(self) -> None:
        """Stop without a deadline, as the console program does when interrupted."""
        try:
            self.stop()
        except KeyboardInterrupt:
            self.logger.warning(
                "Still waiting for the uploads under way to end. Interrupt once "
                "more to stop without them."
            )
            try:
                self.stop()
            except KeyboardInterrupt:
                self.stop(timeout=0)

    def stop(self, timeout: float | None = None) -> bool:
        """Stop watching, let the uploads under way end, and stop the rest.

        Files that had not been handed to an upload worker yet are not
        uploaded, and each is named in the log. With a ``timeout``, uploads
        that have not ended when it runs out are abandoned: one waiting for a
        retry or for a free worker is named in the log, and one in the middle
        of a request is left to its thread, which does not keep the process
        from exiting. Safe to call more than once, and before :meth:`start`.

        :param timeout: Seconds to wait; None to wait until every upload
            handed to a worker has ended
        :type timeout: float | None
        :return: Whether everything ended in that time
        :rtype: bool
        """
        self.shutdown_event.set()
        with self._lock:
            self._stop_asked = True
            if not self._started:
                return True
        deadline = None if timeout is None else time.monotonic() + timeout

        def remaining() -> float | None:
            return None if deadline is None else max(0.0, deadline - time.monotonic())

        stopped = self.watcher.stop(remaining())
        stopped = self._join(self._thread, remaining()) and stopped
        stopped = self.uploader.finish(remaining()) and stopped
        for helper in list(self._helpers):
            wait = HELPER_STOP_TIME if deadline is None else remaining()
            stopped = self._join(helper, wait) and stopped
        return stopped

    @staticmethod
    def _join(task: Task | None, timeout: float | None) -> bool:
        """Wait for one of the agent's threads; whether it has ended."""
        if task is None or task.is_current:
            return True
        return task.wait(timeout)

    def _begin(self) -> None:
        """Start what watches and uploads; the steps before the credential check."""
        if mascope_sdk.SERVICE_NAME == SDK_SERVICE_NAME:
            raise RuntimeError(
                "This process has not said who it is to the server. Call "
                "mascope_file_agent.identity() before starting an agent."
            )
        with self._lock:
            if self._started or self.shutdown_event.is_set():
                raise RuntimeError(
                    "This agent was started or stopped before. Build a new one "
                    "to run again."
                )
            self._started = True
        if mascope_sdk.VERIFY_TLS != self.settings.verify_tls:
            self.logger.warning(
                "The agent's 'verify_tls' setting is "
                f"{str(self.settings.verify_tls).lower()}, but the process was "
                f"told verify_tls={str(mascope_sdk.VERIFY_TLS).lower()} in "
                "identity(); uploads follow identity()."
            )

        configured_tz = (self.settings.timezone or "").strip()
        if configured_tz and configured_tz != self.timezone:
            self.logger.warning(
                f"Configured timezone '{configured_tz}' is not a zone this machine "
                "can resolve, so it was ignored. Use an IANA name such as "
                "'Europe/Helsinki'."
            )
        if self.instrument:
            self.logger.info(f"Reporting instrument: {self.instrument}")

        if self.timezone:
            self.logger.info(f"Reporting acquisition timezone: {self.timezone}")
        else:
            self.logger.warning(
                "Could not determine this machine's timezone; acquisition times "
                "will be resolved with the server's timezone instead. Set "
                "'timezone' in the agent configuration to report it explicitly."
            )

        try:
            self.uploader.start()
            self.watcher.start()
        except BaseException:
            # A watched folder that has gone since the agent was built, say:
            # the workers must not be left waiting for an agent that never ran.
            self.stop()
            raise

    def _run_on_thread(self) -> None:
        """Run on the agent's own thread, and leave nothing behind."""
        try:
            self._run()
        except Exception:
            # Nobody is waiting for what this thread raises, and without this
            # line an agent that died at its start would only be seen to stop.
            self.logger.exception("The agent stopped on an unexpected error")
        finally:
            # Ended without being asked to: nobody else is going to stop the
            # watcher and the workers.
            with self._lock:
                asked = self._stop_asked
            if not asked:
                self.stop()

    def _run(self) -> None:
        """Check the credential, then keep it alive and upload until stopped."""
        try:
            if not self.shutdown_event.is_set():
                self.credentials.check_at_start()
            helpers = [
                Task(
                    self.credentials.renewal_loop,
                    "file-agent-renewal",
                    (self.shutdown_event,),
                ),
                Task(
                    self.status_follower.run,
                    "file-agent-status",
                    (self.shutdown_event,),
                ),
            ]
            for helper in helpers:
                helper.start()
            # Only once they run: stop() joins what it finds here.
            self._helpers = helpers
            self.uploader.run_until_complete()
        finally:
            # However this ended, the threads that wait on the event end too.
            self.shutdown_event.set()
