"""
Integration tests for manual curation of an assignment (the PATCH write path).

Two actions share one endpoint: promoting one of a row's stored runner-ups, and
committing a composition the caller names (the re-search case). Both edit the
row in place and mark it as human-made.

Every test here MUTATES assignment rows, so none of them may touch the
session-scoped ``pa_test_data`` ledger: ``curated_run`` seeds a run of its own
per test and deletes it afterwards.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.new.peak_assignments.config import MAX_ALTERNATIVES_CEILING
from mascope_backend.db import (
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    TargetCompound,
    TargetIon,
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
                # A satellite carries provenance of its own, and no column
                # holds it - so a restore that only put the columns back would
                # lose it. `score_version` is the one key here that no archived
                # winner snapshot repeats, which makes it the tell.
                provenance={
                    "plausibility": 0.9,
                    "evidence": 0.79,
                    "score_version": "v2-test",
                },
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
async def test_undoing_an_override_brings_the_known_identity_back_too(
    editor_client, curated_run, async_session_factory
):
    """The same key, in the other direction - the one nothing else covers.

    The test above proves an ENGINE alternative's identities cross over, because
    the engine writes them at the top level of the entry. The displaced winner's
    snapshot is written here instead, and `reference_identities` is one of the
    keys buried inside `previous.engine_judgement` - which nothing reads. So an
    undo would put the formula back and silently strip the compound's name off a
    row that had carried it all along, with only a re-run to restore it. The
    snapshot repeats the key at the top level for exactly this.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.provenance = {
            **row.provenance,
            "reference_identities": [{"name": "D-Glucose", "source": "test"}],
        }
        await session.commit()

    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    overridden = await _row(async_session_factory, curated_run["m0_id"])
    # While the override stands the row is nameless, and rightly so: the
    # promoted formula is a different compound and carries no identities.
    assert "reference_identities" not in overridden.provenance
    # They went with the winner they name, where the undo can find them.
    assert overridden.alternatives[0]["reference_identities"][0]["name"] == "D-Glucose"

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
    assert row.provenance["reference_identities"][0]["name"] == "D-Glucose"


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
async def test_a_stored_candidate_that_names_no_formula_is_refused(
    editor_client, curated_run, async_session_factory
):
    """A candidate with no formula in it is not a choice anyone can commit.

    The shape is real: the database stage builds a runner-up out of a target
    row, and that row's compound formula can be absent, while an imported run's
    alternatives are whatever the publishing client sent. Promoted as they
    stand they would blank the row's assignment and demote its family - an
    assignment destroyed by a click labelled "use this" - and mark the wreckage
    'manual', so nothing about the ledger would say it was an accident.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.alternatives = [
            {
                "ion_formula": "C7H17O5+",
                "ionization_mechanism_id": curated_run["mechanism_id"],
                "fit_score": 0.62,
            },
            _alternative(curated_run["mechanism_id"], assigned_formula=""),
        ]
        await session.commit()

    for index in (0, 1):
        response = await editor_client.patch(
            _url(curated_run, curated_run["m0_id"]),
            json={"action": "promote_alternative", "alternative_index": index},
        )
        assert response.status_code == 422, f"alternative {index}"

    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"
    assert row.source == "database"
    # And the family of the formula that still stands is still standing.
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.owner_peak_assignment_id == curated_run["m0_id"]


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
    # And the whole family is back with it, not just the M0 - an undo that
    # left the satellites unassigned would be half an undo.
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula == "C6H12O6"
    assert child.owner_peak_assignment_id == curated_run["m0_id"]


# ---------------------------------------------------------------------------
# The demoted family, and putting it back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_demoted_satellites_are_archived_on_their_owner(
    editor_client, curated_run, async_session_factory
):
    """Demoting a family is only half of what an override owes it.

    The satellites are stripped from the ledger's point of view, so the record
    of what they were has to live somewhere a later edit can find it by a
    primary-key read - which is the M0's own provenance, keyed by the compound
    they were satellites of.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    row = await _row(async_session_factory, curated_run["m0_id"])
    manual = row.provenance["manual"]
    assert len(manual["demoted"]) == 1
    entry = manual["demoted"][0]
    assert entry["peak_assignment_id"] == curated_run["child_id"]
    assert entry["sample_peak_id"] == "cur-2"
    # Keyed by the COMPOUND, which is a formula under an adduct: that pair is
    # what a later edit has to commit for this family to come back.
    assert entry["owner_formula"] == "C6H12O6"
    assert entry["owner_ionization_mechanism_id"] == curated_run["mechanism_id"]
    # One act, one instant. The satellite's own record carries the same
    # timestamp, which is how a restore tells an untouched demotion from a row
    # someone has curated since.
    assert entry["at"] == manual["at"]
    assert entry["previous"]["assigned_formula"] == "C6H12O6"
    assert entry["previous"]["isotope_label"] == "M+1"
    assert entry["previous"]["role"] == "iso_child"
    assert entry["previous"]["tier"] == "assigned"
    assert entry["previous"]["fit_score"] == pytest.approx(0.88)
    # The provenance blob too - it holds numbers no column does.
    assert entry["provenance"]["score_version"] == "v2-test"


@pytest.mark.asyncio
async def test_promoting_the_previous_compound_back_restores_the_family(
    editor_client, curated_run, async_session_factory
):
    """The undo the inspector promises, in full.

    Restoring only the M0 would leave the family as orphaned unassigned peaks:
    the isotopologue block would vanish from the inspector and the tier counts
    would carry two extra unassigned rows, with only a re-assignment of the
    whole sample to fix it.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    stripped = await _row(async_session_factory, curated_run["child_id"])
    assert stripped.role == "unassigned"  # it really was demoted first

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 0,
            "expected_formula": "C6H12O6",
        },
    )

    assert response.status_code == 200
    assert {row["sample_peak_id"] for row in response.json()["data"]} == {
        "cur-1",
        "cur-2",
    }
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula == "C6H12O6"
    assert child.ion_formula == "C6H13O6+"
    assert child.ionization_mechanism_id == curated_run["mechanism_id"]
    assert child.isotope_label == "M+1"
    assert child.role == "iso_child"
    assert child.fit_score == pytest.approx(0.88)
    assert child.owner_peak_assignment_id == curated_run["m0_id"]
    # The tier it had, not one re-judged: 0.88 sits below this run's 0.9 band,
    # so a recompute would land 'candidate' and lose what the run decided.
    assert child.tier == "assigned"
    # And it reads as the engine's row again rather than a person's edit: the
    # demotion's manual block goes, the provenance it had comes back.
    assert child.source == "database"
    assert child.provenance == {
        "plausibility": 0.9,
        "evidence": 0.79,
        "score_version": "v2-test",
    }
    # The snapshot the demotion pushed onto its alternatives is popped back
    # off, so a demote/restore round trip does not grow the list every time.
    assert child.alternatives is None
    # The owner stops offering a restore that has already happened, and says
    # what it did.
    m0 = await _row(async_session_factory, curated_run["m0_id"])
    assert "demoted" not in m0.provenance["manual"]
    assert m0.provenance["manual"]["restored"] == [curated_run["child_id"]]


@pytest.mark.asyncio
async def test_a_satellite_curated_by_hand_is_not_restored_over(
    editor_client, curated_run, async_session_factory
):
    """A person's judgement on the satellite is newer than the undo.

    Putting the engine's older row back over it would destroy a deliberate act
    in order to reverse an accidental one, so the row is left exactly as its
    curator left it and only reported as skipped.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    claimed = await editor_client.patch(
        _url(curated_run, curated_run["child_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C3H8O3",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "fit_score": 0.95,
        },
    )
    assert claimed.status_code == 200

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 0,
            "expected_formula": "C6H12O6",
        },
    )

    assert response.status_code == 200
    assert response.json()["results"] == 1  # the M0 alone; nothing came back
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula == "C3H8O3"
    assert child.source == "manual"
    assert child.owner_peak_assignment_id is None
    m0 = await _row(async_session_factory, curated_run["m0_id"])
    assert m0.provenance["manual"]["restore_skipped"] == [curated_run["child_id"]]
    assert "restored" not in m0.provenance["manual"]
    # Consumed either way: the row belongs to whoever claimed it now, so no
    # later edit gets a second attempt at overwriting them.
    assert "demoted" not in m0.provenance["manual"]


@pytest.mark.asyncio
async def test_a_satellite_whose_row_is_gone_is_reported_not_passed_over(
    editor_client, curated_run, async_session_factory
):
    """An undo that cannot reach a satellite has to say so.

    The archive names rows by id out of a JSON blob, and the row can be gone by
    the time the undo runs - deleted, or (in an imported run's provenance)
    never this run's to write in the first place. Passed over in silence the
    response would report a successful undo while the satellite stayed demoted,
    with nothing anywhere recording that anything was missed, and the archive
    entry gone too - a satellite permanently unrestorable and no trace of it.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    async with async_session_factory() as session:
        # Demoted, so nothing points at it any more and it deletes cleanly.
        await session.execute(
            delete(PeakAssignment).where(
                PeakAssignment.peak_assignment_id == curated_run["child_id"]
            )
        )
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 0,
            "expected_formula": "C6H12O6",
        },
    )

    assert response.status_code == 200
    assert "could not be put back" in response.json()["message"]
    m0 = await _row(async_session_factory, curated_run["m0_id"])
    manual = m0.provenance["manual"]
    assert manual["restore_failed"] == [curated_run["child_id"]]
    # Not a skip: that word means a row somebody else now owns was deliberately
    # left alone, which is the opposite of what happened here.
    assert "restore_skipped" not in manual
    assert "restored" not in manual
    # And the entry is consumed: no id ever names that row again, so keeping it
    # would hold an archive slot to offer an undo that can only fail.
    assert "demoted" not in manual


@pytest.mark.asyncio
async def test_an_archive_entry_that_cannot_be_committed_is_reported_and_kept(
    editor_client, curated_run, async_session_factory
):
    """The other unreachable case, which deserves the other answer.

    ``provenance`` is JSON an import may have written, so an archived state can
    be unusable - a formula longer than its column, a fit score outside it.
    Refusing the whole curation over it would be the wrong verdict, since the
    request is fine, but restoring nothing and saying nothing is how a satellite
    goes missing quietly. Unlike a deleted row this one is still standing, so
    the entry stays archived: repair the provenance and the undo works again.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    async with async_session_factory() as session:
        m0 = await session.get(PeakAssignment, curated_run["m0_id"])
        provenance = deepcopy(m0.provenance)
        # Past the 256 the column holds. The demotion's own timestamp is left
        # alone, so the entry still passes the "nobody has curated this since"
        # gate and fails on the state itself.
        provenance["manual"]["demoted"][0]["previous"]["assigned_formula"] = "C" * 300
        m0.provenance = provenance
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 0,
            "expected_formula": "C6H12O6",
        },
    )

    assert response.status_code == 200
    assert "could not be put back" in response.json()["message"]
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula is None  # left exactly as it was
    m0 = await _row(async_session_factory, curated_run["m0_id"])
    manual = m0.provenance["manual"]
    assert manual["restore_failed"] == [curated_run["child_id"]]
    # Kept, not consumed: the row it describes is still there to be restored.
    assert manual["demoted"][0]["peak_assignment_id"] == curated_run["child_id"]


@pytest.mark.asyncio
async def test_a_restore_only_fires_for_the_compound_it_was_archived_under(
    editor_client, curated_run, async_session_factory
):
    """A satellite belongs to a compound, not to a row.

    Committing some third formula is not the compound coming back, so the
    family stays demoted - and the archive rides along to the edit that does
    put that compound back, rather than being lost because the row was edited
    twice in between.
    """
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "fit_score": 0.93,
        },
    )
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C9H20O2",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "fit_score": 0.7,
        },
    )

    assert response.json()["results"] == 1  # nothing demoted, nothing restored
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula is None
    assert child.owner_peak_assignment_id is None
    m0 = await _row(async_session_factory, curated_run["m0_id"])
    assert m0.provenance["manual"]["demoted"][0]["owner_formula"] == "C6H12O6"

    # Its own compound, two edits later, still restores it: the alternatives
    # now read [C12H22O11, C6H12O6, C7H16O5], newest displacement first.
    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "promote_alternative",
            "alternative_index": 1,
            "expected_formula": "C6H12O6",
        },
    )

    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula == "C6H12O6"
    assert child.owner_peak_assignment_id == curated_run["m0_id"]


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
# The run's own yardstick, and the ceiling on its lists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_without_tier_bands_falls_back_to_its_configs_thresholds(
    editor_client, curated_run, async_session_factory
):
    """``tier_bands`` is a later column, so a run computed before it recorded
    its thresholds only in the config blob.

    Without this fallback such a run's curated rows would be tiered by the
    engine defaults while every other row of the same ledger was tiered by the
    config's bands, and one row's 'assigned' would mean something different
    from its neighbour's.
    """
    async with async_session_factory() as session:
        run = await session.get(PeakAssignmentRun, curated_run["run_id"])
        run.tier_bands = None
        run.config = {"assigned_threshold": 0.6, "candidate_threshold": 0.3}
        await session.commit()

    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    # The candidate's 0.62 clears the config's 0.6 band and clears neither the
    # run's own 0.9 nor the engine's default 0.8, so 'assigned' can only have
    # come from the config.
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.tier == "assigned"


@pytest.mark.asyncio
async def test_the_configs_pre_rename_upper_threshold_is_read_too(
    editor_client, curated_run, async_session_factory
):
    """A run configured before the tier rename names its upper band
    ``identified_threshold``.

    ``normalize_tier_bands`` does not reach it - that renames tier KEYS in the
    bands column, not a config field that carries the same band under another
    name - so without the second spelling here a pre-rename run would silently
    drop to the engine defaults for exactly the rows a person curated.
    """
    async with async_session_factory() as session:
        run = await session.get(PeakAssignmentRun, curated_run["run_id"])
        run.tier_bands = None
        run.config = {"identified_threshold": 0.6, "candidate_threshold": 0.3}
        await session.commit()

    await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.tier == "assigned"


@pytest.mark.asyncio
async def test_a_run_that_records_no_bands_at_all_uses_the_engine_defaults(
    editor_client, curated_run, async_session_factory
):
    """The oldest runs record their thresholds nowhere at all.

    Something still has to tier a row curated into one, and the engine defaults
    are what that run was itself computed under. The alternative is a float()
    of None out of the band lookup, which would make the rows of every such run
    uncurateable with a 500.
    """
    async with async_session_factory() as session:
        run = await session.get(PeakAssignmentRun, curated_run["run_id"])
        run.tier_bands = None
        run.config = None
        await session.commit()

    await editor_client.patch(
        _url(curated_run, curated_run["blank_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "fit_score": 0.85,
        },
    )

    # 0.85 clears the default 0.8 band but not the run's own 0.9, so 'assigned'
    # here says the defaults were reached and RUN_BANDS was not.
    row = await _row(async_session_factory, curated_run["blank_id"])
    assert row.tier == "assigned"


@pytest.mark.asyncio
async def test_the_alternatives_list_is_truncated_at_its_ceiling(
    editor_client, curated_run, async_session_factory
):
    """Every override pushes the winner it displaced onto the head of the list.

    The list is a JSON blob on the highest-volume table, so without the ceiling
    a row curated back and forth would grow it by a candidate each time, and an
    imported row can arrive at the ceiling already with no override at all.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.alternatives = [
            _alternative(
                curated_run["mechanism_id"], assigned_formula=f"C{n}H{2 * n}O2"
            )
            for n in range(1, MAX_ALTERNATIVES_CEILING + 1)
        ]
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "fit_score": 0.7,
        },
    )

    assert response.status_code == 200
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert len(row.alternatives) == MAX_ALTERNATIVES_CEILING
    formulas = [alternative["assigned_formula"] for alternative in row.alternatives]
    # The displaced winner takes the head and the loss comes off the tail, so
    # what falls out is the weakest runner-up rather than the formula a person
    # just replaced - which is the entry the undo needs.
    last = MAX_ALTERNATIVES_CEILING
    assert formulas[0] == "C6H12O6"
    assert f"C{last}H{2 * last}O2" not in formulas
    assert f"C{last - 1}H{2 * (last - 1)}O2" in formulas


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
async def test_a_candidate_that_names_no_adduct_cannot_be_committed(
    editor_client, curated_run, async_session_factory
):
    """The untargeted stage's `other_candidates` shortlist is formula names and
    a plausibility and nothing else.

    Committed as they stand they would give a row with a formula and no
    mechanism, which cannot carry a verification identity - the very thing
    ``SetAssignmentBody`` makes the mechanism mandatory to prevent. Refusing
    here is what makes the two actions agree; the way to commit such a formula
    is the re-search hand button, which supplies an adduct.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.alternatives = [
            {
                "assigned_formula": "C9H12O3",
                "plausibility": 0.71,
                "source": "untargeted",
            }
        ]
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 422
    # Refused before anything was written: the old winner still stands, adduct
    # and all, and its satellite was not demoted on the way to the refusal.
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"
    assert row.ionization_mechanism_id == curated_run["mechanism_id"]
    assert row.source == "database"
    assert row.tier == "assigned"
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula == "C6H12O6"
    assert child.owner_peak_assignment_id == curated_run["m0_id"]


@pytest.mark.asyncio
async def test_a_candidates_target_ion_supplies_the_adduct_it_omits(
    editor_client, curated_run, async_session_factory
):
    """The documented fallback, which is how an alternative written before
    alternatives carried ``ionization_mechanism_id`` still promotes to a
    complete assignment: a database candidate names the target ion it was
    scored against, and that ion knows its own mechanism.
    """
    compound_id = gen_id()
    ion_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCompound(
                target_compound_id=compound_id,
                target_compound_name="Curation fallback compound",
                target_compound_formula="C7H16O5",
            )
        )
        session.add(
            TargetIon(
                target_ion_id=ion_id,
                target_compound_id=compound_id,
                ionization_mechanism_id=curated_run["mechanism_id"],
                target_ion_formula="C7H17O5+",
            )
        )
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        # No `ionization_mechanism_id` at all - the pre-mechanism shape.
        row.alternatives = [
            {**ALTERNATIVE, "target_ion_id": ion_id},
        ]
        await session.commit()

    try:
        response = await editor_client.patch(
            _url(curated_run, curated_run["m0_id"]),
            json={"action": "promote_alternative", "alternative_index": 0},
        )

        assert response.status_code == 200
        row = await _row(async_session_factory, curated_run["m0_id"])
        assert row.assigned_formula == "C7H16O5"
        assert row.ionization_mechanism_id == curated_run["mechanism_id"]
        assert row.target_ion_id == ion_id
    finally:
        async with async_session_factory() as session:
            # Cascades to the ion; the assignment's links are ON DELETE SET NULL.
            await session.execute(
                delete(TargetCompound).where(
                    TargetCompound.target_compound_id == compound_id
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_a_candidate_whose_target_ion_is_gone_is_told_which_it_is(
    editor_client, curated_run, async_session_factory
):
    """A refusal has to name the thing that is actually wrong.

    The inspector offers "use this" on a candidate that names a target ion
    without knowing whether the ion still exists, so this click is reachable.
    Answering it with the adductless refusal - "that candidate names no adduct,
    search this peak's composition" - would tell a person their candidate never
    had one, when it had one perfectly well until the target library moved on.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        # Never inserted: the shape of a target ion deleted since the run, and
        # no mechanism of its own, so the dead ion is the only route to one.
        row.alternatives = [{**ALTERNATIVE, "target_ion_id": gen_id()}]
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 422
    assert "no longer exists" in response.json()["error"]
    assert "names no adduct" not in response.json()["error"]
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C6H12O6"


@pytest.mark.asyncio
async def test_a_candidates_dead_target_links_are_dropped_not_flushed(
    editor_client, curated_run, async_session_factory
):
    """The target ids in a candidate were written when the run was computed,
    and the target library has moved on since.

    They are foreign keys on the assignment, so committing an id whose row has
    been deleted raises out of the flush - a 500 on a request that is otherwise
    perfectly good, and one nobody can fix except by editing the target
    library. Dropping them keeps the override a verdict on the chemistry rather
    than on the library's history.
    """
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        row.alternatives = [
            _alternative(
                curated_run["mechanism_id"],
                # Never inserted: the shape of a target deleted since the run.
                target_compound_id=gen_id(),
                target_ion_id=gen_id(),
            )
        ]
        await session.commit()

    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={"action": "promote_alternative", "alternative_index": 0},
    )

    assert response.status_code == 200
    row = await _row(async_session_factory, curated_run["m0_id"])
    assert row.assigned_formula == "C7H16O5"
    assert row.target_compound_id is None
    assert row.target_ion_id is None
    # The adduct is unaffected: it is the candidate's own, and only the
    # mechanism fallback would have had to read the dead ion.
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
async def test_the_same_formula_under_another_adduct_still_demotes_the_family(
    editor_client, curated_run, async_session_factory
):
    """The other half of the rule the test above pins, and the half the
    (formula, mechanism) comparison was written for.

    A family belongs to a COMPOUND, and a compound is a formula under an adduct:
    the satellites of C6H12O6 as a protonated ion are not the satellites of the
    same formula sodiated - a different ion at a different m/z, with different
    peaks. Comparing formulas alone would leave the old family standing under an
    adduct their M0 no longer carries, and the ledger would show an isotopologue
    block that no longer belongs to anything.
    """
    other_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=other_id,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"+Na+ ({other_id})",
            )
        )
        await session.commit()

    try:
        response = await editor_client.patch(
            _url(curated_run, curated_run["m0_id"]),
            json={
                "action": "set_assignment",
                # The formula the row already carries, under a different adduct.
                "assigned_formula": "C6H12O6",
                "ionization_mechanism_id": other_id,
                "fit_score": 0.93,
            },
        )

        assert response.status_code == 200
        assert response.json()["results"] == 2  # the row and its ex-satellite
        child = await _row(async_session_factory, curated_run["child_id"])
        assert child.assigned_formula is None
        assert child.role == "unassigned"
        assert child.owner_peak_assignment_id is None
        # Archived under the adduct they belonged to, so it is that adduct
        # coming back that brings them home - not the formula on its own.
        m0 = await _row(async_session_factory, curated_run["m0_id"])
        entry = m0.provenance["manual"]["demoted"][0]
        assert entry["owner_formula"] == "C6H12O6"
        assert entry["owner_ionization_mechanism_id"] == curated_run["mechanism_id"]
    finally:
        async with async_session_factory() as session:
            # The row's link is ON DELETE SET NULL, and the run is dropped by
            # the fixture's own teardown straight after.
            await session.execute(
                delete(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism_id == other_id
                )
            )
            await session.commit()


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
async def test_a_searched_composition_archives_the_winner_it_displaced(
    editor_client, curated_run, async_session_factory
):
    """The displaced-winner path in full, on the action that does not read the
    row for its candidate.

    ``set_assignment`` builds its winner from the request body, so it reaches
    the archive down a different branch than ``promote_alternative`` - which is
    where the snapshot, the calibrated-field archive and the family demotion
    are otherwise pinned. A regression that overwrote the row in place on this
    branch, keeping the engine's P(correct) beside a formula the caller typed
    and leaving the old family assigned, would pass every one of those.
    """
    response = await editor_client.patch(
        _url(curated_run, curated_run["m0_id"]),
        json={
            "action": "set_assignment",
            "assigned_formula": "C12H22O11",
            "ionization_mechanism_id": curated_run["mechanism_id"],
            "fit_score": 0.7,
        },
    )

    assert response.status_code == 200
    assert response.json()["results"] == 2  # the curated row and its satellite

    row = await _row(async_session_factory, curated_run["m0_id"])
    # The whole winner, not just its formula: promoting this entry back has to
    # restore the adduct and the numbers too, or the undo is a different row.
    displaced = row.alternatives[0]
    assert displaced["assigned_formula"] == "C6H12O6"
    assert displaced["ion_formula"] == "C6H13O6+"
    assert displaced["ionization_mechanism_id"] == curated_run["mechanism_id"]
    assert displaced["fit_score"] == pytest.approx(0.95)
    assert displaced["tier"] == "assigned"
    assert displaced["source"] == "database"
    # The engine's reading of that winner travels with it and not with the row:
    # stated beside a formula a person typed, P(correct) would be a calibrated
    # probability for a candidate nothing calibrated.
    assert displaced["engine_judgement"]["p_correct"] == pytest.approx(0.93)
    for key in ("p_correct", "calibrated", "calibration", "corroboration"):
        assert key not in row.provenance
    archived = row.provenance["manual"]["previous"]["engine_judgement"]
    assert archived["p_correct"] == pytest.approx(0.93)
    assert archived["corroboration"]["n_adducts"] == 2

    # The old formula's family goes with it, archived under the compound it
    # was a family of so that committing that compound back brings it home.
    entry = row.provenance["manual"]["demoted"][0]
    assert entry["peak_assignment_id"] == curated_run["child_id"]
    assert entry["owner_formula"] == "C6H12O6"
    assert entry["owner_ionization_mechanism_id"] == curated_run["mechanism_id"]
    child = await _row(async_session_factory, curated_run["child_id"])
    assert child.assigned_formula is None
    assert child.owner_peak_assignment_id is None


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


@pytest.mark.asyncio
async def test_an_adduct_reached_through_a_target_ion_is_polarity_checked_too(
    editor_client, curated_run, async_session_factory
):
    """The fallback is the untested half of the rule, and the reachable half.

    A hand-supplied id is not the only way an opposite-polarity adduct gets in:
    ``alternatives`` is untyped JSON that nothing validates on the way in, an
    imported run's entries are whatever the publishing client sent, and a target
    compound ordinarily carries ions in both polarities - so a candidate
    pointing at a negative-mode ion is an ordinary route for an adduct this
    measurement could not have produced. Checking only the id a caller states
    leaves that door open while the docstring says it is shut.
    """
    opposite_id = gen_id()
    compound_id = gen_id()
    ion_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=opposite_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"-H- ({opposite_id})",
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=compound_id,
                target_compound_name="Opposite polarity compound",
                target_compound_formula="C7H16O5",
            )
        )
        session.add(
            TargetIon(
                target_ion_id=ion_id,
                target_compound_id=compound_id,
                ionization_mechanism_id=opposite_id,
                target_ion_formula="C7H15O5-",
            )
        )
        row = await session.get(PeakAssignment, curated_run["m0_id"])
        # No mechanism of its own, so the ion's is the one that would land.
        row.alternatives = [{**ALTERNATIVE, "target_ion_id": ion_id}]
        await session.commit()

    try:
        response = await editor_client.patch(
            _url(curated_run, curated_run["m0_id"]),
            json={"action": "promote_alternative", "alternative_index": 0},
        )

        assert response.status_code == 422
        # The polarity refusal specifically, not the adductless one: the
        # fallback did resolve a mechanism, and that mechanism is the problem.
        detail = response.json()["detail"]
        assert detail["mechanism_polarity"] == "-"
        assert detail["sample_polarity"] != "-"
        # Refused before anything was written, the family included.
        row = await _row(async_session_factory, curated_run["m0_id"])
        assert row.assigned_formula == "C6H12O6"
        assert row.ionization_mechanism_id == curated_run["mechanism_id"]
        assert row.source == "database"
        child = await _row(async_session_factory, curated_run["child_id"])
        assert child.owner_peak_assignment_id == curated_run["m0_id"]
    finally:
        async with async_session_factory() as session:
            # The compound cascades to its ion, which frees the mechanism.
            await session.execute(
                delete(TargetCompound).where(
                    TargetCompound.target_compound_id == compound_id
                )
            )
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
