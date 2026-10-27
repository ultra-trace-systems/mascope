from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.workspaces.dependencies import (
    check_sample_file_instrument_access,
    require_sample_role,
)
from mascope_backend.db import User

from .service import get_sample_axis_fingerprint, get_sample_file_axis_fingerprint


#: Included in the sample files' router, under ``/api/sample/files``.
sample_file_axis_fingerprint_router = APIRouter()

#: Included in the samples' router, under ``/api/samples``.
sample_axis_fingerprint_router = APIRouter()


@sample_file_axis_fingerprint_router.get("/{sample_file_id}/axis_fingerprint")
@api_route(token_access=True)
async def get_sample_file_axis_fingerprint_route(
    sample_file_id: str,
    user: User = Depends(current_active_user),
):
    """
    Retrieve the axis fingerprint of a raw Orbitrap file.

    On some Orbitrap models the data system divides the frequency axis into
    frequency bands, and each mass calibration stores one m/z offset per band.
    The fingerprint reads those offsets back from the file's stored profile.
    It describes the raw file's own m/z axis: Mascope never alters an m/z
    with it. The document follows the JSON schema `mascope.axis_fingerprint`,
    version 1, and adds `sample_file_id`.

    It is computed when the file is converted and served as stored, whatever
    its `status`: `ok` (at least one acquisition mode has a table of frequency
    bands), `no_fingerprint` (read, but no mode gave one), `unavailable` (the
    server's reader cannot read the stored profile points), `error` (the
    computation failed; `error` says how) or `missing` (not computed for this
    file; nothing computes one on request). The last three carry nothing but
    their status, and an error its message.

    A file that is not an Orbitrap file has no fingerprint, and is answered
    with 404.

    :param sample_file_id: ID of the sample file.
    :param user: Authenticated user.
    :return: The fingerprint document of the sample file.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "guest")
    return await get_sample_file_axis_fingerprint(sample_file_id)


@sample_axis_fingerprint_router.get("/{sample_item_id}/axis_fingerprint")
@api_route(token_access=True)
async def get_sample_axis_fingerprint_route(
    sample_item_id: str,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """
    Retrieve the axis fingerprint of a sample's raw Orbitrap file, with what
    the sample reads of it.

    The document is the file's, as `GET
    /api/sample/files/{sample_file_id}/axis_fingerprint` serves it. Where it
    describes the file's scans (`status` `ok` or `no_fingerprint`), it also
    carries an `item` block, and its `modes` are listed for the sample: the
    mode its scans belong to first, then the other modes of its polarity and
    stream, then the rest. `dominant_mode` stays the file's.

    The `item` block holds:

    - `sample_item_id`, `sample_file_id`, `polarity`, and `t0_s` and `t1_s`,
      the sample's window [s];
    - `stream_key`, the scan stream the sample reads, null when it reads every
      MS1 scan of its polarity;
    - `scans`, the scan numbers its peak list is built from: the MS1 scans of
      its polarity and stream starting in its window, without a stream's
      outlier first scan. A stitched sample reads the streams that own some
      m/z of it;
    - `mode_id`, the mode most of those scans belong to, null when none does;
    - `lock`, that mode's `used_share`, `used_count`, `m_lock_eff` and
      `s_lock_eff_ppm` recomputed over the scans of the sample in it. Over
      every scan of the mode, it is the mode's own;
    - `mz_calibration_factor`, the factor the file's mass calibration gives
      the sample's stream: the stream's own where the calibration names one,
      else the file's; and `file_mz_calibration_factor`, the file's own,
      which `/api/samples/centroids` multiplies into every mass it serves of
      the file. Both null for a file that was never calibrated. A
      calibration never names a composite, so a stitched sample's factor is
      the file's, while its peaks carry the factor of each stream that owns
      a run of it, which `streams` lists;
    - `streams`, the scan streams its peak list is read from, each with its
      `stream_key`, its `scans` (those of `scans` that are of the stream)
      and its `mz_calibration_factor`: the sample's own stream, or each
      stream that owns some m/z of a stitched sample, in its stitch map's
      order. The sample's peak listing names the stream each peak of a
      stitched sample is of, which joins a peak to its factor here. Null for
      a sample that reads every MS1 scan of its polarity, whose peaks all
      carry the file's factor.

    :param sample_item_id: The unique identifier of the sample.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: The fingerprint document of the sample's file, with its `item`
        block.
    """
    return await get_sample_axis_fingerprint(sample_item_id)
