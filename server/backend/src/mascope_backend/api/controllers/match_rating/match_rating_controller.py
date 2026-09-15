import json
from datetime import datetime, timezone

from sqlalchemy import (
    func,
    select,
)

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.match_rating.match_rating_pydantic_model import (
    MatchRatingCreate,
    MatchRatingSortColumn,
)
from mascope_backend.db import MatchRating, async_session
from mascope_backend.db.id import gen_id


@api_controller()
async def get_match_ratings(
    sample_item_id: str = None,
    target_ion_id: str = None,
    rating: int = None,
    sort: str = None,
    order: str = None,
    page: int | None = None,
    limit: int | None = None,
    datetime_min: datetime = None,
    datetime_max: datetime = None,
) -> dict:
    """
    Retrieves a paginated list of match ratings, optionally filtered by various parameters, and sorted by a specified column.

    Steps:
    1. Construct a SQLAlchemy query to select all match ratings.
    2. Apply filters based on provided parameters.
    3. Apply sorting based on the provided sort and order parameters.
    4. Apply pagination based on the provided page and limit parameters.
    5. Execute the query and fetch the results.
    6. Deserialize checklist and environment from JSON strings.
    7. Convert the results into a list of dictionaries for JSON serialization.

    :param sample_item_id: Filter by sample item ID, defaults to None.
    :type sample_item_id: str, optional
    :param target_ion_id: Filter by target ion ID, defaults to None.
    :type target_ion_id: str, optional
    :param rating: Filter by rating value, defaults to None.
    :type rating: int, optional
    :param sort: Column to sort by, defaults to None.
    :type sort: str, optional
    :param order: Sorting order, defaults to None.
    :type order: str, optional
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None, optional
    :param datetime_min: Filter by minimum datetime, defaults to None.
    :type datetime_min: datetime, optional
    :param datetime_max: Filter by maximum datetime, defaults to None.
    :type datetime_max: datetime, optional
    :return: Dictionary containing total count and list of match ratings.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Step 1: Construct base query
        stmt = select(MatchRating)

        # Step 2: Apply filters
        if sample_item_id:
            stmt = stmt.filter(MatchRating.sample_item_id == sample_item_id)
        if target_ion_id:
            stmt = stmt.filter(MatchRating.target_ion_id == target_ion_id)
        if rating is not None:
            stmt = stmt.filter(MatchRating.rating == rating)
        if datetime_min:
            stmt = stmt.where(MatchRating.match_rating_utc_created >= datetime_min)
        if datetime_max:
            stmt = stmt.where(MatchRating.match_rating_utc_created <= datetime_max)

        # Step 3: Apply sorting
        if sort:
            stmt = stmt.order_by(
                order_by_column(MatchRating, sort, order, MatchRatingSortColumn)
            )

        # Step 4: Apply pagination
        total = await session.scalar(select(func.count()).select_from(stmt))
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)

        # Step 5: Execute query
        result = await session.execute(stmt)
        match_ratings = result.scalars().all()

        # Step 6: Deserialize from JSON string checklist and environment
        for match_rating in match_ratings:
            match_rating.checklist = (
                json.loads(match_rating.checklist) if match_rating.checklist else {}
            )
            match_rating.environment = (
                json.loads(match_rating.environment) if match_rating.environment else {}
            )

        # Step 7: Return serialized results
        return {
            "message": "Match ratings retrieved successfully.",
            "results": total,
            "data": [match_rating.to_dict() for match_rating in match_ratings],
        }


@api_controller()
async def get_match_rating(match_rating_id: str) -> dict:
    """
    Retrieves a single match rating by its unique ID.

    Steps:
    1. Execute a query to fetch the match rating with the specified ID.
    2. Check if the match rating exists. If not, raise a NotFoundException.
    3. Deserialize checklist and environment from JSON strings.
    4. Return the match rating's details as a dictionary.

    :param match_rating_id: Unique identifier of the match rating to retrieve.
    :type match_rating_id: str
    :raises NotFoundException: If the match rating with the given ID is not found.
    :return: Dictionary containing the match rating's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch match rating by ID
        match_rating = await session.get(MatchRating, match_rating_id)

        # Step 2: Check existence
        if not match_rating:
            raise NotFoundException(
                f"Match rating with ID '{match_rating_id}' not found"
            )

        # Step 3: Deserialize checklist and environment
        match_rating.checklist = (
            json.loads(match_rating.checklist) if match_rating.checklist else {}
        )
        match_rating.environment = (
            json.loads(match_rating.environment) if match_rating.environment else {}
        )

        # Step 4: Return match rating details
        return {
            "message": f"Match rating '{match_rating_id}' retrieved successfully.",
            "data": match_rating.to_dict(),
        }


@api_controller()
async def create_match_rating(match_rating: MatchRatingCreate) -> dict:
    """
    Creates a new match rating record in the database.

    Steps:
    1. Create a new MatchRating instance from the provided Pydantic model.
    2. Add the new instance to the database session and commit.
    3. Refresh the instance to ensure it's updated with any database-side generated values.
    4. Return the created match rating details.

    :param match_rating: Pydantic model containing match rating details to create
    :type match_rating: MatchRatingCreate
    :return: Dictionary containing created match rating details
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Create new match rating instance
        new_match_rating = MatchRating(
            match_rating_id=gen_id(32),
            sample_item_id=match_rating.sample_item_id,
            target_ion_id=match_rating.target_ion_id,
            rating=match_rating.rating,
            # Convert Pydantic model to dictionary and then to JSON string
            checklist=json.dumps(
                match_rating.checklist.model_dump() if match_rating.checklist else {}
            ),
            environment=json.dumps(match_rating.environment.model_dump()),
            match_rating_utc_created=datetime.now(timezone.utc),
        )

        # Step 2: Add to session and commit
        session.add(new_match_rating)
        await session.commit()

        # Step 3: Refresh instance
        await session.refresh(new_match_rating)

        if not new_match_rating:
            raise NotFoundException("Failed to create match rating")

        # Deserialize from JSON string for response
        new_match_rating.checklist = json.loads(new_match_rating.checklist)
        new_match_rating.environment = json.loads(new_match_rating.environment)

        # Step 4: Return created match rating details
        return {
            "message": "Rating submitted successfully. Thanks for your feedback!",
            "data": new_match_rating.to_dict(),
        }
