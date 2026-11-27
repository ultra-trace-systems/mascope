"""What a hand M0 claims of its lines, and the source a demoted one comes back
with (``hand_claims``)."""

import pytest

from mascope_backend.api.new.peak_assignments.hand_claims import (
    ACTION_CLAIM_LINE,
    _run_would_take,
    is_claimed_line,
    restored_source,
)
from mascope_backend.api.new.peak_assignments.tiering import LineGate
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance


CLAIMED = {"manual": {"action": ACTION_CLAIM_LINE, "at": "2026-11-27T00:00:00"}}


@pytest.mark.parametrize("source", ["database", "untargeted"])
def test_an_engine_source_comes_back_as_it_was(source):
    assert restored_source(source, None) == source


def test_a_line_a_hand_m0_claimed_comes_back_manual():
    assert restored_source("manual", CLAIMED) == "manual"


@pytest.mark.parametrize(
    "provenance",
    [
        None,
        {},
        {"manual": {"action": "set_assignment"}},
        {"manual": "claim_isotope_line"},
        ["manual"],
    ],
)
def test_any_other_manual_row_comes_back_sourceless(provenance):
    """An archive is JSON an import could have written anything into."""
    assert restored_source("manual", provenance) is None
    assert not is_claimed_line(provenance)


def test_an_unknown_source_comes_back_sourceless_even_on_a_claimed_line():
    assert restored_source("imported", CLAIMED) is None


#: A fitted curve whose lower end opens to 0.44 of the share at an expected
#: S/N of 10 to 50, as step 5.3 measured on Orbitrap files.
CURVE = EnvelopeTolerance(snr_edges=(3.0, 10.0, 50.0), lower=(0.4, 0.44, 0.7))


def _gate(*, tof: bool) -> LineGate:
    return LineGate(
        mz_tolerance_ppm=3.0,
        abundance_floor=0.01,
        detect_snr=10.0,
        detect_from="default",
        rel_detect_no_snr=0.05,
        tof=tof,
    )


def _line(abundance_error: float) -> dict:
    return {
        "mz_error_ppm": 0.5,
        "abundance_error": abundance_error,
        "expected_snr": 20.0,
        "observed_share": 1.0 + abundance_error,
        "predicted_share": 1.0,
    }


def test_an_orbitrap_line_below_the_fixed_window_is_not_claimed_on_a_curve():
    """An Orbitrap run reads a line by its scan presence and holds it to the
    fixed window, whatever the curve allows a peak list's share."""
    assert "would not take" in _run_would_take(_line(-0.5), _gate(tof=False), CURVE)


def test_a_tof_line_below_the_fixed_window_is_claimed_within_the_curve():
    assert _run_would_take(_line(-0.5), _gate(tof=True), CURVE) == ""


@pytest.mark.parametrize("tof", [False, True])
def test_a_line_within_the_fixed_window_is_claimed_on_either(tof):
    assert _run_would_take(_line(-0.3), _gate(tof=tof), CURVE) == ""


@pytest.mark.parametrize("tof", [False, True])
def test_a_line_under_the_curve_is_not_claimed_on_either(tof):
    assert "would not take" in _run_would_take(_line(-0.6), _gate(tof=tof), CURVE)
