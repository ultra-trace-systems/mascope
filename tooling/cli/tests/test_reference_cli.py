"""`mascope reference` guard rails, and the Stage A licence gate it reports.

An activating sync replaces whatever is currently serving annotations, and
`--prune` deletes the load it replaces, so both are gated behind a confirmation
the way `mascope dev db drop` is. These tests pin that the gate is reached
before any database work: declining must abort without a connection being
opened, which is what makes a mistyped command harmless.

`status` is the one place an operator can see which reference records peak
assignment is actually allowed to match. The tests at the bottom pin both
halves of that: the default (unconfigured = everything matched, and no extra
query paid for), and what a configured allowlist reports per active source.
"""

import pytest
from sqlalchemy import create_engine
from typer.testing import CliRunner

import mascope_cli.cmd.reference.main as reference_main
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
