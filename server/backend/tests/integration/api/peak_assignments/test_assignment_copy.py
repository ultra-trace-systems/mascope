"""
Integration tests for copying a curated sample's assignments to its batch.

Covers the endpoint contract (the eligibility preview, the launch's 202
partition, its refusals) and the fan-out itself: that each destination gets one
complete run, published under the reserved copy engine through the import
pipeline, with the source's curation carried and its evidence re-measured.

The seeded re-score needs a real peak file, which these fixtures do not have,
so the scoring seam is stubbed per test with a table of destination fits - the
copy's own remap, drop rules, publishing and reporting are exercised for real
against it. The scoring chain's own behaviour is the engine's suite; that the
copy re-tiers from whatever it returns is asserted here through that table.

The feature flag is forced through the ``MASCOPE_PEAK_ASSIGNMENT`` env
override, like the other write tests.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.new.peak_assignments import copy_service, import_service
from mascope_backend.api.new.peak_assignments import routes as routes_module
from mascope_backend.api.new.peak_assignments.config import (
    COPY_ENGINE,
    IN_APP_ENGINE,
)
from mascope_backend.api.new.peak_assignments.copy_service import (
    copy_assignments_to_batch,
)
from mascope_backend.db import (
    Dataset,
    InstrumentFunction,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


TIER_BANDS = {"assigned": 0.8, "candidate": 0.5}

#: The source's curated ledger: two peaks of one compound (an M0 and its
#: isotopologue child) plus one peak of another compound. Spelled out as a
#: table so a test can say which of them a destination is missing.
SOURCE_SPECS = (
    # (peak id, m/z, formula, ion formula, role, isotope label, fit, tier)
    ("src-1", 181.0707, "C6H12O6", "C6H13O6+", "M0", "M0", 0.95, "assigned"),
    ("src-2", 182.0741, "C6H12O6", "C6H13O6+", "iso_child", "M+1", 0.95, "assigned"),
    ("src-3", 250.1200, "C10H16N2", "C10H17N2+", "M0", "M0", 0.61, "candidate"),
)

#: The source run's residual mass offset: every row above carries this error,
#: so it is the median the copy corrects the source axis by.
SOURCE_MU_PPM = 1.2

#: The destination's peak file. The first three are the same species as the
#: source's, sitting where a well-calibrated destination measures them - that
#: is, a few tenths of a ppm from the source's peaks once the source's own 1.2
#: ppm offset is corrected out, comfortably inside the 2 ppm drift margin these
#: fixtures fall back to (no resolution function behind a synthetic file). The
#: fourth is the destination's own peak, which nothing explains and which must
#: therefore come back as an unassigned placeholder.
DESTINATION_PEAKS = (
    ("dst-1", 181.07050, 4200.0),
    ("dst-2", 182.07390, 300.0),
    ("dst-3", 250.11975, 900.0),
    ("dst-4", 310.99990, 55.0),
)


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest.fixture(autouse=True)
def stub_peak_reads(monkeypatch):
    """Serve both samples' peaks without a peak file behind them.

    Two seams, because the copy reads peaks twice for two different purposes:
    the engine's full read (ids, m/z, intensity - what the copied rows land
    on) and the import's id-only read (what validation admits). Both must
    agree, so both are served from one table; a copied row landing on a peak
    the importer does not know would be a 422 rather than a silent mismatch.
    """
    peaks_df = pd.DataFrame(
        {
            "sample_peak_id": [peak_id for peak_id, _, _ in DESTINATION_PEAKS],
            "mz": [mz for _, mz, _ in DESTINATION_PEAKS],
            "intensity": [intensity for _, _, intensity in DESTINATION_PEAKS],
        }
    )

    def _load(_sample):
        return peaks_df.copy()

    async def _peak_ids(_sample):
        return {peak_id for peak_id, _, _ in DESTINATION_PEAKS}

    monkeypatch.setattr(copy_service, "load_sample_peaks", _load)
    monkeypatch.setattr(import_service, "_load_peak_ids", _peak_ids)


@pytest.fixture(autouse=True)
def stub_fold_in(monkeypatch):
    """Keep the batch fold-in out of these tests, but record it.

    Publishing folds each destination in, which is best-effort and covered by
    its own suite; here it would only be a slow no-op over fixture data.
    Recorded so a test can assert every published copy reached it.
    """
    from mascope_backend.api.new.peak_assignments import batch_peaks_controller

    calls = []

    async def _fold(sample_item_id):
        calls.append(sample_item_id)
        return None

    monkeypatch.setattr(batch_peaks_controller, "fold_sample_into_batch_peaks", _fold)
    return calls


@pytest.fixture
def destination_fits(monkeypatch):
    """Stand in for the seeded re-score with a per-formula fit table.

    Returns a dict the test writes destination fits into, keyed by formula;
    a formula absent from it scores None (nothing measurable on this sample).
    Patching here rather than at ``compute_match_isotopes`` keeps the test
    about what the copy does with a re-measured fit - carry it onto the row
    and re-tier from it - rather than about the matcher's numerics.
    """
    fits: dict[str, float | None] = {}

    async def _rescore(destination, source_rows, match_params):
        ion_by_seed = {}
        fit_by_ion = {}
        errors = {}
        for index, formula in enumerate(
            sorted(
                {
                    row["assigned_formula"]
                    for row in source_rows
                    if row["assigned_formula"]
                }
            )
        ):
            ion_id = f"seed-ion-{index}"
            for mechanism_id in {
                row["ionization_mechanism_id"]
                for row in source_rows
                if row["assigned_formula"] == formula
            }:
                ion_by_seed[(formula, mechanism_id)] = ion_id
            fit_by_ion[ion_id] = fits.get(formula)
            for peak_id, _, _ in DESTINATION_PEAKS:
                errors[(ion_id, peak_id)] = {
                    "mz_error_ppm": 0.8,
                    "abundance_error": 0.03,
                    "sample_peak_tof": None,
                }
        return ion_by_seed, fit_by_ion, errors, (0.0, 1.5)

    monkeypatch.setattr(copy_service, "_seeded_rescore", _rescore)
    return fits


@pytest_asyncio.fixture
async def copy_batch(async_session_factory, pa_test_data):
    """A batch with a curated source, an eligible destination, and two skips.

    Its own dataset and batch, not ``pa_test_data``'s: the partition covers
    every sample of the batch, so a shared one would make these assertions
    depend on what other suites seeded. Torn down afterwards for the same
    reason, innermost first.
    """
    now = datetime.now(timezone.utc)
    dataset_id = gen_id()
    sample_batch_id = gen_id()
    instrument_function_id = gen_id(32)
    source_run_id = gen_id()
    ids = {
        "source": gen_id(),
        "destination": gen_id(),
        "blank": gen_id(),
        "negative": gen_id(),
    }
    file_ids = {key: gen_id() for key in ids}
    assignment_ids = {peak_id: gen_id(32) for peak_id, *_ in SOURCE_SPECS}

    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=pa_test_data["workspace_id"],
                dataset_name=f"Assignment Copy Dataset {dataset_id}",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=sample_batch_id,
                dataset_id=dataset_id,
                sample_batch_name="Assignment Copy Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            InstrumentFunction(
                instrument_function_id=instrument_function_id,
                instrument="orbi-test",
                method_file="assignment-copy.meth",
                datetime_utc=now,
            )
        )
        # A sample counts as measured precisely by having an instrument
        # function; the blank deliberately has none.
        for key, file_id in file_ids.items():
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    instrument_function_id=(
                        None if key == "blank" else instrument_function_id
                    ),
                    filename=f"assignment-copy-{key}-{file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 8, 19, 12, 0, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-" if key == "negative" else "+",
                )
            )
        # Names order the fan-out: "A ..." before "B ...", which is the order
        # the partition reports and the task visits.
        for key, name in (
            ("source", "A Curated Source"),
            ("destination", "B Sibling Sample"),
            ("blank", "C Blank Sample"),
            ("negative", "D Negative Sample"),
        ):
            session.add(
                SampleItem(
                    sample_item_id=ids[key],
                    sample_batch_id=sample_batch_id,
                    sample_file_id=file_ids[key],
                    sample_item_name=name,
                    sample_item_type="blank" if key == "blank" else "sample",
                    polarity="-" if key == "negative" else "+",
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=now,
                )
            )

        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=source_run_id,
                sample_item_id=ids["source"],
                engine=IN_APP_ENGINE,
                engine_version="0.2.0-test",
                status="completed",
                config={"run_untargeted": True},
                tier_bands=dict(TIER_BANDS),
                peak_assignment_run_utc_created=now - timedelta(hours=1),
                peak_assignment_run_utc_completed=now - timedelta(minutes=55),
            )
        )
        for peak_id, mz, formula, ion_formula, role, label, fit, tier in SOURCE_SPECS:
            session.add(
                PeakAssignment(
                    peak_assignment_id=assignment_ids[peak_id],
                    peak_assignment_run_id=source_run_id,
                    sample_item_id=ids["source"],
                    sample_peak_id=peak_id,
                    sample_peak_mz=mz,
                    sample_peak_intensity=5000.0,
                    role=role,
                    assigned_formula=formula,
                    ion_formula=ion_formula,
                    isotope_label=label,
                    source="database",
                    fit_score=fit,
                    mz_error_ppm=SOURCE_MU_PPM,
                    abundance_error=0.05,
                    tier=tier,
                    owner_peak_assignment_id=(
                        assignment_ids["src-1"] if role == "iso_child" else None
                    ),
                    alternatives=[{"assigned_formula": "C7H16O5", "fit_score": 0.4}],
                    provenance={"plausibility": 0.9, "evidence": 0.87},
                )
            )
        # An unassigned placeholder on the source: it must NOT be copied - the
        # destination gets placeholders built for its own peaks instead.
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=source_run_id,
                sample_item_id=ids["source"],
                sample_peak_id="src-4",
                sample_peak_mz=999.0,
                sample_peak_intensity=10.0,
                role="unassigned",
                tier="unassigned",
            )
        )
        await session.commit()

    yield {
        "sample_batch_id": sample_batch_id,
        "source_run_id": source_run_id,
        "assignment_ids": assignment_ids,
        **ids,
    }

    async with async_session_factory() as session:
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.sample_item_id.in_(tuple(ids.values()))
            )
        )
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(tuple(ids.values())))
        )
        await session.execute(
            delete(SampleFile).where(
                SampleFile.sample_file_id.in_(tuple(file_ids.values()))
            )
        )
        await session.execute(
            delete(InstrumentFunction).where(
                InstrumentFunction.instrument_function_id == instrument_function_id
            )
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == sample_batch_id)
        )
        await session.execute(delete(Dataset).where(Dataset.dataset_id == dataset_id))
        await session.commit()


@pytest.fixture
def launched_copies(monkeypatch):
    """Record the background fan-out instead of running it.

    The route's decision - the partition it reports and the refusals it
    raises - is what the endpoint tests are about; the fan-out itself is
    driven directly by the tests below.
    """
    calls = []

    async def _copy(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "message": "recorded"}

    monkeypatch.setattr(routes_module, "copy_assignments_to_batch", _copy)
    return calls


def _preview_url(sample_item_id: str) -> str:
    return f"/api/peak-assignments/sample/{sample_item_id}/copy-to-batch"


async def _runs_of(session_factory, sample_item_id) -> list[PeakAssignmentRun]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(PeakAssignmentRun)
                    .where(PeakAssignmentRun.sample_item_id == sample_item_id)
                    .order_by(PeakAssignmentRun.peak_assignment_run_utc_created)
                )
            )
            .scalars()
            .all()
        )


async def _rows_of(session_factory, run_id) -> list[PeakAssignment]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(PeakAssignment)
                    .where(PeakAssignment.peak_assignment_run_id == run_id)
                    .order_by(PeakAssignment.sample_peak_mz)
                )
            )
            .scalars()
            .all()
        )


class TestPreview:
    """The eligibility list the copy dialog renders."""

    @pytest.mark.asyncio
    async def test_it_reports_every_sibling_with_its_verdict(
        self, editor_client, copy_batch, feature_enabled
    ):
        """One entry per other sample of the batch, source excluded."""
        response = await editor_client.get(_preview_url(copy_batch["source"]))

        assert response.status_code == 200
        record = response.json()["data"][0]
        assert record["source_peak_assignment_run_id"] == copy_batch["source_run_id"]
        assert record["source_engine"] == IN_APP_ENGINE
        by_id = {entry["sample_item_id"]: entry for entry in record["destinations"]}
        assert set(by_id) == {
            copy_batch["destination"],
            copy_batch["blank"],
            copy_batch["negative"],
        }
        assert by_id[copy_batch["destination"]]["eligible"] is True
        assert by_id[copy_batch["destination"]]["reason"] is None
        assert by_id[copy_batch["blank"]]["eligible"] is False
        assert "blank" in by_id[copy_batch["blank"]]["reason"]
        assert by_id[copy_batch["negative"]]["eligible"] is False
        assert "polarity" in by_id[copy_batch["negative"]]["reason"]

    @pytest.mark.asyncio
    async def test_a_source_with_no_run_previews_without_one(
        self, editor_client, copy_batch, feature_enabled
    ):
        """The dialog still lists the batch so it can say why it cannot copy."""
        response = await editor_client.get(_preview_url(copy_batch["destination"]))

        assert response.status_code == 200
        record = response.json()["data"][0]
        assert record["source_peak_assignment_run_id"] is None
        assert len(record["destinations"]) == 3

    @pytest.mark.asyncio
    async def test_a_guest_may_not_preview_a_copy(
        self, guest_client, copy_batch, feature_enabled
    ):
        """Gated like the launch: this surface only stages a write."""
        response = await guest_client.get(_preview_url(copy_batch["source"]))

        assert response.status_code == 403


class TestLaunchOutcomes:
    """What the launch endpoint decides before it answers."""

    @pytest.mark.asyncio
    async def test_it_answers_with_the_partition_it_will_execute(
        self, editor_client, copy_batch, feature_enabled, launched_copies
    ):
        response = await editor_client.post(_preview_url(copy_batch["source"]))

        assert response.status_code == 202
        record = response.json()["data"][0]
        assert record["admitted"] == [copy_batch["destination"]]
        assert {entry["sample_item_id"] for entry in record["skipped"]} == {
            copy_batch["blank"],
            copy_batch["negative"],
        }
        assert record["source_peak_assignment_run_id"] == copy_batch["source_run_id"]

    @pytest.mark.asyncio
    async def test_the_reported_partition_is_the_one_handed_to_the_task(
        self, editor_client, copy_batch, feature_enabled, launched_copies
    ):
        """Not recomputed behind the response, so the two cannot disagree."""
        response = await editor_client.post(_preview_url(copy_batch["source"]))

        assert response.status_code == 202
        assert len(launched_copies) == 1
        handed = launched_copies[0]["partition"]
        assert handed.admitted == (copy_batch["destination"],)
        assert handed.source_run_id == copy_batch["source_run_id"]

    @pytest.mark.asyncio
    async def test_a_source_with_no_completed_run_is_refused(
        self, editor_client, copy_batch, feature_enabled, launched_copies
    ):
        response = await editor_client.post(_preview_url(copy_batch["destination"]))

        assert response.status_code == 422
        assert "no completed peak assignment run" in response.json()["error"]
        assert launched_copies == []

    @pytest.mark.asyncio
    async def test_a_batch_with_no_eligible_destination_is_refused(
        self,
        editor_client,
        copy_batch,
        feature_enabled,
        launched_copies,
        async_session_factory,
    ):
        """Nothing to do is a 422 naming the skips, not an accepted no-op."""
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleItem).where(
                    SampleItem.sample_item_id == copy_batch["destination"]
                )
            )
            await session.commit()

        response = await editor_client.post(_preview_url(copy_batch["source"]))

        assert response.status_code == 422
        assert "No sample in the batch is eligible" in response.json()["error"]
        assert launched_copies == []

    @pytest.mark.asyncio
    async def test_a_guest_may_not_copy(
        self, guest_client, copy_batch, feature_enabled, launched_copies
    ):
        response = await guest_client.post(_preview_url(copy_batch["source"]))

        assert response.status_code == 403
        assert launched_copies == []

    @pytest.mark.asyncio
    async def test_the_feature_flag_gates_the_copy(
        self, editor_client, copy_batch, monkeypatch, launched_copies
    ):
        monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")

        response = await editor_client.post(_preview_url(copy_batch["source"]))

        assert response.status_code == 403
        assert launched_copies == []


class TestFanOut:
    """What the fan-out publishes onto each destination."""

    @pytest_asyncio.fixture
    async def copied(
        self, copy_batch, feature_enabled, destination_fits, async_session_factory
    ):
        """Run the fan-out with every source formula measurable downstream."""
        destination_fits.update({"C6H12O6": 0.91, "C10H16N2": 0.62})
        result = await copy_assignments_to_batch(
            sample_item_id=copy_batch["source"],
            independent_transaction=False,
        )
        runs = await _runs_of(async_session_factory, copy_batch["destination"])
        return result, runs

    @pytest.mark.asyncio
    async def test_it_publishes_one_completed_run_per_destination(
        self, copied, copy_batch
    ):
        result, runs = copied

        assert result["status"] == "success"
        assert result["data"]["copied_count"] == 1
        assert len(runs) == 1
        assert runs[0].status == "completed"

    @pytest.mark.asyncio
    async def test_the_run_is_attributed_to_the_reserved_copy_engine(self, copied):
        _, runs = copied

        assert runs[0].engine == COPY_ENGINE

    @pytest.mark.asyncio
    async def test_the_run_declares_the_sources_tier_bands(self, copied):
        """Copied tiers mean what the source's tiers meant."""
        _, runs = copied

        assert runs[0].tier_bands == TIER_BANDS

    @pytest.mark.asyncio
    async def test_the_run_discloses_the_copy_manifest(self, copied, copy_batch):
        """The disclosure an import owes, filled with what the copy did."""
        _, runs = copied
        manifest = runs[0].calibration["copy"]

        assert manifest["source_sample_item_id"] == copy_batch["source"]
        assert manifest["source_peak_assignment_run_id"] == copy_batch["source_run_id"]
        assert manifest["mode"] == "seeded_rescore"
        assert manifest["mapping"]["mapped"] == 3
        assert manifest["mapping"]["unassigned_placeholders"] == 1
        assert "mu_source_ppm" in manifest
        assert "mu_destination_ppm" in manifest

    @pytest.mark.asyncio
    async def test_every_destination_peak_gets_exactly_one_row(
        self, copied, copy_batch, async_session_factory
    ):
        """A complete run: anything less silently shrinks the batch view."""
        _, runs = copied
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)

        assert [row.sample_peak_id for row in rows] == [
            peak_id for peak_id, _, _ in DESTINATION_PEAKS
        ]

    @pytest.mark.asyncio
    async def test_the_unexplained_destination_peak_is_unassigned(
        self, copied, async_session_factory
    ):
        _, runs = copied
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        placeholder = next(row for row in rows if row.sample_peak_id == "dst-4")

        assert placeholder.role == "unassigned"
        assert placeholder.tier == "unassigned"
        assert placeholder.assigned_formula is None

    @pytest.mark.asyncio
    async def test_copied_rows_take_the_destinations_peak_identity(
        self, copied, async_session_factory
    ):
        _, runs = copied
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        row = next(row for row in rows if row.sample_peak_id == "dst-1")

        assert row.assigned_formula == "C6H12O6"
        assert row.sample_peak_mz == pytest.approx(181.07050)
        assert row.sample_peak_intensity == pytest.approx(4200.0)

    @pytest.mark.asyncio
    async def test_the_evidence_is_the_destinations_own(
        self, copied, async_session_factory
    ):
        """Not the source's 0.95/1.2 ppm - the numbers this sample supports."""
        _, runs = copied
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        row = next(row for row in rows if row.sample_peak_id == "dst-1")

        assert row.fit_score == pytest.approx(0.91)
        assert row.mz_error_ppm == pytest.approx(0.8)
        assert row.abundance_error == pytest.approx(0.03)

    @pytest.mark.asyncio
    async def test_curation_and_copy_provenance_travel_with_the_row(
        self, copied, copy_batch, async_session_factory
    ):
        _, runs = copied
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        row = next(row for row in rows if row.sample_peak_id == "dst-1")

        assert row.alternatives == [{"assigned_formula": "C7H16O5", "fit_score": 0.4}]
        assert row.provenance["plausibility"] == 0.9
        assert row.provenance["copied_from"] == {
            "sample_item_id": copy_batch["source"],
            "sample_peak_id": "src-1",
            "peak_assignment_id": copy_batch["assignment_ids"]["src-1"],
            "fit_score": 0.95,
        }

    @pytest.mark.asyncio
    async def test_the_isotopologue_owner_link_is_rebuilt_on_the_destination(
        self, copied, async_session_factory
    ):
        """The link must name a row of the new run, not the source's."""
        _, runs = copied
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        by_peak = {row.sample_peak_id: row for row in rows}

        assert by_peak["dst-2"].role == "iso_child"
        assert (
            by_peak["dst-2"].owner_peak_assignment_id
            == by_peak["dst-1"].peak_assignment_id
        )

    @pytest.mark.asyncio
    async def test_the_copy_carries_no_calibrated_confidence(
        self, copied, async_session_factory
    ):
        """The import strips the server-owned keys from a copied row too."""
        _, runs = copied
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        row = next(row for row in rows if row.sample_peak_id == "dst-1")

        assert "p_correct" not in (row.provenance or {})

    @pytest.mark.asyncio
    async def test_each_published_copy_is_folded_into_the_batch(
        self, copied, copy_batch, stub_fold_in
    ):
        assert stub_fold_in == [copy_batch["destination"]]

    @pytest.mark.asyncio
    async def test_ineligible_siblings_are_reported_not_failed(
        self, copied, copy_batch
    ):
        """A blank and a wrong-polarity sample are skips, and say why."""
        result, _ = copied
        outcomes = {
            outcome["sample_item_id"]: outcome for outcome in result["data"]["outcomes"]
        }

        assert result["data"]["failed_count"] == 0
        assert outcomes[copy_batch["blank"]]["status"] == "skipped"
        assert outcomes[copy_batch["negative"]]["status"] == "skipped"
        assert "polarity" in outcomes[copy_batch["negative"]]["reason"]

    @pytest.mark.asyncio
    async def test_the_fan_out_reloads_the_batch(self, copied, copy_batch):
        """The notification data the decorator turns into the reload event."""
        result, _ = copied

        assert result["_notification_data"] == {
            "sample_batch_id": copy_batch["sample_batch_id"]
        }


class TestDestinationHonestTiers:
    """A destination whose data supports a formula less must say so."""

    @pytest.mark.asyncio
    async def test_a_weaker_fit_lands_in_a_lower_tier(
        self, copy_batch, feature_enabled, destination_fits, async_session_factory
    ):
        """The source row is 'assigned' at 0.95; here the data gives 0.62."""
        destination_fits.update({"C6H12O6": 0.62, "C10H16N2": 0.62})

        await copy_assignments_to_batch(
            sample_item_id=copy_batch["source"], independent_transaction=False
        )

        runs = await _runs_of(async_session_factory, copy_batch["destination"])
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        row = next(row for row in rows if row.sample_peak_id == "dst-1")

        assert row.fit_score == pytest.approx(0.62)
        assert row.tier == "candidate"

    @pytest.mark.asyncio
    async def test_a_formula_the_destination_cannot_support_claims_nothing(
        self, copy_batch, feature_enabled, destination_fits, async_session_factory
    ):
        """No measurable fit means no tier claim, not the inherited one."""
        destination_fits.update({"C6H12O6": None, "C10H16N2": 0.9})

        await copy_assignments_to_batch(
            sample_item_id=copy_batch["source"], independent_transaction=False
        )

        runs = await _runs_of(async_session_factory, copy_batch["destination"])
        rows = await _rows_of(async_session_factory, runs[0].peak_assignment_run_id)
        row = next(row for row in rows if row.sample_peak_id == "dst-1")

        assert row.fit_score is None
        assert row.tier == "below_assignability"
        # Still the curated formula: the copy re-measures evidence, it does
        # not re-decide what the peak is.
        assert row.assigned_formula == "C6H12O6"


class TestRepublishing:
    """Copies are append-only, and the newest is what the ledger opens."""

    @pytest.mark.asyncio
    async def test_copying_twice_adds_a_second_run(
        self, copy_batch, feature_enabled, destination_fits, async_session_factory
    ):
        destination_fits.update({"C6H12O6": 0.91, "C10H16N2": 0.62})

        await copy_assignments_to_batch(
            sample_item_id=copy_batch["source"], independent_transaction=False
        )
        await copy_assignments_to_batch(
            sample_item_id=copy_batch["source"], independent_transaction=False
        )

        runs = await _runs_of(async_session_factory, copy_batch["destination"])
        assert len(runs) == 2
        assert {run.status for run in runs} == {"completed"}
        assert len({run.peak_assignment_run_id for run in runs}) == 2


class TestAdmission:
    """A destination already busy is skipped and reported, never failed."""

    @pytest.mark.asyncio
    async def test_a_destination_with_a_run_in_flight_is_skipped(
        self, copy_batch, feature_enabled, destination_fits, async_session_factory
    ):
        destination_fits.update({"C6H12O6": 0.91})
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=gen_id(),
                    sample_item_id=copy_batch["destination"],
                    engine=IN_APP_ENGINE,
                    engine_version="test",
                    status="running",
                    peak_assignment_run_utc_created=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        result = await copy_assignments_to_batch(
            sample_item_id=copy_batch["source"], independent_transaction=False
        )

        outcomes = {
            outcome["sample_item_id"]: outcome for outcome in result["data"]["outcomes"]
        }
        assert outcomes[copy_batch["destination"]]["status"] == "skipped"
        assert result["data"]["failed_count"] == 0

    @pytest.mark.asyncio
    async def test_a_failing_destination_does_not_abort_the_fan_out(
        self, copy_batch, feature_enabled, destination_fits, monkeypatch
    ):
        """One bad destination is reported; the rest of the batch proceeds."""
        destination_fits.update({"C6H12O6": 0.91})

        async def _explode(*args, **kwargs):
            raise RuntimeError("peak file unreadable")

        monkeypatch.setattr(copy_service, "_publish_copied_run", _explode)

        result = await copy_assignments_to_batch(
            sample_item_id=copy_batch["source"], independent_transaction=False
        )

        outcomes = {
            outcome["sample_item_id"]: outcome for outcome in result["data"]["outcomes"]
        }
        assert result["status"] == "failed"
        assert outcomes[copy_batch["destination"]]["status"] == "failed"
        assert "peak file unreadable" in outcomes[copy_batch["destination"]]["reason"]
        # The skips are still reported rather than lost with the failure.
        assert outcomes[copy_batch["blank"]]["status"] == "skipped"


class TestEngineReservation:
    """The copy engine is the server's to stamp, like the in-app one."""

    @pytest.mark.asyncio
    async def test_an_external_import_may_not_claim_the_copy_engine(
        self, editor_client, copy_batch, feature_enabled
    ):
        """Otherwise an import could forge the first-party copy badge."""
        response = await editor_client.post(
            f"/api/peak-assignments/sample/{copy_batch['destination']}/runs/import",
            json={
                "engine": COPY_ENGINE,
                "engine_version": "1.0.0",
                "tier_bands": TIER_BANDS,
                "calibration": {"method": "none"},
                "rows": [
                    {
                        "sample_peak_id": "dst-1",
                        "sample_peak_mz": 181.0709,
                        "sample_peak_intensity": 4200.0,
                        "role": "M0",
                        "assigned_formula": "C6H12O6",
                        "source": "untargeted",
                        "fit_score": 0.9,
                        "tier": "assigned",
                    }
                ],
                "chunk": {"import_id": f"forge-{gen_id()}", "complete": True},
            },
        )

        assert response.status_code == 422
        assert "reserved" in response.json()["error"]
