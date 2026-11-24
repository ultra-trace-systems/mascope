"""Unit tests for what a batch stage leaves on a member and how a sample's
judging is asked again: the batch's mark, its withdrawal, the readings a
sample's judging proposes together, the members that take its tier, and the
tier a batch decision proposes its reading at.

See ``batch_rejudge.py`` and ``batch_peaks.batch_mark``; the database side is
in the stage suites (``test_batch_series_api.py``,
``test_batch_known_species_api.py``, ``test_batch_water_ladder_api.py``).
"""

from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.batch_known_species import (
    ROW_BEFORE_KEY,
    restore_row,
    with_row_before,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    STAGE_VERSION,
    batch_mark,
    batch_mark_of,
    mark_member,
    restore_marked,
    role_code,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_rejudge import (
    floors_of,
    given_readings,
    judged_targets,
    new_tier,
)
from mascope_backend.api.new.peak_assignments.judging_loop import raise_to_floors


def _member(anchor="bp-1", **fields):
    base = dict(
        batch_peak_id=anchor,
        sample_item_id="s-1",
        sample_peak_id="p-1",
        candidate=0,
        target_compound_id=None,
        tier=tier_code("candidate"),
        fit_score=0.7,
        role=role_code("M0"),
        owner_batch_peak_id=None,
        p_correct=None,
        batch_written=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


_REGISTRY = [
    {"formula": "C6H12O6", "ion_formula": "C6H13O6+", "ionization_mechanism_id": "m"}
]


class TestTheMark:
    def test_it_keeps_the_stage_its_version_and_what_the_member_read(self):
        member = _member()
        token = STAGE_VERSION.set(4)
        try:
            mark = batch_mark(member, "series")
        finally:
            STAGE_VERSION.reset(token)
        assert mark["stage"] == "series"
        assert mark["version"] == 4
        assert mark["before"] == {
            "candidate": 0,
            "target_compound_id": None,
            "tier": tier_code("candidate"),
            "fit_score": 0.7,
            "role": role_code("M0"),
            "owner_batch_peak_id": None,
            "p_correct": None,
        }

    def test_outside_a_pass_it_carries_no_version(self):
        assert batch_mark(_member(), "search")["version"] is None

    def test_an_earlier_stages_mark_is_kept_inside(self):
        member = _member()
        mark_member(member, "search")
        member.tier = tier_code("assigned")
        mark_member(member, "series")
        assert batch_mark_of(member)["stage"] == "series"
        assert batch_mark_of(member)["before"]["batch_written"]["stage"] == "search"

    def test_a_stage_takes_off_the_members_own_target_and_keeps_it_before(self):
        """The member's own target compound is what its sample matched the
        fold's reading to: a stage's claim is not that reading, so the mark
        keeps it and the member names none until the claim is withdrawn. A
        person's or an import's write leaves it."""
        member = _member(target_compound_id="tc-1")
        mark_member(member, "search")
        assert member.target_compound_id is None
        assert batch_mark_of(member)["before"]["target_compound_id"] == "tc-1"
        member = _member(target_compound_id="tc-1")
        mark_member(member, None)
        assert member.target_compound_id == "tc-1"

    def test_a_person_or_an_import_takes_it_off(self):
        member = _member()
        mark_member(member, "search")
        mark_member(member, None)
        assert batch_mark_of(member) is None


class TestTheWithdrawal:
    def test_a_member_reads_again_what_it_read_before_the_stage(self):
        member = _member(target_compound_id="tc-1")
        mark_member(member, "known_species")
        member.candidate, member.tier, member.fit_score = 3, tier_code("assigned"), None
        assert restore_marked(member, "known_species")
        assert (
            member.candidate,
            member.target_compound_id,
            member.tier,
            member.fit_score,
        ) == (0, "tc-1", tier_code("candidate"), 0.7)
        member = _member()
        mark_member(member, "search")
        mark_member(member, "known_species")
        member.candidate, member.tier, member.fit_score = 3, tier_code("assigned"), None
        assert restore_marked(member, "known_species")
        assert (member.candidate, member.tier, member.fit_score) == (
            0,
            tier_code("candidate"),
            0.7,
        )
        # The earlier stage's mark is the member's again.
        assert batch_mark_of(member)["stage"] == "search"

    def test_a_member_another_stage_wrote_is_left(self):
        member = _member()
        mark_member(member, "series")
        assert not restore_marked(member, "known_species")
        assert not restore_marked(_member(), "known_species")

    def test_a_row_reads_again_what_it_read_unless_a_person_chose_since(self):
        row = SimpleNamespace(
            assigned_formula="C9H8",
            tier="candidate",
            source="untargeted",
            provenance=None,
        )
        values = with_row_before(
            row, {"assigned_formula": None, "tier": "unassigned", "provenance": {}}
        )
        assert values["provenance"][ROW_BEFORE_KEY] == {
            "assigned_formula": "C9H8",
            "tier": "candidate",
            "provenance": None,
        }
        for key, value in values.items():
            setattr(row, key, value)
        assert restore_row(row)
        assert (row.assigned_formula, row.tier, row.provenance) == (
            "C9H8",
            "candidate",
            None,
        )
        curated = SimpleNamespace(
            source="manual", provenance={ROW_BEFORE_KEY: {"tier": "candidate"}}
        )
        assert not restore_row(curated)


class TestTheJudging:
    def test_every_monoisotopic_reading_is_proposed_together(self):
        members = [
            _member("bp-1"),
            _member("bp-2", role=role_code("iso_child"), owner_batch_peak_id="bp-1"),
            _member("bp-3", candidate=None, role=role_code("unassigned")),
            _member("bp-4", role=role_code("reagent")),
        ]
        given = given_readings(members, {"bp-1": _REGISTRY, "bp-4": _REGISTRY})
        assert list(given) == ["bp-1"]
        assert given["bp-1"].formula == "C6H12O6"

    def test_the_members_a_stage_wrote_or_a_decision_corroborates_take_the_tier(self):
        marked = _member("bp-1")
        mark_member(marked, "search")
        members = [marked, _member("bp-2"), _member("bp-3")]
        assert judged_targets(members, frozenset({"bp-3"})) == {"bp-1", "bp-3"}

    def test_a_listed_compound_the_batch_claimed_is_proposed_at_candidate(self):
        listed = _member("bp-1", sample_peak_id="p-9")
        mark_member(listed, "known_species")
        library = _member("bp-2", role=role_code("reagent"))
        mark_member(library, "known_species")
        searched = _member("bp-3")
        mark_member(searched, "search")
        assert floors_of([listed, library, searched]) == {"p-9": "candidate"}

    def test_the_tier_is_taken_only_off_a_row_that_reads_the_member(self):
        annotation = given_readings([_member()], {"bp-1": _REGISTRY})["bp-1"]
        row = {
            "assigned_formula": "C6H12O6",
            "ionization_mechanism_id": "m",
            "role": "M0",
            "tier": "assigned",
        }
        assert new_tier(_member(), row, annotation) == "assigned"
        assert (
            new_tier(_member(), {**row, "assigned_formula": "C7"}, annotation) is None
        )
        assert (
            new_tier(_member(), {**row, "ionization_mechanism_id": "n"}, annotation)
            is None
        )
        assert new_tier(_member(), {**row, "role": "iso_child"}, annotation) is None
        assert new_tier(_member(), None, annotation) is None


def test_a_floor_raises_a_monoisotopic_row_only_where_it_is_lower():
    rows = [
        {"sample_peak_id": "a", "role": "M0", "tier": "below_assignability"},
        {"sample_peak_id": "b", "role": "M0", "tier": "assigned"},
        {"sample_peak_id": "c", "role": "iso_child", "tier": "below_assignability"},
        {"sample_peak_id": "d", "role": "M0", "tier": "below_assignability"},
    ]
    floors = {"a": "candidate", "b": "candidate", "c": "candidate"}
    assert raise_to_floors(rows, floors) == 1
    assert [row["tier"] for row in rows] == [
        "candidate",
        "assigned",
        "below_assignability",
        "below_assignability",
    ]


def test_a_line_is_judged_through_its_owner():
    given = given_readings([_member("bp-1")], {"bp-1": _REGISTRY})
    from mascope_backend.api.new.peak_assignments.batch_rejudge import judged_reading

    line = _member("bp-2", role=role_code("iso_child"), owner_batch_peak_id="bp-1")
    # A line the strip wrote, under an owner no stage touched.
    assert judged_reading(line, {"bp-2"}, given) is given["bp-1"]
    # A line of a target anchor.
    assert judged_reading(line, {"bp-1"}, given) is given["bp-1"]
    assert judged_reading(line, {"bp-9"}, given) is None
    assert judged_reading(_member("bp-1"), {"bp-1"}, given) is given["bp-1"]
    assert judged_reading(_member("bp-1"), set(), given) is None
    claimed = _member("bp-1", role=role_code("reagent"))
    assert judged_reading(claimed, {"bp-1"}, given) is None


def test_a_peak_the_batch_claimed_is_no_given_readings():
    from mascope_backend.api.new.peak_assignments.pipeline import (
        PROPAGATION,
        RunContext,
    )

    ctx = RunContext(
        path=PROPAGATION,
        sample=None,
        config=None,
        run_id="r",
        held_peak_ids=frozenset({"p-7"}),
    )
    ctx.reagent_peak_ids = {"p-1"}
    assert ctx.claimed_peak_ids == {"p-1", "p-7"}


def test_the_samples_a_step_restored_are_read_off_its_outcomes():
    from mascope_backend.api.new.peak_assignments.batch_stages import restored_in

    outcomes = [
        {"data": {"restored_samples": {"s-1": ["bp-1"], "s-2": ["bp-2"]}}},
        {"data": {"restored_samples": {"s-1": ["bp-3"]}}},
        {"data": {"samples_judged": 2}},
        None,
    ]
    assert restored_in(outcomes) == {"s-1": {"bp-1", "bp-3"}, "s-2": {"bp-2"}}


def test_the_judging_says_what_it_measured_kept_and_could_not_keep():
    """MAS-1073: the outcome counts the readings measured and kept, and names
    the samples with no recorded measurement to keep (MAS-1071)."""
    from mascope_backend.api.new.peak_assignments.batch_rejudge import judged_message

    counts = {"samples_judged": 2, "members_moved": 1, "readings_scored": 5}
    plain = judged_message({**counts, "readings_kept": 7})
    assert plain == (
        "Judged 2 samples again as a run judges them: 1 member took another "
        "tier; 5 readings measured and 7 kept from earlier in the pass."
    )
    unrecorded = judged_message({**counts, "samples_unrecorded": 1})
    assert unrecorded.endswith(
        " 1 of them had no recorded measurement to keep, and every reading of "
        "them was measured again."
    )
