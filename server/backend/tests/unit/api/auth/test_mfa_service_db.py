"""Tests: the second factor's single-use guarantees, against a real database.

`test_mfa_service.py` next door covers what a code check decides in Python and
is deliberately free of the database. What it cannot reach is the half of the
design that lives in SQL: a TOTP counter and a recovery code are each spendable
exactly once, and that is enforced by two conditional UPDATEs whose affected-row
count is the claim. Those are only meaningful against a database that takes a
row lock, so they are exercised here through the same `async_session()` the
routes use - pointed at the unit test database by the `patch_db` fixture in
`tests/unit/conftest.py`.

Every account here is committed rather than flushed: each function under test
opens its own session on its own connection, so an uncommitted row would be
invisible to the code being tested. The `make_user` fixture deletes what it
created, which cascades to `user_recovery_code`.
"""

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from test_utils import gen_test_id

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.mfa import crypto, service
from mascope_backend.db import User, UserRecoveryCode


#: A fixed TOTP counter, pinned in place of the wall clock. Verification
#: resolves a code against `current_timestep()`, so without this a counter
#: boundary crossing mid-test could put a code and its replay on either side of
#: it and decide the outcome. Freezing it also lets a test spend a counter and
#: present the same code again without racing the real one.
FROZEN_STEP = 58_000_000

#: A fixed seed rather than `pyotp.random_base32()`: with the counter frozen
#: too, the six digits every assertion below turns on are identical on every
#: run. `test_the_frozen_code_belongs_to_one_counter_only` checks the one
#: property that has to hold for that to be safe.
FROZEN_SEED = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def _code_at(secret: str, step: int) -> str:
    """The code a correctly-set authenticator would show at ``step``."""
    return service._totp(secret).generate_otp(step)


async def _reload(async_session_factory, user_id: int) -> User:
    """Re-read the account, as the next request's dependency would.

    Every function under test writes through a bulk UPDATE in its own session,
    which leaves the ORM object the caller passed in stale. The routes never
    notice because each request loads the account again; a test that wants the
    written state has to do the same.
    """
    async with async_session_factory() as session:
        return await session.get(User, user_id)


async def _enrol(
    async_session_factory, user: User, step: int
) -> tuple[User, list[str]]:
    """Run the enrol/confirm pair the two enrolment routes run.

    :return: The reloaded account and the recovery codes shown once.
    """
    secret, _uri = await service.begin_enrollment(user)
    user = await _reload(async_session_factory, user.id)
    codes = await service.confirm_enrollment(user, _code_at(secret, step))
    assert codes is not None, "the enrolment code did not verify"
    reloaded = await _reload(async_session_factory, user.id)
    # begin_enrollment picks a random seed, so unlike FROZEN_SEED its codes are
    # not checked for collisions across the drift window. A collision would
    # record a neighbouring counter; fail loudly rather than drift on.
    assert reloaded.mfa_last_timestep == step, (
        "the enrolment code hit a neighbouring counter"
    )
    return reloaded, codes


async def _recovery_rows(async_session_factory, user_id: int) -> list[UserRecoveryCode]:
    """Every recovery-code row the account holds, spent or not."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id)
        )
        return list(result.scalars().all())


# --- Fixtures ---


@pytest.fixture
def mfa_key(monkeypatch):
    """A deployment key, without touching the real secrets directory."""
    monkeypatch.setattr(crypto, "mfa_encryption_key", lambda: "unit-test-mfa-key")


@pytest.fixture
def frozen_clock(monkeypatch) -> int:
    """Pin the TOTP counter for the duration of a test - see FROZEN_STEP."""
    monkeypatch.setattr(service, "current_timestep", lambda: FROZEN_STEP)
    return FROZEN_STEP


@pytest_asyncio.fixture
async def make_user(async_session_factory, mfa_key):
    """Factory for throwaway accounts, removed again afterwards.

    Committed, not flushed: the functions under test read the account through
    their own connection and would not see an open transaction's rows.
    """
    created: list[int] = []

    async def _make(*, enabled: bool, secret: str | None) -> User:
        async with async_session_factory() as session:
            user = User(
                email=f"mfa-db-{gen_test_id(10)}@test.com",
                username=f"mfa_db_{gen_test_id(10)}",
                hashed_password="not-a-real-hash",
                is_active=True,
                is_verified=False,
                mfa_secret=crypto.encrypt_secret(secret) if secret else None,
                mfa_enabled=enabled,
                mfa_confirmed_at=datetime.now(timezone.utc) if enabled else None,
                mfa_last_timestep=None,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        created.append(user.id)
        return user

    yield _make

    if created:
        async with async_session_factory() as session:
            # A bulk DELETE rather than session.delete(): the FK on
            # user_recovery_code is ON DELETE CASCADE, so the database clears
            # the codes without the ORM loading every relationship first.
            await session.execute(delete(User).where(User.id.in_(created)))
            await session.commit()


@pytest_asyncio.fixture
async def enrolled_user(make_user) -> User:
    """An account with a confirmed factor that has never spent a counter."""
    return await make_user(enabled=True, secret=FROZEN_SEED)


@pytest_asyncio.fixture
async def bare_user(make_user) -> User:
    """An account with no factor yet, ready to enrol."""
    return await make_user(enabled=False, secret=None)


# --- The frozen clock, checked ---


def test_the_frozen_code_resolves_to_its_own_counter(frozen_clock):
    """The pinned seed's code resolves to FROZEN_STEP, not to a neighbour.

    `verify_code_at_timestep` walks the window from the earliest counter and
    returns the first match, so a seed whose codes collided across the window
    would silently record the wrong counter and make every single-use assertion
    below mean something else. Checking it through the function itself keeps the
    choice of seed honest and pins the window it is checked against.
    """
    code = _code_at(FROZEN_SEED, frozen_clock)
    assert service.verify_code_at_timestep(FROZEN_SEED, code, None) == frozen_clock
    for offset in (-1, 1):
        neighbour = _code_at(FROZEN_SEED, frozen_clock + offset)
        assert (
            service.verify_code_at_timestep(FROZEN_SEED, neighbour, None)
            == frozen_clock + offset
        )


# --- One TOTP code, one sign-in ---


@pytest.mark.asyncio
async def test_one_totp_code_signs_in_only_once(
    async_session_factory, enrolled_user, frozen_clock
):
    """A code that verified once is refused the second time.

    Both calls are handed the same ORM object, still carrying
    `mfa_last_timestep = None`. That is not a shortcut: it is exactly what two
    requests arriving with one observed code hold, since each loads the account
    before either has written. So the in-Python refusal inside
    `verify_code_at_timestep` never fires, and the only thing that can refuse
    the second call is the conditional UPDATE in `verify_totp_for_user`.
    """
    code = _code_at(FROZEN_SEED, frozen_clock)

    assert await service.verify_totp_for_user(enrolled_user, code) is True
    assert await service.verify_totp_for_user(enrolled_user, code) is False

    stored = await _reload(async_session_factory, enrolled_user.id)
    assert stored.mfa_last_timestep == frozen_clock


@pytest.fixture
def held_at_the_first_statement(monkeypatch):
    """Hold the next two callers side by side at their first statement.

    Left to itself `asyncio.gather` does not produce a race here: the first
    task runs its whole statement-to-commit sequence before the second has even
    checked out a connection, so the second reads an already-committed row and
    refuses on its own - which a read-then-write implementation would do just
    as happily as the conditional UPDATE. Establishing both connections and
    then releasing both callers together is what puts them at the database at
    the same time, which is the state the guard has to survive.
    """
    barrier = asyncio.Barrier(2)
    real_async_session = service.async_session
    remaining = 2

    def _gated_session():
        nonlocal remaining
        session = real_async_session()
        if remaining <= 0:
            return session
        remaining -= 1
        real_execute = session.execute
        pending = True

        async def execute(*args, **kwargs):
            nonlocal pending
            if pending:
                pending = False
                # Dial before waiting: a caller still opening its connection
                # after the barrier releases is a caller that is not racing.
                await session.connection()
                await barrier.wait()
            return await real_execute(*args, **kwargs)

        session.execute = execute
        return session

    monkeypatch.setattr(service, "async_session", _gated_session)


@pytest.mark.asyncio
async def test_two_concurrent_verifications_of_one_code_yield_one_success(
    async_session_factory, enrolled_user, frozen_clock, held_at_the_first_statement
):
    """One observed code mints one session even when two requests race.

    Both callers are held at the database with the account still unwritten, so
    neither can be refused by the in-Python check and neither can be refused by
    having read the other's commit. The only thing left to refuse the loser is
    the conditional UPDATE's affected-row count.
    """
    code = _code_at(FROZEN_SEED, frozen_clock)

    # A guard that never reaches the database would park a caller on the
    # barrier forever; time out rather than hang the run.
    async with asyncio.timeout(30):
        results = await asyncio.gather(
            service.verify_totp_for_user(enrolled_user, code),
            service.verify_totp_for_user(enrolled_user, code),
        )

    assert sum(results) == 1, f"one code was spent {sum(results)} times: {results}"

    stored = await _reload(async_session_factory, enrolled_user.id)
    assert stored.mfa_last_timestep == frozen_clock


@pytest.mark.asyncio
async def test_confirming_enrolment_spends_the_code_it_confirmed(
    async_session_factory, bare_user, frozen_clock
):
    """The code that armed the factor cannot then be replayed to sign in.

    `confirm_enrollment` records the counter it consumed. Without that the
    enrolling user's very first code is still spendable at the verify route for
    the rest of the drift window - the exact replay the column exists to close.
    """
    secret, _uri = await service.begin_enrollment(bare_user)
    user = await _reload(async_session_factory, bare_user.id)
    code = _code_at(secret, frozen_clock)
    assert await service.confirm_enrollment(user, code) is not None

    user = await _reload(async_session_factory, bare_user.id)
    assert user.mfa_enabled is True
    assert user.mfa_last_timestep == frozen_clock
    assert await service.verify_totp_for_user(user, code) is False


@pytest.mark.asyncio
async def test_the_next_counter_still_verifies_after_one_is_spent(
    async_session_factory, enrolled_user, frozen_clock
):
    """Refusing a replay must not lock the account out of its next sign-in."""
    assert (
        await service.verify_totp_for_user(
            enrolled_user, _code_at(FROZEN_SEED, frozen_clock)
        )
        is True
    )

    user = await _reload(async_session_factory, enrolled_user.id)
    assert (
        await service.verify_totp_for_user(
            user, _code_at(FROZEN_SEED, frozen_clock + 1)
        )
        is True
    )

    stored = await _reload(async_session_factory, enrolled_user.id)
    assert stored.mfa_last_timestep == frozen_clock + 1


# --- One recovery code, one sign-in ---


@pytest.mark.asyncio
async def test_a_recovery_code_is_spent_only_once(
    async_session_factory, bare_user, frozen_clock
):
    """The second submission of a redeemed code is refused.

    Nothing in Python stands between the two calls - the account object is not
    even consulted - so the refusal is entirely the `used_at IS NULL` filter on
    the UPDATE and the affected-row count it produces.
    """
    user, codes = await _enrol(async_session_factory, bare_user, frozen_clock)

    assert await service.redeem_recovery_code(user, codes[0]) is True
    assert await service.redeem_recovery_code(user, codes[0]) is False

    assert await service.unused_recovery_code_count(user) == len(codes) - 1


@pytest.mark.asyncio
async def test_a_redeemed_recovery_code_is_kept_and_stamped(
    async_session_factory, bare_user, frozen_clock
):
    """Redemption marks the row, it does not remove it.

    The row is what stops the same code being re-issued into the slot, and what
    lets an operator see that a recovery happened.
    """
    user, codes = await _enrol(async_session_factory, bare_user, frozen_clock)
    assert await service.redeem_recovery_code(user, codes[0]) is True

    rows = await _recovery_rows(async_session_factory, user.id)
    assert len(rows) == len(codes)

    spent = [row for row in rows if row.used_at is not None]
    assert len(spent) == 1
    assert spent[0].code_hash == service.hash_recovery_code(codes[0])


@pytest.mark.asyncio
async def test_recovery_codes_are_stored_only_as_digests(
    async_session_factory, bare_user, frozen_clock
):
    """A database dump hands over no usable recovery code."""
    user, codes = await _enrol(async_session_factory, bare_user, frozen_clock)

    stored = {
        row.code_hash for row in await _recovery_rows(async_session_factory, user.id)
    }

    assert stored == {service.hash_recovery_code(code) for code in codes}
    typed_forms = set(codes) | {service.normalize_recovery_code(c) for c in codes}
    assert not stored & typed_forms


# --- Disabling and re-enrolling ---


@pytest.mark.asyncio
async def test_disabling_clears_the_factor_and_its_recovery_codes(
    async_session_factory, bare_user, frozen_clock
):
    """Nothing survives a disable that could still authenticate the account.

    A recovery code left behind - even marked used - would be a credential the
    owner believes they revoked.
    """
    user, codes = await _enrol(async_session_factory, bare_user, frozen_clock)
    assert await service.unused_recovery_code_count(user) == len(codes)

    await service.disable_mfa(user.id)
    user = await _reload(async_session_factory, user.id)

    assert user.mfa_secret is None
    assert user.mfa_enabled is False
    assert user.mfa_confirmed_at is None
    assert user.mfa_last_timestep is None
    assert await _recovery_rows(async_session_factory, user.id) == []


@pytest.mark.asyncio
async def test_re_enrolling_after_a_disable_leaves_exactly_ten_unused_codes(
    async_session_factory, bare_user, frozen_clock
):
    """The set the user just wrote down is the whole set, and the only one.

    Re-enrolling at the same frozen counter is legitimate: `disable_mfa` resets
    `mfa_last_timestep` to NULL along with the seed, so the second confirmation
    is not replaying anything - the seed it confirms is a different one.
    """
    user, first_codes = await _enrol(async_session_factory, bare_user, frozen_clock)
    assert await service.redeem_recovery_code(user, first_codes[0]) is True

    await service.disable_mfa(user.id)
    user = await _reload(async_session_factory, user.id)

    user, second_codes = await _enrol(async_session_factory, user, frozen_clock)

    assert len(second_codes) == auth_settings.mfa.RECOVERY_CODE_COUNT == 10
    assert await service.unused_recovery_code_count(user) == 10

    # The nine the earlier enrolment left unspent are gone, not merely
    # outnumbered.
    assert await service.redeem_recovery_code(user, first_codes[1]) is False


@pytest.mark.asyncio
async def test_confirming_enrolment_replaces_codes_left_from_an_earlier_one(
    async_session_factory, bare_user, frozen_clock
):
    """Confirmation clears the account's codes before writing the new set.

    The disable path clears them too, so on the ordinary route this DELETE is
    the second of two. Seeding the rows directly is what isolates it: it is the
    one guarantee that holds however the leftovers got there.
    """
    async with async_session_factory() as session:
        session.add_all(
            [
                UserRecoveryCode(
                    user_id=bare_user.id,
                    code_hash=service.hash_recovery_code(f"LEFTOVER-{index}"),
                )
                for index in range(3)
            ]
        )
        await session.commit()

    user, codes = await _enrol(async_session_factory, bare_user, frozen_clock)

    assert len(await _recovery_rows(async_session_factory, user.id)) == len(codes) == 10
    assert await service.redeem_recovery_code(user, "LEFTOVER-0") is False


@pytest.mark.asyncio
async def test_the_unused_count_is_what_the_status_route_reports(
    async_session_factory, bare_user, frozen_clock
):
    """`unused_recovery_code_count` counts only what is still spendable.

    It is the number the enrolment screen shows, and the prompt to generate a
    fresh set is driven off it.
    """
    user, codes = await _enrol(async_session_factory, bare_user, frozen_clock)

    for spent, code in enumerate(codes[:3], start=1):
        assert await service.redeem_recovery_code(user, code) is True
        assert await service.unused_recovery_code_count(user) == len(codes) - spent

    async with async_session_factory() as session:
        total = await session.execute(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user.id)
        )
    assert total.scalar() == len(codes)
