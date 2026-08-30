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
#: inherited, nor 'assigned' under the default bands. The mechanism is filled in
#: per test from the fixture's own, since it is a foreign key.
ALTERNATIVE = {
    "assigned_formula": "C7H16O5",
    "ion_formula": "C7H17O5+",
    "isotope_label": "M0",
    "fit_score": 0.62,
    "mz_error_ppm": 4.2,
    "plausibility": 0.5,
    "source": "database",
}


def _alternative(mechanism_id: str, **overrides) -> dict:
    """The stock runner-up, under a real mechanism, with fields overridden."""
    return {**ALTERNATIVE, "ionization_mechanism_id": mechanism_id, **overrides}


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
                alternatives=[_alternative(mechanism_id)],
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
async def test_the_promoted_formulas_known_identity_comes_with_it(
    editor_client, curated_run, async_session_factory
):
    """`reference_identities` is the one provenance key that DOES cross over.

    It is not a judgement about the winner that was displaced - it names the
    formula the row now holds, so dropping it would leave a known compound
    unnamed in the inspector for no reason.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.alternatives = [
            _alternative(
                curated_run["mechanism_id"],
                reference_identities=[{"name": "Xylitol pentanoate", "source": "test"}],
            )
        ]
        await session.commit()

    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.provenance["reference_identities"][0]["name"] == "Xylitol pentanoate"


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


@pytest.mark.asyncio
async def test_the_promoted_candidates_adduct_comes_with_it(
    editor_client, curated_run, async_session_factory
):
    """A formula is half an assignment.

    Without the mechanism the row would land adductless, and a verification's
    identity - peak + formula + mechanism - would be incomplete. This is what
    the engine change recording ``ionization_mechanism_id`` on alternatives is
    for.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.ionization_mechanism_id == curated_run["mechanism_id"]


@pytest.mark.asyncio
async def test_committing_the_formula_the_row_already_carries_keeps_the_family(
    editor_client, curated_run, async_session_factory
):
    """Demotion is about a compound being REPLACED, not about a write happening.

    A candidate entry naming the formula and adduct the row already carries
    leaves the family standing for exactly what it stood for before, so
    stripping it would destroy correct rows to record a change that did not
    happen.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.alternatives = [
            _alternative(curated_run["mechanism_id"], assigned_formula="C6H12O6")
        ]
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 200
    assert response.json()["results"] == 1  # the curated row alone
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula == "C6H12O6"
    assert child.role == "iso_child"
    assert child.owner_peak_assignment_id == curated_run["m0_id"]


@pytest.mark.asyncio
async def test_a_satellite_row_can_be_curated_on_its_own(
    editor_client, curated_run, async_session_factory
):
    """Curation is about the row in hand, not the family M0 a verdict is
    redirected to - an alternative index only means something against one row's
    list. The satellite detaches from a family whose compound it no longer
    shares, and the M0 it left is untouched.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["child_id"])
        row.alternatives = [_alternative(curated_run["mechanism_id"])]
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["child_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 200
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula == "C7H16O5"
    assert child.owner_peak_assignment_id is None
    assert child.role == "M0"
    m0 = await _row(async_session_factory, curated_run["m0_id"])
    assert m0.assigned_formula == "C6H12O6"
    assert m0.source == "database"


@pytest.mark.asyncio
async def test_a_searched_composition_can_replace_an_existing_assignment(
    editor_client, curated_run, async_session_factory
):
    """``set_assignment`` is not only for blank peaks: the displaced winner is
    archived exactly as a promotion archives it.
    """
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "fit_score": 0.93,
        },
    )

    assert response.status_code == 200
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C12H22O11"
    assert row.alternatives[0]["assigned_formula"] == "C6H12O6"
    assert row.provenance["manual"]["previous_formula"] == "C6H12O6"
    # And the satellites of the formula it replaced are gone, as for a promote.
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.role == "unassigned"


@pytest.mark.asyncio
async def test_a_malformed_stored_candidate_is_refused_not_flushed(
    editor_client, curated_run, async_session_factory
):
    """``alternatives`` is a bare JSON list that nothing validates on the way
    in, and an imported run's entries are whatever the publishing client sent.
    A candidate that cannot go in the columns has to be a 422 naming the field,
    not a class-22 data error surfacing as a 500.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.alternatives = [
            _alternative(curated_run["mechanism_id"], assigned_formula="C" * 300),
            _alternative(curated_run["mechanism_id"], fit_score=7.5),
            _alternative(curated_run["mechanism_id"], mz_error_ppm="not a number"),
        ]
        await session.commit()

    for index in (0, 1, 2):
        response = await editor_client.patch(
            _url(curated_run, curated_run["m0_id"]),
            json={"action": "promote_alternative", "alternative_index": index},
        )
        assert response.status_code == 422, f"alternative {index}"

    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"


@pytest.mark.asyncio
async def test_an_adduct_of_the_wrong_polarity_is_refused(
    editor_client, curated_run, async_session_factory
):
    """The rule the import path enforces on the same column: a mechanism of the
    opposite polarity is not an adduct this measurement could have produced.
    The in-app engine satisfies it structurally by only searching the sample's
    own mechanisms, so a hand-supplied id is the one way in.
    """
    opposite_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=opposite_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"-H- ({opposite_id})",
            )
        )
        await session.commit()

    try:
        response = await editor_client.patch(
            _url(curated_run, curated_run["blank_id"]),
            json={
                "action": "set_assignment",
                "assigned_formula": "C12H22O11",
                "ionization_mechanism_id": opposite_id,
            },
        )

        assert response.status_code == 422
        row = await _row(async_session_factory, curated_run["blank_id"])
        assert row.assigned_formula is None
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism_id == opposite_id
                )
            )
            await session.commit()


# ---------------------------------------------------------------------------
# The verification layer is left alone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curating_writes_no_verification(editor_client, curated_run):
    """Choosing a candidate and vouching for one are different acts.

    A ``confirmed`` verdict requires an evidence level by design, which an
    automatic one would have to fabricate, and verdicts are the confidence
    calibration's training labels - so an override must not manufacture one.
    """
    before = await editor_client.get(
        f"/api/peak-assignments/sample/{curated_run['sample_item_id']}/verifications"
    )

    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    after = await editor_client.get(
        f"/api/peak-assignments/sample/{curated_run['sample_item_id']}/verifications"
    )
    assert after.json()["results"] == before.json()["results"]


@pytest.mark.asyncio
async def test_an_existing_verdict_stays_with_the_formula_it_judged(
    editor_client, curated_run
):
    """A verdict's identity is peak + formula + mechanism, so an override
    CHANGES the identity rather than re-pointing the verdict.

    The old verdict is neither destroyed nor inherited: it stays in the
    append-only history attached to the formula a person actually judged, and
    the row reads unverified until someone judges the new one. This is why
    nothing here has to touch the verification table at all.
    """
    verdict = await editor_client.post(
        f"/api/peak-assignments/sample/{curated_run['sample_item_id']}/verify",
        json={
            "peak_assignment_id": curated_run["m0_id"],
            "verdict": "confirmed",
            "evidence_level": "msms",
        },
    )
    assert verdict.status_code == 201

    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    verifications = await editor_client.get(
        f"/api/peak-assignments/sample/{curated_run['sample_item_id']}/verifications"
    )
    records = [
        record
        for record in verifications.json()["data"]
        if record["sample_peak_id"] == "cur-1"
    ]
    assert len(records) == 1
    # Still against C6H12O6 - the formula it was recorded on, not the new one.
    assert records[0]["assigned_formula"] == "C6H12O6"
    assert records[0]["verdict"] == "confirmed"
