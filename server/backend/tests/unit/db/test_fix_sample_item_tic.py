"""
Unit tests for the sample item TIC fix script.

The script reads each file and polarity whose items store a TIC once, with
the rule a new item gets, and writes the result to every item of the pair.
The database side is stood in for here. Hermetic: no server, no database.
"""

import pytest

from mascope_backend.api.controllers.sample.items import sample_items_controller
from mascope_backend.db.scripts import fix_sample_item_tic as script


def _target(filename, polarity="+", items=1, stored=100.0):
    return {
        "filename": filename,
        "polarity": polarity,
        "items": items,
        "stored_min": stored,
        "stored_max": stored,
    }


@pytest.fixture
def stand_ins(monkeypatch):
    """The database and the file reads, each recording what it was asked."""
    state = {"targets": [], "tic": {}, "reads": [], "writes": []}

    async def configure_database_engine():
        return None

    async def targets():
        return state["targets"]

    async def sample_item_tic(filename, polarity):
        state["reads"].append((filename, polarity))
        given = state["tic"][(filename, polarity)]
        if isinstance(given, Exception):
            raise given
        return given

    async def apply(target, tic):
        state["writes"].append((target["filename"], target["polarity"], tic))
        return target["items"]

    monkeypatch.setattr(script, "configure_database_engine", configure_database_engine)
    monkeypatch.setattr(script, "_targets", targets)
    monkeypatch.setattr(script, "_sample_item_tic", sample_item_tic)
    monkeypatch.setattr(script, "_apply", apply)
    monkeypatch.delenv("DRY_RUN", raising=False)
    return state


def test_the_script_can_be_run():
    """`mascope db script run` finds a script by its callable main()."""
    assert callable(script.main)


def test_it_reads_with_the_rule_a_new_item_gets():
    """The controller's own function, not a copy that could drift from it."""
    assert script._sample_item_tic is sample_items_controller._sample_item_tic


@pytest.mark.asyncio
async def test_each_file_and_polarity_is_read_once_and_written_to_its_items(
    stand_ins,
):
    stand_ins["targets"] = [
        _target("file-A", "+", items=3),
        _target("file-A", "-", items=1),
        _target("file-B", None, items=2),
    ]
    stand_ins["tic"] = {
        ("file-A", "+"): 2.0,
        ("file-A", "-"): 3.0,
        ("file-B", None): 4.0,
    }

    await script.run()

    assert stand_ins["reads"] == [("file-A", "+"), ("file-A", "-"), ("file-B", None)]
    assert stand_ins["writes"] == [
        ("file-A", "+", 2.0),
        ("file-A", "-", 3.0),
        ("file-B", None, 4.0),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unreadable", [FileNotFoundError("gone"), ValueError("no scans"), float("nan")]
)
async def test_a_file_that_cannot_be_read_is_skipped_and_the_rest_written(
    stand_ins, unreadable
):
    stand_ins["targets"] = [_target("file-A"), _target("file-B")]
    stand_ins["tic"] = {("file-A", "+"): unreadable, ("file-B", "+"): 5.0}

    await script.run()

    assert stand_ins["writes"] == [("file-B", "+", 5.0)]


@pytest.mark.asyncio
async def test_a_dry_run_reads_every_file_and_writes_nothing(stand_ins, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    stand_ins["targets"] = [_target("file-A"), _target("file-B")]
    stand_ins["tic"] = {("file-A", "+"): 1.0, ("file-B", "+"): 2.0}

    await script.run()

    assert len(stand_ins["reads"]) == 2
    assert stand_ins["writes"] == []
