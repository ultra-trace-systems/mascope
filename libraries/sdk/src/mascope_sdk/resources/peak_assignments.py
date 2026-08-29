"""Peak assignments resource for the Mascope SDK.

Reads persisted peak-centric assignment results: every observed peak of a
sample with its committed formula, adduct, evidence, and confidence tier, as
produced by a server-side assignment run (launched from the Mascope app).

This is the read surface only. Launching assignment runs and recording
verification verdicts stay app-side for now (see
``docs/dev/sdk_peak_assignment.md``).
"""

from typing import Any

import pandas as pd
from loguru import logger

from .._http import http_get
from ._base import BaseResource, _coerce_datetime_columns


#: Rows requested per page when reading a run. The list endpoint caps a page
#: at 5000 rows (its own default is 1000); the SDK asks for the cap to
#: minimise round trips and pages until it has ``total`` rows.
PAGE_LIMIT = 5000


def _coerce_utc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert ``*_utc_*`` audit columns to timezone-aware datetimes.

    The run and assignment records name their timestamps
    ``peak_assignment_run_utc_created`` / ``..._utc_completed``, which the
    shared ``_coerce_datetime_columns`` (keyed on ``datetime`` in the column
    name) does not catch.
    """
    for col in df.columns:
        if "_utc" not in col or pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        try:
            df[col] = pd.to_datetime(df[col], utc=True)
        except Exception as e:
            # INFO: fires per column per DataFrame on odd data
            logger.info(f"Failed to convert column {col} to datetime: {e}")
    return df


class PeakAssignmentsResource(BaseResource):
    """Resource for reading peak-centric assignment results.

    A peak assignment run assigns a composition to *every* observed peak of a
    sample - database-known targets first (Stage A), then untargeted search
    (Stage B) - arbitrates a single owner per peak, and files each assignment
    into a confidence tier. Runs are launched from the Mascope app; this
    resource reads the persisted results.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # Run history of a sample, newest first
        runs = mascope.peak_assignments.list_runs("sample-123")

        # The full ledger of the latest completed run (one row per peak)
        assignments = mascope.peak_assignments.get("sample-123")
        assignments.attrs["run"]  # run metadata (engine_version, config, ...)

        # Only the confidently assigned peaks
        assigned = mascope.peak_assignments.get("sample-123", tier="assigned")
    """

    def _get_envelope(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET *path* and return the full response envelope.

        The shared ``_get`` unwraps responses to their ``data`` field, which
        drops the ``total`` the paged ledger read needs to know when it has
        the whole run - so the paging loop reads the envelope itself.
        """
        response = http_get(
            url=self._client.url,
            path=path,
            access_token=self._client.access_token,
            params=params,
            timeout=self._client._timeout,
            verify_ssl=self._client._verify_ssl,
            service_name=self._client._service_name,
        )
        return response.json()

    def list_runs(self, sample_id: str) -> pd.DataFrame | None:
        """List the peak assignment runs of a sample, newest first.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :return: A DataFrame with one row per run, containing:

                 - ``peak_assignment_run_id``: Run identifier
                 - ``engine``: Which engine produced the run - ``mascope`` for
                   a run this deployment computed, otherwise the name of the
                   external engine that published it (see
                   :meth:`~mascope_sdk.MascopeClient.peak_assignments`). Never
                   null: rows predating the column were backfilled to
                   ``mascope``, and the name is reserved server-side so an
                   import cannot claim it.
                 - ``engine_version``: Assignment engine version
                 - ``status``: ``pending`` -> ``running`` -> ``completed`` |
                   ``failed`` | ``cancelled``; ``importing`` while an external
                   run is still being uploaded (its rows are not servable yet)
                 - ``config``: Run configuration (dict)
                 - ``tier_bands``: The ``assigned`` / ``candidate``
                   fit-score thresholds this run tiered with (dict). A tier is
                   only comparable across engines under the bands that
                   produced it. Null for runs predating the column.
                 - ``calibration``: What an external engine disclosed about
                   its calibration at import (dict). Null for ``mascope``
                   runs, whose calibration state is the sample's own.
                 - ``error``: Failure reason, if any
                 - ``peak_assignment_run_utc_created`` /
                   ``peak_assignment_run_utc_completed``: Timestamps (UTC)

                 Returns None if the sample has no runs.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            runs = mascope.peak_assignments.list_runs("sample-123")
            completed = runs[runs["status"] == "completed"]
        """
        data = self._get(f"peak-assignments/sample/{sample_id}/runs")
        if not data:
            return None
        return _coerce_utc_columns(_coerce_datetime_columns(pd.DataFrame(data)))

    def get(
        self,
        sample_id: str,
        *,
        run_id: str | None = None,
        tier: str | None = None,
        role: str | None = None,
        source: str | None = None,
    ) -> pd.DataFrame | None:
        """Get the peaks-with-assignments ledger of a sample, one row per peak.

        Reads the requested run (or the latest completed run) in full: the
        endpoint is paginated, and this method pages internally until it has
        every matching row, so the caller never sees a page boundary. The run
        metadata (engine, engine version, status, config, tier bands,
        calibration disclosure, timestamps - the full run record listed under
        :meth:`list_runs`) is attached on ``df.attrs["run"]`` as a dict.

        Rows are the slim ledger projection: per-peak scalars plus the
        flattened calibration scalars (``p_correct``,
        ``p_correct_provisional``, ``corroboration_adducts``). The
        inspector-detail JSON (``alternatives``, ``provenance``) of a single
        assignment is served by :meth:`detail`.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param run_id: Specific run to read. Defaults to the latest completed
                       run, resolved via :meth:`list_runs`.
        :type run_id: str, optional
        :param tier: Filter by confidence tier: ``assigned`` | ``candidate``
                     | ``below_assignability`` | ``unassigned``. The server
                     still accepts the legacy spelling ``identified`` for
                     ``assigned``, so older scripts keep working.
        :type tier: str, optional
        :param role: Filter by peak role: ``M0`` | ``iso_child`` | ``reagent``
                     | ``artifact`` | ``unassigned``.
        :type role: str, optional
        :param source: Filter by assignment source: ``database`` |
                       ``untargeted``.
        :type source: str, optional
        :return: A DataFrame with one row per observed peak, containing:

                 - ``peak_assignment_id``, ``peak_assignment_run_id``
                 - ``sample_item_id``, ``sample_peak_id``
                 - ``sample_peak_mz``, ``sample_peak_intensity``,
                   ``sample_peak_tof``
                 - ``role``, ``tier``, ``source``
                 - ``assigned_formula``, ``ion_formula``,
                   ``ionization_mechanism_id``
                 - ``isotope_label``, ``isotope_formula``
                 - ``fit_score``, ``mz_error_ppm``, ``abundance_error``
                 - ``p_correct``, ``p_correct_provisional``,
                   ``corroboration_adducts``
                 - ``target_compound_id``, ``target_ion_id`` (set for
                   database-sourced assignments)
                 - ``owner_peak_assignment_id`` (for isotope children, the
                   M0 assignment that owns them)

                 Rows are ordered by m/z. The run metadata dict is on
                 ``df.attrs["run"]``. Returns None if the sample has no
                 completed run (and ``run_id`` was not given), or if no rows
                 match the filters.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample (or the given run) is not found.
        :raises ValidationError: If a filter value is not one of the accepted
                                 enum values (server-side 422).
        :raises MascopeAPIError: If the API request fails.

        Example::

            assignments = mascope.peak_assignments.get("sample-123")
            print(assignments.attrs["run"]["engine_version"])
            assignments["tier"].value_counts()

            # Untargeted winners only
            stage_b = mascope.peak_assignments.get(
                "sample-123", source="untargeted"
            )
        """
        # Resolve the run client-side (the ledger response carries no run
        # object), so the call is deterministic and the run metadata can be
        # attached to the result.
        runs = self.list_runs(sample_id)
        run_meta: dict[str, Any] | None = None
        if run_id is None:
            if runs is None:
                logger.info("Sample '{}' has no peak assignment runs", sample_id)
                return None
            completed = runs[runs["status"] == "completed"]
            if completed.empty:
                logger.info(
                    "Sample '{}' has no completed peak assignment run", sample_id
                )
                return None
            latest = completed.sort_values(
                "peak_assignment_run_utc_created", ascending=False
            ).iloc[0]
            run_id = latest["peak_assignment_run_id"]
            run_meta = latest.to_dict()
        elif runs is not None:
            match = runs[runs["peak_assignment_run_id"] == run_id]
            if not match.empty:
                run_meta = match.iloc[0].to_dict()

        params: dict[str, Any] = {
            "peak_assignment_run_id": run_id,
            "limit": PAGE_LIMIT,
            "offset": 0,
        }
        for key, value in (("tier", tier), ("role", role), ("source", source)):
            if value is not None:
                params[key] = value

        # Page until we have `total` rows. Ordering is stable server-side
        # (m/z with the primary key as tiebreak), so paging never drops or
        # repeats a row.
        rows: list[dict[str, Any]] = []
        while True:
            envelope = self._get_envelope(
                f"peak-assignments/sample/{sample_id}", params=dict(params)
            )
            page = envelope.get("data") or []
            total = envelope.get("total", len(page))
            rows.extend(page)
            if not page or len(rows) >= total:
                break
            params["offset"] = len(rows)

        if not rows:
            logger.info(
                "Run '{}' of sample '{}' has no assignments matching the filters",
                run_id,
                sample_id,
            )
            return None

        df = _coerce_utc_columns(_coerce_datetime_columns(pd.DataFrame(rows)))
        df.attrs["run"] = run_meta or {"peak_assignment_run_id": run_id}
        return df

    def detail(self, sample_id: str, peak_assignment_id: str) -> dict | None:
        """Get one assignment in full, including ``alternatives`` and
        ``provenance``.

        The complement of :meth:`get`, whose rows are a slim projection: this
        fetches the inspector-detail JSON of a single assignment - the ranked
        alternative compositions considered for the peak and the full scoring
        provenance.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param peak_assignment_id: The ID of the assignment
                                   (``peak_assignment_id`` column of
                                   :meth:`get`).
        :type peak_assignment_id: str
        :return: The full assignment record: every :meth:`get` column plus
                 ``alternatives`` (list) and ``provenance`` (dict). Returns
                 None if the response carries no record.
        :rtype: dict | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample or assignment is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            assignments = mascope.peak_assignments.get("sample-123")
            top = assignments.sort_values(
                "sample_peak_intensity", ascending=False
            ).iloc[0]
            full = mascope.peak_assignments.detail(
                "sample-123", top["peak_assignment_id"]
            )
            for alternative in full["alternatives"] or []:
                print(alternative)
        """
        data = self._get(
            f"peak-assignments/sample/{sample_id}/assignment/{peak_assignment_id}"
        )
        if not data:
            return None
        return data[0]
