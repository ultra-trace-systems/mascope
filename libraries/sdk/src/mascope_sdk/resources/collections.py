"""Target collections resource for the Mascope SDK."""

from collections.abc import Sequence
from typing import Any

from ._base import BaseResource, Needs


#: The role a change to a collection itself needs, as docs/user/sdk/index.md
#: lists it.
_EDITOR = (
    "editor in the collection's workspace (a global collection: the global admin role)"
)


class CollectionsResource(BaseResource):
    """Resource for target collection writes.

    A target collection is a list of compounds to match. It belongs to a
    workspace, or to every workspace when global, and is associated with the
    batches it is matched in.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient(workspace="My Workspace")

        collection = mascope.collections.create(
            "Organic acids",
            compounds=[{"target_compound_formula": "C2H4O2"}],
        )
        mascope.collections.associate(
            collection["target_collection_id"], ["batch-1", "batch-2"]
        )
    """

    def create(
        self,
        name: str,
        description: str | None = None,
        collection_type: str | None = None,
        compounds: Sequence[dict[str, Any]] | None = None,
        compound_ids: Sequence[str] | None = None,
        batch_ids: Sequence[str] | None = None,
        workspace_id: str | None = None,
        is_global: bool = False,
    ) -> dict[str, Any]:
        """Create a target collection.

        :param name: The collection's name.
        :type name: str
        :param description: Its description.
        :type description: str, optional
        :param collection_type: ``"TARGETS"``, ``"DIAGNOSTICS"`` or
            ``"CALIBRANTS"``; the server's default when None.
        :type collection_type: str, optional
        :param compounds: Compounds to create and add, each a dict with
            ``target_compound_formula`` and, optionally,
            ``target_compound_name`` and ``cas_number``.
        :type compounds: Sequence[dict[str, Any]], optional
        :param compound_ids: Existing compounds to add.
        :type compound_ids: Sequence[str], optional
        :param batch_ids: Batches to associate it with.
        :type batch_ids: Sequence[str], optional
        :param workspace_id: The workspace it belongs to; the client's when
            None.
        :type workspace_id: str, optional
        :param is_global: Make it a global collection, in no one workspace.
            ``workspace_id`` is then ignored.
        :type is_global: bool, optional
        :return: The new collection, with its ``target_collection_id``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role in the
            workspace (a global collection: the global admin role), or on the
            batches.
        """
        workspace_id = None if is_global else workspace_id or self._client.workspace_id
        body: dict[str, Any] = {
            "target_collection_name": name,
            "workspace_id": workspace_id,
        }
        if description is not None:
            body["target_collection_description"] = description
        if collection_type is not None:
            body["target_collection_type"] = collection_type
        if compounds is not None:
            body["target_compounds_create"] = list(compounds)
        if compound_ids is not None:
            body["target_compound_ids"] = list(compound_ids)
        if batch_ids is not None:
            body["sample_batch_ids"] = list(batch_ids)
        role = (
            "the global admin role"
            if workspace_id is None
            else "editor in the workspace"
        ) + (" and editor on the batches" if batch_ids else "")
        return self._write(
            "POST",
            "target/collections",
            Needs(
                "create a target collection in",
                "workspace",
                workspace_id or "(global)",
                role,
            ),
            data=body,
        )

    def update(
        self,
        collection_id: str,
        name: str | None = None,
        description: str | None = None,
        collection_type: str | None = None,
        compounds: Sequence[dict[str, Any]] | None = None,
        compound_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Change a collection's name, description, type or compounds.

        Only the fields given are sent; the others stay as they are. The
        compounds are the exception within what is sent: *compounds* and
        *compound_ids* together replace the collection's list. To
        change which batches it is associated with, use :meth:`associate`:
        the route refuses compounds and batches in one request.

        :param collection_id: The collection.
        :type collection_id: str
        :param name: Its new name.
        :type name: str, optional
        :param description: Its new description.
        :type description: str, optional
        :param collection_type: Its new type.
        :type collection_type: str, optional
        :param compounds: Compounds to create, as for :meth:`create`. With
            *compound_ids* they make up the collection's whole compound list:
            a compound it has now that neither names is taken out of it. To
            add compounds, send the current ``compound_ids`` too.
        :type compounds: Sequence[dict[str, Any]], optional
        :param compound_ids: The existing compounds it is to hold, all of
            them: as with *compounds*, the rest are taken out.
        :type compound_ids: Sequence[str], optional
        :return: The updated collection.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role in the
            collection's workspace (a global collection: the global admin
            role).
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["target_collection_name"] = name
        if description is not None:
            body["target_collection_description"] = description
        if collection_type is not None:
            body["target_collection_type"] = collection_type
        if compounds is not None:
            body["target_compounds_create"] = list(compounds)
        if compound_ids is not None:
            body["target_compound_ids"] = list(compound_ids)
        return self._write(
            "PATCH",
            f"target/collections/{collection_id}",
            Needs("update", "target collection", collection_id, _EDITOR),
            data=body,
        )

    def associate(self, collection_id: str, batch_ids: Sequence[str]) -> dict[str, Any]:
        """Set the batches a collection is associated with.

        *batch_ids* is the whole list after the call, not an addition: a
        batch the collection has now and the list leaves out is detached, and
        an empty list detaches it from every batch. The batches whose
        association changes are marked for a rematch.

        This sends a batches-only update, which needs only the guest role on
        the collection, so a workspace editor can associate a global
        collection with their own batches.

        :param collection_id: The collection.
        :type collection_id: str
        :param batch_ids: The batches it is to be associated with.
        :type batch_ids: Sequence[str]
        :return: The updated collection.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the guest role on the
            collection, or the editor role on a batch added or removed.
        """
        return self._write(
            "PATCH",
            f"target/collections/{collection_id}",
            Needs(
                "associate batches with",
                "target collection",
                collection_id,
                "guest on the collection and editor on each batch added or removed",
            ),
            data={"sample_batch_ids": list(batch_ids)},
        )

    def delete(
        self, collection_id: str, delete_orphan_compounds: bool = False
    ) -> dict[str, Any]:
        """Delete a collection. This cannot be undone.

        :param collection_id: The collection.
        :type collection_id: str
        :param delete_orphan_compounds: Also delete the compounds no other
            collection uses. They are shared by every workspace.
        :type delete_orphan_compounds: bool, optional
        :return: ``{"status": ..., "message": ...}``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role in the
            collection's workspace (a global collection: the global admin
            role).
        """
        return self._write(
            "DELETE",
            f"target/collections/{collection_id}",
            Needs("delete", "target collection", collection_id, _EDITOR),
            params={"delete_orphan_compounds": delete_orphan_compounds},
        )
