"""
Target collection utility functions for collection operations and change detection.

This module contains helper functions for target collection management operations,
including change detection, validation utilities, and data transformation
functions used across target collection-related controllers.
"""

from typing import TypedDict

from sqlalchemy import or_, select

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    TargetCollectionUpdate,
)
from mascope_backend.db import (
    Dataset,
    IonizationMode,
    SampleBatch,
    TargetCollection,
    async_session,
)
from mascope_backend.runtime import runtime


class TargetCollectionChanges(TypedDict):
    compounds: bool
    compounds_to_add: set[str]
    compounds_to_remove: set[str]
    batches: bool
    batches_to_add: set[str]
    batches_to_remove: set[str]
    basic_fields: bool
    collection_type: bool


def detect_target_collection_changes(
    target_collection_db: TargetCollection,
    target_collection_update: TargetCollectionUpdate,
    updated_compound_ids: set[str] | None = None,
) -> TargetCollectionChanges:
    """
    Detects changes between existing target collection and update data.

    Compares current collection state with proposed updates to determine
    which fields have actually changed and what specific items need to be
    added or removed.

    :param target_collection_db: Current target collection entity from database
    :type target_collection_db: TargetCollection
    :param target_collection_update: Pydantic model containing proposed updates
    :type target_collection_update: TargetCollectionUpdate
    :param updated_compound_ids: Final compound IDs that should be associated with the
                                 collection
    :type updated_compound_ids: set[str] | None
    :return: Dictionary mapping change types to boolean flags and sets of IDs
    :rtype: dict[str, bool | set[str]]
    """
    sample_batches_db = {sb.sample_batch_id for sb in target_collection_db.sample_batch}
    target_compounds_db = {
        tc.target_compound_id for tc in target_collection_db.target_compound
    }
    # Calculate compound changes
    compounds_changed = False
    compounds_to_add = set()
    compounds_to_remove = set()
    if updated_compound_ids is not None:
        compounds_to_add = updated_compound_ids - target_compounds_db
        compounds_to_remove = target_compounds_db - updated_compound_ids
        compounds_changed = len(compounds_to_add) > 0 or len(compounds_to_remove) > 0

    # Calculate batch changes
    batches_changed = False
    batches_to_add = set()
    batches_to_remove = set()
    if target_collection_update.sample_batch_ids is not None:
        desired_batches = set(target_collection_update.sample_batch_ids)
        batches_to_add = desired_batches - sample_batches_db
        batches_to_remove = sample_batches_db - desired_batches
        batches_changed = len(batches_to_add) > 0 or len(batches_to_remove) > 0

    # Basic field changes
    collection_type_changed = (
        target_collection_update.target_collection_type is not None
        and target_collection_update.target_collection_type
        != target_collection_db.target_collection_type
    )
    name_changed = (
        target_collection_update.target_collection_name is not None
        and target_collection_update.target_collection_name
        != target_collection_db.target_collection_name
    )
    description_changed = (
        target_collection_update.target_collection_description is not None
        and target_collection_update.target_collection_description
        != target_collection_db.target_collection_description
    )
    workspace_id_changed = (
        "workspace_id" in target_collection_update.model_fields_set
        and target_collection_update.workspace_id != target_collection_db.workspace_id
    )
    basic_fields_changed = (
        collection_type_changed
        or name_changed
        or description_changed
        or workspace_id_changed
    )

    runtime.logger.debug(
        "Detected target collection changes:\n"
        f"  basic_fields_changed: {basic_fields_changed}\n"
        f"  compounds_changed: {compounds_changed}\n"
        f"  compounds_to_add: {list(compounds_to_add)}\n"
        f"  compounds_to_remove: {list(compounds_to_remove)}\n"
        f"  batches_changed: {batches_changed}\n"
        f"  batches_to_add: {list(batches_to_add)}\n"
        f"  batches_to_remove: {list(batches_to_remove)}"
    )

    return {
        "compounds": compounds_changed,
        "compounds_to_add": compounds_to_add,
        "compounds_to_remove": compounds_to_remove,
        "batches": batches_changed,
        "batches_to_add": batches_to_add,
        "batches_to_remove": batches_to_remove,
        "basic_fields": basic_fields_changed,
        "collection_type": collection_type_changed,
    }


async def validate_batch_scope_for_collection(
    sample_batch_ids: list[str] | None,
    workspace_id: str | None,
) -> None:
    """Validate that batches assigned to a workspace-scoped collection belong
    to that workspace.

    Global collections (``workspace_id`` is None) may be assigned to batches
    in any workspace. This enforces at assignment time the same invariant
    that :func:`validate_scope_change` enforces when narrowing scope.

    :param sample_batch_ids: Sample batch IDs being assigned to the collection
    :param workspace_id: The collection's (effective) workspace_id, None for global
    :raises ApiException: 409 if any batch belongs to a different workspace
    """
    if workspace_id is None or not sample_batch_ids:
        return

    async with async_session() as session:
        result = await session.execute(
            select(SampleBatch.sample_batch_id)
            .join(Dataset, Dataset.dataset_id == SampleBatch.dataset_id)
            .where(SampleBatch.sample_batch_id.in_(sample_batch_ids))
            .where(Dataset.workspace_id != workspace_id)
            .limit(1)
        )
        out_of_scope = result.scalar_one_or_none()

    if out_of_scope is not None:
        msg = (
            "Cannot assign batches from other workspaces to a workspace-scoped "
            "collection. Move the collection to the global scope first, or pick "
            "batches from the collection's workspace."
        )
        raise ApiException(
            user_message=msg,
            tech_message=msg,
            status_code=409,
        )


async def validate_scope_change(
    target_collection_db: TargetCollection,
    new_workspace_id: str | None,
) -> None:
    """Validate that a collection's scope can be changed to a new workspace.

    When narrowing scope (global→workspace or workspaceA→workspaceB), checks
    that no existing batch associations fall outside the target workspace.
    Expanding to global (any→null) is always allowed.

    :param target_collection_db: Current target collection entity from database
    :param new_workspace_id: The target workspace_id (None for global)
    :raises ApiException: If associated batches exist outside the target workspace
    """
    # No actual change
    if new_workspace_id == target_collection_db.workspace_id:
        return

    # Expanding to global is always safe — visible to everyone
    if new_workspace_id is None:
        return

    # An ionization mode is global reference data: every workspace processing a
    # sample under it reads the collections it names. Pulling one of those into
    # a single workspace would leave the rest of the instance matching against
    # a collection they cannot see, and would be a way around the read check on
    # the mode routes - bind a global collection, then narrow it.
    async with async_session() as session:
        referencing_mode = (
            await session.execute(
                select(IonizationMode.ionization_mode_name)
                .where(
                    or_(
                        IonizationMode.calibration_collection_id
                        == target_collection_db.target_collection_id,
                        IonizationMode.diagnostic_collection_id
                        == target_collection_db.target_collection_id,
                    )
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    if referencing_mode is not None:
        msg = (
            "Cannot change collection scope: the collection is used by "
            f"ionization mode '{referencing_mode}', which is shared across "
            "all workspaces."
        )
        raise ApiException(
            user_message=msg,
            tech_message=msg,
            status_code=409,
        )

    # No batch associations — scope change is safe
    if not target_collection_db.sample_batch:
        return

    # Check if any associated batches belong to a different workspace
    batch_ids = [assoc.sample_batch_id for assoc in target_collection_db.sample_batch]
    async with async_session() as session:
        result = await session.execute(
            select(Dataset.workspace_id)
            .join(SampleBatch, SampleBatch.dataset_id == Dataset.dataset_id)
            .where(SampleBatch.sample_batch_id.in_(batch_ids))
            .where(Dataset.workspace_id != new_workspace_id)
            .limit(1)
        )
        out_of_scope = result.scalar_one_or_none()

    if out_of_scope is not None:
        msg = (
            "Cannot change collection scope: the collection is associated "
            "with batches in other workspaces."
        )
        raise ApiException(
            user_message=msg,
            tech_message=msg,
            status_code=409,
        )
