import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import xarray as xr
from colorcet import glasbey_hv as colormap
from sqlalchemy import and_, select

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.db import MatchIsotope, Sample, TargetIsotope, async_session
from mascope_backend.socket import sio
from mascope_file.name import get_instrument_type
from mascope_match.params import unmatched_isotope_params
from mascope_signal.peak import filter_peaks, get_peaks


# TODO_configuration shift traces color
COLOR_OFFSET = 5
DMZ_TOF = 0.5
DMZ_ORBI = 0.01
UNITS = "counts/s"


@api_controller_background_task(
    error_notification_rooms=["user_id"],
)
async def visualize_ion_focus(
    sample_item_id: str,
    target_ion_id: str,
    peak_min_intensity: float,
    mz_tolerance: float,
    isotope_ratio_tolerance: float,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str = None,
    sid: str | None = None,
):
    """
    Visualizes the focus on a specific ion for a given sample item by computing and emitting sum spectrum and time series data.

    The function performs the following steps:
    - Fetches sample filename and target ion data from the database.
    - Loads the sample file and prepares a data slice for analysis.
    - Iterates over each target isotope, computing and emitting visualization traces for the sum spectrum and time series.
    - Constructs and emits the sum timeseries trace if applicable.

    :param sample_item_id: ID of the sample item to visualize.
    :type sample_item_id: str
    :param target_ion_id: ID of the target ion to focus on.
    :type target_ion_id: str
    :param peak_min_intensity: Minimum peak intensity threshold for considering a match.
    :type peak_min_intensity: float
    :param mz_tolerance: Tolerance for mass-to-charge ratio (m/z) error, in parts per million (ppm).
    :type mz_tolerance: float
    :param isotope_ratio_tolerance: The maximum allowed deviation in expected isotope ratios, (0.1 means 10%).
    :type isotope_ratio_tolerance: float
    :param independent_transaction: Indicates if the visualization should be considered an independent transaction, which affects sio event emission.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional,
    :param process_id: Process ID for tracking the visualization task.
    :type process_id: str, optional
    :param sid: Socket ID of the requesting client. Passed back to the client
    :type sid: str | None, optional
    :raises NotFoundException: If the sample item or target ion does not exist or does not meet the specified criteria.
    """
    # --- Fetch sample and match isotope data --- #
    sample_task = fetch_sample(sample_item_id)
    isotope_task = _fetch_isotopes(sample_item_id, target_ion_id)
    sample, isotopes = await asyncio.gather(sample_task, isotope_task)

    await emit_isotope_visualization(
        sample,
        isotopes,
        peak_min_intensity=peak_min_intensity,
        mz_tolerance=mz_tolerance,
        isotope_ratio_tolerance=isotope_ratio_tolerance,
        user_id=user_id,
        sid=sid,
    )


async def emit_isotope_visualization(
    sample: Sample,
    isotopes: list,
    *,
    peak_min_intensity: float,
    mz_tolerance: float,
    isotope_ratio_tolerance: float,
    user_id: int | None,
    sid: str | None,
) -> None:
    """Compute and emit the sum-spectrum and time-series traces for a set of
    isotopes against a sample.

    The isotope source is deliberately abstracted: `visualize_ion_focus` passes
    the persisted target ion's matched isotopes, while the peak-centric engine
    passes isotopes computed on the fly from an assigned composition (so an
    untargeted assignment - which has no target_ion_id - can still be verified in
    the Fit view). Each isotope must carry the attributes `_process_isotope`
    reads: mz, relative_abundance, target_isotope_id, sample_peak_mz,
    sample_peak_intensity, match_score, match_mz_error, match_abundance_error.
    """
    # -- Extract data for IsotopeContext --- #
    (
        peak_timeseries,
        mean_peak_heights,
        averaged_signal,
    ) = await _load_peaks_and_averaged_signal(sample, isotopes)
    isotope_relative_abundances = [iso.relative_abundance for iso in isotopes]

    # --- Process each isotope and generate visualization traces --- #
    match_counter = 0
    sum_timeseries = None
    main_isotope_height = 0
    all_spectrum_traces = []
    all_timeseries_traces = []
    for i, iso in enumerate(isotopes):
        ctx = IsotopeContext(
            index=i,
            main_isotope_height=main_isotope_height,
            relative_abundances=isotope_relative_abundances,
            averaged_signal=averaged_signal,
            peak_timeseries=peak_timeseries,
            mean_peak_heights=mean_peak_heights,
            peak_min_intensity=peak_min_intensity,
            mz_tolerance=mz_tolerance,
            isotope_ratio_tolerance=isotope_ratio_tolerance,
            dmz=(
                DMZ_ORBI if get_instrument_type(sample.filename) == "orbi" else DMZ_TOF
            ),
        )
        isotope_result = _process_isotope(iso, ctx, sum_timeseries)
        match_counter += isotope_result.match_count
        sum_timeseries = isotope_result.sum_timeseries
        main_isotope_height = isotope_result.main_isotope_height

        all_spectrum_traces.extend(isotope_result.spectrum_traces)
        all_timeseries_traces.extend(isotope_result.timeseries_traces)

    if match_counter > 1 and sum_timeseries is not None:
        sum_timeseries_time = sum_timeseries.time.values.astype(np.float32)
        sum_timeseries_y = sum_timeseries.values.astype(np.float32)
        all_timeseries_traces.append(
            {
                "name": "sum",
                "type": "scatter",
                "fill": "tozeroy",
                "fillcolor": "rgba(136, 136, 136, .3)",
                "line": {"color": "rgb(136, 136, 136)"},
                "x": sum_timeseries_time.tobytes(),
                "y": sum_timeseries_y.tobytes(),
                "unit": UNITS,
            },
        )

    # Socket ID is included in the payload, so the client can filter messages
    # in case the same user has multiple sessions open
    await sio.emit(
        "visualization_signal_sum_spectrum",
        {"sid": sid, "data": all_spectrum_traces},
        room=f"user-{user_id}",
    )
    await sio.emit(
        "visualization_signal_timeseries",
        {"sid": sid, "data": all_timeseries_traces},
        room=f"user-{user_id}",
    )


# --- Helpers --- #


@dataclass
class IsotopeContext:
    """Keeps shared data among all isotopes"""

    index: int
    main_isotope_height: float
    relative_abundances: list
    averaged_signal: xr.DataArray
    peak_timeseries: xr.DataArray
    mean_peak_heights: xr.DataArray
    peak_min_intensity: float
    mz_tolerance: float
    isotope_ratio_tolerance: float
    dmz: float
    color_offset: int = COLOR_OFFSET


@dataclass
class IsotopeResult:
    spectrum_traces: list = field(default_factory=list)
    timeseries_traces: list = field(default_factory=list)
    match_count: int = 0
    sum_timeseries: xr.DataArray | None = None
    main_isotope_height: float = 0


async def _load_peaks_and_averaged_signal(
    sample: Sample,
    isotopes: list[SimpleNamespace],
) -> tuple[xr.DataArray, xr.DataArray, xr.DataArray]:
    """
    Loads peak data and averaged signal for the specified sample and match isotopes.

    :param sample: The sample object containing filename and time range.
    :type sample: Sample
    :param isotopes: List of match isotope data.
    :type isotopes: list[SimpleNamespace]
    :return: Tuple of (peak_timeseries, mean_peak_heights, averaged_signal) for the specified m/z window.
    :rtype: tuple[xr.DataArray, xr.DataArray, xr.DataArray]
    """

    instrument_type = get_instrument_type(sample.filename)
    match instrument_type:
        case "tof":
            peak_data_type = "area"
            dmz = DMZ_TOF
        case "orbi":
            peak_data_type = "height"
            dmz = DMZ_ORBI
        case _:
            raise ValueError(f"Unknown instrument type: {instrument_type}")

    # --- Get averaged signal around target isotopes (+-dmz) --- #
    match_mzs = [
        iso.sample_peak_mz for iso in isotopes if iso.sample_peak_mz is not None
    ]
    mz_min, mz_max = min(match_mzs) - dmz, max(match_mzs) + dmz
    # Reconstructed for display so the profile overlays the centroids.
    averaged_signal = (
        m_compute.get_sum_signal(
            sample.filename,
            sample.t0,
            sample.t1,
            polarity=sample.polarity,
            average=True,
            reconstruct=True,
        )
        .sel(mz=slice(mz_min, mz_max))
        .compute()
    )

    # --- Load peak timeseries for peaks in target isotopes range --- #
    mzs_to_load = (
        m_io.load_peak_data(sample.filename).sel(mz=slice(mz_min, mz_max)).mz.values
    )
    peak_timeseries = await m_compute.load_peak_timeseries(sample.filename, mzs_to_load)

    # --- Get peak heights to plot isotope expected heights ---
    scan_timestamps = m_compute.get_scan_timestamps(
        sample.filename, t_min=sample.t0, t_max=sample.t1, polarity=sample.polarity
    )
    mean_peak_heights = (
        peak_timeseries.peak_heights.sel(time=scan_timestamps, method="nearest")
        .mean(dim="time")
        .compute()
    )

    # --- Prepare peak timeseries with instrument-specific data type --- #
    # Leave instrument-specific peak data type in the timeseries
    peak_timeseries = get_peaks(peak_timeseries, peak_data_type).compute()

    # Set Nan for times not in timestamps to ensure gaps in timeseries
    # in case of several polarities within one sample file
    time_scan = peak_timeseries.time.values.astype(np.float32)
    signal_gap_mask = ~np.isin(time_scan, scan_timestamps.astype(np.float32))
    time_axis = peak_timeseries.dims.index("time")
    if time_axis == 0:
        peak_timeseries.values[signal_gap_mask, ...] = np.nan
    else:
        peak_timeseries.values[..., signal_gap_mask] = np.nan

    # Attach sample file metadata to peak_data
    props = m_io.read_props(sample.filename)
    peak_timeseries.attrs.update({"props": props})

    return peak_timeseries, mean_peak_heights, averaged_signal


async def _fetch_isotopes(sample_item_id, target_ion_id):
    """
    Fetches match and target isotopes for a given target ion ID and sample item ID

    :param sample_item_id: ID of the sample item to fetch matched isotopes for.
    :type sample_item_id: str
    :param target_ion_id: ID of the target ion to fetch matched isotopes for.
    :type target_ion_id: str
    :raises NotFoundException: If no isotopes are found matching the criteria.
    :return: List of isotopes merged from TargetIsotope, MatchIsotope matching the criteria.
    :rtype: list
    """
    async with async_session() as session:
        # LEFT OUTER join so every target isotope of the ion is visualized, not
        # just those with a stored match_isotope row. Non-matching isotopes are no
        # longer persisted (only match_score > 0 is stored), so an inner join would
        # silently drop them and their spectrum could not be plotted on click.
        stmt = (
            select(TargetIsotope, MatchIsotope)
            .select_from(TargetIsotope)
            .outerjoin(
                MatchIsotope,
                and_(
                    MatchIsotope.target_isotope_id == TargetIsotope.target_isotope_id,
                    MatchIsotope.sample_item_id == sample_item_id,
                ),
            )
            .where(TargetIsotope.target_ion_id == target_ion_id)
            .order_by(TargetIsotope.relative_abundance.desc())
        )
        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        raise NotFoundException("No isotopes found for the given target ion ID.")

    def _model_to_dict(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    # Merge TargetIsotope and MatchIsotope data into a single list of isotopes;
    # use SimpleNamespace for easy attribute access with dot notation.
    isotopes = []
    for target, match in rows:
        data = _model_to_dict(target)
        if match is not None:
            data.update(_model_to_dict(match))
        else:
            # Reconstruct the unmatched placeholder (no stored row): the spectrum
            # is drawn around the expected m/z, mirroring what a stored unmatched
            # row used to provide (assign_defaults_to_unmatched at compute time).
            data.update(unmatched_isotope_params.model_dump())
            data["sample_peak_mz"] = target.mz
        isotopes.append(SimpleNamespace(**data))

    return isotopes


def _process_isotope(
    iso: SimpleNamespace, ctx: IsotopeContext, sum_timeseries: xr.DataArray | None
) -> IsotopeResult:
    """
    Processes a single isotope for visualization, generating spectrum and timeseries traces,
    and updating the sum timeseries if peaks match the target isotope.

    :param iso: The isotope data.
    :type iso: SimpleNamespace
    :param ctx: The context containing isotope and processing parameters.
    :type ctx: IsotopeContext
    :param sum_timeseries: The current sum timeseries to update, or None.
    :type sum_timeseries: xr.DataArray | None
    :return: Result object containing traces, match count, updated sum_timeseries, and main isotope height.
    :rtype: IsotopeResult
    """
    isotope_result = IsotopeResult(
        main_isotope_height=ctx.main_isotope_height, sum_timeseries=sum_timeseries
    )
    mz_min, mz_max = iso.mz - ctx.dmz, iso.mz + ctx.dmz
    isotope_averaged_spec = ctx.averaged_signal.sel(mz=slice(mz_min, mz_max))
    if ctx.mean_peak_heights.mz.size > 0:
        isotope_peak_heights = ctx.mean_peak_heights.sel(mz=slice(mz_min, mz_max))
    else:
        isotope_peak_heights = None

    if isotope_averaged_spec.size == 0:
        # No spectrum in the given mz range
        averaged_spec_mz = np.array([mz_min, mz_max], dtype=np.float32)
        averaged_spec_y = np.array([0, 0], dtype=np.float32)
        isotope_expected_height = 0
    else:
        averaged_spec_mz = isotope_averaged_spec.mz.values.astype(np.float32)
        averaged_spec_y = isotope_averaged_spec.values.astype(np.float32)
        if ctx.index == 0:
            # Main isotope: determine main isotope height
            try:
                filtered_isotope_peak_heights = filter_peaks(
                    isotope_peak_heights, intensity=ctx.peak_min_intensity
                )
                peak = filtered_isotope_peak_heights.sel(mz=iso.mz, method="nearest")
                isotope_result.main_isotope_height = peak.item()
            except KeyError:
                # Fall-back if no peak is found
                isotope_result.main_isotope_height = np.max(averaged_spec_y)

        # Calculate expected height based on relative abundance
        isotope_expected_height = isotope_result.main_isotope_height * (
            iso.relative_abundance / ctx.relative_abundances[0]
        )

    # Add spectrum trace for the isotope
    iso_color = colormap[ctx.index + ctx.color_offset]
    isotope_result.spectrum_traces.append(
        {
            "name": "{:d}".format(round(iso.mz)),
            "target_isotope_id": iso.target_isotope_id,
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "rgb({},{},{})".format(*iso_color)},
            "fill": "tozeroy",
            "fillcolor": "rgba({},{},{}, .3)".format(*iso_color),
            "x": averaged_spec_mz.tobytes(),
            "y": averaged_spec_y.tobytes(),
            "unit": UNITS,
        }
    )

    if isotope_peak_heights is None:
        return isotope_result
    # Convert to float32 for more efficient serialization to the frontend
    peak_x = isotope_peak_heights.mz.values.astype(np.float32)
    peak_y = isotope_peak_heights.values.astype(np.float32)
    # Multiplicate each peak to create vertical lines in the plot
    peak_x = np.repeat(peak_x, 4)
    peak_y = np.column_stack(
        [
            np.zeros_like(peak_y),
            peak_y,
            np.zeros_like(peak_y),
            np.full_like(peak_y, np.nan),
        ]
    ).flatten()
    # Add all peaks trace for the isotope
    isotope_result.spectrum_traces.append(
        {
            "name": "{:.6f}".format(iso.sample_peak_mz),
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "grey"},
            "x": peak_x.tobytes(),
            "y": peak_y.tobytes(),
            "unit": UNITS,
        }
    )

    # Get peak height. Can't use iso.sample_peak_intensity since peak area is used for TOFs
    peak_height = ctx.mean_peak_heights.sel(
        mz=iso.sample_peak_mz, method="nearest"
    ).item()

    # Derive match criteria
    match_score_ok = iso.match_score > 0.0
    intensity_ok = peak_height >= ctx.peak_min_intensity
    mz_ok = abs(iso.match_mz_error) <= ctx.mz_tolerance
    abundance_ratio_ok = abs(iso.match_abundance_error) <= ctx.isotope_ratio_tolerance
    is_match = match_score_ok and intensity_ok and mz_ok and abundance_ratio_ok

    if is_match:
        # Add the matched peak in white on top of peaks trace to highlight it
        peak_trace = {
            "name": "{:.6f}".format(iso.sample_peak_mz),
            "type": "scatter",
            "mode": "lines+markers",
            "line": {"color": "white"},
            "x": [iso.sample_peak_mz, iso.sample_peak_mz],
            "y": [0, peak_height],
            "unit": UNITS,
        }
        isotope_result.spectrum_traces.append(peak_trace)

    # Add isotope expected trace
    isotope_result.spectrum_traces.append(
        {
            "name": "target m/z",
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "red"},
            "x": [float(iso.mz), float(iso.mz)],
            "y": [0, isotope_expected_height],
            "unit": UNITS,
        }
    )

    # If there is a matching peak, add timeseries trace and update sum timeseries
    if is_match:
        isotope_result.match_count += 1
        match_timeseries = ctx.peak_timeseries.sel(
            mz=iso.sample_peak_mz, method="nearest"
        )

        timeseries_time = match_timeseries.time.values.astype(np.float32)
        timeseries_y = match_timeseries.values.astype(np.float32)

        timeseries_rgb = colormap[ctx.index + ctx.color_offset]
        timeseries_trace = {
            "name": "{:.6f}".format(iso.mz),
            "type": "scatter",
            "mode": "lines",
            "line": {"color": "rgb({},{},{})".format(*timeseries_rgb)},
            "fill": "tozeroy",
            "fillcolor": "rgba({},{},{},.3)".format(*timeseries_rgb),
            "x": timeseries_time.tobytes(),
            "y": timeseries_y.tobytes(),
            "unit": UNITS,
        }
        isotope_result.timeseries_traces.append(timeseries_trace)
        if isotope_result.sum_timeseries is None:
            isotope_result.sum_timeseries = match_timeseries.copy()
        else:
            isotope_result.sum_timeseries += match_timeseries

    return isotope_result
