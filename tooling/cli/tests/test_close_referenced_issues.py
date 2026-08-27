"""
Guards for `tooling/close-referenced-issues.py`, the parser behind the
close-on-merge workflow.

Both ways of being wrong cost something, and they are not symmetric. A missed
reference leaves an issue open, which is the status quo this exists to end -
annoying, visible, recoverable by hand. A spurious one CLOSES SOMEBODY'S LIVE
ISSUE from a merge that never claimed to finish it, and nobody is watching for
that. So the interesting tests here are the ones that pin what must NOT match.

`Refs #N` is the case that matters most. This repository's commit messages use
it deliberately for an issue a change references but does not finish - reading
it as a close would shut live work on every merge.
"""

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "close-referenced-issues.py"

# Guarded the same way as test_check_licenses.py: repo-root tooling/ is not
# present in every checkout or packaged layout.
if not SCRIPT.is_file():
    pytest.skip(
        "repo-root tooling/close-referenced-issues.py not available",
        allow_module_level=True,
    )


def _load():
    """Import the script by path - a hyphenated filename is not a module name."""
    spec = importlib.util.spec_from_file_location("close_referenced_issues", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


closer = _load()


# ============= What closes =============


@pytest.mark.parametrize(
    "keyword",
    [
        "Close",
        "Closes",
        "Closed",
        "Fix",
        "Fixes",
        "Fixed",
        "Resolve",
        "Resolves",
        "Resolved",
    ],
)
def test_every_github_keyword_closes(keyword):
    """All of GitHub's spellings, so this agrees with what authors already write."""
    assert closer.closing_references(f"{keyword} #1382") == [1382]


@pytest.mark.parametrize(
    "text",
    [
        "closes #1382",
        "CLOSES #1382",
        "Closes: #1382",
        "Closes:  #1382",
        "Closes\n#1382",
        "This one closes #1382 at last.",
    ],
)
def test_the_spelling_around_the_keyword_does_not_matter(text):
    assert closer.closing_references(text) == [1382]


def test_several_references_are_all_returned_in_order():
    body = "Closes #1793 and #1794.\n\nAlso fixes #1009."
    # `and #1794` carries no keyword of its own, so it is not a closing
    # reference - GitHub reads it the same way.
    assert closer.closing_references(body) == [1793, 1009]


def test_a_repeated_reference_is_returned_once():
    assert closer.closing_references("Closes #7. Really, closes #7.") == [7]


# ============= What must NOT close =============


def test_refs_does_not_close():
    """The one that matters: `Refs #N` means related to, not finished.

    Every commit in this batch used `Refs #N` for exactly that reason. Reading
    it as a close would shut live issues on merge.
    """
    assert closer.closing_references("Refs #1368") == []
    assert closer.closing_references("refs #1368\nsee also #1370") == []


def test_a_bare_reference_does_not_close():
    assert closer.closing_references("Related to #1382, see #1391.") == []


def test_another_repository_is_left_alone():
    """`owner/repo#N` would be someone else's issue, and the token cannot anyway."""
    assert closer.closing_references("Closes ultra-trace-systems/other#12") == []
    assert closer.closing_references("Fixes other/repo#12") == []


def test_a_word_ending_in_a_keyword_does_not_close():
    """`prefixes #12` ends in `fixes` but is not a closing keyword."""
    assert closer.closing_references("The prefixes #12 uses are wrong") == []
    assert closer.closing_references("It unfixes #12") == []


def test_inline_code_cannot_close_anything():
    """The case that caught this parser out, on the pull request adding it.

    That body documented the behaviour of a closing keyword in prose, and an
    earlier draft read its own documentation as three real declarations -
    including one against issue #1. GitHub does not linkify inside a code
    span either, so ignoring them matches it.
    """
    assert closer.closing_references("`Closes #1 and #2` links only the first") == []
    assert closer.closing_references("Their bodies say `Closes #1793 and #1794`.") == []


def test_inline_code_with_doubled_backticks_counts_too():
    assert closer.closing_references("``Closes #999``") == []


def test_a_real_reference_beside_a_quoted_one_still_closes():
    body = "Unlike `Closes #999`, this one really does it.\n\nCloses #12"
    assert closer.closing_references(body) == [12]


def test_a_fenced_block_cannot_close_anything():
    """Bodies here quote logs and diffs freely; a fence is text, not a reference."""
    body = "Log output:\n\n```\nCloses #999 was printed by the tool\n```\n\nCloses #12"
    assert closer.closing_references(body) == [12]


def test_a_tilde_fence_counts_too():
    body = "~~~\nfixes #999\n~~~\n\nfixes #12"
    assert closer.closing_references(body) == [12]


def test_an_unterminated_fence_swallows_the_rest():
    """A body that opens a fence and never closes it is all code from there on."""
    body = "Fixes #12\n\n```\nCloses #999\n"
    assert closer.closing_references(body) == [12]


def test_an_empty_body_is_not_an_error():
    assert closer.closing_references("") == []
    assert closer.closing_references("\n\n") == []


# ============= The script as the workflow runs it =============


def test_it_prints_one_number_per_line(capsys, tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Closes #1793\n\nFixes #1009\n", encoding="utf-8")

    assert closer.main(["close-referenced-issues.py", str(body)]) == 0

    assert capsys.readouterr().out == "1793\n1009\n"


def test_it_prints_nothing_when_there_is_nothing_to_close(capsys, tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Refs #1368", encoding="utf-8")

    assert closer.main(["close-referenced-issues.py", str(body)]) == 0

    assert capsys.readouterr().out == ""


def test_too_many_arguments_is_a_usage_error(capsys):
    assert closer.main(["close-referenced-issues.py", "a", "b"]) == 2
    assert "usage:" in capsys.readouterr().err
