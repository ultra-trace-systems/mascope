"""High-level data loading functions for the Mascope SDK."""

import re
from typing import Any

import pandas as pd
from loguru import logger

from ._concurrent import run_concurrent
from ._resolve import _name_mask
from .client import MascopeClient


def _resolve_sample(client: MascopeClient, sample: str) -> str:
    """Resolve a sample name or ID to a sample_item_id.

    Searches the metadata cache first (fast, no API calls if samples were
    previously listed). Falls back to ``samples.get()`` for a direct ID lookup.

    :param client: The MascopeClient instance.
    :param sample: Sample name (or substring) or sample ID.
    :return: The resolved sample_item_id.
    :raises ValueError: If the sample cannot be found.
    """
    from ._resolve import resolve_id
    from .exceptions import NotFoundError

    # Search cached sample lists
    cached_samples = [
        df for key, df in client._cache.items() if key.startswith("samples:")
    ]
    if cached_samples:
        all_samples = pd.concat(cached_samples, ignore_index=True)
        try:
            return resolve_id(
                sample,
                all_samples,
                id_column="sample_item_id",
                name_column="sample_item_name",
                entity_label="sample",
            )
        except ValueError:
            pass  # Not in cache, try direct API call

    # Fall back to direct API call by ID
    try:
        sample_data = client.samples.get(sample)
        if sample_data:
            return sample_data["sample_item_id"]
    except NotFoundError:
        pass

    raise ValueError(
        f"Sample '{sample}' not found. "
        "Load samples first with samples.list() or load_peaks(), "
        "then retry with the sample name."
    )


def _confirm_sample_count(count: int, threshold: int) -> None:
    """Ask the user to confirm if sample count exceeds *threshold*.

    Raises ``KeyboardInterrupt`` when the user declines.
    """
    # INFO: preamble for the interactive confirmation prompt below, not a fault
    logger.info(
        (
            "The requested number of samples ({}) exceeds the confirmation threshold "
            "{}. Please check the confirmation prompt."
        ),
        count,
        threshold,
    )
    try:
        answer = input(
            f"About to load data for {count} samples. This may take a while."
            f" Continue? [y/N] "
        )
    except EOFError:
        # Non-interactive environment (e.g. script) - proceed silently
        return
    if answer.strip().lower() not in ("y", "yes"):
        raise KeyboardInterrupt(
            f"Cancelled by user ({count} samples exceeded threshold of {threshold})"
        )


def _resolve_batches(
    client: MascopeClient,
    dataset: "str | re.Pattern",
    batches: "str | re.Pattern | None" = None,
    *,
    exact: bool = False,
) -> "tuple[pd.DataFrame | None, str]":
    """Resolve a dataset and the batches in it the filter keeps.

    :param client: The MascopeClient instance.
    :param dataset: Dataset name or literal substring (or ID); pass a compiled
      ``re.Pattern`` to match by regex.
    :param batches: Optional case-insensitive filter on batch names. A string is
      a literal substring (or full-name match when ``exact`` is True); a
      compiled ``re.Pattern`` is used as a regex. See :func:`_name_mask`.
    :param exact: Require the filter to match the whole name instead of a substring.
    :return: The batches kept (None when the dataset has none, or none match)
      and the dataset id.
    :raises ValueError: If the dataset cannot be resolved.
    """
    from ._resolve import resolve_id

    datasets = client.datasets.list()
    dataset_id = resolve_id(
        dataset,
        datasets,
        id_column="dataset_id",
        name_column="dataset_name",
        entity_label="dataset",
    )
    logger.info("Loading dataset '{}'", dataset)
    all_batches = client.batches._list_by_id(dataset_id)
    if all_batches is None or all_batches.empty:
        logger.info("No batches found in dataset")
        return None, dataset_id
    if batches is not None:
        all_batches = all_batches[
            _name_mask(all_batches["sample_batch_name"], batches, exact=exact)
        ]
        if all_batches.empty:
            logger.info("No batches matching '{}'", batches)
            return None, dataset_id
    batch_names = all_batches["sample_batch_name"].tolist()
    logger.info("Found {} batch(es): {}", len(all_batches), batch_names)
    return all_batches, dataset_id


def _collect_sample_tasks(
    client: MascopeClient,
    dataset: "str | re.Pattern",
    batches: "str | re.Pattern | None" = None,
    samples: "str | re.Pattern | None" = None,
    *,
    exact: bool = False,
) -> tuple[list[tuple[Any, str]], str]:
    """Resolve dataset/batches and collect (sample_row, batch_name) pairs.

    :param client: The MascopeClient instance.
    :param dataset: Dataset name or literal substring (or ID); pass a compiled
      ``re.Pattern`` to match by regex.
    :param batches: Optional case-insensitive filter on batch names. A string is
      a literal substring (or full-name match when ``exact`` is True); a
      compiled ``re.Pattern`` is used as a regex. See :func:`_name_mask`.
    :param samples: Optional case-insensitive filter on sample names, same
      semantics as ``batches``.
    :param exact: Require the filter to match the whole name instead of a substring.
    :return: Tuple of (sample_tasks, dataset_id).
    :raises ValueError: If dataset or batches cannot be resolved.
    """
    all_batches, dataset_id = _resolve_batches(client, dataset, batches, exact=exact)
    if all_batches is None:
        return [], dataset_id

    sample_tasks: list[tuple[Any, str]] = []
    for _, batch_row in all_batches.iterrows():
        batch_id = batch_row["sample_batch_id"]
        batch_name = batch_row["sample_batch_name"]

        batch_samples = client.samples._list_by_id(batch_id)
        if batch_samples is None or batch_samples.empty:
            logger.info("Batch '{}': no samples, skipping", batch_name)
            continue

        if samples is not None:
            batch_samples = batch_samples[
                _name_mask(batch_samples["sample_item_name"], samples, exact=exact)
            ]
            if batch_samples.empty:
                continue

        logger.info("Batch '{}': {} sample(s)", batch_name, len(batch_samples))
        for _, sample_row in batch_samples.iterrows():
            sample_tasks.append((sample_row, batch_name))

    return sample_tasks, dataset_id


def load_peaks(
    client: MascopeClient,
    dataset: "str | re.Pattern",
    batches: "str | re.Pattern | None" = None,
    *,
    samples: "str | re.Pattern | None" = None,
    exact: bool = False,
    matches: bool = True,
    areas: bool = True,
    heights: bool = True,
    average: bool = True,
    confirm_above: int | None = 100,
    max_workers: int = 8,
) -> pd.DataFrame | None:
    """Load peaks for all samples across one or more batches.

    Handles the typical workflow of selecting a dataset, filtering batches
    by name, iterating all samples, and concatenating peak data into a single
    DataFrame enriched with batch and sample metadata.

    Requests are made concurrently for better performance. A progress bar is
    displayed during loading.

    :param client: The MascopeClient instance.
    :type client: MascopeClient
    :param dataset: Dataset name or literal substring (or dataset ID); pass a
                    compiled ``re.Pattern`` to match by regex.
    :type dataset: str | re.Pattern
    :param batches: Optional case-insensitive filter on batch names. A string is
                    a literal substring, so it can select several batches at once
                    (e.g. ``"blank"`` matches every batch whose name contains
                    "blank"); pass a compiled ``re.Pattern`` to match by regex.
                    If not provided, all batches in the dataset are loaded.
    :type batches: str | re.Pattern, optional
    :param samples: Optional case-insensitive filter on sample names, same
                    semantics as ``batches``.
    :type samples: str | re.Pattern, optional
    :param exact: Match a string ``batches`` / ``samples`` against the whole name
                  instead of as a substring. Use this to select a single named
                  batch. Not valid with a compiled pattern. Defaults to False.
    :type exact: bool
    :param matches: Include matched compounds/ions/isotopes. Defaults to True.
    :type matches: bool
    :param areas: Include peak areas. Defaults to True.
    :type areas: bool
    :param heights: Include peak heights. Defaults to True.
    :type heights: bool
    :param average: Return averaged data across time. Defaults to True.
    :type average: bool
    :param confirm_above: If the number of samples exceeds this threshold,
                          an interactive confirmation prompt is shown before
                          loading starts. Set to ``None`` to disable.
                          Defaults to 100.
    :type confirm_above: int | None
    :param max_workers: Maximum number of concurrent requests. Defaults to 8.
    :type max_workers: int
    :return: A DataFrame containing all peaks enriched with columns:

             - ``sample_batch_name``: Name of the batch the sample belongs to
             - ``sample_item_name``: Name of the sample
             - ``datetime_utc``: Measurement start timestamp (UTC)

             Plus all columns from
               :meth:`~mascope_sdk.resources.samples.SamplesResource.get_peaks`.

             When a peak matches multiple isotopes it is expanded into
             one row per match.  Use ``target_ion_id`` /
             ``target_compound_id`` for grouping to avoid
             double-counting peaks whose matches share the same
             formula.

             Returns None if no peaks are found.
    :rtype: pd.DataFrame | None
    :raises ValueError: If the dataset or batches cannot be resolved.
    :raises KeyboardInterrupt: If the user declines the confirmation prompt.

    Example::

        mascope = MascopeClient()

        # Load all peaks from batches containing "Uronium"
        peaks = mascope.load_peaks(
            dataset="My Dataset",
            batches="Uronium",
        )

        # Filter by sample name
        peaks = mascope.load_peaks(
            dataset="My Dataset",
            samples="blank",
        )

        # Disable confirmation prompt
        peaks = mascope.load_peaks(
            dataset="My Dataset",
            confirm_above=None,
        )
    """
    sample_tasks, _ = _collect_sample_tasks(
        client, dataset, batches, samples=samples, exact=exact
    )
    if not sample_tasks:
        logger.info("No samples found")
        return None

    if confirm_above is not None and len(sample_tasks) > confirm_above:
        _confirm_sample_count(len(sample_tasks), confirm_above)

    # Load peaks concurrently with progress bar
    def _fetch_peaks(sample_row: Any, batch_name: str) -> pd.DataFrame | None:
        sample_id = sample_row["sample_item_id"]
        peaks = client.samples.get_peaks(
            sample_id,
            matches=matches,
            areas=areas,
            heights=heights,
            average=average,
        )
        if peaks is None or peaks.empty:
            return None

        # Enrich with batch and sample context
        peaks.insert(0, "sample_batch_name", batch_name)
        peaks.insert(
            peaks.columns.get_loc("sample_item_id") + 1,
            "sample_item_name",
            sample_row["sample_item_name"],
        )
        if "datetime_utc" in sample_row.index:
            peaks.insert(
                peaks.columns.get_loc("sample_item_name") + 1,
                "datetime_utc",
                sample_row["datetime_utc"],
            )
        return peaks

    frames: list[pd.DataFrame] = run_concurrent(
        _fetch_peaks,
        sample_tasks,
        max_workers=max_workers,
        desc="Loading peaks",
        unit="sample",
    )

    if not frames:
        logger.info("No peaks found")
        return None

    # Drop all-NA columns per frame to avoid FutureWarning on concat
    # with mixed empty/populated columns.
    frames = [f.dropna(axis=1, how="all") for f in frames]
    result = pd.concat(frames, ignore_index=True)
    logger.info("Loaded {} peaks total", len(result))
    return result


def load_assignments(
    client: MascopeClient,
    dataset: "str | re.Pattern",
    batches: "str | re.Pattern | None" = None,
    *,
    samples: "str | re.Pattern | None" = None,
    exact: bool = False,
    run: str = "latest",
    tier: str | None = None,
    source: str | None = None,
    confirm_above: int | None = 100,
    max_workers: int = 8,
) -> pd.DataFrame | None:
    """Load peak assignments for all samples across one or more batches.

    The peak-assignment counterpart of :func:`load_peaks`: resolves the
    dataset/batch/sample selection, reads each sample's latest completed peak
    assignment run, and concatenates everything into a single DataFrame
    enriched with batch and sample metadata.

    Read-only: samples without a completed assignment run contribute nothing
    (and are logged) rather than being assigned on the fly. Each per-sample
    fetch pages the run internally and pulls core rows only - the per-peak
    ``alternatives``/``provenance`` JSON stays behind
    :meth:`~mascope_sdk.resources.peak_assignments.PeakAssignmentsResource.detail`.

    :param client: The MascopeClient instance.
    :type client: MascopeClient
    :param dataset: Dataset name or literal substring (or dataset ID); pass a
                    compiled ``re.Pattern`` to match by regex.
    :type dataset: str | re.Pattern
    :param batches: Optional case-insensitive filter on batch names. A string
                    is a literal substring; pass a compiled ``re.Pattern`` to
                    match by regex. If not provided, all batches in the
                    dataset are loaded.
    :type batches: str | re.Pattern, optional
    :param samples: Optional case-insensitive filter on sample names, same
                    semantics as ``batches``.
    :type samples: str | re.Pattern, optional
    :param exact: Match a string ``batches`` / ``samples`` against the whole
                  name instead of as a substring. Not valid with a compiled
                  pattern. Defaults to False.
    :type exact: bool
    :param run: Which run to read per sample. Only ``"latest"`` (the latest
                completed run of each sample) is supported; run IDs are
                per-sample and cannot be given across samples.
    :type run: str
    :param tier: Filter by confidence tier.
    :type tier: str, optional
    :param source: Filter by assignment source (``database``/``untargeted``/
                   ``manual`` - the last being rows a person assigned by hand).
                   A curated row leaves the two engine sources, so reading a
                   batch as ``database`` plus ``untargeted`` drops it.
    :type source: str, optional
    :param confirm_above: If the number of samples exceeds this threshold,
                          an interactive confirmation prompt is shown before
                          loading starts. Set to ``None`` to disable.
                          Defaults to 100.
    :type confirm_above: int | None
    :param max_workers: Maximum number of concurrent requests. Defaults to 8.
    :type max_workers: int
    :return: A DataFrame containing all assignments enriched with columns:

             - ``sample_batch_name``: Name of the batch the sample belongs to
             - ``sample_item_name``: Name of the sample
             - ``datetime_utc``: Measurement start timestamp (UTC)

             Plus all columns from
             :meth:`~mascope_sdk.resources.peak_assignments.PeakAssignmentsResource.get`
             (one row per observed peak).

             Returns None if no assignments are found.
    :rtype: pd.DataFrame | None
    :raises ValueError: If the dataset or batches cannot be resolved, or
                        ``run`` is not ``"latest"``.
    :raises KeyboardInterrupt: If the user declines the confirmation prompt.

    Example::

        assignments = load_assignments(
            mascope, dataset="My Dataset", batches="Uronium"
        )
        assignments.groupby("sample_item_name")["tier"].value_counts()
    """
    if run != "latest":
        raise ValueError(
            "Only run='latest' is supported: assignment run IDs are "
            "per-sample, so a single run ID cannot select runs across "
            "samples. Use peak_assignments.get(sample_id, run_id=...) to "
            "read a specific run of one sample."
        )

    sample_tasks, _ = _collect_sample_tasks(
        client, dataset, batches, samples=samples, exact=exact
    )
    if not sample_tasks:
        logger.info("No samples found")
        return None

    if confirm_above is not None and len(sample_tasks) > confirm_above:
        _confirm_sample_count(len(sample_tasks), confirm_above)

    # Load assignments concurrently with progress bar
    def _fetch_assignments(sample_row: Any, batch_name: str) -> pd.DataFrame | None:
        sample_id = sample_row["sample_item_id"]
        assignments = client.peak_assignments.get(sample_id, tier=tier, source=source)
        if assignments is None or assignments.empty:
            logger.info(
                "Sample '{}': no assignments (no completed run, or filters "
                "matched nothing), skipping",
                sample_row["sample_item_name"],
            )
            return None

        # Enrich with batch and sample context (rows already carry
        # sample_item_id from the API)
        assignments.insert(0, "sample_batch_name", batch_name)
        assignments.insert(
            assignments.columns.get_loc("sample_item_id") + 1,
            "sample_item_name",
            sample_row["sample_item_name"],
        )
        if "datetime_utc" in sample_row.index:
            assignments.insert(
                assignments.columns.get_loc("sample_item_name") + 1,
                "datetime_utc",
                sample_row["datetime_utc"],
            )
        return assignments

    frames: list[pd.DataFrame] = run_concurrent(
        _fetch_assignments,
        sample_tasks,
        max_workers=max_workers,
        desc="Loading assignments",
        unit="sample",
    )

    if not frames:
        logger.info("No assignments found")
        return None

    skipped = len(sample_tasks) - len(frames)
    if skipped:
        logger.info("{} sample(s) contributed no assignments", skipped)

    # Drop all-NA columns per frame to avoid FutureWarning on concat
    # with mixed empty/populated columns.
    frames = [f.dropna(axis=1, how="all") for f in frames]
    result = pd.concat(frames, ignore_index=True)
    logger.info("Loaded {} assignments total", len(result))
    return result


def load_peaks_by_stage(
    client: MascopeClient,
    sample: str,
    stages: list[tuple[float, float] | tuple[float, float, str]],
    *,
    matches: bool = True,
    areas: bool = True,
    heights: bool = True,
    max_workers: int = 8,
) -> pd.DataFrame | None:
    """Load averaged peaks for each time-range stage of a single sample.

    For each stage (time range), requests the averaged peak list from the API
    and concatenates the results into a single DataFrame. This is useful when
    a measurement consists of several stages (e.g. blank, sample introduction,
    wash) and the scientist wants to compare the peak list per stage.

    Requests are made concurrently for better performance.

    :param client: The MascopeClient instance.
    :type client: MascopeClient
    :param sample: Sample name or sample ID. If a name is given, it is
                   resolved via the API. Use ``samples.list()`` to find
                   available samples.
    :type sample: str
    :param stages: List of time-range tuples defining stages. Each element can
                   be ``(t_min, t_max)`` or ``(t_min, t_max, name)`` where
                   *name* is a human-readable label for the stage.
    :type stages: list[tuple[float, float] | tuple[float, float, str]]
    :param matches: Include matched compounds/ions/isotopes. Defaults to True.
    :type matches: bool
    :param areas: Include peak areas. Defaults to True.
    :type areas: bool
    :param heights: Include peak heights. Defaults to True.
    :type heights: bool
    :param max_workers: Maximum number of concurrent requests. Defaults to 8.
    :type max_workers: int
    :return: A DataFrame containing peaks with columns:

             - ``stage``: 0-based stage index
             - ``stage_name``: Stage label (from the tuple, or None)
             - ``t_min``: Start time of the stage in seconds
             - ``t_max``: End time of the stage in seconds

             Plus all columns from
               :meth:`~mascope_sdk.resources.samples.SamplesResource.get_peaks`.
             Returns None if no peaks are found.
    :rtype: pd.DataFrame | None
    :raises ValueError: If stages is empty or the sample cannot be found.

    Example::

        mascope = MascopeClient()

        # Define named stages
        stages = [
            (0, 30, "blank"),
            (30, 120, "sample"),
            (120, 180, "wash"),
        ]

        peaks = mascope.load_peaks_by_stage(
            sample="my-sample-id",
            stages=stages,
        )

        # Compare areas between stages
        peaks.groupby("stage_name")["area"].sum()
    """
    if not stages:
        raise ValueError(
            "stages must be a non-empty list of (t_min, t_max[, name]) tuples"
        )

    # Resolve sample name or ID using cached sample lists
    sample_id = _resolve_sample(client, sample)

    # Normalise stages to (t_min, t_max, name | None)
    normalised: list[tuple[float, float, str | None]] = []
    for s in stages:
        if len(s) == 3:
            normalised.append((s[0], s[1], str(s[2])))  # type: ignore
        elif len(s) == 2:
            normalised.append((s[0], s[1], None))
        else:
            raise ValueError(
                "Each stage must be a tuple of (t_min, t_max) or (t_min, t_max, name)"
            )

    def _fetch_stage_peaks(
        stage_idx: int,
        t_min: float,
        t_max: float,
        stage_name: str | None,
    ) -> pd.DataFrame | None:
        peaks = client.samples.get_peaks(
            sample_id,
            matches=matches,
            areas=areas,
            heights=heights,
            average=True,
            t_min=t_min,
            t_max=t_max,
        )
        if peaks is None or peaks.empty:
            return None

        peaks["stage"] = stage_idx
        peaks["stage_name"] = stage_name
        peaks["t_min"] = t_min
        peaks["t_max"] = t_max
        return peaks

    frames: list[pd.DataFrame] = run_concurrent(
        _fetch_stage_peaks,
        [
            (idx, t_min, t_max, name)
            for idx, (t_min, t_max, name) in enumerate(normalised)
        ],
        max_workers=max_workers,
        desc="Loading stages",
        unit="stage",
    )

    if not frames:
        logger.info("No peaks found")
        return None

    frames = [f.dropna(axis=1, how="all") for f in frames]
    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("stage").reset_index(drop=True)
    logger.info("Loaded {} peaks across {} stages", len(result), len(stages))
    return result


_FORMULA_COLUMNS = {
    "compound": "target_compound_formula",
    "ion": "target_ion_formula",
    "isotope": "target_isotope_formula",
}

_NAME_COLUMNS = {
    "compound": "target_compound_name",
}


def load_peak_timeseries(
    client: MascopeClient,
    dataset: "str | re.Pattern",
    batches: "str | re.Pattern | None" = None,
    *,
    samples: "str | re.Pattern | None" = None,
    exact: bool = False,
    compound: str | list[str] | None = None,
    ion: str | list[str] | None = None,
    isotope: str | list[str] | None = None,
    confirm_above: int | None = 20,
    max_workers: int = 8,
) -> pd.DataFrame | None:
    """Load intra-sample peak timeseries for matched peaks across batches.

    Resolves a compound, ion, or isotope formula to the corresponding peak IDs
    via match data, then fetches the per-scan timeseries for each peak in each
    sample. The hierarchy is: compound -> ions -> isotopes -> peaks (1:1).

    Provide exactly one of ``compound``, ``ion``, or ``isotope``. Each accepts
    a single string or a list of strings to load timeseries for multiple
    targets in a single pass (peaks are discovered once per sample).

    Requests are made concurrently for better performance. A progress bar is
    displayed during loading.

    :param client: The MascopeClient instance.
    :type client: MascopeClient
    :param dataset: Dataset name or literal substring (or dataset ID); pass a
                    compiled ``re.Pattern`` to match by regex.
    :type dataset: str | re.Pattern
    :param batches: Optional case-insensitive filter on batch names. A string is
                    a literal substring; pass a compiled ``re.Pattern`` for regex.
    :type batches: str | re.Pattern, optional
    :param samples: Optional case-insensitive filter on sample names, same
                    semantics as ``batches``.
    :type samples: str | re.Pattern, optional
    :param exact: Match a string ``batches`` / ``samples`` against the whole name
                  instead of as a substring. Not valid with a compiled pattern.
                  Defaults to False.
    :type exact: bool
    :param compound: Target compound name(s) or formula(s).
    :type compound: str | list[str], optional
    :param ion: Target ion formula(s) to resolve.
    :type ion: str | list[str], optional
    :param isotope: Target isotope formula(s) to resolve.
    :type isotope: str | list[str], optional
    :param confirm_above: If the number of samples exceeds this threshold,
                          an interactive confirmation prompt is shown before
                          loading starts. Set to ``None`` to disable.
                          Defaults to 20.
    :type confirm_above: int | None
    :param max_workers: Maximum number of concurrent requests. Defaults to 8.
    :type max_workers: int
    :return: A DataFrame with one row per time point per peak, containing:

             - ``sample_batch_name``: Batch name
             - ``sample_item_id``: Sample ID
             - ``sample_item_name``: Sample name
             - ``datetime_utc``: Absolute datetime per data point (UTC)
             - ``peak_id``: Peak identifier
             - ``mz``: Actual m/z of the peak
             - ``target_compound_name``: Matched compound name
             - ``target_compound_formula``: Matched compound formula
             - ``target_ion_formula``: Matched ion formula
             - ``target_isotope_formula``: Matched isotope formula
             - ``time``: Relative time in seconds within the sample
             - ``height``: Intensity at each time point

             Returns None if no matching peaks are found.
    :rtype: pd.DataFrame | None
    :raises ValueError: If zero or more than one formula parameter is provided.
    :raises KeyboardInterrupt: If the user declines the confirmation prompt.

    Example::

        mascope = MascopeClient()

        # Timeseries for all peaks matched to Urea
        ts = mascope.load_peak_timeseries(
            dataset="My Dataset",
            batches="Uronium",
            compound="CH4N2O",
        )

        # Multiple compounds in one call
        ts = mascope.load_peak_timeseries(
            dataset="My Dataset",
            batches="Uronium",
            compound=["CH4N2O", "Lactic acid"],
        )
    """
    # Validate exactly one formula parameter is provided
    provided = {
        k: v
        for k, v in {"compound": compound, "ion": ion, "isotope": isotope}.items()
        if v is not None
    }
    if len(provided) != 1:
        raise ValueError(
            "Provide exactly one of 'compound', 'ion', or 'isotope'. "
            f"Got: {list(provided.keys()) or 'none'}"
        )
    formula_level, formula_raw = next(iter(provided.items()))
    formula_column = _FORMULA_COLUMNS[formula_level]
    # Normalise to a list of values
    formula_values: list[str] = (
        formula_raw if isinstance(formula_raw, list) else [formula_raw]
    )
    formula_set = set(formula_values)

    # --- Discover samples across batches ---
    sample_tasks, _ = _collect_sample_tasks(
        client, dataset, batches, samples=samples, exact=exact
    )
    if not sample_tasks:
        logger.info("No samples found")
        return None

    if confirm_above is not None and len(sample_tasks) > confirm_above:
        _confirm_sample_count(len(sample_tasks), confirm_above)

    # --- Load peaks with matches for each sample (concurrent) ---
    # to discover which peak_ids match the formula
    logger.info("Resolving peaks matching {} in {}", formula_column, formula_values)

    def _get_matched_peaks(
        sample_row: Any, batch_name: str
    ) -> list[tuple[Any, str, str, str | None, str | None, str | None, str | None]]:
        """Return (sample_row, batch_name, peak_id, compound_name,compound_formula,
        ion, isotope)."""
        sample_id = sample_row["sample_item_id"]
        peaks = client.samples.get_peaks(sample_id, matches=True)
        if peaks is None or peaks.empty:
            return []

        # Match by formula OR by name (for compounds)
        mask = peaks[formula_column].isin(formula_set)
        name_column = _NAME_COLUMNS.get(formula_level)
        if name_column and name_column in peaks.columns:
            mask = mask | peaks[name_column].isin(formula_set)
        matched = peaks[mask]
        if matched.empty:
            return []

        result = []
        for _, peak_row in matched.iterrows():
            result.append(
                (
                    sample_row,
                    batch_name,
                    peak_row["peak_id"],
                    peak_row.get("target_compound_name"),
                    peak_row.get("target_compound_formula"),
                    peak_row.get("target_ion_formula"),
                    peak_row.get("target_isotope_formula"),
                )
            )
        return result

    # Collect all peak tasks across all samples
    matched_lists = run_concurrent(
        _get_matched_peaks,
        sample_tasks,
        max_workers=max_workers,
        desc="Finding peaks",
        unit="sample",
    )
    all_peak_tasks: list[
        tuple[Any, str, str, str | None, str | None, str | None, str | None]
    ] = [task for batch in matched_lists for task in batch]

    if not all_peak_tasks:
        logger.info("No peaks matching {} in {}", formula_column, formula_values)
        return None

    logger.info(
        "Found {} peak(s) across {} sample(s)",
        len(all_peak_tasks),
        len({t[0]["sample_item_id"] for t in all_peak_tasks}),
    )

    # --- Fetch timeseries for each peak (concurrent) ---
    def _fetch_timeseries(
        sample_row: Any,
        batch_name: str,
        peak_id: str,
        compound_name: str | None,
        compound_formula: str | None,
        ion_formula: str | None,
        isotope_formula: str | None,
    ) -> pd.DataFrame | None:
        ts = client.samples.get_peak_timeseries(
            sample_id=sample_row["sample_item_id"],
            peak_id=peak_id,
        )
        if ts is None or ts.empty:
            return None

        # Enrich with context
        ts.insert(0, "sample_batch_name", batch_name)
        ts.insert(1, "sample_item_id", sample_row["sample_item_id"])
        ts.insert(2, "sample_item_name", sample_row["sample_item_name"])
        if "datetime_utc" in sample_row.index:
            sample_t0 = pd.Timestamp(sample_row["datetime_utc"])
            # Absolute datetime per data point = sample start + relative time
            ts.insert(
                3,
                "datetime_utc",
                sample_t0 + pd.to_timedelta(ts["time"], unit="s"),
            )

        # Add match context
        ts["target_compound_name"] = compound_name
        ts["target_compound_formula"] = compound_formula
        ts["target_ion_formula"] = ion_formula
        ts["target_isotope_formula"] = isotope_formula
        return ts

    frames: list[pd.DataFrame] = run_concurrent(
        _fetch_timeseries,
        all_peak_tasks,
        max_workers=max_workers,
        desc="Loading timeseries",
        unit="peak",
    )

    if not frames:
        logger.info("No timeseries data loaded")
        return None

    frames = [f.dropna(axis=1, how="all") for f in frames]
    result = pd.concat(frames, ignore_index=True)
    logger.info("Loaded {} timeseries points total", len(result))
    return result


def load_batch_ledger(
    client: MascopeClient,
    dataset: "str | re.Pattern",
    batches: "str | re.Pattern | None" = None,
    *,
    exact: bool = False,
    members: bool = True,
) -> pd.DataFrame | None:
    """Load the batch ledger of one or more batches into a single DataFrame.

    The batch-primary counterpart of :func:`load_assignments`: resolves the
    dataset/batch selection and reads each batch's ledger - its batch peaks
    (one per species across the batch) and, by default, their members (one
    per sample the species was seen in, the anchor's consensus beside the
    sample's own reading) - concatenated with ``sample_batch_name`` prepended.

    :param client: The MascopeClient instance.
    :type client: MascopeClient
    :param dataset: Dataset name or literal substring (or dataset ID); pass a
                    compiled ``re.Pattern`` to match by regex.
    :type dataset: str | re.Pattern
    :param batches: Optional case-insensitive filter on batch names.
    :type batches: str | re.Pattern, optional
    :param exact: Match a string ``batches`` against the whole name.
    :type exact: bool
    :param members: Return the member rows (default) rather than the species
                    table. With members, the species table rides on
                    ``df.attrs["batch_peaks"]``.
    :type members: bool
    :return: The ledger rows of every batch that has one, or None.
    :rtype: pd.DataFrame | None
    :raises ValueError: If the dataset cannot be resolved.

    Example::

        ledger = load_batch_ledger(mascope, dataset="My Dataset", batches="Uronium")
        ledger.to_csv("ledger.csv", index=False)
        ledger.attrs["batch_peaks"]  # one row per batch peak
    """
    all_batches, _ = _resolve_batches(client, dataset, batches, exact=exact)
    if all_batches is None:
        return None
    frames: list[pd.DataFrame] = []
    species_frames: list[pd.DataFrame] = []
    for _, batch_row in all_batches.iterrows():
        batch_id = batch_row["sample_batch_id"]
        batch_name = batch_row["sample_batch_name"]
        species = client.batch_peaks.list(batch_id)
        if species is None:
            logger.info("Batch '{}': no batch ledger yet, skipping", batch_name)
            continue
        species = species.copy()
        species.insert(0, "sample_batch_name", batch_name)
        if not members:
            frames.append(species)
            continue
        rows = client.batch_peaks.members(batch_id)
        if rows is None:
            logger.info("Batch '{}': a ledger with no members, skipping", batch_name)
            continue
        rows = rows.copy()
        rows.insert(0, "sample_batch_name", batch_name)
        frames.append(rows)
        species_frames.append(species)
    if not frames:
        logger.info("No batch ledger found")
        return None
    result = pd.concat(frames, ignore_index=True)
    if members:
        result.attrs["batch_peaks"] = pd.concat(species_frames, ignore_index=True)
    logger.info("Loaded {} ledger row(s) from {} batch(es)", len(result), len(frames))
    return result
