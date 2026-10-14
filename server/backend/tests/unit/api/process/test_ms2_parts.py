"""
MS2 parts: what a file's scan census says, and the items made from it.

A file nothing binds to a chemistry has no samples, and its fragmentation
scans are read through a sample. So a file of fragmentation scans alone,
which no chemistry would change anything about, is filed as MS2 parts - one
item per polarity that holds any, with no ionization mode
(``process/ms2_parts.py``).

Everything that decides whether, and for which polarities, is read from the
census the converter stored, and from nothing else. These tests pin that
reading, one decision each: what counts as a fragmentation scan, which
polarities hold one, what a file with no census says (nothing), and what
tells a file of fragmentation scans alone from a blank measurement.

The pipeline's side - when parts are filed and how the file's status reads
afterwards - is in ``test_auto_process.py``.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.sample.files.process import census
from mascope_backend.api.controllers.sample.files.process.census import (
    fragmentation_polarities,
    holds_only_fragmentation_scans,
    records_ms1_scans,
    records_only_fragmentation_scans,
)
from mascope_backend.api.controllers.sample.files.process.ms2_parts import (
    file_fragmentation_scans,
    filed_sentence,
    ms2_batch_name,
)
from mascope_backend.api.controllers.sample.files.process.service import (
    acquisition_batch_name,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    AcquisitionItemCreate,
)


_MS2_PARTS = "mascope_backend.api.controllers.sample.files.process.ms2_parts"


def _stream(ms_order, polarity="+"):
    """A census stream, as far as these readers look at one."""
    return {
        "key": f"FTMS {polarity} p NSI Full ms{ms_order}",
        "signature": {"ms_order": ms_order, "polarity": polarity},
    }


# ---------------------------------------------------------------------------
# Reading the census
# ---------------------------------------------------------------------------


def test_a_polarity_with_an_ms2_stream_holds_fragmentation_scans():
    census = [_stream(1), _stream(2)]

    assert fragmentation_polarities(census, "+") == ["+"]


def test_a_polarity_with_ms1_streams_only_holds_none():
    assert fragmentation_polarities([_stream(1)], "+") == []


def test_each_polarity_answers_for_itself():
    """A file that fragments in one polarity is filed for that one alone."""
    census = [_stream(1, "+"), _stream(1, "-"), _stream(2, "-")]

    assert fragmentation_polarities(census, "+-") == ["-"]


def test_the_answer_comes_in_the_files_polarity_order():
    """Items are made in it, as the bound ones are."""
    census = [_stream(2, "-"), _stream(2, "+")]

    assert fragmentation_polarities(census, "+-") == ["+", "-"]


def test_any_order_above_one_is_fragmentation():
    assert fragmentation_polarities([_stream(3)], "+") == ["+"]


@pytest.mark.parametrize("census", [None, []])
def test_a_file_with_no_census_says_nothing(census):
    """Converted before the census existed, or by a reader that takes none.

    Such a file waits for a chemistry as it always did: the census is the
    only thing read here, so that deciding never opens a raw file.
    """
    assert fragmentation_polarities(census, "+") == []


def test_a_polarity_the_file_does_not_hold_is_left_out():
    """An item is made per polarity of the file, and it has no such scans."""
    assert fragmentation_polarities([_stream(2, "-")], "+") == []


def test_a_file_that_records_no_polarity_holds_none():
    assert fragmentation_polarities([_stream(2)], None) == []


@pytest.mark.parametrize("order", [None, "2", 2.0, True])
def test_an_ms_order_that_is_not_a_whole_number_is_not_fragmentation(order):
    """A props file nothing in Mascope wrote must not make an item."""
    assert fragmentation_polarities([_stream(order)], "+") == []


def test_a_census_polarity_that_is_not_text_names_no_polarity():
    """And does not fail the file, which is the part that matters."""
    census = [{"key": "odd", "signature": {"ms_order": 2, "polarity": ["+"]}}]

    assert fragmentation_polarities(census, "+") == []


def test_ms1_scans_are_recorded_where_the_census_holds_an_ms1_stream():
    assert records_ms1_scans([_stream(2), _stream(1)]) is True
    assert records_ms1_scans([_stream(2)]) is False


def test_a_true_ms_order_is_not_an_ms1_stream():
    """``True == 1`` in Python, and a flag is not an MS order."""
    assert records_ms1_scans([_stream(True)]) is False


def test_fragmentation_scans_alone_is_streams_with_no_ms1_among_them():
    assert records_only_fragmentation_scans([_stream(2), _stream(3)]) is True
    assert records_only_fragmentation_scans([_stream(1), _stream(2)]) is False


@pytest.mark.parametrize("census", [None, []])
def test_no_census_is_not_fragmentation_scans_alone(census):
    """It is what a blank measurement's file carries, and a TOF file's.

    Read the other way, every blank would be reported as an acquisition of
    fragmentation scans.
    """
    assert records_only_fragmentation_scans(census) is False


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------


def _sample_file():
    sample_file = MagicMock()
    sample_file.sample_file_id = "sf-001"
    sample_file.instrument = "instrument-A"
    sample_file.datetime = datetime(2026, 9, 7, 12, 27, 59)
    return sample_file


def test_the_batch_is_named_for_the_files_own_day():
    """Dated off the instrument-local time, as a bound item's batch is."""
    assert ms2_batch_name(_sample_file()) == (
        "2026-09-07 MS2 acquisition (no chemistry)"
    )


@pytest.mark.parametrize(
    "mode_name",
    [
        "MS2",
        "MS2 acquisition (no chemistry)",
        "MS2 acquisition",
        "",
        "(no chemistry)",
    ],
)
def test_no_chemistry_shares_the_batch_of_ms2_parts(mode_name):
    """A mode's name is free text, and a batch is found by its name.

    Sharing it, a day's bound samples and MS2 parts would share a batch, and
    whichever came first would set its target collections - leaving the
    bound samples nothing to be matched against. No mode name can make a
    bound batch's name, which ends in " acquisition", the MS2 batch's.
    """
    sample_file = _sample_file()

    assert acquisition_batch_name(sample_file, mode_name) != ms2_batch_name(sample_file)


async def _filed(polarities):
    """File these polarities, with the batch and item writers recorded."""

    async def batch_of(sample_batch):
        return {
            "data": {
                "sample_batch_id": f"sb-{sample_batch.polarity}",
                "sample_batch_name": sample_batch.sample_batch_name,
                "polarity": sample_batch.polarity,
            },
            "created": True,
        }

    with (
        patch(
            f"{_MS2_PARTS}.get_or_create_acquisition_batch", side_effect=batch_of
        ) as get_batch,
        patch(f"{_MS2_PARTS}.create_sample_items", new_callable=AsyncMock) as create,
    ):
        create.return_value = {"data": [{"sample_item_id": "si-001"}]}
        samples, batches = await file_fragmentation_scans(
            _sample_file(), "ds-001", polarities
        )
    return samples, batches, get_batch, create


@pytest.mark.asyncio
async def test_each_polarity_is_filed_in_the_days_ms2_batch_of_its_polarity():
    _, batches, get_batch, _ = await _filed(["+", "-"])

    asked = [call.kwargs["sample_batch"] for call in get_batch.call_args_list]
    assert [
        (b.dataset_id, b.sample_batch_name, b.polarity, b.sample_batch_type)
        for b in asked
    ] == [
        ("ds-001", "2026-09-07 MS2 acquisition (no chemistry)", "+", "ACQUISITION"),
        ("ds-001", "2026-09-07 MS2 acquisition (no chemistry)", "-", "ACQUISITION"),
    ]
    assert [batch["sample_batch_id"] for batch in batches] == ["sb-+", "sb--"]


@pytest.mark.asyncio
async def test_the_batch_carries_no_target_collections():
    """An ACQUISITION batch's collections are its mode's, and there is none."""
    _, _, get_batch, _ = await _filed(["+"])

    assert get_batch.call_args.kwargs["sample_batch"].target_collection_ids == []


@pytest.mark.asyncio
async def test_the_item_has_no_ionization_mode_and_no_rung():
    """Nothing bound it, which is what NULL in both columns already means."""
    _, _, _, create = await _filed(["+"])

    (item,) = create.await_args.kwargs["sample_items"]
    assert isinstance(item, AcquisitionItemCreate)
    assert item.ionization_mode_id is None
    assert (item.bound_by, item.method_binding_id) == (None, None)


@pytest.mark.asyncio
async def test_the_item_is_the_pipelines_own():
    """ACQUISITION, so that a rebuild replaces it with the bound one."""
    _, _, _, create = await _filed(["+", "-"])

    items = create.await_args.kwargs["sample_items"]
    assert [
        (i.sample_batch_id, i.polarity, i.sample_item_type, i.sample_file_id)
        for i in items
    ] == [
        ("sb-+", "+", "ACQUISITION", "sf-001"),
        ("sb--", "-", "ACQUISITION", "sf-001"),
    ]
    assert {item.sample_item_name for item in items} == {"2026-09-07 12:27:59"}


@pytest.mark.asyncio
async def test_the_item_covers_the_whole_acquisition():
    """Its TIC and window are left to item creation, which spans every scan."""
    _, _, _, create = await _filed(["+"])

    (item,) = create.await_args.kwargs["sample_items"]
    assert (item.tic, item.t0, item.t1) == (None, None, None)


@pytest.mark.asyncio
async def test_what_was_made_is_handed_back():
    samples, batches, _, _ = await _filed(["+"])

    assert samples == [{"sample_item_id": "si-001"}]
    assert (
        batches[0]["sample_batch_name"] == "2026-09-07 MS2 acquisition (no chemistry)"
    )


def test_the_sentence_names_the_batch_and_its_polarity():
    batches = [
        {
            "sample_batch_name": "2026-09-07 MS2 acquisition (no chemistry)",
            "polarity": "+",
        }
    ]

    assert filed_sentence(batches) == (
        "filed as an MS2 sample in '2026-09-07 MS2 acquisition (no chemistry)' (+)"
    )


def test_the_sentence_names_both_batches_of_a_two_polarity_file():
    batches = [
        {
            "sample_batch_name": "2026-09-07 MS2 acquisition (no chemistry)",
            "polarity": "+",
        },
        {
            "sample_batch_name": "2026-09-07 MS2 acquisition (no chemistry)",
            "polarity": "-",
        },
    ]

    assert filed_sentence(batches) == (
        "filed as MS2 samples in '2026-09-07 MS2 acquisition (no chemistry)' "
        "(+) and '2026-09-07 MS2 acquisition (no chemistry)' (-)"
    )


# ---------------------------------------------------------------------------
# Reading a stored file's census
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "only"),
    [
        ([_stream(2)], True),
        ([_stream(1), _stream(2)], False),
        ([], False),
        (None, False),
    ],
    ids=["MS2 only", "MS1 too", "no census", "unreadable props"],
)
async def test_a_stored_file_is_read_off_its_census(stored, only):
    """What the MS1 reads of a sample ask before answering it empty.

    An unreadable ``.props`` reads as None, and is no more a file with no
    MS1 scans than one with no census is.
    """
    with patch.object(
        census, "read_scan_streams", AsyncMock(return_value=stored)
    ) as read:
        assert await holds_only_fragmentation_scans("instrument-A_file") is only

    read.assert_awaited_once_with("instrument-A_file")
