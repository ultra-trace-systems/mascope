"""
Tests: reporting where a method binding and a filename token disagree.

The script runs by hand on a production database, so nothing else exercises
it. These do: they seed bindings and the files of their methods, and run the
script's body against them.

TOF files throughout, as in the backfill's tests: their reader records no
scan-stream census, so the signature class is the polarity and nothing has to
read a `.props` off a filestore these tests do not have.

Every mode in the database is a candidate for a token match, including modes
another test is holding at the same time, so each test that asserts on a token
answer passes the report its own modes. One deliberately does not, to prove
the report reads them from the database when nobody hands them over.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.controllers.sample.files.process.bindings import (
    REPOINT_AFTER,
)
from mascope_backend.db import (
    IonizationMode,
    MethodBinding,
    SampleFile,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts.report_method_binding_disagreements import (
    method_binding_report,
)
from mascope_backend.method_keys import binding_digest, chemistry_key, method_key


_REPORT = "mascope_backend.db.scripts.report_method_binding_disagreements"

#: Every file read, however many other tests are holding files of their own.
_ALL_FILES = 1_000_000

METHOD = "nitrate_tof.meth"

#: Tokens AND file names are built from this alphabet, so that a token another
#: test has configured cannot read as a substring of a file name here. Those
#: names hold nothing else - no digits, no upper case - so only a token drawn
#: from these same five letters could match one, and nothing else draws from
#: them. Without that, a two-character token like "BR" would land inside a
#: random name often enough to fail a run now and then.
_TOKEN_ALPHABET = "qzvwx"


def _letters(length: int) -> str:
    """A unique string a token of any other test cannot be a substring of."""
    return "".join(
        _TOKEN_ALPHABET[ord(c) % len(_TOKEN_ALPHABET)] for c in gen_id(length)
    )


def _token() -> str:
    """A token no other test's file names contain."""
    return _letters(8)


@pytest_asyncio.fixture
async def fleet(async_session_factory):
    """One instrument, and ways to give it modes, bindings and files."""
    instrument = f"instrument-{gen_id(8)}"
    made: dict[str, list] = {"files": [], "modes": [], "bindings": []}

    async def add_mode(polarity="-", mechanisms=("mech-no3",), token=None):
        mode = IonizationMode(
            ionization_mode_id=gen_id(),
            ionization_mode_name=f"Mode {gen_id(8)}",
            ionization_mode_polarity=polarity,
            ionization_mechanism_ids=list(mechanisms),
            ionization_mode_token=token,
        )
        async with async_session_factory() as session:
            session.add(mode)
            await session.commit()
        made["modes"].append(mode.ionization_mode_id)
        return mode

    async def add_binding(
        mode,
        *,
        polarity=None,
        method_file=METHOD,
        chemistries=None,
        state="learned",
        candidate=None,
        n_candidate_streams=0,
        n_disagreements=0,
    ):
        """A binding of this instrument, keyed as the pipeline would key it."""
        polarity = polarity or mode.ionization_mode_polarity
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        binding = MethodBinding(
            method_binding_id=gen_id(),
            # The real digest, from the real key: the rung looks a file up by
            # it, so a made-up value would simply never be found.
            binding_key=binding_digest(instrument, method_key(method_file), polarity),
            instrument=instrument,
            method_key=method_key(method_file),
            signature_class=polarity,
            ionization_mode_id=mode.ionization_mode_id if mode else None,
            chemistry_keys=(
                chemistries
                if chemistries is not None
                else [chemistry_key(mode.ionization_mechanism_ids)]
            ),
            state=state,
            source="history",
            first_seen=when,
            last_seen=when,
            n_streams=4,
            n_disagreements=n_disagreements,
            candidate_mode_id=candidate.ionization_mode_id if candidate else None,
            n_candidate_streams=n_candidate_streams,
        )
        async with async_session_factory() as session:
            session.add(binding)
            await session.commit()
        made["bindings"].append(binding.method_binding_id)
        return binding

    async def add_file(
        *,
        polarity="-",
        name=None,
        method_file=METHOD,
        minutes=0,
        instrument_type="tof",
    ):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)
        sample_file = SampleFile(
            sample_file_id=gen_id(),
            filename=f"{name or _token()}_{_letters(12)}.h5",
            instrument=instrument,
            instrument_type=instrument_type,
            method_file=method_file,
            datetime=when.replace(tzinfo=None),
            datetime_utc=when,
            length=60.0,
            range=[40.0, 600.0],
            polarity=polarity,
        )
        async with async_session_factory() as session:
            session.add(sample_file)
            await session.commit()
        made["files"].append(sample_file.sample_file_id)
        return sample_file

    yield {
        "instrument": instrument,
        "add_mode": add_mode,
        "add_binding": add_binding,
        "add_file": add_file,
    }

    async with async_session_factory() as session:
        await session.execute(
            delete(MethodBinding).where(
                MethodBinding.method_binding_id.in_(made["bindings"])
            )
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(made["files"]))
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(made["modes"])
            )
        )
        await session.commit()


async def _walk(modes=None, **kwargs):
    """Run the report and return both of its answers.

    ``modes=None`` leaves the mode fetch alone, which is what
    :func:`test_it_runs_at_all` wants; anything else is handed to the token
    rule in place of every mode in the database.
    """
    kwargs.setdefault("files_limit", _ALL_FILES)
    if modes is None:
        return await method_binding_report(**kwargs)
    with patch(
        f"{_REPORT}.fetch_all_ionization_modes",
        AsyncMock(return_value=list(modes)),
    ):
        return await method_binding_report(**kwargs)


async def _report(binding, modes=None, **kwargs):
    """Run the report and return what it says about one binding."""
    reports, _ = await _walk(modes=modes, **kwargs)
    return reports[binding.method_binding_id]


@pytest.mark.asyncio
async def test_it_runs_at_all(fleet):
    """The script has to survive a real database before anything else matters.

    The modes come from the database here, not from the test: that fetch is
    the one thing the other cases patch away, and a report that could not do
    it would report nothing about any token.
    """
    mode = await fleet["add_mode"]()
    binding = await fleet["add_binding"](mode)
    await fleet["add_file"]()

    report = await _report(binding)

    assert report.files == 1
    assert report.blocked is None


@pytest.mark.asyncio
async def test_a_token_naming_another_row_is_a_disagreement(fleet):
    """The headline: what a person reads before confirming a binding."""
    bound = await fleet["add_mode"](mechanisms=("mech-no3",))
    named = await fleet["add_mode"](mechanisms=("mech-br",), token=_token())
    binding = await fleet["add_binding"](bound)
    newest = await fleet["add_file"](name=named.ionization_mode_token, minutes=90)
    await fleet["add_file"](name=named.ionization_mode_token)

    report = await _report(binding, modes=[bound, named])

    assert report.agree == 0
    assert report.differing == 2
    other = report.differ[named.ionization_mode_id]
    assert other.name == named.ionization_mode_name
    # Different reagents, so this is not a site renaming a mode: the two rules
    # disagree about the chemistry itself.
    assert other.same_chemistry is False
    assert other.newest == newest.datetime_utc


@pytest.mark.asyncio
async def test_one_chemistry_under_two_rows_reads_as_the_same_chemistry(fleet):
    """The distinction the fleet measurement turned on.

    Nearly every disagreement it found was one chemistry under two mode rows -
    a site that could not rename a mode in use and made a second - which is a
    different thing to confirm than two rules disagreeing about the reagents.
    """
    bound = await fleet["add_mode"](mechanisms=("mech-no3", "mech-deprot"))
    successor = await fleet["add_mode"](
        mechanisms=("mech-deprot", "mech-no3"), token=_token()
    )
    binding = await fleet["add_binding"](bound)
    await fleet["add_file"](name=successor.ionization_mode_token)

    report = await _report(binding, modes=[bound, successor])

    assert report.differ[successor.ionization_mode_id].same_chemistry is True


@pytest.mark.asyncio
async def test_a_file_a_token_names_is_asked_about_too(fleet):
    """Production reaches the rung only for a file no token names; this does not.

    Those files are the whole reason to read this report: a confirmed binding
    sits ABOVE the token, so they are what a confirmation would move. A report
    that asked the rung only where production does would say nothing about any
    of them.
    """
    mode = await fleet["add_mode"](token=_token())
    binding = await fleet["add_binding"](mode)
    await fleet["add_file"](name=mode.ionization_mode_token)

    report = await _report(binding, modes=[mode])

    assert report.agree == 1
    assert report.no_token == 0
    assert report.differ == {}


@pytest.mark.asyncio
async def test_a_file_no_token_names_is_what_the_binding_routes(fleet):
    """Not agreement: nothing agreed, and these are the files "route" binds."""
    mode = await fleet["add_mode"](token=_token())
    binding = await fleet["add_binding"](mode)
    # Another token of the same alphabet, which the mode's token is not a
    # substring of - so the file's name says nothing about its chemistry.
    await fleet["add_file"](name=_token())

    report = await _report(binding, modes=[mode])

    assert report.no_token == 1
    assert report.agree == 0
    assert report.ambiguous_name == 0


@pytest.mark.asyncio
async def test_a_name_matching_two_chemistries_is_neither(fleet):
    """An ambiguous name parks however the binding is set, so it is not counted.

    This is also what pins the order of the two except clauses in the token
    answer: ``NoTokenMatchError`` subclasses ``ValueError``, so catching the
    wider one first would read every file above as ambiguous and empty the
    count the rung exists to fill.
    """
    bound = await fleet["add_mode"](token=_token())
    rival = await fleet["add_mode"](mechanisms=("mech-br",), token=_token())
    binding = await fleet["add_binding"](bound)
    await fleet["add_file"](
        name=f"{bound.ionization_mode_token}{rival.ionization_mode_token}"
    )

    report = await _report(binding, modes=[bound, rival])

    assert report.ambiguous_name == 1
    assert report.agree == 0
    assert report.no_token == 0
    assert report.differ == {}


@pytest.mark.asyncio
async def test_a_key_seen_with_two_chemistries_is_reported_as_unable_to_route(fleet):
    """Read off the row: four of the rung's six guards need no file at all."""
    mode = await fleet["add_mode"]()
    binding = await fleet["add_binding"](
        mode, chemistries=["mech-no3", "mech-br"], state="ambiguous"
    )

    report = await _report(binding, modes=[mode])

    assert (
        report.blocked == "their method has been seen running more than one chemistry"
    )


@pytest.mark.asyncio
async def test_a_constant_method_name_is_reported_as_unable_to_route(fleet):
    """The guard that no number of chosen chemistries can satisfy.

    ``currentacquisition.ini`` keys as ``""``, the rung declines before any
    lookup, and the learner records the next choice under the same empty name.
    Most of the fleet's token-less TOF files are these, so a report that let
    them look routable would be read by the people it is most wrong for.
    """
    mode = await fleet["add_mode"]()
    binding = await fleet["add_binding"](mode, method_file="CurrentAcquisition.ini")

    report = await _report(binding, modes=[mode])

    assert report.blocked == "their method reports no name of its own"


@pytest.mark.asyncio
async def test_a_move_in_progress_is_reported_with_its_run(fleet):
    """What tells whether REPOINT_AFTER is holding real changes too long."""
    current = await fleet["add_mode"]()
    candidate = await fleet["add_mode"]()
    binding = await fleet["add_binding"](
        current, candidate=candidate, n_candidate_streams=REPOINT_AFTER - 1
    )

    report = await _report(binding, modes=[current])

    assert report.candidate_name == candidate.ionization_mode_name
    assert report.n_candidate_streams == REPOINT_AFTER - 1


@pytest.mark.asyncio
async def test_it_reads_only_so_many_files_of_one_key(fleet):
    """One method in daily use must not spend the whole budget.

    All three files here arrive in one page, which is where the first version
    of the cap failed: it was applied once per page, before any file of that
    page had been counted, so every file of a busy key in the newest page was
    read whatever the cap said.
    """
    mode = await fleet["add_mode"](token=_token())
    binding = await fleet["add_binding"](mode)
    for minutes in range(3):
        await fleet["add_file"](name=mode.ionization_mode_token, minutes=minutes)

    report = await _report(binding, modes=[mode], per_key=1)

    assert report.files == 1
    assert report.agree == 1


@pytest.mark.asyncio
async def test_the_walked_counts_are_a_partition_of_the_files(fleet):
    """A dual-polarity file is one file in the walked line, not two.

    The cap is applied per polarity, where the count is, while the line counts
    files - so a dual-polarity file past the cap was counted twice into a
    total it appears in once, and the parts of the line stopped adding up to
    it. The sum below is what says they do, whatever else the database holds.
    """
    negative = await fleet["add_mode"](polarity="-")
    positive = await fleet["add_mode"](polarity="+")
    await fleet["add_binding"](negative)
    await fleet["add_binding"](positive)
    for minutes in range(2):
        await fleet["add_file"](polarity="+-", minutes=minutes)

    _, walk = await _walk(modes=[negative, positive], per_key=1)

    assert (
        walk.compared + walk.no_binding + walk.enough + walk.could_not_place
        == walk.walked
    )
    assert walk.compared == 1
    assert walk.enough == 1
