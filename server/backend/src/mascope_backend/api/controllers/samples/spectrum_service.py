"""A sample's spectrum for the person asking, and a stale peak store on the way.

A sample's spectrum is read from its file the way the file's peak store says
its peaks were detected (``mascope_signal.compute.get_sample_sum_signal``).
A per-stream store the file no longer reads back is refused there, since the
pooled profile would sit under peaks that were detected elsewhere, and
detecting the file's peaks again is what repairs it. A match that meets such
a store asks for that itself (``match_controller``). So does a spectrum:
otherwise the sample pane shows its listed peaks and no spectrum until
somebody happens to refresh the batch.

**Asked for on behalf of whoever opened the spectrum, where they may.** The
rebuild rematches every sample of the file as that person, which is what an
editor's refresh of the batch does and a guest cannot do. A guest is told
what the store's own error says and nothing is queued.

**Asked for, never waited for.** The request still fails for the stale
sample: its spectrum does not exist until the detection has run. Its message
says that the detection is on its way.

Beside the controllers they call and not in them: the request goes through
the file controller, which itself reads samples.
"""

from mascope_backend.api.controllers.match.match_controller import (
    request_stale_peak_store_rebuilds,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.samples.samples_controller import (
    get_sample_spectrum,
    get_samples_spectra,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.lib.stale_peak_store import is_stale_peak_store
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_backend.api.new.workspaces.dependencies import check_sample_access
from mascope_backend.db import User
from mascope_backend.db.id import gen_id


#: The workspace role a person needs for a rebuild to be asked for in their
#: name: the role that may compute a sample's matches.
REBUILD_ROLE = "editor"


async def request_rebuilds_for(user: User, stale: list[dict]) -> int:
    """Ask for the rebuild of the stale peak stores a person met.

    :param user: Whoever asked for the spectra.
    :type user: User
    :param stale: The samples whose store was stale, each with its
        ``sample_item_id``, ``sample_file_id`` and ``filename``.
    :type stale: list[dict]
    :return: How many files were queued: those of the samples the person may
        rematch, once each, where the file converter could be asked.
    :rtype: int
    """
    files: dict[str, str] = {}
    for entry in stale:
        try:
            await check_sample_access(entry["sample_item_id"], user, REBUILD_ROLE)
        except ForbiddenAccessException:
            continue
        files[entry["sample_file_id"]] = entry["filename"]
    if not files:
        return 0
    return await request_stale_peak_store_rebuilds(files, user, gen_id(8))


async def get_sample_spectrum_for(user: User, sample_item_id: str, **params) -> dict:
    """A sample's spectrum; a stale peak store on the way is asked rebuilt.

    :param user: Whoever asked.
    :type user: User
    :param sample_item_id: The sample.
    :type sample_item_id: str
    :param params: The time and m/z ranges, as :func:`get_sample_spectrum`
        takes them.
    :raises ApiException: What :func:`get_sample_spectrum` raises. For a
        stale store whose rebuild was queued, the same error with a sentence
        saying so.
    :return: What :func:`get_sample_spectrum` returns.
    :rtype: dict
    """
    try:
        return await get_sample_spectrum(sample_item_id=sample_item_id, **params)
    except ApiException as error:
        if not is_stale_peak_store(error):
            raise
        sample = await fetch_sample(sample_item_id)
        queued = await request_rebuilds_for(
            user,
            [
                {
                    "sample_item_id": sample_item_id,
                    "sample_file_id": sample.sample_file_id,
                    "filename": sample.filename,
                }
            ],
        )
        if not queued:
            raise
        raise ApiException(
            f"{error.user_message.rstrip()} Peak detection has been queued for "
            "the file: open the sample again once it has finished.",
            error.tech_message,
            error.status_code,
        ) from error


async def get_samples_spectra_for(
    user: User, sample_item_ids: list[str], **params
) -> dict:
    """Several samples' spectra; the stale peak stores met are asked rebuilt.

    :param user: Whoever asked.
    :type user: User
    :param sample_item_ids: The samples.
    :type sample_item_ids: list[str]
    :param params: The time and m/z ranges, as :func:`get_samples_spectra`
        takes them.
    :return: What :func:`get_samples_spectra` returns, its message saying
        how many of the stale files were queued for peak detection.
    :rtype: dict
    """
    result = await get_samples_spectra(sample_item_ids=sample_item_ids, **params)
    stale = result.get("stale") or []
    if not stale:
        return result
    queued = await request_rebuilds_for(user, stale)
    if queued:
        files = len({entry["sample_file_id"] for entry in stale})
        result["message"] += (
            " Peak detection has been queued for "
            f"{'it' if files == 1 else f'{queued} of them'}: ask again once it "
            "has finished."
        )
    return result
