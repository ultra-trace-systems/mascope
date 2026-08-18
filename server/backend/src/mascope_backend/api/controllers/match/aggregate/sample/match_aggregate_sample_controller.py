import pandas as pd
from sqlalchemy import (
    select,
)

from mascope_backend.api.controllers.match.aggregate.match_aggregate_controller import (
    aggregate_match_isotope_filtered_data,
    aggregate_matches,
)
from mascope_backend.api.controllers.match.lib.match_aggregate import (
    aggregate_match_compounds_light,
    aggregate_match_ions,
    aggregate_match_ions_light,
    compile_samples_df,
    set_ions_match_category,
    snr_columns_json_safe,
)
from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    fit_sample_mass_accuracy,
    ion_score_v2,
    match_score_version,
    sample_noise_floor,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.target.ions.target_ions_controller import (
    create_target_ions,
)
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.models.target.collections.config import (
    target_collection_config,
)
from mascope_backend.api.new.ionization.modes.util import (
    fetch_sample_ionization_mechanism_ids,
)
from mascope_backend.api.new.match.params import (
    apply_match_params,
    default_match_params,
)
from mascope_backend.db import (
    IonizationMechanism,
    TargetCompound,
    TargetIon,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_file.string import norm
from mascope_match import compute_match_isotopes
from mascope_match.params import BaseMatchParams


@api_controller()
async def aggregate_sample_match_ion(
    sample_item_id: str,
    target_ion_id: str,
    target_collection_id: str,
    match_params: BaseMatchParams,
) -> dict:
    """
    Aggregates ion-specific match information for a given sample item by fetching match data at the isotope level,
    applying the provided filter parameters, and returning aggregated match data for ions and isotopes.

    Key Points:
    - Ion-specific `match_params` are required; stored or default parameters are NOT used for filtering.
    - The function directly processes the aggregated match isotope data without unnecessary intermediate steps.
    - Response structure matches match records format with nested match data.

    Steps:
    1. Verify the existence of the specified sample item and target ion.
    2. Aggregate and filter the match data at the isotopic level using the provided filter parameters.
    3. If the DataFrame is empty, return a response indicating no matches were found.
    4. Filter the aggregated match isotopes data by `target_collection_id` to remove potential duplicates.
    5. Aggregate the data for match ions and filter it by `target_collection_id`.
    6. Prepare the final output, including the counts and details of matched ions and isotopes.

    :param sample_item_id: ID of the sample item for which to retrieve ion matches.
    :type sample_item_id: str
    :param target_ion_id: ID of the target ion for which matches are filtered.
    :type target_ion_id: str
    :param target_collection_id: ID of the target collection to filter out duplicates.
    :type target_collection_id: str
    :param match_params: Ion-specific filter parameters for match score and sample peak area filtering.
    :type match_params: BaseMatchParams
    :return: Dictionary containing aggregated match information for ions and isotopes.
    :rtype: dict
    """
    async with async_session() as session:
        # Fetch sample and target ion to verify its existence
        sample = await fetch_sample(sample_item_id)
        ion = await session.get(TargetIon, target_ion_id)
        if not ion:
            raise NotFoundException(f"Target ion with ID '{target_ion_id}' not found")

        # Aggregate and filter match data at the isotope level using the provided filter parameters
        aggregated_match_isotope_filtered_data_df = (
            await aggregate_match_isotope_filtered_data(
                sample_item_id=sample.sample_item_id,
                target_ion_id=target_ion_id,
                match_params=match_params,
            )
        )

        # Check if the DataFrame is empty
        if aggregated_match_isotope_filtered_data_df.empty:
            return {
                "matches": {
                    "match_ions": 0,
                    "match_isotopes": 0,
                },
                "match_ions": [],
                "match_isotopes": [],
            }

        # Filter match_isotopes_df duplicates (if compound is present in 2 different collections) by target_collection_id
        match_isotopes_df = aggregated_match_isotope_filtered_data_df[
            aggregated_match_isotope_filtered_data_df["target_collection_id"]
            == target_collection_id
        ].copy()

        # Aggregate fields for match ions and filter duplicates by target_collection_id
        match_ions_data_df, _ = await aggregate_match_ions(
            aggregated_match_isotope_filtered_data_df, match_params
        )
        match_ions_df = match_ions_data_df[
            match_ions_data_df["target_collection_id"] == target_collection_id
        ].copy()

        # Calculate alarming flag based on target_collection_type
        match_ions_df["alarming"] = match_ions_df["target_collection_type"].isin(
            target_collection_config.APP_ALARMING_COLLECTION_TYPES
        )
        match_isotopes_df["alarming"] = match_isotopes_df[
            "target_collection_type"
        ].isin(target_collection_config.APP_ALARMING_COLLECTION_TYPES)

        # Transform match_ions to nested structure
        match_ions_list = []
        for _, row in match_ions_df.sort_values(
            by=["match_category", "match_score"], ascending=[False, False]
        ).iterrows():
            ion_data = {
                "target_compound_id": row["target_compound_id"],
                "target_compound_name": row["target_compound_name"],
                "target_compound_formula": row["target_compound_formula"],
                "target_ion_id": row["target_ion_id"],
                "target_ion_formula": row["target_ion_formula"],
                "ionization_mechanism": row["ionization_mechanism"],
                "filter_params": row.get("filter_params", {}),
                "match": {
                    "sample_item_id": row["sample_item_id"],
                    "match_score": row["match_score"],
                    "match_category": row["match_category"],
                    "sample_peak_intensity_sum": row["sample_peak_intensity_sum"],
                    "alarming": row["alarming"],
                },
            }

            # Convert pandas NaN to None for JSON serialization
            for key, value in ion_data.items():
                if key != "match" and pd.isna(value):
                    ion_data[key] = None
            for key, value in ion_data["match"].items():
                if pd.isna(value):
                    ion_data["match"][key] = None

            match_ions_list.append(ion_data)

        # Transform match_isotopes to nested structure
        match_isotopes_list = []
        for _, row in match_isotopes_df.sort_values(by="mz", ascending=True).iterrows():
            isotope_data = {
                "target_compound_id": row["target_compound_id"],
                "target_ion_id": row["target_ion_id"],
                "target_isotope_id": row["target_isotope_id"],
                "target_isotope_formula": row["target_isotope_formula"],
                "mz": row["mz"],
                "relative_abundance": row["relative_abundance"],
                "resolution": row["resolution"],
                "match": {
                    "sample_item_id": row["sample_item_id"],
                    "sample_peak_id": None,  # Not available in aggregated data
                    "sample_peak_mz": row["sample_peak_mz"],
                    "sample_peak_intensity": row["sample_peak_intensity"],
                    "sample_peak_intensity_relative": row[
                        "sample_peak_intensity_relative"
                    ],
                    "sample_peak_tof": row["sample_peak_tof"],
                    "match_abundance_error": row["match_abundance_error"],
                    "match_mz_error": row["match_mz_error"],
                    "match_score": row["match_score"],
                    "match_category": row["match_category"],
                    "alarming": row["alarming"],
                },
            }

            # Convert pandas NaN to None for JSON serialization
            for key, value in isotope_data.items():
                if key != "match" and pd.isna(value):
                    isotope_data[key] = None
            for key, value in isotope_data["match"].items():
                if pd.isna(value):
                    isotope_data["match"][key] = None

            match_isotopes_list.append(isotope_data)

        # Prepare the final output
        if len(match_ions_df) and len(match_isotopes_df):
            message = "Match information retrieved successfully"
        else:
            message = "No matches found for the specified criteria"

        return {
            "message": message,
            "data": {
                "matches": {
                    "match_ions": len(match_ions_list),
                    "match_isotopes": len(match_isotopes_list),
                },
                "match_ions": match_ions_list,
                "match_isotopes": match_isotopes_list,
            },
        }


@api_controller()
async def aggregate_sample_match_compound(
    sample_item_id: str,
    target_compound_formula: str,
    match_params: BaseMatchParams | None = None,
    target_compound_name: str = "Unknown Compound",
) -> dict:
    """
    Retrieves matches for compounds within a sample based on a target compound formula,
    applying specified match parameters to filter the matches.

    Steps:
    1. Verify the existence of the sample and its batch, extract ion mechanisms.
    2. Prepare the target compound by normalizing its formula and creating a target compound instance.
    3. Generate and create target ions and isotopes for the compound.
    4. Compute matches for the created isotopes within the sample file.
    5. Apply filters to the computed isotope matches based on the provided parameters.
    6. Aggregate ion-level data from the filtered isotopes.
    7. Aggregate compound-level data from the ions and merge with target compound information.

    :param sample_item_id: Unique identifier of the sample item to analyze.
    :type sample_item_id: str
    :param target_compound_formula: Chemical formula of the target compound.
    :type target_compound_formula: str
    :param target_compound_name: The name of the target compound
    :type target_compound_name: str
    :param match_params: Parameters to filter the match results, affecting which matches are considered significant
    :type match_params: BaseMatchParams
    :raises NotFoundException: Raised if the sample item or sample batch cannot be found.
    :raises ValueError: Raised if no ion mechanisms are defined for the sample batch.
    :return: A dictionary containing aggregated match compounds, ions, and isotopes, each as a list of dictionaries.
    :rtype: dict
    """
    # match param defaults depend on instrument
    # so we use a helper to infer them:
    if not match_params:
        match_params = await default_match_params(sample_item_id)
    # data retrieval
    async with async_session() as session:
        # Step 1: Fetch sample related data and verify its existence
        sample = await fetch_sample(sample_item_id)

        ion_mechanisms_ids = await fetch_sample_ionization_mechanism_ids(sample_item_id)
        if not ion_mechanisms_ids:
            raise ValueError(
                f"There are no ion mechanisms for sample item '{sample.sample_item_name}'."
            )

        # Fetch the ionization mechanisms from the database using the extracted IDs
        result = await session.execute(
            select(IonizationMechanism).filter(
                IonizationMechanism.ionization_mechanism_id.in_(ion_mechanisms_ids)
            )
        )
        ionization_mechanisms = result.scalars().all()
        if not ionization_mechanisms:
            raise NotFoundException(
                f"Ionization mechanisms with IDs {ion_mechanisms_ids} not found"
            )

        # Step 2: Prepare target compound
        # Normalize the compound formula for consistency
        normalized_formula = norm(target_compound_formula)

        # Initialize the target compound with the normalized formula
        target_compound = TargetCompound(
            target_compound_id=gen_id(),
            target_compound_name=target_compound_name,
            target_compound_formula=normalized_formula,
        )

        # Step 3: Generate and create target ions and isotopes.
        # Create target ions for the compound
        ion_creation_result = await create_target_ions(
            target_compound=target_compound,
            ionization_mechanisms=ionization_mechanisms,
            independent_transaction=False,
            session=session,
        )

        # Convert 'created_ions' list into a DataFrame
        created_ions_df = pd.DataFrame(ion_creation_result["created_ions"])
        # Convert created isotopes to pandas DataFrame
        target_isotopes_df = pd.DataFrame(ion_creation_result["created_isotopes"])

        # Step 4: Compute matches for the isotopes in the sample file.
        match_isotope_df = await compute_match_isotopes(
            filename=sample.filename,
            target_isotopes_df=target_isotopes_df,
            match_params=match_params,
            polarity=sample.polarity,
        )

        # Drop the 'index' column from the match_isotope_df DataFrame if it exists
        match_isotope_df = match_isotope_df.drop(columns=["index"], errors="ignore")
        # Step 5: Apply filters to the computed isotope matches based on the provided parameters.
        filtered_match_isotope_df = apply_match_params(match_isotope_df, match_params)

        # Step 6: Aggregate ion-level data from the filtered isotopes.
        match_ions_data_df = aggregate_match_ions_light(filtered_match_isotope_df)
        match_ions_df = pd.merge(
            match_ions_data_df, created_ions_df, on="target_ion_id", how="left"
        )

        # Step 7: Aggregate compound-level data from the ions and merge with target compound information.
        match_compounds_data_df = aggregate_match_compounds_light(match_ions_df)

        # Convert the dictionary into a DataFrame
        target_compound_df = pd.DataFrame([target_compound.to_dict()])

        # Merge match_compounds_data_df with target_compound_df
        merged_match_compounds_df = pd.merge(
            match_compounds_data_df,
            target_compound_df,
            on="target_compound_id",
            how="left",
        )

        # Step 6: Prepare the final output
        if len(merged_match_compounds_df) > 0:
            message = f"Match information for compound '{target_compound_formula}' retrieved successfully"
        else:
            message = f"No matches found for the specified compound '{target_compound_formula}'"

        return {
            "message": message,
            "data": {
                "match_compounds": merged_match_compounds_df.to_dict("records"),
                "match_ions": match_ions_df.to_dict("records"),
                "match_isotopes": snr_columns_json_safe(
                    filtered_match_isotope_df
                ).to_dict("records"),
            },
        }


@api_controller()
async def aggregate_sample_match_compounds(
    sample_item_id: str,
    target_compound_formulas: list[str],
    match_params: BaseMatchParams | None = None,
    ion_mechanism_ids: list[str] | None = None,
) -> dict:
    """
    Retrieves matches for compounds within a sample based on a target compound formula,
    applying specified match parameters to filter the matches.

    Steps:
    1. Verify the existence of the sample and its batch, extract ion mechanisms.
    2. Prepare the target compound by normalizing its formula and creating a target compound instance.
    3. Generate and create target ions and isotopes for the compound.
    4. Compute matches for the created isotopes within the sample file.
    5. Apply filters to the computed isotope matches based on the provided parameters.
    6. Aggregate ion-level data from the filtered isotopes.
    7. Aggregate compound-level data from the ions and merge with target compound information.

    :param sample_item_id: Unique identifier of the sample item to analyze.
    :type sample_item_id: str
    :param target_compound_formulas: Chemical formulas of the target compounds.
    :type target_compound_formulas: list[str]
    :param match_params: Parameters to filter the match results, affecting which matches are considered significant
    :type match_params: BaseMatchParams
    :param ion_mechanism_ids: Ionization mechanisms IDs to use in matching
    :type ion_mechanism_ids: list[str]
    :raises NotFoundException: Raised if the sample item or sample batch cannot be found.
    :raises ValueError: Raised if no ion mechanisms are defined for the sample batch.
    :return: A dictionary containing aggregated match compounds, ions, and isotopes, each as a list of dictionaries.
    :rtype: dict
    """
    # match param defaults depend on instrument
    # so we use a helper to infer them:
    if not match_params:
        match_params = await default_match_params(sample_item_id)
    # data retrieval
    async with async_session() as session:
        # Step 1: Fetch sample related data and verify its existence
        sample = await fetch_sample(sample_item_id)

        if not ion_mechanism_ids:
            ion_mechanism_ids = await fetch_sample_ionization_mechanism_ids(
                sample_item_id
            )

        if not ion_mechanism_ids:
            raise ValueError(
                f"No ion mechanisms were provided, and there are no ion mechanisms for sample item '{sample.sample_item_name}'."
            )

        # Fetch the ionization mechanisms from the database using the extracted IDs
        result = await session.execute(
            select(IonizationMechanism).filter(
                IonizationMechanism.ionization_mechanism_id.in_(ion_mechanism_ids)
            )
        )
        ionization_mechanisms = result.scalars().all()
        if not ionization_mechanisms:
            raise NotFoundException(
                f"Ionization mechanisms with IDs {ion_mechanism_ids} not found"
            )

        target_compounds = []
        created_ions = []
        created_isotopes = []

        # Step 2: Prepare target compounds
        for target_compound_formula in target_compound_formulas:
            # Normalize the compound formula for consistency
            normalized_formula = norm(target_compound_formula)

            # Initialize the target compound with the normalized formula
            target_compound = TargetCompound(
                target_compound_id=gen_id(),
                target_compound_formula=normalized_formula,
            )
            target_compounds.append(target_compound.to_dict())

            # Step 3: Generate and create target ions and isotopes from the composition.
            (
                target_ions,
                target_isotopes,
            ) = generate_target_ions_from_composition(
                target_compound, ionization_mechanisms
            )
            for target_isotope in target_isotopes:
                # Add the isotopes to be committed to the db
                session.add(target_isotope)
            for target_ion in target_ions:
                # Add the ions to be committed to the db
                session.add(target_ion)

            created_ions += [ion.to_dict() for ion in target_ions]
            created_isotopes += [isotope.to_dict() for isotope in target_isotopes]

    # Convert the dictionaries into DataFrames
    target_compound_df = pd.DataFrame(target_compounds)
    created_ions_df = pd.DataFrame(created_ions)
    target_isotopes_df = pd.DataFrame(created_isotopes)

    results = []
    if len(target_isotopes_df) > 0:
        # Step 4: Compute matches for the isotopes in the sample file.
        match_isotope_df = await compute_match_isotopes(
            filename=sample.filename,
            target_isotopes_df=target_isotopes_df,
            match_params=match_params,
            polarity=sample.polarity,
        )

        # Drop the 'index' column from the match_isotope_df DataFrame if it exists
        match_isotope_df = match_isotope_df.drop(columns=["index"], errors="ignore")
        # Step 5: Apply filters to the computed isotope matches based on the provided parameters.
        filtered_match_isotope_df = apply_match_params(match_isotope_df, match_params)

        # Step 6: Aggregate ion-level data from the filtered isotopes.
        match_ions_df = (
            filtered_match_isotope_df.groupby("target_ion_id")
            .agg(
                match_score=(
                    "match_score",
                    lambda x: (
                        x * filtered_match_isotope_df.loc[x.index, "relative_abundance"]
                    ).sum(),
                ),
                sample_peak_intensity_sum=("sample_peak_intensity", "sum"),
            )
            .reset_index()
        )
        # Phase C experiment: optionally replace per-ion match_score with the
        # consolidated mascope_tools v2 score (additive + gated; v1 unchanged).
        if match_score_version() == 2:
            _mu, _sigma = fit_sample_mass_accuracy(filtered_match_isotope_df)
            _noise = sample_noise_floor(filtered_match_isotope_df)
            _v2 = (
                filtered_match_isotope_df.groupby(
                    "target_ion_id", sort=False, dropna=False
                )
                .apply(
                    lambda g: ion_score_v2(g, sigma_ppm=_sigma, mu=_mu, noise=_noise)
                )
                .rename("match_score_v2")
                .reset_index()
            )
            match_ions_df = match_ions_df.merge(_v2, on="target_ion_id", how="left")
            match_ions_df["match_score"] = match_ions_df["match_score_v2"].fillna(
                match_ions_df["match_score"]
            )
            match_ions_df = match_ions_df.drop(columns=["match_score_v2"])
        # set instrument and match category
        match_ions_df["instrument"] = sample.instrument
        match_ions_df = await set_ions_match_category(match_ions_df, match_params)
        # merge with created ions
        match_ions_df = pd.merge(
            match_ions_df, created_ions_df, on="target_ion_id", how="left"
        )

        # Step 7: Aggregate compound-level data from the ions and merge with target compound information.
        match_compounds_data_df = (
            match_ions_df.sort_values(
                by=["match_category", "match_score"], ascending=[False, False]
            )
            .groupby("target_compound_id")[match_ions_df.columns]
            .apply(
                lambda df: pd.Series(
                    {
                        "match_score": df.iloc[0]["match_score"],
                        "match_category": df.iloc[0]["match_category"],
                        "sample_peak_intensity_sum": df[
                            "sample_peak_intensity_sum"
                        ].sum(),
                    }
                ),
            )
            .reset_index()
        )

        # Explicitly cast match_category to int
        match_compounds_data_df["match_category"] = match_compounds_data_df[
            "match_category"
        ].astype(int)

        # Merge match_compounds_data_df with target_compound_df
        merged_match_compounds_df = pd.merge(
            match_compounds_data_df,
            target_compound_df,
            on="target_compound_id",
            how="left",
        )
        match_compounds = merged_match_compounds_df.to_dict("records")
        match_ions = match_ions_df.to_dict("records")
        match_isotopes = snr_columns_json_safe(filtered_match_isotope_df).to_dict(
            "records"
        )

        results = [
            {
                **compound,
                "children": [
                    {
                        **ion,
                        "children": [
                            {**isotope}
                            for isotope in match_isotopes
                            if isotope["target_ion_id"] == ion["target_ion_id"]
                        ],
                    }
                    for ion in match_ions
                    if ion["target_compound_id"] == compound["target_compound_id"]
                ],
            }
            for compound in match_compounds
        ]

    # Prepare the final output
    if len(results) > 0:
        message = f"{len(results)} matches found for {len(target_compound_formulas)} target compounds"
    else:
        message = "No matches found for the specified compounds"

    return {"message": message, "data": results}


@api_controller()
async def get_sample_and_aggregated_matches(
    sample_item_id: str,
) -> dict:
    """
    Retrieves detailed information for a specific sample, including aggregated match data for isotopes, ions,
    compounds, and collections. This function is an updated version of the deprecated `get_sample_aggregate_matches` (old get_sample)

    NOTE: This function is currently deprecated and may be removed in the future. It is being retained temporarily for testing purposes and is not used in the current frontend of mascope.

    Steps:
    1. Fetch the sample using the provided sample item ID to ensure it exists.
    2. Aggregate match data for the sample, including isotopes, ions, compounds, and collections, using the new aggregation controllers.
    3. If no match data is found, return a message indicating the absence of match data.
    4. Compile the sample data, merging it with the aggregated match data.
    5. Prepare the final output, including the sample data and aggregated match details, and return it in a structured dictionary format.

    :param sample_item_id: Unique identifier for the sample.
    :type sample_item_id: str
    :raises NotFoundException: If the sample with the specified item ID is not found.
    :return: A dictionary containing the sample information and aggregated match data.
    :rtype: dict
    """
    # Step 1: Fetch sample to verify its existence
    sample = await fetch_sample(sample_item_id)

    sample_dict = sample.to_dict()

    # Step 2: Aggregate the match data using the new aggregation controllers
    aggregated_result = await aggregate_matches(
        sample_item_id=sample_item_id, match_isotopes=True
    )

    if aggregated_result.get("results", 0) == 0:
        message = f"No match data found for sample '{sample.sample_item_name}'"
        return {
            "message": message,
        }

    # Step 3: Unpack the aggregated match data
    match_data = aggregated_result.get("data", {})
    match_isotopes = match_data.get("match_isotopes", [])
    match_ions = match_data.get("match_ions", [])
    match_compounds = match_data.get("match_compounds", [])
    match_collections = match_data.get("match_collections", [])
    match_samples = match_data.get("match_samples", [])

    # Step 4: Compile the sample data, merging it with the aggregated match data
    sample_df = pd.DataFrame([sample_dict])
    match_samples_df = pd.DataFrame(match_samples)
    sample_df = await compile_samples_df(sample_df, match_samples_df)

    # Step 5: Prepare the final output
    result = {}
    result["sample"] = sample_df.to_dict(orient="records")[0]

    # Add the matches field as a dictionary
    matches = {
        "matches": {
            "match_isotopes": len(match_isotopes),
            "match_ions": len(match_ions),
            "match_compounds": len(match_compounds),
            "match_collections": len(match_collections),
        }
    }

    result.update(matches)

    # Add the aggregated dataframes to the sample dictionary
    result["match_collections"] = match_collections
    result["match_compounds"] = match_compounds
    result["match_ions"] = match_ions
    result["match_isotopes"] = match_isotopes

    return result
