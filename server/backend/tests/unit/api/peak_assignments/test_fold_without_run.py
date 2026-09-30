"""Unit tests for the run-less ingest fold.

``fold_sample_peaks_without_run`` is what the ingest hook calls under
``peak_assignment_ingest_ledger = "batch"``: Stage A in memory, the unexplained
peaks as placeholders, and the whole handed to the batch fold with nothing
written to ``peak_assignment``. Everything it reads is patched, so these pin
the contract between the hook and the fold rather than the engine.
"""

from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.engine import SampleMassAccuracy
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id


_SVC = "mascope_backend.api.new.peak_assignments.service"
_CTL = "mascope_backend.api.new.peak_assignments.batch_peaks_controller"


def _stage_a_row(sample_item_id: str, run_id: str) -> dict:
    """One row as Stage A shapes it for the bulk insert it will never see."""
    return {
        "peak_assignment_id": "a" * 32,
        "peak_assignment_run_id": run_id,
        "sample_item_id": sample_item_id,
        "sample_peak_id": "p1",
        "sample_peak_mz": 181.0707,
        "sample_peak_intensity": 5000.0,
        "sample_peak_tof": None,
        "role": "M0",
        "assigned_formula": "C6H12O6",
        "ion_formula": "C6H13O6+",
        "ionization_mechanism_id": None,
        "isotope_label": "M0",
        "isotope_formula": None,
        "source": "database",
        "fit_score": 0.95,
        "mz_error_ppm": 1.0,
        "abundance_error": 0.0,
        "tier": "assigned",
        "target_compound_id": None,
        "target_ion_id": None,
        "owner_peak_assignment_id": None,
        "alternatives": None,
        "provenance": {"p_correct": 0.9},
    }


def _claim(acquired: bool):
    """Stand in for the cross-worker advisory claim, granted or not."""

    @asynccontextmanager
    async def claim(kind, resource_id):
        yield acquired

    return claim


def _patched(
    ineligible: str | None = None,
    *,
    acquired: bool = True,
    in_flight: str | None = None,
):
    """Patch everything the fold reads; return the stack and its mocks."""
    stack = ExitStack()
    sample = SimpleNamespace(
        sample_item_id="si-1", sample_item_name="S1", filename="f.zarr", polarity="+"
    )
    mocks = {
        # The fold takes the same admission an explicit run does, so both halves
        # of it are patched here rather than reaching the database.
        "claim": stack.enter_context(
            patch(f"{_SVC}.assignment_claim", _claim(acquired))
        ),
        "in_flight": stack.enter_context(
            patch(
                f"{_SVC}.in_flight_run_id",
                new_callable=AsyncMock,
                return_value=in_flight,
            )
        ),
        "fetch": stack.enter_context(
            patch(f"{_SVC}.fetch_sample", new_callable=AsyncMock, return_value=sample)
        ),
        "ineligible": stack.enter_context(
            patch(f"{_SVC}.ineligible_reason", return_value=ineligible)
        ),
        "params": stack.enter_context(
            patch(
                f"{_SVC}.default_match_params",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(isotope_abundance_threshold=0.01),
            )
        ),
        "peaks": stack.enter_context(
            patch(
                f"{_SVC}.load_sample_peaks",
                return_value=pd.DataFrame(
                    {
                        "sample_peak_id": ["p1", "p2"],
                        "mz": [181.0707, 250.1],
                        "intensity": [5000.0, 300.0],
                    }
                ),
            )
        ),
        "mechanisms": stack.enter_context(
            patch(
                f"{_SVC}.fetch_sample_mechanisms",
                new_callable=AsyncMock,
                return_value=(["m-h"], []),
            )
        ),
        # The deployment's rows for the profile's secondary channels, which the
        # nitrogen check reads through as a run does. None unless a test says.
        "secondary": stack.enter_context(
            patch(
                f"{_SVC}.fetch_mechanisms_by_notation",
                new_callable=AsyncMock,
                return_value=[],
            )
        ),
        "stage_a": stack.enter_context(
            patch(f"{_SVC}._stage_a_assignments", new_callable=AsyncMock)
        ),
        # The file's resolving power, which the gate reads a line's neighbours
        # in. None unless a test says: the instrument functions live in the
        # database.
        "resolution": stack.enter_context(
            patch(f"{_SVC}._resolution_of", new_callable=AsyncMock, return_value=None)
        ),
        "fold": stack.enter_context(
            patch(
                f"{_CTL}.fold_sample_into_batch_peaks",
                new_callable=AsyncMock,
                return_value="batch-1",
            )
        ),
    }
    mocks["stage_a"].return_value = (
        [_stage_a_row("si-1", fold_run_id("si-1"))],
        None,
        SampleMassAccuracy(),
    )
    return stack, mocks


@pytest.mark.asyncio
async def test_folds_stage_a_and_the_placeholders_without_persisting():
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    stack, mocks = _patched()
    with stack:
        assert await fold_sample_peaks_without_run("si-1") == "batch-1"

    fold = mocks["fold"]
    fold.assert_awaited_once()
    args, kwargs = fold.call_args
    assert args == ("si-1",)
    # Nothing was written to peak_assignment, and the fold is told so.
    assert kwargs["persisted"] is False
    rows = kwargs["rows"]
    assert [row.sample_peak_id for row in rows] == ["p1", "p2"]
    assert rows[0].assigned_formula == "C6H12O6"
    assert rows[0].provenance["p_correct"] == 0.9
    # The unexplained peak is a placeholder, as it is in a run's ledger.
    assert rows[1].role == "unassigned" and rows[1].assigned_formula is None
    assert rows[1].sample_peak_intensity == 300.0
    # Every row is stamped with the derived run's id: the run it will never be.
    assert {row.peak_assignment_run_id for row in rows} == {fold_run_id("si-1")}

    # Stage A ran database-first, with the same id to stamp its rows with.
    stage_a = mocks["stage_a"]
    stage_a.assert_awaited_once()
    assert stage_a.call_args.args[1].run_untargeted is False
    assert stage_a.call_args.args[5] == fold_run_id("si-1")


@pytest.mark.asyncio
async def test_stage_a_is_told_where_the_reagent_lines_put_the_axis():
    """The fold's Stage A falls back to the reagent lines' offset as a run's does.

    Otherwise a sample whose library is too thin to fit an offset would be
    scored at zero on this ledger and at its reagent lines' offset in the run
    that later replaces it.
    """
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )
    from mascope_tools.composition.reagents import reagent_library

    ladder = {cluster.label: cluster.mz for cluster in reagent_library("UR")}
    low = 1.0 - 1.3e-6
    labels = ("[CH4N2O+H]+", "[(CH4N2O)2+H]+", "[(CH4N2O)3+H]+")
    stack, mocks = _patched()
    # An Orbitrap, whose scoring width 1.3 ppm is beyond.
    stack.enter_context(patch(f"{_SVC}.get_instrument_type", return_value="orbi"))
    mocks["peaks"].return_value = pd.DataFrame(
        {
            "sample_peak_id": ["p1", "r1", "r2", "r3"],
            "mz": [181.0707] + [ladder[label] * low for label in labels],
            "intensity": [5000.0, 2.0e6, 3.0e6, 1.0e5],
        }
    )
    mocks["mechanisms"].return_value = (
        ["m-h", "m-urea"],
        [
            SimpleNamespace(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism=notation,
                ionization_mechanism_polarity="+",
            )
            for mechanism_id, notation in (
                ("m-h", "[M+H]+"),
                ("m-urea", "[M+CH4N2O+H]+"),
            )
        ],
    )
    with stack:
        assert await fold_sample_peaks_without_run("si-1") == "batch-1"

    offset = mocks["stage_a"].call_args.kwargs["reagent_offset"]
    assert (offset.lines, offset.beyond_width) == (3, True)
    assert offset.mu_ppm == pytest.approx(-1.3, abs=1e-6)


@pytest.mark.asyncio
async def test_the_fold_gates_its_stage_a_rows_as_a_run_does():
    """A Stage A row off calibration is capped on this ledger too.

    Every commit here is a Stage A one, and the gate caps a row off calibration
    in a run unless an isotopologue tracks it, whether the target library or a
    reference mirror won it. A fold that skipped the gate would hold those rows
    at a tier the run takes from them.
    """
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    run_id = fold_run_id("si-1")

    def stage_a_row(row_id, ppm, compound):
        return _stage_a_row("si-1", run_id) | {
            "peak_assignment_id": row_id,
            "sample_peak_id": f"peak-{row_id}",
            "mz_error_ppm": ppm,
            "target_compound_id": compound,
        }

    rows = [
        stage_a_row(f"anchor-{i}", 0.1 if i % 2 else -0.1, f"compound-{i}")
        for i in range(12)
    ] + [
        stage_a_row("library", 5.0, "compound-library"),
        stage_a_row("seed", 5.0, None),
    ]

    stack, mocks = _patched()
    mocks["stage_a"].return_value = (rows, None, SampleMassAccuracy(0.0, 0.1, 12))
    with stack:
        assert await fold_sample_peaks_without_run("si-1") == "batch-1"

    folded = {row.sample_peak_id: row for row in mocks["fold"].call_args.kwargs["rows"]}
    assert folded["peak-library"].tier == "below_assignability"
    assert folded["peak-seed"].tier == "below_assignability"
    assert folded["peak-seed"].assigned_formula == "C6H12O6"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "noise, neighbour, tier",
    [
        # Faint and alone: a 1.3 ppm miss is within what its noise explains.
        (3.0, False, "candidate"),
        # Bright and alone: the same miss is a coincidence, 2.5 widths from the
        # centre, which a run holds at candidate as well.
        (300.0, False, "candidate"),
        # Faint, beside a peak as tall, 6 ppm off: twelve widths from the
        # centre, and its neighbour's push explains it where its noise alone
        # would not, so it is held at candidate rather than dropped below.
        (3.0, True, "candidate"),
    ],
)
async def test_the_fold_reads_an_isotopologue_s_line_as_a_run_does(
    noise, neighbour, tier
):
    """An isotopologue that misses its parent is held on this ledger too, and
    the gate reads its line off the same spectrum and resolution a run does."""
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    run_id = fold_run_id("si-1")
    miss = 6.0 if neighbour else 1.3

    def stage_a_row(row_id, ppm, *, mz, role="M0", owner=None):
        return _stage_a_row("si-1", run_id) | {
            "peak_assignment_id": row_id,
            "sample_peak_id": f"peak-{row_id}",
            "sample_peak_mz": mz,
            "mz_error_ppm": ppm,
            "target_compound_id": f"compound-{row_id}" if role == "M0" else "c-m0",
            "role": role,
            "owner_peak_assignment_id": owner,
        }

    rows = [
        stage_a_row(f"anchor-{i}", 0.1 if i % 2 else -0.1, mz=300.0 + i)
        for i in range(12)
    ] + [
        stage_a_row("m0", 0.0, mz=181.0707),
        stage_a_row("line", miss, mz=182.0741, role="iso_child", owner="m0"),
    ]
    peaks = {
        "sample_peak_id": [row["sample_peak_id"] for row in rows],
        "mz": [row["sample_peak_mz"] for row in rows],
        "intensity": [5000.0] * 13 + [300.0],
        "signal_to_noise": [500.0] * 13 + [noise],
    }
    if neighbour:
        peaks["sample_peak_id"].append("beside")
        peaks["mz"].append(182.0741 * (1 + 30e-6))
        peaks["intensity"].append(300.0)
        peaks["signal_to_noise"].append(3.0)

    stack, mocks = _patched()
    # An Orbitrap, whose lines track their parents within 0.9 ppm.
    stack.enter_context(patch(f"{_SVC}.get_instrument_type", return_value="orbi"))
    mocks["stage_a"].return_value = (rows, None, SampleMassAccuracy(0.0, 0.1, 12))
    mocks["peaks"].return_value = pd.DataFrame(peaks)
    # Twenty-ppm lines, so the neighbour 30 ppm off sits 1.5 of them away and
    # pushes by a quarter width, 5 ppm.
    mocks["resolution"].return_value = lambda mz: 50_000.0
    with stack:
        assert await fold_sample_peaks_without_run("si-1") == "batch-1"

    mocks["resolution"].assert_awaited_once()
    folded = {row.sample_peak_id: row for row in mocks["fold"].call_args.kwargs["rows"]}
    line = folded["peak-line"]
    assert line.tier == tier
    expected = "in_doubt" if noise < 10 else "untracked"
    assert line.provenance["mass_gate"]["tracking"] == expected
    assert folded["peak-m0"].tier == "assigned"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ammonium_shows, tier", [(True, "candidate"), (False, "assigned")]
)
async def test_the_fold_asks_a_mirror_rows_nitrogen_count_as_a_run_does(
    ammonium_shows, tier
):
    """A reference-list row whose ion reads as well another way is capped here too.

    Dimethylformamide through [M+H]+ is acrolein through the ammonium adduct, the
    uronium profile's secondary channel. Where the spectrum shows that channel's
    carrier a run reads the row through it and caps it at candidate, so the
    fold does as well. Where it does not, no run reads that channel and the row
    keeps its tier.
    """
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    dimethylformamide = _stage_a_row("si-1", fold_run_id("si-1")) | {
        "sample_peak_mz": 74.06004,
        "assigned_formula": "C3H7N1O1",
        "ion_formula": "C3H8N1O1+",
        "ionization_mechanism_id": "m-h",
    }
    peaks = {"sample_peak_id": ["p1"], "mz": [74.06004], "intensity": [5000.0]}
    if ammonium_shows:
        # The urea-ammonium cluster the channel is detected by.
        peaks["sample_peak_id"].append("p2")
        peaks["mz"].append(78.06619)
        peaks["intensity"].append(1000.0)

    def mechanism(mechanism_id, notation):
        return SimpleNamespace(
            ionization_mechanism_id=mechanism_id,
            ionization_mechanism=notation,
            ionization_mechanism_polarity="+",
        )

    stack, mocks = _patched()
    mocks["stage_a"].return_value = ([dimethylformamide], None, SampleMassAccuracy())
    mocks["peaks"].return_value = pd.DataFrame(peaks)
    mocks["mechanisms"].return_value = (
        ["m-h", "m-urea"],
        [mechanism("m-h", "[M+H]+"), mechanism("m-urea", "[M+CH4N2O+H]+")],
    )
    mocks["secondary"].return_value = [mechanism("m-nh4", "[M+NH4]+")]
    with stack:
        assert await fold_sample_peaks_without_run("si-1") == "batch-1"

    folded = {row.sample_peak_id: row for row in mocks["fold"].call_args.kwargs["rows"]}
    assert folded["p1"].assigned_formula == "C3H7N1O1"
    assert folded["p1"].tier == tier


@pytest.mark.asyncio
async def test_the_fold_reads_a_list_through_the_channels_a_run_opens():
    """The fold resolves a run's channels before its Stage A, so a reference
    list is read through them on this ledger as in a run, and a reading
    through one is held as a run holds it.

    A charge-transfer source declares electron transfer alone; methyl loss,
    the siloxanes' base peak there, is a channel the run opens for itself.
    """
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    def mechanism(mechanism_id, notation):
        return SimpleNamespace(
            ionization_mechanism_id=mechanism_id,
            ionization_mechanism=notation,
            ionization_mechanism_polarity="+",
        )

    d4 = _stage_a_row("si-1", fold_run_id("si-1")) | {
        "sample_peak_mz": 281.0512,
        "assigned_formula": "C8H24O4Si4",
        "ion_formula": "C7H21O4Si4+",
        "ionization_mechanism_id": "m-me",
    }
    stack, mocks = _patched()
    mocks["stage_a"].return_value = ([d4], None, SampleMassAccuracy())
    # The window starts above every probe of the source's channels, so its
    # silence there is the window's and the channels are opened.
    mocks["peaks"].return_value = pd.DataFrame(
        {"sample_peak_id": ["p1"], "mz": [281.0512], "intensity": [5000.0]}
    )
    mocks["mechanisms"].return_value = (["m-ct"], [mechanism("m-ct", "[M]+.")])
    mocks["secondary"].return_value = [
        mechanism("m-me", "[M-CH3]+"),
        mechanism("m-h", "[M+H]+"),
    ]
    with stack:
        stack.enter_context(patch(f"{_SVC}.get_instrument_type", return_value="orbi"))
        assert await fold_sample_peaks_without_run("si-1") == "batch-1"

    args = mocks["stage_a"].call_args.args
    assert args[3] == ["m-ct"]
    assert [m.ionization_mechanism_id for m in args[4]] == ["m-ct", "m-me", "m-h"]
    folded = {row.sample_peak_id: row for row in mocks["fold"].call_args.kwargs["rows"]}
    assert folded["p1"].assigned_formula == "C8H24O4Si4"
    assert folded["p1"].tier == "candidate"
    assert folded["p1"].provenance["minor_channel"] == {
        "corroborated_by": None,
        "capped": True,
    }


@pytest.mark.asyncio
async def test_an_ineligible_sample_is_skipped_and_nothing_is_written():
    """A blank, or a sample whose calibration is unverified, is refused a run;
    it is refused a fold on the same grounds, and nothing reaches the batch."""
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    stack, mocks = _patched(ineligible="the sample is a blank")
    with stack:
        assert await fold_sample_peaks_without_run("si-1") is None

    mocks["stage_a"].assert_not_called()
    mocks["fold"].assert_not_called()


@pytest.mark.asyncio
async def test_a_sample_with_a_run_in_flight_is_not_folded():
    """An explicit run is assigning the sample, so the fold stands down.

    Both write the sample's members and the fold keeps only what Stage A found,
    so folding here would drop the run's untargeted results from the ledger
    while its `peak_assignment` rows stayed. The run folds itself when it
    completes, so nothing is lost by standing down.
    """
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    stack, mocks = _patched(in_flight="run-9")
    with stack:
        assert await fold_sample_peaks_without_run("si-1") is None

    mocks["stage_a"].assert_not_called()
    mocks["fold"].assert_not_called()


@pytest.mark.asyncio
async def test_a_sample_another_worker_holds_is_not_folded():
    """The claim is what makes the stand-down hold across workers: the
    in-process set only knows about folds this process started."""
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    stack, mocks = _patched(acquired=False)
    with stack:
        assert await fold_sample_peaks_without_run("si-1") is None

    # Refused before the durable check, which is a query the claim holder has
    # already made.
    mocks["in_flight"].assert_not_called()
    mocks["stage_a"].assert_not_called()
    mocks["fold"].assert_not_called()


class TestTheFoldClaimsInTheRunsWindow:
    """The reagent pre-pass claims within the resolved profile's
    ``mz_precision_ppm``, and that comes from the sample's instrument class. The
    fold has to read the same class an explicit run does, or the two ledgers
    disagree about which peaks are reagent - which is the one thing the shared
    helper exists to prevent.
    """

    @staticmethod
    def _resolve_spy(stack, instrument):
        """Patch the instrument read and capture what resolve_profile is told."""
        from mascope_backend.api.new.peak_assignments import service

        seen = {}
        stack.enter_context(
            patch(f"{_SVC}.get_instrument_type", side_effect=instrument)
        )
        real = service.resolve_profile

        def spy(config, **kwargs):
            seen.update(kwargs)
            return real(config, **kwargs)

        stack.enter_context(patch(f"{_SVC}.resolve_profile", side_effect=spy))
        return seen

    @pytest.mark.asyncio
    async def test_the_instrument_class_reaches_the_profile(self):
        from mascope_backend.api.new.peak_assignments.service import (
            fold_sample_peaks_without_run,
        )

        stack, _ = _patched()
        with stack:
            seen = self._resolve_spy(stack, lambda _filename: "orbi")
            await fold_sample_peaks_without_run("si-1")

        assert seen["instrument_type"] == "orbi"

    @pytest.mark.asyncio
    async def test_stage_a_is_told_the_class_width_the_fold_resolved(self):
        """Where the target library fits no width, Stage A stands in the class's.

        That is the width a run stands in, so a fold and a run on the same thin
        sample score its Stage A rows alike.
        """
        from mascope_backend.api.new.peak_assignments.service import (
            fold_sample_peaks_without_run,
        )

        stack, mocks = _patched()
        with stack:
            self._resolve_spy(stack, lambda _filename: "orbi")
            await fold_sample_peaks_without_run("si-1")

        assert mocks["stage_a"].call_args.kwargs["fallback_sigma_ppm"] == 0.3

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "context, ceiling", [("ambient-air", "shipped"), ("none", None)]
    )
    async def test_stage_a_is_told_the_ceiling_the_fold_resolved(
        self, context, ceiling
    ):
        """The fold matches the reference under the context it resolved, as a
        run does, so the two ledgers hold the same known set."""
        from dataclasses import replace

        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.service import (
            fold_sample_peaks_without_run,
        )
        from mascope_tools.composition.profiles import (
            KNOWN_WINDOW_CEILING,
            get_chemistry_context,
        )

        real = service.resolve_profile

        def resolved_under_the_context(config, **kwargs):
            return replace(
                real(config, **kwargs), context=get_chemistry_context(context)
            )

        stack, mocks = _patched()
        with stack:
            stack.enter_context(
                patch(f"{_SVC}.get_instrument_type", return_value="orbi")
            )
            stack.enter_context(
                patch(f"{_SVC}.resolve_profile", side_effect=resolved_under_the_context)
            )
            await fold_sample_peaks_without_run("si-1")

        expected = KNOWN_WINDOW_CEILING if ceiling == "shipped" else None
        assert mocks["stage_a"].call_args.kwargs["known_window"] == expected

    @pytest.mark.asyncio
    async def test_a_filename_that_names_no_instrument_does_not_fail_the_fold(self):
        """The parse raises for a sample that keeps no data file and whose name
        does not say. Standing down is this path's contract, so the fold reads
        it defensively and carries on with no class."""
        from mascope_backend.api.new.peak_assignments.service import (
            fold_sample_peaks_without_run,
        )

        def boom(_filename):
            raise ValueError("the name does not say")

        stack, mocks = _patched()
        with stack:
            seen = self._resolve_spy(stack, boom)
            assert await fold_sample_peaks_without_run("si-1") == "batch-1"

        assert seen["instrument_type"] is None
        mocks["fold"].assert_awaited_once()
