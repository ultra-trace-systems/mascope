"""
Integration tests for manual curation of an assignment (the PATCH write path).

Two actions share one endpoint: promoting one of a row's stored runner-ups, and
committing a composition the caller names (the re-search case). Both edit the
row in place and mark it as human-made.

Every test here MUTATES assignment rows, so none of them may touch the
session-scoped ``pa_test_data`` ledger: ``curated_run`` seeds a run of its own
per test and deletes it afterwards.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import (
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
)
from mascope_backend.db.id import gen_id


@pytest.fixture
def feature_disabled(monkeypatch):
    """Force the peak assignment feature off for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    """Curation is a gated write, so every test here opts the environment in.

    Autouse rather than requested per test: the flag-off case overrides it, and
    a test that forgot it would report the feature gate's 403 as the role
    gate's - the two are indistinguishable in a sanitized error body.
    """
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


#: The bands this run tiered with. Deliberately not the engine defaults
#: (0.8 / 0.5), so a tier recomputed under *the run's* bands is distinguishable
#: from one recomputed under the defaults.
RUN_BANDS = {"assigned": 0.9, "candidate": 0.4}

#: The runner-up promoted by most tests. Its fit sits between the two bands, so
#: promoting it must land 'candidate' - neither the winner's 'assigned' tier
#: inherited, nor 'assigned' under the default bands.
ALTERNATIVE = {
    "assigned_formula": "C7H16O5",
    "ion_formula": "C7H17O5+",
    "fit_score": 0.62,
    "mz_error_ppm": 4.2,
    "plausibility": 0.5,
    "source": "database",
}


@pytest_asyncio.fixture
async def curated_run(async_session_factory, pa_test_data):
    """A completed run of its own, with an M0, its satellite, and a blank peak.

    Function-scoped and self-deleting: these tests rewrite the rows they touch,
    and the shared ``pa_test_data`` ledger is read by every other module in the
    session.
    """
    now = datetime.now(timezone.utc)
    run_id = gen_id()
    m0_id = gen_id(32)
    child_id = gen_id(32)
    blank_id = gen_id(32)
    mechanism_id = gen_id()

    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="+",
                # Unique per test: the notation carries a unique constraint.
                ionization_mechanism=f"+H+ ({mechanism_id})",
            )
        )
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=pa_test_data["sample_item_id"],
                engine_version="0.1.0-test",
                status="completed",
                config={"run_untargeted": True},
                tier_bands=dict(RUN_BANDS),
                peak_assignment_run_utc_created=now - timedelta(minutes=10),
                peak_assignment_run_utc_completed=now - timedelta(minutes=9),
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=m0_id,
                peak_assignment_run_id=run_id,
                sample_item_id=pa_test_data["sample_item_id"],
                sample_peak_id="cur-1",
                sample_peak_mz=181.0707,
                sample_peak_intensity=5000.0,
                role="M0",
                assigned_formula="C6H12O6",
                ion_formula="C6H13O6+",
                ionization_mechanism_id=mechanism_id,
                isotope_label="M0",
                source="database",
                fit_score=0.95,
                mz_error_ppm=1.2,
                abundance_error=0.05,
                tier="assigned",
                alternatives=[dict(ALTERNATIVE)],
                provenance={
                    "plausibility": 0.9,
                    "confidence": 0.8,
                    "evidence": 0.87,
                    "calibrated": True,
                    "p_correct": 0.93,
                    "calibration": {"provisional": True},
                    "corroboration": {"n_adducts": 2, "adducts": ["+H+", "+Na+"]},
                },
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=child_id,
                peak_assignment_run_id=run_id,
                sample_item_id=pa_test_data["sample_item_id"],
                sample_peak_id="cur-2",
                sample_peak_mz=182.0741,
                sample_peak_intensity=350.0,
                role="iso_child",
                assigned_formula="C6H12O6",
                ion_formula="C6H13O6+",
                ionization_mechanism_id=mechanism_id,
                isotope_label="M+1",
                source="database",
                fit_score=0.88,
                tier="assigned",
                owner_peak_assignment_id=m0_id,
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=blank_id,
                peak_assignment_run_id=run_id,
                sample_item_id=pa_test_data["sample_item_id"],
                sample_peak_id="cur-3",
                sample_peak_mz=250.5,
                sample_peak_intensity=42.0,
                role="unassigned",
                tier="unassigned",
            )
        )
        await session.commit()

    yield {
        "sample_item_id": pa_test_data["sample_item_id"],
        "run_id": run_id,
        "m0_id": m0_id,
        "child_id": child_id,
        "blank_id": blank_id,
        "mechanism_id": mechanism_id,
    }

    async with async_session_factory() as session:
        # The run cascades to its assignments; the mechanism is independent.
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.peak_assignment_run_id == run_id
            )
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


def _url(data: dict, assignment_id: str) -> str:
    return (
        f"/api/peak-assignments/sample/{data['sample_item_id']}"
        f"/assignment/{assignment_id}"
    )


async def _row(async_session_factory, assignment_id: str) -> PeakAssignment:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(PeakAssignment).where(
                    PeakAssignment.peak_assignment_id == assignment_id
                )
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# promote_alternative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promoting_a_runner_up_swaps_it_with_the_winner(
    editor_client, curated_run, async_session_factory
):
    """The chosen candidate becomes the assignment; the winner it beat becomes
    the first close alternative, which is what makes the change undoable."""
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 0,
            "expected_formula": "C7H16O5",
        },
    )

    assert response.status_code == 200
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C7H16O5"
    assert row.ion_formula == "C7H17O5+"
    assert row.fit_score == pytest.approx(0.62)
    assert row.mz_error_ppm == pytest.approx(4.2)
    assert row.source == "manual"
    assert row.alternatives[0]["assigned_formula"] == "C6H12O6"
    assert row.alternatives[0]["fit_score"] == pytest.approx(0.95)
    # The promoted entry left the list rather than being duplicated in it.
    assert [alt["assigned_formula"] for alt in row.alternatives] == ["C6H12O6"]


@pytest.mark.asyncio
async def test_the_tier_is_recomputed_under_the_runs_own_bands(
    editor_client, curated_run, async_session_factory
):
    """0.62 is 'candidate' under this run's 0.9/0.4 bands.

    Inheriting the winner's tier would leave it 'assigned', and re-tiering
    under the engine's default 0.8/0.5 bands would too - so this pins both the
    recompute and the fact that it reads the run's bands rather than defaults.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.tier == "candidate"


@pytest.mark.asyncio
async def test_the_calibrated_fields_do_not_survive_the_override(
    editor_client, curated_run, async_session_factory
):
    """P(correct) belongs to the arbitration that produced the OLD winner.

    Carried across it would read as a calibrated probability for a formula the
    calibration never scored - the fabricated number the whole calibration
    layer exists to refuse. It is archived with the winner it describes.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    row = await _row(async_session_factory, curated_run["m0_id"])
    for key in (
        "p_correct",
        "calibrated",
        "calibration",
        "corroboration",
        "confidence",
    ):
        assert key not in row.provenance
    archived = row.provenance["manual"]["previous"]["engine_judgement"]
    assert archived["p_correct"] == pytest.approx(0.93)
    assert archived["corroboration"]["n_adducts"] == 2


@pytest.mark.asyncio
async def test_the_override_records_who_changed_it_and_from_what(
    editor_client, curated_run, async_session_factory
):
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.json()["data"][0]["source"] == "manual"
    row = await _row(async_session_factory, curated_run["m0_id"])
    manual = row.provenance["manual"]
    assert manual["action"] == "promote_alternative"
    assert manual["scored_by"] == "run_alternative"
    assert manual["previous_formula"] == "C6H12O6"
    assert manual["user_id"] is not None
    assert manual["at"]
    # Plausibility is a property of the formula, so it is computed here rather
    # than carried from the candidate's own claim about itself.
    assert row.provenance["plausibility"] is not None


@pytest.mark.asyncio
async def test_satellites_of_the_replaced_formula_are_demoted(
    editor_client, curated_run, async_session_factory
):
    """A satellite is the same compound as its M0 seen through one heavy atom.

    Once a person has rejected that compound, the satellite has nothing left to
    claim - and leaving it assigned would let one isotopologue family show two
    different formulas.
    """
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    body = response.json()
    assert body["results"] == 2
    assert body["data"][1]["peak_assignment_id"] == curated_run["child_id"]

    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula is None
    assert child.role == "unassigned"
    assert child.tier == "unassigned"
    assert child.owner_peak_assignment_id is None
    assert child.fit_score is None
    # Nothing is thrown away: what it used to be is recoverable from the row.
    assert child.alternatives[0]["assigned_formula"] == "C6H12O6"
    assert child.provenance["manual"]["previous_owner_formula"] == "C6H12O6"


@pytest.mark.asyncio
async def test_a_stale_index_is_refused_rather_than_committed(
    editor_client, curated_run, async_session_factory
):
    """The guard exists for the second curator: between reading the card and
    clicking it, position 0 may hold a different candidate entirely."""
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 0,
            "expected_formula": "C9H20O2",
        },
    )

    assert response.status_code == 409
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"
    assert row.source == "database"


@pytest.mark.asyncio
async def test_an_index_past_the_end_is_a_verdict_on_the_request(
    editor_client, curated_run
):
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 7},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_promoting_back_undoes_the_override(
    editor_client, curated_run, async_session_factory
):
    """The displaced winner sits at index 0, so the same action restores it -
    the property that makes an override reversible without a re-run."""
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 0,
            "expected_formula": "C6H12O6",
        },
    )

    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"
    assert row.fit_score == pytest.approx(0.95)
    assert row.tier == "assigned"
    # Still a curated row: a person decided it, even back to what it was.
    assert row.source == "manual"


# ---------------------------------------------------------------------------
# set_assignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_searched_composition_lands_on_an_unassigned_peak(
    editor_client, curated_run, async_session_factory
):
    """The re-search case: the peak's row is an `unassigned` placeholder with
    no runner-ups, so there is nothing to promote."""
    response = await editor_client.patch(
        _url(curated_run, curated_run["blank_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "ion_formula": "C12H23O11+",
            "fit_score": 0.93,
            "mz_error_ppm": -0.8,
        },
    )

    assert response.status_code == 200
    row = await _row(async_session_factory, curated_run["blank_id"])
    assert row.assigned_formula == "C12H22O11"
    assert row.ionization_mechanism_id == curated_run["mechanism_id"]
    assert row.role == "M0"
    assert row.tier == "assigned"  # 0.93 clears this run's 0.9 band
    assert row.source == "manual"
    assert row.provenance["manual"]["scored_by"] == "composition_search"
    # There was no winner to displace, so nothing was archived as one.
    assert row.provenance["manual"].get("previous") is None


@pytest.mark.asyncio
async def test_a_satellite_is_not_committed_as_a_compounds_main_peak(
    editor_client, curated_run, async_session_factory
):
    """A hit labelled M+1 is a claim about a satellite. Recorded as an M0 it
    would enter the compound's satellite as the compound itself, which every
    consumer that folds a family onto its M0 would then believe."""
    await editor_client.patch(
        _url(curated_run, curated_run["blank_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "isotope_label": "M+1",
            "fit_score": 0.93,
        },
    )

    row = await _row(async_session_factory, curated_run["blank_id"])
    assert row.role == "iso_child"
    assert row.isotope_label == "M+1"
    # No owner is invented for it: the run arbitrated no family for this
    # formula, and an ownerless satellite is ordinary engine output anyway.
    assert row.owner_peak_assignment_id is None


@pytest.mark.asyncio
async def test_an_unknown_mechanism_is_refused_not_flushed(
    editor_client, curated_run, async_session_factory
):
    """It is a foreign key on the row, so an unchecked id would surface as a
    500 from the flush instead of a verdict on the request."""
    response = await editor_client.patch(
        _url(curated_run, curated_run["blank_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": "no-such-mech",
        },
    )

    assert response.status_code == 422
    row = await _row(async_session_factory, curated_run["blank_id"])
    assert row.assigned_formula is None


@pytest.mark.asyncio
async def test_an_unknown_action_is_refused(editor_client, curated_run):
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "delete_everything"},
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# What a curated row looks like to the readers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_curated_row_is_an_ordinary_ledger_row(editor_client, curated_run):
    """It reads back through the ledger like any other row, and the new source
    is filterable - which is also what lets a copied override survive an
    import, since one literal types the read filter and the import row alike."""
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    response = await editor_client.get(
        f"/api/peak-assignments/sample/{curated_run['sample_item_id']}",
        params={"peak_assignment_run_id": curated_run["run_id"], "source": "manual"},
    )

    assert response.status_code == 200
    rows = response.json()["data"]
    # The promoted M0 and the satellite the override demoted.
    assert {row["sample_peak_id"] for row in rows} == {"cur-1", "cur-2"}
    curated = next(row for row in rows if row["sample_peak_id"] == "cur-1")
    assert curated["assigned_formula"] == "C7H16O5"
    # No calibrated probability is flattened onto it, because it carries none.
    assert curated["p_correct"] is None


@pytest.mark.asyncio
async def test_the_detail_endpoint_serves_the_override_record(
    editor_client, curated_run
):
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    response = await editor_client.get(_url(curated_run, curated_run["m0_id"]))

    assert response.status_code == 200
    record = response.json()["data"][0]
    assert record["provenance"]["manual"]["previous_formula"] == "C6H12O6"
    assert record["alternatives"][0]["assigned_formula"] == "C6H12O6"


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_guest_cannot_curate(guest_client, curated_run, async_session_factory):
    """Paired with the editor tests above on the same feature flag, so the 403
    is the role gate rather than the feature gate."""
    response = await guest_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 403
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"


@pytest.mark.asyncio
async def test_curation_is_refused_when_the_feature_is_disabled(
    editor_client, curated_run, feature_disabled, async_session_factory
):
    """The same client and body that succeed above, refused on the flag alone.

    The error body is sanitized to an error_id, so the flag's authorship of
    this 403 is proven by the pairing rather than by its prose.
    """
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 403
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"


@pytest.mark.asyncio
async def test_a_run_still_being_written_cannot_be_curated(
    editor_client, curated_run, async_session_factory
):
    """An edit into a run the engine is still writing would be overwritten by
    the writer, with neither side noticing.

    The same row is curated successfully once the run is marked completed, so
    the 409 is the status gate and nothing else about the request.
    """
    async with async_session_factory() as session:
        run = await session.get(PeakAssignmentRun, curated_run["run_id"])
        run.status = "running"
        await session.commit()

    refused = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    assert refused.status_code == 409

    async with async_session_factory() as session:
        run = await session.get(PeakAssignmentRun, curated_run["run_id"])
        run.status = "completed"
        await session.commit()

    accepted = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_an_assignment_of_another_sample_is_not_found(editor_client, curated_run):
    response = await editor_client.patch(
        _url(curated_run, "no-such-assignment-id"),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 404
