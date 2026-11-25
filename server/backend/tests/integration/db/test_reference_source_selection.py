"""After ``reference sync`` and the seed, a source row says where it applies.

A list's header names its regimes (``applies_to_contexts``) and says whether
it is ``always_active``; the row records both, on Postgres's JSON and Boolean
columns, and a source that declares neither - a list without them, a CSV -
keeps both null. Runs the backend's own ``reference_sync`` script against the
integration database, then ``reference seed``'s library call, which brings an
active row up to date without a reload.

Every source here has a name of its own and is deleted afterwards: the
database is shared by the session, and the sources route lists what is
active.
"""

import json
import sys

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.pool import NullPool

from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import reference_sync as script
from mascope_reference.schema import reference_compound, reference_source
from mascope_reference.seed import seed


@pytest.fixture
def sync_engine(async_engine):
    url = async_engine.url.set(drivername="postgresql+psycopg2")
    engine = create_engine(
        url.render_as_string(hide_password=False), poolclass=NullPool
    )
    yield engine
    engine.dispose()


@pytest.fixture
def names(sync_engine):
    """Source names this test may load under; their rows are deleted after."""
    made = [f"mas336-{stem}-{gen_id(6).lower()}" for stem in ("a", "b", "c", "d")]
    yield made
    with sync_engine.begin() as conn:
        ids = select(reference_source.c.reference_source_id).where(
            reference_source.c.name.in_(made)
        )
        conn.execute(
            delete(reference_compound).where(
                reference_compound.c.reference_source_id.in_(ids)
            )
        )
        conn.execute(delete(reference_source).where(reference_source.c.name.in_(made)))


def _list(directory, list_id: str, **header):
    data = {
        "schema_version": 2,
        "id": list_id,
        "label": "A test list",
        "data_version": "v1",
        "license": "CC-BY-4.0",
        "species": [{"formula": "C10H16O7"}],
        **header,
    }
    path = directory / f"{list_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _sync(monkeypatch, engine, *argv: str) -> None:
    monkeypatch.setattr(script, "_sync_engine", lambda: engine)
    monkeypatch.setattr(sys, "argv", ["reference_sync", *argv])
    script.main()


def _rows(engine, names) -> dict[str, tuple]:
    with engine.connect() as conn:
        return {
            row.name: (row.applies_to_contexts, row.always_active)
            for row in conn.execute(
                select(
                    reference_source.c.name,
                    reference_source.c.applies_to_contexts,
                    reference_source.c.always_active,
                ).where(reference_source.c.name.in_(names))
            )
        }


def test_reference_sync_writes_the_header_facts(
    monkeypatch, tmp_path, sync_engine, names
):
    regimes, always, plain, csv_name = names
    paths = {
        regimes: _list(
            tmp_path, regimes, applies_to_contexts=["monoterpene_ox", "biogenic_soa"]
        ),
        always: _list(tmp_path, always, always_active=True),
        plain: _list(tmp_path, plain),
    }
    for name, path in paths.items():
        _sync(
            monkeypatch,
            sync_engine,
            "peaklist",
            str(path),
            "--version",
            "v1",
            "--name",
            name,
        )
    csv = tmp_path / "list.csv"
    csv.write_text("name,formula\nD4,C8H24O4Si4\n", encoding="utf-8")
    _sync(
        monkeypatch,
        sync_engine,
        "custom",
        str(csv),
        "--version",
        "v1",
        "--name",
        csv_name,
    )

    assert _rows(sync_engine, names) == {
        regimes: (["monoterpene_ox", "biogenic_soa"], None),
        always: (None, True),
        plain: (None, None),
        csv_name: (None, None),
    }
    # Null in SQL, not the JSON value null: a query can ask IS NULL.
    with sync_engine.connect() as conn:
        undeclared = set(
            conn.execute(
                select(reference_source.c.name)
                .where(reference_source.c.name.in_(names))
                .where(reference_source.c.applies_to_contexts.is_(None))
                .where(reference_source.c.always_active.is_(None))
            ).scalars()
        )
    assert undeclared == {plain, csv_name}


def test_the_seed_brings_an_active_row_up_to_date(tmp_path, sync_engine, names):
    regimes, always, *_ = names
    _list(tmp_path, regimes, applies_to_contexts=["isoprene_ox"])
    _list(tmp_path, always, always_active=True)
    seed(sync_engine, directory=tmp_path)
    # What a row loaded before rows recorded them holds.
    with sync_engine.begin() as conn:
        conn.execute(
            update(reference_source)
            .where(reference_source.c.name.in_([regimes, always]))
            .values(applies_to_contexts=None, always_active=None)
        )

    outcomes = seed(sync_engine, directory=tmp_path)

    assert all(o.refreshed and not o.loaded for o in outcomes)
    assert _rows(sync_engine, [regimes, always]) == {
        regimes: (["isoprene_ox"], None),
        always: (None, True),
    }
    assert not any(o.refreshed for o in seed(sync_engine, directory=tmp_path))
