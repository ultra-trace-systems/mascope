"""
Rebuild path: ingest the bundle's raw files through the real upload pipeline.

Instead of dropping files into the file-converter's watch directory (which has
no auth/context and never registers a file context), this uploads each raw file
to the real ``/api/sample/files/upload`` endpoint - exactly what the File Agent
does. The endpoint registers the file context (auth) and writes the file to
``filestreams``, where the file-converter ingests it and runs the full
``RawProcessor`` -> peak detection -> matching pipeline.

Uploads are driven from a background thread because the app is launched in the
foreground; the thread waits for the backend HTTP server *and* for the
file-converter's socket to connect, then uploads, then keeps sweeping the
converter's quarantine folder so a file that failed anyway is re-uploaded rather
than silently dropped from the rebuild.

The wait matters: the upload endpoint pushes each file's context (uploader
identity + access token) over the ``/file-converter`` socket at upload time. A
converter that has not connected yet never receives it, and the file fails deep
in processing with "not registered in file converter service" and is quarantined
- which is how a rebuild ends up with fewer sample files than the bundle has raw
files while both sample batches still finish ``ready``.
"""

import subprocess
import threading
import time
from pathlib import Path

from mascope_cli.cmd.demo import bundles
from mascope_cli.cmd.demo._seed import DEMO_ENV, env_dir  # noqa: F401 - re-export
from mascope_cli.runtime import runtime


# The fixed file-agent token + service name the demo seeds (mirror
# server seed_demo.DEMO_TOKENS["file-agent"]). Using the file-agent service
# replicates the File Agent's exact upload request.
_UPLOAD_TOKEN = "mascope_demo_file_agent_token"
_UPLOAD_SERVICE = "file-agent"
_UPLOAD_PATH = "sample/files/upload"

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
                "-p",
                str(redis_cfg.port),
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
            f"{_CONVERTER_BLIND_WAIT_S:.0f}s before uploading instead. Files "
            "uploaded before the converter connects are quarantined (they are "
            "retried later, but the rebuild takes longer)."
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
        "uploading anyway. Files it never registered are quarantined and "
        "retried once it comes up."
    )
    return False


def _post_raw(url: str, raw: Path) -> str | None:
    """
    Upload one raw file to the sample-file upload endpoint.

    :param url: Backend base URL.
    :param raw: Raw file to upload.
    :return: ``None`` on success, or the failure description.
    """
    import mascope_sdk
    from mascope_sdk import api_post_file
    from mascope_sdk.exceptions import MascopeError

    # Match the File Agent's service header for the upload request.
    mascope_sdk.SERVICE_NAME = _UPLOAD_SERVICE
    try:
        api_post_file(
            url=url,
            path=_UPLOAD_PATH,
            access_token=_UPLOAD_TOKEN,
            filepath=str(raw),
        )
    except MascopeError as e:
        return str(e)
    return None


def upload_raw(
    version: str | None = None,
    source_dir: "Path | None" = None,
    *,
    converter_baseline: str | None = None,
    wait_for_converter: bool = False,
) -> int:
    """
    Upload the bundle's raw files to the real sample-file upload endpoint.

    Replicates the File Agent's request (bearer ``file-agent`` token +
    ``X-Service-Name: file-agent``) so the server registers context, stores the
    file, and the converter ingests it.

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override (see module docstring).
    :param converter_baseline: Socket-presence value sampled before the stack
        started (:func:`converter_presence`), used only when
        ``wait_for_converter`` is set.
    :param wait_for_converter: Hold the first upload until the file-converter's
        socket has connected, so its file context is not emitted into the void.
    :return: Number of files successfully uploaded.
    """
    raw_files = _raw_files(version, source_dir)
    url = f"http://localhost:{runtime.meta.api_port}"

    if wait_for_converter:
        _wait_for_converter(converter_baseline)

    total = len(raw_files)
    runtime.logger.info(f"Uploading {total} raw file(s) to {url}/api/{_UPLOAD_PATH}")

    uploaded = 0
    for i, raw in enumerate(raw_files, 1):
        error = _post_raw(url, raw)
        if error is None:
            uploaded += 1
        else:
            runtime.logger.error(f"Upload of {raw.name} failed: {error}")
        if i % 25 == 0 or i == total:
            runtime.logger.info(f"  uploaded {uploaded}/{total}")

    if uploaded == total:
        runtime.logger.success(f"Uploaded all {total} raw file(s); ingestion proceeds")
    else:
        runtime.logger.warning(
            f"Uploaded {uploaded}/{total} raw file(s); {total - uploaded} failed "
            "(see errors above)"
        )
    return uploaded


def sweep_failed_uploads(
    version: str | None = None,
    source_dir: "Path | None" = None,
    *,
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
    the converter is connected.

    Ends when the converter has consumed everything: nothing left pending and
    nothing quarantined that is still worth retrying. ``deadline`` is only a
    backstop for a converter that has stopped making progress altogether.

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override.
    :param max_retries: Re-upload attempts per file before giving up on it.
    :param poll: Seconds between sweeps.
    :param deadline: Hard cap on the total sweep, in seconds.
    :return: Names of files still quarantined when the sweep ended (empty when
             the rebuild ingested everything).
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

    remaining = [name for name in _list_files(quarantine) if name in by_name]
    if remaining:
        runtime.logger.error(
            f"{len(remaining)} raw file(s) never ingested after "
            f"{max_retries} retries: {', '.join(sorted(remaining))}. The demo "
            "database is incomplete - do not capture goldens from this run "
            "(see the converter's log for why each file failed)."
        )
    else:
        runtime.logger.success(
            "File-converter drained its queue with nothing left quarantined"
        )
    return sorted(remaining)


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
    socket, then uploads, then sweeps the converter's quarantine folder for the
    rest of the run.

    The converter's socket presence is sampled *here*, synchronously, because
    this runs before the stack is launched: whatever the key holds now cannot be
    this run's converter, which is exactly what makes it a usable baseline.

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override.
    """
    baseline = converter_presence()

    def _worker() -> None:
        url = f"http://localhost:{runtime.meta.api_port}"
        if not _wait_for_backend(url):
            runtime.logger.error(
                "Backend did not become ready in time; skipping raw upload. "
                "Once it is up, re-run 'mascope demo --rebuild' or upload manually."
            )
            return
        try:
            upload_raw(
                version,
                source_dir=source_dir,
                converter_baseline=baseline,
                wait_for_converter=True,
            )
            sweep_failed_uploads(version, source_dir=source_dir)
        except Exception as e:  # noqa: BLE001 - background thread, just log
            runtime.logger.error(f"Deferred raw upload failed: {e}")

    threading.Thread(target=_worker, name="demo-upload-raw", daemon=True).start()
    runtime.logger.info(
        "Raw files will be uploaded to the server once the backend and the "
        "file-converter are ready; ingestion then proceeds in the background."
    )
