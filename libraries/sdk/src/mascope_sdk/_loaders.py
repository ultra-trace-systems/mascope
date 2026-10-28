"""High-level data loading functions for the Mascope SDK."""

import copy
import os
import re
from collections.abc import Callable, Iterable
from typing import Any

import pandas as pd
from loguru import logger

from ._concurrent import TaskFailure, failure_record, mark_complete, run_concurrent
from ._http import status_error
from ._resolve import _name_mask
from .client import MascopeClient
from .exceptions import ConfirmationRequiredError, NotFoundError


def _note_warnings(warned: list[str], peaks: pd.DataFrame | None) -> None:
    """Keep what the server warned about one read, for the combined frame.

    ``pd.concat`` keeps ``attrs`` only where every input's are equal, and a
    read that came back empty is dropped before the concat - the read most
    worth a warning, when every one of its peaks was left out. So each read's
    warnings are gathered as it arrives and set on the result once.

    :param warned: The loader's running list, appended to in place.
    :param peaks: What ``get_peaks`` returned for one sample or stage.
    """
    if peaks is not None:
        warned.extend(peaks.attrs.get("warnings", ()))


def _distinct(warned: list[str]) -> list[str]:
    """Each warning once, in the order first seen.

    The stages of one sample are all short by the same peaks, so they all
    warn alike.
    """
    return list(dict.fromkeys(warned))


def _sample_failures(
    failures: list[TaskFailure], *, peak: bool = False
) -> list[dict[str, Any]]:
    """The records of failed per-sample tasks, whose args start ``(row, batch)``.

    :param failures: What :func:`run_concurrent` returned as failed.
    :param peak: The tasks were per peak, the peak id third in their args.
    """
    records = []
    for failure in failures:
        sample_row, batch_name = failure.args[:2]
        where = {
            "sample_batch_name": batch_name,
            "sample_item_id": sample_row["sample_item_id"],
            "sample_item_name": sample_row["sample_item_name"],
        }
        if peak:
            where["peak_id"] = failure.args[2]
        records.append(failure_record(failure, **where))
    return records


def _stack(frames: list[pd.DataFrame], warned: list[str]) -> pd.DataFrame | None:
    """One frame from the reads of a loader, or None when there is nothing.

    A read that listed no peak adds no rows. When none of them did, what is
    returned depends on why: nothing found and nothing warned is None, as it
    always was. But reads that warned came back empty because every peak was
    left out of them - the state of any sample that was detected and never
    matched - and None would say "no peaks here" to a script and take the
    warnings with it. That is an empty frame instead, with the columns of a
    read, for the caller to set the warnings on.

    :param frames: What each read returned, the empty ones included.
    :param warned: What the reads warned.
    :return: The rows of every read, an empty frame, or None.
    """
    filled = [frame for frame in frames if not frame.empty]
    if filled:
        # Drop all-NA columns per frame to avoid FutureWarning on concat
        # with mixed empty/populated columns.
        filled = [frame.dropna(axis=1, how="all") for frame in filled]
        return pd.concat(filled, ignore_index=True)
    if warned and frames:
        return frames[0].iloc[0:0].copy()
    return None


def _same(a: Any, b: Any) -> bool:
    """Whether two ``attrs`` values are the same, without leaving it to ``==``.

    A frame compared with ``==`` answers cell by cell, and asking that answer
    for its truth raises. It is how ``pd.concat`` itself fails on frames whose
    ``attrs`` hold one - the batch ledger's species table.
    """
    if isinstance(a, (pd.DataFrame, pd.Series)):
        return type(a) is type(b) and a.equals(b)
    try:
        return bool(a == b)
    except (TypeError, ValueError):
        return False


def concat_frames(
    frames: Iterable[pd.DataFrame | None], *, ignore_index: bool = True
) -> pd.DataFrame | None:
    """Stack frames the SDK returned, keeping what their ``attrs`` say.

    ``pd.concat`` keeps ``attrs`` only when every input's are equal, all or
    nothing. Two peak reads that are short by different numbers of peaks
    differ in ``attrs["warnings"]``, so concatenating them drops the warnings
    and the provenance with them - and that is the common case for a loop
    over samples.

    Here each entry of ``attrs`` is decided on its own. The warnings of every
    frame are kept, each once, and so are the failures of every frame; the
    result is ``complete`` unless some frame says it is not. Any other entry
    is kept when every frame carries it with the same value - the provenance
    of loads from one build - and dropped otherwise.

    The rows are stacked without their ``attrs``, so nothing depends on how
    pandas compares them: it compares with ``==``, which raises on frames
    whose ``attrs`` hold a frame, as a batch ledger's do.

    :param frames: The frames to stack. None entries - a load that found
        nothing - are skipped.
    :param ignore_index: Number the rows afresh. Defaults to True.
    :return: The stacked frame, or None when there was no frame to stack.
    """
    kept = [frame for frame in frames if frame is not None]
    if not kept:
        return None

    bare = []
    for frame in kept:
        view = frame.copy(deep=False)
        view.attrs = {}
        bare.append(view)
    result = pd.concat(bare, ignore_index=ignore_index)

    attrs = {
        key: copy.deepcopy(value)
        for key, value in kept[0].attrs.items()
        if key != "warnings"
        and all(
            key in frame.attrs and _same(frame.attrs[key], value) for frame in kept[1:]
        )
    }
    if any("warnings" in frame.attrs for frame in kept):
        attrs["warnings"] = _distinct(
            [warning for frame in kept for warning in frame.attrs.get("warnings", ())]
        )
    if any("failures" in frame.attrs for frame in kept):
        attrs["failures"] = [
            copy.deepcopy(failure)
            for frame in kept
            for failure in frame.attrs.get("failures", ())
        ]
    if any("complete" in frame.attrs for frame in kept):
        attrs["complete"] = all(frame.attrs.get("complete", True) for frame in kept)
    result.attrs = attrs
    return result


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

    Raises ``KeyboardInterrupt`` when the user declines, and
    :class:`~mascope_sdk.exceptions.ConfirmationRequiredError` when there is
    no interactive input to ask (stdin at end of file, as in a script or an
    agent's shell): a caller that cannot answer is not waved through.
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
        raise ConfirmationRequiredError(count, threshold) from None
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
    matches: bool | None = None,
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

    Each batch is read in one request (``batch_peaks.peak_lists``) from a
    server that offers it; with ``samples``, the request names the samples
    chosen (a hundred per request), so the server reads no others. From an
    older server, whose answer to that is 404, each sample is read on its
    own, concurrently. A progress bar is displayed during loading.

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
    :param matches: Include matched compounds/ions/isotopes. By default they
                    are left out when each batch is read in one request, and
                    included when an older server has each sample read on
                    its own, as they always were.
    :type matches: bool | None
    :param areas: Include peak areas. Defaults to True.
    :type areas: bool
    :param heights: Include peak heights. Defaults to True.
    :type heights: bool
    :param average: Return averaged data across time. Defaults to True.
    :type average: bool
    :param confirm_above: If the number of samples exceeds this threshold,
                          an interactive confirmation prompt is shown before
                          loading starts; a caller with no interactive input
                          gets :class:`~mascope_sdk.ConfirmationRequiredError`
                          instead. Set to ``None`` to disable.
                          Defaults to 100.
    :type confirm_above: int | None
    :param max_workers: Maximum number of concurrent requests. Defaults to 8.
    :type max_workers: int
    :return: A DataFrame containing all peaks enriched with columns:

             - ``sample_batch_name``: Name of the batch the sample belongs to
             - ``sample_item_name``: Name of the sample
             - ``datetime_utc``: Measurement start timestamp (UTC)

             Plus all columns from
               :meth:`~mascope_sdk.resources.samples.SamplesResource.get_peaks`,
             and ``is_satellite`` (False throughout: satellite peaks are left
             out) where each batch was read in one request.

             When a peak matches multiple isotopes it is expanded into
             one row per match.  Use ``target_ion_id`` /
             ``target_compound_id`` for grouping to avoid
             double-counting peaks whose matches share the same
             formula.

             Returns None if no peaks are found and nothing was warned
             about.

             ``df.attrs["warnings"]`` lists what the server warned about
             the samples' reads, each warning once; empty when it warned
             about none.

             ``df.attrs["complete"]`` is False when the read of some
             sample failed, and ``df.attrs["failures"]`` names each
             one and its error ([] when none did). When every read
             failed the result is an empty frame carrying them.

             ``df.attrs["segments"]`` maps each sample read, by
             ``sample_item_id``, to its ``attrs["segments"]`` from
             ``get_peaks``: the scan streams of a stitched sample, None for
             one that is not stitched.
    :rtype: pd.DataFrame | None
    :raises ValueError: If the dataset or batches cannot be resolved.
    :raises KeyboardInterrupt: If the user declines the confirmation prompt.
    :raises ConfirmationRequiredError: If the prompt has no interactive
        input to read.

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

    if getattr(client, "_server_has_batch_peak_lists", True):
        result = _load_batch_peak_lists(
            client,
            sample_tasks,
            # With no sample filter, every sample of each batch is asked for
            whole_batches=samples is None,
            matches=bool(matches),
            areas=areas,
            heights=heights,
            average=average,
            max_workers=max_workers,
        )
        if result is not _NO_BATCH_READ:
            return result

    return load_sample_peaks(
        client,
        sample_tasks,
        matches=True if matches is None else matches,
        areas=areas,
        heights=heights,
        average=average,
        max_workers=max_workers,
    )


#: What :func:`_load_batch_peak_lists` answers when the server cannot read a
#: batch in one request, and each sample has to be read on its own.
_NO_BATCH_READ = object()

#: Samples named in one batch read when a load asks for part of a batch.
#: Each ID goes in the request's URL; a hundred keep it well inside the 8 KB
#: a proxy commonly allows a request line.
_SAMPLES_PER_READ = 100


def _load_batch_peak_lists(
    client: MascopeClient,
    sample_tasks: list[tuple[Any, str]],
    *,
    whole_batches: bool,
    matches: bool,
    areas: bool,
    heights: bool,
    average: bool,
    max_workers: int,
) -> Any:
    """Load the samples' peaks one batch per request, as :func:`load_peaks`.

    Each batch's peak lists come in one read
    (:meth:`~mascope_sdk.resources.batch_peaks.BatchPeaksResource.peak_lists`),
    and are laid out as :func:`load_sample_peaks` lays out its per-sample
    reads: the same rows, the same columns in the same order, the same
    ``attrs``. Where only some samples of a batch are asked for, the read
    names them, so the server opens no other sample's peaks: a hundred per
    request (:data:`_SAMPLES_PER_READ`).

    A sample the server could not read is a failure of that sample; a read
    that failed is a failure of each of its samples asked for; so is a sample
    asked for that the read neither answered nor named. Each is named in
    ``attrs["failures"]`` as the per-sample read's would be.

    :param whole_batches: Every sample of each batch is asked for, so each
        batch is read whole, in one request.
    :return: What :func:`load_sample_peaks` would, or :data:`_NO_BATCH_READ`
        when the server answers 404 to every batch: it is older than the
        read, and is not asked again.
    """
    by_batch: dict[str, list[tuple[Any, str]]] = {}
    for task in sample_tasks:
        sample_row = task[0]
        if "sample_batch_id" not in sample_row.index:
            # A listing that does not say which batch: read sample by sample
            return _NO_BATCH_READ
        by_batch.setdefault(sample_row["sample_batch_id"], []).append(task)

    # Per read: its index, its batch, the samples it answers for, and
    # whether it names them
    reads: list[tuple[int, str, list[tuple[Any, str]], bool]] = []
    for batch_id, tasks in by_batch.items():
        step = len(tasks) if whole_batches else _SAMPLES_PER_READ
        for begin in range(0, len(tasks), step):
            reads.append(
                (len(reads), batch_id, tasks[begin : begin + step], not whole_batches)
            )

    def _fetch(
        index: int, batch_id: str, tasks: list[tuple[Any, str]], named: bool
    ) -> pd.DataFrame:
        return client.batch_peaks.peak_lists(
            batch_id,
            samples=[row["sample_item_id"] for row, _ in tasks] if named else None,
            matches=matches,
            average=average,
        )

    frames, failed = run_concurrent(
        _fetch,
        reads,
        max_workers=min(max_workers, len(reads)),
        desc="Loading peaks",
        unit="read",
    )
    if not frames and all(isinstance(f.error, NotFoundError) for f in failed):
        client._server_has_batch_peak_lists = False
        # INFO: the load goes on, a request per sample, as it always did
        logger.info(
            "The server reads no batch's peak lists in one request; "
            "reading each sample on its own"
        )
        return _NO_BATCH_READ

    failures: list[dict[str, Any]] = []
    for failure in failed:
        for sample_row, batch_name in failure.args[2]:
            failures.append(
                failure_record(
                    failure,
                    sample_batch_name=batch_name,
                    sample_item_id=sample_row["sample_item_id"],
                    sample_item_name=sample_row["sample_item_name"],
                )
            )

    def _sample_failure(
        sample_row: Any, batch_name: str, error: Exception
    ) -> dict[str, Any]:
        return failure_record(
            TaskFailure((sample_row, batch_name), error),
            sample_batch_name=batch_name,
            sample_item_id=sample_row["sample_item_id"],
            sample_item_name=sample_row["sample_item_name"],
        )

    unread = {f.args[0] for f in failed}
    tables: list[pd.DataFrame] = []
    segments: dict[str, list[dict] | None] = {}
    for (_, batch_id, tasks, _named), frame in zip(
        [r for r in reads if r[0] not in unread], frames
    ):
        rows = {row["sample_item_id"]: (row, name) for row, name in tasks}
        url = f"{client.url}/api/batch-peaks/batch/{batch_id}/peaks"
        answered = {s.get("sample_item_id") for s in frame.attrs.get("samples", ())}
        for said in frame.attrs.get("failures", ()):
            sample_id = said.get("sample_item_id")
            if sample_id not in rows:
                continue
            answered.add(sample_id)
            error = status_error(said.get("status_code"), said.get("error", ""), url)
            failures.append(_sample_failure(*rows[sample_id], error))
        for sample_id, (sample_row, batch_name) in rows.items():
            if sample_id not in answered:
                # Gone between the listing and the read: as the per-sample
                # read of it would have answered
                error = status_error(
                    404, f"Sample '{sample_id}' was not in its batch's read", url
                )
                failures.append(_sample_failure(sample_row, batch_name, error))
        for sample_id, sample_segments in frame.attrs.get("segments", {}).items():
            if sample_id in rows:
                segments[sample_id] = sample_segments
        tables.append(_as_sample_reads(client, frame, rows, matches=matches))

    kept = [table for table in tables if not table.empty]
    result = pd.concat(kept, ignore_index=True) if kept else None
    if result is not None:
        # As the per-sample reads are stacked: a column none of them filled
        # is not there (_stack)
        result = result.dropna(axis=1, how="all")
        if not areas:
            result = result.drop(columns=["area"], errors="ignore")
        if not heights:
            result = result.drop(columns=["height"], errors="ignore")
    result = mark_complete(result, failures)
    if result is None:
        logger.info("No peaks found")
        return None

    result.attrs["segments"] = segments
    result.attrs["warnings"] = []
    logger.info("Loaded {} peaks total", len(result))
    return result


def _as_sample_reads(
    client: MascopeClient,
    frame: pd.DataFrame,
    rows: dict[str, tuple[Any, str]],
    *,
    matches: bool,
) -> pd.DataFrame:
    """A batch's peak lists laid out as :func:`load_sample_peaks` lays out
    its reads of the same samples.

    Only the samples asked for are kept, in the order they were asked for:
    the listing's, which a server need not keep for samples that started at
    the same moment. Their names and times come from the listing too, as the
    per-sample reads take them, rather than from the table.

    :param frame: The batch's peak lists.
    :param rows: Per sample asked for in order, its listing row and its
        batch's name.
    :param matches: The table carries the match columns.
    """
    from .resources.samples import score_matches

    frame = frame[frame["sample_item_id"].isin(rows)]
    frame = frame.drop(columns=["sample_item_name", "datetime_utc"])
    # Stable: a sample's own rows keep the order the server listed them in
    position = {sample_id: index for index, sample_id in enumerate(rows)}
    frame = frame.iloc[
        frame["sample_item_id"].map(position).argsort(kind="stable")
    ].reset_index(drop=True)
    if matches:
        frame = score_matches(frame, client)

    def column(of: Callable[[Any, str], Any]) -> pd.Series:
        return frame["sample_item_id"].map(
            {sample_id: of(*task) for sample_id, task in rows.items()}
        )

    frame.insert(0, "sample_batch_name", column(lambda row, name: name))
    frame.insert(2, "sample_item_name", column(lambda row, _: row["sample_item_name"]))
    if all("datetime_utc" in row.index for row, _ in rows.values()):
        frame.insert(3, "datetime_utc", column(lambda row, _: row["datetime_utc"]))
    # The read's own attrs name every sample it read, asked for or not, and
    # pandas carries them through all of the above: the load sets its own
    frame.attrs = {}
    return frame


def save_snapshot(frame: pd.DataFrame, path: "str | os.PathLike[str]") -> None:
    """Write a loaded frame to a Parquet file that reads back as it was.

    ``attrs`` - the provenance, the failures, the segments - go into the
    file's metadata, where ``pd.read_parquet`` finds them again: a snapshot
    a script can work from offline, with no server and no credentials.

    :param frame: What a loader returned.
    :param path: Where to write it; an existing file is replaced.
    """
    frame.to_parquet(path, index=False)
    logger.info("Saved {} rows to {}", len(frame), os.fspath(path))


def load_sample_peaks(
    client: MascopeClient,
    sample_tasks: list[tuple[Any, str]],
    *,
    matches: bool = True,
    areas: bool = True,
    heights: bool = True,
    average: bool = True,
    max_workers: int = 8,
    on_result: Callable[[tuple[Any, ...], pd.DataFrame], None] | None = None,
) -> pd.DataFrame | None:
    """Load the peaks of samples already chosen, as :func:`load_peaks` does.

    The half of :func:`load_peaks` after the samples are resolved, for a
    caller that resolves them itself (the ``mascope_ai`` toolkit, by ID): it
    never prompts, and returns what :func:`load_peaks` returns - the enriched
    rows, ``attrs["complete"]``, ``attrs["failures"]``, ``attrs["warnings"]``
    and ``attrs["segments"]`` - without the provenance stamp, which the
    client's methods add.

    :param client: The MascopeClient instance.
    :param sample_tasks: ``(sample_row, batch_name)`` per sample, the row as
        the sample listing gives it (``sample_item_id``,
        ``sample_item_name`` and, if known, ``datetime_utc``).
    :param on_result: Called with each sample's task and its enriched peaks
        as they arrive (see :func:`run_concurrent`); whatever it raises stops
        the load and propagates.
    :return: As :func:`load_peaks`.
    """
    warned: list[str] = []
    # Each sample's own: the samples of a load need not be stitched alike
    segments: dict[str, list[dict] | None] = {}

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
        _note_warnings(warned, peaks)
        if peaks is None:
            return None
        segments[sample_id] = peaks.attrs.get("segments")

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

    frames, failures = run_concurrent(
        _fetch_peaks,
        sample_tasks,
        max_workers=max_workers,
        desc="Loading peaks",
        unit="sample",
        on_result=on_result,
    )

    result = mark_complete(_stack(frames, warned), _sample_failures(failures))
    if result is None:
        logger.info("No peaks found")
        return None

    result.attrs["segments"] = segments
    result.attrs["warnings"] = _distinct(warned)
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
                          loading starts; a caller with no interactive input
                          gets :class:`~mascope_sdk.ConfirmationRequiredError`
                          instead. Set to ``None`` to disable.
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

             ``df.attrs["complete"]`` is False when the read of some
             sample failed, and ``df.attrs["failures"]`` names each
             one and its error ([] when none did). When every read
             failed the result is an empty frame carrying them.
    :rtype: pd.DataFrame | None
    :raises ValueError: If the dataset or batches cannot be resolved, or
                        ``run`` is not ``"latest"``.
    :raises KeyboardInterrupt: If the user declines the confirmation prompt.
    :raises ConfirmationRequiredError: If the prompt has no interactive
        input to read.

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

    frames, failures = run_concurrent(
        _fetch_assignments,
        sample_tasks,
        max_workers=max_workers,
        desc="Loading assignments",
        unit="sample",
    )
    failed = _sample_failures(failures)

    if not frames:
        logger.info("No assignments found")
        return mark_complete(None, failed)

    skipped = len(sample_tasks) - len(frames) - len(failed)
    if skipped:
        logger.info("{} sample(s) contributed no assignments", skipped)

    # Drop all-NA columns per frame to avoid FutureWarning on concat
    # with mixed empty/populated columns.
    frames = [f.dropna(axis=1, how="all") for f in frames]
    result = pd.concat(frames, ignore_index=True)
    logger.info("Loaded {} assignments total", len(result))
    return mark_complete(result, failed)


def load_peaks_by_stage(
    client: MascopeClient,
    sample: str,
    stages: list[tuple[float, float] | tuple[float, float, str]],
    *,
    matches: bool = True,
    areas: bool = True,
    heights: bool = True,
    max_workers: int = 8,
    on_result: Callable[[tuple[Any, ...], pd.DataFrame], None] | None = None,
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
    :param on_result: Called with each stage's task, ``(stage, t_min, t_max,
        name)``, and its peaks as they arrive (see :func:`run_concurrent`);
        whatever it raises stops the load and propagates. Not offered by
        :meth:`MascopeClient.load_peaks_by_stage`; the ``mascope_ai`` toolkit
        counts rows against its cap with it.
    :type on_result: Callable, optional
    :return: A DataFrame containing peaks with columns:

             - ``stage``: 0-based stage index
             - ``stage_name``: Stage label (from the tuple, or None)
             - ``t_min``: Start time of the stage in seconds
             - ``t_max``: End time of the stage in seconds

             Plus all columns from
               :meth:`~mascope_sdk.resources.samples.SamplesResource.get_peaks`.
             Returns None if no peaks are found and nothing was warned about.

             ``df.attrs["warnings"]`` lists what the server warned about
             the stages, each warning once; it is empty when no stage was
             warned about.

             ``df.attrs["complete"]`` is False when the read of some
             stage failed, and ``df.attrs["failures"]`` names each
             one and its error ([] when none did). When every read
             failed the result is an empty frame carrying them.

             ``df.attrs["segments"]`` is the sample's, as ``get_peaks``
             gives it: the scan streams of a stitched sample, None for one
             that is not stitched.
    :rtype: pd.DataFrame | None
    :raises ValueError: If stages is empty or the sample cannot be found.

    .. note::

        A stage is a time-ranged read, which leaves out every peak whose time
        series the server has not computed yet - typically the peaks no target
        matched. When ``df.attrs["warnings"]`` says so, have them computed with
        ``mascope.samples.compute_peak_timeseries(sample_id)`` and load again.

        When *every* peak is left out - a sample that was detected and never
        matched - the result is an empty frame carrying the warnings, not
        None, so ``df.attrs["warnings"]`` can be read in that case too.

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

    warned: list[str] = []
    # The stages are reads of one sample, so they all say the same of it
    segments: list[list[dict] | None] = []

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
        _note_warnings(warned, peaks)
        if peaks is None:
            return None
        segments.append(peaks.attrs.get("segments"))

        peaks["stage"] = stage_idx
        peaks["stage_name"] = stage_name
        peaks["t_min"] = t_min
        peaks["t_max"] = t_max
        return peaks

    frames, failures = run_concurrent(
        _fetch_stage_peaks,
        [
            (idx, t_min, t_max, name)
            for idx, (t_min, t_max, name) in enumerate(normalised)
        ],
        max_workers=max_workers,
        desc="Loading stages",
        unit="stage",
        on_result=on_result,
    )
    failed = [
        failure_record(
            failure,
            sample_item_id=sample_id,
            stage=failure.args[0],
            stage_name=failure.args[3],
            t_min=failure.args[1],
            t_max=failure.args[2],
        )
        for failure in failures
    ]

    result = _stack(frames, warned)
    if result is not None:
        result = result.sort_values("stage").reset_index(drop=True)
    result = mark_complete(result, failed)
    if result is None:
        logger.info("No peaks found")
        return None

    result.attrs["segments"] = segments[0] if segments else None
    result.attrs["warnings"] = _distinct(warned)
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
                          loading starts; a caller with no interactive input
                          gets :class:`~mascope_sdk.ConfirmationRequiredError`
                          instead. Set to ``None`` to disable.
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

             ``df.attrs["complete"]`` is False when the read of some
             sample or peak failed, and ``df.attrs["failures"]`` names each
             one and its error ([] when none did). When every read
             failed the result is an empty frame carrying them.
    :rtype: pd.DataFrame | None
    :raises ValueError: If zero or more than one formula parameter is provided.
    :raises KeyboardInterrupt: If the user declines the confirmation prompt.
    :raises ConfirmationRequiredError: If the prompt has no interactive
        input to read.

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
    # Normalise to a list of values
    formula_values: list[str] = (
        formula_raw if isinstance(formula_raw, list) else [formula_raw]
    )

    # --- Discover samples across batches ---
    sample_tasks, _ = _collect_sample_tasks(
        client, dataset, batches, samples=samples, exact=exact
    )
    if not sample_tasks:
        logger.info("No samples found")
        return None

    if confirm_above is not None and len(sample_tasks) > confirm_above:
        _confirm_sample_count(len(sample_tasks), confirm_above)

    return load_sample_timeseries(
        client,
        sample_tasks,
        formula_level,
        formula_values,
        max_workers=max_workers,
    )


def load_sample_timeseries(
    client: MascopeClient,
    sample_tasks: list[tuple[Any, str]],
    formula_level: str,
    formula_values: list[str],
    *,
    max_workers: int = 8,
    on_result: Callable[[tuple[Any, ...], pd.DataFrame], None] | None = None,
) -> pd.DataFrame | None:
    """Load the peak time series of samples already chosen.

    The half of :func:`load_peak_timeseries` after the samples are resolved,
    for a caller that resolves them itself (the ``mascope_ai`` toolkit): it
    never prompts, and returns what :func:`load_peak_timeseries` returns,
    ``attrs["complete"]`` and ``attrs["failures"]`` included, without the
    provenance stamp.

    :param client: The MascopeClient instance.
    :param sample_tasks: ``(sample_row, batch_name)`` per sample, as for
        :func:`load_sample_peaks`.
    :param formula_level: ``"compound"``, ``"ion"`` or ``"isotope"``.
    :param formula_values: The formulas (or, for compounds, names) to load.
    :param on_result: Called with each peak's task - ``(sample_row,
        batch_name, peak_id, ...)`` - and its time series as they arrive (see
        :func:`run_concurrent`); whatever it raises stops the load and
        propagates. The discovery reads are not passed to it.
    :return: As :func:`load_peak_timeseries`.
    :raises ValueError: On an unknown *formula_level*.
    """
    if formula_level not in _FORMULA_COLUMNS:
        raise ValueError(
            f"formula_level must be one of {sorted(_FORMULA_COLUMNS)}, "
            f"not {formula_level!r}"
        )
    formula_column = _FORMULA_COLUMNS[formula_level]
    formula_set = set(formula_values)

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
    matched_lists, discovery_failures = run_concurrent(
        _get_matched_peaks,
        sample_tasks,
        max_workers=max_workers,
        desc="Finding peaks",
        unit="sample",
    )
    all_peak_tasks: list[
        tuple[Any, str, str, str | None, str | None, str | None, str | None]
    ] = [task for batch in matched_lists for task in batch]
    failed = _sample_failures(discovery_failures)

    if not all_peak_tasks:
        logger.info("No peaks matching {} in {}", formula_column, formula_values)
        return mark_complete(None, failed)

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

    frames, timeseries_failures = run_concurrent(
        _fetch_timeseries,
        all_peak_tasks,
        max_workers=max_workers,
        desc="Loading timeseries",
        unit="peak",
        on_result=on_result,
    )
    failed += _sample_failures(timeseries_failures, peak=True)

    if not frames:
        logger.info("No timeseries data loaded")
        return mark_complete(None, failed)

    frames = [f.dropna(axis=1, how="all") for f in frames]
    result = pd.concat(frames, ignore_index=True)
    logger.info("Loaded {} timeseries points total", len(result))
    return mark_complete(result, failed)


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
