"""Unit tests for the per-anchor untargeted search's pure parts.

No DB: which member represents an anchor, how the representatives group into
searches, how an isotopologue row finds its owner's anchor, and what the
outcome says. See ``batch_untargeted.py``.
"""

import math
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import DEFAULT, AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from stage_stubs import seeded_frame  # the tests root, on sys.path

from mascope_backend.api.new.peak_assignments import batch_runs, batch_untargeted
from mascope_backend.api.new.peak_assignments.batch_peaks import role_code
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    MIN_PERSISTENCE_SPECTRA,
    SPLIT_BOUNDS,
    Annotation,
    Occurrence,
    choose_representatives,
    group_by_sample,
    merge_search_rows,
    occurrence_split,
    owner_anchor_of,
    persistent_anchors,
    search_outcome,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.partner_gate import PARTNER_GATE_KEPT
from mascope_backend.api.new.peak_assignments.pipeline import admitted_targets
from mascope_backend.api.new.peak_assignments.profiles import (
    NamedContext,
    ResolvedProfile,
    resolve_profile,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ARTIFACT,
    ROLE_REAGENT,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import StageAKnown
from mascope_backend.api.new.peak_assignments.tiers import TIER_ASSIGNED, TIER_CANDIDATE
from mascope_match.params import OrbiMatchParams
from mascope_tools.composition.mass_accuracy import PRED_SIGMA_PPM
from mascope_tools.composition.profiles import CHAMBER, INSTRUMENT_FALLBACK_SIGMA_PPM


REAGENT_ROLE_CODE = role_code(ROLE_REAGENT)
ARTIFACT_ROLE_CODE = role_code(ROLE_ARTIFACT)


def _member(anchor, sample, peak, intensity, role=None, candidate=None):
    return SimpleNamespace(
        batch_peak_id=anchor,
        sample_item_id=sample,
        sample_peak_id=peak,
        intensity=intensity,
        role=role,
        candidate=candidate,
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


def test_a_member_that_reads_a_formula_is_no_representative():
    """An anchor most of whose members the reagent pre-pass claimed stays
    unassigned however its other members read. Its brightest real member
    reads a formula its sample's ledger already holds, which the search would
    not search, as a run never searches its Stage A's peaks: the anchor is
    searched on the brightest member that reads nothing."""
    members = [
        _member("bp-1", "s1", "p1", 9e6, role=REAGENT_ROLE_CODE),
        _member("bp-1", "s2", "p1", 9e6, role=REAGENT_ROLE_CODE),
        _member("bp-1", "s3", "p1", 5e5, candidate=0),
        _member("bp-1", "s4", "p1", 2e5),
    ]

    assert choose_representatives(members)["bp-1"].sample_item_id == "s4"
    # With none that reads nothing, the anchor is not searched.
    assert choose_representatives(members[:3]) == {}


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


class _Searched(Exception):
    """Raised by the fake finder once it has seen what the search hands it."""


_SI = "mascope_backend.api.new.peak_assignments.sample_inputs"
_SM = "mascope_backend.api.new.peak_assignments.sample_mechanisms"
_MR = "mascope_backend.api.new.peak_assignments.model_readers"
_SA = "mascope_backend.api.new.peak_assignments.stage_a_pass"
_PIPE = "mascope_backend.api.new.peak_assignments.pipeline"
_BU = "mascope_backend.api.new.peak_assignments.batch_untargeted"


def _sample(filename="orbi-sample.raw", polarity="-"):
    return SimpleNamespace(
        sample_item_id="si-1",
        sample_batch_id="sb-1",
        sample_item_name="Sample One",
        filename=filename,
        polarity=polarity,
    )


def _mechanism(notation, mechanism_id):
    return SimpleNamespace(
        ionization_mechanism=notation,
        ionization_mechanism_id=mechanism_id,
        ionization_mechanism_polarity=notation[-1],
    )


def _recorded(width: float) -> dict:
    """A latest run's config whose one record of its measurement model fitted
    ``width`` and no centre."""
    return {
        "measurement_model": {
            "version": 1,
            "mu_ppm": None,
            "mu_source": "none",
            "sigma_ppm": width,
            "fallback_sigma_ppm": 0.5,
        }
    }


def _searching(
    sample,
    peaks,
    mechanisms,
    *,
    finder,
    secondary=(),
    width=None,
    known=None,
    explained=frozenset(),
    resolution=None,
):
    """Stand in for what a batch search of one sample reads: its sample row,
    peaks and mechanisms, its file's resolving power and its latest run's
    record of its measurement model (none unless a test names a ``width``),
    the batch's records of it (none), its known set and its finder.
    Everything between them is the stage list's own.

    :return: The stack of stand-ins, and the ones a test reads back.
    """
    stack = ExitStack()

    def stand_in(target, value=DEFAULT, **kwargs):
        return stack.enter_context(patch(target, value, **kwargs))

    mocks = {
        "fetch": stand_in(f"{_BU}.fetch_sample", AsyncMock(return_value=sample)),
        "explained": stand_in(
            f"{_BU}._explained_peaks", AsyncMock(return_value=frozenset(explained))
        ),
        "peaks": stand_in(f"{_SI}.load_sample_peaks", return_value=peaks),
        "mechanisms": stand_in(
            f"{_SM}.fetch_sample_mechanisms",
            AsyncMock(
                return_value=(
                    [m.ionization_mechanism_id for m in mechanisms],
                    mechanisms,
                )
            ),
        ),
        "secondary": stand_in(
            f"{_SM}.fetch_mechanisms_by_notation",
            AsyncMock(return_value=list(secondary)),
        ),
        "params": stand_in(
            f"{_PIPE}.default_match_params",
            AsyncMock(
                return_value=SimpleNamespace(
                    mz_tolerance=15.0, isotope_abundance_threshold=1e-4
                )
            ),
        ),
        "setup": stand_in(f"{_PIPE}.sample_setup", AsyncMock(return_value=None)),
        "purity": stand_in(
            f"{_PIPE}.sample_label_purity", AsyncMock(return_value=None)
        ),
        "species": stand_in(
            "mascope_backend.api.new.peak_assignments.known_species.batch_species_for",
            AsyncMock(return_value=[]),
        ),
        "series": stand_in(
            "mascope_backend.api.new.peak_assignments.batch_lines.batch_series_by_peak",
            AsyncMock(return_value={}),
        ),
        "resolution": stand_in(
            f"{_SI}._resolution_of", AsyncMock(return_value=resolution)
        ),
        "cap": stand_in(
            "mascope_backend.api.new.peak_assignments.judging_loop._source_line_cap",
            AsyncMock(return_value=None),
        ),
        "latest": stand_in(
            f"{_MR}._latest_in_app_run",
            AsyncMock(
                return_value=(None, None)
                if width is None
                else ("run-1", _recorded(width))
            ),
        ),
        "known": stand_in(
            f"{_SI}._fetch_known_isotopes",
            AsyncMock(return_value=pd.DataFrame() if known is None else known),
        ),
        "seeded": stand_in(
            "mascope_backend.api.new.peak_assignments.judging_loop._seeded_rescore",
            AsyncMock(return_value=({}, None)),
        ),
        "finder": stand_in(f"{_PIPE}.assign_compositions", finder),
    }
    # The recorded measurement reads the sample's inputs in its own module, and
    # Stage A its known isotopes: the same stand-ins there.
    stand_in(f"{_MR}.load_sample_peaks", mocks["peaks"])
    stand_in(f"{_MR}.fetch_sample_mechanisms", mocks["mechanisms"])
    stand_in(f"{_MR}._resolution_of", mocks["resolution"])
    stand_in(f"{_SA}._read_known_set", _stage_a_known(mocks["known"]))
    return stack, mocks


def _stage_a_known(known: AsyncMock) -> AsyncMock:
    """Stage A's known set read through the same stand-in as the search's,
    with no reference ion left out."""

    async def read(*args, **kwargs):
        return StageAKnown(await known(*args, **kwargs), pd.DataFrame(), {})

    return AsyncMock(side_effect=read)


def _peaks(specs):
    return pd.DataFrame(
        [
            {"sample_peak_id": pid, "mz": mz, "intensity": intensity}
            for pid, mz, intensity in specs
        ]
    )


@pytest.mark.asyncio
async def test_a_batch_search_runs_every_stage_of_the_list():
    # The batch search of one sample is the stage list's BATCH_SEARCH path:
    # the run's stages, with the search on the representatives proposed and
    # the sample's recorded measurement.
    from mascope_backend.api.new.peak_assignments import pipeline

    passes = []
    run_stages = pipeline.run_stages

    async def recording(ctx):
        passes.append(ctx)
        return await run_stages(ctx)

    stack, mocks = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=lambda *args, **kwargs: (pd.DataFrame(), None),
        explained={"p0"},
    )
    with stack, patch(f"{_BU}.run_stages", side_effect=recording):
        rows, unsearched, admitted = await batch_untargeted._search_sample(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )

    (ctx,) = passes
    assert ctx.path is pipeline.BATCH_SEARCH
    assert ctx.path.measurement is pipeline.Measurement.RECORDED
    assert ctx.ran == list(pipeline.STAGES)
    assert ctx.targets == {"p1"} and ctx.explained_peak_ids == {"p0"}
    assert (rows, unsearched, admitted) == ([], 0, set())


@pytest.mark.asyncio
async def test_a_batch_search_reads_its_lists_at_the_files_resolution():
    # The stage list reads the file's R(m/z) once, and the lists the search
    # reads for the neutrals they name are resolved at that reading, as a
    # run's Stage A resolves its known set at it (MAS-977).
    def orbitrap(mz):
        return 100_000.0

    stack, mocks = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=lambda *args, **kwargs: (pd.DataFrame(), None),
        resolution=orbitrap,
    )
    with stack:
        await batch_untargeted._search_sample("si-1", {"p1"}, PeakAssignmentConfig())

    mocks["resolution"].assert_awaited_once()
    mocks["known"].assert_awaited_once()
    assert mocks["known"].await_args.kwargs["resolution"] is orbitrap
    # For the neutrals alone: the reference lists' ions, not their lines,
    # which nothing here matches (MAS-1274).
    assert mocks["known"].await_args.kwargs["lines"] is False


@pytest.mark.asyncio
async def test_a_judging_reads_what_its_lists_hold_without_their_lines():
    """The neutrals and the ions a judging of the batch stages reads off its
    lists (``pipeline._read_lists``) are read through the mechanisms it
    judges by, and off the reference lists' ions alone: it matches none of
    their lines (MAS-1274)."""
    from mascope_backend.api.new.peak_assignments import pipeline

    judged = [_mechanism("[M-H]-", "im-h"), _mechanism("[M+Br]-", "im-br")]
    ctx = SimpleNamespace(
        sample=_sample(),
        match_params=SimpleNamespace(isotope_abundance_threshold=1e-4),
        mechanism_ids=["im-h"],
        judged_mechanisms=judged,
        searched_mechanisms=judged[:1],
        resolved_profile=SimpleNamespace(context=SimpleNamespace(known_window=None)),
        resolution=None,
    )
    known = pd.DataFrame(
        {
            "target_compound_formula": ["C6H12O6"],
            "ionization_mechanism_id": ["im-br"],
            "target_compound_id": [None],
            "reference_identities": [[{"name": "glucose"}]],
        }
    )
    with patch(f"{_SI}._fetch_known_isotopes", AsyncMock(return_value=known)) as fetch:
        formulas, ions = await pipeline._read_lists(ctx)

    assert fetch.await_args.args[3] is judged
    assert fetch.await_args.kwargs["lines"] is False
    assert formulas == frozenset({"C6H12O6"})
    assert list(ions) == [("C6H12O6", "im-br")]


def test_the_batch_search_s_explained_peaks_are_its_frame():
    # What a run's Stage A and pre-passes are to its search: a peak the batch
    # ledger explains is no target and no twin, and the search writes no row
    # on it. Pinned through the twin re-read below; this is its query's shape.
    assert batch_untargeted.CLAIMED_ROLE_CODES == frozenset(
        {role_code(ROLE_REAGENT), role_code(ROLE_ARTIFACT)}
    )


@pytest.mark.asyncio
async def test_the_search_s_partner_gate_turns_tropylium_as_a_run_s_does():
    # The judging a run's rows get, the partner gate first: the tropylium ion
    # reads as toluene less a hydride here as in a run of the sample, not as
    # the protonated C7H6 the election prefers; and the lists it names reach
    # the second-channel cap after the gate, as they reach a run's.
    peaks = _peaks(
        [
            ("p1", 91.0542, 1.0e6),
            ("p2", 92.0621, 3.0e6),
            ("p3", 202.0777, 5.0e6),
        ]
    )
    mode = [_mechanism("[M]+.", "im-ct")]
    secondary = [_mechanism("[M+H]+", "im-h"), _mechanism("[M-H]+", "im-hydride")]

    def finder(*args, **kwargs):
        return (
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
            None,
        )

    stack, mocks = _searching(
        _sample(polarity="+"), peaks, mode, finder=finder, secondary=secondary
    )
    cap = stack.enter_context(
        patch(
            "mascope_backend.api.new.peak_assignments.judging_loop.cap_second_channel",
            wraps=batch_untargeted_second_channel(),
        )
    )
    with stack:
        rows, _, _ = await batch_untargeted._search_sample(
            "si-1", {"p1", "p2"}, PeakAssignmentConfig()
        )

    tropylium = next(row for row in rows if row["sample_peak_id"] == "p1")
    assert tropylium["assigned_formula"] == "C7H8"
    assert tropylium["ionization_mechanism_id"] == "im-hydride"
    assert tropylium["provenance"]["partner_gate"]["through"] == "[M-H]+"
    assert "listed_formulas" in cap.call_args.kwargs


def batch_untargeted_second_channel():
    from mascope_backend.api.new.peak_assignments import judging_loop

    return judging_loop.cap_second_channel


@pytest.mark.asyncio
@pytest.mark.parametrize("instrument", ["orbi", "tof"])
async def test_the_search_holds_a_reading_only_an_organonitrates_skeleton_admits(
    instrument,
):
    # Step 3.6's rider, asked by the tiering pass as a run asks it: a nitrate
    # search on an Orbitrap reads the skeleton, so it commits the dinitrate its
    # windows refused as a formula, and holds it at candidate for it. A TOF
    # reads no skeleton.
    filename = "orbi-sample.raw" if instrument == "orbi" else "tof-sample.h5"

    def finder(*args, **kwargs):
        return (
            pd.DataFrame([_match(286.0063, "C5H8N2O8", "C5H8N3O11-", "[M+NO3]-")]),
            None,
        )

    stack, _ = _searching(
        _sample(filename=filename),
        _peaks([("p1", 286.0063, 1.0e6)]),
        [_mechanism("[M+NO3]-", "im-no3")],
        finder=finder,
    )
    with stack:
        rows, _, _ = await batch_untargeted._search_sample(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )

    (row,) = rows
    rules = {reason["rule"] for reason in row["provenance"]["tier_reasons"]}
    assert ("nox_skeleton" in rules) is (instrument == "orbi")
    if instrument == "orbi":
        assert row["tier"] == TIER_CANDIDATE


@pytest.mark.asyncio
async def test_a_searched_member_reads_the_tier_a_run_of_its_sample_gives():
    # MAS-858: a reading the batch search makes is judged by the run's rules,
    # not banded by its evidence alone. A reading that stands on its one peak,
    # no line of its envelope in the ledger beside it, is held at candidate by
    # the lone-peak rule, as a run of the sample holds it, though its evidence
    # bands it assigned.
    def finder(*args, **kwargs):
        return (
            pd.DataFrame([_match(160.8413, "Br2", "Br2H-", "[M-H]-")]),
            None,
        )

    stack, _ = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
    )
    with stack:
        rows, _, _ = await batch_untargeted._search_sample(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )

    (row,) = rows
    capping = [
        reason["rule"] for reason in row["provenance"]["tier_reasons"] if reason["caps"]
    ]
    assert row["tier"] == TIER_CANDIDATE
    assert capping == ["lone_peak"]
    assert row["provenance"]["evidence"] >= PeakAssignmentConfig().assigned_threshold


# --- a batch decision measured on a member (pipeline.PROPAGATION) ----------------


def _scorer(pairings, peaks, scored_with):
    """The seeded scorer for canned pairings: ``(formula, mechanism id)`` ->
    ``(ion id, ion, peaks)``, each ion a line on each of its peaks at one fit;
    each call's keyword arguments appended to ``scored_with``."""

    async def score(scored, seeds, params, **kwargs):
        scored_with.append(kwargs)
        ion_by_seed = {seed: pairings[seed][0] for seed in seeds if seed in pairings}
        fit_by_ion = {ion: 0.95 for ion in ion_by_seed.values()}
        errors = {
            (pairings[seed][0], peak): {"mz_error_ppm": 0.1}
            for seed in ion_by_seed
            for peak in pairings[seed][2]
        }
        return (
            ion_by_seed,
            fit_by_ion,
            errors,
            seeded_frame(
                ion_by_seed,
                fit_by_ion,
                errors,
                peaks,
                lambda formula, mechanism_id: pairings[(formula, mechanism_id)][1],
            ),
        )

    return score


def _grid(rivals, calls, fit_tied=()):
    """The grid's rivals of every reading it is asked about
    (``finder.rivals_of_readings``): ``rivals`` as ``(formula, ion, notation)``,
    each at the reading's fit; each call's readings and keyword arguments
    appended to ``calls``."""
    from mascope_tools.composition.finder import ReadingRivals

    def read(peaks, config, readings, heuristics=None, scoring=None, **kwargs):
        calls.append((list(readings), kwargs))
        return [
            ReadingRivals(
                density=1 + len(rivals),
                rivals=tuple(
                    {
                        "formula": formula,
                        "ion": ion,
                        "ionization_mechanism": notation,
                        "fit_score": 0.95,
                        "mz_error_ppm": 0.1,
                    }
                    for formula, ion, notation in rivals
                ),
                fit_score=0.95,
                candidates=len(rivals),
                in_grid=True,
                fit_tied=tuple(
                    {
                        "formula": formula,
                        "ion": ion,
                        "ionization_mechanism": notation,
                        "fit_score": 0.95,
                        "mz_error_ppm": 0.1,
                    }
                    for formula, ion, notation in fit_tied
                ),
            )
            for _ in readings
        ]

    return read


async def _measure(
    sample,
    peaks,
    mechanisms,
    readings,
    *,
    secondary=(),
    known=None,
    path=None,
    resolution=None,
    grid=None,
    fit_tied=(),
    kept=None,
    width=None,
):
    """Measure batch decisions on one sample as the propagation does
    (``batch_untargeted.measure_on_sample``), and keep nothing.

    :param readings: ``(anchor, formula, ion, mechanism id, peaks)`` per
        decision: its ion a line on each of the peaks, at one fit.
    :param path: The path; the propagation's unless a test says.
    :param resolution: The file's resolving power; none unless a test says.
    :param grid: The grid's rivals of every reading, ``(formula, ion,
        notation, mechanism id, peaks)`` each, measured as the readings are;
        None asks the real grid.
    :param fit_tied: The grid's formulas only plausibility separated from
        every reading (``finder.ReadingRivals.fit_tied``), shaped as ``grid``'s.
    :param kept: A batch pass's store (``kept_measurement.KeptMeasurement``)
        the measurement reads and fills, as the batch's judging passes one.
    :param width: The width the sample's latest run recorded; none unless a
        test says.
    :return: The judged rows by peak, and the stand-ins, the seeded scorer's
        keyword arguments per call among them (``scored``) and the grid's
        calls (``grid``).
    """
    from mascope_backend.api.new.peak_assignments import pipeline

    pairings = {
        (formula, mechanism_id): (f"ion-{anchor}", ion, paired)
        for anchor, formula, ion, mechanism_id, paired in readings
    }
    for index, (formula, ion, _, mechanism_id, paired) in enumerate(
        [*(grid or ()), *fit_tied]
    ):
        pairings[(formula, mechanism_id)] = (f"ion-rival-{index}", ion, paired)
    scored_with = []
    score = _scorer(pairings, peaks, scored_with)

    stack, mocks = _searching(
        sample,
        peaks,
        mechanisms,
        finder=lambda *args, **kwargs: (pd.DataFrame(), None),
        secondary=secondary,
        known=known,
        resolution=resolution,
        width=width,
    )
    mocks["scored"] = scored_with
    mocks["grid"] = []
    if kept is not None:
        from functools import partial

        stack.enter_context(
            patch(f"{_BU}.RunContext", partial(pipeline.RunContext, kept=kept))
        )
    if grid is not None:
        stack.enter_context(
            patch(
                "mascope_backend.api.new.peak_assignments.given_rivals.rivals_of_readings",
                _grid(
                    [(f, ion, notation) for f, ion, notation, _, _ in grid],
                    mocks["grid"],
                    [(f, ion, notation) for f, ion, notation, _, _ in fit_tied],
                ),
            )
        )
    annotations = {
        anchor: Annotation(formula, ion, mechanism_id, "M0", None)
        for anchor, formula, ion, mechanism_id, _ in readings
    }
    with stack, patch(f"{_PIPE}.score_seeds", score):
        ctx = await batch_untargeted.measure_on_sample(
            "si-1",
            annotations,
            PeakAssignmentConfig(),
            path or pipeline.PROPAGATION,
        )
    return {row["sample_peak_id"]: row for row in ctx.judged.rows}, mocks


@pytest.mark.asyncio
async def test_a_measured_reading_is_read_at_the_files_resolution():
    # The stage list reads the file's R(m/z) once: a decision's lines are
    # measured at that reading, and the lists a run of the member's sample
    # reads are resolved at it, as a run's Stage A and its re-score read
    # theirs (MAS-977).
    def orbitrap(mz):
        return 100_000.0

    _, mocks = await _measure(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        [("bp-1", "Br2", "Br2H-", "im-h", ["p1"])],
        resolution=orbitrap,
    )

    mocks["resolution"].assert_awaited_once()
    assert [kwargs["resolution"] for kwargs in mocks["scored"]] == [orbitrap]
    mocks["known"].assert_awaited_once()
    assert mocks["known"].await_args.kwargs["resolution"] is orbitrap


def _reasons(row):
    return [
        (reason["rule"], reason["caps"])
        for reason in row["provenance"].get("tier_reasons", [])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "formula, ion, capping",
    [
        ("Br2", "Br2H-", ["lone_peak"]),
        ("C7H7O", "C7H6O-", ["odd_electron", "lone_peak"]),
    ],
    ids=["lone", "radical"],
)
async def test_a_reading_measured_on_a_member_is_judged_as_its_search_judges_it(
    formula, ion, capping
):
    # MAS-858: the batch search reads a reading on the sample it searched, and
    # the propagation measures it on another's member. A run of either sample
    # finds it by searching, so both are the formula search's rows, asked the
    # rules a run asks only of those: a reading alone on its one peak is held
    # at candidate in both (the lone peak, and the radical for an
    # odd-electron neutral), though its evidence bands it assigned.
    peaks = _peaks([("p1", 160.8413, 1.0e6)])
    mechanisms = [_mechanism("[M-H]-", "im-h")]

    def finder(*args, **kwargs):
        return pd.DataFrame([_match(160.8413, formula, ion, "[M-H]-")]), None

    stack, _ = _searching(_sample(), peaks, mechanisms, finder=finder)
    with stack:
        (searched,), _, _ = await batch_untargeted._search_sample(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )
    measured, _ = await _measure(
        _sample(), peaks, mechanisms, [("bp-1", formula, ion, "im-h", ["p1"])]
    )

    member = measured["p1"]
    for row in (searched, member):
        assert row["source"] == "untargeted"
        assert row["tier"] == TIER_CANDIDATE
        assert [rule for rule, caps in _reasons(row) if caps] == capping
    assert _reasons(member) == _reasons(searched)
    assert member["provenance"]["evidence"] >= PeakAssignmentConfig().assigned_threshold


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "compound, identities, detail",
    [
        (
            "tc-br2",
            None,
            "matched from the target library, which somebody named for this data",
        ),
        (None, [{"name": "bromine"}], "matched from a reference list, as bromine"),
    ],
    ids=["target library", "reference list"],
)
async def test_a_member_reading_an_ion_its_lists_hold_is_that_lists_row(
    compound, identities, detail
):
    # A run of the sample commits an ion its lists hold through Stage A, as
    # that list's row, which the rules for the search's rows do not ask: the
    # lone reading the test above holds stands at the tier its evidence bands,
    # on the list.
    known = pd.DataFrame(
        [
            {
                "target_compound_formula": "Br2",
                "ionization_mechanism_id": "im-h",
                "target_compound_id": compound,
                "reference_identities": identities,
            }
        ]
    )
    measured, _ = await _measure(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        [("bp-1", "Br2", "Br2H-", "im-h", ["p1"])],
        known=known,
    )

    row = measured["p1"]
    assert row["source"] == "database"
    assert row["target_compound_id"] == compound
    assert row["tier"] == TIER_ASSIGNED
    assert not [rule for rule, caps in _reasons(row) if caps]
    (listed,) = [
        reason
        for reason in row["provenance"]["tier_reasons"]
        if reason["rule"] == "on_a_list"
    ]
    assert listed["detail"] == detail


def _claiming(claim, peak_ids):
    from mascope_backend.api.new.peak_assignments.prepasses import SourceIonHits

    """The claim stage taking ``peak_ids``, through the reagent pre-pass or the
    artifact one, with no row of its own; None claims nothing."""
    if claim == "reagent":
        return patch(
            "mascope_backend.api.new.peak_assignments.prepasses._reagent_assignments",
            return_value=([], set(peak_ids), None, SourceIonHits()),
        )
    if claim == "artifact":
        return patch(
            "mascope_backend.api.new.peak_assignments.prepasses._artifact_assignments",
            return_value=([], set(peak_ids)),
        )
    return nullcontext()


#: A Br2H- reading and the peaks of its envelope: its monoisotopic line on p1,
#: the 81Br line on p2 and the 81Br2 one on p3.
_BR2_PEAKS = [
    ("p1", 158.8450, 0.514e6),
    ("p2", 160.8430, 1.0e6),
    ("p3", 162.8410, 0.486e6),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["reagent", "artifact", None])
async def test_a_measured_reading_takes_no_line_on_a_peak_the_claim_stage_took(claim):
    # A run elects no reading on a peak its claim stage took: Stage A drops a
    # line that landed on one, and the search drops its row there. So a
    # reading measured on a member whose 81Br line falls on a claimed peak
    # stands on its monoisotopic line alone, and the lone-peak rule holds it
    # at candidate, as it holds the search's row in the sample it searched.
    # The claimed peak is still in the spectrum, so no line reads absent. With
    # nothing claimed the line is the second observation in both.
    peaks = _peaks(_BR2_PEAKS)
    mechanisms = [_mechanism("[M-H]-", "im-h")]
    line = {**_match(160.8430, "Br2", "Br2H-", "[M-H]-"), "isotope_label": "81Br"}

    def finder(*args, **kwargs):
        return pd.DataFrame([_match(158.8450, "Br2", "Br2H-", "[M-H]-"), line]), None

    stack, _ = _searching(_sample(), peaks, mechanisms, finder=finder)
    with stack, _claiming(claim, {"p2"}):
        rows, _, _ = await batch_untargeted._search_sample(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )
    with _claiming(claim, {"p2"}):
        measured, _ = await _measure(
            _sample(),
            peaks,
            mechanisms,
            [("bp-1", "Br2", "Br2H-", "im-h", ["p1", "p2"])],
        )

    searched = {row["sample_peak_id"]: row for row in rows}
    capping = {}
    for path, ledger in (("search", searched), ("measured", measured)):
        capping[path] = [rule for rule, caps in _reasons(ledger["p1"]) if caps]
        if claim is None:
            assert ledger["p2"]["role"] == "iso_child"
            assert ledger["p1"]["tier"] == TIER_ASSIGNED
        else:
            assert set(ledger) == {"p1"}
            assert ledger["p1"]["tier"] == TIER_CANDIDATE
            assert capping[path] == ["lone_peak"]
    assert capping["measured"] == capping["search"]


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["reagent", "artifact"])
async def test_a_measured_reading_whose_ion_the_claim_stage_took_is_elected_nowhere(
    claim,
):
    # Where the claim stage took a reading's monoisotopic peak, the peak is the
    # source's and so is the envelope a run reads off it: Stage A drops the
    # whole ion, and the search is never offered the peak. A batch decision
    # measured there is elected on no peak, its 81Br line's included, and the
    # member keeps what it reads.
    with _claiming(claim, {"p1"}):
        measured, _ = await _measure(
            _sample(),
            _peaks(_BR2_PEAKS),
            [_mechanism("[M-H]-", "im-h")],
            [("bp-1", "Br2", "Br2H-", "im-h", ["p1", "p2"])],
        )

    assert measured == {}


@pytest.mark.asyncio
async def test_a_pin_takes_its_members_peak_whatever_the_claim_stage_took():
    # A person's formula takes the peak it was chosen for: a pin is measured
    # and elected on its member's peak, and on its lines, though a pre-pass
    # claimed them, as a pin always was.
    from mascope_backend.api.new.peak_assignments import pipeline

    with _claiming("reagent", {"p1", "p2"}):
        measured, _ = await _measure(
            _sample(),
            _peaks(_BR2_PEAKS),
            [_mechanism("[M-H]-", "im-h")],
            [("bp-1", "Br2", "Br2H-", "im-h", ["p1", "p2"])],
            path=pipeline.PERSONS_CHOICE,
        )

    assert measured["p1"]["assigned_formula"] == "Br2"
    assert measured["p2"]["role"] == "iso_child"


#: A bromophenolate reading, C6H4BrO- through [M-H]-, and the peaks of its
#: lines the spectrum shows: its monoisotopic line on p1, the 81Br line on p2,
#: and their 13C lines on p3 and p4.
_BROMOPHENOL = ("C6H5BrO", "im-h")
_BROMOPHENOL_LINES = {
    "C6H4BrO-": "p1",
    "[81Br]C6H4O-": "p2",
    "[13C]C5H4BrO-": "p3",
    "[13C][81Br]C5H4O-": "p4",
}


async def _measure_by_the_seeded_scorer(*, listed, claim, brighter=1.0, missing=()):
    """Measure the bromophenolate reading on a member as the propagation does,
    through the real seeded scorer: its ion's lines, gating and fit are the
    engine's, and only the match pass is stood in for. Each line of
    ``_BROMOPHENOL_LINES`` is found on its peak at its predicted height, the
    81Br line's ``brighter`` times it, and the rest are absent.

    :param listed: Whether the sample's lists hold the reading's ion.
    :param claim: The claim stage taking p2, as in :func:`_claiming`.
    :param missing: Lines the match pass returns no row for at all.
    :return: The judged rows by peak, and the frames the gating was handed.
    """
    from mascope_backend.api.new.peak_assignments import pipeline, seeded_scoring

    gated = []
    gate_and_score = seeded_scoring.gate_and_score

    def gating(frame, *args, **kwargs):
        gated.append(frame)
        return gate_and_score(frame, *args, **kwargs)

    async def match(filename, target_isotopes_df, polarity):
        frame = target_isotopes_df.reset_index(drop=True)
        frame = frame[~frame["target_isotope_formula"].isin(missing)].copy()
        peak = frame["target_isotope_formula"].map(_BROMOPHENOL_LINES)
        found = peak.notna()
        height = frame["relative_abundance"] * 1.0e6
        height = height.where(
            frame["target_isotope_formula"] != "[81Br]C6H4O-", height * brighter
        )
        frame["sample_peak_id"] = peak.where(found, None)
        frame["sample_peak_mz"] = frame["mz"].where(found, np.nan)
        frame["sample_peak_intensity"] = height.where(found, 0.0)
        frame["sample_peak_tof"] = None
        frame["match_mz_error"] = np.where(found, 0.1, np.nan)
        frame["match_abundance_error"] = np.where(
            found, (height / (frame["relative_abundance"] * 1.0e6) - 1.0) * 100, np.nan
        )
        frame["match_score"] = np.where(found, 1.0, 0.0)
        return frame

    mechanism = _mechanism("[M-H]-", "im-h")

    async def mechanisms(mechanism_ids):
        return {"im-h": mechanism}

    known = (
        pd.DataFrame(
            [
                {
                    "target_compound_formula": _BROMOPHENOL[0],
                    "ionization_mechanism_id": _BROMOPHENOL[1],
                    "target_compound_id": "tc-bromophenol",
                    "reference_identities": None,
                }
            ]
        )
        if listed
        else None
    )
    peaks = _peaks(
        [
            ("p1", 170.9451, 0.47e6),
            ("p3", 171.9485, 0.03e6),
            ("p2", 172.9431, 0.46e6 * brighter),
            ("p4", 173.9464, 0.03e6),
        ]
    )
    stack, _ = _searching(
        _sample(),
        peaks,
        [mechanism],
        finder=lambda *args, **kwargs: (pd.DataFrame(), None),
        known=known,
    )
    stack.enter_context(patch.object(seeded_scoring, "compute_match_isotopes", match))
    stack.enter_context(
        patch.object(seeded_scoring, "fetch_mechanisms_by_id", mechanisms)
    )
    stack.enter_context(
        patch.object(
            seeded_scoring, "sample_label_purity", AsyncMock(return_value=None)
        )
    )
    stack.enter_context(patch.object(seeded_scoring, "gate_and_score", gating))
    # The gating reads the match parameters as a sample's are.
    stack.enter_context(
        patch(
            f"{_PIPE}.default_match_params",
            AsyncMock(return_value=OrbiMatchParams()),
        )
    )
    with stack, _claiming(claim, {"p2"}):
        ctx = await batch_untargeted.measure_on_sample(
            "si-1",
            {"bp-1": Annotation(_BROMOPHENOL[0], "C6H4BrO-", "im-h", "M0", None)},
            PeakAssignmentConfig(),
            pipeline.PROPAGATION,
        )
    return {row["sample_peak_id"]: row for row in ctx.judged.rows}, gated


def _evidence(row):
    return row["provenance"]["evidence"]


@pytest.mark.asyncio
@pytest.mark.parametrize("brighter", [1.0, 20.0], ids=["accepted", "refused"])
@pytest.mark.parametrize("claim", ["reagent", "artifact"])
async def test_a_listed_reading_is_fitted_without_its_line_on_a_claimed_peak(
    claim, brighter
):
    # MAS-1047: a reading the member's lists hold is the list's row a run of
    # the sample makes, and Stage A measures that ion without its lines on the
    # peaks the claim stage took, before it gates and fits. So does the
    # measurement here: the 81Br line on the claimed p2 is in neither fit,
    # whether the gating would have accepted it (as present) or refused it as
    # far brighter than it predicts (as missing). It reads as the reading does
    # where the match pass found no such line at all.
    measured, gated = await _measure_by_the_seeded_scorer(
        listed=True, claim=claim, brighter=brighter
    )
    unclaimed, _ = await _measure_by_the_seeded_scorer(
        listed=True, claim=None, missing=("[81Br]C6H4O-",)
    )

    (frame,) = gated
    assert "p2" not in set(frame["sample_peak_id"])
    assert set(measured) == set(unclaimed) == {"p1", "p3", "p4"}
    for peak in measured:
        for read in (lambda row: row["fit_score"], _evidence):
            assert read(measured[peak]) == pytest.approx(read(unclaimed[peak]))
    assert measured["p1"]["source"] == "database"


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["reagent", "artifact"])
async def test_a_searched_reading_keeps_its_claimed_line_as_its_pattern(claim):
    # A reading the lists do not hold is the formula search's row, whose
    # re-score reads a claimed peak as part of the pattern: its fit still
    # counts the 81Br line, here refused as far brighter than it predicts, and
    # the elect stage drops its row there afterwards.
    measured, gated = await _measure_by_the_seeded_scorer(
        listed=False, claim=claim, brighter=20.0
    )
    listed, _ = await _measure_by_the_seeded_scorer(
        listed=True, claim=claim, brighter=20.0
    )

    (frame,) = gated
    assert "p2" in set(frame["sample_peak_id"])
    assert "p2" not in measured
    assert measured["p1"]["source"] == "untargeted"
    assert measured["p1"]["fit_score"] < listed["p1"]["fit_score"]


async def _measure_ammonium_reading(*, known, opened=True, partner=False):
    """The ammonium reading of ``_search_ammonium_reading``, measured on a
    uronium sample whose spectrum opens the channel or not (``opened``), over
    a known set of ``known``; with ``partner``, beside the same neutral
    measured through the mode's own protonation.

    :return: The judged row on its peak, and the fake known-set read.
    """
    # 138.0986 is the carrier that opens the ammonium channel; 171.1016 is
    # C9H14O3 + H+.
    specs = [("p0", 100.0, 1.0e6), ("p2", 188.1281, 1.0e5)]
    readings = [("bp-1", "C9H14O3", "C9H18NO3+", "im-nh4", ["p2"])]
    if opened:
        specs.append(("p1", 138.0986, 1.0e4))
    if partner:
        specs.append(("p3", 171.1016, 1.0e5))
        readings.append(("bp-2", "C9H14O3", "C9H15O3+", "im-h", ["p3"]))
    measured, mocks = await _measure(
        _sample(polarity="+"),
        _peaks(specs),
        [_mechanism("[M+H]+", "im-h"), _mechanism("[M+CH4N2O+H]+", "im-urea")],
        readings,
        secondary=[_mechanism("[M+NH4]+", "im-nh4")],
        known=known,
    )
    return measured["p2"], mocks["known"]


@pytest.mark.asyncio
async def test_a_measured_reading_through_a_channel_its_sample_did_not_open_is_held():
    # MAS-1039: a sample whose spectrum opens the ammonium channel holds a
    # reading through it at candidate, since nothing corroborates it.
    # Propagated to a member whose sample shows no carrier, a run of which
    # never opens the channel, the reading is judged as an opened channel's
    # all the same: held at candidate there too, not assigned on its evidence
    # alone. The lists are read through the channel as a run that opened it
    # reads them.
    judged = {}
    for opened in (True, False):
        row, known_isotopes = await _measure_ammonium_reading(
            known=pd.DataFrame(), opened=opened
        )
        assert row["ionization_mechanism_id"] == "im-nh4"
        assert row["source"] == "untargeted"
        assert row["tier"] == TIER_CANDIDATE
        assert row["provenance"]["minor_channel"] == {
            "corroborated_by": None,
            "capped": True,
            "isotopologue": False,
        }
        assert (
            row["provenance"]["evidence"] >= PeakAssignmentConfig().assigned_threshold
        )
        (call,) = known_isotopes.await_args_list
        assert [m.ionization_mechanism_id for m in call.args[3]] == [
            "im-h",
            "im-urea",
            "im-nh4",
        ]
        judged[opened] = _reasons(row)
    assert judged[False] == judged[True]


@pytest.mark.asyncio
@pytest.mark.parametrize("lift", ["list", "second_channel"])
async def test_corroboration_lifts_a_reading_through_a_channel_its_sample_did_not_open(
    lift,
):
    # What lifts a run's own reading through an opened channel lifts the
    # propagated one alike, on a member whose sample did not open it: a list
    # naming its neutral, or the sample showing the neutral through one of the
    # mode's own channels. The member then reads what the sample that opened
    # the channel reads.
    known = (
        pd.DataFrame(
            {
                "target_compound_formula": ["O3C9H14"],
                "ionization_mechanism_id": ["im-h"],
                "target_compound_id": ["tc-1"],
            }
        )
        if lift == "list"
        else pd.DataFrame()
    )
    judged = {}
    for opened in (True, False):
        row, _ = await _measure_ammonium_reading(
            known=known, opened=opened, partner=lift == "second_channel"
        )
        assert row["ionization_mechanism_id"] == "im-nh4"
        assert row["provenance"]["minor_channel"]["corroborated_by"] == lift
        assert row["provenance"]["minor_channel"]["capped"] is False
        judged[opened] = (row["tier"], _reasons(row))
    assert judged[False] == judged[True]
    if lift == "second_channel":
        # Its partner is a second observation, so nothing else holds it.
        assert judged[False][0] == TIER_ASSIGNED


@pytest.mark.asyncio
@pytest.mark.parametrize("opened", [True, False], ids=["opened", "not opened"])
@pytest.mark.parametrize("partner", [True, False], ids=["partner", "no partner"])
async def test_the_partner_gate_reads_a_channel_its_sample_did_not_open(
    opened, partner
):
    # MAS-1039: formate is a partner-gated channel of the bromide source. A
    # reading through it measured on a member whose spectrum shows no formate,
    # so that a run of its sample never opens the channel, is held to the gate
    # as on a sample that opened it: it stands on a partner, the neutral
    # through the mode's own bromide adduct, and is kept and held at candidate
    # without one.
    # 44.9982 is the formate anion, the channel's carrier; 229.1081 is
    # C10H16O3 + HCOO- and 263.0288 C10H16O3 + 79Br-.
    specs = [("p0", 300.0, 1.0e6), ("p2", 229.1081, 1.0e5)]
    readings = [("bp-1", "C10H16O3", "C11H17O5-", "im-formate", ["p2"])]
    if opened:
        specs.append(("p1", 44.9982, 1.0e5))
    if partner:
        specs.append(("p3", 263.0288, 1.0e5))
        readings.append(("bp-2", "C10H16O3", "C10H16O3Br-", "im-br", ["p3"]))
    measured, _ = await _measure(
        _sample(),
        _peaks(specs),
        [_mechanism("[M+Br]-", "im-br")],
        readings,
        secondary=[_mechanism("[M+HCOO]-", "im-formate")],
    )

    row = measured["p2"]
    assert row["ionization_mechanism_id"] == "im-formate"
    gate = {"channel": "[M+HCOO]-", "partner": partner}
    if not partner:
        gate["kept"] = PARTNER_GATE_KEPT
    assert row["provenance"]["partner_gate"] == gate
    assert row["provenance"]["minor_channel"]["capped"] is not partner
    if not partner:
        assert row["tier"] == TIER_CANDIDATE


@pytest.mark.asyncio
async def test_a_listed_ion_through_a_channel_its_sample_did_not_open_reads_its_family():
    # MAS-1039: where a list holds the ion itself through the ammonium
    # channel, the reading is that list's row, as on the sample that opened
    # the channel, and its same-ion family is read through its own channel
    # beside the mode's: C9H17NO3 + H+ is the same ion, a rival through
    # protonation.
    known = pd.DataFrame(
        {
            "target_compound_formula": ["O3C9H14"],
            "ionization_mechanism_id": ["im-nh4"],
            "target_compound_id": ["tc-1"],
        }
    )
    judged = {}
    for opened in (True, False):
        row, _ = await _measure_ammonium_reading(known=known, opened=opened)
        assert row["ionization_mechanism_id"] == "im-nh4"
        assert row["target_compound_id"] == "tc-1"
        family = sorted(
            (alternative["assigned_formula"], alternative["ionization_mechanism_id"])
            for alternative in row["alternatives"] or []
            if alternative.get("same_ion")
        )
        judged[opened] = (row["tier"], _reasons(row), family)
    assert judged[False] == judged[True]
    assert judged[False][2]


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [True, False], ids=["listed", "unlisted"])
async def test_a_channel_its_sample_did_not_open_judges_only_its_own_readings(listed):
    # MAS-1039: a reading through the ammonium channel the member's sample did
    # not open is judged as an opened channel's, and nothing else is. Y, an
    # N-bearing neutral through the mode's own protonation with its 13C line,
    # reads the same tier, reasons and alternatives with or without that
    # reading in the pass: it gains no ammonium rival (C8H12O2 + NH4+ is the
    # same ion), which a run of its sample, showing no ammonium carrier, would
    # never give it either.
    # 158.1176 is C8H15NO2 + H+, 159.1209 its 13C line; 188.1281 is
    # C9H14O3 + NH4+.
    known = (
        pd.DataFrame(
            {
                "target_compound_formula": ["O2NC8H15"],
                "ionization_mechanism_id": ["im-h"],
                "target_compound_id": ["tc-1"],
            }
        )
        if listed
        else pd.DataFrame()
    )
    y = ("bp-y", "C8H15NO2", "C8H16NO2+", "im-h", ["p4", "p5"])
    x = ("bp-1", "C9H14O3", "C9H18NO3+", "im-nh4", ["p2"])
    judged = {}
    for readings in ([y], [y, x]):
        measured, _ = await _measure(
            _sample(polarity="+"),
            _peaks(
                [
                    ("p0", 100.0, 1.0e6),
                    ("p2", 188.1281, 1.0e5),
                    ("p4", 158.1176, 1.0e5),
                    ("p5", 159.1209, 9.0e3),
                ]
            ),
            [_mechanism("[M+H]+", "im-h"), _mechanism("[M+CH4N2O+H]+", "im-urea")],
            readings,
            secondary=[_mechanism("[M+NH4]+", "im-nh4")],
            known=known,
        )
        row = measured["p4"]
        assert row["ionization_mechanism_id"] == "im-h"
        judged[len(readings)] = (
            row["tier"],
            _reasons(row),
            sorted(
                str(alternative.get("ionization_mechanism_id"))
                for alternative in row["alternatives"] or []
            ),
            "same_ion_settled" in row["provenance"].get("cross_channel", {}),
        )
        if len(readings) == 2:
            assert measured["p2"]["tier"] == TIER_CANDIDATE
    assert judged[2] == judged[1]
    assert "im-nh4" not in judged[2][2]
    assert judged[2][0] == TIER_ASSIGNED


@pytest.mark.asyncio
async def test_a_channel_its_sample_did_not_open_corroborates_none_of_its_own_readings(
    monkeypatch,
):
    # MAS-1039: the member's own reading of a molecule, through the mode's
    # protonation, reads as a run of its sample reads it, with or without the
    # same molecule's reading through the ammonium channel its spectrum did
    # not open: a run of the member never reads that one, so it is no second
    # channel of the member's reading. The ammonium reading itself still
    # counts the member's own reading as its partner and is lifted by it. The
    # tiering pass reads the channel so too.
    from mascope_backend.api.new.peak_assignments import judging_loop

    tiered_with = []
    apply_tiering = judging_loop.apply_tiering

    def tiering(*args, **kwargs):
        tiered_with.append(kwargs.get("given_channels"))
        return apply_tiering(*args, **kwargs)

    monkeypatch.setattr(judging_loop, "apply_tiering", tiering)
    judged = {}
    for readings in (
        [("bp-2", "C9H14O3", "C9H15O3+", "im-h", ["p3"])],
        [
            ("bp-2", "C9H14O3", "C9H15O3+", "im-h", ["p3"]),
            ("bp-1", "C9H14O3", "C9H18NO3+", "im-nh4", ["p2"]),
        ],
    ):
        measured, _ = await _measure(
            _sample(polarity="+"),
            _peaks(
                [("p0", 100.0, 1.0e6), ("p2", 188.1281, 1.0e5), ("p3", 171.1016, 1.0e5)]
            ),
            [_mechanism("[M+H]+", "im-h"), _mechanism("[M+CH4N2O+H]+", "im-urea")],
            readings,
            secondary=[_mechanism("[M+NH4]+", "im-nh4")],
        )
        row = measured["p3"]
        assert row["ionization_mechanism_id"] == "im-h"
        cross_channel = row["provenance"]["cross_channel"]
        judged[len(readings)] = (row["tier"], _reasons(row), cross_channel)
        if len(readings) == 2:
            given = measured["p2"]
            assert given["ionization_mechanism_id"] == "im-nh4"
            assert given["provenance"]["minor_channel"]["corroborated_by"] == (
                "second_channel"
            )
            assert given["provenance"]["cross_channel"]["corroborated"] is True
            assert given["provenance"]["cross_channel"]["channels"] == [
                "[M+H]+",
                "[M+NH4]+",
            ]
            assert given["tier"] == TIER_ASSIGNED
    assert judged[2] == judged[1]
    assert set(tiered_with) == {frozenset(), frozenset({"[M+NH4]+"})}
    assert judged[1][0] == TIER_CANDIDATE
    assert judged[1][2]["corroborated"] is False
    assert judged[1][2]["channels"] == ["[M+H]+"]


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [True, False], ids=["listed", "unlisted"])
async def test_a_list_naming_the_neutral_corroborates_a_measured_reading(listed):
    # Step 3.4 as a run of the sample reads it, and as the batch search reads
    # it (``test_a_list_naming_the_neutral_corroborates_a_batch_searchs_reading``):
    # a reading through a secondary channel is the search's row, held at
    # candidate unless a list the sample is read against names its neutral.
    # The library holds the neutral through the mode's own channel only, so a
    # run would have to search for its ammonium ion, as for any other.
    known = pd.DataFrame(
        {
            "target_compound_formula": ["O3C9H14" if listed else "C9H16O3"],
            "ionization_mechanism_id": ["im-h"],
            "target_compound_id": ["tc-1"],
        }
    )
    row, known_isotopes = await _measure_ammonium_reading(known=known)

    assert row["source"] == "untargeted"
    assert row["provenance"]["minor_channel"]["corroborated_by"] == (
        "list" if listed else None
    )
    assert row["provenance"]["minor_channel"]["capped"] is not listed
    # Read as a run reads it: the target library through the mode's own
    # mechanisms, the reference lists through every searched one, under the
    # context's ceiling, at the file's resolving power (none read here).
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
        ).context.known_window,
        "resolution": None,
        # Read for what they list, not matched (MAS-1274).
        "lines": False,
    }


@pytest.mark.asyncio
async def test_the_measured_readings_partner_gate_turns_tropylium_as_a_searchs():
    # The search's row carries the other readings of its ion the search would
    # have held, so the partner gate weighs them as it weighs the batch
    # search's (``test_the_search_s_partner_gate_turns_tropylium_as_a_run_s_does``):
    # the tropylium ion measured as protonated C7H6 reads as toluene less a
    # hydride, which the sample shows through electron transfer.
    measured, _ = await _measure(
        _sample(polarity="+"),
        _peaks(
            [
                ("p1", 91.0542, 1.0e6),
                ("p2", 92.0621, 3.0e6),
                ("p3", 202.0777, 5.0e6),
            ]
        ),
        [_mechanism("[M]+.", "im-ct")],
        [
            ("bp-1", "C7H6", "C7H7+", "im-h", ["p1"]),
            ("bp-2", "C7H8", "C7H8+", "im-ct", ["p2"]),
        ],
        secondary=[_mechanism("[M+H]+", "im-h"), _mechanism("[M-H]+", "im-hydride")],
    )

    tropylium = measured["p1"]
    assert tropylium["assigned_formula"] == "C7H8"
    assert tropylium["ionization_mechanism_id"] == "im-hydride"
    assert tropylium["provenance"]["partner_gate"]["through"] == "[M-H]+"
    # The reading it was measured as, set aside on it as the search's own.
    (displaced,) = [
        alternative
        for alternative in tropylium["alternatives"]
        if alternative["assigned_formula"] == "C7H6"
    ]
    assert displaced["same_ion"] is True
    assert displaced["source"] == "untargeted"
    assert {
        alternative["source"] for alternative in measured["p2"]["alternatives"]
    } == {"untargeted"}


@pytest.mark.asyncio
async def test_a_persons_choice_reads_no_list():
    # A pin's rules are the spectrum's alone, which ask nothing of a list.
    from mascope_backend.api.new.peak_assignments import pipeline

    _, mocks = await _measure(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        [("bp-1", "Br2", "Br2H-", "im-h", ["p1"])],
        path=pipeline.PERSONS_CHOICE,
    )

    mocks["known"].assert_not_awaited()


def test_a_known_set_holds_an_ion_through_the_channel_it_was_read_through():
    # Stage A reads the target library through the mode's own channels and a
    # reference list through every searched one: an ion is held by neutral and
    # channel, a neutral spelled another way is the same, and the reference
    # lists' names ride on whichever list's row holds it.
    from mascope_backend.api.new.peak_assignments.engine import ListedIon, listed_ions

    names = [{"name": "acetic acid"}]
    benzene = [{"name": "benzene"}]
    columns = [
        "target_compound_formula",
        "ionization_mechanism_id",
        "target_compound_id",
        "reference_identities",
    ]
    known = pd.DataFrame(
        [
            # Two compounds of one neutral in the library: the first by id.
            ("CH3COOH", "im-h", "tc-2", None),
            ("C2H4O2", "im-h", "tc-1", None),
            ("C2H4O2", "im-nh4", None, names),
            ("C2H4O2", "im-h", None, names),
            ("C6H6", "im-ct", None, benzene),
            (None, "im-h", "tc-3", None),
        ],
        columns=columns,
    )

    assert listed_ions(known) == {
        ("C2H4O2", "im-h"): ListedIon("tc-1", names),
        ("C2H4O2", "im-nh4"): ListedIon(None, names),
        ("C6H6", "im-ct"): ListedIon(None, benzene),
    }
    # The target library's frame alone, before a reference list joins it:
    # its ions name their compounds and no reference list's names.
    library = pd.DataFrame(
        [("C7H8", "im-ct", "tc-4")],
        columns=columns[:3],
    )
    assert listed_ions(library) == {("C7H8", "im-ct"): ListedIon("tc-4", None)}
    assert listed_ions(pd.DataFrame()) == {}
    # A known set read for the neutrals it names alone holds no ion.
    assert listed_ions(pd.DataFrame({"target_compound_formula": ["C2H4O2"]})) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [True, False], ids=["recorded", "none"])
async def test_the_search_s_mass_gate_judges_at_the_samples_recorded_calibration(
    recorded,
):
    # The measurement model a batch search reads is the one the sample's
    # latest run recorded: a reading 5 ppm off that run's model, whose width is
    # 0.1 ppm widened to 0.51, is held below assignability as that run's gate
    # holds it. With no run to read one from, the model is the class's
    # stand-in and is refitted on what the search committed - one
    # uncorroborated reading, too few - and the gate stands down.
    from mascope_backend.api.new.peak_assignments.row_terms import MEASUREMENT_MODEL_KEY

    reading = _match(160.8413, "Br2", "Br2H-", "[M-H]-")
    reading["mz_error_ppm"] = 5.0

    def finder(*args, **kwargs):
        return pd.DataFrame([reading]), None

    stack, mocks = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
    )
    if recorded:
        mocks["latest"].return_value = (
            "run-1",
            {
                MEASUREMENT_MODEL_KEY: {
                    "version": 2,
                    "mu_ppm": 0.0,
                    "mu_source": "fitted",
                    "sigma_ppm": 0.1,
                    "fallback_sigma_ppm": 0.3,
                }
            },
        )
    with stack:
        rows, _, _ = await batch_untargeted._search_sample(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )

    (row,) = rows
    gate = row["provenance"]["mass_gate"]
    # One read of the sample's latest run gives the model.
    mocks["latest"].assert_awaited_once_with("si-1")
    if recorded:
        assert row["provenance"]["mass_z"] == pytest.approx(5.0 / 0.5099, abs=0.01)
        assert row["tier"] == "below_assignability"
        assert gate["capped"] == "below_assignability"
    else:
        assert "capped" not in gate
        assert "mass_z" not in row["provenance"]
        assert row["tier"] == TIER_CANDIDATE


@pytest.mark.asyncio
async def test_a_representative_the_claim_stage_takes_is_no_target():
    # The batch search runs the claim stage a run runs: a representative its
    # sample's artifact pre-pass claims is taken out of what the search
    # enumerates, as a run's search never searches a peak that stage took. The
    # other representative is searched.
    searched = []

    def finder(*args, **kwargs):
        searched.append(sorted(kwargs["targets"]))
        return pd.DataFrame(), None

    stack, _ = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6), ("p2", 200.0, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
    )
    with (
        stack,
        patch(
            "mascope_backend.api.new.peak_assignments.prepasses._artifact_assignments",
            return_value=([], {"p1"}),
        ),
    ):
        await batch_untargeted._search_sample(
            "si-1", {"p1", "p2"}, PeakAssignmentConfig()
        )

    assert searched == [[200.0]]


@pytest.mark.asyncio
async def test_a_representative_the_batch_ledger_explains_is_no_target():
    # A peak the sample's ledger already reads is not searched, as a run's
    # search never searches its Stage A's: the representative chosen before
    # the ledger came to read it is taken out of what the search enumerates.
    searched = []

    def finder(*args, **kwargs):
        searched.append(sorted(kwargs["targets"]))
        return pd.DataFrame(), None

    stack, _ = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6), ("p2", 200.0, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
        explained={"p1"},
    )
    with stack:
        await batch_untargeted._search_sample(
            "si-1", {"p1", "p2"}, PeakAssignmentConfig()
        )

    assert searched == [[200.0]]


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
    seen = {}

    def finder(*args, **kwargs):
        seen["scoring"] = kwargs["scoring"]
        raise _Searched

    stack, _ = _searching(
        _sample(filename=filename),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
    )
    with stack, pytest.raises(_Searched):
        await batch_untargeted._search_sample("si-1", {"p1"}, PeakAssignmentConfig())

    assert seen["scoring"].sigma_ppm == pytest.approx(
        math.hypot(INSTRUMENT_FALLBACK_SIGMA_PPM[instrument], PRED_SIGMA_PPM)
    )
    assert seen["scoring"].mz_tolerance_ppm == 15.0


@pytest.mark.asyncio
async def test_a_searched_samples_width_centre_and_tolerance_are_one_records():
    # MAS-867: the width, the centre and the line-share tolerance a batch
    # search ranks a sample at come from one read of one run's record, its
    # measurement model and the tolerance beside it. A run that completes
    # mid-search is a second record the search never reads.
    seen = {}

    def finder(*args, **kwargs):
        seen["scoring"] = kwargs["scoring"]
        raise _Searched

    first = {
        "measurement_model": {
            "version": 2,
            "mu_ppm": -0.4,
            "mu_source": "fitted",
            "sigma_ppm": 0.2,
            "fallback_sigma_ppm": 0.3,
            "width_floor_ppm": 0.6,
        },
        "envelope_tolerance": {
            "form": "centre_width",
            "level": "own",
            "centre": 0.07,
            "floor": 0.05,
            "detect_snr": 3.0,
        },
    }
    later = {
        "measurement_model": {
            "version": 1,
            "mu_ppm": 1.5,
            "mu_source": "fitted",
            "sigma_ppm": 2.0,
        },
        "envelope_tolerance": None,
    }
    stack, mocks = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
    )
    mocks["latest"].side_effect = [("run-1", first), ("run-2", later)]
    with stack, pytest.raises(_Searched):
        await batch_untargeted._search_sample("si-1", {"p1"}, PeakAssignmentConfig())

    mocks["latest"].assert_awaited_once_with("si-1")
    scoring = seen["scoring"]
    # The record's floor, its centre and its tolerance, all three the first
    # run's.
    assert scoring.sigma_ppm == pytest.approx(0.6)
    assert scoring.mu_ppm == pytest.approx(-0.4)
    assert scoring.envelope_tolerance.centre == pytest.approx(0.07)


@pytest.fixture(autouse=True)
def nothing_named():
    """The context a sample's batch and dataset name lives with them in the
    database; these samples' name none unless a test says."""
    with patch(
        f"{_PIPE}.named_context_for", AsyncMock(return_value=NamedContext())
    ) as named:
        yield named


@pytest.mark.asyncio
async def test_a_batch_search_searches_under_the_context_its_batch_names(
    nothing_named,
):
    # Step 3.6: the batch search resolves the context a run of the sample
    # would, the one the batch or its dataset names, so its grid and ratio
    # windows are that context's.
    nothing_named.return_value = NamedContext(batch="chamber")
    seen = {}

    def finder(frame, search, heuristics, **kwargs):
        seen["windows"] = heuristics.context_ratio_windows
        raise _Searched

    stack, _ = _searching(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
    )
    with stack, pytest.raises(_Searched):
        await batch_untargeted._search_sample("si-1", {"p1"}, PeakAssignmentConfig())

    nothing_named.assert_awaited_with("sb-1")
    assert seen["windows"] == CHAMBER.ratio_windows()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "width, window", [(1.0, 6 * math.hypot(1.0, PRED_SIGMA_PPM)), (None, 10.0)]
)
async def test_a_tof_batch_search_is_bounded_by_its_recorded_width(
    width, window, monkeypatch
):
    # Step 3.6, as a run's own search is bounded by its Stage A's: at the width
    # the sample's latest run fitted, six widths from its centre; at the class's
    # 10 ppm where no run fitted one, however narrow the class's own width.
    monkeypatch.setitem(INSTRUMENT_FALLBACK_SIGMA_PPM, "tof", 0.5)
    seen = {}

    def finder(frame, search, heuristics, **kwargs):
        seen["window"] = search.mass_range_ppm
        raise _Searched

    stack, _ = _searching(
        _sample(filename="tof-sample.h5"),
        _peaks([("p1", 160.8413, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
        width=width,
    )
    with stack, pytest.raises(_Searched):
        await batch_untargeted._search_sample("si-1", {"p1"}, PeakAssignmentConfig())

    assert seen["window"] == pytest.approx(window, abs=1e-3)


@pytest.mark.asyncio
@pytest.mark.parametrize("counted", [True, False], ids=["counted", "uncounted"])
async def test_a_batch_search_reads_the_peak_lists_scan_counts(counted):
    # As a run's search does: a dim line is read at its mean per scan where
    # the file keeps the counts.
    peaks = _peaks([("p1", 160.8413, 1.0e6)])
    if counted:
        peaks["n_scans"] = 3.0
        peaks["n_scans_total"] = 4.0
    seen = {}

    def finder(frame, *args, **kwargs):
        seen["columns"] = set(frame.columns)
        raise _Searched

    stack, _ = _searching(
        _sample(), peaks, [_mechanism("[M-H]-", "im-h")], finder=finder
    )
    with stack, pytest.raises(_Searched):
        await batch_untargeted._search_sample("si-1", {"p1"}, PeakAssignmentConfig())

    assert ({"n_scans", "n_scans_total"} <= seen["columns"]) is counted


async def _search_ammonium_reading(*, opened, known):
    """A batch search of one uronium sample whose finder reads one peak as
    C9H14O3 through ammonium, the channel's carrier shown or not (``opened``),
    over a known set of ``known``.

    :return: The search's rows and the fake known-set read.
    """
    # 138.0986 is the carrier that opens the ammonium channel on a uronium
    # source (`with_secondary_channels`); 188.1281 is C9H14O3 + NH4+.
    specs = [("p0", 100.0, 1.0e6), ("p2", 188.1281, 1.0e5)]
    if opened:
        specs.append(("p1", 138.0986, 1.0e4))
    mode = [_mechanism("[M+H]+", "im-h"), _mechanism("[M+CH4N2O+H]+", "im-urea")]
    reading = _match(188.1281, "C9H14O3", "C9H18NO3+", "[M+NH4]+")

    def finder(*args, **kwargs):
        return pd.DataFrame([reading]), None

    stack, mocks = _searching(
        _sample(polarity="+"),
        _peaks(specs),
        mode,
        finder=finder,
        secondary=[_mechanism("[M+NH4]+", "im-nh4")],
        known=known,
    )
    with stack:
        rows, _, _ = await batch_untargeted._search_sample(
            "si-1", {"p2"}, PeakAssignmentConfig()
        )
    return rows, mocks["known"]


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [True, False], ids=["listed", "unlisted"])
async def test_a_list_naming_the_neutral_corroborates_a_batch_searchs_reading(
    listed,
):
    # Step 3.4 as a run of the sample reads it: a reading through a secondary
    # channel is corroborated where a list the sample is read against names
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
    # Read as a run reads it: the target library through the mode's own
    # mechanisms, the reference lists through every searched one, under the
    # context's ceiling, at the file's resolving power (none read here).
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
        ).context.known_window,
        "resolution": None,
        # Read for what they list, not matched (MAS-1274).
        "lines": False,
    }


@pytest.mark.asyncio
async def test_a_batch_search_reads_its_lists_whatever_channel_it_opens():
    # As a run's Stage A reads them: the lists tell the partner gate what they
    # name and can open a halogen to the twin re-read, whether or not the
    # spectrum opens a secondary channel.
    _, known_isotopes = await _search_ammonium_reading(
        opened=False, known=pd.DataFrame()
    )
    known_isotopes.assert_awaited_once()


class TestTheBatchSearchReadsHalogenTwins:
    """MAS-911: the halogen-twin re-read follows the batch search inside its
    propose stage, with a twin unexplained in the batch's own frame."""

    @staticmethod
    def _chlorobenzene():
        from test_halogen_twins import CHLOROBENZENE, M0, TOLUENE

        lines = sorted(CHLOROBENZENE + TOLUENE)
        peaks = pd.DataFrame(
            [
                {"sample_peak_id": f"p{index}", "mz": mz, "intensity": height}
                for index, (mz, height) in enumerate(lines)
            ]
        )
        target = peaks.loc[(peaks["mz"] - M0).abs().idxmin(), "sample_peak_id"]
        return peaks, target, M0

    async def _search(self, peaks, target, *, config=None, explained=frozenset()):
        from test_halogen_twins import matches

        def finder(frame, *args, **kwargs):
            return matches(frame), None

        stack, mocks = _searching(
            _sample(polarity="+"),
            peaks,
            [_mechanism("[M]+.", "im-ct")],
            finder=finder,
            explained=explained,
        )
        with stack:
            rows, _, _ = await batch_untargeted._search_sample(
                "si-1", {target}, config or PeakAssignmentConfig()
            )
        return rows

    @pytest.mark.asyncio
    async def test_a_chlorine_twin_left_unexplained_is_read_again(self):
        peaks, target, m0 = self._chlorobenzene()

        rows = await self._search(peaks, target)

        (row,) = [row for row in rows if row["sample_peak_id"] == target]
        assert row["sample_peak_mz"] == pytest.approx(m0)
        assert row["assigned_formula"] == "C6H5Cl"
        assert row["provenance"]["halogen_twin"]["halogen"] == "Cl"

    @pytest.mark.asyncio
    async def test_a_twin_the_batch_ledger_explains_is_no_twin(self):
        # The 37Cl line's peak reads a formula in the batch ledger already:
        # in the batch's frame it is explained, as a run's Stage A row is.
        from test_halogen_twins import TWIN

        peaks, target, _ = self._chlorobenzene()
        twin = peaks.loc[(peaks["mz"] - TWIN).abs().idxmin(), "sample_peak_id"]

        rows = await self._search(peaks, target, explained={twin})

        assert not any(row.get("assigned_formula") == "C6H5Cl" for row in rows)

    @pytest.mark.asyncio
    async def test_a_bromide_sample_reads_no_bromine_twin(self, monkeypatch):
        from mascope_backend.api.new.peak_assignments import judging_loop

        seen = {}
        real = judging_loop.twin_halogens

        def spy(*args, **kwargs):
            seen["halogens"] = real(*args, **kwargs)
            return seen["halogens"]

        monkeypatch.setattr(judging_loop, "twin_halogens", spy)
        peaks, target, _ = self._chlorobenzene()

        await self._search(peaks, target, config=PeakAssignmentConfig(profile="BR"))

        assert seen["halogens"] == ("Cl",)


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

    start = AsyncMock(return_value="run-7")
    with (
        patch.object(batch_runs, "start_run", start),
        patch.object(batch_runs, "complete_run", AsyncMock()),
        patch.object(batch_untargeted, "run_batch_untargeted_search", search),
    ):
        await batch_untargeted.search_batch_untargeted.__wrapped__("sb-1")
        assert seen["batch_peak_run_id"] == "run-7"
        # Inside a pass it writes into the pass's run and opens none.
        start.reset_mock()
        await batch_untargeted.search_batch_untargeted.__wrapped__(
            "sb-1", batch_peak_run_id="pass-run"
        )
        assert seen["batch_peak_run_id"] == "pass-run"
        start.assert_not_called()


def test_a_judged_row_reads_an_annotation_in_its_formula_mechanism_and_role():
    # A member takes a batch decision only where its sample's judged row on
    # the member's peak reads the decision: the formula, through its
    # mechanism, in the role the anchor plays. The same formula read as one
    # of its isotope lines is another reading, whose tier is that line's.
    annotation = Annotation(
        formula="C6H12O6",
        ion_formula="C6H13O6+",
        ionization_mechanism_id="mech-h",
        role="M0",
        owner_batch_peak_id=None,
    )
    row = {
        "assigned_formula": "C6H12O6",
        "ionization_mechanism_id": "mech-h",
        "role": "M0",
    }

    assert batch_untargeted.reads_annotation(row, annotation)
    assert not batch_untargeted.reads_annotation(
        row | {"role": "iso_child"}, annotation
    )
    assert not batch_untargeted.reads_annotation(
        row | {"ionization_mechanism_id": "mech-na"}, annotation
    )
    assert not batch_untargeted.reads_annotation(
        row | {"assigned_formula": "C6H10O5"}, annotation
    )


# --- the rivals of a measured reading (MAS-1045) ---------------------------------

#: Two formulas the grid holds for Br2H-'s peak that its evidence cannot tell
#: from it: ``(formula, ion, notation, mechanism id, peaks)``.
_CROWD = [
    ("C5H8O4", "C5H7O4-", "[M-H]-", "im-h", ["p1"]),
    ("C6H10O5", "C6H9O5-", "[M-H]-", "im-h", ["p1"]),
]


#: A formula the grid holds there whose fit ties Br2H-'s and whose
#: plausibility separates it (``finder.ReadingRivals.fit_tied``, MAS-975).
_FIT_TIED = [("CH2N2O5", "CHN2O5-", "[M-H]-", "im-h", ["p1"])]


async def _search_crowded(peaks, mechanisms):
    """The batch search's row on the crowded peak: the finder commits Br2H-
    and names the crowd against it (``finder.CANDIDATE_RIVALS``), and the
    search measures them beside it at the sample's model, as a run does
    (``judging_loop._seeded_rescore``)."""
    from mascope_backend.api.new.peak_assignments import judging_loop

    rescore = judging_loop._seeded_rescore
    pairings = {("Br2", "im-h"): ("ion-br2", "Br2H-", ["p1"])}
    for index, (formula, ion, _, mechanism_id, paired) in enumerate(_CROWD):
        pairings[(formula, mechanism_id)] = (f"ion-rival-{index}", ion, paired)

    def finder(*args, **kwargs):
        match = _match(160.8413, "Br2", "Br2H-", "[M-H]-")
        match["candidate_density"] = 1 + len(_CROWD)
        match["candidate_rivals"] = [
            {"formula": formula, "ion": ion, "ionization_mechanism": notation}
            for formula, ion, notation, _, _ in _CROWD
        ]
        return pd.DataFrame([match]), None

    stack, _ = _searching(_sample(), peaks, mechanisms, finder=finder)
    stack.enter_context(patch.object(judging_loop, "_seeded_rescore", rescore))
    stack.enter_context(
        patch.object(judging_loop, "score_seeds", _scorer(pairings, peaks, []))
    )
    with stack:
        (row,), _, _ = await batch_untargeted._search_sample(
            "si-1", {"p1"}, PeakAssignmentConfig()
        )
    return row


@pytest.mark.asyncio
async def test_a_measured_reading_on_a_crowded_peak_takes_its_searchs_density():
    # MAS-1045: on a peak the grid holds two formulas its evidence cannot tell
    # from the reading, the batch search's row is held at candidate by the
    # density rule. The same reading measured on a member is asked the grid
    # too, its rivals measured beside it as the search's are: one density,
    # one tier, the same rules capping both.
    peaks = _peaks([("p1", 160.8413, 1.0e6)])
    mechanisms = [_mechanism("[M-H]-", "im-h")]
    searched = await _search_crowded(peaks, mechanisms)
    measured, mocks = await _measure(
        _sample(),
        peaks,
        mechanisms,
        [("bp-1", "Br2", "Br2H-", "im-h", ["p1"])],
        grid=_CROWD,
        fit_tied=_FIT_TIED,
    )

    member = measured["p1"]
    # The search's own rows name no rival by its fit alone, here as in a run.
    assert "fit_tied" not in member["provenance"]["rival_set"]
    for row in (searched, member):
        assert row["provenance"]["candidate_density"] == 3
        assert row["tier"] == TIER_CANDIDATE
        assert [rule for rule, caps in _reasons(row) if caps] == [
            "candidate_density",
            "lone_peak",
        ]
        rivals = row["provenance"]["rival_set"]["rivals"]
        assert sorted(rival["formula"] for rival in rivals) == ["C5H8O4", "C6H10O5"]
    assert _reasons(member) == _reasons(searched)
    # The search's own reading: radicals are among its candidates.
    ((reading,), kwargs) = mocks["grid"][0]
    assert (reading.mz, reading.formula, reading.ionization_mechanism) == (
        160.8413,
        "Br2",
        "[M-H]-",
    )
    # Ordered among the grid's candidates by its own mass error, as a list's is.
    assert reading.mz_error_ppm == pytest.approx(0.1)
    assert kwargs == {"closed_shell_only": False}
    # The rivals are measured in a pass of their own, once.
    assert len(mocks["scored"]) == 2


@pytest.mark.asyncio
async def test_a_measured_lists_reading_reads_its_grid_rivals_as_a_runs_does():
    # A run puts a list's reading to the search, which counts the grid's
    # closed-shell rivals into its density and records what it saw
    # (``engine.GRID_RIVALS``); the identification level then reads the row's
    # rivals as measured. So does the same reading measured on a member.
    known = pd.DataFrame(
        [
            {
                "target_compound_formula": "Br2",
                "ionization_mechanism_id": "im-h",
                "target_compound_id": "tc-br2",
                "reference_identities": None,
            }
        ]
    )
    measured, mocks = await _measure(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6), ("p2", 162.8393, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        [("bp-1", "Br2", "Br2H-", "im-h", ["p1", "p2"])],
        grid=_CROWD,
        fit_tied=_FIT_TIED,
        known=known,
    )

    row = measured["p1"]
    assert row["source"] == "database"
    assert row["provenance"]["candidate_density"] == 3
    assert row["tier"] == TIER_CANDIDATE
    assert "candidate_density" in [rule for rule, caps in _reasons(row) if caps]
    grid = row["provenance"]["grid_rivals"]
    assert [rival["formula"] for rival in grid["rivals"]] == ["C5H8O4", "C6H10O5"]
    # What only plausibility separated is measured and recorded apart, and
    # moves no density (MAS-975), as on a run's list row.
    (tied,) = row["provenance"]["rival_set"]["fit_tied"]
    assert tied["formula"] == "CH2N2O5"
    assert grid["fit_tied"] == 1
    level = row["provenance"]["identification_level"]
    assert "rivals not measured" not in level["evidence"]
    assert sorted(entry["formula"] for entry in level["rivals"]) == [
        "C5H8O4",
        "C6H10O5",
        "CH2N2O5",
    ]
    # The grid is asked of the reading's own peak, not of its 81Br line's.
    assert measured["p2"]["role"] == "iso_child"
    ((reading,), asked) = mocks["grid"][0]
    assert reading.mz == 160.8413
    assert asked == {"closed_shell_only": True}


@pytest.mark.asyncio
async def test_a_rival_the_pass_also_gives_is_measured_as_the_reading_it_gives():
    # A formula the grid names against one reading that the batch also gives
    # on another peak is measured once, in the given readings' frame, as a
    # run measures a named rival its search also commits.
    measured, mocks = await _measure(
        _sample(),
        _peaks([("p1", 160.8413, 1.0e6), ("p2", 300.0, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        [
            ("bp-1", "Br2", "Br2H-", "im-h", ["p1"]),
            ("bp-2", "C5H8O4", "C5H7O4-", "im-h", ["p2"]),
        ],
        grid=[("C5H8O4", "C5H7O4-", "[M-H]-", "im-h", ["p2"])],
    )

    assert len(mocks["scored"]) == 1
    assert measured["p1"]["provenance"]["candidate_density"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["finder", "notation"])
async def test_a_channel_the_grid_cannot_read_leaves_the_reading_measured(refusal):
    # A mechanism the finder cannot parse refuses the grid read. The reading
    # is then measured as before, without the grid's rivals, not left
    # unmeasured with the rest of its sample.
    from mascope_tools.composition.exceptions import CompositionFinderException
    from mascope_tools.composition.mechanism_notation import MechanismNotationError

    error = (
        CompositionFinderException("Unsupported ionization mechanism")
        if refusal == "finder"
        else MechanismNotationError("not in the standard adduct notation")
    )
    with patch(
        "mascope_backend.api.new.peak_assignments.given_rivals.rivals_of_readings",
        side_effect=error,
    ):
        measured, mocks = await _measure(
            _sample(),
            _peaks([("p1", 160.8413, 1.0e6)]),
            [_mechanism("[M-H]-", "im-h")],
            [("bp-1", "Br2", "Br2H-", "im-h", ["p1"])],
        )

    row = measured["p1"]
    assert row["provenance"]["candidate_density"] == 1
    assert row["tier"] == TIER_CANDIDATE
    assert [rule for rule, caps in _reasons(row) if caps] == ["lone_peak"]
    assert len(mocks["scored"]) == 1


@pytest.mark.asyncio
async def test_a_reading_the_grid_cannot_read_is_measured_without_its_rivals():
    # The grid answers None for a reading whose formula or channel it cannot
    # read (``finder.rivals_of_readings``). The reading is then measured as
    # before, without grid rivals, and the miss is kept through the pass: a
    # second judging does not ask the grid of it again.
    from mascope_backend.api.new.peak_assignments.kept_measurement import (
        KeptMeasurement,
    )

    kept = KeptMeasurement()
    grid = MagicMock(return_value=[None])
    measured = []
    with patch(
        "mascope_backend.api.new.peak_assignments.given_rivals.rivals_of_readings",
        grid,
    ):
        for _ in range(2):
            rows, mocks = await _measure(
                _sample(),
                _peaks([("p1", 160.8413, 1.0e6)]),
                [_mechanism("[M-H]-", "im-h")],
                [("bp-1", "Br2", "Br2H-", "im-h", ["p1"])],
                kept=kept,
                width=0.6,
            )
            measured.append(rows["p1"])

    for row in measured:
        assert row["provenance"]["candidate_density"] == 1
        assert "grid_rivals" not in row["provenance"]
        assert [rule for rule, caps in _reasons(row) if caps] == ["lone_peak"]
    assert grid.call_count == 1


def test_the_grid_is_asked_once_of_a_reading_a_pass_judges_again():
    # A batch pass judges a sample again after each stage that rewrites it,
    # with the same readings at the same peaks and model: the grid's answer
    # for each is kept (``kept_measurement.kept_grid``) and read again, and
    # only a reading it was not asked of, or asked another way, asks it.
    from mascope_backend.api.new.peak_assignments import given_rivals

    peaks = _peaks([("p1", 160.8413, 1.0e6), ("p2", 300.0, 1.0e6)])
    rows = [
        {
            "role": "M0",
            "assigned_formula": formula,
            "ionization_mechanism_id": "im-h",
            "sample_peak_id": peak,
            "mz_error_ppm": 0.1,
        }
        for formula, peak in (("Br2", "p1"), ("C5H8O4", "p2"))
    ]
    calls = []
    grid = _grid([(f, ion, notation) for f, ion, notation, _, _ in _CROWD], calls)

    def ask(rows, *, listed, memo):
        return given_rivals.given_readings_rivals(
            rows,
            peaks,
            ["mz", "intensity"],
            notation_by_id={"im-h": "[M-H]-"},
            search_config="config",
            heuristics="heuristics",
            scoring=None,
            listed=listed,
            memo=memo,
        )

    memo = {}
    with patch.object(given_rivals, "rivals_of_readings", grid):
        first = ask(rows[:1], listed=True, memo=memo)
        again = ask(rows, listed=True, memo=memo)
        other_way = ask(rows[:1], listed=False, memo=memo)
        unkept = ask(rows[:1], listed=True, memo=None)

    asked = [[reading.formula for reading in readings] for readings, _ in calls]
    assert asked == [["Br2"], ["C5H8O4"], ["Br2"], ["Br2"]]
    assert [row["assigned_formula"] for row, _ in again] == ["Br2", "C5H8O4"]
    assert again[0][1] is first[0][1]
    assert other_way[0][1] is not first[0][1]
    assert len(unkept) == 1


@pytest.mark.asyncio
async def test_a_pass_judging_a_sample_again_reads_the_grid_it_asked():
    # The batch's judging measures a sample's readings again after each stage
    # that rewrites it, in one pass's store: the second measurement asks the
    # grid nothing and reads the same rivals, at the same density.
    from mascope_backend.api.new.peak_assignments.kept_measurement import (
        KeptMeasurement,
    )

    kept = KeptMeasurement()
    measured = []
    for _ in range(2):
        rows, mocks = await _measure(
            _sample(),
            _peaks([("p1", 160.8413, 1.0e6)]),
            [_mechanism("[M-H]-", "im-h")],
            [("bp-1", "Br2", "Br2H-", "im-h", ["p1"])],
            grid=_CROWD,
            kept=kept,
            width=0.6,
        )
        measured.append((rows["p1"], len(mocks["grid"])))

    (first, asked_first), (again, asked_again) = measured
    assert (asked_first, asked_again) == (1, 0)
    assert again["provenance"]["candidate_density"] == 3
    assert again["provenance"]["rival_set"] == first["provenance"]["rival_set"]


#: The lists' known set of the sample: dinitrophenol from a reference list, and
#: glucose from the target library alone.
_LISTED = pd.DataFrame(
    {
        "target_compound_formula": ["C6H4N2O5", "C6H12O6"],
        "ionization_mechanism_id": ["im-h", "im-h"],
        "target_compound_id": [None, "tc-glucose"],
        "reference_identities": [[{"name": "2,4-dinitrophenol"}], None],
    }
)


@pytest.mark.asyncio
async def test_a_batch_search_spares_the_neutrals_its_lists_name_the_windows():
    # Decision MAS-1280: the search grades what it proposes with the
    # neutrals a reference list names left out of the windows, as a run's
    # search does once its Stage A has read them; the target library's are
    # graded like any other.
    from mascope_tools.composition.formula_shape import graded_run

    seen = []

    def finder(*args, **kwargs):
        seen.append(graded_run())
        return pd.DataFrame(), None

    stack, _ = _searching(
        _sample(),
        _peaks([("p1", 183.0047, 1.0e6)]),
        [_mechanism("[M-H]-", "im-h")],
        finder=finder,
        known=_LISTED,
    )
    with stack:
        await batch_untargeted._search_sample("si-1", {"p1"}, PeakAssignmentConfig())

    (graded,) = seen
    assert graded.listed == {"C6H4N2O5"}


@pytest.mark.asyncio
async def test_a_member_is_judged_with_the_neutrals_its_lists_name_out_of_the_windows():
    # The propagation judges a reading as a run of the member's sample would,
    # its lists read as that run reads them: the same neutrals spared, from
    # the election of one reading per peak on. In ambient air's windows the
    # listed dinitrophenol is graded far down; spared, it keeps its bare
    # plausibility and is assigned, as a run of the sample assigns it.
    from mascope_backend.api.new.peak_assignments import pipeline
    from mascope_tools.composition.formula_shape import graded_in, graded_run
    from mascope_tools.composition.heuristic_filter import formula_plausibility
    from mascope_tools.composition.profiles import AMBIENT_AIR

    windows = AMBIENT_AIR.ratio_windows()
    with graded_in(windows):
        graded = formula_plausibility("C6H4N2O5")
    assert graded < 0.5 < formula_plausibility("C6H4N2O5")

    seen = []
    judge = pipeline.STAGE_FUNCTIONS[pipeline.Stage.JUDGE]

    async def watched(ctx):
        seen.append(graded_run())
        await judge(ctx)

    with (
        patch.dict(pipeline.STAGE_FUNCTIONS, {pipeline.Stage.JUDGE: watched}),
        patch.object(ResolvedProfile, "graded", lambda self: graded_in(windows)),
    ):
        rows, _ = await _measure(
            _sample(),
            _peaks([("p1", 183.0047, 1.0e6)]),
            [_mechanism("[M-H]-", "im-h")],
            [("bp-1", "C6H4N2O5", "C6H3N2O5-", "im-h", ["p1"])],
            known=_LISTED,
        )

    assert seen
    assert {graded.listed for graded in seen} == {frozenset({"C6H4N2O5"})}
    row = rows["p1"]
    assert row["provenance"]["plausibility"] == formula_plausibility("C6H4N2O5")
    assert row["provenance"]["reference_identities"] == [{"name": "2,4-dinitrophenol"}]
    assert row["tier"] == TIER_ASSIGNED
