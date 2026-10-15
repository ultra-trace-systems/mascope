"""
Integration tests: what bound a sample's chemistry is read, and never taken.

``sample_item.bound_by`` and ``sample_item.method_binding_id`` say how
auto-processing decided an item's chemistry. Two paths serialise a sample
item, and both show them: the item routes read the table through
``SampleItemRead``, and ``GET /api/samples`` - what the web app and the SDK
list samples with - reads ``sample_view``, as does the row a created item is
answered with.

Showing them makes them fields a client holds, and a client that sends a row
back sends them too. So the same requests are made here with the two fields
in them: an item made that way records no rung, and an update neither sets
one nor clears the one an item has. Only a changed mode clears it, which the
update's own answer then shows.

Run against the real routes and a real database, because what is pinned is
the whole way: the model a route reads its body into, the controller, the
row, and the row read back.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.db import (
    Dataset,
    IonizationMode,
    MethodBinding,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

#: The rung and binding each seeded item holds, by the name the tests know
#: it under. The binding's id is filled in by the fixture.
_SEEDED = {
    "declared": ("declared", None),
    "method": ("method", "binding"),
    "by_hand": (None, None),
}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def provenance_sample_view(async_engine):
    """``sample_view``, which ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def seeded(async_session_factory, test_users):
    """A batch the editor may edit, holding an item of each kind.

    One its file's acquisition record bound, one a method binding bound, and
    one a person made, which nothing routed.
    """
    ids = {
        name: gen_id(16)
        for name in (
            "workspace",
            "dataset",
            "batch",
            "file",
            "mode",
            "other_mode",
            "binding",
            *_SEEDED,
        )
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Binding provenance {ids['workspace']}",
                workspace_status="active",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=ids["workspace"],
                user_id=test_users["editor"].id,
                workspace_role="editor",
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Binding provenance",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Binding provenance",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"provenance-orbi_{ids['file']}",
                instrument="provenance-orbi",
                datetime=_NOW.replace(tzinfo=None),
                datetime_utc=_NOW,
                length=60.0,
                range=[100.0, 500.0],
                polarity="-",
            )
        )
        for key in ("mode", "other_mode"):
            session.add(
                IonizationMode(
                    ionization_mode_id=ids[key],
                    ionization_mode_name=f"Provenance {key} {ids[key]}",
                    ionization_mode_token=None,
                    ionization_mode_polarity="-",
                    ionization_mechanism_ids=[],
                )
            )
        await session.flush()
        session.add(
            MethodBinding(
                method_binding_id=ids["binding"],
                binding_key=f"provenance-{ids['binding']}",
                instrument="provenance-orbi",
                method_key="nitrate.meth",
                signature_class="-",
                ionization_mode_id=ids["mode"],
                chemistry_keys=["mech-a"],
                state="learned",
                source="token",
                first_seen=_NOW,
                last_seen=_NOW,
                n_streams=1,
                n_disagreements=0,
            )
        )
        await session.flush()
        for name, (rung, binding) in _SEEDED.items():
            session.add(
                SampleItem(
                    sample_item_id=ids[name],
                    sample_batch_id=ids["batch"],
                    sample_file_id=ids["file"],
                    sample_item_name=f"Provenance {name}",
                    sample_item_type="SAMPLE",
                    sample_item_attributes={},
                    polarity="-",
                    ionization_mode_id=ids["mode"],
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    bound_by=rung,
                    method_binding_id=None if binding is None else ids[binding],
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        # The workspace takes its dataset, batch and items with it; the file
        # takes whatever item is left; the binding and the modes are their own.
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == ids["file"])
        )
        await session.execute(
            delete(MethodBinding).where(
                MethodBinding.method_binding_id == ids["binding"]
            )
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_([ids["mode"], ids["other_mode"]])
            )
        )
        await session.commit()


def _expected(ids: dict) -> dict[str, tuple[str | None, str | None]]:
    """``{sample_item_id: (bound_by, method_binding_id)}`` as seeded."""
    return {
        ids[name]: (rung, None if binding is None else ids[binding])
        for name, (rung, binding) in _SEEDED.items()
    }


def _provenance(row: dict) -> tuple[str | None, str | None]:
    """The two fields of a serialised row. A KeyError is the finding: the
    row does not carry them at all."""
    return row["bound_by"], row["method_binding_id"]


async def _stored(async_session_factory, sample_item_id: str):
    """The two columns as the table holds them, whatever a route answered."""
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleItem.bound_by, SampleItem.method_binding_id).where(
                    SampleItem.sample_item_id == sample_item_id
                )
            )
        ).one()


def _item_body(ids: dict, **changes) -> dict:
    """An item as a client sends one, with nothing left to compute."""
    return {
        "sample_batch_id": ids["batch"],
        "sample_file_id": ids["file"],
        "sample_item_name": "Provenance request",
        "sample_item_type": "SAMPLE",
        "sample_item_attributes": {},
        "polarity": "-",
        "ionization_mode_id": ids["mode"],
        "tic": 1000.0,
        "t0": 0.0,
        "t1": 60.0,
    } | changes


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_item_is_read_with_what_bound_it(editor_client, seeded):
    for sample_item_id, expected in _expected(seeded).items():
        response = await editor_client.get(f"/api/sample/items/{sample_item_id}")

        assert response.status_code == 200, response.text
        assert _provenance(response.json()["data"]) == expected


@pytest.mark.asyncio
async def test_a_batchs_items_are_listed_with_what_bound_each(editor_client, seeded):
    response = await editor_client.get(
        "/api/sample/items", params={"sample_batch_id": seeded["batch"]}
    )

    assert response.status_code == 200, response.text
    listed = {
        row["sample_item_id"]: _provenance(row) for row in response.json()["data"]
    }
    assert listed == _expected(seeded)


@pytest.mark.asyncio
async def test_the_sample_view_lists_what_bound_each_sample(editor_client, seeded):
    """What the web app and the SDK list a batch's samples with."""
    response = await editor_client.get(
        "/api/samples", params={"sample_batch_id": seeded["batch"]}
    )

    assert response.status_code == 200, response.text
    listed = {
        row["sample_item_id"]: _provenance(row) for row in response.json()["data"]
    }
    assert listed == _expected(seeded)


@pytest.mark.asyncio
async def test_one_sample_is_read_through_the_view_with_what_bound_it(
    editor_client, seeded
):
    response = await editor_client.get(f"/api/samples/{seeded['method']}")

    assert response.status_code == 200, response.text
    assert _provenance(response.json()["data"]) == ("method", seeded["binding"])


# ---------------------------------------------------------------------------
# Not taken
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_created_item_cannot_claim_a_rung(
    editor_client, seeded, async_session_factory
):
    """Named with a binding that exists, so that nothing but the request
    model stands between the claim and the row."""
    response = await editor_client.post(
        "/api/sample/items",
        json=_item_body(seeded, bound_by="method", method_binding_id=seeded["binding"]),
    )

    assert response.status_code == 201, response.text
    (created,) = response.json()["data"]
    # The row it is answered with is the view's, and says so itself.
    assert _provenance(created) == (None, None)
    assert tuple(await _stored(async_session_factory, created["sample_item_id"])) == (
        None,
        None,
    )


@pytest.mark.asyncio
async def test_an_update_cannot_give_an_item_a_rung(
    editor_client, seeded, async_session_factory
):
    response = await editor_client.patch(
        f"/api/sample/items/{seeded['by_hand']}",
        json={
            "sample_item": _item_body(
                seeded,
                sample_item_name="Provenance renamed",
                bound_by="method",
                method_binding_id=seeded["binding"],
            )
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["sample_item_name"] == "Provenance renamed"
    assert _provenance(updated) == (None, None)
    assert tuple(await _stored(async_session_factory, seeded["by_hand"])) == (
        None,
        None,
    )


@pytest.mark.asyncio
async def test_an_update_cannot_clear_or_change_the_rung_an_item_has(
    editor_client, seeded, async_session_factory
):
    """What a client does that sends back a row it changed one field of,
    with the rung blanked or rewritten on the way."""
    for claim in (
        {"bound_by": None, "method_binding_id": None},
        {"bound_by": "token", "method_binding_id": None},
    ):
        response = await editor_client.patch(
            f"/api/sample/items/{seeded['method']}",
            json={
                "sample_item": _item_body(
                    seeded, sample_item_name="Provenance renamed", **claim
                )
            },
        )

        assert response.status_code == 200, response.text
        assert _provenance(response.json()["data"]) == ("method", seeded["binding"])
        assert tuple(await _stored(async_session_factory, seeded["method"])) == (
            "method",
            seeded["binding"],
        )


@pytest.mark.asyncio
async def test_a_changed_mode_is_answered_with_no_rung(
    editor_client, seeded, async_session_factory
):
    """The one thing a request does to the rung: a mode set by hand was not
    bound by whatever bound the old one, and the answer shows it cleared."""
    response = await editor_client.patch(
        f"/api/sample/items/{seeded['declared']}",
        json={
            "sample_item": _item_body(seeded, ionization_mode_id=seeded["other_mode"])
        },
    )

    assert response.status_code == 200, response.text
    assert _provenance(response.json()["data"]) == (None, None)
    assert tuple(await _stored(async_session_factory, seeded["declared"])) == (
        None,
        None,
    )
