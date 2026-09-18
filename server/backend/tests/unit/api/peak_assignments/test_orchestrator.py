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
from types import SimpleNamespace
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
                        ionization_mechanism="+H+",
                        ionization_mechanism_polarity="+",
                    )
                ],
            ),
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
        # The seeded re-score opens the sample file again; these tests declare
        # their rows rather than measure them, so it stands down and every row
        # keeps the finder's own fit. Its own wiring is tested separately.
        "seeded": patch(
            f"{_MOD}._seeded_fits", new_callable=AsyncMock, return_value={}
        ),
        # The file's resolving power lives with its instrument functions in the
        # database; these files have none, as a file never fitted has none.
        "resolution": patch(
            f"{_MOD}._resolution_of", new_callable=AsyncMock, return_value=None
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
    async def test_a_peak_whose_line_left_the_ledger_is_persisted_unassigned(self):
        # An isotopologue released with the reading it belonged to is a peak
        # nothing explains, and the ledger still has a row for it.
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        judge = service.judge_commits

        def releasing(rows, **kwargs):
            judged = judge(rows, **kwargs)
            judged.rows = [row for row in judged.rows if row["role"] == "M0"]
            return judged

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        recorder = _Recorder()
        _start(_patches(recorder, peaks, _stage_a_rows()))
        patch(f"{_MOD}.judge_commits", side_effect=releasing).start()

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
        lines = config["mass_calibration"]["lines"]
        assert (lines["noise"], lines["resolution"]) == (False, True)
        assert set(config["mass_calibration"]["isotopologues"]) == {
            "tracks",
            "in_doubt",
            "untracked",
        }

    @pytest.mark.asyncio
    async def test_the_passes_are_handed_the_runs_own_bands(self):
        # A row under the top band names it among its reasons, so the bands the
        # tiering pass reads are the ones the run tiered on.
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        judge = service.judge_commits
        handed = {}

        def recording(rows, **kwargs):
            handed.update(kwargs)
            return judge(rows, **kwargs)

        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        _start(_patches(_Recorder(), peaks, _stage_a_rows()))
        patch(f"{_MOD}.judge_commits", side_effect=recording).start()

        await _run(
            PeakAssignmentConfig(
                run_untargeted=False, assigned_threshold=0.8, candidate_threshold=0.5
            )
        )

        assert handed["tier_bands"] == {"assigned": 0.8, "candidate": 0.5}

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


class TestAListHitMeetsTheGrid:
    """Every peak a list read is put to the formula search, which may keep the
    list's reading there or let a rival take the peak."""

    RIVAL = {
        "formula": "C7H16O5",
        "ion": "C7H17O5+",
        "ionization_mechanism": "+H+",
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
            "ionization_mechanism": "+H+",
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
                    "ionization_mechanism": "+H+",
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
                ionization_mechanism="+H+",
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
                "ionization_mechanism": "+H+",
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
            "ionization_mechanism": "+H+",
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
        mocks["ionizations"].return_value = (["+NH4+"], {"+NH4+": "im-2"})

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

        Dimethylformamide through +H+ is acrolein through +NH4+, and the run
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
            ionization="+H+",
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
                        ionization_mechanism="+H+",
                        ionization_mechanism_polarity="+",
                    ),
                    SimpleNamespace(
                        ionization_mechanism_id="im-2",
                        ionization_mechanism="+NH4+",
                        ionization_mechanism_polarity="+",
                    ),
                ],
            ),
        )
        patches["ionizations"] = patch(
            f"{_MOD}._untargeted_ionization_notations",
            return_value=(["+H+", "+NH4+"], {"+H+": "im-1", "+NH4+": "im-2"}),
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
            "via": "+NH4+",
        }
        assert row["tier"] == "candidate"
        assert REASON_AMBIGUOUS_NITROGEN in {
            reason["rule"] for reason in row["provenance"]["tier_reasons"]
        }
        cross_channel = recorder.recorded_configs()[-1]["cross_channel"]
        assert (cross_channel["capped"], cross_channel["capped_mirror"]) == (1, 1)


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
        }

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
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.mu_ppm == pytest.approx(-1.3, abs=1e-6)
        recorded = recorder.recorded_configs()[-1]["pattern_scoring"]
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

        await _run(PeakAssignmentConfig(run_untargeted=False, context=context))

        expected = KNOWN_WINDOW_CEILING if ceiling == "shipped" else None
        assert mocks["reference"].await_args.kwargs == {"known_window": expected}
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
        assert "mass_calibration" not in configs[0]
        assert configs[1]["mass_calibration"]["committed"] == len(_stage_a_rows())
        snapshot = configs[0]["resolved_profile"]
        # '+H+' is diagnostic of nothing, so a positive sample falls back to
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
