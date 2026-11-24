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
back. A second batch assigns nothing and holds two CF2 ladders and their 13C
lines: the run lists the ladders by mass alone, leaves the lines out and
writes nothing on its batch peaks.
See ``batch_series.py``.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from pass_runs import step_outcome  # the tests root, on sys.path
from sqlalchemy import select, update
from stage_stubs import (  # the tests root, on sys.path
    frame,
    stub_sample_reads,
    stub_seeded_scorer,
)
from stage_stubs import mechanism as mechanism_row

from mascope_backend.api.new.peak_assignments import (
    batch_peaks_routes,
    batch_series,
    pipeline,
)
from mascope_backend.api.new.peak_assignments.batch_lines import batch_series_by_peak
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    DIAGNOSTIC_ISOTOPOLOGUES_KEY,
    SERIES_KEY,
    is_stale,
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
from mascope_backend.api.new.peak_assignments.batch_stages import (
    STAGES,
    assign_batch,
    stage_standing,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import evidence_for
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
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
from mascope_tools.composition.series import (
    CONTAMINANT_UNITS,
    ISOTOPE_SPACINGS_DA,
    ion_mz,
)
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


async def _new_batch(async_session_factory, pa_test_data, label="series"):
    """An empty batch of two negative-mode samples, S1 and S2, in the test
    dataset; the mechanism the suite reads is registered."""
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
                    filename=f"orbi-{label}-{name}-{sample_file_id}.zarr",
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
    return batch_id, samples


@pytest_asyncio.fixture
async def series_batch(async_session_factory, pa_test_data):
    """A batch of two samples whose six assigned compounds have their CH2
    homologues in the ledger. In S1 the rival candidate is measured well, in S2
    poorly."""
    batch_id, samples = await _new_batch(async_session_factory, pa_test_data)
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

    def peaks_of(sample_item_id):
        return frame(
            (peak_id, _mz(formula), 5000.0)
            for peak_id, (formula, _) in _peaks().items()
        )

    monkeypatch.setattr(batch_series, "_mode_chemistry", chemistry)
    # The proposals are measured on each member through the stage list, which
    # reads the sample's peaks and mode as a run does.
    stub_sample_reads(monkeypatch, peaks_of, [mechanism_row(NOTATION, MECHANISM)])
    monkeypatch.setattr(pipeline, "default_match_params", match_params)
    stub_seeded_scorer(
        monkeypatch, seeded, peaks_of, ion_formula=lambda formula, _mech: _ion(formula)
    )
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
    # The corroborated one is measured too, on the members that do not read
    # its formula yet (MAS-769): here none, so it is left as it is.
    assert counts["anchors_measured"] == 12
    assert counts["samples_measured"] == 2
    # Each measured seed is scored once per sample.
    assert len(stubbed) == 2
    assert all(len(seeds) == 12 for seeds in stubbed)

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


async def test_a_sample_whose_proposals_cannot_be_measured_is_named(
    series_batch, stubbed, monkeypatch
):
    """A sample whose measurement raises is counted and skipped, the other
    still measured, and named, as its stage records it (MAS-1060)."""
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    measure = batch_series._propagate_to_sample

    async def failing(sample_batch_id, sample_item_id, *args, **kwargs):
        if sample_item_id == samples["S2"]:
            raise FileNotFoundError("S2's peak file has moved")
        return await measure(sample_batch_id, sample_item_id, *args, **kwargs)

    monkeypatch.setattr(batch_series, "_propagate_to_sample", failing)
    counts = await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id="run-series"
    )
    assert counts["samples_measured"] == 1
    assert counts["samples_failed"] == 1
    assert counts["samples_unread"] == [samples["S2"]]


async def test_a_proposal_the_envelope_does_not_reach_leaves_its_anchor_as_it_was(
    async_session_factory, series_batch, stubbed
):
    batch_id = series_batch["batch_id"]
    counts = await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    anchors = await _anchors(async_session_factory, batch_id)
    unreached = anchors["up-5"]
    assert unreached.consensus_tier == "unassigned"
    assert not _carries(unreached)
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


async def test_a_candidate_the_series_corroborates_is_judged_again_at_once(
    async_session_factory, series_batch, stubbed, only_its_own_stage
):
    """Run as a batch stage, the detection judges again every sample a record
    of its corroborates (MAS-765): the series is the second observation the
    candidate on its formula lacked, and its members read the tier a run of
    their sample gives the reading now, with no new run. What they measured
    stays."""
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    outcome = await assign_batch(sample_batch_id=batch_id, from_stage="series")
    [stage] = outcome["data"]["stages"]
    judged = stage["outcomes"][-1]["data"]
    assert judged["samples_judged"] == 2
    anchors = await _anchors(async_session_factory, batch_id)
    confirmed = anchors["up-0"]
    assert confirmed.consensus_formula == _step(SOURCES[0], 1)
    assert confirmed.consensus_tier == "assigned"
    for name in ("S1", "S2"):
        members = await _members(async_session_factory, samples[name])
        assert tier_name(members["up-0"].tier) == "assigned"
        assert members["up-0"].fit_score == pytest.approx(0.7)
        # What the series measured carries its mark; what it only
        # corroborated does not.
        assert members["up-1"].batch_written["stage"] == "series"
        assert members["up-0"].batch_written is None
        # A member the sample's own run read, that no decision touched, keeps
        # the run's tier.
        assert tier_name(members["src-0"].tier) == "assigned"


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
    carried = _carries(rival)
    assert carried == (rival.consensus_formula == below)


#: A deprotonation that reads, as another neutral, the ion the walk's adduct
#: makes of a neutral: C8H10O3 + HSO4- and C8H12O7S - H+ are one ion.
SAME_ION_MECHANISM = "mech-series-dep"
SAME_ION_NOTATION = "[M-H]- (series test)"
SAME_ION_RIVAL = "C8H12O7S"


def _lines_record(formula, mechanism, ion):
    return {
        "formula": formula,
        "ion_formula": ion,
        "ionization_mechanism_id": mechanism,
        "batch_peak_run_id": "run-lines",
        "predicted": [],
    }


@pytest_asyncio.fixture
async def stamped_rival_batch(async_session_factory, pa_test_data):
    """The series batch with two line parents held poorly in both samples, so
    the series' formula would take every member of each: the rival one CH2
    below the first compound, another ion than the walk proposes there, and
    one CH2 below the second compound C8H12O7S deprotonated, the ion the walk
    proposes there through its own adduct. The stamped lines' record of each
    stands on its reading (MAS-1091, MAS-1144)."""
    batch_id, samples = await _new_batch(
        async_session_factory, pa_test_data, label="series-stamped"
    )
    async with async_session_factory() as session:
        if await session.get(IonizationMechanism, SAME_ION_MECHANISM) is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=SAME_ION_MECHANISM,
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=SAME_ION_NOTATION,
                )
            )
            await session.commit()
    below, same_ion = _step(SOURCES[0], -1), _step(SOURCES[1], -1)
    for name in ("S1", "S2"):
        readings = _peaks()
        readings["down-0"] = (below, ("candidate", RIVAL, 0.40))
        readings["down-1"] = (same_ion, ("candidate", SAME_ION_RIVAL, 0.40))
        rows = _rows(samples[name], readings)
        held = next(row for row in rows if row.sample_peak_id == "down-1")
        held.ionization_mechanism_id = SAME_ION_MECHANISM
        held.ion_formula = _ion(same_ion)
        await fold_sample_into_batch_peaks(samples[name], rows=rows, persisted=False)
    async with async_session_factory() as session:
        for formula, record in (
            (RIVAL, _lines_record(RIVAL, MECHANISM, _ion(RIVAL))),
            (
                SAME_ION_RIVAL,
                _lines_record(SAME_ION_RIVAL, SAME_ION_MECHANISM, _ion(same_ion)),
            ),
        ):
            parent = (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == batch_id,
                        BatchPeak.consensus_formula == formula,
                    )
                )
            ).scalar_one()
            parent.provenance = {
                **(parent.provenance or {}),
                DIAGNOSTIC_ISOTOPOLOGUES_KEY: record,
            }
        await session.commit()
    return {"batch_id": batch_id, "samples": samples}


async def test_a_pass_moves_a_stamped_parent_within_its_ion_and_ends_current(
    async_session_factory, stamped_rival_batch, stubbed, only_these_stages
):
    """The series yields to the isotope lines of another ion (MAS-1091): its
    proposal onto a parent read as another ion is not applied. A proposal of
    the parent's own ion is (MAS-1144): the parent's record stands after the
    series' consensus recompute, naming the reading it moved to. So the pass
    ends with every stage current, and a second has nothing to run."""
    only_these_stages("series")
    batch_id = stamped_rival_batch["batch_id"]
    outcome = await assign_batch(sample_batch_id=batch_id)
    standing = await stage_standing(batch_id)
    assert {e["stage"]: e["state"] for e in standing["stages"]} == {
        spec.stage.value: "current" for spec in STAGES
    }
    assert standing["stale_stages"] == []
    assert (await assign_batch(sample_batch_id=batch_id))["status"] == "skipped"

    anchors = await _anchors(async_session_factory, batch_id)
    parent = anchors["down-0"]
    assert parent.consensus_formula == RIVAL
    record = parent.provenance[DIAGNOSTIC_ISOTOPOLOGUES_KEY]
    assert not is_stale(record)
    assert SERIES_KEY not in parent.provenance
    moved = anchors["down-1"]
    proposed = _step(SOURCES[1], -1)
    assert (moved.consensus_formula, moved.ionization_mechanism_id) == (
        proposed,
        MECHANISM,
    )
    assert moved.provenance[DIAGNOSTIC_ISOTOPOLOGUES_KEY] == _lines_record(
        proposed, MECHANISM, _ion(proposed)
    )
    assert moved.provenance[SERIES_KEY]["formula"] == proposed
    for name in ("S1", "S2"):
        members = await _members(
            async_session_factory, stamped_rival_batch["samples"][name]
        )
        assert (
            resolve_candidate(parent.candidates, members["down-0"].candidate)["formula"]
            == RIVAL
        )
        assert (
            resolve_candidate(moved.candidates, members["down-1"].candidate)["formula"]
            == proposed
        )
    # The rest of the family is measured as before.
    assert anchors["up-1"].consensus_formula == _step(SOURCES[1], 1)

    series = next(s for s in outcome["data"]["stages"] if s["stage"] == "series")
    counts = series["outcomes"][0]["data"]
    assert counts["anchors_yielded"] == 1
    assert counts["anchors_proposed"] == 11


def _carries(anchor):
    """Whether an anchor carries a series record a consensus has not marked
    stale: one kept stale stands for no reading."""
    record = (anchor.provenance or {}).get(SERIES_KEY)
    return record is not None and not is_stale(record)


def _records(anchors):
    """Peak id -> the series record its anchor carries, for those carrying one
    a consensus has not marked stale."""
    return {
        peak_id: anchor.provenance[SERIES_KEY]
        for peak_id, anchor in anchors.items()
        if SERIES_KEY in (anchor.provenance or {})
        and not is_stale(anchor.provenance[SERIES_KEY])
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
    # Half the members it measured now read assigned, the series their second
    # observation, as a run of their sample counts it. The rest are held at
    # candidate by the rules such a run asks of the same reading, the search's
    # in a run: an expected line absent, or the oxygen lattice of C5H4O7. The
    # batch's tier for them is what a walk starts from, unless it is the
    # series' own.
    lifted = {k for k in before if anchors[k].consensus_tier == "assigned"}
    assert lifted == {"down-1", "down-2", "down-3", "up-2", "up-3"}
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


async def test_a_fold_then_an_interruption_keeps_the_series(
    async_session_factory, series_batch, stubbed, monkeypatch
):
    """A fold that lands before the first sample is measured marks the records
    of the anchors not measured yet stale rather than taking them off
    (MAS-772): the run that stops after it leaves the family's records for the
    next run to find, as a run that stops with no fold does."""
    batch_id = series_batch["batch_id"]
    every = {
        a.batch_peak_id
        for a in (await _anchors(async_session_factory, batch_id)).values()
    }
    propagate = batch_series._propagate_to_sample
    calls = []

    async def fold_then_die(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            await recompute_batch_consensus(batch_id, every)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return await propagate(*args, **kwargs)

    monkeypatch.setattr(batch_series, "_propagate_to_sample", fold_then_die)
    with pytest.raises(asyncio.CancelledError):
        await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    anchors = await _anchors(async_session_factory, batch_id)
    # The fold kept every record, marked stale where its anchor did not read
    # the proposal yet; the measurement then made those it reached stand.
    assert len(_records(anchors)) == 10
    kept = [a for a in anchors.values() if SERIES_KEY in (a.provenance or {})]
    assert len(kept) > 10
    monkeypatch.setattr(batch_series, "_propagate_to_sample", propagate)
    counts = await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    assert counts["families_committed"] == 1
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


async def _ledger_state(session_factory, batch_id, samples):
    """Peak id -> (consensus formula, consensus tier, standing series formula,
    and per sample the formula and tier its member reads): the ledger as a
    reader compares two of them."""
    anchors = await _anchors(session_factory, batch_id)
    members = {
        name: await _members(session_factory, sample_item_id)
        for name, sample_item_id in samples.items()
    }
    state = {}
    for peak, anchor in anchors.items():
        series = (anchor.provenance or {}).get(SERIES_KEY)
        per_sample = []
        for name in sorted(members):
            member = members[name].get(peak)
            if member is None:
                continue
            identity = resolve_candidate(anchor.candidates, member.candidate)
            per_sample.append((name, identity.get("formula"), tier_name(member.tier)))
        state[peak] = (
            anchor.consensus_formula,
            anchor.consensus_tier,
            None if series is None or is_stale(series) else series["formula"],
            tuple(per_sample),
        )
    return state


async def test_an_interrupted_then_clean_detection_leaves_one_clean_runs_ledger(
    async_session_factory, pa_test_data, series_batch, stubbed, monkeypatch
):
    """A detection that stops after the first sample, then a clean one, leave
    the ledger one clean detection leaves (MAS-769): the clean one measures
    the proposals it finds corroborated on the members that do not read them
    yet, the second sample's among them."""
    batch_id, samples = series_batch["batch_id"], series_batch["samples"]
    propagate = batch_series._propagate_to_sample
    calls = []

    async def dies_on_the_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return await propagate(*args, **kwargs)

    monkeypatch.setattr(batch_series, "_propagate_to_sample", dies_on_the_second)
    with pytest.raises(asyncio.CancelledError):
        await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    monkeypatch.setattr(batch_series, "_propagate_to_sample", propagate)
    await run_batch_series_detection(batch_id, PeakAssignmentConfig())
    interrupted = await _ledger_state(async_session_factory, batch_id, samples)

    # The same batch again, detected once, cleanly.
    clean_id, clean_samples = await _new_batch(
        async_session_factory, pa_test_data, label="series-clean"
    )
    for name, fit in (("S1", 0.95), ("S2", 0.40)):
        readings = _peaks()
        readings["down-0"] = (_step(SOURCES[0], -1), ("candidate", RIVAL, fit))
        await fold_sample_into_batch_peaks(
            clean_samples[name],
            rows=_rows(clean_samples[name], readings),
            persisted=False,
        )
    await run_batch_series_detection(clean_id, PeakAssignmentConfig())
    clean = await _ledger_state(async_session_factory, clean_id, clean_samples)
    assert interrupted == clean
    # The second sample's members of the proposals were measured.
    measured = [
        peak
        for peak, (_, _, series, per_sample) in interrupted.items()
        if series and all(formula == series for _, formula, _ in per_sample)
    ]
    assert len(measured) >= 9


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
    assert not _carries(anchors["src-2"])
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
    only_its_own_stage,
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
    step = step_outcome(series[0], "series")
    assert series[0].config["settings"]["mz_precision_ppm"] == 2.5
    assert step["config"]["mz_precision_ppm"] == 2.5
    assert step["data"]["anchors_proposed"] == 12
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

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
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

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    await start_run(series_batch["batch_id"], ACTION_SERIES)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{series_batch['batch_id']}/detect-series"
    )
    assert response.status_code == 409, response.text
    assert launched == []


#: Two CF2 ladders no sample assigns a peak of: perfluoroalkyl anions from C2F5-
#: and the perfluorinated acids' deprotonated ions from trifluoroacetate, seven
#: rungs each, so twelve links between them - the fewest a family needs. Each
#: rung's 13C line, a tenth as bright, is a batch peak too; linked, the lines
#: would make two ladders more.
CF2 = next(unit for unit in CONTAMINANT_UNITS if unit.formula == "CF2")
LADDER_FEET = {"acid": 112.98563, "alkyl": 118.99257}
C13 = ISOTOPE_SPACINGS_DA[0]


def _ladder_peaks():
    """Peak id -> its m/z, lightest ladder first."""
    return {
        f"{name}-{k}": foot + k * CF2.mass
        for name, foot in LADDER_FEET.items()
        for k in range(7)
    }


def _ladder_lines():
    """Line id -> its m/z: each rung's 13C line."""
    return {f"{peak}-13c": mz + C13 for peak, mz in _ladder_peaks().items()}


def _unassigned_rows(sample_item_id, mz_by_peak, intensity=5000.0):
    return [
        SimpleNamespace(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id=peak_id,
            sample_peak_mz=mz,
            sample_peak_intensity=intensity,
            role="unassigned",
            assigned_formula=None,
            ion_formula=None,
            ionization_mechanism_id=None,
            source=None,
            tier="unassigned",
            fit_score=None,
            mz_error_ppm=None,
            owner_peak_assignment_id=None,
            provenance=None,
        )
        for peak_id, mz in mz_by_peak.items()
    ]


@pytest_asyncio.fixture
async def ladder_batch(async_session_factory, pa_test_data):
    """A batch of two samples that assigns nothing, its peaks two CF2 ladders."""
    batch_id, samples = await _new_batch(
        async_session_factory, pa_test_data, label="ladder"
    )
    for sample_item_id in samples.values():
        rows = _unassigned_rows(sample_item_id, _ladder_peaks()) + _unassigned_rows(
            sample_item_id, _ladder_lines(), intensity=500.0
        )
        assert (
            await fold_sample_into_batch_peaks(
                sample_item_id, rows=rows, persisted=False
            )
            == batch_id
        )
    return {"batch_id": batch_id, "samples": samples}


async def test_a_ladder_nothing_anchors_is_listed_on_the_run_and_written_nowhere(
    async_session_factory, ladder_batch, stubbed
):
    batch_id = ladder_batch["batch_id"]
    counts = await run_batch_series_detection(
        batch_id, PeakAssignmentConfig(), batch_peak_run_id="run-ladder"
    )

    # Nothing assigned to walk from, and the mode linked all the same.
    assert (counts["modes_walked"], counts["modes_anchored"]) == (1, 0)
    assert (counts["families_committed"], counts["anchors_proposed"]) == (0, 0)
    assert counts["ladders_committed"] == 1
    assert counts["ladder_families"] == ["fluorinated"]
    assert (counts["ladder_members"], counts["ladder_fdr"]) == (14, 0.0)
    [mode] = counts["units"]
    assert mode["units"] == []
    by_unit = {unit["unit"]: unit for unit in mode["ladders"]}
    assert (by_unit["CF2"]["n_links"], by_unit["CF2"]["significant"]) == (12, True)
    assert (by_unit["C2H6OSi"]["significant"], by_unit["C2H6OSi"]["ladders"]) == (
        False,
        [],
    )

    async with async_session_factory() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
    by_mz = {round(anchor.mz, 4): anchor for anchor in anchors}
    peaks = _ladder_peaks()
    assert [ladder["batch_peak_ids"] for ladder in by_unit["CF2"]["ladders"]] == [
        [by_mz[round(peaks[f"{name}-{k}"], 4)].batch_peak_id for k in range(7)]
        for name in LADDER_FEET
    ]
    assert by_unit["CF2"]["ladders"][0]["mz"][0] == round(LADDER_FEET["acid"], 6)
    # The rungs' 13C lines are batch peaks nothing reads, read as lines by
    # their brightest member and linked into no ladder.
    lines = {round(mz, 4) for mz in _ladder_lines().values()}
    assert lines <= set(by_mz)

    # A ladder proposes no formula: nothing is measured and no batch peak
    # carries a record or a reading.
    assert stubbed == []
    assert counts["members_assigned"] == 0
    assert not any(_carries(anchor) for anchor in anchors)
    assert all(anchor.consensus_formula is None for anchor in anchors)
    outcome = series_outcome(counts, batch_id)
    assert outcome["status"] == "success"
    assert outcome["message"].startswith(
        "No series was walked from an assigned batch peak: the batch has none "
        "to walk from, or no batch peak unassigned or at candidate to step "
        "onto. By mass alone, the fluorinated ladder beat the decoys"
    )
