"""The instrument rows: made wherever an instrument's name is written.

An instrument is a row of its own (``db.models.Instrument``) that outlives its
files. Every table that names an instrument in a free-text ``instrument``
column carries the row's id beside it, and this module is how a writer gets
that id: by name, spelled exactly as written, the row made if there is none.

Two ways in, by what the writer knows:

- :func:`record_instrument_seen` when a file of the instrument is registered.
  The file is the evidence: it fills what the reader reported (the class, the
  model) and moves ``last_seen_utc``.
- :func:`instrument_id_for` everywhere else - a dataset, a mode's scope, a
  paired device, a notification, a method binding, an instrument function.
  Those name an instrument without being evidence of it, so they only make
  sure the row exists and touch nothing on it.

Both run in the caller's session and leave the commit to it, so a row and the
link to it are written together or not at all.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.db import Instrument
from mascope_backend.db.id import gen_id


#: The maker of the data system each reader reads, by instrument class: a
#: ``.raw`` file is Thermo's format and a ``.h5`` TofDaq file TOFWERK's.
VENDOR_BY_TYPE = {"orbi": "Thermo Fisher Scientific", "tof": "TOFWERK"}


def _named(name: str | None) -> bool:
    return bool(name and name.strip())


async def instrument_id_for(session: AsyncSession, name: str | None) -> str | None:
    """The id of the instrument row named ``name``, made if there is none.

    Touches nothing on a row that exists, so it takes no lock on it: a writer
    deep in a long transaction can call it without holding up every other
    writer of the same instrument until it commits.

    :param session: The caller's session; the row is made in its transaction.
    :param name: The instrument's name, as the writer spells it.
    :return: The row's id, or None for no name.
    """
    if not _named(name):
        return None
    await session.execute(
        insert(Instrument)
        .values(
            instrument_id=gen_id(16),
            name=name,
            created_utc=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=[Instrument.name])
    )
    return (
        await session.execute(
            select(Instrument.instrument_id).where(Instrument.name == name)
        )
    ).scalar_one()


async def record_instrument_seen(
    session: AsyncSession,
    name: str | None,
    *,
    instrument_type: str | None = None,
    model: str | None = None,
) -> str | None:
    """Make or touch the row of the instrument a registered file names.

    What the row does not know yet the file fills: the class, and the vendor
    that follows from it. A model the file reports replaces the one the row
    holds, and a file that reports none leaves it, so a file converted before
    the model was read cannot erase it. ``last_seen_utc`` moves to now.

    :param session: The caller's session; the row is written in its
        transaction and locked until it ends, which on registration is short.
    :param name: The instrument's name, as the file's name spells it.
    :param instrument_type: The class the reader read the file as.
    :param model: The model the reader reported, None for none.
    :return: The row's id, or None for no name.
    """
    if not _named(name):
        return None
    now = datetime.now(timezone.utc)
    statement = insert(Instrument).values(
        instrument_id=gen_id(16),
        name=name,
        instrument_type=instrument_type,
        vendor=VENDOR_BY_TYPE.get(instrument_type or ""),
        model=model or None,
        created_utc=now,
        last_seen_utc=now,
    )
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[Instrument.name],
        set_={
            "instrument_type": func.coalesce(
                Instrument.instrument_type, excluded.instrument_type
            ),
            "vendor": func.coalesce(Instrument.vendor, excluded.vendor),
            "model": func.coalesce(excluded.model, Instrument.model),
            "last_seen_utc": func.greatest(
                Instrument.last_seen_utc, excluded.last_seen_utc
            ),
        },
    ).returning(Instrument.instrument_id)
    return (await session.execute(statement)).scalar_one()
