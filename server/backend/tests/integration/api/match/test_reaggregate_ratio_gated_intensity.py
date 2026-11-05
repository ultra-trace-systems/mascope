"""Integration tests: ``reaggregate_ratio_gated_intensity`` on a stored batch.

The fixture is a batch matched before the ratio gate stopped taking a line's
intensity (#2943): one bromide ion with two near-equal lines, whose 81Br line
missed the ratio tolerance in one sample, so that sample's stored sums hold
the 79Br line alone at every level. The script writes the sums a batch match
of this build stores, and nothing else.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select, update

import mascope_backend.api.controllers.match.aggregate.match_aggregate_controller as mac
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    MatchCollection,
    MatchCompound,
    MatchIon,
    MatchIsotope,
    MatchSample,
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
from mascope_backend.db.scripts import reaggregate_ratio_gated_intensity as script
from mascope_match.params import OrbiMatchParams


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)

#: The 79Br line's intensity, and the 81Br line's in each sample.
INTENSITY_79 = 1000.0
INTENSITY_81 = 1270.0

#: The sample whose 81Br line missed the ratio (+0.3 against the Orbitrap
#: default 0.2), one whose line fits, one whose line misses the ratio but is
#: off its mass as well (12 ppm against 5), and one whose line was paired to
#: no signal at all, the most a ratio can miss by.
MISSED, FITS, OFF_MASS, DARK = "missed", "fits", "off_mass", "dark"
LINES_81 = {
    MISSED: {"abundance": 0.3, "mz": 0.5, "intensity": INTENSITY_81},
    FITS: {"abundance": 0.05, "mz": 0.5, "intensity": INTENSITY_81},
    OFF_MASS: {"abundance": 0.3, "mz": 12.0, "intensity": INTENSITY_81},
    DARK: {"abundance": -1.0, "mz": 0.5, "intensity": 0.0},
}

LEVELS = (MatchIon, MatchCompound, MatchCollection, MatchSample)


@pytest_asyncio.fixture
async def bromide_batch(async_session_factory):
    """A matched batch of three samples, aggregated by this build, with the
    missed sample's sums then set back to what the old gate stored."""
    ids = SimpleNamespace(
        workspace=gen_id(),
        dataset=gen_id(),
        batch=gen_id(),
        file=gen_id(),
        items={name: gen_id() for name in LINES_81},
        mechanism=gen_id(),
        mode=gen_id(),
        collection=gen_id(),
        compound=gen_id(),
        ion=gen_id(),
        iso79=gen_id(),
        iso81=gen_id(),
    )
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.workspace,
                workspace_name=f"Reagg WS {ids.workspace}",
                workspace_description="Ratio gate re-aggregation test workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids.dataset,
                workspace_id=ids.workspace,
                dataset_name=f"Reagg DS {ids.dataset}",
                dataset_description="Ratio gate re-aggregation test dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids.batch,
                dataset_id=ids.dataset,
                sample_batch_name="Re-aggregation Batch",
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
                polarity="-",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"[M+Br]- {ids.mechanism}",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids.mode,
                ionization_mode_name="Re-aggregation Mode",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[ids.mechanism],
            )
        )
        for index, item_id in enumerate(ids.items.values()):
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=ids.batch,
                    sample_file_id=ids.file,
                    ionization_mode_id=ids.mode,
                    sample_item_name=f"Reagg Item {index + 1}",
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={},
                    polarity="-",
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        session.add(
            TargetCollection(
                target_collection_id=ids.collection,
                target_collection_name=f"Reagg Collection {ids.collection}",
                target_collection_description="",
                target_collection_type="TARGETS",
                workspace_id=ids.workspace,
            )
        )
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=ids.collection, sample_batch_id=ids.batch
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=ids.compound,
                target_compound_name=f"Reagg Compound {ids.compound}",
                target_compound_formula="CH2O2",
            )
        )
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=ids.compound, target_collection_id=ids.collection
            )
        )
        session.add(
            TargetIon(
                target_ion_id=ids.ion,
                target_compound_id=ids.compound,
                ionization_mechanism_id=ids.mechanism,
                target_ion_formula="CH2O2Br-",
            )
        )
        for isotope_id, mz, abundance in (
            (ids.iso79, 124.9244, 0.507),
            (ids.iso81, 126.9224, 0.493),
        ):
            session.add(
                TargetIsotope(
                    target_isotope_id=isotope_id,
                    target_ion_id=ids.ion,
                    target_isotope_formula="CH2O2Br-",
                    mz=mz,
                    relative_abundance=abundance,
                    resolution="HIGH",
                )
            )
        for name, item_id in ids.items.items():
            line = LINES_81[name]
            for isotope_id, intensity, abundance_error, mz_error in (
                (ids.iso79, INTENSITY_79, 0.0, 0.4),
                (ids.iso81, line["intensity"], line["abundance"], line["mz"]),
            ):
                session.add(
                    MatchIsotope(
                        match_isotope_id=gen_id(32),
                        target_isotope_id=isotope_id,
                        sample_item_id=item_id,
                        sample_peak_id=gen_id(20),
                        sample_peak_mz=0.0,
                        sample_peak_intensity=intensity,
                        sample_peak_intensity_relative=0.0,
                        sample_peak_tof=0.0,
                        match_abundance_error=abundance_error,
                        match_mz_error=mz_error,
                        match_score=0.9 * (1 - abs(abundance_error)),
                        match_isotope_utc_created=_NOW,
                    )
                )
        await session.commit()

    await mac.aggregate_and_create_matches(sample_batch_id=ids.batch)
    ids.fresh = await sums(async_session_factory, ids.items.values())
    # What the old gate stored for the missed sample: the 79Br line alone
    async with async_session_factory() as session:
        for model in LEVELS:
            await session.execute(
                update(model)
                .where(model.sample_item_id == ids.items[MISSED])
                .values(sample_peak_intensity_sum=INTENSITY_79)
            )
        await session.commit()
    return ids


async def sums(async_session_factory, sample_item_ids) -> dict:
    """``(level, sample) -> (sum, category)`` over every level."""
    found = {}
    async with async_session_factory() as session:
        for model in LEVELS:
            rows = await session.execute(
                select(
                    model.sample_item_id,
                    model.sample_peak_intensity_sum,
                    model.match_category,
                ).where(model.sample_item_id.in_(list(sample_item_ids)))
            )
            for sample_item_id, total, category in rows:
                found[(model.__tablename__, sample_item_id)] = (total, category)
    return found


class TestTheFixture:
    @pytest.mark.asyncio
    async def test_this_build_sums_both_lines_and_scores_the_miss(self, bromide_batch):
        ids = bromide_batch
        both = INTENSITY_79 + INTENSITY_81
        for model in LEVELS:
            table = model.__tablename__
            assert ids.fresh[(table, ids.items[MISSED])][0] == pytest.approx(both)
            assert ids.fresh[(table, ids.items[FITS])][0] == pytest.approx(both)
            assert ids.fresh[(table, ids.items[OFF_MASS])][0] == pytest.approx(
                INTENSITY_79
            )
        # The miss still costs the ion its category
        assert ids.fresh[("match_ion", ids.items[MISSED])][1] == 0
        assert ids.fresh[("match_ion", ids.items[FITS])][1] == 2


class TestReaggregate:
    @pytest.mark.asyncio
    async def test_the_candidates_are_the_samples_with_a_line_beyond_the_tolerance(
        self, bromide_batch
    ):
        ids = bromide_batch
        found = await script.candidates({ids.batch})
        assert set(found) == {ids.batch}
        assert set(found[ids.batch]) == {ids.items[MISSED], ids.items[OFF_MASS]}

    @pytest.mark.asyncio
    async def test_a_dry_run_counts_and_writes_nothing(
        self, bromide_batch, async_session_factory
    ):
        ids = bromide_batch
        before = await sums(async_session_factory, ids.items.values())
        tally = await script.reaggregate(
            dry_run=True, batch_size=500, batches={ids.batch}
        )
        assert tally.samples == 2
        assert dict(tally.changed) == {level.name: 1 for level in script.LEVELS}
        assert sum(tally.written.values()) == 0
        assert tally.categories == 0
        assert tally.factors == [pytest.approx((INTENSITY_79 + INTENSITY_81) / 1000)]
        assert await sums(async_session_factory, ids.items.values()) == before

    @pytest.mark.asyncio
    async def test_a_run_writes_the_sums_a_batch_match_stores(
        self, bromide_batch, async_session_factory, monkeypatch
    ):
        ids = bromide_batch
        locked = []
        acquire = script.acquire_match_write_locks

        async def recording(session, sample_item_ids):
            locked.append(set(sample_item_ids))
            await acquire(session, sample_item_ids)

        monkeypatch.setattr(script, "acquire_match_write_locks", recording)
        tally = await script.reaggregate(
            dry_run=False, batch_size=1, batches={ids.batch}
        )
        assert dict(tally.written) == {level.name: 1 for level in script.LEVELS}
        after = await sums(async_session_factory, ids.items.values())
        assert after.keys() == ids.fresh.keys()
        for key, (total, category) in after.items():
            assert total == pytest.approx(ids.fresh[key][0])
            assert category == ids.fresh[key][1]
        # Every write held its batch's match-write lock, and says when it was
        # made
        assert locked == [{ids.items[MISSED]}] * len(script.LEVELS)
        async with async_session_factory() as session:
            for model in LEVELS:
                modified = getattr(model, f"{model.__tablename__}_utc_modified")
                stamps = dict(
                    (
                        await session.execute(
                            select(model.sample_item_id, modified).where(
                                model.sample_item_id.in_(list(ids.items.values()))
                            )
                        )
                    ).all()
                )
                assert stamps[ids.items[MISSED]] is not None
                assert stamps[ids.items[FITS]] is None

        # A second run finds nothing left to write
        again = await script.reaggregate(
            dry_run=False, batch_size=500, batches={ids.batch}
        )
        assert sum(again.changed.values()) == 0
        assert sum(again.written.values()) == 0

    @pytest.mark.asyncio
    async def test_a_category_is_reported_and_never_written(
        self, bromide_batch, async_session_factory
    ):
        # A row stored by other code than this build's: its category is not
        # the one this build computes. The sum is put right, the category
        # left as it was.
        ids = bromide_batch
        missed = ids.items[MISSED]
        async with async_session_factory() as session:
            await session.execute(
                update(MatchIon)
                .where(MatchIon.sample_item_id == missed)
                .values(match_category=2)
            )
            await session.commit()
        tally = await script.reaggregate(
            dry_run=False, batch_size=500, batches={ids.batch}
        )
        assert tally.categories == 1
        total, category = (await sums(async_session_factory, [missed]))[
            ("match_ion", missed)
        ]
        assert total == pytest.approx(INTENSITY_79 + INTENSITY_81)
        assert category == 2

    @pytest.mark.asyncio
    async def test_a_sum_that_moved_since_it_was_read_is_left_alone(
        self, bromide_batch, async_session_factory, monkeypatch
    ):
        # A match of the sample lands between the read and the write: the
        # sum it stored is of this build, and the script does not write
        # over it.
        ids = bromide_batch
        missed = ids.items[MISSED]
        aggregate = script.aggregate_matches

        async def matched_meanwhile(**kwargs):
            frames = await aggregate(**kwargs)
            async with async_session_factory() as session:
                await session.execute(
                    update(MatchIon)
                    .where(MatchIon.sample_item_id == missed)
                    .values(sample_peak_intensity_sum=1234.0)
                )
                await session.commit()
            return frames

        monkeypatch.setattr(script, "aggregate_matches", matched_meanwhile)
        tally = await script.reaggregate(
            dry_run=False, batch_size=500, batches={ids.batch}
        )
        assert tally.written["match_ions"] == 0
        assert tally.moved["match_ions"] == 1
        assert tally.written["match_samples"] == 1
        total, _ = (await sums(async_session_factory, [missed]))[("match_ion", missed)]
        assert total == 1234.0

    @pytest.mark.asyncio
    async def test_an_ions_own_tolerance_reaches_the_candidates(
        self, bromide_batch, async_session_factory
    ):
        # The ion's own tolerance for its instrument is under the fitting
        # line's 0.05 error: that sample is read too, and its line now misses,
        # which only its category would say. The category is reported, not
        # written, and its sum does not move.
        ids = bromide_batch
        own = OrbiMatchParams(isotope_ratio_tolerance=0.04).model_dump()
        async with async_session_factory() as session:
            await session.execute(
                update(TargetIon)
                .where(TargetIon.target_ion_id == ids.ion)
                .values(filter_params={"test-orbion": own})
            )
            await session.commit()
        found = await script.candidates({ids.batch})
        assert ids.items[FITS] in found[ids.batch]

        tally = await script.reaggregate(
            dry_run=False, batch_size=500, batches={ids.batch}
        )
        assert tally.samples == 3
        assert tally.categories == 1
        fits = (await sums(async_session_factory, [ids.items[FITS]]))[
            ("match_ion", ids.items[FITS])
        ]
        assert fits == ids.fresh[("match_ion", ids.items[FITS])]

    @pytest.mark.asyncio
    async def test_other_batches_are_left_out(self, bromide_batch):
        # The query itself keeps the named batches: nothing filters after it
        assert await script.candidates({"no-such-batch"}) == {}
        assert bromide_batch.batch in await script.candidates()

    @pytest.mark.asyncio
    async def test_unset_the_chunks_are_a_batch_matchs(
        self, bromide_batch, monkeypatch
    ):
        # Without BATCH_SIZE a batch is aggregated in the chunks a batch match
        # of it takes, sized from its target set: two lines a sample here, so
        # a budget of two rows makes chunks of one sample.
        ids = bromide_batch
        monkeypatch.setattr(mac, "BATCH_AGGREGATION_CHUNK_ROW_BUDGET", 2)
        monkeypatch.setattr(mac, "BATCH_AGGREGATION_MIN_CHUNK", 1)
        chunks = []
        aggregate = script.aggregate_matches

        async def recording(**kwargs):
            chunks.append(len(kwargs["sample_item_ids"]))
            return await aggregate(**kwargs)

        monkeypatch.setattr(script, "aggregate_matches", recording)
        # (another test's own ion tolerance, left in the database, can make
        # the fitting sample a candidate too)
        tally = await script.reaggregate(dry_run=True, batches={ids.batch})
        assert tally.samples >= 2
        assert chunks == [1] * tally.samples

        # BATCH_SIZE overrides it
        chunks.clear()
        await script.reaggregate(
            dry_run=True, batch_size=tally.samples, batches={ids.batch}
        )
        assert chunks == [tally.samples]

    @pytest.mark.asyncio
    async def test_the_settings_come_from_the_environment(
        self, bromide_batch, async_session_factory, monkeypatch
    ):
        ids = bromide_batch
        configured = []

        async def configure():
            configured.append(True)

        monkeypatch.setattr(script, "configure_database_engine", configure)
        monkeypatch.setenv("REAGGREGATE_BATCHES", f" {ids.batch} ,")
        monkeypatch.setenv("BATCH_SIZE", "1")
        monkeypatch.setenv("DRY_RUN", "1")
        before = await sums(async_session_factory, ids.items.values())
        await script.run()
        assert configured == [True]
        assert await sums(async_session_factory, ids.items.values()) == before

        monkeypatch.delenv("DRY_RUN")
        # Another batch named: this one is left as it is
        monkeypatch.setenv("REAGGREGATE_BATCHES", "no-such-batch")
        await script.run()
        assert await sums(async_session_factory, ids.items.values()) == before

        monkeypatch.setenv("REAGGREGATE_BATCHES", ids.batch)
        await script.run()
        missed = await sums(async_session_factory, [ids.items[MISSED]])
        assert missed[("match_ion", ids.items[MISSED])][0] == pytest.approx(
            INTENSITY_79 + INTENSITY_81
        )


class TestSettings:
    def test_the_batch_size(self, monkeypatch):
        monkeypatch.delenv("BATCH_SIZE", raising=False)
        assert script._batch_size() is None
        monkeypatch.setenv("BATCH_SIZE", "20")
        assert script._batch_size() == 20
        for bad in ("0", "-3", "many"):
            monkeypatch.setenv("BATCH_SIZE", bad)
            with pytest.raises(script.BadSetting):
                script._batch_size()

    def test_the_batches(self, monkeypatch):
        monkeypatch.delenv("REAGGREGATE_BATCHES", raising=False)
        assert script._batches() is None
        monkeypatch.setenv("REAGGREGATE_BATCHES", "a, b,,")
        assert script._batches() == {"a", "b"}


class TestChangedSums:
    KEY = ("sample_item_id", "target_ion_id")

    def test_a_row_this_would_create_is_not_written(self):
        stored = pd.DataFrame(
            {
                "sample_item_id": ["s"],
                "target_ion_id": ["a"],
                "sample_peak_intensity_sum": [1.0],
            }
        )
        computed = pd.DataFrame(
            {
                "sample_item_id": ["s", "s"],
                "target_ion_id": ["a", "b"],
                "sample_peak_intensity_sum": [2.0, 5.0],
            }
        )
        changes = script.changed_sums(stored, computed, self.KEY)
        assert changes.to_dict("records") == [
            {"sample_item_id": "s", "target_ion_id": "a", "old": 1.0, "new": 2.0}
        ]

    def test_a_sum_that_grew_from_nothing_has_no_factor(self):
        changes = pd.DataFrame({"old": [0.0, 1000.0], "new": [5.0, 2000.0]})
        assert script.growth_factors(changes) == [2.0]


class TestSmallestRatioTolerance:
    def test_the_defaults_without_overrides(self):
        assert script.smallest_ratio_tolerance([]) == pytest.approx(0.15)

    def test_an_ions_own_tolerance_below_them(self):
        assert script.smallest_ratio_tolerance(
            [
                {"orbi-a": {"isotope_ratio_tolerance": 0.05}},
                {"orbi-b": {"mz_tolerance": 3}},
                None,
            ]
        ) == pytest.approx(0.05)
