"""How a thin Orbitrap file's pool reads each of its neighbours (step 3.5b).

``envelope_fit.read_pool`` picks the neighbours; the service reads each one
(``service._neighbour_envelope_lines``): its own Stage A first pass at today's
numbers, on its own peaks, mechanisms, channels and pre-passes, composed as the
run-less fold composes a sample (``service._stage_a_inputs``), under the reading
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
from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import SampleMassAccuracy
from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
from mascope_tools.composition import custom_elements
from mascope_tools.composition.envelope_tolerance import (
    FORM_CURVE,
    EnvelopeLines,
    fit_envelope_tolerance,
)


_SVC = "mascope_backend.api.new.peak_assignments.service"

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


def _inputs(purity_seen: list) -> AsyncMock:
    """``_stage_a_inputs``, stood in for: the neighbour's own inputs."""

    async def compose(sample, config, peaks_df, run_id):
        purity_seen.append(custom_elements._LABEL_PURITY.get())
        return service._StageAInputs(
            mechanism_ids=["m-own"],
            searched_mechanisms=[SimpleNamespace(ionization_mechanism_id="m-own")],
            resolved_profile=SimpleNamespace(
                fallback_sigma_ppm=0.61,
                context=SimpleNamespace(known_window="the neighbour's window"),
            ),
            resolution=None,
            reagent=[],
            reagent_offset="the neighbour's reagent offset",
            artifact=[],
            claimed_peak_ids={"p-reagent"},
        )

    return AsyncMock(side_effect=compose)


def _first_pass(purity_seen: list) -> AsyncMock:
    """``_stage_a_first_pass``, stood in for."""

    async def first_pass(*args, **kwargs):
        purity_seen.append(custom_elements._LABEL_PURITY.get())
        return service._StageAFirstPass(
            rows=[{"role": "M0"}],
            scored=pd.DataFrame({"frame": [1]}),
            gated=pd.DataFrame({"frame": [1]}),
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
            patch(f"{_SVC}._stage_a_inputs", _inputs(purity_seen)) as inputs,
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
        # Its own inputs, composed from its own peaks under the run's config.
        composed = inputs.await_args.args
        assert composed[:3] == (neighbour, config, PEAKS)
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
        assert args[5] == composed[3]
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
            "mass_accuracy": SampleMassAccuracy(sigma_ppm=0.4),
            "fallback_sigma_ppm": 0.61,
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
            patch(f"{_SVC}._stage_a_inputs", new_callable=AsyncMock) as inputs,
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
            gated=pd.DataFrame(),
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
            _rows, _, _, envelope, _ = await service._stage_a_assignments(
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
