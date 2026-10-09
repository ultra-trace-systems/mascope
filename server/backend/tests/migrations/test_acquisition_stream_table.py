"""Seeded test for `5a0e9de94ed9`, which adds the stream table and the
stream a sample item is cut from.

The table starts empty and the column starts NULL, so what there is to pin
is what that NULL means and what the two references allow.

An item made before the migration must read NULL and keep reading NULL: it
spans every MS1 scan of its polarity, which is what it was made as, and
section 9.1 of the design note forbids deciding afterwards that it meant
something narrower.

A stream belongs to its file and goes with it. An item does not go with its
stream, and a stream cannot be taken from under an item either: the reference
has no ON DELETE action, because the two obvious ones are both wrong. SET
NULL would turn the item silently into one over the whole polarity, and
CASCADE would delete a sample because a description of its file was rewritten.

And a file holds a key once, while two files may hold the same key - a key is
a stream's name within its file and nothing more.

A composite is a row like any stream: its segments point at it, it carries
the map they were stitched by and no census, and it is not taken from under
its segments any more than a stream from under its item. With its file it
goes in one statement, segments and all. Both references carry the file, so
neither an item nor a segment can point into another file.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "5a0e9de94ed9"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_WORKSPACE_ID = "ws-streams00001"
_DATASET_ID = "ds-streams00001"
_BATCH_ID = "sb-streams00001"
_FILE_ID = "sf-streams00001"
_OTHER_FILE_ID = "sf-streams00002"
#: Made before the column existed.
_OLD_ITEM = "si-streams-old1"
#: Made after, for one stream of the file.
_NEW_ITEM = "si-streams-new1"
_SETTLING = "st-streams00001"
_MEASURING = "st-streams00002"
#: A composite and the two segments stitched into it.
_COMPOSITE = "st-streams-comp"
_LOW = "st-streams-low1"
_HIGH = "st-streams-hig1"

_KEY = "FTMS - p NSI Full ms [40.0000-600.0000] R=120000"

#: A composite's own polarity's part of the map, its owners named by key.
_STITCH = (
    '{"rule": 1, "runs": [[40, 122, "%s"], [122, 600, "%s"]], '
    '"source": "default", "notes": []}' % (f"{_KEY} event=3", f"{_KEY} event=4")
)
_PARAMS = (
    '{"source": "opentfraw", "scans_sampled": 4, '
    '"constant": {"Micro Scan Count:": 10, "AGC Target:": 1000000}, '
    '"varying": ["Ion Injection Time (ms):"]}'
)

_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity,
                             instrument_type, method_file)
    VALUES (:file, :filename, 'instrument-A', :local, :utc,
            60.0, CAST('[40.0, 600.0]' AS json), '-', 'orbi', 'two-ranges.meth')
"""

_SEED_SQL = [
    """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:workspace, 'Stream table')
    """,
    """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:dataset, :workspace, 'Stream table')
    """,
    """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:batch, :dataset, 'Stream table')
    """,
    _FILE_SQL,
    """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type, polarity)
    VALUES (:old_item, :batch, :file, 'before the column', 'ACQUISITION', '-')
    """,
]

_IDS = {
    "workspace": _WORKSPACE_ID,
    "dataset": _DATASET_ID,
    "batch": _BATCH_ID,
    "file": _FILE_ID,
    "filename": "stream-table-a.raw",
    "old_item": _OLD_ITEM,
    "local": datetime(2026, 10, 1, 12, 0),
    "utc": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
}


def _insert_stream(
    conn,
    stream_id: str,
    key: str,
    file_id: str = _FILE_ID,
    event: int | None = 1,
    composite: str | None = None,
    stitch: str | None = None,
    census: bool | None = None,
) -> None:
    """A stream with its census, or, given a map, a composite without one;
    ``census`` forces either shape, for the tests of the check."""
    if census is None:
        census = stitch is None
    conn.execute(
        text(
            "INSERT INTO acquisition_stream ("
            "  stream_id, sample_file_id, stream_key, signature_key,"
            "  scan_segment, scan_event, signature, acquisition_params,"
            "  scan_count, blocks, t_first, t_last, composite_stream_id, stitch"
            ") VALUES (:id, :file, :key, :signature_key, :segment, :event,"
            "          CAST(:signature AS json), CAST(:params AS json),"
            "          :scan_count, :blocks, :t_first, :t_last,"
            "          :composite, CAST(:stitch AS json))"
        ),
        {
            "id": stream_id,
            "file": file_id,
            "key": key,
            "signature_key": _KEY if census else None,
            "segment": None if event is None else 1,
            "event": event,
            "signature": (
                '{"ms_order": 1, "polarity": "-"}'
                if stitch is None
                else '{"ms_order": 1, "polarity": "-", "composite": true}'
            ),
            "params": _PARAMS if census else None,
            "scan_count": 4 if census else None,
            "blocks": 1 if census else None,
            "t_first": 0.0 if census else None,
            "t_last": 3.0 if census else None,
            "composite": composite,
            "stitch": stitch,
        },
    )


def _count(engine: Engine, table: str, where: str = "TRUE", **params) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE {where}"), params
        ).scalar_one()


def _stream_of(engine: Engine, item_id: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT stream_id FROM sample_item WHERE sample_item_id = :id"),
            {"id": item_id},
        ).scalar_one()


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A file with a pooled item, as every file has today, then the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        for statement in _SEED_SQL:
            conn.execute(text(statement), _IDS)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_the_table_arrives_empty(upgraded: Engine):
    assert _count(upgraded, "acquisition_stream") == 0


def test_an_item_made_before_the_column_points_at_no_stream(upgraded: Engine):
    assert _stream_of(upgraded, _OLD_ITEM) is None


def test_an_item_can_point_at_its_stream(upgraded: Engine):
    with upgraded.begin() as conn:
        _insert_stream(conn, _SETTLING, f"{_KEY} event=1", event=1)
        _insert_stream(conn, _MEASURING, f"{_KEY} event=2", event=2)
        conn.execute(
            text(
                "INSERT INTO sample_item ("
                "  sample_item_id, sample_batch_id, sample_file_id,"
                "  sample_item_name, sample_item_type, polarity, stream_id"
                ") VALUES (:id, :batch, :file, 'the measuring experiment',"
                "          'ACQUISITION', '-', :stream)"
            ),
            _IDS | {"id": _NEW_ITEM, "stream": _MEASURING},
        )

    assert _stream_of(upgraded, _NEW_ITEM) == _MEASURING
    # And the item made under the polarity rule still says what it said
    assert _stream_of(upgraded, _OLD_ITEM) is None


def test_a_file_holds_a_key_once(upgraded: Engine):
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            _insert_stream(conn, "st-streams-dup1", f"{_KEY} event=1")


def test_two_files_may_hold_the_same_key(upgraded: Engine):
    """A key is a stream's name within its file, so another file's stream of
    the same name is another stream."""
    with upgraded.begin() as conn:
        conn.execute(
            text(_FILE_SQL),
            _IDS | {"file": _OTHER_FILE_ID, "filename": "stream-table-b.raw"},
        )
        _insert_stream(
            conn, "st-streams-oth1", f"{_KEY} event=1", file_id=_OTHER_FILE_ID
        )

    assert (
        _count(
            upgraded, "acquisition_stream", "stream_key = :key", key=f"{_KEY} event=1"
        )
        == 2
    )


def test_a_file_that_records_no_experiment_has_no_segment_or_event(upgraded: Engine):
    with upgraded.begin() as conn:
        _insert_stream(
            conn, "st-streams-none", _KEY, file_id=_OTHER_FILE_ID, event=None
        )

    with upgraded.connect() as conn:
        row = conn.execute(
            text(
                "SELECT scan_segment, scan_event FROM acquisition_stream "
                "WHERE stream_id = 'st-streams-none'"
            )
        ).one()
    assert (row.scan_segment, row.scan_event) == (None, None)


def test_a_key_as_wide_as_a_signature_class_fits(upgraded: Engine):
    """Keys are built from scan filters and have no fixed length; the column
    is as wide as the one that holds a whole polarity's signatures joined."""
    wide = "FTMS - p NSI Full ms " + "[40.0000-600.0000] " * 25
    assert 480 < len(wide) <= 512
    with upgraded.begin() as conn:
        _insert_stream(conn, "st-streams-wide", wide, file_id=_OTHER_FILE_ID)

    assert _count(upgraded, "acquisition_stream", "stream_key = :key", key=wide) == 1


def test_a_stream_is_not_taken_from_under_its_item(upgraded: Engine):
    """Refused rather than resolved either way: the item neither loses its
    stream, which would make it an item over the whole polarity, nor goes
    with it."""
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text("DELETE FROM acquisition_stream WHERE stream_id = :stream"),
                {"stream": _MEASURING},
            )

    assert _stream_of(upgraded, _NEW_ITEM) == _MEASURING


def test_a_stream_no_item_points_at_can_go(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text("DELETE FROM acquisition_stream WHERE stream_id = :stream"),
            {"stream": _SETTLING},
        )

    assert _count(upgraded, "acquisition_stream", "stream_id = :id", id=_SETTLING) == 0


def test_a_stream_is_no_segment_and_carries_no_map_unless_written_so(
    upgraded: Engine,
):
    with upgraded.connect() as conn:
        row = conn.execute(
            text(
                "SELECT composite_stream_id, stitch, acquisition_params "
                "FROM acquisition_stream WHERE stream_id = :id"
            ),
            {"id": _MEASURING},
        ).one()
    assert (row.composite_stream_id, row.stitch) == (None, None)
    # Its parameters are stored as the census samples them
    assert row.acquisition_params["constant"]["Micro Scan Count:"] == 10
    assert row.acquisition_params["varying"] == ["Ion Injection Time (ms):"]


@pytest.mark.parametrize(
    ("stitch", "census"),
    [(_STITCH, True), (None, False)],
    ids=["a-composite-with-a-census", "a-stream-without-one"],
)
def test_a_row_is_a_stream_with_a_census_or_a_composite_with_a_map(
    upgraded: Engine, stitch, census
):
    """Either shape, and nothing between: a reader tells the two apart by
    whichever it looks at first."""
    with pytest.raises(IntegrityError, match="census_or_map"):
        with upgraded.begin() as conn:
            _insert_stream(
                conn,
                "st-streams-shap",
                "neither",
                event=None,
                stitch=stitch,
                census=census,
            )


def test_no_stream_is_its_own_composite(upgraded: Engine):
    with pytest.raises(IntegrityError, match="not_its_own_composite"):
        with upgraded.begin() as conn:
            _insert_stream(
                conn, "st-streams-self", "itself", event=5, composite="st-streams-self"
            )


def test_a_composite_carries_its_map_and_its_segments_point_at_it(upgraded: Engine):
    """The composite is a row like any stream of its file, and its segments
    name it; the map that cut them is on the composite and nowhere else."""
    with upgraded.begin() as conn:
        _insert_stream(conn, _COMPOSITE, "composite -", event=None, stitch=_STITCH)
        _insert_stream(conn, _LOW, f"{_KEY} event=3", event=3, composite=_COMPOSITE)
        _insert_stream(conn, _HIGH, f"{_KEY} event=4", event=4, composite=_COMPOSITE)

    with upgraded.connect() as conn:
        segments = (
            conn.execute(
                text(
                    "SELECT stream_id FROM acquisition_stream "
                    "WHERE composite_stream_id = :id ORDER BY stream_id"
                ),
                {"id": _COMPOSITE},
            )
            .scalars()
            .all()
        )
        stitch = conn.execute(
            text("SELECT stitch FROM acquisition_stream WHERE stream_id = :id"),
            {"id": _COMPOSITE},
        ).scalar_one()
    assert segments == sorted([_LOW, _HIGH])
    # Its own polarity's part of the map, the owners named by their key
    assert stitch["runs"] == [
        [40, 122, f"{_KEY} event=3"],
        [122, 600, f"{_KEY} event=4"],
    ]
    assert (stitch["rule"], stitch["source"]) == (1, "default")
    # A composite has no census of its own
    with upgraded.connect() as conn:
        census = conn.execute(
            text(
                "SELECT signature_key, acquisition_params, scan_count, blocks,"
                " t_first, t_last, signature FROM acquisition_stream"
                " WHERE stream_id = :id"
            ),
            {"id": _COMPOSITE},
        ).one()
    assert tuple(census)[:6] == (None,) * 6
    assert census.signature == {"ms_order": 1, "polarity": "-", "composite": True}


def test_a_segment_points_at_a_stream_of_the_table(upgraded: Engine):
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            _insert_stream(
                conn,
                "st-streams-orph",
                f"{_KEY} event=5",
                event=5,
                composite="st-nowhere",
            )


def test_a_composite_is_no_segment(upgraded: Engine):
    """A composite, map and all, that points at another composite is neither
    shape: the check refuses it."""
    with pytest.raises(IntegrityError, match="census_or_map"):
        with upgraded.begin() as conn:
            _insert_stream(
                conn,
                "st-streams-nest",
                "composite of a composite",
                event=None,
                composite=_COMPOSITE,
                stitch=_STITCH,
            )


def test_a_segment_points_at_a_composite_of_its_own_file_only(upgraded: Engine):
    """The reference carries the file. The other file is there from the
    tests above."""
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            _insert_stream(
                conn,
                "st-streams-xfil",
                f"{_KEY} event=9",
                file_id=_OTHER_FILE_ID,
                event=9,
                composite=_COMPOSITE,
            )


def test_an_item_reads_a_stream_of_its_own_file_only(upgraded: Engine):
    """The reference carries the file: an item of one file cannot point at
    a stream of another, however the stream is named."""
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sample_item ("
                    "  sample_item_id, sample_batch_id, sample_file_id,"
                    "  sample_item_name, sample_item_type, polarity, stream_id"
                    ") VALUES (:id, :batch, :file, 'another file''s stream',"
                    "          'ACQUISITION', '-', :stream)"
                ),
                _IDS
                | {
                    "id": "si-streams-xfil",
                    "file": _OTHER_FILE_ID,
                    "stream": _COMPOSITE,
                },
            )


def test_a_composite_is_not_taken_from_under_its_segments(upgraded: Engine):
    """Refused, as a stream is not taken from under its item: a segment left
    pointing at nothing would be a stream of a composite that is not there."""
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text("DELETE FROM acquisition_stream WHERE stream_id = :stream"),
                {"stream": _COMPOSITE},
            )

    assert _count(upgraded, "acquisition_stream", "stream_id = :id", id=_COMPOSITE) == 1


def test_the_reference_carries_the_names_the_models_give_it(upgraded: Engine):
    """A migrated database and one created from the models must agree, or
    the next revision to touch either would fail on one of them."""
    with upgraded.connect() as conn:
        constraints = (
            conn.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid IN ('sample_item'::regclass,"
                    "                   'acquisition_stream'::regclass)"
                )
            )
            .scalars()
            .all()
        )
        indexes = (
            conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename IN ('sample_item', 'acquisition_stream')"
                )
            )
            .scalars()
            .all()
        )
    expected = {
        "fk_sample_item_sample_file_id_acquisition_stream",
        "fk_acquisition_stream_sample_file_id_sample_file",
        "fk_acquisition_stream_sample_file_id_acquisition_stream",
        "uq_acquisition_stream_file_key",
        "uq_acquisition_stream_file_stream",
        "pk_acquisition_stream",
        "ck_acquisition_stream_census_or_map",
        "ck_acquisition_stream_not_its_own_composite",
    }
    assert expected <= set(constraints), set(constraints)
    assert "ix_sample_item_stream_id" in indexes, indexes
    assert "ix_acquisition_stream_composite_stream_id" in indexes, indexes


def test_a_files_streams_and_items_go_with_the_file(upgraded: Engine):
    """One statement takes the file, its items, its streams and its
    composite with its segments: the references between them are checked at
    the end of it, when all are gone."""
    assert (
        _count(upgraded, "acquisition_stream", "sample_file_id = :file", file=_FILE_ID)
        == 4
    )
    with upgraded.begin() as conn:
        conn.execute(text("DELETE FROM sample_file WHERE sample_file_id = :file"), _IDS)

    assert (
        _count(upgraded, "acquisition_stream", "sample_file_id = :file", file=_FILE_ID)
        == 0
    )
    assert _count(upgraded, "sample_item", "sample_file_id = :file", file=_FILE_ID) == 0
    # The other file's streams are untouched
    assert (
        _count(
            upgraded,
            "acquisition_stream",
            "sample_file_id = :file",
            file=_OTHER_FILE_ID,
        )
        == 3
    )


def test_the_downgrade_takes_the_column_and_the_table(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it leaves the database at PRIOR_REVISION.
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with upgraded.connect() as conn:
        column = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'sample_item' AND column_name = 'stream_id'"
                )
            )
            .scalars()
            .all()
        )
        table = conn.execute(
            text("SELECT to_regclass('acquisition_stream')")
        ).scalar_one()
    assert column == []
    assert table is None
