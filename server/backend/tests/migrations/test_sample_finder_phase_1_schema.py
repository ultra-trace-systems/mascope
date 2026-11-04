"""Seeded test for `13278d0d892f`, the schema the finder and refreshable batches read.

The revision fills ``sample_file.mz_min`` and ``mz_max`` for every row there
is, from ``range``, in SQL: so what there is to pin is that it reads every
range the way the model reads one from then on (``range_bounds``), the
shapes a range should not have included, without failing on one. The rest
starts NULL - the recipe of every batch, the origin key of every item - and
the sample view, recreated, shows the key and the m/z each sample reads.
The downgrade leaves the view the previous revision made.

Rows are seeded at the previous revision through raw SQL rather than the ORM,
as the other seeded migration tests are: the ORM maps the schema at head.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mascope_backend.db.models import range_bounds


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "13278d0d892f"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_NEW_COLUMNS = {"origin_key", "mz_min", "mz_max"}

#: Every range a row may hold, by name: the ones the converter writes, the
#: whole axis the TofWerk bug wrote, and shapes no writer should have left.
_RANGES = {
    "plain": [100.0, 500.0],
    "whole numbers": [50, 750],
    "an axis": [20.0, 20.5, 21.0, 1200.0],
    "empty": [],
    "one number": [100.0],
    "an object": {"min": 0, "max": 500},
    "text": "[50, 750]",
    "text in it": ["50", 750.0],
    "a null in it": [50.0, None],
}

_IDS = {
    "workspace": "ws-finderschema1",
    "dataset": "ds-finderschema1",
    "batch": "sb-finderschema1",
    "files": {name: f"sf-finder-{index:02d}" for index, name in enumerate(_RANGES)},
    "single": "st-finder-single",
    "another shape": "st-finder-shape",
    "not numbers": "st-finder-words",
    "composite": "st-finder-compos",
    "segments": ["st-finder-seg-01", "st-finder-seg-02"],
    #: The plain file's items, by the stream each reads
    "items": {
        "no stream": "si-finder-none",
        "one stream": "si-finder-single",
        "composite": "si-finder-compos",
        "a census of another shape": "si-finder-shape",
        "a census of words": "si-finder-words",
    },
}

_NOW = {
    "local": datetime(2026, 11, 4, 12, 0),
    "utc": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
}


def _seed(conn) -> None:
    conn.execute(
        text(
            "INSERT INTO workspace (workspace_id, workspace_name) "
            "VALUES (:id, 'Finder schema')"
        ),
        {"id": _IDS["workspace"]},
    )
    conn.execute(
        text(
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name) "
            "VALUES (:id, :workspace, 'Finder schema')"
        ),
        {"id": _IDS["dataset"], "workspace": _IDS["workspace"]},
    )
    conn.execute(
        text(
            "INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name) "
            "VALUES (:id, :dataset, 'Finder schema')"
        ),
        {"id": _IDS["batch"], "dataset": _IDS["dataset"]},
    )
    for name, mz_range in _RANGES.items():
        conn.execute(
            text(
                "INSERT INTO sample_file (sample_file_id, filename, instrument, "
                '"datetime", datetime_utc, length, "range", polarity, '
                "instrument_type) VALUES (:id, :filename, 'instrument-a', "
                ":local, :utc, 60.0, CAST(:range AS json), '+-', 'orbi')"
            ),
            {
                "id": _IDS["files"][name],
                "filename": f"finder-schema-{_IDS['files'][name]}.raw",
                "range": json.dumps(mz_range),
                **_NOW,
            },
        )

    plain = _IDS["files"]["plain"]
    stream = (
        "INSERT INTO acquisition_stream (stream_id, sample_file_id, stream_key, "
        "signature_key, signature, scan_count, blocks, t_first, t_last, "
        "composite_stream_id, stitch) VALUES (:id, :file, :key, :key, "
        "CAST(:signature AS json), 4, 1, 0.0, 3.0, :composite, NULL)"
    )
    conn.execute(
        text(stream),
        {
            "id": _IDS["single"],
            "file": plain,
            "key": "FTMS - p NSI Full ms [150.0000-450.0000] R=120000",
            "signature": json.dumps(
                {"ms_order": 1, "polarity": "-", "scan_ranges": [[150.0, 450.0]]}
            ),
            "composite": None,
        },
    )
    # Two a view every listing reads must not fail on: ranges that are no
    # list, and a list of what are not ranges of numbers
    for stream_id, scan_ranges in (
        (_IDS["another shape"], "150-450"),
        (_IDS["not numbers"], [["low", 450.0], ["150", "450"], [150.0], "x"]),
    ):
        conn.execute(
            text(stream),
            {
                "id": stream_id,
                "file": plain,
                "key": f"FTMS - p NSI Full ms {stream_id}",
                "signature": json.dumps(
                    {"ms_order": 1, "polarity": "-", "scan_ranges": scan_ranges}
                ),
                "composite": None,
            },
        )
    conn.execute(
        text(
            "INSERT INTO acquisition_stream (stream_id, sample_file_id, stream_key, "
            "signature, stitch) VALUES (:id, :file, 'composite +', "
            "CAST(:signature AS json), CAST(:stitch AS json))"
        ),
        {
            "id": _IDS["composite"],
            "file": plain,
            "signature": json.dumps(
                {"ms_order": 1, "polarity": "+", "composite": True}
            ),
            "stitch": json.dumps({"rule": 1, "runs": [], "notes": []}),
        },
    )
    for stream_id, scan_range in zip(
        _IDS["segments"], ([110.0, 300.0], [290.0, 480.0])
    ):
        conn.execute(
            text(stream),
            {
                "id": stream_id,
                "file": plain,
                "key": f"FTMS + p NSI Full ms [{scan_range[0]:.4f}-{scan_range[1]:.4f}]",
                "signature": json.dumps(
                    {"ms_order": 1, "polarity": "+", "scan_ranges": [scan_range]}
                ),
                "composite": _IDS["composite"],
            },
        )

    for shown, (polarity, stream_id) in {
        "no stream": ("-", None),
        "one stream": ("-", _IDS["single"]),
        "composite": ("+", _IDS["composite"]),
        "a census of another shape": ("-", _IDS["another shape"]),
        "a census of words": ("-", _IDS["not numbers"]),
    }.items():
        conn.execute(
            text(
                "INSERT INTO sample_item (sample_item_id, sample_batch_id, "
                "sample_file_id, sample_item_name, sample_item_type, polarity, "
                "stream_id, t0, t1) VALUES (:id, :batch, :file, :name, "
                "'ACQUISITION', :polarity, :stream, 0.0, 60.0)"
            ),
            {
                "id": _IDS["items"][shown],
                "batch": _IDS["batch"],
                "file": plain,
                "name": shown,
                "polarity": polarity,
                "stream": stream_id,
            },
        )


def _view_columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sample_view'"
            )
        ).all()
    return {row[0] for row in rows}


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Every kind of range and of stream an item reads, then the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        _seed(conn)
    # Checked where the view is still the old one: the tests below would pass
    # as well against a view that had the columns all along.
    assert not _NEW_COLUMNS & _view_columns(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_every_file_carries_the_bounds_the_model_reads_off_its_range(
    upgraded: Engine,
):
    with upgraded.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT sample_file_id, "range", mz_min, mz_max FROM sample_file '
                "WHERE sample_file_id = ANY(:ids)"
            ),
            {"ids": list(_IDS["files"].values())},
        ).all()
    by_id = {row.sample_file_id: row for row in rows}

    stored = {
        name: (by_id[file_id].mz_min, by_id[file_id].mz_max)
        for name, file_id in _IDS["files"].items()
    }
    assert stored == {
        name: range_bounds(by_id[file_id].range)
        for name, file_id in _IDS["files"].items()
    }
    # And what that rule gives, so the two cannot agree on nothing
    assert stored == {
        "plain": (100.0, 500.0),
        "whole numbers": (50.0, 750.0),
        "an axis": (20.0, 1200.0),
        "empty": (None, None),
        "one number": (None, None),
        "an object": (None, None),
        "text": (None, None),
        "text in it": (None, None),
        "a null in it": (None, None),
    }


def test_nothing_has_a_recipe_or_an_origin_key_yet(upgraded: Engine):
    with upgraded.connect() as conn:
        recipes = conn.execute(
            text("SELECT count(source_filter) FROM sample_batch")
        ).scalar_one()
        keys = conn.execute(
            text("SELECT count(origin_key) FROM sample_item")
        ).scalar_one()

    assert (recipes, keys) == (0, 0)


def test_the_new_columns_are_indexed_for_what_reads_them(upgraded: Engine):
    with upgraded.connect() as conn:
        indexes = dict(
            conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE indexname = ANY(:names)"
                ),
                {
                    "names": [
                        "ix_sample_file_mz_min",
                        "ix_sample_file_mz_max",
                        "ix_sample_item_sample_batch_id_origin_key",
                    ]
                },
            ).all()
        )

    assert set(indexes) == {
        "ix_sample_file_mz_min",
        "ix_sample_file_mz_max",
        "ix_sample_item_sample_batch_id_origin_key",
    }
    assert indexes["ix_sample_item_sample_batch_id_origin_key"].endswith(
        "(sample_batch_id, origin_key) WHERE (origin_key IS NOT NULL)"
    )


def test_the_view_shows_the_m_z_each_sample_reads_and_its_key(upgraded: Engine):
    with upgraded.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT sample_item_name, mz_min, mz_max, origin_key FROM sample_view "
                "WHERE sample_batch_id = :batch"
            ),
            {"batch": _IDS["batch"]},
        ).all()

    assert {row.sample_item_name: tuple(row[1:]) for row in rows} == {
        # Its file's
        "no stream": (100.0, 500.0, None),
        # Its stream's scan range
        "one stream": (150.0, 450.0, None),
        # Its composite's segments'
        "composite": (110.0, 480.0, None),
        # Its file's, where its stream's cannot be read
        "a census of another shape": (100.0, 500.0, None),
        "a census of words": (100.0, 500.0, None),
    }


def test_the_downgrade_takes_the_columns_out_and_keeps_the_view(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it takes the database back to PRIOR_REVISION.
    before = _view_columns(upgraded)

    downgrade(seeded_alembic_config, PRIOR_REVISION)

    after = _view_columns(upgraded)
    assert after, "the downgrade must leave sample_view in place"
    assert before - after == _NEW_COLUMNS
    assert after <= before
    with upgraded.connect() as conn:
        left = {
            (row.table_name, row.column_name)
            for row in conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE (table_name, column_name) IN (('sample_file', 'mz_min'), "
                    "('sample_file', 'mz_max'), ('sample_batch', 'source_filter'), "
                    "('sample_item', 'origin_key'))"
                )
            )
        }
        still_read = conn.execute(
            text("SELECT count(*) FROM sample_view WHERE sample_batch_id = :batch"),
            {"batch": _IDS["batch"]},
        ).scalar_one()
    assert left == set()
    assert still_read == len(_IDS["items"])
