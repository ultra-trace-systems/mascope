"""Read a py-spy profile of a timed run down to the functions (MAS-1179).

``perf.py`` says how long each step and stage takes; this says which
functions the time goes to. Record the run with py-spy's raw output, one
line per distinct stack and its sample count::

    py-spy record --rate 20 --nonblocking --threads --idle --format raw \\
        --output run.txt -- uv run python tooling/assignment_perf/perf.py ...
    uv run python tooling/assignment_perf/profile_report.py run.txt

It prints, as Markdown tables a PR body can paste:

- the functions with the most time on the stack (inclusive), backend and
  library code only, and those with the most time at the top (self);
- the time in a few kinds of work across the whole run: SQL (the driver
  and SQLAlchemy), pandas row access (``iterrows``, ``.loc``/``.iloc`` on
  rows, ``apply`` by row), the rest of pandas, numpy, and idle.

The loop thread's samples, idle ones included, are the run's wall time.
The work is counted over every thread's busy samples: since MAS-947 the
engine's steps run in ``to_thread`` workers while the loop waits.
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
import sysconfig
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional


#: A frame as py-spy writes it: ``function (path:line)``.
FRAME = re.compile(r"^(?P<fn>.*?) \((?P<path>[^()]*?)(?::(?P<line>\d+))?\)$")

#: Mascope's own code: the backend and the libraries, where each module's
#: file lives. py-spy writes a path short, from the package
#: (``mascope_tools/composition/line_model.py``, or
#: ``peak_assignments/tiering.py`` for the backend), so a frame is ours when
#: some file under these ends in its path. An allowlist: a package nobody
#: listed is never counted as ours.
SOURCE_ROOTS = ("server/backend/src", "libraries/*/src")

#: The checkout this script lives in.
REPOSITORY = Path(__file__).resolve().parents[2]


@functools.cache
def our_paths(repository: Path = REPOSITORY) -> frozenset[str]:
    """Every tail of every module path under the source roots:
    ``mascope_backend/api/new/peak_assignments/tiering.py`` gives that, and
    ``api/new/...``, down to ``tiering.py``. Standard-library names are left
    out, so a module of ours called like one never claims it."""
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    tails = set()
    for root in SOURCE_ROOTS:
        for base in repository.glob(root):
            for file in base.rglob("*.py"):
                parts = file.relative_to(base).parts
                for i in range(len(parts)):
                    tail = "/".join(parts[i:])
                    if not (stdlib / tail).exists():
                        tails.add(tail)
    return frozenset(tails)


#: The kinds of work, matched against a stack's frames; the first that
#: matches anywhere in the stack takes the sample.
KINDS = (
    ("SQL", ("asyncpg/", "sqlalchemy/", "psycopg")),
    (
        "pandas row access",
        (
            "iterrows (pandas",
            "itertuples (pandas",
            "_LocIndexer",
            "_iLocIndexer",
            "__getitem__ (pandas/core/indexing.py",
            "_getitem_axis (pandas/core/indexing.py",
            "_get_value (pandas/core/frame.py",
            "apply_standard (pandas/core/apply.py",
            "_ixs (pandas/core/frame.py",
        ),
    ),
    ("pandas, the rest", ("pandas/",)),
    ("numpy and scipy", ("numpy/", "scipy/")),
)


def parse(lines: Iterable[str]) -> list[tuple[str, list[str], int]]:
    """``(thread, frames from the root, samples)`` per line of a raw profile.

    py-spy writes the thread as the first frame when ``--threads`` is on."""
    stacks = []
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        stack, _, count = line.rpartition(" ")
        if not count.isdigit():
            continue
        frames = stack.split(";")
        thread = (
            frames[0] if frames and frames[0].startswith(("thread", "process")) else ""
        )
        if thread:
            frames = frames[1:]
        stacks.append((thread, frames, int(count)))
    return stacks


def short(frame: str) -> str:
    """``module.function`` for a frame, the module from its path."""
    m = FRAME.match(frame.strip())
    if not m:
        return frame.strip()
    path = m.group("path")
    for marker in ("site-packages/", "src/"):
        if marker in path:
            path = path.rsplit(marker, 1)[1]
    module = path.removesuffix(".py").replace("/", ".")
    return f"{module}.{m.group('fn')}"


def ours(frame: str) -> bool:
    """Whether a frame runs Mascope's code (:data:`SOURCE_ROOTS`)."""
    m = FRAME.match(frame.strip())
    if not m:
        return False
    path = m.group("path")
    if "site-packages/" in path:
        return path.rsplit("site-packages/", 1)[1].startswith("mascope")
    return path in our_paths()


#: Frames a thread waits in: a sample whose top frame is one of these did no
#: work. py-spy's ``--idle`` keeps them, so the run's length can be read off
#: the loop thread's samples.
IDLE_TOPS = (
    "_worker (concurrent/futures/thread.py",
    "wait (threading.py",
    "_recv (multiprocessing/connection.py",
    "Connection._recv (multiprocessing/connection.py",
    "select (selectors.py",
    "get (queue.py",
    "sleep (",
    "epoll",
)

#: Threads that are the measurement's own or the logger's, never the work's.
OWN_THREADS = ("(sample)", "loguru-writer")


def loop_thread(stacks) -> Optional[str]:
    """The event loop's thread: Python's main thread."""
    for thread, _, _ in stacks:
        if thread.endswith("MainThread"):
            return thread
    totals = Counter()
    for thread, _, n in stacks:
        totals[thread] += n
    return totals.most_common(1)[0][0] if totals else None


def is_idle(frames: list[str]) -> bool:
    top = frames[-1] if frames else ""
    return any(w in top for w in IDLE_TOPS)


def report(stacks, rate: float, top: int = 25) -> str:
    """The run by kind of work and by function, over every thread's busy
    samples: since MAS-947 the engine's steps run in ``to_thread`` workers,
    so the loop thread mostly waits while a worker does the work."""
    loop = loop_thread(stacks)
    inclusive: Counter = Counter()
    self_time: Counter = Counter()
    kinds: Counter = Counter()
    wall = busy = loop_busy = 0
    for thread, frames, n in stacks:
        if thread == loop:
            wall += n
        if any(o in thread for o in OWN_THREADS) or is_idle(frames):
            continue
        busy += n
        if thread == loop:
            loop_busy += n
        for name in {short(f) for f in frames if ours(f)}:
            inclusive[name] += n
        ours_frames = [f for f in frames if ours(f)]
        if ours_frames:
            self_time[short(ours_frames[-1])] += n
        joined = ";".join(frames)
        for kind, needles in KINDS:
            if any(needle in joined for needle in needles):
                kinds[kind] += n
                break
        else:
            kinds["Python, ours and the rest"] += n

    def secs(n: int) -> str:
        return f"{n / rate:,.1f}"

    def share(n: int) -> str:
        return f"{100 * n / busy:.1f}" if busy else "-"

    cores = busy / wall if wall else 0.0
    out = [
        f"Run: {secs(wall)} s. Busy, all threads: {secs(busy)} s "
        f"({cores:.2f} cores on average), of it on the loop thread {secs(loop_busy)} s.",
        "",
        "| kind of work (busy samples, all threads) | s | % |",
        "| -- | -- | -- |",
    ]
    for kind, n in kinds.most_common():
        out.append(f"| {kind} | {secs(n)} | {share(n)} |")
    out += ["", "| function (inclusive) | s | % |", "| -- | -- | -- |"]
    for name, n in inclusive.most_common(top):
        out.append(f"| `{name}` | {secs(n)} | {share(n)} |")
    out += ["", "| function (self, our deepest frame) | s | % |", "| -- | -- | -- |"]
    for name, n in self_time.most_common(top):
        out.append(f"| `{name}` | {secs(n)} | {share(n)} |")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("profile", help="py-spy's raw output")
    parser.add_argument("--rate", type=float, default=20.0, help="samples a second")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)
    with Path(args.profile).open() as fh:
        print(report(parse(fh), args.rate, args.top))


if __name__ == "__main__":
    sys.exit(main())
