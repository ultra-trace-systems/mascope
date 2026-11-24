"""Integration tests: the metadata field registry, ``/api/metadata/fields``.

A registry field is shared reference data (decision 14 of
``docs/dev/sample_filters_and_metadata.md``, MAS-104): a guest reads, the
global editor role or higher creates, changes, retires and deletes. What the
service guards is the meaning of values written under a field: its key never
changes, a field in use (an attribute template selects it) is retired rather
than deleted and keeps its type, and the field Mascope ships keeps its key,
type and allowed values and stays.

The integration database is made by ``create_all``, not by the migrations, so
the shipped ``environment`` row the migration seeds is inserted here; the
migration's own seed is pinned in ``tests/migrations``.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import AttributeTemplate, AttributeTemplateField, MetadataField
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)


def _key(stem: str) -> str:
    """A key no other test uses: the database is shared by the session."""
    return f"{stem}_{gen_id(6).lower()}"


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
async def cleanup(async_session_factory):
    """Delete the templates and fields a test made, templates first."""
    made = {"templates": [], "fields": []}
    yield made
    async with async_session_factory() as session:
        if made["templates"]:
            await session.execute(
                delete(AttributeTemplate).where(
                    AttributeTemplate.attribute_template_id.in_(made["templates"])
                )
            )
        if made["fields"]:
            await session.execute(
                delete(MetadataField).where(
                    MetadataField.metadata_field_id.in_(made["fields"])
                )
            )
        await session.commit()


async def _create(client, cleanup, **body) -> dict:
    body = {"label": "Site", "type": "text", **body}
    body.setdefault("key", _key("site"))
    response = await client.post("/api/metadata/fields", json=body)
    assert response.status_code == 201, response.text
    field = response.json()["data"]
    cleanup["fields"].append(field["metadata_field_id"])
    return field


async def _template(client, cleanup, fields, template=()) -> dict:
    response = await client.post(
        "/api/attribute_templates",
        json={
            "name": f"Form {gen_id(6)}",
            "type": "sample_item",
            "template": list(template),
            "fields": fields,
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()["data"]
    cleanup["templates"].append(record["attribute_template_id"])
    return record


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_guest_reads_the_registry(guest_client, editor_client, cleanup):
    field = await _create(editor_client, cleanup)

    listed = await guest_client.get("/api/metadata/fields")
    assert listed.status_code == 200, listed.text
    assert field["key"] in {f["key"] for f in listed.json()["data"]}

    one = await guest_client.get(f"/api/metadata/fields/{field['metadata_field_id']}")
    assert one.status_code == 200, one.text
    assert one.json()["data"]["label"] == "Site"


@pytest.mark.asyncio
async def test_a_guest_cannot_create_change_retire_or_delete(
    guest_client, editor_client, cleanup
):
    field = await _create(editor_client, cleanup)
    path = f"/api/metadata/fields/{field['metadata_field_id']}"

    created = await guest_client.post(
        "/api/metadata/fields", json={"key": _key("x"), "label": "X", "type": "text"}
    )
    assert created.status_code == 403, created.text
    changed = await guest_client.patch(path, json={"label": "Elsewhere"})
    assert changed.status_code == 403, changed.text
    retired = await guest_client.patch(path, json={"retired": True})
    assert retired.status_code == 403, retired.text
    deleted = await guest_client.delete(path)
    assert deleted.status_code == 403, deleted.text

    stored = await editor_client.get(path)
    assert stored.json()["data"]["label"] == "Site"
    assert stored.json()["data"]["retired"] is False


@pytest.mark.asyncio
async def test_an_editor_creates_changes_retires_and_deletes(editor_client, cleanup):
    """The global editor role is enough for every write: no admin gate."""
    field = await _create(
        editor_client,
        cleanup,
        label="  Inlet height ",
        type="number",
        unit="m",
        description="Height of the inlet above ground.",
    )
    assert field["label"] == "Inlet height"
    assert field["unit"] == "m"
    assert field["system"] is False
    assert field["retired"] is False
    assert field["in_use"] is False
    path = f"/api/metadata/fields/{field['metadata_field_id']}"

    changed = await editor_client.patch(path, json={"label": "Inlet", "unit": "cm"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["label"] == "Inlet"
    assert changed.json()["data"]["unit"] == "cm"
    assert changed.json()["data"]["description"] == field["description"]

    retired = await editor_client.patch(path, json={"retired": True})
    assert retired.status_code == 200, retired.text
    assert retired.json()["data"]["retired"] is True
    assert retired.json()["data"]["retired_utc"] is not None

    deleted = await editor_client.delete(path)
    assert deleted.status_code == 200, deleted.text
    gone = await editor_client.get(path)
    assert gone.status_code == 404, gone.text


# ---------------------------------------------------------------------------
# What a field may be
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_key_is_unique_retired_or_not(editor_client, cleanup):
    field = await _create(editor_client, cleanup)
    await editor_client.patch(
        f"/api/metadata/fields/{field['metadata_field_id']}", json={"retired": True}
    )
    again = await editor_client.post(
        "/api/metadata/fields",
        json={"key": field["key"], "label": "Site again", "type": "text"},
    )
    assert again.status_code == 409, again.text
    assert "restore it" in again.text


@pytest.mark.asyncio
async def test_a_key_never_changes(editor_client, cleanup):
    field = await _create(editor_client, cleanup)
    path = f"/api/metadata/fields/{field['metadata_field_id']}"
    response = await editor_client.patch(path, json={"key": _key("renamed")})
    assert response.status_code == 422, response.text
    assert (await editor_client.get(path)).json()["data"]["key"] == field["key"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key", ["Site", "1site", "site-name", "site name", "", "s" * 65]
)
async def test_a_key_is_lower_case_letters_digits_and_underscores(editor_client, key):
    response = await editor_client.post(
        "/api/metadata/fields", json={"key": key, "label": "Site", "type": "text"}
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_an_unknown_type_is_refused(editor_client):
    response = await editor_client.post(
        "/api/metadata/fields",
        json={"key": _key("site"), "label": "Site", "type": "colour"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_a_blank_label_is_refused(editor_client, cleanup):
    created = await editor_client.post(
        "/api/metadata/fields",
        json={"key": _key("site"), "label": "  ", "type": "text"},
    )
    assert created.status_code == 422, created.text

    field = await _create(editor_client, cleanup)
    cleared = await editor_client.patch(
        f"/api/metadata/fields/{field['metadata_field_id']}", json={"label": " "}
    )
    assert cleared.status_code == 422, cleared.text


@pytest.mark.asyncio
async def test_an_enum_needs_allowed_values(editor_client, cleanup):
    for values in (None, []):
        response = await editor_client.post(
            "/api/metadata/fields",
            json={
                "key": _key("inlet"),
                "label": "Inlet",
                "type": "enum",
                "allowed_values": values,
            },
        )
        assert response.status_code == 422, response.text

    field = await _create(
        editor_client, cleanup, type="enum", allowed_values=[" ambient", "chamber "]
    )
    assert field["allowed_values"] == ["ambient", "chamber"]

    emptied = await editor_client.patch(
        f"/api/metadata/fields/{field['metadata_field_id']}",
        json={"allowed_values": []},
    )
    assert emptied.status_code == 422, emptied.text


@pytest.mark.asyncio
async def test_a_tags_field_may_leave_its_values_open(editor_client, cleanup):
    field = await _create(editor_client, cleanup, type="tags", allowed_values=[])
    assert field["allowed_values"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [["a", "a"], ["a", " "]])
async def test_allowed_values_are_unique_and_not_blank(editor_client, values):
    response = await editor_client.post(
        "/api/metadata/fields",
        json={
            "key": _key("inlet"),
            "label": "Inlet",
            "type": "enum",
            "allowed_values": values,
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field_type", ["text", "number", "datetime"])
async def test_only_a_choice_has_allowed_values(editor_client, field_type):
    response = await editor_client.post(
        "/api/metadata/fields",
        json={
            "key": _key("site"),
            "label": "Site",
            "type": field_type,
            "allowed_values": ["a"],
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_a_type_change_away_from_a_choice_drops_its_values(
    editor_client, cleanup
):
    field = await _create(editor_client, cleanup, type="enum", allowed_values=["a"])
    changed = await editor_client.patch(
        f"/api/metadata/fields/{field['metadata_field_id']}", json={"type": "text"}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["type"] == "text"
    assert changed.json()["data"]["allowed_values"] is None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_retired_field_is_listed_only_when_asked_for(editor_client, cleanup):
    active = await _create(editor_client, cleanup, label="Campaign")
    retired = await _create(editor_client, cleanup, label="Old campaign")
    await editor_client.patch(
        f"/api/metadata/fields/{retired['metadata_field_id']}", json={"retired": True}
    )

    default = await editor_client.get("/api/metadata/fields")
    keys = {f["key"] for f in default.json()["data"]}
    assert active["key"] in keys
    assert retired["key"] not in keys

    everything = await editor_client.get(
        "/api/metadata/fields", params={"include_retired": "true"}
    )
    listed = {f["key"]: f for f in everything.json()["data"]}
    assert listed[retired["key"]]["retired"] is True
    assert listed[active["key"]]["retired"] is False
    assert everything.json()["results"] == len(everything.json()["data"])


@pytest.mark.asyncio
async def test_a_restored_field_is_listed_again(editor_client, cleanup):
    field = await _create(editor_client, cleanup)
    path = f"/api/metadata/fields/{field['metadata_field_id']}"
    await editor_client.patch(path, json={"retired": True})
    restored = await editor_client.patch(path, json={"retired": False})
    assert restored.json()["data"]["retired"] is False
    assert restored.json()["data"]["retired_utc"] is None
    listed = await editor_client.get("/api/metadata/fields")
    assert field["key"] in {f["key"] for f in listed.json()["data"]}


@pytest.mark.asyncio
async def test_the_listing_is_ordered_by_label(editor_client, cleanup):
    stem = gen_id(6)
    for label in (f"zeta {stem}", f"Alpha {stem}", f"beta {stem}"):
        await _create(editor_client, cleanup, label=label)
    listed = await editor_client.get("/api/metadata/fields")
    ours = [f["label"] for f in listed.json()["data"] if f["label"].endswith(stem)]
    assert ours == [f"Alpha {stem}", f"beta {stem}", f"zeta {stem}"]


# ---------------------------------------------------------------------------
# A field in use
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_field_in_use_refuses_deletion_and_is_retired(editor_client, cleanup):
    field = await _create(editor_client, cleanup)
    await _template(editor_client, cleanup, [field["key"]])
    path = f"/api/metadata/fields/{field['metadata_field_id']}"

    assert (await editor_client.get(path)).json()["data"]["in_use"] is True
    deleted = await editor_client.delete(path)
    assert deleted.status_code == 409, deleted.text
    assert "Retire it instead" in deleted.text
    assert (await editor_client.get(path)).status_code == 200

    retired = await editor_client.patch(path, json={"retired": True})
    assert retired.status_code == 200, retired.text
    assert retired.json()["data"]["retired"] is True


@pytest.mark.asyncio
async def test_a_field_in_use_keeps_its_type(editor_client, cleanup):
    field = await _create(editor_client, cleanup)
    await _template(editor_client, cleanup, [field["key"]])
    path = f"/api/metadata/fields/{field['metadata_field_id']}"

    changed = await editor_client.patch(path, json={"type": "number"})
    assert changed.status_code == 409, changed.text
    assert (await editor_client.get(path)).json()["data"]["type"] == "text"

    # Sending its own type back is no change.
    echoed = await editor_client.patch(path, json={"type": "text", "label": "Place"})
    assert echoed.status_code == 200, echoed.text


@pytest.mark.asyncio
async def test_a_field_a_deleted_template_selected_can_be_deleted(
    editor_client, cleanup
):
    field = await _create(editor_client, cleanup)
    template = await _template(editor_client, cleanup, [field["key"]])
    removed = await editor_client.delete(
        f"/api/attribute_templates/{template['attribute_template_id']}"
    )
    assert removed.status_code == 200, removed.text

    path = f"/api/metadata/fields/{field['metadata_field_id']}"
    assert (await editor_client.get(path)).json()["data"]["in_use"] is False
    deleted = await editor_client.delete(path)
    assert deleted.status_code == 200, deleted.text


# ---------------------------------------------------------------------------
# The field Mascope ships
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_shipped_field_stays(editor_client, system_field):
    path = f"/api/metadata/fields/{system_field.metadata_field_id}"

    deleted = await editor_client.delete(path)
    assert deleted.status_code == 409, deleted.text
    retired = await editor_client.patch(path, json={"retired": True})
    assert retired.status_code == 409, retired.text
    retyped = await editor_client.patch(path, json={"type": "enum"})
    assert retyped.status_code == 409, retyped.text
    revalued = await editor_client.patch(path, json={"allowed_values": ["forest"]})
    assert revalued.status_code == 409, revalued.text

    stored = (await editor_client.get(path)).json()["data"]
    assert stored["system"] is True
    assert stored["retired"] is False
    assert stored["type"] == "tags"
    assert stored["allowed_values"] == []


@pytest.mark.asyncio
async def test_the_shipped_field_takes_a_new_label(editor_client, system_field):
    path = f"/api/metadata/fields/{system_field.metadata_field_id}"
    try:
        changed = await editor_client.patch(
            path,
            json={
                "label": "Sampled environment",
                # The pane sends the stored values back with every save.
                "type": "tags",
                "allowed_values": [],
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["label"] == "Sampled environment"
    finally:
        await editor_client.patch(path, json={"label": "Environment"})


@pytest.mark.asyncio
async def test_a_null_type_leaves_the_type_alone(editor_client, cleanup, system_field):
    """``"type": null`` means unchanged, on the shipped field as on any other."""
    plain = await _create(editor_client, cleanup)
    changed = await editor_client.patch(
        f"/api/metadata/fields/{plain['metadata_field_id']}",
        json={"type": None, "label": "Sampling site"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["type"] == "text"
    assert changed.json()["data"]["label"] == "Sampling site"

    path = f"/api/metadata/fields/{system_field.metadata_field_id}"
    try:
        shipped = await editor_client.patch(
            path, json={"type": None, "label": "Sampled environment"}
        )
        assert shipped.status_code == 200, shipped.text
        assert shipped.json()["data"]["type"] == "tags"
        assert shipped.json()["data"]["label"] == "Sampled environment"
    finally:
        await editor_client.patch(path, json={"label": "Environment"})


# ---------------------------------------------------------------------------
# Attribute templates select registry fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_template_selects_fields_in_order(editor_client, guest_client, cleanup):
    site = await _create(editor_client, cleanup, label="Site")
    inlet = await _create(editor_client, cleanup, label="Inlet")
    template = await _template(
        editor_client,
        cleanup,
        [inlet["key"], site["key"]],
        template=[{"label": "Stage", "required": False, "value": ""}],
    )
    assert template["fields"] == [inlet["key"], site["key"]]
    assert template["template"][0]["label"] == "Stage"

    one = await guest_client.get(
        f"/api/attribute_templates/{template['attribute_template_id']}"
    )
    assert one.json()["data"]["fields"] == [inlet["key"], site["key"]]
    listed = await guest_client.get("/api/attribute_templates")
    by_id = {t["attribute_template_id"]: t for t in listed.json()["data"]}
    assert by_id[template["attribute_template_id"]]["fields"] == [
        inlet["key"],
        site["key"],
    ]


@pytest.mark.asyncio
async def test_a_template_of_labels_alone_keeps_working(editor_client, cleanup):
    """Existing templates hold labels only: batch annotations, as before."""
    response = await editor_client.post(
        "/api/attribute_templates",
        json={
            "name": f"Labels {gen_id(6)}",
            "type": "sample_item",
            "template": [{"label": "Group", "required": True, "value": "A"}],
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()["data"]
    cleanup["templates"].append(record["attribute_template_id"])
    assert record["fields"] == []
    assert record["template"] == [{"label": "Group", "required": True, "value": "A"}]


@pytest.mark.asyncio
async def test_an_empty_template_is_refused(editor_client, cleanup):
    created = await editor_client.post(
        "/api/attribute_templates",
        json={"name": "Empty", "type": "sample_item", "template": [], "fields": []},
    )
    assert created.status_code == 422, created.text

    field = await _create(editor_client, cleanup)
    template = await _template(editor_client, cleanup, [field["key"]])
    emptied = await editor_client.patch(
        f"/api/attribute_templates/{template['attribute_template_id']}",
        json={"name": template["name"], "type": "sample_item", "fields": []},
    )
    assert emptied.status_code == 422, emptied.text


@pytest.mark.asyncio
async def test_a_template_refuses_an_unknown_or_repeated_key(editor_client, cleanup):
    field = await _create(editor_client, cleanup)
    for fields in ([_key("nowhere")], [field["key"], field["key"]]):
        response = await editor_client.post(
            "/api/attribute_templates",
            json={"name": "Form", "type": "sample_item", "fields": fields},
        )
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_a_retired_field_stays_in_a_template_but_is_not_added(
    editor_client, cleanup
):
    kept = await _create(editor_client, cleanup, label="Kept")
    other = await _create(editor_client, cleanup, label="Other")
    template = await _template(editor_client, cleanup, [kept["key"]])
    for field in (kept, other):
        await editor_client.patch(
            f"/api/metadata/fields/{field['metadata_field_id']}",
            json={"retired": True},
        )
    path = f"/api/attribute_templates/{template['attribute_template_id']}"
    body = {"name": template["name"], "type": "sample_item"}

    renamed = await editor_client.patch(path, json={**body, "fields": [kept["key"]]})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["fields"] == [kept["key"]]

    added = await editor_client.patch(
        path, json={**body, "fields": [kept["key"], other["key"]]}
    )
    assert added.status_code == 422, added.text
    created = await editor_client.post(
        "/api/attribute_templates", json={**body, "fields": [other["key"]]}
    )
    assert created.status_code == 422, created.text


@pytest.mark.asyncio
async def test_a_template_change_without_fields_keeps_them(editor_client, cleanup):
    field = await _create(editor_client, cleanup)
    template = await _template(editor_client, cleanup, [field["key"]])
    changed = await editor_client.patch(
        f"/api/attribute_templates/{template['attribute_template_id']}",
        json={
            "name": "Renamed",
            "type": "sample_item",
            "template": [{"label": "Stage"}],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["name"] == "Renamed"
    assert changed.json()["data"]["fields"] == [field["key"]]


@pytest.mark.asyncio
async def test_a_template_reorders_and_drops_fields(editor_client, cleanup):
    first = await _create(editor_client, cleanup, label="First")
    second = await _create(editor_client, cleanup, label="Second")
    template = await _template(editor_client, cleanup, [first["key"], second["key"]])
    path = f"/api/attribute_templates/{template['attribute_template_id']}"
    body = {"name": template["name"], "type": "sample_item"}

    reordered = await editor_client.patch(
        path, json={**body, "fields": [second["key"], first["key"]]}
    )
    assert reordered.json()["data"]["fields"] == [second["key"], first["key"]]

    dropped = await editor_client.patch(path, json={**body, "fields": [second["key"]]})
    assert dropped.json()["data"]["fields"] == [second["key"]]
    released = await editor_client.get(
        f"/api/metadata/fields/{first['metadata_field_id']}"
    )
    assert released.json()["data"]["in_use"] is False


@pytest.mark.asyncio
async def test_a_template_reads_its_fields_by_position(
    editor_client, async_session_factory, cleanup
):
    """Not by the order the rows happen to be stored in."""
    first = await _create(editor_client, cleanup, label="First")
    second = await _create(editor_client, cleanup, label="Second")
    template = await _template(editor_client, cleanup, [first["key"]])
    template_id = template["attribute_template_id"]
    async with async_session_factory() as session:
        await session.execute(
            delete(AttributeTemplateField).where(
                AttributeTemplateField.attribute_template_id == template_id
            )
        )
        await session.commit()
        # The later position stored first.
        for field, position in ((second, 1), (first, 0)):
            session.add(
                AttributeTemplateField(
                    attribute_template_id=template_id,
                    metadata_field_id=field["metadata_field_id"],
                    position=position,
                )
            )
            await session.commit()
    one = await editor_client.get(f"/api/attribute_templates/{template_id}")
    assert one.json()["data"]["fields"] == [first["key"], second["key"]]
