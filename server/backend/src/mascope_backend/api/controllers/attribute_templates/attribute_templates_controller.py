from sqlalchemy import (
    func,
    select,
)

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.attribute_templates.attribute_template_pydantic_model import (
    ATTRIBUTE_TEMPLATE_SORT_COLUMNS,
    AttributeTemplateCreateBody,
    AttributeTemplateUpdateBody,
)
from mascope_backend.db import AttributeTemplate, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.socket.records.service import emit_record_reload


@api_controller()
async def get_attribute_templates(
    sort: str = "name",
    order: str = "asc",
    page: int | None = None,
    limit: int | None = None,
):
    """
    Retrieves a paginated list of attribute templates, optionally sorted.

    Steps:
    1. Construct the query with optional sorting.
    2. Calculate total count for pagination.
    3. Fetch the paginated results.
    4. Return the results and total count.

    :param sort: Column name to sort the results by.
    :type sort: str
    :param order: Order of sorting ('asc' or 'desc').
    :type order: str
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None
    :param limit: Number of results per page, defaults to None (no pagination).
    :type limit: int | None
    :raises ApiException: For handling any exceptions that occur during the process.
    :return: Dictionary containing total count and paginated attribute templates.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Step 1: Construct query
        stmt = select(AttributeTemplate)
        if sort:
            stmt = stmt.order_by(
                order_by_column(
                    AttributeTemplate, sort, order, ATTRIBUTE_TEMPLATE_SORT_COLUMNS
                )
            )
        # Step 2: Count total results
        total = await session.scalar(select(func.count()).select_from(stmt))

        # Step 3: Fetch paginated results
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)
        attribute_templates = result.scalars().all()

    # Step 4: Return results
    return {
        "message": "Attribute templates retrieved successfully.",
        "results": total,
        "data": [template.to_dict() for template in attribute_templates],
    }


@api_controller()
async def get_attribute_template(attribute_template_id: str):
    """
    Retrieves a single attribute template by its ID.

    Steps:
    1. Execute a query to fetch the attribute template with the specified ID.
    2. Check if the attribute template exists. If not, raise a NotFoundException.
    3. Return the attribute template's details as a dictionary.

    :param attribute_template_id: ID of the attribute template to retrieve.
    :type attribute_template_id: str
    :raises NotFoundException: If the attribute template is not found.
    :raises ApiException: For handling any other exceptions that occur.
    :return: The attribute template data.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch attribute template by ID
        template = await session.get(AttributeTemplate, attribute_template_id)
        # Step 2: If attribute template not found, raise exception
        if not template:
            raise NotFoundException(
                f"AttributeTemplate with ID '{attribute_template_id}' not found"
            )
    # Step 3: Return attribute template details
    return {
        "message": f"Attribute template '{template.name}' retrieved successfully.",
        "data": template.to_dict(),
    }


@api_controller()
async def create_attribute_template(template_data: AttributeTemplateCreateBody):
    """
    Creates a new attribute template with the given data.

    Steps:
    1. Convert template fields to a dictionary.
    2. Construct a new AttributeTemplate object and add it to the session.
    3. Commit the transaction and refresh the instance.
    4. Emit an 'template_reload' event.
    5. Return the created attribute template data.

    :param template_data: Data for creating the attribute template.
    :type template_data: AttributeTemplateCreateBody
    :raises ApiException: For handling any exceptions that occur.
    :return: The created attribute template data.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Convert each TemplateField object in the template list to a dictionary
        template_dicts = [field.model_dump() for field in template_data.template]

        # Step 2: Construct new template
        new_template = AttributeTemplate(
            attribute_template_id=gen_id(16),
            name=template_data.name,
            type=template_data.type,
            template=template_dicts,
        )
        session.add(new_template)

        # Step 3: Commit and refresh
        await session.commit()
        await session.refresh(new_template)

    # Step 4: Emit the event to inform the clients about the new template
    await emit_record_reload(record_type="template")

    # Step 5: Return created template
    return {
        "message": f"Attribute template '{new_template.name}' created successfully.",
        "data": new_template.to_dict(),
    }


@api_controller()
async def update_attribute_template(
    attribute_template_id: str, template_data: AttributeTemplateUpdateBody
):
    """
    Updates an existing attribute template with new data.

    Steps:
    1. Fetch the existing attribute template from the database using the provided ID.
    2. Update the template's properties with the new data.
    3. Commit the changes to the database.
    4. Emit an "template_reload" event to notify clients about the updated template.
    5. Return the updated template as a dictionary.

    :param attribute_template_id: The ID of the attribute template to update.
    :type attribute_template_id: str
    :param template_data: New data for updating the attribute template.
    :type template_data: AttributeTemplateUpdateBody
    :raises NotFoundException: Raised if the attribute template with the given ID is not found.
    :raises process_exception: Handles any exceptions that occur during the update process.
    :return: The updated attribute template as a dictionary.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch the existing attribute template from the database using the provided ID.
        template = await session.get(AttributeTemplate, attribute_template_id)
        if not template:
            raise NotFoundException(
                f"AttributeTemplate with ID '{attribute_template_id}' not found"
            )

        # Step 2: Update the template's properties with the new data.
        for key, value in template_data.dict(exclude_unset=True).items():
            setattr(template, key, value)

        # Step 3: Commit the changes to the database.
        await session.commit()

    # Step 4: Emit an "template_reload" event to notify clients about the updated template.
    await emit_record_reload(record_type="template")

    # Step 5: Return the updated template as a dictionary.
    return {
        "message": f"Attribute template '{template.name}' updated successfully.",
        "data": template.to_dict(),
    }


@api_controller()
async def delete_attribute_template(attribute_template_id: str):
    """
    Deletes an attribute template by its ID.

    Steps:
    1. Fetch the attribute template from the database using the provided ID.
    2. Delete the fetched template from the session and commit the changes to the database.
    3. Emit an "template_reload" event to notify clients about the deletion.

    :param attribute_template_id: The ID of the attribute template to delete.
    :type attribute_template_id: str
    :raises NotFoundException: Raised if the attribute template with the given ID is not found.
    :raises process_exception: Handles any exceptions that occur during the deletion process.
    """
    async with async_session() as session:
        # Step 1: Fetch the attribute template from the database using the provided ID.
        template = await session.get(AttributeTemplate, attribute_template_id)
        if not template:
            raise NotFoundException(
                f"AttributeTemplate with ID '{attribute_template_id}' not found"
            )

        # Step 2: Delete the fetched template from the session and commit the changes to the database.
        await session.delete(template)
        await session.commit()

    # Step 3: Emit an "template_reload" event to notify clients about the deletion.
    await emit_record_reload(record_type="template")

    return {
        "message": f"Attribute template '{template.name}' deleted successfully.",
    }
