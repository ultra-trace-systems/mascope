"""Unit tests: the declared rung in the auto-processing pipeline.

Rung 0 of the binding ladder: a file whose acquisition record names its
chemistry is bound by it, ahead of its file name. What is pinned here is how
the pipeline uses that answer - which rung the items record, what the file's
method is taught, what the status says, and that a record which binds nothing
costs the file none of the rungs below.

Everything around the binding is scripted, and each test stops the pipeline
where it has seen enough.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.sample.files.process import service
from mascope_backend.api.controllers.sample.files.process.service import ItemProvenance
from mascope_backend.api.controllers.sample.files.process.streams import (
    StoreStreams,
    StreamRows,
)
from mascope_backend.api.new.ionization.modes.util import NoTokenMatchError


_SVC = "mascope_backend.api.controllers.sample.files.process.service"

RECORD = {"schema": "mascope-acquisition/1", "ionization": "NO3"}
UNANSWERED = (
    "Its acquisition record names the chemistry 'NO3', but no ionization mode "
    "of this instrument has that token in a polarity the file holds"
)


class _SeenEnough(Exception):
    """Raised by a stand-in to end the pipeline where a test stops looking."""


def _mode(mode_id="im-nitrate", name="Nitrate", polarity="-"):
    mode = MagicMock()
    mode.ionization_mode_id = mode_id
    mode.ionization_mode_name = name
    mode.ionization_mode_polarity = polarity
    return mode


def _sample_file():
    sample_file = MagicMock()
    sample_file.sample_file_id = "sf-001"
    sample_file.instrument = "Orbi-Lab2"
    sample_file.filename = "Orbi-Lab2_2026.10.07-12h00m00s_run_0042"
    sample_file.polarity = "-"
    sample_file.datetime = datetime(2026, 10, 7, 12, 0, 0)
    sample_file.datetime_utc = datetime(2026, 10, 7, 9, 0, 0, tzinfo=timezone.utc)
    return sample_file


@pytest.fixture
def pipeline():
    """The pipeline's surroundings, scripted; the stand-ins by name."""
    with (
        patch(f"{_SVC}.fetch_sample_file", new_callable=AsyncMock) as fetch,
        patch(f"{_SVC}.read_scan_streams", new_callable=AsyncMock) as census,
        patch(f"{_SVC}.pooled_streams_note", return_value=None),
        patch(f"{_SVC}.get_acquisition_dataset", new_callable=AsyncMock) as dataset,
        patch(f"{_SVC}._modes_its_record_declares", new_callable=AsyncMock) as declares,
        patch(
            f"{_SVC}.resolve_ionization_modes_by_tokens", new_callable=AsyncMock
        ) as tokens,
        patch(f"{_SVC}.routes_on_method_binding", return_value=False),
        patch(f"{_SVC}.learn_method_bindings", new_callable=AsyncMock) as learn,
        patch(
            f"{_SVC}.create_acquisition_batches_and_items", new_callable=AsyncMock
        ) as create,
        patch(f"{_SVC}.record_processing_status", new_callable=AsyncMock) as status,
    ):
        fetch.return_value = _sample_file()
        census.return_value = []
        dataset.return_value = {"data": {"dataset_id": "ds-001"}}
        create.return_value = ([], [], StreamRows(), StoreStreams())
        yield MagicMock(
            declares=declares, tokens=tokens, learn=learn, create=create, status=status
        )


async def _run(**arguments):
    return await service._auto_process_sample_file(sample_file_id="sf-001", **arguments)


@pytest.mark.asyncio
async def test_a_file_is_bound_by_what_its_record_declares(pipeline):
    nitrate = _mode()
    pipeline.declares.return_value = ([nitrate], None)
    pipeline.status.side_effect = _SeenEnough

    with pytest.raises(_SeenEnough):
        await _run()

    # Ahead of the file name, which is not even read.
    pipeline.tokens.assert_not_called()
    created = pipeline.create.call_args.kwargs
    assert created["ionization_modes"] == [nitrate]
    assert created["provenance"] == {"im-nitrate": ItemProvenance("declared")}
    # What the status the file gets says bound it.
    assert pipeline.status.call_args.args[1].value == "bound"
    assert pipeline.status.call_args.args[2] == (
        "Bound to 'Nitrate' (-) by its acquisition record."
    )


@pytest.mark.asyncio
async def test_a_declaration_teaches_the_files_method_under_its_own_name(pipeline):
    nitrate = _mode()
    pipeline.declares.return_value = ([nitrate], None)
    pipeline.create.side_effect = _SeenEnough

    with pytest.raises(_SeenEnough):
        await _run()

    learned = pipeline.learn.call_args
    assert learned.args[1] == [nitrate]
    assert learned.kwargs["source"] == "declared"


@pytest.mark.asyncio
async def test_a_file_with_no_declaration_is_bound_by_its_name_as_before(pipeline):
    bromide = _mode("im-bromide", "Bromide")
    pipeline.declares.return_value = ([], None)
    pipeline.tokens.return_value = [bromide]
    pipeline.status.side_effect = _SeenEnough

    with pytest.raises(_SeenEnough):
        await _run()

    assert pipeline.create.call_args.kwargs["provenance"] == {
        "im-bromide": ItemProvenance("token")
    }
    assert pipeline.learn.call_args.kwargs["source"] == "token"
    assert pipeline.status.call_args.args[2] == (
        "Bound by file-name token to 'Bromide' (-)."
    )


@pytest.mark.asyncio
async def test_a_declaration_that_binds_nothing_leaves_the_name_its_turn(pipeline):
    """One rung that did not answer is not a file that cannot be bound."""
    bromide = _mode("im-bromide", "Bromide")
    pipeline.declares.return_value = ([], UNANSWERED)
    pipeline.tokens.return_value = [bromide]
    pipeline.create.side_effect = _SeenEnough

    with pytest.raises(_SeenEnough):
        await _run()

    assert pipeline.create.call_args.kwargs["provenance"] == {
        "im-bromide": ItemProvenance("token")
    }


@pytest.mark.asyncio
async def test_a_parked_file_says_what_its_record_named(pipeline):
    """Whoever picks its chemistry reads what the instrument said it was."""
    pipeline.declares.return_value = ([], UNANSWERED)
    pipeline.tokens.side_effect = NoTokenMatchError(
        "No ionization mode tokens found for file x. Configure tokens in "
        "ionization settings"
    )

    result = await _run()

    assert result["status"] == "parked"
    pipeline.create.assert_not_called()
    assert pipeline.status.call_args.args[1].value == "needs_chemistry"
    assert pipeline.status.call_args.args[2] == (
        f"{UNANSWERED}. No ionization mode tokens found for file x. Configure "
        "tokens in ionization settings."
    )


@pytest.mark.asyncio
async def test_an_ambiguous_name_parks_beside_the_declaration_too(pipeline):
    pipeline.declares.return_value = ([], UNANSWERED)
    pipeline.tokens.side_effect = ValueError("2 modes match polarity -")

    await _run()

    assert pipeline.status.call_args.args[2] == (
        f"{UNANSWERED}. 2 modes match polarity -."
    )


@pytest.mark.asyncio
async def test_a_file_that_parks_with_no_declaration_reads_as_it_did(pipeline):
    pipeline.declares.return_value = ([], None)
    pipeline.tokens.side_effect = ValueError("2 modes match polarity -")

    await _run()

    assert pipeline.status.call_args.args[2] == "2 modes match polarity -."


@pytest.mark.asyncio
async def test_a_persons_choice_is_not_second_guessed_by_the_record(pipeline):
    """Somebody who re-binds a file has seen what its record said."""
    chosen = _mode("im-chosen", "Chosen")
    pipeline.create.side_effect = _SeenEnough

    with (
        patch(f"{_SVC}.fetch_ionization_modes", new_callable=AsyncMock),
        patch(f"{_SVC}.choose_ionization_modes", return_value=[chosen]),
        pytest.raises(_SeenEnough),
    ):
        await _run(ionization_mode_ids=["im-chosen"])

    pipeline.declares.assert_not_called()
    assert pipeline.create.call_args.kwargs["provenance"] == {
        "im-chosen": ItemProvenance("explicit")
    }


# ---------------------------------------------------------------------------
# Reading the declaration
# ---------------------------------------------------------------------------


def _session_holding(record):
    """A stand-in for ``async_session`` whose query answers with ``record``."""
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=record)
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context)


@pytest.mark.parametrize(
    "record",
    [None, {"schema": "mascope-acquisition/1"}, {**RECORD, "ionization": ""}],
)
@pytest.mark.asyncio
async def test_a_file_whose_record_names_no_chemistry_declares_nothing(record):
    with (
        patch(f"{_SVC}.async_session", _session_holding(record)),
        patch(
            f"{_SVC}.resolve_ionization_modes_by_declaration", new_callable=AsyncMock
        ) as resolve,
    ):
        declared = await service._modes_its_record_declares(_sample_file())

    assert declared == ([], None)
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_the_chemistry_a_record_names_is_resolved_for_its_file():
    nitrate, sample_file = _mode(), _sample_file()
    with (
        patch(f"{_SVC}.async_session", _session_holding(RECORD)),
        patch(
            f"{_SVC}.resolve_ionization_modes_by_declaration",
            new_callable=AsyncMock,
            return_value=([nitrate], None),
        ) as resolve,
    ):
        declared = await service._modes_its_record_declares(sample_file)

    assert declared == ([nitrate], None)
    resolve.assert_awaited_once_with(sample_file, "NO3")


@pytest.mark.asyncio
async def test_a_declaration_that_binds_nothing_says_what_it_named():
    with (
        patch(f"{_SVC}.async_session", _session_holding(RECORD)),
        patch(
            f"{_SVC}.resolve_ionization_modes_by_declaration",
            new_callable=AsyncMock,
            return_value=(
                [],
                "no ionization mode of this instrument has that token in a "
                "polarity the file holds",
            ),
        ),
    ):
        declared = await service._modes_its_record_declares(_sample_file())

    assert declared == ([], UNANSWERED)
