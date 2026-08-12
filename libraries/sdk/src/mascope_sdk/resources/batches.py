"""Sample batches resource for the Mascope SDK."""

import json
import re
from typing import Any

import pandas as pd
from loguru import logger

from .._resolve import resolve_id
from ._base import BaseResource, _coerce_datetime_columns


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
            return self._client._cache[cache_key]
        logger.info("Fetching batches for dataset '{}'", dataset_id)
        data = self._get("sample/batches", params={"dataset_id": dataset_id})
        if not data:
            return None
        df = _coerce_datetime_columns(pd.DataFrame(data))
        logger.info("Found {} batch(es)", len(df))
        self._client._cache[cache_key] = df
        return df

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
