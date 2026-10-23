"""
Integration tests: *Find structure*, the reference rows that could give a
target compound its structure.

The route lists the active reference rows whose Hill formula is the one asked
about and that carry a structure, with the rows of the compound's CAS number
first and those of its name next, each with its source and licence. A pick is
saved through the compound update like any typed identity, so the InChI is
still checked against the compound's formula there.

Every test asks about a formula of its own: the reference mirror is shared
with the rest of the suite, and the demo lists may name common formulas.
"""

from datetime import datetime, timezone
from random import randrange
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import ReferenceCompound, ReferenceSource, TargetCompound
from mascope_backend.db.id import gen_id
from mascope_reference import canonical_formula, monoisotopic_mass
from mascope_reference.query import STRUCTURE_PAGE_CAP


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ROUTE = "/api/target/compounds/structures"


def _key(letter: str) -> str:
    """An InChIKey-shaped key, one per letter."""
    return f"{letter * 14}-{letter * 10}-N"


def _reference(source_id: int, formula: str, native_id: str, **values):
    return ReferenceCompound(
        reference_source_id=source_id,
        formula=canonical_formula(formula),
        monoisotopic_mass=monoisotopic_mass(canonical_formula(formula)),
        source_native_id=native_id,
        license=values.pop("license", "CC-BY-4.0"),
        xrefs=values.pop("xrefs", {}),
        **values,
    )


@pytest_asyncio.fixture
async def seeded(async_session_factory):
    """Reference rows of a formula no other test uses, in Hill order
    ``C{n}H{m}N3O9S2``: in an active CompTox-like source a CAS-numbered row and
    a named one, in an active custom list a row under another licence and a
    row with no structure, and in an inactive load a row of the same CAS
    number."""
    carbon, hydrogen = randrange(40, 99), randrange(10, 99)
    ids = SimpleNamespace(
        hill=f"C{carbon}H{hydrogen}N3O9S2",
        # The same formula as nobody writes it.
        scrambled=f"S2O9N3H{hydrogen}C{carbon}",
        cas=f"{randrange(10**6, 10**7)}-{randrange(10, 99)}-{randrange(10)}",
        sources=[],
    )
    ids.inchi = f"InChI=1S/{ids.hill}/c1-2"
    async with async_session_factory() as session:
        for name, license, active in (
            ("comptox", "CC-BY-4.0", True),
            ("custom", "custom", True),
            ("comptox", "CC-BY-4.0", False),
        ):
            source = ReferenceSource(
                name=f"structures-{name}-{gen_id(6)}",
                version="test",
                license=license,
                record_count=1,
                is_active=active,
                ingested_at=_NOW,
                known_window={},
            )
            session.add(source)
            await session.flush()
            ids.sources.append(source.reference_source_id)
        comptox, custom, inactive = ids.sources
        session.add_all(
            [
                _reference(
                    comptox,
                    ids.hill,
                    "DTXSID-A",
                    name="Alpha isomer",
                    inchikey=_key("A"),
                    inchi=ids.inchi,
                    smiles="CC",
                    xrefs={"casrn": ids.cas},
                ),
                _reference(
                    comptox,
                    ids.hill,
                    "DTXSID-B",
                    name="Beta isomer",
                    inchikey=_key("B"),
                ),
                _reference(
                    custom,
                    ids.hill,
                    "row-1",
                    name="Gamma isomer",
                    inchikey=_key("C"),
                    license="CC0-1.0",
                ),
                _reference(custom, ids.hill, "row-2", name="No structure"),
                _reference(
                    inactive,
                    ids.hill,
                    "DTXSID-OLD",
                    name="Alpha isomer, old load",
                    inchikey=_key("D"),
                    xrefs={"casrn": ids.cas},
                ),
            ]
        )
        await session.commit()

    yield ids

    async with async_session_factory() as session:
        await session.execute(
            delete(ReferenceCompound).where(
                ReferenceCompound.reference_source_id.in_(ids.sources)
            )
        )
        await session.execute(
            delete(ReferenceSource).where(
                ReferenceSource.reference_source_id.in_(ids.sources)
            )
        )
        await session.commit()


def _names(response) -> list[str]:
    return [row["name"] for row in response.json()["data"]]


@pytest.mark.asyncio
async def test_the_formula_is_read_in_any_notation(guest_client, seeded):
    response = await guest_client.get(_ROUTE, params={"formula": seeded.scrambled})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["formula"] == seeded.hill
    assert body["results"] == 3


@pytest.mark.asyncio
async def test_only_active_sources_rows_with_a_structure_are_listed(
    guest_client, seeded
):
    response = await guest_client.get(
        _ROUTE, params={"formula": seeded.hill, "cas_number": seeded.cas}
    )

    # Not the inactive load's row of the same CAS number, nor the row
    # without a structure.
    assert sorted(_names(response)) == ["Alpha isomer", "Beta isomer", "Gamma isomer"]


@pytest.mark.asyncio
async def test_each_row_shows_its_source_licence_and_cas_number(guest_client, seeded):
    response = await guest_client.get(_ROUTE, params={"formula": seeded.hill})

    rows = {row["name"]: row for row in response.json()["data"]}
    alpha, gamma = rows["Alpha isomer"], rows["Gamma isomer"]
    assert alpha["source"].startswith("structures-comptox-")
    assert alpha["license"] == "CC-BY-4.0"
    assert gamma["source"].startswith("structures-custom-")
    assert gamma["license"] == "CC0-1.0"
    assert {
        field: alpha[field]
        for field in ("inchikey", "inchi", "smiles", "cas_number", "source_native_id")
    } == {
        "inchikey": _key("A"),
        "inchi": seeded.inchi,
        "smiles": "CC",
        "cas_number": seeded.cas,
        "source_native_id": "DTXSID-A",
    }
    assert gamma["cas_number"] is None


@pytest.mark.asyncio
async def test_the_cas_match_comes_first_then_the_name_match(guest_client, seeded):
    response = await guest_client.get(
        _ROUTE,
        params={
            "formula": seeded.hill,
            # Undashed, against the row's dashed number.
            "cas_number": seeded.cas.replace("-", ""),
            "name": "gamma ISOMER",
        },
    )

    rows = response.json()["data"]
    assert [row["name"] for row in rows] == [
        "Alpha isomer",
        "Gamma isomer",
        "Beta isomer",
    ]
    assert [(row["cas_match"], row["name_match"]) for row in rows] == [
        (True, False),
        (False, True),
        (False, False),
    ]


@pytest.mark.asyncio
async def test_the_rows_are_paged(guest_client, seeded):
    pages = [
        await guest_client.get(
            _ROUTE,
            params={
                "formula": seeded.hill,
                "cas_number": seeded.cas,
                "page": page,
                "limit": 2,
            },
        )
        for page in (0, 1)
    ]

    assert [page.json()["results"] for page in pages] == [3, 3]
    assert _names(pages[0]) + _names(pages[1]) == [
        "Alpha isomer",
        "Beta isomer",
        "Gamma isomer",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paging",
    [
        {"limit": STRUCTURE_PAGE_CAP + 1},
        {"limit": 0},
        {"page": -1},
    ],
)
async def test_a_page_past_the_cap_or_before_the_first_is_refused(
    guest_client, seeded, paging
):
    response = await guest_client.get(_ROUTE, params={"formula": seeded.hill, **paging})

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_a_formula_no_reference_row_has_says_so(guest_client, seeded):
    formula = f"C{randrange(100, 199)}H3Cl9"

    response = await guest_client.get(_ROUTE, params={"formula": formula})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["results"], body["data"]) == (0, [])
    assert "No active reference list has a structure" in body["message"]
    assert formula in body["message"]


@pytest.mark.asyncio
async def test_a_picked_structure_is_saved_through_the_compound_update(
    editor_client, async_session_factory, seeded
):
    """Two steps, as the editor takes them: the lookup with the compound's
    formula and CAS number, then the update with the first row's identity."""
    name = f"Structures test {gen_id()}"
    created = await editor_client.post(
        "/api/target/compounds",
        json=[
            {
                "target_compound_name": name,
                "target_compound_formula": seeded.hill,
                "cas_number": seeded.cas,
            }
        ],
    )
    assert created.status_code == 201, created.text
    (compound,) = created.json()["data"]["created_compounds"]
    try:
        found = await editor_client.get(
            _ROUTE,
            params={
                "formula": compound["target_compound_formula"],
                "cas_number": compound["cas_number"],
                "name": name,
            },
        )
        pick = found.json()["data"][0]
        assert pick["cas_match"]

        response = await editor_client.patch(
            "/api/target/compounds",
            json=[
                {
                    "target_compound_id": compound["target_compound_id"],
                    "target_compound_name": name,
                    "cas_number": pick["cas_number"],
                    "inchikey": pick["inchikey"],
                    "inchi": pick["inchi"],
                    "smiles": pick["smiles"],
                }
            ],
        )

        assert response.status_code == 200, response.text
        async with async_session_factory() as session:
            stored = await session.get(TargetCompound, compound["target_compound_id"])
            assert (stored.inchikey, stored.inchi, stored.smiles) == (
                _key("A"),
                seeded.inchi,
                "CC",
            )
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(TargetCompound).where(
                    TargetCompound.target_compound_name == name
                )
            )
            await session.commit()
