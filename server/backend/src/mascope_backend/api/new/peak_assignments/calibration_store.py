"""Backend calibration store (D6): load the assignment-confidence calibration from the DB.

The confidence calibration (score -> P(correct) Platt curve + per-adduct corroboration log-odds)
lives per instrument. This loader reads the active row from ``assignment_calibration`` for the
given instrument + fit-score version and returns a :class:`mascope_tools...Calibration`; when the
table has no active row it falls back to the in-code provisional curve
(``mascope_tools...calibration_for``). So a deployment can refit and override the shipped default
without a code change, while an empty store still behaves exactly as before.

``mascope_tools`` stays DB-free: it defines the ``Calibration`` value object and the pure
``apply_calibration`` / ``apply_corroboration`` math; this backend module is the only place that
knows about the DB table.

A stored curve also records the ionization setups its labels were scored under
(:class:`StoredCalibration`), since a curve is valid per reagent chemistry and the
``(instrument, score_version)`` key cannot say which. The lookup still answers with the
instrument's curve, but warns when a run's setup is not among them: the record-it form of
``docs/dev/ingest_routing_and_splitting.md`` phase 8 (the retired ionization note's 8.2),
before the setup becomes part of the key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, update

from mascope_backend.db import AssignmentCalibration, async_session
from mascope_backend.runtime import runtime
from mascope_tools.composition.calibration import Calibration, calibration_for


@dataclass(frozen=True)
class StoredCalibration(Calibration):
    """A curve read from the store, with the setups it was fit under.

    ``ionization_setups`` holds ``{"ionization_mode_id", "version"}`` records,
    each a version of an ionization mode some of the curve's labels were scored
    under. Empty where the row records none: a curve fit before setups were
    recorded, whose labels the migration could not trace to a sample.
    """

    ionization_setups: tuple[dict, ...] = field(default=())


def fit_under(
    calibration: Calibration | None, ionization_setup: dict | None
) -> bool | None:
    """Whether a curve was fit under the setup a run searches under.

    :return: True or False where both are known, None where the curve records
        no setups (the in-code provisional curve, an unrecorded fit) or the run
        has none (a sample bound under no mode).
    """
    setups = getattr(calibration, "ionization_setups", ())
    if not setups or ionization_setup is None:
        return None
    return ionization_setup in setups


#: (row, setup) pairs already warned about: once per process, not once per sample.
_warned: set[tuple[int, str, int | None]] = set()


def _to_calibration(row: AssignmentCalibration) -> Calibration:
    return Calibration(
        a=row.a,
        b=row.b,
        instrument=row.instrument,
        n_pos=row.n_pos,
        n_neg=row.n_neg,
        ece=row.ece,
        fit_utc=row.fit_utc.isoformat() if row.fit_utc else None,
        source=row.source,
        provisional=row.provisional,
        corroboration_weights=dict(row.corroboration_weights)
        if row.corroboration_weights
        else None,
    )


def _to_stored(row: AssignmentCalibration) -> StoredCalibration:
    plain = _to_calibration(row)
    return StoredCalibration(
        **{name: getattr(plain, name) for name in plain.__dataclass_fields__},
        ionization_setups=tuple(row.ionization_setups or ()),
    )


async def load_calibration(
    instrument: str | None,
    score_version: int,
    ionization_setup: dict | None = None,
) -> Calibration | None:
    """The calibration for ``(instrument, score_version)``: the active DB row if present, else
    the in-code provisional curve, else ``None`` (uncalibrated instrument).

    ``None`` stays a first-class answer -- callers must report the assignment uncalibrated rather
    than borrow another instrument's curve. A DB row always wins over the in-code default, so
    refitting a curve (a new active row) overrides the shipped provisional without code changes.

    A row is returned as a :class:`StoredCalibration`. Given the setup the run searches under,
    a curve fit under other setups only is still applied - it is the instrument's curve - but
    with a warning, once per curve and setup, rather than silently."""
    if not instrument:
        return None
    inst = str(instrument).lower()
    async with async_session() as session:
        row = (
            (
                await session.execute(
                    select(AssignmentCalibration)
                    .where(
                        AssignmentCalibration.instrument == inst,
                        AssignmentCalibration.score_version == score_version,
                        AssignmentCalibration.is_active.is_(True),
                    )
                    .order_by(AssignmentCalibration.created_utc.desc())
                )
            )
            .scalars()
            .first()
        )
    if row is not None:
        stored = _to_stored(row)
        if fit_under(stored, ionization_setup) is False:
            warned = (
                row.assignment_calibration_id,
                ionization_setup["ionization_mode_id"],
                ionization_setup.get("version"),
            )
            if warned not in _warned:
                _warned.add(warned)
                runtime.logger.warning(
                    f"The confidence calibration of '{inst}' was fit under "
                    f"{len(stored.ionization_setups)} ionization setup(s), not "
                    f"under version {ionization_setup.get('version')} of mode "
                    f"{ionization_setup['ionization_mode_id']}, which this run "
                    "searches under. It is applied all the same; refit it from "
                    "verifications of this setup's samples to calibrate them."
                )
        return stored
    return calibration_for(inst)


async def save_calibration(
    calibration: Calibration,
    score_version: int,
    ionization_setups: list[dict] | None = None,
) -> None:
    """Persist ``calibration`` as the new **active** row for its instrument + ``score_version``,
    deactivating any previously active row (the V2 recalibration write). Append-only: old rows are
    kept (``is_active=False``) as history, so a bad refit can be traced and rolled back.

    ``ionization_setups`` are the setups the labels were scored under, recorded on the row."""
    inst = (calibration.instrument or "").lower()
    async with async_session() as session:
        await session.execute(
            update(AssignmentCalibration)
            .where(
                AssignmentCalibration.instrument == inst,
                AssignmentCalibration.score_version == score_version,
                AssignmentCalibration.is_active.is_(True),
            )
            .values(is_active=False)
        )
        session.add(
            AssignmentCalibration(
                instrument=inst,
                score_version=score_version,
                a=calibration.a,
                b=calibration.b,
                n_pos=calibration.n_pos,
                n_neg=calibration.n_neg,
                ece=calibration.ece,
                source=calibration.source,
                provisional=calibration.provisional,
                corroboration_weights=(
                    dict(calibration.corroboration_weights)
                    if calibration.corroboration_weights
                    else None
                ),
                ionization_setups=ionization_setups or None,
                is_active=True,
                fit_utc=datetime.now(timezone.utc),
                created_utc=datetime.now(timezone.utc),
            )
        )
        await session.commit()
