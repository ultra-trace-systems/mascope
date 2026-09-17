"""
Integration tests for the chemistry profile preview.

A launcher names what a run's ``auto`` profile and context will resolve to
before the run starts. What these pin is the read the resolution depends on: a
sample's ionization mode, only the mechanisms at the sample's own polarity, a
sample with no mode resolving on its polarity alone, and a batch counting its
samples per distinct answer.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)

#: The notations the fingerprint reads. Exact, since detection matches them as
#: stored, so they are looked up before being created: another test may have
#: seeded the same row, and the column is unique.
_UREA = ("+(CH4N2O)H+", "+")
_PROTON = ("+H+ (profile preview test)", "+")
_BROMIDE = ("+Br-", "-")


async def _mechanism_id(session, notation: str, polarity: str) -> str:
    existing = (
        await session.execute(
            select(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism == notation
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.ionization_mechanism_id
    mechanism_id = gen_id()
    session.add(
        IonizationMechanism(
            ionization_mechanism_id=mechanism_id,
            ionization_mechanism=notation,
            ionization_mechanism_polarity=polarity,
        )
    )
    await session.flush()
    return mechanism_id


@pytest_asyncio.fixture
async def preview_batch(async_session_factory, pa_test_data):
    """A batch of four positive samples on three footings.

    Two on a urea mode, one on a mode whose only diagnostic mechanism is the
    negative bromide one, and one with no mode at all. Seeded afresh for each
    test, so every name carries its row's id: dataset names are unique within
    a workspace.
    """
    dataset_id = gen_id()
    batch_id = gen_id()
    urea_items = [gen_id(), gen_id()]
    bromide_item = gen_id()
    modeless_item = gen_id()
    async with async_session_factory() as session:
        urea = await _mechanism_id(session, *_UREA)
        proton = await _mechanism_id(session, *_PROTON)
        bromide = await _mechanism_id(session, *_BROMIDE)
        urea_mode = gen_id()
        bromide_mode = gen_id()
        session.add_all(
            [
                IonizationMode(
                    ionization_mode_id=urea_mode,
                    ionization_mode_name=f"Profile preview urea mode {urea_mode}",
                    ionization_mode_polarity="+",
                    ionization_mechanism_ids=[proton, urea],
                ),
                # A mode may list a mechanism of the other polarity; a run reads
                # only the ones at the sample's own, and so must the preview.
                IonizationMode(
                    ionization_mode_id=bromide_mode,
                    ionization_mode_name=f"Profile preview mixed mode {bromide_mode}",
                    ionization_mode_polarity="+",
                    ionization_mechanism_ids=[proton, bromide],
                ),
                Dataset(
                    dataset_id=dataset_id,
                    workspace_id=pa_test_data["workspace_id"],
                    dataset_name=f"Profile preview dataset {dataset_id}",
                    dataset_utc_created=_NOW,
                ),
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=dataset_id,
                    sample_batch_name=f"Profile preview batch {batch_id}",
                    sample_batch_utc_created=_NOW,
                ),
            ]
        )
        await session.flush()
        for item_id, mode_id in [
            (urea_items[0], urea_mode),
            (urea_items[1], urea_mode),
            (bromide_item, bromide_mode),
            (modeless_item, None),
        ]:
            file_id = gen_id()
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"profile-preview-{file_id}.raw",
                    instrument="orbi-test",
                    datetime=datetime(2026, 9, 17, 12, 0, 0),
                    datetime_utc=_NOW,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="+",
                )
            )
            await session.flush()
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=file_id,
                    ionization_mode_id=mode_id,
                    sample_item_name=f"Profile preview {item_id}",
                    sample_item_type="sample",
                    polarity="+",
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    return {
        "batch": batch_id,
        "urea": urea_items[0],
        "bromide": bromide_item,
        "modeless": modeless_item,
    }


def _only(body: dict) -> dict:
    assert body["results"] == 1
    return body["data"][0]


@pytest.mark.asyncio
async def test_auto_names_the_profile_the_samples_mode_carries(
    guest_client, preview_batch
):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{preview_batch['urea']}/profile-preview"
    )
    assert response.status_code == 200
    preview = _only(response.json())
    assert preview["profile"] == "UR"
    assert preview["profile_label"] == "Uronium (urea) CIMS"
    assert preview["requested_profile"] == "auto"
    assert preview["context"] == "uronium"
    assert preview["requested_context"] == "auto"
    assert preview["polarity"] == "+"
    assert preview["samples"] == 1


@pytest.mark.asyncio
async def test_a_mechanism_of_the_other_polarity_is_not_read(
    guest_client, preview_batch
):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{preview_batch['bromide']}/profile-preview"
    )
    assert response.status_code == 200
    # The mode lists the bromide mechanism, but the sample is positive.
    assert _only(response.json())["profile"] == "ESI_POS"


@pytest.mark.asyncio
async def test_a_sample_with_no_mode_resolves_on_its_polarity(
    guest_client, preview_batch
):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{preview_batch['modeless']}/profile-preview"
    )
    assert response.status_code == 200
    preview = _only(response.json())
    assert preview["profile"] == "ESI_POS"
    assert preview["context"] == "none"


@pytest.mark.asyncio
async def test_a_batch_counts_its_samples_per_answer(guest_client, preview_batch):
    response = await guest_client.get(
        f"/api/peak-assignments/batch/{preview_batch['batch']}/profile-preview"
    )
    assert response.status_code == 200
    body = response.json()
    assert [(row["profile"], row["samples"]) for row in body["data"]] == [
        ("ESI_POS", 2),
        ("UR", 2),
    ]


@pytest.mark.asyncio
async def test_a_named_profile_and_context_apply_to_every_sample(
    guest_client, preview_batch
):
    response = await guest_client.get(
        f"/api/peak-assignments/batch/{preview_batch['batch']}/profile-preview",
        params={"profile": "BR", "context": "chamber"},
    )
    assert response.status_code == 200
    preview = _only(response.json())
    assert preview["profile"] == "BR"
    assert preview["requested_profile"] == "BR"
    assert preview["context"] == "chamber"
    # What a launcher warns on: a negative profile over positive samples.
    assert preview["profile_polarity"] == "-"
    assert preview["polarity"] == "+"
    assert preview["samples"] == 4


@pytest.mark.asyncio
async def test_a_named_profile_takes_its_own_context_under_auto(
    guest_client, preview_batch
):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{preview_batch['urea']}/profile-preview",
        params={"profile": "NO3"},
    )
    assert response.status_code == 200
    preview = _only(response.json())
    assert preview["profile"] == "NO3"
    assert preview["context"] == "ambient-air"


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"profile": "XYZ"}, {"context": "moon"}])
async def test_a_name_the_run_config_refuses_is_refused(
    guest_client, preview_batch, params
):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{preview_batch['urea']}/profile-preview",
        params=params,
    )
    assert response.status_code == 422


@pytest_asyncio.fixture
async def foreign_sample(async_session_factory, test_users):
    """A sample in a workspace none of the test users belongs to."""
    workspace_id = gen_id()
    dataset_id = gen_id()
    batch_id = gen_id()
    file_id = gen_id()
    item_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"Profile preview elsewhere {workspace_id}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        await session.flush()
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name=f"Profile preview elsewhere {dataset_id}",
                dataset_utc_created=_NOW,
            )
        )
        await session.flush()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Profile preview elsewhere {batch_id}",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=f"profile-preview-elsewhere-{file_id}.raw",
                instrument="orbi-test",
                datetime=datetime(2026, 9, 17, 12, 0, 0),
                datetime_utc=_NOW,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        await session.flush()
        session.add(
            SampleItem(
                sample_item_id=item_id,
                sample_batch_id=batch_id,
                sample_file_id=file_id,
                sample_item_name=f"Profile preview elsewhere {item_id}",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    return {"sample": item_id, "batch": batch_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["sample", "batch"])
async def test_a_sample_or_batch_the_user_cannot_read_is_refused(
    guest_client, foreign_sample, scope
):
    response = await guest_client.get(
        f"/api/peak-assignments/{scope}/{foreign_sample[scope]}/profile-preview"
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["sample", "batch"])
async def test_a_sample_or_batch_that_does_not_exist_is_not_found(
    owner_client, pa_test_data, scope
):
    # A superuser passes the access check whatever the id names, so the preview
    # itself has to tell a missing id from a scope with nothing in it.
    response = await owner_client.get(
        f"/api/peak-assignments/{scope}/no-such-{scope}/profile-preview"
    )
    assert response.status_code == 404
    # The message names the id that was asked for, not a lookup it fell into.
    assert f"no-such-{scope}" in response.text


@pytest.mark.asyncio
async def test_a_batch_with_no_samples_resolves_to_nothing(
    guest_client, async_session_factory, pa_test_data
):
    dataset_id = gen_id()
    batch_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=pa_test_data["workspace_id"],
                dataset_name=f"Profile preview empty {dataset_id}",
                dataset_utc_created=_NOW,
            )
        )
        await session.flush()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Profile preview empty {batch_id}",
                sample_batch_utc_created=_NOW,
            )
        )
        await session.commit()

    response = await guest_client.get(
        f"/api/peak-assignments/batch/{batch_id}/profile-preview"
    )
    assert response.status_code == 200
    assert response.json()["results"] == 0
    assert response.json()["data"] == []
