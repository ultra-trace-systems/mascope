"""
Rebuild path: ingest the bundle's raw files through the real upload pipeline.

Instead of dropping files into the file-converter's watch directory (which has
no auth/context and never registers a file context), this uploads each raw file
through the resumable (TUS) upload endpoint with
:func:`mascope_sdk.api_post_file_tus` - exactly what the File Agent does. The
endpoint registers the file context (auth) and writes the file to
``filestreams``, where the file-converter ingests it and runs the full
``RawProcessor`` -> peak detection -> matching pipeline.

Uploads are driven from a background thread because the app is launched in the
foreground; the thread waits for the backend HTTP server *and* for the
file-converter's socket to connect, then uploads, then keeps sweeping the
converter's quarantine folder so a file that failed anyway is re-uploaded rather
than silently dropped from the rebuild.

The wait matters: the server refuses an upload with 503 while no file-converter
is connected, before a byte is transferred. Such a file reaches neither
``filestreams`` nor its quarantine folder, so nothing downstream would retry it:
the uploader has to hold its uploads, and its retries, until a converter is
connected.
"""

import subprocess
import threading
import time
from pathlib import Path

from mascope_cli.cmd.demo import build_bundle, bundles
from mascope_cli.cmd.demo._seed import _MODE, DEMO_ENV, env_dir  # noqa: F401 - re-export
from mascope_cli.runtime import runtime


# The fixed file-agent token + service name the demo seeds (mirror
# server seed_demo.DEMO_TOKENS["file-agent"]). Using the file-agent service
# replicates the File Agent's exact upload request.
_UPLOAD_TOKEN = "mascope_demo_file_agent_token"
_UPLOAD_SERVICE = "file-agent"

# Redis key the backend writes when the file-converter's socket connects, and
# clears when it disconnects (mirrors
# ``mascope_backend.socket.storage.config.SocketStorageConfig.service_key``).
# Its value is the converter's socket id, rewritten on every connect - which is
# what makes it usable as a readiness signal even though the key is not scoped
# to one env: another instance's converter (or a stale key from a run that was
# killed) holds a *different* id than the converter this run is about to start.
_CONVERTER_PRESENCE_KEY = "mascope:service:file-converter"

# Readiness wait for that key to change. Generous: the converter retries its
# backend connection with a doubling delay capped at 30 s, so on a slow start it
# can trail the backend's first HTTP response by a minute or more.
_CONVERTER_WAIT_S = 300.0
_CONVERTER_POLL_S = 2.0
# Fallback when the presence key cannot be read at all (Redis not reachable
# through the dev container). Blind, but better than uploading immediately.
_CONVERTER_BLIND_WAIT_S = 60.0

# Upload passes over the raw set. The resumable upload already resumes a dropped
# chunk from the server's offset, so a failure that reaches this code is one the
# SDK gave up on: a refusal (no converter connected) or a server that stayed
# unreachable. Such a file is re-posted in a later pass rather than dropped - it
# would otherwise never reach filestreams and so never show up in the quarantine
# sweep either. The pause also lets a completion the client stopped waiting for
# finish on the server before the file is looked for there.
_UPLOAD_MAX_PASSES = 3
_UPLOAD_RETRY_PAUSE_S = 30.0

# Quarantine sweep: how often to look, and how many times one file is re-fed
# before it is reported as genuinely broken rather than unlucky.
_SWEEP_POLL_S = 20.0
_SWEEP_MAX_RETRIES = 2
# Backstop only - the sweep normally ends when the converter has consumed
# everything (see :func:`sweep_failed_uploads`), not on this deadline.
_SWEEP_DEADLINE_S = 12 * 3600.0


def _raw_dir(version: str | None, source_dir: "Path | None") -> Path:
    """Resolve the bundle's raw/ directory (published cache or local override)."""
    root = Path(source_dir) if source_dir else bundles.bundle_dir(version)
    src = root / "raw"
    if not src.is_dir():
        raise FileNotFoundError(f"No raw/ directory found at: {src}")
    return src


def _raw_files(version: str | None, source_dir: "Path | None") -> list[Path]:
    """The bundle's raw files, in a stable order."""
    return sorted(p for p in _raw_dir(version, source_dir).iterdir() if _is_raw(p))


def _is_raw(path: Path) -> bool:
    """Whether a path is a raw acquisition file."""
    return path.suffix.lower() == ".raw"


def _filestreams_dir() -> Path:
    """The demo env's filestreams folder - the converter's watch directory."""
    return env_dir() / "filestreams"


def _wait_for_backend(url: str, timeout: float = 300.0, poll: float = 2.0) -> bool:
    """
    Poll the backend until it serves HTTP.

    Probes a public, always-present endpoint (the first-owner status route)
    rather than the OpenAPI schema, which is disabled outside dev mode.

    :param url: Backend base URL (e.g. ``http://localhost:8090``).
    :param timeout: Max seconds to wait.
    :param poll: Seconds between attempts.
    :return: ``True`` once ready, ``False`` on timeout.
    """
    import requests

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{url}/api/users/first-owner/status", timeout=5)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(poll)
    return False


def _redis_get(key: str) -> str | None:
    """
    Read a Redis key through the dev Redis container.

    Uses ``docker exec`` rather than a Redis client so the CLI keeps its
    dependency-light operator surface (the same approach as
    ``mascope dev redis``).

    :param key: Redis key to read.
    :return: The value (``""`` when the key is unset), or ``None`` when Redis
             could not be queried at all.
    """
    redis_cfg = runtime.full_config.backend.redis
    if not redis_cfg or redis_cfg.host not in ("localhost", "127.0.0.1"):
        return None

    try:
        res = subprocess.run(
            [
                "docker",
                "exec",
                redis_cfg.get_redis_container_name(mode=runtime.mode),
                "redis-cli",
                # No -p: this runs *inside* the container, where the server
                # always listens on redis-cli's default 6379. `redis_cfg.port`
                # is the host-side port of the compose mapping
                # ("${MASCOPE_REDIS_PORT:-6379}:6379") and only matches by
                # coincidence when the mapping is left at its default.
                "get",
                key,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return res.stdout.strip() if res.returncode == 0 else None


def converter_presence() -> str | None:
    """
    Read the file-converter's current socket id, as the backend recorded it.

    :return: The socket id, ``""`` when no converter is connected, or ``None``
             when the presence key could not be read.
    """
    return _redis_get(_CONVERTER_PRESENCE_KEY)


def _wait_for_converter(baseline: str | None) -> bool:
    """
    Wait until a file-converter has connected since ``baseline`` was taken.

    ``baseline`` must be sampled *before* the stack starts. Waiting for the
    presence key to merely exist would be satisfied instantly by a stale key
    (a demo run that was killed never fires the disconnect handler) or by
    another worktree's converter, since the key is not env-scoped; waiting for
    the recorded socket id to *change* is satisfied only by a fresh connect.

    :param baseline: Presence value sampled before launch, or ``None`` if it
                     could not be read (falls back to a blind wait).
    :return: ``True`` if a fresh connect was observed.
    """
    if baseline is None:
        runtime.logger.warning(
            "Cannot read the file-converter's socket presence; waiting "
            f"{_CONVERTER_BLIND_WAIT_S:.0f}s before uploading instead. The "
            "server refuses uploads until the converter connects; refused files "
            "are re-posted in later passes."
        )
        time.sleep(_CONVERTER_BLIND_WAIT_S)
        return False

    runtime.logger.info("Waiting for the file-converter to connect...")
    deadline = time.monotonic() + _CONVERTER_WAIT_S
    while time.monotonic() < deadline:
        current = converter_presence()
        if current and current != baseline:
            runtime.logger.success("File-converter connected; starting uploads")
            return True
        time.sleep(_CONVERTER_POLL_S)

    runtime.logger.warning(
        f"File-converter did not connect within {_CONVERTER_WAIT_S:.0f}s; "
        "uploading anyway. The server refuses uploads until a converter "
        "connects; refused files are re-posted in later passes, each held until "
        "one has."
    )
    return False


def _await_connected_converter() -> None:
    """
    Hold a retry pass until some file-converter is connected.

    Unlike :func:`_wait_for_converter` this does not need a *fresh* connect: it
    stands in for the server's own 503 gate, which asks only whether a converter
    is connected now. A converter that crashed and came back, or one that
    connected only after the startup wait gave up, lets the retries through; one
    that never connects costs each pass this wait and no more. When the presence
    key cannot be read the pass goes ahead unheld.
    """
    deadline = time.monotonic() + _CONVERTER_WAIT_S
    announced = False
    while time.monotonic() < deadline:
        current = converter_presence()
        if current is None or current:
            return
        if not announced:
            announced = True
            runtime.logger.warning(
                "No file-converter is connected; holding the retry until one is"
            )
        time.sleep(_CONVERTER_POLL_S)
    runtime.logger.warning(
        f"No file-converter connected within {_CONVERTER_WAIT_S:.0f}s; retrying anyway"
    )


def _post_raw(url: str, raw: Path) -> str | None:
    """
    Upload one raw file through the resumable (TUS) upload endpoint.

    :param url: Backend base URL.
    :param raw: Raw file to upload.
    :return: ``None`` on success, or the failure description.
    """
    import mascope_sdk
    from mascope_sdk import api_post_file_tus
    from mascope_sdk.exceptions import MascopeError

    # Match the File Agent's service header for the upload request.
    mascope_sdk.SERVICE_NAME = _UPLOAD_SERVICE
    try:
        api_post_file_tus(url=url, access_token=_UPLOAD_TOKEN, filepath=str(raw))
    except MascopeError as e:
        return str(e)
    return None


def _ingested_keys() -> set[str] | None:
    """
    The acquisition keys of every sample file in the demo database.

    The database stores the name the converter rebuilds from a file's own
    metadata, not the uploaded one, so both sides are keyed on the acquisition
    stamp they share (:func:`build_bundle._acquisition_key`, the key the
    authoring guard ``check_raw_coverage`` compares on). Read through the dev
    Postgres container, like :func:`_redis_get` reads Redis.

    :return: The keys, or ``None`` when the database could not be queried.
    """
    db_cfg = runtime.full_config.backend.database
    try:
        res = subprocess.run(
            [
                "docker",
                "exec",
                db_cfg.get_postgres_container_name(mode=_MODE),
                "psql",
                "-U",
                db_cfg.user,
                "-d",
                db_cfg.get_postgres_database_name(DEMO_ENV),
                "-tA",
                "-c",
                "SELECT filename FROM sample_file",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return {
        build_bundle._acquisition_key(line)
        for line in res.stdout.splitlines()
        if line.strip()
    }


def _landed(name: str) -> bool:
    """
    Whether the server holds a file, whatever the client saw of its upload.

    Looked up fresh on every call, never from a listing taken earlier: the
    converter deletes a stream file once it has ingested it, so a file that was
    pending a moment ago may now be a database row instead. The folders are
    read first and the database last, and that order is what makes the answer
    safe while the converter works: a stream file leaves ``filestreams`` only
    after its row is committed (or by moving to its quarantine folder), so a
    file absent from both folders and from the database had not arrived.

    A quarantined file has arrived too: the quarantine sweep re-posts it, and
    posting it again here as well would only race that.

    :param name: The raw file's name, as uploaded.
    :return: ``True`` if the file is pending, quarantined, or ingested.
    """
    streams = _filestreams_dir()
    if name in _list_files(streams) or name in _list_files(streams / "failed_files"):
        return True
    ingested = _ingested_keys()
    if ingested is None:
        runtime.logger.warning(
            f"Cannot read the demo database to check whether {name} was "
            "ingested; checked the filestreams folders only"
        )
        return False
    return build_bundle._acquisition_key(name) in ingested


def upload_raw(
    version: str | None = None,
    source_dir: "Path | None" = None,
    *,
    converter_baseline: str | None = None,
    wait_for_converter: bool = False,
    max_passes: int = _UPLOAD_MAX_PASSES,
    retry_pause: float = _UPLOAD_RETRY_PAUSE_S,
) -> list[str]:
    """
    Upload the bundle's raw files to the real sample-file upload endpoint.

    Replicates the File Agent's request (bearer ``file-agent`` token +
    ``X-Service-Name: file-agent``) so the server registers context, stores the
    file, and the converter ingests it.

    Files that failed are re-posted in later passes rather than dropped. After
    every pass, the last included, each failed file is looked up on the server
    (:func:`_landed`): one the server holds anyway - pending, quarantined or
    already a sample file in the database - counts as uploaded and is not
    re-posted, since the converter would quarantine the duplicate and the run
    would report a file it did ingest as missing. Each retry pass first waits
    for a connected converter, which the server requires of every upload.

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override (see module docstring).
    :param converter_baseline: Socket-presence value sampled before the stack
        started (:func:`converter_presence`), used only when
        ``wait_for_converter`` is set.
    :param wait_for_converter: Hold the first upload until the file-converter's
        socket has connected, so its file context is not emitted into the void.
    :param max_passes: Upload attempts over the set before giving up.
    :param retry_pause: Seconds to let the server catch up before a retry pass.
    :return: Names of the files that never reached the server (empty on a clean
             run).
    """
    raw_files = _raw_files(version, source_dir)
    url = f"http://localhost:{runtime.meta.api_port}"

    if wait_for_converter:
        _wait_for_converter(converter_baseline)

    total = len(raw_files)
    runtime.logger.info(f"Uploading {total} raw file(s) to {url} (resumable)")

    outstanding = list(raw_files)
    uploaded = 0
    for attempt in range(1, max_passes + 1):
        if attempt > 1:
            runtime.logger.warning(
                f"Retrying {len(outstanding)} failed upload(s) "
                f"(pass {attempt}/{max_passes})"
            )
            _await_connected_converter()

        failed: list[Path] = []
        for i, raw in enumerate(outstanding, 1):
            error = _post_raw(url, raw)
            if error is None:
                uploaded += 1
            else:
                failed.append(raw)
                runtime.logger.error(f"Upload of {raw.name} failed: {error}")
            if i % 25 == 0 or i == len(outstanding):
                runtime.logger.info(f"  uploaded {uploaded}/{total}")
        if not failed:
            outstanding = []
            break

        # A failed file may have reached the server anyway. Give a completion
        # the client stopped waiting for time to finish, then look each file
        # up - after the last pass too, or a file that landed on it would be
        # reported as never reaching the server. One that landed counts as
        # uploaded, or the progress line ends below the total the summary
        # reports.
        time.sleep(retry_pause)
        landed = [raw for raw in failed if _landed(raw.name)]
        if landed:
            uploaded += len(landed)
            runtime.logger.info(
                f"  {len(landed)} file(s) reached the server despite the error; "
                "not re-posting"
            )
        outstanding = [raw for raw in failed if raw not in landed]
        if not outstanding:
            break

    missing = sorted(raw.name for raw in outstanding)
    if not missing:
        runtime.logger.success(f"Uploaded all {total} raw file(s); ingestion proceeds")
    else:
        runtime.logger.error(
            f"Uploaded {uploaded}/{total} raw file(s); {len(missing)} never reached "
            f"the server after {max_passes} pass(es): {', '.join(missing)}"
        )
    return missing


def clear_stale_streams(
    version: str | None = None, source_dir: "Path | None" = None
) -> list[str]:
    """
    Drop copies of this bundle's files an earlier run left in ``filestreams``.

    Two folders keep them. Nothing empties ``filestreams/failed_files``, so a
    rebuild that dropped files leaves them there for the next one to find, and
    :func:`sweep_failed_uploads` would re-upload those stale copies into a run
    that is ingesting the same files from the bundle anyway. A run killed
    mid-ingestion leaves the files it had not reached yet pending in the
    ``filestreams`` root, where the converter would ingest them a second time
    beside this run's uploads, and where :func:`_landed` would take them for
    this run's. Either way the converter quarantines the duplicates
    (``FileExistsError`` on the filestore directory), ending a complete rebuild
    with a list of files it wrongly reports as never ingested.

    Only the bundle's own names are removed, and only before the stack starts:
    the bundle still holds every one of them as source, and a stale copy
    carries nothing the run is about to upload again.

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override.
    :return: Names of the stale copies that were removed; a name stale in both
             folders is listed twice.
    """
    streams = _filestreams_dir()
    by_name = {p.name for p in _raw_files(version, source_dir)}
    stale: list[str] = []
    for folder, what in (
        (streams, "left pending"),
        (streams / "failed_files", "quarantined"),
    ):
        names = sorted(name for name in _list_files(folder) if name in by_name)
        for name in names:
            (folder / name).unlink(missing_ok=True)
        if names:
            runtime.logger.warning(
                f"Cleared {len(names)} file(s) {what} by an earlier rebuild "
                f"before uploading: {', '.join(names)}"
            )
        stale.extend(names)
    return stale


def sweep_failed_uploads(
    version: str | None = None,
    source_dir: "Path | None" = None,
    *,
    never_uploaded: list[str] | None = None,
    max_retries: int = _SWEEP_MAX_RETRIES,
    poll: float = _SWEEP_POLL_S,
    deadline: float = _SWEEP_DEADLINE_S,
) -> list[str]:
    """
    Re-upload raw files the converter quarantined, until the run is drained.

    The converter deletes a stream file from ``filestreams`` once it has ingested
    it and moves it to ``filestreams/failed_files`` when it could not, so the two
    folders describe the whole run: work still queued or in progress, and work
    that fell out. This sweeps the second back through the uploader - a file that
    failed only because its context never arrived succeeds on a second pass, once
    the converter is connected. A quarantined file the database already holds is
    a duplicate of one that was ingested: its copy is dropped, not re-posted, and
    it is not reported as missing.

    Ends when the converter has consumed everything: nothing left pending and
    nothing quarantined that is still worth retrying. ``deadline`` is only a
    backstop for a converter that has stopped making progress altogether.

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override.
    :param never_uploaded: Names :func:`upload_raw` could not get to the server
        at all. They reach neither folder, so the sweep cannot discover them and
        would otherwise report a short run as drained.
    :param max_retries: Re-upload attempts per file before giving up on it.
    :param poll: Seconds between sweeps.
    :param deadline: Hard cap on the total sweep, in seconds.
    :return: Names of the bundle's files that did not make it through, whatever
             stage they fell out at (empty when the rebuild ingested everything).
    """
    url = f"http://localhost:{runtime.meta.api_port}"
    by_name = {p.name: p for p in _raw_files(version, source_dir)}
    streams = _filestreams_dir()
    quarantine = streams / "failed_files"

    attempts: dict[str, int] = {}
    reported_foreign: set[str] = set()
    end = time.monotonic() + deadline

    while time.monotonic() < end:
        pending = _list_files(streams)
        stuck = _list_files(quarantine)

        for name in sorted(set(stuck) - set(by_name)):
            if name not in reported_foreign:
                reported_foreign.add(name)
                runtime.logger.warning(
                    f"Quarantined file {name} is not part of this bundle; "
                    "leaving it alone"
                )

        ours = sorted(name for name in stuck if name in by_name)
        if ours:
            ingested = _ingested_keys() or set()
            for name in ours:
                if build_bundle._acquisition_key(name) in ingested:
                    runtime.logger.info(
                        f"Quarantined file {name} is a duplicate of one already "
                        "ingested; dropping it"
                    )
                    (quarantine / name).unlink(missing_ok=True)
                    stuck.remove(name)

        retryable = [
            name
            for name in sorted(stuck)
            if name in by_name and attempts.get(name, 0) < max_retries
        ]

        if not pending and not retryable:
            break

        for name in retryable:
            attempts[name] = attempts.get(name, 0) + 1
            runtime.logger.warning(
                f"Re-uploading quarantined file {name} "
                f"(attempt {attempts[name]}/{max_retries})"
            )
            error = _post_raw(url, by_name[name])
            if error is None:
                # Drop the quarantined copy only once its replacement is in:
                # the bundle still holds the source, and leaving it would make
                # the next sweep re-upload a file that is already back in flight.
                (quarantine / name).unlink(missing_ok=True)
            else:
                runtime.logger.error(f"Re-upload of {name} failed: {error}")

        time.sleep(poll)

    # Everything this run cannot account for, at whichever stage it fell out:
    # never uploaded, still quarantined, or still queued when the backstop
    # deadline expired. Only an empty union means the converter received the
    # bundle's whole raw set - the quarantine folder alone does not say that,
    # since a file that never reached the server leaves no trace in either.
    stranded = set(never_uploaded or [])
    stranded.update(name for name in _list_files(quarantine) if name in by_name)
    stranded.update(name for name in _list_files(streams) if name in by_name)

    if stranded:
        runtime.logger.error(
            f"{len(stranded)} of {len(by_name)} raw file(s) did not make it "
            f"through ingestion: {', '.join(sorted(stranded))}. The demo "
            "database is incomplete - do not capture goldens from this run "
            "(see the converter's log for why each file failed)."
        )
    else:
        runtime.logger.success(
            f"All {len(by_name)} raw file(s) reached the converter, with nothing "
            "left quarantined"
        )
    return sorted(stranded)


def _list_files(directory: Path) -> list[str]:
    """Names of the files directly in ``directory`` (subfolders are not files)."""
    if not directory.is_dir():
        return []
    return [p.name for p in directory.iterdir() if p.is_file()]


def upload_raw_deferred(
    version: str | None = None, source_dir: "Path | None" = None
) -> None:
    """
    Upload raw files once the stack is serving, from a background daemon thread.

    The app is launched in the foreground, so this defers the uploads to a
    thread that first waits for the backend HTTP server and the file-converter's
    socket, uploads, then sweeps the converter's quarantine folder for the rest
    of the run.

    Two things happen *here*, synchronously, because this runs before the stack
    is launched. The converter's socket presence is sampled: whatever the key
    holds now cannot be this run's converter, which is exactly what makes it a
    usable baseline. And the copies an earlier rebuild left in ``filestreams``
    are cleared (:func:`clear_stale_streams`) while no converter is running
    that could start ingesting them.

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override.
    """
    baseline = converter_presence()
    try:
        clear_stale_streams(version, source_dir=source_dir)
    except OSError as e:
        # The worker meets the same problem and reports it; clearing is not
        # worth stopping the launch for.
        runtime.logger.error(f"Could not clear stale stream files: {e}")

    def _worker() -> None:
        url = f"http://localhost:{runtime.meta.api_port}"
        if not _wait_for_backend(url):
            runtime.logger.error(
                "Backend did not become ready in time; skipping raw upload. "
                "Once it is up, re-run 'mascope demo --rebuild' or upload manually."
            )
            return
        try:
            missing = upload_raw(
                version,
                source_dir=source_dir,
                converter_baseline=baseline,
                wait_for_converter=True,
            )
            sweep_failed_uploads(version, source_dir=source_dir, never_uploaded=missing)
        except Exception as e:  # noqa: BLE001 - background thread, just log
            runtime.logger.error(f"Deferred raw upload failed: {e}")

    threading.Thread(target=_worker, name="demo-upload-raw", daemon=True).start()
    runtime.logger.info(
        "Raw files will be uploaded to the server once the backend and the "
        "file-converter are ready; ingestion then proceeds in the background."
    )
