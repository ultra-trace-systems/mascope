"""
Lazy access to the CLI's Runtime instance.

Importing this module is side-effect free. Creating a `Runtime` requires a
configured environment — `MASCOPE_PATH`, the `*.mascope.toml` layers, a
writable `.runtime/` for `state.json` — and reconfigures the global logger,
so instantiation is deferred until a command actually needs it. This keeps
`import mascope_cli` (and e.g. `mascope --help`) working anywhere.
"""

import os

import typer

from mascope_cli.home import default_home, is_initialized
from mascope_runtime import Runtime


# The file in an env's filestore where the backend keeps the id it generated
# for its deployment. It is mascope_runtime.config.DEPLOYMENT_FILE, named here
# rather than imported so the CLI still imports against an installed runtime
# older than that name, as its floor in pyproject.toml allows. A test keeps
# the two equal.
DEPLOYMENT_FILE = "deployment.json"

_runtime: Runtime | None = None


def _ensure_mascope_path() -> None:
    """
    Resolve MASCOPE_PATH before the Runtime reads it.

    An explicit env var always wins; otherwise fall back to the platform
    default home if `mascope init` has set it up. The resolved path is
    exported to the environment so subprocesses (docker compose) and every
    code path reading `os.environ["MASCOPE_PATH"]` see the same home.

    :raises typer.Exit: When no runtime home can be resolved.
    """
    if os.environ.get("MASCOPE_PATH"):
        return
    home = default_home()
    if is_initialized(home):
        os.environ["MASCOPE_PATH"] = str(home)
        return
    typer.secho(
        "No Mascope runtime home found. Run `mascope init` to create one at "
        f"{home}, or set MASCOPE_PATH to an existing Mascope directory.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(1)


def get_runtime() -> Runtime:
    """
    The CLI's Runtime singleton, created on first use.

    :return: The shared Runtime instance for the `cli` module.
    :rtype: Runtime
    """
    global _runtime
    if _runtime is None:
        _ensure_mascope_path()
        _runtime = Runtime("cli")
    return _runtime


class _LazyRuntime:
    """
    Attribute proxy over :func:`get_runtime`.

    Lets command modules keep importing `runtime` at module scope while the
    underlying Runtime is only instantiated on first attribute access.
    """

    def __getattr__(self, name: str):
        return getattr(get_runtime(), name)


runtime = _LazyRuntime()
