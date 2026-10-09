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
from mascope_backend.api.controllers.sample.files.process.streams import (
    StoreStreams,
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
        # Pass the provenance through untouched: whether a binding row still
        # exists is its own question, tested on its own below.
        patch(
            f"{_SVC}._with_live_bindings",
            new_callable=AsyncMock,
            side_effect=lambda held: held,
        ),
        # A file with no census, as the fixture file has none: nothing to
        # point an item at, which is its own question, tested in
        # test_stream_items
        patch(
            f"{_SVC}.read_store_streams",
            new_callable=AsyncMock,
            return_value=StoreStreams(),
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
@pytest.mark.parametrize(
    "live, expected",
    [
        (["mb-live00000001"], "mb-live00000001"),
        ([], None),
    ],
    ids=["the binding is still there", "it was deleted mid-run"],
)
async def test_a_binding_deleted_mid_run_costs_the_link_not_the_file(live, expected):
    """``ON DELETE SET NULL`` does not reach a row being inserted a minute later.

    The id is read before the items are written - at the rung, and for a
    re-process during validation, which can be minutes earlier. A binding
    deleted in between would fail the whole file's processing on the foreign
    key, which costs the file its samples; dropping the id costs one item its
    link to a binding that no longer exists. The rung stays either way,
    because "bound by its acquisition method" is still true of that item.
    """
    from mascope_backend.api.controllers.sample.files.process.service import (
        _with_live_bindings,
    )

    rows = MagicMock()
    rows.all.return_value = live
    session = AsyncMock()
    session.scalars.return_value = rows
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch(f"{_SVC}.async_session", return_value=context):
        kept = await _with_live_bindings(
            {"im-001": ItemProvenance("method", "mb-live00000001")}
        )

    assert kept == {"im-001": ItemProvenance("method", expected)}


@pytest.mark.asyncio
async def test_no_binding_ids_means_no_query():
    """Every file routed by a token or a person, which is nearly all of them."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        _with_live_bindings,
    )

    with patch(f"{_SVC}.async_session") as session_factory:
        kept = await _with_live_bindings({"im-001": ItemProvenance("token")})

    assert kept == {"im-001": ItemProvenance("token")}
    session_factory.assert_not_called()


def _stored_item(**overrides):
    """A stored ACQUISITION item, as the update controller reads one back."""
    from datetime import datetime, timezone

    from mascope_backend.db import SampleItem

    fields = {
        "sample_item_id": "si-0001",
        "sample_batch_id": "sb-001",
        "sample_file_id": "sf-001",
        "sample_item_name": "2026-10-01 09:30:00",
        "sample_item_type": "ACQUISITION",
        "sample_item_attributes": {},
        "filter_id": None,
        "tic": 1.0,
        "polarity": "-",
        "ionization_mode_id": "im-token",
        "t0": 0.0,
        "t1": 1.0,
        "locked": 1,
        "bound_by": "token",
        "method_binding_id": "mb-000000000001",
        "sample_item_utc_created": datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    return SampleItem(**(fields | overrides))


async def _updated(stored, **changes):
    """Apply an update to a stored item, as the PATCH route would."""
    from mascope_backend.api.controllers.sample.items.sample_items_controller import (
        update_sample_item,
    )
    from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
        SampleItemUpdate,
    )

    payload = {
        "sample_batch_id": stored.sample_batch_id,
        "sample_file_id": stored.sample_file_id,
        "sample_item_name": stored.sample_item_name,
        # ACQUISITION is system-managed, so an update must name a type a
        # person may set - which is itself part of why the stale provenance
        # is reachable at all.
        "sample_item_type": "UNKNOWN",
        "sample_item_attributes": {},
        "tic": stored.tic,
        "polarity": stored.polarity,
        "ionization_mode_id": stored.ionization_mode_id,
        "t0": stored.t0,
        "t1": stored.t1,
    }
    session = AsyncMock()
    session.get.return_value = stored
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    with patch(f"{_ITEMS}.async_session", return_value=context):
        await update_sample_item(
            sample_item_id=stored.sample_item_id,
            sample_item=SampleItemUpdate(**(payload | changes)),
        )
    return stored


@pytest.mark.asyncio
async def test_a_hand_set_mode_clears_the_rung_that_decided_the_old_one():
    """Otherwise a token gets the credit for a mode somebody typed.

    And on a method item the binding would name a different mode from the
    item, which is the shape a re-pointed binding leaves behind - so a hand
    edit would be read as one by the report that looks for them.
    """
    stored = await _updated(_stored_item(), ionization_mode_id="im-by-hand")

    assert stored.ionization_mode_id == "im-by-hand"
    assert (stored.bound_by, stored.method_binding_id) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"polarity": "+"}, {"sample_file_id": "sf-another"}],
    ids=["another-polarity", "another-file"],
)
async def test_a_changed_file_or_polarity_clears_the_stream_the_item_read(changes):
    """The stream was a stream of the old file and polarity. Cleared, the
    item reads what a hand-made item reads, where left alone it would read a
    spectrum of the other polarity or be refused as a stream of another
    file, as a database error."""
    stored = _stored_item()
    stored.stream_id = "st-composite"

    stored = await _updated(stored, **changes)

    assert stored.stream_id is None


@pytest.mark.asyncio
async def test_an_update_that_leaves_the_file_and_polarity_alone_keeps_the_stream():
    stored = _stored_item()
    stored.stream_id = "st-composite"

    stored = await _updated(stored, sample_item_name="renamed")

    assert stored.stream_id == "st-composite"


@pytest.mark.asyncio
async def test_an_update_that_leaves_the_mode_alone_keeps_the_rung():
    """Renaming a sample says nothing about how its chemistry was decided."""
    stored = await _updated(_stored_item(), sample_item_name="renamed")

    assert stored.sample_item_name == "renamed"
    assert (stored.bound_by, stored.method_binding_id) == (
        "token",
        "mb-000000000001",
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
