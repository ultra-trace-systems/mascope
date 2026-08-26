"""
Guards for the dependency licence gate, `tooling/check-licenses.py`.

The gate is worth exactly what its verdicts are worth, and both ways of being
wrong cost something real. A wrong PASS ships a licence nobody cleared - the
primeicons case the gate exists to catch. A wrong FAIL is not merely noise:
the script's own instructions steer whoever hits one toward adding the
identifier to ALLOWED, so a false failure is pressure to widen the allowlist,
which is the one response the script itself calls out as defeating the check.

Neither direction is exercised by CI otherwise. Every one of the 400-odd
packages in the real lockfile declares a bare identifier, so the parser's
interesting paths - operators, precedence, grouping, free text - are reached
only from here.

`OR` is the case worth naming: SPDX `(MIT OR CC0-1.0)` means the licensee
picks, so one allowed option is enough, while `AND` imposes both. Reading OR
as AND fails a dependency that is perfectly usable under MIT.
"""

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "check-licenses.py"

# Guarded the same way as test_systemd_units.py: repo-root tooling/ is not
# present in every checkout or packaged layout.
if not SCRIPT.is_file():
    pytest.skip(
        "repo-root tooling/check-licenses.py not available", allow_module_level=True
    )


def _load():
    """Import the script by path - a hyphenated filename is not a module name."""
    spec = importlib.util.spec_from_file_location("check_licenses", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


licences = _load()

LEAF_MIT = ("licence", "MIT", None)
LEAF_ISC = ("licence", "ISC", None)


def verdict(declared: str) -> str | None:
    """The reason `declared` fails, or None if it is allowed."""
    findings = licences.judge("pkg@1.0.0", declared)
    return findings[0][2] if findings else None


def lockfile(tmp_path: Path, packages: dict, version: int = 3) -> Path:
    path = tmp_path / "package-lock.json"
    body = {"lockfileVersion": version, "name": "mascope", "packages": packages}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Single identifiers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("identifier", sorted(licences.ALLOWED))
def test_every_allowlisted_identifier_parses_and_passes(identifier):
    """An entry that does not parse would silently never match anything."""
    assert licences.parse_expression(identifier) == ("licence", identifier, None)
    assert verdict(identifier) is None


@pytest.mark.parametrize(
    "declared", ["AGPL-3.0", "GPL-3.0-or-later", "0BSD", "UNLICENSED", "CC-BY-4.0"]
)
def test_an_identifier_off_the_allowlist_is_reported_by_name(declared):
    assert verdict(declared) == f"not on the allowlist: {declared}"


@pytest.mark.parametrize("spelling", ["mit", "MIT", "Mit", "apache-2.0", "APACHE-2.0"])
def test_spdx_identifiers_are_matched_case_insensitively(spelling):
    """
    SPDX defines identifiers as case-insensitive. Matching them exactly names
    an approved licence as "not on the allowlist", which invites a second
    ALLOWED entry for a licence that is already there.
    """
    assert verdict(spelling) is None


# --------------------------------------------------------------------------
# AND / OR
# --------------------------------------------------------------------------


def test_and_requires_every_operand():
    assert verdict("(BSD-3-Clause AND Apache-2.0)") is None
    assert verdict("MIT AND AGPL-3.0") == "not on the allowlist: AGPL-3.0"
    assert verdict("MIT AND GPL-3.0 AND Zlib") == "not on the allowlist: GPL-3.0, Zlib"


@pytest.mark.parametrize(
    "declared",
    [
        "(MIT OR CC0-1.0)",  # type-fest, and the shape npm uses most
        "MIT OR GPL-3.0",
        "GPL-3.0 OR MIT",  # the allowed option is not always first
        "(AGPL-3.0 OR MIT OR GPL-3.0)",  # nor in a fixed position
        "(GPL-3.0 OR (MIT AND ISC))",  # the usable option can be compound
    ],
)
def test_or_is_satisfied_by_one_allowed_option(declared):
    """The regression: OR is a choice, so one allowed option carries it."""
    assert verdict(declared) is None


def test_or_fails_only_when_no_option_is_allowed():
    assert verdict("(GPL-3.0 OR LGPL-3.0)") == (
        "no allowed option in: (GPL-3.0 OR LGPL-3.0)"
    )


def test_a_failing_choice_names_the_subexpression_not_its_identifiers():
    """
    Listing GPL-3.0 and LGPL-3.0 as if both were required would misdescribe
    the licence; the reader has to see that it was a choice that failed.
    """
    reason = verdict("MIT AND (GPL-3.0 OR LGPL-3.0)")
    assert reason == "no allowed option in: (GPL-3.0 OR LGPL-3.0)"


def test_and_binds_tighter_than_or():
    """
    Precedence is not cosmetic here: under the other reading
    "AGPL-3.0 AND MIT OR MIT" parses as AGPL-3.0 AND (MIT OR MIT) and fails.
    """
    assert licences.parse_expression("MIT AND ISC OR OFL-1.1") == (
        "or",
        (("and", (LEAF_MIT, LEAF_ISC)), ("licence", "OFL-1.1", None)),
    )
    assert verdict("AGPL-3.0 AND MIT OR MIT") is None


def test_parentheses_group_without_changing_a_lone_identifier():
    assert licences.parse_expression("(MIT)") == LEAF_MIT
    assert licences.parse_expression("((MIT))") == LEAF_MIT
    assert verdict("(MIT AND (ISC AND MIT))") is None


# --------------------------------------------------------------------------
# WITH exceptions
# --------------------------------------------------------------------------


def test_an_exception_nobody_has_read_is_a_finding():
    assert licences.ALLOWED_EXCEPTIONS == set(), "the cases below assume none"
    assert verdict("Apache-2.0 WITH LLVM-exception") == (
        "unreviewed exception: LLVM-exception"
    )


def test_a_licence_and_its_exception_are_reported_together():
    """Reporting only the first would cost a second CI round-trip to find the rest."""
    assert verdict("GPL-3.0 WITH Classpath-exception-2.0") == (
        "not on the allowlist: GPL-3.0; unreviewed exception: Classpath-exception-2.0"
    )


# --------------------------------------------------------------------------
# Free text and malformed expressions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared",
    [
        "SEE LICENSE IN LICENSE.md",  # primeicons 8.0.0, the motivating case
        "Custom: https://badge.fury.io/js/primeicons.svg",
        "Public Domain",
        "MIT and Apache-2.0",  # SPDX operators are uppercase
        "",
        "   ",
    ],
)
def test_free_text_is_not_an_expression(declared):
    assert licences.parse_expression(declared) is None
    assert verdict(declared) == licences.NOT_SPDX


@pytest.mark.parametrize(
    "declared",
    [
        "MIT (",  # unbalanced - must not pass as a bare MIT
        "(MIT",
        ")MIT(",
        "MIT )",
        "()",
        "MIT AND",
        "AND MIT",
        "MIT OR",
        "MIT WITH",
        "WITH MIT",
        "MIT WITH x WITH y",
        "MIT MIT",
        "MIT AND OR ISC",
    ],
)
def test_a_malformed_expression_is_not_an_expression(declared):
    """
    An expression that does not parse is suspicious, not "close enough to MIT".
    Flattening the parentheses away used to let "MIT (" through as allowed.
    """
    assert licences.parse_expression(declared) is None
    assert verdict(declared) == licences.NOT_SPDX


# --------------------------------------------------------------------------
# Walking a lockfile
# --------------------------------------------------------------------------


def test_a_clean_lockfile_passes(tmp_path, capsys):
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
            "node_modules/glob": {"version": "11.0.0", "license": "ISC"},
        },
    )
    assert licences.check(path) == []
    assert "2 packages, every licence allowed" in capsys.readouterr().out


def test_the_licence_string_that_motivated_the_gate_is_caught(tmp_path):
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
            "node_modules/primeicons": {
                "version": "8.0.0",
                "license": "SEE LICENSE IN LICENSE.md",
            },
        },
    )
    assert licences.check(path) == [
        ("primeicons@8.0.0", "SEE LICENSE IN LICENSE.md", licences.NOT_SPDX)
    ]


def test_a_package_declaring_no_licence_is_a_finding(tmp_path):
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
            "node_modules/mystery": {"version": "1.0.0"},
        },
    )
    assert licences.check(path) == [("mystery@1.0.0", "<none>", "declares no licence")]


def test_the_root_project_and_workspace_links_are_skipped(tmp_path, capsys):
    """Neither declares a licence of its own, and neither is a dependency."""
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},  # the repo itself
            "node_modules/ui": {"resolved": "packages/ui", "link": True},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
        },
    )
    assert licences.check(path) == []
    assert "1 packages, every licence allowed" in capsys.readouterr().out


def test_a_scoped_and_nested_package_is_named_by_its_package_name(tmp_path):
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/@babel/core": {"version": "7.0.0", "license": "AGPL-3.0"},
            "node_modules/vite/node_modules/esbuild": {
                "version": "0.25.0",
                "license": "AGPL-3.0",
            },
        },
    )
    assert [ident for ident, _, _ in licences.check(path)] == [
        "@babel/core@7.0.0",
        "esbuild@0.25.0",
    ]


def test_a_reviewed_package_is_exempt_only_at_the_pinned_version(tmp_path):
    """The pin is the point: a bump comes back for a fresh look."""
    reviewed = "combine-errors@3.0.3"
    assert reviewed in licences.REVIEWED

    def at(version):
        path = lockfile(
            tmp_path,
            {
                "": {"name": "mascope"},
                "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
                "node_modules/combine-errors": {"version": version},
            },
        )
        return licences.check(path)

    assert at("3.0.3") == []
    assert at("3.0.4") == [("combine-errors@3.0.4", "<none>", "declares no licence")]


def _without_reviewed(tmp_path):
    return lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
        },
    )


def test_a_reviewed_entry_that_is_gone_is_reported(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert licences.check(_without_reviewed(tmp_path)) == []
    out = capsys.readouterr().out
    for stale in licences.REVIEWED:
        assert f"note: {stale} is no longer installed" in out


def test_a_stale_entry_becomes_an_annotation_under_actions(
    tmp_path, capsys, monkeypatch
):
    """
    Nobody opens the log of a job that passed, so on a green run the note has
    to be an annotation or it is not a signal at all.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert licences.check(_without_reviewed(tmp_path)) == []
    out = capsys.readouterr().out
    for stale in licences.REVIEWED:
        assert f"::warning file=tooling/check-licenses.py::{stale} is no longer" in out
    assert "note:" not in out


# --------------------------------------------------------------------------
# Guards - refusing to pass on a file it cannot read
# --------------------------------------------------------------------------


def test_a_lockfile_version_without_licence_data_is_refused(tmp_path):
    """lockfileVersion 1 records no licences, so "no findings" means nothing."""
    path = lockfile(tmp_path, {"": {}}, version=1)
    with pytest.raises(SystemExit, match="carries no licence data"):
        licences.check(path)


def test_an_empty_package_map_is_refused(tmp_path):
    path = lockfile(tmp_path, {})
    with pytest.raises(SystemExit, match="refusing to pass on nothing"):
        licences.check(path)


def test_a_lockfile_holding_only_the_root_entry_is_refused(tmp_path):
    path = lockfile(tmp_path, {"": {"name": "mascope"}})
    with pytest.raises(SystemExit, match="refusing to pass on nothing"):
        licences.check(path)


# --------------------------------------------------------------------------
# Scoped grants
# --------------------------------------------------------------------------


def test_a_scoped_licence_is_cleared_for_the_package_it_was_argued_for():
    """MPL-2.0 was reasoned about lightningcss, including its platform binaries."""
    assert "MPL-2.0" in licences.SCOPED
    assert licences.judge("lightningcss@1.30.2", "MPL-2.0") == []
    assert licences.judge("lightningcss-linux-x64-gnu@1.30.2", "MPL-2.0") == []


def test_a_scoped_licence_does_not_carry_to_another_package():
    """
    The whole point of the scope: the next MPL dependency has to be read on
    its own terms rather than inheriting an argument made about lightningcss.
    """
    assert verdict("MPL-2.0") == "cleared only for other packages: MPL-2.0"
    findings = licences.judge("some-other-pkg@1.0.0", "MPL-2.0")
    assert findings == [
        ("some-other-pkg@1.0.0", "MPL-2.0", "cleared only for other packages: MPL-2.0")
    ]


def test_a_scoped_licence_is_reported_differently_from_an_unknown_one():
    """ "Not on the allowlist" would be a lie - it is on a list, just not this one."""
    assert "allowlist" not in verdict("MPL-2.0")
    assert verdict("AGPL-3.0") == "not on the allowlist: AGPL-3.0"


def test_a_scoped_grant_still_obeys_or_semantics():
    assert licences.judge("some-other-pkg@1.0.0", "(MIT OR MPL-2.0)") == []
    assert licences.judge("lightningcss@1.0.0", "MIT AND MPL-2.0") == []


def test_a_scoped_package_name_is_matched_by_prefix_not_substring():
    """A prefix keeps the platform binaries in; a substring would let anything in."""
    assert licences.judge("not-lightningcss@1.0.0", "MPL-2.0") != []


# --------------------------------------------------------------------------
# One package, however many paths
# --------------------------------------------------------------------------


def test_a_hoisted_package_is_reported_once(tmp_path):
    """
    npm puts one package at several paths. Reporting each copy padded the
    count, so "6 packages need a decision" could mean one package six times.
    """
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
            "node_modules/ansi-regex": {"version": "5.0.1", "license": "AGPL-3.0"},
            "node_modules/a/node_modules/ansi-regex": {
                "version": "5.0.1",
                "license": "AGPL-3.0",
            },
            "node_modules/b/node_modules/ansi-regex": {
                "version": "5.0.1",
                "license": "AGPL-3.0",
            },
        },
    )
    assert licences.check(path) == [
        ("ansi-regex@5.0.1", "AGPL-3.0", "not on the allowlist: AGPL-3.0")
    ]


def test_a_hoisted_package_counts_once_when_clean(tmp_path, capsys):
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
            "node_modules/a/node_modules/vue": {"version": "3.5.0", "license": "MIT"},
        },
    )
    assert licences.check(path) == []
    assert "1 packages, every licence allowed" in capsys.readouterr().out


def test_two_versions_of_one_package_are_reported_separately(tmp_path):
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
            "node_modules/ansi-regex": {"version": "5.0.1", "license": "AGPL-3.0"},
            "node_modules/a/node_modules/ansi-regex": {
                "version": "6.2.2",
                "license": "AGPL-3.0",
            },
        },
    )
    assert [ident for ident, _, _ in licences.check(path)] == [
        "ansi-regex@5.0.1",
        "ansi-regex@6.2.2",
    ]


def test_a_non_string_licence_field_is_a_finding(tmp_path):
    """npm no longer writes this shape; an unreadable one must not read as fine."""
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
            "node_modules/old": {
                "version": "1.0.0",
                "license": {"type": "MIT", "url": "https://example.invalid"},
            },
        },
    )
    assert [(i, r) for i, _, r in licences.check(path)] == [
        ("old@1.0.0", "non-string licence field")
    ]
