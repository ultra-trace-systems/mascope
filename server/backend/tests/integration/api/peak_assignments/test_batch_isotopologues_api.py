"""Integration tests for the batch's isotopologue stamping (step 4.2c).

Seeds a batch of six samples folded into the ledger: three bromide adducts. The
first one's 81Br line is a peak the samples' own runs read as its isotopologue
in two samples and left unassigned in the rest; the second one's is a peak
every sample assigned a formula of its own, at the right height; the third
one's is such a peak at a third of it. One sample is folded from a run of its
own, whose fitted envelope tolerance (a TOF's centre of +20%) is the only thing
that admits that sample's first line, read 20% high. The reading's arithmetic
is the library's (``test_diagnostic_isotopologues.py``) and the stamping's the
pure tests' (``unit/.../test_batch_isotopologues.py``); what is proven here is
the batch-level contract: the records written on the parents and the lines,
each sample's run tolerance read, nothing else moving, the consensus keeping
the records and a parent whose reading moves or goes taking its stamps off its
lines, a new stamping replacing the last, the ledger, an earlier run's snapshot
and the member rows serving them, and the route running it as a batch run. See
``batch_isotopologues.py``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from mascope_backend.api.new.peak_assignments import batch_peaks_routes
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    run_batch_isotopologues,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    DIAGNOSTIC_ISOTOPOLOGUES_KEY,
    STAMP_PICKER,
    STAMP_PREDICTED,
    STAMPED_ISOTOPOLOGUE_OF_KEY,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ISOTOPOLOGUES,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.db import (
    BatchPeak,
    BatchPeakRun,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.diagnostic_isotopologues import (
    VERDICT_NOT_STAMPED,
    VERDICT_STAMPED,
)


pytestmark = pytest.mark.asyncio

#: A real adduct no other suite names: the mechanism table's notations are
#: unique, and the integration database is shared by the whole run.
MECH, NOTATION = "mech-stamp-br", "[M+Br]-"

N_SAMPLES = 6
#: The sample folded from a run of its own.
RUN_SAMPLE = "S00"
#: The samples whose own runs read the first line as its parent's isotopologue.
PICKER_SAMPLES = ("S01", "S02")

BR81 = 0.4931 / 0.5069
BR_OFFSET = 1.997953

#: A TOF run's fitted tolerance: its lines read 20% high, about that centre.
CENTRE_RECORD = {
    "form": "centre_width",
    "level": "own",
    "centre": 0.2,
    "width": 0.05,
    "floor": 0.05,
    "lines": 30,
    "absent": 0,
    "detect_snr": 3.0,
}


#: Peak id -> (m/z, formula, ion formula, intensity of sample i).
def _peaks():
    return {
        "p": (138.94, "C2H4O2", "C2H4BrO2-", lambda i: 1e5 * (1 + i)),
        "line": (
            138.94 + BR_OFFSET,
            None,
            None,
            lambda i: 1e5 * (1 + i) * BR81 * (1.2 if i == 0 else 1.0),
        ),
        "p2": (152.9557, "C3H6O2", "C3H6BrO2-", lambda i: 2e5 * (1 + i)),
        "x": (
            152.9557 + BR_OFFSET,
            "C5H8O3",
            "C5H7O3-",
            lambda i: 2e5 * (1 + i) * BR81,
        ),
        "p3": (166.97, "C4H8O2", "C4H8BrO2-", lambda i: 3e5 * (1 + i)),
        "y": (166.97 + BR_OFFSET, "C6H10O3", "C6H9O3-", lambda i: 3e5 * (1 + i) * 0.3),
    }


def _rows(sample_item_id, index, *, run_id=None, overrides=None, leave_out=()):
    rows = {}
    for peak_id, (mz, formula, ion, intensity) in _peaks().items():
        if peak_id in leave_out:
            continue
        formula, ion = (overrides or {}).get(peak_id, (formula, ion))
        rows[peak_id] = dict(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=run_id or fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id=peak_id,
            sample_peak_mz=mz,
            sample_peak_intensity=intensity(index),
            role="M0" if formula else "unassigned",
            assigned_formula=formula,
            ion_formula=ion,
            ionization_mechanism_id=MECH if formula else None,
            source="untargeted" if formula else None,
            tier="assigned" if formula else "unassigned",
            fit_score=0.9 if formula else None,
            mz_error_ppm=0.1 if formula else None,
            owner_peak_assignment_id=None,
            provenance=None,
        )
    if f"S{index:02d}" in PICKER_SAMPLES and "line" in rows and "p" in rows:
        rows["line"].update(
            role="iso_child",
            assigned_formula=rows["p"]["assigned_formula"],
            ion_formula="[81Br]C2H4O2-",
            ionization_mechanism_id=MECH,
            source="untargeted",
            tier="assigned",
            fit_score=0.9,
            owner_peak_assignment_id=rows["p"]["peak_assignment_id"],
        )
    return list(rows.values())


async def _seed(async_session_factory, pa_test_data):
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
                sample_batch_name=f"Isotopologues {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, MECH) is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=MECH,
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=NOTATION,
                )
            )
        for i in range(N_SAMPLES):
            name = f"S{i:02d}"
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-isotopologues-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 11, 0, i, 0),
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
                    sample_item_name=f"Isotopologues {name}",
                    sample_item_type="sample",
                    polarity="-",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        # The one sample whose rows are a run's own, with the tolerance it
        # fitted.
        run_id = gen_id()
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=samples[RUN_SAMPLE],
                engine_version="0.1.0-test",
                status="completed",
                config={"envelope_tolerance": CENTRE_RECORD},
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        for row in _rows(samples[RUN_SAMPLE], 0, run_id=run_id):
            session.add(PeakAssignment(**row))
        await session.commit()
    for i in range(N_SAMPLES):
        await _fold(samples, i)
    return {"batch_id": batch_id, "samples": samples}


async def _fold(samples, index, **kwargs):
    name = f"S{index:02d}"
    if name == RUN_SAMPLE and not kwargs:
        folded = await fold_sample_into_batch_peaks(samples[name])
    else:
        folded = await fold_sample_into_batch_peaks(
            samples[name],
            rows=[SimpleNamespace(**r) for r in _rows(samples[name], index, **kwargs)],
            persisted=False,
        )
    assert folded is not None


@pytest_asyncio.fixture
async def isotopologue_batch(async_session_factory, pa_test_data):
    return await _seed(async_session_factory, pa_test_data)


async def _anchors(session_factory, batch_id):
    """Peak id -> its anchor, by the m/z each peak was seeded at."""
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
    by_mz = {round(anchor.mz, 3): anchor for anchor in anchors}
    return {
        peak_id: by_mz.get(round(mz, 3)) for peak_id, (mz, *_rest) in _peaks().items()
    }


# The records are read off the stored provenance by the names a stamping
# writes, not through the constants: a renamed key orphans every record a
# server already holds.
def _parent(anchor):
    return (anchor.provenance or {}).get("diagnostic_isotopologues")


def _line(anchor):
    return (anchor.provenance or {}).get("stamped_isotopologue_of")


def _br(anchor):
    return next(e for e in _parent(anchor)["predicted"] if e["label"] == "81Br")


async def test_the_lines_holding_their_ratio_are_stamped_and_nothing_moves(
    async_session_factory, isotopologue_batch
):
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    before = await _anchors(async_session_factory, batch_id)
    summary = await run_batch_isotopologues(batch_id, batch_peak_run_id="run-stamp")
    anchors = await _anchors(async_session_factory, batch_id)
    ids = {key: anchor.batch_peak_id for key, anchor in anchors.items()}

    p = _br(anchors["p"])
    assert p["batch_peak_id"] == ids["line"]
    assert p["verdict"] == VERDICT_STAMPED
    assert p["stamped"] is True
    assert (p["judged"], p["holds"]) == (N_SAMPLES, N_SAMPLES)
    assert (p["n_predicted"], p["n_picker"]) == (4, 2)
    assert _parent(anchors["p"])["formula"] == "C2H4O2"
    assert _parent(anchors["p"])["ionization_mechanism_id"] == MECH
    assert _parent(anchors["p"])["batch_peak_run_id"] == "run-stamp"

    line = _line(anchors["line"])
    assert line["batch_peak_id"] == ids["p"]
    assert line["label"] == "81Br"
    assert line["stamps"] == {
        samples[name]: (STAMP_PICKER if name in PICKER_SAMPLES else STAMP_PREDICTED)
        for name in samples
    }

    # A peak every sample assigned a formula of its own is the second
    # parent's isotope line where its ratio holds...
    assert _line(anchors["x"])["batch_peak_id"] == ids["p2"]
    assert set(_line(anchors["x"])["stamps"].values()) == {STAMP_PREDICTED}
    # ...and nobody's where it does not.
    assert _br(anchors["p3"])["verdict"] == VERDICT_NOT_STAMPED
    assert _br(anchors["p3"])["batch_peak_id"] == ids["y"]
    # The parents are no one's isotopologue, and the third parent's line is not.
    for key in ("p", "p2", "p3", "y"):
        assert _line(anchors[key]) is None

    assert summary["lines_stamped"] == 2
    assert summary["stamps_picker"] == 2
    assert summary["samples_read"] == N_SAMPLES
    # The seeded files have no peak list to read a noise estimate off.
    assert summary["samples_with_noise"] == 0

    # Nothing but the records moved.
    for key, anchor in anchors.items():
        old = before[key]
        assert anchor.consensus_tier == old.consensus_tier
        assert anchor.consensus_formula == old.consensus_formula
        assert anchor.isotopologue_of == old.isotopologue_of
    assert anchors["x"].consensus_tier == "assigned"


async def test_each_samples_run_tolerance_is_the_one_read(
    async_session_factory, isotopologue_batch, monkeypatch
):
    """The run sample's line reads 20% high: only its run's centre admits it."""
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    await run_batch_isotopologues(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    assert samples[RUN_SAMPLE] in _line(anchors["line"])["stamps"]

    from mascope_backend.api.new.peak_assignments import batch_isotopologues

    async def no_fits(sample_batch_id):
        return {}

    monkeypatch.setattr(batch_isotopologues, "recorded_envelope_tolerances", no_fits)
    await run_batch_isotopologues(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    assert samples[RUN_SAMPLE] not in _line(anchors["line"])["stamps"]
    assert _br(anchors["p"])["holds"] == N_SAMPLES - 1


async def test_each_peaks_noise_is_read_off_its_samples_peak_list(
    async_session_factory, isotopologue_batch, monkeypatch
):
    """At an S/N of three a line's reading cannot tell, so nothing stamps."""
    batch_id = isotopologue_batch["batch_id"]
    from mascope_backend.api.new.peak_assignments import batch_isotopologues

    asked = []

    async def noisy(sample_item_id, peaks):
        asked.append((sample_item_id, frozenset(peaks)))
        return {peak: 3.0 for peak in peaks}

    monkeypatch.setattr(batch_isotopologues, "_sample_noise", noisy)
    summary = await run_batch_isotopologues(batch_id)
    assert len(asked) == N_SAMPLES
    assert summary["samples_with_noise"] == N_SAMPLES
    assert all({"p", "line", "p2", "x", "p3", "y"} == peaks for _, peaks in asked)
    assert summary["lines_stamped"] == 0
    anchors = await _anchors(async_session_factory, batch_id)
    assert _br(anchors["p"])["uninformative"] == N_SAMPLES
    assert _line(anchors["line"]) is None


async def test_the_consensus_keeps_the_records_while_the_parent_reads_the_same(
    async_session_factory, isotopologue_batch
):
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    await run_batch_isotopologues(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    await recompute_batch_consensus(
        batch_id, {anchor.batch_peak_id for anchor in anchors.values()}
    )
    kept = await _anchors(async_session_factory, batch_id)
    assert _parent(kept["p"]) == _parent(anchors["p"])
    assert _line(kept["line"]) == _line(anchors["line"])

    # Most of the batch now reads the first parent as another compound: the
    # reading its isotope lines were predicted for is gone, and so are its
    # stamps; the second parent's stand.
    for i in range(1, N_SAMPLES):
        await _fold(samples, i, overrides={"p": ("C9H8", "C9H8Br-")})
    moved = await _anchors(async_session_factory, batch_id)
    assert moved["p"].consensus_formula == "C9H8"
    assert _parent(moved["p"]) is None
    assert _line(moved["line"]) is None
    assert moved["line"].batch_peak_utc_modified > kept["line"].batch_peak_utc_modified
    assert _line(moved["x"]) == _line(anchors["x"])


async def test_a_line_stamped_for_another_parent_keeps_that_stamp(
    async_session_factory, isotopologue_batch
):
    """A parent's reading moving takes off only the stamps that name it."""
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    await run_batch_isotopologues(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    # The first parent's record still names the line, but the line has since
    # been stamped for the third parent.
    other = {**_line(anchors["line"]), "batch_peak_id": anchors["p3"].batch_peak_id}
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == anchors["line"].batch_peak_id)
            .values(
                provenance={
                    **anchors["line"].provenance,
                    STAMPED_ISOTOPOLOGUE_OF_KEY: other,
                }
            )
        )
        await session.commit()
    for i in range(1, N_SAMPLES):
        await _fold(samples, i, overrides={"p": ("C9H8", "C9H8Br-")})
    moved = await _anchors(async_session_factory, batch_id)
    assert _parent(moved["p"]) is None
    assert _line(moved["line"]) == other


async def test_a_parent_that_goes_takes_its_stamps_off(
    async_session_factory, isotopologue_batch
):
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    await run_batch_isotopologues(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    assert _line(anchors["x"]) is not None
    for i in range(N_SAMPLES):
        await _fold(samples, i, leave_out=("p2",))
    gone = await _anchors(async_session_factory, batch_id)
    assert gone["p2"] is None
    assert _line(gone["x"]) is None
    # The line's own reading did not move; taking the stamp off stamps it.
    assert gone["x"].consensus_formula == anchors["x"].consensus_formula
    assert gone["x"].batch_peak_utc_modified > anchors["x"].batch_peak_utc_modified
    assert _line(gone["line"]) == _line(anchors["line"])


def _fold_while_stamping(monkeypatch, samples, indexes, **kwargs):
    """Fold samples again once the stamping has read the ledger, before it
    writes: as a fold landing while it reads the noise would."""
    from mascope_backend.api.new.peak_assignments import batch_isotopologues

    async def folds_first(needed):
        for i in indexes:
            await _fold(samples, i, **kwargs)
        return {}

    monkeypatch.setattr(batch_isotopologues, "read_noise", folds_first)


async def test_a_parent_moving_while_it_is_stamped_gets_no_record(
    async_session_factory, isotopologue_batch, monkeypatch
):
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    _fold_while_stamping(
        monkeypatch,
        samples,
        range(1, N_SAMPLES),
        overrides={"p": ("C9H8", "C9H8Br-")},
    )
    summary = await run_batch_isotopologues(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["p"].consensus_formula == "C9H8"
    assert _parent(anchors["p"]) is None
    assert _line(anchors["line"]) is None
    # The second parent did not move: its record and its line's stand.
    assert _br(anchors["p2"])["stamped"] is True
    assert _line(anchors["x"])["batch_peak_id"] == anchors["p2"].batch_peak_id
    assert summary["parents_moved"] == 1


async def test_a_parent_going_while_it_is_stamped_leaves_no_stamp(
    async_session_factory, isotopologue_batch, monkeypatch
):
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    _fold_while_stamping(monkeypatch, samples, range(N_SAMPLES), leave_out=("p2",))
    summary = await run_batch_isotopologues(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["p2"] is None
    assert _line(anchors["x"]) is None
    assert _line(anchors["line"])["batch_peak_id"] == anchors["p"].batch_peak_id
    assert summary["parents_moved"] == 1


async def test_a_new_stamping_replaces_the_last(
    async_session_factory, isotopologue_batch
):
    batch_id = isotopologue_batch["batch_id"]
    anchors = await _anchors(async_session_factory, batch_id)
    stale_parent = {
        "formula": "C4H8O2",
        "ionization_mechanism_id": MECH,
        "predicted": [],
    }
    stale_line = {"batch_peak_id": anchors["p3"].batch_peak_id, "stamps": {}}
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == anchors["y"].batch_peak_id)
            .values(provenance={STAMPED_ISOTOPOLOGUE_OF_KEY: stale_line})
        )
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == anchors["p"].batch_peak_id)
            .values(provenance={DIAGNOSTIC_ISOTOPOLOGUES_KEY: stale_parent})
        )
        await session.commit()
    await run_batch_isotopologues(batch_id, batch_peak_run_id="run-2")
    anchors = await _anchors(async_session_factory, batch_id)
    assert _line(anchors["y"]) is None
    assert _parent(anchors["p"])["batch_peak_run_id"] == "run-2"
    assert _br(anchors["p"])["stamped"] is True


async def test_the_ledger_and_the_member_rows_serve_it_and_an_earlier_run_keeps_it(
    guest_client, async_session_factory, isotopologue_batch
):
    batch_id, samples = isotopologue_batch["batch_id"], isotopologue_batch["samples"]
    run_id = await start_run(batch_id, ACTION_ISOTOPOLOGUES)
    await run_batch_isotopologues(batch_id, batch_peak_run_id=run_id)
    await complete_run(batch_id, run_id)
    anchors = await _anchors(async_session_factory, batch_id)
    ids = {key: anchor.batch_peak_id for key, anchor in anchors.items()}

    async def ledger(**params):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch_id}", params={"min_n_present": 1, **params}
        )
        assert response.status_code == 200, response.text
        return {row["batch_peak_id"]: row for row in response.json()["data"]}

    live = await ledger()
    assert live[ids["p"]]["diagnostic_isotopologues"]["n_stamped"] == 1
    served = live[ids["line"]]["stamped_isotopologue_of"]
    assert served["batch_peak_id"] == ids["p"]
    assert served["batch_peak_run_id"] == run_id
    # The ledger leaves the per-sample stamps to the member rows.
    assert "stamps" not in served
    assert live[ids["y"]]["stamped_isotopologue_of"] is None

    later = await start_run(batch_id, ACTION_ISOTOPOLOGUES)
    await complete_run(batch_id, later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier[ids["line"]]["stamped_isotopologue_of"] == served
    assert (
        earlier[ids["p"]]["diagnostic_isotopologues"]
        == live[ids["p"]]["diagnostic_isotopologues"]
    )

    async def members(name):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch_id}/members",
            params={"sample_item_id": samples[name]},
        )
        assert response.status_code == 200, response.text
        return {row["batch_peak_id"]: row for row in response.json()["data"]}

    picked = await members(PICKER_SAMPLES[0])
    assert (
        picked[ids["line"]]["stamped_isotopologue_of"],
        picked[ids["line"]]["stamp_source"],
    ) == (
        ids["p"],
        STAMP_PICKER,
    )
    other = await members("S04")
    assert other[ids["line"]]["stamp_source"] == STAMP_PREDICTED
    assert other[ids["x"]]["stamped_isotopologue_of"] == ids["p2"]
    assert other[ids["y"]]["stamped_isotopologue_of"] is None
    assert other[ids["p"]]["stamp_source"] is None


async def test_the_route_runs_it_as_a_batch_run(
    editor_client, guest_client, async_session_factory, isotopologue_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = isotopologue_batch["batch_id"]
    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/stamp-isotopologues"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/stamp-isotopologues"
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
    [run] = [run for run in runs if run.action == ACTION_ISOTOPOLOGUES]
    assert run.status == "completed"
    assert run.is_current == 1
    assert run.config == {
        "hold_widths": 2.0,
        "max_span": 0.5,
        "min_judged": 3,
        "track_share": 0.8,
    }
    assert run.summary["lines_stamped"] == 2
    anchors = await _anchors(async_session_factory, batch_id)
    assert _parent(anchors["p"])["batch_peak_run_id"] == run.batch_peak_run_id


async def test_the_route_refuses_while_another_run_rewrites_the_ledger(
    editor_client, isotopologue_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "stamp_batch_isotopologues", fake_task)
    await start_run(isotopologue_batch["batch_id"], ACTION_ISOTOPOLOGUES)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{isotopologue_batch['batch_id']}/stamp-isotopologues"
    )
    assert response.status_code == 409, response.text
    assert launched == []
