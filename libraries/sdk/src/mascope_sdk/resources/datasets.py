"""Datasets resource for the Mascope SDK."""

import pandas as pd
from loguru import logger

from ._base import BaseResource, _coerce_datetime_columns


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
