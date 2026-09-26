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

Membership is only half of what keeps the claim honest; the other half is the
mass a claim is made against. It is not a fixed tolerance around the library's
own number: the source's base ions are found first and say where THIS spectrum
puts the reagent's masses, and every other rung is claimed at the instrument's
own precision against a mass they have corrected. Without that a window wide
enough to absorb a real miscalibration is also wide enough to absorb the
neighbouring analyte, and on the gate it did exactly that - see
:func:`mascope_tools.composition.reagents.match_reagent_clusters`.

What a reagent row claims, and what it does not: it names the ion formula, which
is known exactly, and it names no ``assigned_formula``, because there is no
analyte. That is the whole point rather than a gap in the data - a row with no
assigned formula votes on no consensus, weighs on no tier, and is counted as
explained by its role. The tier is 'unassigned' for the same reason, and means
what it says: no analyte was assigned to this peak. The role is what says the
peak is accounted for.

The library holds more than the reagent's own ladder: the ions a source makes
of the air it ionizes and the calibrant beam an Orbitrap's internal-calibration
source runs, each ion carrying its family and the literature that names it -
or, for the few no work found names, what shows it - which the row records so
a reader can check the claim against a paper. A fourth family is claimed after
the stages rather than before them: the fragments a source makes of an
analyte, which only the analyte's own commit licenses (:func:`claim_fragments`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from mascope_backend.api.new.peak_assignments.cross_channel import (
    neutral_key,
    same_ion_readings,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_REAGENT,
    SOURCE_REAGENT,
    is_target_library_row,
)
from mascope_backend.api.new.peak_assignments.mass_gate import is_committed
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.reagents import (
    FAMILY_FRAGMENT,
    KIND_FRAGMENT,
    FragmentIon,
    FragmentLadder,
    ReagentCalibration,
    ReagentCluster,
    ReagentHit,
    fragment_ladders,
    match_reagent_clusters,
    reagent_library,
)


__all__ = [
    "FragmentClaims",
    "build_reagent_assignments",
    "claim_fragments",
    "claim_reagent_peaks",
    "fragment_ladders_for",
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


def fragment_ladders_for(profile_name: str) -> tuple[FragmentLadder, ...]:
    """The analytes a resolved profile's source breaks, and what into.

    A pass-through for the same reason :func:`reagent_library_for` is one.

    :param profile_name: The resolved profile's name.
    :return: Its fragment ladders, empty when it names none.
    """
    return fragment_ladders(profile_name)


def _provenance(hit: ReagentHit) -> dict:
    """What a reagent row records about why it was claimed.

    The ion's family and the literature that names it travel with every row, so
    a reader checks a claim against a paper rather than against the table.

    The parent of an isotopologue is named here rather than through
    ``owner_peak_assignment_id``: that link models one thing in this ledger - an
    isotopologue naming the M0 analyte it belongs to - and the import path
    enforces it, so a reagent row must not name an owner. The relationship is
    still recorded, just as provenance rather than as structure.
    """
    record: dict = {
        "reagent": {
            "ion": hit.cluster.label,
            "kind": hit.cluster.kind,
            "family": hit.cluster.family,
            "references": list(hit.cluster.references),
            "mz": round(hit.cluster.mz, 5),
        }
    }
    if hit.cluster.observed:
        # No work names this ion; the row says what shows it instead.
        record["reagent"]["observed"] = hit.cluster.observed
    if hit.is_isotopologue and hit.predicted_relative is not None:
        # The isotopologue's share of its cluster's monoisotopic peak, so a reader
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
    claim_ppm: float,
    purity: float | None = None,
) -> tuple[list[ReagentHit], ReagentCalibration | None]:
    """Match the library against a sample's peaks.

    :param peaks_df: The sample's peaks, with ``mz`` and ``intensity`` columns.
        Hits index into this frame positionally.
    :param library: The reagent ions, from :func:`reagent_library_for`.
    :param claim_ppm: The run's own m/z precision. A claim is made at the
        instrument's precision against a mass the sample's anchor ions have
        corrected, not in a window wide enough to swallow a neighbour.
    :param purity: The labelled reagent's isotopic purity, so a labelled
        reagent's isotopologues are predicted with the label's own abundance.
    :return: One hit per claimed peak, and what the anchors said; the
        calibration is ``None`` when there was nothing to match.
    """
    if not library or peaks_df.empty:
        return [], None
    return match_reagent_clusters(
        library,
        peaks_df["mz"].to_numpy(),
        peaks_df["intensity"].to_numpy(),
        claim_ppm=claim_ppm,
        purity=purity,
    )


# --- fragments ----------------------------------------------------------------
#
# A fragment is claimed after the stages, not before them, because what licenses
# the claim is a commit: the parent's. The pre-pass sees only peaks, and a peak
# at a fragment's mass is as often a component's own ion - C6H7+ is a
# monoterpene's fragment and protonated benzene - so a claim made before either
# stage could only bury the component whenever the parent was also there.


#: The tiers at which a committed row shows its molecule to the claim: the bar
#: the partner gate sets for a partner (``engine.apply_partner_gates``).
_SHOWN_TIERS = (TIER_ASSIGNED, TIER_CANDIDATE)


@dataclass
class FragmentClaims:
    """What the fragment claim took from a sample's committed rows.

    :param rows: The committed rows it left, in the order given. The rows passed
        in are not modified.
    :param fragments: The reagent rows it wrote on the peaks it took: each
        fragment, then the lines of its envelope the stages had committed.
    :param peak_ids: The peaks those rows are on.
    :param summary: A JSON-serializable record for the run's config.
    """

    rows: list[dict]
    fragments: list[dict]
    peak_ids: set[str]
    summary: dict


def _ion_key(formula: str | None) -> str:
    """An ion's composition, however its charge was written.

    A committed row spells its ion with the charge (``C6H7+``), a ladder
    without it. A labelled or isotope-substituted ion keys to nothing a ladder
    holds, which is what it should.
    """
    text = str(formula or "").strip().rstrip("+-.")
    return neutral_key(text) if text else ""


def _height(row: dict) -> float:
    """A row's peak height, 0 for one that has none."""
    try:
        height = float(row.get("sample_peak_intensity") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return height if math.isfinite(height) else 0.0


def _channel(row: dict, notation_by_id: dict[str, str]) -> str | None:
    """The channel a row was read through, None where the run does not search it."""
    return notation_by_id.get(str(row.get("ionization_mechanism_id")))


def _shown_on(
    rows: list[dict],
    notation_by_id: dict[str, str],
    minor_channels: frozenset[str],
) -> dict[str, set[str]]:
    """The peaks on which the sample shows each molecule.

    Per neutral, every peak whose monoisotopic row commits it at candidate or
    better through one of the mode's own channels: what the partner gate calls a
    partner and the cross-channel pass a molecule the sample shows
    (``cross_channel.partner_heights``). An opportunistic channel shows nothing
    on its own.
    """
    shown: dict[str, set[str]] = {}
    for row in rows:
        if row.get("tier") not in _SHOWN_TIERS:
            continue
        channel = _channel(row, notation_by_id)
        if channel is None or channel in minor_channels:
            continue
        shown.setdefault(neutral_key(row.get("assigned_formula")), set()).add(
            str(row.get("sample_peak_id"))
        )
    return shown


def _fragment_row(
    row: dict,
    fragment: FragmentIon,
    ladder: FragmentLadder,
    *,
    parent: dict,
    ratio: float,
    read_as: dict | None,
) -> dict:
    """The reagent row a claimed fragment's peak becomes.

    Shaped as the pre-pass's rows are (:func:`build_reagent_assignments`): the
    fragment's ion and no analyte, since the peak is an ion the source made of
    the parent, not a molecule of the sample. The parent it was claimed for, and
    the reading the stages had committed on the peak, are provenance.

    :param row: The committed row on the fragment's peak, or on one of the
        lines of its envelope.
    :param fragment: The fragment ion.
    :param ladder: The ladder it belongs to.
    :param parent: The parent's brightest committed row.
    :param ratio: The fragment's height over the parent's.
    :param read_as: What the stages had read the peak as; None on an
        isotopologue line, whose reading was its monoisotopic row's.
    """
    mz = float(row.get("sample_peak_mz") or 0.0)
    record: dict = {
        "ion": fragment.label,
        "kind": KIND_FRAGMENT,
        "family": FAMILY_FRAGMENT,
        "references": list(ladder.references),
        "mz": round(fragment.mz, 5),
        "parent": {
            "formula": ladder.parent,
            "label": ladder.label,
            "peak_mz": round(float(parent.get("sample_peak_mz") or 0.0), 5),
            "ratio": round(ratio, 4),
            "max_ratio": round(fragment.max_ratio, 4),
        },
    }
    if read_as is not None:
        record["read_as"] = read_as
    isotopologue = row.get("role") == ROLE_ISO_CHILD
    return {
        "peak_assignment_id": gen_id(32),
        "peak_assignment_run_id": row.get("peak_assignment_run_id"),
        "sample_item_id": row.get("sample_item_id"),
        "sample_peak_id": str(row.get("sample_peak_id")),
        "sample_peak_mz": mz,
        "sample_peak_intensity": row.get("sample_peak_intensity"),
        "sample_peak_tof": row.get("sample_peak_tof"),
        "role": ROLE_REAGENT,
        # No analyte: the ion is one the source made of the parent.
        "assigned_formula": None,
        "ion_formula": fragment.formula,
        "ionization_mechanism_id": None,
        "isotope_label": row.get("isotope_label") if isotopologue else None,
        "isotope_formula": None,
        "source": SOURCE_REAGENT,
        "fit_score": None,
        # The fragment's own line is measured against the fragment's mass, as a
        # reagent row is against its ion's. An isotopologue line keeps the error
        # the stage measured it at, against that line's own mass.
        "mz_error_ppm": (
            row.get("mz_error_ppm")
            if isotopologue
            else round((mz - fragment.mz) / fragment.mz * 1e6, 4)
        ),
        "abundance_error": None,
        "tier": TIER_UNASSIGNED,
        "target_compound_id": None,
        "target_ion_id": None,
        "owner_peak_assignment_id": None,
        "alternatives": None,
        "provenance": {"reagent": record},
    }


def claim_fragments(
    rows: list[dict],
    ladders: tuple[FragmentLadder, ...],
    *,
    notation_by_id: dict[str, str],
    minor_channels: frozenset[str] = frozenset(),
) -> FragmentClaims:
    """Claim the peaks that are fragments of an analyte the stages committed.

    Read over both stages' committed rows as they built them, before the passes
    that judge them: a fragment read as a molecule is a partner, a second
    channel and a rival to every other reading of its mass, and the partner
    gate and the cross-channel pass must not weigh it as one. A fragment row is
    claimed where three things hold:

    - **The parent is committed.** A monoisotopic row commits the ladder's
      neutral through one of the mode's own channels, at any tier: a parent
      under its band is still the parent, and what the claim asks of it is its
      height, not its fit. An opportunistic channel makes no parent, as it makes
      no partner.
    - **The ladder's ratio holds.** The fragment's peak is no taller, over the
      parent's brightest such row, than the literature lets the parent make it
      (``FragmentIon.max_ratio``). There is no lower bound: a softer source
      breaks the parent less than the references' sources did.
    - **Nothing else shows a reading of the ion.** No reading of the fragment's
      ion - the row's own, or one of the same-ion readings it displaced - names
      a molecule the sample shows on a peak outside the ladder, at candidate or
      better through one of the mode's own channels. That is what tells a
      monoterpene's C6H7+ from protonated benzene: benzene's own radical cation
      is a peak of its own, and the claim yields to it. The ladder's peaks show
      nothing to the test, since a molecule read on one of them is the parent's
      fragment again.

    A row of the target library is not taken: the workspace named that compound
    for the peak. A claimed row's isotopologue lines go with it, as reagent rows
    of the fragment.

    :param rows: Both stages' committed rows, as built.
    :param ladders: The profile's fragment ladders.
    :param notation_by_id: The searched mechanisms, by the id the rows carry.
    :param minor_channels: The run's opportunistic channels.
    :return: What the claim left and wrote.
    """
    summary: dict = {
        "ladders": [ladder.label for ladder in ladders],
        "parents": [],
        "claimed": 0,
        "claimed_isotopologues": 0,
        # Rows on a fragment's ion that the claim left, by why.
        "held": {"taller_than_ladder": 0, "shown_elsewhere": 0, "target_library": 0},
        "claims": [],
    }
    if not ladders:
        return FragmentClaims(list(rows), [], set(), summary)
    monoisotopic = [
        row for row in rows if is_committed(row) and row.get("role") == ROLE_M0
    ]
    shown = _shown_on(monoisotopic, notation_by_id, minor_channels)
    claimed: dict[str, dict] = {}
    for ladder in ladders:
        parent_key = neutral_key(ladder.parent)
        parents = [
            row
            for row in monoisotopic
            if neutral_key(row.get("assigned_formula")) == parent_key
            and _channel(row, notation_by_id) is not None
            and _channel(row, notation_by_id) not in minor_channels
        ]
        parent = max(parents, key=_height, default=None)
        if parent is None or _height(parent) <= 0.0:
            continue
        summary["parents"].append(
            {
                "formula": ladder.parent,
                "peak_mz": round(float(parent.get("sample_peak_mz") or 0.0), 5),
                "tier": parent.get("tier"),
            }
        )
        by_ion = {_ion_key(fragment.formula): fragment for fragment in ladder.fragments}
        parent_ids = {str(row.get("peak_assignment_id")) for row in parents}
        within: list[tuple[dict, FragmentIon, float]] = []
        for row in monoisotopic:
            fragment = by_ion.get(_ion_key(row.get("ion_formula")))
            if fragment is None or str(row.get("peak_assignment_id")) in parent_ids:
                continue
            ratio = _height(row) / _height(parent)
            if ratio > fragment.max_ratio:
                summary["held"]["taller_than_ladder"] += 1
                continue
            within.append((row, fragment, ratio))
        ladder_peaks = {str(row.get("sample_peak_id")) for row in parents} | {
            str(row.get("sample_peak_id")) for row, _, _ in within
        }
        for row, fragment, ratio in within:
            if is_target_library_row(row):
                summary["held"]["target_library"] += 1
                continue
            readings = [row] + same_ion_readings(row, notation_by_id)
            if any(
                shown.get(neutral_key(reading.get("assigned_formula")), set())
                - ladder_peaks
                for reading in readings
            ):
                summary["held"]["shown_elsewhere"] += 1
                continue
            claimed.setdefault(
                str(row.get("peak_assignment_id")),
                {
                    "row": row,
                    "fragment": fragment,
                    "ladder": ladder,
                    "parent": parent,
                    "ratio": ratio,
                },
            )

    fragments: list[dict] = []
    for claim in claimed.values():
        row = claim["row"]
        fragments.append(
            _fragment_row(
                row,
                claim["fragment"],
                claim["ladder"],
                parent=claim["parent"],
                ratio=claim["ratio"],
                read_as={
                    "formula": row.get("assigned_formula"),
                    "ionization": _channel(row, notation_by_id),
                    "tier": row.get("tier"),
                },
            )
        )
        summary["claimed"] += 1
        summary["claims"].append(
            {
                "ion": claim["fragment"].label,
                "mz": round(float(row.get("sample_peak_mz") or 0.0), 5),
                "parent": claim["ladder"].parent,
                "ratio": round(claim["ratio"], 4),
                "read_as": row.get("assigned_formula"),
            }
        )
    for row in rows:
        claim = claimed.get(str(row.get("owner_peak_assignment_id")))
        if claim is None or row.get("role") != ROLE_ISO_CHILD:
            continue
        fragments.append(
            _fragment_row(
                row,
                claim["fragment"],
                claim["ladder"],
                parent=claim["parent"],
                ratio=claim["ratio"],
                read_as=None,
            )
        )
        summary["claimed_isotopologues"] += 1

    taken = set(claimed)
    kept = [
        row
        for row in rows
        if str(row.get("peak_assignment_id")) not in taken
        and str(row.get("owner_peak_assignment_id")) not in taken
    ]
    return FragmentClaims(
        kept,
        fragments,
        {str(row["sample_peak_id"]) for row in fragments},
        summary,
    )
