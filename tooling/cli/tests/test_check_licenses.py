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
    "declared",
    [
        "AGPL-3.0",
        "GPL-3.0-or-later",
        "SSPL-1.0",
        "EUPL-1.2",
        # npm's marker for "proprietary, do not publish". Note it is not the
        # `Unlicense` public-domain dedication, which IS allowed - the two
        # differ by one character and mean opposite things.
        "UNLICENSED",
    ],
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
    assert verdict("MIT AND GPL-3.0 AND SSPL-1.0") == (
        "not on the allowlist: GPL-3.0, SSPL-1.0"
    )


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
    assert verdict("Apache-2.0 WITH Classpath-exception-2.0") == (
        "unreviewed exception: Classpath-exception-2.0"
    )


def test_a_reviewed_exception_passes():
    """llvmlite declares this one, and it only widens Apache-2.0."""
    assert "LLVM-exception" in licences.ALLOWED_EXCEPTIONS
    assert verdict("BSD-2-Clause AND Apache-2.0 WITH LLVM-exception") is None


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
    assert reviewed in licences.NPM_REVIEWED

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
    for stale in licences.NPM_REVIEWED:
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
    for stale in licences.NPM_REVIEWED:
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


@pytest.fixture
def scoped(monkeypatch):
    """Install a synthetic SCOPED grant.

    These pin the mechanism, not the allowlist's current contents - coupling
    them to whichever licence happens to be scoped today means every future
    allowlist decision breaks unrelated tests, which is how the first version
    of this file broke when MPL-2.0 moved to ALLOWED.
    """

    def install(identifier, prefixes):
        monkeypatch.setattr(licences, "SCOPED", {identifier: prefixes})
        monkeypatch.setattr(
            licences, "_SCOPED_FOLDED", {identifier.casefold(): prefixes}
        )

    return install


def test_a_scoped_licence_is_cleared_for_the_package_it_was_argued_for(scoped):
    scoped("EPL-2.0", ("somelib",))
    assert licences.judge("somelib@1.0.0", "EPL-2.0") == []
    assert licences.judge("somelib-linux-x64@1.0.0", "EPL-2.0") == []


def test_a_scoped_licence_does_not_carry_to_another_package(scoped):
    """The point of the scope: the next arrival is read on its own terms."""
    scoped("EPL-2.0", ("somelib",))
    assert licences.judge("other-pkg@1.0.0", "EPL-2.0") == [
        ("other-pkg@1.0.0", "EPL-2.0", "cleared only for other packages: EPL-2.0")
    ]


def test_a_scoped_licence_is_reported_differently_from_an_unknown_one(scoped):
    """ "Not on the allowlist" would be untrue - it is on a list, not this one."""
    scoped("EPL-2.0", ("somelib",))
    assert "allowlist" not in licences.judge("other@1.0.0", "EPL-2.0")[0][2]
    assert verdict("AGPL-3.0") == "not on the allowlist: AGPL-3.0"


def test_a_scoped_grant_still_obeys_or_semantics(scoped):
    scoped("EPL-2.0", ("somelib",))
    assert licences.judge("other@1.0.0", "(MIT OR EPL-2.0)") == []
    assert licences.judge("somelib@1.0.0", "MIT AND EPL-2.0") == []


def test_a_scoped_package_name_is_matched_by_prefix_not_substring(scoped):
    """A prefix keeps the platform binaries in; a substring lets anything in."""
    scoped("EPL-2.0", ("somelib",))
    assert licences.judge("not-somelib@1.0.0", "EPL-2.0") != []


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


# --------------------------------------------------------------------------
# The Python side: uv.lock plus installed distribution metadata
# --------------------------------------------------------------------------


class FakeMetadata:
    """The subset of importlib.metadata's message object the checker uses."""

    def __init__(self, name, expression=None, classifiers=(), free=None):
        self._fields = {
            "Name": name,
            "License-Expression": expression,
            "License": free,
        }
        self._classifiers = list(classifiers)

    def get(self, key, default=None):
        return self._fields.get(key, default) or default

    def get_all(self, key):
        return self._classifiers if key == "Classifier" else None


def uv_lock(tmp_path, packages, first_party=()):
    """Write a uv.lock with ``packages`` as {name: version}."""
    lines = []
    for name, version in packages.items():
        lines += [
            "[[package]]",
            f'name = "{name}"',
            f'version = "{version}"',
            'source = { registry = "https://pypi.org/simple" }',
            "",
        ]
    for name in first_party:
        lines += [
            "[[package]]",
            f'name = "{name}"',
            'version = "0.0.0"',
            'source = { editable = "libraries/x" }',
            "",
        ]
    path = tmp_path / "uv.lock"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture
def environment(monkeypatch):
    """Stand in for the installed distributions the checker reads."""

    import importlib.metadata

    def install(metadatas):
        class FakeDist:
            def __init__(self, metadata):
                self.metadata = metadata

        # Patch the function on the real module rather than swapping the module
        # in sys.modules: `import importlib.metadata` resolves the attribute off
        # the package, so a swapped entry is bypassed and the real import breaks.
        monkeypatch.setattr(
            importlib.metadata,
            "distributions",
            lambda: [FakeDist(m) for m in metadatas],
        )

    return install


def test_a_declared_spdx_expression_is_judged_directly(tmp_path, environment):
    """PEP 639 metadata is already SPDX, so it goes straight to the parser."""
    environment(
        [
            FakeMetadata("anyio", expression="MIT"),
            FakeMetadata("cryptography", expression="Apache-2.0 OR BSD-3-Clause"),
        ]
    )
    lock = uv_lock(tmp_path, {"anyio": "4.0.0", "cryptography": "43.0.0"})
    assert licences.check_python(lock) == []


def test_an_or_expression_from_pypi_needs_only_one_allowed_option(
    tmp_path, environment
):
    """cryptography really does declare this; reading OR as AND would fail it."""
    environment([FakeMetadata("cryptography", expression="Apache-2.0 OR BSD-3-Clause")])
    lock = uv_lock(tmp_path, {"cryptography": "43.0.0"})
    assert licences.check_python(lock) == []


def test_a_classifier_is_mapped_to_spdx(tmp_path, environment):
    environment(
        [
            FakeMetadata(
                "aiosqlite", classifiers=["License :: OSI Approved :: MIT License"]
            )
        ]
    )
    lock = uv_lock(tmp_path, {"aiosqlite": "0.20.0"})
    assert licences.check_python(lock) == []


def test_several_classifiers_all_have_to_be_allowed(tmp_path, environment):
    """Two licence classifiers mean both apply, which is what AND means."""
    environment(
        [
            FakeMetadata(
                "odd",
                classifiers=[
                    "License :: OSI Approved :: MIT License",
                    "License :: OSI Approved :: GNU Affero General Public License v3",
                ],
            )
        ]
    )
    lock = uv_lock(tmp_path, {"odd": "1.0.0"})
    findings = licences.check_python(lock)
    assert len(findings) == 1
    assert "unmapped classifier" in findings[0][2], (
        "the AGPL classifier must not be masked by the MIT one alongside it"
    )


def test_an_unmapped_classifier_is_a_finding_naming_its_source(tmp_path, environment):
    """psycopg2's LGPL classifier is not in CLASSIFIERS, and must not pass."""
    environment(
        [
            FakeMetadata(
                "somelib",
                classifiers=[
                    "License :: OSI Approved :: GNU Library or Lesser "
                    "General Public License (LGPL)"
                ],
            )
        ]
    )
    lock = uv_lock(tmp_path, {"somelib": "1.0.0"})
    findings = licences.check_python(lock)
    assert len(findings) == 1
    assert "unmapped classifier" in findings[0][2]


def test_free_text_is_normalised_only_where_it_is_unambiguous(tmp_path, environment):
    environment(
        [
            FakeMetadata("asttokens", free="Apache 2.0"),
            FakeMetadata("partd", free="BSD"),
            FakeMetadata("vague", free="see the website"),
        ]
    )
    lock = uv_lock(tmp_path, {"asttokens": "3.0.1", "partd": "1.4.2", "vague": "1.0.0"})
    findings = licences.check_python(lock)
    assert [ident for ident, _, _ in findings] == ["vague@1.0.0"]
    assert "License free text" in findings[0][2]


def test_a_verdict_says_when_it_came_from_weaker_evidence(tmp_path, environment):
    """A classifier is coarser than SPDX; the reader should know which it was."""
    environment(
        [
            FakeMetadata(
                "gpl-thing",
                classifiers=["License :: OSI Approved :: MIT License"],
                free=None,
            ),
            FakeMetadata("clean", expression="AGPL-3.0"),
        ]
    )
    lock = uv_lock(tmp_path, {"gpl-thing": "1.0.0", "clean": "1.0.0"})
    findings = {ident: reason for ident, _, reason in licences.check_python(lock)}
    # The SPDX-declared one carries no provenance suffix; there is nothing to
    # double-check about where the string came from.
    assert findings["clean@1.0.0"] == "not on the allowlist: AGPL-3.0"


def test_a_package_declaring_nothing_is_a_finding(tmp_path, environment):
    environment([FakeMetadata("mystery")])
    lock = uv_lock(tmp_path, {"mystery": "1.0.0"})
    assert licences.check_python(lock) == [
        ("mystery@1.0.0", "<none>", "declares no licence")
    ]


def test_first_party_packages_are_not_checked(tmp_path, environment):
    """Our own code is covered by this repository's LICENSE, not by a gate."""
    environment([FakeMetadata("anyio", expression="MIT")])
    lock = uv_lock(
        tmp_path, {"anyio": "4.0.0"}, first_party=["mascope-chem", "mascope-backend"]
    )
    assert licences.check_python(lock) == []


def test_a_locked_package_that_is_not_installed_is_a_finding(tmp_path, environment):
    """
    Reading an environment is what disqualified `npm query` for the npm side.
    A package nobody could read is not a licence anybody approved.
    """
    environment([FakeMetadata("anyio", expression="MIT")])
    lock = uv_lock(tmp_path, {"anyio": "4.0.0", "mac-only-thing": "0.1.4"})
    assert licences.check_python(lock) == [
        ("mac-only-thing@0.1.4", "<not installed>", "locked but not installed here")
    ]


def test_an_unsynced_environment_is_refused_rather_than_reported(tmp_path, environment):
    """The false green the npm side was designed to avoid: nothing installed."""
    environment([])
    lock = uv_lock(tmp_path, {f"pkg{n}": "1.0.0" for n in range(10)})
    with pytest.raises(SystemExit, match="uv sync"):
        licences.check_python(lock)


def test_a_lockfile_with_no_third_party_packages_is_refused(tmp_path, environment):
    environment([])
    lock = uv_lock(tmp_path, {}, first_party=["mascope"])
    with pytest.raises(SystemExit, match="refusing to pass"):
        licences.check_python(lock)


def test_reviewed_python_packages_are_exempt_at_the_pinned_version(
    tmp_path, environment
):
    reviewed = "psycopg2-binary@2.9.12"
    assert reviewed in licences.PYTHON_REVIEWED

    def at(version):
        environment(
            [
                FakeMetadata(
                    "psycopg2-binary",
                    classifiers=[
                        "License :: OSI Approved :: GNU Library or Lesser "
                        "General Public License (LGPL)"
                    ],
                )
            ]
        )
        return licences.check_python(uv_lock(tmp_path, {"psycopg2-binary": version}))

    assert at("2.9.12") == []
    assert at("3.0.0") != []


def test_names_are_normalised_between_the_lockfile_and_the_environment(
    tmp_path, environment
):
    """uv.lock writes `clr-loader`; the installed distribution says `clr_loader`."""
    environment([FakeMetadata("clr_loader", expression="MIT")])
    assert licences.check_python(uv_lock(tmp_path, {"clr-loader": "0.3.1"})) == []
