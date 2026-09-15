"""`mascope reference` guard rails, and the Stage A licence gate it reports.

An activating sync replaces whatever is currently serving annotations, and
`--prune` deletes the load it replaces, so both are gated behind a confirmation
the way `mascope dev db drop` is. These tests pin that the gate is reached
before any database work: declining must abort without a connection being
opened, which is what makes a mistyped command harmless.

`status` is the one place an operator can see which reference records peak
assignment is actually allowed to match. The tests at the bottom pin three
things: the default (unconfigured = everything matched, and no extra query
paid for), what a configured allowlist reports per active source, and that the
full licence-tag vocabulary is printed either way - the gate is an exact string
match, so a tag the operator never saw is a tag they drop without noticing.

The last three tests leave the CLI and read the documentation instead. The
vocabulary is enumerated by hand in four places, and an exact-match gate turns
a tag missing from any of them into a source dropped in silence. Each place is
checked on its own enumeration and never on the copy-paste example, which
already spells out five of the six tags and would otherwise answer for prose
that has gone stale; the example is then checked separately, against the
registry.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from typer.testing import CliRunner

import mascope_cli.cmd.reference.main as reference_main
import mascope_reference.sources as reference_sources
from mascope_cli.cmd.reference.main import reference_app
from mascope_cli.runtime import runtime


runner = CliRunner()


@pytest.fixture
def dump(tmp_path):
    """A readable file so the argument's exists=True check passes."""
    path = tmp_path / "custom.csv"
    path.write_text("name,formula\nGlucose,C6H12O6\n", encoding="utf-8")
    return path


@pytest.fixture
def no_engine(monkeypatch):
    """Fail loudly if the command reaches the database."""

    def _boom():
        raise AssertionError("the database must not be touched before confirmation")

    monkeypatch.setattr("mascope_cli.cmd.reference.main._sync_engine", _boom)


def test_sync_aborts_when_confirmation_is_declined(dump, no_engine):
    result = runner.invoke(
        reference_app,
        ["sync", "custom", str(dump), "--version", "v1"],
        input="n\n",
    )
    assert result.exit_code != 0


def test_sync_confirmation_names_the_deletion_when_pruning(dump, no_engine):
    result = runner.invoke(
        reference_app,
        ["sync", "custom", str(dump), "--version", "v1", "--prune"],
        input="n\n",
    )
    # The operator is told that prior loads are destroyed, not just replaced.
    assert "DELETE" in result.output
    assert result.exit_code != 0


def test_staging_needs_no_confirmation(dump, monkeypatch):
    # Staging exposes nothing, so it must not prompt. It is allowed to reach the
    # engine; failing there proves the prompt was not the thing that stopped it.
    reached = {}

    def _engine():
        reached["yes"] = True
        raise RuntimeError("stop here")

    monkeypatch.setattr("mascope_cli.cmd.reference.main._sync_engine", _engine)
    runner.invoke(
        reference_app,
        ["sync", "custom", str(dump), "--version", "v1", "--stage"],
        input="",
    )
    assert reached.get("yes") is True


def test_activate_aborts_when_confirmation_is_declined(no_engine):
    result = runner.invoke(
        reference_app,
        ["activate", "custom", "--version", "v1"],
        input="n\n",
    )
    assert result.exit_code != 0


# --- The Stage A licence gate reported by `status` / `sources` --------------


_SOURCE_DDL = """
CREATE TABLE reference_source (
    reference_source_id INTEGER PRIMARY KEY,
    name TEXT,
    version TEXT,
    license TEXT,
    record_count INTEGER,
    is_active BOOLEAN,
    ingested_at TEXT
)
"""

_COMPOUND_DDL = """
CREATE TABLE reference_compound (
    reference_compound_id INTEGER PRIMARY KEY,
    reference_source_id INTEGER,
    formula TEXT,
    license TEXT
)
"""

#: (source id, name, version, source licence, active, [record licences]).
#: 'my-list' has records under two licences, which the `custom` adapter allows -
#: the source's own licence is not the whole story, so the gate is reported from
#: the per-record ones.
_MIRROR = [
    (1, "pubchem", "2026-01", "public-domain", True, ["public-domain"] * 3),
    (2, "hmdb", "5.0", "hmdb-attribution", True, ["hmdb-attribution"] * 2),
    (3, "my-list", "2026-07", "custom", True, ["custom", "public-domain"]),
    (4, "pubchem", "2025-01", "public-domain", False, ["public-domain"]),
]


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A SQLite stand-in for the reference mirror, wired into `_sync_engine`.

    The library addresses these tables through column-name-only handles, so a
    minimal schema is enough to exercise the real queries `status` runs.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    compound_id = 0
    with engine.begin() as conn:
        conn.exec_driver_sql(_SOURCE_DDL)
        conn.exec_driver_sql(_COMPOUND_DDL)
        for source_id, name, version, lic, active, records in _MIRROR:
            conn.exec_driver_sql(
                "INSERT INTO reference_source VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    name,
                    version,
                    lic,
                    len(records),
                    active,
                    "2026-01-01 00:00:00.000000",
                ),
            )
            for record_license in records:
                compound_id += 1
                conn.exec_driver_sql(
                    "INSERT INTO reference_compound VALUES (?, ?, ?, ?)",
                    (compound_id, source_id, "C10H16O3", record_license),
                )
    monkeypatch.setattr(reference_main, "_sync_engine", lambda: engine)
    # Rich wraps to the terminal width, and a wrapped verdict is unreadable in
    # a captured run as well as in a narrow terminal - pin a wide console so
    # these assertions test the content, not the wrapping.
    monkeypatch.setattr(reference_main.console, "width", 200)
    yield engine
    engine.dispose()


@pytest.fixture
def gate(monkeypatch):
    """Set (or clear) the deployment's reference-licence allowlist."""

    def _set(allowed):
        monkeypatch.setattr(runtime.full_config.backend, "reference_licenses", allowed)

    return _set


def _flat(output: str) -> str:
    """Reduce Rich's boxes and padding so assertions read as plain sentences."""
    stripped = "".join(" " if ord(ch) in range(0x2500, 0x2580) else ch for ch in output)
    return " ".join(stripped.split())


def test_the_gate_is_unset_in_the_shipped_config():
    """The default every deployment inherits. An allowlist default would
    silently shrink what peak assignment matches, with nothing in the UI to
    say so - so 'unset' is the property, not an accident of this fixture."""
    assert runtime.full_config.backend.reference_licenses is None


def test_status_says_nothing_is_gated_by_default(mirror, monkeypatch):
    """Unconfigured must report 'everything matched' - and must not pay for
    the per-record roll-up, which is an aggregate over the compound table."""

    def _boom(*args, **kwargs):
        raise AssertionError("no gate configured: nothing to roll up")

    monkeypatch.setattr(reference_main, "_record_licenses", _boom)
    result = runner.invoke(reference_app, ["status"])
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)
    assert "not configured" in flat
    assert "matches every active source" in flat
    # Rich reads square brackets as markup, so the toml section name has to be
    # escaped - an instruction that lost the '[backend]' is not an instruction.
    assert "[backend] reference_licenses" in flat


def test_status_reports_what_the_gate_blocks(mirror, gate):
    gate(["public-domain"])
    result = runner.invoke(reference_app, ["status"])
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)

    assert "Stage A licence gate: public-domain" in flat
    # A source whose records are all allowed, one where none are, and one the
    # gate splits down the middle.
    assert "pubchem 2026-01 matched" in flat
    assert "hmdb 5.0 NOT matched hmdb-attribution" in flat
    assert "my-list 2026-07 partly matched public-domain custom" in flat


def test_status_reports_only_active_sources_in_the_gate_table(mirror, gate):
    """The inactive 2025 pubchem load is not matched by anything, so listing it
    beside a verdict would suggest the gate is what excluded it."""
    gate(["hmdb-attribution"])
    result = runner.invoke(reference_app, ["status"])
    flat = _flat(result.output)
    assert "Peak assignment (Stage A) reference matching" in flat
    # The active load is judged...
    assert "pubchem 2026-01 NOT matched" in flat
    # ...and the superseded one appears only in the sources table above, which
    # still lists every load.
    assert flat.count("pubchem 2025-01") == 1


def test_sources_flags_an_adapter_the_gate_excludes(gate, monkeypatch):
    monkeypatch.setattr(reference_main.console, "width", 200)
    gate(["public-domain"])
    result = runner.invoke(reference_app, ["sources"])
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)
    assert "hmdb (license: hmdb-attribution) (outside this deployment" in flat
    assert "pubchem (license: public-domain) (outside" not in flat


def test_sources_flags_nothing_when_no_gate_is_configured(monkeypatch):
    monkeypatch.setattr(reference_main.console, "width", 200)
    result = runner.invoke(reference_app, ["sources"])
    assert result.exit_code == 0, result.output
    assert "(outside this deployment" not in _flat(result.output)


def test_status_names_every_licence_tag_when_ungated(mirror, monkeypatch):
    """The vocabulary is what an operator needs *before* writing an allowlist,
    and only three of the six tags are loaded on this mirror - so it has to come
    from the adapter registry, not from whatever happens to be ingested."""

    def _boom(*args, **kwargs):
        raise AssertionError("no gate configured: nothing to roll up")

    monkeypatch.setattr(reference_main, "_record_licenses", _boom)
    result = runner.invoke(reference_app, ["status"])
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)
    assert "Reference licence tags" in flat
    assert "CC-BY-4.0 chebi, lipidmaps matched" in flat
    assert "CC0 coconut matched" in flat
    assert "custom custom matched" in flat
    assert "hmdb-attribution hmdb matched" in flat
    assert "open norman matched" in flat
    assert "public-domain comptox, pubchem matched" in flat


def test_status_names_the_tags_a_gate_leaves_out(mirror, gate):
    """The allowlist that reads as obviously safe - the three most permissive
    tags - also declines NORMAN and every hand-authored row carrying no licence
    of its own. Naming those is the point of the table: the gate is an exact
    string match, and a record it drops leaves no trace in a result."""
    gate(["public-domain", "CC0", "CC-BY-4.0"])
    result = runner.invoke(reference_app, ["status"])
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)
    assert "public-domain comptox, pubchem matched" in flat
    assert "open norman NOT matched" in flat
    assert "hmdb-attribution hmdb NOT matched" in flat
    assert "custom custom NOT matched" in flat


# The copy-paste allowlist example, in every place that carries one. Written as
# an assignment everywhere, so one marker finds it and so that what an operator
# pastes carries the key name too.
_EXAMPLE = 'reference_licenses = ["'
# Any written-out list of quoted tags: the example above, and the narrower
# counter-example docs/maintaining.md argues against.
_TAG_LIST = re.compile(r'\[\s*"')

_ROOT = Path(__file__).parents[3]
# Both toml copies wrap their enumeration in the same two sentences.
_TOML_BOUNDS = ("is dropped silently.", "). `mascope")

# Every hand-written place that has to name the whole vocabulary, as
# label -> (file, the markers its enumeration sits between).
#
# The bounds pick out the enumeration itself rather than the section around it,
# and that matters twice over. The copy-paste example spells out five of the
# six tags, so a region reaching it is satisfied by the example alone and
# cannot see the enumeration lose a tag - the example is checked separately,
# against the registry. And docs/maintaining.md says `custom` four more times
# in the prose around its table, enough to stand in for a deleted row.
#
# Off this test's own location, not the installed package's: the repo root
# copies only exist in a monorepo checkout. Both toml copies are here because
# they ship separately and have to say the same thing - the repo root one and
# the copy `mascope init` writes.
_PLACES: dict[str, tuple[Path, str, str]] = {
    "base.mascope.toml": (_ROOT / "base.mascope.toml", *_TOML_BOUNDS),
    "tooling/cli/.../data/base.mascope.toml": (
        Path(reference_main.__file__).parents[2] / "data" / "base.mascope.toml",
        *_TOML_BOUNDS,
    ),
    "docs/maintaining.md": (
        _ROOT / "docs" / "maintaining.md",
        "| Licence tag | Default for | Note |",
        "\n\n",
    ),
    "libraries/runtime/.../config.py field comment": (
        _ROOT / "libraries" / "runtime" / "src" / "mascope_runtime" / "config.py",
        "a result to say why.",
        "). Write the allowlist",
    ),
}

# The labels a vocabulary failure can name, spelled out so that a place dropped
# from _PLACES cannot quietly stop being checked.
_DOCUMENTED_PLACES = [
    "base.mascope.toml",
    "docs/maintaining.md",
    "libraries/runtime/.../config.py field comment",
    "tooling/cli/.../data/base.mascope.toml",
]


class _UnlistedAdapter:
    """A source registered without its tag being documented anywhere.

    Adding a source is a one-line registration in ``mascope_reference.sources``
    and nothing else in the pipeline changes - which is why the hand-written
    vocabulary can rot behind it unnoticed.
    """

    name = "fake-source"
    license = "unlisted-licence"


def _documented_regions() -> dict[str, str]:
    """Label -> the hand-written enumeration that has to name every tag."""
    regions = {}
    for label, (path, start, end) in _PLACES.items():
        text = path.read_text(encoding="utf-8")
        assert start in text, f"{label} no longer contains {start!r}"
        body = text.split(start, 1)[1]
        assert end in body, f"{label} no longer contains {end!r} after {start!r}"
        region = body.split(end, 1)[0]
        assert not _TAG_LIST.search(region), (
            f"the {label} region now reaches a written-out tag list. Narrow it "
            "back to the enumeration: a region holding the copy-paste example "
            "passes on the example's tags and stops guarding anything."
        )
        regions[label] = region
    return regions


def _tags_undocumented(tags: list[str]) -> dict[str, list[str]]:
    """Which of ``tags`` each place fails to name in its enumeration.

    :param tags: Licence tags every place is expected to enumerate.
    :return: Label -> the tags missing from it; places naming them all are
        omitted.
    """
    missing = {}
    for label, region in _documented_regions().items():
        absent = [tag for tag in tags if tag not in region]
        if absent:
            missing[label] = absent
    return missing


def test_the_documented_tag_vocabulary_covers_every_adapter():
    """Four places list the tags by hand, so they can go stale the moment an
    adapter is added - the exact silent-shrink this section exists to prevent,
    one release later. This fails the suite instead.
    """
    tags = list(reference_main._registered_licenses())
    missing = _tags_undocumented(tags)
    assert not missing, (
        f"licence tags missing from the hand-written vocabulary: {missing}. "
        "Enumerate each one where its place lists them: the 'All six' sentence "
        "in both base.mascope.toml copies, the tag table in "
        "docs/maintaining.md, and the reference_licenses field comment in "
        "config.py. Adding it to a copy-paste example does not count."
    )


def test_the_vocabulary_guard_fails_on_an_undocumented_adapter(monkeypatch):
    """The guard's own coverage, since what it reads is prose rather than code
    it runs: a source registered with a tag nothing documents has to fail it,
    in every one of the four places, or the guard is decoration.
    """
    monkeypatch.setitem(
        reference_sources._ADAPTERS, _UnlistedAdapter.name, _UnlistedAdapter()
    )
    tags = list(reference_main._registered_licenses())
    assert _UnlistedAdapter.license in tags

    missing = _tags_undocumented(tags)
    assert sorted(missing) == _DOCUMENTED_PLACES, missing
    for label, absent in missing.items():
        assert absent == [_UnlistedAdapter.license], label


def test_the_allowlist_example_names_every_tag_it_does_not_decline():
    """The example is a written-out list, so it freezes the vocabulary the same
    way an operator's own list does - and it is the list they paste. A source
    added later brings a tag the example does not name, and pasting it drops
    that source silently. It declines one tag on purpose; it has to name every
    other, and the four copies of it have to agree.
    """
    tags = set(reference_main._registered_licenses())
    examples = {}

    for label, (path, _start, _end) in _PLACES.items():
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if _EXAMPLE in line
        ]
        assert len(lines) == 1, f"expected one allowlist example in {label}: {lines}"
        listed = re.findall(r'"([^"]+)"', lines[0])
        examples[label] = listed

        unknown = sorted(set(listed) - tags)
        assert not unknown, f"{label} example names tags no adapter carries: {unknown}"
        declined = sorted(tags - set(listed))
        assert len(declined) == 1, (
            f"the {label} example leaves out {declined}; it is meant to decline "
            "exactly one tag and name every other. A source added since? Add "
            "its tag - an operator who pastes this drops that source silently."
        )

    agreed = {tuple(listed) for listed in examples.values()}
    assert len(agreed) == 1, f"the allowlist example has drifted apart: {examples}"
