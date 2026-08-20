import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Event, Queue
from queue import Empty
from threading import Lock, Thread

import watchdog
from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer

import mascope_sdk
from mascope_file_agent import __version__
from mascope_file_agent import config as agent_config
from mascope_file_agent.config import ConfigError
from mascope_file_agent.wizard import (
    CREDENTIAL_OK,
    CREDENTIAL_REJECTED,
    check_credential,
    run_pairing,
    run_setup_wizard,
)
from mascope_runtime import Runtime


mascope_sdk.SERVICE_NAME = "file-agent"
from mascope_sdk import (  # noqa: E402  (needs SERVICE_NAME set first)
    api_post_file_tus,
    api_renew_agent_token,
)
from mascope_sdk.exceptions import (  # noqa: E402
    AuthenticationError,
    NotFoundError,
    TusNotSupportedError,
    ValidationError,
)


# Seconds after startup before the first token renewal, then between renewals
# when the server reports no lifetime, and after a transient renewal failure.
RENEW_INITIAL_DELAY = 60
RENEW_FALLBACK_INTERVAL = 7 * 24 * 60 * 60  # 7 days
RENEW_RETRY_DELAY = 5 * 60  # 5 minutes
RENEW_MIN_INTERVAL = 60 * 60  # never renew more often than hourly

# The live access token. Held apart from runtime.config so the renewal loop can
# rotate it under the uploader without a restart; both are guarded by the lock.
_token_lock = Lock()
_access_token = None

# IANA timezone reported with each upload, resolved once at start (see
# resolve_timezone). None when this machine could not name its zone.
_timezone = None

# Set in prod mode so the renewal loop can persist a rotated token to the
# user-facing config.toml (None in dev, where the CLI owns the config).
_config_path = None
_settings = None


def current_access_token() -> str | None:
    """The access token uploads should use right now (renewal may rotate it)."""
    with _token_lock:
        return _access_token


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


def _set_access_token(token: str) -> None:
    """Update the live access token used by subsequent uploads."""
    global _access_token
    with _token_lock:
        _access_token = token


def _persist_token(token: str) -> None:
    """Write a rotated token back to config.toml so a restart keeps using it.

    A no-op in dev mode, where there is no user config file to own.
    """
    if not _config_path or _settings is None:
        return
    _settings["access_token"] = token
    try:
        agent_config.write_user_config(_config_path, _settings)
    except OSError as e:
        # The in-memory token still works this session; only restart continuity
        # is at risk, so warn rather than fail.
        runtime.logger.warning(f"Could not persist the renewed token: {e}")


def _renewal_loop(stop_event) -> None:
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
                    URL, current_access_token()
                )
            except (TusNotSupportedError, AuthenticationError) as e:
                # 404: this server has no renewal endpoint (older release).
                # 401: the credential is not a renewable device token. Neither
                # is worth a tight retry, but neither proves the condition is
                # permanent - a rolling restart or a proxy blip answers both
                # the same way - so back off instead of ending the loop. A
                # thread that returns here never renews again, and the token
                # then lapses silently 30 days later.
                runtime.logger.info(f"Token renewal unavailable, backing off: {e}")
                delay = RENEW_FALLBACK_INTERVAL
                continue
            except Exception as e:
                runtime.logger.info(f"Token renewal failed, will retry: {e}")
                delay = RENEW_RETRY_DELAY
                continue

            _set_access_token(new_token)
            _persist_token(new_token)
            runtime.logger.info("Renewed the agent access token.")
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
            runtime.logger.exception("Token renewal loop error; continuing")
            delay = RENEW_RETRY_DELAY


def _start_token_renewal(stop_event) -> None:
    """Start the background token-renewal thread (daemon)."""
    thread = Thread(target=_renewal_loop, args=(stop_event,), daemon=True)
    thread.start()


HOST = None
PORT = None
URL = None
SHUTDOWN_EVENT = Event()

runtime = None

executor = ThreadPoolExecutor(max_workers=3)


def get_upload_filename(filepath: str) -> str | None:
    """Compute the upload filename by applying configured prefix and/or suffix.

    Returns the modified filename if prefix or suffix is configured,
    otherwise returns None (indicating the original filename should be used).

    :param filepath: Full path to the file
    :type filepath: str
    :return: Modified filename or None
    :rtype: str | None
    """
    prefix = runtime.config.filename_prefix or ""
    suffix = runtime.config.filename_suffix or ""
    if not prefix and not suffix:
        return None
    for label, value in (("filename_prefix", prefix), ("filename_suffix", suffix)):
        if any(sep in value for sep in ("/", "\\", os.sep) if sep) or ".." in value:
            raise ValueError(f"{label} contains invalid characters: {value!r}")
    basename = os.path.basename(filepath)
    stem, ext = os.path.splitext(basename)
    return f"{prefix}{stem}{suffix}{ext}"


# Serializes the re-pair offer: three upload workers hitting a dead credential
# at once must not each prompt. Declining is remembered so the console does not
# nag on every file for the rest of the session.
_repair_lock = Lock()
_repair_declined = False

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


def _offer_repair(token_used: str, reason: str) -> bool:
    """Offer to pair this machine again after the server refused its credential.

    Recovery used to mean re-launching the agent with ``--setup``, which is a
    lot to ask of whoever runs the instrument: the console is already open in
    front of them and the credential is already known to be dead. Pairing needs
    a person with an editor account to approve in the browser either way, so
    asking here changes nothing about who may connect a machine.

    :param token_used: The credential the failed attempt used; a different live
        token means another worker already fixed it.
    :type token_used: str
    :param reason: The server's explanation, shown to the operator.
    :type reason: str
    :return: Whether the caller should retry the upload.
    :rtype: bool
    """
    global _repair_declined

    if not (sys.stdin and sys.stdin.isatty()):
        return False  # started without a console; the log line has to do

    with _repair_lock:
        if current_access_token() != token_used:
            return True  # another worker re-paired while this one waited
        if _repair_declined:
            return False
        print(REPAIR_BANNER.format(reason=reason))
        try:
            answer = input("Pair this machine again now? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _repair_declined = True
            return False
        if answer in ("n", "no"):
            _repair_declined = True
            print(REPAIR_DECLINED_NOTE)
            return False

        token = run_pairing(HOST, verify=getattr(runtime.config, "verify_tls", True))
        if not token:
            _repair_declined = True
            return False
        _set_access_token(token)
        _persist_token(token)
        runtime.logger.info("Paired again; resuming uploads.")
        return True


def _check_credential_at_start() -> None:
    """Ask the server about this machine's credential before any file needs it.

    A credential that lapsed while the machine was off, or was revoked, is
    only discovered when the first acquisition tries to upload - which is the
    worst moment for whoever is running the instrument, and the point at which
    data is already waiting. Asking once at startup moves that discovery to
    where a person is most likely to be looking at the window.

    Only an answered refusal prompts. A machine that just booted may have no
    network yet, and a server may be restarting; pairing fixes neither, so
    those are logged and left to the upload retries.
    """
    outcome, message = check_credential(
        HOST,
        current_access_token(),
        verify=getattr(runtime.config, "verify_tls", True),
    )
    if outcome == CREDENTIAL_OK:
        return
    if outcome == CREDENTIAL_REJECTED:
        if not _offer_repair(current_access_token(), message):
            runtime.logger.error(
                f"This machine's Mascope credential was refused: {message} "
                "Uploads will fail until it is paired again - start the "
                "Mascope File Agent from the Start Menu and answer the prompt."
            )
        return
    runtime.logger.info(
        f"Could not confirm this machine's credential at startup: {message} "
        "Continuing; uploads and token renewal retry on their own."
    )


def process_file_upload(filepath: str, max_retries: int = 10) -> None:
    """Process file upload

    :param filepath: Full path to the file to be uploaded
    :type filepath: str
    """
    for attempt in range(1, max_retries + 1):
        token_used = current_access_token()
        try:
            upload_sample_file(filepath)
            return
        except ValueError as ve:
            runtime.logger.error(f"File upload failed: {ve}")
            break  # do not retry on validation errors
        except AuthenticationError as e:
            if _offer_repair(token_used, str(e)):
                continue  # fresh credential in hand - try this file again
            runtime.logger.error(
                f"File upload failed for file {os.path.basename(filepath)}: {e} "
                "Retrying will not help - the server rejected this machine's "
                "credential. It may have been revoked, or have expired while "
                "the agent was offline. Start the Mascope File Agent again "
                "from the Start Menu and it will offer to pair this machine."
            )
            break  # a rejected token stays rejected until the machine re-pairs
        except (NotFoundError, ValidationError) as e:
            runtime.logger.error(
                f"File upload failed for file {os.path.basename(filepath)}: {e} "
                "Retrying will not help - the server rejected the request. "
                "A 404 usually means the configured host is not the Mascope "
                "API (in development setups the frontend dev server cannot "
                "receive uploads; use the backend address, e.g. "
                "http://localhost:8090), or that the server is too old to "
                "accept agent uploads. Fix 'host' in the file-agent "
                "configuration and restart the agent, or update the server."
            )
            break  # a wrong address or rejected payload cannot heal by waiting
        except Exception as e:
            # Timeouts, connection and server errors are transient - retry.
            # The message carries the specific cause (e.g. connection refused,
            # HTTP status + server error message).
            # INFO per attempt: retries are routine on a flaky network; the
            # final give-up below logs at ERROR
            runtime.logger.info(
                f"Upload attempt {attempt}/{max_retries} for file "
                f"{os.path.basename(filepath)} failed: {e}"
            )
            runtime.logger.info("Retrying upload in 30 seconds...")
            time.sleep(30)
    # Max retries exceeded, give up
    runtime.logger.error(
        f"File upload failed for file {os.path.basename(filepath)} after {attempt} attempts"
    )
    # Move failed file into a separate directory
    failed_dir = mkdir(runtime.config.source, "failed_uploads")
    failed_filepath = os.path.join(failed_dir, os.path.basename(filepath))
    shutil.copyfile(filepath, failed_filepath)
    runtime.logger.debug(f"Copied failed file to {failed_filepath}")


def upload_sample_file(filepath: str) -> None:
    """Upload the acquired file to Mascope server using Mascope API

    Uploads with the resumable TUS protocol (chunked, no size limit).
    There is no fallback to the legacy single-request endpoint: every
    supported server accepts agent TUS uploads, so a refusal here is a real
    failure - a rejected credential above all - and must be reported as one
    rather than retried against a capped endpoint.

    :param filepath: Full path to the file to be uploaded
    :type filepath: str
    :raises Exception: Raises an exception if the request fails (status code != 200)
    """
    # Validate file extension before upload request
    file_ext = os.path.splitext(filepath)[1].lower()
    mask_ext = os.path.splitext(runtime.config.mask)[1].lower()
    if file_ext != mask_ext:
        raise ValueError(f"{file_ext} is not an allowed file extension!")

    runtime.logger.debug(f"Making an upload request to {URL} for file {filepath}")
    upload_filename = get_upload_filename(filepath)
    if upload_filename:
        runtime.logger.info(
            f"Uploading file {os.path.basename(filepath)} as {upload_filename}"
        )

    # Raises a typed mascope_sdk exception carrying the specific cause
    # (rejected token, timeout, connection error, server error message).
    api_post_file_tus(
        url=URL,
        # The live token, not runtime.config's boot-time snapshot: renewal
        # rotates it and the server reaps the superseded ones, so a stale copy
        # here would 401 non-retryably while the agent holds a valid credential.
        access_token=current_access_token(),
        filepath=filepath,
        upload_filename=upload_filename,
        timezone=_timezone,
    )
    runtime.logger.info(f"File upload of file {os.path.basename(filepath)} succeeded!")


def mkdir(*args: tuple) -> str:
    """
    Creates a directory at the specified path if it does not already exist.

    :param args: Components of the path to be joined.
    :type args: tuple
    :return: The path of the created directory.
    :rtype: str
    """

    path = os.path.join(*args)
    os.makedirs(path, exist_ok=True)
    return path


def resolve_settings(mascope_path: str, env_path: str) -> dict:
    """Load the agent settings, running the guided setup when needed.

    Settings come from the single user-facing ``config.toml`` at the root
    of `mascope_path`. When it is missing, settings from a pre-config.toml
    install are migrated; when required settings are still missing (or the
    agent was started with ``--setup``), the interactive wizard collects
    them and writes the file.

    :param mascope_path: The agent's data directory (MASCOPE_PATH)
    :type mascope_path: str
    :param env_path: Path of the ``.runtime/env/prod`` directory
    :type env_path: str
    :return: Complete, validated settings dict
    :rtype: dict
    :raises ConfigError: When settings are missing and the wizard cannot run,
        or the watched folder does not exist
    """
    config_path = os.path.join(mascope_path, agent_config.CONFIG_FILENAME)
    if os.path.exists(config_path):
        settings = agent_config.load_user_config(config_path)
    else:
        settings = agent_config.load_legacy_config(env_path)
        if settings:
            agent_config.write_user_config(config_path, settings)
            print(f"Migrated existing settings to {config_path}")
        else:
            settings = agent_config.merge_settings({})

    if "--setup" in sys.argv[1:] or agent_config.missing_settings(settings):
        if not (sys.stdin and sys.stdin.isatty()):
            raise ConfigError(
                "The agent is not configured. Start it in a console to use "
                "the guided setup, or fill in host, access_token and source "
                f"in:\n  {config_path}"
            )
        settings = run_setup_wizard(settings)
        agent_config.write_user_config(config_path, settings)
        print(f"Settings saved to {config_path}\n")

    if not os.path.isdir(settings["source"]):
        raise ConfigError(
            f"The watched folder does not exist: {settings['source']}\n"
            f"Update 'source' in {config_path}, or restart the agent "
            "with --setup to run the guided setup again."
        )
    return settings


def initialize() -> None:
    """Initialize the application and runtime depending on dev/prod mode

    If in prod mode, check if runtime directory structure exists, and create if not.

    :return: Return nothing
    :rtype: None
    """
    global runtime
    # check if we are running in a pyinstaller bundle
    bundled = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    if bundled:
        # prod mode
        # set MASCOPE_PATH as %AppData%\Mascope\FileAgent
        mascope_path = mkdir(os.environ["APPDATA"], "Mascope", "FileAgent")
        os.environ.setdefault("MASCOPE_PATH", mascope_path)
        # setup runtime environment
        env_path = mkdir(mascope_path, ".runtime", "env", "prod")
        mkdir(mascope_path, "logs")
        # resolve user settings (guided setup on first run) and regenerate
        # the runtime-format config the mascope_runtime loader reads
        settings = resolve_settings(mascope_path, env_path)
        agent_config.write_runtime_config(env_path, settings, mascope_path)
        # Remember where the config lives so the renewal loop can persist a
        # rotated token back to it.
        global _config_path, _settings
        _config_path = os.path.join(mascope_path, agent_config.CONFIG_FILENAME)
        _settings = settings
        # initialize the runtime in production mode
        runtime = Runtime("file-agent", env="prod", mode="prod", path=mascope_path)
    else:
        # dev mode
        # runtime state inherited from the CLI
        runtime = Runtime("file-agent")


class FileSystemWatcher:
    """Watch for file system events in a specified directory"""

    class FileSystemEventHandler(PatternMatchingEventHandler):
        """File system event handler

        Implement callbacks for file system events.

        :param PatternMatchingEventHandler: Event handler from the watchdog package
        :type PatternMatchingEventHandler: watchdog.events.PatternMatchingEventHandler
        """

        def __init__(self, client, patterns):
            self.client = client
            super().__init__(patterns=patterns)

        def on_created(self, event: watchdog.events.FileSystemEvent) -> None:
            """New file created

            :param event: Filesystem event
            :type event: watchdog.events.FileSystemEvent
            """
            try:
                self.client.on_filesystem_object_created(event.src_path)
            except Exception:
                runtime.logger.exception("Unexpected error handling filesystem event")

        def on_moved(self, event: watchdog.events.FileSystemEvent) -> None:
            """File moved

            :param event: Filesystem event
            :type event: watchdog.events.FileSystemEvent
            """
            try:
                self.client.on_filesystem_object_created(event.dest_path)
            except Exception:
                runtime.logger.exception("Unexpected error handling filesystem event")

    def __init__(self, client, path: str, mask: str, recursive=False):
        self.client = client
        self.path = path
        self.mask = mask
        self.recursive = recursive
        self.observer = Observer()
        self.handler = self.FileSystemEventHandler(self.client, patterns=[self.mask])

    def start(self) -> None:
        """Start watching.

        Start `FileSystemEventHandler`
        """
        self.observer.schedule(self.handler, self.path, recursive=self.recursive)
        self.observer.start()
        scope = " and its subfolders" if self.recursive else ""
        runtime.logger.info(
            f"Started watching {self.path}{scope} for new files "
            f"matching pattern '{self.mask}'"
        )

    def stop(self) -> None:
        """Stop watching.

        Stop `FileSystemEventHandler`
        """
        self.observer.stop()
        self.observer.join()
        runtime.logger.info("File system watcher stopped")

    def run(self) -> None:
        """Main loop

        Start `FileSystemEventHandler` and do nothing.
        """
        self.start()
        while not self.client.shutdown_event.is_set():
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                self.client.shutdown_event.set()
            except Exception:
                runtime.logger.exception("Unexpected error in the watcher loop")
        self.stop()

    def run_as_daemon(self):
        """Run as daemon"""
        t = Thread(target=self.run)
        t.daemon = True
        t.start()


class FileUploader:
    """Watch for new files matching a specified `mask` in `source` directory, upload to
    Mascope after file has not been accessed for specified timeout period.
    """

    def __init__(self, source_path: str, mask: str, recursive: bool = False):
        self.shutdown_event = Event()
        self.jobs = Queue()
        self.watcher = FileSystemWatcher(
            client=self, path=source_path, mask=mask, recursive=recursive
        )

    def on_filesystem_object_created(self, fname: str) -> None:
        """Callback on file created.

        First wait while filesize is changing. Then check file access
        by dummy rename operation. Finally, put file into `self.jobs` queue.

        :param fname: File path
        :type fname: str
        """
        # failed_uploads holds copies of files that already failed; watching
        # it recursively would re-upload (and re-fail) them in a loop
        if "failed_uploads" in os.path.normpath(fname).split(os.sep):
            runtime.logger.debug(f"Ignoring file in failed_uploads: {fname}")
            return
        runtime.logger.info(f"File created: {fname}")
        # Wait until the file is ready
        filesize = -1
        while True:
            while filesize != os.path.getsize(fname):
                filesize = os.path.getsize(fname)
                time.sleep(1)
            try:
                os.rename(fname, fname)
                break
            except PermissionError:
                runtime.logger.debug(f"File {fname} is not ready")
                time.sleep(1)
        self.jobs.put(fname)

    def seconds_since_last_access(self, fname: str) -> float:
        """Count the seconds since the file was last accessed

        :param fname: Path of the file
        :type fname: str
        :return: Seconds since last access
        :rtype: float
        """
        return time.time() - os.stat(fname).st_atime

    def run_until_complete(self):
        """
        Main loop that continuously checks for jobs to process and uploads files if necessary.

        This method runs in a loop until the `shutdown_event` is set. It periodically checks
        for new jobs from the `jobs` queue and processes them. If a job is found, it checks the
        time since the last access and decides whether to requeue the job or upload the file.
        The loop handles several exceptions to ensure smooth operation and logs critical errors.

        Exceptions Handled:
            - Empty: Raised when the `jobs` queue is empty.
            - FileNotFoundError: Raised when the file to be uploaded is not found.
            - SameFileError: Raised when there is an attempt to upload the same file.
            - KeyboardInterrupt: Raised when the process is interrupted by the user.
            - Exception: Catches all other exceptions and logs them as critical errors.

        The method ensures that the `shutdown_event` is set when exiting, either normally or due
        to an exception.
        """
        try:
            while not self.shutdown_event.is_set():
                time.sleep(1)
                fname = None
                try:
                    fname = self.jobs.get_nowait()
                    runtime.logger.debug(fname)
                    if self.seconds_since_last_access(fname) < runtime.config.timeout:
                        self.jobs.put(fname)
                        runtime.logger.debug(f"Put {fname} back to queue")
                        continue
                    # Submit file upload task for the thread pool executor
                    executor.submit(process_file_upload, fname)
                except Empty:
                    continue

        except KeyboardInterrupt:
            runtime.logger.info("Shutdown requested by user.")
        except Exception:
            runtime.logger.exception("Unexpected error in the upload loop")
        finally:
            self.shutdown_event.set()


def pause_before_exit() -> None:
    """Keep the console window open so double-click users can read the error."""
    if getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        try:
            input("Press Enter to exit...")
        except EOFError:
            pass


def run() -> None:
    """Main function of the application

    Start `FileUploader` thread and wait until it finishes
    """
    # Initialize runtime
    try:
        initialize()
    except ConfigError as e:
        print(f"\nConfiguration error:\n{e}\n")
        if runtime is not None:
            # Also record it in the agent log: without this a misconfigured
            # prod agent (started headless) dies invisibly
            runtime.logger.error(f"Configuration error: {e}")
        pause_before_exit()
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(1)

    runtime.logger.info(f"Mascope File Agent {__version__}")

    global URL
    global HOST
    global PORT

    PORT = runtime.meta.api_port
    HOST = runtime.config.host
    match runtime.mode:
        case "dev":
            URL = f"http://{HOST}:{PORT}"
        case "prod":
            # https unless the host is configured with an explicit scheme
            URL = agent_config.base_url(HOST) if HOST else None
    if not URL:
        runtime.logger.error(
            "Mascope host not defined, please check configuration. Exiting..."
        )
        raise RuntimeError("Mascope host not defined, please check configuration.")

    if not os.path.isdir(runtime.config.source):
        raise RuntimeError(f"Invalid source directory {runtime.config.source}")

    # TLS verification and the live token are read from config here; the
    # renewal loop rotates the token under the uploader as it runs.
    mascope_sdk.VERIFY_TLS = getattr(runtime.config, "verify_tls", True)
    _set_access_token(runtime.config.access_token)

    global _timezone
    _timezone = resolve_timezone(getattr(runtime.config, "timezone", ""))
    configured_tz = (getattr(runtime.config, "timezone", "") or "").strip()
    if configured_tz and configured_tz != _timezone:
        runtime.logger.warning(
            f"Configured timezone '{configured_tz}' is not a zone this machine "
            "can resolve, so it was ignored. Use an IANA name such as "
            "'Europe/Helsinki'."
        )
    if _timezone:
        runtime.logger.info(f"Reporting acquisition timezone: {_timezone}")
    else:
        runtime.logger.warning(
            "Could not determine this machine's timezone; acquisition times "
            "will be resolved with the server's timezone instead. Set "
            "'timezone' in the agent configuration to report it explicitly."
        )

    uploader = FileUploader(
        runtime.config.source, runtime.config.mask, recursive=runtime.config.recursive
    )
    uploader.watcher.run_as_daemon()
    _check_credential_at_start()
    _start_token_renewal(uploader.shutdown_event)
    uploader.run_until_complete()
    executor.shutdown(wait=True)


if __name__ == "__main__":
    run()
