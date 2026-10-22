"""
Guards for `tooling/notes.py`, which checks the header every design note under
docs/dev opens with and writes the README's table of notes from those headers.

What it is for is that a note cannot land without saying how far it is built
and where its open work is tracked, and that the table cannot drift from the
notes: a `design` or `building` note tracked nowhere, a status the table does
not repeat, a note the table leaves out, each fails the check and names the
file. What it must not do is fail a note for being merely old: that is a
warning.
"""

import datetime
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLING = REPO_ROOT / "tooling"
SCRIPT = TOOLING / "notes.py"

# Guarded the same way as test_changelog.py: repo-root tooling/ is not present
# in every checkout or packaged layout.
if not SCRIPT.is_file():
    pytest.skip("repo-root tooling/notes.py not available", allow_module_level=True)


def _load():
    # notes.py imports changelog.py from beside it, as `python3 tooling/notes.py`
    # finds it when CI runs the script.
    sys.path.insert(0, str(TOOLING))
    try:
        spec = importlib.util.spec_from_file_location("notes", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TOOLING))
    return module


notes = _load()

TODAY = datetime.date(2026, 10, 22)
LINEAR = "[A plan](https://linear.app/ultra-trace/project/a-plan-0123456789ab) (Linear)"
GITHUB = "GitHub [#12](https://github.com/ultra-trace-systems/mascope/issues/12)"
README_HEAD = """# Design notes

Prose about the notes.

"""
RETIRED = """
## Retired on 2026-10-01

| Note | Where its content went |
|---|---|
| `old_note.md` | `good.md` section 2 |
"""


def note(
    status="building",
    as_of="2026-10-20",
    tracked=LINEAR,
    what="A note that is good.",
    extra="",
    banner=True,
):
    """A note's text, its header made of the given fields (None leaves one out)."""
    fields = [("Status", status), ("As of", as_of), ("Tracked in", tracked)]
    lines = [f"- **{name}:** {value}" for name, value in fields if value is not None]
    if extra:
        lines.append(extra)
    if what is not None:
        lines.append(f"- **What:** {what}")
    top = "`A BANNER`\n\n" if banner else ""
    return f"{top}# A note\n\n" + "\n".join(lines) + "\n\n## Purpose\n\nText.\n"


def make_root(tmp_path, files, rows=None):
    """A checkout's docs/dev: the notes, and a README whose table is `rows`
    (default: what index writes)."""
    directory = tmp_path / "docs" / "dev"
    directory.mkdir(parents=True)
    (directory / "developer_guide.md").write_text("# Mascope\n\nNot a note.\n")
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    table = "| Note | Status | Tracked in | What it is |\n|---|---|---|---|\n"
    (directory / "README.md").write_text(
        README_HEAD + table + "".join(f"{row}\n" for row in rows or []) + RETIRED,
        encoding="utf-8",
    )
    if rows is None:
        notes.index(tmp_path)
    return tmp_path


def errors(root, today=TODAY):
    return [
        f"{p.path}:{p.line}: {p.message}"
        for p in notes.check(root, today=today)
        if p.level == "error"
    ]


def warnings(root, today=TODAY):
    return [p for p in notes.check(root, today=today) if p.level == "warning"]


def readme_lines(root):
    return (root / "docs/dev/README.md").read_text(encoding="utf-8").split("\n")


# ============= The repository itself =============


def test_the_repository_passes_its_own_check():
    problems = notes.check(REPO_ROOT)
    assert [p for p in problems if p.level == "error"] == []


def test_index_over_the_real_notes_reproduces_the_committed_readme(tmp_path):
    shutil.copytree(REPO_ROOT / "docs" / "dev", tmp_path / "docs" / "dev")
    committed = (tmp_path / "docs/dev/README.md").read_bytes()
    assert "up to date" in notes.index(tmp_path)
    assert (tmp_path / "docs/dev/README.md").read_bytes() == committed


def test_the_readme_and_the_developer_guide_are_not_notes():
    names = [path.name for path in notes.note_paths(REPO_ROOT)]
    assert "README.md" not in names and "developer_guide.md" not in names
    assert "fair_roadmap.md" in names


# ============= check: the header =============


@pytest.mark.parametrize(
    "text",
    [
        note(),
        note(banner=False),
        note(status="design", tracked=GITHUB),
        note(status="built", tracked="closed; a leftover is MAS-1"),
        note(status="built", tracked="none; a leftover has no issue"),
        note(status="retired", tracked="closed"),
        note(extra="- **Supersedes:** `old_note.md` (retired 2026-10-01)"),
        note(extra="- **Superseded by:** [`other.md`](other.md)"),
        # A long value runs on in lines indented two spaces.
        note(
            tracked="[A plan](https://linear.app/ultra-trace/issue/MAS-1/x)\n  (more)"
        ),
    ],
)
def test_a_good_note_passes(tmp_path, text):
    root = make_root(tmp_path, {"good.md": text, "other.md": note()})
    assert notes.check(root, today=TODAY) == []


def test_a_note_without_a_header_fails(tmp_path):
    root = make_root(
        tmp_path, {"good.md": note(), "bare.md": "# A note\n\nJust text.\n"}, rows=[]
    )
    assert "docs/dev/bare.md:3: no header" in "\n".join(errors(root))


def test_a_note_without_a_title_fails(tmp_path):
    root = make_root(tmp_path, {"bare.md": "Some text.\n\nMore text.\n"}, rows=[])
    assert "docs/dev/bare.md:1: no `# Title` line" in "\n".join(errors(root))


@pytest.mark.parametrize("field", ["Status", "As of", "Tracked in", "What"])
def test_a_header_missing_a_field_fails(tmp_path, field):
    keys = {"Status": "status", "As of": "as_of", "Tracked in": "tracked"}
    kwargs = {keys.get(field, "what"): None}
    root = make_root(tmp_path, {"good.md": note(), "gap.md": note(**kwargs)}, rows=[])
    assert any(
        e.startswith("docs/dev/gap.md:") and f"the header has no `{field}`" in e
        for e in errors(root)
    )


def test_fields_out_of_order_fail(tmp_path):
    text = note().replace(
        f"- **As of:** 2026-10-20\n- **Tracked in:** {LINEAR}",
        f"- **Tracked in:** {LINEAR}\n- **As of:** 2026-10-20",
    )
    root = make_root(tmp_path, {"good.md": text})
    assert errors(root) == [
        "docs/dev/good.md:7: `As of` comes after `Tracked in`: " + notes.HOW_TO_HEAD
    ]


def test_an_unknown_field_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note(extra="- **Owner:** someone")})
    assert errors(root) == ["docs/dev/good.md:8: `Owner` is not a header field"]


def test_a_field_written_twice_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note(extra="- **Tracked in:** closed")})
    assert errors(root) == ["docs/dev/good.md:8: `Tracked in` is in the header twice"]


def test_a_bad_status_word_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note(status="shipped")})
    assert errors(root) == [
        "docs/dev/good.md:5: Status is `shipped`, not one of "
        "design, building, built, retired"
    ]


@pytest.mark.parametrize(
    # Python 3.11 and later read the basic and week forms too
    "as_of",
    ["22.10.2026", "2026-13-01", "soon", "20261022", "2026-W43-4"],
)
def test_as_of_must_be_an_iso_date(tmp_path, as_of):
    root = make_root(tmp_path, {"good.md": note(as_of=as_of)})
    assert errors(root) == [
        f"docs/dev/good.md:6: As of is `{as_of}`, not a date written YYYY-MM-DD"
    ]


def test_as_of_one_day_ahead_passes(tmp_path):
    # CI runs on UTC, and an author east of it may already be on tomorrow
    root = make_root(tmp_path, {"good.md": note(as_of="2026-10-23")})
    assert notes.check(root, today=TODAY) == []


def test_as_of_in_the_future_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note(as_of="2026-10-24")})
    assert errors(root) == [
        "docs/dev/good.md:6: As of is 2026-10-24, a day still to come"
    ]


@pytest.mark.parametrize("status", ["design", "building"])
@pytest.mark.parametrize("word", ["none", "closed"])
def test_an_open_note_tracked_nowhere_fails(tmp_path, status, word):
    root = make_root(
        tmp_path, {"good.md": note(status=status, tracked=f"{word}; later")}
    )
    [problem] = errors(root)
    assert problem.startswith("docs/dev/good.md:7: ")
    assert (
        f"a {status} note has open work, so it cannot be tracked in `{word}`" in problem
    )


def test_closed_or_none_counts_only_at_the_start_of_tracked_in(tmp_path):
    tracked = f"{LINEAR}, none of the phases done yet"
    root = make_root(tmp_path, {"good.md": note(tracked=tracked)})
    assert notes.check(root, today=TODAY) == []


@pytest.mark.parametrize(
    "tracked",
    [
        "the assignment quality plan",
        "MAS-71",
        "GitHub #2098",
        "[A PR](https://github.com/ultra-trace-systems/mascope/pull/12)",
    ],
)
def test_tracked_in_must_link_a_tracker(tmp_path, tracked):
    root = make_root(tmp_path, {"good.md": note(tracked=tracked)})
    [problem] = errors(root)
    assert problem.startswith("docs/dev/good.md:7: Tracked in links no Linear")


def test_supersedes_must_name_a_note_or_a_retired_one(tmp_path):
    root = make_root(
        tmp_path,
        {
            "good.md": note(extra="- **Supersedes:** `old_note.md` and `lost.md`"),
            "other.md": note(extra="- **Superseded by:** the new plan"),
        },
    )
    assert errors(root) == [
        "docs/dev/good.md:8: Supersedes names `lost.md`, which is neither in "
        "docs/dev nor in the README's retired notes",
        "docs/dev/other.md:8: Superseded by names no note: write its file name in "
        "backticks",
    ]


def test_supersedes_may_name_a_note_as_a_link(tmp_path):
    root = make_root(
        tmp_path,
        {
            "good.md": note(extra="- **Supersedes:** [the old plan](old_note.md)"),
            "other.md": note(extra="- **Supersedes:** [the lost plan](lost.md)"),
        },
    )
    assert errors(root) == [
        "docs/dev/other.md:8: Supersedes names `lost.md`, which is neither in "
        "docs/dev nor in the README's retired notes",
    ]


def test_an_empty_what_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note(what="")})
    assert errors(root) == [
        "docs/dev/good.md:8: What is empty: one sentence on what the note is"
    ]


# ============= check: staleness =============


@pytest.mark.parametrize("status", ["design", "building"])
def test_a_stale_open_note_is_a_warning_not_an_error(tmp_path, status):
    root = make_root(tmp_path, {"good.md": note(status=status, as_of="2026-07-23")})
    assert errors(root) == []
    [warning] = warnings(root)
    assert (warning.path, warning.line) == ("docs/dev/good.md", 6)
    assert f"a {status} note last checked 91 days ago" in warning.message


def test_ninety_days_is_not_stale_yet(tmp_path):
    root = make_root(tmp_path, {"good.md": note(as_of="2026-07-24")})
    assert notes.check(root, today=TODAY) == []


def test_a_built_note_does_not_go_stale(tmp_path):
    root = make_root(
        tmp_path,
        {"good.md": note(status="built", tracked="closed", as_of="2025-01-01")},
    )
    assert notes.check(root, today=TODAY) == []


# ============= check: the README's table =============


def test_a_table_row_that_disagrees_with_its_header_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note()})
    path = root / "docs/dev/README.md"
    path.write_text(path.read_text().replace("| building |", "| built |"))
    assert errors(root) == [
        "docs/dev/README.md:7: the table's Status for `good.md` is not its header's "
        "(line 5); run uv run python tooling/notes.py index"
    ]


@pytest.mark.parametrize("column", ["Tracked in", "What"])
def test_the_other_columns_must_agree_too(tmp_path, column):
    root = make_root(tmp_path, {"good.md": note()})
    path = root / "docs/dev/README.md"
    old = {"Tracked in": "(Linear)", "What": "A note that is good."}[column]
    path.write_text(path.read_text().replace(old, "something else"))
    [problem] = errors(root)
    assert f"the table's {column} for `good.md` is not its header's" in problem


def test_a_note_missing_from_the_table_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note()})
    (root / "docs/dev/new.md").write_text(note(), encoding="utf-8")
    assert errors(root) == [
        "docs/dev/README.md:5: `new.md` is not in the table; "
        "run uv run python tooling/notes.py index"
    ]


def test_a_row_for_a_missing_file_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note()})
    (root / "docs/dev/good.md").rename(root / "docs/dev/renamed.md")
    problems = errors(root)
    assert (
        "docs/dev/README.md:7: the table lists `good.md`, which is not a note in "
        "docs/dev; run uv run python tooling/notes.py index"
    ) in problems
    assert any("`renamed.md` is not in the table" in p for p in problems)


def test_a_note_listed_twice_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note()})
    row = readme_lines(root)[6]
    path = root / "docs/dev/README.md"
    path.write_text(path.read_text().replace(row, f"{row}\n{row}"))
    assert errors(root) == [
        "docs/dev/README.md:8: `good.md` is in the table twice; "
        "run uv run python tooling/notes.py index"
    ]


def test_a_readme_without_the_table_fails(tmp_path):
    root = make_root(tmp_path, {"good.md": note()})
    (root / "docs/dev/README.md").write_text(README_HEAD)
    assert errors(root) == [
        "docs/dev/README.md:None: no table of notes headed "
        "`| Note | Status | Tracked in | What it is |`"
    ]


# ============= index =============


def test_index_writes_one_row_per_note_in_name_order(tmp_path):
    root = make_root(
        tmp_path,
        {
            "zeta.md": note(status="design", what="The last."),
            "alpha.md": note(
                status="built",
                tracked=f"closed; see\n  {GITHUB}",
                what="Has a | pipe.",
            ),
        },
    )
    lines = readme_lines(root)
    assert lines[4:8] == [
        "| Note | Status | Tracked in | What it is |",
        "|---|---|---|---|",
        f"| [`alpha.md`](alpha.md) | built | closed; see {GITHUB} | Has a \\| pipe. |",
        f"| [`zeta.md`](zeta.md) | design | {LINEAR} | The last. |",
    ]
    # What is around the table stays, and the escaped pipe reads back.
    assert lines[:4] == README_HEAD.split("\n")[:4]
    assert "## Retired on 2026-10-01" in lines
    assert notes.check(root, today=TODAY) == []


def test_index_is_idempotent(tmp_path):
    root = make_root(tmp_path, {"good.md": note(), "other.md": note(status="design")})
    before = (root / "docs/dev/README.md").read_bytes()
    assert "up to date" in notes.index(root)
    assert (root / "docs/dev/README.md").read_bytes() == before


def test_index_replaces_a_stale_table(tmp_path):
    root = make_root(
        tmp_path,
        {"good.md": note()},
        rows=["| [`gone.md`](gone.md) | built | closed | Gone. |"],
    )
    assert "wrote the table (1 note)" in notes.index(root)
    assert notes.check(root, today=TODAY) == []


def test_index_refuses_when_a_note_lacks_a_field_it_shows(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)  # problems to stderr
    root = make_root(tmp_path, {"good.md": note()})
    (root / "docs/dev/gap.md").write_text(note(what=None), encoding="utf-8")
    before = (root / "docs/dev/README.md").read_bytes()
    with pytest.raises(
        notes.NotesError, match="^1 note lacks a field the table shows; nothing"
    ):
        notes.index(root)
    (root / "docs/dev/bare.md").write_text(
        note(status=None, what=None), encoding="utf-8"
    )
    with pytest.raises(
        notes.NotesError, match="^2 notes lack a field the table shows; nothing"
    ):
        notes.index(root)
    err = capsys.readouterr().err
    assert "docs/dev/gap.md" in err and "docs/dev/bare.md" in err
    assert (root / "docs/dev/README.md").read_bytes() == before


# ============= main =============


def test_main_exit_codes(tmp_path, capsys):
    # main checks against the real date
    today = datetime.date.today().isoformat()
    root = make_root(tmp_path, {"good.md": note(as_of=today)})
    assert notes.main(["check"], root=root) == 0
    assert capsys.readouterr().out == "notes: ok\n"
    (root / "docs/dev/bare.md").write_text("# A note\n\nText.\n")
    assert notes.main(["check"], root=root) == 1
    assert "2 problems" in capsys.readouterr().err  # no header, not in the table


def test_main_passes_with_only_warnings(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)  # problems to stderr
    root = make_root(tmp_path, {"good.md": note(as_of="2020-01-01")})
    assert notes.main(["check"], root=root) == 0
    captured = capsys.readouterr()
    assert captured.out == "notes: ok (1 warning)\n"
    assert "docs/dev/good.md:6: warning: a building note last checked" in captured.err


def test_problems_are_annotations_in_github_actions(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    root = make_root(
        tmp_path,
        {"good.md": note(as_of="2020-01-01"), "bad.md": note(status="shipped")},
    )
    assert notes.main(["check"], root=root) == 1
    out = capsys.readouterr().out.split("\n")
    assert out[0].startswith("::error file=docs/dev/bad.md,line=5::Status is `shipped`")
    assert out[1].startswith("::warning file=docs/dev/good.md,line=6::a building note")
