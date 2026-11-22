"""What the batch decided of a sample's rows, as the run reads it onto them.

The series the batch detected on a sample's peaks, stamped on the rows that
read their formulas, and the batch's isotopologue lines a row is read as.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.batch_peaks import (
    SERIES_KEY,
    standing_series_record,
)
from mascope_backend.api.new.peak_assignments.config import MAX_ALTERNATIVES_CEILING
from mascope_backend.api.new.peak_assignments.engine import (
    DISPLACED_BY_CLAIM,
    ROLE_ISO_CHILD,
    ROLE_M0,
    reading_as_alternative,
)
from mascope_backend.api.new.peak_assignments.envelope_claims import isotopologue_names
from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    inherited_level,
    is_level,
)
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session


async def batch_series_by_peak(sample_item_id: str) -> dict[str, dict]:
    """The series records of the batch peaks a sample's peaks sit in.

    The batch's series detection (``batch_series.py``) records on a batch peak
    the homologous series that proposed its formula; a run of one of the
    batch's samples reads them here, by the peak each of its members is.

    :param sample_item_id: The sample.
    :return: Sample peak id -> the record of its batch peak, for the peaks
        whose batch peak carries one beside the formula it names
        (``batch_peaks.standing_series_record``).
    """
    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.sample_peak_id,
                    BatchPeak.provenance,
                    BatchPeak.consensus_formula,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeakOccurrence.sample_item_id == sample_item_id,
                    BatchPeak.provenance[SERIES_KEY].is_not(None),
                )
            )
        ).all()
    records: dict[str, dict] = {}
    for peak_id, provenance, formula in rows:
        record = standing_series_record(
            {"provenance": provenance, "consensus_formula": formula}
        )
        if record is not None:
            records[str(peak_id)] = record
    return records


def stamp_series(rows: Iterable[dict], series_by_peak: dict[str, dict]) -> int:
    """Copy the batch's series records onto the run's rows of the same reading.

    A record says a homologous series of the batch proposed this formula for
    this peak, through this mechanism; a row that reads the peak so carries it
    (``tiering.SERIES``), and the tiering pass counts it as a second
    observation of the row (``tiering.series_anchor``). A row that reads the
    peak another way does not: the series vouches for its formula, not for the
    peak. Only monoisotopic rows: an isotope line is judged through its owner.

    :param rows: The run's committed rows, written into.
    :param series_by_peak: :func:`batch_series_by_peak`'s answer.
    :return: How many rows carry a record.
    """
    stamped = 0
    for row in rows:
        if row.get("role") != ROLE_M0:
            continue
        record = series_by_peak.get(str(row.get("sample_peak_id")))
        if (
            record is None
            or record.get("formula") != row.get("assigned_formula")
            or record.get("ionization_mechanism_id")
            != row.get("ionization_mechanism_id")
        ):
            continue
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
            row["provenance"] = provenance
        provenance[SERIES_KEY] = dict(record)
        stamped += 1
    return stamped


def series_anchored(rows: Iterable[dict]) -> int:
    """How many rows carry a batch series' record (:func:`stamp_series`, which
    stamps monoisotopic rows alone): the count a run's tiering record keeps.

    Read on the judged ledger rather than at the stamp, since the stamp comes
    before the judging: a row the fragment claim then reads as the source's
    leaves the ledger, and its record with it.

    :param rows: The judged rows.
    :return: The count.
    """
    return sum(1 for row in rows if SERIES_KEY in (row.get("provenance") or {}))


#: The provenance block a sample's row carries once the batch's
#: merged-isotopologue reading rewrote it as an owner's line (step 4.2f): the
#: line, the batch peaks of the row and of its parent, the share the batch read
#: over its spectra, and the reading the row displaced.
BATCH_ISOTOPOLOGUE = "batch_isotopologue"

#: The provenance list an owner's row carries of the lines the batch's
#: reading rewrote as its isotopologues, one entry per row.
BATCH_ISOTOPOLOGUES = "batch_isotopologues"


@dataclass(frozen=True)
class BatchLine:
    """A line the batch read a merged row as: a parent's isotopologue.

    :param label: The line, as the envelope predictor names it (``13C``).
    :param mz_offset: Its m/z above the parent's monoisotopic line.
    :param share: Its predicted share of the parent's line.
    :param batch_peak_id: The batch peak of the row read as the line.
    :param parent_batch_peak_id: The batch peak of the parent.
    :param reading: What the batch read of the share over its spectra
        (``merged_isotopologues.ShareReading.to_json``).
    :param batch_peak_run_id: The batch run that read it.
    :param version: The version of the batch stage whose record a sample's
        run read the line off (``batch_decisions``), which the row cites;
        None for the batch's own rewrite, whose run names it.
    """

    label: str
    mz_offset: float
    share: float
    batch_peak_id: str
    parent_batch_peak_id: str
    reading: dict
    batch_peak_run_id: str | None = None
    version: int | None = None


def read_as_batch_isotopologue(row: dict, owner: dict, line: BatchLine) -> dict:
    """The values that rewrite a sample's row as its owner's isotopologue,
    where the batch read the row's peak as the owner's line (step 4.2f).

    The per-sample side of a merged row the batch strips: the sample's row on
    the line's peak becomes an isotopologue child of the sample's row on the
    parent's peak, so the sample's ledger agrees with the batch's. Written as
    step 2.4e's claims write a line (``envelope_claims``): in the owner's
    convention (:func:`envelope_claims.isotopologue_names`), measured by the
    owner's fit, with the reading it displaced first among its alternatives,
    where "use this" puts it back. The tier is the owner's, as a line the
    stages commit carries its owner's: the batch read the share over its
    spectra, which is no doubt a single file could raise. So is its
    identification level, as an isotopologue row shows its owner's
    (``identification_level.inherited_level``); an owner that states none, a
    row from before the level, leaves the line with none. The row is left as
    it is; the caller writes the values, onto a dict or a stored row.

    :param row: The sample's row on the line's peak.
    :param owner: The sample's row on the parent's peak.
    :param line: What the batch read the row as.
    :return: The row's new column values.
    """
    displaced = reading_as_alternative(row, DISPLACED_BY_CLAIM)
    previous = row.get("provenance") or {}
    owner_provenance = owner.get("provenance") or {}
    owner_mz = _finite(owner.get("sample_peak_mz"))
    line_mz = None if owner_mz is None else owner_mz + line.mz_offset
    label, ion_formula, isotope_formula = isotopologue_names(
        owner, line.label, line_mz if line_mz is not None else 0.0
    )
    peak_mz = _finite(row.get("sample_peak_mz"))
    intensity = _finite(row.get("sample_peak_intensity"))
    owner_intensity = _finite(owner.get("sample_peak_intensity"))
    observed = (
        intensity / owner_intensity
        if intensity is not None and owner_intensity
        else None
    )
    record = {
        "line": line.label,
        "batch_peak_id": line.batch_peak_id,
        "parent_batch_peak_id": line.parent_batch_peak_id,
        "predicted_mz": None if line_mz is None else round(line_mz, 6),
        "predicted_share": round(line.share, 6),
        "observed_share": None if observed is None else round(observed, 6),
        "reading": dict(line.reading),
        "batch_peak_run_id": line.batch_peak_run_id,
        **({} if line.version is None else {"version": line.version}),
        "displaced": {
            "role": row.get("role"),
            "assigned_formula": row.get("assigned_formula"),
            "ion_formula": row.get("ion_formula"),
            "ionization_mechanism_id": row.get("ionization_mechanism_id"),
            "isotope_label": row.get("isotope_label"),
            "source": row.get("source"),
            "tier": row.get("tier"),
            "evidence": previous.get("evidence"),
        },
    }
    alternatives = row.get("alternatives") or []
    if row.get("assigned_formula"):
        # A peak the run left unassigned had no reading to put back.
        alternatives = [displaced, *alternatives]
    owner_level = owner_provenance.get(IDENTIFICATION_LEVEL)
    level = (
        inherited_level(owner_level, str(owner.get("peak_assignment_id") or ""))
        if isinstance(owner_level, dict) and is_level(owner_level.get("level"))
        else None
    )
    return {
        "role": ROLE_ISO_CHILD,
        "assigned_formula": owner.get("assigned_formula"),
        "ion_formula": ion_formula,
        "ionization_mechanism_id": owner.get("ionization_mechanism_id"),
        "isotope_label": label,
        "isotope_formula": isotope_formula,
        "source": owner.get("source"),
        "fit_score": owner.get("fit_score"),
        "mz_error_ppm": (
            None
            if peak_mz is None or line_mz is None
            else (peak_mz - line_mz) / line_mz * 1e6
        ),
        "abundance_error": (
            None if observed is None or line.share <= 0 else observed / line.share - 1.0
        ),
        "tier": owner.get("tier"),
        "target_compound_id": owner.get("target_compound_id"),
        "target_ion_id": owner.get("target_ion_id"),
        "owner_peak_assignment_id": owner.get("peak_assignment_id"),
        "alternatives": alternatives[:MAX_ALTERNATIVES_CEILING] or None,
        "level": None if level is None else level["level"],
        "provenance": {
            key: owner_provenance[key]
            for key in ("plausibility", "evidence", "score_version")
            if key in owner_provenance
        }
        | {BATCH_ISOTOPOLOGUE: record}
        | ({} if level is None else {IDENTIFICATION_LEVEL: level}),
    }


def with_batch_isotopologue_line(owner: dict, row: dict, line: BatchLine) -> dict:
    """The owner's provenance with the line the batch rewrote recorded on it,
    so the rewrite is read on both rows: the row it took, its peak and batch
    peak, and the line. A line recorded for the same peak before is replaced.

    :param owner: The sample's row on the parent's peak.
    :param row: The sample's row it took as its line.
    :param line: What the batch read the row as.
    :return: The owner's new provenance; the owner is left as it is.
    """
    provenance = dict(owner.get("provenance") or {})
    entries = [
        entry
        for entry in provenance.get(BATCH_ISOTOPOLOGUES) or []
        if isinstance(entry, dict)
        and entry.get("sample_peak_id") != row.get("sample_peak_id")
    ]
    entries.append(
        {
            "peak_assignment_id": row.get("peak_assignment_id"),
            "sample_peak_id": row.get("sample_peak_id"),
            "line": line.label,
            "batch_peak_id": line.batch_peak_id,
            "parent_batch_peak_id": line.parent_batch_peak_id,
            "batch_peak_run_id": line.batch_peak_run_id,
            **({} if line.version is None else {"version": line.version}),
        }
    )
    provenance[BATCH_ISOTOPOLOGUES] = entries
    return provenance


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
