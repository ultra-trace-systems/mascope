"""
Isotope-level match records service for target isotopes with match data.
"""

from sqlalchemy import and_, select

from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.samples.samples_controller import (
    get_samples,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.models.target.collections.config import (
    target_collection_config,
)
from mascope_backend.api.new.ionization.modes.util import (
    fetch_batch_ionization_mechanism_ids,
    fetch_sample_ionization_mechanism_ids,
)
from mascope_backend.api.new.match.params.lib import (
    instrument_default_match_params,
    isotope_abundance_threshold_expr,
)
from mascope_backend.db import (
    IonizationMechanism,
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
from mascope_match.params import (
    ORBI_DEFAULT_ISOTOPE_ABUNDANCE_THRESHOLD,
    TOF_DEFAULT_ISOTOPE_ABUNDANCE_THRESHOLD,
    BaseMatchParams,
    unmatched_isotope_params,
)


@api_controller()
async def get_match_isotope_records(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_collection_id: str | None = None,
    target_ion_id: str | None = None,
    match_params: BaseMatchParams | None = None,
    include_subthreshold: bool = False,
) -> dict:
    """
    Retrieves target isotopes with match isotope data for sample or batch.

    Handles entity validation, instrument type detection, and orchestrates the data retrieval process.
    For sample-level queries, returns target isotopes with actual match data and calculated match_category.
    For batch-level queries, returns target isotopes with placeholder match data.
    Applies instrument-specific resolution filtering (HIGH for Orbitrap, LOW for ToF).

    :param sample_item_id: Unique identifier of the sample item, defaults to None
    :type sample_item_id: str | None
    :param sample_batch_id: Unique identifier of the sample batch, defaults to None
    :type sample_batch_id: str | None
    :param target_collection_id: Optional filter by specific target collection, defaults to None
    :type target_collection_id: str | None
    :param target_ion_id: Optional filter by specific target ion, defaults to None
    :type target_ion_id: str | None
    :param match_params: Match parameters containing settings for filtering, default to None
    :type match_params: BaseMatchParams | None
    :param include_subthreshold: When True, also return isotopes below the abundance
        threshold (which carry no stored match data). Defaults to False so the table
        mirrors what is actually matched and stored.
    :type include_subthreshold: bool
    :return: Dictionary containing status, message, results count, and match isotope records data
    :rtype: dict
    """
    if sample_item_id:
        sample = await fetch_sample(sample_item_id)
        entity_name = sample.sample_item_name
        entity_type = "sample"

        data = await _get_sample_match_isotope_records(
            sample,
            target_collection_id,
            target_ion_id,
            match_params,
            include_subthreshold,
        )
    else:
        sample_batch = await fetch_sample_batch(sample_batch_id)
        entity_name = sample_batch.sample_batch_name
        entity_type = "batch"

        data = await _get_batch_match_isotope_records(
            sample_batch, target_collection_id, target_ion_id, include_subthreshold
        )

    if not data:
        return {
            "status": "success",
            "message": f"No match isotopes found for {entity_type} '{entity_name}'",
            "results": 0,
            "data": [],
        }

    return {
        "status": "success",
        "message": f"Successfully retrieved match isotope records for {entity_type} '{entity_name}'",
        "results": len(data),
        "data": data,
    }


async def _get_sample_match_isotope_records(
    sample: Sample,
    target_collection_id: str | None = None,
    target_ion_id: str | None = None,
    match_params: BaseMatchParams | None = None,
    include_subthreshold: bool = False,
) -> list[dict]:
    """
    Retrieves target isotopes with match isotope data for a sample.

    Applies instrument-specific resolution filtering and match_params logic.
    Returns all target isotopes with match data if it exists, otherwise None placeholders.

    :param sample: Sample item SQLAlchemy object
    :type sample: Sample
    :param target_collection_id: Optional target collection filter
    :type target_collection_id: str | None
    :param target_ion_id: Optional target ion filter
    :type target_ion_id: str | None
    :param match_params: Optional match parameters for filtering
    :type match_params: BaseMatchParams | None
    :return: List of isotope records with nested match data and calculated match_category
    :rtype: list[dict]
    """
    async with async_session() as session:
        # Get sample ionization mechanism IDs
        sample_ionization_mechanism_ids = await fetch_sample_ionization_mechanism_ids(
            sample.sample_item_id
        )

        # Determine instrument type and resolution for filtering
        isotope_resolution = "LOW" if sample.instrument_type == "tof" else "HIGH"

        query = (
            select(
                TargetIsotope,
                TargetIon,
                TargetCompound,
                IonizationMechanism,
                MatchIsotope,
                TargetCollection.target_collection_type.in_(
                    target_collection_config.APP_ALARMING_COLLECTION_TYPES
                ).label("alarming"),
            )
            .distinct(TargetIsotope.target_isotope_id)
            .select_from(TargetIsotope)
            .join(
                TargetIon,
                TargetIon.target_ion_id == TargetIsotope.target_ion_id,
            )
            .join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            .join(
                TargetCompound,
                TargetCompound.target_compound_id == TargetIon.target_compound_id,
            )
            .join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetCompound.target_compound_id,
            )
            .join(
                TargetCollection,
                TargetCollection.target_collection_id
                == TargetCompoundInTargetCollection.target_collection_id,
            )
            .join(
                TargetCollectionInSampleBatch,
                TargetCollectionInSampleBatch.target_collection_id
                == TargetCollection.target_collection_id,
            )
            .outerjoin(
                MatchIsotope,
                and_(
                    MatchIsotope.target_isotope_id == TargetIsotope.target_isotope_id,
                    MatchIsotope.sample_item_id == sample.sample_item_id,
                ),
            )
            .where(
                and_(
                    TargetCollectionInSampleBatch.sample_batch_id
                    == sample.sample_batch_id,
                    TargetIon.ionization_mechanism_id.in_(
                        sample_ionization_mechanism_ids
                    ),
                    TargetIsotope.resolution == isotope_resolution,
                )
            )
        )

        if not include_subthreshold:
            # Hide isotopes below the effective abundance threshold (ion-scoped
            # override in filter_params, else instrument default). These carry no
            # stored match data; the switch lets a future UI opt into showing them.
            default_threshold = (
                match_params or instrument_default_match_params(sample.instrument)
            ).isotope_abundance_threshold
            query = query.where(
                TargetIsotope.relative_abundance
                >= isotope_abundance_threshold_expr(
                    TargetIon.filter_params, sample.instrument, default_threshold
                )
            )

        if target_collection_id:
            query = query.where(
                TargetCollection.target_collection_id == target_collection_id
            )

        if target_ion_id:
            query = query.where(TargetIon.target_ion_id == target_ion_id)

        result = await session.execute(query)
        rows = result.all()

        # Process results and apply match_params logic
        data = []
        for row in rows:
            # Build base isotope data
            isotope_data = {
                "target_compound_id": row.TargetCompound.target_compound_id,
                "target_compound_name": row.TargetCompound.target_compound_name,
                "target_compound_formula": row.TargetCompound.target_compound_formula,
                "cas_number": row.TargetCompound.cas_number,
                "target_ion_id": row.TargetIon.target_ion_id,
                "target_ion_formula": row.TargetIon.target_ion_formula,
                "ionization_mechanism_id": row.TargetIon.ionization_mechanism_id,
                "ionization_mechanism": row.IonizationMechanism.ionization_mechanism,
                "ionization_mechanism_polarity": row.IonizationMechanism.ionization_mechanism_polarity,
                "filter_params": row.TargetIon.filter_params,
                "target_isotope_id": row.TargetIsotope.target_isotope_id,
                "target_isotope_formula": row.TargetIsotope.target_isotope_formula,
                "mz": row.TargetIsotope.mz,
                "relative_abundance": row.TargetIsotope.relative_abundance,
                "resolution": row.TargetIsotope.resolution,
                "instrument": sample.instrument,  # Add instrument for _apply_match_params logic
            }

            # Build match data with proper match_params application
            if row.MatchIsotope:
                match_data = {
                    "match_isotope_id": row.MatchIsotope.match_isotope_id,
                    "sample_item_id": row.MatchIsotope.sample_item_id,
                    "sample_peak_id": row.MatchIsotope.sample_peak_id,
                    "sample_peak_mz": row.MatchIsotope.sample_peak_mz,
                    "sample_peak_intensity": row.MatchIsotope.sample_peak_intensity,
                    "sample_peak_intensity_relative": row.MatchIsotope.sample_peak_intensity_relative,
                    "sample_peak_tof": row.MatchIsotope.sample_peak_tof,
                    "match_abundance_error": row.MatchIsotope.match_abundance_error,
                    "match_mz_error": row.MatchIsotope.match_mz_error,
                    "match_score": row.MatchIsotope.match_score,
                    "match_isotope_utc_created": row.MatchIsotope.match_isotope_utc_created,
                    "match_isotope_utc_modified": row.MatchIsotope.match_isotope_utc_modified,
                    "alarming": row.alarming,
                }

                # Apply match_params for filtering and to calculate match_category
                match_data = _apply_match_params(isotope_data, match_data, match_params)
            else:
                # No stored row: this isotope had no matched peak. Unmatched
                # isotopes are no longer persisted, so emit the same placeholder
                # values that a stored unmatched row + _apply_match_params used to
                # produce (score 0 -> "no match" category 0), reconstructed from
                # the target isotope. sample_peak_mz mirrors the target m/z, as
                # assign_defaults_to_unmatched does at compute time.
                match_data = {
                    "match_isotope_id": None,
                    "sample_item_id": sample.sample_item_id,
                    "sample_peak_id": unmatched_isotope_params.sample_peak_id,
                    "sample_peak_mz": isotope_data["mz"],
                    "sample_peak_intensity": unmatched_isotope_params.sample_peak_intensity,
                    "sample_peak_intensity_relative": unmatched_isotope_params.sample_peak_intensity_relative,
                    "sample_peak_tof": unmatched_isotope_params.sample_peak_tof,
                    "match_abundance_error": unmatched_isotope_params.match_abundance_error,
                    "match_mz_error": unmatched_isotope_params.match_mz_error,
                    "match_score": unmatched_isotope_params.match_score,
                    "match_isotope_utc_created": None,
                    "match_isotope_utc_modified": None,
                    "match_category": 0,
                    "alarming": row.alarming,
                }

            # Remove instrument from isotope_data before final response
            del isotope_data["instrument"]
            isotope_data["match"] = match_data
            data.append(isotope_data)

        return data


def _apply_match_params(
    isotope_data: dict, match_data: dict, match_params: BaseMatchParams | None = None
) -> dict:
    """
    Apply match_params filtering logic to calculate match_category and filter match_score.

    Follows the same logic as the original apply_match_params function but for individual records.
    TODO refactor to avoid code duplication with apply_match_params in match/params/lib.py

    :param isotope_data: Target isotope data including instrument info
    :param match_params: Optional; Pydantic model of filtering parameters.
    :type match_params: BaseMatchParams
    :param provided_match_params: Optional provided match parameters
    :return: Updated match_data with calculated match_category
    """
    # Determine the match parameters to use based on the priority:
    #     1. Provided match parameters
    #     2. Ion-specific match parameters for the sample instrument
    #     3. Default match parameters
    if match_params:
        params = match_params.model_dump()
    elif (
        isotope_data.get("filter_params")
        and isotope_data["instrument"] in isotope_data["filter_params"]
    ):
        params = isotope_data["filter_params"][isotope_data["instrument"]]
    else:
        params = instrument_default_match_params(
            isotope_data["instrument"]
        ).model_dump()

    # Check if all required fields are present and valid
    required_fields = [
        "match_mz_error",
        "match_abundance_error",
        "sample_peak_intensity",
    ]

    # Check for None/NaN values in required fields
    if any(match_data.get(field) is None for field in required_fields):
        match_data["match_category"] = None
        return match_data

    # Also check relative_abundance from isotope_data
    if isotope_data.get("relative_abundance") is None:
        match_data["match_category"] = None
        return match_data

    # Apply filtering logic
    passes_filters = all(
        [
            abs(match_data["match_mz_error"]) <= params["mz_tolerance"],
            abs(match_data["match_abundance_error"])
            <= params["isotope_ratio_tolerance"],
            match_data["sample_peak_intensity"] >= params["peak_min_intensity"],
        ]
    )

    # Filter match_score based on criteria
    if not passes_filters:
        match_data["match_score"] = 0

    # Filter sample_peak_intensity
    intensity_passes_filters = all(
        [
            abs(match_data["match_mz_error"]) <= params["mz_tolerance"],
            abs(match_data["match_abundance_error"])
            <= params["isotope_ratio_tolerance"],
        ]
    )

    if not intensity_passes_filters:
        match_data["sample_peak_intensity"] = 0

    # Calculate match_category based on match_score
    match_score = match_data["match_score"] or 0
    if match_score >= params["probable_match_threshold"]:
        match_data["match_category"] = 2  # Probable match
    elif match_score >= params["possible_match_threshold"]:
        match_data["match_category"] = 1  # Possible match
    else:
        match_data["match_category"] = 0  # No match

    return match_data


async def _get_batch_match_isotope_records(
    sample_batch: SampleBatch,
    target_collection_id: str | None = None,
    target_ion_id: str | None = None,
    include_subthreshold: bool = False,
) -> list[dict]:
    """
    Retrieves target isotopes with placeholder match data for a batch.

    Determines resolution based on all instrument types in the batch.
    Uses HIGH resolution if any Orbitrap instruments are present, otherwise LOW.

    :param sample_batch: Sample batch SQLAlchemy object
    :type sample_batch: SampleBatch
    :param target_collection_id: Optional target collection filter
    :type target_collection_id: str | None
    :param target_ion_id: Optional target ion filter
    :type target_ion_id: str | None
    :return: List of isotope records with placeholder match data
    :rtype: list[dict]
    """
    async with async_session() as session:
        # Get all batch ionization mechanism IDs (used for any sample in batch)
        batch_ionization_mechanism_ids = await fetch_batch_ionization_mechanism_ids(
            sample_batch.sample_batch_id
        )

        # Determine resolution based on instrument types in batch
        samples = await get_samples(sample_batch_id=sample_batch.sample_batch_id)
        instrument_types = {sample["instrument_type"] for sample in samples["data"]}
        isotope_resolution = "HIGH" if "orbi" in instrument_types else "LOW"

        query = (
            select(
                TargetIsotope,
                TargetIon,
                TargetCompound,
                IonizationMechanism,
                TargetCollection.target_collection_type.in_(
                    target_collection_config.APP_ALARMING_COLLECTION_TYPES
                ).label("alarming"),
            )
            .distinct(TargetIsotope.target_isotope_id)
            .select_from(TargetIsotope)
            .join(
                TargetIon,
                TargetIon.target_ion_id == TargetIsotope.target_ion_id,
            )
            .join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            .join(
                TargetCompound,
                TargetCompound.target_compound_id == TargetIon.target_compound_id,
            )
            .join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetCompound.target_compound_id,
            )
            .join(
                TargetCollection,
                TargetCollection.target_collection_id
                == TargetCompoundInTargetCollection.target_collection_id,
            )
            .join(
                TargetCollectionInSampleBatch,
                TargetCollectionInSampleBatch.target_collection_id
                == TargetCollection.target_collection_id,
            )
            .where(
                and_(
                    TargetCollectionInSampleBatch.sample_batch_id
                    == sample_batch.sample_batch_id,
                    TargetIon.ionization_mechanism_id.in_(
                        batch_ionization_mechanism_ids
                    ),
                    TargetIsotope.resolution == isotope_resolution,
                )
            )
        )

        if not include_subthreshold:
            # Batch overview spans potentially mixed instruments; apply the default
            # threshold for the chosen resolution (HIGH=Orbi, LOW=ToF). Per-ion
            # overrides are not resolved here as the view carries placeholder data.
            default_threshold = (
                ORBI_DEFAULT_ISOTOPE_ABUNDANCE_THRESHOLD
                if isotope_resolution == "HIGH"
                else TOF_DEFAULT_ISOTOPE_ABUNDANCE_THRESHOLD
            )
            query = query.where(TargetIsotope.relative_abundance >= default_threshold)

        if target_collection_id:
            query = query.where(
                TargetCollection.target_collection_id == target_collection_id
            )

        if target_ion_id:
            query = query.where(TargetIon.target_ion_id == target_ion_id)

        result = await session.execute(query)
        rows = result.all()

        data = []
        for row in rows:
            isotope_data = {
                "target_compound_id": row.TargetCompound.target_compound_id,
                "target_compound_name": row.TargetCompound.target_compound_name,
                "target_compound_formula": row.TargetCompound.target_compound_formula,
                "cas_number": row.TargetCompound.cas_number,
                "target_ion_id": row.TargetIon.target_ion_id,
                "target_ion_formula": row.TargetIon.target_ion_formula,
                "ionization_mechanism_id": row.TargetIon.ionization_mechanism_id,
                "ionization_mechanism": row.IonizationMechanism.ionization_mechanism,
                "ionization_mechanism_polarity": row.IonizationMechanism.ionization_mechanism_polarity,
                "filter_params": row.TargetIon.filter_params,
                "target_isotope_id": row.TargetIsotope.target_isotope_id,
                "mz": row.TargetIsotope.mz,
                "relative_abundance": row.TargetIsotope.relative_abundance,
                "resolution": row.TargetIsotope.resolution,
            }

            isotope_data["match"] = {
                "match_isotope_id": None,
                "sample_item_id": None,
                "sample_peak_id": None,
                "sample_peak_mz": None,
                "sample_peak_intensity": None,
                "sample_peak_intensity_relative": None,
                "sample_peak_tof": None,
                "match_abundance_error": None,
                "match_mz_error": None,
                "match_score": None,
                "match_isotope_utc_created": None,
                "match_isotope_utc_modified": None,
                "match_category": None,
                "alarming": None,
            }

            data.append(isotope_data)

        return data
