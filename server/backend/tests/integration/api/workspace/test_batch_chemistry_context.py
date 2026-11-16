"""
Tests: a sample batch names the chemistry context its samples were drawn from.

Step 3.6 of the assignment quality plan: a dataset or a batch names its
context once, and a peak assignment run on ``auto`` takes it before the
reagent profile's default, the batch's before the dataset's. What these pin is
that the batch routes store it by the context's own name, keep it through an
update that leaves it out, clear it on null or ``auto``, and refuse a context
nothing would resolve.
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
