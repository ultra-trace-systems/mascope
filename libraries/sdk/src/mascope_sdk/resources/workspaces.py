"""Workspaces resource for the Mascope SDK."""

import pandas as pd
from loguru import logger

from ._base import BaseResource, _coerce_datetime_columns


class WorkspacesResource(BaseResource):
    """Resource for workspace operations.

    Workspaces are the top-level access-control boundary in Mascope.
    Each workspace contains datasets, and user access is managed via
    workspace memberships.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient(workspace="My Workspace")

        # List all workspaces
        workspaces = mascope.workspaces.list()
        print(workspaces[["workspace_id", "workspace_name"]])
    """

    def list(self) -> pd.DataFrame | None:
        """List all workspaces the current user has access to.

        :return: A DataFrame containing workspace information with columns
                 including `workspace_id` and `workspace_name`, or None
                 if no workspaces are found.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            workspaces = mascope.workspaces.list()
            print(workspaces[["workspace_id", "workspace_name"]])
        """
        cache_key = "workspaces"
        if cache_key in self._client._cache:
            # A copy: a caller editing its listing must not edit the cache
            return self._client._cache[cache_key].copy()
        logger.info("Fetching workspaces")
        data = self._get("workspaces")
        if not data:
            return None
        df = _coerce_datetime_columns(pd.DataFrame(data))
        logger.info("Found {} workspace(s)", len(df))
        self._client._cache[cache_key] = df
        return df.copy()
