"""
Ionization mechanisms controller for managing ionization mechanism operations.
"""

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import (
    delete,
    func,
    select,
)

from mascope_backend.api.controllers.target.ions.target_ions_controller import (
    create_target_ions,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.ionization_mechanisms.ionization_mechanism_pydantic_model import (
    IonizationMechanismCreate,
    IonizationMechanismRead,
    IonizationMechanismSortColumn,
)
from mascope_backend.api.new.ionization.modes.util import system_mode_adopted
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    TargetCompound,
    TargetIon,
    TargetIsotope,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import (
    emit_record_created,
    emit_record_deleted,
)
from mascope_tools.composition.mechanism_notation import mechanism_spellings


#: Mechanisms already reported as unwritable, so a stored row does not log on
#: every read of the listing it appears in.
_reported_unwritable: set[str] = set()


def read_ionization_mechanism(ionization_mechanism) -> dict:
    """
    One stored mechanism as a response body, reporting it if it is unwritable.

    :param ionization_mechanism: The stored ``IonizationMechanism`` row.
    :return: The row's fields.
    """
    report_if_unwritable(ionization_mechanism)
    return IonizationMechanismRead.model_validate(ionization_mechanism).model_dump()


def report_if_unwritable(ionization_mechanism) -> None:
    """
    Log a stored mechanism the write rules refuse, once per id per process.

    Reads report a stored row as it is, which is what keeps one such row from
    failing a whole listing. Nothing else would then say it is there, while
    clients go on offering it - as a mechanism to bind an ionization mode to,
    for instance, where an empty modification such as "++" yields atomless
    ions. Logged at WARNING because it is the operator who can fix the row.

    :param ionization_mechanism: The stored ``IonizationMechanism`` row.
    """
    if ionization_mechanism.ionization_mechanism_id in _reported_unwritable:
        return
    try:
        IonizationMechanismCreate.model_validate(ionization_mechanism)
    except ValidationError as e:
        _reported_unwritable.add(ionization_mechanism.ionization_mechanism_id)
        reasons = "; ".join(error["msg"] for error in e.errors())
        runtime.logger.warning(
            f"Stored ionization mechanism "
            f"'{ionization_mechanism.ionization_mechanism}' "
            f"({ionization_mechanism.ionization_mechanism_id}) is one the "
            f"current rules refuse: {reasons}. It is listed as it is; "
            f"creating it again would be rejected."
        )


@api_controller()
async def get_ionization_mechanisms(
    ionization_mechanism_polarity: str | None = None,
    ionization_mechanism: list | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of ionization mechanisms, optionally filtered by polarity or mechanism,
    and sorted by a specified column.

    Steps:
    1. Construct a SQLAlchemy query to select all ionization mechanisms.
    2. Apply filtering based on provided parameters.
    3. Apply sorting based on the provided sort and order parameters.
    4. Apply pagination based on the provided page and limit parameters.
    5. Execute the query and fetch the results.
    6. Convert the results into a list of dictionaries for JSON serialization.

    :param ionization_mechanism_polarity: Filter by polarity, defaults to None.
    :param ionization_mechanism: Filter by mechanism, defaults to None.
    :type ionization_mechanism: list | None
    :param sort: Column to sort by, defaults to None.
    :param order: Sorting order, defaults to None.
    :param page: Page number for pagination, defaults to None (no pagination).
    :param limit: Number of items per page, defaults to None (no pagination).
    :return: A dictionary with the total count and a list of ionization mechanisms.
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        stmt = select(IonizationMechanism)

        # Step 2: Apply filters if specified
        if ionization_mechanism_polarity:
            stmt = stmt.filter(
                IonizationMechanism.ionization_mechanism_polarity
                == ionization_mechanism_polarity
            )
        if ionization_mechanism:
            # A row not yet rewritten to the standard notation holds the
            # legacy spelling, and has to be found by either.
            stmt = stmt.where(
                IonizationMechanism.ionization_mechanism.in_(
                    mechanism_spellings(ionization_mechanism)
                )
            )

        # Step 3: Apply sorting
        if sort:
            stmt = stmt.order_by(
                order_by_column(
                    IonizationMechanism, sort, order, IonizationMechanismSortColumn
                )
            )

        # Step 4: Apply pagination
        total = await session.scalar(select(func.count()).select_from(stmt))
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        # Step 5: Execute the query
        result = await session.execute(stmt)
        ionization_mechanisms = result.scalars().all()

        # Step 6: Return results
        return {
            "message": "Retrieved ionization mechanisms successfully.",
            "results": total,
            "data": [
                read_ionization_mechanism(ionization_mechanism)
                for ionization_mechanism in ionization_mechanisms
            ],
        }


@api_controller()
async def get_ionization_mechanism(ionization_mechanism_id: str) -> dict:
    """
    Retrieves a single ionization mechanism by its unique ID.

    Steps:
    - Execute a query to fetch the ionization mechanism with the specified ID.
    - Check if the ionization mechanism exists. If not, raise a NotFoundException.
    - Retrieve all ionization modes that use this ionization mechanism.
    - Return the ionization mechanism's details as a dictionary.

    :param ionization_mechanism_id: Unique identifier of the ionization mechanism to retrieve.
    :raises NotFoundException: If the ionization mechanism with the given ID is not found.
    :return: The requested ionization mechanism's details.
    """
    async with async_session() as session:
        # -- Fetch ionization mechanism by ID -- #
        ionization_mechanism = await session.get(
            IonizationMechanism, ionization_mechanism_id
        )

        # -- If ionization mechanism not found, raise exception -- #
        if not ionization_mechanism:
            raise NotFoundException(
                f"Ionization mechanism with ID '{ionization_mechanism_id}' not found"
            )

        # -- Retrieve ionization modes using the specified ionization mechanism -- #
        affected_ion_modes = []
        result = await session.execute(select(IonizationMode))
        all_ion_modes = result.scalars().all()
        for ion_mode in all_ion_modes:
            if ionization_mechanism_id in ion_mode.ionization_mechanism_ids:
                affected_ion_modes.append(ion_mode)

        affected_ion_mode_info = [
            {
                "ion_mode_id": ion_mode.ionization_mode_id,
                "ion_mode_name": ion_mode.ionization_mode_name,
            }
            for ion_mode in affected_ion_modes
        ]

        # -- Return ionization mechanism details with ionization modes -- #
        ionization_mechanism_data = read_ionization_mechanism(ionization_mechanism)
        ionization_mechanism_data["ionization_modes_count"] = len(affected_ion_modes)
        ionization_mechanism_data["ionization_modes"] = affected_ion_mode_info
        return {
            "message": f"Ionization mechanism '{ionization_mechanism.ionization_mechanism}' retrieved successfully.",
            "data": ionization_mechanism_data,
        }


@api_controller()
async def create_ionization_mechanism(
    ionization_mechanism_create: IonizationMechanismCreate,
) -> dict:
    """
    Creates a new ionization mechanism and generates corresponding ions for each existing target compound in the database.

    Steps:
    - Check if the ionization mechanism already exists using the get_ionization_mechanisms function.
    - Create a new ionization mechanism instance and add it to the session.
    - Fetch all target compounds from the database.
    - For each target compound, create target ions with the new ionization mechanism.
    - Commit the transaction to persist changes to the database.
    - Return the created ionization mechanism's details with a success message.

    :param ionization_mechanism: Ionization mechanism to create
    :type ionization_mechanism: IonizationMechanismCreate
    :raises HTTPException: If the ionization mechanism already exists.
    :raises NotFoundException: If the ionization mechanism is not found after creation.
    :return: Created ionization mechanism details.
    :rtype: dict
    """
    # --- Check if the ionization mechanism already exists --- #
    existing_mechanisms = await get_ionization_mechanisms(
        ionization_mechanism=[ionization_mechanism_create.ionization_mechanism]
    )

    if existing_mechanisms["results"] != 0:
        raise HTTPException(
            status_code=409,
            detail=f"Ionization mechanism '{ionization_mechanism_create.ionization_mechanism}' already exists",
        )

    new_ionization_mechanism = IonizationMechanism(
        ionization_mechanism_id=gen_id(11),
        **ionization_mechanism_create.model_dump(),
    )

    # --- Create a new ionization mechanism instance and add it to the session. --- #
    async with async_session() as session:
        session.add(new_ionization_mechanism)

        # --- Fetch all target compounds --- #
        stmt = select(TargetCompound)
        result = await session.execute(stmt)
        target_compounds = result.scalars().all()

        # --- Create target ions with new mechanism for each compound --- #
        for target_compound in target_compounds:
            # Create target ions for the compound
            await create_target_ions(
                target_compound=target_compound,
                ionization_mechanisms=[new_ionization_mechanism],
                independent_transaction=False,
                session=session,
            )

        # --- Commit the transaction --- #
        await session.commit()
        await session.refresh(new_ionization_mechanism)

    if not new_ionization_mechanism:
        raise NotFoundException(
            f"Ionization mechanism with ID '{new_ionization_mechanism.ionization_mechanism_id}' not found after it should have been created"
        )
    ionization_mechanism_data = IonizationMechanismRead.model_validate(
        new_ionization_mechanism
    ).model_dump()

    # --- Emit creation event --- #
    await emit_record_created(
        record_type="ionization_mechanism",
        record_id=new_ionization_mechanism.ionization_mechanism_id,
        record=ionization_mechanism_data,
    )

    # --- Return created ionization mechanism details with a success message --- #
    return {
        "message": f"Ionization mechanism '{new_ionization_mechanism.ionization_mechanism}' was created successfully.",
        "data": ionization_mechanism_data,
    }


async def _release_unadopted_system_modes(
    session, ionization_mechanism_id: str
) -> set[str]:
    """Delete the seeded modes holding this mechanism that nobody adopted.

    Adoption is ``system_mode_adopted``, the same predicate the listing reads,
    so a mode this refuses to release is one the deployment can actually see.
    A seeded mode that is not adopted carries nothing of the deployment's.

    :param session: The session the deletion runs in.
    :param ionization_mechanism_id: The mechanism about to be deleted.
    :return: The ids released. This session has not committed them, so the
        caller cannot tell they are gone by reading another one.
    :rtype: set[str]
    """
    released: set[str] = set()
    modes = (
        (
            await session.execute(
                select(IonizationMode).where(
                    IonizationMode.system_key.is_not(None),
                    ~system_mode_adopted(),
                )
            )
        )
        .scalars()
        .all()
    )
    for mode in modes:
        if ionization_mechanism_id not in mode.ionization_mechanism_ids:
            continue
        await session.delete(mode)
        released.add(mode.ionization_mode_id)
    return released


@api_controller()
async def delete_ionization_mechanism(ionization_mechanism_id: str) -> dict:
    """
    Deletes an ionization mechanism by its ID, ensuring it's not used in any ionization mode

    Steps:
    - Retrieve the ionization mechanism along with any referencing ionization modes.
    - If referenced in any ionization modes, raise an ApiException preventing deletion.
    - If no ionization modes use this ionization mechanism, delete related TargetIsotope and TargetIon records.
    - Delete the ionization mechanism from the database.
    - Emit deletion event via socket.

    :param ionization_mechanism_id: The unique identifier of the ionization mechanism to delete.
    :type ionization_mechanism_id: str
    :raises ApiException: If the ionization mechanism is referenced by any ionization modes.
    :raises NotFoundException: If no ionization mechanism is found with the provided ID.
    :return: Deleted ionization mechanism message.
    :rtype: dict
    """
    # -- Fetch the ionization mechanism -- #
    async with async_session() as session:
        ionization_mechanism = await session.get(
            IonizationMechanism, ionization_mechanism_id
        )
        if not ionization_mechanism:
            raise NotFoundException(
                f"Ionization mechanism with ID '{ionization_mechanism_id}' not found"
            )

        # -- Drop the seeded modes nobody adopted that hold this mechanism -- #
        # A mode Mascope ships cannot be deleted through its own route, so one
        # built on a mechanism the deployment is retiring would pin that
        # mechanism for good. Unadopted, the mode has no collections and no
        # samples, so nothing is lost; seeding leaves it out from now on,
        # because the mechanism it needs is gone.
        released = await _release_unadopted_system_modes(
            session, ionization_mechanism_id
        )

        # -- Find the ionization modes still referencing it -- #
        # Read in this session rather than through get_ionization_mechanism,
        # which opens its own: the modes just released are deleted here and
        # not yet committed, so another session would still count them and
        # refuse a deletion nothing is blocking.
        blocking = [
            mode
            for mode in (await session.execute(select(IonizationMode))).scalars().all()
            if ionization_mechanism_id in mode.ionization_mechanism_ids
            and mode.ionization_mode_id not in released
        ]

        # -- Prevent deletion if referenced in any ionization modes -- #
        if blocking:
            raise ApiException(
                f"Ionization mechanism '{ionization_mechanism.ionization_mechanism}' cannot be deleted as it is used in"
                f" {len(blocking)} ionization modes.",
                {
                    "ionization_modes": [
                        {
                            "ion_mode_id": mode.ionization_mode_id,
                            "ion_mode_name": mode.ionization_mode_name,
                        }
                        for mode in blocking
                    ]
                },
                400,
            )

        # -- Manually delete related TargetIsotope and TargetIon records -- #

        # Delete TargetIsotope records
        delete_target_isotope_query = delete(TargetIsotope).where(
            TargetIsotope.target_ion_id.in_(
                select(TargetIon.target_ion_id).where(
                    TargetIon.ionization_mechanism_id == ionization_mechanism_id
                )
            )
        )

        await session.execute(delete_target_isotope_query)

        # Delete TargetIon records
        delete_target_ion_query = delete(TargetIon).where(
            TargetIon.ionization_mechanism_id == ionization_mechanism_id
        )
        await session.execute(delete_target_ion_query)

        # Delete the IonizationMechanism record
        delete_ionization_mechanism_query = delete(IonizationMechanism).where(
            IonizationMechanism.ionization_mechanism_id == ionization_mechanism_id
        )
        await session.execute(delete_ionization_mechanism_query)

        # Commit the transaction
        await session.commit()

    # -- Emit deletion event -- #
    await emit_record_deleted(
        record_type="ionization_mechanism",
        record_id=ionization_mechanism_id,
    )

    return {
        "message": f"Ionization mechanism '{ionization_mechanism.ionization_mechanism}' was deleted successfully."
    }
