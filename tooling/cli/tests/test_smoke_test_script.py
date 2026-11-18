"""
Tests for tooling/smoke-test.sh, the release gate and the packaged-smoke job's
check of a running stack.

The script runs against a stub stack: a local HTTP server that answers the
routes it reads. Every body is large, with the text a check looks for at its
start, which is the case that broke the gate at random (mascope#3179): a check
that piped the body into ``grep -q`` saw grep exit at the first match, the
writer fail on the closed pipe, and ``pipefail`` turn a served page into a
failed check. With bodies this size that happens on every run, not some.
Skipped without bash or curl (CI runs these on Ubuntu).
"""

import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "smoke-test.sh"

BASH = shutil.which("bash")
CURL = shutil.which("curl")

pytestmark = [
    pytest.mark.skipif(not SCRIPT.is_file(), reason="tooling/smoke-test.sh absent"),
    pytest.mark.skipif(
        sys.platform == "win32" or BASH is None or CURL is None,
        reason="needs bash and curl on a POSIX host",
    ),
]

EMAIL = "demo@mascope.app"
# Far past any pipe buffer or single write, so a reader that stops at the
# first match always leaves the writer with bytes it cannot write. In lines,
# as a real page is: grep reads a whole line before it can stop, so one 4 MB
# line would be read to its end and hide the race.
PADDING = ("x" * 79 + "\n") * (50 * 1024)
# The same as JSON whitespace, so a JSON body stays valid JSON.
JSON_PADDING = (" " * 79 + "\n") * (50 * 1024)


def _pages() -> dict[str, tuple[str, str]]:
    """Path -> (content type, body) of a stack the smoke test passes on."""
    return {
        "/": ("text/html", f"<!DOCTYPE html><html><!-- {PADDING} --></html>"),
        "/docs/": (
            "text/html",
            f"<!doctype html><title>Mascope docs</title><!-- {PADDING} -->",
        ),
        "/docs/help-content.json": (
            "application/json",
            '{"title": "help"' + JSON_PADDING + "}",
        ),
        "/docs/openapi.json": (
            "application/json",
            '{"openapi": "3.1.0",' + JSON_PADDING + '"paths": {}}',
        ),
        "/api/users/me": ("application/json", json.dumps({"email": EMAIL})),
        "/api/workspaces": (
            "application/json",
            '{"data": [{"workspace_id": "w1"}],' + JSON_PADDING + '"total": 1}',
        ),
    }


class _Stack:
    """A threaded HTTP server answering the smoke test's routes from ``pages``."""

    def __init__(self, pages: dict[str, tuple[str, str]]):
        stack = self
        self.pages = pages

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the test output quiet
                pass

            def _send(self, status: int, ctype: str, body: bytes, cookie=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # a reader that hung up early, as the old checks did

            def do_GET(self):
                page = stack.pages.get(self.path)
                if page is None:
                    self._send(404, "text/plain", b"not found")
                    return
                ctype, body = page
                self._send(200, ctype, body.encode())

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if self.path == "/api/auth/login":
                    self._send(200, "application/json", b"{}", "mascope_auth=t")
                else:
                    self._send(404, "text/plain", b"not found")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _run(pages: dict[str, tuple[str, str]]) -> subprocess.CompletedProcess:
    with _Stack(pages) as stack:
        return subprocess.run(
            [BASH, str(SCRIPT)],
            env={
                "PATH": str(Path(CURL).parent) + ":/usr/bin:/bin",
                "SMOKE_BASE_URL": stack.url,
                "SMOKE_EMAIL": EMAIL,
                "SMOKE_TIMEOUT": "0",
            },
            capture_output=True,
            text=True,
            timeout=120,
        )


def test_passes_on_large_bodies_matched_at_their_start():
    result = _run(_pages())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[smoke] PASS" in result.stdout
    # No writer was cut off: curl's exit 23 or a SIGPIPE'd echo would say so.
    assert "Failure writing output" not in result.stderr


@pytest.mark.parametrize(
    ("path", "body", "message"),
    [
        ("/", "<html>no doctype</html>", "response is not an HTML document"),
        ("/docs/", "<!doctype html><title>Docs</title>", "/docs/ does not serve"),
        ("/docs/help-content.json", "[]", "/docs/help-content.json is missing"),
        ("/docs/openapi.json", '{"swagger": "2.0"}', "/docs/openapi.json does not"),
        # The address is matched as text: a regex would let "." match the "-".
        ("/api/users/me", '{"email": "demo@mascope-app"}', "does not identify"),
        ("/api/workspaces", '{"data": []}', "returned no workspaces"),
    ],
)
def test_each_check_fails_on_a_body_without_its_text(path, body, message):
    pages = _pages()
    pages[path] = (pages[path][0], body)
    result = _run(pages)
    assert result.returncode == 1
    assert message in result.stderr
    assert "[smoke] PASS" not in result.stdout


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/docs/", "/docs/ does not serve the documentation site"),
        ("/docs/help-content.json", "/docs/help-content.json is missing"),
        ("/docs/openapi.json", "/docs/openapi.json does not serve"),
    ],
)
def test_a_missing_page_fails_with_its_own_message(path, message):
    pages = _pages()
    del pages[path]  # the stub answers 404, so curl -f fails the fetch
    result = _run(pages)
    assert result.returncode == 1
    assert message in result.stderr
