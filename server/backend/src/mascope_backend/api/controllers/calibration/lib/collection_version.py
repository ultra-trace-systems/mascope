"""
Whether a TOF fit was made on its calibration collection as it stands now.

A TOF fit's record names the version of the calibration collection it was
offered (``quality.collection``, ``TofCalibrationHandler._fitted_on``): the
collection's id and a hash of the isotopes it offered under the mode's
mechanisms (:func:`calibration_isotopes_sha256`). An edit of the collection's
compounds, or of the mode's mechanisms, changes that hash; pointing the mode
at another collection changes the id. A file whose record names another
version than the one its mode gives a fit now was fitted on calibrants the
mode no longer has, and a re-calibration would fit it on others.

The sample list reads it per sample (``samples_controller.get_samples``), so
the calibration badge can say so.
"""

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_isotopes_sha256,
)
from mascope_backend.api.controllers.target.associations.target_compound_in_target_collection_controller import (
    get_target_compound_in_target_collection,
)
from mascope_backend.api.controllers.target.isotopes.target_isotopes_controller import (
    get_target_isotopes,
)
from mascope_backend.api.new.ionization.modes.versions import current_versions


async def collection_version(
    collection_id: str | None, mechanism_ids: list[str] | None
) -> dict | None:
    """The version of a calibration collection a TOF fit is offered now.

    Read as the node reads the collection
    (``CalibrationHandler._resolve_calibration_isotopes``): the collection's
    compounds, their isotopes under the mechanisms at the TOF resolution.

    :param collection_id: The calibration collection.
    :param mechanism_ids: The mode's ionization mechanisms.
    :return: ``{"id", "isotopes_sha256"}`` as a record's ``quality.collection``
        holds it, None when the collection offers a fit no isotope.
    """
    if not collection_id:
        return None
    compounds = await get_target_compound_in_target_collection(
        target_collection_id=collection_id
    )
    compound_ids = [item["target_compound_id"] for item in compounds["data"]]
    if not compound_ids:
        return None
    isotopes = pd.DataFrame(
        (
            await get_target_isotopes(
                target_compound_ids=compound_ids,
                ionization_mechanism_ids=mechanism_ids,
                resolution="LOW",
            )
        )["data"]
    )
    if isotopes.empty:
        return None
    return {
        "id": collection_id,
        "isotopes_sha256": calibration_isotopes_sha256(isotopes),
    }


async def current_collection_versions(
    session: AsyncSession, mode_ids: set[str]
) -> dict[str, dict | None]:
    """The collection version each mode's current version offers a fit now.

    A sample bound under an earlier version of its mode moves onto the
    current one when it is re-calibrated, so the current version is the one
    its record is compared with.

    :param session: An open session.
    :param mode_ids: Any versions of modes.
    :return: Per given id, as :func:`collection_version` answers for its
        current version; None for a mode no version of which is active.
    """
    currents = await current_versions(session, mode_ids)
    cache: dict[tuple, dict | None] = {}
    answer: dict[str, dict | None] = {}
    for mode_id in mode_ids:
        mode = currents.get(mode_id)
        if mode is None:
            answer[mode_id] = None
            continue
        key = (
            mode.calibration_collection_id,
            tuple(sorted(mode.ionization_mechanism_ids or [])),
        )
        if key not in cache:
            cache[key] = await collection_version(
                mode.calibration_collection_id, mode.ionization_mechanism_ids
            )
        answer[mode_id] = cache[key]
    return answer


def recorded_version(record: dict | None) -> dict | None:
    """The collection version a calibration record names, None for none.

    Only a TOF fit made since the record carries it names one; an Orbitrap
    fit, a legacy record and an unfitted or failed one do not.
    """
    quality = record.get("quality") if isinstance(record, dict) else None
    version = quality.get("collection") if isinstance(quality, dict) else None
    if not isinstance(version, dict) or not version.get("isotopes_sha256"):
        return None
    return version


def collection_stale(record: dict | None, current: dict | None) -> bool | None:
    """Whether a record was fitted on another collection version than ``current``.

    :param record: A file's ``mz_calibration``.
    :param current: What :func:`collection_version` answers for its mode now.
    :return: None when the record names no version, else whether the one it
        names differs from ``current``. A mode whose collection offers no
        isotope now differs from every version.
    """
    version = recorded_version(record)
    if version is None:
        return None
    if current is None:
        return True
    return (version.get("id"), version.get("isotopes_sha256")) != (
        current["id"],
        current["isotopes_sha256"],
    )
