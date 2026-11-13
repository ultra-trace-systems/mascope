"""``bench run <ref> --post <pr>``: the card as one comment on a pull request.

Step 6.6 of the assignment quality plan. A run that is given a pull request
posts what it made there, under the caller's own ``gh`` identity (the builder
App on the agent box, as for every comment the agents write):

- **a card**: its gate line, the headline table (the samples read and every
  gated reading), what moved since develop's baseline among those, and the
  whole card and each set's card in collapsed sections;
- **a refused card**: the refusal, the file that held the name and the kind
  of name it was, never the name;
- **a failed run**: the tail of its error.

The PR carries one bench comment: the first comment that starts with
:data:`MARKER` is edited in place on a re-run, and one is made only when there
is none (or it cannot be edited). A PR whose branch lives in a fork is refused
before anything runs. Everything posted is scrubbed with the bench's deny list
first (``run.py``); a body the scrub refuses is replaced by the refusal.

This module holds the PR's side and the comment's text; ``run.py`` decides
what to post and scrubs it.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]

#: The bench comment's first line: how a re-run finds the comment to edit.
MARKER = "<!-- mascope-bench-card -->"

#: GitHub refuses a comment over 65,536 characters; the body stays under this.
LIMIT = 60_000

#: How much of an error a failed run posts.
TAIL_LINES = 30
TAIL_CHARS = 3_000

#: The kinds of name the deny list holds, in the order a token is matched
#: against them: a refused card names the kind, never the name.
KINDS = (
    ("ids", "a sample, file or run id"),
    ("strict", "a raw file's name"),
    ("hosts", "a host name"),
    (
        "names",
        "a name the bench instance holds (a workspace, dataset, batch, "
        "instrument, ionization mode, collection or set label)",
    ),
)
UNKNOWN_KIND = "a name the deny list holds"

#: Where a refusal was made when it was the whole comment, not a card file.
COMMENT = "the comment"


class PostError(RuntimeError):
    """The PR cannot take the card (a fork), or ``gh`` failed; the message says why."""


# ----------------------------------------------------------------- the PR


@dataclass
class PullRequest:
    number: int
    url: str
    head_sha: str
    head_branch: str
    base_branch: str


class Gh(Protocol):
    """What posting asks of GitHub (a fake in the tests)."""

    def pull(self, number: int) -> dict:
        """``gh pr view``'s JSON: url, head and base, ``isCrossRepository``."""

    def marked(self, number: int) -> list[dict]:
        """The PR's comments that start with :data:`MARKER`, oldest first:
        ``{"id", "url"}``."""

    def create(self, number: int, body: str) -> str:
        """Comment on the PR; the comment's URL."""

    def edit(self, comment_id: int, body: str) -> str:
        """Replace a comment's body; the comment's URL."""


class GhCli:
    """``gh`` as the caller is signed in, run in a checkout whose remote
    names the repository (``{owner}/{repo}`` in ``gh api`` paths)."""

    def __init__(self, cwd: Path = REPO, gh: str = "gh"):
        self.cwd = cwd
        self.gh = gh

    def _gh(self, *args: str, body: str | None = None) -> str:
        try:
            out = subprocess.run(
                [self.gh, *args],
                cwd=self.cwd,
                input=None if body is None else json.dumps({"body": body}),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise PostError(f"gh could not be run: {error}") from None
        if out.returncode != 0:
            raise PostError(
                f"gh {' '.join(args[:2])} failed: {out.stderr.strip()[-400:]}"
            )
        return out.stdout

    def pull(self, number: int) -> dict:
        return json.loads(
            self._gh(
                "pr",
                "view",
                str(number),
                "--json",
                "number,url,headRefOid,headRefName,baseRefName,isCrossRepository",
            )
        )

    def marked(self, number: int) -> list[dict]:
        out = self._gh(
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/issues/{number}/comments",
            "--jq",
            f".[] | select(.body | startswith({json.dumps(MARKER)})) "
            "| {id: .id, url: .html_url}",
        )
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def create(self, number: int, body: str) -> str:
        out = self._gh(
            "api",
            "-X",
            "POST",
            f"repos/{{owner}}/{{repo}}/issues/{number}/comments",
            "--input",
            "-",
            "--jq",
            ".html_url",
            body=body,
        )
        return out.strip()

    def edit(self, comment_id: int, body: str) -> str:
        out = self._gh(
            "api",
            "-X",
            "PATCH",
            f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}",
            "--input",
            "-",
            "--jq",
            ".html_url",
            body=body,
        )
        return out.strip()


@dataclass
class Poster:
    """One PR the run posts on.

    :ivar gh: GitHub (:class:`GhCli`, a fake in the tests).
    :ivar number: The PR's number.
    """

    gh: Gh
    number: int
    pr: PullRequest | None = None

    def open(self) -> PullRequest:
        """Read the PR; refuse one whose branch lives in a fork.

        :raises PostError: On a fork, or a PR ``gh`` cannot read.
        """
        found = self.gh.pull(self.number)
        if found.get("isCrossRepository"):
            raise PostError(
                f"PR #{self.number} comes from a fork: the bench posts only on a "
                f"PR whose branch is in this repository"
            )
        self.pr = PullRequest(
            number=int(found.get("number") or self.number),
            url=found.get("url") or "",
            head_sha=found.get("headRefOid") or "",
            head_branch=found.get("headRefName") or "",
            base_branch=found.get("baseRefName") or "develop",
        )
        return self.pr

    def post(self, body: str) -> tuple[str, bool]:
        """Edit the PR's bench comment, or make it when there is none.

        A marked comment that cannot be edited (another identity's, say) is
        passed over for the next; with none left, a new one is made.

        :return: The comment's URL, and whether an existing one was edited.
        """
        for comment in self.gh.marked(self.number):
            try:
                return self.gh.edit(comment["id"], body), True
            except PostError:
                continue
        return self.gh.create(self.number, body), False


# ------------------------------------------------------------ the deny list


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z0-9]+", str(text)) if w]


def token_kind(token: str, deny: dict[str, list[str]]) -> str:
    """The kind of name a refused token is, by the list that holds it (:data:`KINDS`).

    A token is a name whole or one word of it, so it is matched both ways.
    """
    wanted = token.strip().lower()
    for key, kind in KINDS:
        for entry in deny.get(key) or ():
            entry = str(entry or "").strip()
            if not entry:
                continue
            if wanted == entry.lower() or wanted in {w.lower() for w in _words(entry)}:
                return kind
    return UNKNOWN_KIND


def refused_token(text: str) -> tuple[str, str] | None:
    """The file and token of the comparison's own refusal, from its output.

    ``compare_runs.py`` prints ``card refused, nothing written: <file> holds
    '<token>', which the deny list names``. The token stays on the box: only
    its kind is posted.
    """
    import ast

    found = None
    for line in text.splitlines():
        match = re.search(
            r"card refused, nothing written: (\S+) holds (.+), which the deny list names",
            line,
        )
        if match:
            found = match
    if found is None:
        return None
    try:
        token = ast.literal_eval(found.group(2))
    except (ValueError, SyntaxError):
        token = found.group(2)
    return found.group(1), str(token)


def tail(text: str, lines: int = TAIL_LINES, chars: int = TAIL_CHARS) -> str:
    """The last lines of a text, at most ``chars`` of it."""
    kept = "\n".join(text.rstrip().splitlines()[-lines:])
    return kept[-chars:]


# -------------------------------------------------------------- the comment

#: Every sentence the comment prints of its own. Their words are the
#: comment's own vocabulary: a name made of them alone names nothing the
#: comment would leak (``run.py`` gives them to the scrub).
TEXTS = (
    (
        MARKER,
        "Bench card: PASS FAIL",
        "Bench run failed",
        "Bench card refused",
        "at, the PR's head; not the PR's head, which is",
        "reference; decoy arms; min, finished",
        "Samples read: of",
        "metric target",
        "Since develop's baseline at, made",
        "The headline readings among them:",
        "set metric was now",
        "None of the headline readings moved.",
        "No diff: develop's baseline, made, is older than this PR's merge base; "
        "the nightly run of develop's head makes a newer one.",
        "No diff: the merge base of this PR (and origin/) could not be found.",
        "No diff: there is no baseline of develop yet.",
        "Left out:",
        "The whole card",
        "Card",
        "cut to fit a comment; the whole card is on the agent box, cards/",
        "The card was refused by the scrub: the comment holds. Nothing of it was "
        "written or posted; the run log on the agent box names it.",
        "The card was written on the agent box, but the comment was not posted",
        "The comparison wrote no card (exit).",
        "No set could be read.",
        "The run stopped:",
        "The error's text was refused by the scrub: it holds. It is printed where "
        "the run ran.",
        "The deny list could not be built to scrub the error, so its text is not "
        "posted.",
        "Posted by bench run --post on the agent box; a re-run edits this comment.",
        "details summary sub",
    )
    + tuple(kind for _, kind in KINDS)
    + (UNKNOWN_KIND,)
)
WORDS = frozenset(w.lower() for text in TEXTS for w in _words(text))


def _sha(sha: str | None) -> str:
    return str(sha or "?")[:9]


def _head(title: str, summary: dict, pr: PullRequest, full: bool = True) -> list[str]:
    """The comment's title and the line saying what was benched.

    :param full: Add the reference's pin and version, the decoy arms and the
        run's time. A refusal or a failure prints the ref and its commit
        alone: the reference's version string is the server's text, and a
        refusal must hold nothing the refused comment held.
    """
    sha = summary.get("sha")
    line = f"`{summary['ref']}` at `{_sha(sha)}`"
    if sha and pr.head_sha and sha != pr.head_sha:
        line += f", not the PR's head, which is `{_sha(pr.head_sha)}`"
    elif sha:
        line += ", the PR's head"
    if not full:
        return [MARKER, f"### {title}", "", line + "."]
    reference = summary.get("reference") or {}
    if reference.get("pin"):
        line += f"; reference {reference['pin']}"
        if reference.get("version"):
            line += f" ({reference['version']})"
    if summary.get("decoys"):
        line += "; decoy arms"
    if summary.get("minutes") is not None:
        line += f"; {summary['minutes']} min"
    if summary.get("finished"):
        line += f", finished {summary['finished']}"
    return [MARKER, f"### {title}", "", line + "."]


def _sets_line(summary: dict) -> str | None:
    parts = [
        f"{name} {s['read']} of {s['samples']}"
        for name, s in (summary.get("sets") or {}).items()
    ]
    return "Samples read: " + ", ".join(parts) + "." if parts else None


def headline_keys(card: dict, cr) -> list[str]:
    """The readings the comment's table shows: the samples read, and every
    reading a target gates or a set missed. With no gate, every reading."""
    keys: list[str] = []
    for column in card["columns"].values():
        keys += [k for k in column.get("readings", {}) if k not in keys]
    thresholds = card.get("thresholds")
    gate = card.get("gate") or {}
    if not thresholds:
        return keys
    missed = {
        k
        for marks in (gate.get("marks") or {}).values()
        for k, m in marks.items()
        if m == "miss"
    }
    return [
        k
        for k in keys
        if k == "samples"
        or k in missed
        or cr.target_for(thresholds, k, None, None) is not None
    ]


def headline_table(card: dict, cr) -> list[str]:
    """The card's table, a set per column, on the headline readings alone."""
    names = list(card["columns"])
    gate = card.get("gate") or {}
    thresholds = card.get("thresholds")
    head = "| metric | " + " | ".join(names) + " |"
    rule = "|---|" + "---|" * len(names)
    if thresholds:
        head += f" target ({gate.get('stage')}) |"
        rule += "---|"
    lines = [head, rule]
    for key in headline_keys(card, cr):
        unit = cr.unit_of(key)
        cells = []
        for name in names:
            one = card["columns"][name].get("readings", {}).get(key)
            cell = cr.format_value(one.get("value"), unit) if one else "-"
            if (gate.get("marks") or {}).get(name, {}).get(key) == "miss":
                cell += " MISS"
            cells.append(cell)
        row = f"| {cr.label_of(key)} | " + " | ".join(cells) + " |"
        if thresholds:
            row += (
                f" {cr.describe_target(cr.target_for(thresholds, key, None, None))} |"
            )
        lines.append(row)
    return lines


def _demote(markdown: str) -> str:
    """A card's headings, three levels down, so they sit under the comment's."""
    return "\n".join(
        "###" + line if line.startswith("#") else line
        for line in markdown.rstrip().splitlines()
    )


def _details(summary: str, markdown: str) -> list[str]:
    return [
        "",
        "<details>",
        f"<summary>{summary}</summary>",
        "",
        markdown,
        "",
        "</details>",
    ]


def card_body(
    summary: dict,
    out: Path,
    pr: PullRequest,
    problems: list[str],
    cr,
    *,
    no_diff: str | None = None,
) -> str:
    """The comment for a run that wrote its card.

    :param summary: The run's ``run.json``.
    :param out: The card's folder: ``card.json``, ``card.md``, each set's
        ``card_<set>.md`` and, with a baseline, ``diff.json``.
    :param problems: What made the run exit 1 beside the gate.
    :param no_diff: Why the card was not diffed (an old baseline, none yet).
    """
    card = json.loads((out / "card.json").read_text(encoding="utf-8"))
    verdict = (summary.get("card") or {}).get("verdict")
    title = "Bench card" + (
        f": {verdict.upper()}" if verdict in ("pass", "fail") else ""
    )
    lines = _head(title, summary, pr)
    sets = _sets_line(summary)
    if sets:
        lines.append(sets)
    gate = (summary.get("card") or {}).get("gate")
    if gate:
        lines += ["", f"**{gate}**"]
    lines += [""] + headline_table(card, cr)

    diff_file = out / "diff.json"
    if diff_file.exists():
        diff = json.loads(diff_file.read_text(encoding="utf-8"))
        base = diff.get("baseline") or {}
        lines += [
            "",
            f"**Since develop's baseline** (`{base.get('ref')}` at "
            f"`{_sha(base.get('sha'))}`, made {base.get('at')}): "
            f"{cr.baseline_line(diff)}.",
        ]
        keys = set(headline_keys(card, cr))
        moved = [one for one in diff["moved"] if one["metric"] in keys]
        if moved:
            lines += [
                "",
                "The headline readings among them:",
                "",
                "| set | metric | was | now |",
                "|---|---|---|---|",
            ]
            for one in moved:
                unit = cr.unit_of(one["metric"])
                lines.append(
                    f"| {one['set']} | {cr.label_of(one['metric'])} | "
                    f"{cr.format_reading(one['was'], unit)} | "
                    f"{cr.format_reading(one['now'], unit)} |"
                )
        elif diff["moved"]:
            lines += ["", "None of the headline readings moved."]
    elif no_diff:
        lines += ["", f"**{no_diff}**"]

    if problems:
        lines += ["", "**Left out:**", ""] + [f"- {one}" for one in problems]

    whole = (out / "card.md").read_text(encoding="utf-8")
    per_set = [
        (name, (out / f"card_{name}.md").read_text(encoding="utf-8"))
        for name in (summary.get("card") or {}).get("sets") or []
        if (out / f"card_{name}.md").exists()
    ]
    foot = [
        "",
        f"<sub>Posted by `bench run {summary['ref']} --post {pr.number}` on the agent box; a re-run edits this comment.</sub>",
    ]
    body = lines + _details("The whole card", _demote(whole))
    for name, text in per_set:
        body += _details(f"Card {name}", _demote(text))
    if len("\n".join(body + foot)) > LIMIT:
        # The sets' cards go first; the whole card is cut only if it alone is too long.
        body = lines + _details("The whole card", _demote(whole))
        room = LIMIT - len("\n".join(lines + foot)) - 400
        if len(whole) > room:
            note = f"\n\n(cut to fit a comment; the whole card is on the agent box, `cards/{out.parent.name}/{out.name}/`)"
            body = lines + _details(
                "The whole card", _demote(whole[: max(room, 0)]) + note
            )
    return "\n".join(body + foot) + "\n"


def refused_body(summary: dict, pr: PullRequest, where: str, kind: str) -> str:
    """The comment for a card the scrub refused: the file and the kind of name."""
    lines = _head("Bench card refused", summary, pr, full=False)
    sets = _sets_line(summary)
    if sets:
        lines.append(sets)
    # The card's own files are refused before they are written; a comment
    # made of written files is refused before it is posted.
    unsaid = (
        "The card was written on the agent box, but the comment was not posted"
        if where == COMMENT
        else "Nothing of it was written or posted"
    )
    lines += [
        "",
        f"**The card was refused by the scrub:** {where} holds {kind}. "
        f"{unsaid}; the run log on the agent box names it.",
        "",
        f"<sub>Posted by `bench run {summary['ref']} --post {pr.number}` on the agent box; a re-run edits this comment.</sub>",
    ]
    return "\n".join(lines) + "\n"


def failed_body(
    summary: dict,
    pr: PullRequest,
    what: str,
    text: str | None,
    *,
    withheld: str | None = None,
) -> str:
    """The comment for a run that wrote no card: what stopped it and the tail
    of its error, or why the tail is not posted."""
    lines = _head("Bench run failed", summary, pr, full=False)
    lines += ["", f"**{what}**"]
    if text:
        fence = "````" if "```" in text else "```"
        lines += ["", fence, text, fence]
    elif withheld:
        lines += ["", withheld]
    lines += [
        "",
        f"<sub>Posted by `bench run {summary['ref']} --post {pr.number}` on the agent box; a re-run edits this comment.</sub>",
    ]
    return "\n".join(lines) + "\n"
