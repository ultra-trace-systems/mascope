"""Uploading the files the watcher reports: when, how often, and what then.

A complete file waits until nothing has touched it for the configured time,
is uploaded by one of a few workers, is tried again while the failure is one
that can clear, and is set aside in ``failed_uploads`` when it cannot be
uploaded.
"""

import logging
import os
import shutil
import time
from queue import Empty, Queue
from threading import Event, Lock

from mascope_file_agent._threads import Task, interrupts
from mascope_sdk import api_post_file_tus
from mascope_sdk.acquisition import sidecar_path
from mascope_sdk.exceptions import (
    AuthenticationError,
    NotFoundError,
    ValidationError,
)


#: The folder inside the watched one where files that could not be uploaded
#: are set aside. Never watched.
FAILED_UPLOADS_DIR = "failed_uploads"

#: Seconds between two looks at the files waiting to be uploaded.
POLL_INTERVAL = 1

#: Seconds between two attempts at a file whose upload failed.
RETRY_DELAY = 30

#: How many files are uploaded at the same time.
UPLOAD_WORKERS = 3


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


def _rejection_guidance(error: Exception) -> str:
    """What the operator should do about an upload the server refused outright.

    These are not failures waiting to clear: the request was understood and
    rejected, so the only useful thing the agent can do is name the cause and
    the fix. The commonest by far is a file whose name does not identify an
    instrument, which the person at the acquisition software can correct.

    :param error: The terminal error raised by the upload.
    :type error: Exception
    :return: A sentence or two of guidance, appended to the log line.
    :rtype: str
    """
    message = str(error).lower()

    if "instrument" in message:
        return (
            "Mascope reads the instrument from the start of the file name, up "
            "to the first underscore - 'Orbion_...' is instrument 'Orbion' - "
            "and this name does not give one it recognizes. Either have the "
            "acquisition software name files that way, or set "
            "'filename_prefix' in the agent configuration to add it, "
            "including the underscore (filename_prefix = 'Orbion_')."
        )
    if isinstance(error, NotFoundError):
        return (
            "A 404 usually means the configured host is not the Mascope API "
            "(in development setups the frontend dev server cannot receive "
            "uploads; use the backend address, e.g. http://localhost:8090), "
            "or that the server is too old to accept agent uploads. Fix "
            "'host' in the agent configuration and restart it, or update the "
            "server."
        )
    return (
        "The server understood the request and rejected it, so sending the "
        "same file again cannot succeed."
    )


class FileUploader:
    """Upload the files handed over, each after it has been left alone a while.

    :meth:`enqueue` takes a complete file; :meth:`run_until_complete` hands it
    to the upload workers once it has not been accessed for the configured
    ``timeout``; a worker uploads it, retrying while that can help.

    :param settings: The agent's settings.
    :param url: The server's base URL.
    :param credentials: Holds the live access token, and replaces a refused one.
    :param logger: Where the lines go.
    :param shutdown_event: Set to stop taking files.
    :param timezone: IANA timezone reported with each upload; None when this
        machine could not name its zone.
    :param instrument: Name of the instrument reported with each upload; None
        leaves the server reading it from the file name.
    :param status_follower: Told of each uploaded file, to follow what the
        server makes of it; None to follow nothing.
    :param provenance: Asked for what goes with each upload to say where the
        file came from - its acquisition record and its hash; None to send
        neither.
    """

    def __init__(
        self,
        settings,
        url: str,
        credentials,
        logger=None,
        shutdown_event=None,
        timezone: str | None = None,
        instrument: str | None = None,
        status_follower=None,
        provenance=None,
    ):
        self.settings = settings
        self.url = url
        self.credentials = credentials
        self.logger = logger or logging.getLogger(__name__)
        self.shutdown_event = shutdown_event or Event()
        self.timezone = timezone
        self.instrument = instrument
        self.status_follower = status_follower
        self.provenance = provenance
        #: Complete files waiting to have been left alone for long enough.
        self.jobs = Queue()
        # Files handed to the workers. Daemon threads rather than an executor:
        # an executor's threads are joined when the interpreter exits, so an
        # upload stuck on a server that never answers would hold a stopping
        # process open for as long as the request's timeout.
        self._uploads = Queue()
        self._workers: list[Task] = []
        self._lock = Lock()
        # The files a worker has begun, and those of them that are between
        # two attempts rather than in one.
        self._in_flight: set[str] = set()
        self._retrying: set[str] = set()
        # Files the loop could not look at when their turn came, so that the log
        # says so once for each and not once a second. The loop's own.
        self._unreadable: set[str] = set()
        # Set once finish() has begun: no file is taken after that, so every
        # file is either handed to a worker ahead of the workers' release or
        # named in the log as not uploaded - never left in a queue nobody reads.
        self._finishing = False
        # Set when the wait for the workers ran out: uploads waiting for a
        # retry or for a free worker are abandoned.
        self._abort = Event()

    def start(self) -> None:
        """Start the upload workers."""
        # Created and started under the lock finish() takes, so that it never
        # finds a worker that exists and has not been started.
        with self._lock:
            if self._workers or self._finishing:
                return
            self._workers = [
                Task(self._work, f"file-agent-upload-{number}")
                for number in range(1, UPLOAD_WORKERS + 1)
            ]
            for worker in self._workers:
                worker.start()

    def enqueue(self, fname: str) -> None:
        """Take a complete file, to upload once it has been left alone.

        :param fname: File path
        :type fname: str
        """
        self._put(self.jobs, fname)

    def _put(self, queue: Queue, fname: str) -> None:
        """Queue a file, unless the uploader is past taking any."""
        with self._lock:
            finishing = self._finishing
            if not finishing:
                queue.put(fname)
        if finishing:
            self.not_uploaded(fname)

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
            - FileNotFoundError: Raised when the file is gone by the time its turn comes.
            - OSError: Raised when the file cannot be looked at just now.
            - KeyboardInterrupt: Raised when the process is interrupted by the user.
            - Exception: Catches all other exceptions and logs them as critical errors.

        The method ensures that the `shutdown_event` is set when exiting, either normally or due
        to an exception.
        """
        try:
            self._poll()
        except KeyboardInterrupt:
            self.logger.info("Shutdown requested by user.")
        except Exception:
            self.logger.exception("Unexpected error in the upload loop")
        finally:
            self.shutdown_event.set()

    def _poll(self) -> None:
        """Hand each waiting file to the workers once it has been left alone.

        The loop of ``run_until_complete``, in a function of its own so that
        the handlers there are a frame away from it. CPython 3.12 acts on a
        pending interrupt at a loop's backward jump only once it has jumped,
        and looks for the handler at the instruction before the jump's
        target (python/cpython#108214). For a loop that opens a ``try``
        block that instruction is outside the block, so an interrupt taken
        on a ``continue`` is not seen by a handler written around the loop
        in the same function. One that leaves this function reaches the
        caller's handlers as any exception does.

        The issue is not being fixed in 3.12. Python 3.13 looks before it
        jumps, which mends the ``continue``, but leaves such a loop's own
        jump back from the end of its body outside the block, and an
        interrupt taken there is lost the same way. 3.14 mends both of
        these, though not every jump that looks for signals, so no later
        Python is simply the way out: see
        ``tests/test_interrupt_handlers.py`` before the agent moves to one.

        An interrupt that was counted while the agent runs on the main
        thread (``_threads.Interrupts``) is raised here, once every time
        round: after the wait and before the look at the queue, with the
        lock of neither in hand.
        """
        while not self.shutdown_event.wait(POLL_INTERVAL):
            interrupts.raise_pending()
            fname = None
            try:
                fname = self.jobs.get_nowait()
                self.logger.debug(fname)
                try:
                    untouched = self.seconds_since_last_access(fname)
                except FileNotFoundError as e:
                    # Deleted or renamed since it appeared, which acquisition
                    # software does as a matter of course. One file less to
                    # upload, not a reason to stop uploading the others.
                    self._unreadable.discard(fname)
                    self.logger.warning(
                        f"{os.path.basename(fname)}: not uploaded, as it was "
                        f"gone when its turn came ({e}). A file renamed to a "
                        "name the agent watches for is uploaded under that "
                        "name."
                    )
                    continue
                except OSError as e:
                    # Still there, for all this says: a folder on a network
                    # that dropped out for a moment, a file something has
                    # locked. It waits its turn again, as a file not yet
                    # left alone does, and the log says so once.
                    if fname not in self._unreadable:
                        self._unreadable.add(fname)
                        self.logger.warning(
                            f"{os.path.basename(fname)}: could not be looked "
                            f"at just now ({e}). It stays in the queue and "
                            "is uploaded once it can be."
                        )
                    self._put(self.jobs, fname)
                    continue
                self._unreadable.discard(fname)
                if untouched < self.settings.timeout:
                    self._put(self.jobs, fname)
                    self.logger.debug(f"Put {fname} back to queue")
                    continue
                # Hand the file to the upload workers
                self._put(self._uploads, fname)
            except Empty:
                continue

    def finish(self, timeout: float | None = None) -> bool:
        """Let the uploads already handed to the workers end, then stop them.

        Files still waiting to have been left alone are not uploaded. When
        ``timeout`` runs out first, the uploads waiting for a retry or for a
        free worker are abandoned too; one a worker is in the middle of is left
        to its thread, which does not keep the process from exiting.

        :param timeout: Seconds to wait; None to wait until they have ended
        :type timeout: float | None
        :return: Whether every upload handed to a worker ended in that time
        :rtype: bool
        """
        with self._lock:
            first, self._finishing = not self._finishing, True
            # Not the worker this is called from, if it is one: a Repair that
            # stops the agent on a refused upload is on a worker's thread, and
            # that thread cannot wait for its own end.
            workers = [w for w in self._workers if not w.is_current]
        if first:
            self._release_workers()
        deadline = None if timeout is None else time.monotonic() + timeout
        for worker in workers:
            worker.wait(
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
        done = all(worker.ended for worker in workers)
        if not done:
            with self._lock:
                # Not the ones between two attempts: the abort ends their
                # wait, and each says for itself that it was not uploaded.
                uploading = sorted(self._in_flight - self._retrying)
            self._abort.set()
            for fname in uploading:
                self.logger.warning(
                    f"{os.path.basename(fname)}: its upload had not ended when "
                    "the agent stopped. Whether it arrived shows in Raw files "
                    "on the server."
                )
        # Whatever never reached a worker.
        for waiting in (self._uploads, self.jobs):
            while True:
                try:
                    fname = waiting.get_nowait()
                except Empty:
                    break
                if fname is not None:
                    self.not_uploaded(fname)
        # Emptying the queue took with it the release of every worker that had
        # not picked its own up yet: one still in an upload when the wait ran
        # out, or the one this was called from. Each gets it again, or it
        # would wait for it for ever once its upload is over.
        self._release_workers()
        return done

    def _release_workers(self) -> None:
        """Tell each worker still running to end once nothing is left to upload."""
        for worker in self._workers:
            if not worker.ended:
                self._uploads.put(None)

    def not_uploaded(self, fname: str) -> None:
        """Say that a file was left behind because the agent stopped.

        :param fname: File path
        :type fname: str
        """
        self.logger.warning(
            f"{os.path.basename(fname)}: not uploaded, as the agent is stopping. "
            "It stays in the watched folder; to upload it, move it out of the "
            "folder and back in once the agent runs again."
        )

    def _work(self) -> None:
        """Upload the files handed over, one after another, until released."""
        while True:
            fname = self._uploads.get()
            if fname is None:
                return
            if self._abort.is_set():
                self.not_uploaded(fname)
                continue
            with self._lock:
                self._in_flight.add(fname)
            try:
                self.process_file_upload(fname)
            except Exception:
                # Nothing collects what a worker raises, so say it here.
                self.logger.exception(
                    f"Unexpected error uploading {os.path.basename(fname)}"
                )
            finally:
                with self._lock:
                    self._in_flight.discard(fname)

    def _wait_to_retry(self, filepath: str) -> bool:
        """Wait out the time between two attempts at a file.

        :param filepath: Full path to the file being retried
        :type filepath: str
        :return: Whether the wait was cut short because the agent stopped
            waiting for its uploads
        :rtype: bool
        """
        with self._lock:
            self._retrying.add(filepath)
        try:
            return self._abort.wait(RETRY_DELAY)
        finally:
            with self._lock:
                self._retrying.discard(filepath)

    def get_upload_filename(self, filepath: str) -> str | None:
        """Compute the upload filename by applying configured prefix and/or suffix.

        Returns the modified filename if prefix or suffix is configured,
        otherwise returns None (indicating the original filename should be used).

        :param filepath: Full path to the file
        :type filepath: str
        :return: Modified filename or None
        :rtype: str | None
        """
        prefix = self.settings.filename_prefix or ""
        suffix = self.settings.filename_suffix or ""
        if not prefix and not suffix:
            return None
        for label, value in (("filename_prefix", prefix), ("filename_suffix", suffix)):
            if any(sep in value for sep in ("/", "\\", os.sep) if sep) or ".." in value:
                raise ValueError(f"{label} contains invalid characters: {value!r}")
        basename = os.path.basename(filepath)
        stem, ext = os.path.splitext(basename)
        return f"{prefix}{stem}{suffix}{ext}"

    def process_file_upload(self, filepath: str, max_retries: int = 10) -> None:
        """Process file upload

        :param filepath: Full path to the file to be uploaded
        :type filepath: str
        """
        try:
            self._upload_or_set_aside(filepath, max_retries)
        finally:
            # What was kept for the attempts - the file's hash above all -
            # was kept for this upload and no longer.
            if self.provenance is not None:
                self.provenance.forget(filepath)

    def _upload_or_set_aside(self, filepath: str, max_retries: int) -> None:
        """Upload a file, trying again while that can help; set it aside when
        it cannot be uploaded.

        :param filepath: Full path to the file to be uploaded
        :type filepath: str
        :param max_retries: How many attempts to make
        :type max_retries: int
        """
        for attempt in range(1, max_retries + 1):
            token_used = self.credentials.current_access_token()
            try:
                self.upload_sample_file(filepath)
                return
            except ValueError as ve:
                self.logger.error(f"File upload failed: {ve}")
                break  # do not retry on validation errors
            except AuthenticationError as e:
                if self.credentials.offer_repair(token_used, str(e)):
                    continue  # fresh credential in hand - try this file again
                self.logger.error(
                    f"File upload failed for file {os.path.basename(filepath)}: {e} "
                    "Retrying will not help - the server rejected this machine's "
                    "credential. It may have been revoked, or have expired while "
                    "the agent was offline. "
                    f"{self.credentials.repair.advice_on_upload}"
                )
                break  # a rejected token stays rejected until the machine re-pairs
            except (NotFoundError, ValidationError) as e:
                self.logger.error(
                    f"File upload failed for file {os.path.basename(filepath)}: "
                    f"{e} {_rejection_guidance(e)}"
                )
                break  # a rejected file cannot heal by waiting
            except Exception as e:
                # Timeouts, connection and server errors are transient - retry.
                # The message carries the specific cause (e.g. connection refused,
                # HTTP status + server error message).
                # INFO per attempt: retries are routine on a flaky network; the
                # final give-up below logs at ERROR
                self.logger.info(
                    f"Upload attempt {attempt}/{max_retries} for file "
                    f"{os.path.basename(filepath)} failed: {e}"
                )
                self.logger.info(f"Retrying upload in {RETRY_DELAY} seconds...")
                if self._wait_to_retry(filepath):
                    self.not_uploaded(filepath)
                    return
        # Out of attempts, or refused outright: set the file aside where the
        # operator can find it, and say how to make it try again.
        name = os.path.basename(filepath)
        try:
            failed_dir = mkdir(self.settings.source, FAILED_UPLOADS_DIR)
            shutil.copyfile(filepath, os.path.join(failed_dir, name))
            self._keep_sidecar(filepath, failed_dir)
        except OSError as e:
            # The file can disappear mid-retry (an operator tidying up, an
            # instrument rewriting it). Nothing left to preserve, and this runs in
            # a worker thread, so say it and stop.
            self.logger.error(f"Gave up on {name}, and could not keep a copy: {e}")
            return
        self.logger.error(
            f"Gave up on {name} after {attempt} "
            f"attempt{'s' if attempt != 1 else ''}. A copy is in {failed_dir} - "
            "once the cause is fixed, put it back in the watched folder to "
            "upload it."
        )

    def _keep_sidecar(self, filepath: str, failed_dir: str) -> None:
        """Set a file's acquisition record aside with it, if it has one.

        So that the file put back in the watched folder is uploaded with its
        record, when the record comes back with it. A record that cannot be
        copied does not keep the file from being set aside.

        :param filepath: Full path of the file being set aside
        :type filepath: str
        :param failed_dir: The folder its copy is in
        :type failed_dir: str
        """
        sidecar = sidecar_path(filepath)
        try:
            shutil.copyfile(
                sidecar, os.path.join(failed_dir, os.path.basename(sidecar))
            )
        except FileNotFoundError:
            # No record beside the file, which is the ordinary case.
            pass
        except OSError as e:
            self.logger.warning(
                f"Could not keep the acquisition record of "
                f"{os.path.basename(filepath)} with its copy: {e}"
            )

    def upload_sample_file(self, filepath: str) -> None:
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
        mask_ext = os.path.splitext(self.settings.mask)[1].lower()
        if file_ext != mask_ext:
            raise ValueError(f"{file_ext} is not an allowed file extension!")

        self.logger.debug(f"Making an upload request to {self.url} for file {filepath}")
        upload_filename = self.get_upload_filename(filepath)
        if upload_filename:
            self.logger.info(
                f"Uploading file {os.path.basename(filepath)} as {upload_filename}"
            )

        record, sha256 = None, None
        if self.provenance is not None:
            record, sha256 = self.provenance.for_upload(filepath)
        try:
            self._send(filepath, upload_filename, record, sha256)
        except ValidationError as refused:
            if record is None:
                raise
            # The record must not cost the file its upload, and it is large
            # for a request header: a proxy in front of the server can refuse
            # the request for it. Whatever else was refused is refused again.
            #
            # Not every refusal comes here. A 409 is no ValidationError to the
            # SDK, so a server that will not have an upload at all, record or
            # no record, can say so with one and is not sent the file anyway.
            self.logger.warning(
                f"{os.path.basename(filepath)}: refused with its acquisition "
                f"record ({refused}). Uploading it without the record."
            )
            record = None
            self._send(filepath, upload_filename, None, sha256)
        self.logger.info(f"File upload of file {os.path.basename(filepath)} succeeded!")
        if record is not None:
            self.logger.info(
                f"{os.path.basename(filepath)}: its acquisition record went with it."
            )
        if self.status_follower is not None:
            # By the name it had here, which the server keeps as its
            # source_filename whatever name it was uploaded under.
            self.status_follower.follow(os.path.basename(filepath))

    def _send(
        self,
        filepath: str,
        upload_filename: str | None,
        record: bytes | None,
        sha256: str | None,
    ) -> None:
        """Make one upload of a file, with what goes with it.

        :param filepath: Full path to the file to be uploaded
        :type filepath: str
        :param upload_filename: The name to upload it under; None for its own
        :type upload_filename: str | None
        :param record: The document of its acquisition record; None for none
        :type record: bytes | None
        :param sha256: Its SHA-256; None to send none
        :type sha256: str | None
        """
        with_it = {}
        if record is not None:
            with_it["acquisition"] = record
        if sha256 is not None:
            with_it["sha256"] = sha256
        # Raises a typed mascope_sdk exception carrying the specific cause
        # (rejected token, timeout, connection error, server error message).
        api_post_file_tus(
            url=self.url,
            # The live token, not the one the agent was started with: renewal
            # rotates it and the server reaps the superseded ones, so a stale copy
            # here would 401 non-retryably while the agent holds a valid credential.
            access_token=self.credentials.current_access_token(),
            filepath=filepath,
            upload_filename=upload_filename,
            timezone=self.timezone,
            instrument=self.instrument,
            **with_it,
        )
