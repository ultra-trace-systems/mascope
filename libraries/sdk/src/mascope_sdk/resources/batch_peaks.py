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

import io
import json
import time
from collections.abc import Sequence
from typing import Any

import pandas as pd
from loguru import logger

from ._base import BaseResource, _coerce_utc_columns


#: Rows requested per members page: the endpoint's cap, so a batch's ledger
#: comes in as few round trips as it allows.
PAGE_LIMIT = 5000

#: The columns of :meth:`BatchPeaksResource.series`, in order: the member's
#: own, and the consensus of its batch peak beside them.
SERIES_COLUMNS = [
    "sample_item_id",
    "batch_peak_id",
    "sample_peak_id",
    "batch_mz",
    "consensus_formula",
    "consensus_ion_formula",
    "consensus_tier",
    "intensity",
    "tier",
]

#: The key of a peak-lists table's metadata that holds what the reads said.
PEAK_LISTS_METADATA_KEY = b"mascope"

#: Per batch peak, the consensus field each anchor column of the series is.
_SERIES_ANCHOR = {
    "batch_peak_id": "batch_peak_id",
    "batch_mz": "mz",
    "consensus_formula": "consensus_formula",
    "consensus_ion_formula": "consensus_ion_formula",
    "consensus_tier": "consensus_tier",
}

#: Per batch peak, the ``peak_series`` array each member column is.
_SERIES_MEMBER = {
    "sample_item_id": "sample_item_ids",
    "sample_peak_id": "sample_peak_ids",
    "intensity": "intensities",
    "tier": "tiers",
}


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

        # An external engine's batch-level result, landed on the ledger as a run
        run = mascope.batch_peaks.import_run(
            "batch-123", rows, engine="peaky", engine_version="0.7.0"
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
                 ``consensus_role`` (``reagent`` or ``artifact`` on a peak the
                 source accounts for, whose ion ``consensus_ion_formula`` then
                 names; null otherwise),
                 ``best_fit_score``, ``support_fraction``, ``n_present``,
                 ``is_ambiguous``, ``max_intensity`` (in
                 ``intensity_variable``), ``isotopologue_of`` (the batch peak
                 this one is an isotopologue of, or null), ``curated``
                 (pinned by hand for the whole batch) and
                 ``reference_listing`` (what a reference list calls the
                 consensus formula where a member matched it from one:
                 ``name``, ``source``, ``tags``, ``total``; null otherwise).
                 None when the batch has no ledger yet.
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
                 ``consensus_ion_formula``, ``consensus_role``,
                 ``consensus_ionization_mechanism_id``,
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

    def peak_lists(
        self,
        sample_batch_id: str,
        *,
        samples: "Sequence[str] | None" = None,
        matches: bool = False,
        satellites: bool = False,
        average: bool = True,
    ) -> pd.DataFrame:
        """Every peak of every sample of a batch, in one read.

        The batch form of :meth:`~mascope_sdk.resources.samples.SamplesResource.get_peaks`:
        each sample read as ``get_peaks`` reads it with no time or m/z range,
        and the rows stacked in the order :meth:`samples.list` gives the
        samples. The server sends the table as Parquet, so a batch of
        hundreds of samples is one request rather than one per sample.
        :meth:`~mascope_sdk.MascopeClient.load_peaks` reads batches this way
        from a server that offers it, a hundred samples per call.

        This is the read and nothing else: it does not need the batch to have
        a ledger, and it is not stamped with the server's provenance.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :param samples: Read only these samples of the batch, by
                        ``sample_item_id``: the server opens only their peak
                        lists. An ID that is no sample of the batch is named
                        in ``attrs["failures"]`` with status 404. Every
                        sample when omitted. Each ID goes in the request's
                        URL, so ask for at most a hundred or so at a time.
        :type samples: Sequence[str], optional
        :param matches: Add the match columns, one row per match of a peak; a
                        peak no target matched keeps one row with them null.
                        Defaults to False.
        :type matches: bool
        :param satellites: Keep the peaks detection flagged satellites -
                           sidelobes of a stronger peak - which ``get_peaks``
                           leaves out. Defaults to False.
        :type satellites: bool
        :param average: Average each peak's area and height over its scans;
                        False for the sums. Defaults to True.
        :type average: bool
        :return: One row per peak (per match with ``matches``):
                 ``sample_item_id``, ``sample_item_name``, ``datetime_utc``
                 (the sample's start), ``peak_id``, ``mz``, ``sparsity``,
                 ``signal_to_noise`` (``NaN`` where the file has no
                 estimate), ``segment`` (the scan stream of a stitched
                 sample's peak, ``NaN`` otherwise), ``area``, ``height`` and
                 ``is_satellite``. With ``matches``, also
                 ``match_score_isotope``, ``relative_abundance``,
                 ``target_isotope_id``, ``target_isotope_formula``,
                 ``target_ion_id``, ``target_ion_formula``,
                 ``target_compound_id``, ``target_compound_name``,
                 ``target_compound_formula``, ``ionization_mechanism_id``,
                 ``target_collection_ids`` and ``target_collection_names``.

                 ``attrs["samples"]`` lists each sample read and its row
                 count. ``attrs["failures"]`` lists each sample whose read
                 failed - ``sample_item_id``, ``sample_item_name``,
                 ``status_code``, ``error`` - and ``attrs["complete"]`` is
                 False when there is one: the rest of the batch is answered
                 without it. ``attrs["segments"]`` maps each sample read to
                 the segments of its stitched spectrum, None for one that is
                 not stitched.
        :rtype: pd.DataFrame
        :raises NotFoundError: From a server older than this read.
        :raises MascopeTimeoutError: If the server takes longer than the
            client's read timeout. Not retried, unlike other reads: the server
            builds the whole table before it answers, so a retry would only
            build it again. Ask for fewer ``samples`` at a time instead.
        :raises PermissionDeniedError: Without the guest role on the batch.
        :raises ValueError: If ``samples`` is empty: the server reads every
            sample of a batch when it is given none.

        Example::

            peaks = mascope.batch_peaks.peak_lists("batch-123")
            peaks.groupby("sample_item_id")["height"].sum()
        """
        if samples is not None and not samples:
            raise ValueError("samples is empty: pass None to read every sample")
        # Here, not at the top: a program that bundles the SDK but never
        # reads a table (the File Agent) leaves pyarrow out of its bundle
        import pyarrow as pa
        import pyarrow.parquet as pq

        response = self._get(
            f"batch-peaks/batch/{sample_batch_id}/peaks",
            params={
                "matches": str(matches).lower(),
                "satellites": str(satellites).lower(),
                "average": str(average).lower(),
                **({} if samples is None else {"sample_item_id": list(samples)}),
            },
            stream=True,
            # The server builds the whole table before it answers: sent again,
            # the read would only start over, beside the build still running
            retry_timeouts=False,
        )
        table = pq.read_table(io.BytesIO(response.content))
        said = json.loads(
            (table.schema.metadata or {}).get(PEAK_LISTS_METADATA_KEY, b"{}")
        )
        frame = table.to_pandas()
        for name in table.column_names:
            if pa.types.is_list(table.schema.field(name).type):
                # As get_peaks lists them, not as numpy arrays
                frame[name] = frame[name].map(
                    lambda v: v.tolist() if v is not None else None
                )
        failures = list(said.get("failures") or [])
        frame.attrs["samples"] = list(said.get("samples") or [])
        frame.attrs["failures"] = failures
        frame.attrs["complete"] = not failures
        frame.attrs["segments"] = dict(said.get("segments") or {})
        logger.info(
            "Read {} peak(s) of {} sample(s) of batch '{}'",
            len(frame),
            len(frame.attrs["samples"]),
            sample_batch_id,
        )
        if failures:
            logger.warning(
                "{} sample(s) of batch '{}' could not be read: {}",
                len(failures),
                sample_batch_id,
                "; ".join(
                    f"{f.get('sample_item_name')}: {f.get('error')}" for f in failures
                ),
            )
        return frame

    def series(
        self,
        sample_batch_id: str,
        *,
        tier: str | None = None,
        min_n_present: int = 2,
        run_id: str | None = None,
    ) -> pd.DataFrame:
        """Every sample's intensity of every batch peak of a batch, one row
        per (sample, batch peak).

        One read per batch, over the compact series route the app's batch
        overview draws from: the slim counterpart of :meth:`members`, with no
        paging. With no ``run_id`` the batch's runs are listed first, to name
        the current one.

        While a rebuild or search run is in flight, the previous run is still
        the current one but the live ledger is being rewritten: the rows can
        then be half of each, whatever ``attrs["run_id"]`` says. A warning is
        logged when the runs show one running; read again once it finishes.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :param tier: Only the batch peaks of this consensus tier:
                     ``assigned`` | ``candidate`` | ``below_assignability`` |
                     ``unassigned``.
        :type tier: str, optional
        :param min_n_present: Only the batch peaks seen in at least this many
                              samples. Defaults to 2, the route's own, which
                              leaves out the peaks one sample alone saw; 1
                              keeps every one.
        :type min_n_present: int
        :param run_id: Read the series as this batch run left them (see
                       :meth:`runs`); the live ledger when omitted.
        :type run_id: str, optional
        :return: A DataFrame with the member's ``sample_item_id``,
                 ``sample_peak_id``, ``intensity`` (in the unit the batch
                 peak's ``intensity_variable`` in :meth:`list` names) and
                 ``tier``, beside its batch peak's ``batch_peak_id``,
                 ``batch_mz``, ``consensus_formula``,
                 ``consensus_ion_formula`` and ``consensus_tier``; empty, with
                 those columns, when the batch has no ledger.
                 ``attrs["run_id"]`` is the run read: ``run_id`` when given,
                 else the run current when the read began, or None for a batch
                 with no runs. Stamped with the server's
                 ``attrs["provenance"]`` as the loaders' frames are. Join
                 ``samples.list(batch=...)`` on ``sample_item_id`` for each
                 sample's time.
        :rtype: pd.DataFrame
        :raises PermissionDeniedError: Without the guest role on the batch.
        :raises NotFoundError: If ``run_id`` is no run of the batch.

        Example::

            series = mascope.batch_peaks.series("batch-123", tier="assigned")
            series.attrs["run_id"]  # which run's formulas these are
        """
        if run_id is None:
            runs = self._get(f"batch-peaks/batch/{sample_batch_id}/runs") or []
            run_id = next(
                (r["batch_peak_run_id"] for r in runs if r.get("current")), None
            )
            if any(r.get("status") == "running" for r in runs):
                logger.warning(
                    "A batch run of batch '{}' is in flight: its ledger is being "
                    "rewritten, so these series can mix the current run and the "
                    "new one. Read them again once it finishes.",
                    sample_batch_id,
                )
        body: dict[str, Any] = {
            "sample_batch_id": sample_batch_id,
            "min_n_present": min_n_present,
        }
        if tier is not None:
            body["tier"] = tier
        if run_id is not None:
            body["batch_peak_run_id"] = run_id

        columns: dict[str, list[Any]] = {name: [] for name in SERIES_COLUMNS}
        for peak in self._post("batch-peaks/records/series", body) or []:
            series = peak["peak_series"]
            n = len(series["sample_item_ids"])
            for column, key in _SERIES_MEMBER.items():
                columns[column].extend(series[key])
            for column, key in _SERIES_ANCHOR.items():
                columns[column].extend([peak[key]] * n)
        frame = pd.DataFrame(columns).astype(
            {"batch_mz": "float64", "intensity": "float64"}
        )
        frame.attrs["run_id"] = run_id
        logger.info(
            "Read {} batch-peak point(s) of batch '{}'", len(frame), sample_batch_id
        )
        return self._client._with_provenance(frame)

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

    def import_run(
        self,
        sample_batch_id: str,
        rows: "pd.DataFrame | list[dict[str, Any]]",
        *,
        engine: str,
        engine_version: str,
        config: dict[str, Any] | None = None,
        mz_tolerance_ppm: float = 5.0,
        wait: bool = True,
        poll_interval: float = 2.0,
        timeout: float = 900.0,
    ) -> dict[str, Any] | None:
        """Land an external engine's batch-level result on the batch ledger,
        as a batch run.

        ``rows`` is one identity per m/z: ``mz``, ``formula`` (neutral),
        optionally ``ion_formula`` and ``ionization_mechanism_id`` (the adduct
        as this deployment's mechanism id; a row without one cannot be
        measured and is skipped). Other columns are ignored. Each row is
        matched to the batch peak nearest its m/z within ``mz_tolerance_ppm``
        and measured against that peak's members by the server, so the ledger
        carries the server's fit of the engine's formula under the engine's
        name. Curated and isotopologue batch peaks are left alone. The run's
        ``summary`` counts what landed and, by reason, what did not.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :param rows: The engine's rows, as a DataFrame or a list of dicts.
        :type rows: pd.DataFrame | list[dict]
        :param engine: The engine's name (not the server's own).
        :type engine: str
        :param engine_version: The engine's version string.
        :type engine_version: str
        :param config: The engine's own record of the run, kept on the batch
                       run verbatim.
        :type config: dict, optional
        :param mz_tolerance_ppm: The m/z match tolerance. Defaults to 5.
        :type mz_tolerance_ppm: float
        :param wait: Poll until the run completes or fails. Defaults to True.
        :type wait: bool
        :param poll_interval: Seconds between polls.
        :type poll_interval: float
        :param timeout: Seconds to wait before giving up on the poll.
        :type timeout: float
        :return: The run record - ``status`` completed with its ``summary``, or
                 failed with its ``error`` - or the running record when
                 ``wait`` is False. None when the server accepted nothing.
        :rtype: dict | None
        :raises TimeoutError: When the run is still running after ``timeout``.
        """
        if isinstance(rows, pd.DataFrame):
            keep = [
                c
                for c in ("mz", "formula", "ion_formula", "ionization_mechanism_id")
                if c in rows.columns
            ]
            rows = rows[keep].astype(object).where(rows[keep].notna(), None)
            rows = rows.to_dict("records")
        body = {
            "engine": engine,
            "engine_version": engine_version,
            "config": config,
            "mz_tolerance_ppm": mz_tolerance_ppm,
            "rows": list(rows),
        }
        data = self._post(f"batch-peaks/batch/{sample_batch_id}/runs/import", body)
        run = data[0] if data else None
        if run is None or not wait:
            return run
        run_id = run["batch_peak_run_id"]
        deadline = time.monotonic() + timeout
        while True:
            runs = self._get(f"batch-peaks/batch/{sample_batch_id}/runs") or []
            latest = next((r for r in runs if r["batch_peak_run_id"] == run_id), None)
            if latest is not None and latest.get("status") != "running":
                return latest
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"batch import run '{run_id}' still running after {timeout} s"
                )
            time.sleep(poll_interval)
