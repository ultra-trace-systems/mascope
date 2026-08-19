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
from mascope_file_agent.wizard import run_setup_wizard
from mascope_runtime import Runtime


mascope_sdk.SERVICE_NAME = "file-agent"
from mascope_sdk import (  # noqa: E402  (needs SERVICE_NAME set first)
    api_post_file,
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

# Set in prod mode so the renewal loop can persist a rotated token to the
# user-facing config.toml (None in dev, where the CLI owns the config).
_config_path = None
_settings = None


def current_access_token() -> str | None:
    """The access token uploads should use right now (renewal may rotate it)."""
    with _token_lock:
        return _access_token


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
    half the server-reported lifetime. Stops quietly when the server has no
    renewal endpoint (older release) or the token is not renewable (a manual
    or expired token): uploads then continue on the current token and surface
    their own actionable error if it has lapsed.

    :param stop_event: Set on agent shutdown to end the loop.
    """
    delay = RENEW_INITIAL_DELAY
    while not stop_event.wait(delay):
        try:
            new_token, expires_in = api_renew_agent_token(URL, current_access_token())
        except (TusNotSupportedError, AuthenticationError) as e:
            # 404: server has no renewal endpoint. 401: the token is not a
            # live device token (manual, expired or revoked). Either way there
            # is nothing to rotate - keep the current token and stop.
            runtime.logger.info(f"Token renewal not available; keeping the token: {e}")
            return
        except Exception as e:
            runtime.logger.info(f"Token renewal failed, will retry: {e}")
            delay = RENEW_RETRY_DELAY
            continue
        _set_access_token(new_token)
        _persist_token(new_token)
        runtime.logger.info("Renewed the agent access token.")
        delay = max(RENEW_MIN_INTERVAL, expires_in // 2) if expires_in else (
            RENEW_FALLBACK_INTERVAL
        )


def _start_token_renewal(stop_event) -> None:
    """Start the background token-renewal thread (daemon)."""
    thread = Thread(target=_renewal_loop, args=(stop_event,), daemon=True)
    thread.start()


# Size cap for the legacy single-request upload endpoint only; resumable
# TUS uploads are chunked and have no practical size limit.
FILE_UPLOAD_SIZE_LIMIT = 100 * 1024**2  # 100 MB

# Set after the first upload attempt shows the server has no
# token-accessible TUS endpoint (older Mascope release), so every
# subsequent file skips the doomed TUS attempt.
_legacy_upload = False

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


def process_file_upload(filepath: str, max_retries: int = 10) -> None:
    """Process file upload

    :param filepath: Full path to the file to be uploaded
    :type filepath: str
    """
    for attempt in range(1, max_retries + 1):
        try:
            upload_sample_file(filepath)
            return
        except ValueError as ve:
            runtime.logger.error(f"File upload failed: {ve}")
            break  # do not retry on validation errors
        except AuthenticationError as e:
            runtime.logger.error(
                f"File upload failed for file {os.path.basename(filepath)}: {e} "
                "Retrying will not help - fix the access_token in the "
                "file-agent configuration and restart the agent."
            )
            break  # a rejected token stays rejected; do not retry
        except (NotFoundError, ValidationError) as e:
            runtime.logger.error(
                f"File upload failed for file {os.path.basename(filepath)}: {e} "
                "Retrying will not help - the server rejected the request. "
                "A 404 usually means the configured host is not the Mascope "
                "API (in development setups the frontend dev server cannot "
                "receive uploads; use the backend address, e.g. "
                "http://localhost:8090). Fix 'host' in the file-agent "
                "configuration and restart the agent."
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
    Servers without token-accessible TUS uploads (older Mascope releases)
    are detected on the first attempt and fall back to the legacy
    single-request endpoint, which caps files at 100 MB.

    :param filepath: Full path to the file to be uploaded
    :type filepath: str
    :raises Exception: Raises an exception if the request fails (status code != 200)
    """
    global _legacy_upload

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
    if not _legacy_upload:
        try:
            api_post_file_tus(
                url=URL,
                access_token=current_access_token(),
                filepath=filepath,
                upload_filename=upload_filename,
            )
            runtime.logger.info(
                f"File upload of file {os.path.basename(filepath)} succeeded!"
            )
            return
        except TusNotSupportedError as e:
            # Raised only by the upload *creation* request (404: no TUS
            # route, 401: route not token-accessible) - an older server.
            # The legacy attempt below gives the definitive answer (a
            # genuinely bad token fails there with the proper message).
            # Mid-transfer errors keep their normal types and are retried
            # by process_file_upload, so a backend restart mid-upload can
            # never latch the agent onto the capped legacy path.
            runtime.logger.info(
                "The server does not accept agent TUS uploads, falling back "
                f"to the legacy upload endpoint: {e}"
            )
            _legacy_upload = True

    # Legacy single-request upload: enforce its size cap
    file_size = os.stat(filepath).st_size
    if file_size > FILE_UPLOAD_SIZE_LIMIT:
        raise ValueError(
            f"File size ({round(file_size / (1024**2), 1)} MB) exceeds the maximum "
            f"allowed size ({FILE_UPLOAD_SIZE_LIMIT / (1024**2)} MB) of the "
            "server's upload endpoint. Upgrading the Mascope server enables "
            "uploads of any size."
        )
    api_post_file(
        url=URL,
        path="sample/files/upload",
        access_token=runtime.config.access_token,
        filepath=filepath,
        upload_filename=upload_filename,
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

    uploader = FileUploader(
        runtime.config.source, runtime.config.mask, recursive=runtime.config.recursive
    )
    uploader.watcher.run_as_daemon()
    _start_token_renewal(uploader.shutdown_event)
    uploader.run_until_complete()
    executor.shutdown(wait=True)


if __name__ == "__main__":
    run()
