"""The evidence a run would have stored, measured on demand for a derived row.

A sample served from the batch ledger (``fold_view``) shows each peak with the
fit and the tier its member carries, and nothing else a run computes: the m/z
and abundance error of every isotopologue, the isotope labels, the chemical
plausibility, the evidence the tier was read off. The member row is slim by
design (design note section 4.2) and the sample has no run to have stored them.

This measures them when the inspector asks - the same way the finder's
formula-only alternatives are measured on request (``alternatives_scoring``):
the family's composition is scored against the sample's own peaks through the
seeded chain, and the numbers are served without being persisted. Always
through the family's M0, because the envelope is the compound's: an
isotopologue's error is the pairing of its M0's ion, and measuring the
isotopologue on its own would be a different, wrong claim.

The stored fit stays the fit: the tier was read off it, and the evidence
reported here is that fit times the plausibility, so the numbers on the card
agree with the chip. The measurement's own fit is reported beside it.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.peak_assignments.batch_peaks import resolve_candidate
from mascope_backend.api.new.peak_assignments.engine import (
    _isotope_offset_label,
    evidence_for,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    fold_assignment_id,
    fold_id_target,
    fold_member,
    is_fold_id,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    finite_or_none,
    score_or_none,
    score_seeds,
)
from mascope_backend.db import async_session
from mascope_tools.composition.heuristic_filter import formula_plausibility


def _peak_id(value: Any) -> Optional[str]:
    """The peak a scored row paired to, or None for the matcher's blanks."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value)
    return text or None


def isotopologue_rows(ion_rows: pd.DataFrame) -> list[dict]:
    """One entry per predicted isotopologue of an ion, lightest first.

    Each carries its offset label (``M0``, ``M+1`` ...), its isotope formula,
    its theoretical m/z and relative abundance, and - when the matcher paired
    it to one of the sample's peaks - that peak with the errors of the
    pairing. Two isotopologues can pair to the same peak in a crowded window;
    the more abundant one keeps the peak and the other reads as unpaired, the
    rule ``seeded_scoring.scored_maps`` applies.

    The relative abundance is a fraction of the M0's, so the M0 reads 1 - the
    way a run's rows and the inspector's column read it. The seed pattern
    arrives normalised to its sum, where an M0 is only as large as its share of
    the envelope; without an M0 abundance to scale by, the values pass through.

    :param ion_rows: The scored frame's rows for one ion.
    :return: The entries, in m/z order.
    """
    if ion_rows.empty:
        return []
    ordered = ion_rows.sort_values("mz")
    main_mz = float(ordered["mz"].iloc[0])
    main_abundance = finite_or_none(ordered["relative_abundance"].iloc[0])

    def relative_to_m0(value: Optional[float]) -> Optional[float]:
        if value is None or not main_abundance or main_abundance <= 0:
            return value
        return value / main_abundance

    entries: list[dict] = []
    for row in ordered.to_dict("records"):
        peak = _peak_id(row.get("sample_peak_id"))
        entries.append(
            {
                "isotope_label": _isotope_offset_label(float(row["mz"]), main_mz),
                "isotope_formula": _peak_id(row.get("target_isotope_formula")),
                "mz": finite_or_none(row.get("mz")),
                "relative_abundance": relative_to_m0(
                    finite_or_none(row.get("relative_abundance"))
                ),
                "sample_peak_id": peak,
                "mz_error_ppm": (
                    finite_or_none(row.get("match_mz_error")) if peak else None
                ),
                "abundance_error": (
                    finite_or_none(row.get("match_abundance_error")) if peak else None
                ),
            }
        )
    # The more abundant isotopologue keeps a contested peak.
    holder: dict[str, int] = {}
    for index, entry in enumerate(entries):
        peak = entry["sample_peak_id"]
        if peak is None:
            continue
        held = holder.get(peak)
        if held is None:
            holder[peak] = index
            continue
        weaker = (
            index
            if (entry["relative_abundance"] or 0.0)
            <= (entries[held]["relative_abundance"] or 0.0)
            else held
        )
        if weaker == held:
            holder[peak] = index
        entries[weaker].update(
            {"sample_peak_id": None, "mz_error_ppm": None, "abundance_error": None}
        )
    return entries


def _envelope(entry: dict, sample_name: str) -> dict:
    measured = entry.get("blocked_reason") is None
    return {
        "status": "success",
        "message": (
            f"Measured the composition of assignment '{entry['peak_assignment_id']}' "
            f"against sample '{sample_name}'"
            if measured
            else f"Assignment '{entry['peak_assignment_id']}' could not be measured: "
            f"{entry['blocked_reason']}"
        ),
        "results": 1,
        "data": [entry],
    }


@api_controller()
async def measure_derived_assignment(
    sample_item_id: str,
    peak_assignment_id: str,
) -> dict:
    """Measure a derived row's family against its sample, for the inspector.

    A run's own row is answered with nothing to do: it carries the numbers it
    was scored with. A derived row is measured through its family's M0 - an
    isotopologue names its owner, and the owner's member is what is scored -
    so the entry's ``peak_assignment_id`` is the M0's, and the inspector reads
    every family member's numbers off the one entry by ``sample_peak_id``.

    :param sample_item_id: The sample the row belongs to.
    :param peak_assignment_id: The row (a derived id, ``fold-<batch peak>``).
    :return: Status envelope; ``data`` is one entry, scored or blocked with a
        reason, or empty for a run's own row.
    :raises NotFoundException: The derived row is not this sample's.
    """
    if not is_fold_id(peak_assignment_id):
        return {
            "status": "success",
            "message": (
                f"Assignment '{peak_assignment_id}' is a run's own row and carries "
                "the numbers it was scored with; nothing to measure."
            ),
            "results": 0,
            "data": [],
        }
    sample = await fetch_sample(sample_item_id)
    async with async_session() as session:
        found = await fold_member(
            session, sample_item_id, fold_id_target(peak_assignment_id)
        )
        if found is None:
            raise NotFoundException(
                f"Assignment '{peak_assignment_id}' not found for sample "
                f"'{sample.sample_item_name}'"
            )
        member, anchor = found
        if member.owner_batch_peak_id:
            owner = await fold_member(
                session, sample_item_id, member.owner_batch_peak_id
            )
            if owner is not None:
                member, anchor = owner
        identity = resolve_candidate(anchor.candidates, member.candidate)
        entry: dict[str, Any] = {
            "peak_assignment_id": fold_assignment_id(anchor.batch_peak_id),
            "sample_peak_id": str(member.sample_peak_id),
            "assigned_formula": identity.get("formula"),
            "ionization_mechanism_id": identity.get("ionization_mechanism_id"),
            "ion_formula": identity.get("ion_formula"),
            "fit_score": member.fit_score,
            "measured_fit_score": None,
            "plausibility": None,
            "evidence": None,
            "mz_error_ppm": None,
            "abundance_error": None,
            "isotopologues": [],
        }
    formula = entry["assigned_formula"]
    mechanism_id = entry["ionization_mechanism_id"]
    if not formula:
        entry["blocked_reason"] = "the peak carries no formula to measure"
        return _envelope(entry, sample.sample_item_name)
    entry["plausibility"] = round(float(formula_plausibility(formula)), 4)
    entry["evidence"] = evidence_for(member.fit_score, formula)
    if not mechanism_id:
        entry["blocked_reason"] = (
            "the assignment names no adduct, and the ion is built from one"
        )
        return _envelope(entry, sample.sample_item_name)

    match_params = await default_match_params(sample_item_id)
    ion_by_seed, fit_by_ion, _errors, scored_df = await score_seeds(
        sample, {(formula, mechanism_id)}, match_params
    )
    ion_id = ion_by_seed.get((formula, mechanism_id))
    if ion_id is None or scored_df.empty or "target_ion_id" not in scored_df.columns:
        entry["blocked_reason"] = (
            "the ion could not be generated, or nothing in this sample's spectrum "
            "lies in its m/z windows"
        )
        return _envelope(entry, sample.sample_item_name)
    ion_rows = scored_df[scored_df["target_ion_id"].astype(str) == str(ion_id)]
    isotopologues = isotopologue_rows(ion_rows)
    entry["measured_fit_score"] = score_or_none(fit_by_ion.get(str(ion_id)))
    if entry["fit_score"] is None and entry["measured_fit_score"] is not None:
        entry["fit_score"] = entry["measured_fit_score"]
        entry["evidence"] = evidence_for(entry["fit_score"], formula)
    ion_formula = ion_rows["target_ion_formula"].iloc[0] if len(ion_rows) else None
    if isinstance(ion_formula, str) and ion_formula:
        entry["ion_formula"] = ion_formula
    entry["isotopologues"] = isotopologues
    m0 = next((iso for iso in isotopologues if iso["isotope_label"] == "M0"), None)
    if m0 is not None and m0["sample_peak_id"] == entry["sample_peak_id"]:
        entry["mz_error_ppm"] = m0["mz_error_ppm"]
        entry["abundance_error"] = m0["abundance_error"]
    else:
        entry["blocked_reason"] = (
            "the ion's monoisotopic peak did not land on this peak within the "
            "sample's m/z window"
        )
    return _envelope(entry, sample.sample_item_name)
