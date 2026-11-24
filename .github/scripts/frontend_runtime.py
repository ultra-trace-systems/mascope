"""
Print the runtime the frontend image was built with, with keys of it changed.

CI's e2e job runs two specs a second time with the assignment views switched
on (.github/docker-compose.demo.assignment.yaml). The frontend reads that
switch, `peak_assignment` under `[meta]`, from the runtime its container
publishes at start (server/frontend/docker-entrypoint.sh), and the value has
to be a whole runtime: the app reads every `[meta]` key it needs from it, and
one written by hand would miss the next key a change adds. So it is derived
from the one the image was built with:

    MASCOPE_PATH=$GITHUB_WORKSPACE MASCOPE_VERSION=<the image's tag> \
        uv run python .github/scripts/frontend_runtime.py meta.peak_assignment=true

The image's build arg comes from the CLI's production build, which computes
it in `_compose_env` from the checkout's config layers in prod mode. This
asks the same function, after the same setup the CLI's callbacks make: the
version, then prod mode with its config reloaded. In the job that built the
images, from the same checkout and with the image's tag as MASCOPE_VERSION,
the result is the build arg itself. Each `KEY=VALUE` then sets one key, a
dotted path into the runtime, to VALUE read as JSON.

Prints one line of JSON on stdout, the runtime the step hands the frontend;
the runtime's logs go to stderr. Starts nothing, and writes no state: the
prod mode is held in memory, not in the home's state.json.
"""

import contextlib
import importlib
import json
import os
import sys


def _set(runtime: dict, assignment: str) -> None:
    """
    Set one dotted key of the runtime to a JSON value.

    :param runtime: The runtime, changed in place
    :param assignment: ``KEY=VALUE``: a dotted path that must already exist,
        and a JSON value
    :raises SystemExit: On a malformed assignment or a key the runtime lacks
    """
    key, sep, value = assignment.partition("=")
    if not sep or not key:
        sys.exit(f"not KEY=VALUE: {assignment!r}")
    *parents, leaf = key.split(".")
    node = runtime
    for part in parents:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            sys.exit(f"the runtime has no {key!r}")
    # Only a key the build already carries: a typo must not add a new one
    # the app never reads, and leave the switch it meant off.
    if not isinstance(node, dict) or leaf not in node:
        sys.exit(f"the runtime has no {key!r}")
    try:
        node[leaf] = json.loads(value)
    except json.JSONDecodeError as exc:
        sys.exit(f"{key}: {value!r} is not JSON ({exc})")


def build_runtime() -> dict:
    """
    The frontend's runtime, as the production build passes it to the image.

    :return: The parsed runtime
    """
    if not os.environ.get("MASCOPE_PATH"):
        sys.exit("MASCOPE_PATH must name the checkout the images were built from")
    # By its module path: the package re-exports the group's callback as
    # `main`, which hides the module of the same name.
    prod = importlib.import_module("mascope_cli.cmd.prod.main")
    from mascope_cli.runtime import get_runtime
    from mascope_cli.version import resolve_version
    from mascope_runtime.state import RuntimeTempState

    # Every log line to stderr, so stdout carries the JSON alone.
    with contextlib.redirect_stdout(sys.stderr):
        runtime = get_runtime()
        # As the CLI's callback: an explicit MASCOPE_VERSION wins, else git.
        if not os.environ.get("MASCOPE_VERSION"):
            os.environ["MASCOPE_VERSION"] = resolve_version(runtime)
        # As the prod group's callback, then `build`. The callback writes
        # the mode into the home's state.json; this keeps it in memory, so
        # a run against a home that other processes read leaves them alone.
        runtime.state = RuntimeTempState(runtime.env.name, prod._MODE)
        runtime.reload_config()
        baked = prod._compose_env(building=True)["MASCOPE_RUNTIME"]
    return json.loads(baked)


def main(argv: list[str]) -> None:
    runtime = build_runtime()
    for assignment in argv:
        _set(runtime, assignment)
    print(json.dumps(runtime))


if __name__ == "__main__":
    main(sys.argv[1:])
