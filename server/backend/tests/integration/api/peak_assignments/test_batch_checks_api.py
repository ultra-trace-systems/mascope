"""Integration tests for the batch's isotope checks (step 4.2g).

Seeds a batch of six samples folded into the ledger: a bromide cluster with
its 81Br line, a labelled reagent core and its 14N impurity line, a labelled
analyte cluster whose 14N line runs three times the impurity's share, a
labelled reading with no 14N line at all, and a neutral seen protonated and
urea-clustered. One sample is folded from a run of its own. The batch is
stamped (step 4.2c) and read for co-variation (step 4.2) by those runs' own
code, so the checks read real records. The rules are the library's
(``test_batch_check_rules.py``) and the reading's arithmetic the pure tests'
(``unit/.../test_batch_checks.py``); what is proven here is the batch-level
contract: the facts written on the batch peaks, nothing else moving, the noise
read off the samples, the consensus keeping a record while its reading stands,
a batch peak that moves while it is read getting none, a new reading replacing
the last, the ledger, an earlier run's snapshot, the member rows and a
sample's detail serving them, and the route running it as a batch run. See
``batch_checks.py``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from mascope_backend.api.new.peak_assignments import (
    batch_checks,
    batch_isotopologues,
    batch_peaks_routes,
)
from mascope_backend.api.new.peak_assignments.batch_checks import run_batch_checks
from mascope_backend.api.new.peak_assignments.batch_coherence import (
    run_batch_co_variation,
)
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    run_batch_isotopologues,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_BATCH_CHECKS,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    fold_assignment_id,
    fold_run_id,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

#: The mechanisms the seeded rows read through. The mechanism table's notations
#: are unique and the integration database is shared by the whole run, so each
#: is a spelling no other suite stores: the halogen lock and the twin read no
#: notation, and the uronium pair's two are legacy and reordered spellings,
#: which the reading compares by their standard key.
NOTATIONS = {
    "br": ("[M+Br]- (batch checks test)", "-"),
    "ln": ("[M+^NO3]- (batch checks test)", "-"),
    "n": ("[M+NO3]- (batch checks test)", "-"),
    "h": ("+H+", "+"),
    "u": ("[M+H+CH4N2O]+", "+"),
}

N_SAMPLES = 6
#: The sample folded from a run of its own.
RUN_SAMPLE = "S00"

BR81 = 0.4931 / 0.5069
BR_OFFSET = 1.997952
TWIN_OFFSET = -0.997035
TWIN = 0.02 / 0.98
NOISE = 1000.0

#: The run sample's fitted tolerance: a detection threshold no twin reaches, so
#: that sample cannot tell a twin's absence.
BLIND_RECORD = {
    "form": "centre_width",
    "level": "own",
    "centre": 0.0,
    "width": 0.05,
    "floor": 0.05,
    "lines": 30,
    "absent": 0,
    "detect_snr": 1e9,
}


#: Peak id -> (m/z, formula, ion formula, mechanism, role, owner peak,
#: intensity of sample i).
def _peaks():
    return {
        "p": (138.94, "C2H4O2", "C2H4BrO2-", "br", "M0", None, lambda i: 1e5 * (1 + i)),
        "line": (
            138.94 + BR_OFFSET,
            None,
            None,
            None,
            "unassigned",
            None,
            lambda i: 1e5 * (1 + i) * BR81,
        ),
        "core": (62.9854, None, "^NO3-", None, "reagent", None, lambda i: 1e7),
        "core14": (
            62.9854 + TWIN_OFFSET,
            None,
            "NO3-",
            None,
            "reagent",
            "core",
            lambda i: 1e7 * TWIN,
        ),
        "x": (
            263.0903,
            "C10H16O4",
            "C10H16O4^NO3-",
            "ln",
            "M0",
            None,
            lambda i: 2e5 * (1 + i),
        ),
        "x14": (
            263.0903 + TWIN_OFFSET,
            "C10H16O4",
            "C10H16NO7-",
            "n",
            "M0",
            None,
            lambda i: 2e5 * (1 + i) * TWIN * 3,
        ),
        "y": (
            235.0590,
            "C8H12O4",
            "C8H12O4^NO3-",
            "ln",
            "M0",
            None,
            lambda i: 3e5 * (1 + i),
        ),
        "h": (
            153.1274,
            "C10H16O",
            "C10H17O+",
            "h",
            "M0",
            None,
            lambda i: 5e4 * (1 + i),
        ),
        "u": (
            213.1598,
            "C10H16O",
            "C11H21N2O2+",
            "u",
            "M0",
            None,
            lambda i: 4e4 * (1 + i),
        ),
    }


def _rows(sample_item_id, index, mechanisms, *, run_id=None, overrides=None):
    rows = {}
    for peak_id, (mz, formula, ion, mech, role, owner, intensity) in _peaks().items():
        formula, ion = (overrides or {}).get(peak_id, (formula, ion))
        rows[peak_id] = dict(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=run_id or fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id=peak_id,
            sample_peak_mz=mz,
            sample_peak_intensity=intensity(index),
            role=role,
            assigned_formula=formula,
            ion_formula=ion,
            ionization_mechanism_id=mechanisms[mech] if mech else None,
            source="untargeted" if formula else None,
            tier="assigned" if formula else "unassigned",
            fit_score=0.9 if formula else None,
            mz_error_ppm=0.1 if formula else None,
            owner_peak_assignment_id=owner,
            provenance=None,
        )
    for row in rows.values():
        if row["owner_peak_assignment_id"]:
            row["owner_peak_assignment_id"] = rows[row["owner_peak_assignment_id"]][
                "peak_assignment_id"
            ]
    return rows


async def _mechanisms(session) -> dict[str, str]:
    """Key -> the mechanism id each seeded notation is stored under."""
    found = {}
    for key, (notation, polarity) in NOTATIONS.items():
        mechanism_id = (
            await session.execute(
                select(IonizationMechanism.ionization_mechanism_id).where(
                    IonizationMechanism.ionization_mechanism == notation
                )
            )
        ).scalar()
        if mechanism_id is None:
            mechanism_id = f"mech-checks-{key}"
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=mechanism_id,
                    ionization_mechanism_polarity=polarity,
                    ionization_mechanism=notation,
                )
            )
        found[key] = mechanism_id
    return found


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
                sample_batch_name=f"Checks {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        mechanisms = await _mechanisms(session)
        for i in range(N_SAMPLES):
            name = f"S{i:02d}"
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-checks-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 12, 0, i, 0),
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
                    sample_item_name=f"Checks {name}",
                    sample_item_type="sample",
                    polarity="-",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        run_id = gen_id()
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=samples[RUN_SAMPLE],
                engine_version="0.1.0-test",
                status="completed",
                config={"envelope_tolerance": BLIND_RECORD},
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        run_rows = _rows(samples[RUN_SAMPLE], 0, mechanisms, run_id=run_id)
        for row in run_rows.values():
            session.add(PeakAssignment(**row))
        await session.commit()
    seeded = {
        "batch_id": batch_id,
        "samples": samples,
        "mechanisms": mechanisms,
        "run_rows": {k: row["peak_assignment_id"] for k, row in run_rows.items()},
    }
    for i in range(N_SAMPLES):
        await _fold(seeded, i)
    return seeded


async def _fold(seeded, index, **kwargs):
    name = f"S{index:02d}"
    sample = seeded["samples"][name]
    if name == RUN_SAMPLE and not kwargs:
        folded = await fold_sample_into_batch_peaks(sample)
    else:
        rows = _rows(sample, index, seeded["mechanisms"], **kwargs)
        folded = await fold_sample_into_batch_peaks(
            sample,
            rows=[SimpleNamespace(**r) for r in rows.values()],
            persisted=False,
        )
    assert folded is not None


@pytest.fixture
def noisy(monkeypatch):
    """Every peak's signal-to-noise, as its sample's peak list would give it:
    the seeded files have none to read."""
    asked = []

    async def noise(sample_item_id, peaks):
        asked.append((sample_item_id, frozenset(peaks)))
        return {peak: NOISE for peak in peaks}

    monkeypatch.setattr(batch_isotopologues, "_sample_noise", noise)
    return asked


@pytest_asyncio.fixture
async def checks_batch(async_session_factory, pa_test_data, noisy):
    seeded = await _seed(async_session_factory, pa_test_data)
    await run_batch_isotopologues(seeded["batch_id"], batch_peak_run_id="run-stamp")
    await run_batch_co_variation(seeded["batch_id"], batch_peak_run_id="run-cov")
    return seeded


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
    return {peak_id: by_mz.get(round(spec[0], 3)) for peak_id, spec in _peaks().items()}


# The records are read off the stored provenance by the name a reading writes,
# not through the constant: a renamed key orphans every record a server holds.
def _checks(anchor):
    return (anchor.provenance or {}).get("batch_checks")


async def test_the_facts_are_recorded_and_nothing_moves(
    async_session_factory, checks_batch, noisy
):
    batch_id = checks_batch["batch_id"]
    before = await _anchors(async_session_factory, batch_id)
    noisy.clear()
    summary = await run_batch_checks(batch_id, batch_peak_run_id="run-checks")
    anchors = await _anchors(async_session_factory, batch_id)
    ids = {key: anchor.batch_peak_id for key, anchor in anchors.items()}

    lock = _checks(anchors["p"])["halogen_lock"]
    assert (lock["verdict"], lock["element"], lock["label"]) == ("locked", "Br", "81Br")
    assert (lock["n_ion"], lock["n_neutral"]) == (1, 0)
    assert lock["batch_peak_id"] == ids["line"]
    assert lock["n_stamped_in"] == N_SAMPLES
    assert lock["stamping_run_id"] == "run-stamp"
    assert _checks(anchors["p"])["formula"] == "C2H4O2"
    assert _checks(anchors["p"])["batch_peak_run_id"] == "run-checks"

    core = _checks(anchors["core"])["label_twin"]
    assert (core["verdict"], core["batch_peak_id"]) == ("present", ids["core14"])
    # The run sample's own detection threshold leaves it out.
    assert core["judged"] == N_SAMPLES - 1
    assert core["twin_role"] == "reagent"
    assert _checks(anchors["core"])["ion_formula"] == "^NO3-"
    cluster = _checks(anchors["x"])["label_twin"]
    assert (cluster["verdict"], cluster["batch_peak_id"]) == ("present", ids["x14"])
    assert cluster["excess"] == pytest.approx(3.0, rel=1e-3)
    assert cluster["twin_ion_formula"] == "C10H16NO7-"
    missing = _checks(anchors["y"])["label_twin"]
    assert (missing["verdict"], missing["judged"], missing["present"]) == (
        "absent",
        N_SAMPLES - 1,
        0,
    )
    # No unlabelled ion is read for a twin.
    for key in ("x14", "core14", "p", "h"):
        assert "label_twin" not in (_checks(anchors[key]) or {})

    for own, partner in (("h", "u"), ("u", "h")):
        pair = _checks(anchors[own])["neutral_pair"]
        assert pair["partner_batch_peak_id"] == ids[partner]
        assert pair["co_variation_run_id"] == "run-cov"
        # Six samples are too few spectra for a co-variation reading.
        assert (pair["verdict"], pair["reason"]) == ("untestable", "few_spectra")

    assert (summary["locked"], summary["locked_br"]) == (1, 1)
    assert (summary["labelled_ions"], summary["twin_present"]) == (3, 2)
    assert summary["twin_absent"] == 1
    assert (summary["pairs"], summary["pairs_untestable"]) == (1, 1)
    assert summary["samples_read"] == N_SAMPLES
    assert summary["samples_with_noise"] == N_SAMPLES
    assert summary["anchors_moved"] == 0
    # The noise read is the labelled ions' and their twins'.
    assert {peaks for _, peaks in noisy} == {
        frozenset({"core", "core14", "x", "x14", "y"})
    }

    # Nothing but the records moved.
    for key, anchor in anchors.items():
        old = before[key]
        assert anchor.consensus_tier == old.consensus_tier
        assert anchor.consensus_formula == old.consensus_formula
        assert anchor.isotopologue_of == old.isotopologue_of
        old_provenance = {
            k: v for k, v in (old.provenance or {}).items() if k != "batch_checks"
        }
        new_provenance = {
            k: v for k, v in (anchor.provenance or {}).items() if k != "batch_checks"
        }
        assert new_provenance == old_provenance


async def test_the_consensus_keeps_a_record_while_the_reading_stands(
    async_session_factory, checks_batch
):
    batch_id = checks_batch["batch_id"]
    await run_batch_checks(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    await recompute_batch_consensus(
        batch_id, {anchor.batch_peak_id for anchor in anchors.values()}
    )
    anchors = await _anchors(async_session_factory, batch_id)
    assert _checks(anchors["x"])["label_twin"]["verdict"] == "present"
    # Every sample reads the labelled cluster as another formula: its record,
    # read for the old one, comes off; the others stand.
    for i in range(N_SAMPLES):
        await _fold(checks_batch, i, overrides={"x": ("C11H20O3", "C11H20O3^NO3-")})
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["x"].consensus_formula == "C11H20O3"
    assert _checks(anchors["x"]) is None
    assert _checks(anchors["y"])["label_twin"]["verdict"] == "absent"


async def test_a_batch_peak_moving_while_it_is_read_gets_no_record(
    async_session_factory, checks_batch, monkeypatch
):
    batch_id = checks_batch["batch_id"]
    read = batch_checks.read_noise

    async def folds_first(needed):
        for i in range(N_SAMPLES):
            await _fold(checks_batch, i, overrides={"y": ("C9H16O3", "C9H16O3^NO3-")})
        return await read(needed)

    monkeypatch.setattr(batch_checks, "read_noise", folds_first)
    summary = await run_batch_checks(batch_id)
    anchors = await _anchors(async_session_factory, batch_id)
    assert anchors["y"].consensus_formula == "C9H16O3"
    assert _checks(anchors["y"]) is None
    assert _checks(anchors["x"])["label_twin"]["verdict"] == "present"
    assert summary["anchors_moved"] == 1


async def test_a_new_reading_replaces_the_last(async_session_factory, checks_batch):
    batch_id = checks_batch["batch_id"]
    anchors = await _anchors(async_session_factory, batch_id)
    stale = {
        "formula": None,
        "ion_formula": "unread",
        "ionization_mechanism_id": None,
        "label_twin": {"verdict": "present"},
    }
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == anchors["line"].batch_peak_id)
            .values(
                provenance={
                    **(anchors["line"].provenance or {}),
                    "batch_checks": stale,
                }
            )
        )
        await session.commit()
    await run_batch_checks(batch_id, batch_peak_run_id="run-2")
    anchors = await _anchors(async_session_factory, batch_id)
    assert _checks(anchors["line"]) is None
    assert _checks(anchors["p"])["batch_peak_run_id"] == "run-2"


async def test_the_ledger_the_members_and_a_sample_s_detail_serve_it(
    guest_client, async_session_factory, checks_batch
):
    batch_id, samples = checks_batch["batch_id"], checks_batch["samples"]
    run_id = await start_run(batch_id, ACTION_BATCH_CHECKS)
    await run_batch_checks(batch_id, batch_peak_run_id=run_id)
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
    assert live[ids["p"]]["batch_checks"]["halogen_lock"]["verdict"] == "locked"
    assert live[ids["x"]]["batch_checks"]["label_twin"]["verdict"] == "present"
    assert live[ids["line"]]["batch_checks"] is None

    later = await start_run(batch_id, ACTION_BATCH_CHECKS)
    await complete_run(batch_id, later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier[ids["p"]]["batch_checks"] == live[ids["p"]]["batch_checks"]

    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}/members",
        params={"sample_item_id": samples["S03"]},
    )
    assert response.status_code == 200, response.text
    members = {row["batch_peak_id"]: row for row in response.json()["data"]}
    assert (
        members[ids["p"]]["halogen_lock"],
        members[ids["p"]]["halogen_element"],
    ) == (
        "locked",
        "Br",
    )
    assert members[ids["y"]]["label_twin"] == "absent"
    assert members[ids["h"]]["neutral_pair"] == "untestable"
    assert members[ids["line"]]["halogen_lock"] is None

    async def detail(sample_item_id, peak_assignment_id):
        response = await guest_client.get(
            f"/api/peak-assignments/sample/{sample_item_id}/assignment/"
            f"{peak_assignment_id}"
        )
        assert response.status_code == 200, response.text
        [row] = response.json()["data"]
        return row

    run_rows = checks_batch["run_rows"]
    own = await detail(samples[RUN_SAMPLE], run_rows["x"])
    assert own["batch_checks"] == live[ids["x"]]["batch_checks"]
    reagent = await detail(samples[RUN_SAMPLE], run_rows["core"])
    assert reagent["batch_checks"]["label_twin"]["verdict"] == "present"
    # The 14N line's own row is not what was read.
    assert (await detail(samples[RUN_SAMPLE], run_rows["x14"]))["batch_checks"] is None
    derived = await detail(samples["S03"], fold_assignment_id(ids["p"]))
    assert derived["batch_checks"]["halogen_lock"]["element"] == "Br"

    # Where the run's row reads its peak another way, the facts are not its.
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(PeakAssignment.peak_assignment_id == run_rows["x"])
            .values(ionization_mechanism_id=checks_batch["mechanisms"]["n"])
        )
        await session.commit()
    assert (await detail(samples[RUN_SAMPLE], run_rows["x"]))["batch_checks"] is None


async def test_the_route_runs_it_as_a_batch_run(
    editor_client, guest_client, async_session_factory, checks_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = checks_batch["batch_id"]
    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/read-batch-checks"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/read-batch-checks"
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
        members = (
            await session.execute(
                select(BatchPeakOccurrence.role, BatchPeakOccurrence.tier)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == batch_id)
            )
        ).all()
    [run] = [run for run in runs if run.action == ACTION_BATCH_CHECKS]
    assert run.status == "completed"
    assert run.is_current == 1
    assert run.config["absent_share"] == 0.8
    assert run.summary["locked"] == 1
    anchors = await _anchors(async_session_factory, batch_id)
    assert _checks(anchors["p"])["batch_peak_run_id"] == run.batch_peak_run_id
    assert len(members) == N_SAMPLES * len(_peaks())


async def test_the_route_refuses_while_another_run_rewrites_the_ledger(
    editor_client, checks_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "read_batch_checks", fake_task)
    await start_run(checks_batch["batch_id"], ACTION_BATCH_CHECKS)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{checks_batch['batch_id']}/read-batch-checks"
    )
    assert response.status_code == 409, response.text
    assert launched == []
