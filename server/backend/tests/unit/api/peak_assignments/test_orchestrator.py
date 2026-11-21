"""
Unit tests for ``assign_sample_peaks`` - the orchestrator itself.

The engine's pure functions are covered in detail by ``test_engine.py``, but the
function that *composes* them - load peaks, Stage A, Stage B over the remainder,
unassigned fill, persist, finalize - had no direct test: everything that named it
mocked it. These pin the properties that only emerge from the composition, and
that a per-function test cannot see:

- every observed peak ends up with exactly one row (the ledger is complete);
- Stage B is only offered peaks Stage A did not claim;
- owners are inserted before the children that reference them;
- the run is finalized 'completed' on success and 'failed' when a stage raises.

The heavy edges are faked (peak loading, the targeted matcher, the composition
search, the calibration store, the database session) but the real arbitration,
untargeted mapping and unassigned fill run, so the composition under test is the
production one.

Note ``assign_sample_peaks`` is wrapped by ``api_controller_background_task``;
these call it with ``user_id=None`` so notification delivery stays on its quiet
path rather than reaching Socket.IO.
"""

import math
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from test_engine import _isotope_row  # same directory; pytest puts it on sys.path

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    FileLines,
)
from mascope_backend.api.new.peak_assignments.profiles import NamedContext


_MOD = "mascope_backend.api.new.peak_assignments.service"
#: The stage list, which looks the library's names up in its own module.
_PIPE = "mascope_backend.api.new.peak_assignments.pipeline"


def _claim_stub(acquired=True):
    """A stand-in for the cross-process assignment claim (no DB)."""

    @asynccontextmanager
    async def _claim(kind, resource_id):
        yield acquired

    return _claim


def _scalar_session(value):
    """An async_session() context whose scalar() resolves to ``value``."""
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=value)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _peaks_df(peak_specs: list[tuple[str, float, float]]) -> pd.DataFrame:
    """Build the frame ``load_sample_peaks`` returns: id, m/z, intensity."""
    return pd.DataFrame(
        [
            {"sample_peak_id": pid, "mz": mz, "intensity": intensity}
            for pid, mz, intensity in peak_specs
        ]
    )


@pytest.mark.parametrize("counted", [True, False], ids=["counted", "uncounted"])
def test_the_engines_peak_read_carries_the_scan_counts(counted):
    """Beside the noise estimate, where the file keeps them; a peak whose
    count the detection could not take reads NaN, never zero."""
    from mascope_backend.api.new.peak_assignments.service import load_sample_peaks

    peak_data = SimpleNamespace(
        peak_ids=["p1", "p2"],
        mz_values=[100.0, 101.0],
        heights=[10.0, 1.0],
        areas=None,
        signal_to_noise=[50.0, 5.0],
        n_scans=[4, None] if counted else None,
        n_scans_total=[4, 4] if counted else None,
    )
    sample = SimpleNamespace(filename="orbi-sample.raw", polarity="+", t0=0, t1=1)
    with (
        patch(f"{_MOD}.extract_peaks", return_value=peak_data),
        patch(f"{_MOD}.get_instrument_type", return_value="orbi"),
    ):
        peaks = load_sample_peaks(sample)

    if counted:
        assert peaks["n_scans"].iloc[0] == 4.0
        assert np.isnan(peaks["n_scans"].iloc[1])
        assert peaks["n_scans_total"].tolist() == [4.0, 4.0]
    else:
        assert not {"n_scans", "n_scans_total"} & set(peaks.columns)


def _sample() -> MagicMock:
    sample = MagicMock()
    sample.sample_item_id = "si-1"
    sample.sample_item_name = "Sample One"
    sample.sample_batch_id = "sb-1"
    sample.filename = "orbi-sample.raw"
    # The single-character form the sample row actually carries (String(1)),
    # which is what the mechanism join and the profile fallback both read.
    sample.polarity = "+"
    sample.instrument_function_id = "if-1"
    sample.instrument = "orbi"
    # No calibration record at all is eligible (matching the batch partition
    # and the targeted gate, which treat absent calibration as verified).
    sample.mz_calibration = None
    return sample


class _Recorder:
    """Captures the rows the orchestrator bulk-inserts, and the statements it
    issues alongside them."""

    def __init__(self):
        self.rows: list[dict] = []
        self.statements: list = []
        self.finalized: list[tuple[str, str]] = []

    def session_factory(self):
        session = AsyncMock()

        async def execute(statement, params=None):
            if isinstance(params, list):
                self.rows.extend(params)
            else:
                self.statements.append(statement)
            return MagicMock()

        session.execute = AsyncMock(side_effect=execute)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    def recorded_configs(self) -> list:
        """The `config` blobs written on the run, in order.

        Read off the compiled statements for the same reason the calibrations
        are: what matters is that the run row carries the resolution, not that
        some helper was called.
        """
        values = []
        for statement in self.statements:
            params = statement.compile().params
            if "config" in params:
                values.append(params["config"])
        return values

    def recorded_calibrations(self) -> list:
        """The `confidence_calibration` values written on the run, in order.

        Read off the compiled statements rather than a mocked helper, because
        what the test is pinning is that the curve is committed in the same
        transaction as the ledger - not merely that some function was called.
        """
        values = []
        for statement in self.statements:
            params = statement.compile().params
            if "confidence_calibration" in params:
                values.append(params["confidence_calibration"])
        return values


def _patches(
    recorder: _Recorder,
    peaks: pd.DataFrame,
    match_rows: list[dict],
    stage_b_matches: pd.DataFrame | None = None,
):
    """Fake the orchestrator's edges, leaving its composition logic real."""
    run = MagicMock()
    run.peak_assignment_run_id = "run-1"

    match_params = MagicMock()
    match_params.isotope_abundance_threshold = 0.0

    known_df = pd.DataFrame(match_rows) if match_rows else pd.DataFrame()

    return {
        "fetch_sample": patch(
            f"{_MOD}.fetch_sample", new_callable=AsyncMock, return_value=_sample()
        ),
        "match_params": patch(
            f"{_PIPE}.default_match_params",
            new_callable=AsyncMock,
            return_value=match_params,
        ),
        "create_run": patch(
            f"{_MOD}._create_run", new_callable=AsyncMock, return_value=run
        ),
        "finalize": patch(f"{_MOD}._finalize_run", new_callable=AsyncMock),
        "load_peaks": patch(f"{_MOD}.load_sample_peaks", return_value=peaks),
        "known": patch(
            f"{_MOD}._fetch_known_target_isotopes",
            new_callable=AsyncMock,
            return_value=known_df,
        ),
        "reference": patch(
            f"{_MOD}._fetch_reference_known_isotopes",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ),
        # The matcher already ran upstream in production; hand back the rows.
        "matcher": patch(
            f"{_MOD}.compute_match_isotopes",
            new_callable=AsyncMock,
            return_value=known_df,
        ),
        # Gating and fit scoring are covered by their own tests; keep the frame
        # intact so arbitration sees exactly the rows the test declared.
        "apply_params": patch(
            f"{_MOD}.gate_lines", side_effect=lambda df, *_args, **_kwargs: df
        ),
        "fit": patch(f"{_MOD}.score_ions_by_fit", side_effect=lambda df, **_kwargs: df),
        "calibration": patch(
            f"{_MOD}.load_calibration", new_callable=AsyncMock, return_value=None
        ),
        "mechanisms": patch(
            f"{_MOD}.fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(
                ["im-1"],
                [
                    SimpleNamespace(
                        ionization_mechanism_id="im-1",
                        ionization_mechanism="[M+H]+",
                        ionization_mechanism_polarity="+",
                    )
                ],
            ),
        ),
        "ionizations": patch(
            f"{_MOD}._untargeted_ionization_notations",
            return_value=(["[M+H]+"], {"[M+H]+": "[M+H]+"}),
        ),
        "compositions": patch(
            f"{_PIPE}.assign_compositions",
            return_value=(
                stage_b_matches if stage_b_matches is not None else pd.DataFrame(),
                {},
            ),
        ),
        # The seeded re-score opens the sample file again; these tests declare
        # their rows rather than measure them, so it stands down and every row
        # keeps the finder's own fit. Its own wiring is tested separately.
        "seeded": patch(
            f"{_MOD}._seeded_rescore",
            new_callable=AsyncMock,
            return_value=({}, None),
        ),
        # The file's resolving power lives with its instrument functions in the
        # database; these files have none, as a file never fitted has none.
        "resolution": patch(
            f"{_MOD}._resolution_of", new_callable=AsyncMock, return_value=None
        ),
        # What the sample's batch decided of its known species lives with its
        # batch ledger in the database; these samples' batches decided none.
        "batch_species": patch(
            f"{_PIPE}.batch_species_for", new_callable=AsyncMock, return_value=[]
        ),
        # What the file was acquired at, which a thin Orbitrap file's pooled
        # curve is matched on, is read off its stream row and its .props:
        # neither exists here, as for a file converted before either kept it,
        # so no neighbour is pooled.
        "acquisition": patch(
            "mascope_backend.api.new.peak_assignments.envelope_fit."
            "acquisition_settings",
            new_callable=AsyncMock,
            return_value=None,
        ),
        # The context the sample's batch and dataset name lives with them in
        # the database; this sample's name none.
        "named_context": patch(
            f"{_PIPE}.named_context_for",
            new_callable=AsyncMock,
            return_value=NamedContext(),
        ),
        "claim": patch(f"{_MOD}.assignment_claim", _claim_stub()),
        "session": patch(f"{_MOD}.async_session", side_effect=recorder.session_factory),
        # The ledger is inserted where the run keeps it, in the stage list.
        "ledger_session": patch(
            f"{_PIPE}.async_session", side_effect=recorder.session_factory
        ),
        "progress": patch(
            f"{_PIPE}.send_progress_user_notification", new_callable=AsyncMock
        ),
    }


def _start(patches: dict) -> dict:
    return {key: p.start() for key, p in patches.items()}


@pytest.fixture(autouse=True)
def _stop_all_patches():
    yield
    patch.stopall()


async def _run(config=None):
    from mascope_backend.api.new.peak_assignments.service import assign_sample_peaks

    return await assign_sample_peaks(
        sample_item_id="si-1",
        config=config,
        independent_transaction=True,
        user_id=None,
        process_id="proc-1",
    )


def _stage_a_rows() -> list[dict]:
    """One two-isotopologue ion matching peaks p1 (M0) and p2 (child)."""
    return [
        _isotope_row(
            target_isotope_id="ti-1",
            target_ion_id="ion-1",
            target_compound_id="tc-1",
            compound_formula="C6H12O6",
            ion_formula="C6H13O6+",
            mz=181.0707,
            relative_abundance=1.0,
            sample_peak_id="p1",
            sample_peak_intensity=10000.0,
        ),
        _isotope_row(
            target_isotope_id="ti-2",
            target_ion_id="ion-1",
            target_compound_id="tc-1",
            compound_formula="C6H12O6",
            ion_formula="C6H13O6+",
            mz=182.0741,
            relative_abundance=0.066,
            sample_peak_id="p2",
            sample_peak_intensity=660.0,
        ),
    ]


def _mirror_rows() -> list[dict]:
    """The same ion as :func:`_stage_a_rows`, matched from a reference list."""
    rows = _stage_a_rows()
    for row in rows:
        row["target_compound_id"] = None
        row["reference_identities"] = [{"name": "glucose", "source": "a seed list"}]
    return rows


class TestLedgerCompleteness:
    @pytest.mark.asyncio
    async def test_every_peak_gets_exactly_one_row(self):
        """The ledger is one row per observed peak - no gaps, no duplicates."""
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 300.1234, 500.0),
                ("p4", 400.5678, 250.0),
            ]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))

        result = await _run(PeakAssignmentConfig(run_untargeted=False))

        assigned_ids = [row["sample_peak_id"] for row in recorder.rows]
        assert len(assigned_ids) == len(peaks)
        assert set(assigned_ids) == set(peaks["sample_peak_id"])
        assert len(set(assigned_ids)) == len(assigned_ids), "a peak got two rows"
        assert result["data"]["total_peaks"] == 4

    @pytest.mark.asyncio
    async def test_peaks_nothing_explains_are_persisted_as_unassigned(self):
        """Unexplained peaks are recorded, not dropped."""
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df(
            [("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0), ("p9", 999.0, 10.0)]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        by_id = {row["sample_peak_id"]: row for row in recorder.rows}
        assert by_id["p9"]["tier"] == "unassigned"
        assert by_id["p9"]["assigned_formula"] is None

    @pytest.mark.asyncio
    async def test_owners_are_inserted_before_their_children(self):
        """A child's owner must already exist: the self-FK is validated per row.

        The child's peak id deliberately sorts *before* its owner's, so the
        natural per-peak ordering puts the child first and only the explicit
        owners-first sort can fix it. With the ids the other way round this test
        would pass whether or not that sort existed.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        # "a-child" sorts before "b-owner", and the M0 (owner) is the
        # higher-abundance isotopologue, i.e. b-owner.
        rows = [
            _isotope_row(
                target_isotope_id="ti-1",
                target_ion_id="ion-1",
                target_compound_id="tc-1",
                compound_formula="C6H12O6",
                ion_formula="C6H13O6+",
                mz=181.0707,
                relative_abundance=1.0,
                sample_peak_id="b-owner",
                sample_peak_intensity=10000.0,
            ),
            _isotope_row(
                target_isotope_id="ti-2",
                target_ion_id="ion-1",
                target_compound_id="tc-1",
                compound_formula="C6H12O6",
                ion_formula="C6H13O6+",
                mz=182.0741,
                relative_abundance=0.066,
                sample_peak_id="a-child",
                sample_peak_intensity=660.0,
            ),
        ]
        peaks = _peaks_df(
            [("b-owner", 181.0707, 10000.0), ("a-child", 182.0741, 660.0)]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, rows))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        # Guard against the fixture silently losing its point.
        owners = [r for r in recorder.rows if r.get("owner_peak_assignment_id")]
        assert owners, "fixture produced no iso_child row to order"

        seen_ids: set[str] = set()
        for row in recorder.rows:
            owner = row.get("owner_peak_assignment_id")
            if owner is not None:
                assert owner in seen_ids, "child inserted before its owner"
            seen_ids.add(row["peak_assignment_id"])


class TestTheJudgedLedger:
    """What the run persists is what the passes that judge it left."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("untargeted", [False, True], ids=["known", "search"])
    async def test_a_run_runs_every_stage_of_the_list(self, untargeted):
        # The run is the stage list's RUN path: every stage, from resolving
        # the chemistry to persisting the ledger, once per judging round.
        from mascope_backend.api.new.peak_assignments import pipeline
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        passes = []
        run_stages = pipeline.run_stages

        async def recording(ctx):
            passes.append(ctx)
            return await run_stages(ctx)

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        _start(_patches(_Recorder(), peaks, _stage_a_rows()))
        patch(f"{_PIPE}.run_stages", side_effect=recording).start()

        result = await _run(PeakAssignmentConfig(run_untargeted=untargeted))

        (ctx,) = passes
        assert ctx.path is pipeline.RUN
        assert ctx.ran == list(pipeline.STAGES)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_a_peak_whose_line_left_the_ledger_is_persisted_unassigned(self):
        # An isotopologue released with the reading it belonged to is a peak
        # nothing explains, and the ledger still has a row for it.
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        judged_ledger = service.Judging.result

        def releasing(self):
            judged = judged_ledger(self)
            judged.rows = [row for row in judged.rows if row["role"] == "M0"]
            return judged

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))
        patch.object(service.Judging, "result", releasing).start()

        result = await _run(PeakAssignmentConfig(run_untargeted=False))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert set(by_peak) == {"p1", "p2"}
        assert by_peak["p1"]["role"] == "M0"
        assert (by_peak["p2"]["role"], by_peak["p2"]["tier"]) == (
            "unassigned",
            "unassigned",
        )
        assert result["data"]["database_assigned"] == 1
        assert result["data"]["unassigned"] == 1

    @pytest.mark.asyncio
    async def test_the_run_reads_its_lines_off_its_own_peaks_and_resolution(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        mocks["resolution"].return_value = lambda mz: 100_000.0

        await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["resolution"].assert_awaited_once()
        assert mocks["resolution"].call_args.args[0].sample_item_id == "si-1"
        config = recorder.recorded_configs()[-1]
        # These peaks carry no noise estimate, and the file a resolving power.
        lines = config["measurement_model"]["gate"]["lines"]
        assert (lines["noise"], lines["resolution"]) == (False, True)
        assert set(config["measurement_model"]["gate"]["isotopologues"]) == {
            "tracks",
            "in_doubt",
            "untracked",
        }

    @pytest.mark.asyncio
    async def test_the_runs_re_score_reads_its_lines_at_the_runs_resolution(self):
        # The search's readings are tiered on a re-score of their lines, read at
        # the R(m/z) the run read Stage A's at and the finder ranked them at:
        # with none, an Orbitrap file's lines would be its fine lines, unmerged,
        # and a TOF file's merged at 10,000 (MAS-977).
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        def orbitrap(mz):
            return 100_000.0

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        mocks["resolution"].return_value = orbitrap

        await _run(PeakAssignmentConfig(run_untargeted=True))

        mocks["seeded"].assert_awaited_once()
        assert mocks["seeded"].await_args.kwargs["resolution"] is orbitrap

    @pytest.mark.asyncio
    async def test_stage_a_reads_its_known_set_at_the_runs_resolution(self):
        # The stage list reads the file's R(m/z) once, and Stage A resolves the
        # lines of its known set at that reading (MAS-977): with none, an
        # Orbitrap file's lines would be its fine lines, unmerged.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        def orbitrap(mz):
            return 100_000.0

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        mocks["resolution"].return_value = orbitrap

        await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["resolution"].assert_awaited_once()
        mocks["known"].assert_awaited_once()
        file_lines = mocks["known"].await_args.args[3]
        assert file_lines.resolution is orbitrap
        assert mocks["reference"].await_args.kwargs["file_lines"] is file_lines

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read", [True, False], ids=["read", "none"])
    async def test_a_neighbour_s_lines_are_read_at_the_files_resolution(self, read):
        # The resolving power the mass gate reads the lines at is the one the
        # envelope rule and the claims predict a neighbour's lines at: on a TOF
        # its M+1 peak holds its 13C, 15N, 2H and 17O lines together.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        mocks["resolution"].return_value = (lambda mz: 5000.0) if read else None

        await _run(PeakAssignmentConfig(run_untargeted=False))

        tiering = recorder.recorded_configs()[-1]["tiering"]
        assert tiering["lines_merged_at_resolution"] is read

    @pytest.mark.asyncio
    async def test_the_source_s_lines_anchor_the_runs_calibration(self):
        # The urea source's three ladder ions are claimed before either stage
        # and are no commit, but their masses are the file's best known: the
        # gate fits beside the run's one corroborated commit over them.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.reagents import reagent_library

        ladder = {cluster.label: cluster.mz for cluster in reagent_library("UR")}
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("r1", ladder["[CH4N2O+H]+"], 2.0e6),
                ("r2", ladder["[(CH4N2O)2+H]+"], 3.0e6),
                ("r3", ladder["[(CH4N2O)3+H]+"], 1.0e5),
            ]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(profile="UR", run_untargeted=False))

        gate = recorder.recorded_configs()[-1]["measurement_model"]["gate"]
        # The library's monoisotopic row and the three lines, which the refit
        # anchored on.
        anchors = gate["refit"]["anchors"]
        assert (anchors["commits"], anchors["source_lines"]) == (1, 3)
        assert (gate["committed"], gate["corroborated"]) == (2, 2)
        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert {by_peak[peak]["role"] for peak in ("r1", "r2", "r3")} == {"reagent"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read", [True, False], ids=["read", "none"])
    async def test_the_source_s_isotope_lines_are_claimed_at_the_files_resolution(
        self, read
    ):
        # On a TOF the urea dimer's peak one mass unit up holds its 13C and 15N
        # lines, and sits 19 ppm under the 13C line: the pre-pass finds it only
        # where the run hands it the file's resolving power.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.heuristic_filter import (
            predict_resolved_isotopes,
        )
        from mascope_tools.composition.reagents import reagent_library

        def tof(mz):
            return 5000.0

        ladder = {cluster.label: cluster.mz for cluster in reagent_library("UR")}
        mzs, _, labels = predict_resolved_isotopes("C2H9N4O2", 1, 1e-3, tof)
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("r1", ladder["[CH4N2O+H]+"], 2.0e6),
                ("r2", ladder["[(CH4N2O)2+H]+"], 3.0e6),
                ("r2-m1", float(mzs[labels.index("13C")]), 1.1e5),
            ]
        )
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        mocks["resolution"].return_value = tof if read else None

        await _run(PeakAssignmentConfig(profile="UR", run_untargeted=False))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert (by_peak["r2-m1"]["role"] == "reagent") is read

    @pytest.mark.asyncio
    async def test_the_passes_are_handed_the_runs_own_bands(self):
        # A row under the top band names it among its reasons, so the bands the
        # tiering pass reads are the ones the run tiered on.
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        judge = service.Judging
        handed = {}

        def recording(rows, **kwargs):
            handed.update(kwargs)
            return judge(rows, **kwargs)

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        _start(_patches(_Recorder(), peaks, _stage_a_rows()))
        patch(f"{_MOD}.Judging", side_effect=recording).start()

        await _run(
            PeakAssignmentConfig(
                run_untargeted=False, assigned_threshold=0.8, candidate_threshold=0.5
            )
        )

        assert handed["tier_bands"] == {"assigned": 0.8, "candidate": 0.5}

    @pytest.mark.asyncio
    async def test_the_passes_are_handed_the_runs_context(self):
        # The tiering pass asks the context's priors (step 3.6), so the context
        # it reads is the one the run resolved, and the run records which.
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        judge = service.Judging
        handed = {}

        def recording(rows, **kwargs):
            handed.update(kwargs)
            return judge(rows, **kwargs)

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))
        patch(f"{_MOD}.Judging", side_effect=recording).start()

        await _run(PeakAssignmentConfig(run_untargeted=False, context="chamber"))

        assert handed["context"].name == "chamber"
        # A '[M+H]+' mode is no nitrate source: no skeleton reading.
        assert handed["nox_skeleton"] is False
        config = recorder.recorded_configs()[-1]
        assert config["tiering"]["context_priors"] == {
            "context": "chamber",
            "priors": ["sulfur", "odd_nitrogen"],
        }
        assert config["resolved_profile"]["context_priors"] == [
            "sulfur",
            "odd_nitrogen",
        ]

    @pytest.mark.asyncio
    async def test_every_committed_row_is_given_its_identification_level(self):
        # Step 5.8: the level is read off the finished ledger, with what the
        # run read of it - its spectrum, the gate its lines were asked at, its
        # channels, instrument, context and artifacts - and recorded with the
        # tiering record; the column goes in beside the tier.
        from mascope_backend.api.new.peak_assignments import pipeline, service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.identification_level import (
            LEVEL_RULES_VERSION,
        )
        from mascope_backend.api.new.peak_assignments.mass_gate import SpectrumLines

        levels, judge = pipeline.apply_levels, service.Judging
        handed, judged = {}, {}

        def recording(rows, **kwargs):
            handed.update(kwargs)
            return levels(rows, **kwargs)

        def judging(rows, **kwargs):
            judged.update(kwargs)
            return judge(rows, **kwargs)

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))
        patch(f"{_PIPE}.apply_levels", side_effect=recording).start()
        patch(f"{_MOD}.Judging", side_effect=judging).start()
        # A file whose resolving power the run read, so the one the level pass
        # is handed can be told from none.
        patch(
            f"{_MOD}._resolution_of",
            new_callable=AsyncMock,
            return_value=lambda mz: 120000.0,
        ).start()

        await _run(PeakAssignmentConfig(run_untargeted=False, context="chamber"))

        # The spectrum and the resolving power the tiering pass read.
        assert isinstance(handed["lines"], SpectrumLines)
        assert handed["lines"] is judged["lines"]
        assert handed["resolution"] is judged["resolution"]
        assert handed["resolution"] is not None
        assert handed["tiering"]["absent_line"]["spectrum"] is True
        assert handed["context"].name == "chamber"
        assert handed["notation_by_id"]
        assert handed["instrument_type"] == "orbi"
        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        m0, child = by_peak["p1"], by_peak["p2"]
        assert m0["level"] in ("4a", "4b", "5a", "5b")
        assert m0["provenance"]["identification_level"]["level"] == m0["level"]
        assert child["level"] == m0["level"]
        assert (
            child["provenance"]["identification_level"]["inherited_from"]
            == (m0["peak_assignment_id"])
        )
        config = recorder.recorded_configs()[-1]
        assert config["tiering"]["identification_level"]["level_rules_version"] == (
            LEVEL_RULES_VERSION
        )
        assert config["tiering"]["identification_level"]["levels"] == {m0["level"]: 1}

    @pytest.mark.asyncio
    async def test_stage_b_is_built_from_the_rows_the_twin_reread_leaves(self):
        # Step 3.6: the run hands the election's rows to the halogen-twin
        # re-read, builds its Stage B rows from what it returns, and names the
        # twin on the row it re-read.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 112.0074, 5000.0),
            ]
        )
        placeholders = pd.DataFrame(
            [
                {"mz": mz, "formula": "---", "ion": "---", "isotope_label": "---"}
                for mz in (112.0074, 181.0707, 182.0741)
            ]
        )
        reread = pd.DataFrame(
            [
                {
                    "mz": 112.0074,
                    "formula": "C6H5Cl",
                    "ion": "C6H5Cl+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "[M+H]+",
                    "isotopic_pattern_score": 0.95,
                    "mz_error_ppm": 0.1,
                    "intensity_error": 0.0,
                }
            ]
        )
        record = {"halogen": "Cl", "twin_mz": 114.0044, "replaced": None}
        recorder = _Recorder()
        mocks = _start(
            _patches(recorder, peaks, _stage_a_rows(), stage_b_matches=placeholders)
        )
        handed = AsyncMock(return_value=(reread, {112.0074: record}))
        patch(f"{_MOD}._reread_halogen_twins", handed).start()

        await _run(PeakAssignmentConfig())

        assert handed.await_args.args[0]["mz"].tolist() == placeholders["mz"].tolist()
        assert handed.await_args.kwargs["searched_mzs"].tolist() == [112.0074]
        # Stage A's peaks are another pass's: neither asked nor a twin.
        assert sorted(handed.await_args.kwargs["claimed_mzs"].tolist()) == [
            181.0707,
            182.0741,
        ]
        assert handed.await_args.kwargs["resolved_profile"].context is not None
        row = next(row for row in recorder.rows if row["sample_peak_id"] == "p3")
        assert row["assigned_formula"] == "C6H5Cl"
        assert row["provenance"]["halogen_twin"] == record
        assert mocks["compositions"].called

    @pytest.mark.asyncio
    async def test_a_nitrate_run_hands_the_passes_its_skeleton_reading(self):
        # Step 3.6's rider: a nitrate run on an Orbitrap in ambient air reads
        # an organonitrate's skeleton, so its tiering pass holds what only the
        # skeleton admits, and its search reads the skeleton in its filter.
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        judge = service.Judging
        handed = {}

        def recording(rows, **kwargs):
            handed.update(kwargs)
            return judge(rows, **kwargs)

        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 300.1234, 500.0),
            ]
        )
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        patch(f"{_MOD}.Judging", side_effect=recording).start()

        await _run(PeakAssignmentConfig(profile="NO3"))

        assert handed["nox_skeleton"] is True
        assert mocks["compositions"].call_args.args[2].context_nox_skeleton is True
        config = recorder.recorded_configs()[-1]
        assert config["resolved_profile"]["nox_skeleton"] is True
        assert config["tiering"]["nox_skeleton"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "named, requested, context, source",
        [
            # A run on auto takes what the sample's batch names, before its
            # dataset's and before the profile's default (step 3.6).
            (
                NamedContext(batch="chamber", dataset="indoor-air"),
                "auto",
                "chamber",
                "batch",
            ),
            (NamedContext(dataset="indoor-air"), "auto", "indoor-air", "dataset"),
            (NamedContext(), "auto", "none", "profile"),
            # A run that names a context keeps it.
            (NamedContext(batch="chamber"), "water", "water", "config"),
        ],
    )
    async def test_a_run_on_auto_takes_the_context_its_data_names(
        self, named, requested, context, source
    ):
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        judge = service.Judging
        handed = {}

        def recording(rows, **kwargs):
            handed.update(kwargs)
            return judge(rows, **kwargs)

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        started = _start(_patches(recorder, peaks, _stage_a_rows()))
        started["named_context"].return_value = named
        patch(f"{_MOD}.Judging", side_effect=recording).start()

        await _run(PeakAssignmentConfig(run_untargeted=False, context=requested))

        started["named_context"].assert_awaited_with("sb-1")
        assert handed["context"].name == context
        snapshot = recorder.recorded_configs()[-1]["resolved_profile"]
        assert (snapshot["context"], snapshot["context_source"]) == (context, source)
        assert snapshot["named_context"] == {
            "batch": named.batch,
            "dataset": named.dataset,
        }

    @pytest.mark.asyncio
    async def test_the_tiering_pass_reads_the_runs_tolerance_and_artifacts(self):
        # The absent-line rule asks a row's lines from the threshold the
        # run's fitted tolerance sets, reads the artifact pre-pass's peaks as
        # no line, and holds a TOF's halogen readings to their M+2 line, so
        # the run hands it all three.
        import numpy as np

        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.envelope_fit import (
            RunEnvelopeFit,
        )
        from mascope_backend.api.new.peak_assignments.tiering import (
            detect_threshold,
        )
        from mascope_tools.composition.envelope_tolerance import (
            FORM_CURVE,
            EnvelopeLines,
            fit_envelope_tolerance,
        )

        # Lines at an expected signal-to-noise of 5 the file never holds, and
        # at 15 it always does: an absent line counts from 10.
        fitted = RunEnvelopeFit(
            fit_envelope_tolerance(
                FORM_CURVE,
                EnvelopeLines(),
                [
                    EnvelopeLines(
                        np.r_[np.full(10, 5.0), np.full(30, 15.0)],
                        np.r_[np.full(10, np.nan), np.full(30, 0.75)],
                    )
                ],
            ),
            None,
        )
        assert fitted.tolerance.detect_snr == 10.0
        judge = service.Judging
        handed = {}

        def recording(rows, **kwargs):
            handed.update(kwargs)
            return judge(rows, **kwargs)

        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p-lobe", 181.0750, 5.0),
            ]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))
        patch(
            f"{_MOD}.fit_run_envelope_tolerance",
            new_callable=AsyncMock,
            return_value=fitted,
        ).start()
        patch(
            f"{_MOD}.claim_artifact_peaks",
            side_effect=lambda df, *_args: df[df["sample_peak_id"] == "p-lobe"],
        ).start()
        patch(f"{_MOD}.Judging", side_effect=recording).start()

        await _run(PeakAssignmentConfig(run_untargeted=False))

        assert handed["model"].envelope_tolerance == fitted.tolerance
        assert handed["artifact_peak_ids"] == frozenset({"p-lobe"})
        assert handed["instrument_type"] == "orbi"
        assert handed["lines"].holds("p1")
        tiering = recorder.recorded_configs()[-1]["tiering"]
        assert tiering["absent_line"]["detect_snr"] == 10.0
        assert detect_threshold(fitted.tolerance)[0] == 10.0

    @pytest.mark.asyncio
    async def test_the_run_records_what_its_claims_did(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        tiering = recorder.recorded_configs()[-1]["tiering"]
        assert {
            key: tiering[key]
            for key in (
                "claimed",
                "claimed_with_their_lines",
                "claim_rounds",
                "held",
                "released",
                "unapplied",
            )
        } == {
            "claimed": 0,
            "claimed_with_their_lines": 0,
            "claim_rounds": 1,
            "held": {},
            "released": 0,
            "unapplied": 0,
        }

    @pytest.mark.asyncio
    async def test_a_fragment_of_a_committed_parent_is_persisted_as_the_sources(self):
        # The fragment claim reads the stages' commits as the judging round's
        # reading changes settle them, before any cap, and the peak it takes
        # is written once, as the source's ion.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.engine import FRAGMENTS_KEY
        from mascope_tools.composition.reagents import FragmentIon, FragmentLadder

        ladder = FragmentLadder(
            parent="C6H12O6",
            label="a stated parent",
            fragments=(FragmentIon("C3H7O3", 1, "[C3H7O3]+", literature_ratio=1.0),),
            references=("nist",),
        )
        fragment = _isotope_row(
            target_isotope_id="ti-3",
            target_ion_id="ion-2",
            target_compound_id="tc-2",
            compound_formula="C3H6O3",
            ion_formula="C3H7O3+",
            mz=91.0390,
            relative_abundance=1.0,
            sample_peak_id="p3",
            sample_peak_intensity=5000.0,
        )
        # A reference list's reading, which the claim may take: a compound of
        # the target library keeps its peak.
        fragment["target_compound_id"] = None
        fragment["reference_identities"] = [{"name": "lactic acid", "source": "a list"}]
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 91.0390, 5000.0),
            ]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows() + [fragment]))
        patch(f"{_PIPE}.fragment_ladders_for", return_value=(ladder,)).start()

        await _run(PeakAssignmentConfig(run_untargeted=False))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert len(recorder.rows) == len(by_peak) == len(peaks)
        assert by_peak["p1"]["role"] == "M0"
        claimed = by_peak["p3"]
        assert (claimed["role"], claimed["assigned_formula"]) == ("reagent", None)
        assert claimed["ion_formula"] == "C3H7O3+"
        assert claimed["provenance"]["reagent"]["family"] == "fragment"
        assert claimed["provenance"]["reagent"]["parent"]["formula"] == "C6H12O6"
        recorded = recorder.recorded_configs()[-1][FRAGMENTS_KEY]
        assert (recorded["claimed"], recorded["ladders"]) == (1, ["a stated parent"])

    @pytest.mark.asyncio
    async def test_a_profile_with_no_ladder_records_no_claim(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.engine import FRAGMENTS_KEY

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        assert FRAGMENTS_KEY not in recorder.recorded_configs()[-1]


class TestAListHitMeetsTheGrid:
    """Every peak a list read is put to the formula search, which may keep the
    list's reading there or let a rival take the peak."""

    RIVAL = {
        "formula": "C7H16O5",
        "ion": "C7H17O5+",
        "ionization_mechanism": "[M+H]+",
        "fit_score": 0.97,
        "mz_error_ppm": 0.3,
    }

    @staticmethod
    def _kept(rivals):
        from mascope_tools.composition.finder import (
            KNOWN_KEPT,
            KNOWN_RIVALS,
            ReadingRivals,
        )

        return {
            "mz": 181.0707,
            "formula": "C6H12O6",
            "ion": "C6H13O6+",
            "isotope_label": "M0",
            "other_candidates": "",
            KNOWN_KEPT: True,
            KNOWN_RIVALS: ReadingRivals(
                density=1 + len(rivals),
                rivals=tuple(rivals),
                fit_score=0.9,
                candidates=4,
                in_grid=True,
            ),
        }

    @staticmethod
    def _taken():
        """C7H16O5 takes the list's peak, and its envelope claims p2."""
        from mascope_tools.composition.finder import KNOWN_DISPLACED

        rival = {
            "formula": "C7H16O5",
            "ion": "C7H17O5+",
            "ionization_mechanism": "[M+H]+",
            "isotopic_pattern_score": 0.95,
            "composition_error_ppm": 0.3,
            "candidate_density": 1,
            "other_candidates": "",
        }
        return [
            {
                **rival,
                "mz": 181.0707,
                "isotope_label": "M0",
                KNOWN_DISPLACED: {
                    "peak_mz": 181.0707,
                    "formula": "C6H12O6",
                    "ion": "C6H13O6+",
                    "ionization_mechanism": "[M+H]+",
                    "fit_score": 0.3,
                    "evidence": 0.3,
                    "rival_evidence": 0.95,
                    "prior": 2.0,
                },
            },
            {**rival, "mz": 182.0741, "isotope_label": "13C"},
        ]

    def _start_with(self, recorder, rows, stage_a=None):
        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        return _start(
            _patches(
                recorder,
                peaks,
                _mirror_rows() if stage_a is None else stage_a,
                pd.DataFrame(rows),
            )
        )

    @pytest.mark.asyncio
    async def test_each_list_peak_is_put_to_the_search_with_its_reading(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.finder import ListReading

        recorder = _Recorder()
        mocks = self._start_with(recorder, [self._kept([])])

        await _run(PeakAssignmentConfig(run_untargeted=True))

        mocks["compositions"].assert_called_once()
        kwargs = mocks["compositions"].call_args.kwargs
        # The monoisotopic row only, at its peak, through the channel it was
        # matched on; its isotopologue's peak is the list's and not searched.
        assert kwargs["known"] == {
            181.0707: ListReading(
                mz=181.0707,
                formula="C6H12O6",
                ionization_mechanism="[M+H]+",
                mz_error_ppm=1.0,
                keeps_peak=False,
            )
        }
        assert kwargs["targets"] == [181.0707]
        assert kwargs["known_prior"] == 2.0
        assert kwargs["closed_shell_rivals"] is True

    @pytest.mark.asyncio
    async def test_a_target_library_reading_is_asked_to_keep_its_peak(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        mocks = self._start_with(recorder, [self._kept([])], stage_a=_stage_a_rows())

        await _run(PeakAssignmentConfig(run_untargeted=True))

        (reading,) = mocks["compositions"].call_args.kwargs["known"].values()
        # Measured against the grid all the same.
        assert reading.keeps_peak is True

    #: What the batch's series detection recorded for p1's batch peak: a
    #: homologue one CH2 above an assigned C5H10O6 proposed this reading.
    SERIES = {
        "formula": "C6H12O6",
        "ionization_mechanism_id": "[M+H]+",
        "has_anchor": True,
        "unit": "CH2",
        "step": 1,
        "depth": 1,
        "root_formula": "C5H10O6",
    }

    def _with_series(self, record):
        return patch(
            f"{_MOD}.batch_series_by_peak",
            new_callable=AsyncMock,
            return_value={"p1": record},
        ).start()

    @pytest.mark.asyncio
    async def test_a_series_of_the_batch_settles_the_rivals_it_names(self):
        # The same contested reading as below, on a peak whose batch peak a
        # homologous series vouches for: the series is evidence from outside
        # the peak, so the density rule does not take its tier (step 4.1).
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        self._start_with(recorder, [self._kept([self.RIVAL])])
        series = self._with_series(self.SERIES)

        await _run(PeakAssignmentConfig(run_untargeted=True))

        series.assert_awaited_once_with("si-1")
        row = {row["sample_peak_id"]: row for row in recorder.rows}["p1"]
        assert row["provenance"]["series"] == self.SERIES
        rules = {reason["rule"] for reason in row["provenance"]["tier_reasons"]}
        assert "series_anchor" in rules
        assert "candidate_density" not in rules
        assert row["tier"] == "assigned"
        assert recorder.recorded_configs()[-1]["tiering"]["series_anchored"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "change",
        [{"formula": "C7H16O5"}, {"ionization_mechanism_id": "[M+NH4]+"}],
        ids=["another formula", "another mechanism"],
    )
    async def test_a_series_of_another_reading_is_not_this_rows(self, change):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        self._start_with(recorder, [self._kept([self.RIVAL])])
        self._with_series({**self.SERIES, **change})

        await _run(PeakAssignmentConfig(run_untargeted=True))

        row = {row["sample_peak_id"]: row for row in recorder.rows}["p1"]
        assert "series" not in row["provenance"]
        assert row["tier"] == "candidate"
        assert recorder.recorded_configs()[-1]["tiering"]["series_anchored"] == 0

    @pytest.mark.asyncio
    async def test_a_series_reading_the_fragment_claim_takes_is_not_counted(self):
        # The series records are stamped on the rows as elected, before the
        # judging round reads the fragments: the reading of p3 carries one and
        # then leaves the ledger as the source's fragment, so the run's record
        # counts the series-anchored rows the judged ledger holds, p1's alone.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.reagents import FragmentIon, FragmentLadder

        ladder = FragmentLadder(
            parent="C6H12O6",
            label="a stated parent",
            fragments=(FragmentIon("C3H7O3", 1, "[C3H7O3]+", literature_ratio=1.0),),
            references=("nist",),
        )
        fragment = _isotope_row(
            target_isotope_id="ti-3",
            target_ion_id="ion-2",
            target_compound_id=None,
            compound_formula="C3H6O3",
            ion_formula="C3H7O3+",
            mz=91.0390,
            relative_abundance=1.0,
            sample_peak_id="p3",
            sample_peak_intensity=5000.0,
        )
        fragment["reference_identities"] = [{"name": "lactic acid", "source": "a list"}]
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 91.0390, 5000.0),
            ]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows() + [fragment]))
        patch(f"{_PIPE}.fragment_ladders_for", return_value=(ladder,)).start()
        patch(
            f"{_MOD}.batch_series_by_peak",
            new_callable=AsyncMock,
            return_value={
                "p1": self.SERIES,
                "p3": {**self.SERIES, "formula": "C3H6O3", "root_formula": "C2H4O3"},
            },
        ).start()

        await _run(PeakAssignmentConfig(run_untargeted=False))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert by_peak["p1"]["provenance"]["series"] == self.SERIES
        assert by_peak["p3"]["role"] == "reagent"
        assert "series" not in by_peak["p3"]["provenance"]
        assert recorder.recorded_configs()[-1]["tiering"]["series_anchored"] == 1

    @pytest.mark.asyncio
    async def test_a_kept_reading_counts_its_rivals_and_keeps_its_row(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        self._start_with(recorder, [self._kept([self.RIVAL])])

        await _run(PeakAssignmentConfig(run_untargeted=True))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        row = by_peak["p1"]
        assert (row["source"], row["assigned_formula"]) == ("database", "C6H12O6")
        provenance = row["provenance"]
        assert provenance["candidate_density"] == 2
        assert provenance["grid_rivals"]["added"] == 1
        assert "candidate_density" in {
            reason["rule"] for reason in provenance["tier_reasons"]
        }
        assert row["tier"] == "candidate"
        assert by_peak["p2"]["role"] == "iso_child"
        scope = recorder.recorded_configs()[-1]["search_scope"]
        assert scope["list_hits"] == {
            "measured": 1,
            "with_rivals": 1,
            "taken_by_rivals": 0,
            "kept_by_lines": 0,
            "kept_by_library": 0,
            "prior": 2.0,
        }

    @pytest.mark.asyncio
    async def test_a_rival_a_reading_was_held_against_is_recorded(self):
        from dataclasses import replace

        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.finder import HELD_BY_LINES, KNOWN_RIVALS

        kept = self._kept([self.RIVAL])
        kept[KNOWN_RIVALS] = replace(
            kept[KNOWN_RIVALS],
            held_against={
                "formula": "C7H16O5",
                "ion": "C7H17O5+",
                "ionization_mechanism": "[M+H]+",
                "fit_score": 0.971234,
                "prior": 2.0,
                "why": HELD_BY_LINES,
                "unexplained_lines": [182.0741234],
            },
        )
        recorder = _Recorder()
        self._start_with(recorder, [kept])

        await _run(PeakAssignmentConfig(run_untargeted=True))

        row = next(row for row in recorder.rows if row["sample_peak_id"] == "p1")
        assert row["provenance"]["grid_rivals"]["held_against"] == {
            "formula": "C7H16O5",
            "ion_formula": "C7H17O5+",
            "ionization_mechanism": "[M+H]+",
            "fit_score": 0.9712,
            "prior": 2.0,
            "why": "unexplained_lines",
            "unexplained_lines": [182.07412],
        }
        scope = recorder.recorded_configs()[-1]["search_scope"]
        assert (
            scope["list_hits"]["kept_by_lines"],
            scope["list_hits"]["kept_by_library"],
        ) == (1, 0)

    @pytest.mark.asyncio
    async def test_a_kept_reading_with_no_rival_keeps_its_tier(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        mocks = self._start_with(recorder, [self._kept([])])

        await _run(PeakAssignmentConfig(run_untargeted=True))

        row = next(row for row in recorder.rows if row["sample_peak_id"] == "p1")
        assert row["tier"] == "assigned"
        assert row["provenance"]["grid_rivals"]["added"] == 0
        # The kept reading is no commit of the search's, so nothing re-measures
        # it.
        assert mocks["seeded"].call_args.args[2] == set()

    @pytest.mark.asyncio
    async def test_a_rival_takes_the_peak_and_the_row_names_the_list(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        self._start_with(recorder, self._taken())

        result = await _run(PeakAssignmentConfig(run_untargeted=True))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        row = by_peak["p1"]
        assert (row["source"], row["assigned_formula"], row["role"]) == (
            "untargeted",
            "C7H16O5",
            "M0",
        )
        first = row["alternatives"][0]
        assert first["assigned_formula"] == "C6H12O6"
        assert first["source"] == "database"
        assert first["target_compound_id"] is None
        assert first["reference_identities"] == [
            {"name": "glucose", "source": "a seed list"}
        ]
        assert first["displaced_by_rival"] is True
        reading = row["provenance"]["list_reading"]
        assert reading["assigned_formula"] == "C6H12O6"
        assert reading["reference_identities"] == first["reference_identities"]
        assert (reading["evidence"], reading["rival_evidence"], reading["prior"]) == (
            0.3,
            0.95,
            2.0,
        )
        # The list's isotopologue left with it, and the rival's envelope holds
        # its peak now.
        assert by_peak["p2"]["source"] == "untargeted"
        assert by_peak["p2"]["owner_peak_assignment_id"] == row["peak_assignment_id"]
        scope = recorder.recorded_configs()[-1]["search_scope"]
        assert scope["list_hits"]["taken_by_rivals"] == 1
        assert result["data"]["database_assigned"] == 0

    @pytest.mark.asyncio
    async def test_a_line_the_rival_does_not_claim_is_left_unassigned(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        self._start_with(recorder, self._taken()[:1])

        await _run(PeakAssignmentConfig(run_untargeted=True))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert by_peak["p1"]["assigned_formula"] == "C7H16O5"
        assert by_peak["p2"]["role"] == "unassigned"

    @pytest.mark.asyncio
    async def test_a_run_without_the_search_asks_nothing(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        mocks = self._start_with(recorder, self._taken())

        await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["compositions"].assert_not_called()
        row = next(row for row in recorder.rows if row["sample_peak_id"] == "p1")
        assert row["source"] == "database"
        assert "grid_rivals" not in row["provenance"]

    @pytest.mark.asyncio
    async def test_a_list_peak_on_a_channel_the_search_does_not_run_is_not_asked(
        self,
    ):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        mocks = self._start_with(recorder, self._taken())
        mocks["ionizations"].return_value = (["[M+NH4]+"], {"[M+NH4]+": "im-2"})

        await _run(PeakAssignmentConfig(run_untargeted=True))

        # Nothing else to search either, so the search does not run.
        mocks["compositions"].assert_not_called()
        row = next(row for row in recorder.rows if row["sample_peak_id"] == "p1")
        assert row["source"] == "database"


class TestStageHandoff:
    @pytest.mark.asyncio
    async def test_stage_b_is_never_offered_a_peak_stage_a_owns(self):
        """The single-owner invariant across the stage boundary.

        Stage A's peaks must not be searched, or two stages could claim the same
        peak and violate the per-run uniqueness constraint. They are still handed
        to the finder - as isotope-pattern context, which is what lets a Stage B
        ion's envelope be scored against the whole spectrum - so what says the
        boundary holds is the target list, not the frame.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 300.1234, 500.0),
            ]
        )
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=True))

        mocks["compositions"].assert_called_once()
        call = mocks["compositions"].call_args
        # The whole spectrum is the context...
        assert set(call.args[0]["mz"].tolist()) == {181.0707, 182.0741, 300.1234}
        # ...and what is enumerated is the peak no earlier pass explains and the
        # list's own peak, where the list's reading is put beside the grid's.
        # The Stage A ion's isotopologue on p2 is not.
        assert set(call.kwargs["targets"]) == {300.1234, 181.0707}
        assert set(call.kwargs["known"]) == {181.0707}

    @pytest.mark.asyncio
    async def test_the_judging_is_told_what_the_lists_name(self):
        """Every neutral the lists Stage A read name, matched or not: a
        secondary-channel winner a list names is corroborated by it (3.4), in
        the judging rounds' second-channel cap."""
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p3", 300.1234, 500.0)])
        recorder = _Recorder()
        patches = _patches(recorder, peaks, _stage_a_rows())
        unmatched = _mirror_rows()[:1]
        unmatched[0]["target_compound_formula"] = "O3C9H14"
        patches["reference"] = patch(
            f"{_MOD}._fetch_reference_known_isotopes",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(unmatched),
        )
        patches["judging"] = patch(f"{_MOD}.Judging", wraps=service.Judging)
        mocks = _start(patches)

        await _run(PeakAssignmentConfig(run_untargeted=True))

        kwargs = mocks["judging"].call_args.kwargs
        assert kwargs["listed_formulas"] == frozenset({"C6H12O6", "C9H14O3"})
        # ...and the channels the run opened, a list's readings through which
        # the cap judges.
        assert "opened_channels" in kwargs

    @pytest.mark.asyncio
    async def test_untargeted_stage_is_skipped_when_disabled(self):
        """Stage A only means the composition search is never invoked."""
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p3", 300.1234, 500.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["compositions"].assert_not_called()


class TestAReferenceMirrorsNitrogenCount:
    @pytest.mark.asyncio
    async def test_its_ion_s_other_reading_reaches_the_cross_channel_pass(self):
        """A list's formula is given its ion's family before the pass reads it.

        Dimethylformamide through [M+H]+ is acrolein through [M+NH4]+, and the run
        searches both channels. Nothing else saw the neutral, so the count on it
        is the list's answer and the row is capped - which it can only be if the
        family was written onto the row before the cross-channel pass ran.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.cross_channel import (
            REASON_AMBIGUOUS_NITROGEN,
        )

        mz = 74.06004
        peaks = _peaks_df([("p1", mz, 10000.0)])
        dimethylformamide = _isotope_row(
            target_isotope_id="ref-iso-1",
            target_ion_id="ref-ion-1",
            target_compound_id=None,
            compound_formula="C3H7N1O1",
            ion_formula="C3H8N1O1+",
            mz=mz,
            relative_abundance=1.0,
            sample_peak_id="p1",
            sample_peak_intensity=10000.0,
            match_score=0.95,
            match_mz_error=0.2,
            ionization="[M+H]+",
            ionization_mechanism_id="im-1",
        )
        dimethylformamide["reference_identities"] = [
            {"name": "N,N-dimethylformamide", "source": "a seed list"}
        ]
        recorder = _Recorder()
        patches = _patches(recorder, peaks, [dimethylformamide])
        patches["mechanisms"] = patch(
            f"{_MOD}.fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(
                ["im-1", "im-2"],
                [
                    SimpleNamespace(
                        ionization_mechanism_id="im-1",
                        ionization_mechanism="[M+H]+",
                        ionization_mechanism_polarity="+",
                    ),
                    SimpleNamespace(
                        ionization_mechanism_id="im-2",
                        ionization_mechanism="[M+NH4]+",
                        ionization_mechanism_polarity="+",
                    ),
                ],
            ),
        )
        patches["ionizations"] = patch(
            f"{_MOD}._untargeted_ionization_notations",
            return_value=(
                ["[M+H]+", "[M+NH4]+"],
                {"[M+H]+": "im-1", "[M+NH4]+": "im-2"},
            ),
        )
        _start(patches)

        await _run(PeakAssignmentConfig(run_untargeted=False))

        (row,) = [r for r in recorder.rows if r["sample_peak_id"] == "p1"]
        assert row["assigned_formula"] == "C3H7N1O1"
        assert row["target_compound_id"] is None
        assert [
            (alternative["assigned_formula"], alternative["ionization_mechanism_id"])
            for alternative in row["alternatives"]
            if alternative.get("same_ion")
        ] == [("C3H4O", "im-2")]
        assert row["provenance"]["cross_channel"]["ambiguous_nitrogen"] == {
            "alternative": "C3H4O",
            "via": "[M+NH4]+",
        }
        assert row["tier"] == "candidate"
        assert REASON_AMBIGUOUS_NITROGEN in {
            reason["rule"] for reason in row["provenance"]["tier_reasons"]
        }
        cross_channel = recorder.recorded_configs()[-1]["cross_channel"]
        assert (cross_channel["capped"], cross_channel["capped_mirror"]) == (1, 1)


class TestTheListsAreReadThroughTheOpenedChannels:
    """A charge-transfer source declares electron transfer alone and opens
    methyl loss and proton transfer for itself. The reference lists are read
    through all three; the target library through the mode's alone."""

    CT = SimpleNamespace(
        ionization_mechanism_id="im-ct",
        ionization_mechanism="[M]+.",
        ionization_mechanism_polarity="+",
    )
    METHYL_LOSS = SimpleNamespace(
        ionization_mechanism_id="im-me",
        ionization_mechanism="[M-CH3]+",
        ionization_mechanism_polarity="+",
    )
    PROTON = SimpleNamespace(
        ionization_mechanism_id="im-h",
        ionization_mechanism="[M+H]+",
        ionization_mechanism_polarity="+",
    )

    @staticmethod
    def _d4(sample_peak_id, mz, relative_abundance, intensity):
        """D4's methyl-loss ion, the siloxane's base peak on this source,
        matched from the shipped cyclic-siloxane list."""
        row = _isotope_row(
            target_isotope_id=f"ref-iso-{sample_peak_id}",
            target_ion_id="ref-ion-1",
            target_compound_id=None,
            compound_formula="C8H24O4Si4",
            ion_formula="C7H21O4Si4+",
            mz=mz,
            relative_abundance=relative_abundance,
            sample_peak_id=sample_peak_id,
            sample_peak_intensity=intensity,
            match_score=0.95,
            match_mz_error=0.2,
            ionization="[M-CH3]+",
            ionization_mechanism_id="im-me",
        )
        row["reference_identities"] = [
            {"name": "octamethylcyclotetrasiloxane", "source": "cyclic-siloxanes"}
        ]
        return row

    def _start_with(self, recorder, peaks, match_rows):
        patches = _patches(recorder, peaks, match_rows)
        patches["mechanisms"] = patch(
            f"{_MOD}.fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(["im-ct"], [self.CT]),
        )
        # The deployment's rows for the profile's secondary channels. The
        # spectrum starts above every probe, so the source's silence there is
        # the window's and both channels are opened.
        patches["secondary"] = patch(
            f"{_MOD}.fetch_mechanisms_by_notation",
            new_callable=AsyncMock,
            return_value=[self.METHYL_LOSS, self.PROTON],
        )
        patches["ionizations"] = patch(
            f"{_MOD}._untargeted_ionization_notations",
            return_value=(
                ["[M]+.", "[M-CH3]+", "[M+H]+"],
                {"[M]+.": "im-ct", "[M-CH3]+": "im-me", "[M+H]+": "im-h"},
            ),
        )
        return _start(patches)

    @pytest.mark.asyncio
    async def test_the_lists_are_read_through_them_and_the_library_is_not(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 281.0512, 1.0e6)])
        recorder = _Recorder()
        mocks = self._start_with(
            recorder, peaks, [self._d4("p1", 281.0512, 1.0, 1.0e6)]
        )

        await _run(PeakAssignmentConfig(run_untargeted=False))

        snapshot = recorder.recorded_configs()[0]["resolved_profile"]
        assert snapshot["profile"] == "EASYIC_POS"
        # The workspace named its compounds for the mode it attached them to.
        assert mocks["known"].await_args.args[2] == ["im-ct"]
        # A list names a compound, not the channel it is seen through.
        assert [
            mechanism.ionization_mechanism_id
            for mechanism in mocks["reference"].await_args.args[2]
        ] == ["im-ct", "im-me", "im-h"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "silicon_line, tier", [(False, "candidate"), (True, "assigned")]
    )
    async def test_a_reading_through_one_is_held_as_the_search_s_are(
        self, silicon_line, tier
    ):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        specs = [("p1", 281.0512, 1.0e6)]
        rows = [self._d4("p1", 281.0512, 1.0, 1.0e6)]
        if silicon_line:
            # The 29Si line of its envelope, an isotopologue of its own.
            specs.append(("p2", 282.0508, 2.0e5))
            rows.append(self._d4("p2", 282.0508, 0.2, 2.0e5))
        recorder = _Recorder()
        self._start_with(recorder, _peaks_df(specs), rows)

        await _run(PeakAssignmentConfig(run_untargeted=False))

        (row,) = [r for r in recorder.rows if r["sample_peak_id"] == "p1"]
        assert row["assigned_formula"] == "C8H24O4Si4"
        assert row["ionization_mechanism_id"] == "im-me"
        assert row["tier"] == tier
        assert row["provenance"]["minor_channel"] == {
            "corroborated_by": "isotopologue" if silicon_line else None,
            "capped": not silicon_line,
        }
        # The siloxane's ion reads no other way the run searches, so the gate
        # keeps it as it keeps any such row.
        assert row["provenance"]["partner_gate"]["partner"] is False


class TestTheLaddersAreClimbedThroughTheOpenedChannels:
    """The reagent pre-pass's ladder claim reads the channels the run opens:
    an ethanol less a hydride holding a second ethanol is the source's ion
    only where the run abstracts a hydride, which a charge-transfer mode does
    not declare (step 3.3d)."""

    HYDRIDE = SimpleNamespace(
        ionization_mechanism_id="im-hy",
        ionization_mechanism="[M-H]+",
        ionization_mechanism_polarity="+",
    )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("abstracts", [True, False], ids=["hydride", "none"])
    async def test_a_hydride_bound_rung_is_claimed_where_the_run_abstracts_one(
        self, abstracts
    ):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.reagents import ion_mz

        opened = TestTheListsAreReadThroughTheOpenedChannels
        peaks = _peaks_df(
            [
                ("core", ion_mz("C2H5O", 1), 1.0e6),
                ("rung", ion_mz("C4H11O2", 1), 5.0e5),
            ]
        )
        recorder = _Recorder()
        patches = _patches(recorder, peaks, [])
        patches["mechanisms"] = patch(
            f"{_MOD}.fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(["im-ct"], [opened.CT]),
        )
        patches["secondary"] = patch(
            f"{_MOD}.fetch_mechanisms_by_notation",
            new_callable=AsyncMock,
            return_value=[opened.PROTON] + ([self.HYDRIDE] if abstracts else []),
        )
        _start(patches)

        await _run(PeakAssignmentConfig(run_untargeted=False))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert (by_peak["rung"]["role"] == "reagent") is abstracts
        assert by_peak["core"]["role"] != "reagent"


class TestTheRunRow:
    @pytest.mark.asyncio
    async def test_it_names_this_engine_and_its_version(self):
        from mascope_backend.api.new.peak_assignments.config import (
            IN_APP_ENGINE,
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.service import _create_run

        session = MagicMock()
        session.commit = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(f"{_MOD}.async_session", return_value=ctx):
            run = await _create_run("si-1", PeakAssignmentConfig())

        session.add.assert_called_once_with(run)
        # The number, not the imported constant: two runs are comparable only
        # under the same engine, so the version moves on purpose and this test
        # moves with it.
        assert (run.engine, run.engine_version) == (IN_APP_ENGINE, "0.5.0")


class TestRunFinalization:
    @pytest.mark.asyncio
    async def test_successful_run_is_finalized_completed(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["finalize"].assert_awaited_once()
        assert mocks["finalize"].await_args.args[:2] == ("run-1", "completed")
        # No curve was loaded (the store mock returns None), so the run records none.
        assert recorder.recorded_calibrations() == [None]

    @pytest.mark.asyncio
    async def test_the_curve_the_run_applied_is_recorded_on_it(self):
        """The calibration record is written once, on the run, in the same
        transaction as the ledger - the rows carry only the P(correct) values
        read off it.

        Committed with the rows rather than at finalize because a run that is
        interrupted in between keeps its ledger: the startup reaper marks it
        failed and a read that names it by id still serves those rows, so a
        curve recorded only on the completed path would be missing from exactly
        the ledgers that outlived their run.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.calibration import Calibration

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        mocks["calibration"].return_value = Calibration(
            a=5.74, b=-3.36, instrument="orbi", provisional=True, source="unit test"
        )

        await _run(PeakAssignmentConfig(run_untargeted=False))

        assert recorder.recorded_calibrations() == [
            {
                "instrument": "orbi",
                "provisional": True,
                "source": "unit test",
            }
        ]
        # Finalize is left with nothing to say about the curve.
        assert "confidence_calibration" not in mocks["finalize"].await_args.kwargs
        for row in recorder.rows:
            provenance = row.get("provenance") or {}
            assert "calibration" not in provenance
            assert "calibrated" not in provenance

    @pytest.mark.asyncio
    async def test_failing_stage_finalizes_the_run_failed(self):
        """A crash must not leave the run 'running' - that is the stuck-run path.

        The startup reaper exists because this can still be missed (a killed
        worker never reaches any finalizer), but an ordinary exception must not
        need it.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0)])
        recorder = _Recorder()
        patches = _patches(recorder, peaks, _stage_a_rows())
        mocks = _start(patches)
        mocks["matcher"].side_effect = RuntimeError("matcher exploded")

        await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["finalize"].assert_awaited_once()
        run_id, status = mocks["finalize"].await_args.args[:2]
        assert (run_id, status) == ("run-1", "failed")

    @pytest.mark.asyncio
    async def test_cancelled_run_is_finalized_cancelled(self):
        """A cancelled run must reach a terminal state without a restart.

        CancelledError is a BaseException, so the ordinary failure handler
        never sees it - a mid-flight cancel used to leave the row 'running'
        forever on a worker that never restarts, invisible to the read model
        and refused by the retention prune. The cancellation itself must still
        propagate to the caller, and the in-flight claim must be released.
        """
        import asyncio

        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.service import (
            _sample_assignments_in_flight,
            assign_sample_peaks,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))

        started = asyncio.Event()

        async def _hang(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        mocks["matcher"].side_effect = _hang

        task = asyncio.create_task(
            assign_sample_peaks(
                sample_item_id="si-1",
                config=PeakAssignmentConfig(run_untargeted=False),
                independent_transaction=True,
                user_id=None,
                process_id="proc-1",
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        mocks["finalize"].assert_awaited_once()
        assert mocks["finalize"].await_args.args[:2] == ("run-1", "cancelled")
        assert "si-1" not in _sample_assignments_in_flight

    @pytest.mark.asyncio
    async def test_failure_before_the_first_stage_finalizes_the_run_failed(self):
        """The run record must be covered by the finalizer from the moment it
        exists.

        The gap this pins: notification construction sits between run creation
        and the assignment stages, and a crash there used to leak the run as
        'running' until the startup reaper.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        with patch(
            f"{_MOD}.UserNotification",
            side_effect=RuntimeError("notification exploded"),
        ):
            await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["finalize"].assert_awaited_once()
        assert mocks["finalize"].await_args.args[:2] == ("run-1", "failed")

    @pytest.mark.asyncio
    async def test_failure_propagates_to_a_batch_caller(self):
        """The batch counts failures by catching, so the error must reach it.

        ``api_controller_background_task`` only re-raises for a nested call - the
        shape the batch uses (``independent_transaction=False`` with a
        ``parent_id``). If that ever changed, every failing sample would be
        silently counted as assigned.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.service import (
            assign_sample_peaks,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        mocks["matcher"].side_effect = RuntimeError("matcher exploded")

        with pytest.raises(Exception):
            await assign_sample_peaks(
                sample_item_id="si-1",
                config=PeakAssignmentConfig(run_untargeted=False),
                independent_transaction=False,
                user_id=None,
                process_id="proc-1",
                parent_id="batch-proc",
            )

        assert mocks["finalize"].await_args.args[:2] == ("run-1", "failed")


class TestResolvedProfile:
    """The chemistry a run searched under is recorded on the run.

    A run whose config says ``profile: "auto"`` records nothing about what auto
    meant unless the resolution is snapshotted beside it, and the presets are
    library data that will be revised. Without the snapshot two runs months
    apart would carry identical configs and incomparable results.
    """

    @pytest.mark.asyncio
    async def test_stage_a_stands_in_the_class_width_the_run_resolved(self):
        # Where the target library matches too few lines to fit a width, the
        # untargeted stage and the gate stand in the instrument class's. Stage A
        # has to be told the same width, or it falls back to the fit score's
        # generic 2 ppm, which on an Orbitrap is several times the instrument's.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.engine import (
            STAGE_A_FIT_MIN_IONS,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        snapshot = recorder.recorded_configs()[0]["resolved_profile"]
        assert snapshot["fallback_sigma_ppm"] == 0.3  # the sample file is a .raw
        # The generic positive preset has no reagent, so no pre-pass reading.
        assert mocks["fit"].call_args.kwargs == {
            "fallback_sigma_ppm": 0.3,
            "reagent_offset": None,
            "min_anchor_ions": STAGE_A_FIT_MIN_IONS,
            # Fitted off the frame (the run's v1): only a decoy run scores at
            # another's model.
            "model": None,
            # The first pass reads today's numbers, since the tolerance is
            # fitted on its commits; only a decoy reads its control's.
            "envelope_tolerance": None,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filename, instrument", [("orbi-sample.raw", "orbi"), ("tof-sample.h5", "tof")]
    )
    async def test_the_search_stands_in_its_own_instrument_s_width(
        self, filename, instrument
    ):
        # A library too thin to fit a width: the untargeted stage is scored at
        # the class width of the sample's own instrument, which the run reads
        # off the sample (here its name) and hands the library's derivation.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.mass_accuracy import PRED_SIGMA_PPM
        from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 300.1234, 500.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, []))
        mocks["fetch_sample"].return_value.filename = filename

        await _run(PeakAssignmentConfig())

        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.sigma_ppm == pytest.approx(
            math.hypot(INSTRUMENT_FALLBACK_SIGMA_PPM[instrument], PRED_SIGMA_PPM)
        )

    @pytest.mark.asyncio
    async def test_the_search_matches_envelopes_at_the_files_resolving_power(self):
        # A TOF peak at the 13C position holds the 15N, 2H and 17O lines too;
        # the finder can merge them only if the run hands it the file's
        # resolving power, and the run records that it did.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        def resolution(mz):
            return 8000.0

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 300.1234, 500.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, []))
        mocks["resolution"].return_value = resolution

        await _run(PeakAssignmentConfig())

        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.resolution is resolution
        recorded = recorder.recorded_configs()[-1]["measurement_model"]["scoring"]
        assert recorded["lines_merged_at_resolution"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("column", [True, False], ids=["with S/N", "without"])
    async def test_the_run_records_the_signal_to_noise_it_scored_on(self, column):
        # The frame the search scores over is the one whose column is ranked:
        # a TOF peak list written before the picker's fix does not track
        # height, and the run is where that has to show.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        intensities = [1000.0 * (index + 1) for index in range(40)]
        peaks = _peaks_df(
            [
                (f"p{index}", 150.0 + 3.1 * index, intensity)
                for index, intensity in enumerate(intensities)
            ]
        )
        if column:
            peaks["signal_to_noise"] = [intensity / 50.0 for intensity in intensities]
        recorder = _Recorder()
        _start(_patches(recorder, peaks, []))

        await _run(PeakAssignmentConfig())

        recorded = recorder.recorded_configs()[-1]["measurement_model"]["scoring"]
        if column:
            assert recorded["snr_source"] == "peak_list"
            assert recorded["snr_peaks"] == 40
            assert recorded["snr_intensity_spearman"] == pytest.approx(1.0)
        else:
            assert recorded["snr_source"] == "none"
            assert recorded["snr_intensity_spearman"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("counted", [True, False], ids=["counted", "uncounted"])
    async def test_the_search_reads_the_peak_lists_scan_counts(self, counted):
        # How many of its scans hold each peak decides how far a dim line's
        # height is diluted: the search is handed the counts where the file
        # keeps them, and nothing in their place where it does not.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df(
            [(f"p{index}", 150.0 + 3.1 * index, 1000.0) for index in range(5)]
        )
        peaks["signal_to_noise"] = 20.0
        if counted:
            peaks["n_scans"] = [4.0, 2.0, 1.0, 4.0, np.nan]
            peaks["n_scans_total"] = 4.0
        mocks = _start(_patches(_Recorder(), peaks, []))

        await _run(PeakAssignmentConfig(run_untargeted=True))

        mocks["compositions"].assert_called_once()
        frame = mocks["compositions"].call_args.args[0]
        if counted:
            assert {"n_scans", "n_scans_total"} <= set(frame.columns)
            assert frame["n_scans"].tolist()[:4] == [4.0, 2.0, 1.0, 4.0]
            assert (frame["n_scans_total"] == 4.0).all()
        else:
            assert not {"n_scans", "n_scans_total"} & set(frame.columns)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "library", [True, False], ids=["two library lines", "no library match"]
    )
    async def test_a_thin_library_is_scored_at_the_reagent_lines_offset(self, library):
        # Two library lines, or none, are too few to fit an offset, and this
        # uronium source's own ions all sit 1.3 ppm low. Stage A scores its ions
        # there, the untargeted stage is scored there, and the run says why.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.reagents import reagent_library

        ladder = {cluster.label: cluster.mz for cluster in reagent_library("UR")}
        low = 1.0 - 1.3e-6
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 300.1234, 500.0),
                ("r1", ladder["[CH4N2O+H]+"] * low, 2.0e6),
                ("r2", ladder["[(CH4N2O)2+H]+"] * low, 3.0e6),
                ("r3", ladder["[(CH4N2O)3+H]+"] * low, 1.0e5),
            ]
        )
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows() if library else []))

        await _run(PeakAssignmentConfig(profile="UR"))

        if library:
            offset = mocks["fit"].call_args.kwargs["reagent_offset"]
            assert (offset.lines, offset.beyond_width) == (3, True)
            assert offset.mu_ppm == pytest.approx(-1.3, abs=1e-6)
        # The search's commits are measured again as ions at the same model,
        # centred where Stage A was.
        seeded = mocks["seeded"].call_args.kwargs["model"]
        assert seeded.mu_source == "reagent"
        assert (seeded.reagent.lines, seeded.reagent.beyond_width) == (3, True)
        assert seeded.scoring_mu_ppm == pytest.approx(-1.3, abs=1e-6)
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.mu_ppm == pytest.approx(-1.3, abs=1e-6)
        recorded = _ranked_at(recorder.recorded_configs()[-1])
        assert recorded["mu_source"] == "reagent"
        assert recorded["sigma_source"] == "instrument_class"
        assert recorded["reagent_lines"] == 3
        assert recorded["reagent_mu_ppm"] == pytest.approx(-1.3, abs=1e-4)
        # The reagent's own peaks are still the pre-pass's, not the search's.
        # The library's monoisotopic peak is searched, with its reading.
        searched = set(mocks["compositions"].call_args.kwargs["targets"])
        assert searched == (
            {181.0707, 300.1234} if library else {181.0707, 182.0741, 300.1234}
        )

    @pytest.mark.asyncio
    async def test_the_re_score_is_centred_where_the_library_fitted(self):
        # A library that fits an offset of its own scores Stage A and the
        # search there, wherever the reagent lines sit. The re-score the rows
        # are tiered on is centred there too, not on the lines' -1.3.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.engine import (
            SampleMassAccuracy,
        )
        from mascope_tools.composition.reagents import reagent_library

        ladder = {cluster.label: cluster.mz for cluster in reagent_library("UR")}
        low = 1.0 - 1.3e-6
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 300.1234, 500.0),
                ("r1", ladder["[CH4N2O+H]+"] * low, 2.0e6),
                ("r2", ladder["[(CH4N2O)2+H]+"] * low, 3.0e6),
                ("r3", ladder["[(CH4N2O)3+H]+"] * low, 1.0e5),
            ]
        )
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        _start(
            {
                "accuracy": patch(
                    f"{_MOD}.sample_mass_accuracy",
                    side_effect=lambda df, reagent, **_kwargs: SampleMassAccuracy(
                        mu_ppm=0.2, sigma_ppm=0.3, anchors=9, reagent=reagent
                    ),
                )
            }
        )

        await _run(PeakAssignmentConfig(profile="UR"))

        assert mocks["fit"].call_args.kwargs["reagent_offset"].mu_ppm == (
            pytest.approx(-1.3, abs=1e-6)
        )
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.mu_ppm == pytest.approx(0.2)
        seeded = mocks["seeded"].call_args.kwargs["model"]
        assert seeded.scoring_mu_ppm == pytest.approx(0.2)
        assert seeded.mu_source == "fitted"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "context, ceiling",
        [
            ("ambient-air", "shipped"),
            # The identity context sets no ceiling: each source is bounded by
            # its own row alone.
            ("none", None),
        ],
    )
    async def test_stage_a_matches_the_reference_under_the_resolved_contexts_ceiling(
        self, context, ceiling
    ):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition.profiles import KNOWN_WINDOW_CEILING

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))

        def resolution(mz):
            return 60000.0

        mocks["resolution"].return_value = resolution

        await _run(PeakAssignmentConfig(run_untargeted=False, context=context))

        expected = KNOWN_WINDOW_CEILING if ceiling == "shipped" else None
        assert mocks["reference"].await_args.kwargs == {
            "known_window": expected,
            # The run's own reading of its file's resolving power, which the
            # known set's lines are resolved at (MAS-977).
            "file_lines": FileLines("HIGH", resolution),
        }
        snapshot = recorder.recorded_configs()[0]["resolved_profile"]
        assert snapshot["known_window"] == (
            None if expected is None else expected.to_json()
        )

    @pytest.mark.asyncio
    async def test_the_resolution_is_stamped_on_the_run(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        # Twice: once before the stages, so a run that fails still says what it
        # would have searched, and once after the ledger is built, which is the
        # earliest the run's own mass calibration can be measured. Both carry
        # the resolution and it does not change between them.
        configs = recorder.recorded_configs()
        assert len(configs) == 2
        assert configs[0]["resolved_profile"] == configs[1]["resolved_profile"]
        assert "measurement_model" not in configs[0]
        assert configs[1]["measurement_model"]["gate"]["committed"] == len(
            _stage_a_rows()
        )
        snapshot = configs[0]["resolved_profile"]
        # '[M+H]+' is diagnostic of nothing, so a positive sample falls back to
        # the generic positive preset - and says that it did.
        assert snapshot["profile"] == "ESI_POS"
        assert snapshot["requested_profile"] == "auto"
        assert snapshot["element_ranges_source"] == "profile"
        assert snapshot["mz_precision_ppm"] == 3.0  # the sample file is a .raw

    @pytest.mark.asyncio
    async def test_it_is_stamped_even_when_the_untargeted_stage_never_runs(self):
        # A run has to say what it would have searched: the config is what a
        # later reader compares two runs on, whether or not Stage B fired.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, []))

        await _run(PeakAssignmentConfig(run_untargeted=False))

        assert "resolved_profile" in recorder.recorded_configs()[0]

    @pytest.mark.asyncio
    async def test_the_identity_profile_searches_what_it_always_did(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df([("p1", 181.0707, 10000.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, []))

        await _run(PeakAssignmentConfig(profile="none"))

        search_config = mocks["compositions"].call_args.args[1]
        heuristics = mocks["compositions"].call_args.args[2]
        assert search_config.element_count_ranges == "C0-100 H0-100 O0-100 N0-100"
        assert search_config.mass_range_ppm == 10.0
        assert heuristics.use_senior is True
        assert heuristics.context_ratio_windows == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "instrument, fitted, window, source",
        [
            # Step 3.6: a TOF's window bounded by its fitted width, the centre
            # reached and six widths on: 1.0 + 6 * hypot(1.0, 0.5).
            ("tof", True, 1.0 + 6 * math.hypot(1.0, 0.5), "fitted_width"),
            # Nothing fitted, nothing to bound by: the class's 10 ppm.
            ("tof", False, 10.0, "profile"),
            # An Orbitrap's window is not bounded.
            ("orbi", True, 3.0, "profile"),
        ],
    )
    async def test_a_tofs_window_is_bounded_by_its_fitted_width(
        self, instrument, fitted, window, source, monkeypatch
    ):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.engine import (
            SampleMassAccuracy,
        )
        from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM

        # A class width narrow enough to bound the window, were a class width
        # what bounds it: only a width the sample fitted does.
        monkeypatch.setitem(INSTRUMENT_FALLBACK_SIGMA_PPM, "tof", 0.5)

        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 300.1234, 500.0),
            ]
        )
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
        accuracy = (
            SampleMassAccuracy(mu_ppm=-1.0, sigma_ppm=1.0, anchors=9, anchor_ions=9)
            if fitted
            else SampleMassAccuracy()
        )
        _start(
            {
                "instrument": patch(
                    f"{_MOD}.get_instrument_type", return_value=instrument
                ),
                # The stage list reads the class off the file name too.
                "stage_instrument": patch(
                    f"{_PIPE}.get_instrument_type", return_value=instrument
                ),
                "accuracy": patch(
                    f"{_MOD}.sample_mass_accuracy",
                    side_effect=lambda df, reagent, **_kwargs: replace(
                        accuracy, reagent=reagent
                    ),
                ),
            }
        )

        await _run(PeakAssignmentConfig(profile="BR"))

        search_config = mocks["compositions"].call_args.args[1]
        assert search_config.mass_range_ppm == pytest.approx(window, abs=1e-3)
        snapshot = recorder.recorded_configs()[-1]["resolved_profile"]
        assert snapshot["mz_precision_source"] == source
        assert snapshot["mz_precision_ppm"] == pytest.approx(window, abs=1e-3)
        if source == "fitted_width":
            assert snapshot["fitted_window"]["class_mz_precision_ppm"] == 10.0
            assert snapshot["fitted_window"]["sigmas"] == 6.0
        else:
            assert snapshot["fitted_window"] is None

    @pytest.mark.asyncio
    async def test_a_named_profile_configures_the_search(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_tools.composition import profiles as presets

        peaks = _peaks_df([("p1", 181.0707, 10000.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, []))

        await _run(PeakAssignmentConfig(profile="BR"))

        search_config = mocks["compositions"].call_args.args[1]
        heuristics = mocks["compositions"].call_args.args[2]
        assert search_config.element_count_ranges == presets.resolve_element_ranges(
            presets.BR, presets.AMBIENT_AIR
        )
        assert search_config.mass_range_ppm == 3.0
        assert heuristics.context_ratio_windows == (presets.AMBIENT_AIR.ratio_windows())


# Protonated neutrals the identity grid reaches, chosen so a context has
# something to act on: oxalic and citric acid, a nitrogen-rich base past ambient
# air's N cap, nitric acid with no carbon at all, and two terpenoids.
_IDENTITY_PEAKS = [
    ("p1", 91.00259, 20000.0),  # C2H2O4 + H+
    ("p2", 193.03428, 15000.0),  # C6H8O7 + H+
    ("p3", 127.07267, 12000.0),  # C3H6N6 + H+
    ("p4", 155.10666, 18000.0),  # C9H14O2 + H+
    ("p5", 169.12231, 9000.0),  # C10H16O2 + H+
    ("p6", 64.00292, 7000.0),  # HNO3 + H+
]


async def _untargeted_ledger(config, *, without_context_layer=False) -> str:
    """The ledger one run writes through the real untargeted search, as bytes.

    The composition search runs unfaked, so its grid and its heuristic rules are
    the ones the run resolved. ``without_context_layer`` takes the chemistry
    context out of the untargeted search at the two places it reaches there: the
    grid its caps narrow and the ratio gate in the heuristic rules. What is left
    is that search before contexts existed. Stage A's reference fetch is faked
    (``_patches``), so the ceiling a context sets over it runs in neither
    ledger; ``test_stage_a_matches_the_reference_under_the_resolved_contexts_ceiling``
    pins that ceiling.
    """
    import json

    from mascope_backend.api.new.peak_assignments import profiles as resolution
    from mascope_tools.composition import heuristic_filter
    from mascope_tools.composition.models import HeuristicFilterConfig
    from mascope_tools.composition.profiles import ChemistryContext

    recorder = _Recorder()
    patches = _patches(recorder, _peaks_df(_IDENTITY_PEAKS), [])
    del patches["compositions"]
    if without_context_layer:
        patches["context"] = patch.object(
            resolution,
            "get_chemistry_context",
            lambda name: ChemistryContext(name="none", label="None", known_window=None),
        )
        patches["grid"] = patch.object(
            resolution,
            "resolve_element_ranges",
            lambda profile, context, min_carbon=None: profile.element_ranges,
        )
        patches["filter"] = patch.object(
            resolution.ResolvedProfile,
            "heuristics_config",
            lambda self, *, use_senior=True: HeuristicFilterConfig(
                use_senior=use_senior
            ),
        )
        patches["rules"] = patch.object(
            heuristic_filter,
            "HEURISTIC_RULES",
            [
                rule
                for rule in heuristic_filter.HEURISTIC_RULES
                if rule is not heuristic_filter.rule_context_ratios
            ],
        )
    _start(patches)
    try:
        await _run(config)
    finally:
        patch.stopall()
    text = json.dumps(recorder.rows, sort_keys=True, default=str)
    # A row's id is drawn fresh on every run; it is relabelled by the order the
    # rows were written in, wherever it is referenced, so two runs compare on
    # everything else.
    for index, row in enumerate(recorder.rows):
        text = text.replace(row["peak_assignment_id"], f"assignment-{index}")
    return text


class TestTheIdentityContext:
    """``context = none`` with no profile bound leaves the ledger as it was.

    The chemistry profiles note (section 7) asks for this as a regression test,
    not a hope: a run that names neither a reagent profile nor a matrix writes
    the rows, byte for byte, that the engine wrote before contexts existed.
    """

    @pytest.mark.asyncio
    async def test_the_ledger_is_byte_identical_to_the_engine_without_contexts(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        config = PeakAssignmentConfig(profile="none", context="none")

        identity = await _untargeted_ledger(config)
        before_contexts = await _untargeted_ledger(config, without_context_layer=True)

        assert identity == before_contexts

    @pytest.mark.asyncio
    async def test_a_context_changes_the_same_ledger(self):
        # Without this the identity above could hold because nothing on these
        # peaks is anything a context acts on.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        identity = await _untargeted_ledger(
            PeakAssignmentConfig(profile="none", context="none")
        )
        ambient = await _untargeted_ledger(
            PeakAssignmentConfig(profile="none", context="ambient-air")
        )

        assert ambient != identity


def _bright_ion_rows(error_ppm: float = 0.6) -> list[dict]:
    """One bright ion matching ten lines of its own, every one ``error_ppm`` off.

    Ten lines are more than a fit asks for, and all of them are one ion's own
    mass error.
    """
    abundances = [1.0, 0.6, 0.4, 0.25, 0.15, 0.1, 0.06, 0.04, 0.025, 0.015]
    return [
        _isotope_row(
            target_isotope_id=f"ti-br-{index}",
            target_ion_id="ion-br",
            target_compound_id="tc-br",
            compound_formula="C20H38O2",
            ion_formula="C20H39O2+",
            mz=311.2945 + 1.00335 * index,
            relative_abundance=abundance,
            sample_peak_id=f"b{index}",
            sample_peak_intensity=1.0e5 * abundance,
            match_mz_error=error_ppm,
        )
        for index, abundance in enumerate(abundances)
    ]


class TestStageAFitsOnIons:
    """Stage A fits a sample's offset and width only on lines of enough ions.

    One ion's lines share its own mass error. Counted as lines, a bright ion
    with ten of them would calibrate the run on itself: its error the offset,
    its lines' agreement the width, and every other row of both stages judged
    there.
    """

    @pytest.mark.asyncio
    async def test_a_bright_ion_s_lines_alone_do_not_calibrate_the_run(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.engine import (
            STAGE_A_FIT_MIN_IONS,
            score_ions_by_fit,
        )
        from mascope_tools.composition.mass_accuracy import PRED_SIGMA_PPM

        rows = _bright_ion_rows()
        peaks = _peaks_df(
            [
                (row["sample_peak_id"], row["mz"], row["sample_peak_intensity"])
                for row in rows
            ]
            + [("p-rest", 400.5678, 500.0)]
        )
        recorder = _Recorder()
        patches = _patches(recorder, peaks, rows)
        patches.pop("fit")  # Stage A's own fit is what is under test
        mocks = _start(patches)

        await _run(PeakAssignmentConfig())

        # Scored at the class width and no offset, as a library that matched
        # too few lines is - not at its own error and the floor.
        frame = pd.DataFrame(rows)
        on_itself = score_ions_by_fit(frame, fallback_sigma_ppm=0.3)
        stood_in = score_ions_by_fit(
            frame, fallback_sigma_ppm=0.3, min_anchor_ions=STAGE_A_FIT_MIN_IONS
        )
        expected = float(stood_in["match_score"].iloc[0])
        assert expected < float(on_itself["match_score"].iloc[0]) - 0.05
        m0 = next(row for row in recorder.rows if row["sample_peak_id"] == "b0")
        assert m0["fit_score"] == pytest.approx(expected, abs=1e-4)
        # The untargeted stage is scored there too, and the run says why.
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.mu_ppm == 0.0
        assert scoring.sigma_ppm == pytest.approx(math.hypot(0.3, PRED_SIGMA_PPM))
        recorded = _ranked_at(recorder.recorded_configs()[-1])
        assert recorded["sigma_source"] == "instrument_class"
        assert recorded["mu_source"] == "none"
        assert _library_anchors(recorded) == (10, 1)

    @staticmethod
    async def _run_on_ions(ions: int):
        """Run Stage A's own fit over the same ten lines shared out among
        ``ions`` ions, and return what the untargeted stage was scored at and
        what the run recorded."""
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        rows = _bright_ion_rows()
        for index, row in enumerate(rows):
            row["target_ion_id"] = f"ion-{index % ions}"
            row["target_compound_id"] = f"tc-{index % ions}"
            row["relative_abundance"] = 1.0
        peaks = _peaks_df(
            [
                (row["sample_peak_id"], row["mz"], row["sample_peak_intensity"])
                for row in rows
            ]
            + [("p-rest", 400.5678, 500.0)]
        )
        recorder = _Recorder()
        patches = _patches(recorder, peaks, rows)
        patches.pop("fit")
        mocks = _start(patches)

        await _run(PeakAssignmentConfig())

        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        return scoring, _ranked_at(recorder.recorded_configs()[-1])

    @pytest.mark.asyncio
    async def test_lines_of_eight_ions_fit_the_sample(self):
        # Ten lines of eight ions measure the axis: the offset is theirs and the
        # run says it fitted it.
        scoring, recorded = await self._run_on_ions(8)

        assert scoring.mu_ppm == pytest.approx(0.6)
        assert recorded["sigma_source"] == "fitted"
        assert recorded["mu_source"] == "fitted"
        assert _library_anchors(recorded) == (10, 8)

    @pytest.mark.asyncio
    async def test_lines_of_seven_ions_do_not(self):
        # The same ten lines, more than a fit asks for, from seven ions: one
        # short of the eight Stage A asks for, so the sample is scored at the
        # stand-ins.
        from mascope_tools.composition.mass_accuracy import PRED_SIGMA_PPM

        scoring, recorded = await self._run_on_ions(7)

        assert scoring.mu_ppm == 0.0
        assert scoring.sigma_ppm == pytest.approx(math.hypot(0.3, PRED_SIGMA_PPM))
        assert recorded["sigma_source"] == "instrument_class"
        assert recorded["mu_source"] == "none"
        assert _library_anchors(recorded) == (10, 7)


def _ranked_at(config: dict) -> dict:
    """The model a run's search ranked at, as its record keeps it: the v1
    beside the v2 the run went on to, or the model itself."""
    model = config["measurement_model"]
    return model.get("ranked_at") or model


def _library_anchors(record: dict) -> tuple[int, int]:
    """The library lines and ions a v1 model was fitted on."""
    anchors = record["anchors"]
    return anchors["library_lines"], anchors["library_ions"]


def _censored_library(
    ions: int = 25, *, ratio: float = 0.8, error_ppm: float = 2.0
) -> list[dict]:
    """``ions`` library compounds, each committed with its 13C line ``ratio``
    of its share at an expected S/N of 15, every line ``error_ppm`` off.

    Plus one more whose 13C line the match parameters turned away (a zero
    match score), which the fit reads as absent: the frame it reads is the
    scored one, where that line's intensity is zeroed, not the gated one.
    """
    rows = []
    for index in range(ions + 1):
        carbons = 10 + index
        hydrogens = 2 * carbons - 4
        compound = f"C{carbons}H{hydrogens}O4"
        ion = f"C{carbons}H{hydrogens + 1}O4+"
        mz = 199.0 + 14.01565 * index
        share = 0.11
        for position, (isotope, abundance, intensity) in enumerate(
            [
                (ion, 1.0, 1e5),
                (f"C{carbons - 1}[13C]H{hydrogens + 1}O4+", share, 1e5 * share * ratio),
            ]
        ):
            row = _isotope_row(
                target_isotope_id=f"ti-{index}-{position}",
                target_ion_id=f"ion-{index}",
                target_compound_id=f"tc-{index}",
                compound_formula=compound,
                ion_formula=ion,
                mz=mz + 1.00335 * position,
                relative_abundance=abundance,
                sample_peak_id=f"c{index}-{position}",
                sample_peak_intensity=intensity,
                match_mz_error=error_ppm,
            )
            row["target_isotope_formula"] = isotope
            row["signal_to_noise"] = 15.0 / share * intensity / 1e5
            if index == ions and position == 1:
                row["match_score"] = 0.0
            rows.append(row)
    return rows


class TestTheRunFitsItsEnvelopeTolerance:
    """Stage A fits the tolerance on what it committed, and every score of the
    run reads it: Stage A's own rows, the search's ranking, the seeded
    re-score of the search's rows. The run records it."""

    @staticmethod
    async def _run_on(rows):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        peaks = _peaks_df(
            [
                (row["sample_peak_id"], row["mz"], row["sample_peak_intensity"])
                for row in rows
            ]
            + [("p-rest", 600.5678, 500.0)]
        )
        recorder = _Recorder()
        patches = _patches(recorder, peaks, rows)
        patches.pop("fit")  # Stage A's own fit, at both tolerances, is under test
        mocks = _start(patches)

        await _run(PeakAssignmentConfig())

        return recorder, mocks

    @pytest.mark.asyncio
    async def test_a_censored_library_fits_a_curve_every_score_reads(self):
        from mascope_backend.api.new.peak_assignments.engine import (
            STAGE_A_FIT_MIN_IONS,
            score_ions_by_fit,
        )
        from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance

        rows = _censored_library()
        recorder, mocks = await self._run_on(rows)

        recorded = recorder.recorded_configs()[-1]["envelope_tolerance"]
        # Every library line 2 ppm off: Stage A's own centre, which the gate
        # is read about, takes them in.
        assert (recorded["form"], recorded["level"]) == ("curve", "own")
        assert (recorded["lines"], recorded["absent"]) == (25, 1)
        assert recorded["bins"][1]["lower"] == pytest.approx(0.8)
        assert recorded["acquisition"] is None
        tolerance = EnvelopeTolerance.from_record(recorded)

        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.envelope_tolerance == tolerance
        assert mocks["seeded"].await_args.kwargs["model"].envelope_tolerance == (
            tolerance
        )

        frame = pd.DataFrame(rows)
        fitted, today = (
            score_ions_by_fit(
                frame,
                fallback_sigma_ppm=0.3,
                min_anchor_ions=STAGE_A_FIT_MIN_IONS,
                envelope_tolerance=at,
            )
            for at in (tolerance, None)
        )
        m0 = next(row for row in recorder.rows if row["sample_peak_id"] == "c0-0")
        assert m0["fit_score"] == pytest.approx(
            float(fitted["match_score"].iloc[0]), abs=1e-4
        )
        assert m0["fit_score"] > float(today["match_score"].iloc[0]) + 0.2

    @pytest.mark.asyncio
    async def test_a_sample_no_library_matched_can_take_its_neighbours_curve(self):
        # Stage A committed nothing, so there is nothing of its own to score
        # again; the pooled curve still reaches the search and is recorded.
        import numpy as np

        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.envelope_fit import (
            RunEnvelopeFit,
        )
        from mascope_tools.composition.envelope_tolerance import (
            FORM_CURVE,
            EnvelopeLines,
            fit_envelope_tolerance,
        )

        settings = {"polarity": "+", "microscans": 10.0, "resolution": 120000.0}
        pooled = RunEnvelopeFit(
            fit_envelope_tolerance(
                FORM_CURVE,
                EnvelopeLines(),
                [EnvelopeLines(np.full(30, 15.0), np.full(30, 0.75))],
            ),
            settings,
        )
        peaks = _peaks_df([("p-rest", 600.5678, 500.0)])
        recorder = _Recorder()
        mocks = _start(_patches(recorder, peaks, []))
        patch(
            f"{_MOD}.fit_run_envelope_tolerance",
            new_callable=AsyncMock,
            return_value=pooled,
        ).start()

        await _run(PeakAssignmentConfig())

        recorded = recorder.recorded_configs()[-1]["envelope_tolerance"]
        assert (recorded["level"], recorded["pooled_files"]) == ("pooled", 2)
        assert recorded["acquisition"] == settings
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.envelope_tolerance == pooled.tolerance
        assert mocks["finalize"].await_args.args[1] == "completed"

    @pytest.mark.asyncio
    async def test_a_thin_library_scores_at_today_s_numbers(self):
        from mascope_tools.composition.envelope_tolerance import DEFAULT_TOLERANCE

        rows = _censored_library(ions=8)
        recorder, mocks = await self._run_on(rows)

        recorded = recorder.recorded_configs()[-1]["envelope_tolerance"]
        assert (recorded["level"], recorded["lines"]) == ("default", 8)
        assert recorded["detect_snr"] == 3.0
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.envelope_tolerance == DEFAULT_TOLERANCE
        assert mocks["seeded"].await_args.kwargs["model"].envelope_tolerance == (
            DEFAULT_TOLERANCE
        )


class TestEligibilityGate:
    """The per-sample path refuses exactly what the batch partition skips.

    Fit scores are computed from mass errors, so a sample whose m/z
    calibration is unverified must not receive a confidently-tiered ledger;
    a blank sample has no peaks to assign. Both skips must be *returned*, not
    raised: the returned payload carries the _notification_data the
    decorator's success path turns into a reload, which a raised warning
    silently dropped. Neither may leave a run row behind.
    """

    @pytest.mark.asyncio
    async def test_unverified_calibration_is_refused_without_a_run(self):
        sample = _sample()
        sample.mz_calibration = {"verified": False}
        recorder = _Recorder()
        mocks = _start(_patches(recorder, _peaks_df([]), _stage_a_rows()))
        mocks["fetch_sample"].return_value = sample

        result = await _run()

        assert result["status"] == "skipped"
        assert "calibration" in result["message"]
        assert result["_notification_data"]["sample_item_id"] == "si-1"
        mocks["create_run"].assert_not_called()

    @pytest.mark.asyncio
    async def test_blank_sample_skip_delivers_notification_data(self):
        sample = _sample()
        sample.instrument_function_id = None
        recorder = _Recorder()
        mocks = _start(_patches(recorder, _peaks_df([]), _stage_a_rows()))
        mocks["fetch_sample"].return_value = sample

        result = await _run()

        assert result["status"] == "skipped"
        assert result["_notification_data"] == {
            "sample_batch_id": "sb-1",
            "sample_item_id": "si-1",
        }
        mocks["create_run"].assert_not_called()

    @pytest.mark.asyncio
    async def test_verified_calibration_proceeds(self):
        sample = _sample()
        sample.mz_calibration = {"verified": True}
        recorder = _Recorder()
        mocks = _start(
            _patches(recorder, _peaks_df([("p1", 181.0707, 10000.0)]), _stage_a_rows())
        )
        mocks["fetch_sample"].return_value = sample

        result = await _run()

        assert result["status"] == "success"
        mocks["create_run"].assert_called_once()


class TestSampleAdmissionControl:
    """One assignment per sample per worker.

    A run is CPU-bound and writes a full ledger, so a second concurrent run of
    the same sample buys nothing: it duplicates a run the user did not ask for
    while competing for the pool the interactive request path needs. The batch
    path has always refused a batch already in flight; this pins the same for
    the per-sample path, which a double-clicked "Assign peaks" reaches.

    Only the wrapper is under test, so the engine itself is replaced wholesale
    and just `fetch_sample` (reached on the refusal path, to name the sample) is
    faked.
    """

    @staticmethod
    def _sample():
        sample = MagicMock()
        sample.sample_item_name = "S1"
        return sample

    @pytest.mark.asyncio
    async def test_second_concurrent_run_of_one_sample_is_refused(self):
        import asyncio

        from mascope_backend.api.new.peak_assignments.service import (
            assign_sample_peaks,
        )

        release = asyncio.Event()
        started = asyncio.Event()

        async def _slow_run(**kwargs):
            started.set()
            await release.wait()
            return {"status": "success", "message": "done"}

        with (
            patch(f"{_MOD}.fetch_sample", AsyncMock(return_value=self._sample())),
            patch(f"{_MOD}._run_sample_assignment", side_effect=_slow_run),
            patch(f"{_MOD}.assignment_claim", _claim_stub()),
            # No durable run in the way: the refusal under test is the
            # in-flight set, one worker deep.
            patch(f"{_MOD}.in_flight_run_id", AsyncMock(return_value=None)),
        ):
            first = asyncio.create_task(
                assign_sample_peaks(
                    sample_item_id="si-1",
                    independent_transaction=True,
                    user_id=None,
                    process_id="proc-1",
                )
            )
            await started.wait()
            second = await assign_sample_peaks(
                sample_item_id="si-1",
                independent_transaction=True,
                user_id=None,
                process_id="proc-2",
            )
            release.set()
            await first

        assert second["status"] == "skipped"
        assert "already running" in second["message"]

    @pytest.mark.asyncio
    async def test_a_durable_non_terminal_run_is_refused_before_the_engine_starts(self):
        """The check that lets an import and an in-app assign refuse each other.

        The advisory claim cannot see an import: it belongs to one process, and
        an import assembles across several requests at a remote client's pace.
        So admission also asks the database whether this sample already has a
        run that has not reached an outcome - and here it does, even though the
        claim was free.
        """
        from mascope_backend.api.new.peak_assignments.service import (
            assign_sample_peaks,
        )

        engine = AsyncMock()
        with (
            patch(f"{_MOD}.fetch_sample", AsyncMock(return_value=self._sample())),
            patch(f"{_MOD}._run_sample_assignment", engine),
            patch(f"{_MOD}.assignment_claim", _claim_stub(acquired=True)),
            patch(
                f"{_MOD}.in_flight_run_id",
                AsyncMock(return_value="import-in-progress"),
            ),
        ):
            result = await assign_sample_peaks(
                sample_item_id="si-1",
                independent_transaction=True,
                user_id=None,
                process_id="proc-1",
            )

        engine.assert_not_called()
        assert result["status"] == "skipped"
        assert result["data"]["peak_assignment_run_id"] == "import-in-progress"

    @pytest.mark.asyncio
    async def test_the_claim_is_released_when_a_run_fails(self):
        """A failed run must not lock the sample out of being assigned again."""
        from mascope_backend.api.new.peak_assignments.service import (
            _sample_assignments_in_flight,
            assign_sample_peaks,
        )

        with (
            patch(f"{_MOD}.fetch_sample", AsyncMock(return_value=self._sample())),
            patch(f"{_MOD}._run_sample_assignment", side_effect=RuntimeError("boom")),
            patch(f"{_MOD}.assignment_claim", _claim_stub()),
        ):
            # Whether the decorator re-raises or reports the failure is its own
            # concern; what matters here is that the claim does not outlive the run.
            try:
                await assign_sample_peaks(
                    sample_item_id="si-1",
                    independent_transaction=True,
                    user_id=None,
                    process_id="proc-1",
                )
            except RuntimeError:
                pass

        assert "si-1" not in _sample_assignments_in_flight

    @pytest.mark.asyncio
    async def test_a_different_sample_is_not_blocked(self):
        import asyncio

        from mascope_backend.api.new.peak_assignments.service import (
            assign_sample_peaks,
        )

        release = asyncio.Event()
        started = asyncio.Event()

        async def _slow_run(**kwargs):
            started.set()
            await release.wait()
            return {"status": "success", "message": "done"}

        with (
            patch(f"{_MOD}.fetch_sample", AsyncMock(return_value=self._sample())),
            patch(f"{_MOD}._run_sample_assignment", side_effect=_slow_run),
            patch(f"{_MOD}.assignment_claim", _claim_stub()),
        ):
            first = asyncio.create_task(
                assign_sample_peaks(
                    sample_item_id="si-1",
                    independent_transaction=True,
                    user_id=None,
                    process_id="proc-1",
                )
            )
            await started.wait()
            release.set()
            other = await assign_sample_peaks(
                sample_item_id="si-2",
                independent_transaction=True,
                user_id=None,
                process_id="proc-2",
            )
            await first

        assert other["status"] != "skipped"

    @pytest.mark.asyncio
    async def test_claim_held_in_another_worker_is_refused_with_the_run(self):
        """A duplicate in a different process is refused and told which run.

        The in-flight set only sees this worker; the cross-process claim
        extends the refusal across workers, and the refusal reports the
        in-flight run it can see so the client can follow the run actually
        producing the ledger. Which run that is comes from durable run state,
        so that is the seam this stubs.
        """
        from mascope_backend.api.new.peak_assignments.service import (
            assign_sample_peaks,
        )

        engine = AsyncMock()
        with (
            patch(f"{_MOD}.fetch_sample", AsyncMock(return_value=self._sample())),
            patch(f"{_MOD}._run_sample_assignment", engine),
            patch(f"{_MOD}.assignment_claim", _claim_stub(acquired=False)),
            patch(
                f"{_MOD}.in_flight_run_id",
                AsyncMock(return_value="run-elsewhere"),
            ),
        ):
            result = await assign_sample_peaks(
                sample_item_id="si-1",
                independent_transaction=True,
                user_id=None,
                process_id="proc-1",
            )

        engine.assert_not_called()
        assert result["status"] == "skipped"
        assert "already running" in result["message"]
        assert result["data"]["peak_assignment_run_id"] == "run-elsewhere"


@pytest.mark.asyncio
async def test_the_run_records_the_setup_it_searched_under():
    """Every write of the run's config carries the version of the sample's
    ionization mode, and the calibration is looked up for it."""
    from mascope_backend.api.new.peak_assignments.config import (
        PeakAssignmentConfig,
    )

    stamp = {"ionization_mode_id": "mode-v2", "version": 2}
    peaks = _peaks_df([("p1", 181.0707, 10000.0)])
    recorder = _Recorder()
    mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
    setup = patch(f"{_PIPE}.sample_setup", new_callable=AsyncMock, return_value=stamp)
    setup.start()

    await _run(PeakAssignmentConfig(run_untargeted=False))

    configs = recorder.recorded_configs()
    assert len(configs) == 2
    assert [config["ionization_setup"] for config in configs] == [stamp, stamp]
    assert mocks["calibration"].await_args.kwargs["ionization_setup"] == stamp


@pytest.mark.asyncio
async def test_the_calibration_is_looked_up_for_the_profile_the_run_resolved():
    """The curve's record of its profile is compared with the run's own (MAS-63)."""
    from mascope_backend.api.new.peak_assignments.calibration_store import (
        StoredCalibration,
    )
    from mascope_backend.api.new.peak_assignments.config import (
        PeakAssignmentConfig,
    )

    peaks = _peaks_df([("p1", 181.0707, 10000.0)])
    recorder = _Recorder()
    mocks = _start(_patches(recorder, peaks, _stage_a_rows()))
    mocks["calibration"].return_value = StoredCalibration(
        a=5.74, b=-3.36, instrument="orbi", reagent_profile="another-profile"
    )

    await _run(PeakAssignmentConfig(run_untargeted=False))

    resolved = recorder.recorded_configs()[0]["resolved_profile"]["profile"]
    assert resolved and resolved != "another-profile"
    assert mocks["calibration"].await_args.kwargs["reagent_profile"] == resolved
    # and the run's record of the curve says it was fit under another one
    (record,) = recorder.recorded_calibrations()
    assert record["fit_profile"] == "another-profile"
    assert record["profile_mismatch"] is True


class TestTheRunTakesItsBatchsWord:
    """The reagent pre-pass claims a library ion its sample's batch decided
    present, where the file's own claim leaves it (step 4.2h): the urea
    trimer at a millionth of the base peak, under the pass's floor."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("decided", [True, False], ids=["decided", "not"])
    async def test_an_ion_the_batch_decided_present_is_claimed(self, decided):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.reagent_pass import (
            batch_species_from,
        )
        from mascope_tools.composition.reagents import ion_mz

        peaks = _peaks_df(
            [
                ("monomer", ion_mz("CH5N2O", 1), 1.0e9),
                ("dimer", ion_mz("C2H9N4O2", 1), 5.0e8),
                ("trimer", ion_mz("C3H13N6O3", 1), 1.0e3),
            ]
        )
        species = batch_species_from(
            {
                "species": {
                    "kind": "library",
                    "ion_formula": "C3H13N6O3+",
                    "label": "[(CH4N2O)3+H]+",
                },
                "verdict": "present",
                "held": None,
                "reads": {},
            },
            "bp-trimer",
        )
        recorder = _Recorder()
        patches = _patches(recorder, peaks, [])
        patches["mechanisms"] = patch(
            f"{_MOD}.fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(
                ["im-u"],
                [
                    SimpleNamespace(
                        ionization_mechanism_id="im-u",
                        ionization_mechanism="[M+CH4N2O+H]+",
                        ionization_mechanism_polarity="+",
                    )
                ],
            ),
        )
        patches["batch_species"] = patch(
            f"{_PIPE}.batch_species_for",
            new_callable=AsyncMock,
            return_value=[species] if decided else [],
        )
        mocks = _start(patches)

        await _run(PeakAssignmentConfig(run_untargeted=False))

        mocks["batch_species"].assert_awaited_once()
        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        trimer = by_peak["trimer"]
        assert (trimer["role"] == "reagent") is decided
        if decided:
            reagent = trimer["provenance"]["reagent"]
            assert reagent["reason"] == "batch_known_species"
            assert reagent["batch_known_species"]["batch_peak_id"] == "bp-trimer"
        # The file's own claims are its own either way.
        assert by_peak["monomer"]["role"] == "reagent"
        assert "reason" not in by_peak["monomer"]["provenance"]["reagent"]


class TestStageBWinnerConfidence:
    """The run measures its untargeted winners' shortlists only when asked
    (step 4.15), records what it found, and moves no tier."""

    WINNER = {
        "mz": 300.1234,
        "formula": "C16H21NO4",
        "ion": "C16H22NO4+",
        "isotope_label": "M0",
        "ionization_mechanism": "[M+H]+",
        "isotopic_pattern_score": 0.95,
        "composition_error_ppm": 0.3,
        "candidate_density": 2,
        "other_candidates": "C14H13N5O2, C18H17N3",
    }

    #: A secondary channel the run opened beside the sample's own adduct.
    OPENED = SimpleNamespace(
        ionization_mechanism_id="im-2",
        ionization_mechanism="[M+Na]+",
        ionization_mechanism_polarity="+",
    )

    def _start_with(self, recorder, *, opened=False):
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 300.1234, 500.0),
            ]
        )
        patches = _patches(
            recorder, peaks, _stage_a_rows(), pd.DataFrame([self.WINNER])
        )
        # The shortlist pass reads the sample file through the seeded chain;
        # here it states what it found: one entry on the winner's own peak, one
        # whose monoisotopic line sits on another peak.
        patches["shortlist"] = patch(
            "mascope_backend.api.new.peak_assignments.alternatives_scoring.score_seeds",
            new_callable=AsyncMock,
            return_value=(
                {
                    ("C14H13N5O2", "im-1"): "ion-a",
                    ("C18H17N3", "im-1"): "ion-b",
                    # The second entry through the opened channel: an ion on
                    # the winner's peak, which only that channel makes of it.
                    ("C18H17N3", "im-2"): "ion-c",
                },
                {"ion-a": 0.9, "ion-b": 0.99, "ion-c": 0.6},
                {},
                pd.DataFrame(
                    [
                        {
                            "target_ion_id": "ion-a",
                            "target_ion_formula": "C14H14N5O2+",
                            "mz": 300.12,
                            "sample_peak_id": "p3",
                            "match_mz_error": 0.4,
                            "match_abundance_error": 0.05,
                        },
                        {
                            "target_ion_id": "ion-b",
                            "target_ion_formula": "C18H18N3+",
                            "mz": 276.15,
                            "sample_peak_id": "elsewhere",
                            "match_mz_error": 0.1,
                            "match_abundance_error": 0.05,
                        },
                        {
                            "target_ion_id": "ion-c",
                            "target_ion_formula": "C18H17N3Na+",
                            "mz": 300.13,
                            "sample_peak_id": "p3",
                            "match_mz_error": 0.6,
                            "match_abundance_error": 0.05,
                        },
                    ]
                ),
            ),
        )
        patches["seeded"] = patch(
            f"{_MOD}._seeded_rescore",
            new_callable=AsyncMock,
            return_value=({}, None),
        )
        if opened:
            # The run opens a secondary channel for this sample, which the
            # search enumerates through beside the sample's own adduct.
            patches["opened"] = patch(
                f"{_MOD}._searched_mechanisms",
                side_effect=lambda mechanisms, _secondary, _profile: (
                    mechanisms + [self.OPENED]
                ),
            )
        return _start(patches)

    async def _ledger(self, *, opened=False, **options):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        recorder = _Recorder()
        mocks = self._start_with(recorder, opened=opened)
        await _run(PeakAssignmentConfig(**{"run_untargeted": True, **options}))
        rows = {row["sample_peak_id"]: row for row in recorder.rows}
        scope = recorder.recorded_configs()[-1].get("search_scope")
        patch.stopall()
        return rows, scope, mocks

    @pytest.mark.asyncio
    async def test_off_by_default_it_measures_and_records_nothing(self):
        rows, scope, mocks = await self._ledger()

        mocks["shortlist"].assert_not_awaited()
        assert rows["p3"]["source"] == "untargeted"
        assert "confidence" not in rows["p3"]["provenance"]
        assert "is_tie" not in rows["p3"]["provenance"]
        assert "winner_confidence" not in scope

    @pytest.mark.asyncio
    async def test_on_the_winner_records_its_share_of_the_measured_field(self):
        rows, scope, mocks = await self._ledger(stage_b_confidence=True)

        provenance = rows["p3"]["provenance"]
        # The winner and the one entry the pass placed on its peak; the other
        # entry explains another peak and is no candidate for this one.
        assert provenance["n_candidates"] == 2
        assert provenance["is_tie"] is True
        assert 0.0 < provenance["confidence"] < 1.0
        # The shortlist stays formula-only: the share is stored, not the fits.
        assert [alt for alt in rows["p3"]["alternatives"] if "fit_score" in alt] == []
        # A database winner keeps the confidence its own stage gave it.
        assert rows["p1"]["source"] == "database"
        assert rows["p1"]["provenance"]["n_candidates"] == 1
        summary = scope["winner_confidence"]
        assert {key: summary[key] for key in summary if key != "seconds"} == {
            "peak_budget": 200,
            "winners": 1,
            "measured": 1,
            "unreached": 0,
            "formulas": 2,
            "placed": 1,
            "ties": 1,
        }
        assert summary["seconds"] >= 0

    @pytest.mark.asyncio
    async def test_the_pass_is_seeded_and_scored_as_the_run_scores(self):
        _rows, _scope, mocks = await self._ledger(stage_b_confidence=True)

        call = mocks["shortlist"].await_args
        # Every shortlist formula under every adduct the run searched.
        assert call.args[1] == {("C14H13N5O2", "im-1"), ("C18H17N3", "im-1")}
        # At the model the winners' own fits were scored and capped at: one
        # centre and one width for the winner and its rivals, never a model
        # fitted off a frame of mostly wrong formulas.
        assert call.kwargs["model"] == mocks["seeded"].call_args.kwargs["model"]
        assert call.kwargs["model"].version == 1

    @pytest.mark.asyncio
    async def test_a_run_that_takes_v2_measures_and_records_v2(self):
        # The refit puts the centre 1.5 ppm off v1's, beyond the bound: the run
        # takes v2. The shortlist is then measured at v2, the model its
        # winners were scored and capped at, and the run records v2, which
        # every reader outside it reads.
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.mass_gate import (
            MassCalibration,
        )
        from mascope_tools.composition.calibration import (
            Calibration,
            apply_calibration,
        )
        from mascope_tools.composition.measurement_model import MeasurementModel

        recorder = _Recorder()
        mocks = self._start_with(recorder)
        curve = Calibration(a=5.74, b=-3.36, instrument="orbi")
        mocks["calibration"].return_value = curve
        refitted = MassCalibration(mu_ppm=-1.5, sigma_ppm=0.2, anchors=40)
        _start(
            {
                "refit": patch(
                    "mascope_backend.api.new.peak_assignments.mass_gate."
                    "fit_run_mass_accuracy",
                    return_value=refitted,
                )
            }
        )

        await _run(PeakAssignmentConfig(run_untargeted=True, stage_b_confidence=True))

        shortlist = mocks["shortlist"].await_args.kwargs["model"]
        ranked = mocks["seeded"].call_args.kwargs["model"]
        assert (ranked.version, shortlist.version) == (1, 2)
        assert shortlist.scoring_mu_ppm == pytest.approx(-1.5)
        config = recorder.recorded_configs()[-1]
        assert config["measurement_model"]["gate"]["refit"]["adopted"] is True
        recorded = MeasurementModel.from_run_config(config)
        assert recorded.version == 2
        assert recorded.scoring_mu_ppm == pytest.approx(-1.5)
        assert recorded.record() == shortlist.record()
        # The v1 the search ranked at is kept beside it.
        assert config["measurement_model"]["ranked_at"] == ranked.record()
        # The library's rows were scored again at v2, and read their
        # P(correct) off the run's curve at the evidence v2 gave them.
        assert config["measurement_model"]["gate"]["refit"]["rescored"] >= 1
        listed = [row for row in recorder.rows if row.get("source") == "database"]
        assert listed
        for row in listed:
            provenance = row["provenance"]
            assert provenance["p_correct"] == round(
                float(apply_calibration(provenance["evidence"], curve)), 4
            )

    @pytest.mark.asyncio
    async def test_the_shortlist_is_tried_through_the_channels_the_run_opened(self):
        rows, scope, mocks = await self._ledger(opened=True, stage_b_confidence=True)

        assert mocks["shortlist"].await_args.args[1] == {
            ("C14H13N5O2", "im-1"),
            ("C14H13N5O2", "im-2"),
            ("C18H17N3", "im-1"),
            ("C18H17N3", "im-2"),
        }
        # The second entry lands on the winner's peak only through the opened
        # channel, and is one more candidate for it.
        assert scope["winner_confidence"]["placed"] == 2
        assert rows["p3"]["provenance"]["n_candidates"] == 3

    @pytest.mark.asyncio
    async def test_a_run_with_no_untargeted_stage_measures_nothing(self):
        _rows, scope, mocks = await self._ledger(
            stage_b_confidence=True, run_untargeted=False
        )

        mocks["shortlist"].assert_not_awaited()
        assert mocks["finalize"].await_args.args[:2] == ("run-1", "completed")
        assert not scope

    @pytest.mark.asyncio
    async def test_the_budget_reaches_the_pass(self):
        rows, scope, mocks = await self._ledger(
            stage_b_confidence=True, stage_b_confidence_peaks=1
        )

        assert scope["winner_confidence"]["peak_budget"] == 1
        assert scope["winner_confidence"]["measured"] == 1

    @pytest.mark.asyncio
    async def test_no_tier_moves(self):
        off, _scope, _mocks = await self._ledger()
        on, _scope, _mocks = await self._ledger(stage_b_confidence=True)

        assert {peak: row["tier"] for peak, row in on.items()} == {
            peak: row["tier"] for peak, row in off.items()
        }


@pytest.mark.asyncio
async def test_the_mass_gate_reads_the_sources_lines_by_the_calibrant_cap():
    """The run reads the sample's calibrant cap once and hands it to the
    mass gate, which leaves a source line brighter than it out of its
    anchors."""
    from mascope_backend.api.new.peak_assignments import service
    from mascope_backend.api.new.peak_assignments.config import (
        PeakAssignmentConfig,
    )
    from mascope_backend.api.new.peak_assignments.mass_gate import SourceLineCap

    cap = SourceLineCap(tic=1.0e6, tic_share_max=0.25)
    peaks = _peaks_df([("p1", 181.0707, 10000.0)])
    _start(_patches(_Recorder(), peaks, _stage_a_rows()))
    read = patch(
        f"{_MOD}._source_line_cap", new_callable=AsyncMock, return_value=cap
    ).start()
    judged = patch(f"{_MOD}.Judging", wraps=service.Judging).start()

    await _run(PeakAssignmentConfig(run_untargeted=False))

    read.assert_awaited_once()
    assert judged.call_args.kwargs["source_line_cap"] is cap


class TestTheIngestFoldTiersAsARunDoes:
    """The run-less ingest fold is the stage list's INGEST_FOLD path: a run's
    stages with the known set alone proposed, kept as the batch ledger's
    members. So a fold and a run of the same sample without the search give
    every Stage A row the same tier, the tiering rules' caps included."""

    @pytest.mark.asyncio
    async def test_a_fold_and_a_run_of_one_sample_tier_every_stage_a_row_alike(self):
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.service import (
            fold_sample_peaks_without_run,
        )

        # Beside the two-line ion, a bright one whose 13C line the spectrum
        # does not hold: the absent-line rule holds it at candidate in a run.
        rows = _stage_a_rows() + [
            _isotope_row(
                target_isotope_id="ti-3",
                target_ion_id="ion-2",
                target_compound_id="tc-2",
                compound_formula="C10H16O",
                ion_formula="C10H17O+",
                mz=153.1274,
                relative_abundance=1.0,
                sample_peak_id="p3",
                sample_peak_intensity=50000.0,
            )
        ]
        peaks = _peaks_df(
            [
                ("p1", 181.0707, 10000.0),
                ("p2", 182.0741, 660.0),
                ("p3", 153.1274, 50000.0),
            ]
        )
        recorder = _Recorder()
        _start(_patches(recorder, peaks, rows))
        fold = patch(
            "mascope_backend.api.new.peak_assignments.batch_peaks_controller."
            "fold_sample_into_batch_peaks",
            new_callable=AsyncMock,
            return_value="sb-1",
        ).start()
        patch(
            f"{_MOD}.in_flight_run_id", new_callable=AsyncMock, return_value=None
        ).start()

        await _run(PeakAssignmentConfig(run_untargeted=False))
        assert await fold_sample_peaks_without_run("si-1") == "sb-1"

        def tiers(rows):
            return {
                row["sample_peak_id"]: (
                    row["role"],
                    row["tier"],
                    [
                        reason["rule"]
                        for reason in (row.get("provenance") or {}).get(
                            "tier_reasons", []
                        )
                        if reason.get("caps")
                    ],
                )
                for row in rows
                if row["source"] == "database"
            }

        (folded,) = [
            call.kwargs["rows"] for call in fold.call_args_list if "rows" in call.kwargs
        ]
        run = tiers(recorder.rows)
        assert run == tiers([vars(row) for row in folded])
        # The rule bites on the bright ion, in both.
        assert run["p3"][1:] == ("candidate", ["absent_line"])
        assert run["p1"][1] == "assigned"
