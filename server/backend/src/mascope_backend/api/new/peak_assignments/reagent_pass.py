"""The reagent pre-pass: the peaks the source made, taken out before the stages.

A chemical-ionization source is loud on its own account. Its reagent ion
clusters with itself, with water, and with the acid it sheds, and those ions are
the brightest things in the spectrum - the top ten peaks of every sample on the
gate, most of the total signal. None of them is sample chemistry.

Left alone they do one of two harmful things. They sit in the residual, where
they make the unexplained signal look like a measurement problem rather than a
known source background. Or, worse, a stage reads one of them as an analyte:
a reagent cluster has a perfectly ordinary elemental composition, so an
untargeted search will happily fit a neutral to it and commit a phantom that
then dominates the assigned signal. The reference engine hit exactly that -
its bright urea reagent ions came back as ammonium-adduct analytes - and had
to add a merge-level guard to strip them again afterwards.

So the library runs FIRST, before either stage sees the peak list, and the peaks
it claims are removed from both stages' inputs. That ordering is what makes the
claim stick: there is no lock to fight over, because nothing downstream is ever
offered the peak. The cost of the ordering is that a curated target compound
whose exact mass coincides with a reagent cluster would lose - which is why
membership of the library is drawn as narrowly as it is (see
``mascope_tools.composition.reagents``): every atom of a library ion comes from
the reagent, the solvent or the instrument background, and a cluster of the
reagent with anything the sample supplied is deliberately left out, because that
ion IS the analyte's adduct channel.

What a reagent row claims, and what it does not: it names the ion formula, which
is known exactly, and it names no ``assigned_formula``, because there is no
analyte. That is the whole point rather than a gap in the data - a row with no
assigned formula votes on no consensus, weighs on no tier, and is counted as
explained by its role. The tier is 'unassigned' for the same reason, and means
what it says: no analyte was assigned to this peak. The role is what says the
peak is accounted for.
"""

from __future__ import annotations

import pandas as pd

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_REAGENT,
    SOURCE_REAGENT,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db.id import gen_id
from mascope_tools.composition.reagents import (
    ReagentCluster,
    ReagentHit,
    match_reagent_clusters,
    reagent_library,
)


__all__ = [
    "build_reagent_assignments",
    "claim_reagent_peaks",
    "reagent_library_for",
]


def reagent_library_for(profile_name: str) -> tuple[ReagentCluster, ...]:
    """The cluster library a resolved profile's source makes.

    A thin pass-through, kept so the backend names the library in one place and
    a caller does not have to know that the table is keyed on the profile name.

    :param profile_name: The resolved profile's name.
    :return: Its reagent ions, empty when the profile has no single reagent.
    """
    return reagent_library(profile_name)


def _provenance(hit: ReagentHit) -> dict:
    """What a reagent row records about why it was claimed.

    The parent of a satellite is named here rather than through
    ``owner_peak_assignment_id``: that link models one thing in this ledger - an
    isotopologue naming the M0 analyte it belongs to - and the import path
    enforces it, so a reagent row must not name an owner. The relationship is
    still recorded, just as provenance rather than as structure.
    """
    record: dict = {
        "reagent": {
            "ion": hit.cluster.label,
            "kind": hit.cluster.kind,
            "mz": round(hit.cluster.mz, 5),
        }
    }
    if hit.is_satellite and hit.predicted_relative is not None:
        # The satellite's share of its cluster's monoisotopic peak, so a reader
        # can check the claim against the peak's own height. Which cluster it
        # belongs to is already in `ion` above, and which line of that cluster
        # is in the row's `isotope_label`.
        record["reagent"]["predicted_relative"] = round(hit.predicted_relative, 5)
    return record


def build_reagent_assignments(
    hits: list[ReagentHit],
    peaks_df: pd.DataFrame,
    sample_item_id: str,
    peak_assignment_run_id: str,
) -> list[dict]:
    """Turn claimed peaks into ledger rows.

    :param hits: The claims, from :func:`claim_reagent_peaks`.
    :param peaks_df: The frame the hits' positions index into, positionally.
    :param sample_item_id: The sample these rows belong to.
    :param peak_assignment_run_id: The run they belong to.
    :return: One row per claimed peak.
    """
    peak_ids = peaks_df["sample_peak_id"].to_numpy()
    return [
        {
            "peak_assignment_id": gen_id(32),
            "peak_assignment_run_id": peak_assignment_run_id,
            "sample_item_id": sample_item_id,
            "sample_peak_id": str(peak_ids[hit.index]),
            "sample_peak_mz": float(hit.mz),
            "sample_peak_intensity": float(hit.intensity),
            "sample_peak_tof": None,
            "role": ROLE_REAGENT,
            # No analyte was assigned to this peak, and none should be inferred
            # from the ion formula: the ion is the source's, not the sample's.
            "assigned_formula": None,
            "ion_formula": hit.cluster.formula,
            "ionization_mechanism_id": None,
            "isotope_label": hit.isotope_label,
            "isotope_formula": None,
            "source": SOURCE_REAGENT,
            "fit_score": None,
            "mz_error_ppm": round(float(hit.mz_error_ppm), 4),
            "abundance_error": None,
            "tier": TIER_UNASSIGNED,
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_peak_assignment_id": None,
            "alternatives": None,
            "provenance": _provenance(hit),
        }
        for hit in hits
    ]


def claim_reagent_peaks(
    peaks_df: pd.DataFrame,
    library: tuple[ReagentCluster, ...],
    *,
    purity: float | None = None,
) -> list[ReagentHit]:
    """Match the library against a sample's peaks.

    :param peaks_df: The sample's peaks, with ``mz`` and ``intensity`` columns.
        Hits index into this frame positionally.
    :param library: The reagent ions, from :func:`reagent_library_for`.
    :param purity: The labelled reagent's isotopic purity, so a labelled
        reagent's satellites are predicted with the label's own abundance.
    :return: One hit per claimed peak.
    """
    if not library or peaks_df.empty:
        return []
    return match_reagent_clusters(
        library,
        peaks_df["mz"].to_numpy(),
        peaks_df["intensity"].to_numpy(),
        purity=purity,
    )
