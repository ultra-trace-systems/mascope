from sqlalchemy import func, select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.db import SampleFile, async_session


@api_controller()
async def get_instruments() -> dict:
    """
    Retrieve all instruments in the database, using the sample file table's instrument column.

    The instrument class comes from the files converted under each name -
    recorded by the reader at conversion - rather than from the name, so an
    instrument can be called anything. A name that has been fed files of both
    classes is listed once, under the class that sorts first.

    :return: A dictionary containing:
        - message: A human-readable message summarizing the result.
        - results: The total number of distinct instruments.
        - data: A list of instruments with their names and resolved types.
    :rtype: dict
    """
    async with async_session() as session:
        result = await session.execute(
            select(SampleFile.instrument, func.min(SampleFile.instrument_type))
            .group_by(SampleFile.instrument)
            .order_by(SampleFile.instrument)
        )
        instrument_list = [
            {"instrument": instrument, "type": instrument_type}
            for instrument, instrument_type in result.all()
        ]

        return {
            "message": f"Retrieved {len(instrument_list)} instrument records",
            "results": len(instrument_list),
            "data": instrument_list,
        }
