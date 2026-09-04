"""A derived row's evidence measured on demand for the inspector.

One sample folded without a run, its glucose M0 assigned with its M+1 as an
isotopologue; the seeded scorer is stubbed with a canned pairing. What is
real: the derived row and its family resolved off the batch ledger, the
stored fit kept as the fit, and the response shape the inspector reads.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import derived_evidence
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.engine import evidence_for
from mascope_backend.api.new.peak_assignments.fold_view import (
    fold_assignment_id,
    fold_run_id,
)
from mascope_backend.db import (
    BatchPeak,
    IonizationMechanism,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

M0_ID = gen_id(32)


def _rows(sample_item_id):
    """Glucose at 181 with its M+1 at 182, and an unassigned 250."""
    run_id = fold_run_id(sample_item_id)
    shared = dict(
        peak_assignment_run_id=run_id,
        sample_item_id=sample_item_id,
        sample_peak_tof=None,
        target_compound_id=None,
        target_ion_id=None,
        provenance=None,
        evidence=None,
        p_correct=None,
    )
    return [
        SimpleNamespace(
            peak_assignment_id=M0_ID,
            sample_peak_id="p1",
            sample_peak_mz=181.0707,
            sample_peak_intensity=9000.0,
            role="M0",
            assigned_formula="C6H12O6",
            ion_formula="C6H13O6+",
            ionization_mechanism_id="mech-h",
            source="database",
            tier="assigned",
            fit_score=0.9,
            mz_error_ppm=0.4,
            owner_peak_assignment_id=None,
            **shared,
        ),
        SimpleNamespace(
            peak_assignment_id=gen_id(32),
            sample_peak_id="p2",
            sample_peak_mz=182.0741,
            sample_peak_intensity=700.0,
            role="iso_child",
            assigned_formula="C6H12O6",
            ion_formula="C6H13O6+",
            ionization_mechanism_id="mech-h",
            source="database",
            tier="assigned",
            fit_score=0.9,
            mz_error_ppm=1.1,
            owner_peak_assignment_id=M0_ID,
            **shared,
        ),
        SimpleNamespace(
            peak_assignment_id=gen_id(32),
            sample_peak_id="p3",
            sample_peak_mz=250.1,
            sample_peak_intensity=300.0,
            role="unassigned",
            assigned_formula=None,
            ion_formula=None,
            ionization_mechanism_id=None,
            source=None,
            tier="unassigned",
            fit_score=None,
            mz_error_ppm=None,
            owner_peak_assignment_id=None,
            **shared,
        ),
    ]


@pytest_asyncio.fixture
async def derived_sample(async_session_factory, pa_test_data):
    """A batch of its own with one sample folded without a run."""
    now = datetime.now(timezone.utc)
    batch_id, sample_file_id, sample_item_id = gen_id(), gen_id(), gen_id()
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
                sample_batch_name=f"Evidence {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, "mech-h") is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id="mech-h",
                    ionization_mechanism_polarity="+",
                    ionization_mechanism="H+ (derived evidence test)",
                )
            )
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"orbi-evidence-{sample_file_id}.zarr",
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
                sample_item_name="Evidence S1",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        await session.commit()
    assert (
        await fold_sample_into_batch_peaks(
            sample_item_id, rows=_rows(sample_item_id), persisted=False
        )
        == batch_id
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
    by_mz = {round(anchor.mz): anchor.batch_peak_id for anchor in anchors}
    return {"sample_item_id": sample_item_id, "anchors": by_mz}


@pytest.fixture
def stubbed_scorer(monkeypatch):
    """The seeded scorer answering with glucose's envelope paired to the 181
    and the 182, and an M+2 nothing paired to."""
    calls = []

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01)

    async def seeded(sample, seeds, params):
        calls.append(set(seeds))
        ion_by_seed = {("C6H12O6", "mech-h"): "ion-glc"}
        fit_by_ion = {"ion-glc": 0.85}
        scored = pd.DataFrame(
            [
                {
                    "target_ion_id": "ion-glc",
                    "mz": 181.0707,
                    "target_isotope_formula": "C6H13O6+",
                    "relative_abundance": 1.0,
                    "sample_peak_id": "p1",
                    "match_mz_error": 0.4,
                    "match_abundance_error": 0.02,
                    "target_ion_formula": "C6H13O6+",
                },
                {
                    "target_ion_id": "ion-glc",
                    "mz": 182.0741,
                    "target_isotope_formula": "C5[13C]H13O6+",
                    "relative_abundance": 0.066,
                    "sample_peak_id": "p2",
                    "match_mz_error": 1.1,
                    "match_abundance_error": -0.05,
                    "target_ion_formula": "C6H13O6+",
                },
                {
                    "target_ion_id": "ion-glc",
                    "mz": 183.0775,
                    "target_isotope_formula": "C4[13C]2H13O6+",
                    "relative_abundance": 0.004,
                    "sample_peak_id": None,
                    "match_mz_error": None,
                    "match_abundance_error": None,
                    "target_ion_formula": "C6H13O6+",
                },
            ]
        )
        return ion_by_seed, fit_by_ion, {}, scored

    monkeypatch.setattr(derived_evidence, "default_match_params", match_params)
    monkeypatch.setattr(derived_evidence, "score_seeds", seeded)
    return calls


def _url(sample_item_id, assignment_id):
    return f"/api/peak-assignments/sample/{sample_item_id}/assignment/{assignment_id}/evidence"


async def test_the_m0_is_measured_and_its_family_reported_with_the_stored_fit(
    guest_client, derived_sample, stubbed_scorer
):
    sample_item_id = derived_sample["sample_item_id"]
    m0_id = fold_assignment_id(derived_sample["anchors"][181])

    response = await guest_client.get(_url(sample_item_id, m0_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"] == 1
    entry = body["data"][0]
    assert entry["peak_assignment_id"] == m0_id
    assert entry["sample_peak_id"] == "p1"
    assert entry["assigned_formula"] == "C6H12O6"
    assert entry["ionization_mechanism_id"] == "mech-h"
    assert entry["blocked_reason"] is None
    # The stored fit stays the fit the tier was read off; the measurement's
    # own fit is reported beside it.
    assert entry["fit_score"] == pytest.approx(0.9, abs=1e-6)
    assert entry["measured_fit_score"] == 0.85
    assert entry["evidence"] == pytest.approx(evidence_for(0.9, "C6H12O6"), abs=1e-6)
    assert 0 < entry["plausibility"] <= 1
    assert entry["mz_error_ppm"] == 0.4
    assert entry["abundance_error"] == 0.02
    assert [iso["isotope_label"] for iso in entry["isotopologues"]] == [
        "M0",
        "M+1",
        "M+2",
    ]
    assert entry["isotopologues"][1]["sample_peak_id"] == "p2"
    assert entry["isotopologues"][1]["mz_error_ppm"] == 1.1
    assert entry["isotopologues"][1]["isotope_formula"] == "C5[13C]H13O6+"
    assert entry["isotopologues"][2]["sample_peak_id"] is None
    assert stubbed_scorer == [{("C6H12O6", "mech-h")}]


async def test_an_isotopologue_is_measured_through_its_m0(
    guest_client, derived_sample, stubbed_scorer
):
    sample_item_id = derived_sample["sample_item_id"]
    child_id = fold_assignment_id(derived_sample["anchors"][182])
    m0_id = fold_assignment_id(derived_sample["anchors"][181])

    response = await guest_client.get(_url(sample_item_id, child_id))

    assert response.status_code == 200, response.text
    entry = response.json()["data"][0]
    # The entry is the family's, keyed by the M0; the child reads its own
    # numbers off the isotopologue that paired to its peak.
    assert entry["peak_assignment_id"] == m0_id
    assert entry["sample_peak_id"] == "p1"
    assert entry["isotopologues"][1]["sample_peak_id"] == "p2"


async def test_an_unassigned_derived_row_says_there_is_nothing_to_measure(
    guest_client, derived_sample, stubbed_scorer
):
    sample_item_id = derived_sample["sample_item_id"]
    bare_id = fold_assignment_id(derived_sample["anchors"][250])

    response = await guest_client.get(_url(sample_item_id, bare_id))

    assert response.status_code == 200, response.text
    entry = response.json()["data"][0]
    assert entry["assigned_formula"] is None
    assert "no formula" in entry["blocked_reason"]
    assert entry["isotopologues"] == []
    assert stubbed_scorer == []


async def test_a_runs_own_row_and_an_unknown_derived_row(
    guest_client, derived_sample, stubbed_scorer
):
    sample_item_id = derived_sample["sample_item_id"]

    own = await guest_client.get(_url(sample_item_id, "some-run-row"))
    assert own.status_code == 200, own.text
    assert own.json()["results"] == 0
    assert own.json()["data"] == []

    missing = await guest_client.get(_url(sample_item_id, fold_assignment_id("nope")))
    assert missing.status_code == 404
    assert stubbed_scorer == []
