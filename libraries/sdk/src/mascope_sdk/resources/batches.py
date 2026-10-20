"""Sample batches resource for the Mascope SDK."""

import json
import re
from collections.abc import Sequence
from typing import Any

import pandas as pd
from loguru import logger

from .._resolve import resolve_id
from ._base import BaseResource, Needs, _coerce_datetime_columns


class BatchesResource(BaseResource):
    """Resource for sample batch operations.

    Sample batches group related samples together within a dataset.
    They contain metadata about the batch and references to individual samples.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # List batches by dataset name (substring match)
        batches = mascope.batches.list("My Dataset")

        # Or by dataset ID
        batches = mascope.batches.list("ws-123")
    """

    def _resolve_dataset_id(self, dataset: "str | re.Pattern") -> str:
        """Resolve a dataset name or ID to a dataset ID."""
        datasets = self._client.datasets.list()
        return resolve_id(
            dataset,
            datasets,
            id_column="dataset_id",
            name_column="dataset_name",
            entity_label="dataset",
        )

    def list(self, dataset: "str | re.Pattern") -> pd.DataFrame | None:
        """List all sample batches in a dataset.

        :param dataset: Dataset name or literal substring (or dataset ID); pass
                        a compiled ``re.Pattern`` to match by regex.
        :type dataset: str | re.Pattern
        :return: A DataFrame containing sample batch information with columns
                 including ``sample_batch_id`` and ``sample_batch_name``, or
                 None if no batches are found.
        :rtype: pd.DataFrame | None
        :raises ValueError: If the dataset cannot be resolved.
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            # By name
            batches = mascope.batches.list("My Dataset")

            # By ID
            batches = mascope.batches.list("GwvleF1LJtEcfUQg")
        """
        dataset_id = self._resolve_dataset_id(dataset)
        return self._list_by_id(dataset_id)

    def _list_by_id(self, dataset_id: str) -> pd.DataFrame | None:
        """List batches by dataset ID (no name resolution)."""
        cache_key = f"batches:{dataset_id}"
        if cache_key in self._client._cache:
            # A copy: a caller editing its listing must not edit the cache
            return self._client._cache[cache_key].copy()
        logger.info("Fetching batches for dataset '{}'", dataset_id)
        data = self._get("sample/batches", params={"dataset_id": dataset_id})
        if not data:
            return None
        df = _coerce_datetime_columns(pd.DataFrame(data))
        logger.info("Found {} batch(es)", len(df))
        self._client._cache[cache_key] = df
        return df.copy()

    def get_data(self, batch_id: str) -> dict[str, Any]:
        """Retrieve detailed data for all samples in a batch.

        This method fetches comprehensive data for a sample batch, including
        samples and combined match/targets data for compounds, ions, and isotopes.

        The data is retrieved in streaming mode to handle potentially large responses.

        :param batch_id: The ID of the sample batch to retrieve data for.
        :type batch_id: str
        :return: A dictionary containing:

                 - ``result``: Summary statistics about the retrieved data
                 - ``sample_batch``: Information about the sample batch
                 - ``samples``: A list of samples within the batch
                 - ``compounds``: Data for compounds (match + target data combined)
                 - ``ions``: Data for ions (match + target data combined)
                 - ``isotopes``: Data for isotopes (match + target data combined)

                 Or an empty dictionary if no data is found.
        :rtype: dict[str, Any]
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the batch is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            batch_data = mascope.batches.get_data(batch_id="batch-456")
            print(f"Batch: {batch_data['sample_batch']['name']}")
            print(f"Samples: {len(batch_data['samples'])}")
            print(f"Compounds: {len(batch_data['compounds'])}")
        """
        response = self._get(
            f"match/targets/batch/{batch_id}",
            stream=True,
        )

        # Parse streamed response
        try:
            chunks = []
            for chunk in response.iter_content(chunk_size=8192, decode_unicode=True):
                if chunk:
                    chunks.append(chunk)
            content = "".join(chunks)
            batch_data = json.loads(content)
        finally:
            response.close()

        if not batch_data:
            return {}

        # Extract and restructure the data
        return {
            "result": batch_data.get("result", {}),
            "sample_batch": batch_data.get("data", {}).get("sample_batch", {}),
            "samples": batch_data.get("data", {}).get("samples", []),
            "compounds": batch_data.get("data", {}).get("compounds", []),
            "ions": batch_data.get("data", {}).get("ions", []),
            "isotopes": batch_data.get("data", {}).get("isotopes", []),
        }

    def update(
        self,
        batch_id: str,
        target_collection_ids: Sequence[str],
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename a batch, change its description, and set its collections.

        ``target_collection_ids`` is required, as the route requires it, and
        *replaces* the batch's target collections: to only rename a batch,
        send the collections it has now. An empty list detaches them all.

        :param batch_id: The batch.
        :type batch_id: str
        :param target_collection_ids: The target collections the batch is to
            have.
        :type target_collection_ids: Sequence[str]
        :param name: Its new name.
        :type name: str, optional
        :param description: Its new description.
        :type description: str, optional
        :return: The updated batch.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role on the batch,
            or the global admin role for a locked batch.
        """
        body: dict[str, Any] = {"target_collection_ids": list(target_collection_ids)}
        if name is not None:
            body["sample_batch_name"] = name
        if description is not None:
            body["sample_batch_description"] = description
        return self._write(
            "PATCH",
            f"sample/batches/{batch_id}",
            Needs(
                "update",
                "batch",
                batch_id,
                "editor on the batch (a locked batch: the global admin role)",
            ),
            data=body,
        )

    def copy(
        self,
        batch_id: str,
        dataset_id: str,
        name: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Copy a batch, with its samples, into a dataset.

        The copy runs in the background on the server: the call returns when
        it has started, and the new batch appears in :meth:`list` once done.
        A locked dataset takes no copy, whatever the caller's role.

        :param batch_id: The batch to copy.
        :type batch_id: str
        :param dataset_id: The dataset to copy it into.
        :type dataset_id: str
        :param name: The new batch's name.
        :type name: str
        :param description: The new batch's description.
        :type description: str, optional
        :return: The server's ``message`` and the task's ``process_id``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role on the batch
            and on the destination dataset, or if that dataset is locked.
        """
        body: dict[str, Any] = {"dataset_id": dataset_id, "sample_batch_name": name}
        if description is not None:
            body["sample_batch_description"] = description
        return self._write(
            "POST",
            f"sample/batches/{batch_id}/copy",
            Needs(
                "copy",
                "batch",
                batch_id,
                f"editor on the batch and on dataset '{dataset_id}'"
                " (a locked dataset: none)",
            ),
            data=body,
        )

    def delete(self, batch_id: str) -> dict[str, Any]:
        """Delete a batch and its samples. This cannot be undone.

        The delete runs in the background on the server: the call returns
        when it has started.

        :param batch_id: The batch.
        :type batch_id: str
        :return: The server's ``message`` and the task's ``process_id``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role on the batch,
            or the owner role for a locked batch.
        """
        return self._write(
            "DELETE",
            f"sample/batches/{batch_id}",
            Needs(
                "delete",
                "batch",
                batch_id,
                "editor on the batch (a locked batch: the owner role)",
            ),
        )
