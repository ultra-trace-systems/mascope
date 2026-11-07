"""Integration tests for the batch's series detection (step 4.1).

Seeds a batch of two samples folded without runs: six assigned compounds and,
one CH2 above and below each, the twelve anchors the CH2 walk reaches - the
fewest a family needs. Most of them nobody assigned; one is held at candidate
on the formula the walk proposes, one on another. The mode's chemistry and the
seeded scorer are stubbed - the walk and its decoys are covered in the
library, the scorer in its own suite; what is proven here is the batch-level
contract: the family is committed, every proposal is measured through the
propagation chain or recorded beside the reading it confirms, a better
measured reading keeps its member, the anchors carry the series for as long
as their consensus names its formula, a fold during the run keeps them, a
second run on the unchanged ledger finds them again and so does one after a
run that stopped partway, no reader serves a record its anchor does not read
yet, an earlier run's records come off unless their mode could not be walked,
the run is recorded as a batch run, and a run of a sample reads the records
back.
See ``batch_series.py``.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select, update

from mascope_backend.api.new.peak_assignments import (
    batch_peaks_routes,
    batch_series,
    batch_untargeted,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    SERIES_KEY,
    resolve_candidate,
    role_name,
    tier_name,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SERIES,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_series import (
    SOURCE_SERIES,
    ModeChemistry,
    run_batch_series_detection,
    series_outcome,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import evidence_for
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.service import batch_series_by_peak
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    IonizationMechanism,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.series import ion_mz
from mascope_tools.composition.utils import parse_formula_tokens


pytestmark = pytest.mark.asyncio

MECHANISM = "mech-series-hso4"
#: A real adduct no other suite names: the mechanism table's notations are
#: unique, and the integration database is shared by the whole run.
NOTATION = "[M+HSO4]-"

SOURCES = ["C8H10O5", "C9H12O3", "C10H16O4", "C7H8O6", "C11H18O2", "C6H6O7"]

#: The reading the candidate one CH2 below the first compound is held on.
RIVAL = "C6H4O6"


def _step(formula, n):
    counts = parse_formula_tokens(formula)
    return f"C{counts['C'] + n}H{counts['H'] + 2 * n}O{counts['O']}"


def _mz(formula):
    return ion_mz(parse_formula_tokens(formula), NOTATION)


def _ion(formula):
    counts = parse_formula_tokens(formula)
    return f"C{counts['C']}H{counts['H'] + 1}O{counts['O'] + 4}S-"


#: Peak id -> (formula the walk proposes or the compound's own, what the
#: sample's row reads: (tier, formula, fit) or None for no reading).
def _peaks():
    peaks = {}
    for i, formula in enumerate(SOURCES):
        peaks[f"src-{i}"] = (formula, ("assigned", formula, 0.95))
        peaks[f"up-{i}"] = (_step(formula, 1), None)
        peaks[f"down-{i}"] = (_step(formula, -1), None)
    # Held at candidate on the formula the walk proposes for it.
    peaks["up-0"] = (_step(SOURCES[0], 1), ("candidate", _step(SOURCES[0], 1), 0.7))
    return peaks


def _rows(sample_item_id, readings):
    rows = []
    for peak_id, (formula, reading) in readings.items():
        tier, assigned, fit = reading or ("unassigned", None, None)
        rows.append(
            SimpleNamespace(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=fold_run_id(sample_item_id),
                sample_item_id=sample_item_id,
                sample_peak_id=peak_id,
                sample_peak_mz=_mz(formula),
                sample_peak_intensity=5000.0,
                role="M0" if assigned else "unassigned",
                assigned_formula=assigned,
                ion_formula=_ion(assigned) if assigned else None,
                ionization_mechanism_id=MECHANISM if assigned else None,
                source="untargeted" if assigned else None,
                tier=tier,
                fit_score=fit,
                mz_error_ppm=0.1 if assigned else None,
                owner_peak_assignment_id=None,
                provenance=None,
            )
        )
    return rows


@pytest_asyncio.fixture
async def series_batch(async_session_factory, pa_test_data):
    """A batch of two samples whose six assigned compounds have their CH2
    homologues in the ledger. In S1 the rival candidate is measured well, in S2
    poorly."""
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    samples = {}
    async with async_session_factory() as session:
        dataset_id = (
            await session.execute(
                select(SampleBatch.dataset_id).where(
                    SampleBatch.sample_batch_id == pa_test_data["sample_batch_id"]
                )
            )
        ).scalar_one()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Series {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, MECHANISM) is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=MECHANISM,
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=NOTATION,
                )
            )
        for name in ("S1", "S2"):
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-series-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 7, 12, 0, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Series {name}",
                    sample_item_type="sample",
                    polarity="-",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        await session.commit()
    for name, fit in (("S1", 0.95), ("S2", 0.40)):
        readings = _peaks()
        below = _step(SOURCES[0], -1)
        readings["down-0"] = (below, ("candidate", RIVAL, fit))
        assert (
            await fold_sample_into_batch_peaks(
                samples[name], rows=_rows(samples[name], readings), persisted=False
            )
            == batch_id
        )
    return {"batch_id": batch_id, "samples": samples}


#: Formula -> the fit the seeded scorer gives it in either sample. The
#: homologue one CH2 above the last compound is not in it: its envelope reaches
#: no peak.
_FITS = {
    _step(formula, n): 0.85
    for formula in SOURCES
    for n in (1, -1)
    if _step(formula, n) != _step(SOURCES[-1], 1)
}


@pytest.fixture
def stubbed(monkeypatch):
    """The mode's chemistry and the seeded scorer, canned."""
    scored = []

    async def chemistry(sample_item_id, config):
        return ModeChemistry(tolerance_ppm=3.0, labelled=False, profile="NO3")

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01, mz_tolerance=5)

    async def seeded(sample, seeds, params, *, config=None):
        scored.append(set(seeds))
        ion_by_seed, fit_by_ion, errors = {}, {}, {}
        for formula, mechanism in seeds:
            if formula not in _FITS:
                continue
            ion = f"ion-{formula}"
            ion_by_seed[(formula, mechanism)] = ion
            fit_by_ion[ion] = _FITS[formula]
            for peak_id, (proposed, _) in _peaks().items():
                if proposed == formula:
                    errors[(ion, peak_id)] = {"mz_error_ppm": 0.2}
        return ion_by_seed, fit_by_ion, errors, pd.DataFrame({"x": [1]})

    monkeypatch.setattr(batch_series, "_mode_chemistry", chemistry)
    monkeypatch.setattr(batch_untargeted, "default_match_params", match_params)
    monkeypatch.setattr(batch_untargeted, "score_seeds", seeded)
    return scored


async def _anchors(session_factory, batch_id):
    async with session_factory() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
    # Keyed by the peak id the anchor's members carry: one per formula here.
    by_mz = {round(anchor.mz, 3): anchor for anchor in anchors}
    return {
        peak_id: by_mz[round(_mz(formula), 3)]
        for peak_id, (formula, _) in _peaks().items()
    }


async def _members(session_factory, sample_item_id):
    async with session_factory() as session:
        members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.sample_item_id == sample_item_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return {member.sample_peak_id: member for member in members}


async def test_the_family_is_committed_and_its_proposals_measured(
    async_session_factory, series_batch, stubbed
):
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    counts = await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id="run-series"
    )

    [mode] = counts["units"]
    ch2 = next(unit for unit in mode["units"] if unit["unit"] == "CH2")
    assert (ch2["n_links"], ch2["significant"]) == (12, True)
    assert mode["tolerance_ppm"] == 3.0
    assert counts["families_committed"] == 1
    assert counts["fdr"] == 0.0
    # Twelve proposals: one confirms its candidate, eleven are measured.
    assert counts["anchors_proposed"] == 12
    assert counts["anchors_corroborated"] == 1
    assert counts["anchors_measured"] == 11
    assert counts["samples_measured"] == 2
    # Each measured seed is scored once per sample.
    assert len(stubbed) == 2
    assert all(len(seeds) == 11 for seeds in stubbed)

    anchors = await _anchors(async_session_factory, batch_id)
    homologue = anchors["up-1"]
    assert homologue.consensus_formula == _step(SOURCES[1], 1)
    # As the engine spells the ion: C10H14O3 through bisulfate.
    assert homologue.consensus_ion_formula == "C10H15O7S-"
    assert homologue.ionization_mechanism_id == MECHANISM
    entry = resolve_candidate(homologue.candidates, 0)
    assert entry["source"] == SOURCE_SERIES
    record = homologue.provenance[SERIES_KEY]
    assert record["has_anchor"] is True
    assert (record["unit"], record["step"], record["depth"]) == ("CH2", 1, 1)
    assert record["root_batch_peak_id"] == anchors["src-1"].batch_peak_id
    assert record["root_formula"] == SOURCES[1]
    assert record["batch_peak_run_id"] == "run-series"

    s2 = await _members(async_session_factory, samples["S2"])
    assert s2["up-1"].fit_score == pytest.approx(0.85)
    assert role_name(s2["up-1"].role) == "M0"
    assert tier_name(s2["up-1"].tier) in {"assigned", "candidate"}


async def test_a_proposal_the_envelope_does_not_reach_leaves_its_anchor_as_it_was(
    async_session_factory, series_batch, stubbed
):
    batch_id = series_batch["batch_id"]
    counts = await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    anchors = await _anchors(async_session_factory, batch_id)
    unreached = anchors["up-5"]
    assert unreached.consensus_tier == "unassigned"
    assert SERIES_KEY not in (unreached.provenance or {})
    # Proposed, but not in a series once the consensus was taken: this one,
    # and the rival whose better measured sample keeps its consensus.
    assert counts["anchors_in_series"] == counts["anchors_proposed"] - 1 - 1


async def test_a_candidate_on_the_proposed_formula_carries_the_series_untouched(
    async_session_factory, series_batch, stubbed
):
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    anchors = await _anchors(async_session_factory, batch_id)
    confirmed = anchors["up-0"]
    assert confirmed.consensus_formula == _step(SOURCES[0], 1)
    assert confirmed.consensus_tier == "candidate"
    assert confirmed.provenance[SERIES_KEY]["formula"] == _step(SOURCES[0], 1)
    for name in ("S1", "S2"):
        members = await _members(async_session_factory, samples[name])
        assert members["up-0"].fit_score == pytest.approx(0.7)


async def test_a_better_measured_reading_keeps_its_member(
    async_session_factory, series_batch, stubbed
):
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    below = _step(SOURCES[0], -1)
    # S1 reads the rival better than the series' formula measures there; S2
    # reads it worse.
    assert evidence_for(0.95, RIVAL) > evidence_for(0.85, below)
    assert evidence_for(0.40, RIVAL) < evidence_for(0.85, below)
    await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    anchors = await _anchors(async_session_factory, batch_id)
    rival = anchors["down-0"]
    s1 = await _members(async_session_factory, samples["S1"])
    s2 = await _members(async_session_factory, samples["S2"])
    assert resolve_candidate(rival.candidates, s1["down-0"].candidate)["formula"] == (
        RIVAL
    )
    assert s1["down-0"].fit_score == pytest.approx(0.95)
    assert resolve_candidate(rival.candidates, s2["down-0"].candidate)["formula"] == (
        below
    )
    assert s2["down-0"].fit_score == pytest.approx(0.85)
    # The anchor carries the series exactly when its consensus is the series'.
    carried = SERIES_KEY in (rival.provenance or {})
    assert carried == (rival.consensus_formula == below)


def _records(anchors):
    """Peak id -> the series record its anchor carries, for those carrying one."""
    return {
        peak_id: anchor.provenance[SERIES_KEY]
        for peak_id, anchor in anchors.items()
        if SERIES_KEY in (anchor.provenance or {})
    }


async def test_a_second_detection_on_an_unchanged_ledger_keeps_the_series(
    async_session_factory, series_batch, stubbed
):
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    first = await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id="run-1"
    )
    anchors = await _anchors(async_session_factory, batch_id)
    before = _records(anchors)
    assert len(before) == 10
    # Most of the members it measured now read assigned: the batch's tier for
    # them is what a walk starts from, unless it is the series' own.
    lifted = {k for k in before if anchors[k].consensus_tier == "assigned"}
    assert len(lifted) == 9
    members = {
        name: await _members(async_session_factory, samples[name]) for name in samples
    }

    second = await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id="run-2"
    )
    after = _records(await _anchors(async_session_factory, batch_id))
    assert set(after) == set(before)
    assert {k: {**r, "batch_peak_run_id": None} for k, r in after.items()} == {
        k: {**r, "batch_peak_run_id": None} for k, r in before.items()
    }
    assert {r["batch_peak_run_id"] for r in after.values()} == {"run-2"}
    # The same walk against the same decoys: every member it lifted is reached
    # again, beside the reading it holds, and nothing is measured anew.
    assert second["units"] == first["units"]
    assert second["anchors_proposed"] == first["anchors_proposed"]
    assert second["anchors_corroborated"] == 10
    assert second["members_assigned"] == 0
    for name in samples:
        now = await _members(async_session_factory, samples[name])
        assert {k: (m.candidate, m.tier, m.fit_score) for k, m in now.items()} == {
            k: (m.candidate, m.tier, m.fit_score) for k, m in members[name].items()
        }


async def test_a_fold_while_the_proposals_are_measured_keeps_the_series(
    async_session_factory, series_batch, stubbed, monkeypatch
):
    """A sample folding into the batch while the detection measures recomputes
    the anchors it touches from their members as they stand then."""
    batch_id = series_batch["batch_id"]
    every = {
        a.batch_peak_id
        for a in (await _anchors(async_session_factory, batch_id)).values()
    }
    propagate = batch_series._propagate_to_sample

    async def a_fold_lands_first(*args, **kwargs):
        await recompute_batch_consensus(batch_id, every)
        return await propagate(*args, **kwargs)

    monkeypatch.setattr(batch_series, "_propagate_to_sample", a_fold_lands_first)
    counts = await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    assert counts["anchors_in_series"] == counts["anchors_proposed"] - 1 - 1
    assert len(_records(await _anchors(async_session_factory, batch_id))) == 10


class _Interrupted(Exception):
    """A progress notification that could not be sent."""


@pytest.mark.parametrize("stops", ["cancelled", "notification"])
async def test_an_interrupted_detection_is_found_again(
    async_session_factory, series_batch, stubbed, monkeypatch, stops
):
    """A run that stops while it measures leaves its record on the members it
    measured, so the next one walks to them rather than from them."""
    batch_id = series_batch["batch_id"]
    propagate = batch_series._propagate_to_sample
    calls = []

    async def dies_on_the_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return await propagate(*args, **kwargs)

    async def fails_on_the_second(notification):
        calls.append(1)
        if len(calls) == 2:
            raise _Interrupted()

    if stops == "cancelled":
        monkeypatch.setattr(batch_series, "_propagate_to_sample", dies_on_the_second)
        raised, process_id = asyncio.CancelledError, None
    else:
        monkeypatch.setattr(
            batch_series, "send_progress_user_notification", fails_on_the_second
        )
        raised, process_id = _Interrupted, "process-1"
    with pytest.raises(raised):
        await run_batch_series_detection(
            batch_id, PeakAssignmentConfig(), process_id=process_id
        )
    # The first sample was measured, the second was not.
    assert len(stubbed) == 1
    anchors = await _anchors(async_session_factory, batch_id)
    left = _records(anchors)
    # Every member it lifted, and the candidate it confirmed; each beside the
    # formula it names, the anchors it did not reach having lost theirs.
    assert len(left) == 10
    assert all(r["formula"] == anchors[k].consensus_formula for k, r in left.items())
    assert {anchors[k].consensus_tier for k in left} - {"candidate"} == {"assigned"}

    monkeypatch.setattr(batch_series, "_propagate_to_sample", propagate)
    counts = await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    assert counts["families_committed"] == 1
    assert counts["anchors_corroborated"] == 10
    assert len(_records(await _anchors(async_session_factory, batch_id))) == 10


async def test_while_the_proposals_are_measured_no_reader_serves_them_unmeasured(
    async_session_factory, series_batch, stubbed, monkeypatch
):
    """Between the records going on and the members being measured, an anchor
    carries a record its consensus does not name yet; a sample's run and the
    ledger read none there."""
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    propagate = batch_series._propagate_to_sample
    seen = {}

    async def look_first(*args, **kwargs):
        if not seen:
            anchors = await _anchors(async_session_factory, batch_id)
            seen["raw"] = _records(anchors)
            seen["consensus"] = {
                k: anchor.consensus_formula for k, anchor in anchors.items()
            }
            seen["sample"] = await batch_series_by_peak(samples["S1"])
            seen["ledger"] = {
                k: _batch_peak_meta(anchor)["series"] for k, anchor in anchors.items()
            }
        return await propagate(*args, **kwargs)

    monkeypatch.setattr(batch_series, "_propagate_to_sample", look_first)
    await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    # Already on before anything was measured: every proposal's record.
    assert len(seen["raw"]) == 12
    assert seen["consensus"]["up-1"] is None
    # Served only where the anchor already reads the formula: the candidate
    # the series confirms.
    assert set(seen["sample"]) == {"up-0"}
    assert {k for k, series in seen["ledger"].items() if series} == {"up-0"}


async def test_an_earlier_runs_record_comes_off(
    async_session_factory, series_batch, stubbed
):
    batch_id = series_batch["batch_id"]
    anchors = await _anchors(async_session_factory, batch_id)
    stale = anchors["src-2"]
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == stale.batch_peak_id)
            .values(
                provenance={
                    **(stale.provenance or {}),
                    SERIES_KEY: {"formula": SOURCES[2], "has_anchor": True},
                }
            )
        )
        await session.commit()
    await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    anchors = await _anchors(async_session_factory, batch_id)
    assert SERIES_KEY not in anchors["src-2"].provenance
    assert anchors["src-2"].consensus_formula == SOURCES[2]


async def test_a_family_short_of_its_floor_writes_nothing(
    async_session_factory, series_batch, stubbed, monkeypatch
):
    batch_id = series_batch["batch_id"]
    plan = batch_series.plan_series

    def stricter(*args, **kwargs):
        return plan(*args, **{**kwargs, "min_links": 13})

    monkeypatch.setattr(batch_series, "plan_series", stricter)
    counts = await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    assert (counts["families_committed"], counts["anchors_proposed"]) == (0, 0)
    assert counts["fdr"] is None
    assert stubbed == []
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["up-1"].consensus_tier == "unassigned"


async def test_a_run_of_a_sample_reads_its_batch_s_series_back(
    async_session_factory, series_batch, stubbed
):
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    # A series block that names no formula is no record.
    anchors = await _anchors(async_session_factory, batch_id)
    malformed = anchors["src-3"]
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == malformed.batch_peak_id)
            .values(provenance={**malformed.provenance, SERIES_KEY: {"unit": "CH2"}})
        )
        await session.commit()
    records = await batch_series_by_peak(samples["S2"])
    assert "src-3" not in records
    assert records["up-1"]["formula"] == _step(SOURCES[1], 1)
    assert records["up-0"]["formula"] == _step(SOURCES[0], 1)
    # A peak whose batch peak carries no series is not in it.
    assert "src-1" not in records
    assert "up-5" not in records


async def test_the_route_runs_it_as_a_batch_run(
    editor_client,
    guest_client,
    async_session_factory,
    series_batch,
    stubbed,
    monkeypatch,
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = series_batch["batch_id"]
    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/detect-series"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/detect-series",
        json={"config": {"mz_precision_ppm": 2.5}},
    )
    assert accepted.status_code == 202, accepted.text
    assert "please wait" in accepted.json()["message"]
    async with async_session_factory() as session:
        runs = (
            (
                await session.execute(
                    select(BatchPeakRun).where(BatchPeakRun.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
    series = [run for run in runs if run.action == ACTION_SERIES]
    assert len(series) == 1
    assert series[0].status == "completed"
    assert series[0].is_current == 1
    assert series[0].config["mz_precision_ppm"] == 2.5
    assert series[0].summary["anchors_proposed"] == 12
    anchors = await _anchors(async_session_factory, batch_id)
    assert (
        anchors["up-1"].provenance[SERIES_KEY]["batch_peak_run_id"]
        == series[0].batch_peak_run_id
    )


async def test_the_route_refuses_a_decoy(editor_client, series_batch, monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "detect_batch_series", fake_task)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{series_batch['batch_id']}/detect-series",
        json={"config": {"decoy": {"mz_shift_ppm": 9}}},
    )
    assert response.status_code == 422, response.text
    assert "the series detection takes none" in response.text
    assert launched == []


async def test_the_ledger_shows_the_series_and_an_earlier_run_keeps_it(
    guest_client, series_batch, stubbed
):
    batch_id = series_batch["batch_id"]
    run_id = await start_run(batch_id, ACTION_SERIES)
    await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id=run_id
    )
    await complete_run(batch_id, run_id)

    async def ledger(**params):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch_id}",
            params={"min_n_present": 1, **params},
        )
        assert response.status_code == 200, response.text
        return {round(row["mz"], 3): row for row in response.json()["data"]}

    live = await ledger()
    homologue = live[round(_mz(_step(SOURCES[1], 1)), 3)]
    assert homologue["series"]["root_formula"] == SOURCES[1]
    assert homologue["series"]["batch_peak_run_id"] == run_id
    assert live[round(_mz(SOURCES[1]), 3)]["series"] is None

    # A later run snapshots this one; read back, the snapshot still says it.
    later = await start_run(batch_id, ACTION_SERIES)
    await complete_run(batch_id, later)
    before = await ledger(batch_peak_run_id=run_id)
    assert before[round(_mz(_step(SOURCES[1], 1)), 3)]["series"] == homologue["series"]
    assert before[round(_mz(SOURCES[1]), 3)]["series"] is None


async def test_a_mode_whose_chemistry_cannot_be_read_is_skipped(
    async_session_factory, series_batch, stubbed, monkeypatch
):
    batch_id = series_batch["batch_id"]
    await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id="run-1"
    )
    found = _records(await _anchors(async_session_factory, batch_id))
    assert len(found) == 10
    stubbed.clear()

    async def unreadable(sample_item_id, config):
        raise FileNotFoundError("the sample's file has moved")

    monkeypatch.setattr(batch_series, "_mode_chemistry", unreadable)
    counts = await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id="run-2"
    )
    assert (counts["modes_skipped"], counts["modes_walked"]) == (1, 0)
    assert counts["anchors_proposed"] == 0
    assert stubbed == []
    # The mode was not walked, so it found nothing to take off.
    assert _records(await _anchors(async_session_factory, batch_id)) == found
    assert "could not be resolved" in series_outcome(counts, batch_id)["message"]


async def test_the_route_refuses_while_another_run_rewrites_the_ledger(
    editor_client, series_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "detect_batch_series", fake_task)
    await start_run(series_batch["batch_id"], ACTION_SERIES)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{series_batch['batch_id']}/detect-series"
    )
    assert response.status_code == 409, response.text
    assert launched == []
