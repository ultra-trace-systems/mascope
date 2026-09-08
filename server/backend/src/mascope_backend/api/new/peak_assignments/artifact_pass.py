"""The artifact pre-pass: the peaks the detector made, taken out before the stages.

A very intense centroid in an FT spectrum leaves ringing around itself - sidelobes
at a few tens of ppm on either side, a fraction of a percent of the base peak's
height. They are not ions. Left in the peak list they are unexplained signal at
best, and at worst a stage fits a neutral to one: the mass is ordinary, so an
untargeted search has no reason to refuse it.

Most of this is already handled before the engine ever runs. The peak detector
flags sidelobes as it detects (``mascope_signal.peak``, using the same
``flag_satellite_peaks``) and ``mascope_file.io.load_peak_data`` drops what it
flagged, so the assignment engine is normally handed a list they have already
been taken out of. What is left for this pass is the residue that survives the
detector's own read: the detector judges a whole file on summed heights, while a
run judges one sample's time window on averaged intensity, and a ratio that
failed the test on the file's sums can pass it on the sample's. Measured on the
assignment gate, that residue is nothing at all on most sets and about a
hundred peaks on one; the pass is here so the ledger names them rather than
leaving them in the residual, and so the role has a producer.

The pass runs on FT data only. Sidelobes are a property of the Fourier
transform, and the detector's TOF path declines to flag them for exactly that
reason; running an FT heuristic over TOF peaks would claim peaks on a mechanism
that is not there.

An artifact row is shaped like a reagent row and for the same reasons: no
``assigned_formula``, so it votes on no consensus and weighs on no tier; tier
'unassigned', because no analyte was assigned to the peak; no owner, because
owner linkage models an isotopologue naming its M0 and this is neither. It goes
further than a reagent row in one respect - it names no ``ion_formula`` either.
A reagent cluster is an ion whose formula is known exactly; a sidelobe is not an
ion at all, and naming one would be inventing a species to explain a detector's
response.
"""

from __future__ import annotations

import pandas as pd

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ARTIFACT,
    SOURCE_ARTIFACT,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db.id import gen_id
from mascope_tools.alignment.utils import flag_satellite_peaks


__all__ = [
    "ARTIFACT_KIND_SIDELOBE",
    "build_artifact_assignments",
    "claim_artifact_peaks",
]


#: What the pass says it found. One value today; the field is there because a
#: reader of a row should not have to infer which rule claimed the peak, and
#: because ringing is not the only artifact class a spectrum has.
ARTIFACT_KIND_SIDELOBE = "ft_sidelobe"

#: The instrument classes whose spectra can ring. TOF is deliberately absent.
FT_INSTRUMENTS = frozenset({"orbi"})


def claim_artifact_peaks(
    peaks_df: pd.DataFrame,
    instrument_type: str | None,
    claimed_peak_ids: set[str] | None = None,
) -> pd.DataFrame:
    """The peaks of the sample that are the detector's ringing, not ions.

    :param peaks_df: The sample's peaks, with ``sample_peak_id``, ``mz`` and
        ``intensity`` columns.
    :param instrument_type: The sample's instrument class. Anything but an FT
        instrument claims nothing, including an unknown one: a pass that cannot
        say what made the spectrum has no basis for saying a peak is an artifact
        of how it was made.
    :param claimed_peak_ids: Peaks the reagent pre-pass already owns. A reagent
        cluster's weakest satellite can sit in the ringing skirt of a brighter
        line and be flagged here too - on the bromide gate set one peak per
        sample is both - and two rows for one peak is not a ledger. The reagent
        claim wins because it is the more specific statement: it names the ion
        and predicts the peak's height, where this rule only says a peak is
        small and close to a big one. Excluded after the flag rather than
        before it, so a reagent peak still serves as a base peak or a mirror
        partner for the peaks around it.
    :return: The rows of ``peaks_df`` the sidelobe rule fires on.
    """
    if instrument_type not in FT_INSTRUMENTS or peaks_df.empty:
        return peaks_df.iloc[:0]
    flagged = flag_satellite_peaks(peaks_df[["mz", "intensity"]])
    claimed = peaks_df[flagged["is_satellite_peak"].to_numpy()]
    if claimed_peak_ids:
        claimed = claimed[~claimed["sample_peak_id"].isin(claimed_peak_ids)]
    return claimed


def build_artifact_assignments(
    artifact_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
) -> list[dict]:
    """Turn claimed peaks into ledger rows.

    :param artifact_df: The claimed peaks, from :func:`claim_artifact_peaks`.
    :param sample_item_id: The sample these rows belong to.
    :param peak_assignment_run_id: The run they belong to.
    :return: One row per claimed peak.
    """
    return [
        {
            "peak_assignment_id": gen_id(32),
            "peak_assignment_run_id": peak_assignment_run_id,
            "sample_item_id": sample_item_id,
            "sample_peak_id": str(peak.sample_peak_id),
            "sample_peak_mz": float(peak.mz),
            "sample_peak_intensity": float(peak.intensity),
            "sample_peak_tof": None,
            "role": ROLE_ARTIFACT,
            "assigned_formula": None,
            "ion_formula": None,
            "ionization_mechanism_id": None,
            "isotope_label": None,
            "isotope_formula": None,
            "source": SOURCE_ARTIFACT,
            "fit_score": None,
            "mz_error_ppm": None,
            "abundance_error": None,
            "tier": TIER_UNASSIGNED,
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_peak_assignment_id": None,
            "alternatives": None,
            "provenance": {"artifact": {"kind": ARTIFACT_KIND_SIDELOBE}},
        }
        for peak in artifact_df.itertuples()
    ]
