"""The backend's compose healthcheck asks the readiness route, not the socket.

A probe that only connected to the backend's port kept a backend whose
Postgres and Redis were stopped ``healthy``, and everything that reads the
stack's health reads this probe: ``mascope prod update`` waits on it, and
``mascope prod doctor`` reports it (#3470). The probe now requests
``GET /ready``, which answers 200 only while the backend reaches both.

The probe is run here as compose would run it, against a stub server on a
free port, so what it accepts is tested rather than read off its text. A 200
passes, and so does a 404: that is a backend image older than the route, which
the published release is until one ships it, and which answers HTTP, all the
earlier probe asked of it. A 503, the route's own not ready, fails, as does
any other answer or none. The compose files tested are the packaged copies,
which ``test_package_data.py`` holds equal to the repository's.
"""

import http.server
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

import mascope_cli


DATA_DIR = Path(mascope_cli.__file__).resolve().parent / "data"
COMPOSE_FILES = ["docker-compose.yaml", "docker-compose.demo.yaml"]


def _backend_healthcheck(name: str) -> dict:
    compose = yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))
    return compose["services"]["backend"]["healthcheck"]


def _probe_command(name: str, port: int) -> str:
    """The backend's probe as a shell command, pointed at ``port``."""
    kind, command = _backend_healthcheck(name)["test"]
    assert kind == "CMD-SHELL"
    assert "8090" in command and "/ready" in command
    return command.replace("8090", str(port)).replace(
        "python -c", f'"{sys.executable}" -c', 1
    )


class _Stub(http.server.BaseHTTPRequestHandler):
    """Answers every request with the class's status, and records its path."""

    status = 200
    paths: list[str] = []

    def do_GET(self):  # noqa: N802 - the handler's name is http.server's
        type(self).paths.append(self.path)
        self.send_response(self.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def stub():
    """A server on a free port whose answer each test sets."""
    _Stub.status, _Stub.paths = 200, []
    server = http.server.ThreadingHTTPServer(("localhost", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _run(command: str) -> int:
    return subprocess.run(["sh", "-c", command], timeout=30).returncode


@pytest.mark.skipif(sys.platform == "win32", reason="compose runs the probe in sh")
@pytest.mark.parametrize("name", COMPOSE_FILES)
@pytest.mark.parametrize(
    ("status", "passes"),
    [(200, True), (404, True), (503, False), (204, False), (500, False)],
)
def test_the_probe_passes_on_ready_or_on_a_backend_without_the_route(
    stub, name, status, passes
):
    _Stub.status = status

    code = _run(_probe_command(name, stub.server_address[1]))

    assert (code == 0) is passes
    assert _Stub.paths == ["/ready"]


@pytest.mark.skipif(sys.platform == "win32", reason="compose runs the probe in sh")
@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_the_probe_fails_while_nothing_listens(name):
    with socket.socket() as s:
        s.bind(("localhost", 0))
        port = s.getsockname()[1]
    # Closed again: nothing listens on the port now.

    assert _run(_probe_command(name, port)) != 0


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_the_probe_timings_are_the_ones_the_update_waits_for(name):
    """The readiness route changed what the probe asks, not when: the 360 s
    ``wait_healthy`` waits is the start period and five probes ten seconds
    apart (``test_prod_auto.py``), and each probe has 5 s to answer."""
    check = _backend_healthcheck(name)

    assert (
        check["interval"],
        check["timeout"],
        check["retries"],
        check["start_period"],
    ) == ("10s", "5s", 5, "300s")


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_the_file_converter_still_switches_the_inherited_check_off(name):
    compose = yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))

    assert compose["services"]["file_converter"]["healthcheck"] == {}
