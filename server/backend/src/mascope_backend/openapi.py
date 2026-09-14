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
runtime. The document comes out the same wherever it is rendered for a given
version, and nothing of the machine rendering it - its runtime state, env
overrides or secrets - can reach a file that every deployment serves
anonymously. The version it names is the caller's to pass: the frontend image
build passes the one it builds.

    uv run mascope-backend openapi --output site/openapi.json
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path


#: The runtime state the backend image bakes in (server/backend/Dockerfile).
_PROD_STATE = {
    "env": {"active": "prod", "override": None},
    "mode": {"active": "prod", "override": None},
}

#: The config layers a prod runtime loads from its home. The env-specific layer
#: is left out on purpose: the document describes a default deployment.
_CONFIG_LAYERS = ("base.mascope.toml", "prod.mascope.toml")

#: Caller variables kept from the child: every Mascope setting, so none of the
#: caller's runtime can shape the document - MASCOPE_ENV and
#: MASCOPE_COOKIE_SCOPED would rename the session cookie it declares, and a
#: MASCOPE_SENTRY_DSN would have the render report to a live error tracker.
_SCRUBBED_PREFIX = "MASCOPE_"

#: Interpreter settings kept from the child as well: PYTHONOPTIMIZE=2 strips
#: docstrings, which the operations take their descriptions from and which a
#: dependency (pyteomics) needs to import at all.
_SCRUBBED_ENV = ("PYTHONOPTIMIZE",)

#: What every secret read answers in the child. At least 32 bytes, so the JWT
#: key-length check (api/new/auth/config.py) stays quiet.
_PLACEHOLDER_SECRET = "placeholder-secret-for-rendering-the-openapi-document"


class OpenApiRenderError(RuntimeError):
    """Raised when the OpenAPI document cannot be rendered."""


def _child_environment(home: Path, version: str | None) -> dict[str, str]:
    """
    The environment the child renders in.

    The caller's own, less every Mascope setting and ``PYTHONOPTIMIZE``, pointed
    at the throwaway ``home``. ``MASCOPE_VERSION``, which the app names as the
    document's ``info.version``, comes only from ``version``: which release a
    document describes is for the caller to say, not whatever version the
    rendering machine's environment happens to carry.

    :param home: The throwaway runtime home.
    :param version: The version the document describes, if one was given.
    :return: The child's environment.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_SCRUBBED_PREFIX) and k not in _SCRUBBED_ENV
    }
    # PYTHONUTF8: the runtime's terminal log carries glyphs the Windows
    # default code page cannot encode once stdout is a pipe.
    env.update(MASCOPE_PATH=str(home), PYTHONUTF8="1")
    if version:
        env["MASCOPE_VERSION"] = version
    return env


def render(output: Path, config_home: Path, version: str | None = None) -> dict:
    """
    Render the OpenAPI document of a default production deployment.

    :param output: File to write the document to, as JSON.
    :param config_home: Runtime home holding the config layers to render with,
        normally ``MASCOPE_PATH``.
    :param version: The Mascope version the document describes, named as its
        ``info.version``; without one it carries the app's placeholder.
    :return: The rendered document.
    :raises OpenApiRenderError: If ``output`` is a directory, a config layer is
        missing, the app fails to import (the message then carries the child's
        output), or the document cannot be written.
    """
    # Refused up front rather than once the render has taken its seconds.
    if output.is_dir():
        raise OpenApiRenderError(f"{output} is a directory, not a file to write to")
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

        rendered = home / "openapi.json"
        child = subprocess.run(
            [sys.executable, "-m", "mascope_backend.openapi", str(rendered)],
            env=_child_environment(home, version),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if child.returncode != 0:
            raise OpenApiRenderError(
                "rendering the OpenAPI document failed:\n" + child.stdout + child.stderr
            )
        document = rendered.read_bytes()
    # Written out rather than copied, so a stream such as /dev/stdout works as
    # the output as well as a file.
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(document)
    except OSError as e:
        raise OpenApiRenderError(f"cannot write {output}: {e}") from e
    return json.loads(document)


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
    # Warnings fail the render: FastAPI reports a duplicate operation ID, and
    # pydantic a default it cannot serialize, only as a UserWarning, and either
    # would otherwise ship a degraded document from a green build.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        schema = fast.openapi()
    # newline: byte-identical on every OS, rather than CRLF on Windows.
    document = json.dumps(schema, indent=2) + "\n"
    output.write_text(document, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    _render_here(Path(sys.argv[1]))
