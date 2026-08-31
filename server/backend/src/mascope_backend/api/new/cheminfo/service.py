import numpy as np
import pandas as pd
from sqlalchemy import select

from mascope_backend.api.controllers.match.aggregate.sample.match_aggregate_sample_controller import (
    aggregate_sample_match_compounds,
)
from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    ion_score_v2,
    sample_noise_floor,
)
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_backend.api.new.cheminfo.utils import (
    to_custom_element_format,
    to_explicit_isotope_format,
)
from mascope_backend.api.new.match.params.lib import default_match_params
from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    peak_assignment_enabled,
)
from mascope_backend.api.new.peak_assignments.engine import (
    evidence_for,
    tier_for_evidence,
)
from mascope_backend.api.new.reference import service as reference_service
from mascope_backend.db import (
    IonizationMechanism,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_match.params import BaseMatchParams
from mascope_tools.composition import CompositionSearchConfig
from mascope_tools.composition.finder import find_compositions
from mascope_tools.composition.heuristic_filter import formula_plausibility
from mascope_tools.composition.utils import (
    normalize_formula_with_isotopes,
    parse_composition,
    parse_ionization,
    to_hill_order,
)


# Columns ion_score_v2 needs on a candidate's isotope frame.
_FIT_COLS = frozenset({"relative_abundance", "match_mz_error", "sample_peak_intensity"})

# The matcher's m/z tolerance is a hard accept/reject window around the sample's
# mass-error distribution; the fit score wants that distribution's Gaussian width.
# Reading the tolerance as a ~3 sigma window turns the instrument defaults (5 ppm
# Orbitrap, 15 ppm TOF) into ~1.7 / ~5 ppm, both inside the ranges score_pattern_v2
# documents for those instrument classes.
_TOLERANCE_SIGMA_DIVISOR = 3.0


def _instrument_sigma_ppm(match_params: BaseMatchParams | None) -> float | None:
    """The mass-accuracy sigma (ppm) the fit score should use for this sample.

    The fit score's mass term is ``exp(-0.5 * (ppm / sigma)^2)``, so sigma has to be
    the *instrument's* mass accuracy. Stage A can fit it from the data
    (`fit_sample_mass_accuracy` over every isotopologue the target library matched
    across the whole spectrum - hundreds of independent anchors), but this search
    cannot: its rows are the candidates for ONE m/z, and they are spread over
    +/- ``mz_precision`` of that peak by construction. A sigma fitted from them would
    measure the search window rather than the instrument - with a 30 ppm window the
    mass term goes near-uniform and every candidate scores alike on mass, which is the
    opposite of what rescoring the search is for.

    So it comes from the instrument instead. ``match_params`` is resolved per sample
    from the sample's instrument (or supplied by the caller), and its ``mz_tolerance``
    is the accept/reject window around the same error distribution the fit score
    models.

    :param match_params: Effective match parameters for the sample being searched.
    :return: Sigma in ppm, or None when no usable tolerance is available - which makes
        the caller report no fit score at all. That is deliberate: ``score_pattern_v2``
        falls back to an Orbitrap-only 2.0 ppm when sigma is None, which would collapse
        every candidate's score on a TOF sample, and a missing measurement is more
        honest than a systematically wrong one.
    """
    tolerance = getattr(match_params, "mz_tolerance", None)
    if tolerance is None:
        return None
    try:
        tolerance = float(tolerance)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(tolerance) or tolerance <= 0.0:
        return None
    return tolerance / _TOLERANCE_SIGMA_DIVISOR


def _annotate_assignment_scores(
    results: list[dict], match_params: BaseMatchParams | None
) -> None:
    """Attach fit_score (v2), plausibility, evidence and tier to each candidate.

    Harmonizes the on-demand composition search with the peak-centric assignment
    engine: the fit is computed exactly as Stage A scores an ion (ion_score_v2
    over the candidate's isotope envelope), the plausibility is the graded Seven
    Golden Rules score, evidence is their product, and the tier comes off that
    evidence under the same bands -- so the search reports the same measurements,
    in the same currency, as a committed assignment instead of the legacy match
    score. Mutates in place.

    Two inputs Stage A fits from the whole spectrum cannot be fitted here, because
    every row belongs to the same single m/z:

    - the mass-accuracy sigma comes from the instrument (`_instrument_sigma_ppm`);
      when it cannot be determined the annotation is skipped entirely, leaving the
      response in its unscored shape rather than reporting a misleading score;
    - no fitted offset (``mu``) is subtracted - the only estimate available would be
      the median of the same window-bound errors, i.e. the window's centre.

    The noise floor still comes from the matched intensities. It is only a fallback:
    ``compute_match_isotopes`` carries real per-peak ``signal_to_noise``, and
    ion_score_v2 prefers that whenever the column is populated.
    """
    if not results:
        return
    sigma = _instrument_sigma_ppm(match_params)
    if sigma is None:
        return
    all_isotopes = [iso for entry in results for iso in entry.get("children", [])]
    if not all_isotopes:
        return
    iso_df = pd.DataFrame(all_isotopes)
    if not _FIT_COLS.issubset(iso_df.columns):
        return

    noise = sample_noise_floor(iso_df)
    cfg = PeakAssignmentConfig()
    for entry in results:
        children = entry.get("children", [])
        fit = None
        if children:
            try:
                fit = ion_score_v2(pd.DataFrame(children), sigma_ppm=sigma, noise=noise)
            except Exception:  # scoring must never break the search response
                fit = None
        fit = float(fit) if fit is not None and np.isfinite(fit) else None
        entry["fit_score"] = round(fit, 4) if fit is not None else None
        formula = (entry.get("cheminfo") or {}).get("target_compound_formula")
        entry["plausibility"] = (
            round(float(formula_plausibility(formula)), 4) if formula else None
        )
        # Tiered on evidence, as a committed assignment is: the point of scoring the
        # search the way the engine scores a run is that a candidate's tier here
        # predicts the tier it would carry once assigned, and a chemically
        # implausible candidate must not read 'assigned' in the preview only to be
        # demoted the moment it is committed.
        entry["evidence"] = evidence_for(fit, formula)
        entry["tier"] = tier_for_evidence(
            entry["evidence"],
            candidate_threshold=cfg.candidate_threshold,
            assigned_threshold=cfg.assigned_threshold,
        )


@api_controller()
async def retrieve_compositions_by_mz(
    mz: float,
    ionization_mechanism_ids: list[str],
    mz_precision: float = cheminfo_config.DEFAULT_MZ_PRECISION,
    formula_ranges: str = cheminfo_config.DEFAULT_FORMULA_RANGE,
    known_only: bool = False,
) -> dict:
    """
    Find molecular compositions for a given m/z value using Mascope Tools.

    Steps:
    - Fetch ionization mechanisms from database
    - Convert custom element notation to explicit isotope notation for Mascope Tools,
      e.g. "^N" to "[15N]"
    - Run local composition search via Mascope Tools
    - Map results to the expected response format
      + Explicit isotope formats in formulas are reverted back to custom element format
        e.g. "[15N]" to "^N"
    - Annotate each result with known reference compounds sharing its formula
      (name, structure, source, license), when the reference mirror is in play -
      see `_annotate_with_reference` for when that is. Purely additive - the de
      novo scoring is untouched.

    NOTE: Conversion between custom element notation and explicit isotope notation is only a
    best guess. E.g. "[15N]" will always be converted to "^N" even if the user intended to refer to
    the explicit isotope itself.

    :param mz: The m/z value to search for
    :type mz: float
    :param mz_precision: The tolerance for m/z matching in ppm
    :type mz_precision: float
    :param formula_ranges: Formula ranges permitted in the results
    :type formula_ranges: str
    :param ionization_mechanism_ids: List of ionization mechanism IDs to query against
    :type ionization_mechanism_ids: None | list[str]
    :param known_only: When True, keep only results whose formula matches a known
        reference compound - the suspect-screening prior. Defaults to False.
    :type known_only: bool
    :return: Metadata and a result array of records containing the following fields:
        - target_compound_formula
        - target_compound_unsaturation
        - ionization_mechanism
        - target_isotope_mz
        - target_isotope_mz_error_ppm
        - known_compounds (list of matching reference-database identities; present
          only when the reference mirror is consulted, see `_annotate_with_reference`)
    :rtype: dict
    """
    # Fetch ionization mechanisms from database
    async with async_session() as session:
        result = await session.execute(
            select(IonizationMechanism).filter(
                IonizationMechanism.ionization_mechanism_id.in_(
                    ionization_mechanism_ids
                )
            )
        )
        all_ionization_mechanisms = result.scalars().all()
        ionization_mechanisms = [
            i.ionization_mechanism for i in all_ionization_mechanisms
        ]
        if len(ionization_mechanisms) == 0:
            raise ValueError(
                f"No ionization mechanisms found with the provided IDs: {ionization_mechanism_ids}"
            )

    # Convert custom element notation to explicit isotope notation
    # for both formula ranges and ionization mechanisms
    formula_ranges, _ = to_explicit_isotope_format(formula_ranges)
    explicit_ionizations = [
        to_explicit_isotope_format(i)[0] for i in ionization_mechanisms
    ]
    # Map explicit ionization notation back to DB mechanism objects
    explicit_to_db_mech = dict(zip(explicit_ionizations, all_ionization_mechanisms))

    # Build composition search config and run local composition finder
    config = CompositionSearchConfig(
        ionizations=",".join(explicit_ionizations),
        mass_range_ppm=mz_precision,
        element_count_ranges=formula_ranges,
        use_unsaturation=True,
        min_unsaturation=-1000.0,
        max_unsaturation=10000.0,
        max_result_rows=1000,
    )
    composition_results = find_compositions(mz, config)

    # Map Mascope Tools results to the expected response format
    results = []
    for raw in composition_results:
        try:
            # Convert explicit isotope notation back to custom element
            # notation and re-normalize to Hill order
            formula = to_custom_element_format(raw["formula"])
            formula = to_hill_order(
                parse_composition(normalize_formula_with_isotopes(formula))
            )

            # Find matching ionization mechanism from database.
            # Results use explicit isotope notation (e.g. +[15N]O3-)
            ion_mech_str = raw.get("ionization_mechanism", "")
            db_mech = explicit_to_db_mech.get(ion_mech_str)
            if not db_mech:
                # INFO per result row: fires inside the composition loop
                runtime.logger.info(
                    f"No matching DB ionization mechanism "
                    f"for '{ion_mech_str}', skipping result"
                )
                continue

            # Compute theoretical ion m/z from neutral mass
            # and ionization mechanism
            ion_mech = parse_ionization(ion_mech_str)
            neutral_mass = raw["neutral_mass"]
            theoretical_mz = neutral_mass + (
                ion_mech.mass if ion_mech.addition else -ion_mech.mass
            )

            results.append(
                {
                    "sample_peak_mz": mz,
                    "target_compound_formula": formula,
                    "target_compound_unsaturation": raw.get("unsaturation"),
                    "ionization_mechanism": db_mech.to_dict(),
                    "target_isotope_mz": theoretical_mz,
                    "target_isotope_mz_error_ppm": raw["composition_error_ppm"],
                }
            )
        except Exception:
            runtime.logger.exception(f"Error processing result {raw}")
            # Skip malformed results rather than failing
            continue

    results = await _annotate_with_reference(results, known_only=known_only)

    # Return formatted response
    total_results = len(results)
    return {
        "message": f"Retrieved {total_results} composition results for m/z query",
        "results": total_results,
        "total": total_results,
        "data": results,
    }


# A missing or unmigrated reference mirror is a deployment state, not a per-request
# event: warning about it on every search would turn one misconfiguration into log
# spam. Warn the first time and stay quiet afterwards.
_reference_lookup_warned = False


async def _annotate_with_reference(results: list[dict], known_only: bool) -> list[dict]:
    """Attach ``known_compounds`` to each result from the reference mirror.

    Only when the mirror is actually in play. The reference mirror is a subsystem of
    its own, and this composition search long predates it, so consulting it
    unconditionally would change the response shape (a new ``known_compounds`` key)
    and cost a second session plus an ``IN`` query for every existing SDK client that
    never asked for identities. Two things put it in play:

    - ``known_only`` - the suspect-screening prior, an explicit per-call opt-in that
      cannot be honoured without the lookup;
    - ``peak_assignment_enabled()`` - the deployment has opted into the peak-centric
      surfaces, which is where reference identities are read.

    With neither, the results are returned exactly as they were built, so a
    deployment that opted out sees the pre-feature response and a deployment with no
    reference data loaded pays nothing for it.

    Batches all result formulas into a single indexed lookup. On any failure the
    results are returned with an empty ``known_compounds`` so annotation can never
    break composition search - which means a ``known_only`` search against a broken
    or unmigrated mirror reports nothing rather than claiming unverified identities.

    :param results: Composition results, each carrying ``target_compound_formula``.
    :param known_only: Drop results with no known-compound match when True.
    :return: The results with ``known_compounds`` attached (and filtered if asked),
        or unchanged when the mirror is not consulted.
    """
    global _reference_lookup_warned

    if not results:
        return results
    if not (known_only or peak_assignment_enabled()):
        return results

    formulas = [r["target_compound_formula"] for r in results]
    try:
        annotations = await reference_service.annotate_formulas(formulas)
    except Exception as e:
        if not _reference_lookup_warned:
            _reference_lookup_warned = True
            runtime.logger.warning(
                f"Reference annotation skipped (further occurrences silent): {e}"
            )
        annotations = {}

    annotated = []
    for result in results:
        known = annotations.get(result["target_compound_formula"], [])
        if known_only and not known:
            continue
        annotated.append({**result, "known_compounds": known})
    return annotated


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def match_compositions_by_mz(
    sample_item_id: str,
    mz: float,
    mz_precision: float = 30,
    formula_ranges: str | None = "C0-100 H0-100 O0-100 N0-100",
    ionization_mechanism_ids: list[str] | None = None,
    match_params: BaseMatchParams | None = None,
    independent_transaction: bool = False,
    user_id: None | int = None,
    process_id: None | str = None,
    parent_id: None | str = None,
) -> dict:
    """
    Find molecular compositions for a given m/z value and compute potential
    match data for a specified sample.

    Steps:
    - Find compositions matching the m/z value using Mascope Tools
    - Match these compounds against a specified sample
    - Combine and format the results

    Match ion aggregation level is used to represent the match score of each formula result.
    Match isotopes are included in the result data for more detailed information.

    :param sample_item_id: Sample item ID to match against
    :type sample_item_id: str
    :param mz: The m/z value to search for
    :type mz: float
    :param mz_precision: The tolerance for m/z matching in ppm
    :type mz_precision: float
    :param formula_ranges: Formula ranges permitted in the results
    :type formula_ranges: None | str
    :param ionization_mechanism_ids: List of ionization mechanism IDs to query against
    :type ionization_mechanism_ids: None | list[str]
    :param match_params: Parameters for customizing the matching algorithm. Resolved
        from the sample's instrument when omitted, here rather than inside the
        aggregation call so the fit-score annotation sees the same parameters the
        match itself ran with.
    :type match_params: None | BaseMatchParams
    :param independent_transaction: Whether this is an independent transaction
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: None | str
    :param parent_id: Parent process ID for nested operations
    :type parent_id: None | str
    :return: Metadata and a result array of records containing the following fields:
        - target_compound_formula
        - target_compound_unsaturation
        - ionization_mechanism
        - target_isotope_mz
        - target_isotope_mz_error_ppm
    :rtype: dict
    """
    if match_params is None:
        match_params = await default_match_params(sample_item_id)

    # Get composition data
    runtime.logger.info(f"Starting composition search for m/z {mz}")
    cheminfo_result = await retrieve_compositions_by_mz(
        mz=mz,
        ionization_mechanism_ids=ionization_mechanism_ids,
        mz_precision=mz_precision,
        formula_ranges=formula_ranges,
    )

    cheminfo_data = cheminfo_result.get("data", [])
    cheminfo_results = cheminfo_result.get("results", 0)
    cheminfo_total = cheminfo_result.get("total", 0)

    # Return early if no composition data
    if not cheminfo_data:
        return {
            "message": "No matching compositions found for the specified m/z and parameters.",
            "results": 0,
            "total": 0,
            "data": [],
            "_notification_data": {
                "mz": mz,
                "sample_item_id": sample_item_id,
            },
        }

    # Get matches against the sample
    runtime.logger.info(
        f"Matching {cheminfo_results} compounds against sample {sample_item_id}"
    )

    # Compute matches for the composition results
    # Matches are computed for all ionization mechanisms for each returned formula
    # and later filtered to keep only the one matching the original composition result
    matches_result = await aggregate_sample_match_compounds(
        sample_item_id=sample_item_id,
        target_compound_formulas=[
            info["target_compound_formula"] for info in cheminfo_data
        ],
        ion_mechanism_ids=ionization_mechanism_ids,
        match_params=match_params,
    )

    matches = matches_result.get("data", [])

    # Combine results data with cheminfo data
    data = []
    for info in cheminfo_data:
        # Find match data for the current composition finder result
        match_index = next(
            (
                index
                for index, match_compound in enumerate(matches)
                if info["target_compound_formula"]
                == match_compound["target_compound_formula"]
            ),
            None,
        )
        if match_index is None:
            # INFO per result row: fires inside the composition loop
            runtime.logger.info(
                (
                    "Match data not found for composition result: ",
                    f"{info['target_compound_formula']}",
                )
            )
            runtime.logger.debug(f"Composition result: {info}")
            # Skip this composition result if no match found
            continue
        match_compound = matches[match_index]

        # Iterate over match ions to find the one that matches the ionization mechanism
        # of the current composition result
        for match_ion in match_compound.get("children", []):
            match_ion.update(
                {"target_compound_formula": info["target_compound_formula"]}
            )
            # Find isotopes matching the ionization mechanism
            info_ionization_mechanism = info.get("ionization_mechanism", {})
            if (
                match_ion["ionization_mechanism_id"]
                == info_ionization_mechanism["ionization_mechanism_id"]
            ):
                match_isotopes = match_ion.get("children", [])
                # Make sure the matched peak is the one in the original composition result
                # This is important because when computing matches, the closest peak
                # to target m/z is selected, which may not be the same as the one used in
                # the composition search.
                match_mzs = [iso["sample_peak_mz"] for iso in match_isotopes]
                if info["sample_peak_mz"] not in match_mzs:
                    runtime.logger.debug(
                        f"Requested peak m/z ({info['sample_peak_mz']}) is not in matched m/zs: {match_mzs}. "
                        + f"Skipping the result ({info['target_compound_formula']})",
                    )
                    # Skip this result as the match is for a wrong peak
                    continue

                # Create combined result entry
                matched_info = {
                    **match_ion,
                    "cheminfo": info,
                    "children": match_isotopes,
                }
                data.append(matched_info)
                break

    # Harmonize scoring with the peak-centric engine: fit (v2) + plausibility +
    # tier per candidate, so the search speaks the same language as assignments.
    # Only when the feature is on - otherwise this long-standing search keeps
    # reporting the legacy match score and category it always has, and the extra
    # fields stay absent so the UI falls back to the legacy columns.
    if peak_assignment_enabled():
        _annotate_assignment_scores(data, match_params)

    # Return formatted response with notification data
    result_data = {
        "results": len(data),
        "total": cheminfo_total,
        "data": data,
    }
    message = f"Matched {len(data)} potential compounds with m/z {mz:.4f}."
    runtime.logger.info(message)
    return {
        "message": message,
        **result_data,
        "_notification_data": {
            "mz": mz,
            "sample_item_id": sample_item_id,
            **result_data,
        },
    }
