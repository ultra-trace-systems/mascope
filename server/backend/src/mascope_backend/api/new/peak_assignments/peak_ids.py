"""The id-only read of a sample's peaks.

Two readers need the set of peak ids a sample's peak file holds and nothing
else: an import validates every row it is sent against it, and the runs
listing checks each completed run against it to say whether the run's rows
still name the sample's peaks (``peaks_current``). Both read the same peaks the
in-app engine assigns: ``extract_peaks``'s defaults, satellites left out.
"""

import asyncio

from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks


async def load_peak_ids(sample) -> set[str]:
    """Every peak id the sample's peak file holds.

    Deliberately the id-only read: these readers need the id set, while the
    engine's full load additionally opens the raw data source per call - on
    every chunk of an import, and again on every retry - only to compute an
    averaging divisor. Offloaded to a thread because it is blocking file I/O in
    a request handler.

    :param sample: The sample whose peaks are read.
    :return: The sample's peak ids.
    """
    peak_data = await asyncio.to_thread(
        extract_peaks,
        sample.filename,
        sample.polarity,
        sample.t0,
        sample.t1,
        areas=False,
        heights=False,
        average=False,
    )
    return {str(peak_id) for peak_id in peak_data.peak_ids}
