"""The stage list every assignment path runs (``pipeline.py``, MAS-976).

Each path's own entry is tested beside its path (``test_orchestrator``,
``test_decoy``, ...): that it runs the stage list from its start to the end.
These are the list's own rules, whichever path runs it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
