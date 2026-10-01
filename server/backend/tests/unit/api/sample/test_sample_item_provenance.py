"""
How an ACQUISITION item records the rung that bound it.

Two seams, because the provenance crosses both and either could drop it
silently: the pipeline builds each item with its rung, and the bulk create
writes the column for every row it inserts
(``docs/dev/ingest_routing_and_splitting.md``, section 5.2).

The second one is worth its own test rather than being taken on trust. One
multi-row ``INSERT`` takes its column list from the first dictionary alone, so
a list mixing an item that carries provenance with one that does not would
either drop the column or be refused, depending on which came first - and the
first is the dangerous outcome, since nothing fails and the column is simply
empty.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from mascope_backend.api.controllers.sample.files.process.service import (
    ItemProvenance,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    AcquisitionItemCreate,
    SampleItemCreate,
)


_SVC = "mascope_backend.api.controllers.sample.files.process.service"
_ITEMS = "mascope_backend.api.controllers.sample.items.sample_items_controller"


def _sample_file():
    """A one-polarity file, as far as item creation reads one."""
    from datetime import datetime

    sample_file = MagicMock()
    sample_file.sample_file_id = "sf-001"
    sample_file.instrument = "instrument-A"
    sample_file.datetime = datetime(2026, 10, 1, 9, 30, 0)
    return sample_file


def _mode(mode_id="im-001", polarity="-"):
    mode = MagicMock()
    mode.ionization_mode_id = mode_id
    mode.ionization_mode_name = "Nitrate"
    mode.ionization_mode_polarity = polarity
    mode.diagnostic_collection_id = None
    mode.calibration_collection_id = None
    return mode


async def _items_created(modes, provenance):
    """The item models the pipeline would create for these modes."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        create_acquisition_batches_and_items,
    )

    with (
        patch(
            f"{_SVC}.get_or_create_acquisition_batch",
            new_callable=AsyncMock,
            return_value={"data": {"sample_batch_id": "sb-001"}, "created": True},
        ),
        patch(
            f"{_SVC}.create_sample_items", new_callable=AsyncMock
        ) as create_sample_items,
    ):
        create_sample_items.return_value = {"data": []}
        await create_acquisition_batches_and_items(
            sample_file=_sample_file(),
            dataset_id="ds-001",
            ionization_modes=modes,
            provenance=provenance,
        )
    return create_sample_items.await_args.kwargs["sample_items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rung", ["token", "explicit", "method"])
async def test_each_item_carries_the_rung_that_bound_the_file(rung):
    modes = [_mode("im-neg", "-"), _mode("im-pos", "+")]

    items = await _items_created(
        modes,
        {"im-neg": ItemProvenance(rung), "im-pos": ItemProvenance(rung)},
    )

    assert [item.ionization_mode_id for item in items] == ["im-neg", "im-pos"]
    assert all(isinstance(item, AcquisitionItemCreate) for item in items)
    assert [item.bound_by for item in items] == [rung, rung]
    # The binding row waits for the rung that reads one. Pinned so that
    # building it is a deliberate change rather than something a reader
    # assumes already happens.
    assert [item.method_binding_id for item in items] == [None, None]


@pytest.mark.asyncio
async def test_each_polarity_takes_its_own_provenance():
    """One file, two streams, two histories.

    A polarity-switching method is bound a polarity at a time and keyed a
    polarity at a time, so each item answers for itself - and after a
    re-process one polarity's mode can carry a rung the other's does not.
    """
    modes = [_mode("im-neg", "-"), _mode("im-pos", "+")]

    items = await _items_created(
        modes,
        {"im-neg": ItemProvenance("method", "mb-000000000001")},
    )

    assert [(item.bound_by, item.method_binding_id) for item in items] == [
        ("method", "mb-000000000001"),
        (None, None),
    ]


@pytest.mark.asyncio
async def test_a_rung_outside_the_vocabulary_is_refused():
    """The column is read by counting, so a typo must not be writable.

    "methods" would be written, and then be absent from every count of the
    rungs - the one way a column like this fails without anything failing.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await _items_created(
            [_mode("im-neg", "-")], {"im-neg": ItemProvenance("methods")}
        )


@pytest.mark.asyncio
async def test_the_insert_names_the_provenance_for_every_row():
    """A row with no rung is written as having none, not left out."""
    from mascope_backend.api.controllers.sample.items.sample_items_controller import (
        create_sample_items,
    )

    common = {
        "sample_batch_id": "sb-001",
        "sample_file_id": "sf-001",
        "sample_item_attributes": {},
        "polarity": "-",
        "ionization_mode_id": "im-001",
        # Supplied, so that nothing reads the file to compute them.
        "tic": 1.0,
        "t0": 0.0,
        "t1": 1.0,
    }
    items = [
        AcquisitionItemCreate(
            sample_item_name="routed by its method",
            sample_item_type="ACQUISITION",
            bound_by="method",
            method_binding_id="mb-000000000001",
            **common,
        ),
        SampleItemCreate(
            sample_item_name="made by a person",
            sample_item_type="UNKNOWN",
            **common,
        ),
    ]

    sample_file = MagicMock()
    sample_file.sample_file_id = "sf-001"
    # A plain MagicMock for the result, not an AsyncMock child: scalars() and
    # all() are synchronous, and an AsyncMock would hand the caller coroutines.
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [sample_file]
    session = AsyncMock()
    session.execute.return_value = rows
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)

    created = [MagicMock(sample_item_id="si-0001"), MagicMock(sample_item_id="si-0002")]
    with (
        patch(f"{_ITEMS}.async_session", return_value=context),
        patch(f"{_ITEMS}.gen_id", side_effect=["si-0001", "si-0002"]),
        patch(
            f"{_ITEMS}.fetch_affected_sample_data", new_callable=AsyncMock
        ) as affected,
        patch(
            f"{_ITEMS}.update_sample_batches_modified_timestamp",
            new_callable=AsyncMock,
        ),
    ):
        affected.return_value = MagicMock(
            affected_sample_batch_ids=["sb-001"], affected_samples=created
        )
        await create_sample_items(sample_items=items)

    insert_statement = session.execute.await_args_list[-1].args[0]
    bound = insert_statement.compile(dialect=postgresql.dialect()).params
    assert bound["bound_by_m0"] == "method"
    assert bound["method_binding_id_m0"] == "mb-000000000001"
    assert bound["bound_by_m1"] is None
    assert bound["method_binding_id_m1"] is None
