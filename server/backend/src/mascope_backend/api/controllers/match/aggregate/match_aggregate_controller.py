import asyncio
import time

import pandas as pd
from sqlalchemy import (
    func,
    select,
)

from mascope_backend.api.controllers.match.collections.match_collections_controller import (
    create_match_collections,
)
from mascope_backend.api.controllers.match.compounds.match_compounds_controller import (
    create_match_compounds,
)
from mascope_backend.api.controllers.match.ions.match_ions_controller import (
    create_match_ions,
)
from mascope_backend.api.controllers.match.lib.match_aggregate import (
    MATCH_ISOTOPE_VALUE_COLUMNS,
    aggregate_match_collections,
    aggregate_match_compounds,
    aggregate_match_ions,
    aggregate_match_isotopes,
    aggregate_match_samples,
    reconstruct_full_isotope_frame,
)
from mascope_backend.api.controllers.match.lib.match_remove import remove_matches
from mascope_backend.api.controllers.match.samples.match_samples_controller import (
    create_match_samples,
    delete_match_samples,
)
from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.sample.lib.sample_items_fetch import (
    fetch_sample_item_ids,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import (
    api_controller,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    NotFoundException,
)
from mascope_backend.api.models.match.collections.match_collection_pydantic_model import (
    MatchCollectionBase,
)
from mascope_backend.api.models.match.compounds.match_compound_pydantic_model import (
    MatchCompoundBase,
)
from mascope_backend.api.models.match.ions.match_ion_pydantic_model import (
    MatchIonBase,
)
from mascope_backend.api.models.match.samples.match_sample_pydantic_model import (
    MatchSampleBase,
)
from mascope_backend.api.new.match.params import apply_match_params
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    MatchIsotope,
    Sample,
    SampleBatch,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_backend.socket.records.service import (
    emit_record_created,
    emit_record_reload,
)
from mascope_file.name import get_instrument_type
from mascope_match.params import BaseMatchParams


# Batch-scope aggregation runs in bounded sample chunks: the reconstructed
# isotope frame is O(samples x isotopes x collection memberships) with a
# dozen string columns, so a whole large batch at once does not fit in
# memory (a 2306-sample production batch OOM-killed its worker). Every
# aggregate level is per-sample, so chunking bounds peak memory without
# changing any result.
#
# The chunk size adapts to the batch's shape: per-sample frame weight varies
# by an order of magnitude between small and large target sets, so a fixed
# sample count either wastes chunk overhead on light batches or overshoots
# memory on heavy ones. Set BATCH_AGGREGATION_CHUNK_SIZE to force a fixed
# size (tests); None derives it from the row budget.
BATCH_AGGREGATION_CHUNK_SIZE: int | None = None
# Estimated (unpruned) target-chain rows per chunk. The estimator counts the
# full isotope x collection chain - an upper bound several-fold above the
# realized frame rows (resolution and abundance-threshold pruning happen at
# assembly) - so this budget is calibrated against that overestimate: on the
# production-incident shape it lands near the proven-safe ~200-sample /
# ~0.5M-realized-row operating point.
BATCH_AGGREGATION_CHUNK_ROW_BUDGET = 2_400_000
BATCH_AGGREGATION_MIN_CHUNK = 25
BATCH_AGGREGATION_MAX_CHUNK = 1000


async def _resolve_aggregation_chunk_size(session, sample_batch_id: str) -> int:
    """
    Derives the batch-aggregation chunk size from the batch's target shape.

    The reconstructed frame holds roughly one row per (isotope x collection
    membership) per sample, so the target-chain row count approximates the
    per-sample frame weight; the chunk size fits the row budget within the
    [min, max] clamp.

    :param session: Session for the count query.
    :param sample_batch_id: The batch whose target chain sizes the chunks.
    :return: Samples per aggregation chunk.
    :rtype: int
    """
    if BATCH_AGGREGATION_CHUNK_SIZE is not None:
        return BATCH_AGGREGATION_CHUNK_SIZE

    rows_per_sample = await session.scalar(
        select(func.count())
        .select_from(TargetCollectionInSampleBatch)
        .join(
            TargetCompoundInTargetCollection,
            TargetCompoundInTargetCollection.target_collection_id
            == TargetCollectionInSampleBatch.target_collection_id,
        )
        .join(
            TargetIon,
            TargetIon.target_compound_id
            == TargetCompoundInTargetCollection.target_compound_id,
        )
        .join(
            TargetIsotope,
            TargetIsotope.target_ion_id == TargetIon.target_ion_id,
        )
        .where(TargetCollectionInSampleBatch.sample_batch_id == sample_batch_id)
    )
    return max(
        BATCH_AGGREGATION_MIN_CHUNK,
        min(
            BATCH_AGGREGATION_MAX_CHUNK,
            BATCH_AGGREGATION_CHUNK_ROW_BUDGET // max(1, rows_per_sample or 0),
        ),
    )


@api_controller()
async def aggregate_match_isotope_filtered_data(
    sample_batch_id: str = None,
    sample_item_id: str = None,
    target_ion_id: str = None,
    match_params: BaseMatchParams = None,
    sample_item_ids: list[str] | None = None,
) -> pd.DataFrame:
    async with async_session() as session:
        # Step 1: Verify existence of the entities and fetch required data
        if sample_item_id is not None:
            sample = await fetch_sample(sample_item_id)
            sample_batch_id = sample.sample_batch_id

            # Get target isotope resolution for the sample_item
            instrument_type = get_instrument_type(sample.filename)
            isotope_resolution = "LOW" if instrument_type == "tof" else "HIGH"
        else:
            isotope_resolution = None

        if target_ion_id is not None:
            ion = await session.get(TargetIon, target_ion_id)
            if not ion:
                raise NotFoundException(
                    f"Target ion with ID '{target_ion_id}' not found"
                )

        sample_batch = await session.get(SampleBatch, sample_batch_id)
        if not sample_batch:
            raise NotFoundException(
                f"Sample batch with ID '{sample_batch_id}' not found"
            )

        # Step 3: Construct and execute structured queries
        # a) Query for fetching basic samples information
        sample_query = select(
            Sample.sample_item_id,
            Sample.filename,
            Sample.instrument,
            Sample.sample_item_name,
            Sample.sample_item_type,
            Sample.polarity,
            Sample.ionization_mode_id,
        ).where(Sample.sample_batch_id == sample_batch_id)

        # Apply sample_item_id filter if provided
        if sample_item_id is not None:
            sample_query = sample_query.where(Sample.sample_item_id == sample_item_id)

        # Scope to an explicit sample subset (chunked batch aggregation)
        if sample_item_ids is not None:
            sample_query = sample_query.where(
                Sample.sample_item_id.in_(sample_item_ids)
            )

        sample_result = await session.execute(sample_query)
        samples_df = pd.DataFrame([row._asdict() for row in sample_result.fetchall()])

        if samples_df.empty:
            message = (
                f"No samples found in the batch '{sample_batch.sample_batch_name}'"
            )
            runtime.logger.info(message)
            return samples_df

        # From here on, the in-scope samples (narrows any subset filter above)
        sample_item_ids = samples_df["sample_item_id"].tolist()

        # Get all ionization mechanism ids related to the samples in the batch
        # TODO: Currently this does not take into account that samples may have different
        # ionization modes, and thus different ionization mechanisms.
        sample_ionization_mode_ids = samples_df["ionization_mode_id"].unique().tolist()
        sample_ionization_mechanism_ids = []
        result = await session.execute(
            select(IonizationMode.ionization_mechanism_ids).where(
                IonizationMode.ionization_mode_id.in_(sample_ionization_mode_ids)
            )
        )
        ionization_mechanism_id_lists = result.scalars().all()
        sample_ionization_mechanism_ids = list(
            set(im_id for im_list in ionization_mechanism_id_lists for im_id in im_list)
        )

        # b) Query to get relevant Target data
        target_query = (
            select(
                TargetCollection.target_collection_id,
                TargetCollection.target_collection_name,
                TargetCollection.target_collection_description,
                TargetCollection.target_collection_type,
                TargetCompound.target_compound_id,
                TargetCompound.target_compound_formula,
                TargetCompound.target_compound_name,
                TargetIon.target_ion_id,
                TargetIon.target_ion_formula,
                TargetIon.filter_params,
                IonizationMechanism.ionization_mechanism,
                TargetIsotope.target_isotope_id,
                TargetIsotope.target_isotope_formula,
                TargetIsotope.mz,
                TargetIsotope.relative_abundance,
                TargetIsotope.resolution,
            )
            .select_from(TargetCollectionInSampleBatch)
            .where(TargetCollectionInSampleBatch.sample_batch_id == sample_batch_id)
            .join(
                TargetCollection,
                TargetCollection.target_collection_id
                == TargetCollectionInSampleBatch.target_collection_id,
            )
            .join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_collection_id
                == TargetCollection.target_collection_id,
            )
            .join(
                TargetCompound,
                TargetCompound.target_compound_id
                == TargetCompoundInTargetCollection.target_compound_id,
            )
            .join(
                TargetIon,
                TargetIon.target_compound_id == TargetCompound.target_compound_id,
            )
            .join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            .where(
                IonizationMechanism.ionization_mechanism_id.in_(
                    sample_ionization_mechanism_ids
                ),
            )
            .join(
                TargetIsotope,
                TargetIsotope.target_ion_id == TargetIon.target_ion_id,
            )
        )

        if isotope_resolution is not None:
            # Filter out the isotopes of incorrect resolution
            target_query = target_query.where(
                TargetIsotope.resolution == isotope_resolution
            )

        # Apply target_ion_id filter if provided
        if target_ion_id is not None:
            target_query = target_query.where(TargetIon.target_ion_id == target_ion_id)

        target_result = await session.execute(target_query)
        targets_df = pd.DataFrame([row._asdict() for row in target_result.fetchall()])
        if targets_df.empty:
            runtime.logger.info(
                f"No targets found in the batch '{sample_batch.sample_batch_name}'"
            )
            return targets_df

        # c. Fetch match isotopes for the samples in scope. Restricting to the
        # batch's target isotopes happens via the inner merge with targets_df
        # below, so no (potentially huge) target-isotope IN list is needed here.
        match_isotopes_query = (
            select(
                MatchIsotope.sample_item_id,
                MatchIsotope.target_isotope_id,
                MatchIsotope.match_mz_error,
                MatchIsotope.match_abundance_error,
                MatchIsotope.sample_peak_intensity,
                MatchIsotope.sample_peak_intensity_relative,
                MatchIsotope.sample_peak_mz,
                MatchIsotope.sample_peak_tof,
                MatchIsotope.match_score,
                MatchIsotope.signal_to_noise,
            )
            .select_from(MatchIsotope)
            .where(MatchIsotope.sample_item_id.in_(sample_item_ids))
        )

        match_isotopes_result = await session.execute(match_isotopes_query)
        match_isotopes_df = pd.DataFrame(match_isotopes_result.fetchall())
        if match_isotopes_df.empty:
            # No stored (matched) isotopes for these samples. Seed the expected
            # columns so unmatched reconstruction can still populate every expected
            # isotope (all unmatched) - matching the old behavior where fully
            # unmatched samples/ions produced score-0 rows and aggregates.
            match_isotopes_df = pd.DataFrame(
                columns=["sample_item_id", "target_isotope_id"]
                + MATCH_ISOTOPE_VALUE_COLUMNS
            )

        # Assemble and filter the frame in a worker thread: this is pure
        # CPU-bound pandas work that would otherwise block the event loop
        # (starving health checks and every other request on this worker)
        # for the duration of a large-scope aggregation.
        aggregated_match_isotope_filtered_data_df = await asyncio.to_thread(
            _assemble_filtered_isotope_frame,
            match_isotopes_df,
            samples_df,
            targets_df,
            match_params,
        )
        if aggregated_match_isotope_filtered_data_df.empty:
            runtime.logger.info(
                f"No match isotopes found for the sample batch '{sample_batch.sample_batch_name}'"
            )

        return aggregated_match_isotope_filtered_data_df


def _assemble_filtered_isotope_frame(
    match_isotopes_df: pd.DataFrame,
    samples_df: pd.DataFrame,
    targets_df: pd.DataFrame,
    match_params: BaseMatchParams | None,
) -> pd.DataFrame:
    """
    Builds the filtered isotope frame from the fetched inputs: reconstructs
    the unstored zero-score rows, merges in sample and target context, orders
    and sorts, and applies the match parameters. Pure pandas - safe to run in
    a worker thread.

    :param match_isotopes_df: Stored match isotope rows for the in-scope samples.
    :type match_isotopes_df: pd.DataFrame
    :param samples_df: In-scope samples with their metadata columns.
    :type samples_df: pd.DataFrame
    :param targets_df: The batch's target chain rows.
    :type targets_df: pd.DataFrame
    :param match_params: Optional match parameters for read-time filtering.
    :type match_params: BaseMatchParams | None
    :return: The filtered isotope frame; empty when nothing reconstructs.
    :rtype: pd.DataFrame
    """
    # Reconstruct the unmatched isotopes that are no longer stored, so the
    # isotope table and all higher-level aggregates match the pre-change
    # behavior (unmatched rows contribute 0 to every aggregate).
    match_isotopes_df = reconstruct_full_isotope_frame(
        match_isotopes_df, samples_df, targets_df
    )
    if match_isotopes_df.empty:
        return match_isotopes_df

    # Merge DataFrames
    combined_sample_match_isotopes_df = pd.merge(
        match_isotopes_df, samples_df, on="sample_item_id", how="inner"
    )
    aggregated_sample_match_isotope_data_df = pd.merge(
        combined_sample_match_isotopes_df,
        targets_df,
        on="target_isotope_id",
        how="inner",
    )

    # Define the desired column order
    column_order = [
        "sample_item_id",
        "filename",
        "instrument",
        "sample_item_name",
        "sample_item_type",
        "polarity",
        "target_collection_id",
        "target_collection_name",
        "target_collection_description",
        "target_collection_type",
        "target_compound_id",
        "target_compound_formula",
        "target_compound_name",
        "target_ion_id",
        "target_ion_formula",
        "filter_params",
        "ionization_mechanism",
        "target_isotope_id",
        "target_isotope_formula",
        "mz",
        "relative_abundance",
        "resolution",
        "match_mz_error",
        "match_abundance_error",
        "sample_peak_intensity",
        "sample_peak_intensity_relative",
        "sample_peak_mz",
        "sample_peak_tof",
        "match_score",
    ]

    # Reorder the columns according to the defined order and sort the DataFrame by 'mz'
    aggregated_sample_match_isotope_data_df = (
        aggregated_sample_match_isotope_data_df[column_order]
        .sort_values(by="mz", kind="mergesort")
        .reset_index(drop=True)
    )

    # Nullable label columns must never reach the aggregation groupbys as
    # NaN: pandas drops groups with a missing key (dropna default), which
    # would silently erase every row of e.g. a collection without a
    # description from ALL aggregate levels. The labels are display-only -
    # the persisted match rows carry ids and values, never these strings -
    # so normalizing to "" is lossless.
    label_columns = [
        "filename",
        "instrument",
        "sample_item_name",
        "sample_item_type",
        "target_collection_name",
        "target_collection_description",
        "target_collection_type",
        "target_compound_formula",
        "target_compound_name",
        "target_ion_formula",
        "ionization_mechanism",
        "target_isotope_formula",
    ]
    aggregated_sample_match_isotope_data_df[label_columns] = (
        aggregated_sample_match_isotope_data_df[label_columns].fillna("")
    )

    # Apply match_params (provided may be None) filtering match_score,
    # sample_peak_intensity, setting match_category
    return apply_match_params(aggregated_sample_match_isotope_data_df, match_params)


@api_controller()
async def aggregate_matches(
    sample_batch_id: str = None,
    sample_item_id: str = None,
    target_ion_id: str = None,
    match_params: BaseMatchParams = None,
    match_isotopes: bool = False,
    sample_item_ids: list[str] | None = None,
    as_frames: bool = False,
) -> dict:
    # Aggregate match isotopes filtered data
    aggregated_match_isotope_filtered_data_df = (
        await aggregate_match_isotope_filtered_data(
            sample_batch_id=sample_batch_id,
            sample_item_id=sample_item_id,
            target_ion_id=target_ion_id,
            match_params=match_params,
            sample_item_ids=sample_item_ids,
        )
    )
    if aggregated_match_isotope_filtered_data_df.empty:
        return {"results": 0, "data": {}}

    # Aggregate match isotopes if specified (used for backwards compatibility of get_sample_and_aggregated_matches, may be removed further)
    if match_isotopes:
        _, match_isotopes_df = await aggregate_match_isotopes(
            aggregated_match_isotope_filtered_data_df
        )

    # Aggregate match ions
    match_ions_data_df, match_ions_df = await aggregate_match_ions(
        aggregated_match_isotope_filtered_data_df, match_params
    )

    # Aggregate fields for match compounds
    (
        match_compounds_data_df,
        match_compounds_df,
    ) = await aggregate_match_compounds(match_ions_data_df)

    # Aggregate fields for match collections
    match_collections_df = await aggregate_match_collections(match_compounds_data_df)

    # Aggregate fields for matchSamples
    match_samples_df = await aggregate_match_samples(match_compounds_data_df)

    # Populate the results and data with aggregated data
    aggregated_match_data_dict = {"results": {}, "data": {}}
    aggregated_match_data_dict["results"] = {
        "match_ions": len(match_ions_df),
        "match_compounds": len(match_compounds_df),
        "match_collections": len(match_collections_df),
        "match_samples": len(match_samples_df),
    }

    # Internal fast path (aggregate_and_create_matches): hand the frames over
    # directly - the per-row dict payload and its category/score sort exist
    # for JSON consumers only, and skipping them avoids materializing hundreds
    # of thousands of dicts per large batch.
    if as_frames:
        aggregated_match_data_dict["data"] = {
            "match_ions": match_ions_df,
            "match_compounds": match_compounds_df,
            "match_collections": match_collections_df,
            "match_samples": match_samples_df,
        }
        if match_isotopes:
            aggregated_match_data_dict["data"]["match_isotopes"] = match_isotopes_df
        return {
            "results": aggregated_match_data_dict["results"],
            "data": aggregated_match_data_dict["data"],
        }
    aggregated_match_data_dict["data"] = {
        "match_ions": match_ions_df.sort_values(
            by=["match_category", "match_score"], ascending=[False, False]
        ).to_dict("records"),
        "match_compounds": match_compounds_df.sort_values(
            by=["match_category", "match_score"], ascending=[False, False]
        ).to_dict("records"),
        "match_collections": match_collections_df.sort_values(
            by=["match_category", "match_score"], ascending=[False, False]
        ).to_dict("records"),
        "match_samples": match_samples_df.sort_values(
            by=["match_category", "match_score"], ascending=[False, False]
        ).to_dict("records"),
    }

    # Conditionally add match_isotopes to the dictionary (used for backwards compatibility of get_sample_and_aggregated_matches, may be removed further)
    if match_isotopes:
        aggregated_match_data_dict["data"]["match_isotopes"] = (
            match_isotopes_df.sort_values(
                by=["match_category", "match_score"], ascending=[False, False]
            ).to_dict("records")
        )

    return {
        "results": aggregated_match_data_dict["results"],
        "data": aggregated_match_data_dict["data"],
    }


@api_controller()
async def aggregate_and_create_matches(
    sample_batch_id: str | None = None,
    sample_item_id: str | None = None,
    target_ion_id: str | None = None,
    match_params: BaseMatchParams | None = None,
    match_ions: bool = True,
    match_compounds: bool = True,
    match_collections: bool = True,
    match_samples: bool = True,
    notification: UserNotification | None = None,
    sample_item_ids: list[str] | None = None,
) -> dict:
    """
    Processes aggregated match data by first aggregating data based on given filters and then creating
    entries for each type of match data. After creating the entries, emit notification events for each
    affected sample item.

    Batch scope runs in bounded sample chunks (BATCH_AGGREGATION_CHUNK_SIZE):
    every aggregate level is per-sample, so chunking bounds peak memory
    without changing any persisted value. When the match_samples level is
    enabled, the scope's MatchSample rows are cleared up front and restored
    chunk by chunk - they are the aggregation-completeness signal for
    match_compute_batch's skip probe, and clearing them first makes the
    signal crash-safe (an OOM kill mid-aggregation leaves the probe
    reporting incomplete).

    Note: For notification events, all samples are assumed to belong to the same sample batch.

    Steps:
    1. Resolve the sample scope and split it into chunks (batch scope only).
    2. Clear the scope's MatchSample rows (when that level is enabled).
    3. Per chunk: aggregate match data and create entries for each enabled level.
    4. Report on the success or lack of data across all operations.

    :param sample_batch_id: ID of the sample batch.
    :param sample_item_id: ID of the sample item.
    :param target_ion_id: ID of the target ion.
    :param match_params: Additional match parameters.
    :param notification: Optional progress notification; batch scope reports
        per-chunk progress through it (type "match_aggregate_batch").
    :param sample_item_ids: Optional batch-scope narrowing to the affected
        samples only (every aggregate level is per-sample, so unaffected
        samples' stored aggregates stay valid untouched).
    :return: A dictionary with a message and log of actions taken.
    """
    # Whether this call aggregates a whole batch (rather than a single sample).
    # Batch aggregation creates all matches in one burst at the end, so it
    # notifies the frontend once for the batch instead of once per sample.
    batch_scope = sample_item_id is None and sample_batch_id is not None

    try:
        scope_sample_item_ids, sample_ref = await fetch_sample_item_ids(
            sample_item_id, sample_batch_id
        )
    except NotFoundException as e:
        return {
            "message": str(e),
        }

    # Narrow batch scope to the requested subset (intersected with the
    # batch's actual samples so stale ids cannot widen any delete)
    if batch_scope and sample_item_ids is not None:
        requested = set(sample_item_ids)
        scope_sample_item_ids = [
            item_id for item_id in scope_sample_item_ids if item_id in requested
        ]
        sample_ref += f" ({len(scope_sample_item_ids)} affected samples)"

    level_configs = [
        (create_match_ions, MatchIonBase, "match_ions", match_ions),
        (create_match_compounds, MatchCompoundBase, "match_compounds", match_compounds),
        (
            create_match_collections,
            MatchCollectionBase,
            "match_collections",
            match_collections,
        ),
        (create_match_samples, MatchSampleBase, "match_samples", match_samples),
    ]
    if not any(enabled for _, _, _, enabled in level_configs):
        message = f"No match data types selected for creation for {sample_ref}"
        runtime.logger.info(message)
        return {"message": message}

    # Batch scope aggregates in bounded sample chunks: every aggregate level
    # is per-sample, so chunking changes peak memory from O(batch) to
    # O(chunk) without changing any persisted value. Chunk size adapts to the
    # batch's target shape (see _resolve_aggregation_chunk_size).
    if batch_scope:
        async with async_session() as session:
            chunk_size = await _resolve_aggregation_chunk_size(session, sample_batch_id)
        sample_chunks = [
            scope_sample_item_ids[i : i + chunk_size]
            for i in range(0, len(scope_sample_item_ids), chunk_size)
        ]
    else:
        # Single pass; the sample_item_id parameter scopes the aggregation
        sample_chunks = [None]

    # Crash-safe completeness marker: MatchSample rows are the "aggregation
    # completed" signal for match_compute_batch's skip probe, so clear them
    # for the whole scope up front - the per-chunk creates below restore them
    # sample by sample. ANY death mid-aggregation (exception, OOM kill,
    # worker restart) then leaves the probe reporting incomplete instead of
    # trusting stale aggregates next to freshly stored isotopes.
    if match_samples:
        await delete_match_samples(sample_item_ids=scope_sample_item_ids)

    # Execute the aggregation and create operations chunk by chunk
    statuses = []
    written_by_level: dict[str, int] = {}
    affected_sample_item_ids = set()
    any_match_data = False
    runtime.logger.info(
        f"Creating match aggregates for {sample_ref}"
        + (f" in {len(sample_chunks)} chunk(s)" if len(sample_chunks) > 1 else "")
    )

    for chunk_index, chunk_sample_item_ids in enumerate(sample_chunks):
        chunk_started = time.perf_counter()
        aggregated_result = await aggregate_matches(
            sample_batch_id=sample_batch_id,
            sample_item_id=sample_item_id,
            target_ion_id=target_ion_id,
            match_params=match_params,
            sample_item_ids=chunk_sample_item_ids,
            as_frames=True,
        )
        aggregate_seconds = time.perf_counter() - chunk_started
        if aggregated_result.get("results", 0) != 0:
            any_match_data = True
            match_data = aggregated_result.get("data", {})

            for create_func, model_cls, level_name, enabled in level_configs:
                frame = match_data.get(level_name)
                if not enabled or frame is None or frame.empty:
                    continue
                # Project the frame to the level's model columns and hand the
                # records to the funnel directly: the values are this
                # aggregation's own output, so the pydantic validate +
                # re-serialize round trip would be pure overhead at hundreds
                # of thousands of rows per large batch.
                model_data = frame[list(model_cls.model_fields)].to_dict("records")
                result = await create_func(model_data)
                statuses.append(result.get("status"))
                written_by_level[level_name] = written_by_level.get(
                    level_name, 0
                ) + len(result.get("data", []))

                # Collect sample_item_ids from created records
                for record in result.get("data", []):
                    affected_sample_item_ids.add(record.get("sample_item_id"))

        runtime.logger.debug(
            f"Aggregation chunk {chunk_index + 1}/{len(sample_chunks)}: "
            f"aggregate {aggregate_seconds:.1f}s, create "
            f"{time.perf_counter() - chunk_started - aggregate_seconds:.1f}s"
        )

        # Report per-chunk progress so a minutes-long batch aggregation is
        # visible in the UI after the compute phase's bar completes
        if notification is not None and batch_scope:
            notification.data["_item_index"] = chunk_index
            notification.data["_total_samples"] = len(sample_chunks)
            await send_progress_user_notification(notification, 1.0)

    if not any_match_data:
        message = f"No match data found for {sample_ref}"
        return {
            "message": message,
        }

    messages = [
        f"{level_name}: {written} created or updated"
        for level_name, written in written_by_level.items()
    ]

    # After all creations, emit notification events so the frontend refreshes.
    if not sample_batch_id and affected_sample_item_ids:
        # fetch any sample to get the batch ID
        first_sample_item_id = next(iter(affected_sample_item_ids))
        sample = await fetch_sample(first_sample_item_id)
        sample_batch_id = sample.sample_batch_id

    if batch_scope:
        # Batch aggregation writes every sample's matches in one final burst, so
        # a per-sample match event storm would just tell the frontend to reload N
        # times. Emit a single "batch_match_created" event for the whole batch.
        if sample_batch_id and affected_sample_item_ids:
            await emit_record_created(
                record_type="batch_match",
                record_id=sample_batch_id,
                record={"sample_batch_id": sample_batch_id},
                room=sample_batch_id,
            )
    else:
        # Single-sample aggregation: notify per sample so an open sample view can
        # update incrementally.
        for affected_sample_item_id in affected_sample_item_ids:
            # Emit "sample_match_created" notification event
            await emit_record_created(
                record_type="sample_match",
                record_id=affected_sample_item_id,
                record={
                    "sample_item_id": affected_sample_item_id,
                    "sample_batch_id": sample_batch_id,
                },
                room=sample_batch_id,
            )

    # Peak views are per-sample regardless of scope: an open peak list must
    # refresh its match/formula annotations after a rematch. Each event is
    # scoped to its sample room, so this is not the batch-chart "storm" the
    # single "batch_match_created" event above avoids - only a client actually
    # viewing that sample's peaks is in the room.
    for affected_sample_item_id in affected_sample_item_ids:
        # "target_isotope_formula" (and match category) may have changed.
        await emit_record_reload(
            record_type="peak",
            room=affected_sample_item_id,
        )

    # Determine overall status
    statuses_set = set(statuses)
    if "partial" in statuses_set or len(statuses_set) > 1:
        status = "partial"
    elif statuses_set == {"success"}:
        status = "success"
    else:  # all skipped
        status = "skipped"

    message = (
        f"Aggregate and create matches completed ({status}) for {sample_ref}: "
        + "; ".join(messages)
    )
    runtime.logger.info(message)

    return {
        "status": status,
        "message": message,
        "data": {"affected_sample_item_ids": list(affected_sample_item_ids)},
    }


@api_controller()
async def aggregate_and_recreate_matches(
    sample_batch_id: str | None = None,
    sample_item_id: str | None = None,
    target_ion_id: str | None = None,
    match_params: BaseMatchParams | None = None,
    match_ions: bool = True,
    match_compounds: bool = True,
    match_collections: bool = True,
    match_samples: bool = True,
) -> dict:
    """
    Aggregates and re-creates match data by first removing existing aggregates based on flags,
    then aggregating and creating new entries for each type of match data.

    Used as rematching logic for match- agregated tables.

    Steps:
    1. Remove existing match data based on the provided parameters and flags.
    2. Aggregate and create match data based on the updated dataset.

    :param sample_batch_id: ID of the sample batch.
    :param sample_item_id: ID of the sample item.
    :param target_ion_id: ID of the target ion.
    :param match_params: Additional match parameters.
    :param match_ions: Controls the removal and recreation of ion match data.
    :param match_compounds: Controls the removal and recreation of compound match data.
    :param match_collections: Controls the removal and recreation of collection match data.
    :param match_samples: Controls the removal and recreation of sample match data.
    :return: A dictionary with a message and log of aggregate_and_create_matches actions.
    """
    # Step 1: Remove existing matches based on flags
    sample = None
    sample_batch = None
    if sample_item_id:
        sample = await fetch_sample(sample_item_id)
    if sample_batch_id:
        sample_batch = await fetch_sample_batch(sample_batch_id)
    await remove_matches(
        sample=sample,
        sample_batch=sample_batch,
        match_isotopes=False,
        match_ions=match_ions,
        match_compounds=match_compounds,
        match_collections=match_collections,
        match_samples=match_samples,
    )

    # Step 2: Aggregate and create new match data
    return await aggregate_and_create_matches(
        sample_batch_id=sample_batch_id,
        sample_item_id=sample_item_id,
        target_ion_id=target_ion_id,
        match_params=match_params,
        match_ions=match_ions,
        match_compounds=match_compounds,
        match_collections=match_collections,
        match_samples=match_samples,
    )
