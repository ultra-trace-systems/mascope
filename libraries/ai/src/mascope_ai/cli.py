"""``mascope-ai``: the toolkit's command line.

::

    mascope-ai setup          check the install, the configuration and the
                              connection; idempotent, run it any time
    mascope-ai list [KIND]    what there is: Mascope's objects by kind, the
                              sessions on this machine, a session's Frames
    mascope-ai mcp            the MCP server, for a coding agent (stdio)
    mascope-ai skill          the skill text a coding agent is given

The Mascope URL and the AI agent's token come from the environment or a
``.env`` file, as the SDK reads them (``MASCOPE_URL``,
``MASCOPE_ACCESS_TOKEN``), and so does the workspace, ``MASCOPE_WORKSPACE``,
when the token's user has more than one. ``--workspace`` overrides it.
"""

import argparse
import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from importlib.resources import files

from dotenv import dotenv_values, find_dotenv
from loguru import logger

from mascope_sdk import MascopeClient
from mascope_sdk.exceptions import ConfigurationError

from .errors import ToolkitError, sdk_errors
from .manifest import package_version
from .mcp import Server, inert
from .refs import KINDS, PARENT_KIND
from .session import Session, default_root
from .toolkit import Toolkit


#: The service an AI agent's token is generated for.
SERVICE = "ai-agent"

#: What ``list`` takes for each kind, plural or not.
_KINDS = {
    **{kind: kind for kind in KINDS},
    "workspaces": "workspace",
    "datasets": "dataset",
    "batches": "batch",
    "samples": "sample",
    "collections": "collection",
    "sessions": "session",
    "session": "session",
    "frames": "frame",
    "frame": "frame",
}


def configured_workspace(given: str | None = None) -> str | None:
    """The workspace to open: *given*, else ``MASCOPE_WORKSPACE`` from the
    environment, else from the ``.env`` file the SDK would read."""
    if given:
        return given
    if os.environ.get("MASCOPE_WORKSPACE"):
        return os.environ["MASCOPE_WORKSPACE"]
    dotenv = find_dotenv(usecwd=True)
    return (dotenv_values(dotenv).get("MASCOPE_WORKSPACE") if dotenv else None) or None


def quoted(text: str) -> str:
    """A name as the command line prints it: inert, in double quotes."""
    return json.dumps(inert(text), ensure_ascii=False)


def skill_text() -> str:
    """The skill a coding agent is given with the server (``SKILL.md``)."""
    return files("mascope_ai").joinpath("SKILL.md").read_text(encoding="utf-8")


# --- mcp ---------------------------------------------------------------------


def _mcp(args: argparse.Namespace) -> int:
    session = Session(args.session)
    server = Server(session, workspace=configured_workspace(args.workspace))
    # Stdout is the protocol's alone: keep it for the server, and point the
    # descriptor under sys.stdout at stderr, where the client logs it, so
    # that whatever else is written there - a print, a library's progress
    # bar, a native library's output - cannot break a message
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    # The SDK's client drops loguru's default handler and logs its own
    # lines alone; this one keeps the toolkit's, a failed tool's traceback
    # among them
    logger.add(
        sys.stderr,
        level="INFO",
        filter=lambda record: not record["name"].startswith("mascope_sdk"),
    )
    logger.info("mascope-ai mcp: session {} at {}", session.id, session.path)
    try:
        server.serve(sys.stdin.buffer, protocol)
    except KeyboardInterrupt:
        pass
    return 0


# --- setup -------------------------------------------------------------------


def _setup(args: argparse.Namespace) -> int:
    ok = True

    def report(passed: bool, text: str) -> None:
        nonlocal ok
        ok = ok and passed
        print(f"{'ok  ' if passed else 'FAIL'}  {text}")

    print(
        f"mascope_ai {package_version('mascope_ai')}, "
        f"mascope_sdk {package_version('mascope_sdk')}, "
        f"Python {platform.python_version()} ({sys.executable})"
    )

    root = default_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root):
            pass
        report(True, f"sessions are kept in {root}")
    except OSError as e:
        report(False, f"cannot write sessions to {root}: {e}. Set MASCOPE_AI_SCRATCH.")

    workspace = configured_workspace(args.workspace)
    try:
        with sdk_errors("connect to Mascope"):
            client = MascopeClient(workspace=workspace, service_name=SERVICE)
            rows = client.workspaces.list()
        mine = rows[rows["workspace_id"] == client.workspace_id]
        role = mine.iloc[0].get("my_role") if not mine.empty else None
        report(
            True,
            f"connected to {client.url} as an AI agent: workspace "
            f"{quoted(client.workspace_name)}, role {role or 'unknown'}",
        )
    except ConfigurationError as e:
        report(False, f"{e} Set it in the environment or a .env file.")
    except ToolkitError as e:
        report(False, e.message)

    print(
        "\nNext:\n"
        "  1. Register the server with Claude Code, once:\n"
        f"       claude mcp add mascope -- {sys.executable} -m mascope_ai mcp\n"
        "     It reads MASCOPE_URL, MASCOPE_ACCESS_TOKEN and MASCOPE_WORKSPACE as\n"
        "     this command did; pass them with -e NAME=value if Claude Code's\n"
        "     environment lacks them.\n"
        "  2. Give the agent the skill:\n"
        "       mkdir -p ~/.claude/skills/mascope-ai\n"
        f"       {sys.executable} -m mascope_ai skill > "
        "~/.claude/skills/mascope-ai/SKILL.md\n"
        "  3. `mascope-ai list` shows what there is."
    )
    return 0 if ok else 1


# --- list --------------------------------------------------------------------


def _list(args: argparse.Namespace) -> int:
    kind = _KINDS[args.kind] if args.kind else None
    if kind == "session":
        return _list_sessions()
    if kind == "frame":
        return _list_frames(args.session)
    with tempfile.TemporaryDirectory(prefix="mascope-ai-list-") as scratch:
        # The listing is part of a conversation's record only when it names
        # the conversation's session
        session = Session(args.session) if args.session else Session(root=scratch)
        try:
            kit = Toolkit(
                session=session, workspace=configured_workspace(args.workspace)
            )
            within = kit.resolve(args.dataset, "dataset") if args.dataset else None
            if args.batch:
                within = kit.resolve(args.batch, "batch", within=within)
            found = kit.find(args.query, kind, args.limit, within=within)
        except ConfigurationError as e:
            print(f"{e} `mascope-ai setup` checks the configuration.", file=sys.stderr)
            return 1
        except ToolkitError as e:
            print(e.message, file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(inert(found.to_dict()), ensure_ascii=False, indent=1))
        return 0
    for ref in found:
        parent = f"  in {PARENT_KIND[ref.kind]} {ref.parent}" if ref.parent else ""
        print(f"{ref.kind:<10}  {ref.id}  {quoted(ref.name)}{parent}")
    shown = f"{len(found)} of {found.total} shown"
    if found.total > len(found):
        shown += "; --limit shows more"
    if not found.complete:
        shown += f"; INCOMPLETE, {len(found.failures)} listing(s) failed"
    print(shown)
    return 0


def _list_sessions() -> int:
    root = default_root()
    paths = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    for path in paths:
        manifest = path / "manifest.jsonl"
        stamp = (manifest if manifest.exists() else path).stat().st_mtime
        when = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")
        frames = len(list((path / "frames").glob("f_*.json")))
        print(f"{path.name}  {frames} frame(s)  last written {when}")
    print(f"{len(paths)} session(s) in {root}")
    return 0


def _list_frames(session_id: str | None) -> int:
    if not session_id:
        print("list frames needs --session (list sessions shows them)", file=sys.stderr)
        return 1
    try:
        session = Session(session_id)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    if not session.path.exists():
        print(f"No session {session_id} in {session.path.parent}", file=sys.stderr)
        return 1
    frames = session.frames()
    for frame in frames:
        print(
            f"{frame.id}  {frame.n_rows:,} rows  {frame.query.get('op', '?')}  "
            f"{'complete' if frame.complete else 'INCOMPLETE'}"
        )
    print(
        f"{len(frames)} Frame(s); in Python: from mascope_ai import Session; "
        f"Session({session.id!r}, root={str(session.path.parent)!r}).frame(ID).df"
    )
    return 0


# --- skill -------------------------------------------------------------------


def _skill(args: argparse.Namespace) -> int:
    print(skill_text(), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mascope-ai",
        description="Mascope's toolkit for AI agents: its MCP server, setup and "
        "discovery.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def workspace(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--workspace",
            help="the workspace, by name or ID (default: MASCOPE_WORKSPACE)",
        )

    mcp = commands.add_parser("mcp", help="serve the tools over stdio (MCP)")
    mcp.add_argument(
        "--session",
        default=os.environ.get("MASCOPE_AI_SESSION"),
        help="the session to keep Frames in (default: MASCOPE_AI_SESSION, "
        "else a new one)",
    )
    workspace(mcp)

    setup = commands.add_parser(
        "setup", help="check the install, the configuration and the connection"
    )
    workspace(setup)

    lister = commands.add_parser("list", help="what there is")
    lister.add_argument(
        "kind",
        nargs="?",
        choices=sorted(_KINDS),
        metavar="KIND",
        help="workspaces, datasets, batches, samples, collections (every kind "
        "but samples when left out), sessions, or frames (of --session)",
    )
    lister.add_argument("query", nargs="?", default="", help="part of a name, or an ID")
    lister.add_argument("--dataset", help="within this dataset")
    lister.add_argument("--batch", help="within this batch")
    lister.add_argument("--limit", type=int, default=50)
    lister.add_argument("--session", help="the session to record in, or to list")
    lister.add_argument("--json", action="store_true", help="print JSON")
    workspace(lister)

    commands.add_parser("skill", help="print the skill text")

    args = parser.parse_args(argv)
    run = {"mcp": _mcp, "setup": _setup, "list": _list, "skill": _skill}
    return run[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
