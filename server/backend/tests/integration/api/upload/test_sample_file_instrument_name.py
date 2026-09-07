"""
Integration tests: the instrument name a sample file may be updated to.

``POST /api/sample/files`` has always run the name through
``validate_instrument_name``; ``PATCH /api/sample/files/{id}`` did not, so the
one write path that can *change* an instrument was also the one that could
write a name the rules reject.

A rejected name is not merely untidy. Every sweep that walks the stored
instruments validates each name it finds - ``create_acquisition_datasets``
does it once per instrument on every upload that introduces a new one - so a
single bad row makes that sweep raise for the whole deployment, after the
uploaded file's own row has already been committed.

The empty string is covered too: ``instrument`` is required on
``SampleFileUpdate``, so the route's "is the instrument being changed" guard
only ever skips an empty one, which is exactly the value that must not reach
the column.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id


#: Valid under ``validate_instrument_name``: letters, digits and hyphens only.
INSTRUMENT = "patch-orbi-name"

#: Names the rules reject, each for its own reason.
PADDED = " patch-orbi-name "
UNDERSCORED = "patch_orbi_name"
EMPTY = ""


@pytest_asyncio.fixture
async def stored_file(async_session_factory):
    """A sample file with a valid instrument name, removed afterwards.

    Written straight to the table rather than through the upload route: the
    route's own validation is not what these tests are about, and going
    through it would drag in conversion and dataset side effects.
    """
    file_id = gen_id(16)
    filename = f"{INSTRUMENT}_20260101_0000_{file_id}.raw"
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=filename,
                instrument=INSTRUMENT,
                instrument_type="orbi",
                datetime=datetime(2026, 1, 1, 0, 0),
                datetime_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[0, 500],
                polarity="+",
            )
        )
        await session.commit()

    yield {"id": file_id, "filename": filename}

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == file_id)
        )
        await session.commit()


def _body(stored: dict, instrument: str) -> dict:
    """A complete update body, changing only the instrument.

    ``SampleFileUpdate`` requires the descriptive fields as well, so a body
    carrying the instrument alone is rejected by pydantic before the route
    runs - which would make these tests pass for the wrong reason. The
    filename is carried over unchanged because it is unique.
    """
    return {
        "filename": stored["filename"],
        "instrument": instrument,
        "datetime": "2026-01-01T00:00:00",
        "datetime_utc": "2026-01-01T00:00:00Z",
        "length": 60.0,
        "range": [0, 500],
        "polarity": "+",
    }


async def _instrument_of(async_session_factory, file_id: str) -> str:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleFile.instrument).where(
                    SampleFile.sample_file_id == file_id
                )
            )
        ).scalar_one()


@pytest.mark.parametrize(
    ("instrument", "why"),
    [
        (PADDED, "surrounding whitespace"),
        (UNDERSCORED, "an underscore, which separates filename fields"),
        (EMPTY, "no name at all"),
    ],
)
@pytest.mark.asyncio
async def test_update_refuses_an_invalid_instrument_name(
    admin_client, async_session_factory, stored_file, instrument, why
):
    """A name the rules reject is refused, and the stored one is untouched.

    The refusal has to happen here rather than at the next read: by the time a
    sweep trips over the row, the bad name is already committed and every
    upload that introduces a new instrument fails until someone edits the
    database by hand.
    """
    resp = await admin_client.patch(
        f"/api/sample/files/{stored_file['id']}", json=_body(stored_file, instrument)
    )

    assert resp.status_code == 400, f"{why}: {resp.text}"
    assert await _instrument_of(async_session_factory, stored_file["id"]) == INSTRUMENT


@pytest.mark.asyncio
async def test_update_still_accepts_a_valid_instrument_name(
    admin_client, async_session_factory, stored_file
):
    """The guard is not over-broad: a legal rename still goes through.

    Renaming is the reason the route exists, so a check that refused every
    change would be the worse failure of the two.
    """
    renamed = "patch-orbi-renamed"

    resp = await admin_client.patch(
        f"/api/sample/files/{stored_file['id']}", json=_body(stored_file, renamed)
    )

    assert resp.status_code == 200, resp.text
    assert await _instrument_of(async_session_factory, stored_file["id"]) == renamed
