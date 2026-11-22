"""The stage list every assignment path runs (``pipeline.py``, MAS-976).

Each path's own entry is tested beside its path (``test_orchestrator``,
``test_decoy``, ...): that it runs the stage list from its start to the end.
These are the list's own rules, whichever path runs it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import pipeline
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.pipeline import (
    PATHS,
    ROUND,
    STAGES,
    Stage,
)


def test_the_stages_run_in_the_order_the_plan_names():
    assert [stage.value for stage in STAGES] == [
        "resolve",
        "claim",
        "propose",
        "score",
        "elect",
        "refit",
        "judge",
        "cap",
        "level",
        "persist",
    ]
    assert ROUND == (Stage.REFIT, Stage.JUDGE, Stage.CAP)
    assert set(pipeline.STAGE_FUNCTIONS) == set(STAGES)


@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.name)
def test_a_path_runs_every_stage_after_its_start(path):
    stages = path.stages()
    assert stages[0] is path.start
    assert stages == STAGES[STAGES.index(path.start) :]
    assert set(ROUND) <= set(stages)


def test_every_path_has_a_name_of_its_own():
    assert len({path.name for path in PATHS}) == len(PATHS)


def _context(path) -> pipeline.RunContext:
    return pipeline.RunContext(
        path=path,
        sample=SimpleNamespace(sample_item_id="si-1"),
        config=PeakAssignmentConfig(),
        run_id="r-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds", [1, 3])
async def test_a_round_runs_again_until_the_cap_settles_the_judging(rounds):
    # The cap stage leaves the judging unsettled while its round found a line
    # a neighbour's envelope claims: the round runs again from the refit, and
    # the stages after it run once it settles.
    capped = []

    async def nothing(ctx):
        return None

    async def cap(ctx):
        capped.append(len(capped) + 1)
        if len(capped) == rounds:
            ctx.judged = object()

    stages = {stage: nothing for stage in STAGES}
    stages[Stage.CAP] = cap
    ctx = _context(pipeline.RUN)
    with (
        patch.dict(pipeline.STAGE_FUNCTIONS, stages),
        patch.object(pipeline, "sample_label_purity", AsyncMock(return_value=None)),
    ):
        await pipeline.run_stages(ctx)

    head = list(STAGES[: STAGES.index(Stage.REFIT)])
    tail = list(STAGES[STAGES.index(Stage.CAP) + 1 :])
    assert ctx.ran == head + list(ROUND) * rounds + tail


@pytest.mark.asyncio
async def test_a_path_starts_at_its_own_stage():
    # A path that starts later runs nothing before its start.
    ran = []

    async def record(ctx):
        ran.append(ctx.ran[-1] if ctx.ran else None)

    async def cap(ctx):
        ctx.judged = object()

    stages = {stage: record for stage in STAGES}
    stages[Stage.CAP] = cap
    late = pipeline.AssignmentPath(
        "late",
        Stage.SCORE,
        pipeline.Proposal.KNOWN_AND_SEARCH,
        pipeline.Measurement.FITTED,
    )
    ctx = _context(late)
    with (
        patch.dict(pipeline.STAGE_FUNCTIONS, stages),
        patch.object(pipeline, "sample_label_purity", AsyncMock(return_value=None)),
    ):
        await pipeline.run_stages(ctx)

    assert ctx.ran == list(STAGES[STAGES.index(Stage.SCORE) :])


@pytest.mark.asyncio
async def test_a_searched_row_names_its_twin_s_reading_before_the_list_reading_it_took():
    # A row the search committed on a list hit's peak, and read again from its
    # chlorine twin, names both readings at the head of its alternatives: the
    # one the twin replaced leads, then the list reading it displaced.
    row = {
        "sample_peak_id": "p-1",
        "sample_peak_mz": 201.5,
        "role": "M0",
        "assigned_formula": "C8H9ClO2",
        "alternatives": [{"assigned_formula": "C9H12O3"}],
    }
    listed = {
        "assigned_formula": "C10H16O",
        "ion_formula": "C10H16ONO3",
        "source": "database",
        "provenance": {},
    }
    weighing = {"evidence": 0.6, "fit_score": 0.7, "rival_evidence": 0.8, "prior": 0.1}
    election = SimpleNamespace(
        released={"p-1"},
        displaced={"p-1": (listed, weighing)},
        summary={"measured": 1, "with_rivals": 0, "taken_by_rivals": 1},
    )
    ctx = _context(pipeline.RUN)
    ctx.sample = SimpleNamespace(sample_item_id="si-1", sample_item_name="s-1")
    ctx.resolved_profile = SimpleNamespace(minor_channels=())
    ctx.search = pipeline.SearchProposal(
        remainder=pd.DataFrame({"mz": [201.5]}),
        frame=pd.DataFrame(),
        columns=[],
        mechanism_id_by_notation={},
        scope={},
        ran=True,
        matches=pd.DataFrame(),
        election=election,
        twin_records={
            201.5: {
                "halogen": "Cl",
                "replaced": {"formula": "C7H8O4", "ion": "C7H8O4NO3"},
            }
        },
    )
    with (
        patch.object(
            pipeline, "untargeted_matches_to_peak_assignments", return_value=[row]
        ),
        patch.object(
            pipeline.stage_a_pass, "_record_same_ion_readings", return_value=0
        ),
        patch.object(pipeline.batch_lines, "stamp_series", return_value=0),
        patch.object(
            pipeline.batch_lines, "batch_series_by_peak", AsyncMock(return_value={})
        ),
    ):
        await pipeline.elect(ctx)

    assert [entry["assigned_formula"] for entry in row["alternatives"]] == [
        "C7H8O4",
        "C10H16O",
        "C9H12O3",
    ]
    assert row["alternatives"][0]["halogen_twin_replaced"] is True
    assert row["provenance"]["halogen_twin"]["halogen"] == "Cl"
    assert row["provenance"]["list_reading"]["assigned_formula"] == "C10H16O"
