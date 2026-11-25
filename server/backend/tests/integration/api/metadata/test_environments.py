"""Integration tests: the environment registry, ``/api/environments``.

An environment is shared reference data, as a registry field is (decision 14
of ``docs/dev/sample_filters_and_metadata.md``, MAS-104): a guest reads, the
global editor role or higher creates, changes, retires and deletes. What the
service guards: a key never changes; the shipped ``environment`` field's
allowed values are the registry's keys, retired ones included, after every
write and every start; a shipped environment is edited and retired, never
deleted; and the start creates the shipped set where it is missing and
changes nothing a second time.

The integration database is made by ``create_all``, so the shipped field the
migration seeds is inserted here. The database is shared by the session, and
the field tests read the field's values as empty: every test removes the
environments it made, the shipped ones included, and writes the field's
values back.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.new.metadata.environment_catalogue import (
    SHIPPED_ENVIRONMENTS,
)
from mascope_backend.api.new.metadata.environment_service import (
    ensure_shipped_environments,
    sync_environment_field,
)
from mascope_backend.db import Environment, MetadataField
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)
_SHIPPED_IDS = [shipped.environment_id for shipped in SHIPPED_ENVIRONMENTS]


def _key(stem: str) -> str:
    """A key no other test uses: the database is shared by the session."""
    return f"{stem}-{gen_id(6).lower()}"


@pytest_asyncio.fixture
async def system_field(async_session_factory):
    """The shipped ``environment`` field, as the migration seeds it."""
    async with async_session_factory() as session:
        field = await session.scalar(
            select(MetadataField).where(MetadataField.key == "environment")
        )
        if field is None:
            field = MetadataField(
                metadata_field_id="sysfieldenvironm",
                key="environment",
                label="Environment",
                type="tags",
                allowed_values=[],
                system=True,
                created_utc=_NOW,
                modified_utc=_NOW,
            )
            session.add(field)
            await session.commit()
    return field


@pytest_asyncio.fixture
async def made(async_session_factory, system_field):
    """Delete the environments a test made, and the shipped ones, then write the
    field's values back from what is left."""
    ids: list[str] = []
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(Environment).where(
                Environment.environment_id.in_([*ids, *_SHIPPED_IDS])
            )
        )
        await sync_environment_field(session)
        await session.commit()


async def _field_values(async_session_factory) -> list[str]:
    async with async_session_factory() as session:
        return await session.scalar(
            select(MetadataField.allowed_values).where(
                MetadataField.key == "environment"
            )
        )


async def _create(client, made, **body) -> dict:
    body = {"label": "Coast", **body}
    body.setdefault("key", _key("coast"))
    response = await client.post("/api/environments", json=body)
    assert response.status_code == 201, response.text
    environment = response.json()["data"]
    made.append(environment["environment_id"])
    return environment


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_guest_reads_the_registry(guest_client, editor_client, made):
    environment = await _create(editor_client, made)

    listed = await guest_client.get("/api/environments")
    assert listed.status_code == 200, listed.text
    assert environment["key"] in {e["key"] for e in listed.json()["data"]}

    one = await guest_client.get(f"/api/environments/{environment['environment_id']}")
    assert one.status_code == 200, one.text
    assert one.json()["data"]["label"] == "Coast"

    regimes = await guest_client.get("/api/environments/regimes")
    assert regimes.status_code == 200, regimes.text
    keys = [regime["key"] for regime in regimes.json()["data"]]
    assert "monoterpene_ox" in keys
    assert keys == sorted(keys)


@pytest.mark.asyncio
async def test_a_guest_cannot_create_change_retire_or_delete(
    guest_client, editor_client, made
):
    environment = await _create(editor_client, made)
    path = f"/api/environments/{environment['environment_id']}"

    created = await guest_client.post(
        "/api/environments", json={"key": _key("x"), "label": "X"}
    )
    assert created.status_code == 403, created.text
    changed = await guest_client.patch(path, json={"label": "Elsewhere"})
    assert changed.status_code == 403, changed.text
    retired = await guest_client.patch(path, json={"retired": True})
    assert retired.status_code == 403, retired.text
    deleted = await guest_client.delete(path)
    assert deleted.status_code == 403, deleted.text

    stored = (await editor_client.get(path)).json()["data"]
    assert stored["label"] == "Coast"
    assert stored["retired"] is False


@pytest.mark.asyncio
async def test_an_editor_creates_changes_retires_and_deletes(editor_client, made):
    environment = await _create(editor_client, made)
    path = f"/api/environments/{environment['environment_id']}"

    assert (await editor_client.patch(path, json={"label": "Sea"})).status_code == 200
    assert (await editor_client.patch(path, json={"retired": True})).status_code == 200
    assert (await editor_client.delete(path)).status_code == 200
    assert (await editor_client.get(path)).status_code == 404


# ---------------------------------------------------------------------------
# What an environment holds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_environment_joins_a_key_to_a_context_and_regimes(editor_client, made):
    key = _key("forest")
    environment = await _create(
        editor_client,
        made,
        key=key,
        label="  A forest  ",
        # An alias of a context is stored by the context's own name.
        chemistry_context="ambient",
        regimes=["monoterpene_ox", "biogenic_soa"],
    )
    assert environment["key"] == key
    assert environment["label"] == "A forest"
    assert environment["chemistry_context"] == "ambient-air"
    assert environment["regimes"] == ["monoterpene_ox", "biogenic_soa"]
    assert environment["system"] is False
    assert environment["retired"] is False


@pytest.mark.asyncio
async def test_no_context_and_no_regimes_are_allowed(editor_client, made):
    environment = await _create(editor_client, made, chemistry_context="auto")
    assert environment["chemistry_context"] is None
    assert environment["regimes"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"key": "Coast"},
        {"key": "coast_line"},
        {"key": "coast-"},
        {"label": "   "},
        {"chemistry_context": "forest"},
        {"regimes": ["monoterpene_OH"]},
        {"regimes": ["ap_ox", "ap_ox"]},
        {"environment_id": "chosenbyclient01"},
    ],
)
async def test_what_the_registry_refuses_to_create(editor_client, made, body):
    body = {"key": _key("coast"), "label": "Coast", **body}
    response = await editor_client.post("/api/environments", json=body)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_a_key_is_taken_once_retired_or_not(editor_client, made):
    environment = await _create(editor_client, made)
    path = f"/api/environments/{environment['environment_id']}"
    await editor_client.patch(path, json={"retired": True})

    again = await editor_client.post(
        "/api/environments", json={"key": environment["key"], "label": "Again"}
    )
    assert again.status_code == 409, again.text


@pytest.mark.asyncio
async def test_a_change_sends_only_what_changes(editor_client, made):
    environment = await _create(
        editor_client, made, chemistry_context="chamber", regimes=["isoprene_ox"]
    )
    path = f"/api/environments/{environment['environment_id']}"

    relabelled = (await editor_client.patch(path, json={"label": "Sea"})).json()
    assert relabelled["data"]["label"] == "Sea"
    assert relabelled["data"]["chemistry_context"] == "chamber"
    assert relabelled["data"]["regimes"] == ["isoprene_ox"]

    cleared = await editor_client.patch(
        path, json={"chemistry_context": None, "regimes": []}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["chemistry_context"] is None
    assert cleared.json()["data"]["regimes"] == []

    for refused in (
        {"key": _key("other")},
        {"regimes": None},
        {"regimes": ["nope"]},
        {"chemistry_context": "forest"},
        {"label": ""},
    ):
        response = await editor_client.patch(path, json=refused)
        assert response.status_code == 422, (refused, response.text)
    stored = (await editor_client.get(path)).json()["data"]
    assert stored["key"] == environment["key"]
    assert stored["label"] == "Sea"


@pytest.mark.asyncio
async def test_a_retired_environment_leaves_the_list_and_comes_back(
    editor_client, made
):
    environment = await _create(editor_client, made)
    path = f"/api/environments/{environment['environment_id']}"

    retired = await editor_client.patch(path, json={"retired": True})
    assert retired.json()["data"]["retired"] is True
    listed = (await editor_client.get("/api/environments")).json()["data"]
    assert environment["key"] not in {e["key"] for e in listed}
    everything = (
        await editor_client.get("/api/environments?include_retired=true")
    ).json()["data"]
    assert environment["key"] in {e["key"] for e in everything}

    restored = await editor_client.patch(path, json={"retired": False})
    assert restored.json()["data"]["retired"] is False
    listed = (await editor_client.get("/api/environments")).json()["data"]
    assert environment["key"] in {e["key"] for e in listed}


# ---------------------------------------------------------------------------
# The environment field's values are the registry's keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fields_values_follow_the_registry(
    editor_client, made, async_session_factory
):
    second = await _create(editor_client, made, key=_key("zz-sea"))
    first = await _create(editor_client, made, key=_key("aa-coast"))
    values = await _field_values(async_session_factory)
    # Sorted, whatever order the environments were made in.
    assert values == sorted(values)
    assert {first["key"], second["key"]} <= set(values)

    # A retired environment's key stays: what was tagged with it keeps its
    # meaning.
    await editor_client.patch(
        f"/api/environments/{first['environment_id']}", json={"retired": True}
    )
    assert first["key"] in await _field_values(async_session_factory)

    deleted = await editor_client.delete(f"/api/environments/{first['environment_id']}")
    assert deleted.status_code == 200, deleted.text
    values = await _field_values(async_session_factory)
    assert first["key"] not in values
    assert second["key"] in values

    # And the field's own route still refuses to change them.
    field = (await editor_client.get("/api/metadata/fields")).json()["data"]
    (environment_field,) = [f for f in field if f["key"] == "environment"]
    assert environment_field["allowed_values"] == values
    refused = await editor_client.patch(
        f"/api/metadata/fields/{environment_field['metadata_field_id']}",
        json={"allowed_values": [*values, "elsewhere"]},
    )
    assert refused.status_code == 409, refused.text


# ---------------------------------------------------------------------------
# The shipped set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_start_seeds_the_shipped_set_and_a_second_changes_nothing(
    editor_client, made, async_session_factory
):
    created = await ensure_shipped_environments()
    assert created == [shipped.key for shipped in SHIPPED_ENVIRONMENTS]

    async with async_session_factory() as session:
        rows = {
            row.environment_id: row
            for row in (
                await session.scalars(
                    select(Environment).where(
                        Environment.environment_id.in_(_SHIPPED_IDS)
                    )
                )
            ).all()
        }
        field = await session.scalar(
            select(MetadataField).where(MetadataField.key == "environment")
        )
    for shipped in SHIPPED_ENVIRONMENTS:
        row = rows[shipped.environment_id]
        assert row.key == shipped.key
        assert row.label == shipped.label
        assert row.chemistry_context == shipped.chemistry_context
        assert row.regimes == list(shipped.regimes)
        assert row.system is True
        assert row.retired_utc is None
    assert set(field.allowed_values) >= {s.key for s in SHIPPED_ENVIRONMENTS}

    assert await ensure_shipped_environments() == []
    async with async_session_factory() as session:
        again = await session.scalar(
            select(MetadataField).where(MetadataField.key == "environment")
        )
        modified = {
            row.environment_id: row.modified_utc
            for row in (
                await session.scalars(
                    select(Environment).where(
                        Environment.environment_id.in_(_SHIPPED_IDS)
                    )
                )
            ).all()
        }
    assert again.allowed_values == field.allowed_values
    assert again.modified_utc == field.modified_utc
    assert modified == {i: row.modified_utc for i, row in rows.items()}


@pytest.mark.asyncio
async def test_a_shipped_environment_is_edited_and_retired_not_deleted(
    editor_client, made
):
    await ensure_shipped_environments()
    shipped = SHIPPED_ENVIRONMENTS[0]
    path = f"/api/environments/{shipped.environment_id}"

    deleted = await editor_client.delete(path)
    assert deleted.status_code == 409, deleted.text
    edited = await editor_client.patch(
        path, json={"label": "Taiga", "regimes": ["monoterpene_ox"]}
    )
    assert edited.status_code == 200, edited.text
    retired = await editor_client.patch(path, json={"retired": True})
    assert retired.status_code == 200, retired.text

    # A start keeps the deployment's edit and its retirement.
    assert await ensure_shipped_environments() == []
    stored = (await editor_client.get(path)).json()["data"]
    assert stored["label"] == "Taiga"
    assert stored["regimes"] == ["monoterpene_ox"]
    assert stored["retired"] is True
    assert stored["system"] is True


@pytest.mark.asyncio
async def test_a_deployments_own_environment_under_a_shipped_key_stays_its_own(
    editor_client, made
):
    shipped = SHIPPED_ENVIRONMENTS[0]
    own = await _create(editor_client, made, key=shipped.key, label="Our forest")

    created = await ensure_shipped_environments()

    assert shipped.key not in created
    stored = (
        await editor_client.get(f"/api/environments/{own['environment_id']}")
    ).json()["data"]
    assert stored["label"] == "Our forest"
    assert stored["system"] is False
    missing = await editor_client.get(f"/api/environments/{shipped.environment_id}")
    assert missing.status_code == 404
