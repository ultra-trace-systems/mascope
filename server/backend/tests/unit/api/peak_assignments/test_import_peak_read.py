"""
Unit tests for the peak read an import validates against.

The integration suite stubs :func:`_load_peak_ids` wholesale - the fixture
samples carry a zarr filename with no file behind it - so nothing there
exercises the real call into ``extract_peaks``. A signature change would leave
that whole suite green while every real import failed on its first chunk of
rows. These cover the function body itself, and the per-run cache in front of
it.
"""

import inspect
from types import SimpleNamespace

import pytest

from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_backend.api.new.peak_assignments import import_service


@pytest.fixture
def sample():
    """The attributes the peak read takes off a sample."""
    return SimpleNamespace(filename="import-test.zarr", polarity="+", t0=0.0, t1=60.0)


@pytest.fixture(autouse=True)
def clear_cache():
    """Keep the module-level per-run cache from leaking between tests."""
    import_service._PEAK_IDS_BY_RUN.clear()
    yield
    import_service._PEAK_IDS_BY_RUN.clear()


class TestTheRealPeakRead:
    """What the integration suite's stub hides."""

    @pytest.mark.asyncio
    async def test_the_call_matches_the_real_extract_peaks_signature(
        self, monkeypatch, sample
    ):
        """The guard the stub cannot give: a signature change fails here.

        Records what `_load_peak_ids` passes, then binds it against the *real*
        `extract_peaks` signature - so a renamed or reordered parameter is a
        failure rather than a green suite and a 500 on every import.
        """
        recorded = {}

        def _fake(*args, **kwargs):
            recorded["args"] = args
            recorded["kwargs"] = kwargs
            return SimpleNamespace(peak_ids=[1, 2])

        monkeypatch.setattr(import_service, "extract_peaks", _fake)

        await import_service._load_peak_ids(sample)

        inspect.signature(extract_peaks).bind(*recorded["args"], **recorded["kwargs"])

    @pytest.mark.asyncio
    async def test_the_aggregations_the_id_read_does_not_need_are_off(
        self, monkeypatch, sample
    ):
        """Areas, heights and averaging each open the raw source per call."""
        recorded = {}

        def _fake(*args, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(peak_ids=[])

        monkeypatch.setattr(import_service, "extract_peaks", _fake)

        await import_service._load_peak_ids(sample)

        assert recorded == {"areas": False, "heights": False, "average": False}

    @pytest.mark.asyncio
    async def test_peak_ids_come_back_as_strings(self, monkeypatch, sample):
        """The payload's sample_peak_id is a string; the file's ids may not be."""
        monkeypatch.setattr(
            import_service,
            "extract_peaks",
            lambda *a, **k: SimpleNamespace(peak_ids=[1, 2, 3]),
        )

        assert await import_service._load_peak_ids(sample) == {"1", "2", "3"}


class TestThePerRunCache:
    """One import validates every chunk against the same peak set.

    Re-reading the file per chunk is tens of blocking reads for one dense
    ledger, each inside the advisory claim. Keyed by run so an entry cannot
    outlive the import and go stale against a re-processed sample.
    """

    @pytest.mark.asyncio
    async def test_the_first_read_is_cached_for_the_rest_of_the_run(
        self, monkeypatch, sample
    ):
        reads = []

        async def _read(_sample):
            reads.append(1)
            return {"p1", "p2"}

        monkeypatch.setattr(import_service, "_load_peak_ids", _read)

        first = await import_service._known_peak_ids(sample, "run-1")
        second = await import_service._known_peak_ids(sample, "run-1")

        assert first == second == {"p1", "p2"}
        assert len(reads) == 1

    @pytest.mark.asyncio
    async def test_a_create_has_no_run_id_yet_and_is_not_cached(
        self, monkeypatch, sample
    ):
        """The first request reads; the run it opens is what holds the set."""
        reads = []

        async def _read(_sample):
            reads.append(1)
            return {"p1"}

        monkeypatch.setattr(import_service, "_load_peak_ids", _read)

        await import_service._known_peak_ids(sample, None)
        await import_service._known_peak_ids(sample, None)

        assert len(reads) == 2
        assert import_service._PEAK_IDS_BY_RUN == {}

    @pytest.mark.asyncio
    async def test_a_finished_run_releases_its_entry(self, monkeypatch, sample):
        """Or a worker accumulates peak sets for runs that are long gone."""

        async def _read(_sample):
            return {"p1"}

        monkeypatch.setattr(import_service, "_load_peak_ids", _read)

        await import_service._known_peak_ids(sample, "run-1")
        assert "run-1" in import_service._PEAK_IDS_BY_RUN

        import_service._release_peak_ids("run-1")

        assert import_service._PEAK_IDS_BY_RUN == {}

    def test_the_cache_is_bounded(self):
        """Bounded, so a worker that never sees a run finish cannot grow."""
        for index in range(import_service._PEAK_ID_CACHE_MAX_RUNS + 5):
            import_service._cache_peak_ids(f"run-{index}", {"p1"})

        assert (
            len(import_service._PEAK_IDS_BY_RUN)
            == import_service._PEAK_ID_CACHE_MAX_RUNS
        )
        # The oldest went first.
        assert "run-0" not in import_service._PEAK_IDS_BY_RUN
