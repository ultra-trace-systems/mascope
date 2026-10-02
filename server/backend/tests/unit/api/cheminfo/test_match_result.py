"""
Unit tests: a composition match search's result, and how it reaches the pane.

The search used to send its whole result in its completion notification. Every
Socket.IO emit is published to every backend process through Redis pub/sub, and
a large search's result - every candidate with its whole matched isotope
pattern, about 14 MB - overran Redis' pub/sub output buffer and disconnected the
backend's subscribers. These pin the replacement: the notification carries the
counts and no rows, and the rows are kept for the user who searched, under a
key that expires.
"""

import json
import math
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.new.cheminfo import match_results
from mascope_backend.api.new.cheminfo import service as cheminfo_service
from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_backend.api.new.cheminfo.service import match_compositions_by_mz
from mascope_match.params import OrbiMatchParams


_API_FEATURES = "mascope_backend.api.lib.api_features"

MZ = 401.12345
SAMPLE = "sample-1"
MECHANISM = {
    "ionization_mechanism_id": "mech-1",
    "ionization_mechanism": "[M+NO3]-",
    "ionization_mechanism_polarity": "-",
}

# What a completion notification may weigh, serialized as it is emitted. The
# packet names the search and carries its counts, whatever the number of
# candidates: a few hundred bytes. The budget is for the counts and the message,
# with room to spare, and far below anything that strains Redis pub/sub.
NOTIFICATION_BUDGET_BYTES = 2048


class FakeRedis:
    """The store's Redis, with a clock that the tests move and keys that expire."""

    def __init__(self):
        self.now = 0.0
        self.store: dict[str, tuple[str, float]] = {}
        self.expiries: list[int] = []

    async def set(self, key, value, ex=None):
        self.expiries.append(ex)
        self.store[key] = (value, self.now + ex if ex else math.inf)

    async def get(self, key):
        value, expires = self.store.get(key, (None, math.inf))
        return value if self.now < expires else None


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(match_results, "_redis", lambda: fake)
    return fake


def _isotope(index: int, on_peak: bool) -> dict:
    """One matched isotope line, with every field matching computes for it."""
    mz = MZ + index * 1.00335
    return {
        "target_isotope_id": f"iso-{index:012d}",
        "target_ion_id": "ion-000000000001",
        "target_isotope_formula": f"[13C]{index}C20H30NO13-"
        if index
        else "C20H30NO13-",
        "mz": mz,
        "relative_abundance": 1.0 / (index + 1),
        "resolution": "HIGH",
        "match_isotope_id": f"match-{index:026d}",
        "sample_peak_id": None,
        "sample_peak_mz": MZ if on_peak else (mz if index < 3 else math.nan),
        "sample_peak_intensity": 1.0e6 / (index + 1),
        "sample_peak_intensity_relative": 1.0 / (index + 1),
        "sample_peak_tof": math.nan,
        "match_abundance_error": 0.01,
        "match_mz_error": 0.4 if index < 3 else math.nan,
        "match_score": 0.9 if index < 3 else 0.0,
        "match_category": 2 if index < 3 else None,
        "signal_to_noise": 500.0 / (index + 1),
        "is_satellite": False,
    }


def _formula(index: int) -> str:
    """A formula of its own for each candidate: the task pairs them by formula."""
    return f"C{10 + index // 100}H{20 + index % 100}O4"


def _composition(index: int) -> dict:
    """One composition finder result, as `retrieve_compositions_by_mz` maps it."""
    return {
        "sample_peak_mz": MZ,
        "target_compound_formula": _formula(index),
        "target_compound_unsaturation": 4.0,
        "ionization_mechanism": dict(MECHANISM),
        "target_isotope_mz": MZ,
        "target_isotope_mz_error_ppm": -0.8,
        "known_compounds": [
            {
                "name": "Some compound",
                "source": "pubchem",
                "inchikey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C",
                "smiles": "C" * 40,
                "license": "public-domain",
                "xrefs": {"pubchem_cid": 1234},
            }
        ],
    }


def _matched_compound(index: int, score: float, isotopes: int = 30) -> dict:
    """The matched compound `aggregate_sample_match_compounds` returns for one."""
    return {
        "target_compound_id": f"tc-{index:013d}",
        "target_compound_formula": _formula(index),
        "match_score": score,
        "match_category": 2,
        "sample_peak_intensity_sum": 1.0e6,
        "children": [
            {
                "target_ion_id": f"ion-{index:012d}",
                "target_compound_id": f"tc-{index:013d}",
                "ionization_mechanism_id": "mech-1",
                "target_ion_formula": "C20H30NO13-",
                "filter_params": {},
                "instrument": "orbitrap",
                "match_score": score,
                "match_category": 2,
                "sample_peak_intensity_sum": 1.0e6,
                "children": [_isotope(i, on_peak=i == 0) for i in range(isotopes)],
            }
        ],
    }


async def _search(
    monkeypatch,
    candidates: int,
    *,
    user_id: int | None = 7,
    process_id: str | None = "p-1",
    isotopes: int = 30,
):
    """Run the search task over ``candidates`` matched candidates.

    The composition finder and the matching are stood in for: what is under
    test is what the task does with their output.

    :return: The task's return value and the notification it sent.
    """
    compositions = [_composition(i) for i in range(candidates)]
    matches = [
        _matched_compound(i, score=i / candidates, isotopes=isotopes)
        for i in range(candidates)
    ]
    monkeypatch.setattr(
        cheminfo_service,
        "retrieve_compositions_by_mz",
        AsyncMock(
            return_value={
                "data": compositions,
                "results": len(compositions),
                "total": len(compositions),
            }
        ),
    )
    monkeypatch.setattr(
        cheminfo_service,
        "aggregate_sample_match_compounds",
        AsyncMock(return_value={"data": matches}),
    )
    monkeypatch.setattr(cheminfo_service, "peak_assignment_enabled", lambda: False)

    with (
        patch(
            f"{_API_FEATURES}.handle_notifications", new_callable=AsyncMock
        ) as notify,
        patch(f"{_API_FEATURES}.handle_reloads", new_callable=AsyncMock),
    ):
        result = await match_compositions_by_mz(
            sample_item_id=SAMPLE,
            mz=MZ,
            ionization_mechanism_ids=["mech-1"],
            match_params=OrbiMatchParams(),
            independent_transaction=True,
            user_id=user_id,
            process_id=process_id,
        )
    assert notify.await_count == 1, "the search is announced exactly once"
    return result, notify.await_args.args[1]


# --- The search task --------------------------------------------------------


@pytest.mark.asyncio
async def test_the_notification_carries_no_rows(monkeypatch, fake_redis):
    _, notification = await _search(monkeypatch, candidates=20)

    assert notification.status == "success"
    assert notification.process_id == "p-1"
    assert notification.data == {
        "mz": MZ,
        "sample_item_id": SAMPLE,
        "results": 20,
        "total": 20,
    }


@pytest.mark.asyncio
async def test_the_rows_are_kept_for_the_user_who_searched(monkeypatch, fake_redis):
    result, _ = await _search(monkeypatch, candidates=20, user_id=7, process_id="p-1")

    stored = await match_results.load_match_result(7, "p-1")

    assert stored["mz"] == MZ
    assert stored["sample_item_id"] == SAMPLE
    assert stored["results"] == stored["total"] == 20
    assert [row["target_compound_formula"] for row in stored["data"]] == [
        row["target_compound_formula"] for row in result["data"]
    ]
    # An isotope the sample has no peak for has a NaN m/z error, which JSON
    # cannot carry: it is kept as null, as the socket codec used to send it.
    errors = [line["match_mz_error"] for line in stored["data"][0]["children"]]
    assert errors[:3] == [0.4, 0.4, 0.4]
    assert set(errors[3:]) == {None}
    assert fake_redis.expiries == [cheminfo_config.MATCH_RESULT_TTL_SECONDS]


@pytest.mark.asyncio
async def test_a_search_with_nothing_found_is_kept_too(monkeypatch, fake_redis):
    monkeypatch.setattr(
        cheminfo_service,
        "retrieve_compositions_by_mz",
        AsyncMock(return_value={"data": [], "results": 0, "total": 0}),
    )
    with (
        patch(
            f"{_API_FEATURES}.handle_notifications", new_callable=AsyncMock
        ) as notify,
        patch(f"{_API_FEATURES}.handle_reloads", new_callable=AsyncMock),
    ):
        await match_compositions_by_mz(
            sample_item_id=SAMPLE,
            mz=MZ,
            ionization_mechanism_ids=["mech-1"],
            match_params=OrbiMatchParams(),
            independent_transaction=True,
            user_id=7,
            process_id="p-1",
        )

    assert notify.await_args.args[1].data == {
        "mz": MZ,
        "sample_item_id": SAMPLE,
        "results": 0,
        "total": 0,
    }
    assert (await match_results.load_match_result(7, "p-1"))["data"] == []


@pytest.mark.asyncio
async def test_a_search_nobody_ran_keeps_nothing(monkeypatch, fake_redis):
    result, _ = await _search(monkeypatch, candidates=5, user_id=None)

    assert fake_redis.store == {}
    # A direct caller still gets the rows back.
    assert len(result["data"]) == 5


@pytest.mark.asyncio
async def test_the_notification_stays_small_however_many_candidates(
    monkeypatch, fake_redis
):
    # The size of the production search that overran the pub/sub buffer.
    _, notification = await _search(monkeypatch, candidates=2000)

    packet = json.dumps(notification.model_dump(exclude_none=True))
    assert len(packet.encode()) < NOTIFICATION_BUDGET_BYTES
    assert "data" not in notification.data


# --- The store ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_result_is_read_back_by_the_user_who_ran_it(fake_redis):
    await match_results.save_match_result(7, "p-1", {"results": 1, "data": [1]})

    assert await match_results.load_match_result(7, "p-1") == {
        "results": 1,
        "data": [1],
    }


@pytest.mark.asyncio
async def test_another_user_finds_nothing(fake_redis):
    await match_results.save_match_result(7, "p-1", {"results": 1, "data": [1]})

    assert await match_results.load_match_result(8, "p-1") is None


@pytest.mark.asyncio
async def test_a_result_expires(fake_redis):
    await match_results.save_match_result(7, "p-1", {"results": 1, "data": [1]})

    fake_redis.now += cheminfo_config.MATCH_RESULT_TTL_SECONDS - 1
    assert await match_results.load_match_result(7, "p-1") is not None
    fake_redis.now += 1
    assert await match_results.load_match_result(7, "p-1") is None
