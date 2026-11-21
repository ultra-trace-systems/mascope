"""How a thin Orbitrap file's pool reads each of its neighbours (step 3.5b).

``envelope_fit.read_pool`` picks the neighbours; the service reads each one
(``service._neighbour_envelope_lines``): its own Stage A first pass at today's
numbers, on its own peaks, mechanisms, channels and pre-passes, composed by the
stage list's resolve and claim stages as the run-less fold composes a sample
(``pipeline.resolve``, ``pipeline.claim``), under the reading
run's config and match parameters, at the purity of its own setup. Never its
pooled curve nor a run of its own, so the pool is a function of the reading
run's inputs and the neighbours' data (decision 32, amended on MAS-842). The
picking is ``test_envelope_fit``'s and the integration suite's.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.match.params.lib import instrument_default_match_params
from mascope_backend.api.new.peak_assignments import envelope_fit, pipeline, service
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import SampleMassAccuracy
from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
from mascope_tools.composition import custom_elements
from mascope_tools.composition.envelope_tolerance import (
    FORM_CURVE,
    EnvelopeLines,
    fit_envelope_tolerance,
)
from mascope_tools.composition.measurement_model import MeasurementModel


_SVC = "mascope_backend.api.new.peak_assignments.service"
_PIPE = "mascope_backend.api.new.peak_assignments.pipeline"

#: The neighbour's own setup carries a labelled reagent at this purity.
NEIGHBOUR_PURITY = 0.973


def _neighbour(**overrides) -> SimpleNamespace:
    """A neighbour's sample view row: an eligible Orbitrap file."""
    return SimpleNamespace(
        **{
            "sample_item_id": "si-n",
            "sample_item_name": "Neighbour",
            "sample_batch_id": "sb-1",
            "filename": "orbi-neighbour.raw",
            "polarity": "-",
            "instrument_function_id": "if-1",
            "mz_calibration": None,
            **overrides,
        }
    )


PEAKS = pd.DataFrame(
    {"sample_peak_id": ["p1", "p2"], "mz": [199.1, 200.1], "intensity": [1e5, 1e4]}
)


def _inputs(purity_seen: list) -> tuple[AsyncMock, AsyncMock]:
    """The stage list's resolve and claim stages, stood in for: the
    neighbour's own inputs."""

    async def resolve(ctx):
        purity_seen.append(custom_elements._LABEL_PURITY.get())
        ctx.mechanism_ids = ["m-own"]
        ctx.searched_mechanisms = [SimpleNamespace(ionization_mechanism_id="m-own")]
        ctx.resolved_profile = SimpleNamespace(
            fallback_sigma_ppm=0.61,
            context=SimpleNamespace(known_window="the neighbour's window"),
        )

    async def claim(ctx):
        ctx.reagent_offset = "the neighbour's reagent offset"
        ctx.reagent_peak_ids = {"p-reagent"}

    return AsyncMock(side_effect=resolve), AsyncMock(side_effect=claim)


def _first_pass(purity_seen: list) -> AsyncMock:
    """``_stage_a_first_pass``, stood in for."""

    async def first_pass(*args, **kwargs):
        purity_seen.append(custom_elements._LABEL_PURITY.get())
        return service._StageAFirstPass(
            rows=[{"role": "M0"}],
            scored=pd.DataFrame({"frame": [1]}),
            matched=pd.DataFrame({"frame": [1]}),
            mass_accuracy=SampleMassAccuracy(sigma_ppm=0.4),
            confidence_calibration=None,
            listed_formulas=frozenset(),
        )

    return AsyncMock(side_effect=first_pass)


class TestANeighbourIsReadByItsOwnFirstPass:
    @pytest.mark.asyncio
    async def test_under_the_reading_run_s_config_and_match_parameters(self):
        config = PeakAssignmentConfig(run_untargeted=False)
        match_params = SimpleNamespace(isotope_abundance_threshold=0.01)
        purity_seen: list = []
        resolve, claim = _inputs(purity_seen)
        lines = EnvelopeLines(np.full(5, 15.0), np.full(5, 0.8))
        neighbour = _neighbour()
        threads: dict = {}

        def on_thread(name, result):
            def run(*args, **kwargs):
                threads[name] = threading.current_thread()
                return result

            return run

        with (
            patch(
                f"{_SVC}.load_sample_peaks", side_effect=on_thread("peaks", PEAKS)
            ) as load,
            patch(
                f"{_SVC}.sample_label_purity",
                AsyncMock(return_value=NEIGHBOUR_PURITY),
            ) as purity,
            patch(f"{_PIPE}.resolve", resolve) as inputs,
            patch(f"{_PIPE}.claim", claim) as claimed,
            patch(f"{_SVC}._stage_a_first_pass", _first_pass(purity_seen)) as first,
            patch(
                f"{_SVC}.envelope_lines",
                MagicMock(side_effect=on_thread("lines", lines)),
            ) as read,
            patch(f"{_SVC}.fit_run_envelope_tolerance", new_callable=AsyncMock) as fit,
        ):
            got = await service._neighbour_envelope_lines(
                neighbour, config, match_params
            )

        assert got is lines
        # Its file and its lines are read off the event loop, which a thin
        # run's six reads would otherwise hold for a minute.
        assert set(threads) == {"peaks", "lines"}
        assert all(t is not threading.main_thread() for t in threads.values())
        load.assert_called_once_with(neighbour)
        # Its own inputs, composed from its own peaks under the run's config
        # and match parameters, the fold's first two stages.
        (composed,) = inputs.await_args.args
        assert (
            composed.sample,
            composed.config,
            composed.peaks_df,
            composed.match_params,
        ) == (neighbour, config, PEAKS, match_params)
        assert composed.path is pipeline.INGEST_FOLD
        assert not composed.strict_instrument
        claimed.assert_awaited_once_with(composed)
        # Its first pass: the run's config and match parameters, its own
        # mechanisms, pre-passes, window, width and offset.
        args, kwargs = first.await_args
        assert args[:5] == (
            neighbour,
            config,
            match_params,
            ["m-own"],
            [SimpleNamespace(ionization_mechanism_id="m-own")],
        )
        assert args[5] == composed.run_id
        assert kwargs == {
            "excluded_peak_ids": {"p-reagent"},
            "fallback_sigma_ppm": 0.61,
            "known_window": "the neighbour's window",
            "reagent_offset": "the neighbour's reagent offset",
        }
        # Never a decoy's inherited tolerance, never a fit: its own lines at
        # today's numbers, so the pool cannot feed on itself.
        fit.assert_not_awaited()
        # Its lines read off that pass as a curve's, at its own class width.
        frame, rows = read.call_args.args
        assert list(frame["frame"]) == [1] and rows == [{"role": "M0"}]
        assert read.call_args.kwargs == {
            # The v1 model its own first pass fitted, at its own class width.
            "model": MeasurementModel.v1(SampleMassAccuracy(sigma_ppm=0.4), 0.61),
            "form": FORM_CURVE,
        }
        # Labelled lines predicted at its own setup's purity, while it is read.
        purity.assert_awaited_once_with("si-n")
        assert purity_seen == [NEIGHBOUR_PURITY, NEIGHBOUR_PURITY]
        assert custom_elements._LABEL_PURITY.get() is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "neighbour",
        [
            _neighbour(instrument_function_id=None),
            _neighbour(mz_calibration={"verified": False}),
        ],
        ids=["blank", "unverified calibration"],
    )
    async def test_one_the_engine_would_not_run_is_refused(self, neighbour):
        with patch(f"{_SVC}.load_sample_peaks") as load:
            got = await service._neighbour_envelope_lines(
                neighbour, PeakAssignmentConfig(), SimpleNamespace()
            )

        assert got is None
        load.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "peaks",
        [NotFoundException("not processed yet"), PEAKS.iloc[:0]],
        ids=["no peak list", "an empty one"],
    )
    async def test_one_with_no_peak_list_is_refused(self, peaks):
        def load(sample):
            if isinstance(peaks, Exception):
                raise peaks
            return peaks

        with (
            patch(f"{_SVC}.load_sample_peaks", side_effect=load),
            patch(f"{_PIPE}.resolve", new_callable=AsyncMock) as inputs,
        ):
            got = await service._neighbour_envelope_lines(
                _neighbour(), PeakAssignmentConfig(), SimpleNamespace()
            )

        assert got is None
        inputs.assert_not_awaited()


class TestStageAReadsItsNeighboursUnderItsOwnInputs:
    """``_stage_a_assignments`` hands the fit a reader bound to the run's own
    config and match parameters: a run's under what it was asked, the run-less
    fold's under the defaults it runs at."""

    @pytest.mark.asyncio
    async def test_the_reader_carries_the_run_s_config_and_match_parameters(self):
        config = PeakAssignmentConfig(run_untargeted=False, max_alternatives=2)
        match_params = SimpleNamespace(isotope_abundance_threshold=0.02)
        first = service._StageAFirstPass(
            rows=[],
            scored=pd.DataFrame(),
            matched=pd.DataFrame(),
            mass_accuracy=SampleMassAccuracy(),
            confidence_calibration=None,
            listed_formulas=frozenset(),
        )
        pooled = RunEnvelopeFit(
            fit_envelope_tolerance(
                FORM_CURVE,
                EnvelopeLines(),
                [EnvelopeLines(np.full(30, 15.0), np.full(30, 0.75))],
            )
        )
        neighbour = _neighbour()
        with (
            patch(
                f"{_SVC}._stage_a_first_pass", AsyncMock(return_value=first)
            ) as first_pass,
            patch(
                f"{_SVC}.fit_run_envelope_tolerance", AsyncMock(return_value=pooled)
            ) as fit,
            patch(
                f"{_SVC}._neighbour_envelope_lines",
                AsyncMock(return_value=EnvelopeLines()),
            ) as read,
        ):
            _rows, _, _, envelope, _, _, _ = await service._stage_a_assignments(
                _neighbour(sample_item_id="si-1"),
                config,
                match_params,
                ["m-1"],
                [],
                "run-1",
                fallback_sigma_ppm=0.6,
                known_window=None,
            )
            await fit.await_args.kwargs["read_neighbour"](neighbour)

        assert envelope is pooled
        # The first pass at today's numbers: no tolerance handed to it.
        assert first_pass.await_args.kwargs["inherited_envelope"] is None
        read.assert_awaited_once_with(neighbour, config, match_params)


@pytest.mark.asyncio
async def test_a_process_reads_one_neighbour_at_a_time():
    """Two runs' reads wait for each other rather than work side by side: each
    is seconds of work that holds the interpreter lock, and side by side they
    only slow each other and the event loop."""
    running, most = 0, 0

    async def read(neighbour, config, match_params):
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.01)
        running -= 1
        return EnvelopeLines()

    with patch(f"{_SVC}._read_neighbour_lines", side_effect=read) as reads:
        await asyncio.gather(
            *(
                service._neighbour_envelope_lines(
                    _neighbour(sample_item_id=f"si-{index}"),
                    PeakAssignmentConfig(),
                    SimpleNamespace(),
                )
                for index in range(3)
            )
        )

    assert (reads.await_count, most) == (3, 1)


def _orbi_match_params():
    """The match parameters a run on an Orbitrap file reads at: a fresh copy
    per call, as each run builds its own."""
    return instrument_default_match_params("orbi-x", instrument_type="orbi")


class _GatedReads:
    """``_read_neighbour_lines``, stood in for: each read is recorded, waits
    for :attr:`release` and gives lines of its own, and notes a cancel."""

    def __init__(self, fail: Exception | None = None):
        self.release = asyncio.Event()
        self.reads: list[str] = []
        self.cancelled: list[str] = []
        self.fail = fail

    async def __call__(self, neighbour, config, match_params):
        self.reads.append(neighbour.sample_item_id)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(neighbour.sample_item_id)
            # A read takes a moment to stop: its worker thread runs on.
            await asyncio.sleep(0.01)
            raise
        if self.fail is not None:
            raise self.fail
        return EnvelopeLines(np.full(5, 15.0), np.full(5, 0.8))


async def _until(condition) -> None:
    """Let the loop run until ``condition()`` holds, for two seconds at most."""
    for _ in range(2000):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("the loop never got there")


def _read(neighbour, config=None, match_params=None) -> asyncio.Task:
    return asyncio.create_task(
        service._neighbour_envelope_lines(
            neighbour,
            config or PeakAssignmentConfig(run_untargeted=False),
            match_params or _orbi_match_params(),
        )
    )


class TestRunsReadingOneNeighbourAtOnceShareTheRead:
    """The thin files of a batch run at once read largely the same neighbours.
    A read in flight is shared by every run that asks for the same neighbour
    under an equal config and equal match parameters: they would compute the
    same lines from the same data at the same moment (MAS-906)."""

    @pytest.mark.asyncio
    async def test_two_runs_at_once_read_it_once(self):
        gate = _GatedReads()
        with patch(f"{_SVC}._read_neighbour_lines", gate):
            # Equal, not the same objects: each run builds its own.
            first, second = _read(_neighbour()), _read(_neighbour())
            await _until(lambda: gate.reads)
            await asyncio.sleep(0)
            gate.release.set()
            got = await asyncio.gather(first, second)

        assert gate.reads == ["si-n"]
        assert got[0] is got[1]
        assert got[0].lines == 5

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "other",
        [
            {"neighbour": _neighbour(sample_item_id="si-m")},
            {"config": PeakAssignmentConfig(run_untargeted=False, max_alternatives=2)},
            {
                "match_params": _orbi_match_params().model_copy(
                    update={"isotope_abundance_threshold": 0.5}
                )
            },
        ],
        ids=["another neighbour", "another config", "other match parameters"],
    )
    async def test_never_another_neighbour_nor_under_other_inputs(self, other):
        gate = _GatedReads()
        with patch(f"{_SVC}._read_neighbour_lines", gate):
            first = _read(_neighbour())
            second = _read(**{"neighbour": _neighbour(), **other})
            await _until(lambda: gate.reads)
            # The second read queues behind the first, in the process's slot.
            gate.release.set()
            await asyncio.gather(first, second)

        assert len(gate.reads) == 2

    @pytest.mark.asyncio
    async def test_a_read_that_has_ended_is_read_afresh(self):
        gate = _GatedReads()
        gate.release.set()
        with patch(f"{_SVC}._read_neighbour_lines", gate):
            first = await _read(_neighbour())
            second = await _read(_neighbour())

        assert gate.reads == ["si-n", "si-n"]
        assert first is not second
        # Nothing of either is kept.
        assert service._neighbour_reads_in_flight[asyncio.get_running_loop()] == {}

    @pytest.mark.asyncio
    async def test_a_run_cancelled_while_it_waits_leaves_the_read_to_the_rest(self):
        gate = _GatedReads()
        with patch(f"{_SVC}._read_neighbour_lines", gate):
            first, second = _read(_neighbour()), _read(_neighbour())
            await _until(lambda: gate.reads)
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            gate.release.set()
            got = await second

        assert got.lines == 5
        assert (gate.reads, gate.cancelled) == (["si-n"], [])

    @pytest.mark.asyncio
    async def test_a_read_no_run_waits_on_any_more_is_stopped(self):
        gate = _GatedReads()
        with patch(f"{_SVC}._read_neighbour_lines", gate):
            first = _read(_neighbour())
            await _until(lambda: gate.reads)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            # A run asking while the stopped read winds down reads afresh: it
            # does not join a read being cancelled.
            second = _read(_neighbour())
            await _until(lambda: len(gate.reads) == 2)
            gate.release.set()
            got = await second

        assert gate.cancelled == ["si-n"]
        assert gate.reads == ["si-n", "si-n"]
        assert got.lines == 5

    @pytest.mark.asyncio
    async def test_a_read_that_fails_fails_every_run_on_it_and_is_not_kept(self):
        gate = _GatedReads(fail=RuntimeError("the peak file went away"))
        with patch(f"{_SVC}._read_neighbour_lines", gate):
            first, second = _read(_neighbour()), _read(_neighbour())
            await _until(lambda: gate.reads)
            await asyncio.sleep(0)
            gate.release.set()
            got = await asyncio.gather(first, second, return_exceptions=True)
            gate.fail = None
            again = await _read(_neighbour())

        assert [str(error) for error in got] == ["the peak file went away"] * 2
        assert gate.reads == ["si-n", "si-n"]
        assert again.lines == 5


@pytest.mark.asyncio
async def test_two_thin_runs_pooling_at_once_read_each_neighbour_once():
    """Two thin files of one batch pooled at once, as a batch launch runs
    them: each neighbour is read once, and each pool is what it would be
    alone."""
    neighbours = [
        _neighbour(sample_item_id=f"si-{index}", datetime_utc=None)
        for index in range(3)
    ]
    reads: list[str] = []

    async def read(neighbour, config, match_params):
        reads.append(neighbour.sample_item_id)
        await asyncio.sleep(0.01)
        index = int(neighbour.sample_item_id.removeprefix("si-"))
        return EnvelopeLines(np.full(5, 15.0), np.full(5, 0.6 + index / 10))

    def thin(name):
        return _neighbour(sample_item_id=name, sample_item_name=name, datetime_utc=None)

    def pool(name):
        return envelope_fit.read_pool(
            thin(name),
            {"polarity": "-"},
            service._neighbour_reader(
                PeakAssignmentConfig(run_untargeted=False), _orbi_match_params()
            ),
        )

    with (
        patch.object(
            envelope_fit, "_batch_candidates", AsyncMock(return_value=neighbours)
        ),
        patch.object(
            envelope_fit,
            "acquisition_settings",
            AsyncMock(return_value={"polarity": "-"}),
        ),
        patch(f"{_SVC}._read_neighbour_lines", side_effect=read),
    ):
        together = await asyncio.gather(pool("si-a"), pool("si-b"))
        reads_together = list(reads)
        alone = await pool("si-a")

    assert reads_together == ["si-0", "si-1", "si-2"]
    assert together[0].record() == together[1].record() == alone.record()
    assert [entry["lines"] for entry in alone.record()["files"]] == [5, 5, 5]
