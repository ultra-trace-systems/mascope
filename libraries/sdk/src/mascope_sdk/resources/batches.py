"""Sample batches resource for the Mascope SDK."""

import json
import re
from collections.abc import Sequence
from typing import Any

import pandas as pd
from loguru import logger

from .._resolve import resolve_id
from ._base import BaseResource, Needs, _coerce_datetime_columns


#: The columns of :meth:`BatchesResource.ion_series`, in order.
ION_SERIES_COLUMNS = [
    "sample_item_id",
    "target_ion_id",
    "target_compound_name",
    "target_compound_formula",
    "ion_formula",
    "ionization_mechanism",
    "intensity",
    "match_category",
]


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

    def ion_series(
        self,
        batch_id: str,
        *,
        collection: str | None = None,
        ions: "Sequence[str] | None" = None,
    ) -> pd.DataFrame:
        """The matched intensity of target ions in every sample of a batch,
        one row per (sample, target ion).

        One request per scope, over the compact series route the app's batch
        chart draws from, rather than one per sample as :meth:`~mascope_sdk.client.MascopeClient.load_peaks`
        makes. A row is a (sample, ion) pair the server holds a match record
        for, matched or not: ``match_category`` 0 is no match, at intensity
        0. Filter on ``match_category > 0`` for the detections alone, or
        counts and means per ion take the misses in.

        The ions are those of ``collection``, or ``ions``, or - when neither
        is given - those of every target collection the batch has, read with
        one request per collection. Those collections come from
        :meth:`~mascope_sdk.resources.collections.CollectionsResource.list`,
        whose answer the client caches: after attaching a collection to the
        batch in the same session, call ``clear_cache()`` first. An ion two
        of them share is one row per sample.

        :param batch_id: The ID of the sample batch.
        :type batch_id: str
        :param collection: The ID of a target collection whose ions to read.
        :type collection: str, optional
        :param ions: The IDs of the target ions to read.
        :type ions: Sequence[str], optional
        :return: A DataFrame with ``sample_item_id``, ``target_ion_id``,
                 ``target_compound_name``, ``target_compound_formula``,
                 ``ion_formula``, ``ionization_mechanism``, ``intensity`` (the
                 match's summed peak intensity, 0 for no match) and
                 ``match_category``; empty, with those columns, when the batch
                 has no match records for the ions. Stamped with the
                 server's ``attrs["provenance"]`` as the loaders' frames are.
                 Join ``samples.list(batch=...)`` on ``sample_item_id`` for
                 each sample's time.
        :rtype: pd.DataFrame
        :raises ValueError: If both ``collection`` and ``ions`` are given.
        :raises PermissionDeniedError: Without the guest role on the batch.

        Example::

            series = mascope.batches.ion_series("batch-123")
            wide = series.pivot(
                index="sample_item_id", columns="target_ion_id", values="intensity"
            )
        """
        if collection is not None and ions is not None:
            raise ValueError("Give collection or ions, not both")
        if ions is not None:
            ion_ids = [ions] if isinstance(ions, str) else [*ions]
            scopes = [{"target_ion_ids": ion_ids}] if ion_ids else []
        elif collection is not None:
            scopes = [{"target_collection_id": collection}]
        else:
            listed = self._client.collections.list(
                batch_id=batch_id, all_workspaces=True
            )
            collection_ids = [] if listed is None else listed["target_collection_id"]
            scopes = [{"target_collection_id": c} for c in collection_ids]
            if not scopes:
                logger.info("Batch '{}' has no target collections", batch_id)

        columns: dict[str, list[Any]] = {name: [] for name in ION_SERIES_COLUMNS}
        for scope in scopes:
            body = {"sample_batch_id": batch_id, **scope}
            for ion in self._post("match/records/ion/series", body) or []:
                series = ion["match_series"]
                n = len(series["sample_item_ids"])
                columns["sample_item_id"].extend(series["sample_item_ids"])
                columns["intensity"].extend(series["sample_peak_intensity_sums"])
                columns["match_category"].extend(series["match_categories"])
                columns["target_ion_id"].extend([ion["target_ion_id"]] * n)
                columns["target_compound_name"].extend(
                    [ion["target_compound_name"]] * n
                )
                columns["target_compound_formula"].extend(
                    [ion["target_compound_formula"]] * n
                )
                columns["ion_formula"].extend([ion["target_ion_formula"]] * n)
                columns["ionization_mechanism"].extend(
                    [ion["ionization_mechanism"]] * n
                )
        frame = (
            pd.DataFrame(columns)
            .astype({"intensity": "float64"})
            .drop_duplicates(["sample_item_id", "target_ion_id"], ignore_index=True)
        )
        logger.info(
            "Read {} (sample, ion) record(s) of batch '{}'", len(frame), batch_id
        )
        return self._client._with_provenance(frame)

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
