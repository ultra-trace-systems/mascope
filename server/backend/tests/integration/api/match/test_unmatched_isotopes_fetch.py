"""
Integration tests for ``fetch_sample_unmatched_target_isotopes``
(``api/controllers/target/lib/fetch/target_isotopes_fetch.py``).

The fetch decides which target isotopes still need match computation for a
sample. Stored ``match_isotope`` rows double as "this ion was evaluated"
markers: compute persists every scoring isotope plus one zero-score sentinel
per ion that matched nothing (``select_match_isotopes_to_persist``), so the
rules under test are:

- An ion with NO stored rows is fetched in full (never evaluated, or
  invalidated by deleting its rows).
- An ion with ANY stored row - a real match or a zero-score sentinel - is
  skipped entirely, including its rowless isotopes.
- Deleting an ion's rows (the invalidation paths, e.g. ``update_target_ion``)
  makes the whole ion fetchable again.
- Isotopes below the effective abundance threshold are never fetched.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio

from mascope_backend.api.controllers.match.aggregate.match_aggregate_controller import (
    aggregate_match_isotope_filtered_data,
)
from mascope_backend.api.controllers.match.aggregate.sample.match_aggregate_sample_controller import (
    aggregate_sample_match_ion,
)
from mascope_backend.api.controllers.target.lib.fetch.target_isotopes_fetch import (
    fetch_existing_main_isotope_references,
    fetch_sample_unmatched_target_isotopes,
)
from mascope_backend.api.new.match.records.isotope.service import (
    get_match_isotope_records,
)
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    MatchIsotope,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_match.params import BaseMatchParams


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)

MATCH_PARAMS = BaseMatchParams(
    probable_match_threshold=0.8,
    possible_match_threshold=0.7,
    mz_tolerance=5,
    isotope_ratio_tolerance=0.2,
    peak_min_intensity=0,
    isotope_abundance_threshold=1e-5,
)


@pytest_asyncio.fixture
async def match_chain(async_session_factory):
    """
    A minimal batch -> collection -> compound -> two ions with isotopes chain.

    Ion 1 has isotopes iso1a (main, abundance 1.0), iso1b (0.1) and iso1_sub
    (below the abundance threshold). Ion 2 has iso2a (main) and iso2b.
    Returns ids plus a lightweight object standing in for the sample view row.
    """
    ids = SimpleNamespace(
        workspace=gen_id(),
        dataset=gen_id(),
        batch=gen_id(),
        file=gen_id(),
        item=gen_id(),
        mechanism=gen_id(),
        mode=gen_id(),
        collection=gen_id(),
        compound=gen_id(),
        ion1=gen_id(),
        ion2=gen_id(),
        iso1a=gen_id(),
        iso1b=gen_id(),
        iso1_sub=gen_id(),
        iso2a=gen_id(),
        iso2b=gen_id(),
    )
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.workspace,
                workspace_name=f"Match WS {ids.workspace}",
                workspace_description="Match fetch test workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids.dataset,
                workspace_id=ids.workspace,
                dataset_name=f"Match DS {ids.dataset}",
                dataset_description="Match fetch test dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids.batch,
                dataset_id=ids.dataset,
                sample_batch_name="Match Fetch Batch",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids.file,
                filename=f"test-orbion_{ids.file}.raw",
                instrument="test-orbion",
                datetime=_NOW_NAIVE,
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {ids.mechanism}",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids.mode,
                ionization_mode_name="Match Fetch Mode",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[ids.mechanism],
            )
        )
        session.add(
            SampleItem(
                sample_item_id=ids.item,
                sample_batch_id=ids.batch,
                sample_file_id=ids.file,
                ionization_mode_id=ids.mode,
                sample_item_name="Match Fetch Item",
                sample_item_type="ANALYSIS",
                sample_item_attributes={},
                polarity="+",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=_NOW,
            )
        )
        session.add(
            TargetCollection(
                target_collection_id=ids.collection,
                target_collection_name=f"Match Fetch Collection {ids.collection}",
                workspace_id=ids.workspace,
            )
        )
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=ids.collection,
                sample_batch_id=ids.batch,
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=ids.compound,
                target_compound_name=f"Match Fetch Compound {ids.compound}",
                target_compound_formula="C6H12O6",
            )
        )
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=ids.compound,
                target_collection_id=ids.collection,
            )
        )
        for ion_id in (ids.ion1, ids.ion2):
            session.add(
                TargetIon(
                    target_ion_id=ion_id,
                    target_compound_id=ids.compound,
                    ionization_mechanism_id=ids.mechanism,
                    target_ion_formula="C6H13O6+",
                )
            )
        isotopes = [
            (ids.iso1a, ids.ion1, 181.07, 1.0),
            (ids.iso1b, ids.ion1, 182.07, 0.1),
            (ids.iso1_sub, ids.ion1, 183.07, 1e-7),  # below abundance threshold
            (ids.iso2a, ids.ion2, 203.05, 1.0),
            (ids.iso2b, ids.ion2, 204.05, 0.06),
        ]
        for isotope_id, ion_id, mz, abundance in isotopes:
            session.add(
                TargetIsotope(
                    target_isotope_id=isotope_id,
                    target_ion_id=ion_id,
                    target_isotope_formula="C6H13O6+",
                    mz=mz,
                    relative_abundance=abundance,
                    resolution="HIGH",
                )
            )
        await session.commit()

    ids.sample = SimpleNamespace(
        sample_item_id=ids.item,
        sample_batch_id=ids.batch,
        sample_item_name="Match Fetch Item",
        filename=f"test-orbion_{ids.file}.raw",
        instrument="test-orbion",
        polarity="+",
    )
    return ids


def make_match_isotope_row(
    sample_item_id: str, target_isotope_id: str, match_score: float
) -> MatchIsotope:
    """A stored match isotope row (sentinel when match_score is 0)."""
    return MatchIsotope(
        match_isotope_id=gen_id(32),
        target_isotope_id=target_isotope_id,
        sample_item_id=sample_item_id,
        sample_peak_id="" if match_score == 0 else gen_id(20),
        sample_peak_mz=0.0,
        sample_peak_intensity=0.0,
        sample_peak_intensity_relative=0.0,
        sample_peak_tof=0.0,
        match_abundance_error=0.0,
        match_mz_error=0.0,
        match_score=match_score,
        match_isotope_utc_created=_NOW,
    )


class TestFetchSampleUnmatchedTargetIsotopes:
    @pytest.mark.asyncio
    async def test_unevaluated_ions_fetched_in_full(self, match_chain):
        # No stored rows: both ions' isotopes returned, except the
        # below-threshold isotope which is never fetched.
        df = await fetch_sample_unmatched_target_isotopes(
            match_chain.sample, MATCH_PARAMS
        )

        assert sorted(df["target_isotope_id"]) == sorted(
            [
                match_chain.iso1a,
                match_chain.iso1b,
                match_chain.iso2a,
                match_chain.iso2b,
            ]
        )

    @pytest.mark.asyncio
    async def test_sentinel_row_marks_whole_ion_evaluated(
        self, match_chain, async_session_factory
    ):
        # A zero-score sentinel on ion 1's main isotope excludes ALL of
        # ion 1's isotopes; ion 2 stays fetchable.
        async with async_session_factory() as session:
            session.add(
                make_match_isotope_row(match_chain.item, match_chain.iso1a, 0.0)
            )
            await session.commit()

        df = await fetch_sample_unmatched_target_isotopes(
            match_chain.sample, MATCH_PARAMS
        )

        assert set(df["target_ion_id"]) == {match_chain.ion2}
        assert sorted(df["target_isotope_id"]) == sorted(
            [match_chain.iso2a, match_chain.iso2b]
        )

    @pytest.mark.asyncio
    async def test_scoring_row_excludes_ions_rowless_isotopes(
        self, match_chain, async_session_factory
    ):
        # A real match on one isotope marks the ion evaluated: its
        # zero-score (unstored) isotopes are not re-fetched on refresh.
        async with async_session_factory() as session:
            session.add(
                make_match_isotope_row(match_chain.item, match_chain.iso2a, 0.9)
            )
            await session.commit()

        df = await fetch_sample_unmatched_target_isotopes(
            match_chain.sample, MATCH_PARAMS
        )

        assert set(df["target_ion_id"]) == {match_chain.ion1}

    @pytest.mark.asyncio
    async def test_deleting_ion_rows_makes_it_fetchable_again(
        self, match_chain, async_session_factory
    ):
        # Invalidation paths delete an ion's rows to force recomputation
        # (e.g. update_target_ion after a filter_params change).
        async with async_session_factory() as session:
            sentinel = make_match_isotope_row(match_chain.item, match_chain.iso1a, 0.0)
            session.add(sentinel)
            await session.commit()

        df = await fetch_sample_unmatched_target_isotopes(
            match_chain.sample, MATCH_PARAMS
        )
        assert set(df["target_ion_id"]) == {match_chain.ion2}

        async with async_session_factory() as session:
            await session.delete(
                await session.get(MatchIsotope, sentinel.match_isotope_id)
            )
            await session.commit()

        df = await fetch_sample_unmatched_target_isotopes(
            match_chain.sample, MATCH_PARAMS
        )
        assert set(df["target_ion_id"]) == {match_chain.ion1, match_chain.ion2}


class TestStoredAnchorAndScanCount:
    """What an Orbitrap line's ratio is read against, and over, round-trips.

    A line computed for an ion matched in an earlier run reads its ratio over
    the stored anchor's per-scan series, which it finds by the anchor's peak;
    the scans the ratio was read over are stored and read back on the
    Match tab's route.
    """

    @pytest.mark.asyncio
    async def test_the_reference_carries_the_anchors_peak(
        self, match_chain, async_session_factory
    ):
        anchor = make_match_isotope_row(match_chain.item, match_chain.iso1a, 0.9)
        anchor.sample_peak_mz = 181.07
        async with async_session_factory() as session:
            session.add(anchor)
            await session.commit()

        df = await fetch_existing_main_isotope_references(
            match_chain.item, [match_chain.ion1]
        )

        assert df.to_dict("records") == [
            {
                "target_ion_id": match_chain.ion1,
                "sample_peak_intensity": 0.0,
                "relative_abundance": 1.0,
                "sample_peak_id": anchor.sample_peak_id,
                "sample_peak_mz": 181.07,
            }
        ]

    @pytest.mark.asyncio
    async def test_the_scan_count_is_stored_and_read_back(
        self, match_chain, async_session_factory
    ):
        line = make_match_isotope_row(match_chain.item, match_chain.iso1b, 0.9)
        line.ratio_scan_count = 37
        async with async_session_factory() as session:
            session.add(line)
            await session.commit()

        df = await aggregate_match_isotope_filtered_data(
            sample_item_id=match_chain.item,
            target_ion_id=match_chain.ion1,
            match_params=MATCH_PARAMS,
        )

        counts = df.set_index("target_isotope_id")["ratio_scan_count"]
        assert counts[match_chain.iso1b] == 37
        # The main isotope has no stored row: rebuilt unmatched, nothing read
        assert pd.isna(counts[match_chain.iso1a])

    @pytest.mark.asyncio
    async def test_a_sample_with_no_stored_rows_still_has_the_column(self, match_chain):
        df = await aggregate_match_isotope_filtered_data(
            sample_item_id=match_chain.item,
            target_ion_id=match_chain.ion1,
            match_params=MATCH_PARAMS,
        )

        assert df["ratio_scan_count"].isna().all()

    @pytest.mark.asyncio
    async def test_the_match_tabs_ion_route_returns_the_scan_count(
        self, match_chain, async_session_factory
    ):
        line = make_match_isotope_row(match_chain.item, match_chain.iso1b, 0.9)
        line.ratio_scan_count = 37
        async with async_session_factory() as session:
            session.add(line)
            await session.commit()

        result = await aggregate_sample_match_ion(
            sample_item_id=match_chain.item,
            target_ion_id=match_chain.ion1,
            target_collection_id=match_chain.collection,
            match_params=MATCH_PARAMS,
        )

        counts = {
            isotope["target_isotope_id"]: isotope["match"]["ratio_scan_count"]
            for isotope in result["data"]["match_isotopes"]
        }
        assert counts == {match_chain.iso1a: None, match_chain.iso1b: 37}

    @pytest.mark.asyncio
    async def test_the_isotope_records_route_returns_the_scan_count(
        self, match_chain, async_session_factory
    ):
        line = make_match_isotope_row(match_chain.item, match_chain.iso1b, 0.9)
        line.ratio_scan_count = 37
        async with async_session_factory() as session:
            session.add(line)
            await session.commit()

        records = await get_match_isotope_records(
            sample_item_id=match_chain.item, target_ion_id=match_chain.ion1
        )

        counts = {
            record["target_isotope_id"]: record["match"]["ratio_scan_count"]
            for record in records["data"]
        }
        assert counts == {match_chain.iso1a: None, match_chain.iso1b: 37}

    @pytest.mark.asyncio
    async def test_a_batch_record_has_no_scan_count(self, match_chain):
        records = await get_match_isotope_records(
            sample_batch_id=match_chain.batch, target_ion_id=match_chain.ion1
        )

        assert records["data"]
        assert all(
            record["match"]["ratio_scan_count"] is None for record in records["data"]
        )
