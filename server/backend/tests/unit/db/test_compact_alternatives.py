"""
Unit tests for the packed storage of `PeakAssignment.alternatives`.

`CompactAlternatives` stores the untargeted finder's formula-only shortlist
entries as two-element lists and expands them on read. These pin the codec:
exactly the finder's shape packs, everything else passes through untouched in
both directions, order is kept, and the read side accepts rows written before
the packing (dict entries) as well as after it.

Pure: the type's two hooks are called directly, no database.
"""

from mascope_backend.db.models import (
    CompactAlternatives,
    pack_alternative,
    unpack_alternative,
)


_FORMULA_ONLY = {
    "assigned_formula": "C10H14O8",
    "plausibility": 1.0,
    "source": "untargeted",
}
_SCORED = {
    "assigned_formula": "C8H12O4",
    "ion_formula": "C8H12BrO4-",
    "fit_score": 0.27,
    "plausibility": 1.0,
    "source": "database",
}


def test_a_formula_only_entry_packs_to_formula_and_plausibility():
    assert pack_alternative(_FORMULA_ONLY) == ["C10H14O8", 1.0]


def test_a_null_plausibility_still_packs():
    entry = {**_FORMULA_ONLY, "plausibility": None}
    assert pack_alternative(entry) == ["C10H14O8", None]
    assert unpack_alternative(["C10H14O8", None]) == entry


def test_anything_else_passes_through():
    """A scored contender, an entry with a key of its own, a database-sourced
    formula, a bare formula without a source: none is the finder's shape."""
    for entry in (
        _SCORED,
        {**_FORMULA_ONLY, "note": "published"},
        {**_FORMULA_ONLY, "source": "database"},
        {"assigned_formula": "C7H8O", "plausibility": 0.8},
        {"assigned_formula": None, "plausibility": 0.8, "source": "untargeted"},
        None,
    ):
        assert pack_alternative(entry) is entry


def test_a_plausibility_that_is_not_a_number_does_not_pack():
    """The packed form is `[string, number-or-null]`; an entry that would not
    land in that domain stays a dict, or the two hooks stop being inverses."""
    for plausibility in ("high", True, {"value": 1.0}, ["1.0"]):
        entry = {**_FORMULA_ONLY, "plausibility": plausibility}
        assert pack_alternative(entry) is entry


def test_unpack_restores_the_dict_and_leaves_dicts_alone():
    assert unpack_alternative(["C10H14O8", 1.0]) == _FORMULA_ONLY
    assert unpack_alternative(_SCORED) is _SCORED
    # A list of any other length is not the packed shape and is left as it is.
    odd = ["C10H14O8"]
    assert unpack_alternative(odd) is odd


def test_unpack_claims_only_what_pack_produces():
    """`alternatives` on an imported row is unvalidated client JSON, so a stored
    entry can be a list this column never wrote. Anything but a string paired
    with a number or null is left alone rather than read back as a formula and
    a plausibility nobody stated."""
    for entry in (
        ["C10H14O8", "isomer of the winner"],
        [{"x": 1}, {"y": 2}],
        [1.0, "C10H14O8"],
        ["C10H14O8", True],
        ["C10H14O8", ["nested"]],
        [None, None],
        [],
        ["C10H14O8", 1.0, "extra"],
    ):
        assert unpack_alternative(entry) is entry


def test_the_two_hooks_are_inverses_over_the_packed_domain():
    for plausibility in (1.0, 0.0, 0.427, 1, None):
        entry = {**_FORMULA_ONLY, "plausibility": plausibility}
        assert unpack_alternative(pack_alternative(entry)) == entry
    # And the other way: a stored entry the codec would expand packs back to
    # exactly the two elements it was read from, so a curation write that
    # rewrites the whole list cannot drift it.
    for stored in (["C10H14O8", 1.0], ["C10H14O8", None]):
        assert pack_alternative(unpack_alternative(stored)) == stored


def test_the_column_type_round_trips_a_mixed_list_in_order():
    codec = CompactAlternatives()
    stored = codec.process_bind_param([_FORMULA_ONLY, _SCORED, _FORMULA_ONLY], None)
    assert stored == [["C10H14O8", 1.0], _SCORED, ["C10H14O8", 1.0]]
    assert codec.process_result_value(stored, None) == [
        _FORMULA_ONLY,
        _SCORED,
        _FORMULA_ONLY,
    ]


def test_the_column_type_reads_rows_written_before_the_packing():
    """A row the migration did not rewrite still holds dict entries."""
    codec = CompactAlternatives()
    legacy = [_FORMULA_ONLY, _SCORED]
    assert codec.process_result_value(legacy, None) == legacy


def test_the_column_type_passes_null_through_both_ways():
    codec = CompactAlternatives()
    assert codec.process_bind_param(None, None) is None
    assert codec.process_result_value(None, None) is None


def test_the_column_type_keeps_its_impls_comparison_rules():
    """A `TypeDecorator` inherits neither `hashable` nor
    `coerce_compared_value` from its impl, and `TypeEngine`'s defaults are both
    wrong for a JSON column: uniquing a result that selects it would raise a
    bare `TypeError: unhashable type` instead of the error naming the column,
    and an indexed comparison would coerce the compared value to *this* type -
    JSON-encoding an index key and casting it `::JSON`, for which Postgres has
    no equality operator, so the query fails at execution rather than review.

    Pinned against plain `JSON` rather than against a literal, so the two
    cannot drift apart.
    """
    from sqlalchemy import JSON, Column, Integer, MetaData, Table, select
    from sqlalchemy.dialects import postgresql

    assert CompactAlternatives().hashable is JSON().hashable

    metadata = MetaData()
    table = Table(
        "t",
        metadata,
        Column("id", Integer),
        Column("packed", CompactAlternatives),
        Column("plain", JSON),
    )
    dialect = postgresql.dialect()
    packed, plain = (
        str(
            select(table.c.id)
            .where(table.c[name][0] == "C10H14O8")
            .compile(dialect=dialect)
        ).replace(name, "col")
        for name in ("packed", "plain")
    )
    assert packed == plain


def test_a_null_is_stored_as_the_json_literal_like_every_other_blob():
    """What reaches the database, which the two hooks above cannot show.

    A `TypeDecorator` does not inherit `should_evaluate_none` from its impl, and
    without it the ORM treats a None as an absent value and leaves the column
    out of the INSERT: SQL NULL where the row's other blobs hold the JSON
    literal `null`, and - because a bulk insert batches only rows naming the
    same columns - the ledger's one `executemany` split into a statement per
    run of rows that agree. Both are invisible to a test that calls the hooks
    directly, so this one goes through a real engine (sqlite, hermetic).
    """
    from sqlalchemy import Integer, create_engine, event, insert, text
    from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

    class Base(DeclarativeBase):
        pass

    class Row(Base):
        __tablename__ = "row"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        alternatives: Mapped[list | None] = mapped_column(CompactAlternatives)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    inserts: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, *rest: (
            inserts.append(statement)
            if statement.lstrip().upper().startswith("INSERT")
            else None
        ),
    )

    # Rows with and without alternatives, interleaved as a ledger's are: the
    # engine writes None for every unassigned placeholder peak.
    rows = [
        {"id": index, "alternatives": [_FORMULA_ONLY] if index % 2 else None}
        for index in range(20)
    ]
    with Session(engine) as session:
        session.execute(insert(Row), rows)
        session.commit()

    assert len(inserts) == 1, "the bulk insert must not split on a null blob"
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT alternatives FROM row WHERE id = 0")
        ).scalar()
    assert stored == "null"
