"""Integration tests for the per-anchor untargeted search.

Seeds a batch of two samples folded without runs, in which an anchor at m/z 181
and its M+1 at 182 carry no assignment, and drives the search with the finder
and the seeded scorer replaced by canned results - the engine's chemistry is
covered in the libraries; what is proven here is the batch-level contract:
the brightest member is searched, the result lands on it and on the anchor's
registry with its source, the isotopologue anchor points at its owner's
anchor, the other sample is measured and takes a fit of its own or stays
unassigned, and the consensus follows. See ``batch_untargeted.py``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import batch_untargeted
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    resolve_candidate,
    role_name,
    tier_name,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    run_batch_untargeted_search,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

# Two unassigned peaks per sample: the M0 at 181 and its M+1 at 182. The first
# sample is the brighter one, so it represents both anchors.
_PEAKS = {
    "S1": [("p1", 181.0707, 9000.0), ("p2", 182.0741, 700.0), ("p3", 250.1, 300.0)],
    "S2": [("p1", 181.0707, 4000.0), ("p2", 182.0741, 300.0), ("p3", 250.1, 200.0)],
}


def _unassigned_rows(sample_item_id, peaks):
    return [
        SimpleNamespace(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id=pid,
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
        for pid, mz, intensity in peaks
    ]


@pytest_asyncio.fixture
async def folded_batch(async_session_factory, pa_test_data):
    """A batch of its own with two samples folded without runs, every peak
    unassigned."""
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
                sample_batch_name=f"Untargeted {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        # The mechanism the stubbed search names: a real row, because the
        # consensus writes it into the anchor's foreign-keyed column.
        if await session.get(IonizationMechanism, "mech-h") is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id="mech-h",
                    ionization_mechanism_polarity="+",
                    ionization_mechanism="H+ (untargeted search test)",
                )
            )
        for name in _PEAKS:
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-untargeted-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 7, 4, 12, 0, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="+",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Untargeted {name}",
                    sample_item_type="sample",
                    polarity="+",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        await session.commit()
    for name, sample_item_id in samples.items():
        rows = _unassigned_rows(sample_item_id, _PEAKS[name])
        assert (
            await fold_sample_into_batch_peaks(
                sample_item_id, rows=rows, persisted=False
            )
            == batch_id
        )
    return {"batch_id": batch_id, "samples": samples}


def _frame(peaks):
    return pd.DataFrame(
        {
            "sample_peak_id": [pid for pid, _, _ in peaks],
            "mz": [mz for _, mz, _ in peaks],
            "intensity": [intensity for _, _, intensity in peaks],
        }
    )


def _canned_matches():
    """What the finder answers for S1: glucose at 181 with its M+1 at 182, the
    250 peak unmatched - whichever peaks were enumerated."""
    return (
        pd.DataFrame(
            [
                {
                    "mz": 181.0707,
                    "formula": "C6H12O6",
                    "ion": "C6H13O6+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "H+",
                    "isotopic_pattern_score": 0.92,
                    "mz_error_ppm": 0.4,
                    "intensity_error": 0.02,
                    "other_candidates": "",
                },
                {
                    "mz": 182.0741,
                    "formula": "C6H12O6",
                    "ion": "C6H13O6+",
                    "isotope_label": "M+1",
                    "ionization_mechanism": "H+",
                    "isotopic_pattern_score": 0.92,
                    "mz_error_ppm": 0.6,
                    "intensity_error": -0.05,
                    "other_candidates": "",
                },
                {
                    "mz": 250.1,
                    "formula": "---",
                    "ion": "---",
                    "isotope_label": "---",
                    "other_candidates": "",
                },
            ]
        ),
        {},
    )


@pytest.fixture
def stubbed_engine(monkeypatch, folded_batch):
    """The finder, the peak read, the mechanisms and the seeded scorer replaced
    by canned answers keyed on the sample being searched or measured."""
    samples = folded_batch["samples"]
    by_id = {sample_id: name for name, sample_id in samples.items()}
    calls = {"search": [], "score": []}

    def load_peaks(sample):
        return _frame(_PEAKS[by_id[sample.sample_item_id]])

    def assign(peaks, config, heuristics=None, targets=None):
        calls["search"].append(list(targets))
        return _canned_matches()

    async def mechanisms(sample):
        return (
            ["mech-h"],
            [
                SimpleNamespace(
                    ionization_mechanism="H+", ionization_mechanism_id="mech-h"
                )
            ],
        )

    def notations(mechs):
        return ["H+"], {"H+": "mech-h"}

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01)

    async def seeded(sample, seeds, params):
        calls["score"].append((by_id[sample.sample_item_id], set(seeds)))
        # S2's peaks pair to glucose's envelope: the M0 and the M+1, not the 250.
        ion_by_seed = {("C6H12O6", "mech-h"): "ion-glc"}
        fit_by_ion = {"ion-glc": 0.81}
        errors = {
            ("ion-glc", "p1"): {"mz_error_ppm": 0.9},
            ("ion-glc", "p2"): {"mz_error_ppm": 1.1},
        }
        return ion_by_seed, fit_by_ion, errors, pd.DataFrame({"x": [1]})

    monkeypatch.setattr(batch_untargeted, "load_sample_peaks", load_peaks)
    monkeypatch.setattr(batch_untargeted, "assign_compositions", assign)
    monkeypatch.setattr(batch_untargeted, "fetch_sample_mechanisms", mechanisms)
    monkeypatch.setattr(batch_untargeted, "_untargeted_ionization_notations", notations)
    monkeypatch.setattr(batch_untargeted, "default_match_params", match_params)
    monkeypatch.setattr(batch_untargeted, "score_seeds", seeded)
    return calls


async def _anchors_by_mz(session_factory, batch_id):
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
    return {round(anchor.mz): anchor for anchor in anchors}


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


async def test_the_search_runs_on_the_brightest_member_and_annotates_the_anchor(
    async_session_factory, folded_batch, stubbed_engine
):
    batch_id, samples = folded_batch["batch_id"], folded_batch["samples"]
    counts = await run_batch_untargeted_search(batch_id, PeakAssignmentConfig())

    # Three unassigned anchors (181, 182, 250), all represented by the brighter
    # S1, so one search over S1 with the three representatives as targets -
    # and the finder was asked about the 181 peak among them.
    assert counts["anchors_searched"] == 3
    assert counts["samples_searched"] == 1
    assert len(stubbed_engine["search"]) == 1
    assert 181.0707 in stubbed_engine["search"][0]

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    glucose, isotopologue, bare = anchors[181], anchors[182], anchors[250]
    assert counts["anchors_annotated"] == 2
    assert glucose.consensus_formula == "C6H12O6"
    assert glucose.consensus_ion_formula == "C6H13O6+"
    assert glucose.consensus_tier == "assigned"
    assert resolve_candidate(glucose.candidates, 0)["source"] == "untargeted"
    # The M+1's anchor folds under the M0's: the family link came with the search.
    assert isotopologue.isotopologue_of == glucose.batch_peak_id
    assert isotopologue.consensus_formula == "C6H12O6"
    # Nothing was found for 250; it stays a first-class unassigned trace.
    assert bare.consensus_tier == "unassigned"
    assert bare.n_present == 2

    s1 = await _members(async_session_factory, samples["S1"])
    assert role_name(s1["p1"].role) == "M0"
    assert tier_name(s1["p1"].tier) == "assigned"
    assert s1["p1"].fit_score == pytest.approx(0.92)
    assert role_name(s1["p2"].role) == "iso_child"
    assert s1["p2"].owner_batch_peak_id == glucose.batch_peak_id
    assert s1["p3"].candidate is None


async def test_the_other_sample_is_measured_and_takes_a_fit_of_its_own(
    async_session_factory, folded_batch, stubbed_engine
):
    batch_id, samples = folded_batch["batch_id"], folded_batch["samples"]
    counts = await run_batch_untargeted_search(batch_id, PeakAssignmentConfig())

    # Both samples hold members of the annotated anchors; S1's were written by
    # the search itself, so S2 is the one the seeded chain measured.
    assert counts["samples_rescored"] == 2
    scored = [name for name, _ in stubbed_engine["score"]]
    assert sorted(scored) == ["S1", "S2"]
    assert all(seeds == {("C6H12O6", "mech-h")} for _, seeds in stubbed_engine["score"])
    assert counts["members_propagated"] == 2  # S2's p1 and p2; p3 unreached

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    s2 = await _members(async_session_factory, samples["S2"])
    assert s2["p1"].fit_score == pytest.approx(0.81)
    assert (
        resolve_candidate(anchors[181].candidates, s2["p1"].candidate)["formula"]
        == "C6H12O6"
    )
    assert role_name(s2["p1"].role) == "M0"
    assert tier_name(s2["p1"].tier) in {"assigned", "candidate"}
    assert role_name(s2["p2"].role) == "iso_child"
    assert s2["p2"].owner_batch_peak_id == anchors[181].batch_peak_id
    assert s2["p3"].candidate is None  # the envelope did not reach it
    # Two members now back the consensus.
    assert anchors[181].support_fraction == pytest.approx(1.0)
    assert anchors[181].provenance["n_assigned"] == 2


async def test_a_batch_with_nothing_unassigned_searches_nothing(
    async_session_factory, folded_batch, stubbed_engine
):
    batch_id = folded_batch["batch_id"]
    first = await run_batch_untargeted_search(batch_id, PeakAssignmentConfig())
    assert first["anchors_annotated"] == 2
    # A second pass finds one anchor still unassigned (250) and nothing for it.
    second = await run_batch_untargeted_search(batch_id, PeakAssignmentConfig())
    assert second["anchors_searched"] == 1
    assert second["anchors_annotated"] == 0


async def test_the_route_launches_for_an_editor_and_refuses_a_guest(
    editor_client, guest_client, folded_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = folded_batch["batch_id"]

    launched = {}

    async def fake_task(**kwargs):
        launched.update(kwargs)
        return {"status": "success", "message": "", "data": {}}

    from mascope_backend.api.new.peak_assignments import batch_peaks_routes

    monkeypatch.setattr(batch_peaks_routes, "search_batch_untargeted", fake_task)

    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/search-untargeted"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/search-untargeted",
        json={"config": {"mz_precision_ppm": 2.5}},
    )
    assert accepted.status_code == 202, accepted.text
    assert "please wait" in accepted.json()["message"]
    assert launched["sample_batch_id"] == batch_id
    assert launched["config"].mz_precision_ppm == 2.5
    assert launched["independent_transaction"] is True
