"""
Target isotope service layer.
"""

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import joinedload

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.ionization.modes.util import (
    fetch_batch_ionization_mechanism_ids,
)
from mascope_backend.db import (
    IonizationMechanism,
    SampleBatch,
    TargetCollection,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)


async def get_target_isotopes(
    target_ion_id: str | None = None,
    min_mz: float | None = None,
    max_mz: float | None = None,
    min_relative_abundance: float | None = None,
    max_relative_abundance: float | None = None,
    resolution: str | None = None,
    target_compound_ids: list[str] | None = None,
    ionization_mechanism_ids: list[str] | None = None,
    sample_batch_id: str | None = None,
    target_collection_id: str | None = None,
    show_target_collection: bool = False,
    show_match_params: bool = False,
    show_ionization_mechanism: bool = False,
    sort: str | None = None,
    order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a list of target isotopes based on various filters with optional related entity details.

    This function supports complex querying with options to include details about target collections,
    target ion filter parameters, and ionization mechanism information including polarity data.

    Steps:
    1. Start a session and define the base query with joins to necessary tables.
    2. Apply filters based on input parameters.
    3. Conditionally join with ionization mechanisms when needed.
    4. Adjust the query based on optional filters for collections or batch details.
    5. Apply sorting if specified.
    6. Count total matching isotopes for pagination.
    7. Apply pagination and execute the query.
    8. Construct the response data, including additional details if specified.

    :param target_ion_id: Filter isotopes by the associated target ion ID.
    :type target_ion_id: str | None
    :param min_mz: Minimum m/z value to filter isotopes.
    :type min_mz: float | None
    :param max_mz: Maximum m/z value to filter isotopes.
    :type max_mz: float | None
    :param min_relative_abundance: Minimum relative abundance percentage to filter isotopes.
    :type min_relative_abundance: float | None
    :param max_relative_abundance: Maximum relative abundance percentage to filter isotopes.
    :type max_relative_abundance: float | None
    :param resolution: Required isotope resolution ('LOW' or 'HIGH').
    :type resolution: str | None
    :param target_compound_ids: List of target compound IDs to filter isotopes.
        None applies no compound filter; an empty list matches no isotopes.
    :type target_compound_ids: list[str] | None
    :param ionization_mechanism_ids: List of ionization mechanism IDs to filter isotopes.
    :type ionization_mechanism_ids: list[str] | None
    :param sample_batch_id: Sample batch ID to filter isotopes related to specific batches.
    :type sample_batch_id: str | None
    :param target_collection_id: Target collection ID to filter isotopes by specific collections.
    :type target_collection_id: str | None
    :param show_target_collection: Whether to include target collection details in the response.
    :type show_target_collection: bool
    :param show_match_params: Whether to include filter parameters from the parent target ion.
    :type show_match_params: bool
    :param show_ionization_mechanism: Whether to include ionization mechanism details including polarity.
    :type show_ionization_mechanism: bool
    :param sort: Column name to sort the results by.
    :type sort: str | None
    :param order: Sort order ('asc' for ascending, 'desc' for descending).
    :type order: str | None
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: Optional[int], optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: Optional[int], optional
    :return: Dictionary containing the total count and list of detailed isotopes matching the filters.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Step 1: Construct the base query
        stmt = select(TargetIsotope).join(
            TargetIon, TargetIon.target_ion_id == TargetIsotope.target_ion_id
        )

        # Step 2: Apply filters based on provided arguments
        if target_ion_id:
            stmt = stmt.filter(TargetIsotope.target_ion_id == target_ion_id)
        if min_mz is not None:
            stmt = stmt.filter(TargetIsotope.mz >= min_mz)
        if max_mz is not None:
            stmt = stmt.filter(TargetIsotope.mz <= max_mz)
        if resolution is not None:
            stmt = stmt.filter(TargetIsotope.resolution == resolution)
        if min_relative_abundance is not None:
            stmt = stmt.filter(
                TargetIsotope.relative_abundance >= min_relative_abundance
            )
        if max_relative_abundance is not None:
            stmt = stmt.filter(
                TargetIsotope.relative_abundance <= max_relative_abundance
            )
        # An explicitly empty list means "match nothing", not "no filter":
        # skipping the filter here returned every isotope in the database,
        # across all collections and workspaces, to a caller that had asked
        # for the isotopes of an empty compound set. Only None is no filter.
        if target_compound_ids is not None:
            stmt = stmt.where(TargetIon.target_compound_id.in_(target_compound_ids))

        if ionization_mechanism_ids:
            stmt = stmt.where(
                TargetIon.ionization_mechanism_id.in_(ionization_mechanism_ids)
            )
        # Add ionization mechanism columns if requested
        if show_ionization_mechanism:
            stmt = stmt.join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            stmt = stmt.add_columns(
                IonizationMechanism.ionization_mechanism_polarity,
                IonizationMechanism.ionization_mechanism,
            )

        if show_match_params:
            stmt = stmt.add_columns(
                TargetIon.filter_params,
            )
        # Step 3: Adjust the query based on non-basic filters
        if sample_batch_id or target_collection_id or show_target_collection:
            stmt = stmt.join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetIon.target_compound_id,
            )

            # Filter ions by sample_batch_id if specified
            if sample_batch_id:
                # Fetch sample batch and related ion mechanisms and target collection ids
                result = await session.execute(
                    select(SampleBatch)
                    .options(joinedload(SampleBatch.target_collection))
                    .where(SampleBatch.sample_batch_id == sample_batch_id)
                )
                sample_batch = result.unique().scalar_one_or_none()
                if not sample_batch:
                    raise NotFoundException(
                        f"Sample batch with id {sample_batch_id} not found"
                    )

                batch_ionization_mechanism_ids = (
                    await fetch_batch_ionization_mechanism_ids(sample_batch_id)
                )
                # Since we have eager loaded the target_collection, we can fetch the m directly from sample_batch
                target_collection_ids = [
                    tc.target_collection_id for tc in sample_batch.target_collection
                ]

                # Filter isotopes by batch ionization_mechanism_ids and target_collection_ids
                stmt = stmt.where(
                    TargetCompoundInTargetCollection.target_collection_id.in_(
                        target_collection_ids
                    ),
                    TargetIon.ionization_mechanism_id.in_(
                        batch_ionization_mechanism_ids
                    ),
                ).distinct(TargetIsotope.target_isotope_id)

            # Filter isotopes by target_collection_id if specified
            if target_collection_id:
                stmt = stmt.filter(
                    TargetCompoundInTargetCollection.target_collection_id
                    == target_collection_id
                )

            # Add the target_collection_id to be shown
            if show_target_collection:
                stmt = stmt.join(
                    TargetCollection,
                    TargetCollection.target_collection_id
                    == TargetCompoundInTargetCollection.target_collection_id,
                )
                stmt = stmt.add_columns(
                    TargetCompoundInTargetCollection.target_collection_id,
                    TargetCollection.target_collection_name,
                    TargetCollection.target_collection_type,
                )

        # Step 4: Apply sorting
        if sort:
            if order == "desc":
                stmt = stmt.order_by(desc(getattr(TargetIsotope, sort)))
            else:
                stmt = stmt.order_by(asc(getattr(TargetIsotope, sort)))

        # Step 5: Get total count
        total = await session.scalar(select(func.count()).select_from(stmt))

        # Step 6: Apply pagination
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)

    # Step 7: Construct the response data
    data = []
    for row in result.all():
        # When show_target_collection is true, include target_collection_id
        isotope_data = row.TargetIsotope.to_dict()
        if show_match_params:
            isotope_data["filter_params"] = row.filter_params
        if show_ionization_mechanism:
            isotope_data["ionization_mechanism_polarity"] = (
                row.ionization_mechanism_polarity
            )
            isotope_data["ionization_mechanism"] = row.ionization_mechanism

        if show_target_collection:
            isotope_data["target_collection_id"] = row.target_collection_id
            isotope_data["target_collection_name"] = row.target_collection_name
            isotope_data["target_collection_type"] = row.target_collection_type
        data.append(isotope_data)

    return {
        "message": "Target isotopes retrieved successfully.",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_target_isotope(target_isotope_id: str) -> dict:
    """
    Retrieves a single target isotope by its unique ID.

    Steps:
    1. Execute a query to fetch the target isotope with the specified ID.
    2. Check if the target isotope exists. If not, raise a NotFoundException.
    3. Return the target isotope's details as a dictionary.

    :param target_isotope_id: Unique identifier of the target isotope to retrieve.
    :type target_isotope_id: str
    :raises NotFoundException: If the target isotope with the given ID is not found.
    :return: The requested target isotope's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch target isotope by ID
        target_isotope = await session.get(TargetIsotope, target_isotope_id)

        if not target_isotope:
            # Step 2: If target isotope not found, raise exception
            raise NotFoundException(
                f"Target isotope with ID '{target_isotope_id}' not found"
            )
    # Step 3: Return target isotope details
    return {
        "message": "Target isotope retrieved successfully.",
        "data": target_isotope.to_dict(),
    }
