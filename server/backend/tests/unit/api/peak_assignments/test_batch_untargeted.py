"""Unit tests for the per-anchor untargeted search's pure parts.

No DB: which member represents an anchor, how the representatives group into
searches, how an isotopologue row finds its owner's anchor, and what the
outcome says. See ``batch_untargeted.py``.
"""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import batch_untargeted
from mascope_backend.api.new.peak_assignments.batch_peaks import role_code
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    MIN_PERSISTENCE_SPECTRA,
    SPLIT_BOUNDS,
    Annotation,
    Occurrence,
    admitted_targets,
    choose_representatives,
    gate_search_rows,
    group_by_sample,
    merge_search_rows,
    occurrence_split,
    owner_anchor_of,
    persistent_anchors,
    search_config,
    search_outcome,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ARTIFACT,
    ROLE_REAGENT,
    untargeted_matches_to_peak_assignments,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    resolve_profile,
    with_secondary_channels,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
)
from mascope_tools.composition.mass_accuracy import PRED_SIGMA_PPM
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


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


# --- the merge across samples -------------------------------------------------
#
# The case it exists for: the uronium ion of C20H38O2 at m/z 371.33 (anchor X)
# is brightest in sample A, its 13C line at 372.33 (anchor Y) in sample B. A's
# search elects the ion and claims the line; B's search, where X is no target,
# reads the line on its own as C22H45NOS.


def _row(
    row_id,
    peak,
    formula,
    *,
    mz,
    role="M0",
    owner=None,
    ion=None,
    tier="candidate",
    fit=0.6,
):
    return {
        "peak_assignment_id": row_id,
        "sample_peak_id": peak,
        "sample_peak_mz": mz,
        "assigned_formula": formula,
        "ion_formula": ion,
        "ionization_mechanism_id": "mech",
        "role": role,
        "owner_peak_assignment_id": owner,
        "tier": tier,
        "fit_score": fit,
    }


_MEMBERS = [
    _member("X", "A", "a1", 9e6),
    _member("Y", "A", "a2", 2e6),
    _member("X", "B", "b1", 1e6),
    _member("Y", "B", "b2", 3e6),
]

_A_ROWS = [
    _row("x-m0", "a1", "C20H38O2", mz=371.3268, ion="C21H43N2O3+"),
    _row(
        "x-13c",
        "a2",
        "C20H38O2",
        mz=372.3305,
        role="iso_child",
        owner="x-m0",
        ion="[13C]C20H43N2O3+",
    ),
]

# Better on its own numbers than the claim, as on 13 of the 134 anchors the
# brief counted: the isotopologue reading wins anyway, as it does in a sample.
_B_ROWS = [
    _row("y-alone", "b2", "C22H45NOS", mz=372.3305, tier="assigned", fit=0.9),
]


def _both_orders(rows_by_sample, members=_MEMBERS):
    forward = merge_search_rows(rows_by_sample, members)
    backward = merge_search_rows(dict(reversed(list(rows_by_sample.items()))), members)
    return forward, backward


def test_a_line_one_sample_claims_takes_that_reading_in_either_order():
    for merge in _both_orders({"A": _A_ROWS, "B": _B_ROWS}):
        line = merge.annotations["Y"]
        assert line.role == "iso_child"
        assert line.owner_batch_peak_id == "X"
        assert line.formula == "C20H38O2"
        assert line.ion_formula == "[13C]C20H43N2O3+"
        assert merge.annotations["X"].role == "M0"
        # The standalone reading is written nowhere; the claim is written where
        # it was made, and B is to be searched again without the line.
        assert {r.row["peak_assignment_id"] for r in merge.readings["A"]} == {
            "x-m0",
            "x-13c",
        }
        assert "B" not in merge.readings
        assert merge.withheld == {"B": {"b2"}}


def test_a_line_nobody_claims_keeps_its_own_reading():
    merge = merge_search_rows({"B": _B_ROWS}, _MEMBERS)
    assert merge.annotations["Y"].formula == "C22H45NOS"
    assert merge.annotations["Y"].owner_batch_peak_id is None
    assert [r.row["peak_assignment_id"] for r in merge.readings["B"]] == ["y-alone"]
    assert merge.withheld == {}


def test_a_claim_by_an_ion_that_is_itself_a_line_does_not_hold():
    """X's own reading in A is the 13C line of W's ion in C, so X's ion is not
    committed and its claim on Y is no reading of Y: Y keeps its own, and A is
    searched again without X."""
    members = [*_MEMBERS, _member("W", "C", "c1", 9e6), _member("X", "C", "c2", 1e6)]
    rows = {
        "A": _A_ROWS,
        "B": _B_ROWS,
        "C": [
            _row("w-m0", "c1", "C19H36O2", mz=370.3234),
            _row(
                "w-13c",
                "c2",
                "C19H36O2",
                mz=371.3268,
                role="iso_child",
                owner="w-m0",
            ),
        ],
    }
    for merge in _both_orders(rows, members):
        assert merge.annotations["X"].owner_batch_peak_id == "W"
        assert merge.annotations["Y"].formula == "C22H45NOS"
        assert merge.annotations["Y"].owner_batch_peak_id is None
        assert merge.withheld == {"A": {"a1"}}
        assert "A" not in merge.readings  # neither X's election nor its claim
        assert [r.row["peak_assignment_id"] for r in merge.readings["B"]] == ["y-alone"]


def test_a_line_whose_only_claim_does_not_hold_is_left_unannotated():
    """Once A is searched again without X, Y has no reading of its own left in
    a sample where an uncommitted ion claimed it: no reading is better than a
    line of an ion the batch does not hold."""
    members = [*_MEMBERS, _member("W", "C", "c1", 9e6), _member("X", "C", "c2", 1e6)]
    rows = {
        "A": _A_ROWS,
        "C": [
            _row("w-m0", "c1", "C19H36O2", mz=370.3234),
            _row(
                "w-13c",
                "c2",
                "C19H36O2",
                mz=371.3268,
                role="iso_child",
                owner="w-m0",
            ),
        ],
    }
    merge = merge_search_rows(rows, members)
    assert "Y" not in merge.annotations
    assert set(merge.annotations) == {"W", "X"}


def test_of_two_claims_on_a_line_the_better_one_wins_in_either_order():
    members = [*_MEMBERS, _member("V", "C", "c1", 9e6), _member("Y", "C", "c2", 1e6)]
    rows = {
        "A": [{**_A_ROWS[0]}, {**_A_ROWS[1], "tier": "below_assignability"}],
        "B": _B_ROWS,
        "C": [
            _row("v-m0", "c1", "C21H42O", mz=371.33),
            _row(
                "v-13c",
                "c2",
                "C21H42O",
                mz=372.3305,
                role="iso_child",
                owner="v-m0",
                tier="candidate",
                fit=0.4,
            ),
        ],
    }
    for merge in _both_orders(rows, members):
        assert merge.annotations["Y"].owner_batch_peak_id == "V"
        # Both claims are lines of committed ions, so both are written in the
        # samples that made them; the anchor's annotation is the better one.
        assert {r.row["peak_assignment_id"] for r in merge.readings["A"]} == {
            "x-m0",
            "x-13c",
        }
        assert {r.row["peak_assignment_id"] for r in merge.readings["C"]} == {
            "v-m0",
            "v-13c",
        }


def test_of_two_claims_of_one_tier_the_better_fit_wins_in_either_order():
    members = [*_MEMBERS, _member("V", "C", "c1", 9e6), _member("Y", "C", "c2", 1e6)]
    rows = {
        "A": [{**_A_ROWS[0]}, {**_A_ROWS[1], "fit_score": 0.7}],
        "C": [
            _row("v-m0", "c1", "C21H42O", mz=371.33),
            _row(
                "v-13c",
                "c2",
                "C21H42O",
                mz=372.3305,
                role="iso_child",
                owner="v-m0",
                fit=0.5,
            ),
        ],
    }
    for merge in _both_orders(rows, members):
        assert merge.annotations["Y"].owner_batch_peak_id == "X"


def test_a_long_chain_resolves_from_its_head_without_recursing_through_it():
    """Each anchor is the 13C line of the one below it, each searched in a
    sample of its own. The head is committed, so its line is a line; that line
    is then no committed ion, so the next anchor keeps its own reading, and so
    on, alternating. Anchors are decided lightest first, so however long the
    chain the merge never recurses down it - the ids here sort the other way
    round to show the order is the masses', not the ids'."""
    length = 1500
    members, rows = [], {}
    for i in range(length):
        anchor, sample = f"bp-{length - i:05d}", f"s-{i:05d}"
        members.append(_member(anchor, sample, f"own-{i}", 9e6))
        sample_rows = [_row(f"m0-{i}", f"own-{i}", f"F{i}", mz=100.0 + i)]
        if i + 1 < length:
            members.append(_member(f"bp-{length - i - 1:05d}", sample, f"line-{i}", 1))
            sample_rows.append(
                _row(
                    f"iso-{i}",
                    f"line-{i}",
                    f"F{i}",
                    mz=101.0 + i,
                    role="iso_child",
                    owner=f"m0-{i}",
                )
            )
        rows[sample] = sample_rows
    merge = merge_search_rows(rows, members)
    roles = [merge.annotations[f"bp-{length - i:05d}"].role for i in range(length)]
    assert roles[:4] == ["M0", "iso_child", "M0", "iso_child"]
    assert roles == ["M0" if i % 2 == 0 else "iso_child" for i in range(length)]


def test_a_row_on_a_claimed_member_is_no_reading():
    """A pre-pass owns the peak; an envelope landing on it does not take it."""
    members = [
        _member("X", "A", "a1", 9e6),
        _member("Y", "A", "a2", 2e6, role=REAGENT_ROLE_CODE),
    ]
    merge = merge_search_rows({"A": _A_ROWS}, members)
    assert set(merge.annotations) == {"X"}
    assert [r.row["peak_assignment_id"] for r in merge.readings["A"]] == ["x-m0"]


def test_the_search_config_is_the_orchestrators():
    # Both paths configure the finder from one resolved profile, which is what
    # makes a batch search comparable with a per-sample run.
    resolved = resolve_profile(
        PeakAssignmentConfig(), ["[M+Br]-"], instrument_type="orbi", polarity="-"
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


def _match(mz, formula, ion, mechanism, family=()):
    return {
        "mz": mz,
        "formula": formula,
        "ion": ion,
        "ionization_mechanism": mechanism,
        "isotopic_pattern_score": 0.99,
        "isotope_label": "M0",
        "other_candidates": "",
        "mz_error_ppm": 0.0,
        "intensity_error": 0.0,
        "same_ion_alternatives": [
            {
                "formula": alt_formula,
                "ion": alt_ion,
                "ionization_mechanism": alt_mechanism,
                "neutral_mass": 0.0,
                "unsaturation": None,
            }
            for alt_formula, alt_ion, alt_mechanism in family
        ],
    }


def test_the_search_reads_the_partner_gate_over_its_own_rows():
    # A batch search runs no Stage A and none of the judging passes, so the
    # one rule its rows can apply for themselves is the partner gate: the
    # tropylium ion reads as toluene less a hydride here as in a run of the
    # sample, not as the protonated C7H6 the election prefers.
    config = PeakAssignmentConfig()
    resolved = resolve_profile(config, ["[M]+."], instrument_type="orbi", polarity="+")
    resolved = with_secondary_channels(
        resolved,
        [91.0542, 92.0621, 202.0777],
        [1.0e6, 3.0e6, 5.0e6],
        ["[M+H]+", "[M-H]+"],
    )
    assert {"[M+H]+", "[M-H]+"} <= resolved.partner_gated_channels
    ids = {"[M]+.": "im-ct", "[M+H]+": "im-h", "[M-H]+": "im-hydride"}
    peaks = pd.DataFrame(
        [
            {"sample_peak_id": "p1", "mz": 91.0542, "intensity": 1.0e6},
            {"sample_peak_id": "p2", "mz": 92.0621, "intensity": 3.0e6},
        ]
    )
    rows = untargeted_matches_to_peak_assignments(
        pd.DataFrame(
            [
                _match(
                    91.0542,
                    "C7H6",
                    "C7H7+",
                    "[M+H]+",
                    family=[("C7H8", "C7H7+", "[M-H]+")],
                ),
                _match(92.0621, "C7H8", "C7H8+", "[M]+."),
            ]
        ),
        peaks_df=peaks,
        sample_item_id="si-1",
        peak_assignment_run_id="run-1",
        candidate_threshold=config.candidate_threshold,
        assigned_threshold=config.assigned_threshold,
        mechanism_id_by_notation=ids,
        max_alternatives=5,
        minor_channels=resolved.minor_channels,
    )
    gated = gate_search_rows(
        rows,
        resolved_profile=resolved,
        mechanism_id_by_notation=ids,
        tier_bands=config.tier_bands(),
    )
    tropylium = next(row for row in gated if row["sample_peak_id"] == "p1")
    assert tropylium["assigned_formula"] == "C7H8"
    assert tropylium["ionization_mechanism_id"] == "im-hydride"
    assert tropylium["provenance"]["partner_gate"]["through"] == "[M-H]+"


class _Searched(Exception):
    """Raised by the fake finder once it has seen what the search hands it."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename, instrument", [("orbi-sample.raw", "orbi"), ("tof-sample.h5", "tof")]
)
async def test_a_batch_search_is_scored_at_its_samples_own_instrument(
    filename, instrument
):
    # No Stage A runs on this path, so nothing measured the sample's width: it
    # is scored at its own instrument class's, through the library's
    # derivation, as a run of the sample with a thin library is.
    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="Sample One",
        filename=filename,
        polarity="-",
    )
    peaks = pd.DataFrame([{"sample_peak_id": "p1", "mz": 160.8413, "intensity": 1.0e6}])
    mechanism = SimpleNamespace(
        ionization_mechanism="[M-H]-", ionization_mechanism_id="im-h"
    )
    seen = {}

    def finder(*args, **kwargs):
        seen["scoring"] = kwargs["scoring"]
        raise _Searched

    with (
        patch.object(batch_untargeted, "fetch_sample", AsyncMock(return_value=sample)),
        patch.object(batch_untargeted, "load_sample_peaks", return_value=peaks),
        patch.object(
            batch_untargeted,
            "fetch_sample_mechanisms",
            AsyncMock(return_value=(["im-h"], [mechanism])),
        ),
        patch.object(
            batch_untargeted, "fetch_mechanisms_by_notation", AsyncMock(return_value=[])
        ),
        patch.object(
            batch_untargeted,
            "default_match_params",
            AsyncMock(
                return_value=SimpleNamespace(
                    mz_tolerance=15.0, isotope_abundance_threshold=1e-4
                )
            ),
        ),
        patch.object(batch_untargeted, "_resolution_of", AsyncMock(return_value=None)),
        patch.object(batch_untargeted, "assign_compositions", finder),
        pytest.raises(_Searched),
    ):
        await batch_untargeted._search_sample_peaks(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )

    assert seen["scoring"].sigma_ppm == pytest.approx(
        math.hypot(INSTRUMENT_FALLBACK_SIGMA_PPM[instrument], PRED_SIGMA_PPM)
    )
    assert seen["scoring"].mz_tolerance_ppm == 15.0


@pytest.mark.asyncio
@pytest.mark.parametrize("counted", [True, False], ids=["counted", "uncounted"])
async def test_a_batch_search_reads_the_peak_lists_scan_counts(counted):
    # As a run's search does: a dim line is read at its mean per scan where
    # the file keeps the counts.
    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="Sample One",
        filename="orbi-sample.raw",
        polarity="-",
    )
    peaks = pd.DataFrame([{"sample_peak_id": "p1", "mz": 160.8413, "intensity": 1.0e6}])
    if counted:
        peaks["n_scans"] = 3.0
        peaks["n_scans_total"] = 4.0
    mechanism = SimpleNamespace(
        ionization_mechanism="[M-H]-", ionization_mechanism_id="im-h"
    )
    seen = {}

    def finder(frame, *args, **kwargs):
        seen["columns"] = set(frame.columns)
        raise _Searched

    with (
        patch.object(batch_untargeted, "fetch_sample", AsyncMock(return_value=sample)),
        patch.object(batch_untargeted, "load_sample_peaks", return_value=peaks),
        patch.object(
            batch_untargeted,
            "fetch_sample_mechanisms",
            AsyncMock(return_value=(["im-h"], [mechanism])),
        ),
        patch.object(
            batch_untargeted, "fetch_mechanisms_by_notation", AsyncMock(return_value=[])
        ),
        patch.object(
            batch_untargeted,
            "default_match_params",
            AsyncMock(
                return_value=SimpleNamespace(
                    mz_tolerance=15.0, isotope_abundance_threshold=1e-4
                )
            ),
        ),
        patch.object(batch_untargeted, "_resolution_of", AsyncMock(return_value=None)),
        patch.object(batch_untargeted, "assign_compositions", finder),
        pytest.raises(_Searched),
    ):
        await batch_untargeted._search_sample_peaks(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )

    assert ({"n_scans", "n_scans_total"} <= seen["columns"]) is counted


async def _search_ammonium_reading(*, opened, known):
    """A batch search of one uronium sample whose finder reads one peak as
    C9H14O3 through ammonium, the channel's carrier shown or not (``opened``),
    over a known set of ``known``.

    :return: The search's rows and the fake known-set read.
    """
    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="Sample One",
        filename="orbi-sample.raw",
        polarity="+",
    )
    # 138.0986 is the carrier that opens the ammonium channel on a uronium
    # source (`with_secondary_channels`); 188.1281 is C9H14O3 + NH4+.
    specs = [("p0", 100.0, 1.0e6), ("p2", 188.1281, 1.0e5)]
    if opened:
        specs.append(("p1", 138.0986, 1.0e4))
    peaks = pd.DataFrame(
        [
            {"sample_peak_id": pid, "mz": mz, "intensity": intensity}
            for pid, mz, intensity in specs
        ]
    )
    mode = [
        SimpleNamespace(
            ionization_mechanism=notation,
            ionization_mechanism_id=mechanism_id,
            ionization_mechanism_polarity="+",
        )
        for notation, mechanism_id in (
            ("[M+H]+", "im-h"),
            ("[M+CH4N2O+H]+", "im-urea"),
        )
    ]
    ammonium = SimpleNamespace(
        ionization_mechanism="[M+NH4]+",
        ionization_mechanism_id="im-nh4",
        ionization_mechanism_polarity="+",
    )
    reading = _match(188.1281, "C9H14O3", "C9H18NO3+", "[M+NH4]+")
    known_isotopes = AsyncMock(return_value=known)

    def finder(*args, **kwargs):
        return pd.DataFrame([reading]), None

    with (
        patch.object(batch_untargeted, "fetch_sample", AsyncMock(return_value=sample)),
        patch.object(batch_untargeted, "load_sample_peaks", return_value=peaks),
        patch.object(
            batch_untargeted,
            "fetch_sample_mechanisms",
            AsyncMock(return_value=(["im-h", "im-urea"], mode)),
        ),
        patch.object(
            batch_untargeted,
            "fetch_mechanisms_by_notation",
            AsyncMock(return_value=[ammonium]),
        ),
        patch.object(
            batch_untargeted,
            "default_match_params",
            AsyncMock(
                return_value=SimpleNamespace(
                    mz_tolerance=15.0, isotope_abundance_threshold=1e-4
                )
            ),
        ),
        patch.object(batch_untargeted, "_resolution_of", AsyncMock(return_value=None)),
        patch.object(
            batch_untargeted,
            "recorded_envelope_tolerance",
            AsyncMock(return_value=None),
        ),
        patch.object(
            batch_untargeted, "sample_reagent_offset", AsyncMock(return_value=None)
        ),
        patch.object(batch_untargeted, "_seeded_fits", AsyncMock(return_value={})),
        patch.object(batch_untargeted, "_fetch_known_isotopes", known_isotopes),
        patch.object(batch_untargeted, "assign_compositions", finder),
    ):
        rows, _, _ = await batch_untargeted._search_sample_peaks(
            "si-1", {"p2"}, PeakAssignmentConfig()
        )
    return rows, known_isotopes


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [True, False], ids=["listed", "unlisted"])
async def test_a_list_naming_the_neutral_lifts_a_batch_searchs_cap(listed):
    # Step 3.4 as a run of the sample reads it: a reading through a secondary
    # channel reaches assigned when a list the sample is read against names
    # its neutral, though no Stage A ran here to read the list.
    known = pd.DataFrame(
        {"target_compound_formula": ["O3C9H14" if listed else "C9H16O3"]}
    )
    rows, known_isotopes = await _search_ammonium_reading(opened=True, known=known)

    (row,) = [row for row in rows if row["sample_peak_id"] == "p2"]
    assert row["ionization_mechanism_id"] == "im-nh4"
    assert row["provenance"]["minor_channel"]["corroborated_by"] == (
        "list" if listed else None
    )
    assert row["provenance"]["minor_channel"]["capped"] is not listed
    assert row["tier"] == (TIER_ASSIGNED if listed else TIER_CANDIDATE)
    # Read as a run reads it: the target library through the mode's own
    # mechanisms, the reference lists through every searched one, under the
    # context's ceiling.
    (call,) = known_isotopes.await_args_list
    sample, threshold, mechanism_ids, mechanisms = call.args
    assert sample.sample_item_id == "si-1"
    assert threshold == 1e-4
    assert mechanism_ids == ["im-h", "im-urea"]
    assert [m.ionization_mechanism_id for m in mechanisms] == [
        "im-h",
        "im-urea",
        "im-nh4",
    ]
    assert call.kwargs == {
        "known_window": resolve_profile(
            PeakAssignmentConfig(),
            ["[M+H]+", "[M+CH4N2O+H]+"],
            instrument_type="orbi",
            polarity="+",
        ).context.known_window
    }


@pytest.mark.asyncio
async def test_a_batch_search_with_no_secondary_channel_reads_no_list():
    # Only a secondary channel's readings consult the lists, so a sample whose
    # spectrum opens none pays for no read of them.
    _, known_isotopes = await _search_ammonium_reading(
        opened=False, known=pd.DataFrame()
    )
    known_isotopes.assert_not_awaited()


# --- persistence: the second admission (step 4.2b) --------------------------------


def _bimodal(n_spectra, transient, persistent):
    """Shares of a partition of ``n_spectra``: ``transient`` traces in one or
    two spectra, ``persistent`` ones in all but one or all."""
    shares = [(1 + i % 2) / n_spectra for i in range(transient)]
    shares += [(n_spectra - i % 2) / n_spectra for i in range(persistent)]
    return shares


def test_the_split_falls_between_the_two_modes():
    split = occurrence_split(_bimodal(40, 200, 30), 40)
    # Above every transient share and below every persistent one.
    assert 2 / 40 < split < 39 / 40
    assert SPLIT_BOUNDS[0] <= split <= SPLIT_BOUNDS[1]


def test_the_split_is_the_batchs_own():
    # Two batches of one size whose modes sit apart split apart: the split is
    # measured off the distribution, not a constant.
    low = [0.1] * 50 + [0.3] * 50
    high = [0.6] * 50 + [0.9] * 50
    assert occurrence_split(low, 20) == pytest.approx(0.25)
    assert occurrence_split(high, 20) == pytest.approx(0.75)
    middle = [0.3] * 50 + [0.6] * 50
    assert occurrence_split(middle, 20) == pytest.approx(0.45)


def test_the_split_sits_midway_between_neighbouring_shares():
    shares = [1 / 12] * 30 + [2 / 12] * 10 + [11 / 12] * 8 + [1.0] * 4
    split = occurrence_split(shares, 12)
    assert split == pytest.approx(0.5 * (2 / 12 + 11 / 12))


def test_a_one_mode_batch_is_held_inside_the_bounds():
    # Nearly every trace transient: Otsu still splits, near the low end, and
    # the bound keeps the split from taking every recurring noise line.
    nearly_all_low = [1 / 50] * 300 + [2 / 50] * 100 + [3 / 50] * 5
    assert occurrence_split(nearly_all_low, 50) == SPLIT_BOUNDS[0]
    nearly_all_high = [48 / 50] * 5 + [49 / 50] * 100 + [1.0] * 300
    assert occurrence_split(nearly_all_high, 50) == SPLIT_BOUNDS[1]


def test_too_few_spectra_or_one_share_have_no_split():
    shares = _bimodal(MIN_PERSISTENCE_SPECTRA - 1, 20, 5)
    assert occurrence_split(shares, MIN_PERSISTENCE_SPECTRA - 1) is None
    assert occurrence_split(_bimodal(MIN_PERSISTENCE_SPECTRA, 20, 5), 10) is not None
    assert occurrence_split([0.5] * 30, 20) is None


def test_persistent_anchors_are_read_per_partition():
    orbitrap = ("mode", "sum_peak_heights")
    tof = ("mode", "sum_peak_areas")
    counts = [(f"o{i}", orbitrap, 1) for i in range(20)]
    counts += [("o-persistent", orbitrap, 11), ("o-faint", orbitrap, 2)]
    # The TOF partition has too few spectra for a split: nothing there is
    # persistent, however often it recurs.
    counts += [(f"t{i}", tof, 1) for i in range(20)] + [("t-everywhere", tof, 5)]
    persistent, splits = persistent_anchors(counts, {orbitrap: 12, tof: 5})

    assert set(persistent) == {"o-persistent"}
    assert persistent["o-persistent"].share == pytest.approx(11 / 12)
    assert persistent["o-persistent"].split == splits[orbitrap]
    assert splits[tof] is None


def test_an_anchor_is_persistent_only_above_the_split():
    # Otsu splits these at 0.175, which the bound lifts to 0.25: a share of
    # exactly 0.25 is not above it.
    partition = ("mode", "sum_peak_heights")
    counts = [(f"low{i}", partition, 2) for i in range(10)]
    counts += [(f"edge{i}", partition, 5) for i in range(10)]
    persistent, splits = persistent_anchors(counts, {partition: 20})
    assert splits[partition] == SPLIT_BOUNDS[0]
    assert persistent == {}


def _frame(*peaks):
    return pd.DataFrame(
        [
            {"sample_peak_id": pid, "mz": mz, "intensity": intensity}
            for pid, mz, intensity in peaks
        ]
    )


def test_a_faint_persistent_peak_is_admitted_beside_the_floor():
    frame = _frame(
        ("bright", 181.07, 9000.0),
        ("faint-persistent", 150.0, 40.0),
        ("faint-transient", 160.0, 40.0),
    )
    targets, scope, by_persistence = admitted_targets(
        frame,
        {"bright", "faint-persistent", "faint-transient"},
        {"faint-persistent"},
        peak_intensity_threshold=100.0,
        max_untargeted_peaks=None,
        ceiling=5000,
    )

    assert set(targets["sample_peak_id"]) == {"bright", "faint-persistent"}
    assert by_persistence == {"faint-persistent"}
    assert scope["admitted_by_persistence"] == 1
    # The faint transient peak is no eligible peak, as under the floor today.
    assert scope["eligible_peaks"] == 2
    assert scope["searched_peaks"] == 2
    assert scope["limited"] is False


def test_a_persistent_peak_past_the_cap_is_admitted():
    frame = _frame(("a", 100.0, 900.0), ("b", 110.0, 800.0), ("c", 120.0, 700.0))
    targets, scope, by_persistence = admitted_targets(
        frame,
        {"a", "b", "c"},
        {"c"},
        peak_intensity_threshold=0.0,
        max_untargeted_peaks=1,
        ceiling=5000,
    )

    assert set(targets["sample_peak_id"]) == {"a", "c"}
    assert by_persistence == {"c"}
    # "b" is eligible and was left by the cap; "c" was eligible already.
    assert scope["eligible_peaks"] == 3
    assert scope["searched_peaks"] == 2
    assert scope["limited"] is True


def test_a_persistent_peak_the_height_admits_is_not_admitted_by_persistence():
    frame = _frame(("a", 100.0, 900.0))
    _, scope, by_persistence = admitted_targets(
        frame,
        {"a"},
        {"a"},
        peak_intensity_threshold=0.0,
        max_untargeted_peaks=None,
        ceiling=5000,
    )
    assert by_persistence == set()
    assert scope["admitted_by_persistence"] == 0


def test_the_ceiling_bounds_both_admissions_the_faint_brightest_first():
    frame = _frame(
        ("bright", 100.0, 900.0),
        ("p1", 110.0, 30.0),
        ("p2", 120.0, 20.0),
        ("p3", 130.0, 10.0),
    )
    targets, scope, by_persistence = admitted_targets(
        frame,
        {"bright", "p1", "p2", "p3"},
        {"p1", "p2", "p3"},
        peak_intensity_threshold=100.0,
        max_untargeted_peaks=None,
        ceiling=3,
    )
    assert set(targets["sample_peak_id"]) == {"bright", "p1", "p2"}
    assert by_persistence == {"p1", "p2"}
    assert scope["eligible_peaks"] == 4
    assert scope["limited"] is True


def test_a_peak_that_represents_no_anchor_is_not_admitted_by_persistence():
    frame = _frame(("a", 100.0, 900.0), ("b", 110.0, 10.0))
    targets, _, by_persistence = admitted_targets(
        frame,
        {"a"},
        {"b"},
        peak_intensity_threshold=100.0,
        max_untargeted_peaks=None,
        ceiling=5000,
    )
    assert set(targets["sample_peak_id"]) == {"a"}
    assert by_persistence == set()


def test_the_record_names_the_reading_elected_on_its_own_peak():
    occurrence = Occurrence(n_present=11, n_samples=12, split=0.4583333)
    m0 = Annotation("C5H8O2", "C5H9O2+", "mech-h", "M0", None)
    line = Annotation("C6H12O6", "C6H13O6+", "mech-h", "iso_child", "bp-owner")

    record = occurrence.record(m0, "run-1")
    assert record == {
        "share": 0.9167,
        "n_present": 11,
        "n_samples": 12,
        "split": 0.4583,
        "formula": "C5H8O2",
        "ion_formula": "C5H9O2+",
        "ionization_mechanism_id": "mech-h",
        "batch_peak_run_id": "run-1",
    }
    # An isotope line of another ion, and nothing found, elect no reading of
    # the anchor's own: the admission is still recorded.
    for annotation in (line, None):
        empty = occurrence.record(annotation, "run-1")
        assert empty["formula"] is None
        assert empty["ion_formula"] is None
        assert empty["ionization_mechanism_id"] is None
        assert empty["share"] == 0.9167


def test_the_outcome_says_what_persistence_admitted_and_what_it_holds():
    counts = {
        "anchors_searched": 10,
        "anchors_annotated": 4,
        "members_propagated": 7,
        "samples_searched": 2,
        "anchors_admitted_by_persistence": 3,
        "anchors_held_by_persistence": 2,
    }
    message = search_outcome(counts, "sb-1")["message"]
    assert "3 batch peaks under the intensity floor or past the peak cap" in message
    assert "2 of them are held at candidate until an isotope line" in message
    counts["anchors_held_by_persistence"] = 1
    assert "1 of them is held at candidate" in search_outcome(counts, "sb-1")["message"]
    # One admitted, and its reading held.
    counts["anchors_admitted_by_persistence"] = 1
    message = search_outcome(counts, "sb-1")["message"]
    assert "1 batch peak under" in message
    assert "recurs across the batch; its reading is held at candidate" in message
    # None held - each lifted, or below candidate on the vote: nothing is said
    # held.
    counts["anchors_held_by_persistence"] = 0
    message = search_outcome(counts, "sb-1")["message"]
    assert "searched because the peak recurs across the batch." in message
    assert "held" not in message
    counts["anchors_admitted_by_persistence"] = 0
    assert "recurs" not in search_outcome(counts, "sb-1")["message"]


@pytest.mark.asyncio
async def test_the_search_names_its_run_on_the_records_it_writes():
    seen = {}

    async def search(sample_batch_id, config, **kwargs):
        seen.update(kwargs)
        return {"anchors_searched": 0}

    with (
        patch.object(batch_untargeted, "start_run", AsyncMock(return_value="run-7")),
        patch.object(batch_untargeted, "complete_run", AsyncMock()),
        patch.object(batch_untargeted, "run_batch_untargeted_search", search),
    ):
        await batch_untargeted.search_batch_untargeted.__wrapped__("sb-1")

    assert seen["batch_peak_run_id"] == "run-7"
