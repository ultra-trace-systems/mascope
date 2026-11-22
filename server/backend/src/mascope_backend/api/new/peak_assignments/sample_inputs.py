"""What a sample's run reads before any stage: its peaks and its known isotopes.

The peak list of a sample's file, its resolving power and source ion
current, whether it can be assigned at all, and the isotopologue lines of the
target library and the reference sources at the file's resolution.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    FileLines,
    generate_target_ions_from_composition,
    lines_at_file,
)
from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.api.new.match.params.lib import isotope_abundance_threshold_expr
from mascope_backend.api.new.peak_assignments.engine import REFERENCE_IDENTITIES_COL
from mascope_backend.api.new.reference.licenses import reference_license_gate
from mascope_backend.db import (
    IonizationMechanism,
    Sample,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_type
from mascope_match import shift_mz
from mascope_reference import iter_known_compositions, known_state_fingerprint
from mascope_reference.scope import SAMPLE_POLARITIES
from mascope_signal.compute import (
    PartialPeakStoreError,
    StalePeakStoreError,
    get_stream_tic_by_peak,
    get_tic_per_scan,
)
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS, label_purity
from mascope_tools.composition.known_window import KnownWindow


#: The columns of a sample's peak list (:func:`load_sample_peaks`) the
#: untargeted search reads, where the list has them: the noise estimate
#: decides whether a predicted line's absence is evidence, and the scan
#: counts how far a dim line's height is diluted by the scans it missed.
SEARCH_PEAK_COLUMNS = (
    "mz",
    "intensity",
    "signal_to_noise",
    "n_scans",
    "n_scans_total",
)


def resolution_type_of(sample) -> str:
    """The isotopologue resolution a sample's instrument is scored at."""
    return "LOW" if get_instrument_type(sample.filename) == "tof" else "HIGH"


#: The caller has not read the sample file's resolving power, and the chain
#: reads it (``sample_inputs._resolution_of``). Distinct from None, the answer for a
#: file whose instrument functions could not be read.
RESOLVE_RESOLUTION = object()


# -------------------------------------------------------------------
# Assignment engine orchestration
# -------------------------------------------------------------------


def ineligible_reason(sample: Sample) -> str | None:
    """Why this sample cannot usefully be assigned, or None if it can.

    Mirrors the eligibility checks the targeted controllers apply
    (``match_compute_batch`` and the rematch verified-calibration gate): a
    blank sample carries no peaks, and a sample whose m/z calibration exists
    but is unverified would produce mass errors - and therefore fit scores and
    tiers - that mean nothing. Shared by the batch partition and the
    per-sample guard so one sample assigned from the sample menu is refused on
    exactly the condition a batch would have skipped it under.

    :param sample: Sample to test.
    :return: A short reason string, or None when the sample is eligible.
    """
    if sample.instrument_function_id is None:
        return "blank sample (no peaks)"
    if sample.mz_calibration and not sample.mz_calibration.get("verified", False):
        return "m/z calibration not verified"
    return None


#: The stored library's fine structure, which a run resolves at the file's
#: R(m/z) (``target_ions_compute.lines_at_file``).
FINE_LINES = "HIGH"

#: The column a fetched fine line carries its ion's abundance threshold in.
_ABUNDANCE_THRESHOLD_COL = "abundance_threshold"


async def _fetch_known_target_isotopes(
    sample: Sample,
    isotope_abundance_threshold: float,
    ionization_mechanism_ids: list[str],
    file_lines: FileLines,
) -> pd.DataFrame:
    """
    Fetch the full known-isotopologue set for a sample (Stage A input).

    Unlike fetch_sample_unmatched_target_isotopes this does not exclude
    already-matched isotopes - a peak assignment run always evaluates the
    whole library - and it carries the compound/ion metadata that ends up
    denormalized on PeakAssignment rows.

    The library's fine lines are read and resolved at the file's R(m/z)
    (``target_ions_compute.lines_at_file``), as the envelope match predicts a
    search candidate's, rather than its stored ``LOW`` lines, merged at one
    resolving power for every TOF, or its unmerged ``HIGH`` ones (MAS-977).
    The abundance threshold, an ion's own or the instrument's, is applied to
    the resolved lines.

    :param sample: Sample model object
    :param isotope_abundance_threshold: Minimum relative abundance for a
        target isotope to participate
    :param ionization_mechanism_ids: The sample's mechanism ids, resolved
        once per run by :func:`fetch_sample_mechanisms`
    :param file_lines: The sample file's resolution, its lines resolved at.
    :return: DataFrame of target isotopes with compound/ion metadata
    """
    async with async_session() as session:
        abundance_threshold = isotope_abundance_threshold_expr(
            TargetIon.filter_params,
            sample.instrument,
            isotope_abundance_threshold,
        )

        stmt = (
            select(
                TargetIsotope.target_isotope_id,
                TargetIsotope.target_ion_id,
                TargetIsotope.target_isotope_formula,
                TargetIsotope.mz,
                TargetIsotope.relative_abundance,
                TargetIsotope.resolution,
                TargetIon.target_ion_formula,
                TargetIon.ionization_mechanism_id,
                TargetCompound.target_compound_id,
                TargetCompound.target_compound_formula,
                IonizationMechanism.ionization_mechanism,
                IonizationMechanism.ionization_mechanism_polarity,
                abundance_threshold.label(_ABUNDANCE_THRESHOLD_COL),
            )
            .distinct(TargetIsotope.target_isotope_id)
            .select_from(TargetIsotope)
            .join(TargetIon, TargetIon.target_ion_id == TargetIsotope.target_ion_id)
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
                TargetCollectionInSampleBatch,
                TargetCollectionInSampleBatch.target_collection_id
                == TargetCompoundInTargetCollection.target_collection_id,
            )
            .where(
                TargetCollectionInSampleBatch.sample_batch_id == sample.sample_batch_id,
                TargetIon.ionization_mechanism_id.in_(ionization_mechanism_ids),
                IonizationMechanism.ionization_mechanism_polarity == sample.polarity,
                TargetIsotope.resolution == FINE_LINES,
            )
        )
        if not (rows := (await session.execute(stmt)).all()):
            return pd.DataFrame()

    target_isotopes_df = await asyncio.to_thread(
        lines_at_file,
        pd.DataFrame([row._asdict() for row in rows]),
        file_lines,
        _ABUNDANCE_THRESHOLD_COL,
    )
    target_isotopes_df = target_isotopes_df.drop(columns=[_ABUNDANCE_THRESHOLD_COL])
    if target_isotopes_df.empty:
        return pd.DataFrame()
    runtime.logger.info(
        f"Found {len(target_isotopes_df)} known target isotopes for sample "
        f"'{sample.sample_item_name}' (polarity: {sample.polarity})"
    )
    return target_isotopes_df


def _build_reference_isotopes_df(
    known_compositions: list,
    mechanisms: list,
) -> pd.DataFrame:
    """Expand reference formulas into their ions' fine lines (CPU-bound).

    For every unique reference formula, reuses the same target-ion path the
    curated library uses to produce ions -> isotopologues, keeps their fine
    structure, which a run resolves at its file's R(m/z) and holds to its
    abundance floor (``target_ions_compute.lines_at_file``), and shapes them
    like :func:`_fetch_known_target_isotopes` output. Reference rows carry synthetic
    isotope/ion ids (for in-run grouping only), no ``target_compound_id``, and a
    ``reference_identities`` list the inversion drops into provenance.
    """
    mech_by_id = {m.ionization_mechanism_id: m for m in mechanisms}
    rows: list[dict] = []
    for known in known_compositions:
        compound = SimpleNamespace(
            target_compound_id="reference",
            target_compound_formula=known.formula,
        )
        try:
            ions, isotopes = generate_target_ions_from_composition(compound, mechanisms)
        except Exception as error:  # noqa: BLE001 - a bad formula skips, never fails the run
            runtime.logger.debug(
                f"Skipping reference formula '{known.formula}': {error}"
            )
            continue
        ion_by_id = {ion.target_ion_id: ion for ion in ions}
        identities = [asdict(identity) for identity in known.identities]
        for iso in isotopes:
            if iso.resolution != FINE_LINES:
                continue
            ion = ion_by_id.get(iso.target_ion_id)
            if ion is None:
                continue
            mechanism = mech_by_id.get(ion.ionization_mechanism_id)
            rows.append(
                {
                    "target_isotope_id": iso.target_isotope_id,
                    "target_ion_id": iso.target_ion_id,
                    "target_isotope_formula": iso.target_isotope_formula,
                    "mz": iso.mz,
                    "relative_abundance": iso.relative_abundance,
                    "resolution": iso.resolution,
                    "target_ion_formula": ion.target_ion_formula,
                    "ionization_mechanism_id": ion.ionization_mechanism_id,
                    # No curated target for a reference-derived formula.
                    "target_compound_id": None,
                    "target_compound_formula": known.formula,
                    "ionization_mechanism": (
                        mechanism.ionization_mechanism if mechanism else None
                    ),
                    "ionization_mechanism_polarity": (
                        mechanism.ionization_mechanism_polarity if mechanism else None
                    ),
                    REFERENCE_IDENTITIES_COL: identities,
                }
            )
    return pd.DataFrame(rows)


# The expanded reference frame, its ions' fine lines, is identical for every
# run over the same (reference state, licence gate, mechanism set), so a
# Stage-A batch of N samples should pay the IsoSpec expansion once, not N
# times; each run then resolves it at its own file's R(m/z) and floor. The key's reference part comes from known_state_fingerprint, so a
# re-synced or (de)activated source invalidates naturally; the licence gate is
# in the key too, because narrowing it must not be served the wider frame a run
# from before the change left behind, and so is the labelled reagent's purity
# a run's setup reads its labelled lines at. A handful of entries covers a
# deployment's realistic (mechanism set, polarity) combinations.
_REFERENCE_ISOTOPE_CACHE_MAX = 8
_reference_isotope_cache: OrderedDict[tuple, pd.DataFrame] = OrderedDict()
_reference_isotope_cache_lock = asyncio.Lock()


async def _fetch_reference_known_isotopes(
    sample: Sample,
    isotope_abundance_threshold: float,
    mechanisms: list[SimpleNamespace],
    *,
    known_window: KnownWindow | None,
    file_lines: FileLines,
) -> pd.DataFrame:
    """Reference-database contribution to the Stage A known set.

    Pulls the active reference compounds each source admits - its own window
    under the context's ceiling, its radical allowance, and its polarity against
    the sample's (:func:`iter_known_compositions`) - then expands them into
    matchable isotope rows off the event loop. Returns an empty frame when there
    is no reference data or the sample has no matching ionization mechanisms -
    so the seam is a no-op until a reference database is loaded.

    The expansion is cached across runs (see the cache note above); the lock
    makes concurrent runs with the same key wait for one build instead of
    duplicating it.

    :param sample: Sample model object.
    :param isotope_abundance_threshold: Minimum relative abundance for a
        reference isotope to participate.
    :param mechanisms: The sample's polarity-matching mechanisms, resolved
        once per run by :func:`fetch_sample_mechanisms`.
    :param known_window: The resolved chemistry context's ceiling on every
        source's window, or None where the context sets none.
    :param file_lines: The sample file's resolution, its lines resolved at.
    :return: DataFrame in the known-isotope shape, or empty.
    """
    if not mechanisms:
        return pd.DataFrame()

    async with async_session() as session:
        fingerprint = await known_state_fingerprint(session)
    if not fingerprint:
        # No active reference sources: nothing to expand, and nothing worth a
        # cache slot either.
        return pd.DataFrame()

    licenses = reference_license_gate()
    polarity = SAMPLE_POLARITIES.get(sample.polarity)
    cache_key = (
        fingerprint,
        None if licenses is None else tuple(licenses),
        tuple(sorted(m.ionization_mechanism_id for m in mechanisms)),
        known_window,
        polarity,
        # The lines of a labelled ion are built at the purity in effect, the
        # run's setup's: a run under another setup builds its own.
        tuple(label_purity(symbol) for symbol in sorted(CUSTOM_ELEMENTS)),
    )

    async with _reference_isotope_cache_lock:
        reference_isotopes_df = _reference_isotope_cache.get(cache_key)
        if reference_isotopes_df is not None:
            _reference_isotope_cache.move_to_end(cache_key)
            runtime.logger.debug(
                f"Reference isotope cache hit for sample '{sample.sample_item_name}'"
            )
        else:
            reference_isotopes_df = await _build_reference_known_isotopes(
                sample, polarity, licenses, mechanisms, known_window
            )
            _reference_isotope_cache[cache_key] = reference_isotopes_df
            while len(_reference_isotope_cache) > _REFERENCE_ISOTOPE_CACHE_MAX:
                _reference_isotope_cache.popitem(last=False)
    # Resolved outside the lock: the fine lines are shared, the file's R(m/z)
    # is this run's.
    return await asyncio.to_thread(
        lines_at_file, reference_isotopes_df, file_lines, isotope_abundance_threshold
    )


async def _build_reference_known_isotopes(
    sample: Sample,
    polarity,
    licenses,
    mechanisms: list[SimpleNamespace],
    known_window: KnownWindow | None,
) -> pd.DataFrame:
    """The reference lists' fine lines for a cache miss of
    :func:`_fetch_reference_known_isotopes`."""
    async with async_session() as session:
        # licenses=None keeps every record, which is what an unconfigured
        # deployment must get - the argument is only narrowing.
        known = await iter_known_compositions(
            session,
            licenses=None if licenses is None else set(licenses),
            ceiling=known_window,
            polarity=polarity,
        )
    if not known:
        reference_isotopes_df = pd.DataFrame()
    else:
        reference_isotopes_df = await asyncio.to_thread(
            _build_reference_isotopes_df,
            known,
            mechanisms,
        )
    gate = "" if licenses is None else f" (licences: {', '.join(licenses)})"
    runtime.logger.info(
        f"Built {len(reference_isotopes_df)} reference known isotopes from "
        f"{len(known)} reference formulas for sample "
        f"'{sample.sample_item_name}'{gate}"
    )
    return reference_isotopes_df


def _combine_known_isotopes(
    target_isotopes_df: pd.DataFrame, reference_isotopes_df: pd.DataFrame
) -> pd.DataFrame:
    """Union the target-library and reference known-isotope frames for Stage A.

    Target rows gain a null ``reference_identities`` column so the two frames
    align; the matcher and inversion then treat both uniformly, with reference
    rows distinguished only by that carried column.
    """
    frames = [
        frame
        for frame in (target_isotopes_df, reference_isotopes_df)
        if not frame.empty
    ]
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    if REFERENCE_IDENTITIES_COL not in combined.columns:
        combined[REFERENCE_IDENTITIES_COL] = None
    return combined


async def _fetch_known_isotopes(
    sample: Sample,
    isotope_abundance_threshold: float,
    mechanism_ids: list[str],
    mechanisms: list[SimpleNamespace],
    *,
    known_window: KnownWindow | None,
    resolution: Callable[[float], float] | None | object = RESOLVE_RESOLUTION,
) -> pd.DataFrame:
    """The known set a run of the sample reads: the target library and the
    reference lists, as one frame (:func:`_combine_known_isotopes`), each
    ion's lines as the file's R(m/z) shows them.

    Stage A matches it against the sample's peaks. A batch search, which runs
    no Stage A, reads it for the neutrals it names alone
    (:func:`engine.listed_formula_identities`), so that a list corroborates a
    secondary-channel reading there as it does in a run of the sample.

    :param sample: Sample model object.
    :param isotope_abundance_threshold: Minimum relative abundance for an
        isotope to participate.
    :param mechanism_ids: The mode's mechanism ids, which the target library
        is read through (:func:`_fetch_known_target_isotopes`).
    :param mechanisms: The mechanisms the reference lists are read through
        (:func:`_fetch_reference_known_isotopes`).
    :param known_window: The resolved chemistry context's ceiling on every
        reference source's window, or None where the context sets none.
    :param resolution: The file's resolving power as the run read it
        (:func:`_resolution_of`), or :data:`RESOLVE_RESOLUTION` to read it here.
    :return: The combined frame, empty where neither names anything.
    """
    if resolution is RESOLVE_RESOLUTION:
        resolution = await _resolution_of(sample)
    file_lines = FileLines(resolution_type_of(sample), resolution)
    target_isotopes_df = await _fetch_known_target_isotopes(
        sample, isotope_abundance_threshold, mechanism_ids, file_lines
    )
    reference_isotopes_df = await _fetch_reference_known_isotopes(
        sample,
        isotope_abundance_threshold,
        mechanisms,
        known_window=known_window,
        file_lines=file_lines,
    )
    return _combine_known_isotopes(target_isotopes_df, reference_isotopes_df)


def load_sample_peaks(sample: Sample) -> pd.DataFrame:
    """
    Load every observed peak of the sample with an averaged intensity.

    Uses peak heights for Orbitrap files and peak areas for TOF files,
    mirroring the targeted matcher. Public because it defines the engine's
    peak read - id set, m/z axis, and the instrument-correct intensity
    quantity - and the batch ledger's propagation must see a sample through
    exactly the same read, or the members it re-measures would carry a
    different intensity quantity than an engine run's.

    The per-peak signal-to-noise rides along when the file carries one, because
    the untargeted stage judges a predicted isotopologue's absence against the
    noise rather than against its abundance alone. It is the same estimate the
    targeted match path reads, arriving by a different route: that one takes it
    off the match frame `compute_match_isotopes` builds, and the untargeted
    stage has no match frame - it works from this peak list.

    So do the scan counts where the file keeps them (``n_scans`` and
    ``n_scans_total``): how many of the scans averaged into each peak hold it.
    An Orbitrap scan keeps a dim line only where it clears that scan's noise,
    so the averaged list reads it diluted by the share of scans it missed, and
    the untargeted stage reads it at its mean per scan instead
    (``mascope_tools.composition.scan_presence``).

    Inside a decoy run's shifted axis (``mascope_match.shifted_mz_axis``) the
    m/z are read moved, as the targeted matcher reads them there, so every
    stage of the run sees one spectrum.

    :param sample: Sample model object
    :return: DataFrame with sample_peak_id, mz, intensity, and (where the file
        has them) signal_to_noise, n_scans and n_scans_total columns
    """
    peak_data = extract_peaks(sample.filename, sample.polarity, sample.t0, sample.t1)
    instrument_type = get_instrument_type(sample.filename)
    intensities = peak_data.heights if instrument_type == "orbi" else peak_data.areas
    peaks_df = pd.DataFrame(
        {
            "sample_peak_id": [str(peak_id) for peak_id in peak_data.peak_ids],
            "mz": shift_mz(peak_data.mz_values),
            "intensity": intensities if intensities is not None else 0.0,
        }
    )
    peaks_df["intensity"] = peaks_df["intensity"].fillna(0.0)
    if peak_data.signal_to_noise is not None:
        # Left absent rather than filled: a missing column says "this file
        # records no noise estimate", and a zero would say "measured, and
        # noise-free", which is the one reading that must not happen.
        peaks_df["signal_to_noise"] = peak_data.signal_to_noise
    if peak_data.n_scans is not None and peak_data.n_scans_total is not None:
        # Absent for a file that keeps no count, as the noise estimate is; a
        # peak whose count the detection could not take reads NaN
        peaks_df["n_scans"] = pd.Series(peak_data.n_scans, dtype=float).to_numpy()
        peaks_df["n_scans_total"] = pd.Series(
            peak_data.n_scans_total, dtype=float
        ).to_numpy()
    return peaks_df


async def _resolution_of(sample) -> Callable[[float], float] | None:
    """The sample file's resolving power as a function of m/z, if it has one.

    What tells the mass gate how close two peaks are to each other in their own
    widths. A file whose instrument functions were never fitted still gets its
    lines judged, on their noise alone, so a read that fails stands down.

    :param sample: The sample view row.
    :return: The resolution function, or None.
    """
    try:
        _, resolution = await read_instrument_functions(
            sample.filename, polarity=sample.polarity
        )
    except Exception as exc:  # noqa: BLE001 - the lines are read without it
        runtime.logger.info(
            f"No resolution function for sample '{sample.sample_item_name}' "
            f"({exc}); its isotope lines are judged without their neighbours"
        )
        return None
    return resolution


def _source_ion_current(
    filename: str, polarity: str
) -> tuple[float, dict[str, float] | None]:
    """The ion current a sample's source lines are shares of.

    The mean TIC per scan of the polarity and, for a polarity the file's
    store stitches, the mean TIC per scan of the stream each peak is of
    (``get_stream_tic_by_peak``). A stale per-stream store, or one that
    carries only part of what a per-stream store does, is read as a pooled
    one, as the sample's own TIC reads it: its lines are held to the
    polarity's.

    Synchronous: it reads the filestore, so it runs in a worker thread.

    :return: The polarity's TIC, and the TIC by peak id or None
    """
    _, tic_per_scan = get_tic_per_scan(filename, polarity=polarity)
    tic = float(np.mean(tic_per_scan))
    try:
        tic_by_peak = get_stream_tic_by_peak(filename, polarity)
    except (StalePeakStoreError, PartialPeakStoreError):
        tic_by_peak = None
    return tic, tic_by_peak


def count_sample_peaks(sample: Sample) -> int:
    """How many peaks the sample's peak file holds.

    The id-only read the import validator uses, not the engine's own
    :func:`load_sample_peaks`: that one also opens the raw data source to
    average intensities, and the ingest ceiling needs only the count - and
    needs it before any run row exists. Blocking file I/O; call it off the
    event loop.

    :param sample: Sample model object
    :return: The number of detected peaks
    """
    peak_data = extract_peaks(
        sample.filename,
        sample.polarity,
        sample.t0,
        sample.t1,
        areas=False,
        heights=False,
        average=False,
    )
    return len(peak_data.peak_ids)
