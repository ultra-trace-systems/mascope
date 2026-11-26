# ruff: noqa: F811 - the batch and its fixtures come from test_batch_checks_api
"""Integration tests for the batch's identification level (step 5.8c).

On the batch the isotope checks' tests seed (``test_batch_checks_api``): a
bromide cluster with its 81Br line stamped and locked, a labelled cluster
whose 14N twin is present, a labelled reading with no twin, and a neutral seen
through two channels. One sample is folded from a run of its own, whose rows
here carry the levels that run stated. The rules are the pure tests'
(``unit/.../test_batch_level.py``); what is proven here is the stage's
contract: the level written on each batch peak off the real records, read only
while they stand, nothing else moving, the consensus keeping the record while
its reading stands, and the ledger, the members' export rows and a sample's
detail serving it. See ``batch_level.py``.
"""

import pytest
from sqlalchemy import select, update
from test_batch_checks_api import (  # noqa: F401 - the batch and its fixtures
    N_SAMPLES,
    RUN_SAMPLE,
    _anchors,
    _fold,
    checks_batch,
    noisy,
)

from mascope_backend.api.new.peak_assignments.batch_checks import run_batch_checks
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    run_batch_isotopologues,
)
from mascope_backend.api.new.peak_assignments.batch_level import run_batch_levels
from mascope_backend.api.new.peak_assignments.batch_peaks import is_stale
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    recompute_batch_consensus,
)
from mascope_backend.db import BatchPeak, PeakAssignment


pytestmark = pytest.mark.asyncio

#: A rival of the bromide cluster's reading that holds no bromine.
NO_BROMINE = {
    "formula": "C3H5NO4",
    "ion_formula": "C3H4NO4-",
    "record": "rival_set",
    "fit": 0.88,
    "test": "open",
    "detail": "within the tie margin",
}

#: What the run sample's run stated of each row, as its rows carry it.
STATED = {
    "p": {
        "level": "5a",
        "evidence": "one rival left open: C3H5NO4",
        "lift": "exclude C3H5NO4",
        "rivals": [NO_BROMINE],
        "split": {
            "level": "4a",
            "evidence": "the split is pinned: one plausible decomposition",
            "lift": "a context list for the run's chemistry; MS/MS",
            "fact": {"fact": "one_decomposition", "record": "ionization_mechanism"},
        },
    },
    "x": {"level": "4b", "evidence": "the split is open", "lift": "settle it"},
    "y": {"level": "4b", "evidence": "the split is open", "lift": "settle it"},
    "h": {"level": "3b", "evidence": "a base of this formula", "lift": "a list"},
}


async def _state_levels(session_factory, checks_batch, stated=STATED):
    """Give the run sample's rows the levels their run stated."""
    rows = checks_batch["run_rows"]
    async with session_factory() as session:
        for key, record in stated.items():
            await session.execute(
                update(PeakAssignment)
                .where(PeakAssignment.peak_assignment_id == rows[key])
                .values(
                    level=record["level"],
                    provenance={"identification_level": record},
                )
            )
        await session.commit()


# Read off the stored provenance by the name the stage writes, not through the
# constant: a renamed key orphans every record a server holds.
def _level(anchor):
    record = (anchor.provenance or {}).get("identification_level")
    return None if is_stale(record) else record


async def _levels(session_factory, checks_batch, stated=STATED):
    await run_batch_checks(checks_batch["batch_id"], batch_peak_run_id="run-checks")
    await _state_levels(session_factory, checks_batch, stated)
    summary = await run_batch_levels(
        checks_batch["batch_id"], batch_peak_run_id="run-level"
    )
    return summary, await _anchors(session_factory, checks_batch["batch_id"])


async def test_the_lock_lifts_the_bromide_and_the_twin_pins_the_label(
    async_session_factory, checks_batch
):
    before = await _anchors(async_session_factory, checks_batch["batch_id"])
    summary, anchors = await _levels(async_session_factory, checks_batch)

    bromide = _level(anchors["p"])
    assert bromide["level"] == "4a"
    assert bromide["moved_by"] == ["halogen_lock"]
    assert bromide["members"] == {"5a": 1}
    assert bromide["members_under_batch"] == {"4a": 1}
    assert bromide["read_from"] == checks_batch["samples"][RUN_SAMPLE]
    assert bromide["batch_peak_run_id"] == "run-level"
    [out] = bromide["rivals"]
    assert (out["test"], out["record"]) == ("isotopes", "batch_checks.halogen_lock")
    assert f"stamped in {N_SAMPLES} of {N_SAMPLES} samples" in out["detail"]
    lock = bromide["batch_facts"]["halogen_lock"]
    assert (lock["stamping_run_id"], lock["batch_peak_run_id"]) == (
        "run-stamp",
        "run-checks",
    )
    assert (bromide["formula"], bromide["ion_formula"]) == ("C2H4O2", "C2H4BrO2-")

    cluster = _level(anchors["x"])
    assert (cluster["level"], cluster["moved_by"]) == ("4a", ["label_twin"])
    assert "the split is pinned by the label twin" in cluster["evidence"]
    # No twin read present, no pin.
    assert _level(anchors["y"])["level"] == "4b"
    # A member's 3b is kept: the batch's lifts end at 4 and 4a.
    assert _level(anchors["h"])["level"] == "3b"
    # A batch peak no member's run stated a level of has none.
    assert _level(anchors["u"]) is None
    assert _level(anchors["line"]) is None

    assert summary["anchors_read"] == 4
    assert summary["levels"] == {"3b": 1, "4a": 2, "4b": 1}
    assert (summary["lifted"], summary["refuted"]) == (2, 0)
    assert (summary["by_halogen_lock"], summary["by_label_twin"]) == (1, 1)
    assert summary["anchors_moved"] == 0
    # Nothing else moves: no consensus, tier, or run row.
    for key, anchor in anchors.items():
        if anchor is None:
            continue
        assert (anchor.consensus_formula, anchor.consensus_tier) == (
            before[key].consensus_formula,
            before[key].consensus_tier,
        )
    async with async_session_factory() as session:
        stated = (
            await session.execute(
                select(PeakAssignment.level).where(
                    PeakAssignment.peak_assignment_id == checks_batch["run_rows"]["p"]
                )
            )
        ).scalar_one()
    assert stated == "5a"


async def test_a_lock_whose_stamping_no_longer_stands_lifts_nothing(
    async_session_factory, checks_batch
):
    batch_id = checks_batch["batch_id"]
    await run_batch_checks(batch_id, batch_peak_run_id="run-checks")
    # A later stamping replaces the record the lock was read off.
    await run_batch_isotopologues(batch_id, batch_peak_run_id="run-stamp-2")
    await _state_levels(async_session_factory, checks_batch)

    summary = await run_batch_levels(batch_id, batch_peak_run_id="run-level")
    anchors = await _anchors(async_session_factory, batch_id)

    bromide = _level(anchors["p"])
    assert bromide["level"] == "5a"
    assert "halogen_lock" not in bromide["batch_facts"]
    assert summary["by_halogen_lock"] == 0


async def test_a_stamped_bromine_line_refutes_a_halogen_free_reading(
    async_session_factory, checks_batch
):
    batch_id = checks_batch["batch_id"]
    anchors = await _anchors(async_session_factory, batch_id)
    line = anchors["line"]
    rows = checks_batch["run_rows"]
    # The line's own members read it as a monoisotopic formula holding no
    # bromine, which the batch stamped as the bromide cluster's 81Br line.
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == line.batch_peak_id)
            .values(consensus_formula="C4H6O5", consensus_ion_formula="C4H5O5-")
        )
        await session.execute(
            update(PeakAssignment)
            .where(PeakAssignment.peak_assignment_id == rows["line"])
            .values(
                assigned_formula="C4H6O5",
                ion_formula="C4H5O5-",
                role="M0",
                tier="assigned",
                level="4a",
                provenance={
                    "identification_level": {
                        "level": "4a",
                        "evidence": "every rival out",
                    }
                },
            )
        )
        await session.commit()

    summary, anchors = await _levels(async_session_factory, checks_batch)

    refuted = _level(anchors["line"])
    assert (refuted["level"], refuted["members"]) == ("5b", {"4a": 1})
    assert refuted["evidence"].startswith(
        "refuted by the batch's isotopes: the batch stamped this peak as the 81Br "
        "line of C2H4BrO2-"
    )
    assert refuted["rivals"][0]["formula"] == "C2H4O2"
    assert refuted["rivals"][0]["line"] == "81Br"
    stamp = refuted["batch_facts"]["stamped_as"]
    assert stamp["parent_batch_peak_id"] == anchors["p"].batch_peak_id
    assert stamp["batch_peak_run_id"] == "run-stamp"
    assert (summary["refuted"], summary["by_stamped_halogen_line"]) == (1, 1)


async def test_the_consensus_keeps_the_record_while_the_reading_stands(
    async_session_factory, checks_batch
):
    batch_id = checks_batch["batch_id"]
    _, anchors = await _levels(async_session_factory, checks_batch)
    await recompute_batch_consensus(
        batch_id, {anchor.batch_peak_id for anchor in anchors.values() if anchor}
    )
    anchors = await _anchors(async_session_factory, batch_id)
    assert _level(anchors["x"])["level"] == "4a"
    # Every sample reads the labelled cluster as another formula: its level,
    # read for the old one, is kept marked stale; the others stand.
    for i in range(N_SAMPLES):
        await _fold(checks_batch, i, overrides={"x": ("C11H20O3", "C11H20O3^NO3-")})
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["x"].consensus_formula == "C11H20O3"
    assert _level(anchors["x"]) is None
    assert is_stale(anchors["x"].provenance["identification_level"])
    assert _level(anchors["p"])["level"] == "4a"


async def test_a_member_reading_another_formula_is_not_counted(
    async_session_factory, checks_batch
):
    # The run sample's row reads the bromide cluster's peak as another formula:
    # its level is about that formula, not the batch peak's reading.
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(PeakAssignment.peak_assignment_id == checks_batch["run_rows"]["p"])
            .values(assigned_formula="C3H5NO4")
        )
        await session.commit()

    _, anchors = await _levels(async_session_factory, checks_batch)

    assert _level(anchors["p"]) is None


async def test_a_new_reading_replaces_the_last(async_session_factory, checks_batch):
    batch_id = checks_batch["batch_id"]
    await _levels(async_session_factory, checks_batch)
    # The run sample's row of the labelled cluster no longer states a level.
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(PeakAssignment.peak_assignment_id == checks_batch["run_rows"]["x"])
            .values(level=None, provenance=None)
        )
        await session.commit()

    await run_batch_levels(batch_id, batch_peak_run_id="run-level-2")
    anchors = await _anchors(async_session_factory, batch_id)

    assert _level(anchors["x"]) is None
    assert "identification_level" not in (anchors["x"].provenance or {})
    assert _level(anchors["p"])["batch_peak_run_id"] == "run-level-2"


async def test_the_ledger_the_members_and_a_sample_s_detail_serve_it(
    guest_client, async_session_factory, checks_batch
):
    batch_id, samples = checks_batch["batch_id"], checks_batch["samples"]
    _, anchors = await _levels(async_session_factory, checks_batch)
    ids = {key: anchor.batch_peak_id for key, anchor in anchors.items() if anchor}

    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}", params={"min_n_present": 1}
    )
    assert response.status_code == 200, response.text
    ledger = {row["batch_peak_id"]: row for row in response.json()["data"]}
    bromide = ledger[ids["p"]]
    assert bromide["level"] == "4a"
    assert bromide["level_evidence"] == _level(anchors["p"])["evidence"]
    assert bromide["level_lift"] == _level(anchors["p"])["lift"]
    assert bromide["identification_level"]["moved_by"] == ["halogen_lock"]
    # The members' split stays beside it.
    assert (bromide["member_level"], bromide["member_levels"]) == ("5a", {"5a": 1})
    assert ledger[ids["u"]]["level"] is None

    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}/members",
        params={"sample_item_id": samples["S03"]},
    )
    assert response.status_code == 200, response.text
    members = {row["batch_peak_id"]: row for row in response.json()["data"]}
    assert members[ids["p"]]["batch_level"] == "4a"
    assert members[ids["p"]]["batch_level_lift"] == bromide["level_lift"]
    assert members[ids["u"]]["batch_level"] is None

    async def detail(peak_assignment_id):
        response = await guest_client.get(
            f"/api/peak-assignments/sample/{samples[RUN_SAMPLE]}/assignment/"
            f"{peak_assignment_id}"
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["data"]
        return row

    rows = checks_batch["run_rows"]
    own = await detail(rows["p"])
    assert own["level"] == "5a"
    borrowed = own["batch_level"]
    assert (borrowed["level"], borrowed["stated"], borrowed["borrowed"]) == (
        "4a",
        "5a",
        True,
    )
    assert borrowed["batch_peak_id"] == ids["p"]
    # Where the batch moves nothing, the row shows its own level alone.
    assert (await detail(rows["h"]))["batch_level"] is None
