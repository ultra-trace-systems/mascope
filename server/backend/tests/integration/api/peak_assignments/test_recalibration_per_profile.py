"""The refit from verdicts, per reagent profile (MAS-63, the plan's step 4.3).

``recalibrate_instrument`` fits the labels of one reagent profile when asked
for it, records the profile on the curve, and refits the curve's corroboration
weights from those labels; a pooled fit records a profile only where every
label shares it. The label pool admits the untargeted stage's verdicts once
their evidence is on the v2 scale, and a refit worse than the curve in force
leaves that curve in force.

The sample's file is read as a made-up instrument class here, so the pool this
module fits on holds its own labels and nobody else's, and the curve it saves
is one no other test reads.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.new.peak_assignments import service
from mascope_backend.db import (
    AssignmentCalibration,
    AssignmentVerification,
    Dataset,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCompound,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.calibration import (
    Calibration,
    apply_calibration,
    refit_corroboration,
)


#: The labels' own channel, and the other channel some of their neutrals were
#: also committed through.
OWN, OTHER = "[M+H2PO4]-", "[M+NO3]-"


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on - recalibrate is gated."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest_asyncio.fixture
async def cal_data(async_session_factory, pa_test_data, monkeypatch):
    """A sample, runs under two profiles and two engine versions, and a way to label.

    ``label(run, verdict, evidence, ...)`` writes a verdict on a row of its
    own in ``run``, of a target compound and neutral no other label has: a
    database row unless ``source`` says otherwise, which names its compound
    unless it is an untargeted one or ``compound=False`` (a reference
    mirror's row). ``also_seen`` names the tier at which the
    run also committed that neutral through the other channel, in a row of
    its own, which the row's cross-channel record names at any tier, as the
    engine's does; ``sibling_role``, ``sibling_run`` and ``sibling_source``
    place that row elsewhere, and ``sibling_compound`` ("same", "other" for
    an isomer's, or None) says which compound it names. ``role`` is the
    judged row's; ``row=False`` leaves the row out, as a pruned run would.

    :return: Ids the tests address, the instrument class and ``label``.
    """
    now = datetime.now(timezone.utc)
    ids = {
        "dataset": gen_id(),
        "batch": gen_id(),
        "file": gen_id(),
        "sample": gen_id(),
        "profile": f"cal-profile-{gen_id(6)}",
        "other_profile": f"cal-other-{gen_id(6)}",
        "inst": f"cls{gen_id(6).lower()}",
    }
    ids["runs"] = {
        "current": gen_id(),
        "other_profile": gen_id(),
        "old": gen_id(),
    }
    marker = f"cal-{ids['file']}"
    monkeypatch.setattr(
        service,
        "_instrument_class",
        lambda filename: ids["inst"] if filename and marker in filename else None,
    )

    created_mechanisms = []
    async with async_session_factory() as session:
        for key, notation in (("mechanism", OWN), ("other_mechanism", OTHER)):
            mechanism = (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism == notation
                    )
                )
            ).scalar_one_or_none()
            if mechanism is None:
                mechanism = IonizationMechanism(
                    ionization_mechanism_id=gen_id(),
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=notation,
                )
                session.add(mechanism)
                created_mechanisms.append(mechanism.ionization_mechanism_id)
            ids[key] = mechanism.ionization_mechanism_id
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=pa_test_data["workspace_id"],
                dataset_name=f"Cal Dataset {ids['dataset']}",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name=f"Cal Batch {ids['batch']}",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"tof-{marker}.zarr",
                instrument="tof-test",
                datetime=datetime(2026, 11, 1, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=ids["sample"],
                sample_batch_id=ids["batch"],
                sample_file_id=ids["file"],
                sample_item_name=f"Cal Sample {ids['sample']}",
                sample_item_type="sample",
                polarity="-",
                sample_item_utc_created=now,
            )
        )
        await session.flush()
        for key, profile, version in (
            ("current", ids["profile"], "0.5.0"),
            ("other_profile", ids["other_profile"], "0.5.0"),
            ("old", ids["profile"], "0.4.0"),
        ):
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=ids["runs"][key],
                    sample_item_id=ids["sample"],
                    engine="mascope",
                    engine_version=version,
                    status="completed",
                    config={"resolved_profile": {"profile": profile}},
                    peak_assignment_run_utc_created=now - timedelta(hours=1),
                    peak_assignment_run_utc_completed=now,
                )
            )
        await session.commit()

    neutrals = iter(range(1, 10_000))
    compounds: list[str] = []

    async def label(
        run,
        verdict,
        evidence,
        *,
        level="visual",
        source="database",
        also_seen=None,
        sibling_role="M0",
        sibling_run=None,
        sibling_source="database",
        sibling_compound="same",
        role="M0",
        row=True,
        compound=True,
    ):
        peak = gen_id(12)
        assignment_id = gen_id(32) if row else None
        formula = f"C{next(neutrals)}H12O6"
        compounds_of = {"same": gen_id(16), "other": gen_id(16)}
        async with async_session_factory() as session:
            for compound_id in compounds_of.values():
                session.add(
                    TargetCompound(
                        target_compound_id=compound_id,
                        target_compound_formula=formula,
                    )
                )
                compounds.append(compound_id)
            await session.flush()
            if row:
                channels = sorted([OWN, OTHER] if also_seen else [OWN])
                session.add(
                    PeakAssignment(
                        peak_assignment_id=assignment_id,
                        peak_assignment_run_id=ids["runs"][run],
                        sample_item_id=ids["sample"],
                        sample_peak_id=peak,
                        sample_peak_mz=200.0,
                        sample_peak_intensity=1000.0,
                        role=role,
                        assigned_formula=formula,
                        ionization_mechanism_id=ids["mechanism"],
                        source=source,
                        target_compound_id=(
                            compounds_of["same"]
                            if compound and source != "untargeted"
                            else None
                        ),
                        fit_score=evidence,
                        tier="candidate",
                        provenance={
                            "evidence": evidence,
                            "cross_channel": {"channels": channels},
                        },
                    )
                )
                if also_seen:
                    session.add(
                        PeakAssignment(
                            peak_assignment_id=gen_id(32),
                            peak_assignment_run_id=ids["runs"][sibling_run or run],
                            sample_item_id=ids["sample"],
                            sample_peak_id=gen_id(12),
                            sample_peak_mz=250.0,
                            sample_peak_intensity=500.0,
                            role=sibling_role,
                            assigned_formula=formula,
                            ionization_mechanism_id=ids["other_mechanism"],
                            source=sibling_source,
                            target_compound_id=compounds_of.get(sibling_compound),
                            tier=also_seen,
                            provenance={},
                        )
                    )
                await session.flush()
            session.add(
                AssignmentVerification(
                    assignment_verification_id=gen_id(32),
                    sample_item_id=ids["sample"],
                    peak_assignment_id=assignment_id,
                    peak_assignment_run_id=ids["runs"][run],
                    sample_peak_id=peak,
                    assigned_formula=formula,
                    ionization_mechanism_id=ids["mechanism"],
                    verdict=verdict,
                    evidence_level=level,
                    evidence=evidence,
                    verified_utc=now,
                )
            )
            await session.commit()

    ids["label"] = label
    yield ids

    async with async_session_factory() as session:
        await session.execute(
            delete(AssignmentVerification).where(
                AssignmentVerification.sample_item_id == ids["sample"]
            )
        )
        await session.execute(
            delete(PeakAssignment).where(PeakAssignment.sample_item_id == ids["sample"])
        )
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.sample_item_id == ids["sample"]
            )
        )
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id == ids["sample"])
        )
        if compounds:
            await session.execute(
                delete(TargetCompound).where(
                    TargetCompound.target_compound_id.in_(compounds)
                )
            )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
        )
        await session.execute(
            delete(Dataset).where(Dataset.dataset_id == ids["dataset"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == ids["file"])
        )
        await session.execute(
            delete(AssignmentCalibration).where(
                AssignmentCalibration.instrument == ids["inst"]
            )
        )
        if created_mechanisms:
            await session.execute(
                delete(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism_id.in_(created_mechanisms)
                )
            )
        await session.commit()


def _draw(n, seed, truth=Calibration(a=6.0, b=-3.0)):
    """``n`` (evidence, confirmed) pairs drawn from a known curve, so they fit."""
    rng = np.random.default_rng(seed)
    evidence = (np.arange(n) + 0.5) / n
    confirmed = rng.uniform(0, 1, n) < apply_calibration(evidence, truth)
    return [(round(float(e), 4), bool(c)) for e, c in zip(evidence, confirmed)]


async def _label_draw(
    cal_data, run, pairs, *, seen_when_confirmed=0.6, seed=0, low_tier_when_rejected=0.0
):
    """Label ``pairs`` in ``run``; a confirmed label's neutral is also seen
    through the other channel, at candidate, more often than a rejected one's.
    Of the rejected labels not seen so, ``low_tier_when_rejected`` are seen
    through it below assignability, which no weight is folded in for. Returns
    what each label confidently saw beside its own channel, and its label."""
    rng = np.random.default_rng(seed + 100)
    observed, labels = [], []
    for evidence, confirmed in pairs:
        also = rng.uniform() < (seen_when_confirmed if confirmed else 0.15)
        low = not confirmed and not also and rng.uniform() < low_tier_when_rejected
        await cal_data["label"](
            run,
            "confirmed" if confirmed else "rejected",
            evidence,
            level="pattern",
            also_seen="candidate" if also else ("below_assignability" if low else None),
        )
        observed.append([OTHER] if also else [])
        labels.append(int(confirmed))
    return observed, labels


async def _active(async_session_factory, inst) -> list[AssignmentCalibration]:
    async with async_session_factory() as session:
        return (
            (
                await session.execute(
                    select(AssignmentCalibration).where(
                        AssignmentCalibration.instrument == inst,
                        AssignmentCalibration.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_a_profiles_refit_fits_its_labels_and_refits_its_corroboration(
    cal_data, async_session_factory
):
    """Sixty labels under the profile, twenty under another that would pull the
    curve the other way: the refit asked for the profile fits its sixty, records
    the profile, and refits the other channel's weight from them."""
    observed, labels = await _label_draw(cal_data, "current", _draw(60, seed=1))
    # The other profile's labels contradict the curve: confirmed at low evidence.
    for evidence, confirmed in _draw(20, seed=2):
        await cal_data["label"](
            "other_profile", "rejected" if confirmed else "confirmed", evidence
        )

    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["recalibrated"] is True, result["message"]
    assert result["n_pos"] + result["n_neg"] == 60
    assert result["n_pos"] == sum(labels)
    assert result["reagent_profile"] == cal_data["profile"]
    expected = refit_corroboration(observed, labels, ["pattern"] * 60)
    assert expected["refit"], "the draw must refit the other channel"
    assert result["corroboration_refit"] == expected["refit"]
    # The labels' own channel is never a channel that corroborates them.
    assert set(result["corroboration_refit"]) == {OTHER}
    assert result["corroboration_weights"] == expected["weights"]
    assert f"profile '{cal_data['profile']}'" in result["message"]

    (row,) = await _active(async_session_factory, cal_data["inst"])
    assert row.reagent_profile == cal_data["profile"]
    assert row.corroboration_weights == expected["weights"]
    assert row.n_pos + row.n_neg == 60


@pytest.mark.asyncio
async def test_a_sibling_below_assignability_is_no_corroboration(cal_data):
    """Most rejected labels' neutrals also have a below-assignability row
    through the other channel, which their cross-channel records name. The
    engine folds no weight in for such a sibling, so the refit does not count
    it either: the weight is the one the confident siblings alone give."""
    observed, labels = await _label_draw(
        cal_data, "current", _draw(60, seed=11), low_tier_when_rejected=0.9
    )

    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["recalibrated"] is True, result["message"]
    expected = refit_corroboration(observed, labels, ["pattern"] * 60)
    assert expected["refit"], "the draw must refit the other channel"
    assert result["corroboration_refit"] == expected["refit"]
    assert result["corroboration_weights"] == expected["weights"]


@pytest.mark.asyncio
async def test_only_the_judged_runs_monoisotopic_siblings_count(cal_data):
    """A candidate sibling counts when it is a monoisotopic row of the judged
    run. An isotopologue through the other channel is the same ion again, and
    a row of another run is another run's evidence: neither counts. A label
    on an isotopologue row fits the curve and sits out the weights, as the
    engine folds weights into monoisotopic rows only."""
    observed, labels = [], []
    for i, (evidence, confirmed) in enumerate(_draw(60, seed=12)):
        sibling = {}
        if confirmed and i % 2 == 0:
            sibling = {"also_seen": "candidate"}
        elif not confirmed and i % 3 == 0:
            sibling = {"also_seen": "candidate", "sibling_role": "iso_child"}
        elif not confirmed and i % 3 == 1:
            sibling = {"also_seen": "candidate", "sibling_run": "old"}
        await cal_data["label"](
            "current",
            "confirmed" if confirmed else "rejected",
            evidence,
            level="pattern",
            **sibling,
        )
        observed.append([OTHER] if confirmed and i % 2 == 0 else [])
        labels.append(int(confirmed))
    # Labels of the other run of the profile, so the refit reads its rows too.
    await cal_data["label"]("old", "confirmed", 0.85, level="pattern")
    await cal_data["label"]("old", "rejected", 0.15, level="pattern")
    observed += [[], []]
    labels += [1, 0]
    for evidence, confirmed in _draw(10, seed=13):
        await cal_data["label"](
            "current",
            "confirmed" if confirmed else "rejected",
            evidence,
            level="pattern",
            role="iso_child",
            also_seen="candidate",
        )

    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["recalibrated"] is True, result["message"]
    assert result["n_pos"] + result["n_neg"] == 72
    expected = refit_corroboration(observed, labels, ["pattern"] * 62)
    assert expected["refit"], "the draw must refit the other channel"
    assert result["corroboration_refit"] == expected["refit"]
    assert result["corroboration_weights"] == expected["weights"]


@pytest.mark.asyncio
async def test_only_stage_a_siblings_of_the_compound_count(cal_data):
    """The engine folds a weight in among Stage A's rows of one target
    compound. A candidate row of the same neutral that the formula search
    committed, one that names an isomer, and one a hand edit made all go
    through the other channel beside rejected labels here; the fold never
    sees any of them, so neither does the refit."""
    observed, labels = [], []
    for i, (evidence, confirmed) in enumerate(_draw(60, seed=14)):
        sibling = {}
        if confirmed and i % 2 == 0:
            sibling = {"also_seen": "candidate"}
        elif not confirmed and i % 3 == 0:
            sibling = {
                "also_seen": "candidate",
                "sibling_source": "untargeted",
                "sibling_compound": None,
            }
        elif not confirmed and i % 3 == 1:
            sibling = {"also_seen": "candidate", "sibling_compound": "other"}
        elif not confirmed:
            sibling = {"also_seen": "candidate", "sibling_source": "manual"}
        await cal_data["label"](
            "current",
            "confirmed" if confirmed else "rejected",
            evidence,
            level="pattern",
            **sibling,
        )
        observed.append([OTHER] if confirmed and i % 2 == 0 else [])
        labels.append(int(confirmed))

    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["recalibrated"] is True, result["message"]
    expected = refit_corroboration(observed, labels, ["pattern"] * 60)
    assert expected["refit"], "the draw must refit the other channel"
    assert result["corroboration_refit"] == expected["refit"]
    assert result["corroboration_weights"] == expected["weights"]


@pytest.mark.parametrize(
    "row",
    [
        {"source": "untargeted"},
        {"source": "manual"},
        {"source": "database", "compound": False},
    ],
    ids=["untargeted", "manual", "reference-mirror"],
)
@pytest.mark.asyncio
async def test_a_label_off_a_stage_a_target_row_sits_out_the_weights(cal_data, row):
    """A verdict on a row the formula search committed, on one a hand edit
    made (which names its compound), or on a reference mirror's Stage A row
    (which names none) fits the curve, but no run folds a weight into such a
    row, so it sits out the weights. Every such label here is a
    rejection that saw no other channel: counted, they would raise the other
    channel's weight."""
    observed, labels = await _label_draw(cal_data, "current", _draw(60, seed=15))
    for evidence, _ in _draw(10, seed=16):
        await cal_data["label"]("current", "rejected", evidence, level="pattern", **row)

    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["n_pos"] + result["n_neg"] == 70
    expected = refit_corroboration(observed, labels, ["pattern"] * 60)
    assert expected["refit"], "the draw must refit the other channel"
    assert result["corroboration_refit"] == expected["refit"]
    assert result["corroboration_weights"] == expected["weights"]


@pytest.mark.asyncio
async def test_a_pooled_refit_across_profiles_records_none_and_refits_no_weight(
    cal_data, async_session_factory
):
    await _label_draw(cal_data, "current", _draw(40, seed=3))
    await _label_draw(cal_data, "other_profile", _draw(40, seed=4), seed=4)

    result = await service.recalibrate_instrument(cal_data["inst"])

    assert result["recalibrated"] is True, result["message"]
    assert result["n_pos"] + result["n_neg"] == 80
    assert result["reagent_profile"] is None
    assert result["corroboration_refit"] is None
    # No curve was in force for the class, so none is carried forward either.
    assert result["corroboration_weights"] is None
    (row,) = await _active(async_session_factory, cal_data["inst"])
    assert row.reagent_profile is None
    assert row.corroboration_weights is None


@pytest.mark.asyncio
async def test_a_pooled_refit_whose_labels_share_a_profile_is_that_profiles(
    cal_data, async_session_factory, monkeypatch
):
    """Every label under one profile: the pooled refit is that profile's, and
    the weights the curve in force carries are kept for what is not refit."""
    observed, labels = await _label_draw(cal_data, "current", _draw(60, seed=5))
    # A curve far off the labels, so the refit beats it and is put in force.
    current = Calibration(
        a=0.5, b=-2.5, corroboration_weights={"[M+Br]-": 2.28, "+NO3-": 0.5}
    )
    monkeypatch.setattr(service, "load_calibration", AsyncMock(return_value=current))

    result = await service.recalibrate_instrument(cal_data["inst"])

    assert result["recalibrated"] is True, result["message"]
    assert result["reagent_profile"] == cal_data["profile"]
    expected = refit_corroboration(
        observed, labels, ["pattern"] * 60, current=current.corroboration_weights
    )
    assert result["corroboration_weights"] == expected["weights"]
    # Kept, and the refit one replaces its older spelling.
    assert result["corroboration_weights"]["[M+Br]-"] == 2.28
    assert "+NO3-" not in result["corroboration_weights"]
    (row,) = await _active(async_session_factory, cal_data["inst"])
    assert row.reagent_profile == cal_data["profile"]


@pytest.mark.asyncio
async def test_a_label_with_no_cross_channel_reading_sits_out_the_weights(
    cal_data,
):
    """A label whose row is gone fits the curve but tells nothing of the
    channels, so the weights are refit from the rest. Every such label here is
    a rejection: counted as one that saw no other channel, they would raise
    the other channel's weight (an even split would leave the ratio as it is)."""
    observed, labels = await _label_draw(cal_data, "current", _draw(60, seed=6))
    for evidence, _ in _draw(10, seed=7):
        await cal_data["label"](
            "current", "rejected", evidence, level="pattern", row=False
        )

    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["n_pos"] + result["n_neg"] == 70
    expected = refit_corroboration(observed, labels, ["pattern"] * 60)
    assert result["corroboration_refit"] == expected["refit"]


@pytest.mark.asyncio
async def test_the_pool_admits_stage_b_labels_on_the_v2_scale_only(
    cal_data, monkeypatch
):
    """Untargeted rows of a 0.5.0 run are labels; of a 0.4.0 run, whose
    untargeted stage still fitted with v1, they are not, nor is a label of that
    run whose row is gone. Its database rows are."""
    await cal_data["label"]("current", "confirmed", 0.91, source="untargeted")
    await cal_data["label"]("current", "rejected", 0.12)
    await cal_data["label"]("old", "confirmed", 0.81)
    await cal_data["label"]("old", "rejected", 0.22, source="untargeted")
    await cal_data["label"]("old", "rejected", 0.33, row=False)

    captured = {}

    def _recalibrate(scores, labels, levels, **kwargs):
        captured["scores"] = sorted(scores)
        raise service.InsufficientCalibrationData("stop here")

    monkeypatch.setattr(service, "recalibrate", _recalibrate)
    result = await service.recalibrate_instrument(cal_data["inst"])

    assert captured["scores"] == [0.12, 0.81, 0.91]
    assert result["recalibrated"] is False
    assert result["n_untargeted"] == 1
    assert (result["n_pos"], result["n_neg"]) == (2, 1)


@pytest.mark.asyncio
async def test_a_refit_worse_than_the_curve_in_force_is_not_saved(
    cal_data, async_session_factory, monkeypatch
):
    """Labels drawn from the very curve in force: a refit on sixty of them is a
    noisier estimate of it, which is reported and not put in force."""
    truth = Calibration(a=6.0, b=-3.0)
    await _label_draw(cal_data, "current", _draw(60, seed=0, truth=truth))
    monkeypatch.setattr(service, "load_calibration", AsyncMock(return_value=truth))
    real = service.recalibrate

    def _worse(*args, **kwargs):
        out = real(*args, **kwargs)
        # Whatever this draw gives, the refit is held to the incumbent's ECE.
        return {**out, "activate": False, "refusal": "ece_regression"}

    monkeypatch.setattr(service, "recalibrate", _worse)
    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["recalibrated"] is False
    assert result["refusal"] == "ece_regression"
    assert result["before_ece"] is not None and result["after_ece"] is not None
    assert "Kept the calibration" in result["message"]
    assert await _active(async_session_factory, cal_data["inst"]) == []


@pytest.mark.asyncio
async def test_labels_that_make_no_usable_curve_are_reported_not_raised(
    cal_data, async_session_factory
):
    """Confirmed at low evidence, rejected at high: the fit's slope is the wrong
    sign, and the route says so rather than failing."""
    for evidence, confirmed in _draw(60, seed=8):
        await cal_data["label"](
            "current", "rejected" if confirmed else "confirmed", evidence
        )

    result = await service.recalibrate_instrument(
        cal_data["inst"], profile=cal_data["profile"]
    )

    assert result["recalibrated"] is False
    assert "usable curve" in result["message"]
    assert await _active(async_session_factory, cal_data["inst"]) == []


@pytest.mark.asyncio
async def test_the_route_fits_the_profile_it_is_asked_for(
    owner_client, cal_data, feature_enabled
):
    await _label_draw(cal_data, "current", _draw(60, seed=9))
    await _label_draw(cal_data, "other_profile", _draw(30, seed=10), seed=10)

    response = await owner_client.post(
        f"/api/peak-assignments/calibration/{cal_data['inst']}/recalibrate",
        params={"profile": cal_data["profile"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recalibrated"] is True, body["message"]
    assert body["n_pos"] + body["n_neg"] == 60
    assert body["reagent_profile"] == cal_data["profile"]
    assert set(body["corroboration_refit"]) == {OTHER}
