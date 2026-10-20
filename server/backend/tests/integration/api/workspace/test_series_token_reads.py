"""
Tests: the two compact per-batch series answer a real SDK token, and keep their
workspace ACL.

``POST /api/match/records/ion/series`` and ``POST /api/batch-peaks/records/series``
are what the app's two batch charts draw from, and what the visual digests'
SDK wrappers read, one call per batch (``docs/dev/visual_digest.md``, phase 0).
The flag is pinned in ``unit/api/routes/test_token_access_coverage.py``; here a
guest's token, sent the way the SDK sends it, goes through the auth selector,
the role dependency and the handler, and gets the seeded series back. A token
holds its user's rights and nothing more: a batch of a workspace the user is no
member of is refused, and a token sent under another service's name is refused
before the handler runs.

Fixtures used:
- ``alpha_batch`` and ``alpha_item`` in ``ws_alpha`` (every test user is a member)
- ``beta_batch`` - in ``ws_beta``, where only the owner is a member
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AccessToken,
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    MatchIon,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    hash_access_token,
)
from mascope_backend.db.id import gen_id


ION_SERIES = "/api/match/records/ion/series"
PEAK_SERIES = "/api/batch-peaks/records/series"


@pytest_asyncio.fixture
async def series(async_session_factory, ws_alpha, alpha_batch, alpha_item):
    """One matched target ion and one batch peak in ``alpha_batch``'s sample."""
    ids = SimpleNamespace(
        mechanism=gen_id(),
        compound=gen_id(),
        collection=gen_id(),
        ion=gen_id(),
        batch_peak=gen_id(),
        sample_peak=gen_id(20),
    )
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {ids.mechanism}",
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=ids.compound,
                target_compound_name=f"Series Compound {ids.compound}",
                target_compound_formula="C6H12O6",
            )
        )
        session.add(
            TargetCollection(
                target_collection_id=ids.collection,
                target_collection_name=f"Series Collection {ids.collection}",
                workspace_id=ws_alpha["workspace_id"],
            )
        )
        await session.flush()
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=ids.compound,
                target_collection_id=ids.collection,
            )
        )
        session.add(
            TargetIon(
                target_ion_id=ids.ion,
                target_compound_id=ids.compound,
                ionization_mechanism_id=ids.mechanism,
                target_ion_formula="C6H13O6+",
            )
        )
        session.add(
            BatchPeak(
                batch_peak_id=ids.batch_peak,
                sample_batch_id=alpha_batch,
                mz=181.0707,
                mz_tol_ppm=3.0,
                consensus_formula="C6H12O6",
                n_present=1,
            )
        )
        await session.flush()
        session.add(
            MatchIon(
                match_ion_id=gen_id(32),
                sample_item_id=alpha_item,
                target_ion_id=ids.ion,
                match_score=0.9,
                match_category=2,
                sample_peak_intensity_sum=1234.0,
            )
        )
        session.add(
            BatchPeakOccurrence(
                batch_peak_id=ids.batch_peak,
                sample_item_id=alpha_item,
                sample_peak_id=ids.sample_peak,
                intensity=567.0,
            )
        )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(BatchPeak).where(BatchPeak.batch_peak_id == ids.batch_peak)
        )
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_id == ids.compound
            )
        )
        await session.execute(
            delete(TargetCollection).where(
                TargetCollection.target_collection_id == ids.collection
            )
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == ids.mechanism
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, test_users):
    """A real ``mascope_sdk`` API token of the guest user."""
    token = f"series-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token_hash=hash_access_token(token),
                user_id=test_users["guest"].id,
                service_name="mascope_sdk",
            )
        )
        await session.commit()
    yield token
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.token_hash == hash_access_token(token)
            )
        )
        await session.commit()


def _token_client(token: str, service: str = "mascope_sdk") -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": service},
    )


def _ion_body(series, batch_id: str) -> dict:
    return {"sample_batch_id": batch_id, "target_collection_id": series.collection}


def _peak_body(series, batch_id: str) -> dict:
    # Full-batch load; the one seeded sample puts the peak at n_present=1.
    return {"sample_batch_id": batch_id, "min_n_present": 1}


def _the_ion_series(resp, series) -> dict:
    return next(
        each["match_series"]
        for each in resp.json()["data"]
        if each["target_ion_id"] == series.ion
    )


def _the_peak_series(resp, series) -> dict:
    return next(
        each["peak_series"]
        for each in resp.json()["data"]
        if each["batch_peak_id"] == series.batch_peak
    )


@pytest.mark.asyncio
async def test_an_sdk_token_reads_the_ion_series(
    sdk_token, series, alpha_batch, alpha_item
):
    async with _token_client(sdk_token) as client:
        resp = await client.post(ION_SERIES, json=_ion_body(series, alpha_batch))

    assert resp.status_code == 200, resp.text
    assert _the_ion_series(resp, series) == {
        "sample_item_ids": [alpha_item],
        "sample_peak_intensity_sums": [1234.0],
        "match_categories": [2],
    }


@pytest.mark.asyncio
async def test_an_sdk_token_reads_the_batch_peak_series(
    sdk_token, series, alpha_batch, alpha_item
):
    async with _token_client(sdk_token) as client:
        resp = await client.post(PEAK_SERIES, json=_peak_body(series, alpha_batch))

    assert resp.status_code == 200, resp.text
    peak_series = _the_peak_series(resp, series)
    assert peak_series["sample_item_ids"] == [alpha_item]
    assert peak_series["sample_peak_ids"] == [series.sample_peak]
    assert peak_series["intensities"] == [567.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "body"), [(ION_SERIES, _ion_body), (PEAK_SERIES, _peak_body)]
)
@pytest.mark.parametrize("service", ["file-agent", "file-converter", "sdk"])
async def test_a_token_sent_as_another_service_is_refused(
    sdk_token, series, alpha_batch, url, body, service
):
    async with _token_client(sdk_token, service) as client:
        resp = await client.post(url, json=body(series, alpha_batch))

    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "body"), [(ION_SERIES, _ion_body), (PEAK_SERIES, _peak_body)]
)
async def test_a_token_keeps_its_users_workspace_acl(
    sdk_token, series, beta_batch, url, body
):
    """The guest is no member of ws_beta: the token is accepted, the read is not."""
    async with _token_client(sdk_token) as client:
        resp = await client.post(url, json=body(series, beta_batch))

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_a_guest_session_reads_both_series(
    guest_client, series, alpha_batch, alpha_item
):
    ions = await guest_client.post(ION_SERIES, json=_ion_body(series, alpha_batch))
    peaks = await guest_client.post(PEAK_SERIES, json=_peak_body(series, alpha_batch))

    assert ions.status_code == 200, ions.text
    assert _the_ion_series(ions, series)["sample_item_ids"] == [alpha_item]
    assert peaks.status_code == 200, peaks.text
    assert _the_peak_series(peaks, series)["sample_item_ids"] == [alpha_item]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "body"), [(ION_SERIES, _ion_body), (PEAK_SERIES, _peak_body)]
)
async def test_a_non_member_is_refused(outsider_client, series, alpha_batch, url, body):
    resp = await outsider_client.post(url, json=body(series, alpha_batch))

    assert resp.status_code == 403, resp.text
