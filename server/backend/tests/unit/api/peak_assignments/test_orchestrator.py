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

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from test_engine import _isotope_row  # same directory; pytest puts it on sys.path


_MOD = "mascope_backend.api.new.peak_assignments.service"


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


def _sample() -> MagicMock:
    sample = MagicMock()
    sample.sample_item_id = "si-1"
    sample.sample_item_name = "Sample One"
    sample.sample_batch_id = "sb-1"
    sample.filename = "orbi-sample.raw"
    sample.polarity = "positive"
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
            f"{_MOD}.default_match_params",
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
            f"{_MOD}.apply_match_params", side_effect=lambda df, _params: df
        ),
        "fit": patch(f"{_MOD}.score_ions_by_fit", side_effect=lambda df: df),
        "calibration": patch(
            f"{_MOD}.load_calibration", new_callable=AsyncMock, return_value=None
        ),
        "mechanisms": patch(
            f"{_MOD}.fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(["im-1"], [MagicMock()]),
        ),
        "ionizations": patch(
            f"{_MOD}._untargeted_ionization_notations",
            return_value=(["+H+"], {"+H+": "+H+"}),
        ),
        "compositions": patch(
            f"{_MOD}.assign_compositions",
            return_value=(
                stage_b_matches if stage_b_matches is not None else pd.DataFrame(),
                {},
            ),
        ),
        "claim": patch(f"{_MOD}.assignment_claim", _claim_stub()),
        "session": patch(f"{_MOD}.async_session", side_effect=recorder.session_factory),
        "progress": patch(
            f"{_MOD}.send_progress_user_notification", new_callable=AsyncMock
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


class TestStageHandoff:
    @pytest.mark.asyncio
    async def test_stage_b_is_never_offered_a_peak_stage_a_owns(self):
        """The single-owner invariant across the stage boundary.

        Stage A's peaks must not reach the untargeted search, or two stages could
        claim the same peak and violate the per-run uniqueness constraint.
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
        offered = mocks["compositions"].call_args.args[0]
        offered_mz = set(offered["mz"].tolist())
        # p1/p2 belong to the Stage A ion; only p3 is unexplained.
        assert offered_mz == {300.1234}

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
