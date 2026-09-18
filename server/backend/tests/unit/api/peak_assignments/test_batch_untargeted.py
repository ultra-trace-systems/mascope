"""Unit tests for the per-anchor untargeted search's pure parts.

No DB: which member represents an anchor, how the representatives group into
searches, how an isotopologue row finds its owner's anchor, and what the
outcome says. See ``batch_untargeted.py``.
"""

from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.batch_peaks import role_code
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    choose_representatives,
    group_by_sample,
    owner_anchor_of,
    search_config,
    search_outcome,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ARTIFACT,
    ROLE_REAGENT,
)
from mascope_backend.api.new.peak_assignments.profiles import resolve_profile


REAGENT_ROLE_CODE = role_code(ROLE_REAGENT)
ARTIFACT_ROLE_CODE = role_code(ROLE_ARTIFACT)


def _member(anchor, sample, peak, intensity, role=None):
    return SimpleNamespace(
        batch_peak_id=anchor,
        sample_item_id=sample,
        sample_peak_id=peak,
        intensity=intensity,
        role=role,
    )


def test_the_brightest_member_represents_its_anchor():
    members = [
        _member("bp-1", "s1", "p1", 500.0),
        _member("bp-1", "s2", "p1", 900.0),
        _member("bp-1", "s3", "p1", 700.0),
        _member("bp-2", "s1", "p2", 100.0),
    ]
    chosen = choose_representatives(members)
    assert chosen["bp-1"].sample_item_id == "s2"
    assert chosen["bp-2"].sample_item_id == "s1"


def test_a_member_with_no_intensity_is_never_preferred():
    members = [_member("bp-1", "s1", "p1", None), _member("bp-1", "s2", "p1", 1.0)]
    assert choose_representatives(members)["bp-1"].sample_item_id == "s2"
    # ... but stands in when it is all there is.
    assert choose_representatives([_member("bp-1", "s1", "p1", None)])["bp-1"]


def test_a_reagent_anchor_is_never_searched():
    """Its consensus tier is 'unassigned' because a reagent row carries no
    formula to vote on - but the peak is the source's own chemistry, taken out
    of the per-sample stages on purpose. The batch search is the one path that
    could put an analyte formula back onto it.
    """
    members = [
        _member("bp-1", "s1", "p1", 9e6, role=REAGENT_ROLE_CODE),
        _member("bp-1", "s2", "p1", 8e6, role=REAGENT_ROLE_CODE),
    ]

    assert choose_representatives(members) == {}


def test_an_artifact_anchor_is_never_searched():
    """Same rule, same reason: an anchor whose members are the detector's
    ringing carries no species for the search to name."""
    members = [
        _member("bp-1", "s1", "p1", 9e6, role=ARTIFACT_ROLE_CODE),
        _member("bp-1", "s2", "p1", 8e6, role=ARTIFACT_ROLE_CODE),
    ]

    assert choose_representatives(members) == {}


def test_a_mixed_anchor_is_searched_on_a_real_peak():
    """The brightest member is the reagent one, and it is still not the
    representative: an anchor is searched on a peak that could be an analyte."""
    members = [
        _member("bp-1", "s1", "p1", 9e6, role=REAGENT_ROLE_CODE),
        _member("bp-1", "s2", "p1", 10.0),
    ]

    assert choose_representatives(members)["bp-1"].sample_item_id == "s2"


def test_representatives_group_into_one_search_per_sample():
    chosen = {
        "bp-1": _member("bp-1", "s2", "p1", 900.0),
        "bp-2": _member("bp-2", "s1", "p2", 100.0),
        "bp-3": _member("bp-3", "s2", "p3", 300.0),
    }
    grouped = group_by_sample(chosen)
    assert {
        sample: sorted(m.batch_peak_id for m in ms) for sample, ms in grouped.items()
    } == {
        "s1": ["bp-2"],
        "s2": ["bp-1", "bp-3"],
    }


def test_an_isotopologue_finds_its_owners_anchor_through_the_owners_peak():
    rows = {
        "a-m0": {"peak_assignment_id": "a-m0", "sample_peak_id": "p1", "role": "M0"},
        "a-iso": {
            "peak_assignment_id": "a-iso",
            "sample_peak_id": "p2",
            "role": "iso_child",
            "owner_peak_assignment_id": "a-m0",
        },
    }
    members = {
        "p1": _member("bp-1", "s1", "p1", 1.0),
        "p2": _member("bp-2", "s1", "p2", 1.0),
    }
    assert owner_anchor_of(rows["a-iso"], rows, members) == "bp-1"
    assert owner_anchor_of(rows["a-m0"], rows, members) is None


def test_an_isotopologue_whose_owner_is_not_a_member_stands_on_its_own():
    rows = {
        "a-iso": {
            "peak_assignment_id": "a-iso",
            "sample_peak_id": "p2",
            "role": "iso_child",
            "owner_peak_assignment_id": "a-m0",
        }
    }
    # The owner row is missing entirely...
    assert (
        owner_anchor_of(rows["a-iso"], rows, {"p2": _member("bp-2", "s1", "p2", 1.0)})
        is None
    )
    # ... or names a peak that is no member of a searched anchor.
    rows["a-m0"] = {"peak_assignment_id": "a-m0", "sample_peak_id": "p9", "role": "M0"}
    assert (
        owner_anchor_of(rows["a-iso"], rows, {"p2": _member("bp-2", "s1", "p2", 1.0)})
        is None
    )


def test_the_search_config_is_the_orchestrators():
    # Both paths configure the finder from one resolved profile, which is what
    # makes a batch search comparable with a per-sample run.
    resolved = resolve_profile(
        PeakAssignmentConfig(), ["+Br-"], instrument_type="orbi", polarity="-"
    )
    built = search_config(resolved, ["H+", "Na+"])
    assert built.ionizations == "H+,Na+"
    assert built.mass_range_ppm == resolved.mz_precision_ppm
    assert built.element_count_ranges == resolved.element_ranges
    assert built.use_unsaturation is True


def test_the_outcome_says_when_there_was_nothing_to_search():
    outcome = search_outcome(
        {
            "anchors_searched": 0,
            "anchors_annotated": 0,
            "members_propagated": 0,
            "samples_searched": 0,
            "samples_rescored": 0,
        },
        "sb-1",
    )
    assert outcome["status"] == "partial"
    assert "already carries an assignment" in outcome["message"]
    assert outcome["_notification_data"] == {"sample_batch_id": "sb-1"}


def test_the_outcome_counts_what_was_done():
    outcome = search_outcome(
        {
            "anchors_searched": 12,
            "anchors_annotated": 7,
            "members_propagated": 30,
            "samples_searched": 3,
            "samples_rescored": 9,
        },
        "sb-1",
    )
    assert outcome["status"] == "success"
    assert "12 unassigned batch peaks across 3 samples" in outcome["message"]
    assert "7 assigned a composition" in outcome["message"]
    assert "30 member peaks" in outcome["message"]


def test_the_outcome_says_when_the_cap_left_anchors_unsearched():
    """A batch run has one config for many samples and no per-sample row to
    stamp a search scope on, so the count of what it never looked at has to
    reach the caller through the result. An unsearched anchor and an anchor
    nothing could explain both read as a blank otherwise."""
    counts = {
        "anchors_searched": 12,
        "anchors_unsearched": 4,
        "anchors_annotated": 7,
        "members_propagated": 30,
        "samples_searched": 3,
        "samples_rescored": 9,
    }
    outcome = search_outcome(counts, "sb-1")
    assert outcome["message"].endswith(
        "4 anchors were left unsearched by the peak cap."
    )
    assert outcome["data"]["anchors_unsearched"] == 4

    one = search_outcome({**counts, "anchors_unsearched": 1}, "sb-1")
    assert one["message"].endswith("1 anchor was left unsearched by the peak cap.")

    # The default is every peak, so the sentence is absent on an ordinary run.
    none = search_outcome({**counts, "anchors_unsearched": 0}, "sb-1")
    assert "unsearched" not in none["message"]


def test_the_outcome_names_the_samples_it_could_not_read():
    """A sample that raised is reported, not left to the log: the counts alone
    read like a batch that simply had less to find."""
    counts = {
        "anchors_searched": 12,
        "anchors_annotated": 7,
        "members_propagated": 30,
        "samples_searched": 2,
        "samples_rescored": 9,
        "samples_failed": 1,
    }
    outcome = search_outcome(counts, "sb-1")
    assert outcome["status"] == "partial"
    assert outcome["message"].endswith("1 sample could not be read and was skipped.")

    two = search_outcome({**counts, "samples_failed": 2}, "sb-1")
    assert two["status"] == "partial"
    assert two["message"].endswith("2 samples could not be read and were skipped.")
