from sqlalchemy import and_, delete, distinct, func, or_, select

from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.instruments.registry import instrument_id_for
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
from .versions import (
    VERSIONED_FIELDS,
    SupersededVersionError,
    lineage,
    make_version,
    repoint_routing,
    superseded_ids,
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


async def listed(modes: list[IonizationMode]) -> list[dict]:
    """The modes as the browser holds them: each with its earlier versions.

    ``superseded_ids`` is what lets a reader holding only the current versions
    resolve a sample bound under an earlier one, without the listing offering
    the earlier ones to bind to. Every record of a mode that leaves the server
    goes through here, the socket's included: a record without the field
    would replace a listed one and lose it.
    """
    async with async_session() as session:
        earlier = await superseded_ids(
            session, [mode.ionization_mode_id for mode in modes]
        )
    return [
        {**mode.to_dict(), "superseded_ids": earlier[mode.ionization_mode_id]}
        for mode in modes
    ]


#: The date a daily ACQUISITION batch's name starts with: ``YYYY-MM-DD``.
_BATCH_DATE_LENGTH = len("2026-01-01")


def _named_for_mode(mode_name: str):
    """The daily ACQUISITION batches named for a mode, as a filter.

    A bound batch is named ``"<date> <mode name> acquisition"``
    (``process/service.py`` ``acquisition_batch_name``), and is matched by its
    whole name: it ends in the mode's name and " acquisition", and is exactly
    a date longer than that. A part of the name would also match batches
    named for other modes - one whose name ends in this one's ("BA" for "A"),
    and the batch of MS2 samples filed without a chemistry,
    ``"<date> MS2 acquisition (no chemistry)"``, for a mode named "MS2" - and
    hold back a mode no sample was bound under.

    :param mode_name: The mode's name.
    :return: The filter, for a ``where`` on ``SampleBatch``.
    """
    ending = f" {mode_name} acquisition"
    return and_(
        SampleBatch.sample_batch_name.endswith(ending, autoescape=True),
        func.length(SampleBatch.sample_batch_name) == _BATCH_DATE_LENGTH + len(ending),
    )


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
            if not await token_is_unique(
                ionization_mode_data.ionization_mode_token,
                instrument=ionization_mode_data.instrument,
            ):
                raise ValueError(
                    f"Ionization mode with similar token as '{ionization_mode_data.ionization_mode_token}' already exists"
                )

        # Step 2: Check for name conflicts, among the current versions: an
        # earlier version keeps the name its successor has.
        existing_name = await session.execute(
            select(IonizationMode).where(
                IonizationMode.ionization_mode_name
                == ionization_mode_data.ionization_mode_name,
                IonizationMode.is_active.is_(True),
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
            instrument_id=await instrument_id_for(
                session, ionization_mode_data.instrument
            ),
        )

        # Step 4: Add to session and commit
        session.add(new_ionization_mode)
        await session.commit()

        # Step 5: Refresh and return
        await session.refresh(new_ionization_mode)

    (record,) = await listed([new_ionization_mode])
    await emit_record_created(
        record_type="ionization_mode",
        record_id=new_ionization_mode.ionization_mode_id,
        record=record,
    )
    return {
        "message": "Ionization mode created successfully.",
        "data": record,
    }


@api_controller()
async def get_ionization_mode(ionization_mode_id: str) -> dict:
    """
    Retrieves a single ionization mode by ID.

    By ID alone: a token no longer identifies one mode, since two instruments
    may each have one of their own for the same token (#1463). To find the mode
    a file belongs to, resolve its name with
    :func:`resolve_ionization_modes_by_tokens`, which knows the instrument.

    :param ionization_mode_id: Unique ID of the ionization mode to retrieve.
    :return: The requested ionization mode's details.
    """
    async with async_session() as session:
        label = f"with ID {ionization_mode_id}"
        result = await session.execute(
            select(IonizationMode).where(
                IonizationMode.ionization_mode_id == ionization_mode_id
            )
        )
        ionization_mode = result.scalar_one_or_none()

        # Check existence
        if not ionization_mode:
            raise NotFoundException(f"ionization mode {label} not found")

    # Any version, a superseded one included: a sample bound under it
    # names it.
    (record,) = await listed([ionization_mode])
    return {
        "message": f"Ionization mode {ionization_mode.ionization_mode_name} retrieved successfully.",
        "data": record,
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
        "data": await listed(ionization_modes),
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

    A mode is a versioned setup. An edit that changes what a run computes
    from it - the polarity, the mechanisms or a collection - inserts the next
    version and marks this one inactive; the samples bound under this one
    keep it until they are re-processed, and the method bindings move to the
    new one. An edit of the name, the token or the instrument scope changes
    no result and is made on this row in place. Only the current version can
    be edited.

    Steps:
    - Fetch the ionization mode by its ID from the database.
    - Check for token conflicts if token is being updated.
    - Validate mechanism-polarity consistency.
    - Allow changing the calibration/diagnostic collection (but not clearing it).
    - Check for conflicts with existing acquisition batches.
    - Block updates if any affected batch is currently processing.
    - Flag affected batches: "recalibrate" if the calibration collection changed,
      otherwise "rematch" if mechanisms or the diagnostic collection changed.
    - Mint the next version, or update the fields in place.
    - Commit the changes and return the mode as it now stands.

    :param ionization_mode_id: The ID of the ionization mode to update.
    :param ionization_mode_data: The data to update.
    :return: A dictionary containing the updated ionization mode data.
    """
    async with async_session() as session:
        # Get the existing ionization mode, locked: two edits of one version
        # would otherwise both read it active and each mint a successor. The
        # second waits here, then reads the row the first left inactive. The
        # lock is FOR NO KEY UPDATE, which the foreign-key checks of rows that
        # bind to the mode do not wait for: under FOR UPDATE an ingest that
        # binds a file to it would wait for the edit, and deadlock with the
        # edit's repointing of the binding it holds.
        stmt = (
            select(IonizationMode)
            .where(IonizationMode.ionization_mode_id == ionization_mode_id)
            .with_for_update(key_share=True)
        )
        result = await session.execute(stmt)
        ionization_mode = result.scalar_one_or_none()

        if not ionization_mode:
            raise NotFoundException(
                f"Ionization mode with ID {ionization_mode_id} not found"
            )
        if not ionization_mode.is_active:
            raise SupersededVersionError(
                f"Version {ionization_mode.version} of ionization mode "
                f"'{ionization_mode.ionization_mode_name}' has been replaced by a "
                "later version, and is kept for the samples bound under it. "
                "Edit the current version."
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

        # Check for token conflicts if the token or its scope is being
        # updated. The scope matters as much as the token: widening a mode from
        # one instrument to all of them can collide a token that was fine while
        # only that instrument's names were matched against it.
        effective_token = update_data.get(
            "ionization_mode_token", ionization_mode.ionization_mode_token
        )
        effective_instrument = update_data.get("instrument", ionization_mode.instrument)
        changed = (
            effective_token != ionization_mode.ionization_mode_token
            or effective_instrument != ionization_mode.instrument
        )
        if effective_token and changed:
            if not await token_is_unique(
                effective_token,
                ignore_id=ionization_mode_id,
                instrument=effective_instrument,
            ):
                raise ValueError(
                    f"Ionization mode with similar token as '{effective_token}'"
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

        # A purity left out, or sent as null, is the one the mode has.
        if update_data.get("label_purity") is None:
            update_data.pop("label_purity", None)

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

        # Find affected sample batches: those holding samples under any
        # version of this mode. A sample an earlier edit left on its version
        # moves onto this one when it is re-processed, so this edit is as much
        # its batch's as the current version's samples'.
        affected_batch_ids_result = await session.execute(
            select(distinct(SampleItem.sample_batch_id)).where(
                SampleItem.ionization_mode_id.in_(
                    await lineage(session, ionization_mode_id)
                )
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
        named = [ionization_mode.ionization_mode_name]
        if update_data.get("ionization_mode_name") is not None:
            named.append(update_data["ionization_mode_name"])
        affected_acq_batches = await session.execute(
            select(SampleBatch).where(
                and_(
                    SampleBatch.sample_batch_type == "ACQUISITION",
                    or_(*(_named_for_mode(name) for name in named)),
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
                    case "label_purity":
                        # The reagent's purity renames nothing either, and is
                        # set on exactly the modes that route files, as the
                        # instrument scope below is.
                        continue
                    case "ionization_mechanism_ids" | "ionization_mode_polarity":
                        # Mechanisms and polarity can be updated (triggers rematch)
                        continue
                    case "instrument":
                        # The scope only decides which file names this mode's
                        # token is matched against from now on. It renames
                        # nothing - an acquisition batch is named after the
                        # mode, not its scope - and rebinds nothing, since a
                        # sample item holds the mode it was bound to. Refusing
                        # it here would make the feature unreachable on exactly
                        # the modes it is for: any mode that has ever routed a
                        # file has acquisition batches.
                        continue
                    case _:
                        if getattr(ionization_mode, key) != value:
                            raise ValueError(
                                f"Cannot update ionization mode '{ionization_mode.ionization_mode_name}', "
                                f"as it is in use for one or more acquisition batches."
                            )

        # A change to what a run computes is a new version; the rest is made
        # in place. The row being replaced goes inactive first: the token and
        # system-key indexes hold among the active rows, and the new version
        # usually shares both.
        minted = any(
            field in update_data
            and not _same_value(
                field, update_data[field], getattr(ionization_mode, field)
            )
            for field in VERSIONED_FIELDS
        )
        if minted:
            current = make_version(ionization_mode, update_data)
            ionization_mode.is_active = False
            await session.flush()
            session.add(current)
            await session.flush()
            await repoint_routing(
                session, ionization_mode_id, current.ionization_mode_id
            )
        else:
            current = ionization_mode
            for field, value in update_data.items():
                setattr(current, field, value)
        if "instrument" in update_data:
            current.instrument_id = await instrument_id_for(session, current.instrument)

        # Commit and return
        await session.commit()
        await session.refresh(current)

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

    (record,) = await listed([current])
    if minted:
        # The listing holds current versions only: the replaced one leaves it
        # and the new one joins it.
        await emit_record_deleted(
            record_type="ionization_mode", record_id=ionization_mode_id
        )
        await emit_record_created(
            record_type="ionization_mode",
            record_id=current.ionization_mode_id,
            record=record,
        )
    else:
        await emit_record_updated(
            record_type="ionization_mode",
            record_id=ionization_mode_id,
            record=record,
        )
    return {
        "message": (
            f"Ionization mode {current.ionization_mode_name} updated: version "
            f"{current.version}."
            if minted
            else f"Ionization mode {current.ionization_mode_name} updated successfully."
        ),
        "data": record,
    }


@api_controller()
async def delete_ionization_mode(ionization_mode_id: str) -> dict:
    """
    Deletes an ionization mode by its unique identifier, every version of it.

    Steps:
    1. Fetch the ionization mode by its ID from the database: its current
       version, since a superseded one is kept for its samples.
    2. Check if any version of it is associated with samples. If so, raise an exception.
    3. Delete every version and commit the changes.

    :param ionization_mode_id: The unique identifier of the ionization mode to delete.
    :return: A dictionary with a success message.
    """
    async with async_session() as session:
        # Step 1: Fetch the ionization mode, locked against an edit minting
        # a version this delete would not see
        ionization_mode = await session.get(
            IonizationMode, ionization_mode_id, with_for_update=True
        )

        if not ionization_mode:
            raise NotFoundException(
                f"Ionization mode with ID '{ionization_mode_id}' not found"
            )

        if not ionization_mode.is_active:
            raise SupersededVersionError(
                f"Version {ionization_mode.version} of ionization mode "
                f"'{ionization_mode.ionization_mode_name}' has been replaced by a "
                "later version, and is kept for the samples bound under it. "
                "Delete the current version to delete the mode."
            )

        if ionization_mode.system_key:
            raise ValueError(
                f"Ionization mode '{ionization_mode.ionization_mode_name}' is one "
                "Mascope ships and cannot be deleted"
            )

        # Step 2: Check for associations with samples, under any version
        versions = await lineage(session, ionization_mode_id)
        result = await session.execute(
            select(SampleItem).where(SampleItem.ionization_mode_id.in_(versions))
        )
        if result.scalars().first():
            raise ValueError(
                f"Ionization mode '{ionization_mode.ionization_mode_name}' is associated with "
                "existing samples and cannot be deleted"
            )

        # Step 3: Delete every version and commit changes
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(versions)
            )
        )
        await session.commit()

    await emit_record_deleted(
        record_type="ionization_mode",
        record_id=ionization_mode_id,
    )
    return {
        "message": f"Ionization mode {ionization_mode.ionization_mode_name} deleted successfully.",
    }
