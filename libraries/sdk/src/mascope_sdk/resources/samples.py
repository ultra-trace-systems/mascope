"""Samples resource for the Mascope SDK."""

import re
from collections.abc import Sequence
from typing import Any

import pandas as pd
from loguru import logger

from .._concurrent import run_concurrent
from .._resolve import _match_names, _pattern_text, resolve_id
from ._base import BaseResource, _coerce_datetime_columns
from .ms2 import Ms2Resource


class SamplesResource(BaseResource):
    """Resource for sample operations.

    Provides methods to retrieve sample data, spectra, peaks, and timeseries.
    Samples represent individual measurement files within a sample batch.
    All methods return pandas DataFrames for easy data analysis.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # List samples by batch name
        samples = mascope.samples.list("My Batch")

        # Get spectrum data
        spectrum = mascope.samples.get_spectrum(sample_id="sample-456")

        # Get peak data with match information
        peaks = mascope.samples.get_peaks(sample_id="sample-456")
    """

    def ms2(self, sample_id: str) -> Ms2Resource:
        """Access MS2 analysis sub-resource for a specific sample.

        :param sample_id: The sample item ID.
        :type sample_id: str
        :return: Ms2Resource instance bound to the given sample.
        :rtype: Ms2Resource

        Example::

            ms2 = mascope.samples.ms2("sample-456")
            summary = ms2.get_summary()
        """
        return Ms2Resource(self._client, sample_id)

    def _get_all_batches(self, dataset: str | None = None) -> pd.DataFrame | None:
        """Collect all batches, optionally scoped to a dataset."""
        if dataset is not None:
            return self._client.batches.list(dataset)

        datasets = self._client.datasets.list()
        if datasets is None or datasets.empty:
            raise ValueError("No datasets found.")
        frames = []
        for _, ws in datasets.iterrows():
            ws_batches = self._client.batches.list(ws["dataset_id"])
            if ws_batches is not None and not ws_batches.empty:
                frames.append(ws_batches)
        return pd.concat(frames, ignore_index=True) if frames else None

    def _resolve_batch_id(
        self, batch: "str | re.Pattern", dataset: str | None = None
    ) -> str:
        """Resolve a batch name or ID to a single batch ID.

        Raises if zero or multiple batches match.
        """
        all_batches = self._get_all_batches(dataset)
        return resolve_id(
            batch,
            all_batches,
            id_column="sample_batch_id",
            name_column="sample_batch_name",
            entity_label="batch",
        )

    def _resolve_batch_ids(
        self, batches: "str | re.Pattern", dataset: str | None = None
    ) -> Sequence[str]:
        """Resolve a batch substring or compiled pattern to one or more batch IDs."""
        all_batches = self._get_all_batches(dataset)
        if all_batches is None or all_batches.empty:
            raise ValueError("No batches found.")

        # Exact ID match
        if (
            isinstance(batches, str)
            and batches in all_batches["sample_batch_id"].values
        ):
            return [batches]

        matches = all_batches[_match_names(all_batches["sample_batch_name"], batches)]
        if matches.empty:
            available = all_batches["sample_batch_name"].tolist()
            raise ValueError(
                f"No batch matching '{_pattern_text(batches)}'. "
                f"Available batches: {available}"
            )
        return matches["sample_batch_id"].tolist()

    def list(
        self,
        batch: "str | re.Pattern | None" = None,
        *,
        batches: "str | re.Pattern | None" = None,
        dataset: str | None = None,
        drop_columns: Sequence[str] | None = None,
    ) -> pd.DataFrame | None:
        """List samples from one or more batches.

        Provide exactly one of ``batch`` or ``batches``:

        - ``batch`` resolves to a single batch (raises if the pattern
          matches more than one).
        - ``batches`` resolves to all batches whose name matches the
          given pattern.

        A plain string matches case-insensitively as a **literal substring**
        (regex metacharacters carry no special meaning); pass a compiled
        ``re.Pattern`` to match with a regular expression
        (e.g. ``re.compile("2026-01|2026-02")``), with case-sensitivity
        controlled by its flags.

        :param batch: Batch name or literal substring (or batch ID), or a
                      compiled regex pattern. Must match exactly one batch.
        :type batch: str | re.Pattern, optional
        :param batches: Batch name literal substring or compiled regex pattern.
                        Returns samples from every matching batch.
        :type batches: str | re.Pattern, optional
        :param dataset: Optional dataset name or ID to narrow the search.
                          If not provided, searches across all datasets.
        :type dataset: str, optional
        :return: A DataFrame containing sample information, or None if no
                 samples found. When ``batches`` is used the result includes
                 a ``sample_batch_name`` column.
        :param drop_columns: Optional list of columns to drop from the final DataFrame.
            If None, a default set of less relevant columns will be dropped.
        :type drop_columns: list[str], optional
        :rtype: pd.DataFrame | None
        :raises ValueError: If both or neither of ``batch``/``batches`` are
                            provided, or if ''batch'' matches multiple batches.
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            # Single batch (raises if ambiguous)
            samples = mascope.samples.list(batch="Uronium March")

            # All matching batches
            samples = mascope.samples.list(batches="Uronium")

            # Narrow to a dataset
            samples = mascope.samples.list(batch="Uronium", dataset="KORBI2")
        """
        if (batch is None) == (batches is None):
            raise ValueError("Provide exactly one of 'batch' or 'batches'.")

        if drop_columns is None:
            drop_columns = [
                "instrument_function_id",
                "sample_file_id",
                "ionization_mode_id",
                "locked",
                "sample_item_utc_created",
                "sample_item_utc_modified",
                "match",
            ]

        if batch is not None:
            batch_id = self._resolve_batch_id(batch, dataset=dataset)
            return self._list_by_id(batch_id)

        # batches (plural) - collect samples from all matching batches
        assert batches is not None  # for type checker
        batch_ids = self._resolve_batch_ids(batches, dataset=dataset)
        all_batches_df = self._get_all_batches(dataset)

        # Build a batch_id -> batch_name lookup
        id_to_name: dict[str, str] = {}
        if all_batches_df is not None:
            for _, row in all_batches_df.iterrows():
                id_to_name[row["sample_batch_id"]] = row["sample_batch_name"]

        logger.info("Listing samples from {} batch(es)", len(batch_ids))

        def _fetch_batch_samples(bid: str) -> pd.DataFrame | None:
            samples = self._list_by_id(bid)
            if samples is None or samples.empty:
                return None
            df = samples.copy()
            df.insert(0, "sample_batch_name", id_to_name.get(bid, ""))
            return df

        frames: list[pd.DataFrame] = run_concurrent(
            _fetch_batch_samples,
            [(bid,) for bid in batch_ids],
            max_workers=min(8, len(batch_ids)),
            desc="Listing samples",
            unit="batch",
        )

        if not frames:
            return None

        df = pd.concat(frames, ignore_index=True)
        if drop_columns is not None:
            df = df.drop(columns=drop_columns, errors="ignore")
        return df

    def _list_by_id(self, batch_id: str) -> pd.DataFrame | None:
        """List samples by batch ID (no name resolution)."""
        cache_key = f"samples:{batch_id}"
        if cache_key in self._client._cache:
            return self._client._cache[cache_key]
        data = self._get("samples", params={"sample_batch_id": batch_id})
        if not data:
            return None
        df = _coerce_datetime_columns(pd.DataFrame(data))
        self._client._cache[cache_key] = df
        return df

    def get(self, sample_id: str) -> dict | None:
        """Get details of a specific sample.

        :param sample_id: The ID of the sample to retrieve.
        :type sample_id: str
        :return: A dictionary containing the sample details, or None if not found.
        :rtype: dict | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            sample = mascope.samples.get(sample_id="sample-456")
            print(f"Sample: {sample['sample_item_name']}")
            print(f"Polarity: {sample.get('polarity')}")
        """
        return self._get(f"samples/{sample_id}")

    def get_peaks(
        self,
        sample_id: str,
        *,
        areas: bool = True,
        heights: bool = True,
        average: bool = True,
        matches: bool = True,
        t_min: float | None = None,
        t_max: float | None = None,
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> pd.DataFrame | None:
        """Get peak data from a sample.

        Retrieves detected peaks with automatic polarity filtering based on
        sample metadata. Match data is automatically flattened into columns
        for easy analysis.

        :param sample_id: The ID of the sample to retrieve peaks from.
        :type sample_id: str
        :param areas: Include peak areas (integrated intensity). Defaults to True.
        :type areas: bool
        :param heights: Include peak heights (max intensity). Defaults to True.
        :type heights: bool
        :param average: Return averaged data across time. Defaults to True.
        :type average: bool
        :param matches: Include matched compounds/ions/isotopes. Defaults to True.
        :type matches: bool
        :param t_min: Minimum time in seconds. Uses sample start if not provided.
        :type t_min: float, optional
        :param t_max: Maximum time in seconds. Uses sample end if not provided.
        :type t_max: float, optional
        :param mz_min: Minimum m/z value for filtering.
        :type mz_min: float, optional
        :param mz_max: Maximum m/z value for filtering.
        :type mz_max: float, optional
        :return: A DataFrame containing peak data. When a peak has
                 multiple isotope matches, it is expanded into one row
                 per match. Unmatched peaks have a single row with
                 ``NaN`` match columns.  Use ``target_ion_id`` /
                 ``target_compound_id`` for grouping to avoid
                 double-counting when different compounds share the
                 same formula.

                 Key columns:

                 - ``sample_item_id``: The sample ID
                 - ``peak_id``: Peak identifier (may repeat across
                   rows for multi-matched peaks)
                 - ``mz``, ``area``, ``height``
                 - ``match_score_isotope``, ``match_score_ion``,
                   ``match_score_compound``
                 - ``relative_abundance``
                 - ``target_isotope_id``, ``target_isotope_formula``
                 - ``target_ion_id``, ``target_ion_formula``
                 - ``target_compound_id``, ``target_compound_name``,
                   ``target_compound_formula``
                 - ``ionization_mechanism``
                 - ``target_collection_ids``

                 Returns None if no peaks are found.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            # Get all peaks with match information
            peaks = mascope.samples.get_peaks(sample_id="sample-456")

            # Filter to only matched peaks
            matched = peaks[peaks["target_compound_formula"].notna()]

            # Get peaks in a specific m/z range
            peaks = mascope.samples.get_peaks(
                sample_id="sample-456",
                mz_min=100,
                mz_max=200,
            )
        """
        params: dict[str, Any] = {
            "areas": str(areas).lower(),
            "heights": str(heights).lower(),
            "average": str(average).lower(),
            "matches": str(matches).lower(),
        }
        if t_min is not None:
            params["t_min"] = t_min
        if t_max is not None:
            params["t_max"] = t_max
        if mz_min is not None:
            params["mz_min"] = mz_min
        if mz_max is not None:
            params["mz_max"] = mz_max

        data = self._get(f"samples/{sample_id}/peaks", params=params)
        if not data:
            return None

        # Convert to DataFrame
        df = pd.DataFrame(data)

        # Add sample context column at the beginning
        df.insert(0, "sample_item_id", sample_id)

        # Flatten match data if present
        # Each peak has a list of matches (can be empty or have multiple).
        # We explode into one row per match so all candidate attributions
        # are visible.  Peaks without matches keep a single row with NaN
        # match columns.
        if "match" in df.columns and matches:
            match_keys = [
                "match_score_isotope",
                "relative_abundance",
                "target_isotope_id",
                "target_isotope_formula",
                "target_ion_id",
                "target_ion_formula",
                "target_compound_id",
                "target_compound_name",
                "target_compound_formula",
                "ionization_mechanism_id",
                "target_collection_ids",
            ]

            # Replace empty lists with [None] so unmatched peaks survive
            # the explode as a single row with NaN match columns.
            df["match"] = df["match"].apply(
                lambda x: x if isinstance(x, list) and len(x) > 0 else [None]
            )
            df = df.explode("match", ignore_index=True)

            for key in match_keys:
                df[key] = df["match"].apply(
                    lambda x, k=key: x.get(k) if isinstance(x, dict) else None
                )
            df = df.drop(columns=["match"])

            # Ion-level match score: weighted sum of isotope scores
            # by relative abundance.
            has_match = df["match_score_isotope"].notna()
            if has_match.any():
                matched_rows = df.loc[has_match]
                weighted = (
                    matched_rows["match_score_isotope"]
                    * matched_rows["relative_abundance"]
                )
                ion_scores = (
                    weighted.groupby(matched_rows["target_ion_id"], sort=False)
                    .sum()
                    .rename("match_score_ion")
                )
                df = df.merge(
                    ion_scores, left_on="target_ion_id", right_index=True, how="left"
                )

                # Compound-level match score: max of ion scores.
                compound_scores = (
                    df.loc[has_match]
                    .drop_duplicates(subset=["target_ion_id"])
                    .groupby("target_compound_id", sort=False)["match_score_ion"]
                    .max()
                    .rename("match_score_compound")
                )
                df = df.merge(
                    compound_scores,
                    left_on="target_compound_id",
                    right_index=True,
                    how="left",
                )
            else:
                df["match_score_ion"] = None
                df["match_score_compound"] = None

            # Resolve ionization_mechanism_id to human-readable name
            mechanisms = self._client.ionization.list()
            if mechanisms is not None and not mechanisms.empty:
                id_to_name = dict(
                    zip(
                        mechanisms["ionization_mechanism_id"],
                        mechanisms["ionization_mechanism"],
                    )
                )
                df["ionization_mechanism"] = df["ionization_mechanism_id"].map(
                    id_to_name
                )
                df = df.drop(columns=["ionization_mechanism_id"])

        return df

    def get_peak_timeseries(
        self,
        sample_id: str,
        mz: float | None = None,
        *,
        peak_id: str | None = None,
        mz_tolerance_ppm: float = 1.0,
        t_min: float | None = None,
        t_max: float | None = None,
    ) -> pd.DataFrame | None:
        """Get timeseries data for a specific peak.

        The peak can be identified by either ``peak_id`` (exact) or ``mz``
        (nearest within tolerance). When ``peak_id`` is provided, ``mz`` and
        ``mz_tolerance_ppm`` are ignored.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param mz: The m/z value of the peak. Required if ``peak_id`` is not provided.
        :type mz: float, optional
        :param peak_id: The unique peak identifier. If provided, ``mz`` is ignored.
        :type peak_id: str, optional
        :param mz_tolerance_ppm: m/z tolerance in ppm for peak matching
                                 (only used with ``mz``). Defaults to 1.0.
        :type mz_tolerance_ppm: float
        :param t_min: Minimum time in seconds. Uses sample start if not provided.
        :type t_min: float, optional
        :param t_max: Maximum time in seconds. Uses sample end if not provided.
        :type t_max: float, optional
        :return: A DataFrame containing timeseries data with columns:

                 - ``peak_id``: Peak identifier
                 - ``time``: Time in seconds
                 - ``height``: Intensity value at each time point
                 - ``mz``: Actual m/z of the matched peak

                 Returns None if no matching peak is found.
        :rtype: pd.DataFrame | None
        :raises ValueError: If neither ``peak_id`` nor ``mz`` is provided.
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample or peak is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            # By peak_id (exact)
            ts = mascope.samples.get_peak_timeseries(
                sample_id="sample-456",
                peak_id="abc123",
            )

            # By m/z (nearest match)
            ts = mascope.samples.get_peak_timeseries(
                sample_id="sample-456",
                mz=180.063,
                mz_tolerance_ppm=5.0,
            )
        """
        if peak_id is None and mz is None:
            raise ValueError("Either peak_id or mz must be provided")

        body: dict[str, Any] = {}
        if peak_id is not None:
            body["peak_id"] = peak_id
        else:
            body["peak_mz"] = mz
            body["peak_mz_tolerance_ppm"] = mz_tolerance_ppm
        if t_min is not None:
            body["t_min"] = t_min
        if t_max is not None:
            body["t_max"] = t_max

        data = self._post(f"samples/{sample_id}/peaks/timeseries", data=body)
        if not data:
            return None

        actual_peak_id = data.get("peak_id")
        actual_mz = data.get("mz")
        time_values = data.get("time", [])
        height_values = data.get("height", [])

        if not time_values:
            return None

        return pd.DataFrame(
            {
                "peak_id": actual_peak_id,
                "time": time_values,
                "height": height_values,
                "mz": actual_mz,
            }
        )

    def get_spectrum(
        self,
        sample_id: str,
        *,
        t_min: float | None = None,
        t_max: float | None = None,
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> pd.DataFrame | None:
        """Get spectrum data from a sample.

        Retrieves the averaged mass spectrum with automatic polarity filtering.
        The spectrum represents intensities averaged across all matching scans
        in the specified time window.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param t_min: Minimum time in seconds. Uses sample start if not provided.
        :type t_min: float, optional
        :param t_max: Maximum time in seconds. Uses sample end if not provided.
        :type t_max: float, optional
        :param mz_min: Minimum m/z value for filtering.
        :type mz_min: float, optional
        :param mz_max: Maximum m/z value for filtering.
        :type mz_max: float, optional
        :return: A DataFrame containing spectrum data with columns:

                 - ``mz``: m/z values
                 - ``intensity``: Intensity values

                 Returns None if no spectrum data is found.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            # Get full spectrum
            spectrum = mascope.samples.get_spectrum(sample_id="sample-456")

            # Plot the spectrum
            import matplotlib.pyplot as plt
            plt.stem(spectrum["mz"], spectrum["intensity"])
            plt.xlabel("m/z")
            plt.ylabel("Intensity")
            plt.show()
        """
        params: dict[str, Any] = {}
        if t_min is not None:
            params["t_min"] = t_min
        if t_max is not None:
            params["t_max"] = t_max
        if mz_min is not None:
            params["mz_min"] = mz_min
        if mz_max is not None:
            params["mz_max"] = mz_max

        data = self._get(f"samples/{sample_id}/spectrum", params=params or None)
        if not data:
            return None

        return pd.DataFrame(
            {
                "mz": data.get("mz", []),
                "intensity": data.get("intensity", []),
            }
        )

    def get_spectra(
        self,
        sample_ids: Sequence[str],
        *,
        t_min: float | None = None,
        t_max: float | None = None,
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> pd.DataFrame | None:
        """Get spectra for multiple samples.

        Retrieves averaged spectra for a list of samples with optional filtering.
        Useful for comparing spectra across multiple samples.

        :param sample_ids: List of sample IDs to retrieve spectra for.
        :type sample_ids: list[str]
        :param t_min: Minimum time in seconds.
        :type t_min: float, optional
        :param t_max: Maximum time in seconds.
        :type t_max: float, optional
        :param mz_min: Minimum m/z value for filtering.
        :type mz_min: float, optional
        :param mz_max: Maximum m/z value for filtering.
        :type mz_max: float, optional
        :return: A DataFrame containing spectra data with columns:

                 - ``sample_item_id``: The sample ID
                 - ``mz``: m/z values
                 - ``intensity``: Intensity values

                 Returns None if no data is found.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            spectra = mascope.samples.get_spectra(
                sample_ids=["sample-1", "sample-2", "sample-3"]
            )
            # Group by sample
            for sample_id, group in spectra.groupby("sample_item_id"):
                print(f"Sample {sample_id}: {len(group)} points")
        """
        params: dict[str, Any] = {"sample_item_ids": sample_ids}
        if t_min is not None:
            params["t_min"] = t_min
        if t_max is not None:
            params["t_max"] = t_max
        if mz_min is not None:
            params["mz_min"] = mz_min
        if mz_max is not None:
            params["mz_max"] = mz_max

        data = self._get("samples/spectra", params=params)
        if not data:
            return None

        # Combine all spectra into one DataFrame with sample_item_id column
        frames = []
        for i, spectrum in enumerate(data):
            sample_id = sample_ids[i] if i < len(sample_ids) else None
            df = pd.DataFrame(
                {
                    "sample_item_id": sample_id,
                    "mz": spectrum.get("mz", []),
                    "intensity": spectrum.get("intensity", []),
                }
            )
            frames.append(df)

        if not frames:
            return None

        return pd.concat(frames, ignore_index=True)

    def get_centroids(self, sample_ids: Sequence[str]) -> dict | None:
        """Get centroid data for multiple samples.

        Retrieves per-scan centroid data for the specified samples.

        :param sample_ids: List of sample IDs to retrieve centroids for.
        :type sample_ids: list[str]
        :return: A dictionary containing centroid data keyed by sample ID.
                 Returns None if no data is found.
        :rtype: dict | None
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        .. note::
            This method returns a dict rather than DataFrame due to the
            complex nested structure of per-scan centroid data.
        """
        return self._get("samples/centroids", params={"sample_item_ids": sample_ids})
