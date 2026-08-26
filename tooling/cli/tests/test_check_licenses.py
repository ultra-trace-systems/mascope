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


def test_spdx_identifiers_are_matched_exactly():
    """Casing is not normalised; a differently-cased spelling is a finding."""
    assert verdict("mit") == "not on the allowlist: mit"


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


def test_a_reviewed_entry_that_is_gone_is_reported(tmp_path, capsys):
    path = lockfile(
        tmp_path,
        {
            "": {"name": "mascope"},
            "node_modules/vue": {"version": "3.5.0", "license": "MIT"},
        },
    )
    assert licences.check(path) == []
    out = capsys.readouterr().out
    for stale in licences.REVIEWED:
        assert f"note: {stale} is no longer installed" in out


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
