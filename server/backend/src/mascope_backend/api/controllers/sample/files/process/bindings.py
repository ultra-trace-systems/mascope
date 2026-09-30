"""
Learn which chemistry an acquisition method runs, from the files that route.

Every file that binds on a rung above the method binding - a declaration, a
person's choice, or its filename token - says one true thing about its
method: that method, measuring that way, ran that chemistry. Recording it is
what eventually lets a file route with no token at all
(``docs/dev/ingest_routing_and_splitting.md``, section 5.3).

**Nothing here routes anything yet.** The rows are written and not read: the
rung that consults them arrives with the flag that switches it on, once the
agreement with the token has been measured on real traffic. Until then this
is a recorder, and its one hard requirement is that it can never cost a file
its processing - so :func:`learn_method_bindings` reports failures to the log
and returns, and the caller is not asked to guard it.

**A key routes only while its history agrees on one chemistry.** Each
observation adds its chemistry to the row's ``chemistry_keys``; a second one
marks the row ``ambiguous`` and it never routes again. That is not the same
as the disagreement count, which measures how often it has happened: a key
that was never unanimous must not become a routing binding in the first
place. On the production fleet this holds back about one Orbitrap method key
in forty, and nearly every TOF one - which is the point, since a TOF file's
method name is a constant that separates nothing.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mascope_backend.db import IonizationMode, MethodBinding, SampleFile, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.method_keys import (
    METHOD_KEY_COLUMN,
    SIGNATURE_CLASS_COLUMN,
    binding_digest,
    chemistry_key,
    clipped,
    method_key,
    signature_class,
)
from mascope_backend.runtime import runtime


#: Rungs that may teach a binding, strongest first. Rung 2 is the binding
#: itself and rungs below it are weaker than what they would teach.
LEARNING_SOURCES = ("declared", "explicit", "token")

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


def method_binding_mode() -> str:
    """What this deployment does with method bindings.

    Read from ``method_binding`` in the runtime ``[backend]`` config:
    ``"shadow"`` (the default) learns them and routes nothing on them,
    ``"off"`` records nothing at all.

    :return: ``"shadow"`` or ``"off"``.
    :rtype: str
    """
    return getattr(runtime.config, "method_binding", "shadow")


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
        # The same chemistry as before. The row keeps pointing where it
        # already did, so a deployment that has two identically-built modes
        # does not see the binding wander between them - unless the mode it
        # pointed at has been deleted, when this observation supplies a new
        # one for the chemistry the row already holds.
        if row.ionization_mode_id is None and len(known) == 1:
            row.ionization_mode_id = mode.ionization_mode_id
        return "refreshed"

    # A chemistry this key has not been seen with. The row is never repointed
    # by one - the file it came from routed on a stronger rung and was
    # processed correctly - but the key has now separated nothing, so it is
    # marked and stops being a candidate for routing.
    row.chemistry_keys = known + [chemistry]
    row.n_disagreements = (row.n_disagreements or 0) + 1
    row.state = "ambiguous"
    runtime.logger.info(
        f"Method key {key or '(none)'} on {instrument} has now been seen with "
        f"{len(row.chemistry_keys)} chemistries, so it identifies none of "
        "them; files of this method keep routing by their filename token"
    )
    return "ambiguous"


async def _locked(session, digest: str) -> MethodBinding | None:
    """The binding for a digest, locked for this transaction."""
    return (
        await session.execute(
            select(MethodBinding)
            .where(MethodBinding.binding_key == digest)
            .with_for_update()
        )
    ).scalar_one_or_none()
