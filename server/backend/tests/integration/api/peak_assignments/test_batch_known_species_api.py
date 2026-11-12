"""Integration tests for the batch's known-species decision (step 4.2h).

Seeds a batch of six samples folded into the ledger, two from in-app runs of
their own (S00, S02), one from an imported run (S01) and three from rows no
run holds. The urea monomer is claimed in every sample; the urea trimer is
claimed in one sample only (S02) and read as an analyte, with its 13C line,
or left unread in four others; a hydronium hydrate is claimed in one sample
alone; a listed siloxane is committed from its list in two samples, read as
another compound in two more (one of them by hand, one with an isotope line
of its own) and left unread in one. The verdict is the library's
(``test_known_species.py``) and the plan the pure tests'
(``unit/.../test_batch_known_species.py``); what is proven here is the
batch-level contract: the species claimed and recorded, the members and the
in-app runs' rows rewritten and the import's left, the lines going with a
library ion, the holds recorded with each file's reading, the consensus
keeping the record through a recompute and a re-fold, a new decision
replacing every record, the ledger and an earlier run's snapshot serving it,
a sample's next run taking the batch's word, and the route running it as a
batch run. See ``batch_known_species.py``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments import (
    batch_known_species,
    batch_peaks_routes,
)
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    batch_species_for,
    run_batch_known_species,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    candidate_index,
    role_code,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_KNOWN_SPECIES,
    complete_run,
    start_run,
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
    TargetCompound,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.reagents import ion_mz


pytestmark = pytest.mark.asyncio

#: A mechanism no other suite names: the mechanism table's notations are
#: unique, and the integration database is shared by the whole run.
MECH, NOTATION = "mech-known-h", "[M+H]+ (known species test)"

N_SAMPLES = 6
#: The samples folded from in-app runs of their own, and the one folded from
#: an imported run.
RUN_SAMPLES, IMPORT_SAMPLE = ("S00", "S02"), "S01"

MONOMER, TRIMER, HYDRATE = "CH5N2O+", "C3H13N6O3+", "H5O2+"
D4, D4_ION, OTHER = "C8H24O4Si4", "C8H25O4Si4+", "C9H24N2O4Si3"
IDENTITIES = [
    {
        "name": "octamethylcyclotetrasiloxane",
        "source": "cyclic siloxanes",
        "xrefs": {"tags": ["background"]},
    }
]

#: Peak id -> its m/z.
MZ = {
    "m": 61.03964,
    "t": 181.10437,
    "t13": 182.10773,
    "h": 37.02841,
    "d": 297.08246,
    "d13": 298.08582,
    "a": 215.12779,
}


def _reagent(ion, label):
    return dict(
        role="reagent",
        assigned_formula=None,
        ion_formula=ion,
        ionization_mechanism_id=None,
        source="reagent",
        tier="unassigned",
        fit_score=None,
        provenance={"reagent": {"ion": label, "kind": "cluster", "family": "reagent"}},
    )


def _analyte(formula, ion, *, tier="assigned", fit=0.9, source="untargeted", **extra):
    return {
        "role": "M0",
        "assigned_formula": formula,
        "ion_formula": ion,
        "ionization_mechanism_id": MECH,
        "source": source,
        "tier": tier,
        "fit_score": fit,
        "provenance": {"evidence": 0.8},
        **extra,
    }


UNREAD = dict(
    role="unassigned",
    assigned_formula=None,
    ion_formula=None,
    ionization_mechanism_id=None,
    source=None,
    tier="unassigned",
    fit_score=None,
    provenance=None,
)


def _readings(name):
    """Peak id -> what the sample's run read it as; a peak absent is not in
    the sample."""
    listed = _analyte(
        D4, D4_ION, source="database", provenance={"reference_identities": IDENTITIES}
    )
    readings = {
        "m": _reagent(MONOMER, "[CH4N2O+H]+"),
        "a": _analyte("C10H14O4", "C10H15O4+"),
    }
    readings["t"] = {
        "S02": _reagent(TRIMER, "[(CH4N2O)3+H]+"),
        "S03": UNREAD,
        "S05": None,
    }.get(name, _analyte("C3H12N6O3", TRIMER))
    if name == "S00":
        readings["t13"] = {
            **_analyte("C3H12N6O3", "[13C]C2H13N6O3+"),
            "role": "iso_child",
            "isotope_label": "13C",
            "owner": "t",
        }
    if name == "S03":
        readings["h"] = _reagent(HYDRATE, "[H3O+H2O]+")
    readings["d"] = {
        "S00": listed,
        "S01": _analyte(OTHER, D4_ION, fit=0.5, source="manual"),
        "S02": _analyte(OTHER, D4_ION, tier="candidate", fit=0.5),
        "S03": listed,
        "S04": _analyte(D4, D4_ION, tier="below_assignability", fit=0.4),
        "S05": _analyte(OTHER, D4_ION, fit=0.5),
    }[name]
    if name == "S04":
        # Its own reading of the siloxane, below candidate, with its own line.
        readings["d13"] = {
            **_analyte(D4, "[13C]C7H25O4Si4+", tier="below_assignability", fit=0.4),
            "role": "iso_child",
            "isotope_label": "13C",
            "owner": "d",
        }
    if name == "S05":
        readings["d13"] = {
            **_analyte(OTHER, "[13C]C8H25N2O4Si3+", fit=0.5),
            "role": "iso_child",
            "isotope_label": "13C",
            "owner": "d",
        }
    return {peak: reading for peak, reading in readings.items() if reading}


def _rows(sample_item_id, name, *, run_id=None, overrides=None):
    ids = {peak: gen_id(32) for peak in MZ}
    rows = []
    for peak, reading in _readings(name).items():
        reading = {**reading, **(overrides or {}).get(peak, {})}
        owner = reading.pop("owner", None)
        rows.append(
            {
                "peak_assignment_id": ids[peak],
                "peak_assignment_run_id": run_id or f"fold-{sample_item_id}",
                "sample_item_id": sample_item_id,
                "sample_peak_id": peak,
                "sample_peak_mz": MZ[peak],
                "sample_peak_intensity": 1e5 * (1 + int(name[1:])),
                "mz_error_ppm": 0.1,
                "isotope_label": None,
                "owner_peak_assignment_id": ids[owner] if owner else None,
                **reading,
            }
        )
    return rows


async def _seed(async_session_factory, pa_test_data, overrides=None):
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    samples, runs = {}, {}
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
                sample_batch_name=f"Known species {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, MECH) is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=MECH,
                    ionization_mechanism_polarity="+",
                    ionization_mechanism=NOTATION,
                )
            )
        for i in range(N_SAMPLES):
            name = f"S{i:02d}"
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-known-{name}-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 11, 12, 0, i, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[30.0, 500.0],
                    polarity="+",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=f"Known species {name}",
                    sample_item_type="sample",
                    polarity="+",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        await session.flush()
        for name in (*RUN_SAMPLES, IMPORT_SAMPLE):
            run_id = gen_id()
            runs[name] = run_id
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=samples[name],
                    engine="mascope" if name in RUN_SAMPLES else "peaky-test",
                    engine_version="0.1.0-test",
                    status="completed",
                    peak_assignment_run_utc_created=now,
                    peak_assignment_run_utc_completed=now,
                )
            )
            await session.flush()
            for row in _rows(
                samples[name],
                name,
                run_id=run_id,
                overrides=(overrides or {}).get(name),
            ):
                session.add(PeakAssignment(**row))
        await session.commit()
    for i in range(N_SAMPLES):
        name = f"S{i:02d}"
        if name in runs:
            folded = await fold_sample_into_batch_peaks(samples[name])
        else:
            folded = await fold_sample_into_batch_peaks(
                samples[name],
                rows=[SimpleNamespace(**r) for r in _rows(samples[name], name)],
                persisted=False,
            )
        assert folded is not None
    return {
        "batch_id": batch_id,
        "samples": samples,
        "runs": runs,
        "ids": await _ids(async_session_factory, batch_id),
    }


async def _ids(session_factory, batch_id):
    """Peak id -> its batch peak's id, by the m/z each peak was seeded at."""
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
    by_mz = {round(anchor.mz, 3): anchor.batch_peak_id for anchor in anchors}
    return {peak: by_mz[round(mz, 3)] for peak, mz in MZ.items()}


async def _anchors(session_factory, batch):
    async with session_factory() as session:
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == batch["batch_id"]
                    )
                )
            )
            .scalars()
            .all()
        }
    return {key: anchors[batch_peak_id] for key, batch_peak_id in batch["ids"].items()}


async def _members(session_factory, batch_peak_id):
    async with session_factory() as session:
        return {
            member.sample_item_id: member
            for member in (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id == batch_peak_id
                    )
                )
            )
            .scalars()
            .all()
        }


async def _run_rows(session_factory, run_id):
    """Peak id -> the run's row on it."""
    async with session_factory() as session:
        return {
            row.sample_peak_id: row
            for row in (
                await session.execute(
                    select(PeakAssignment).where(
                        PeakAssignment.peak_assignment_run_id == run_id
                    )
                )
            )
            .scalars()
            .all()
        }


@pytest_asyncio.fixture
async def known_batch(async_session_factory, pa_test_data):
    return await _seed(async_session_factory, pa_test_data)


# The record is read off the stored provenance by the name the decision
# writes, not through the constant: a renamed key orphans every record a
# server already holds.
def _record(anchor):
    return (anchor.provenance or {}).get("known_species")


async def test_a_library_ion_in_most_samples_is_claimed_in_all_and_the_ledgers_agree(
    async_session_factory, known_batch
):
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    before = await _anchors(async_session_factory, batch)
    assert before["t"].consensus_formula == "C3H12N6O3"
    summary = await run_batch_known_species(
        batch["batch_id"], batch_peak_run_id="run-known"
    )
    anchors = await _anchors(async_session_factory, batch)

    trimer = anchors["t"]
    assert trimer.consensus_role == "reagent"
    assert trimer.consensus_ion_formula == TRIMER
    assert trimer.consensus_formula is None
    record = _record(trimer)
    assert record["verdict"] == "present"
    assert record["species"]["kind"] == "library"
    assert record["species"]["label"] == "[(CH4N2O)3+H]+"
    assert (record["files"], record["present"], record["test_held"]) == (6, 5, 1)
    assert record["claimed"] is True
    assert record["members_rewritten"] == 4
    assert record["rows_rewritten"] == 1
    assert record["lines_rewritten"] == 1
    assert record["rows_kept"] == {"imported": 1, "no_row": 2}
    assert set(record["displaced_members"]) == {
        samples[name] for name in ("S00", "S01", "S03", "S04")
    }
    assert record["reads"]["role"] == "reagent"
    assert record["batch_peak_run_id"] == "run-known"
    for member in (await _members(async_session_factory, ids["t"])).values():
        assert member.role == role_code("reagent")
        assert member.tier == tier_code("unassigned")
        assert member.fit_score is None

    # The in-app run's row is the pre-pass's claim, the reading it displaced
    # recorded; its 13C line goes with it, owned as it was.
    rows = await _run_rows(async_session_factory, batch["runs"]["S00"])
    row, line = rows["t"], rows["t13"]
    assert row.role == "reagent"
    assert (row.assigned_formula, row.ion_formula) == (None, TRIMER)
    assert row.tier == "unassigned"
    assert row.provenance["reagent"]["reason"] == "batch_known_species"
    assert row.provenance["reagent"]["batch_known_species"]["batch_peak_id"] == ids["t"]
    assert row.provenance["reagent"]["read_as"]["formula"] == "C3H12N6O3"
    assert row.alternatives[0]["assigned_formula"] == "C3H12N6O3"
    assert row.alternatives[0]["displaced_by_claim"] is True
    assert line.role == "reagent"
    assert line.isotope_label == "13C"
    assert line.owner_peak_assignment_id == row.peak_assignment_id
    line_member = (await _members(async_session_factory, ids["t13"]))[samples["S00"]]
    assert line_member.role == role_code("reagent")
    assert line_member.owner_batch_peak_id == ids["t"]
    assert anchors["t13"].consensus_role == "reagent"
    assert anchors["t13"].isotopologue_of == ids["t"]
    # The imported run is another engine's record, and is not rewritten.
    imported = await _run_rows(async_session_factory, batch["runs"][IMPORT_SAMPLE])
    assert imported["t"].role == "M0"
    assert imported["t"].assigned_formula == "C3H12N6O3"

    assert summary["species"] == 4
    assert (summary["library"], summary["listed"]) == (3, 1)
    assert (summary["present"], summary["unpooled"]) == (3, 1)
    assert summary["claimed"] == 2
    assert summary["partitions"][0]["files"] == N_SAMPLES


async def test_a_species_claimed_everywhere_is_present_with_nothing_to_claim(
    async_session_factory, known_batch
):
    await run_batch_known_species(known_batch["batch_id"])
    monomer = _record((await _anchors(async_session_factory, known_batch))["m"])
    assert monomer["verdict"] == "present"
    assert monomer["test_held"] == N_SAMPLES
    assert monomer["claimed"] is False
    assert monomer["co_variation"] is None


async def test_a_species_in_one_sample_is_not_pooled_and_its_claim_stands(
    async_session_factory, known_batch
):
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["h"])
    assert record["verdict"] == "unpooled"
    assert record["reason"] == "one_file"
    assert record["files_by_reason"] == {"own_claim": 1}
    assert record["files_recorded"][0]["sample_item_id"] == samples["S03"]
    [member] = (await _members(async_session_factory, ids["h"])).values()
    assert member.role == role_code("reagent")


async def test_a_listed_compound_is_claimed_where_present_and_its_holds_recorded(
    async_session_factory, known_batch
):
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    await run_batch_known_species(batch["batch_id"])
    anchors = await _anchors(async_session_factory, batch)

    siloxane = anchors["d"]
    assert siloxane.consensus_formula == D4
    assert siloxane.ionization_mechanism_id == MECH
    record = _record(siloxane)
    assert record["verdict"] == "present"
    assert record["species"]["kind"] == "listed"
    assert record["species"]["formula"] == D4
    assert record["species"]["listing"]["tags"] == ["background"]
    assert record["test_held"] == 2
    assert record["files_by_reason"] == {"carries_lines": 1, "hand_assigned": 1}
    kept = {entry["sample_item_id"]: entry for entry in record["files_recorded"]}
    assert kept[samples["S01"]]["formula"] == OTHER
    assert kept[samples["S05"]]["reason"] == "carries_lines"
    assert set(record["displaced_members"]) == {samples["S02"], samples["S04"]}

    members = await _members(async_session_factory, ids["d"])
    claimed = members[samples["S02"]]
    assert claimed.tier == tier_code("candidate")
    assert claimed.role == role_code("M0")
    assert claimed.fit_score is None
    # Its own reading, lifted: what it measured, and its line, stay.
    lifted = members[samples["S04"]]
    assert lifted.tier == tier_code("candidate")
    assert lifted.fit_score == pytest.approx(0.4)
    line = (await _members(async_session_factory, ids["d13"]))[samples["S04"]]
    assert line.owner_batch_peak_id == ids["d"]
    assert line.role == role_code("iso_child")
    for name in ("S01", "S05"):
        assert members[samples[name]].tier == tier_code("assigned")
    # The in-app run's row reads the list's compound at candidate, with the
    # list's identities and the reading it displaced.
    row = (await _run_rows(async_session_factory, batch["runs"]["S02"]))["d"]
    assert (row.assigned_formula, row.ion_formula) == (D4, D4_ION)
    assert row.tier == "candidate"
    assert row.source == "database"
    assert row.provenance["reference_identities"] == IDENTITIES
    assert row.provenance["batch_known_species"]["read_as"]["formula"] == OTHER
    assert row.alternatives[0]["assigned_formula"] == OTHER
    # The hand assignment is a person's, and stays.
    imported = await _run_rows(async_session_factory, batch["runs"][IMPORT_SAMPLE])
    assert imported["d"].assigned_formula == OTHER


async def test_an_ordinary_analyte_is_never_claimed(async_session_factory, known_batch):
    await run_batch_known_species(known_batch["batch_id"])
    analyte = (await _anchors(async_session_factory, known_batch))["a"]
    assert analyte.consensus_formula == "C10H14O4"
    assert _record(analyte) is None


async def test_the_consensus_and_a_refold_keep_the_record(
    async_session_factory, known_batch
):
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["t"])

    await recompute_batch_consensus(batch["batch_id"], set(ids.values()))
    # The in-app sample folds again from its rewritten rows.
    assert await fold_sample_into_batch_peaks(samples["S00"]) is not None
    anchors = await _anchors(async_session_factory, batch)
    assert anchors["t"].consensus_role == "reagent"
    assert _record(anchors["t"]) == record
    member = (await _members(async_session_factory, ids["t"]))[samples["S00"]]
    assert member.role == role_code("reagent")


async def test_a_second_decision_claims_nothing_twice(
    async_session_factory, known_batch
):
    await run_batch_known_species(known_batch["batch_id"])
    first = {
        key: _record(anchor)
        for key, anchor in (await _anchors(async_session_factory, known_batch)).items()
    }
    again = await run_batch_known_species(known_batch["batch_id"])
    assert again["members_rewritten"] == 0
    assert again["present"] == 3
    anchors = await _anchors(async_session_factory, known_batch)
    assert _record(anchors["t"])["verdict"] == "present"
    # The evidence is each file's own test, as the first decision read it: the
    # batch's own claims (a rewritten row, an import's member, members with no
    # row) are not files whose test held.
    for key in ("t", "m", "d"):
        assert _record(anchors[key])["test_held"] == first[key]["test_held"]
        assert (
            _record(anchors[key])["own_test_samples"] == first[key]["own_test_samples"]
        )
    assert _record(anchors["t"])["own_test_samples"] == [known_batch["samples"]["S02"]]
    assert first["d"]["test_held"] == 2
    assert first["m"]["test_held"] == N_SAMPLES


async def test_the_batchs_own_claims_never_keep_a_species_present(
    async_session_factory, known_batch
):
    """The one sample whose own test held leaves the trimer: the batch's
    earlier claims do not keep it present, and the record lists them."""
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    await run_batch_known_species(batch["batch_id"])
    async with async_session_factory() as session:
        await session.execute(
            delete(BatchPeakOccurrence).where(
                BatchPeakOccurrence.batch_peak_id == ids["t"],
                BatchPeakOccurrence.sample_item_id == samples["S02"],
            )
        )
        await session.commit()
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["t"])
    assert record["verdict"] == "absent"
    assert (record["present"], record["test_held"]) == (4, 0)
    assert record["files_by_reason"] == {"batch_claim": 4}


async def test_a_new_decision_replaces_every_record(async_session_factory, known_batch):
    batch, ids = known_batch, known_batch["ids"]
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == ids["a"])
            .values(
                provenance={
                    "known_species": {"verdict": "present", "reads": {"role": None}}
                }
            )
        )
        await session.commit()
    summary = await run_batch_known_species(batch["batch_id"])
    assert summary["records_cleared"] == 1
    assert _record((await _anchors(async_session_factory, batch))["a"]) is None


async def test_a_hand_assignment_made_since_the_reading_holds_its_sample(
    async_session_factory, known_batch
):
    batch, samples = known_batch, known_batch["samples"]
    anchors, members, claims = await batch_known_species._load_batch(batch["batch_id"])
    plan = batch_known_species.plan_batch(anchors, members, claims)
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.peak_assignment_run_id == batch["runs"]["S00"],
                PeakAssignment.sample_peak_id == "t",
            )
            .values(source="manual")
        )
        await session.commit()
    files = {partition: c["files"] for partition, c in plan.counts.items()}
    await batch_known_species._write_chunk(
        batch["batch_id"], plan.decisions, files, None
    )
    record = _record((await _anchors(async_session_factory, batch))["t"])
    assert record["files_by_reason"] == {"hand_assigned": 1}
    rows = await _run_rows(async_session_factory, batch["runs"]["S00"])
    assert rows["t"].role == "M0"
    assert samples["S00"] not in record["displaced_members"]


async def test_a_samples_next_run_takes_the_batchs_word(
    async_session_factory, known_batch
):
    batch = known_batch
    sample = await fetch_sample(batch["samples"]["S04"])
    assert await batch_species_for(sample) == []
    await run_batch_known_species(batch["batch_id"])
    species = await batch_species_for(sample)
    # The present library ions; not the one too few samples showed, and not
    # the listed compound, which the stages read.
    assert sorted(one.cluster.formula for one in species) == ["C3H13N6O3", "CH5N2O"]
    [trimer] = [one for one in species if one.cluster.formula == "C3H13N6O3"]
    assert trimer.cluster.charge == 1
    assert trimer.record["batch_peak_id"] == batch["ids"]["t"]
    assert trimer.record["verdict"] == "present"


async def test_a_samples_run_takes_one_entry_per_ion(
    async_session_factory, known_batch
):
    """The fold split the trimer's trace over batch peaks decided present: the
    run claims the ion once, as the one in the most samples, whichever comes
    first or last by id."""
    batch = known_batch
    await run_batch_known_species(batch["batch_id"])
    async with async_session_factory() as session:
        trimer = await session.get(BatchPeak, batch["ids"]["t"])
        record = _record(trimer)
        # Ids that sort before and after any the fold mints.
        for i, key in enumerate(("!" + gen_id(15), "~" + gen_id(15))):
            twin = BatchPeak(
                **{
                    column.name: getattr(trimer, column.name)
                    for column in BatchPeak.__table__.columns
                }
            )
            twin.batch_peak_id = key
            twin.mz = trimer.mz * (1 + (i + 1) * 3e-6)
            twin.provenance = {
                **(trimer.provenance or {}),
                "known_species": {**record, "present": 2},
            }
            session.add(twin)
        await session.commit()
    species = await batch_species_for(await fetch_sample(batch["samples"]["S04"]))
    trimers = [one for one in species if one.cluster.formula == "C3H13N6O3"]
    assert [one.record["batch_peak_id"] for one in trimers] == [batch["ids"]["t"]]


async def test_the_ledger_and_an_earlier_run_serve_the_record(
    guest_client, async_session_factory, known_batch
):
    batch, ids = known_batch, known_batch["ids"]
    run_id = await start_run(batch["batch_id"], ACTION_KNOWN_SPECIES)
    await run_batch_known_species(batch["batch_id"], batch_peak_run_id=run_id)
    await complete_run(batch["batch_id"], run_id)

    async def ledger(**params):
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{batch['batch_id']}",
            params={"min_n_present": 1, **params},
        )
        assert response.status_code == 200, response.text
        return {row["batch_peak_id"]: row for row in response.json()["data"]}

    live = await ledger()
    served = live[ids["t"]]["known_species"]
    assert served["verdict"] == "present"
    assert served["batch_peak_run_id"] == run_id
    # The members' earlier states are the run snapshot's to keep.
    assert "displaced_members" not in served
    assert live[ids["a"]]["known_species"] is None

    later = await start_run(batch["batch_id"], ACTION_KNOWN_SPECIES)
    await complete_run(batch["batch_id"], later)
    earlier = await ledger(batch_peak_run_id=run_id)
    assert earlier[ids["t"]]["known_species"] == served


async def test_the_route_runs_it_as_a_batch_run(
    editor_client, guest_client, async_session_factory, known_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    batch_id = known_batch["batch_id"]
    refused = await guest_client.post(
        f"/api/batch-peaks/batch/{batch_id}/decide-known-species"
    )
    assert refused.status_code == 403

    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/decide-known-species"
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
    [run] = [run for run in runs if run.action == ACTION_KNOWN_SPECIES]
    assert run.status == "completed"
    assert run.is_current == 1
    assert run.config["min_present_share"] == 0.5
    assert run.summary["present"] == 3
    anchors = await _anchors(async_session_factory, known_batch)
    assert _record(anchors["t"])["batch_peak_run_id"] == run.batch_peak_run_id


async def test_the_route_refuses_while_another_run_rewrites_the_ledger(
    editor_client, known_batch, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "decide_batch_known_species", fake_task)
    await start_run(known_batch["batch_id"], ACTION_KNOWN_SPECIES)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{known_batch['batch_id']}/decide-known-species"
    )
    assert response.status_code == 409, response.text
    assert launched == []


async def test_a_sample_the_target_library_named_is_kept(
    async_session_factory, known_batch
):
    batch, samples = known_batch, known_batch["samples"]
    compound_id = gen_id(16)
    async with async_session_factory() as session:
        session.add(
            TargetCompound(
                target_compound_id=compound_id, target_compound_formula="C3H12N6O3"
            )
        )
        await session.flush()
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.peak_assignment_run_id == batch["runs"]["S00"],
                PeakAssignment.sample_peak_id == "t",
            )
            .values(target_compound_id=compound_id)
        )
        await session.commit()
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["t"])
    assert record["files_by_reason"] == {"target_library": 1}
    assert samples["S00"] not in record["displaced_members"]
    rows = await _run_rows(async_session_factory, batch["runs"]["S00"])
    assert rows["t"].role == "M0"
    assert rows["t"].target_compound_id == compound_id


async def test_a_listed_species_whose_registry_moved_is_left_for_the_next_run(
    async_session_factory, known_batch
):
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    anchors, members, claims = await batch_known_species._load_batch(batch["batch_id"])
    plan = batch_known_species.plan_batch(anchors, members, claims)
    async with async_session_factory() as session:
        siloxane = await session.get(BatchPeak, ids["d"])
        siloxane.candidates = [
            {key: value for key, value in entry.items() if key != "listing"}
            for entry in siloxane.candidates
        ]
        await session.commit()
    files = {partition: c["files"] for partition, c in plan.counts.items()}
    written = await batch_known_species._write_chunk(
        batch["batch_id"], plan.decisions, files, None
    )
    [done] = [w for w in written if w.decision.row.batch_peak_id == ids["d"]]
    assert not done.claimed
    assert done.decision.held == "moved"
    assert _record((await _anchors(async_session_factory, batch))["d"])["held"] == (
        "moved"
    )
    members = await _members(async_session_factory, ids["d"])
    assert members[samples["S04"]].tier == tier_code("below_assignability")
    assert members[samples["S02"]].tier == tier_code("candidate")
    assert members[samples["S02"]].fit_score == pytest.approx(0.5)


async def test_a_listed_species_whose_readings_moved_to_another_listed_formula_is_held(
    async_session_factory, known_batch
):
    """Between the reading and the write the list tags the other formula too,
    which most samples commit: the batch peak is another listed compound now,
    and the planned one is not claimed over it."""
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    anchors, members, claims = await batch_known_species._load_batch(batch["batch_id"])
    plan = batch_known_species.plan_batch(anchors, members, claims)
    [planned] = [d for d in plan.decisions if d.row.batch_peak_id == ids["d"]]
    assert (planned.species.formula, planned.verdict.verdict) == (D4, "present")
    async with async_session_factory() as session:
        siloxane = await session.get(BatchPeak, ids["d"])
        listing = next(e["listing"] for e in siloxane.candidates if e.get("listing"))
        siloxane.candidates = [
            {**entry, "listing": listing} if entry["formula"] == OTHER else entry
            for entry in siloxane.candidates
        ]
        await session.commit()
    files = {partition: c["files"] for partition, c in plan.counts.items()}
    written = await batch_known_species._write_chunk(
        batch["batch_id"], plan.decisions, files, None
    )
    [done] = [w for w in written if w.decision.row.batch_peak_id == ids["d"]]
    assert not done.claimed
    assert done.decision.held == "moved"
    members = await _members(async_session_factory, ids["d"])
    assert members[samples["S02"]].tier == tier_code("candidate")
    assert members[samples["S04"]].tier == tier_code("below_assignability")
    row = (await _run_rows(async_session_factory, batch["runs"]["S02"]))["d"]
    assert row.assigned_formula == OTHER


async def test_a_line_the_ion_cannot_have_keeps_its_sample(
    async_session_factory, known_batch
):
    """S00 read the trimer's peak as an analyte whose line its row labels
    81Br: the trimer has no bromine, so S00 keeps its reading and the line."""
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.peak_assignment_run_id == batch["runs"]["S00"],
                PeakAssignment.sample_peak_id == "t13",
            )
            .values(isotope_label="81Br")
        )
        await session.commit()
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["t"])
    assert record["claimed"] is True
    assert record["files_by_reason"] == {"carries_lines": 1}
    assert samples["S00"] not in record["displaced_members"]
    assert record["lines_rewritten"] == 0
    rows = await _run_rows(async_session_factory, batch["runs"]["S00"])
    assert (rows["t"].role, rows["t13"].role) == ("M0", "iso_child")
    line_member = (await _members(async_session_factory, ids["t13"]))[samples["S00"]]
    assert line_member.role == role_code("iso_child")


async def test_the_plan_reads_each_lines_label_off_its_row(
    async_session_factory, known_batch
):
    """S00's 13C line under the trimer, a carbon ion, goes with the claim."""
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    anchors, members, claims = await batch_known_species._load_batch(batch["batch_id"])
    assert members[ids["t13"]][samples["S00"]].isotope_label == "13C"
    plan = batch_known_species.plan_batch(anchors, members, claims)
    [trimer] = [d for d in plan.decisions if d.row.batch_peak_id == ids["t"]]
    assert trimer.samples[samples["S00"]] is None


async def test_a_samples_own_claim_made_since_counts_as_its_own(
    async_session_factory, known_batch
):
    """S00's run claims the trimer again on its own after the first decision:
    its row says so, and the next decision counts it, though the first read
    S00 as the batch's."""
    batch, samples = known_batch, known_batch["samples"]
    await run_batch_known_species(batch["batch_id"])
    rows = await _run_rows(async_session_factory, batch["runs"]["S00"])
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(PeakAssignment.peak_assignment_id == rows["t"].peak_assignment_id)
            .values(provenance={"reagent": {"ion": "[(CH4N2O)3+H]+"}})
        )
        await session.commit()
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["t"])
    assert record["test_held"] == 2
    assert record["own_test_samples"] == sorted([samples["S00"], samples["S02"]])


async def test_a_line_row_another_row_owns_is_left_as_it_was(
    async_session_factory, known_batch
):
    """S00's line member names the trimer's batch peak, but its row is owned
    by another of the sample's rows: the member goes with the claim, the row
    is that other row's line and is not rewritten."""
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    rows = await _run_rows(async_session_factory, batch["runs"]["S00"])
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(PeakAssignment.peak_assignment_id == rows["t13"].peak_assignment_id)
            .values(owner_peak_assignment_id=rows["a"].peak_assignment_id)
        )
        await session.commit()
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["t"])
    assert samples["S00"] in record["displaced_members"]
    assert record["lines_rewritten"] == 1
    line_member = (await _members(async_session_factory, ids["t13"]))[samples["S00"]]
    assert line_member.role == role_code("reagent")
    after = await _run_rows(async_session_factory, batch["runs"]["S00"])
    assert after["t"].role == "reagent"
    line = after["t13"]
    assert line.role == "iso_child"
    assert line.assigned_formula == "C3H12N6O3"
    assert line.owner_peak_assignment_id == rows["a"].peak_assignment_id


async def test_a_listed_compound_no_sample_row_committed_reads_off_the_registry(
    async_session_factory, pa_test_data
):
    """The in-app sample leaves the siloxane unread, so only a sample folded
    without a run committed it: the claim's reading comes off the batch
    peak's registry, at the ion's own mass."""
    batch = await _seed(
        async_session_factory, pa_test_data, overrides={"S00": {"d": UNREAD}}
    )
    await run_batch_known_species(batch["batch_id"])
    record = _record((await _anchors(async_session_factory, batch))["d"])
    assert record["verdict"] == "present"
    assert record["held"] is None
    row = (await _run_rows(async_session_factory, batch["runs"]["S00"]))["d"]
    assert (row.assigned_formula, row.ion_formula) == (D4, D4_ION)
    assert row.tier == "candidate"
    assert row.source == "database"
    exact = ion_mz("C8H25O4Si4", 1)
    assert row.mz_error_ppm == pytest.approx((MZ["d"] - exact) / exact * 1e6, abs=1e-3)


async def test_a_split_trace_is_claimed_once_and_its_twin_is_held(
    async_session_factory, known_batch, monkeypatch
):
    """The fold split the trimer's trace: a second batch peak of it, 3 ppm
    higher, holds S03's own claim, an analyte in S04 and an unread peak in
    S05. The trimer's batch peak, in more samples, claims; S03, which reads
    the ion on the twin, is kept; the twin is held, its analyte standing.
    One species per write, so the twin is read again in its own chunk."""
    batch, samples, ids = known_batch, known_batch["samples"], known_batch["ids"]
    twin_id = "~" + gen_id(15)
    async with async_session_factory() as session:
        trimer = await session.get(BatchPeak, ids["t"])
        twin = BatchPeak(
            **{
                column.name: getattr(trimer, column.name)
                for column in BatchPeak.__table__.columns
            }
        )
        twin.batch_peak_id = twin_id
        twin.mz = trimer.mz * (1 + 3e-6)
        registry = list(trimer.candidates or [])
        claim = candidate_index(registry, None, TRIMER, None, source="reagent")
        other = candidate_index(
            registry, "C4H12N4O4", TRIMER, MECH, source="untargeted"
        )
        twin.candidates = registry
        session.add(twin)
        await session.flush()
        for name, role, tier, candidate in (
            ("S03", "reagent", "unassigned", claim),
            ("S04", "M0", "assigned", other),
            ("S05", "unassigned", "unassigned", None),
        ):
            session.add(
                BatchPeakOccurrence(
                    batch_peak_id=twin_id,
                    sample_item_id=samples[name],
                    sample_peak_id="tw",
                    mz_delta_ppm=0.0,
                    intensity=1e5,
                    tier=tier_code(tier),
                    fit_score=0.9 if role == "M0" else None,
                    candidate=candidate,
                    role=role_code(role),
                )
            )
        await session.commit()
    monkeypatch.setattr(batch_known_species, "WRITE_CHUNK", 1)
    await run_batch_known_species(batch["batch_id"])

    anchors = await _anchors(async_session_factory, batch)
    record = _record(anchors["t"])
    assert record["claimed"] is True
    assert record["twin_of"] is None
    assert record["files_by_reason"] == {"reads_on_twin": 1}
    assert samples["S03"] not in record["displaced_members"]
    assert (await _members(async_session_factory, ids["t"]))[
        samples["S03"]
    ].role == role_code("unassigned")
    async with async_session_factory() as session:
        held = await session.get(BatchPeak, twin_id)
    twin_record = _record(held)
    assert twin_record["verdict"] == "present"
    assert twin_record["held"] == "twin"
    assert twin_record["twin_of"] == ids["t"]
    assert twin_record["claimed"] is False
    twin_members = await _members(async_session_factory, twin_id)
    assert twin_members[samples["S04"]].role == role_code("M0")
    assert twin_members[samples["S05"]].role == role_code("unassigned")
    # The next run takes the ion once, off the batch peak that claimed it.
    species = await batch_species_for(await fetch_sample(samples["S05"]))
    trimers = [one for one in species if one.cluster.formula == "C3H13N6O3"]
    assert [one.record["batch_peak_id"] for one in trimers] == [ids["t"]]
