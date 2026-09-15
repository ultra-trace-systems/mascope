from sqlalchemy import and_, select

from mascope_backend.db import (
    IonizationMode,
    SampleFile,
    SampleItem,
    async_session,
)
from mascope_backend.runtime import runtime


COLLECTION_ID_FIELDS = ("calibration_collection_id", "diagnostic_collection_id")


async def fetch_mode_collection_ids(ionization_mode_id: str) -> dict[str, str | None]:
    """Fetch the calibration and diagnostic collection ids a mode is bound to.

    Used by the update route to tell a binding the request is *changing* from
    one it is merely echoing back.

    A mode that does not exist reports no bindings, so every id the request
    names counts as new and is checked. That fails closed; the service's own
    lookup still answers 404 for a caller who clears the check.

    :param ionization_mode_id: The ID of the ionization mode to read.
    :type ionization_mode_id: str
    :return: The mode's current collection ids, keyed by field name.
    :rtype: dict[str, str | None]
    """
    async with async_session() as session:
        row = (
            await session.execute(
                select(
                    IonizationMode.calibration_collection_id,
                    IonizationMode.diagnostic_collection_id,
                ).where(IonizationMode.ionization_mode_id == ionization_mode_id)
            )
        ).one_or_none()

    if row is None:
        return dict.fromkeys(COLLECTION_ID_FIELDS)
    return {field: getattr(row, field) for field in COLLECTION_ID_FIELDS}


async def fetch_all_ionization_modes() -> list[IonizationMode]:
    """Fetch all ionization modes from the database.

    :return: A list of all ionization modes.
    :rtype: list[IonizationMode]
    """
    async with async_session() as session:
        result = await session.execute(select(IonizationMode))
        ionization_modes = result.scalars().all()
        return ionization_modes


async def fetch_batch_ionization_mechanism_ids(sample_batch_id: str) -> list[str]:
    """Fetch ionization mechanism IDs for a given sample batch.

    :param sample_batch_id: The ID of the sample batch to fetch ionization mechanism IDs for.
    :type sample_batch_id: str
    :return: A list of ionization mechanism IDs associated with the sample batch.
    :rtype: list[str]
    """
    async with async_session() as session:
        # Get samples
        result = await session.execute(
            select(SampleItem).where(SampleItem.sample_batch_id == sample_batch_id)
        )
        batch_sample_items = result.scalars().all()
        # Collect unique ionization mode ids in the batch samples
        batch_ion_mode_ids = list(
            set([item.ionization_mode_id for item in batch_sample_items])
        )
        # Get the ionization modes
        result = await session.execute(
            select(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(batch_ion_mode_ids)
            )
        )
        batch_ion_modes = result.scalars().all()
        batch_ionization_mechanism_ids = list(
            set(
                ionization_mechanism_id
                for ion_mode in batch_ion_modes
                for ionization_mechanism_id in ion_mode.ionization_mechanism_ids
            )
        )
        return batch_ionization_mechanism_ids


async def fetch_sample_ionization_mechanism_ids(sample_item_id: str) -> list[str]:
    """Fetch ionization mechanism IDs for a given sample item.

    :param sample_item_id: The ID of the sample item to fetch ionization mechanism IDs for.
    :type sample_item_id: str
    :return: A list of ionization mechanism IDs associated with the sample item.
    :rtype: list[str]
    """
    async with async_session() as session:
        # Get samples
        result = await session.execute(
            select(SampleItem).where(SampleItem.sample_item_id == sample_item_id)
        )
        sample_item = result.scalars().one_or_none()
        if not sample_item:
            raise ValueError(f"Sample item with ID {sample_item_id} not found")

        # Get the ionization mode
        result = await session.execute(
            select(IonizationMode).where(
                IonizationMode.ionization_mode_id == sample_item.ionization_mode_id
            )
        )
        sample_ion_mode = result.scalars().one_or_none()

        return sample_ion_mode.ionization_mechanism_ids


async def resolve_ionization_modes_by_peaks(
    sample_file: SampleFile,
) -> list[IonizationMode]:
    """Resolve ionization modes based on peaks in the sample file.

    :param sample_file: The sample file to resolve ionization modes for.
    :type sample_file: SampleFile
    :raises NotImplementedError: Not implemented yet.
    :return: A list of resolved ionization modes.
    :rtype: list[IonizationMode]
    """

    all_ionization_modes = await fetch_all_ionization_modes()
    for ionization_mode in all_ionization_modes:
        if ionization_mode.ionization_mode_polarity not in sample_file.polarity:
            continue
        # TODO: Fetch the calibration isotopes for
        # ionization_mode.calibration_collection_id (via get_target_isotopes)
        # and match them against the peaks in the sample file.
        raise NotImplementedError(
            "Resolving ionization modes by peaks. Calibration isotopes matching not implemented yet",
        )


async def resolve_ionization_modes_by_tokens(
    sample_file: SampleFile,
) -> list[IonizationMode]:
    """Resolve ionization modes based on tokens in the sample file.

    Raises ValueError if the number of matched ionization modes is incorrect.

    :param sample_file: The sample file to resolve ionization modes for.
    :type sample_file: SampleFile
    :return: A list of resolved ionization modes.
    :rtype: list[IonizationMode]
    """
    runtime.logger.debug(
        f"Resolving ionization modes by tokens for {sample_file.filename}"
    )
    # Fetch all ionization modes
    all_ionization_modes = await fetch_all_ionization_modes()
    # Match ionization modes based on tokens in the filename
    matched_ionization_modes = []
    for ionization_mode in all_ionization_modes:
        if not ionization_mode.ionization_mode_token:
            continue
        if (
            ionization_mode.ionization_mode_token
            and ionization_mode.ionization_mode_polarity in sample_file.polarity
            and ionization_mode.ionization_mode_token in sample_file.filename
        ):
            runtime.logger.debug(
                f"Matched ionization mode token: {ionization_mode.ionization_mode_token} "
                f"with filename: {sample_file.filename}"
            )
            matched_ionization_modes.append(ionization_mode)

    if len(matched_ionization_modes) == len(sample_file.polarity):
        # Found matching ionization modes for all polarities (1 or 2)
        return matched_ionization_modes
    elif len(matched_ionization_modes) == 0:
        # No ionization modes found by tokens
        raise ValueError(
            f"No ionization mode tokens found for file {sample_file.filename}. "
            "Configure tokens in ionization settings"
        )
    elif len(matched_ionization_modes) < len(sample_file.polarity):
        # Not enough ionization modes found by tokens
        raise ValueError(
            f"No ionization mode token found for both polarities in file {sample_file.filename}. "
            "Configure tokens in ionization settings"
        )
    elif len(matched_ionization_modes) > len(sample_file.polarity):
        # Found too many ionization modes, likely overlapping tokens
        raise ValueError(
            f"Found too many matching ionization modes for file {sample_file.filename}. "
            "Configure tokens in ionization settings"
        )


async def token_is_unique(token: str, ignore_id: str | None = None) -> bool:
    """Validate if an ionization mode token overlaps with an existing one.

    Note: This checks for overlapping tokens, not exact matches. Also, it still
    does not completely guarantee that a specific filename would only match one token.

    :param token: The ionization mode token to validate.
    :type token: str
    :param ignore_id: An optional ionization mode ID to ignore during the check (useful when updating).
    :type ignore_id: str | None
    :return: True if the token is unique, False otherwise.
    :rtype: bool
    """
    async with async_session() as session:
        # First check if any existing token contains the new token
        result = await session.execute(
            select(IonizationMode).where(
                and_(
                    IonizationMode.ionization_mode_token.contains(token),
                    IonizationMode.ionization_mode_id != ignore_id,
                )
            )
        )
        if result.scalars().first():
            return False

        # Then fetch all existing tokens and check if new token contains any of them
        all_modes = await fetch_all_ionization_modes()

        # Check if the new token contains any existing token
        for mode in all_modes:
            if not mode.ionization_mode_token or mode.ionization_mode_id == ignore_id:
                continue
            if mode.ionization_mode_token in token:
                runtime.logger.debug(
                    f"New token '{token}' contains existing token '{mode.ionization_mode_token}'"
                )
                return False

        return True
