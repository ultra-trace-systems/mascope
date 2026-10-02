"""
A match copy's progress packet weighs the same however many samples it copies.

``copy_sample_items`` reports a match copy as it goes - six packets per sample,
one per table copied (``copy_sample_items_match_data``) - and every Socket.IO
emit is published to every backend process through Redis pub/sub. A packet that
carried a record per copied sample would make each packet the size of the batch
and the whole copy the square of it: copying a batch of a few thousand samples
would push gigabytes through pub/sub, and deep-copy the list on the event loop
before every one of tens of thousands of emits.

Driven through the undecorated controller with the database stubbed out: the
source samples and the created items are scripted, and the match tables hold
nothing to copy. The real progress path runs down to the emit, which is
captured.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.sample.items import sample_items_controller


_CTRL = "mascope_backend.api.controllers.sample.items.sample_items_controller"
_COPY = "mascope_backend.api.controllers.sample.lib.sample_items_copy"
_SVC = "mascope_backend.socket.notifications.service"

TARGET_BATCH = "target-batch-01"

# One packet per table copied, plus the one announcing the sample:
# MatchIsotope, MatchIon, MatchCompound, MatchCollection and MatchSample.
PACKETS_PER_SAMPLE = 6

# What one progress packet may weigh, serialized as it is emitted: the process,
# the type, a message and the batch. A few hundred bytes, whatever the batch.
PACKET_BUDGET_BYTES = 1024


def _source_sample(index: int) -> SimpleNamespace:
    """A sample to copy, with the fields the copy reads off it."""
    return SimpleNamespace(
        sample_item_id=f"source-{index:09d}",
        sample_batch_id="source-batch-01",
        sample_file_id=f"file-{index:011d}",
        sample_item_name=f"Sample {index}",
        sample_item_type="ACQUISITION",
        sample_item_attributes={},
        filter_id=None,
        tic=1.0e6,
        polarity="-",
        ionization_mode_id="mode-0000000001",
        t0=0.0,
        t1=60.0,
        filename=f"instrumentX_file{index:05d}",
    )


class _Session:
    """An async session whose queries return ``rows`` and whose writes vanish."""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _statement):
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._rows)
        return result

    def add(self, _record):
        pass

    async def commit(self):
        pass


async def _copy_and_capture(samples: int) -> list:
    """Copy ``samples`` samples, matches included, and return every packet emitted."""
    sources = [_source_sample(index) for index in range(samples)]
    created = {
        "data": [
            {
                "sample_item_id": f"copy-{index:011d}",
                "filename": source.filename,
                "polarity": source.polarity,
            }
            for index, source in enumerate(sources)
        ]
    }
    with (
        patch(f"{_CTRL}.async_session", lambda: _Session(sources)),
        patch(
            f"{_CTRL}.fetch_sample_batch",
            AsyncMock(return_value=SimpleNamespace(sample_batch_name="Target")),
        ),
        patch(f"{_CTRL}.create_sample_items", AsyncMock(return_value=created)),
        # The match tables: nothing to copy, so only the reporting runs.
        patch(f"{_COPY}.async_session", lambda: _Session([])),
        patch(f"{_SVC}.emit_user_notification", AsyncMock()) as emit,
    ):
        await sample_items_controller.copy_sample_items.__wrapped__(
            sample_item_ids=[source.sample_item_id for source in sources],
            sample_batch_id=TARGET_BATCH,
            always_copy_matches=True,
            user_id=1,
            process_id="copy-process",
        )
    return [call.args[0] for call in emit.await_args_list]


def _weight(packet) -> int:
    return len(json.dumps(packet.model_dump(exclude_none=True)).encode())


@pytest.mark.asyncio
async def test_every_step_of_every_sample_is_reported():
    """The capture is the copy's own reporting, step by step - not a test artefact."""
    packets = await _copy_and_capture(samples=4)

    assert len(packets) == 4 * PACKETS_PER_SAMPLE
    assert {packet.type for packet in packets} == {"copy_sample_items"}
    assert {packet.status for packet in packets} == {"pending"}


@pytest.mark.asyncio
async def test_a_packet_does_not_grow_with_the_batch():
    small = await _copy_and_capture(samples=3)
    large = await _copy_and_capture(samples=60)

    heaviest = max(_weight(packet) for packet in large)
    assert heaviest < PACKET_BUDGET_BYTES
    # Twenty times the samples, the same packet: what differs is the digits of
    # the sample count in the message and of the progress value.
    assert heaviest - max(_weight(packet) for packet in small) < 64


@pytest.mark.asyncio
async def test_a_packet_names_the_batch_and_nothing_per_sample():
    packets = await _copy_and_capture(samples=5)

    assert {json.dumps(packet.data, sort_keys=True) for packet in packets} == {
        json.dumps({"sample_batch_id": TARGET_BATCH})
    }
