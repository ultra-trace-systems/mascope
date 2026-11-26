"""The API's reads of a sample's ledger.

The runs of a sample, its rows page by page, and one row's detail with the
batch readings, the isotopologue records and the known compounds its formula
names. Reads only: what a run wrote, served as the routes return it.
"""

from collections.abc import Sequence

from fastapi import status
from sqlalchemy import func, select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    CodedHTTPException,
    NotFoundException,
)
from mascope_backend.api.new.peak_assignments.batch_blend import blend_record_of
from mascope_backend.api.new.peak_assignments.batch_level import row_batch_level
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    continuity_reading,
    known_species_summary,
    row_batch_checks,
    row_co_variation,
    row_diagnostic_isotopologues,
    row_flat_series,
    row_persistence,
    row_stamped_isotopologue_of,
    standing_series_record,
    water_ladder_summary,
)
from mascope_backend.api.new.peak_assignments.batch_series import (
    read_batch_groups,
    with_sample_peaks,
)
from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.api.new.peak_assignments.fold_view import (
    derived_ledger,
    fold_id_target,
    fold_member,
    fold_run_record,
    is_fold_id,
    member_detail,
    member_target_id,
)
from mascope_backend.api.new.peak_assignments.identification_level import level_lines
from mascope_backend.api.new.peak_assignments.listing import reference_listing
from mascope_backend.api.new.peak_assignments.model_readers import (
    _row_calibration,
    provenance_with_calibration,
)
from mascope_backend.api.new.peak_assignments.peak_ids import load_peak_ids
from mascope_backend.api.new.peak_assignments.profiles import RESOLVED_PROFILE_KEY
from mascope_backend.api.new.peak_assignments.row_terms import MEASUREMENT_MODEL_KEY
from mascope_backend.api.new.peak_assignments.schemas import DEFAULT_PAGE_LIMIT
from mascope_backend.api.new.peak_assignments.target_listing import with_target_listings
from mascope_backend.api.new.peak_assignments.tiering import (
    LINE_ABSENT,
    PEAK_SEPARATION,
    PREDICTED_ENVELOPE,
    SEPARATED_THROUGH,
)
from mascope_backend.api.new.reference import service as reference_service
from mascope_backend.api.new.reference.licenses import reference_license_gate
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_reference.scope import SAMPLE_POLARITIES
from mascope_tools.composition.arbitration import CANDIDATE_DENSITY
from mascope_tools.composition.known_window import KnownWindow
from mascope_tools.composition.measurement_model import LEGACY_SCORING_KEY


# -------------------------------------------------------------------
# Read model
# -------------------------------------------------------------------


#: The keys of a run's scoring width, as the runs listing serves them.
SCORING_WIDTH_KEYS = ("sigma_ppm", "sigma_source", "mu_ppm", "mu_source")


def scoring_width(run: PeakAssignmentRun) -> dict:
    """The mass-error width a run scored at, and where each number came from.

    An imported run answers with what its engine disclosed. An in-app run
    answers from the measurement model it recorded (``measurement_model``):
    the width it judged a mass error in, which is the fitted width widened by
    the prediction's own spread, and its constant centre; a run from before
    the one record from its ``pattern_scoring`` snapshot. An import's config
    is opaque, so the same keys there are the engine's own business and are
    not read.

    :param run: The run.
    :return: ``sigma_ppm``, ``sigma_source``, ``mu_ppm`` and ``mu_source``,
        each None where the run recorded none.
    """
    if run.scoring_width is not None:
        width = run.scoring_width
    elif run.engine == IN_APP_ENGINE and isinstance(run.config, dict):
        model = run.config.get(MEASUREMENT_MODEL_KEY)
        width = (
            {**model, "sigma_ppm": model.get("width_ppm")}
            if isinstance(model, dict)
            else run.config.get(LEGACY_SCORING_KEY)
        )
    else:
        width = None
    if not isinstance(width, dict):
        width = {}
    return {key: width.get(key) for key in SCORING_WIDTH_KEYS}


def run_record(run: PeakAssignmentRun) -> dict:
    """The run as the runs listing serves it, its scoring width lifted out."""
    return {**run.to_dict(), **scoring_width(run)}


async def _run_peak_probes(session, runs: Sequence[PeakAssignmentRun]) -> dict:
    """One row's peak id for each completed run that has rows.

    The smallest ``sample_peak_id`` of each run, read in one query: a scalar
    ``min`` per run, which the ``(peak_assignment_run_id, sample_peak_id)``
    unique index answers with one index probe instead of a scan of the run's
    rows. One row decides the run, since every detection mints a fresh id for
    every peak and a run is written against one detection.

    :param session: An open session.
    :param runs: The runs being listed.
    :return: Run id -> peak id, for the completed runs with at least one row.
    """
    completed = [
        run.peak_assignment_run_id for run in runs if run.status == "completed"
    ]
    if not completed:
        return {}
    probe = (
        select(func.min(PeakAssignment.sample_peak_id))
        .where(
            PeakAssignment.peak_assignment_run_id
            == PeakAssignmentRun.peak_assignment_run_id
        )
        .scalar_subquery()
    )
    rows = await session.execute(
        select(PeakAssignmentRun.peak_assignment_run_id, probe).where(
            PeakAssignmentRun.peak_assignment_run_id.in_(completed)
        )
    )
    return {run_id: peak_id for run_id, peak_id in rows if peak_id is not None}


async def _peaks_current(sample, probes: dict) -> dict:
    """Whether each probed run's peak id is still one the sample has.

    The sample's peak ids are read once, and only when there is a run to
    check. A read that fails (a missing or unreadable peak file) leaves every
    run unflagged rather than failing the listing.

    :param sample: The sample view row.
    :param probes: Run id -> one of its rows' peak ids (``_run_peak_probes``).
    :return: Run id -> True or False; a run left out is null.
    """
    if not probes:
        return {}
    try:
        peak_ids = await load_peak_ids(sample)
    except Exception as error:
        runtime.logger.warning(
            f"Runs of sample '{sample.sample_item_id}' listed without "
            f"peaks_current: its peak ids could not be read: {error}"
        )
        return {}
    return {run_id: peak_id in peak_ids for run_id, peak_id in probes.items()}


@api_controller()
async def get_peak_assignment_runs(
    sample_item_id: str, include_decoys: bool = False
) -> dict:
    """
    Retrieve all peak assignment runs for a sample, newest first.

    A decoy run (``PeakAssignmentConfig.decoy``) is listed only on request.
    The Sample view and the SDK take the first completed run of this list as
    the sample's result, and a decoy is the engine's answer to a sample whose
    chemistry was made wrong on purpose: listed by default, the newest decoy
    would be read as the sample's ledger by every client that predates it.

    Each completed run says whether its rows still name the sample's peaks
    (``peaks_current``), checked from one row per run against the sample's
    peak ids, read once per listing.

    :param sample_item_id: Unique identifier of the sample item
    :param include_decoys: List the sample's decoy runs too, in their place by
        creation time; the comparison tool reads them so.
    :return: Dictionary with status, message, results count, and run records
    """
    sample = await fetch_sample(sample_item_id)

    async with async_session() as session:
        statement = select(PeakAssignmentRun).where(
            PeakAssignmentRun.sample_item_id == sample_item_id
        )
        if not include_decoys:
            statement = statement.where(PeakAssignmentRun.decoy.is_(None))
        runs = (
            (
                await session.execute(
                    statement.order_by(
                        PeakAssignmentRun.peak_assignment_run_utc_created.desc()
                    )
                )
            )
            .scalars()
            .all()
        )
        # The ledger derived from the batch peaks, for a sample the fold knows.
        # Listed LAST: the UI and the SDK take the first completed run, so a
        # real run keeps winning wherever one exists and the derived one is
        # the fallback for a sample that has none.
        derived = await fold_run_record(session, sample)

        probes = await _run_peak_probes(session, runs)

    current = await _peaks_current(sample, probes)
    data = [
        {**run_record(run), "peaks_current": current.get(run.peak_assignment_run_id)}
        for run in runs
    ]
    if derived is not None:
        data.append(derived)
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} peak assignment run"
            f"{'s' if len(data) != 1 else ''} "
            f"for sample '{sample.sample_item_name}'"
        ),
        "results": len(data),
        "data": data,
    }


# Ledger list projection: every scalar column of a row, but not `alternatives`
# - inspector-only JSON that never has to leave the database for a list read.
# `provenance` (also inspector detail) is still selected, only to be collapsed
# into the few scalars the ledger columns render; see _provenance_scalars.
_LEDGER_COLUMNS = tuple(
    column
    for column in PeakAssignment.__table__.columns
    if column.key != "alternatives"
)


#: The status a run holds while an external client is still uploading its rows.
#: Named here rather than imported from the import module so the read path does
#: not depend on the write path.
IMPORTING_STATUS = "importing"

#: Code carried in the error payload when a read addresses a run that is still
#: assembling. Part of the API contract: a client polling an import it launched
#: has to tell "not finished yet, ask again" apart from the other things a 409
#: on this surface can mean, and the run listing is what tells it when to stop.
RUN_STILL_ASSEMBLING_CODE = "run_still_assembling"


class RunStillAssemblingException(CodedHTTPException):
    """A read named a run whose ledger is still arriving (409).

    Only the *default* read resolves to the latest completed run; a read with an
    explicit ``peak_assignment_run_id`` serves that run whatever its status, and
    ``GET /sample/{id}/runs`` lists runs of every status - so an assembling
    import is reachable by id, and the import's own first response hands the
    client that id. Serving it would present a partial ledger as a ledger: the
    rows are real but the set is not, and nothing in the payload says so.

    Distinguished from the 'running' case on purpose. An in-app run writes its
    whole ledger in one insert at the end, so reading it mid-flight yields an
    empty result that is honest about itself; an import accumulates, so reading
    it mid-flight yields a subset that is not.

    :param peak_assignment_run_id: The run that was addressed.
    :param sample_item_name: The sample it belongs to, for the message.
    """

    error_code = RUN_STILL_ASSEMBLING_CODE

    def __init__(self, peak_assignment_run_id: str, sample_item_name: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Peak assignment run '{peak_assignment_run_id}' for sample "
                f"'{sample_item_name}' is still being imported, so its ledger "
                "is incomplete. Wait for the import to finish - the runs "
                "endpoint reports its status - or read the sample's latest "
                "completed run by omitting the run id."
            ),
        )


def _provenance_scalars(
    provenance: dict | None, run_calibration: dict | None = None
) -> dict:
    """Collapse a provenance blob into the scalars the ledger renders.

    The ledger table shows the evidence its tier was read off, an
    adduct-corroboration count and what a reference list calls the formula on
    every row, and the API serves the calibrated P(correct) with its
    provisional flag beside them (the app does not show it while the curve is
    provisional); everything else in provenance is per-peak inspector detail
    served by :func:`get_peak_assignment_detail`.

    ``evidence`` is here rather than in the inspector because it is what the tier
    chip displays. The chip used to show ``fit_score``, which stopped being the
    number that bucketed the row - and a tier beside a percentage that did not
    produce it is the one pairing guaranteed to be read as a contradiction.

    ``mass_z`` is here for a related reason and a different one: the ledger's
    ppm column states a distance without a scale, and the scale is the run's own
    fitted width. A reader scanning for the rows a run is least sure of, or
    checking why one carries a tier its evidence does not explain, needs the two
    together, and the second is on the run rather than the row.

    ``corroboration_channels`` is here because the corroboration marker beside it
    would otherwise be blank on nearly every row. ``corroboration_adducts`` counts
    the adducts a CURATED compound was matched through, so it reaches only rows
    Stage A claimed - 25 of one gate sample set's 2,062 committed rows, and none
    at all on six of the eight sets. The channel count is the same evidence read
    off the finished ledger instead (``cross_channel``), so it reaches every
    committed row; where both exist the second is the first plus whatever the
    untargeted stage committed of the same neutral, so it is never the smaller
    number. A reader cannot derive it from one page of a paginated ledger, which
    is what makes it a column rather than inspector detail.

    ``candidate_density`` is here because it is the other half of the ppm and
    evidence columns: how many formulas this peak's own evidence could not tell
    apart. A row can carry a strong fit and still be one of three the run could
    not separate, and nothing else on the ledger says so - the stored
    ``alternatives`` are capped, so counting those counts the cap.

    ``reference_listing`` is here because what a list calls the formula is the
    reading a reader asks of a row first, and without it the ledger took the
    inspector to answer: the first name the run matched from a list, that
    list, the lists' tags and how many names there are
    (:func:`~mascope_backend.api.new.peak_assignments.listing.reference_listing`),
    None on a row no list names. The read puts the row's target compound beside
    it, under ``target``
    (:func:`~mascope_backend.api.new.peak_assignments.target_listing.with_target_listings`):
    the provenance does not hold it, the row's ``target_compound_id`` does.

    ``run_calibration`` is the run's ``confidence_calibration``: the curve a
    calibrated row's ``p_correct`` was read off, recorded once per run rather
    than in every row. Which curve applies to this row is
    :func:`_row_calibration`'s to say, so the ledger and the detail agree.

    The four rule records (:func:`_rule_records`) are here because the gate
    card reads them for every committed row of a run, and a whole ledger's
    worth of detail reads took it about an hour over the gate sets.

    ``level_evidence`` and ``level_lift`` are the two lines beside the row's
    identification level, which is the row's own ``level`` column
    (:func:`~mascope_backend.api.new.peak_assignments.identification_level.level_lines`):
    what a reader may state of the row is a column, filtered and exported as
    the tier is, and the facts and rivals behind it stay inspector detail.
    """
    provenance = provenance or {}
    calibration = _row_calibration(provenance, run_calibration) or {}
    corroboration = provenance.get("corroboration") or {}
    channels = (provenance.get("cross_channel") or {}).get("channels")
    return {
        "evidence": provenance.get("evidence"),
        "p_correct": provenance.get("p_correct"),
        "p_correct_provisional": calibration.get("provisional"),
        "corroboration_adducts": corroboration.get("n_adducts"),
        "corroboration_channels": len(channels) if channels else None,
        "candidate_density": provenance.get(CANDIDATE_DENSITY),
        "mass_z": provenance.get("mass_z"),
        "reference_listing": reference_listing(provenance.get("reference_identities")),
        **_rule_records(provenance),
        **level_lines(provenance),
    }


def _rule_records(provenance: dict) -> dict:
    """What the tiering rules recorded of a row, as the ledger serves it.

    The gate card (``tooling/assignment_compare``) reads four things of every
    committed row of a run, each from a provenance block only the detail read
    serves: whether the row carries a tier reason, whether the mass gate names
    a corroboration (G7), how many asked lines of its predicted envelope are
    absent (G13) and whether its peak is unseparated with no separated partner
    channel (G14). One detail read per row is about 60 ms, and a dense set
    holds thousands of committed rows, so the card read each value off the
    paged ledger instead once the ledger served them. They are small derived
    values, not the blocks: ``predicted_envelope`` alone is a list of lines
    per row, and the blocks were most of a row's bytes.

    Each is None where the row holds no such record, so "not measured" stays
    apart from "measured and found nothing", as every other scalar here does.

    - ``tier_reason_rules``: the ``rule`` of each of ``tier_reasons``, in
      order; None where the row records none. The details stay in the
      detail read.
    - ``mass_gate_corroborated``: whether ``mass_gate.corroborated_by`` names a
      corroboration; None where the row has no ``mass_gate`` record.
    - ``envelope_absent_lines``: how many lines of ``predicted_envelope`` are
      ``absent``; None where the row has no predicted envelope.
    - ``peak_unseparated``: whether ``peak_separation`` says the instrument
      did not separate the peak and ``cross_channel.separated_through`` names
      no channel that commits the neutral on a separated peak; None where the
      run did not read the peak's width.

    :param provenance: The row's stored provenance.
    :return: The four values, by their ledger names.
    """
    reasons = provenance.get("tier_reasons")
    gate = provenance.get("mass_gate")
    envelope = provenance.get(PREDICTED_ENVELOPE)
    separation = provenance.get(PEAK_SEPARATION)
    through = (provenance.get("cross_channel") or {}).get(SEPARATED_THROUGH)
    return {
        "tier_reason_rules": (
            [
                str(reason.get("rule"))
                for reason in reasons
                if isinstance(reason, dict) and reason.get("rule")
            ]
            if isinstance(reasons, list) and reasons
            else None
        ),
        "mass_gate_corroborated": (
            bool(gate.get("corroborated_by")) if isinstance(gate, dict) else None
        ),
        "envelope_absent_lines": (
            sum(
                1
                for line in envelope.get("lines") or []
                if isinstance(line, dict) and line.get("state") == LINE_ABSENT
            )
            if isinstance(envelope, dict)
            else None
        ),
        "peak_unseparated": (
            separation.get("separated") is False and not through
            if isinstance(separation, dict)
            else None
        ),
    }


async def _ledger_run(
    session, sample, peak_assignment_run_id: str | None
) -> PeakAssignmentRun | None:
    """The run a sample-ledger read serves.

    The run named, or the sample's latest completed run that is not a decoy
    when none is. Shared by the ledger read and its verification queue, so the
    queue ranks the very rows the ledger shows.

    :param session: An open session.
    :param sample: The sample read.
    :param peak_assignment_run_id: The run asked for, or None for the latest.
    :raises NotFoundException: The run named is not one of the sample's.
    :raises RunStillAssemblingException: The run named is still being imported.
    :return: The run, or None where the sample has no completed run.
    """
    sample_item_id = sample.sample_item_id
    if peak_assignment_run_id is not None:
        run = await session.get(PeakAssignmentRun, peak_assignment_run_id)
        if run is None or run.sample_item_id != sample_item_id:
            raise NotFoundException(
                f"Peak assignment run '{peak_assignment_run_id}' not found "
                f"for sample '{sample.sample_item_name}'"
            )
        if run.status == IMPORTING_STATUS:
            raise RunStillAssemblingException(
                peak_assignment_run_id, sample.sample_item_name
            )
        return run
    return (
        await session.execute(
            select(PeakAssignmentRun)
            .where(
                PeakAssignmentRun.sample_item_id == sample_item_id,
                PeakAssignmentRun.status == "completed",
                # A decoy is read only by its id: it is no answer
                # about the sample, whose chemistry it made wrong.
                PeakAssignmentRun.decoy.is_(None),
            )
            .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@api_controller()
async def get_peak_assignments(
    sample_item_id: str,
    peak_assignment_run_id: str | None = None,
    tier: str | None = None,
    engine_tier: str | None = None,
    tier_disagrees: bool | None = None,
    role: str | None = None,
    source: str | None = None,
    level: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> dict:
    """
    Retrieve peaks-with-assignments for a sample.

    Returns the assignments of the requested run, or of the latest completed
    run when no run id is given. Optional filters narrow by confidence tier,
    peak role, or assignment source.

    A requested run that is still being imported is refused with 409 rather than
    served: its rows are real but its set is not, and the payload has no way to
    say so. See :class:`RunStillAssemblingException`.

    Paged, and slim: the ledger is deliberately complete - one row per detected
    peak - so a dense sample runs to tens of thousands of rows, and the
    `alternatives`/`provenance` JSON would be ~74% of the bytes while only the
    peak inspector reads it. Rows here carry the scalar fields (plus a few
    provenance-derived scalars); the full JSON detail of one assignment is
    served by :func:`get_peak_assignment_detail`. ``total`` reports the match
    count across all pages so a client knows when it has the whole run.

    :param sample_item_id: Unique identifier of the sample item
    :param peak_assignment_run_id: Specific run to read; defaults to the
        latest completed run
    :param tier: Optional filter by confidence tier
    :param engine_tier: Optional filter by the producing engine's own tier
    :param tier_disagrees: Optional filter on whether the engine's own tier
        differs from this server's; rows carrying no engine tier match neither
        answer
    :param role: Optional filter by peak role
    :param source: Optional filter by assignment source (database/untargeted)
    :param level: Optional filter by identification level (``4a``, ``5a``,
        ...); a row derived from the batch ledger carries none and matches no
        level
    :param limit: Maximum rows to return in this page
    :param offset: Rows to skip, for paging
    :return: Dictionary with status, message, total, and slim assignment rows.
        Run identity is on each row (peak_assignment_run_id); run metadata is
        served by the runs endpoint.
    """
    sample = await fetch_sample(sample_item_id)

    derived_filters = dict(
        tier=tier,
        engine_tier=engine_tier,
        tier_disagrees=tier_disagrees,
        role=role,
        source=source,
        level=level,
        limit=limit,
        offset=offset,
    )
    async with async_session() as session:
        if is_fold_id(peak_assignment_run_id):
            # The derived run's id names the sample it is derived for.
            derived = (
                await derived_ledger(session, sample, **derived_filters)
                if fold_id_target(peak_assignment_run_id) == sample_item_id
                else None
            )
            if derived is None:
                raise NotFoundException(
                    f"Peak assignment run '{peak_assignment_run_id}' not found "
                    f"for sample '{sample.sample_item_name}'"
                )
            return derived
        run = await _ledger_run(session, sample, peak_assignment_run_id)

        if run is None:
            # No run - but the batch ledger may still know the sample.
            derived = await derived_ledger(session, sample, **derived_filters)
            if derived is not None:
                return derived
            return {
                "status": "success",
                "message": (
                    f"No completed peak assignment runs exist for sample "
                    f"'{sample.sample_item_name}'"
                ),
                "results": 0,
                "total": 0,
                "data": [],
            }

        filters = [PeakAssignment.peak_assignment_run_id == run.peak_assignment_run_id]
        if tier:
            filters.append(PeakAssignment.tier == tier)
        if engine_tier:
            filters.append(PeakAssignment.engine_tier == engine_tier)
        if tier_disagrees is not None:
            # A row carrying no engine verdict is excluded from BOTH answers:
            # silence is not agreement, and folding it into `False` would report
            # every in-app row as an engine that concurred.
            filters.append(PeakAssignment.engine_tier.is_not(None))
            filters.append(
                PeakAssignment.engine_tier != PeakAssignment.tier
                if tier_disagrees
                else PeakAssignment.engine_tier == PeakAssignment.tier
            )
        if role:
            filters.append(PeakAssignment.role == role)
        if source:
            filters.append(PeakAssignment.source == source)
        if level:
            filters.append(PeakAssignment.level == level)

        total = (
            await session.execute(
                select(func.count()).select_from(PeakAssignment).where(*filters)
            )
        ).scalar_one()

        # Ordered by m/z with the primary key as a tiebreak: two peaks can share
        # an m/z, and paging over an ordering that does not break such ties can
        # return one of them on both pages and the other on neither.
        query = (
            select(*_LEDGER_COLUMNS)
            .where(*filters)
            .order_by(PeakAssignment.sample_peak_mz, PeakAssignment.peak_assignment_id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await session.execute(query)).all()
        # Read inside the session that loaded the run: outside it the instance
        # is detached, and an attribute that has been expired there is a lazy
        # load, which on an async session raises rather than reloading.
        run_calibration = run.confidence_calibration
        # The batch peak each row's peak folded into, so the sample ledger can
        # plot a species in the batch chart. One indexed lookup per page; a
        # peak the ledger does not hold reads as None.
        peak_ids = [str(row.sample_peak_id) for row in rows]
        anchor_by_peak: dict[str, str] = {}
        if peak_ids:
            anchor_by_peak = {
                str(peak_id): batch_peak_id
                for peak_id, batch_peak_id in (
                    await session.execute(
                        select(
                            BatchPeakOccurrence.sample_peak_id,
                            BatchPeakOccurrence.batch_peak_id,
                        ).where(
                            BatchPeakOccurrence.sample_item_id == sample_item_id,
                            BatchPeakOccurrence.sample_peak_id.in_(peak_ids),
                        )
                    )
                ).all()
            }

    data = []
    for row in rows:
        record = row._asdict()
        record.update(
            _provenance_scalars(record.pop("provenance", None), run_calibration)
        )
        record["batch_peak_id"] = anchor_by_peak.get(str(record.get("sample_peak_id")))
        data.append(record)
    # What each row's target compound declares, beside what a list calls its
    # formula: the compound's name and InChIKey, read now, as the names are.
    async with async_session() as session:
        await with_target_listings(session, data)
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} of {total} peak assignment"
            f"{'s' if total != 1 else ''} "
            f"for sample '{sample.sample_item_name}'"
        ),
        "results": len(data),
        "total": total,
        "data": data,
    }


async def _batch_peak_of(
    session, sample_item_id: str, sample_peak_id: object
) -> BatchPeak | None:
    """The batch peak a sample's peak sits in, or None: one indexed scan over
    the sample's members, as the ledger's ``batch_peak_id`` is looked up."""
    if sample_peak_id is None:
        return None
    return (
        await session.execute(
            select(BatchPeak)
            .join(
                BatchPeakOccurrence,
                BatchPeakOccurrence.batch_peak_id == BatchPeak.batch_peak_id,
            )
            .where(
                BatchPeakOccurrence.sample_item_id == sample_item_id,
                BatchPeakOccurrence.sample_peak_id == str(sample_peak_id),
            )
            .order_by(BatchPeak.batch_peak_id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _with_isotopologue_records(
    session, row: dict, anchor: BatchPeak | None
) -> None:
    """Put the batch's isotopologue stamping records on a sample's detail row.

    ``batch_stamped_isotopologue_of``: the stamp on the member the row's peak
    is, with the parent's peak in this sample (``parent_sample_peak_id``) so
    the inspector can focus it; None where the peak carries no stamp there.
    ``batch_diagnostic_isotopologues``: the parent's own record, on a
    monoisotopic row that reads it (:func:`row_diagnostic_isotopologues`).
    """
    stamp = row_stamped_isotopologue_of(row, anchor)
    if stamp is not None:
        stamp["parent_sample_peak_id"] = (
            await session.execute(
                select(BatchPeakOccurrence.sample_peak_id).where(
                    BatchPeakOccurrence.batch_peak_id == stamp["batch_peak_id"],
                    BatchPeakOccurrence.sample_item_id == row.get("sample_item_id"),
                )
            )
        ).scalar_one_or_none()
    row["batch_stamped_isotopologue_of"] = stamp
    row["batch_diagnostic_isotopologues"] = row_diagnostic_isotopologues(row, anchor)


async def _with_batch_readings(
    session, row: dict, anchor: BatchPeak | None, sample_item_id: str
) -> None:
    """Put the batch's readings of the batch peak a sample's row sits in on its
    detail, as the batch ledger serves them (the ``standing_*`` readers), and
    the groups that batch peak is a member of.

    ``batch_series``: the standing series record, with its ``root`` as a group
    member (None where no group lists it). ``batch_continuity``: the
    ratio-stability grade, ``partial`` and ``stale``
    (``batch_peaks.continuity_reading``). ``batch_known_species``,
    ``batch_water_ladder`` and ``batch_blend``: the ledger's records.
    ``batch_level``: the batch's level beside the row's own, where the
    batch's isotope checks move it (``batch_level.row_batch_level``); None
    elsewhere, a row derived from the ledger included.
    ``batch_groups``: the batch's groups the batch peak is in
    (``batch_series.batch_groups``), each member with the sample's own peak
    at it (``sample_peak_id``). All are about the batch peak, whatever the row
    reads its peak as, and None where the peak sits in none.

    Three reads whatever the groups hold: the batch's anchors, the members'
    mechanisms and the sample's peaks at them.
    """

    if anchor is None:
        for key in (
            "batch_series",
            "batch_continuity",
            "batch_known_species",
            "batch_water_ladder",
            "batch_blend",
            "batch_groups",
        ):
            row[key] = None
        return
    groups = await read_batch_groups(
        session, anchor.sample_batch_id, member=anchor.batch_peak_id
    )
    await with_sample_peaks(session, groups, sample_item_id)
    series = standing_series_record(anchor)
    if series is not None:
        root = next(
            (
                member
                for group in groups
                for member in group["members"]
                if member["batch_peak_id"] == series.get("root_batch_peak_id")
            ),
            None,
        )
        series = {**series, "root": root}
    row["batch_series"] = series
    row["batch_continuity"] = continuity_reading(anchor)
    row["batch_known_species"] = known_species_summary(anchor)
    row["batch_water_ladder"] = water_ladder_summary(anchor)
    row["batch_blend"] = blend_record_of(anchor)
    row["batch_groups"] = groups
    row["batch_level"] = row_batch_level(row, anchor)


@api_controller()
async def get_peak_assignment_detail(
    sample_item_id: str,
    peak_assignment_id: str,
) -> dict:
    """
    Retrieve one assignment in full, including the inspector-only JSON detail.

    The complement of :func:`get_peak_assignments`: the list serves a slim
    projection of the whole run, and this serves the `alternatives` and
    `provenance` of a single assignment when a user selects its peak.

    :param sample_item_id: Unique identifier of the sample item
    :param peak_assignment_id: Unique identifier of the assignment
    :return: Dictionary with status, message, and the one full assignment row
    """
    sample = await fetch_sample(sample_item_id)
    polarity = SAMPLE_POLARITIES.get(sample.polarity)

    async with async_session() as session:
        if is_fold_id(peak_assignment_id):
            found = await fold_member(
                session, sample_item_id, fold_id_target(peak_assignment_id)
            )
            if found is None:
                raise NotFoundException(
                    f"Assignment '{peak_assignment_id}' not found for sample "
                    f"'{sample.sample_item_name}'"
                )
            derived = member_detail(*found)
            derived["batch_co_variation"] = row_co_variation(derived, found[1])
            derived["batch_checks"] = row_batch_checks(derived, found[1])
            derived["batch_persistence"] = row_persistence(derived, found[1])
            derived["batch_flat_series"] = row_flat_series(derived, found[1])
            await _with_isotopologue_records(session, derived, found[1])
            await _with_batch_readings(session, derived, found[1], sample_item_id)
            await with_target_listings(
                session, [derived], lambda _row: member_target_id(*found)
            )
        else:
            derived = None
            assignment = await session.get(PeakAssignment, peak_assignment_id)
            if assignment is None or assignment.sample_item_id != sample_item_id:
                raise NotFoundException(
                    f"Assignment '{peak_assignment_id}' not found for sample "
                    f"'{sample.sample_item_name}'"
                )
            record = assignment.to_dict()
            anchor = await _batch_peak_of(
                session, sample_item_id, record.get("sample_peak_id")
            )
            record["batch_co_variation"] = row_co_variation(record, anchor)
            record["batch_checks"] = row_batch_checks(record, anchor)
            record["batch_persistence"] = row_persistence(record, anchor)
            record["batch_flat_series"] = row_flat_series(record, anchor)
            await _with_isotopologue_records(session, record, anchor)
            await _with_batch_readings(session, record, anchor, sample_item_id)
            run = await session.get(
                PeakAssignmentRun, assignment.peak_assignment_run_id
            )
            run_calibration = run.confidence_calibration if run is not None else None
            run_config = run.config if run is not None else None

    # The names are looked up after the session closes: the lookup opens its own,
    # and a read should not hold two connections for one row.
    if derived is not None:
        # A row derived from the batch ledger has no run, so no run recorded the
        # ceiling it would have matched under: each source's own window bounds it.
        return {
            "status": "success",
            "message": (
                f"Retrieved batch-derived assignment '{peak_assignment_id}' "
                f"for sample '{sample.sample_item_name}'"
            ),
            "results": 1,
            "data": [await with_known_compounds(derived, polarity=polarity)],
        }

    # The calibration the run applied is recorded once on the run; fold it back
    # into the row so the provenance keeps the shape the inspector and the SDK
    # read. The detail row is a superset of the list row, so the flattened
    # scalars are carried here too and a client can treat it as a drop-in
    # replacement.
    record["provenance"] = provenance_with_calibration(
        record.get("provenance"), run_calibration
    )
    record.update(_provenance_scalars(record["provenance"], run_calibration))
    async with async_session() as session:
        await with_target_listings(session, [record])
    return {
        "status": "success",
        "message": (
            f"Retrieved assignment '{peak_assignment_id}' for sample "
            f"'{sample.sample_item_name}'"
        ),
        "results": 1,
        "data": [
            await with_known_compounds(
                record, polarity=polarity, ceiling=_recorded_ceiling(run_config)
            )
        ],
    }


def _recorded_ceiling(run_config) -> KnownWindow | None:
    """The ceiling a run's chemistry context put over every reference source.

    :param run_config: A run's stored config, or None.
    :return: The window the run recorded, or None where it recorded none - an
        imported run, or a context that sets no ceiling.
    """
    resolved = (run_config or {}).get(RESOLVED_PROFILE_KEY) or {}
    window = resolved.get("known_window")
    if not isinstance(window, dict):
        return None
    try:
        return KnownWindow.from_json(window)
    except Exception:  # noqa: BLE001 - an unreadable record bounds nothing
        return None


async def with_known_compounds(
    record: dict,
    *,
    polarity: str | None = None,
    ceiling: KnownWindow | None = None,
) -> dict:
    """Name the reference-list compounds a row's formulas are listed as.

    Looked up when the row is read rather than recorded by the run, and for
    every formula the row shows - the committed one, each close alternative and
    each other reading of its ion - so a formula a list holds is named wherever
    it appears. A run records the identities of the list formulas it MATCHED
    (``provenance.reference_identities``); this answers the wider question of
    which formulas the lists hold that a run of this sample could have matched,
    including ones the run reached through the formula search, on runs of any
    build and of any engine. A list names candidates; a formula match is not an
    identification.

    Scoped as Stage A matches (``reference_service.known_listings``): the
    licences the deployment matches against (:func:`reference_license_gate`),
    the sample's polarity, each source's window under the run's ceiling, and a
    radical only from a source that allows radicals. A lookup that fails leaves
    the row as it was: the names are a reading aid, and the row reads without
    them.

    :param record: One full assignment row, modified in place.
    :param polarity: The sample's polarity, ``positive`` or ``negative``.
    :param ceiling: The ceiling the run's chemistry context recorded, or None.
    :return: The row, with ``known_compounds`` and ``known_compounds_total`` on
        it, and on each alternative a list holds: the names, at most as many as a
        run keeps, and how many records name the formula in all.
    """
    alternatives = [
        alternative
        for alternative in record.get("alternatives") or []
        if isinstance(alternative, dict)
    ]
    formulas = [
        str(formula)
        for formula in (
            record.get("assigned_formula"),
            *(alternative.get("assigned_formula") for alternative in alternatives),
        )
        if formula
    ]
    if not formulas:
        return record
    try:
        listings = await reference_service.known_listings(
            formulas,
            licenses=reference_license_gate(),
            ceiling=ceiling,
            polarity=polarity,
        )
    except Exception as error:  # noqa: BLE001 - a name lookup never fails a read
        runtime.logger.debug(f"Reference names skipped on an assignment read: {error}")
        return record

    if record.get("assigned_formula"):
        listing = listings.get(str(record["assigned_formula"])) or {}
        record["known_compounds"] = listing.get("identities", [])
        record["known_compounds_total"] = listing.get("total", 0)
    for alternative in alternatives:
        listing = listings.get(str(alternative.get("assigned_formula")))
        if listing:
            alternative["known_compounds"] = listing["identities"]
            alternative["known_compounds_total"] = listing["total"]
    return record
