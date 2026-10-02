"""
Learn which chemistry an acquisition method runs, from the files that route.

Every file that binds on a rung above the method binding - a declaration, a
person's choice, or its filename token - says one true thing about its
method: that method, measuring that way, ran that chemistry. Recording it is
what eventually lets a file route with no token at all
(``docs/dev/ingest_routing_and_splitting.md``, section 5.3).

**Learning can never cost a file its processing.** It runs on every ingest
and is nobody's dependency: :func:`learn_method_bindings` reports failures to
the log and returns, and the caller is not asked to guard it.

**Reading them back is off unless a deployment asks.** With
``backend.method_binding = "route"``, :func:`resolve_modes_by_method_binding`
binds a file no token names to what its method has been seen running; with
``"shadow"``, the default, the rows are written and never read. The rung sits
below the token either way, so it can only ever reach a file that parks.

**A key routes only while its history agrees on one chemistry.** Each
observation adds its chemistry to the row's ``chemistry_keys``; a second one
marks the row ``ambiguous`` and it never routes again. That is not the same
as the disagreement count, which measures how often it has happened: a key
that was never unanimous must not become a routing binding in the first
place. On the production fleet this holds back about one Orbitrap method key
in forty, and nearly every TOF one - which is the point, since a TOF file's
method name is a constant that separates nothing.

**Agreeing on the chemistry does not settle the row.** Two mode rows can
name one chemistry - a site that could not edit a mode in use made a second
row for the same reagent - and its files bind to the newer one from then on.
A binding that kept the first row it saw would route future files to a row
the site has stopped using, while every state on it read healthy, because by
the measure those states use it is. So the row follows the newest
observations once :data:`REPOINT_AFTER` of them in a row agree
(:func:`follow_row`), and a single re-bound file moves nothing.
"""

from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mascope_backend.binding_rungs import LEARNING_SOURCES
from mascope_backend.db import IonizationMode, MethodBinding, SampleFile, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.method_keys import (
    METHOD_KEY_COLUMN,
    SIGNATURE_CLASS_COLUMN,
    binding_digest,
    chemistry_key,
    clipped,
    instrument_key,
    method_key,
    signature_class,
)
from mascope_backend.runtime import runtime


#: Consecutive observations that must name the same other mode row before a
#: binding follows them to it.
#:
#: Three, because the two ways of being wrong cost different amounts (section
#: 5.3, "Why three"). Moving too eagerly is the expensive error: one corrected
#: file would drag a whole method's routing with it. Moving too slowly is
#: nearly free, because this rung sits below the filename token - a file with
#: a token is unaffected either way, and a file without one routes to another
#: row of the same chemistry, which is where it would have parked before the
#: rung existed. Three is the smallest count that no single re-bind, and no
#: pair of them on one afternoon, can reach, and a method in daily use still
#: reaches it within a day.
REPOINT_AFTER = 3

#: The counts a run that learned nothing returns. ``repeated`` is an
#: observation a file had already made; ``no_signature`` a file whose reader
#: normally records a census but took none, so what it measured is unknown.
_NOTHING_LEARNED = {
    "created": 0,
    "refreshed": 0,
    "ambiguous": 0,
    "repeated": 0,
    "no_signature": 0,
}


def follow_row(
    current: str | None,
    candidate: str | None,
    n_candidate: int,
    observed: str,
) -> tuple[str, str | None, int]:
    """Where a binding points after one more observation of its chemistry.

    Only for an observation that agrees on the chemistry: a new chemistry
    makes the key ambiguous instead, and an ambiguous key routes nothing, so
    there is no point in moving it.

    **Unanimity on the chemistry does not settle the row.** A site that could
    not edit a mode in use made a second row for the same reagent, and its
    files bind to that one from then on. A binding that kept the first row it
    ever saw would go on routing future files to a row the site has stopped
    using - with `state` reading `learned` and `n_disagreements` reading zero,
    because by the measure those use it is healthy. That is what the first
    fleet measurement found (section 5.7), and this is the answer to it: the
    row follows the newest observations once :data:`REPOINT_AFTER` of them in
    a row agree.

    Pure, and shared with the backfill script, which folds the whole history
    oldest first and so must apply exactly this rule to end up where live
    learning would have.

    :param current: The mode the binding points at, or None when the mode it
        pointed at has been deleted.
    :param candidate: The mode the recent observations have been naming, or
        None when they have agreed with ``current``.
    :param n_candidate: How many in a row have named ``candidate``.
    :param observed: The mode this observation names.
    :return: The mode to point at, the candidate to carry, and the length of
        its run. A run of 0 with no candidate is the settled state.
    :rtype: tuple[str, str | None, int]
    """
    if current is None:
        # The row points nowhere and therefore routes nothing, so there is
        # nothing to drag away from: this observation supplies a mode for the
        # chemistry the row already holds.
        return observed, None, 0
    if observed == current:
        # Agreement. Any run toward another row is broken, which is what
        # makes the threshold a count of the LAST few observations rather
        # than of all the ones that ever disagreed.
        return current, None, 0
    run = n_candidate + 1 if observed == candidate else 1
    if run >= REPOINT_AFTER:
        return observed, None, 0
    return current, observed, run


def method_binding_mode() -> str:
    """What this deployment does with method bindings.

    Read from ``method_binding`` in the runtime ``[backend]`` config:
    ``"shadow"`` (the default) learns them and routes nothing on them,
    ``"off"`` records nothing at all, ``"route"`` learns them and binds a
    file no token names to what its method has been seen running.

    :return: ``"shadow"``, ``"off"`` or ``"route"``.
    :rtype: str
    """
    return getattr(runtime.config, "method_binding", "shadow")


def routes_on_method_binding() -> bool:
    """Whether this deployment lets a binding bind a file.

    A separate question from the mode, because one value answers both: under
    ``"route"`` the learner goes on recording exactly as it did, and only the
    rung is added. Nothing about what is learned depends on this.
    """
    return method_binding_mode() == "route"


class MethodRouting(NamedTuple):
    """One polarity's answer from the method binding rung.

    The binding's id travels with the mode because the item records both:
    which rung bound it, and the row that did, so an item bound by a binding
    that has since moved is a query rather than a reconstruction
    (``sample_item.method_binding_id``).
    """

    mode: IonizationMode
    binding_id: str


async def resolve_modes_by_method_binding(
    sample_file: SampleFile, streams: list[dict] | None
) -> tuple[list[MethodRouting], str | None]:
    """Bind a file to the chemistry its acquisition method has been seen running.

    Rung 4 of the ladder (section 5.2), and the whole of what
    ``backend.method_binding = "route"`` switches on. Tried only for a file
    **no token names**: a file whose tokens match ambiguously is a
    configuration to fix, and no binding stands in for that.

    One binding per polarity, because the key carries a signature class and a
    class describes one polarity. Every polarity must answer, as under the
    token rule - a file half of whose polarities had a chemistry would
    otherwise be bound for one and silently lose the other.

    **Six guards, each of them a way this key could be trusted too far**
    (section 5.3):

    - a method name that never varies is no name, so it recognises nothing;
    - a file whose scans were not recorded has no signature class to key on,
      and a stand-in would key it apart from the files that carry one;
    - a key seen with more than one chemistry has separated nothing;
    - a binding whose mode has been deleted points nowhere;
    - a mode belonging to another instrument is not this instrument's answer,
      which is what scoping a mode means (#1463);
    - a mode whose polarity has been edited since the binding learned it is
      not what this polarity measured. The key carries a signature class,
      which describes one polarity, and the class does not change when
      somebody edits the mode - so the binding still resolves and its mode is
      the wrong answer.

    :param sample_file: The file to bind.
    :param streams: Its scan-stream census, as
        :func:`~...process.status.read_scan_streams` returns it.
    :return: One routing per polarity and None, or no routings and the reason
        there are none, as a sentence for the file's processing detail.
    :rtype: tuple[list[MethodRouting], str | None]
    """
    key = method_key(sample_file.method_file)
    if not key:
        return [], (
            "its acquisition method reports no name of its own, so there is "
            "nothing to recognise it by"
        )

    routings: list[MethodRouting] = []
    async with async_session() as session:
        for polarity in sample_file.polarity or "":
            signature = signature_class(streams, polarity, sample_file.instrument_type)
            if signature is None:
                return [], (
                    "what its scans measured was not recorded, so its "
                    "acquisition method cannot be recognised"
                )
            found = (
                await session.execute(
                    select(MethodBinding, IonizationMode)
                    .join(
                        IonizationMode,
                        MethodBinding.ionization_mode_id
                        == IonizationMode.ionization_mode_id,
                    )
                    .where(
                        MethodBinding.binding_key
                        == binding_digest(sample_file.instrument, key, signature)
                    )
                )
            ).first()
            if found is None:
                # No row, or a row whose mode has been deleted: the join drops
                # both, and neither can bind a file.
                return [], (
                    "its acquisition method has not been seen running a "
                    "chemistry on this instrument"
                )
            binding, mode = found
            # Judged on the chemistries, not on `state`. The two say the same
            # thing today and would not once a person can confirm a binding.
            if len(binding.chemistry_keys or []) != 1:
                return [], (
                    "its acquisition method has been seen running more than "
                    "one chemistry, so the method does not say which"
                )
            scope = instrument_key(mode.instrument)
            if scope is not None and scope != instrument_key(sample_file.instrument):
                return [], (
                    "the chemistry its acquisition method was seen running "
                    "belongs to another instrument"
                )
            if mode.ionization_mode_polarity != polarity:
                # The mode was edited after the binding learned it. The key
                # describes one polarity, so a mode of the other is not what
                # this polarity measured.
                return [], (
                    "the chemistry its acquisition method was seen running is "
                    f"no longer recorded for polarity {polarity}"
                )
            routings.append(
                MethodRouting(mode=mode, binding_id=binding.method_binding_id)
            )

    if not routings:
        # The file records no polarity at all. The token rule refuses such a
        # file for the same reason: there is nothing to bind one mode to.
        return [], "its polarities were not recorded"
    return routings, None


async def learn_method_bindings(
    sample_file: SampleFile,
    bound_modes: list[IonizationMode],
    source: str,
    streams: list[dict] | None,
    recorded: set[str] | None = None,
) -> dict[str, int]:
    """Record what the modes this file bound to say about its method.

    One observation per (file, polarity): today a file binds one mode per
    polarity, and each polarity is measured its own way, so each is its own
    key. When streams are bound individually this becomes one per stream and
    nothing else changes.

    Never raises. A file's processing does not depend on the recording
    succeeding, and this runs on every ingest.

    :param sample_file: The file that just bound.
    :param bound_modes: The modes it bound to, one per polarity.
    :param source: The rung that bound it, one of :data:`LEARNING_SOURCES`.
    :param streams: The file's scan-stream census from
        :func:`~...process.status.read_scan_streams`: the streams, ``[]`` when
        the file records none, or ``None`` when its ``.props`` could not be
        read. Required, because the difference between the last two decides
        whether a file that teaches nothing is routine or a fault.
    :param recorded: The binding keys this pipeline run has already taught,
        added to here. The pipeline shares one set across the attempts of a
        run, so a file whose later stages fail and retry - tens of seconds
        apart, with other files of the same key arriving in between - teaches
        its method once and not once per attempt.
    :return: Counts of the rows created, refreshed and marked ambiguous.
    :rtype: dict[str, int]
    """
    counts = dict(_NOTHING_LEARNED)
    if method_binding_mode() == "off":
        return counts
    if source not in LEARNING_SOURCES:
        runtime.logger.debug(
            f"Not learning a method binding from {source!r}: only "
            f"{', '.join(LEARNING_SOURCES)} are above the binding rung"
        )
        return counts

    try:
        key = method_key(sample_file.method_file)
        seen_at = datetime.now(timezone.utc)
        observations = []
        for mode in bound_modes:
            signature = signature_class(
                streams, mode.ionization_mode_polarity, sample_file.instrument_type
            )
            if signature is None:
                # A reader that normally records a census took none for this
                # file, so what it measured is not known. Keying it on
                # anything else would split this method's history between the
                # guess and the census later files carry.
                #
                # WARNING only when the .props could not be READ, which is a
                # fault. A file that simply records no census is ordinary -
                # every Orbitrap file predating the census does, and
                # re-processing those in bulk is routine - and warning on each
                # would open a monitoring event per file telling someone to
                # check a .props that is fine.
                _report_no_signature(
                    sample_file, unreadable=streams is None, recorded=recorded
                )
                counts["no_signature"] += 1
                continue
            digest = binding_digest(sample_file.instrument, key, signature)
            observations.append((digest, signature, mode))
        # Locked in digest order, not the file's polarity order. A
        # polarity-switching method whose files report their polarities the
        # other way round would otherwise have two ingests take the same two
        # rows in opposite orders, which is a deadlock; the loser's recording
        # is dropped, and the one thing this must never do is cost a file
        # anything.
        observations.sort(key=lambda observation: observation[0])
        written: list[str] = []
        async with async_session() as session:
            for digest, signature, mode in observations:
                if recorded is not None and digest in recorded:
                    counts["repeated"] += 1
                    continue
                outcome = await _observe(
                    session,
                    digest=digest,
                    instrument=sample_file.instrument,
                    key=key,
                    signature=signature,
                    mode=mode,
                    source=source,
                    seen_at=seen_at,
                    sample_file_id=sample_file.sample_file_id,
                )
                counts[outcome] = counts.get(outcome, 0) + 1
                written.append(digest)
            await session.commit()
        # Only after the commit. A failure here - the commit itself, or the
        # second _observe of a dual-polarity file - rolls the session back,
        # and a digest already in the set would make the retry skip a binding
        # that was never written, so the run would never teach its method.
        if recorded is not None:
            recorded.update(written)
    except Exception as e:  # noqa: BLE001 - a recording is never worth a file
        runtime.logger.opt(exception=True).warning(
            f"Could not record a method binding for {sample_file.filename}: {e}"
        )
        return dict(_NOTHING_LEARNED)

    return counts


def _report_no_signature(
    sample_file: SampleFile, unreadable: bool, recorded: set[str] | None
) -> None:
    """Say why a file taught nothing, at the level the reason deserves.

    Once per run: the marker goes in the same set the observations use, under
    a name no digest can take, so a retried run does not repeat the line three
    more times.

    :param sample_file: The file that taught nothing.
    :param unreadable: True when its ``.props`` could not be read, which is a
        fault; False when it simply records no census, which is ordinary.
    :param recorded: The run's recorded keys, or None outside a pipeline run.
    """
    marker = f"no-signature:{sample_file.sample_file_id}"
    if recorded is not None:
        if marker in recorded:
            return
        recorded.add(marker)
    if unreadable:
        runtime.logger.warning(
            f"Could not read the .props of {sample_file.filename} on "
            f"{sample_file.instrument}, so its acquisition method learned "
            "nothing from this file. The file itself processes normally."
        )
        return
    runtime.logger.debug(
        f"{sample_file.filename} records no scan-stream census, so its "
        "acquisition method learned nothing from it. Ordinary for a file "
        "converted before the census existed."
    )


async def _observe(
    session,
    digest: str,
    instrument: str,
    key: str,
    signature: str,
    mode: IonizationMode,
    source: str,
    seen_at: datetime,
    sample_file_id: str,
) -> str:
    """Fold one observation into its binding, creating the row if needed.

    Two files of the same method can be processed at once, so the row is
    taken under ``FOR UPDATE`` and created with ``ON CONFLICT DO NOTHING``.
    The insert is attempted only when no row was found, and a conflict then
    means another worker created it first - so the observation is folded into
    that row instead, and is counted exactly once either way.

    **A file repeating what it already said is not a second observation.**
    A repeat is the same file AND the same chemistry as this row *last*
    learned; a file coming back with a different chemistry is new information
    - a person re-bound it - and is recorded, ambiguity and all.

    This catches only a *consecutive* repeat, which is what a person clicking
    re-process twice produces. It is not what stops the pipeline's retries
    counting four times: those are tens of seconds apart, so another file of
    the same key can be learned in between and the comparison misses. The
    run-scoped ``recorded`` set in :func:`learn_method_bindings` is what
    handles retries.

    :return: ``"created"``, ``"refreshed"``, ``"ambiguous"`` or
        ``"repeated"``.
    :rtype: str
    """
    chemistry = chemistry_key(mode.ionization_mechanism_ids)

    row = await _locked(session, digest)
    if row is None:
        created = (
            await session.execute(
                pg_insert(MethodBinding)
                .values(
                    method_binding_id=gen_id(),
                    binding_key=digest,
                    instrument=instrument,
                    method_key=clipped(key, METHOD_KEY_COLUMN),
                    signature_class=clipped(signature, SIGNATURE_CLASS_COLUMN),
                    ionization_mode_id=mode.ionization_mode_id,
                    chemistry_keys=[chemistry],
                    state="learned",
                    source=source,
                    first_seen=seen_at,
                    last_seen=seen_at,
                    n_streams=1,
                    n_disagreements=0,
                    last_sample_file_id=sample_file_id,
                    last_chemistry_key=chemistry,
                    candidate_mode_id=None,
                    n_candidate_streams=0,
                )
                .on_conflict_do_nothing(constraint="uq_method_binding_key")
                .returning(MethodBinding.method_binding_id)
            )
        ).scalar_one_or_none()
        if created is not None:
            return "created"
        row = await _locked(session, digest)
        if row is None:
            # The conflicting row was deleted between the insert and this
            # read. Nothing to fold into, and inventing a second row under
            # the same key is what the constraint exists to prevent.
            runtime.logger.debug(
                f"Method binding {digest[:12]} vanished while it was being "
                "recorded; the observation was dropped"
            )
            return "refreshed"

    if (
        row.last_sample_file_id == sample_file_id
        and row.last_chemistry_key == chemistry
    ):
        # A retry, or a re-run that changed nothing. Nothing to add, and
        # counting it would make n_streams a count of pipeline attempts.
        row.last_seen = seen_at
        return "repeated"

    known = list(row.chemistry_keys or [])
    row.last_seen = seen_at
    row.n_streams = (row.n_streams or 0) + 1
    row.source = source
    row.last_sample_file_id = sample_file_id
    row.last_chemistry_key = chemistry

    if chemistry in known:
        # The same chemistry as before, so nothing about this key's unanimity
        # changes - but the row naming that chemistry may have. Only for a
        # key that is still unanimous: an ambiguous one routes nothing, and
        # moving its pointer would be churn nobody reads.
        if len(known) == 1:
            _follow(row, mode, instrument=instrument, key=key)
        return "refreshed"

    # A chemistry this key has not been seen with. The row is never repointed
    # by one - the file it came from routed on a stronger rung and was
    # processed correctly - but the key has now separated nothing, so it is
    # marked and stops being a candidate for routing.
    row.chemistry_keys = known + [chemistry]
    row.n_disagreements = (row.n_disagreements or 0) + 1
    row.state = "ambiguous"
    # Whatever run was building is moot now, and leaving it would show a
    # pending move on a row that will never route again.
    row.candidate_mode_id = None
    row.n_candidate_streams = 0
    runtime.logger.info(
        f"Method key {key or '(none)'} on {instrument} has now been seen with "
        f"{len(row.chemistry_keys)} chemistries, so it identifies none of "
        "them; files of this method keep routing by their filename token"
    )
    return "ambiguous"


def _follow(
    row: MethodBinding, mode: IonizationMode, instrument: str, key: str
) -> None:
    """Apply :func:`follow_row` to a row, and say so when it moves.

    At INFO, because a binding changing where it sends future files is worth
    a line in a server's log: it is the one thing in this module that alters
    what a later file would be bound to.
    """
    held = row.ionization_mode_id
    points_at, candidate, run = follow_row(
        current=held,
        candidate=row.candidate_mode_id,
        n_candidate=row.n_candidate_streams or 0,
        observed=mode.ionization_mode_id,
    )
    if points_at != held:
        # Two ways to arrive here, and they are not the same event: a row
        # that held nothing takes a mode on one file, because it was routing
        # nothing and had nothing to be dragged away from.
        if held is None:
            runtime.logger.info(
                f"Method key {key or '(none)'} on {instrument} now points at "
                f"'{mode.ionization_mode_name}': the mode it held has been "
                "deleted, so it takes the one this file bound to for the "
                "chemistry it already knows"
            )
        else:
            runtime.logger.info(
                f"Method key {key or '(none)'} on {instrument} now points at "
                f"'{mode.ionization_mode_name}': the last {REPOINT_AFTER} "
                "files of this method bound to it, and it is the same "
                "chemistry as the mode the binding held, so files of this "
                "method follow them"
            )
    row.ionization_mode_id = points_at
    row.candidate_mode_id = candidate
    row.n_candidate_streams = run


async def _locked(session, digest: str) -> MethodBinding | None:
    """The binding for a digest, locked for this transaction."""
    return (
        await session.execute(
            select(MethodBinding)
            .where(MethodBinding.binding_key == digest)
            .with_for_update()
        )
    ).scalar_one_or_none()
