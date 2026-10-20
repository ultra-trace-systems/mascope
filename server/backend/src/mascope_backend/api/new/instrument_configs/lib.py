import asyncio
from functools import partial
from typing import Callable

import numpy as np
from sqlalchemy import select

import mascope_signal.instrument_func.streams as m_stream_functions
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.db import InstrumentFunction as InstrumentConfig
from mascope_backend.db import async_session
from mascope_backend.runtime import runtime
from mascope_signal.instrument_func.fit import r_orbi, r_tof


async def fetch_instrument_config_by_filename(filename: str) -> InstrumentConfig | None:
    """Fetch instrument config from the database based on the sample file name.

    :param filename: Name of the sample file for which instrument functions are required.
    :type filename: str
    :return: InstrumentConfig object containing instrument functions, or None if not found.
    :rtype: InstrumentConfig | None
    """
    async with async_session() as session:
        sample_file = await fetch_sample_file(filename=filename)
        if sample_file.instrument_function_id:
            stmt = select(InstrumentConfig).where(
                InstrumentConfig.instrument_function_id
                == sample_file.instrument_function_id
            )
            results = await session.execute(stmt)
            instrument_config = results.scalar_one_or_none()
            if instrument_config is not None:
                runtime.logger.debug(f"Found instrument config by ID for {filename}")
            else:
                runtime.logger.debug(f"No instrument config found by ID for {filename}")
            return instrument_config


def parse_instrument_functions(
    instrument_config: InstrumentConfig,
) -> tuple[dict, Callable]:
    """Parse instrument functions read from the database, into peak shape and resolution function.

    :param instrument_config: Instrument configuration object containing peak shape and resolution function details.
    :type instrument_config: InstrumentConfig
    :return: A tuple containing peak shape details as a dictionary and a resolution function R as a callable.
             The peak shape details include parameters defining the shape of peaks in the mass spectrum.
             The resolution function R takes a mass (m) and returns the resolution at that mass.
    :rtype: tuple[dict, Callable]
    :raises ValueError: If the instrument configuration does not contain the expected attributes or if they are not
                        in the expected format.
    """
    peakshape = instrument_config.peakshape
    resolution_func_coeffs = instrument_config.resolution_function

    # Validate instrument configuration
    if not (
        hasattr(instrument_config, "peakshape")
        and hasattr(instrument_config, "resolution_function")
    ):
        raise ValueError(
            (
                "Instrument config does not contain peak shape or resolution function: ",
                f"{instrument_config}.",
            )
        )
    if not (isinstance(peakshape, dict) and isinstance(resolution_func_coeffs, list)):
        raise ValueError(
            (
                "Instrument configurations are not in the expected format: ",
                f"peakshape: {peakshape}, resolution_function: {resolution_func_coeffs}.",
            )
        )

    # Derive callable from resolution function parameters
    resolution_function = None
    if len(resolution_func_coeffs) == 1:
        # Use native Orbitrap resolution function
        p1 = resolution_func_coeffs[0]
        resolution_function = partial(r_orbi, a=p1)
    elif len(resolution_func_coeffs) == 2:
        # Use resolution function from Junninen's thesis for TOF
        p1, p2 = resolution_func_coeffs
        resolution_function = partial(r_tof, a=p1, b=p2)
    elif len(resolution_func_coeffs) == 3:
        # Use 2nd order polynomial (backwards compatibility for Orbitrap) TODO: legacy
        resolution_function = np.poly1d(resolution_func_coeffs)

    return peakshape, resolution_function


async def read_instrument_functions(
    filename: str, polarity: str | None = None, stream: str | None = None
) -> tuple[dict, Callable]:
    """Read instrument functions from the database and parse them into
    peak shape dictionary and resolution function callable.

    A file whose peaks were detected per scan stream has an instrument
    function per stream as well, which its peak store records
    (``mascope_signal.instrument_func.streams``). Asked for the peaks of a
    polarity or of one stream, the resolution function then reads each
    peak's width from the stream it came from: in a stitched polarity, the
    stream that owns the peak's m/z. Asked for neither, or for a file
    detected whole, it is the file's.

    :param filename: Sample file name
    :type filename: str
    :param polarity: The polarity the peaks to be read are of
    :type polarity: str, optional
    :param stream: Key of the one scan stream the peaks to be read are of
    :type stream: str, optional
    :return: A tuple containing peak shape details as a dictionary and a resolution function R as a callable.
             The peak shape details include parameters defining the shape of peaks in the mass spectrum.
             The resolution function R takes a mass (m) and returns the resolution at that mass.
    :rtype: tuple[dict, callable]
    :raises ValueError: If no instrument configuration is found for the given filename.
    """
    instrument_config = await fetch_instrument_config_by_filename(filename)
    if instrument_config is None:
        raise ValueError(f"Instrument configuration not found for {filename}.")
    peakshape, resolution_function = parse_instrument_functions(instrument_config)
    if polarity is not None or stream is not None:
        try:
            resolution_function = await asyncio.to_thread(
                m_stream_functions.peak_store_resolution,
                filename,
                resolution_function,
                polarity=polarity,
                stream=stream,
            )
        except Exception:  # noqa: BLE001 - the file's own function still reads
            runtime.logger.opt(exception=True).debug(
                f"Could not read the scan streams' instrument functions of "
                f"{filename}; its peaks are read by the file's"
            )
    return peakshape, resolution_function
