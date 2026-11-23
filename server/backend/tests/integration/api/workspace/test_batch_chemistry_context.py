"""
Tests: a sample batch names the chemistry context its samples were drawn from.

Step 3.6 of the assignment quality plan: a dataset or a batch names its
context once, and a peak assignment run on ``auto`` takes it before the
reagent profile's default, the batch's before the dataset's. What these pin is
that the batch routes store it by the context's own name, keep it through an
update that leaves it out, clear it on null or ``auto``, and refuse a context
nothing would resolve. A copy of the batch keeps it, and keeps the batch's
other assignment inputs too: its assignment settings and its seeding.
"""

import pytest


@pytest.mark.asyncio
async def test_a_batch_names_its_chemistry_context(editor_client, alpha_dataset):
    created = await editor_client.post(
        "/api/sample/batches",
        json={
            "dataset_id": alpha_dataset,
            "sample_batch_name": "Chamber batch",
            "target_collection_ids": [],
            "chemistry_context": "flow-tube",
        },
    )
    assert created.status_code == 201
    batch_id = created.json()["data"]["sample_batch_id"]
    url = f"/api/sample/batches/{batch_id}"
    assert (await editor_client.get(url)).json()["data"][
        "chemistry_context"
    ] == "chamber"

    # A field left out of an update keeps it
    renamed = await editor_client.patch(url, json={"sample_batch_name": "Renamed"})
    assert renamed.status_code == 200
    assert (await editor_client.get(url)).json()["data"][
        "chemistry_context"
    ] == "chamber"

    changed = await editor_client.patch(url, json={"chemistry_context": "indoor"})
    assert changed.status_code == 200
    assert (await editor_client.get(url)).json()["data"][
        "chemistry_context"
    ] == "indoor-air"

    for cleared in (None, "auto"):
        response = await editor_client.patch(url, json={"chemistry_context": cleared})
        assert response.status_code == 200
        assert (await editor_client.get(url)).json()["data"][
            "chemistry_context"
        ] is None


@pytest.mark.asyncio
async def test_an_unknown_chemistry_context_is_refused(editor_client, alpha_batch):
    response = await editor_client.patch(
        f"/api/sample/batches/{alpha_batch}", json={"chemistry_context": "mars"}
    )
    assert response.status_code in (400, 422)
    stored = (await editor_client.get(f"/api/sample/batches/{alpha_batch}")).json()
    assert stored["data"]["chemistry_context"] is None


async def _copy(editor_client, dataset_id, batch_id, name):
    """The copy's listed row, read back after the copy route ran."""
    response = await editor_client.post(
        f"/api/sample/batches/{batch_id}/copy",
        json={"dataset_id": dataset_id, "sample_batch_name": name},
    )
    assert response.status_code == 202
    listed = await editor_client.get(
        "/api/sample/batches", params={"dataset_id": dataset_id}
    )
    copies = [b for b in listed.json()["data"] if b["sample_batch_name"] == name]
    assert len(copies) == 1
    return copies[0]


async def _copy_and_read(editor_client, dataset_id, batch_id, name):
    copied = await _copy(editor_client, dataset_id, batch_id, name)
    return copied["chemistry_context"]


@pytest.mark.asyncio
async def test_a_copied_batch_keeps_its_chemistry_context(
    editor_client, alpha_dataset, alpha_batch
):
    url = f"/api/sample/batches/{alpha_batch}"
    assert (await editor_client.get(url)).json()["data"]["chemistry_context"] is None
    assert (
        await _copy_and_read(editor_client, alpha_dataset, alpha_batch, "Plain copy")
        is None
    )

    named = await editor_client.patch(url, json={"chemistry_context": "chamber"})
    assert named.status_code == 200
    assert (
        await _copy_and_read(editor_client, alpha_dataset, alpha_batch, "Named copy")
        == "chamber"
    )


@pytest.mark.asyncio
async def test_a_copied_batch_keeps_its_assignment_settings(
    editor_client, alpha_dataset, alpha_batch
):
    """A copy keeps the settings as the original stores them, field for
    field: a field the original leaves to the engine's default stays left out,
    null stays null, and the copy's own settings change apart from its
    original's."""
    url = f"/api/sample/batches/{alpha_batch}"
    assert (await editor_client.get(url)).json()["data"]["assignment_settings"] is None
    plain = await _copy(editor_client, alpha_dataset, alpha_batch, "Default copy")
    assert plain["assignment_settings"] is None

    response = await editor_client.patch(
        url,
        json={
            "assignment_settings": {
                "mz_precision_ppm": 2.5,
                "formula_ranges": "C0-40 H0-80 O0-20",
            }
        },
    )
    assert response.status_code == 200, response.text
    saved = {"mz_precision_ppm": 2.5, "formula_ranges": "C0-40 H0-80 O0-20"}
    assert response.json()["data"]["assignment_settings"] == saved

    copied = await _copy(editor_client, alpha_dataset, alpha_batch, "Tuned copy")
    assert copied["assignment_settings"] == saved
    copy_url = f"/api/sample/batches/{copied['sample_batch_id']}"
    assert (await editor_client.get(copy_url)).json()["data"][
        "assignment_settings"
    ] == saved

    # Changing the copy's settings leaves the original's as they were.
    changed = await editor_client.patch(
        copy_url, json={"assignment_settings": {"max_alternatives": 3}}
    )
    assert changed.status_code == 200, changed.text
    assert (await editor_client.get(copy_url)).json()["data"][
        "assignment_settings"
    ] == {"max_alternatives": 3}
    assert (await editor_client.get(url)).json()["data"]["assignment_settings"] == (
        saved
    )


@pytest.mark.asyncio
async def test_a_copied_batch_keeps_its_seeding(
    editor_client, alpha_dataset, alpha_batch
):
    url = f"/api/sample/batches/{alpha_batch}"
    assert (await editor_client.get(url)).json()["data"]["seed_batch_peaks"] is False
    unseeded = await _copy(editor_client, alpha_dataset, alpha_batch, "Unseeded copy")
    assert unseeded["seed_batch_peaks"] is False

    seeded = await editor_client.patch(url, json={"seed_batch_peaks": True})
    assert seeded.status_code == 200, seeded.text
    copied = await _copy(editor_client, alpha_dataset, alpha_batch, "Seeded copy")
    assert copied["seed_batch_peaks"] is True
