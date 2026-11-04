"""
Guards for `tooling/changelog.py`, which keeps changelog entries in one file
per pull request under changelog.d/ and compiles them into CHANGELOG.md when a
release is cut.

What it is for is that two pull requests never edit the same lines, so the
check has to fail a pull request that writes into [Unreleased] directly, and
say how to fix it. What it must not do is lose or change an entry on the way
to CHANGELOG.md: compile writes the fragments' text verbatim, and a fragment
that would not survive that - an unindented paragraph, a heading - fails the
check rather than the release.
"""

import datetime
import importlib.util
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "changelog.py"

# Guarded the same way as test_check_licenses.py: repo-root tooling/ is not
# present in every checkout or packaged layout.
if not SCRIPT.is_file():
    pytest.skip("repo-root tooling/changelog.py not available", allow_module_level=True)


def _load():
    spec = importlib.util.spec_from_file_location("changelog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


changelog = _load()

HEADER = "# Changelog\n\nNotable changes to Mascope are documented here.\n\n"
RELEASED = """## [1.2.0] - 2026.10.01

### Added

- **An old feature.** Its text
  runs on.

### Fixed

- **An old fix.**

## [1.1.0] - 2026.09.01

### Fixed

- **An older fix.**
"""
CITATION = (
    'cff-version: 1.2.0\ntitle: Mascope\nversion: 1.2.0\ndate-released: "2026-10-01"\n'
)

CLI_RELEASE = (
    '"""The release."""\n\n# The release.\nRELEASE = "v1.2.0"\n\nOTHER = "v0"\n'
)
# Each with a version that is not the CLI's ahead of the CLI's own.
CLI_PROJECT = (
    '[tool.before]\nversion = "1.0"\n\n'
    '[project]\nname = "mascope_cli"\nversion = "2026.10.1"\n'
    'classifiers = [\n    "Topic :: A",\n]\n'
)
LOCK = (
    'version = 1\n\n[[package]]\nname = "mascope-ai"\nversion = "2026.10.1"\n'
    'source = { editable = "libraries/ai" }\n\n'
    '[[package]]\nname = "mascope-cli"\nversion = "2026.10.1"\n'
    'source = { editable = "tooling/cli" }\n'
)

ADDED = "- **A new feature.** It does\n  a thing.\n\n  And a second paragraph."
FIXED = "- **A fix.** Of a bug."


def make_root(tmp_path, unreleased="", fragments=None, released=RELEASED):
    """A checkout's files compile writes: CHANGELOG.md, CITATION.cff, the CLI's
    release.py and pyproject.toml, uv.lock, and changelog.d/."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    body = f"{unreleased.strip()}\n\n" if unreleased.strip() else ""
    (tmp_path / "CHANGELOG.md").write_text(
        f"{HEADER}## [Unreleased]\n\n{body}{released}", encoding="utf-8"
    )
    (tmp_path / "CITATION.cff").write_text(CITATION, encoding="utf-8")
    cli = tmp_path / "tooling" / "cli"
    (cli / "src" / "mascope_cli").mkdir(parents=True)
    (cli / "src" / "mascope_cli" / "release.py").write_text(CLI_RELEASE)
    (cli / "pyproject.toml").write_text(CLI_PROJECT)
    (tmp_path / "uv.lock").write_text(LOCK)
    directory = tmp_path / "changelog.d"
    directory.mkdir()
    (directory / "README.md").write_text("# Changelog fragments\n\n- not an entry\n")
    for name, text in (fragments or {}).items():
        (directory / name).write_text(text + "\n", encoding="utf-8")
    return tmp_path


def messages(problems):
    return [f"{p.path}: {p.message}" for p in problems]


# ============= The repository itself =============


def test_the_repository_passes_its_own_check():
    assert changelog.check(REPO_ROOT) == []


def test_the_readme_is_not_a_fragment():
    names = [f.path.name for f in changelog.read_fragments(REPO_ROOT)[0]]
    assert "README.md" not in names


# ============= check: [Unreleased] =============


@pytest.mark.parametrize("unreleased", ["", changelog.UNRELEASED_NOTE])
def test_an_unreleased_section_with_no_entries_passes(tmp_path, unreleased):
    root = make_root(tmp_path, unreleased, {"mas-1-x.fixed.md": FIXED})
    assert changelog.check(root) == []


def test_an_entry_under_unreleased_fails_and_says_how_to_move_it(tmp_path):
    root = make_root(tmp_path, f"### Fixed\n\n{FIXED}")
    [problem] = changelog.check(root)
    assert problem.path == "CHANGELOG.md"
    assert problem.line == 5  # the [Unreleased] heading
    assert "changelog.d/" in problem.message
    assert "uv run python tooling/changelog.py extract" in problem.message


def test_prose_under_unreleased_fails_too(tmp_path):
    # Any edit there is an edit two pull requests can both make.
    root = make_root(tmp_path, "Nothing yet.")
    assert len(changelog.check(root)) == 1


def test_a_changelog_without_an_unreleased_heading_is_an_error(tmp_path):
    root = make_root(tmp_path)
    (root / "CHANGELOG.md").write_text(HEADER + RELEASED, encoding="utf-8")
    with pytest.raises(changelog.ChangelogError, match="Unreleased"):
        changelog.check(root)


# ============= check: fragments =============


@pytest.mark.parametrize(
    "text",
    [
        FIXED,
        ADDED,
        f"{FIXED}\n\n- **A second fix.**",
        # A lazy continuation: Markdown keeps it in the item.
        "- **A fix** whose text\nwraps at the margin.",
        "- **A list in an entry:**\n\n  - one\n  - two",
    ],
)
def test_a_well_formed_fragment_passes(tmp_path, text):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": text})
    assert changelog.check(root) == []


def test_a_branch_name_keeps_its_dots(tmp_path):
    # `deps/bump-vite-7.1` with its `/` written as `-`, as the README says.
    root = make_root(tmp_path, fragments={"deps-bump-vite-7.1.fixed.md": FIXED})
    assert changelog.check(root) == []
    [fragment] = changelog.read_fragments(root)[0]
    assert (fragment.name, fragment.section) == ("deps-bump-vite-7.1", "fixed")


@pytest.mark.parametrize(
    "name, text, expected",
    [
        ("mas-1-x.fixed.md", "", "empty"),
        ("mas-1-x.fixed.md", "A fix, not as a bullet.", 'starts with "- "'),
        (
            "mas-1-x.fixed.md",
            f"{FIXED}\n\n### Fixed",
            "its section comes from the file name",
        ),
        ("mas-1-x.fixed.md", f"{FIXED}\n\nA paragraph at the margin.", "indent"),
        # Straight under the entry, Markdown would read the note as part of it.
        (
            "mas-1-x.fixed.md",
            f"{FIXED}\n{changelog.UNRELEASED_NOTE}",
            "the note that stands under [Unreleased]",
        ),
        ("mas-1-x.fix.md", FIXED, "no section is called 'fix'"),
        ("mas-1-x.md", FIXED, "not a fragment"),
        ("mas-1-x.fixed.txt", FIXED, "not a fragment"),
    ],
)
def test_a_fragment_that_would_not_compile_fails(tmp_path, name, text, expected):
    root = make_root(tmp_path, fragments={name: text})
    [problem] = changelog.check(root)
    assert problem.path == f"changelog.d/{name}"
    assert expected in problem.message


def test_a_directory_in_changelog_d_fails(tmp_path):
    root = make_root(tmp_path)
    (root / "changelog.d" / "mas-1").mkdir()
    [problem] = changelog.check(root)
    assert "not a fragment" in problem.message


def test_an_entry_written_twice_fails(tmp_path):
    root = make_root(
        tmp_path,
        fragments={
            "mas-1-x.fixed.md": FIXED,
            "mas-2-y.fixed.md": f"- **Another.**\n\n{FIXED}",
        },
    )
    [problem] = changelog.check(root)
    assert problem.path == "changelog.d/mas-2-y.fixed.md"
    assert problem.line == 3
    assert "already in changelog.d/mas-1-x.fixed.md" in problem.message


def test_a_duplicate_is_reported_at_its_line_in_the_file(tmp_path):
    # Blank lines above the first entry still count: the annotation goes on
    # the line the duplicate is on.
    root = make_root(
        tmp_path,
        fragments={
            "mas-1-x.fixed.md": FIXED,
            "mas-2-y.fixed.md": f"\n\n- **Another.**\n\n{FIXED}",
        },
    )
    [problem] = changelog.check(root)
    assert problem.line == 5


def test_an_entry_already_released_fails(tmp_path):
    # Rewrapped, still the same entry: a copy, not a new one.
    root = make_root(
        tmp_path,
        fragments={"mas-1-x.added.md": "- **An old\n  feature.** Its text runs on."},
    )
    [problem] = changelog.check(root)
    assert "already in CHANGELOG.md, line 11" in problem.message


def test_release_mode_fails_while_any_fragment_is_left(tmp_path):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    assert changelog.check(root) == []
    [problem] = changelog.check(root, release=True)
    assert problem.path == "changelog.d/mas-1-x.fixed.md"
    assert "compile X.Y.Z" in problem.message
    assert changelog.check(make_root(tmp_path / "empty"), release=True) == []


# ============= compile =============

EXPECTED_RELEASE = f"""{HEADER}## [Unreleased]

{changelog.UNRELEASED_NOTE}

## [1.3.0] - 2026.10.20

### Added

{ADDED}

### Fixed

{FIXED}

- **B fix.**

### Security

- **A security fix.**

{RELEASED}"""


def test_compile_writes_a_release_section_and_deletes_the_fragments(tmp_path):
    root = make_root(
        tmp_path,
        fragments={
            "mas-3-z.security.md": "- **A security fix.**",
            "mas-1-x.fixed.md": FIXED,
            "mas-2-y.fixed.md": "- **B fix.**",
            "mas-1-x.added.md": ADDED,
        },
    )
    summary = changelog.compile_release(root, "1.3.0", datetime.date(2026, 10, 20))

    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == EXPECTED_RELEASE
    assert sorted(p.name for p in (root / "changelog.d").iterdir()) == ["README.md"]
    citation = (root / "CITATION.cff").read_text(encoding="utf-8")
    assert "version: 1.3.0\n" in citation
    assert 'date-released: "2026-10-20"\n' in citation
    assert "cff-version: 1.2.0" in citation  # only the top-level keys move
    assert "4 entries (1 Added, 2 Fixed, 1 Security) from 4 fragments" in summary
    assert changelog.check(root, release=True) == []
    # The CLI is cut with the release, in the same change.
    assert _cli_files(root) == (
        CLI_RELEASE.replace('"v1.2.0"', '"v1.3.0"'),
        CLI_PROJECT.replace('"2026.10.1"', '"2026.10.20"'),
    )
    # Only the CLI's own package moves in the lockfile.
    assert (root / "uv.lock").read_text(encoding="utf-8") == LOCK.replace(
        'cli"\nversion = "2026.10.1"', 'cli"\nversion = "2026.10.20"'
    )
    assert "CLI: RELEASE v1.3.0, version 2026.10.20" in summary


def _cli_files(root):
    cli = root / "tooling" / "cli"
    return (
        (cli / "src" / "mascope_cli" / "release.py").read_text(encoding="utf-8"),
        (cli / "pyproject.toml").read_text(encoding="utf-8"),
    )


def _cli_version(root):
    return _project_version(_cli_files(root)[1])


def _set_cli_version(root, version):
    path = root / "tooling" / "cli" / "pyproject.toml"
    path.write_text(CLI_PROJECT.replace('"2026.10.1"', f'"{version}"'))


@pytest.mark.parametrize(
    "date, version",
    [
        (datetime.date(2026, 11, 2), "2026.11.2"),
        (datetime.date(2026, 12, 31), "2026.12.31"),
        (datetime.date(2027, 1, 9), "2027.1.9"),
    ],
)
def test_compile_sets_the_cli_version_to_the_unpadded_calver(tmp_path, date, version):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    changelog.compile_release(root, "1.3.0", date)
    assert _cli_version(root) == version


@pytest.mark.parametrize(
    "current, expected",
    [("2026.10.20", "2026.10.20.1"), ("2026.10.20.1", "2026.10.20.2")],
)
def test_a_release_the_same_day_as_the_last_takes_a_newer_cli_version(
    tmp_path, current, expected
):
    # The calver of the day is on PyPI already, with the release before.
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    _set_cli_version(root, current)
    summary = changelog.compile_release(root, "1.3.0", datetime.date(2026, 10, 20))
    assert _cli_version(root) == expected
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    assert f'"mascope-cli"\nversion = "{expected}"' in lock
    assert f"version {expected} " in summary


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        # A pre-release or a dev release of the day sorts below its calver.
        ("2026.10.20rc1", "2026.10.20"),
        ("2026.10.20.dev3", "2026.10.20"),
        # A post-release of the day sorts above it: the next fourth part.
        ("2026.10.20.post1", "2026.10.20.1"),
        ("2026.10.20.1.post1", "2026.10.20.2"),
        # Of an earlier day, any suffix is below the day's calver.
        ("2026.10.19.post1", "2026.10.20"),
    ],
)
def test_a_cli_version_that_is_no_plain_release_is_replaced(
    tmp_path, current, expected
):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    _set_cli_version(root, current)
    changelog.compile_release(root, "1.3.0", datetime.date(2026, 10, 20))
    assert _cli_version(root) == expected


@pytest.mark.parametrize("current", ["2026.10.25.post1", "2026.10.21rc1"])
def test_a_suffixed_cli_version_of_a_later_day_is_refused(tmp_path, current):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    _set_cli_version(root, current)
    with pytest.raises(changelog.ChangelogError, match="later than 2026.10.20"):
        changelog.compile_release(root, "1.3.0", datetime.date(2026, 10, 20))
    assert _cli_version(root) == current


def test_a_cli_version_later_than_the_release_date_is_refused(tmp_path):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    _set_cli_version(root, "2026.10.21")
    with pytest.raises(changelog.ChangelogError, match="later than 2026.10.20"):
        changelog.compile_release(root, "1.3.0", datetime.date(2026, 10, 20))
    assert (root / "changelog.d" / "mas-1-x.fixed.md").exists()
    assert "v1.2.0" in _cli_files(root)[0]


def test_compile_adds_what_merged_late_to_the_newest_section(tmp_path):
    root = make_root(
        tmp_path,
        fragments={
            "mas-4-w.fixed.md": "- **A late fix.**",
            "mas-4-w.changed.md": "- **A change.**",
        },
    )
    summary = changelog.compile_release(root, "1.2.0")

    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = text[text.index("## [1.2.0]") : text.index("## [1.1.0]")]
    # Its date stays; a subsection it lacked goes in where SECTIONS puts it,
    # and a late entry follows those already there.
    assert section == (
        "## [1.2.0] - 2026.10.01\n\n"
        "### Added\n\n- **An old feature.** Its text\n  runs on.\n\n"
        "### Changed\n\n- **A change.**\n\n"
        "### Fixed\n\n- **An old fix.**\n\n- **A late fix.**\n\n"
    )
    assert text.count("## [1.2.0]") == 1
    assert 'date-released: "2026-10-01"' in (root / "CITATION.cff").read_text(
        encoding="utf-8"
    )
    assert "gains 2 entries" in summary
    # The section's own date gives the calver the CLI has already, and
    # RELEASE names the release already.
    assert _cli_files(root) == (CLI_RELEASE, CLI_PROJECT)


def test_a_late_compile_keeps_the_cli_version_its_first_compile_set(tmp_path):
    # The first compile of a same-day release took 2026.10.1.1: compiled
    # again, the release keeps it rather than taking a third.
    root = make_root(tmp_path, fragments={"late.fixed.md": "- **Late fix.**"})
    _set_cli_version(root, "2026.10.1.1")
    changelog.compile_release(root, "1.2.0")
    assert _cli_version(root) == "2026.10.1.1"


def test_a_late_compile_with_a_later_date_moves_the_cli_version(tmp_path):
    root = make_root(tmp_path, fragments={"late.fixed.md": "- **Late fix.**"})
    changelog.compile_release(root, "1.2.0", datetime.date(2026, 10, 5))
    assert _cli_version(root) == "2026.10.5"


def test_compile_on_a_changelog_with_no_release_yet(tmp_path):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED}, released="")
    _set_cli_version(root, "0.0.0")
    changelog.compile_release(root, "0.1.0", datetime.date(2026, 1, 2))
    assert (
        (root / "CHANGELOG.md")
        .read_text(encoding="utf-8")
        .endswith(f"## [0.1.0] - 2026.01.02\n\n### Fixed\n\n{FIXED}\n")
    )


def _section(root, version):
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index(f"## [{version}]")
    end = text.find("\n## [", start + 1)
    return text[start : None if end < 0 else end + 1]


def test_a_late_compile_keeps_every_entry_of_a_repeated_subsection(tmp_path):
    # A union merge could leave a subsection twice - v1.2.0 of the real
    # changelog has two `### Changed`. Gathered under the first heading,
    # no entry is lost and none is written twice.
    released = (
        "## [1.2.0] - 2026.10.01\n\n"
        "### Fixed\n\n- **First fix.**\n\n"
        "### Added\n\n- **A feature.**\n\n"
        "### Fixed\n\n- **Second fix.**\n\n"
        "## [1.1.0] - 2026.09.01\n"
    )
    root = make_root(
        tmp_path, fragments={"late.fixed.md": "- **Late fix.**"}, released=released
    )
    changelog.compile_release(root, "1.2.0")
    assert _section(root, "1.2.0") == (
        "## [1.2.0] - 2026.10.01\n\n"
        "### Fixed\n\n- **First fix.**\n\n- **Second fix.**\n\n- **Late fix.**\n\n"
        "### Added\n\n- **A feature.**\n\n"
    )


def test_a_late_compile_adds_to_a_subsection_whatever_its_case(tmp_path):
    released = "## [1.2.0] - 2026.10.01\n\n### fixed\n\n- **An old fix.**\n"
    root = make_root(
        tmp_path, fragments={"late.fixed.md": "- **Late fix.**"}, released=released
    )
    changelog.compile_release(root, "1.2.0")
    assert _section(root, "1.2.0") == (
        "## [1.2.0] - 2026.10.01\n\n### fixed\n\n- **An old fix.**\n\n- **Late fix.**\n"
    )


@pytest.mark.parametrize(
    "date, heading",
    [
        (None, "## [1.2.0] - 2026.10.01 (yanked)"),
        (datetime.date(2026, 10, 5), "## [1.2.0] - 2026.10.05 (yanked)"),
    ],
)
def test_a_late_compile_keeps_what_follows_the_heading_date(tmp_path, date, heading):
    released = RELEASED.replace("2026.10.01", "2026.10.01 (yanked)")
    root = make_root(
        tmp_path, fragments={"late.fixed.md": "- **Late fix.**"}, released=released
    )
    changelog.compile_release(root, "1.2.0", date)
    assert _section(root, "1.2.0").startswith(heading + "\n")


@pytest.mark.parametrize(
    "path, text, expected",
    [
        ("CITATION.cff", "cff-version: 1.2.0\nversion: 1.2.0\n", "date-released"),
        (
            "tooling/cli/src/mascope_cli/release.py",
            'OTHER = "v1.2.0"\n',
            "`RELEASE`",
        ),
        (
            "tooling/cli/pyproject.toml",
            '[project]\nname = "x"\n\n[tool.after]\nversion = "1.0"\n',
            "no `version` under \\[project\\]",
        ),
        ("uv.lock", LOCK.replace('"tooling/cli"', '"elsewhere"'), "`mascope-cli`"),
    ],
)
def test_a_file_it_cannot_set_leaves_everything_as_it_was(
    tmp_path, path, text, expected
):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    (root / path).write_text(text)
    names = (
        "CHANGELOG.md",
        "CITATION.cff",
        "tooling/cli/src/mascope_cli/release.py",
        "tooling/cli/pyproject.toml",
        "uv.lock",
        "changelog.d/mas-1-x.fixed.md",
    )
    before = {name: (root / name).read_text(encoding="utf-8") for name in names}
    with pytest.raises(changelog.ChangelogError, match=expected):
        changelog.compile_release(root, "1.3.0")
    assert before == {
        name: (root / name).read_text(encoding="utf-8") for name in before
    }


def test_the_repository_has_what_compile_sets():
    # The tests above write their own copies of these files: this pins the
    # patterns to the real ones.
    release = (REPO_ROOT / changelog.CLI_RELEASE).read_text(encoding="utf-8")
    project = (REPO_ROOT / changelog.CLI_PROJECT).read_text(encoding="utf-8")
    assert len(re.findall(r'(?m)^RELEASE = ".*"$', release)) == 1
    match = changelog._PROJECT_VERSION.search(project)
    assert match and match["version"] == _project_version(project)
    lock = (REPO_ROOT / changelog.LOCK).read_text(encoding="utf-8")
    locked = changelog._LOCKED_CLI.findall(lock)
    assert len(locked) == 1, "uv.lock has no single editable mascope-cli package"
    assert f'version = "{match["version"]}"' in changelog._LOCKED_CLI.search(lock)[0]


def _project_version(text):
    return tomllib.loads(text)["project"]["version"]


@pytest.mark.parametrize(
    "version, expected",
    [
        ("1.3.0-rc.1", "pre-release compiles nothing"),
        ("v1.3.0", "is not X.Y.Z"),
        ("1.1.5", "older than 1.2.0"),
        ("1.1.0", "older than 1.2.0"),
    ],
)
def test_compile_refuses_a_version_it_cannot_write(tmp_path, version, expected):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    with pytest.raises(changelog.ChangelogError, match=expected):
        changelog.compile_release(root, version)
    assert (root / "changelog.d" / "mas-1-x.fixed.md").exists()


def test_compile_refuses_with_nothing_to_compile(tmp_path):
    with pytest.raises(changelog.ChangelogError, match="no fragments"):
        changelog.compile_release(make_root(tmp_path), "1.3.0")


def test_compile_refuses_while_the_check_fails(tmp_path):
    root = make_root(tmp_path, f"### Fixed\n\n{FIXED}", {"mas-1-x.added.md": ADDED})
    before = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    with pytest.raises(changelog.ChangelogError, match="fix the problems"):
        changelog.compile_release(root, "1.3.0")
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == before


# ============= extract =============


def test_extract_moves_each_section_into_a_fragment_and_empties_unreleased(tmp_path):
    unreleased = f"### Added\n\n{ADDED}\n\n### Fixed\n\n{FIXED}\n\n- **B fix.**"
    root = make_root(tmp_path, unreleased)
    summary = changelog.extract(root, "mas-9-z")

    directory = root / "changelog.d"
    assert (directory / "mas-9-z.added.md").read_text(encoding="utf-8") == ADDED + "\n"
    assert (directory / "mas-9-z.fixed.md").read_text(encoding="utf-8") == (
        f"{FIXED}\n\n- **B fix.**\n"
    )
    assert "mas-9-z.fixed.md: 2 entries" in summary
    assert changelog.check(root) == []
    # Compiled back, the fragments are the section they came from.
    assert changelog.preview(root) == f"## [Unreleased]\n\n{unreleased}\n"


def test_extract_leaves_out_an_entry_it_already_has(tmp_path):
    # A rebase that kept both sides brings back an entry another fragment holds.
    root = make_root(
        tmp_path,
        f"### Fixed\n\n{FIXED}\n\n- **Mine.**",
        {"mas-1-x.fixed.md": FIXED},
    )
    summary = changelog.extract(root, "mas-2-y")
    assert (root / "changelog.d" / "mas-2-y.fixed.md").read_text() == "- **Mine.**\n"
    assert "Left out 1" in summary
    assert changelog.check(root) == []


def test_extract_appends_to_a_fragment_of_the_same_name(tmp_path):
    root = make_root(
        tmp_path, f"### Fixed\n\n{FIXED}", {"mas-2-y.fixed.md": "- **First.**"}
    )
    changelog.extract(root, "mas-2-y")
    assert (
        root / "changelog.d" / "mas-2-y.fixed.md"
    ).read_text() == f"- **First.**\n\n{FIXED}\n"


@pytest.mark.parametrize(
    "unreleased, expected",
    [
        (FIXED, "under no `###` heading"),
        (f"### Bugs\n\n{FIXED}", "no `### Bugs` section"),
        (f"A word first.\n\n### Fixed\n\n{FIXED}", "not an entry"),
        ("", "no entries"),
    ],
)
def test_extract_refuses_what_it_would_lose_or_misfile(tmp_path, unreleased, expected):
    root = make_root(tmp_path, unreleased)
    before = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    with pytest.raises(changelog.ChangelogError, match=expected):
        changelog.extract(root, "mas-1-x")
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == before


def test_extract_does_not_take_the_note_into_an_entry_it_follows(tmp_path):
    # A conflict resolved with the note straight under the branch's entry:
    # Markdown would read the note as that entry's lazy continuation.
    root = make_root(tmp_path, f"### Fixed\n\n- **Mine.**\n{changelog.UNRELEASED_NOTE}")
    changelog.extract(root, "mas-2-y")
    assert (root / "changelog.d" / "mas-2-y.fixed.md").read_text() == "- **Mine.**\n"
    assert changelog.check(root) == []


def test_extract_keeps_the_note_and_refuses_a_bad_name(tmp_path):
    root = make_root(tmp_path, f"{changelog.UNRELEASED_NOTE}\n\n### Fixed\n\n{FIXED}")
    changelog.extract(root, "mas-1-x")
    assert changelog.check(root) == []
    with pytest.raises(changelog.ChangelogError, match="cannot name"):
        changelog.extract(root, "mas/1")


# ============= In a git checkout =============


def _git(root, *args, check=True):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            # Rebases run as a script would: no editor opens.
            "-c",
            "core.editor=true",
            "-c",
            "sequence.editor=:",
            *args,
        ],
        cwd=root,
        check=check,
        capture_output=True,
        encoding="utf-8",
    ).stdout


@pytest.fixture
def repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    root = make_root(tmp_path)
    _git(root, "init", "-q", "-b", "develop")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    return root


def test_two_pull_requests_fragments_merge_cleanly_and_compile_in_merge_order(repo):
    # Two branches off the same develop, each adding its entry, merged the
    # way GitHub's rebase-merge does: the one written second merges first,
    # and the other is rebased onto it. Neither touches a line the other
    # does, so the rebase has nothing to conflict on - and no merge driver
    # is involved, so GitHub sees the same.
    for branch, text in (
        ("mas-1-older", FIXED),
        ("mas-2-newer", "- **Merged first.**"),
    ):
        _git(repo, "checkout", "-q", "-b", branch, "develop")
        (repo / "changelog.d" / f"{branch}.fixed.md").write_text(text + "\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", branch)
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "merge", "-q", "--ff-only", "mas-2-newer")
    _git(repo, "rebase", "-q", "develop", "mas-1-older")
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "merge", "-q", "--ff-only", "mas-1-older")

    assert changelog.check(repo) == []
    changelog.compile_release(repo, "1.3.0", datetime.date(2026, 10, 20))
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    # The order they merged in - not the order of their names or of when
    # they were written.
    assert text.index("Merged first") < text.index("A fix.")


def test_compile_refuses_a_version_already_tagged(repo):
    # Adding to the newest section is for what merged before its tag; after
    # it, the notes are published and the entry belongs in the next release.
    (repo / "changelog.d" / "late.fixed.md").write_text("- **Late fix.**\n")
    _git(repo, "tag", "v1.2.0")
    with pytest.raises(changelog.ChangelogError, match="v1.2.0 is tagged already"):
        changelog.compile_release(repo, "1.2.0")
    assert (repo / "changelog.d" / "late.fixed.md").exists()
    changelog.compile_release(repo, "1.3.0", datetime.date(2026, 10, 20))
    assert "## [1.3.0] - 2026.10.20" in _section(repo, "1.3.0")


def test_extract_names_the_fragments_after_the_branch(repo):
    _git(repo, "checkout", "-q", "-b", "claude/mas-7_fix")
    assert changelog.branch_name(repo) == "claude-mas-7_fix"
    _git(repo, "checkout", "-q", "-b", "deps/bump-vite-7.1")
    assert changelog.branch_name(repo) == "deps-bump-vite-7.1"
    _git(repo, "checkout", "-q", "--detach")
    with pytest.raises(changelog.ChangelogError, match="--name"):
        changelog.branch_name(repo)


def _commit_entry_under_unreleased(repo, branch, entry):
    """A branch, cut from develop, that writes its entry the old way."""
    _git(repo, "checkout", "-q", "-b", branch, "develop")
    path = repo / "CHANGELOG.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "## [Unreleased]\n\n", f"## [Unreleased]\n\n### Fixed\n\n{entry}\n\n"
        ),
        encoding="utf-8",
    )
    _git(repo, "commit", "-q", "-am", f"fix: {branch}")


def _note_on_develop(repo):
    """develop as it is now: the note under [Unreleased]."""
    _git(repo, "checkout", "-q", "develop")
    path = repo / "CHANGELOG.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "## [Unreleased]\n\n", f"## [Unreleased]\n\n{changelog.UNRELEASED_NOTE}\n\n"
        ),
        encoding="utf-8",
    )
    _git(repo, "commit", "-q", "-am", "docs: the note")


def test_extract_mid_rebase_files_under_the_branch_being_rebased(repo):
    # A branch from before the fragments meets a develop whose [Unreleased]
    # now holds the note: the rebase stops on CHANGELOG.md, HEAD detached.
    _commit_entry_under_unreleased(repo, "mas-5/old", "- **Mine.**")
    _note_on_develop(repo)
    path = repo / "CHANGELOG.md"
    _git(repo, "rebase", "develop", "mas-5/old", check=False)
    assert "CHANGELOG.md" in _git(repo, "diff", "--name-only", "--diff-filter=U")

    assert changelog.branch_name(repo) == "mas-5-old"

    # Kept both sides, the section holds the note and the entry; extract
    # files the entry under the branch's name, and the commit that added it
    # carries the fragment instead.
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(
            line
            for line in text.split("\n")
            if not line.startswith(("<<<<<<<", "=======", ">>>>>>>", "|||||||"))
        ),
        encoding="utf-8",
    )
    changelog.extract(repo, changelog.branch_name(repo))
    _git(repo, "add", "CHANGELOG.md", "changelog.d")
    _git(repo, "rebase", "--continue")
    assert _git(repo, "branch", "--show-current").strip() == "mas-5/old"
    assert _git(repo, "diff", "develop", "--", "CHANGELOG.md") == ""
    assert (repo / "changelog.d" / "mas-5-old.fixed.md").read_text() == "- **Mine.**\n"
    assert changelog.check(repo) == []


def test_the_documented_recipe_folds_the_fragment_into_the_entry_commit(repo):
    # changelog.d/README.md, "A branch with an entry in CHANGELOG.md": after
    # the rebase, extract, then fold the result into the commit that added
    # the entry, so that commit passes on its own. This branch was cut from
    # the current develop and edited CHANGELOG.md by mistake; one rebased
    # without a conflict is in the same state.
    _note_on_develop(repo)
    _commit_entry_under_unreleased(repo, "mas-6-old", "- **Mine.**")
    entry = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "later.txt").write_text("a later commit on the branch\n")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-q", "-m", "feat: a later commit")
    assert changelog.check(repo) != []

    changelog.extract(repo, changelog.branch_name(repo))
    _git(repo, "add", "CHANGELOG.md", "changelog.d")
    _git(repo, "commit", "-q", f"--fixup={entry}")
    _git(repo, "rebase", "-q", "-i", "--autosquash", "develop")

    assert _git(repo, "log", "--format=%s", "develop..HEAD").splitlines() == [
        "feat: a later commit",
        "fix: mas-6-old",
    ]
    for commit in ("HEAD~1", "HEAD"):
        assert _git(repo, "diff", "develop", commit, "--", "CHANGELOG.md") == ""
        assert (
            _git(repo, "show", f"{commit}:changelog.d/mas-6-old.fixed.md")
            == "- **Mine.**\n"
        )
    assert changelog.check(repo) == []


# ============= main =============


def test_main_exit_codes(tmp_path, capsys):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    assert changelog.main(["check"], root=root) == 0
    assert changelog.main(["check", "--release"], root=root) == 1
    assert "1 problem" in capsys.readouterr().err
    assert changelog.main(["compile", "1.3.0-rc.1"], root=root) == 1
    assert changelog.main(["compile", "1.3.0", "--date", "2026-10-20"], root=root) == 0
    assert "## [1.3.0] - 2026.10.20" in (root / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )


def test_main_preview_prints_the_unreleased_section(tmp_path, capsysbinary):
    root = make_root(tmp_path, fragments={"mas-1-x.fixed.md": FIXED})
    assert changelog.main(["preview"], root=root) == 0
    assert (
        capsysbinary.readouterr().out
        == f"## [Unreleased]\n\n### Fixed\n\n{FIXED}\n".encode()
    )


def test_problems_are_annotations_in_github_actions(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    root = make_root(tmp_path, f"### Fixed\n\n{FIXED}")
    assert changelog.main(["check"], root=root) == 1
    assert capsys.readouterr().out.startswith("::error file=CHANGELOG.md,line=5::")


def test_an_annotation_escapes_what_github_would_misread(tmp_path, capsys, monkeypatch):
    # A comma in a property ends it, so a misnamed file with one would lose
    # its annotation along with the message.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    root = make_root(tmp_path, fragments={"notes, misc.md": FIXED})
    assert changelog.main(["check"], root=root) == 1
    out = capsys.readouterr().out
    assert out.startswith("::error file=changelog.d/notes%2C misc.md::not a fragment")
