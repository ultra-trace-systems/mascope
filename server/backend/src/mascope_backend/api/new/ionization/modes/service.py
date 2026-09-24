from sqlalchemy import and_, distinct, or_, select

from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.ionization.modes.schema import (
    IonizationModeCreate,
    IonizationModeUpdate,
)
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleItem,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.ionization_catalogue import SYSTEM_MODE_EDITABLE_FIELDS
from mascope_backend.socket.records.service import (
    emit_record_created,
    emit_record_deleted,
    emit_record_updated,
)

from .util import (
    fetch_all_ionization_modes,
    fetch_listable_ionization_modes,
    resolve_ionization_modes_by_tokens,
    token_is_unique,
)


def _mechanism_set(ionization_mechanism_ids) -> set:
    """The mechanisms of a mode, as what they are: a set.

    The order they arrive in carries no meaning, and neither does a repeat.
    Both the protection check and the rematch decision read them this way, and
    read them through here so the two cannot drift apart.
    """
    return set(ionization_mechanism_ids or [])


def _same_value(field: str, incoming, current) -> bool:
    """Whether an update leaves a field as it was."""
    if field == "ionization_mechanism_ids":
        return _mechanism_set(incoming) == _mechanism_set(current)
    return incoming == current


@api_controller()
async def create_ionization_mode(
    ionization_mode_data: IonizationModeCreate,
) -> dict:
    """
    Creates a new ionization mode with the provided details.

    Steps:
    1. Check for conflicting ionization mode tokens if provided.
    2. Check for conflicting ionization mode names.
    3. Construct a new IonizationMode object with the provided details.
    4. Add the new ionization mode to the session and commit the changes to the database.
    5. Refresh the instance and return the details of the created ionization mode.

    :param ionization_mode_data: The ionization mode data to create
    :return: A dictionary containing the created ionization mode data.
    """
    async with async_session() as session:
        # Step 1: Check for token conflicts if provided
        if ionization_mode_data.ionization_mode_token:
            if not await token_is_unique(ionization_mode_data.ionization_mode_token):
                raise ValueError(
                    f"Ionization mode with similar token as '{ionization_mode_data.ionization_mode_token}' already exists"
                )

        # Step 2: Check for name conflicts
        existing_name = await session.execute(
            select(IonizationMode).where(
                IonizationMode.ionization_mode_name
                == ionization_mode_data.ionization_mode_name
            )
        )
        if existing_name.scalar_one_or_none():
            raise ValueError(
                f"Ionization mode with name '{ionization_mode_data.ionization_mode_name}' already exists"
            )

        # Step 3: Create new ionization mode instance
        new_ionization_mode = IonizationMode(
            ionization_mode_id=gen_id(16),
            **ionization_mode_data.model_dump(),
        )

        # Step 4: Add to session and commit
        session.add(new_ionization_mode)
        await session.commit()

        # Step 5: Refresh and return
        await session.refresh(new_ionization_mode)

    await emit_record_created(
        record_type="ionization_mode",
        record_id=new_ionization_mode.ionization_mode_id,
        record=new_ionization_mode.to_dict(),
    )
    return {
        "message": "Ionization mode created successfully.",
        "data": new_ionization_mode.to_dict(),
    }


@api_controller()
async def get_ionization_mode(
    ionization_mode_id: str | None = None,
    token: str | None = None,
) -> dict:
    """
    Retrieves a single ionization mode either by ID or by token. One of them must be provided, but not both.

    Steps:
    1. Validate input parameters to ensure that either an ID or token is provided, but not both.
    2. Construct a query to fetch the ionization mode based on the provided parameter.
    3. Execute the query and fetch the result.
    4. Check if the ionization mode exists. If not, raise a NotFoundException.
    5. Return the ionization mode's details as a dictionary.

    :param ionization_mode_id: Unique ID of the ionization mode to retrieve directly.
    :param token: Token of the ionization mode to retrieve.
    :return: The requested ionization mode's details.
    """
    async with async_session() as session:
        # Validate input parameters
        if ionization_mode_id and token:
            raise ValueError("Provide either ionization_mode_id or token, not both.")

        if not ionization_mode_id and not token:
            raise ValueError("Provide either ionization_mode_id or token.")

        # Construct query based on parameters
        if ionization_mode_id:
            stmt = select(IonizationMode).where(
                IonizationMode.ionization_mode_id == ionization_mode_id
            )
            label = f"with ID {ionization_mode_id}"
        else:  # token
            stmt = select(IonizationMode).where(
                IonizationMode.ionization_mode_token == token
            )
            label = f"with token '{token}'"

        # Execute query
        result = await session.execute(stmt)
        ionization_mode = result.scalar_one_or_none()

        # Check existence
        if not ionization_mode:
            raise NotFoundException(f"ionization mode {label} not found")

        # Return details
        return {
            "message": f"Ionization mode {ionization_mode.ionization_mode_name} retrieved successfully.",
            "data": ionization_mode.to_dict(),
        }


@api_controller()
async def get_ionization_modes(include_system: bool = False) -> dict:
    """
    Retrieves the ionization modes.

    The modes Mascope ships that this deployment has not adopted are left out
    unless asked for: without target collections such a mode calibrates and
    matches nothing. Asking for them is how one is adopted in the first place.

    :param include_system: Whether to include unadopted seeded modes.
    :return: A dictionary containing total results count and a list of ionization modes.
    """
    ionization_modes = await (
        fetch_all_ionization_modes()
        if include_system
        else fetch_listable_ionization_modes()
    )

    return {
        "message": "Ionization modes retrieved successfully.",
        "results": len(ionization_modes),
        "data": [mode.to_dict() for mode in ionization_modes],
    }


@api_controller()
async def get_ionization_modes_by_filename(filename: str) -> dict:
    """Retrieve ionization mode(s) by sample file filename, by searching for tokens.

    :param filename: The filename of the sample file.
    :type filename: str
    :raises NotFoundException: If no ionization modes are found for the given filename.
    :return: A dictionary containing message and retrieved ionization modes.
    :rtype: dict
    """
    sample_file = await fetch_sample_file(filename=filename)
    ionization_modes = await resolve_ionization_modes_by_tokens(sample_file)
    if not ionization_modes:
        raise NotFoundException(
            f"No ionization modes could be resolved for file '{filename}'. No valid tokens in the filename."
        )
    return {
        "message": f"Ionization modes for file {filename} retrieved successfully.",
        "data": [im.to_dict() for im in ionization_modes],
    }


@api_controller()
async def update_ionization_mode(
    ionization_mode_id: str,
    ionization_mode_data: IonizationModeUpdate,
) -> dict:
    """
    Updates an existing ionization mode.

    Steps:
    - Fetch the ionization mode by its ID from the database.
    - Check for token conflicts if token is being updated.
    - Validate mechanism-polarity consistency.
    - Allow changing the calibration/diagnostic collection (but not clearing it).
    - Check for conflicts with existing acquisition batches.
    - Block updates if any affected batch is currently processing.
    - Flag affected batches: "recalibrate" if the calibration collection changed,
      otherwise "rematch" if mechanisms or the diagnostic collection changed.
    - Update the fields that were provided in the request.
    - Commit the changes and return the updated ionization mode.

    :param ionization_mode_id: The ID of the ionization mode to update.
    :param ionization_mode_data: The data to update.
    :return: A dictionary containing the updated ionization mode data.
    """
    async with async_session() as session:
        # Get the existing ionization mode
        stmt = select(IonizationMode).where(
            IonizationMode.ionization_mode_id == ionization_mode_id
        )
        result = await session.execute(stmt)
        ionization_mode = result.scalar_one_or_none()

        if not ionization_mode:
            raise NotFoundException(
                f"Ionization mode with ID {ionization_mode_id} not found"
            )
        update_data = ionization_mode_data.model_dump(exclude_unset=True)

        # A mode Mascope ships owns its chemistry: the name, token, polarity
        # and mechanisms say which chemistry it is and read the same on every
        # server. Its target collections are the deployment's own data.
        # Compared by value, so a request that echoes the whole mode back to
        # change one collection is not refused for the fields it did not touch.
        if ionization_mode.system_key:
            protected = [
                field
                for field, value in update_data.items()
                if field not in SYSTEM_MODE_EDITABLE_FIELDS
                and not _same_value(field, value, getattr(ionization_mode, field))
            ]
            if protected:
                raise ValueError(
                    f"Ionization mode '{ionization_mode.ionization_mode_name}' is one "
                    f"Mascope ships, so {', '.join(sorted(protected))} cannot be "
                    "changed. Its calibration and diagnostic collections can."
                )
            # The echoed list matched as a set, which a reordered or repeated
            # one also does. Dropping it here keeps the stored definition
            # exactly as seeded, so it reads the same on every server.
            update_data.pop("ionization_mechanism_ids", None)

        # Check for token conflicts if being updated
        new_token = update_data.get("ionization_mode_token")
        if new_token and new_token != ionization_mode.ionization_mode_token:
            if not await token_is_unique(new_token, ignore_id=ionization_mode_id):
                raise ValueError(
                    f"Ionization mode with similar token as '{new_token}'"
                    " already exists"
                )

        # Validate mechanism-polarity consistency
        new_mechanism_ids = update_data.get("ionization_mechanism_ids")
        new_polarity = update_data.get(
            "ionization_mode_polarity", ionization_mode.ionization_mode_polarity
        )
        # Validate when either mechanisms or polarity is being updated
        effective_mechanism_ids = (
            new_mechanism_ids
            if new_mechanism_ids is not None
            else ionization_mode.ionization_mechanism_ids
        )
        if (
            "ionization_mechanism_ids" in update_data
            or "ionization_mode_polarity" in update_data
        ):
            mechanisms_result = await session.execute(
                select(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism_id.in_(
                        effective_mechanism_ids
                    )
                )
            )
            mechanisms = mechanisms_result.scalars().all()
            if new_mechanism_ids is not None:
                found_ids = {m.ionization_mechanism_id for m in mechanisms}
                missing = set(new_mechanism_ids) - found_ids
                if missing:
                    raise ValueError(
                        f"Ionization mechanism(s) not found: {', '.join(missing)}"
                    )
            mismatched = [
                m.ionization_mechanism
                for m in mechanisms
                if m.ionization_mechanism_polarity != new_polarity
            ]
            if mismatched:
                raise ValueError(
                    f"Mechanism(s) {', '.join(mismatched)} do not match "
                    f"polarity '{new_polarity}'"
                )

        # Calibration and diagnostic collections may be changed to another
        # collection, but not cleared (un-set to null). Dropping the key when the
        # incoming value is None preserves the currently-defined collection.
        if update_data.get("calibration_collection_id") is None:
            update_data.pop("calibration_collection_id", None)
        if update_data.get("diagnostic_collection_id") is None:
            update_data.pop("diagnostic_collection_id", None)

        # Determine which fields are changing (used to flag affected batches).
        # Computed against the current entity before the values are applied below.
        mechanisms_changed = "ionization_mechanism_ids" in update_data and (
            _mechanism_set(update_data["ionization_mechanism_ids"])
            != _mechanism_set(ionization_mode.ionization_mechanism_ids)
        )
        calibration_changed = (
            "calibration_collection_id" in update_data
            and update_data["calibration_collection_id"]
            != ionization_mode.calibration_collection_id
        )
        diagnostic_changed = (
            "diagnostic_collection_id" in update_data
            and update_data["diagnostic_collection_id"]
            != ionization_mode.diagnostic_collection_id
        )

        # Find affected sample batches (batches containing samples using this mode)
        affected_batch_ids_result = await session.execute(
            select(distinct(SampleItem.sample_batch_id)).where(
                SampleItem.ionization_mode_id == ionization_mode_id
            )
        )
        affected_batch_ids = list(affected_batch_ids_result.scalars().all())

        if affected_batch_ids:
            # Block if any affected batch is currently processing
            processing_batches = await session.execute(
                select(SampleBatch).where(
                    and_(
                        SampleBatch.sample_batch_id.in_(affected_batch_ids),
                        SampleBatch.status == "processing",
                    )
                )
            )
            if processing_batches.scalars().first():
                raise ValueError(
                    f"Cannot update ionization mode '{ionization_mode.ionization_mode_name}', "
                    "as one or more affected batches are currently processing."
                )

        # Check affected acquisition batches for name changes
        affected_acq_batches = await session.execute(
            select(SampleBatch).where(
                and_(
                    SampleBatch.sample_batch_type == "ACQUISITION",
                    or_(
                        SampleBatch.sample_batch_name.contains(
                            f"{ionization_mode.ionization_mode_name} acquisition"
                        ),
                        SampleBatch.sample_batch_name.contains(
                            f"{update_data.get('ionization_mode_name')} acquisition"
                        ),
                    ),
                )
            )
        )
        if affected_acq_batches.scalars().first():
            for key, value in update_data.items():
                match key:
                    case "ionization_mode_token":
                        continue
                    case "diagnostic_collection_id" | "calibration_collection_id":
                        continue
                    case "ionization_mechanism_ids" | "ionization_mode_polarity":
                        # Mechanisms and polarity can be updated (triggers rematch)
                        continue
                    case _:
                        if getattr(ionization_mode, key) != value:
                            raise ValueError(
                                f"Cannot update ionization mode '{ionization_mode.ionization_mode_name}', "
                                f"as it is in use for one or more acquisition batches."
                            )

        # Update the fields that were provided
        for field, value in update_data.items():
            setattr(ionization_mode, field, value)

        # Commit and return
        await session.commit()
        await session.refresh(ionization_mode)

    # Flag affected batches so stale results are reprocessed. A calibration
    # collection change requires a re-fit of m/z calibration to take effect
    # (which itself ends in a rematch), so it gets the stronger "recalibrate"
    # signal. Mechanism or diagnostic collection changes only need a rematch.
    if affected_batch_ids:
        if calibration_changed:
            await update_sample_batch_status(
                sample_batch_ids=affected_batch_ids,
                status="recalibrate",
                independent_transaction=True,
            )
        elif mechanisms_changed or diagnostic_changed:
            await update_sample_batch_status(
                sample_batch_ids=affected_batch_ids,
                status="rematch",
                independent_transaction=True,
            )

    await emit_record_updated(
        record_type="ionization_mode",
        record_id=ionization_mode_id,
        record=ionization_mode.to_dict(),
    )
    return {
        "message": f"Ionization mode {ionization_mode.ionization_mode_name} "
        "updated successfully.",
        "data": ionization_mode.to_dict(),
    }


@api_controller()
async def delete_ionization_mode(ionization_mode_id: str) -> dict:
    """
    Deletes an ionization mode by its unique identifier.

    Steps:
    1. Fetch the ionization mode by its ID from the database.
    2. Check if the ionization mode is associated with any samples. If so, raise an exception.
    3. If the ionization mode is found, delete it from the session and commit the changes.

    :param ionization_mode_id: The unique identifier of the ionization mode to delete.
    :return: A dictionary with a success message.
    """
    async with async_session() as session:
        # Step 1: Fetch the ionization mode
        ionization_mode = await session.get(IonizationMode, ionization_mode_id)

        if not ionization_mode:
            raise NotFoundException(
                f"Ionization mode with ID '{ionization_mode_id}' not found"
            )

        if ionization_mode.system_key:
            raise ValueError(
                f"Ionization mode '{ionization_mode.ionization_mode_name}' is one "
                "Mascope ships and cannot be deleted"
            )

        # Step 2: Check for associations with samples
        result = await session.execute(
            select(SampleItem).where(
                SampleItem.ionization_mode_id == ionization_mode_id
            )
        )
        if result.scalars().first():
            raise ValueError(
                f"Ionization mode '{ionization_mode.ionization_mode_name}' is associated with "
                "existing samples and cannot be deleted"
            )

        # Step 3: Delete the ionization mode and commit changes
        await session.delete(ionization_mode)
        await session.commit()

    await emit_record_deleted(
        record_type="ionization_mode",
        record_id=ionization_mode_id,
    )
    return {
        "message": f"Ionization mode {ionization_mode.ionization_mode_name} deleted successfully.",
    }
