"""
``find``, ``resolve`` and ``describe`` over the SDK's listings.

Name matching is escaped-literal by default, a compiled pattern matches by
regex, an ID or a whole name wins, and several matches come back as
candidates rather than an exception past them. Collections are found too.
``describe`` answers small and cheap.

Hermetic: the workspace of ``conftest.py`` behind a real SDK client.
"""

import json
import re

import pytest

from mascope_ai import AmbiguousName, InvalidRequest, NotFound, Ref


def _names(found) -> list[tuple[str, str]]:
    return [(ref.kind, ref.name) for ref in found]


class TestFind:
    def test_a_string_is_a_literal_not_a_regex(self, kit):
        # "(A)" would be a regex group matching "A"; literally it is the
        # bracketed letter, which one batch has
        assert _names(kit.find("(A)")) == [("batch", "Blank (A)")]
        assert _names(kit.find("Campaign (2026)")) == [("dataset", "Campaign (2026)")]
        assert kit.find(".*") == []

    def test_matching_ignores_case(self, kit):
        assert _names(kit.find("SPRING 2026 REPEAT")) == [
            ("batch", "Spring 2026 repeat")
        ]

    def test_a_compiled_pattern_matches_by_regex(self, kit):
        found = kit.find(re.compile(r"^(Blank|Calibration)"), kind="batch")

        assert _names(found) == [("batch", "Blank (A)"), ("batch", "Calibration")]

    def test_a_pattern_matches_anywhere_in_the_name(self, kit):
        assert _names(kit.find(re.compile(r"2026 rep"))) == [
            ("batch", "Spring 2026 repeat")
        ]

    def test_the_whole_name_comes_before_a_part_of_one(self, kit):
        found = kit.find("Spring 2026")

        assert _names(found) == [
            ("batch", "Spring 2026"),
            ("batch", "Spring 2026 repeat"),
        ]

    def test_an_id_comes_first(self, kit):
        assert [ref.id for ref in kit.find("d2")] == ["d2"]

    def test_every_kind_but_samples_by_default(self, kit):
        found = kit.find("a")

        assert {ref.kind for ref in found} == {
            "workspace",
            "dataset",
            "batch",
            "collection",
        }

    def test_collections_are_found_the_global_ones_too(self, kit, api):
        found = kit.find("", kind="collection")

        assert _names(found) == [
            ("collection", "Calibrants"),
            ("collection", "Organic acids"),
        ]
        assert found[0].parent is None and found[1].parent == "ws1"
        assert api.requests_to("target/collections")[0][2] == {"workspace_id": "ws1"}

    def test_samples_by_kind(self, kit):
        found = kit.find("Sample 1", kind="sample")

        assert [(ref.id, ref.parent) for ref in found] == [("s21", "b2"), ("s31", "b3")]

    def test_within_a_batch_or_dataset(self, kit):
        spring = kit.resolve("Spring 2026", "batch")
        calibration = kit.resolve("Calibration runs", "dataset")

        assert [r.id for r in kit.find("Sample", kind="sample", within=spring)] == [
            "s21",
            "s22",
            "s23",
        ]
        assert _names(kit.find("", kind="batch", within=calibration)) == [
            ("batch", "Calibration")
        ]
        assert _names(kit.find("", kind="collection", within=spring)) == [
            ("collection", "Organic acids")
        ]

    def test_limit_and_total(self, kit):
        found = kit.find("", kind="batch", limit=2)

        assert len(found) == 2 and found.total == 4
        assert "4 match(es), the first 2 shown" in repr(found)
        assert found.to_dict()["total"] == 4

    def test_a_listing_that_failed_makes_the_answer_incomplete(self, kit, api):
        api.fail("GET", "samples", 500)

        found = kit.find("Sample", kind="sample")

        assert found.complete is False
        assert len(found.failures) == 4  # one per batch
        assert "INCOMPLETE" in repr(found)

    def test_refs_carry_parent_and_modified(self, kit):
        (batch,) = kit.find("Calibration", kind="batch")

        assert batch.parent == "d2"
        assert batch.modified is not None and batch.modified.tzinfo is not None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"query": 3},
            {"query": "x", "kind": "instrument"},
            {"query": "x", "within": "b1"},
            {"query": "x", "kind": "workspace", "within": Ref("b1", "batch", "B")},
            {"query": "x", "within": Ref("b1", "batch", "B")},
            {"query": "x", "kind": "collection", "within": Ref("d1", "dataset", "D")},
            {"query": "x", "kind": "sample", "within": Ref("s1", "sample", "S")},
        ],
    )
    def test_bad_arguments(self, kit, kwargs):
        with pytest.raises(InvalidRequest):
            kit.find(**kwargs)


class TestResolve:
    def test_a_whole_name_wins_over_names_it_is_part_of(self, kit):
        assert kit.resolve("Spring 2026", "batch").id == "b2"

    def test_the_only_partial_match(self, kit):
        assert kit.resolve("repeat", "batch").id == "b3"

    def test_several_matches_are_candidates(self, kit):
        with pytest.raises(AmbiguousName) as caught:
            kit.resolve("spring", "batch")

        error = caught.value
        assert [ref.id for ref in error.candidates] == ["b2", "b3"]
        assert error.to_dict()["n_candidates"] == 2
        assert error.to_dict()["candidates"][0]["name"] == "Spring 2026"
        assert "Spring 2026 repeat" in str(error)

    def test_two_whole_name_matches_are_candidates(self, kit):
        with pytest.raises(AmbiguousName) as caught:
            kit.resolve("Sample 1", "sample")

        assert [ref.id for ref in caught.value.candidates] == ["s21", "s31"]

    def test_a_sample_id_is_read_directly(self, kit, api):
        ref = kit.resolve("s22", "sample")

        assert (ref.id, ref.name, ref.parent) == ("s22", "Sample 2", "b2")
        assert api.requests_to("samples") == []  # no listing walked

    def test_nothing_matches(self, kit):
        with pytest.raises(NotFound) as caught:
            kit.resolve("nowhere", "dataset")

        assert caught.value.to_dict()["error"] == "not_found"

    def test_a_ref_passes_through_if_its_kind_fits(self, kit):
        ref = Ref("b1", "batch", "Blank (A)", parent="d1")

        assert kit.resolve(ref, "batch") is ref
        with pytest.raises(InvalidRequest):
            kit.resolve(ref, "sample")


class TestDescribe:
    def test_a_batch(self, kit):
        out = kit.describe(kit.resolve("Spring 2026", "batch"))

        assert out["ref"]["id"] == "b2"
        assert out["n_samples"] == 3
        assert out["first_measured"] and out["last_measured"]
        assert out["n_samples_matched"] == 2  # Sample 3 is not matched
        assert out["collections"] == [
            {
                "id": "c1",
                "kind": "collection",
                "name": "Organic acids",
                "parent": "ws1",
                "modified": None,
            }
        ]
        json.dumps(out)  # plain JSON throughout

    def test_a_batch_ref_without_its_dataset(self, kit):
        out = kit.describe(Ref("b4", "batch", "Calibration"))

        assert out["n_samples"] == 1
        assert out["collections"][0]["name"] == "Calibrants"

    def test_a_collection_lists_twenty_compounds_and_counts_them_all(self, kit):
        out = kit.describe("Organic acids", kind="collection")

        assert out["n_compounds"] == 30
        assert len(out["compounds"]) == 20
        assert out["n_batches"] == 2
        assert out["global"] is False

    def test_a_dataset(self, kit):
        out = kit.describe("Campaign (2026)", kind="dataset")

        assert out["n_batches"] == 3
        assert [b["name"] for b in out["batches"]] == [
            "Blank (A)",
            "Spring 2026",
            "Spring 2026 repeat",
        ]

    def test_a_sample(self, kit):
        out = kit.describe("s21", kind="sample")

        assert out["batch_id"] == "b2"
        assert out["match"]["matched"] is True
        assert set(out["mz_calibration"]) == {"status", "verified"}
        json.dumps(out)

    def test_the_workspaces(self, kit):
        mine = kit.describe("Workspace A", kind="workspace")
        other = kit.describe("Workspace B", kind="workspace")

        assert mine["your_role"] == "editor" and mine["n_datasets"] == 2
        assert other["your_role"] == "guest" and "n_datasets" not in other

    def test_a_name_needs_its_kind(self, kit):
        with pytest.raises(InvalidRequest):
            kit.describe("Spring 2026")

    def test_describe_is_cheap(self, kit, api):
        batch = kit.resolve("Spring 2026", "batch")
        kit.describe(batch)
        before = len(api.calls)

        kit.describe(batch)

        # Every listing it reads, the batch's collections included, is the
        # SDK's cached one the second time
        assert len(api.calls) - before == 0
