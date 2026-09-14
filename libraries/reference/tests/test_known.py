"""Tests for the bulk known-composition provider (Stage A input).

What a source contributes is bounded by what its row records: its window under
the context's ceiling, its radical allowance, and its polarity against the
sample's. A row seeded here without a window reads as the column's default says,
at the mirror window.
"""

from datetime import datetime, timezone

import pytest


pytest.importorskip("aiosqlite")

from sqlalchemy import insert  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from mascope_reference.known import iter_known_compositions  # noqa: E402
from mascope_reference.schema import reference_compound, reference_source  # noqa: E402
from mascope_reference.scope import MIRROR_WINDOW, UNBOUNDED  # noqa: E402
from mascope_tools.composition.known_window import KnownWindow  # noqa: E402


def _seed(sync_engine, source_rows):
    """Insert (source, compounds) groups. source_rows: list of (source_dict, [compound_dicts])."""
    with sync_engine.begin() as conn:
        for source, compounds in source_rows:
            sid = conn.execute(
                insert(reference_source)
                .values(
                    name=source["name"],
                    version="v1",
                    license=source.get("license", "public-domain"),
                    record_count=len(compounds),
                    is_active=source.get("is_active", True),
                    ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    known_window=(
                        source["window"].to_json() if "window" in source else None
                    ),
                    allow_radicals=source.get("allow_radicals", False),
                    polarity=source.get("polarity"),
                )
                .returning(reference_source.c.reference_source_id)
            ).scalar_one()
            for c in compounds:
                conn.execute(
                    insert(reference_compound).values(
                        reference_source_id=sid,
                        formula=c["formula"],
                        monoisotopic_mass=c.get("mass"),
                        charge=c.get("charge"),
                        inchikey=c.get("inchikey"),
                        name=c.get("name"),
                        smiles=None,
                        inchi=None,
                        source_native_id=c.get("id", c["formula"]),
                        xrefs=c.get("xrefs", {}),
                        license=c.get(
                            "license", source.get("license", "public-domain")
                        ),
                    )
                )


async def _known(db_path, **kw):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with async_sessionmaker(engine)() as s:
            return await iter_known_compositions(s, **kw)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dedup_on_formula_with_one_to_many_identities(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "src-a"},
                [
                    {"formula": "C10H16O3", "mass": 184.11, "name": "Pinonic acid"},
                    {"formula": "C9H14O4", "mass": 186.09, "name": "Pinic acid"},
                ],
            ),
            (
                {"name": "src-b"},
                [
                    {
                        "formula": "C10H16O3",
                        "mass": 184.11,
                        "name": "Norpinonic acid isomer",
                    },
                ],
            ),
        ],
    )
    result = await _known(db_path)
    by_formula = {k.formula: k for k in result}
    assert set(by_formula) == {"C10H16O3", "C9H14O4"}
    # One formula shared by two sources -> one composition, two identities.
    names = sorted(i.name for i in by_formula["C10H16O3"].identities)
    assert names == ["Norpinonic acid isomer", "Pinonic acid"]
    assert {i.source for i in by_formula["C10H16O3"].identities} == {"src-a", "src-b"}


@pytest.mark.asyncio
async def test_charged_rows_are_excluded(sync_engine, db_path):
    """Intrinsic charge is recorded, never matched (issue #1726).

    Stage A pairs a neutral formula with an ionization mechanism, so a charged
    row has no neutral precursor to expand. NULL and 0 are both neutral - NULL
    is also what every pre-charge-column row carries.
    """
    _seed(
        sync_engine,
        [
            (
                {"name": "s"},
                [
                    {"formula": "C10H16O3", "mass": 184.11},  # NULL charge - kept
                    {"formula": "C9H14O4", "mass": 186.09, "charge": 0},  # kept
                    {"formula": "C5H14NO", "mass": 104.11, "charge": 1},  # dropped
                ],
            ),
        ],
    )
    result = await _known(db_path)
    assert {k.formula for k in result} == {"C10H16O3", "C9H14O4"}


#: What the shipped lists bring that no formula grid reaches.
_FAMILIES = [
    {"formula": "C10H16O3", "mass": 184.11},  # CHO - inside every window
    {"formula": "C8H24O4Si4", "mass": 296.08},  # D4
    {"formula": "C6H15O4P", "mass": 182.07},  # triethyl phosphate
    {"formula": "C8HF15O2", "mass": 413.97},  # PFOA
]
_CEILING = KnownWindow(
    frozenset({"C", "H", "N", "O", "S", "Si", "P", "F", "Cl", "Br", "I"}),
    max_carbon=40,
    max_mass=700.0,
)


@pytest.mark.asyncio
async def test_a_mirror_is_held_to_its_window_while_a_list_brings_its_families(
    sync_engine, db_path
):
    _seed(
        sync_engine,
        [
            ({"name": "a-list", "window": UNBOUNDED}, [dict(c) for c in _FAMILIES]),
            (
                {"name": "a-mirror", "window": MIRROR_WINDOW},
                [dict(c, id=f"m-{c['formula']}") for c in _FAMILIES],
            ),
        ],
    )
    result = {k.formula: k for k in await _known(db_path, ceiling=_CEILING)}
    assert set(result) == {"C10H16O3", "C8H24O4Si4", "C6H15O4P", "C8HF15O2"}
    # A formula carries the identities of the sources that admit it, and only
    # those: the mirror holds D4 too, outside its window, and lends it nothing.
    assert {i.source for i in result["C8H24O4Si4"].identities} == {"a-list"}
    assert {i.source for i in result["C10H16O3"].identities} == {"a-list", "a-mirror"}


@pytest.mark.asyncio
async def test_a_row_that_records_no_window_is_bounded_like_a_mirror(
    sync_engine, db_path
):
    _seed(sync_engine, [({"name": "legacy"}, [dict(c) for c in _FAMILIES])])
    assert {k.formula for k in await _known(db_path)} == {"C10H16O3"}


@pytest.mark.asyncio
async def test_the_ceiling_bounds_even_an_unbounded_list(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "a-list", "window": UNBOUNDED},
                [
                    {"formula": "C10H16O3", "mass": 184.11},
                    {"formula": "C50H2O2", "mass": 634.0},  # too many carbons
                    {"formula": "C40H70O30", "mass": 1030.4},  # too heavy
                    {
                        "formula": "C2H6Se",
                        "mass": 109.96,
                    },  # dimethyl selenide: Se is not opened
                ],
            )
        ],
    )
    kept = {k.formula for k in await _known(db_path, ceiling=_CEILING)}
    assert kept == {"C10H16O3"}
    # With no ceiling - the identity context - the list is its own bound.
    unbounded = {k.formula for k in await _known(db_path, ceiling=None)}
    assert unbounded == {"C10H16O3", "C50H2O2", "C40H70O30", "C2H6Se"}


@pytest.mark.asyncio
async def test_a_sources_own_bound_holds_under_a_wider_ceiling(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {
                    "name": "a-bounded-list",
                    "window": KnownWindow(
                        frozenset({"C", "H", "O", "Si"}), max_carbon=8
                    ),
                },
                [dict(c) for c in _FAMILIES]
                + [{"formula": "C10H30O5Si5", "mass": 370.09}],  # D5: ten carbons
            )
        ],
    )
    kept = {k.formula for k in await _known(db_path, ceiling=_CEILING)}
    assert kept == {"C8H24O4Si4"}


@pytest.mark.asyncio
async def test_radicals_come_only_from_a_source_that_allows_them(sync_engine, db_path):
    radicals = [
        {"formula": "HO2", "mass": 32.998},
        {"formula": "C10H15O8", "mass": 263.077},  # an RO2
        {"formula": "C10H16O8", "mass": 264.085},  # its closed-shell neighbour
    ]
    _seed(
        sync_engine,
        [
            ({"name": "no-radicals", "window": UNBOUNDED}, [dict(c) for c in radicals]),
            (
                {"name": "radicals", "window": UNBOUNDED, "allow_radicals": True},
                [dict(c, id=f"r-{c['formula']}") for c in radicals[:1]],
            ),
        ],
    )
    result = {k.formula: k for k in await _known(db_path, ceiling=_CEILING)}
    assert set(result) == {"HO2", "C10H16O8"}
    assert {i.source for i in result["HO2"].identities} == {"radicals"}


@pytest.mark.asyncio
async def test_a_source_detected_in_the_other_polarity_contributes_nothing(
    sync_engine, db_path
):
    _seed(
        sync_engine,
        [
            (
                {"name": "positive-list", "window": UNBOUNDED, "polarity": "positive"},
                [{"formula": "C8H24O4Si4", "mass": 296.08}],
            ),
            (
                {"name": "negative-list", "window": UNBOUNDED, "polarity": "negative"},
                [{"formula": "C8HF15O2", "mass": 413.97}],
            ),
            (
                {"name": "both-list", "window": UNBOUNDED},
                [{"formula": "C2HF3O2", "mass": 113.99}],
            ),
        ],
    )
    negative = {
        k.formula for k in await _known(db_path, ceiling=_CEILING, polarity="negative")
    }
    assert negative == {"C8HF15O2", "C2HF3O2"}
    positive = {
        k.formula for k in await _known(db_path, ceiling=_CEILING, polarity="positive")
    }
    assert positive == {"C8H24O4Si4", "C2HF3O2"}
    # A sample whose polarity is not known is matched against every source.
    unknown = {k.formula for k in await _known(db_path, ceiling=_CEILING)}
    assert unknown == {"C8H24O4Si4", "C8HF15O2", "C2HF3O2"}


@pytest.mark.asyncio
async def test_license_filter_and_active_only(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "public", "license": "public-domain"},
                [
                    {"formula": "C10H16O3", "mass": 184.11, "license": "public-domain"},
                ],
            ),
            (
                {"name": "custom", "license": "custom"},
                [
                    {"formula": "C9H14O4", "mass": 186.09, "license": "custom"},
                ],
            ),
            (
                {"name": "stale", "is_active": False},
                [
                    {"formula": "C8H12O4", "mass": 172.07},
                ],
            ),
        ],
    )
    # Inactive source never appears.
    all_active = {k.formula for k in await _known(db_path)}
    assert all_active == {"C10H16O3", "C9H14O4"}
    # License filter keeps only public-domain.
    public = {k.formula for k in await _known(db_path, licenses={"public-domain"})}
    assert public == {"C10H16O3"}


@pytest.mark.asyncio
async def test_identity_cap(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "s"},
                [
                    {
                        "formula": "C10H16O3",
                        "mass": 184.11,
                        "name": f"iso-{i}",
                        "id": f"x{i}",
                    }
                    for i in range(10)
                ],
            )
        ],
    )
    (comp,) = await _known(db_path, max_identities=3)
    assert comp.formula == "C10H16O3"
    assert len(comp.identities) == 3


@pytest.mark.asyncio
async def test_known_state_fingerprint_tracks_active_sources(sync_engine, db_path):
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from mascope_reference.known import known_state_fingerprint

    _seed(
        sync_engine,
        [
            ({"name": "src-a"}, [{"formula": "C10H16O3", "mass": 184.11}]),
            (
                {"name": "src-b", "is_active": False},
                [{"formula": "CH4", "mass": 16.04}],
            ),
        ],
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with async_sessionmaker(engine)() as s:
            before = await known_state_fingerprint(s)
            # Only the active source contributes.
            assert len(before) == 1
            # Activating the second source changes the fingerprint.
            await s.execute(
                update(reference_source)
                .where(reference_source.c.name == "src-b")
                .values(is_active=True)
            )
            after = await known_state_fingerprint(s)
            assert len(after) == 2
            assert after != before
            # Bringing a source's scope up to date without reloading it changes
            # it too: what the known set holds depends on the row's window.
            await s.execute(
                update(reference_source)
                .where(reference_source.c.name == "src-a")
                .values(known_window=UNBOUNDED.to_json(), allow_radicals=True)
            )
            refreshed = await known_state_fingerprint(s)
            assert refreshed != after
            await s.execute(
                update(reference_source)
                .where(reference_source.c.name == "src-a")
                .values(polarity="negative")
            )
            assert await known_state_fingerprint(s) != refreshed
            # Deactivating everything empties it - the no-mirror fast path.
            await s.execute(update(reference_source).values(is_active=False))
            assert await known_state_fingerprint(s) == ()
    finally:
        await engine.dispose()
