"""Datasets resource for the Mascope SDK."""

import builtins
from typing import Any

import pandas as pd
from loguru import logger

from ._base import BaseResource, Needs, _coerce_datetime_columns


#: The role a dataset write needs, as docs/user/sdk/index.md lists it.
_EDITOR = "editor in the workspace (a locked dataset: the global admin role)"


class DatasetsResource(BaseResource):
    """Resource for dataset operations.

    Datasets belong to a workspace and contain sample batches and related data.
    The workspace is determined by the ``MascopeClient`` configuration.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient(workspace="My Workspace")

        # List all datasets in the workspace
        datasets = mascope.datasets.list()
        print(datasets[["dataset_id", "dataset_name"]])

        # Create one, rename it, delete it
        dataset = mascope.datasets.create("Spring campaign")
        mascope.datasets.update(dataset["dataset_id"], name="Spring 2026")
        mascope.datasets.delete(dataset["dataset_id"])

    The write methods act in the client's workspace unless a
    ``workspace_id`` is given.
    """

    def list(self) -> pd.DataFrame | None:
        """List all datasets in the current workspace.

        :return: A DataFrame containing dataset information with columns
                 including ``dataset_id`` and ``dataset_name``, or None if no
                 datasets are found.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            datasets = mascope.datasets.list()
            print(datasets[["dataset_id", "dataset_name"]])
        """
        workspace_id = self._client.workspace_id
        cache_key = f"datasets:{workspace_id}"
        if cache_key in self._client._cache:
            # A copy: a caller editing its listing must not edit the cache
            return self._client._cache[cache_key].copy()
        logger.info("Fetching datasets")
        data = self._get(f"workspaces/{workspace_id}/datasets")
        if not data:
            return None
        df = _coerce_datetime_columns(pd.DataFrame(data))
        logger.info("Found {} dataset(s)", len(df))
        self._client._cache[cache_key] = df
        return df.copy()

    def _workspace(self, workspace_id: str | None) -> str:
        return workspace_id or self._client.workspace_id

    def create(
        self,
        name: str,
        description: str | None = None,
        dataset_type: str | None = None,
        instrument: str | None = None,
        icon: dict[str, str] | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a dataset.

        Needs the editor role in the workspace.

        :param name: The dataset's name, unique in the workspace.
        :type name: str
        :param description: Its description.
        :type description: str, optional
        :param dataset_type: ``"ANALYSIS"`` or ``"ACQUISITION"``; the server's
            default when None. An acquisition dataset needs an instrument.
        :type dataset_type: str, optional
        :param instrument: The instrument it belongs to.
        :type instrument: str, optional
        :param icon: ``{"icon_id": ..., "color": "#RRGGBB"}``.
        :type icon: dict[str, str], optional
        :param workspace_id: The workspace; the client's when None.
        :type workspace_id: str, optional
        :return: The new dataset, with its ``dataset_id``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role there.
        :raises ValidationError: If the server refuses the fields, such as a
            name already taken.
        """
        workspace_id = self._workspace(workspace_id)
        body: dict[str, Any] = {"dataset_name": name}
        if description is not None:
            body["dataset_description"] = description
        if dataset_type is not None:
            body["dataset_type"] = dataset_type
        if instrument is not None:
            body["instrument"] = instrument
        if icon is not None:
            body["icon"] = icon
        return self._write(
            "POST",
            f"workspaces/{workspace_id}/datasets",
            Needs("create a dataset in", "workspace", workspace_id, "editor"),
            data=body,
        )

    def update(
        self,
        dataset_id: str,
        name: str | None = None,
        description: str | None = None,
        icon: dict[str, str] | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Rename a dataset, or change its description or icon.

        Only the fields given are sent; the others stay as they are.

        :param dataset_id: The dataset.
        :type dataset_id: str
        :param name: Its new name.
        :type name: str, optional
        :param description: Its new description.
        :type description: str, optional
        :param icon: Its new icon, ``{"icon_id": ..., "color": "#RRGGBB"}``.
        :type icon: dict[str, str], optional
        :param workspace_id: The workspace it is in; the client's when None.
        :type workspace_id: str, optional
        :return: The updated dataset.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role in the
            workspace, or the global admin role for a locked dataset.
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["dataset_name"] = name
        if description is not None:
            body["dataset_description"] = description
        if icon is not None:
            body["icon"] = icon
        return self._write(
            "PATCH",
            f"workspaces/{self._workspace(workspace_id)}/datasets/{dataset_id}",
            Needs("update", "dataset", dataset_id, _EDITOR),
            data=body,
        )

    def move(
        self,
        dataset_id: str,
        target_workspace_id: str,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Move a dataset, with everything in it, into another workspace.

        :param dataset_id: The dataset.
        :type dataset_id: str
        :param target_workspace_id: The workspace to move it into.
        :type target_workspace_id: str
        :param workspace_id: The workspace it is in now; the client's when
            None.
        :type workspace_id: str, optional
        :return: The moved dataset.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role in both
            workspaces.
        """
        return self._write(
            "POST",
            f"workspaces/{self._workspace(workspace_id)}/datasets/{dataset_id}/move",
            Needs(
                "move",
                "dataset",
                dataset_id,
                f"editor in its workspace and in workspace '{target_workspace_id}'"
                " (a locked dataset: the global admin role)",
            ),
            data={"target_workspace_id": target_workspace_id},
        )

    def delete(
        self, dataset_id: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        """Delete a dataset and everything in it. This cannot be undone.

        :param dataset_id: The dataset.
        :type dataset_id: str
        :param workspace_id: The workspace it is in; the client's when None.
        :type workspace_id: str, optional
        :return: ``{"message": ...}``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role in the
            workspace, or the global admin role for a locked dataset.
        """
        return self._write(
            "DELETE",
            f"workspaces/{self._workspace(workspace_id)}/datasets/{dataset_id}",
            Needs("delete", "dataset", dataset_id, _EDITOR),
        )

    def tombstones(self) -> builtins.list[dict[str, Any]]:
        """What deleted datasets were: one record per deleted dataset.

        A dataset's tombstone is written when it is deleted, alone or with its
        workspace, and outlives both: its name, description, type, instrument,
        ``descriptive_metadata`` (license, authors, keywords, related
        identifiers, funding), ``n_sample_batches``, ``n_sample_items`` and
        ``n_sample_files`` as it stood, ``deleted_utc``, ``deleted_by`` (an
        email), ``deleted_via`` (``"dataset"`` or ``"workspace"``) and the
        ``provenance`` block of the build that deleted it.

        Not limited to the client's workspace: the records of every workspace
        the caller is a member of, and for a global admin or owner every
        record, a deleted workspace's included. Newest delete first.

        :return: The records as dicts; an empty list when there are none.
        :rtype: list[dict[str, Any]]
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the server predates tombstones.
        :raises MascopeAPIError: If the API request fails.

        Example::

            for record in mascope.datasets.tombstones():
                print(record["dataset_name"], record["deleted_utc"])
        """
        return self._get("datasets/tombstones")
