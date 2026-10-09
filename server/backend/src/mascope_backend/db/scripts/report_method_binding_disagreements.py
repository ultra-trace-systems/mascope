"""
Maintenance script to report what the method bindings would route, and where a
filename token says otherwise.

A ``method_binding`` row records which chemistry an acquisition method has been
seen running, so that a later file of the same method can route without a
filename token (``docs/dev/ingest_routing_and_splitting.md``, section 5.3).
The learned rung sits BELOW the token, so a binding can only ever reach a file
no token names. A binding a person CONFIRMS sits above the token instead - and
that is the decision this report exists to inform. For every key it answers
two questions over the files read: what their tokens bind them to today, and
what their method binding would bind them to.

**It writes nothing.** There is no ``DRY_RUN`` because there is nothing to dry
run, and nothing here takes a lock. It says so to the runner as well, with
``WRITES_NOTHING`` below, so no ``pg_dump`` is taken before it: this is meant
to be read before and after every change an operator makes, and a restore
point for a change it cannot make would be paid for per reading.

**Both answers come from the code the pipeline uses**, not from a
reconstruction of it: :func:`resolve_ionization_modes_by_tokens` for the token,
and :func:`resolve_modes_by_method_binding` - the rung itself, with its six
guards - for the binding. The fleet measurement that produced this item did
reproduce the token rule in SQL, and reading it back was the part of that work
that needed the most defending; a report that re-implements either rule
eventually reports a disagreement production does not have.

**The rung is asked about every file read**, including the files a token names.
Production never does that - it reaches the rung only once the token has found
nothing - and the difference is the point: those files are exactly what a
confirmation would move, and they cannot be read off anything else.

**It reads each file's ``.props``**, for the instruments whose reader records a
scan-stream census, because a binding's identity carries the signature class
and the census is the only place that comes from. That is also why this is a
script and not a SQL view.

**Bounded, newest acquisition first.** ``BINDING_REPORT_FILES`` caps the files
one run walks (default 20,000), which bounds what it costs;
``BINDING_REPORT_PER_KEY`` caps how many of them each binding is compared on
(default 200), so one method in daily use cannot spend the whole comparison on
itself. The second is not a bound on cost: a file its own method cannot place
is read and counted under the reason, whatever the key has had already, and
the first cap is what bounds that. Newest first because the question is what the tokens
say *today*; a key whose files are all older than the budget is reported as
having none, rather than being left out silently. Nothing indexes
``sample_file.datetime_utc``, so each page re-sorts the table - which is why
the budget exists at all.

Usage:
    mascope dev db script run report_method_binding_disagreements
    mascope prod db script run report_method_binding_disagreements
    BINDING_REPORT_FILES=100000 mascope prod db script run \
        report_method_binding_disagreements

Date: 2026-10-02
"""

import asyncio
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime as dt

from sqlalchemy import func, select, tuple_

from mascope_backend.api.controllers.sample.files.process.bindings import (
    REPOINT_AFTER,
    method_binding_mode,
    resolve_modes_by_method_binding,
)
from mascope_backend.api.controllers.sample.files.process.status import (
    read_scan_streams,
)
from mascope_backend.api.new.ionization.modes.util import (
    NoTokenMatchError,
    fetch_all_ionization_modes,
    resolve_ionization_modes_by_tokens,
)
from mascope_backend.db import (
    IonizationMode,
    MethodBinding,
    SampleFile,
    async_session,
    configure_database_engine,
)
from mascope_backend.method_keys import (
    CENSUS_BEARING_INSTRUMENT_TYPES,
    METHOD_KEY_COLUMN,
    chemistry_key,
    clipped,
    instrument_key,
    method_key,
)
from mascope_backend.runtime import runtime


#: Read by ``mascope dev|prod db script run``, which then takes no pre-script
#: backup (``mascope_cli.pg.script_writes_nothing``). It is a promise about
#: this module: nothing here opens a write session, and the only thing it
#: touches outside the database is reading each file's ``.props``.
WRITES_NOTHING = True

#: Files whose props are read at once, as in ``backfill_method_bindings``: a
#: small local JSON load each, bounded to keep the open descriptors in hand.
_PROPS_CONCURRENCY = 16

#: Files fetched per page of the walk.
_PAGE = 2000

#: Files one run reads, and files it reads per binding, unless the environment
#: says otherwise.
_DEFAULT_FILES = 20000
_DEFAULT_PER_KEY = 200

#: Keys and reasons listed individually before the report turns to counting.
_PREVIEW_LIMIT = 20


@dataclass
class OtherMode:
    """A chemistry row the token names where the binding names another."""

    name: str
    same_chemistry: bool
    files: int = 0
    newest: dt | None = None


@dataclass(frozen=True)
class TokenAnswer:
    """What a file's name says about its chemistry, as data rather than a raise.

    Three answers, and a report has to tell them apart: a name that says one
    chemistry per polarity, a name no token matches - the files the binding
    rung exists for - and a name that says two, which parks whatever the
    bindings hold.
    """

    #: The mode of each polarity, or None when no token names the file.
    modes: dict[str, IonizationMode] | None
    #: Why the name does not say one chemistry per polarity, when it does not.
    problem: str | None


@dataclass
class Walk:
    """What one run looked at, as a partition of the files it walked.

    Every file walked lands in exactly one of the four: compared against the
    binding of its method, no binding for its instrument and method at all,
    past what that binding still needed, or refused by one of the rung's
    guards. They are all counts of FILES - the attribution below is per
    polarity, and mixing the two units made the parts of this stop adding up.
    """

    total: int
    walked: int = 0
    compared: int = 0
    no_binding: int = 0
    enough: int = 0
    declined: Counter[tuple[str, str]] = field(default_factory=Counter)

    @property
    def could_not_place(self) -> int:
        """Files whose own method could not place them, over every reason."""
        return sum(self.declined.values())


@dataclass
class KeyReport:
    """One binding, and what the files of its method say about it.

    Plain values rather than the row: a report is read and printed long after
    the session that fetched it has closed, and a dataclass of strings cannot
    be a lazy load that fails there.
    """

    instrument: str
    method_key: str
    signature_class: str
    mode_name: str | None
    chemistries: int
    state: str
    n_streams: int
    n_disagreements: int
    candidate_name: str | None
    n_candidate_streams: int
    #: Why this key could never route a file, whatever its files say.
    blocked: str | None
    #: Files of this method compared against this binding - one per file,
    #: never two: a binding's key carries a signature class and a class
    #: describes one polarity, so the two polarities of a file key to
    #: different bindings. The attribution holds to that even if they ever
    #: keyed to the same one, or this would stop being a count of files.
    files: int = 0
    agree: int = 0
    no_token: int = 0
    ambiguous_name: int = 0
    differ: dict[str, OtherMode] = field(default_factory=dict)

    @property
    def differing(self) -> int:
        """Files whose token names a chemistry row other than this binding's."""
        return sum(other.files for other in self.differ.values())

    @property
    def name(self) -> str:
        """The key as a person reads it."""
        return (
            f"{self.instrument} {self.method_key or '(no method name)'} "
            f"[{self.signature_class}]"
        )


def _int_env(name: str, default: int, minimum: int) -> int:
    """Read a bound from the environment, or raise.

    Unlike the same helper in ``prune_peak_assignment_runs``, which falls back
    to its default on an unparseable value because a nightly prune that keeps
    running beats one that stops, this refuses: the bounds are part of what
    the report says about itself, and a run that silently read 20,000 files
    after being asked for 100,000 is a wrong report rather than a slow one.

    :param name: The variable to read.
    :param default: What an unset variable means.
    :param minimum: The smallest value that still produces a report.
    :return: The bound.
    :rtype: int
    :raises ValueError: When the variable is set to anything else.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"{name}={raw!r} is not a whole number. Unset it for {default}."
        ) from None
    if value < minimum:
        raise ValueError(f"{name}={raw!r} is below {minimum}, so it reads nothing.")
    return value


async def _key_reports() -> tuple[
    dict[str, KeyReport], dict[tuple[str, str], set[str]]
]:
    """Every binding row, and an index from a file's method to the ids it may be.

    The index is keyed on the instrument exactly as recorded and on the method
    key CLIPPED to its column, because that is what the row holds; it only
    decides whether reading a file is worth it, and
    :func:`resolve_modes_by_method_binding` decides which binding the file
    actually is - from the full key and the signature class, which no index
    here could carry.

    :return: The reports by binding id, and the binding ids per
        (instrument, clipped method key).
    """
    async with async_session() as session:
        rows = (await session.execute(select(MethodBinding))).scalars().all()
        wanted = {
            mode_id
            for row in rows
            for mode_id in (row.ionization_mode_id, row.candidate_mode_id)
            if mode_id
        }
        modes = {
            mode.ionization_mode_id: mode
            for mode in (
                (
                    await session.execute(
                        select(IonizationMode).where(
                            IonizationMode.ionization_mode_id.in_(wanted)
                        )
                    )
                )
                .scalars()
                .all()
            )
        }

    reports: dict[str, KeyReport] = {}
    by_method: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        mode = modes.get(row.ionization_mode_id)
        candidate = modes.get(row.candidate_mode_id)
        reports[row.method_binding_id] = KeyReport(
            instrument=row.instrument,
            method_key=row.method_key,
            signature_class=row.signature_class,
            mode_name=mode.ionization_mode_name if mode else None,
            chemistries=len(row.chemistry_keys or []),
            state=row.state,
            n_streams=row.n_streams or 0,
            n_disagreements=row.n_disagreements or 0,
            candidate_name=candidate.ionization_mode_name if candidate else None,
            n_candidate_streams=row.n_candidate_streams or 0,
            blocked=_blocked(row, mode),
        )
        by_method.setdefault(
            (row.instrument, clipped(row.method_key, METHOD_KEY_COLUMN)), set()
        ).add(row.method_binding_id)
    return reports, by_method


def _blocked(row: MethodBinding, mode: IonizationMode | None) -> str | None:
    """Why this key could never route a file, read off the row alone.

    Four of the rung's six guards are answerable without a file: a method name
    that never varies, a key seen with two chemistries, a deleted mode, and a
    mode belonging to another instrument. The remaining two - no scan census,
    and a mode whose polarity was edited - are properties of a file and of a
    polarity, so they are reported from what the rung said about the files
    read, not from here.

    :param row: The binding row.
    :param mode: The mode it points at, or None when that is gone.
    :return: The reason, or None when files of this key can route.
    :rtype: str | None
    """
    if not row.method_key:
        return "their method reports no name of its own"
    if len(row.chemistry_keys or []) != 1:
        return "their method has been seen running more than one chemistry"
    if mode is None:
        return "the chemistry they were learned from has been deleted"
    scope = instrument_key(mode.instrument)
    if scope is not None and scope != instrument_key(row.instrument):
        return "their chemistry now belongs to another instrument"
    return None


async def _file_pages(limit: int):
    """The files to read, newest acquisition first, in pages.

    Paged by the last row read rather than by OFFSET, as the binding backfill
    is and for the same reason: the gain is stability, not speed. Neither form
    can walk this order, because nothing indexes ``datetime_utc``.

    :param limit: Files to yield in total.
    :return: Pages of ``SampleFile`` rows.
    """
    read = 0
    cursor = None
    async with async_session() as session:
        while read < limit:
            query = (
                select(SampleFile)
                .order_by(SampleFile.datetime_utc.desc(), SampleFile.filename.desc())
                .limit(min(_PAGE, limit - read))
            )
            if cursor is not None:
                query = query.where(
                    tuple_(SampleFile.datetime_utc, SampleFile.filename) < cursor
                )
            page = (await session.execute(query)).scalars().all()
            if not page:
                return
            read += len(page)
            yield page
            cursor = (page[-1].datetime_utc, page[-1].filename)


async def _census(filenames: list[str]) -> dict[str, list[dict] | None]:
    """The scan-stream census of each file, read a bounded number at a time."""
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(name: str) -> tuple[str, list[dict] | None]:
        async with gate:
            return name, await read_scan_streams(name)

    return dict(await asyncio.gather(*(one(name) for name in filenames)))


async def _token_answer(
    sample_file: SampleFile, modes: list[IonizationMode]
) -> TokenAnswer:
    """What a file's name says about its chemistry, per polarity.

    The pipeline's own rule, with the modes passed in so that asking it about
    many files is not a query per file.

    :param sample_file: The file to match.
    :param modes: Every configured mode, fetched once by the caller.
    :return: The three answers a name can give.
    :rtype: TokenAnswer
    """
    try:
        chosen = await resolve_ionization_modes_by_tokens(sample_file, modes)
    except NoTokenMatchError:
        # Caught before the ValueError below, which it subclasses, and the
        # order is load-bearing here as it is in the pipeline's bind step: a
        # file no token names is what the binding rung is for, while a name
        # matching two chemistries is a configuration to fix that no binding
        # stands in for.
        return TokenAnswer(modes=None, problem=None)
    except ValueError as ambiguous:
        return TokenAnswer(modes=None, problem=str(ambiguous))
    # Keyed by polarity because a binding is per polarity. The token rule
    # returns one mode for each polarity of the file, in the file's own order,
    # or it raises - so there is one here for every polarity the rung answers.
    return TokenAnswer(modes=dict(zip(sample_file.polarity, chosen)), problem=None)


async def method_binding_report(
    files_limit: int = _DEFAULT_FILES, per_key: int = _DEFAULT_PER_KEY
) -> tuple[dict[str, KeyReport], Walk]:
    """Read the bindings, the files, and what the two say about each other.

    Separate from :func:`run` so that it can be exercised against a test
    database: :func:`run` configures the engine, which would point this at the
    deployment's own.

    :param files_limit: Files to read, newest acquisition first.
    :param per_key: Files to compare each binding on.
    :return: The report of each binding, by binding id, and what the run
        looked at.
    :rtype: tuple[dict[str, KeyReport], Walk]
    """
    reports, by_method = await _key_reports()
    async with async_session() as session:
        total = await session.scalar(select(func.count()).select_from(SampleFile))
    walk = Walk(total=total)
    if not reports:
        runtime.logger.info(
            "No method bindings have been learned yet, so there is nothing to "
            "compare with the filename tokens. They are learned from the "
            "files that route; `backfill_method_bindings` reads the ones "
            "already routed."
        )
        return reports, walk

    modes = await fetch_all_ionization_modes()

    async for page in _file_pages(files_limit):
        walk.walked += len(page)
        considered = []
        for sample_file in page:
            ids = by_method.get(
                (
                    sample_file.instrument,
                    clipped(method_key(sample_file.method_file), METHOD_KEY_COLUMN),
                )
            )
            if not ids:
                # No binding of this instrument and method under any signature
                # class, so the rung has nothing to say about this file
                # whatever its scans were. Counted without opening its props:
                # that read is the expensive part of this script, and these
                # files are most of a server that has just started learning.
                walk.no_binding += 1
                continue
            if all(reports[binding_id].files >= per_key for binding_id in ids):
                # Cheap half of the per-key cap: a key that was already full
                # when this page began costs nothing more, not even the props
                # read. The exact half is at the attribution below.
                walk.enough += 1
                continue
            considered.append(sample_file)

        census = await _census(
            sorted(
                {
                    sample_file.filename
                    for sample_file in considered
                    if sample_file.instrument_type in CENSUS_BEARING_INSTRUMENT_TYPES
                }
            )
        )
        for sample_file in considered:
            routings, refused = await resolve_modes_by_method_binding(
                sample_file, census.get(sample_file.filename)
            )
            if refused is not None:
                walk.declined[(refused.reason, refused.remedy)] += 1
                continue
            token = await _token_answer(sample_file, modes)
            # Aligned: the rung appends one routing per polarity of the file,
            # in that order, and returns nothing at all if any polarity fails.
            compared: set[str] = set()
            for polarity, routing in zip(sample_file.polarity, routings):
                report = reports[routing.binding_id]
                # The cap belongs here, where the count is. The filter above
                # can only skip a file whose bindings were already full when
                # the page began, and a page holds up to _PAGE files - so a
                # method in daily use would otherwise take the whole of the
                # first page it fills.
                if routing.binding_id in compared or report.files >= per_key:
                    continue
                compared.add(routing.binding_id)
                _attribute(report, sample_file, polarity, routing, token)
            if compared:
                walk.compared += 1
            else:
                # Every binding this file could speak to had its fill. Counted
                # once, as the page filter above counts it, because the walked
                # line is a partition of files and a dual-polarity file is one
                # file in it.
                walk.enough += 1

    _log(reports, walk, files_limit, per_key)
    return reports, walk


def _attribute(
    report: KeyReport,
    sample_file: SampleFile,
    polarity: str,
    routing,
    token: TokenAnswer,
) -> None:
    """Fold one file's two answers for one polarity into that key's report.

    :param report: The report of the binding that answered.
    :param sample_file: The file both rules were asked about.
    :param polarity: The polarity this binding bound.
    :param routing: What the rung returned for it, a
        ``bindings.MethodRouting``.
    :param token: What :func:`_token_answer` said about the file.
    """
    report.files += 1
    if token.problem is not None:
        # Its name matches two chemistries in a polarity. Such a file parks
        # however this binding is set, because the rung is never reached for
        # it - so it is neither agreement nor disagreement.
        report.ambiguous_name += 1
        return
    if token.modes is None:
        # The only files a learned binding routes: the rung sits below the
        # token, so these are what "route" binds and "shadow" parks.
        report.no_token += 1
        return
    named = token.modes[polarity]
    if named.ionization_mode_id == routing.mode.ionization_mode_id:
        report.agree += 1
        return
    other = report.differ.setdefault(
        named.ionization_mode_id,
        OtherMode(
            name=named.ionization_mode_name,
            # The distinction the fleet measurement turned on: nearly every
            # disagreement it found was one chemistry under two mode rows, a
            # site that could not rename a mode in use and made a second. That
            # is a different thing to confirm than a method whose reagents the
            # two rules disagree about.
            same_chemistry=chemistry_key(named.ionization_mechanism_ids)
            == chemistry_key(routing.mode.ionization_mechanism_ids),
        ),
    )
    other.files += 1
    when = sample_file.datetime_utc
    if when is not None and (other.newest is None or when > other.newest):
        other.newest = when


def _count(number: int, noun: str) -> str:
    """``1 key`` or ``5 keys``.

    This text is read by a person deciding whether to confirm a binding, and
    a report of "1 keys" reads like a report nobody read.
    """
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _log(
    reports: dict[str, KeyReport],
    walk: Walk,
    files_limit: int,
    per_key: int,
) -> None:
    """Write the report to the log, in the order a person needs it.

    Each section leads with its counts and then says what to do about them,
    so the whole thing can be skimmed for the numbers that are not zero.
    """
    blocked = Counter(
        report.blocked for report in reports.values() if report.blocked is not None
    )
    runtime.logger.info(
        f"{_count(len(reports), 'method binding')}, "
        f"{len(reports) - sum(blocked.values())} of which could route a file; "
        f"backend.method_binding is '{method_binding_mode()}'"
    )
    for reason, count in blocked.most_common():
        runtime.logger.info(f"  {_count(count, 'key')} cannot: {reason}")
    runtime.logger.info(
        f"Walked {walk.walked} of {walk.total} files, newest acquisition first "
        f"(BINDING_REPORT_FILES={files_limit}, BINDING_REPORT_PER_KEY={per_key}): "
        f"{walk.compared} compared, "
        f"{walk.no_binding} whose instrument and method have no binding, "
        f"{walk.enough} past what their binding needed, "
        f"{walk.could_not_place} their own method could not place"
    )

    disagreeing = sorted(
        (report for report in reports.values() if report.differ),
        key=lambda report: -report.differing,
    )
    if disagreeing:
        runtime.logger.info(
            f"A token and a binding name different chemistries: "
            f"{_count(len(disagreeing), 'key')}, "
            f"{sum(report.differing for report in disagreeing)} files. The "
            "token wins today, because the learned rung sits below it - so "
            "nothing is routed wrongly now, and confirming such a binding "
            "would move it ABOVE the token and re-route exactly these files:"
        )
        for report in disagreeing[:_PREVIEW_LIMIT]:
            runtime.logger.info(
                f"  {report.name}: binding says {report.mode_name!r}, "
                f"{_count(report.files, 'file')} compared"
            )
            for other in sorted(report.differ.values(), key=lambda o: -o.files):
                same = (
                    "the same chemistry under another row"
                    if other.same_chemistry
                    else "ANOTHER CHEMISTRY"
                )
                newest = f", newest {other.newest:%Y-%m-%d}" if other.newest else ""
                runtime.logger.info(
                    f"      token says {other.name!r} on {other.files} of "
                    f"them{newest} - {same}"
                )
        if len(disagreeing) > _PREVIEW_LIMIT:
            runtime.logger.info(
                f"  ... and {len(disagreeing) - _PREVIEW_LIMIT} more keys"
            )

    agreeing = [
        report for report in reports.values() if report.agree and not report.differ
    ]
    if agreeing:
        runtime.logger.info(
            f"Agreeing with every token that answered: "
            f"{_count(len(agreeing), 'key')}, "
            f"{sum(report.agree for report in agreeing)} files"
        )

    rescued = [report for report in reports.values() if report.no_token]
    if rescued:
        runtime.logger.info(
            f"Holding files no token names: {_count(len(rescued), 'key')}, "
            f"{sum(report.no_token for report in rescued)} files. Those are "
            "the only files a learned binding routes: with method_binding = "
            "'route' it binds them, and otherwise they park for someone to "
            "choose a chemistry."
        )
        for report in sorted(rescued, key=lambda r: -r.no_token)[:_PREVIEW_LIMIT]:
            runtime.logger.info(
                f"  {report.name} -> {report.mode_name!r}: "
                f"{_count(report.no_token, 'file')}"
            )

    moving = [
        report
        for report in reports.values()
        if report.candidate_name and report.n_candidate_streams
    ]
    if moving:
        runtime.logger.info(
            f"Following a move that has not landed: "
            f"{_count(len(moving), 'key')}. A binding re-points once "
            f"{REPOINT_AFTER} observations in a row name the same other row, "
            "and one that names the current row again clears the run:"
        )
        for report in moving[:_PREVIEW_LIMIT]:
            runtime.logger.info(
                f"  {report.name}: {report.mode_name!r} -> "
                f"{report.candidate_name!r}, {report.n_candidate_streams} of "
                f"{REPOINT_AFTER}"
            )

    unreliable = [report for report in reports.values() if report.n_disagreements]
    if unreliable:
        runtime.logger.info(
            f"Seen running a chemistry other than the one they hold: "
            f"{_count(len(unreliable), 'key')}. That is what stops them "
            "routing. A method genuinely run with two reagents needs a "
            "filename token, or a chemistry chosen per file:"
        )
        for report in sorted(unreliable, key=lambda r: -r.n_disagreements)[
            :_PREVIEW_LIMIT
        ]:
            runtime.logger.info(
                f"  {report.name}: {report.chemistries} chemistries, "
                f"{_count(report.n_disagreements, 'disagreement')} over "
                f"{_count(report.n_streams, 'observation')}"
            )

    if walk.declined:
        runtime.logger.info(
            "Files their own method could not place, by what stopped it - the "
            "sentence each file's status carries, and what would change it:"
        )
        for (reason, remedy), count in walk.declined.most_common(_PREVIEW_LIMIT):
            runtime.logger.info(f"  {_count(count, 'file')}: {reason}")
            runtime.logger.info(f"      {remedy}")

    ambiguous_names = sum(report.ambiguous_name for report in reports.values())
    if ambiguous_names:
        runtime.logger.info(
            f"Files whose name matches more than one chemistry in a polarity: "
            f"{ambiguous_names}. Those park whatever the bindings say: an "
            "ambiguous name is a configuration to fix, and no binding stands "
            "in for one."
        )

    silent = [report for report in reports.values() if not report.files]
    if silent:
        runtime.logger.info(
            f"Nothing compared against them here: "
            f"{_count(len(silent), 'key')} - either no file of that method was "
            "among those walked, or every one of them is in the counts above, "
            "held back by a guard or named for two chemistries. Raising "
            "BINDING_REPORT_FILES helps with the first of those only."
        )


async def run() -> None:
    """Initialise the database and report on the bindings."""
    await configure_database_engine()
    await method_binding_report(
        files_limit=_int_env("BINDING_REPORT_FILES", _DEFAULT_FILES, 1),
        per_key=_int_env("BINDING_REPORT_PER_KEY", _DEFAULT_PER_KEY, 1),
    )


def main() -> None:
    """Entry point for ``mascope dev|prod db script run``."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
