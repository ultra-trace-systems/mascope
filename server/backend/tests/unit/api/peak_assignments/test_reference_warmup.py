"""Unit tests: the reference lists' warm-up after a worker starts (MAS-1281).

The warm-up builds the reference cache's entries the first samples would read,
through the cache's one way in (``sample_inputs.reference_fine_lines_built``).
Pinned here: an entry it built is the one a run then reads, with one build; a
run arriving while it builds waits for that build and makes no second copy;
a run needing another entry does not wait behind it; a warm-up that raises
leaves the lazy path working; and a worker's start does not wait for it. The
database edges are faked; ``tests/integration/.../test_reference_warmup_samples.py``
reads the samples it warms from a real database.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.controllers.sample.files.process import service as process
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    FileLines,
)
from mascope_backend.api.new.peak_assignments import (
    pipeline,
    reference_warmup,
    sample_inputs,
)
from mascope_backend.app.fast import fast
from mascope_backend.runtime import runtime
from mascope_tools.composition.custom_elements import labelled_purity


_FAST_MODULE = sys.modules["mascope_backend.app.fast"]
_SI = "mascope_backend.api.new.peak_assignments.sample_inputs"
_RW = "mascope_backend.api.new.peak_assignments.reference_warmup"

#: An Orbitrap file with no resolution read: its fine lines as they are.
_FINE = FileLines("HIGH")


def _mechanism(mechanism_id="mech-1", notation="[M-H]-"):
    return SimpleNamespace(
        ionization_mechanism_id=mechanism_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity="-",
    )


def _sample(name="Sample One"):
    return SimpleNamespace(
        sample_item_id=f"id-{name}",
        sample_item_name=name,
        filename="orbi-sample.raw",
        polarity="-",
        datetime_utc=None,
    )


def _session_ctx():
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


_FRAME = pd.DataFrame(
    {
        "target_ion_id": ["ion-1"],
        "target_isotope_id": ["iso-1"],
        "target_isotope_formula": ["C10H15O3-"],
        "mz": [183.1026],
        "relative_abundance": [1.0],
    }
)


class _Build:
    """A stand-in for the expansion: counts its builds, and holds each until
    released when ``gated``."""

    def __init__(self, gated=False, fail=0):
        self.calls: list[tuple] = []
        self.gate = asyncio.Event()
        if not gated:
            self.gate.set()
        self.fail = fail

    async def __call__(self, sample, polarity, licenses, mechanisms, known_window):
        self.calls.append(tuple(m.ionization_mechanism_id for m in mechanisms))
        await self.gate.wait()
        if self.fail:
            self.fail -= 1
            raise RuntimeError("expansion failed")
        return _FRAME.copy()


@pytest.fixture
def cache():
    """A fresh reference cache, one active source, and the expansion faked."""
    sample_inputs._reference_isotope_cache.clear()
    sample_inputs._reference_isotope_build_locks.clear()
    patches = [
        patch(f"{_SI}.async_session", side_effect=lambda: _session_ctx()),
        patch(
            f"{_SI}.known_state_fingerprint",
            new_callable=AsyncMock,
            return_value=(("src-1", "t1"),),
        ),
        patch(f"{_RW}.async_session", side_effect=lambda: _session_ctx()),
        patch(
            f"{_RW}.known_state_fingerprint",
            new_callable=AsyncMock,
            return_value=(("src-1", "t1"),),
        ),
        patch(f"{_RW}.sample_label_purity", new_callable=AsyncMock, return_value=None),
    ]
    for each in patches:
        each.start()
    yield
    for each in patches:
        each.stop()
    sample_inputs._reference_isotope_cache.clear()
    sample_inputs._reference_isotope_build_locks.clear()


def _build(monkeypatch, **kwargs) -> _Build:
    build = _Build(**kwargs)
    monkeypatch.setattr(sample_inputs, "_build_reference_known_isotopes", build)
    return build


def _resolving_to(monkeypatch, searched, given=()):
    """``pipeline.resolve`` faked to resolve every sample to ``searched``, and
    to the secondary channels ``given`` as given channels."""
    resolved: list[str] = []

    async def resolve(ctx):
        resolved.append(ctx.sample.sample_item_name)
        assert ctx.path is pipeline.INGEST_FOLD
        assert ctx.config.run_untargeted is False
        ctx.searched_mechanisms = list(searched)
        ctx.secondary_mechanisms = list(given)
        ctx.resolved_profile = SimpleNamespace(
            context=SimpleNamespace(known_window=None),
            given_channels=tuple(m.ionization_mechanism for m in given),
        )

    monkeypatch.setattr(pipeline, "resolve", resolve)
    return resolved


def _samples(monkeypatch, samples):
    monkeypatch.setattr(
        reference_warmup,
        "_representative_samples",
        AsyncMock(return_value=list(samples)),
    )


async def _run_reads(sample, mechanisms):
    """The reference lists' lines as a run's Stage A reads them."""
    return await sample_inputs._fetch_reference_known_isotopes(
        sample, 0.0, mechanisms, known_window=None, file_lines=_FINE
    )


@pytest.mark.asyncio
async def test_an_entry_the_warm_up_built_is_the_one_a_run_reads(cache, monkeypatch):
    build = _build(monkeypatch)
    mechanisms = [_mechanism()]
    _resolving_to(monkeypatch, mechanisms)
    _samples(monkeypatch, [_sample()])

    assert await reference_warmup.warm_reference_lists() == 1
    warmed = sample_inputs._reference_isotope_cache[
        next(iter(sample_inputs._reference_isotope_cache))
    ]

    frame = await sample_inputs._reference_fine_lines(_sample(), mechanisms, None)
    lines = await _run_reads(_sample("A later arrival"), mechanisms)

    assert build.calls == [("mech-1",)]
    assert frame is warmed
    assert lines["target_isotope_formula"].tolist() == ["C10H15O3-"]
    # A key's lock lives only while its entry is being built.
    assert sample_inputs._reference_isotope_build_locks == {}


@pytest.mark.asyncio
async def test_an_arrival_during_the_warm_up_waits_for_its_build(cache, monkeypatch):
    build = _build(monkeypatch, gated=True)
    mechanisms = [_mechanism()]
    _resolving_to(monkeypatch, mechanisms)
    _samples(monkeypatch, [_sample()])

    warming = asyncio.create_task(reference_warmup.warm_reference_lists())
    while not build.calls:
        await asyncio.sleep(0)
    arrival = asyncio.create_task(
        sample_inputs._reference_fine_lines(_sample("Arrival"), mechanisms, None)
    )
    for _ in range(20):
        await asyncio.sleep(0)
    assert not arrival.done()

    build.gate.set()
    assert await warming == 1
    frame = await arrival

    assert build.calls == [("mech-1",)]
    assert frame is next(iter(sample_inputs._reference_isotope_cache.values()))


@pytest.mark.asyncio
async def test_a_run_of_another_entry_does_not_wait_behind_a_build(cache, monkeypatch):
    """One lock per key: while the warm-up builds one entry, a sample whose
    entry is warm reads it, and one that needs a third starts its build."""
    build = _build(monkeypatch)
    warm = [_mechanism("mech-2", "[M+Cl]-")]
    await sample_inputs._reference_fine_lines(_sample(), warm, None)
    build.gate.clear()

    building = asyncio.create_task(
        sample_inputs._reference_fine_lines(_sample(), [_mechanism()], None)
    )
    while len(build.calls) < 2:
        await asyncio.sleep(0)

    read = await asyncio.wait_for(
        sample_inputs._reference_fine_lines(_sample("Warm"), warm, None), 1
    )
    third = asyncio.create_task(
        sample_inputs._reference_fine_lines(
            _sample("Third"), [_mechanism("mech-3", "[M+Br]-")], None
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)

    assert read is not None
    assert build.calls == [("mech-2",), ("mech-1",), ("mech-3",)]
    assert not building.done()
    build.gate.set()
    await asyncio.gather(building, third)


@pytest.mark.asyncio
async def test_the_warm_up_builds_at_the_samples_labelled_purity(cache, monkeypatch):
    """A run reads labelled lines at its setup's purity (``run_stages``), so
    the warm-up builds there too: a run at that purity reads the warmed entry,
    one at the default builds its own."""
    build = _build(monkeypatch)
    mechanisms = [_mechanism()]
    _resolving_to(monkeypatch, mechanisms)
    _samples(monkeypatch, [_sample()])
    monkeypatch.setattr(
        reference_warmup, "sample_label_purity", AsyncMock(return_value=0.95)
    )

    assert await reference_warmup.warm_reference_lists() == 1
    with labelled_purity(0.95):
        await sample_inputs._reference_fine_lines(_sample(), mechanisms, None)
    assert len(build.calls) == 1
    await sample_inputs._reference_fine_lines(_sample(), mechanisms, None)
    assert len(build.calls) == 2


@pytest.mark.asyncio
async def test_a_failed_build_leaves_the_lazy_path_building(cache, monkeypatch):
    build = _build(monkeypatch, fail=1)
    mechanisms = [_mechanism()]
    _resolving_to(monkeypatch, mechanisms)
    _samples(monkeypatch, [_sample()])

    assert await reference_warmup.warm_reference_lists() == 0
    assert not sample_inputs._reference_isotope_cache

    lines = await _run_reads(_sample("Arrival"), mechanisms)

    assert build.calls == [("mech-1",), ("mech-1",)]
    assert lines["target_isotope_formula"].tolist() == ["C10H15O3-"]


@pytest.mark.asyncio
async def test_a_warm_up_that_raises_logs_and_never_raises(cache, monkeypatch):
    build = _build(monkeypatch)
    monkeypatch.setattr(
        reference_warmup,
        "_representative_samples",
        AsyncMock(side_effect=RuntimeError("database gone")),
    )
    logged = []
    monkeypatch.setattr(
        runtime.logger, "exception", lambda message, *a, **k: logged.append(message)
    )

    assert await reference_warmup.warm_reference_lists() == 0
    assert logged and "warm-up failed" in logged[0]

    await _run_reads(_sample(), [_mechanism()])
    assert build.calls == [("mech-1",)]


@pytest.mark.asyncio
async def test_one_sample_failing_does_not_end_the_rest(cache, monkeypatch):
    build = _build(monkeypatch)
    _resolving_to(monkeypatch, [_mechanism()])
    resolve = pipeline.resolve

    async def failing_first(ctx):
        if ctx.sample.sample_item_name == "Broken":
            raise FileNotFoundError("no peak list")
        await resolve(ctx)

    monkeypatch.setattr(pipeline, "resolve", failing_first)
    _samples(monkeypatch, [_sample("Broken"), _sample("Fine")])

    assert await reference_warmup.warm_reference_lists() == 1
    assert build.calls == [("mech-1",)]


@pytest.mark.asyncio
async def test_the_lists_are_warmed_through_the_judged_mechanisms_too(
    cache, monkeypatch
):
    """A sample a batch decision gives another channel reads the lists through
    it as well (``RunContext.judged_mechanisms``): that entry is warmed too,
    and a list the two share is built once."""
    build = _build(monkeypatch)
    own = _mechanism()
    given = _mechanism("mech-9", "[M+NO3]-")
    _resolving_to(monkeypatch, [own], given=[given])
    _samples(monkeypatch, [_sample()])

    assert await reference_warmup.warm_reference_lists() == 2
    assert build.calls == [("mech-1",), ("mech-1", "mech-9")]


@pytest.mark.asyncio
async def test_each_mode_is_warmed_up_to_the_cache_size(cache, monkeypatch):
    build = _build(monkeypatch)
    _resolving_to(monkeypatch, [])
    count = sample_inputs._REFERENCE_ISOTOPE_CACHE_MAX + 2
    names = [f"Sample {n}" for n in range(count)]
    by_name = {name: [_mechanism(f"mech-{n}")] for n, name in enumerate(names)}

    async def resolve(ctx):
        ctx.searched_mechanisms = by_name[ctx.sample.sample_item_name]
        ctx.secondary_mechanisms = []
        ctx.resolved_profile = SimpleNamespace(
            context=SimpleNamespace(known_window=None), given_channels=()
        )

    monkeypatch.setattr(pipeline, "resolve", resolve)
    _samples(monkeypatch, [_sample(name) for name in names])

    built = await reference_warmup.warm_reference_lists()

    assert built == sample_inputs._REFERENCE_ISOTOPE_CACHE_MAX
    assert len(build.calls) == built
    assert len(sample_inputs._reference_isotope_cache) == built


@pytest.mark.asyncio
async def test_no_active_list_warms_nothing(cache, monkeypatch):
    build = _build(monkeypatch)
    representatives = AsyncMock(return_value=[_sample()])
    monkeypatch.setattr(reference_warmup, "_representative_samples", representatives)
    with patch(f"{_RW}.known_state_fingerprint", new_callable=AsyncMock) as print_:
        print_.return_value = ()
        assert await reference_warmup.warm_reference_lists() == 0
    representatives.assert_not_awaited()
    assert build.calls == []


@pytest.mark.asyncio
async def test_the_warm_up_logs_its_start_and_its_end(cache, monkeypatch):
    _build(monkeypatch)
    _resolving_to(monkeypatch, [_mechanism()])
    _samples(monkeypatch, [_sample()])
    lines = []
    monkeypatch.setattr(
        runtime.logger, "info", lambda message, *a, **k: lines.append(message)
    )

    await reference_warmup.warm_reference_lists()

    warm_lines = [line for line in lines if line.startswith("Reference lists warm-up")]
    assert len(warm_lines) == 2
    assert "started: 1 ionization mode" in warm_lines[0]
    assert "done: 1 entry built in" in warm_lines[1]


@pytest.mark.parametrize(
    ("assignment", "setting", "on"),
    [("1", True, True), ("1", False, False), ("0", True, False)],
)
def test_the_setting_and_peak_assignment_switch_it(
    monkeypatch, assignment, setting, on
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", assignment)
    monkeypatch.setattr(runtime.config, "reference_warmup", setting, raising=False)
    assert reference_warmup.reference_warmup_enabled() is on


async def _nothing(*_args, **_kwargs) -> None:
    return None


@pytest.fixture
def stubbed_start(monkeypatch):
    """A worker's lifespan with its other start and stop tasks stubbed."""
    monkeypatch.setattr(_FAST_MODULE, "init_db", _nothing)
    monkeypatch.setattr(_FAST_MODULE.redis_storage_client, "connect", _nothing)
    monkeypatch.setattr(_FAST_MODULE.redis_storage_client, "disconnect", _nothing)
    monkeypatch.setattr(_FAST_MODULE, "stop_worker_heartbeat", _nothing)
    monkeypatch.setattr(_FAST_MODULE, "stop_sign_out_retries", _nothing)
    monkeypatch.setattr(process, "drain_auto_process_tasks", _nothing)


@pytest.mark.asyncio
async def test_a_worker_starts_without_waiting_for_the_warm_up(
    stubbed_start, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    monkeypatch.setattr(runtime.config, "reference_warmup", True, raising=False)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def warm():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(reference_warmup, "warm_reference_lists", warm)

    # Bounded: a start that waited for the warm-up, or a shutdown that did not
    # cancel it, would otherwise hang here for ever.
    async with asyncio.timeout(5):
        async with _FAST_MODULE.lifespan(fast):
            await asyncio.wait_for(started.wait(), 1)
            task = reference_warmup._task
            assert task is not None and not task.done()
            stopping = asyncio.get_running_loop().time()

    # The shutdown cancels it rather than waiting for it to end.
    assert asyncio.get_running_loop().time() - stopping < 1
    assert cancelled.is_set()
    assert reference_warmup._task is None


@pytest.mark.asyncio
async def test_a_worker_switched_off_starts_no_warm_up(stubbed_start, monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    monkeypatch.setattr(runtime.config, "reference_warmup", False, raising=False)
    warm = AsyncMock()
    monkeypatch.setattr(reference_warmup, "warm_reference_lists", warm)

    async with _FAST_MODULE.lifespan(fast):
        await asyncio.sleep(0)
        assert reference_warmup._task is None

    warm.assert_not_called()
