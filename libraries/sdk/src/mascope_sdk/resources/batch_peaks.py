"""Batch peaks resource for the Mascope SDK.

Reads a batch's **batch ledger**: the batch peaks - one m/z anchor per species
across the batch's samples, each with the consensus formula and tier the
samples' assignments vote for - and their members, one row per sample the
species was seen in, with that sample's own reading of the peak.

This is the batch-primary picture of peak assignment: every processed sample
folds into the ledger as it arrives, so the ledger is the complete record of a
batch without a per-sample run in sight. Read-only; the ledger is built and
curated from the Mascope app.
"""

from typing import Any

import pandas as pd
from loguru import logger

from ._base import BaseResource, _coerce_utc_columns


#: Rows requested per members page: the endpoint's cap, so a batch's ledger
#: comes in as few round trips as it allows.
PAGE_LIMIT = 5000


class BatchPeaksResource(BaseResource):
    """Resource for reading a batch's batch ledger.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # One row per batch peak: the species table of the batch
        species = mascope.batch_peaks.list("batch-123")
        species["consensus_tier"].value_counts()

        # One row per member: every sample's reading of every batch peak,
        # with the anchor's consensus beside it - a short way to CSV
        ledger = mascope.batch_peaks.members("batch-123")
        ledger.to_csv("ledger.csv", index=False)

        # The batch-level verdicts recorded on the batch's species
        verdicts = mascope.batch_peaks.verdicts("batch-123")

        # The ledger's history, and the species table as an earlier run left it
        runs = mascope.batch_peaks.runs("batch-123")
        earlier = mascope.batch_peaks.list(
            "batch-123", run_id=runs.iloc[-1]["batch_peak_run_id"]
        )
    """

    def list(
        self,
        sample_batch_id: str,
        *,
        tier: str | None = None,
        min_n_present: int = 1,
        run_id: str | None = None,
    ) -> pd.DataFrame | None:
        """List a batch's batch peaks, one row per anchor, in m/z order.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :param tier: Filter by consensus tier: ``assigned`` | ``candidate`` |
                     ``below_assignability`` | ``unassigned``.
        :type tier: str, optional
        :param min_n_present: Keep only batch peaks seen in at least this many
                              samples. Defaults to 1 (every anchor).
        :type min_n_present: int
        :param run_id: Read the ledger as this batch run left it (see
                       :meth:`runs`); the current run, or none, is the live
                       ledger.
        :type run_id: str, optional
        :return: A DataFrame with one row per batch peak: ``batch_peak_id``,
                 ``mz``, ``consensus_formula``, ``consensus_ion_formula``,
                 ``ionization_mechanism_id``, ``consensus_tier``,
                 ``best_fit_score``, ``support_fraction``, ``n_present``,
                 ``is_ambiguous``, ``max_intensity`` (in
                 ``intensity_variable``), ``isotopologue_of`` (the batch peak
                 this one is an isotopologue of, or null) and ``curated``
                 (pinned by hand for the whole batch). None when the batch has
                 no ledger yet.
        :rtype: pd.DataFrame | None
        """
        params: dict[str, Any] = {"min_n_present": min_n_present}
        if tier is not None:
            params["tier"] = tier
        if run_id is not None:
            params["batch_peak_run_id"] = run_id
        data = self._get(f"batch-peaks/batch/{sample_batch_id}", params=params)
        if not data:
            logger.info("Batch '{}' has no batch peaks", sample_batch_id)
            return None
        return pd.DataFrame(data)

    def members(
        self, sample_batch_id: str, *, sample_id: str | None = None
    ) -> pd.DataFrame | None:
        """The whole ledger of a batch as flat rows: one per member, with the
        anchor's consensus beside the member's own reading.

        The endpoint is paged; this pages until it has every row, so the
        caller never sees a page boundary.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :param sample_id: Only this sample's members.
        :type sample_id: str, optional
        :return: A DataFrame with one row per member. Anchor columns:
                 ``batch_peak_id``, ``batch_mz``, ``consensus_formula``,
                 ``consensus_ion_formula``, ``consensus_ionization_mechanism_id``,
                 ``consensus_tier``, ``support_fraction``, ``n_present``,
                 ``is_ambiguous``, ``max_intensity``, ``isotopologue_of``,
                 ``curated``. Member columns: ``sample_item_id``,
                 ``sample_item_name``, ``sample_peak_id``, ``mz``,
                 ``intensity``, ``assigned_formula``, ``ion_formula``,
                 ``ionization_mechanism_id``, ``source``, ``tier``, ``role``,
                 ``fit_score``, ``p_correct``, ``owner_batch_peak_id``. None
                 when the batch has no ledger.
        :rtype: pd.DataFrame | None
        """
        path = f"batch-peaks/batch/{sample_batch_id}/members"
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {"limit": PAGE_LIMIT, "offset": offset}
            if sample_id is not None:
                params["sample_item_id"] = sample_id
            envelope = self._get_envelope(path, params=params)
            page = envelope.get("data") or []
            rows.extend(page)
            total = int(envelope.get("total") or 0)
            if not page or len(rows) >= total:
                break
            offset += len(page)
        if not rows:
            logger.info("Batch '{}' has no ledger members", sample_batch_id)
            return None
        logger.info(
            "Read {} ledger member(s) of batch '{}'", len(rows), sample_batch_id
        )
        return pd.DataFrame(rows)

    def verdicts(self, sample_batch_id: str) -> pd.DataFrame | None:
        """The batch-level verdicts recorded on a batch's species, newest
        first, superseded ones included.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :return: A DataFrame with one row per verdict: the batch peak, the
                 formula judged, ``verdict``, ``evidence_level``, ``note``,
                 ``verified_by``, ``verified_utc``, ``superseded_utc`` (null on
                 the live one), and ``stale`` (a live verdict about a formula
                 the consensus has since left). None when nothing was judged.
        :rtype: pd.DataFrame | None
        """
        data = self._get(f"batch-peaks/batch/{sample_batch_id}/verdicts")
        if not data:
            return None
        return _coerce_utc_columns(pd.DataFrame(data))

    def runs(self, sample_batch_id: str) -> pd.DataFrame | None:
        """A batch's runs, newest first: the batch-level operations that
        rewrote its ledger - a rebuild, an untargeted search with its
        parameters, an import - and the folds that built it.

        Exactly one run is ``current``, the live ledger; :meth:`list` with an
        earlier run's id reads the species table as that run left it.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :return: A DataFrame with one row per run: ``batch_peak_run_id``,
                 ``action``, ``engine``, ``engine_version``, ``status``,
                 ``current``, ``config``, ``summary``, ``error`` and the
                 timestamps. None when the batch has no ledger yet.
        :rtype: pd.DataFrame | None
        """
        data = self._get(f"batch-peaks/batch/{sample_batch_id}/runs")
        if not data:
            return None
        return _coerce_utc_columns(pd.DataFrame(data))
