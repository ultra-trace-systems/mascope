"""Integration tests for the cross-process assignment claim.

The claim is a session-level Postgres advisory lock, so its semantics - a
second holder refused while the first holds, release on context exit,
independence across kinds and resources - can only be pinned against a real
Postgres. Each claim opens its own pooled connection, which is exactly the
cross-process shape: two claims here are indistinguishable from two claims in
two different workers.
"""

import pytest

from mascope_backend.api.new.peak_assignments.admission import assignment_claim


@pytest.mark.asyncio
async def test_second_claim_is_refused_while_the_first_holds():
    async with assignment_claim("sample", "adm-si-1") as first:
        assert first is True
        async with assignment_claim("sample", "adm-si-1") as second:
            assert second is False
        # An inner refusal must not have released the outer holder's lock.
        async with assignment_claim("sample", "adm-si-1") as third:
            assert third is False


@pytest.mark.asyncio
async def test_claim_is_released_on_exit():
    async with assignment_claim("sample", "adm-si-2") as first:
        assert first is True
    async with assignment_claim("sample", "adm-si-2") as again:
        assert again is True


@pytest.mark.asyncio
async def test_kinds_and_resources_are_independent():
    async with assignment_claim("sample", "adm-x") as sample_claim:
        assert sample_claim is True
        # The same id under a different kind is a different lock.
        async with assignment_claim("batch", "adm-x") as batch_claim:
            assert batch_claim is True
        # A different id under the same kind is too.
        async with assignment_claim("sample", "adm-y") as other_sample:
            assert other_sample is True


@pytest.mark.asyncio
async def test_claim_is_released_when_the_body_raises():
    with pytest.raises(RuntimeError):
        async with assignment_claim("sample", "adm-si-3") as acquired:
            assert acquired is True
            raise RuntimeError("boom")
    async with assignment_claim("sample", "adm-si-3") as again:
        assert again is True
