"""
The OpenAPI document a production deployment publishes.

The backend serves its own schema in dev mode only (``openapi_url=None``
otherwise, see ``app/fast.py``), so a production deployment describes its API
with a static copy instead: rendered from the routes when the frontend image
is built, and served by nginx next to the user docs at ``/docs/openapi.json``
(the ``openapi-build`` stage of ``server/frontend/Dockerfile``).

What the app declares depends on the runtime it is imported in - outside prod
the session cookie is named per env - and importing it reads secrets. So
:func:`render` imports the app in a child process, against a throwaway runtime
home set up the way the backend image's is, rather than in the caller's own
runtime. The document comes out the same wherever it is rendered, and nothing
of the machine rendering it - its runtime state, env overrides or secrets - can
reach a file that every deployment serves anonymously.

    uv run mascope-backend openapi --output openapi.json
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


#: The runtime state the backend image bakes in (server/backend/Dockerfile).
_PROD_STATE = {
    "env": {"active": "prod", "override": None},
    "mode": {"active": "prod", "override": None},
}

#: The config layers a prod runtime loads from its home. The env-specific layer
#: is left out on purpose: the document describes a default deployment.
_CONFIG_LAYERS = ("base.mascope.toml", "prod.mascope.toml")

#: Caller variables kept from the child. MASCOPE_ENV and MASCOPE_COOKIE_SCOPED
#: would rename the session cookie the document declares, and a DSN would have
#: the render report to a live error tracker.
_SCRUBBED_ENV = ("MASCOPE_ENV", "MASCOPE_COOKIE_SCOPED", "MASCOPE_SENTRY_DSN")

#: What every secret read answers in the child. At least 32 bytes, so the JWT
#: key-length check (api/new/auth/config.py) stays quiet.
_PLACEHOLDER_SECRET = "placeholder-secret-for-rendering-the-openapi-document"


class OpenApiRenderError(RuntimeError):
    """Raised when the OpenAPI document cannot be rendered."""


def render(output: Path, config_home: Path) -> dict:
    """
    Render the OpenAPI document of a default production deployment.

    :param output: File to write the document to, as JSON.
    :param config_home: Runtime home holding the config layers to render with,
        normally ``MASCOPE_PATH``.
    :return: The rendered document.
    :raises OpenApiRenderError: If a config layer is missing, or the app fails
        to import (the message then carries the child's output).
    """
    with tempfile.TemporaryDirectory(prefix="mascope-openapi-") as tmp:
        home = Path(tmp)
        for name in _CONFIG_LAYERS:
            layer = config_home / name
            if not layer.is_file():
                raise OpenApiRenderError(f"config layer {layer} not found")
            shutil.copyfile(layer, home / name)
        (home / ".runtime").mkdir()
        (home / ".runtime" / "state.json").write_text(
            json.dumps(_PROD_STATE), encoding="utf-8"
        )

        env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
        # PYTHONUTF8: the runtime's terminal log carries glyphs the Windows
        # default code page cannot encode once stdout is a pipe.
        env.update(MASCOPE_PATH=str(home), PYTHONUTF8="1")
        rendered = home / "openapi.json"
        child = subprocess.run(
            [sys.executable, "-m", "mascope_backend.openapi", str(rendered)],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if child.returncode != 0:
            raise OpenApiRenderError(
                "rendering the OpenAPI document failed:\n" + child.stdout + child.stderr
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rendered, output)
    return json.loads(output.read_text(encoding="utf-8"))


def _placeholder_secret(self, envvar: str, path: str, all_lines: bool = False) -> str:
    """Stands in for ``mascope_runtime.Runtime.secret`` in the child."""
    return _PLACEHOLDER_SECRET


def _render_here(output: Path) -> None:
    """
    Child side of :func:`render`: import the app and write its document.

    Only meaningful in the home ``render`` prepares; run on its own it would
    describe whichever runtime the caller happens to be in.

    :param output: File to write the document to.
    """
    from mascope_runtime import Runtime

    # Secrets cannot shape the API description, but the backend reads several
    # while it imports (the database password, the JWT and server-owner keys).
    # Answer every read with a placeholder, so no real secret is opened here
    # and an image build, which has none, needs no stand-in files.
    Runtime.secret = _placeholder_secret

    from mascope_backend.app.fast import fast
    from mascope_backend.runtime import runtime

    if runtime.mode != "prod":
        raise OpenApiRenderError(
            f"the runtime is in {runtime.mode} mode, so the document would not "
            "describe a production deployment - render it with render()"
        )
    # newline: byte-identical on every OS, rather than CRLF on Windows.
    document = json.dumps(fast.openapi(), indent=2) + "\n"
    output.write_text(document, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    _render_here(Path(sys.argv[1]))
