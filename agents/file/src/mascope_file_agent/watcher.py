"""Watching a folder, and telling when a new file in it is complete."""

import logging
import os
import time
from queue import Queue
from threading import Event, Lock
from typing import Callable, Iterable

import watchdog
from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer

from mascope_file_agent._threads import Task


#: Seconds between two looks at a file that is still being written.
POLL_INTERVAL = 1

#: Seconds the observer's own thread is given to end once it is told to. It
#: only ever hands a path over, so it ends at once; the bound is there so that
#: a stop with no deadline is never a wait an interrupt cannot reach.
OBSERVER_STOP_TIME = 5


class FileSystemWatcher:
    """Watch a folder for new files, and report each one once it is complete.

    A file is complete when its size has stopped changing and it can be
    renamed, which on Windows it cannot while the program writing it holds it
    open. Each complete file is handed to the ``on_ready`` callbacks, in
    order, one file at a time, on a thread of the watcher's own.

    :param path: The folder to watch.
    :param mask: Pattern of the file names to report.
    :param recursive: Whether subfolders are watched too.
    :param on_ready: Called with the path of each complete file, in order. An
        error in one is logged and does not keep the file from the rest.
    :param on_dropped: Called with the path of each file the watcher had seen
        and did not report, because it was stopped first.
    :param logger: Where the lines go.
    :param shutdown_event: Set to give up waiting for files to complete;
        :meth:`stop` sets it.
    :param ignored_folders: Names of folders whose files are never reported,
        wherever under ``path`` they are.
    """

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
            self.client.seen(event.src_path)

        def on_moved(self, event: watchdog.events.FileSystemEvent) -> None:
            """File moved

            :param event: Filesystem event
            :type event: watchdog.events.FileSystemEvent
            """
            self.client.seen(event.dest_path)

    def __init__(
        self,
        path: str,
        mask: str,
        recursive: bool = False,
        on_ready: Iterable[Callable[[str], None]] = (),
        on_dropped: Callable[[str], None] | None = None,
        logger=None,
        shutdown_event=None,
        ignored_folders: Iterable[str] = (),
    ):
        self.path = path
        self.mask = mask
        self.recursive = recursive
        #: The callbacks a complete file is handed to, in order.
        self.on_ready = list(on_ready)
        self.on_dropped = on_dropped
        self.logger = logger or logging.getLogger(__name__)
        self.shutdown_event = shutdown_event or Event()
        self.ignored_folders = tuple(ignored_folders)
        self.observer = Observer()
        self.handler = self.FileSystemEventHandler(self, patterns=[self.mask])
        # The files the observer saw, in the order it saw them. They are
        # handled on a thread of the watcher's own, not on the observer's:
        # the observer holds a lock of its own while a handler runs, and
        # stopping it waits for that lock - so a handler that waited for a
        # file to complete would hold up the stop for as long as it took.
        self._seen = Queue()
        self._handling = Task(self._handle_seen, "file-agent-watcher")
        self._lock = Lock()
        self._watching = False

    def seen(self, fname: str) -> None:
        """Take a new file from the observer, to handle in its turn.

        :param fname: File path
        :type fname: str
        """
        self._seen.put(fname)

    def _handle_seen(self) -> None:
        """Handle the files the observer saw, one at a time, until stopped."""
        while True:
            fname = self._seen.get()
            if fname is None:
                return
            try:
                self.on_filesystem_object_created(fname)
            except Exception:
                self.logger.exception("Unexpected error handling filesystem event")

    def _ignored(self, fname: str) -> str | None:
        """The ignored folder the file is in, or None when it is in none."""
        folders = os.path.normpath(fname).split(os.sep)
        for ignored in self.ignored_folders:
            if ignored in folders:
                return ignored
        return None

    def on_filesystem_object_created(self, fname: str) -> None:
        """Callback on file created.

        First wait while filesize is changing. Then check file access
        by dummy rename operation. Finally, hand the file to ``on_ready``.

        :param fname: File path
        :type fname: str
        """
        ignored = self._ignored(fname)
        if ignored is not None:
            # failed_uploads holds copies of files that already failed; watching
            # it recursively would re-upload (and re-fail) them in a loop
            self.logger.debug(f"Ignoring file in {ignored}: {fname}")
            return
        self.logger.info(f"File created: {fname}")
        if not self._wait_until_complete(fname):
            if self.on_dropped is not None:
                self.on_dropped(fname)
            return
        for callback in tuple(self.on_ready):
            try:
                callback(fname)
            except Exception:
                self.logger.exception(
                    f"Unexpected error handling the new file {fname}; continuing"
                )

    def _wait_until_complete(self, fname: str) -> bool:
        """Wait until nothing is writing the file.

        :param fname: File path
        :type fname: str
        :return: Whether the file is complete; False when the wait was given
            up because the watcher is stopping
        :rtype: bool
        """
        filesize = -1
        while True:
            while filesize != os.path.getsize(fname):
                filesize = os.path.getsize(fname)
                if self.shutdown_event.wait(POLL_INTERVAL):
                    return False
            try:
                os.rename(fname, fname)
                return True
            except PermissionError:
                self.logger.debug(f"File {fname} is not ready")
                if self.shutdown_event.wait(POLL_INTERVAL):
                    return False

    def start(self) -> None:
        """Start watching.

        Start `FileSystemEventHandler`
        """
        # Under the lock stop() takes, so that a stop from another thread
        # finds the watcher either not started or started, never in between.
        with self._lock:
            self.observer.schedule(self.handler, self.path, recursive=self.recursive)
            self.observer.start()
            self._handling.start()
            self._watching = True
        scope = " and its subfolders" if self.recursive else ""
        self.logger.info(
            f"Started watching {self.path}{scope} for new files "
            f"matching pattern '{self.mask}'"
        )

    def stop(self, timeout: float | None = None) -> bool:
        """Stop watching.

        Stop `FileSystemEventHandler`. A file whose turn had not come is
        handed to ``on_dropped``, and so is one the watcher was still waiting
        on to be complete.

        :param timeout: Seconds to wait for the file being handled, which is
            as long as its ``on_ready`` callbacks take; None to wait until
            they are done
        :type timeout: float | None
        :return: Whether the watcher's threads ended in that time. False when
            called from an ``on_ready`` callback, whose thread ends after it.
        :rtype: bool
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        self.shutdown_event.set()
        with self._lock:
            watching, self._watching = self._watching, False
            if watching:
                self.observer.stop()
                # Behind whatever the observer had already seen.
                self._seen.put(None)
        if self.observer.ident is None:
            return True  # never started
        self.observer.join(
            OBSERVER_STOP_TIME if timeout is None else min(timeout, OBSERVER_STOP_TIME)
        )
        # The thread that handles the files is the one a stop can have to wait
        # for. Asked from one of its own callbacks, it ends after the callback.
        ended = self._handling.wait(
            None if deadline is None else max(0.0, deadline - time.monotonic())
        )
        if ended and watching:
            self.logger.info("File system watcher stopped")
        return ended
