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

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import (
    batch_peaks_controller,
    batch_untargeted,
    service,
)
from mascope_backend.api.new.peak_assignments.batch_curation import (
    release_batch_peak_curation,
    run_batch_peak_curation,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    PERSISTENCE_KEY,
    TIER_REASONS_KEY,
    candidate_index,
    resolve_candidate,
    role_name,
    tier_name,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SEARCH,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    run_batch_untargeted_search,
    search_outcome,
)
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    fold_assignment_id,
    fold_run_id,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.envelope_tolerance import (
    FORM_CENTRE_WIDTH,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)
from mascope_tools.composition.mass_accuracy import (
    PRED_SIGMA_PPM,
    SampleMassAccuracy,
)
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM
from mascope_tools.composition.reagents import reagent_library


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


def _tof_resolution(mz):
    """The searched file's resolving power, as its instrument functions say."""
    return 8000.0


def _stub_engine(monkeypatch, samples, peaks, matches, pairings):
    """Stub the engine around the search.

    :param matches: The finder's answer for the m/z values it is asked to
        enumerate.
    :param pairings: Formula -> (ion id, fit, the peaks its envelope pairs
        with) in whichever sample the seeded scorer measures it.
    """
    by_id = {sample_id: name for name, sample_id in samples.items()}
    calls = {
        "search": [],
        "score": [],
        "seeded_fits": [],
        "scoring": [],
        "seeded_offsets": [],
        "seeded_tolerances": [],
        "score_configs": [],
    }

    def load_peaks(sample):
        return _frame(peaks[by_id[sample.sample_item_id]])

    def assign(peaks, config, heuristics=None, targets=None, scoring=None):
        calls["search"].append(list(targets))
        calls["scoring"].append(scoring)
        return matches(list(targets))

    async def resolution_of(sample):
        return _tof_resolution

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

    async def seeded_fits(sample, params, seeds, *, reagent_offset, envelope_tolerance):
        # The search's own re-score: the finder's reading measured again as an
        # ion, which is what the rows are tiered on. Canned here for the same
        # reason the finder is - these tests are about the pass, not the fit.
        calls["seeded_fits"].append((by_id[sample.sample_item_id], set(seeds)))
        calls["seeded_offsets"].append(reagent_offset)
        calls["seeded_tolerances"].append(envelope_tolerance)
        return {seed: 0.88 for seed in seeds}

    async def seeded(sample, seeds, params, *, config=None):
        calls["score"].append((by_id[sample.sample_item_id], set(seeds)))
        calls["score_configs"].append((sample, config))
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
    monkeypatch.setattr(batch_untargeted, "_resolution_of", resolution_of)
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
    # The envelopes are matched at the searched file's own resolving power, so
    # a peak holding several lines is scored against all of them.
    assert stubbed_engine["scoring"][0].resolution is _tof_resolution

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


def _urea_lines(shift_ppm):
    """The urea reagent's first three rungs, read ``shift_ppm`` off the axis."""
    ladder = {cluster.label: cluster.mz for cluster in reagent_library("UR")}
    factor = 1.0 + shift_ppm * 1e-6
    return [
        ("r1", ladder["[CH4N2O+H]+"] * factor, 2.0e6),
        ("r2", ladder["[(CH4N2O)2+H]+"] * factor, 3.0e6),
        ("r3", ladder["[(CH4N2O)3+H]+"] * factor, 1.0e5),
    ]


async def test_the_searched_sample_is_re_scored_at_its_reagent_lines_offset(
    folded_batch, stubbed_engine, monkeypatch
):
    # No run's pre-pass is on this path, so the search reads the sample's
    # reagent lines off its own peak list and the re-score of its commits is
    # centred there, as a run of the sample centres its own.
    monkeypatch.setattr(
        batch_untargeted,
        "load_sample_peaks",
        lambda sample: _frame(_PEAKS["S1"] + _urea_lines(-1.3)),
    )

    await run_batch_untargeted_search(
        folded_batch["batch_id"], PeakAssignmentConfig(profile="UR")
    )

    [offset] = stubbed_engine["seeded_offsets"]
    assert (offset.lines, offset.beyond_width) == (3, True)
    assert offset.mu_ppm == pytest.approx(-1.3, abs=1e-6)

    # The propagation of the same run measures the other sample under the
    # search's own configuration, so it resolves the same lines to the same
    # offset rather than the auto profile, whose mechanisms name no reagent.
    monkeypatch.setattr(
        service, "load_sample_peaks", batch_untargeted.load_sample_peaks
    )
    monkeypatch.setattr(
        service, "fetch_sample_mechanisms", batch_untargeted.fetch_sample_mechanisms
    )
    propagated = stubbed_engine["score_configs"]
    assert propagated
    for sample, config in propagated:
        assert config.profile == "UR"
        assert await service.sample_reagent_offset(sample, config) == offset
    assert await service.sample_reagent_offset(propagated[0][0]) is None


async def _record_run(
    session_factory,
    sample_item_id,
    status,
    minutes_ago,
    scoring,
    engine=IN_APP_ENGINE,
    envelope_tolerance=None,
):
    now = datetime.now(timezone.utc)
    config = {"run_untargeted": True, "pattern_scoring": scoring}
    if envelope_tolerance is not None:
        config["envelope_tolerance"] = envelope_tolerance
    async with session_factory() as session:
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=gen_id(),
                sample_item_id=sample_item_id,
                engine=engine,
                engine_version="0.1.0-test",
                status=status,
                config=config,
                peak_assignment_run_utc_created=now - timedelta(minutes=minutes_ago),
            )
        )
        await session.commit()


def _scoring(mu_ppm, mu_source, sigma_source="fitted", sigma_ppm=0.583):
    return {
        "sigma_ppm": sigma_ppm,
        "mu_ppm": mu_ppm,
        "sigma_source": sigma_source,
        "mu_source": mu_source,
        "fitted_anchors": 12,
        "reagent_lines": 3,
        "reagent_mu_ppm": -1.3,
    }


async def test_a_sample_with_a_run_is_re_scored_where_that_run_scored(
    async_session_factory, folded_batch, stubbed_engine, monkeypatch
):
    # The uronium case: the sample's library fitted its own offset near zero,
    # and its reagent lines sit beyond the width at -1.3 ppm. A run scored it
    # at the library's fit, so the search's re-score is centred there too.
    samples = folded_batch["samples"]
    monkeypatch.setattr(
        batch_untargeted,
        "load_sample_peaks",
        lambda sample: _frame(_PEAKS["S1"] + _urea_lines(-1.3)),
    )
    # The latest completed in-app run is the one read: not an older one, nor
    # one still running or failed since, nor a newer import, whose config is
    # the client's own and need not hold a well-formed snapshot.
    await _record_run(
        async_session_factory, samples["S1"], "completed", 60, _scoring(-0.5, "fitted")
    )
    await _record_run(
        async_session_factory, samples["S1"], "completed", 30, _scoring(-0.05, "fitted")
    )
    await _record_run(
        async_session_factory,
        samples["S1"],
        "completed",
        10,
        {"mu_source": "fitted", "mu_ppm": None},
        engine="ext",
    )
    await _record_run(
        async_session_factory, samples["S1"], "running", 5, _scoring(-2.0, "fitted")
    )
    await _record_run(
        async_session_factory, samples["S1"], "failed", 1, _scoring(-2.0, "fitted")
    )

    await run_batch_untargeted_search(
        folded_batch["batch_id"], PeakAssignmentConfig(profile="UR")
    )

    [offset] = stubbed_engine["seeded_offsets"]
    assert offset == service.ReagentOffset(mu_ppm=-0.05, lines=12, beyond_width=True)

    # The other sample has no run, so its propagation claims its lines under
    # the search's configuration; once a run records a centre, that is read.
    monkeypatch.setattr(
        service, "load_sample_peaks", batch_untargeted.load_sample_peaks
    )
    monkeypatch.setattr(
        service, "fetch_sample_mechanisms", batch_untargeted.fetch_sample_mechanisms
    )
    sample, config = next(
        (sample, config)
        for sample, config in stubbed_engine["score_configs"]
        if sample.sample_item_id == samples["S2"]
    )
    claimed = await service.sample_reagent_offset(sample, config)
    assert (claimed.lines, claimed.beyond_width) == (3, True)
    assert claimed.mu_ppm == pytest.approx(-1.3, abs=1e-6)
    await _record_run(
        async_session_factory, samples["S2"], "completed", 1, _scoring(0.0, "none")
    )
    assert await service.sample_reagent_offset(sample, config) is None


#: The width a sample is scored at where nothing fitted one: the Orbitrap
#: class's, widened as a fitted one is.
_CLASS_WIDTH = math.hypot(INSTRUMENT_FALLBACK_SIGMA_PPM["orbi"], PRED_SIGMA_PPM)
#: The width a run of the sample scored at where its library fitted one: well
#: wide of the class's, as on a sample whose lines scatter more than its
#: class's typical accuracy.
_RUN_WIDTH = 1.2


@pytest.mark.parametrize(
    "shift_ppm, recorded, centre_ppm, width_ppm",
    [
        # No run recorded a centre, so the lines are claimed: beyond the width
        # they centre the sample...
        pytest.param(-1.3, None, -1.3, _CLASS_WIDTH, id="lines-beyond-the-width"),
        # ...and inside it they leave it at its nominal masses, as in a run.
        pytest.param(-0.3, None, 0.0, _CLASS_WIDTH, id="lines-inside-the-width"),
        # A run whose library fitted its own offset scored the sample there,
        # not on the lines, and at the width it fitted.
        pytest.param(
            -1.3,
            (-0.05, "fitted", "fitted", _RUN_WIDTH),
            -0.05,
            _RUN_WIDTH,
            id="run-fitted-offset",
        ),
        # A run that scored the sample uncorrected leaves it so, lines or not.
        # Its library matched too few ions to fit an offset, so too few to fit
        # a width (`MASS_ACCURACY_MIN_ANCHORS`): it scored at the class's, and
        # the search takes today's class width, not the one it recorded, as
        # where a release since moved the class's.
        pytest.param(
            -1.3,
            (0.0, "none", "instrument_class", _RUN_WIDTH),
            0.0,
            _CLASS_WIDTH,
            id="run-uncorrected",
        ),
        # A run whose library fitted neither, but whose reagent lines stood in
        # for the centre, scored the sample there and at the class's width.
        pytest.param(
            -1.3,
            (-1.2, "reagent", "instrument_class", _CLASS_WIDTH),
            -1.2,
            _CLASS_WIDTH,
            id="run-class-width",
        ),
        # Nor is a width read off a record that holds none.
        pytest.param(
            -1.3,
            (-0.05, "fitted", "fitted", None),
            -0.05,
            _CLASS_WIDTH,
            id="run-recorded-no-width",
        ),
    ],
)
async def test_the_search_ranks_the_sample_where_a_run_scores_it(
    async_session_factory,
    folded_batch,
    stubbed_engine,
    monkeypatch,
    shift_ppm,
    recorded,
    centre_ppm,
    width_ppm,
):
    # A run's untargeted stage ranks its candidates at the centre it scores
    # the sample at, and at the width its Stage A fitted. A batch search
    # ranked them at zero and at the class's width, so on an axis beyond the
    # width, or on a sample measured wider or narrower than its class, it
    # chose readings a run of the sample would not.
    samples = folded_batch["samples"]
    monkeypatch.setattr(
        batch_untargeted,
        "load_sample_peaks",
        lambda sample: _frame(_PEAKS["S1"] + _urea_lines(shift_ppm)),
    )
    if recorded is not None:
        await _record_run(
            async_session_factory, samples["S1"], "completed", 30, _scoring(*recorded)
        )

    await run_batch_untargeted_search(
        folded_batch["batch_id"], PeakAssignmentConfig(profile="UR")
    )

    [scoring] = stubbed_engine["scoring"]
    assert scoring.mu_ppm == pytest.approx(centre_ppm, abs=1e-6)
    # The re-score of its commits is handed the same centre, under the same
    # rule: taken only beyond the width.
    [offset] = stubbed_engine["seeded_offsets"]
    assert (SampleMassAccuracy(reagent=offset).scoring_mu_ppm or 0.0) == (
        scoring.mu_ppm
    )
    # The run records the width it scored at, which the search ranks at too.
    assert scoring.sigma_ppm == pytest.approx(width_ppm, abs=1e-6)


async def test_a_sample_with_a_run_is_searched_at_that_runs_line_tolerance(
    async_session_factory, folded_batch, stubbed_engine
):
    # No Stage A runs on this path, so nothing commits an envelope to fit a
    # line's tolerance on: the search ranks and re-scores the sample at the
    # one its latest completed in-app run fitted, not an older run's.
    samples = folded_batch["samples"]
    fitted = fit_envelope_tolerance(
        FORM_CENTRE_WIDTH,
        EnvelopeLines(np.full(24, 10.0), np.array([0.9, 1.0, 1.1, 1.3] * 6)),
    ).record()
    older = dict(fitted, centre=0.5)
    await _record_run(
        async_session_factory,
        samples["S1"],
        "completed",
        60,
        _scoring(0.0, "none"),
        envelope_tolerance=older,
    )
    await _record_run(
        async_session_factory,
        samples["S1"],
        "completed",
        30,
        _scoring(0.0, "none"),
        envelope_tolerance=fitted,
    )

    await run_batch_untargeted_search(folded_batch["batch_id"], PeakAssignmentConfig())

    expected = EnvelopeTolerance.from_record(fitted)
    assert expected != EnvelopeTolerance()
    [scoring] = stubbed_engine["scoring"]
    assert scoring.envelope_tolerance == expected
    assert stubbed_engine["seeded_tolerances"] == [expected]


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

    async def failing(sample, seeds, params, *, config=None):
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


# --- persistence: the second admission (step 4.2b) ---------------------------------
#
# Twelve samples. Glucose and its M+1 are bright in all of them; a faint peak at
# 150 is in eleven, under the run's intensity floor everywhere; a faint peak at
# 160 is in two; and each sample has a bright peak no other sample has. The
# occurrence distribution is the batch's two modes: twelve traces in one
# sample, one in two, and three in eleven or twelve.

_FLOOR = 100.0
_FAINT = 150.0913  # C9H11NO + H+
_LINE = 151.0947  # its 13C line
_SODIATED = 172.0733  # C9H11NO + Na+
_TRANSIENT = 160.1


def _persistence_peaks():
    peaks = {}
    for i in range(1, 13):
        bright = 9000.0 if i == 1 else 4000.0
        sample = [
            ("p1", 181.0707, bright),
            ("p2", 182.0741, bright / 12),
            (f"x{i}", 200.0 + i, 1000.0),
        ]
        if i <= 11:
            sample.append(("pf", _FAINT, 60.0 if i == 1 else 40.0))
        if i <= 2:
            sample.append(("pt", _TRANSIENT, 50.0))
        peaks[f"S{i}"] = sample
    return peaks


_PERSISTENCE_PEAKS = _persistence_peaks()


def _persistence_matches(targets):
    rows = []
    if 181.0707 in targets:
        rows += [
            _reading(181.0707, "C6H12O6", "C6H13O6+"),
            _reading(182.0741, "C6H12O6", "[13C]C5H13O6+", label="13C"),
        ]
    if _FAINT in targets:
        rows.append(_reading(_FAINT, "C9H11NO", "C9H12NO+"))
    if _TRANSIENT in targets:
        rows.append(_reading(_TRANSIENT, "C10H13N", "C10H14N+"))
    return pd.DataFrame(rows), {}


@pytest_asyncio.fixture
async def persistence_batch(async_session_factory, pa_test_data, monkeypatch):
    batch = await _fold_batch(async_session_factory, pa_test_data, _PERSISTENCE_PEAKS)
    batch["calls"] = _stub_engine(
        monkeypatch,
        batch["samples"],
        _PERSISTENCE_PEAKS,
        _persistence_matches,
        {
            "C6H12O6": ("ion-glc", 0.81, ["p1", "p2"]),
            "C9H11NO": ("ion-faint", 0.86, ["pf"]),
            "C10H13N": ("ion-transient", 0.86, ["pt"]),
        },
    )
    return batch


async def _search_persistence(batch):
    config = PeakAssignmentConfig(peak_intensity_threshold=_FLOOR)
    run_id = await start_run(batch["batch_id"], ACTION_SEARCH)
    counts = await run_batch_untargeted_search(
        batch["batch_id"], config, batch_peak_run_id=run_id
    )
    await complete_run(batch["batch_id"], run_id, summary=counts)
    return counts, run_id


def _reason_rules(anchor):
    return [
        reason["rule"] for reason in (anchor.provenance or {}).get(TIER_REASONS_KEY, [])
    ]


async def test_a_faint_peak_in_most_spectra_is_searched_and_one_in_few_is_not(
    async_session_factory, persistence_batch
):
    batch_id = persistence_batch["batch_id"]
    counts, run_id = await _search_persistence(persistence_batch)

    # S1 represents glucose, its line and both faint peaks: the floor admits
    # the bright ones, persistence the faint one in eleven samples, and
    # nothing admits the faint one in two.
    (s1_targets,) = [
        targets for targets in persistence_batch["calls"]["search"] if _FAINT in targets
    ]
    assert {181.0707, 182.0741, _FAINT} <= set(s1_targets)
    assert not any(
        _TRANSIENT in targets for targets in persistence_batch["calls"]["search"]
    )
    assert counts["anchors_admitted_by_persistence"] == 1
    (split,) = counts["persistence_splits"]
    # Between the transient mode (one and two of twelve) and the persistent
    # one (eleven and twelve).
    assert 2 / 12 < split["split"] < 11 / 12

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    record = anchors[150].provenance[PERSISTENCE_KEY]
    assert record["share"] == pytest.approx(11 / 12, abs=1e-4)
    assert (record["n_present"], record["n_samples"]) == (11, 12)
    assert record["split"] == split["split"]
    assert record["formula"] == "C9H11NO"
    assert record["ionization_mechanism_id"] == "mech-h"
    assert record["batch_peak_run_id"] == run_id
    # The bright peaks were admitted on their height, as before: no record.
    assert PERSISTENCE_KEY not in (anchors[181].provenance or {})
    assert anchors[160].consensus_tier == "unassigned"
    assert PERSISTENCE_KEY not in (anchors[160].provenance or {})


async def test_a_reading_elected_on_persistence_alone_is_held_at_candidate(
    async_session_factory, persistence_batch
):
    batch_id, samples = persistence_batch["batch_id"], persistence_batch["samples"]
    counts, _ = await _search_persistence(persistence_batch)

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    faint = anchors[150]
    assert faint.consensus_formula == "C9H11NO"
    # Every member measured the ion at a fit the assigned band takes, and the
    # batch peak still stands at candidate: the fit alone never lifts it. Its
    # members are stored there too, so no sample's row of it reads assigned,
    # and the record keeps which, to put them back.
    s2 = await _members(async_session_factory, samples["S2"])
    assert s2["pf"].fit_score == pytest.approx(0.86)
    assert tier_name(s2["pf"].tier) == "candidate"
    held = faint.provenance[PERSISTENCE_KEY]["held_members"]
    assert set(held) == set(samples.values()) - {samples["S12"]}
    assert faint.consensus_tier == "candidate"
    assert _reason_rules(faint) == ["persistence_only"]
    (reason,) = faint.provenance[TIER_REASONS_KEY]
    assert reason["caps"] is True
    assert "11 of its partition's 12 spectra" in reason["detail"]
    assert counts["anchors_held_by_persistence"] == 1
    assert "held at candidate" in search_outcome(counts, batch_id)["message"]
    # Glucose, searched on its height with its 13C line, is not asked.
    assert anchors[181].consensus_tier == "assigned"
    assert _reason_rules(anchors[181]) == []


async def _add_sample(session_factory, batch_id, name, rows_of, defer_to=None):
    """Add a sample to the batch and fold ``rows_of(sample_item_id)`` into it,
    its consensus deferred to ``defer_to`` where given, as a backfill folds."""
    now = datetime.now(timezone.utc)
    sample_file_id, sample_item_id = gen_id(), gen_id()
    async with session_factory() as session:
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
        await session.commit()
    rows = rows_of(sample_item_id)
    assert (
        await fold_sample_into_batch_peaks(
            sample_item_id, rows=rows, persisted=False, defer_consensus_to=defer_to
        )
        == batch_id
    )
    return sample_item_id


def _row(sample_item_id, peak, mz, *, formula=None, mechanism=None, **over):
    """A ledger row of ``sample_item_id`` as a run left it, unassigned unless
    it names a formula."""
    row = _unassigned_rows(sample_item_id, [(peak, mz, 500.0)])[0]
    if formula:
        row.role = "M0"
        row.assigned_formula = formula
        row.ion_formula = f"{formula}+"
        row.ionization_mechanism_id = mechanism
        row.tier = "assigned"
        row.fit_score = 0.99
        row.source = "untargeted"
    for key, value in over.items():
        setattr(row, key, value)
    return row


def _elected_row(sample_item_id, **over):
    # The ion the search elected, its own ion formula: the registry entry the
    # search wrote, which this row then names.
    return _row(
        sample_item_id,
        "pf",
        _FAINT,
        formula="C9H11NO",
        mechanism="mech-h",
        ion_formula="C9H12NO+",
        **over,
    )


async def test_a_sample_reading_it_at_a_better_fit_does_not_lift_it(
    async_session_factory, persistence_batch
):
    batch_id = persistence_batch["batch_id"]
    await _search_persistence(persistence_batch)
    await _add_sample(
        async_session_factory,
        batch_id,
        "S13",
        lambda sample: [_elected_row(sample)],
    )
    faint = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    assert faint.consensus_tier == "candidate"
    assert _reason_rules(faint) == ["persistence_only"]


async def test_its_13c_line_in_a_sample_lifts_it(
    async_session_factory, persistence_batch
):
    batch_id = persistence_batch["batch_id"]
    await _search_persistence(persistence_batch)

    def rows(sample):
        m0 = _elected_row(sample)
        line = _row(
            sample,
            "pl",
            _LINE,
            formula="C9H11NO",
            mechanism="mech-h",
            ion_formula="[13C]C8H12NO+",
            role="iso_child",
            owner_peak_assignment_id=m0.peak_assignment_id,
        )
        return [m0, line]

    await _add_sample(async_session_factory, batch_id, "S13", rows)
    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    faint = anchors[150]
    assert anchors[151].isotopologue_of == faint.batch_peak_id
    assert faint.consensus_tier == "assigned"
    assert _reason_rules(faint) == ["second_line"]
    # The admission stays on the record whatever lifts the cap; the members
    # it held are back at the tier they were measured at.
    assert faint.provenance[PERSISTENCE_KEY]["formula"] == "C9H11NO"
    assert "held_members" not in faint.provenance[PERSISTENCE_KEY]
    s2 = await _members(async_session_factory, persistence_batch["samples"]["S2"])
    assert tier_name(s2["pf"].tier) == "assigned"


async def _sodium_mechanism(session_factory):
    async with session_factory() as session:
        if await session.get(IonizationMechanism, "mech-na") is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id="mech-na",
                    ionization_mechanism_polarity="+",
                    ionization_mechanism="Na+ (untargeted search test)",
                )
            )
            await session.commit()


def _sodiated_rows(sample):
    # The same neutral through sodium, and the faint peak itself unread: the
    # sample's fold reaches the batch peak, which is recomputed.
    return [
        _row(sample, "pf", _FAINT),
        _row(sample, "pna", _SODIATED, formula="C9H11NO", mechanism="mech-na"),
    ]


async def test_the_same_neutral_through_another_channel_lifts_it(
    async_session_factory, persistence_batch
):
    batch_id = persistence_batch["batch_id"]
    await _search_persistence(persistence_batch)
    await _sodium_mechanism(async_session_factory)
    await _add_sample(async_session_factory, batch_id, "S13", _sodiated_rows)

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    assert anchors[172].consensus_formula == "C9H11NO"
    assert anchors[150].consensus_tier == "assigned"
    assert _reason_rules(anchors[150]) == ["corroborated"]


async def test_a_pin_and_its_release_leave_the_hold_for_a_lift_to_take(
    async_session_factory, persistence_batch, monkeypatch
):
    batch_id, samples = persistence_batch["batch_id"], persistence_batch["samples"]
    await _search_persistence(persistence_batch)
    faint = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    held = set(faint.provenance[PERSISTENCE_KEY]["held_members"])
    assert held == set(samples.values()) - {samples["S12"]}
    # Another reading of the peak in its registry, one a sample once carried.
    async with async_session_factory() as session:
        anchor = await session.get(BatchPeak, faint.batch_peak_id)
        registry = list(anchor.candidates)
        other = candidate_index(registry, "C8H11N3", "C8H12N3+", "mech-h")
        anchor.candidates = registry
        await session.commit()
    # The pin measures it in each sample, where its envelope reaches the
    # faint peak, and re-points every member.
    _stub_engine(
        monkeypatch,
        samples,
        _PERSISTENCE_PEAKS,
        _persistence_matches,
        {"C8H11N3": ("ion-other", 0.84, ["pf"])},
    )
    counts = await run_batch_peak_curation(
        batch_id, faint.batch_peak_id, other, "C9H11NO", PeakAssignmentConfig()
    )
    assert counts["members_repointed"] == len(held)
    pinned = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    assert pinned.consensus_formula == "C8H11N3"
    assert "held_members" not in pinned.provenance[PERSISTENCE_KEY]
    # What the pin archives is each member's measurement, not the hold, which
    # went with the pin's recompute.
    archived = pinned.provenance["manual"]["displaced_members"]
    assert {state["sample_item_id"] for state in archived} == held
    assert {tier_name(state["tier"]) for state in archived} == {"assigned"}

    # Released, the elected reading is held again, its members with it.
    await release_batch_peak_curation(batch_id, faint.batch_peak_id)
    released = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    assert released.consensus_formula == "C9H11NO"
    assert released.consensus_tier == "candidate"
    assert _reason_rules(released) == ["persistence_only"]
    assert set(released.provenance[PERSISTENCE_KEY]["held_members"]) == held
    s2 = await _members(async_session_factory, samples["S2"])
    assert tier_name(s2["pf"].tier) == "candidate"

    # And a second channel lifts it, the members it held too.
    await _sodium_mechanism(async_session_factory)
    await _add_sample(async_session_factory, batch_id, "S13", _sodiated_rows)
    lifted = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    assert lifted.consensus_tier == "assigned"
    assert _reason_rules(lifted) == ["corroborated"]
    assert "held_members" not in lifted.provenance[PERSISTENCE_KEY]
    s2 = await _members(async_session_factory, samples["S2"])
    assert tier_name(s2["pf"].tier) == "assigned"


async def test_a_reading_the_vote_puts_below_candidate_is_not_counted_held(
    async_session_factory, persistence_batch, monkeypatch
):
    batch_id = persistence_batch["batch_id"]
    # Every sample measures the faint ion at a fit no tier above
    # below_assignability takes.
    _stub_engine(
        monkeypatch,
        persistence_batch["samples"],
        _PERSISTENCE_PEAKS,
        _persistence_matches,
        {
            "C6H12O6": ("ion-glc", 0.81, ["p1", "p2"]),
            "C9H11NO": ("ion-faint", 0.05, ["pf"]),
        },
    )

    async def seeded_fits(sample, params, seeds, *, reagent_offset, envelope_tolerance):
        return {seed: 0.05 if seed[0] == "C9H11NO" else 0.88 for seed in seeds}

    monkeypatch.setattr(batch_untargeted, "_seeded_fits", seeded_fits)
    counts, _ = await _search_persistence(persistence_batch)

    faint = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    assert faint.consensus_formula == "C9H11NO"
    assert faint.consensus_tier == "below_assignability"
    # The rule stands on the reading - nothing corroborates it - but holds
    # nothing at candidate: the summary does not count it held.
    assert _reason_rules(faint) == ["persistence_only"]
    assert counts["anchors_admitted_by_persistence"] == 1
    assert counts["anchors_held_by_persistence"] == 0
    assert "held" not in search_outcome(counts, batch_id)["message"]


@pytest.mark.parametrize("deferred", [False, True], ids=["fold", "backfill"])
async def test_a_channel_in_a_later_chunk_lifts_it_in_the_same_pass(
    async_session_factory, persistence_batch, monkeypatch, deferred
):
    # One anchor per chunk, and the sodiated one minted with an id that sorts
    # last: when the faint one is recomputed the other channel still reads
    # what it held before the pass - nothing. The persistence rule's second
    # pass reads it again once every chunk is written.
    batch_id = persistence_batch["batch_id"]
    await _search_persistence(persistence_batch)
    await _sodium_mechanism(async_session_factory)
    monkeypatch.setattr(batch_peaks_controller, "_CONSENSUS_CHUNK", 1)
    monkeypatch.setattr(
        batch_peaks_controller, "gen_id", lambda length=16: "zzz" + gen_id(length - 3)
    )
    if deferred:
        touched: set[str] = set()
        await _add_sample(
            async_session_factory, batch_id, "S13", _sodiated_rows, defer_to=touched
        )
        await recompute_batch_consensus(batch_id, touched)
    else:
        await _add_sample(async_session_factory, batch_id, "S13", _sodiated_rows)

    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    assert anchors[150].batch_peak_id < anchors[172].batch_peak_id
    assert anchors[150].consensus_tier == "assigned"
    assert _reason_rules(anchors[150]) == ["corroborated"]


async def test_a_series_of_the_batch_anchoring_it_lifts_it(
    async_session_factory, persistence_batch
):
    batch_id = persistence_batch["batch_id"]
    await _search_persistence(persistence_batch)
    faint_id = (await _anchors_by_mz(async_session_factory, batch_id))[
        150
    ].batch_peak_id
    async with async_session_factory() as session:
        anchor = await session.get(BatchPeak, faint_id)
        anchor.provenance = {
            **(anchor.provenance or {}),
            "series": {
                "formula": "C9H11NO",
                "ionization_mechanism_id": "mech-h",
                "has_anchor": True,
                "unit": "CH2",
            },
        }
        await session.commit()
    await recompute_batch_consensus(batch_id, {faint_id})

    faint = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    assert faint.consensus_tier == "assigned"
    assert _reason_rules(faint) == ["series_anchor"]


async def test_a_chunk_reports_the_batch_peaks_the_rule_asked(
    async_session_factory, persistence_batch
):
    # The set a recompute spanning chunks takes its second pass over: the
    # faint batch peak, never glucose, which the search took on its height.
    batch_id = persistence_batch["batch_id"]
    await _search_persistence(persistence_batch)
    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    ids = [anchors[150].batch_peak_id, anchors[181].batch_peak_id]
    async with async_session_factory() as session:
        asked = await batch_peaks_controller._recompute_consensus_chunk(
            session, ids, datetime.now(timezone.utc)
        )
        await session.rollback()
    assert asked == {anchors[150].batch_peak_id}


async def test_the_ledger_the_snapshot_and_the_inspector_show_it(
    async_session_factory, persistence_batch, guest_client
):
    batch_id, samples = persistence_batch["batch_id"], persistence_batch["samples"]
    _, run_id = await _search_persistence(persistence_batch)
    faint = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    record = {
        key: value
        for key, value in faint.provenance[PERSISTENCE_KEY].items()
        if key != "held_members"
    }
    reasons = faint.provenance[TIER_REASONS_KEY]

    async def ledger(**params):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch_id}",
            params={"min_n_present": 1, **params},
        )
        assert response.status_code == 200, response.text
        return {row["batch_peak_id"]: row for row in response.json()["data"]}

    live = await ledger()
    assert live[faint.batch_peak_id]["persistence"] == record
    assert live[faint.batch_peak_id]["tier_reasons"] == reasons
    assert live[faint.batch_peak_id]["consensus_tier"] == "candidate"
    others = [row for key, row in live.items() if key != faint.batch_peak_id]
    assert all(row["persistence"] is None for row in others)

    # A later run snapshots this one; read back, the snapshot still says it.
    later = await start_run(batch_id, ACTION_SEARCH)
    await complete_run(batch_id, later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier[faint.batch_peak_id]["persistence"] == record
    assert earlier[faint.batch_peak_id]["tier_reasons"] == reasons

    # The inspector reads the share the admission rested on off a sample's
    # derived row of the elected reading, with the batch's reasons.
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{samples['S3']}/assignment/"
        f"{fold_assignment_id(faint.batch_peak_id)}"
    )
    assert response.status_code == 200, response.text
    [row] = response.json()["data"]
    assert row["batch_persistence"] == {
        **record,
        "tier_reasons": reasons,
        "consensus_tier": "candidate",
    }
    # The sample's own row of the reading stands where the batch's does.
    assert row["tier"] == "candidate"


async def test_a_sample_searched_again_takes_its_persistent_peak_again(
    async_session_factory, pa_test_data, monkeypatch
):
    # S2 represents both glucose's M+1 and the faint peak. Its search reads
    # the M+1 on its own; S1's reads it as glucose's line, so S2 is searched
    # again without it - and the faint peak, under the floor, is taken on
    # persistence in that search too.
    peaks = _persistence_peaks()
    peaks["S1"] = [
        ("p1", 181.0707, 9000.0),
        ("p2", 182.0741, 300.0),
        ("x1", 201.0, 1000.0),
    ]
    peaks["S2"] = [
        ("p1", 181.0707, 4000.0),
        ("p2", 182.0741, 700.0),
        ("pf", _FAINT, 60.0),
        ("x2", 202.0, 1000.0),
    ]
    for name, sample in peaks.items():
        if name not in ("S1", "S2"):
            peaks[name] = [
                (peak, mz, 50.0 if peak == "pf" else min(intensity, 4000.0))
                if peak != "p2"
                else (peak, mz, 300.0)
                for peak, mz, intensity in sample
            ]
    batch = await _fold_batch(async_session_factory, pa_test_data, peaks)

    def matches(targets):
        frame, extra = _persistence_matches(targets)
        rows = frame.to_dict("records")
        if 182.0741 in targets:
            rows.append(_reading(182.0741, "C8H11N3", "C8H12N3+"))
        return pd.DataFrame(rows), extra

    calls = _stub_engine(
        monkeypatch,
        batch["samples"],
        peaks,
        matches,
        {
            "C6H12O6": ("ion-glc", 0.81, ["p1", "p2"]),
            "C9H11NO": ("ion-faint", 0.86, ["pf"]),
        },
    )
    counts, _ = await _search_persistence(batch)

    assert counts["samples_researched"] == 1
    again = [targets for targets in calls["search"] if 182.0741 not in targets]
    assert any(_FAINT in targets for targets in again)
    faint = (await _anchors_by_mz(async_session_factory, batch["batch_id"]))[150]
    assert faint.provenance[PERSISTENCE_KEY]["formula"] == "C9H11NO"
    assert faint.consensus_tier == "candidate"


async def test_a_batch_peak_a_later_search_takes_on_its_height_loses_the_record(
    async_session_factory, pa_test_data, monkeypatch
):
    batch = await _fold_batch(async_session_factory, pa_test_data, _PERSISTENCE_PEAKS)
    finds = {"faint": False}

    def matches(targets):
        frame, extra = _persistence_matches(targets)
        if not finds["faint"]:
            frame = frame[frame["mz"] != _FAINT]
        return frame, extra

    _stub_engine(
        monkeypatch,
        batch["samples"],
        _PERSISTENCE_PEAKS,
        matches,
        {
            "C6H12O6": ("ion-glc", 0.81, ["p1", "p2"]),
            "C9H11NO": ("ion-f", 0.86, ["pf"]),
        },
    )
    # Taken on persistence, nothing found: the admission is recorded, no
    # reading elected on it.
    await _search_persistence(batch)
    faint = (await _anchors_by_mz(async_session_factory, batch["batch_id"]))[150]
    assert faint.consensus_tier == "unassigned"
    assert faint.provenance[PERSISTENCE_KEY]["formula"] is None

    # With no floor the next search takes it on its height.
    finds["faint"] = True
    await run_batch_untargeted_search(batch["batch_id"], PeakAssignmentConfig())
    faint = (await _anchors_by_mz(async_session_factory, batch["batch_id"]))[150]
    assert PERSISTENCE_KEY not in (faint.provenance or {})
    assert faint.consensus_tier == "assigned"
    assert _reason_rules(faint) == []


@pytest.mark.parametrize(
    "formula, mechanism",
    [("C10H13N", "mech-h"), ("C9H11NO", "mech-na")],
    ids=["another formula", "another mechanism"],
)
async def test_a_line_of_another_reading_of_the_peak_does_not_lift_it(
    async_session_factory, persistence_batch, formula, mechanism
):
    # A sample that reads the faint peak its own way, and commits that
    # reading's 13C line: the line is not the batch's reading's.
    batch_id = persistence_batch["batch_id"]
    await _search_persistence(persistence_batch)
    await _sodium_mechanism(async_session_factory)

    def rows(sample):
        m0 = _row(sample, "pf", _FAINT, formula=formula, mechanism=mechanism)
        line = _row(
            sample,
            "pl",
            _LINE,
            formula=formula,
            mechanism=mechanism,
            role="iso_child",
            owner_peak_assignment_id=m0.peak_assignment_id,
        )
        return [m0, line]

    await _add_sample(async_session_factory, batch_id, "S13", rows)
    anchors = await _anchors_by_mz(async_session_factory, batch_id)
    assert anchors[151].isotopologue_of == anchors[150].batch_peak_id
    assert anchors[150].consensus_formula == "C9H11NO"
    assert anchors[150].ionization_mechanism_id == "mech-h"
    assert anchors[150].consensus_tier == "candidate"
    assert _reason_rules(anchors[150]) == ["persistence_only"]


async def test_a_runs_own_row_on_the_peak_reads_the_admission(
    async_session_factory, persistence_batch, guest_client
):
    batch_id, samples = persistence_batch["batch_id"], persistence_batch["samples"]
    await _search_persistence(persistence_batch)
    faint = (await _anchors_by_mz(async_session_factory, batch_id))[150]
    now = datetime.now(timezone.utc)
    run_id = gen_id()
    rows = {
        "elected": ("C9H11NO", "mech-h"),
        "another": ("C10H13N", "mech-h"),
    }
    ids = {}
    async with async_session_factory() as session:
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=samples["S2"],
                engine_version="0.1.0-test",
                status="completed",
                config={},
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        await session.flush()
        for key, (formula, mechanism) in rows.items():
            ids[key] = gen_id(32)
            # One row per run and peak: the second reading sits on another run.
            this_run = run_id if key == "elected" else gen_id()
            if this_run != run_id:
                session.add(
                    PeakAssignmentRun(
                        peak_assignment_run_id=this_run,
                        sample_item_id=samples["S2"],
                        engine_version="0.1.0-test",
                        status="completed",
                        config={},
                        peak_assignment_run_utc_created=now - timedelta(minutes=1),
                        peak_assignment_run_utc_completed=now - timedelta(minutes=1),
                    )
                )
                await session.flush()
            session.add(
                PeakAssignment(
                    peak_assignment_id=ids[key],
                    peak_assignment_run_id=this_run,
                    sample_item_id=samples["S2"],
                    sample_peak_id="pf",
                    sample_peak_mz=_FAINT,
                    sample_peak_intensity=40.0,
                    role="M0",
                    assigned_formula=formula,
                    ion_formula=f"{formula}+",
                    ionization_mechanism_id=mechanism,
                    source="untargeted",
                    tier="assigned",
                    fit_score=0.9,
                )
            )
        await session.commit()

    async def detail(peak_assignment_id):
        response = await guest_client.get(
            f"/api/peak-assignments/sample/{samples['S2']}/assignment/"
            f"{peak_assignment_id}"
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["data"]
        return row["batch_persistence"]

    record = {
        key: value
        for key, value in faint.provenance[PERSISTENCE_KEY].items()
        if key != "held_members"
    }
    reasons = faint.provenance[TIER_REASONS_KEY]
    # The run's row reads assigned; the batch peak's tier, served with the
    # batch's reasons, is what they did to the batch's reading.
    assert await detail(ids["elected"]) == {
        **record,
        "tier_reasons": reasons,
        "consensus_tier": "candidate",
    }
    # Another reading of the peak is served the admission, not the reasons.
    assert await detail(ids["another"]) == {
        **record,
        "tier_reasons": None,
        "consensus_tier": None,
    }
