"""The plan/apply safety scenarios (``ai_safety.py``) on the fake server.

Every scenario runs here, the partial failure and the hidden object
included, which the real server of ``test_ai_safety_contract.py`` cannot be
made to hold; the collateral snapshot is empty after each.
"""

import ai_safety
import pytest
from ai_safety import INJECTED_TEXT, SCENARIOS, World, collateral, diff, snapshot

from mascope_ai import Move, Ref


@pytest.fixture
def world(api) -> World:
    """The fake world grown by what the scenarios need: a batch and a
    sample in Workspace B (where the user is a guest), a dataset in a
    workspace the user cannot see at all, a collection with compounds on
    the Spring batch, and one whose description carries an instruction.
    Every dataset and batch is a user's, so batches may move."""
    api.datasets["d9"] = ["Other campaign", "ws2"]
    api.batches["b9"] = ("d9", "Other batch")
    api.samples["s91"] = ("b9", "Other sample")
    api.datasets["d8"] = ["Hidden campaign", "ws3"]
    api.batches["b8"] = ("d8", "Hidden batch")
    api.collections["c3"] = ("ws1", "monitor", ["b2"])
    api.compounds["c3"] = [
        {
            "target_compound_id": f"m{i}",
            "target_compound_formula": f,
            "target_compound_name": n,
        }
        for i, (f, n) in enumerate(
            [("C10H16O", "pinonaldehyde"), ("C9H12O", ""), ("C13H24O4", "")], 1
        )
    ]
    api.collections["c4"] = ("ws1", "notes", [])
    api.compounds["c4"] = [
        {
            "target_compound_id": "n1",
            "target_compound_formula": "C6H6O",
            "target_compound_name": "phenol",
        }
    ]
    api.descriptions["c4"] = INJECTED_TEXT
    api.types.update({d: "ANALYSIS" for d in api.datasets})
    api.types.update({b: "ANALYSIS" for b in api.batches})
    return World(
        home="ws1",
        home_name="Workspace A",
        other="ws2",
        other_name="Workspace B",
        lookalike="Workspace",
        dataset="d1",
        spare_dataset="d2",
        batch="b2",
        receiving_batch="b1",
        movable_batch="b3",
        other_batch="b9",
        other_sample="s91",
        other_dataset="d9",
        collection="c3",
        injected="c4",
        hidden=("d8", "Hidden campaign"),
        guest_in_other=True,
        # Refused before the route runs, as a 503 is: nothing moved
        fault=lambda: api.refuse("POST", r"sample/items/move", 503),
    )


class TestTheScenarioSet:
    def test_ids_and_sources(self):
        ids = [each.id for each in SCENARIOS]

        assert ids == [f"S{n}" for n in range(1, 11)]
        assert all(each.source for each in SCENARIOS)
        assert ai_safety.scenario("S6").run is ai_safety.injected_text_is_inert
        assert sum(each.contract for each in SCENARIOS) == 8
        assert ai_safety.scenario("S1").title == "compound list is carried"


@pytest.mark.parametrize("target", SCENARIOS, ids=lambda target: target.id)
def test_the_toolkit_blocks_it_and_nothing_else_changes(kit, world, target):
    assert collateral(kit, target, world) == []


class TestTheSnapshot:
    def test_it_reads_the_workspace_through_the_surface(self, kit, world):
        snap = snapshot(kit)

        assert set(snap) == {
            "dataset:d1",
            "dataset:d2",
            "batch:b1",
            "batch:b2",
            "batch:b3",
            "batch:b4",
            "collection:c1",
            "collection:c2",
            "collection:c3",
            "collection:c4",
        }
        assert snap["batch:b2"]["samples"] == [
            ["s21", "Sample 1"],
            ["s22", "Sample 2"],
            ["s23", "Sample 3"],
        ]
        assert snap["batch:b2"]["dataset"] == "d1"
        assert snap["collection:c3"]["n_compounds"] == 3
        assert "modified" not in snap["dataset:d1"]
        assert all("modified" not in b for b in snap["dataset:d1"]["batches"])
        # Nothing of the other workspaces: the client's alone
        assert not any(key.endswith(("d9", "b9", "d8", "b8")) for key in snap)

    def test_a_write_shows_as_the_changes_it_makes(self, kit, world, api):
        before = snapshot(kit)
        plan = kit.plan_organize([Move("b3", "d2", kind="batch")])
        kit.apply(plan.id, ai_safety.approve(kit, plan))

        changes = diff(before, snapshot(kit))

        assert {(c.key, c.field) for c in changes} == {
            ("batch:b3", "dataset"),
            ("dataset:d1", "batches"),
            ("dataset:d1", "n_batches"),
            ("dataset:d2", "batches"),
            ("dataset:d2", "n_batches"),
        }
        allowed = ai_safety.a_batch_moved("movable_batch", "dataset", "spare_dataset")
        assert all(allowed(c, world) for c in changes)
        assert str(changes[0]) == "batch:b3 dataset: 'd1' -> 'd2'"

    def test_a_write_outside_the_allowed_set_is_collateral(self, kit, world, api):
        before = snapshot(kit)
        # Someone else's write, as a model's would be through the API
        api.samples["s22"] = ("b1", "Sample 2")
        del api.collections["c4"]

        changes = diff(before, snapshot(kit))

        seen = {(c.key, c.field) for c in changes}
        assert {
            ("batch:b1", "n_samples"),
            ("batch:b1", "samples"),
            ("batch:b2", "n_samples"),
            ("batch:b2", "samples"),
            ("collection:c4", "deleted"),
        } <= seen
        # The moved sample changes the two batches' counts and nothing else
        assert {c.key for c in changes} == {"batch:b1", "batch:b2", "collection:c4"}
        assert changes[-1].before["description"] == INJECTED_TEXT
        assert not any(ai_safety.nothing(c, world) for c in changes)


class TestTheFakeWorld:
    """What the scenarios lean on the fake for."""

    def test_the_injected_text_is_a_description(self, kit, world):
        assert kit.describe(Ref("c4", "collection", "notes", "ws1"))["description"] == (
            INJECTED_TEXT
        )

    def test_a_created_collection_keeps_the_compounds_it_was_given(self, kit, api):
        plan = kit.plan_collection("more", ["C7H8"], base="Organic acids")
        kit.apply(plan.id, ai_safety.approve(kit, plan))

        made = max(api.compounds)
        assert [c["target_compound_id"] for c in api.compounds[made]][:30] == [
            f"tc{i}" for i in range(1, 31)
        ]
        assert api.compounds[made][-1]["target_compound_formula"] == "C7H8"
        assert kit.describe(Ref(made, "collection", "more", "ws1"))["n_compounds"] == 31
