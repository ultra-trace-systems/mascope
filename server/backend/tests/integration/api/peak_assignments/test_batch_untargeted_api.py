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
    search_outcome,
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
    return await _fold_batch(async_session_factory, pa_test_data, _PEAKS)


async def _fold_batch(async_session_factory, pa_test_data, peaks):
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
        for name in peaks:
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
        rows = _unassigned_rows(sample_item_id, peaks[name])
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
    # S2's peaks pair to glucose's envelope: the M0 and the M+1, not the 250.
    return _stub_engine(
        monkeypatch,
        folded_batch["samples"],
        _PEAKS,
        lambda targets: _canned_matches(),
        {"C6H12O6": ("ion-glc", 0.81, ["p1", "p2"])},
    )


def _stub_engine(monkeypatch, samples, peaks, matches, pairings):
    """Stub the engine around the search.

    :param matches: The finder's answer for the m/z values it is asked to
        enumerate.
    :param pairings: Formula -> (ion id, fit, the peaks its envelope pairs
        with) in whichever sample the seeded scorer measures it.
    """
    by_id = {sample_id: name for name, sample_id in samples.items()}
    calls = {"search": [], "score": [], "seeded_fits": []}

    def load_peaks(sample):
        return _frame(peaks[by_id[sample.sample_item_id]])

    def assign(peaks, config, heuristics=None, targets=None, scoring=None):
        calls["search"].append(list(targets))
        return matches(list(targets))

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
        return SimpleNamespace(isotope_abundance_threshold=0.01, mz_tolerance=5)

    async def seeded_fits(sample, params, seeds):
        # The search's own re-score: the finder's reading measured again as an
        # ion, which is what the rows are tiered on. Canned here for the same
        # reason the finder is - these tests are about the pass, not the fit.
        calls["seeded_fits"].append((by_id[sample.sample_item_id], set(seeds)))
        return {seed: 0.88 for seed in seeds}

    async def seeded(sample, seeds, params):
        calls["score"].append((by_id[sample.sample_item_id], set(seeds)))
        ion_by_seed, fit_by_ion, errors = {}, {}, {}
        for formula, mechanism in seeds:
            if formula not in pairings:
                continue
            ion, fit, paired = pairings[formula]
            ion_by_seed[(formula, mechanism)] = ion
            fit_by_ion[ion] = fit
            for peak in paired:
                errors[(ion, peak)] = {"mz_error_ppm": 0.9}
        return ion_by_seed, fit_by_ion, errors, pd.DataFrame({"x": [1]})

    monkeypatch.setattr(batch_untargeted, "load_sample_peaks", load_peaks)
    monkeypatch.setattr(batch_untargeted, "assign_compositions", assign)
    monkeypatch.setattr(batch_untargeted, "fetch_sample_mechanisms", mechanisms)
    monkeypatch.setattr(batch_untargeted, "_untargeted_ionization_notations", notations)
    monkeypatch.setattr(batch_untargeted, "default_match_params", match_params)
    monkeypatch.setattr(batch_untargeted, "score_seeds", seeded)
    monkeypatch.setattr(batch_untargeted, "_seeded_fits", seeded_fits)
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
    # The fit is the re-score's (0.88), not the finder's ranking score (0.92):
    # the searched sample's own rows are tiered the same way the propagated
    # ones are, so one anchor's members are all on one scale.
    assert s1["p1"].fit_score == pytest.approx(0.88)
    assert [name for name, _ in stubbed_engine["seeded_fits"]] == ["S1"]
    assert role_name(s1["p2"].role) == "iso_child"
    assert s1["p2"].owner_batch_peak_id == glucose.batch_peak_id
    assert s1["p3"].candidate is None


async def test_a_secondary_channel_the_mode_declares_is_searched_once(
    folded_batch, stubbed_engine, monkeypatch
):
    # The urea profile names [M+NH4]+ secondary. A mode that declares it already
    # searches it, and searched again every neutral through it would come back
    # as another reading of its own ion - on the batch path as on a run.
    proton = SimpleNamespace(
        ionization_mechanism="H+", ionization_mechanism_id="mech-h"
    )
    ammonium = SimpleNamespace(
        ionization_mechanism="[M+NH4]+", ionization_mechanism_id="mech-nh4"
    )
    searched = []

    async def mechanisms(sample):
        return ["mech-h", "mech-nh4"], [proton, ammonium]

    async def secondary(notations, polarity):
        return [ammonium] if "[M+NH4]+" in notations else []

    def notations(mechs):
        searched.append([mechanism.ionization_mechanism_id for mechanism in mechs])
        return ["H+"], {"H+": "mech-h"}

    monkeypatch.setattr(batch_untargeted, "fetch_sample_mechanisms", mechanisms)
    monkeypatch.setattr(batch_untargeted, "fetch_mechanisms_by_notation", secondary)
    monkeypatch.setattr(batch_untargeted, "_untargeted_ionization_notations", notations)

    await run_batch_untargeted_search(
        folded_batch["batch_id"], PeakAssignmentConfig(profile="UR")
    )

    assert ["mech-h", "mech-nh4"] in searched
    assert all(ids.count("mech-nh4") == 1 for ids in searched)


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


async def test_a_sample_that_cannot_be_measured_is_skipped_and_the_pass_finishes(
    async_session_factory, folded_batch, stubbed_engine, monkeypatch
):
    """One sample's measurement raising - a peak file that has moved, a spectrum
    that will not read - is counted and skipped. The other samples are still
    measured, and the deferred consensus pass still runs over every anchor the
    search set out from, so the anchors describe the members they now have."""
    batch_id, samples = folded_batch["batch_id"], folded_batch["samples"]
    scorer = batch_untargeted.score_seeds  # the fixture's canned pairing

    async def failing(sample, seeds, params):
        if sample.sample_item_id == samples["S2"]:
            raise FileNotFoundError("S2's peak file has moved")
        return await scorer(sample, seeds, params)

    recomputed = []
    recompute = batch_untargeted.recompute_batch_consensus

    async def recording(sample_batch_id, batch_peak_ids):
        recomputed.append(set(batch_peak_ids))
        return await recompute(sample_batch_id, batch_peak_ids)

    monkeypatch.setattr(batch_untargeted, "score_seeds", failing)
    monkeypatch.setattr(batch_untargeted, "recompute_batch_consensus", recording)

    counts = await run_batch_untargeted_search(batch_id, PeakAssignmentConfig())

    assert counts["samples_failed"] == 1
    assert counts["samples_rescored"] == 1
    assert counts["anchors_annotated"] == 2
    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    # The searched sample carries the composition; the one that could not be
    # measured keeps its members as they were.
    s1 = await _members(async_session_factory, samples["S1"])
    assert role_name(s1["p1"].role) == "M0"
    s2 = await _members(async_session_factory, samples["S2"])
    assert s2["p1"].candidate is None
    assert s2["p2"].candidate is None
    # The deferred pass ran once, over every anchor searched, and the consensus
    # counts the one supporter the anchor has.
    assert recomputed == [{anchor.batch_peak_id for anchor in anchors.values()}]
    assert anchors[181].consensus_formula == "C6H12O6"
    assert anchors[181].provenance["n_assigned"] == 1
    outcome = search_outcome(counts, batch_id)
    assert outcome["status"] == "partial"
    assert "1 sample could not be read and was skipped." in outcome["message"]


async def test_a_sample_that_will_not_read_leaves_its_anchors_unsearched(
    async_session_factory, folded_batch, stubbed_engine, monkeypatch
):
    """The search itself raising on a sample is the same stand-down: its anchors
    stay unsearched and the failure is counted, rather than the pass aborting."""
    batch_id = folded_batch["batch_id"]

    def unreadable(sample):
        raise FileNotFoundError("the peak file has moved")

    monkeypatch.setattr(batch_untargeted, "load_sample_peaks", unreadable)

    counts = await run_batch_untargeted_search(batch_id, PeakAssignmentConfig())

    # The brighter sample represents every anchor, so the one search there was
    # is the one that failed.
    assert counts["anchors_searched"] == 3
    assert counts["samples_failed"] == 1
    assert counts["samples_searched"] == 0
    assert counts["anchors_annotated"] == 0
    assert counts["members_propagated"] == 0
    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    assert all(anchor.consensus_formula is None for anchor in anchors.values())
    assert search_outcome(counts, batch_id)["status"] == "partial"


# --- an isotopologue line represented in another sample than its ion ----------
#
# Glucose's M0 is brightest in S1 and its M+1 in S2, so the two anchors are
# searched in different samples. S2's own search, where the M0 is no target,
# reads the M+1 on its own (C8H11N3) and claims the line above it, at 183, as
# that reading's M+1. Searched without the M+1, S2 reads the 183 line on its
# own (C9H10O3).

_SPLIT_PEAKS = {
    "S1": [
        ("p1", 181.0707, 9000.0),
        ("p2", 182.0741, 300.0),
        ("p3", 183.0775, 100.0),
    ],
    "S2": [
        ("p1", 181.0707, 4000.0),
        ("p2", 182.0741, 700.0),
        ("p3", 183.0775, 400.0),
    ],
}


def _reading(mz, formula, ion, label="M0"):
    return {
        "mz": mz,
        "formula": formula,
        "ion": ion,
        "isotope_label": label,
        "ionization_mechanism": "H+",
        "isotopic_pattern_score": 0.9,
        "mz_error_ppm": 0.5,
        "intensity_error": 0.0,
        "other_candidates": "",
    }


def _split_matches(targets):
    rows = []
    if 181.0707 in targets:
        rows += [
            _reading(181.0707, "C6H12O6", "C6H13O6+"),
            _reading(182.0741, "C6H12O6", "[13C]C5H13O6+", label="13C"),
        ]
    if 182.0741 in targets:
        rows += [
            _reading(182.0741, "C8H11N3", "C8H12N3+"),
            _reading(183.0775, "C8H11N3", "[13C]C7H12N3+", label="13C"),
        ]
    elif 183.0775 in targets:
        rows.append(_reading(183.0775, "C9H10O3", "C9H11O3+"))
    return pd.DataFrame(rows), {}


@pytest_asyncio.fixture
async def split_batch(async_session_factory, pa_test_data):
    return await _fold_batch(async_session_factory, pa_test_data, _SPLIT_PEAKS)


@pytest.mark.parametrize("order", ["as folded", "reversed"])
async def test_a_line_searched_in_another_sample_than_its_ion_reads_as_its_line(
    async_session_factory, split_batch, monkeypatch, order
):
    batch_id, samples = split_batch["batch_id"], split_batch["samples"]
    calls = _stub_engine(
        monkeypatch,
        samples,
        _SPLIT_PEAKS,
        _split_matches,
        {
            "C6H12O6": ("ion-glc", 0.81, ["p1", "p2"]),
            "C9H10O3": ("ion-c9", 0.7, ["p3"]),
            "C8H11N3": ("ion-c8", 0.3, ["p2", "p3"]),
        },
    )
    if order == "reversed":
        grouped = batch_untargeted.group_by_sample
        monkeypatch.setattr(
            batch_untargeted,
            "group_by_sample",
            lambda chosen: dict(reversed(list(grouped(chosen).items()))),
        )

    counts = await run_batch_untargeted_search(batch_id, PeakAssignmentConfig())

    # S1 searched for the M0, S2 for the M+1 and the 183 line, and S2 again
    # without the M+1, which its own search had read on its own.
    assert sorted(sorted(targets) for targets in calls["search"]) == [
        [181.0707],
        [182.0741, 183.0775],
        [183.0775],
    ]
    assert counts["samples_searched"] == 2
    assert counts["samples_researched"] == 1
    assert counts["isotopologues_carried"] == 1
    assert counts["anchors_annotated"] == 3
    assert (
        "1 batch peak was read as an isotope line"
        in (search_outcome(counts, batch_id)["message"])
    )

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    glucose, line, above = anchors[181], anchors[182], anchors[183]
    assert line.isotopologue_of == glucose.batch_peak_id
    assert line.consensus_formula == "C6H12O6"
    # The standalone reading reached neither the registry nor the vote, and
    # nothing measured it in the other sample.
    assert {entry["formula"] for entry in line.candidates} == {"C6H12O6"}
    assert not any("C8H11N3" in {f for f, _ in seeds} for _, seeds in calls["score"])
    # The line above is read on its own, as S2 reads it searched without the
    # M+1 - not left as a line of an ion the batch does not hold.
    assert above.consensus_formula == "C9H10O3"
    assert {entry["formula"] for entry in above.candidates} == {"C9H10O3"}
    assert above.isotopologue_of is None

    for name in ("S1", "S2"):
        members = await _members(async_session_factory, samples[name])
        assert role_name(members["p1"].role) == "M0"
        assert role_name(members["p2"].role) == "iso_child"
        assert members["p2"].owner_batch_peak_id == glucose.batch_peak_id
        assert role_name(members["p3"].role) == "M0"
