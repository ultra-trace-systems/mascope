"""Unit tests for the v2 match-score backend adapter (mascope_tools).

`match_isotope` persists `signal_to_noise`, but a row's value is None for files
without noise data and for every row stored before the column existed (no
backfill), so DB-read frames routinely MIX real values and nulls within one ion.
The no-SNR fallback is therefore a first-class production case that must apply
PER ROW - never drag the whole group into no-SNR mode - and must score in
`score_pattern_v2`'s no-SNR mode, never through an intensity-derived proxy.
Several tests below pin that the no-SNR answer equals the real-SNR answer.
"""

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    fit_sample_mass_accuracy,
    ion_score_v2,
    match_score_version,
    sample_noise_floor,
)
from mascope_tools.composition.heuristic_filter import DEFAULT_CALIBRATION_V2


def _ion(m1_intensity, snr=None, rel=0.11, me=(0.2, 0.3)):
    """One ion's isotopologue rows (M0 rel 1.0 + M+1 rel `rel`), as
    compute_match_isotopes produces them (unmatched -> intensity 0). `me` is settable so
    a test can zero the mass errors and isolate the intensity / detectability terms."""
    d = {
        "relative_abundance": [1.0, rel],
        "match_mz_error": list(me),
        "sample_peak_intensity": [1000.0, m1_intensity],
    }
    if snr is not None:
        d["signal_to_noise"] = snr
    return pd.DataFrame(d)


def test_good_match_high_fit():
    # default is the raw FIT score (not the calibrated probability): a good match is high
    assert ion_score_v2(_ion(110.0, snr=[500, 55]), sigma_ppm=0.5) > 0.8


def test_detectable_missing_isotopologue_penalized():
    perfect = ion_score_v2(_ion(110.0, snr=[500, 55]), sigma_ppm=0.5)
    missing = ion_score_v2(
        _ion(0.0, snr=[500, 0]), sigma_ppm=0.5
    )  # M+1 should be visible
    assert missing < perfect


def test_absent_monoisotopic_scores_zero():
    g = pd.DataFrame(
        {
            "relative_abundance": [1.0, 0.11],
            "match_mz_error": [0.0, 0.0],
            "sample_peak_intensity": [0.0, 0.0],
            "signal_to_noise": [0.0, 0.0],
        }
    )
    # Absent monoisotopic -> fit score 0 (no match); this is the default/headline value.
    assert ion_score_v2(g, sigma_ppm=0.5) == 0.0
    # The optional calibrated probability cannot be exactly 0 (sigmoid) -> floor ~0.0155.
    cal = ion_score_v2(
        g, sigma_ppm=0.5, calibrate=True, calibration=DEFAULT_CALIBRATION_V2
    )
    assert cal < 0.02


def test_no_snr_column_scores_without_proxy():
    # No signal_to_noise column is the DB-read case: no-SNR mode, no proxy. For a clean
    # envelope that is the same answer a real SNR gives, to ~0.1%.
    s = ion_score_v2(_ion(110.0), sigma_ppm=0.5, noise=5.0)
    assert s == pytest.approx(0.9560, abs=1e-3)


def test_no_snr_column_matches_real_snr_for_a_clean_envelope():
    # The no-SNR mode is not a different scale: the real-SNR intensity tolerance only
    # exceeds the 5%-of-abundance floor below SNR ~20, so a normally-measured envelope
    # scores identically either way.
    real = ion_score_v2(_ion(110.0, snr=[500, 55]), sigma_ppm=0.5)
    assert real == pytest.approx(
        ion_score_v2(_ion(110.0), sigma_ppm=0.5, noise=5.0), abs=1e-3
    )


def test_per_row_nan_snr_does_not_inflate():
    # An M+1 8x more intense than predicted is a bad fit whatever its SNR row says. A
    # missing per-row SNR used to widen the ratio tolerance to infinity and score 1.0.
    bad = _ion(880.0, snr=[500, np.nan], me=(0.0, 0.0))
    assert ion_score_v2(bad, sigma_ppm=0.5) == pytest.approx(0.2543, abs=1e-3)
    assert ion_score_v2(bad, sigma_ppm=0.5) == pytest.approx(
        ion_score_v2(_ion(880.0, snr=[500, 300], me=(0.0, 0.0)), sigma_ppm=0.5),
        abs=1e-3,
    )
    # ... and without the column at all, where the proxy used to score it 0.7391.
    assert ion_score_v2(
        _ion(880.0, me=(0.0, 0.0)), sigma_ppm=0.5, noise=5.0
    ) == pytest.approx(0.2543, abs=1e-3)


def test_absent_isotopologue_penalised_without_snr_column():
    # The proxy SNR (~4 for a base peak) put the detectability gate out of reach, so an
    # M+1 predicted at 30% of base and entirely absent scored a perfect 1.0. The
    # abundance fallback restores the real-SNR answer exactly.
    absent = dict(rel=0.30, me=(0.0, 0.0))
    no_column = ion_score_v2(_ion(0.0, **absent), sigma_ppm=0.5, noise=5.0)
    assert no_column == pytest.approx(0.7574, abs=1e-3)
    assert no_column == pytest.approx(
        ion_score_v2(_ion(0.0, snr=[500, np.nan], **absent), sigma_ppm=0.5), abs=1e-3
    )


def test_stored_base_snr_gates_sub_fallback_absent_sibling():
    # The persisted-SNR payoff (issue #1724): M+1 predicted at 5.5% of base
    # (~C5) sits below REL_DETECT_NO_SNR, so with no stored SNR its complete
    # absence is uninformative and the ion scores a clean 1.0. A stored base
    # SNR turns the real detectability gate on (0.055 * 500 >= k_detect) and
    # the absence starts costing the candidate. None (not just NaN) is how the
    # DB delivers a null column, so it must read as "no SNR" too.
    absent = dict(rel=0.055, me=(0.0, 0.0))
    without_snr = ion_score_v2(_ion(0.0, snr=[None, None], **absent), sigma_ppm=0.5)
    assert without_snr == pytest.approx(1.0, abs=1e-6)
    with_base_snr = ion_score_v2(_ion(0.0, snr=[500.0, None], **absent), sigma_ppm=0.5)
    assert with_base_snr < without_snr


def test_mixed_null_group_keeps_per_row_snr_concessions():
    # Pre-existing rows have no stored SNR (no backfill), so DB-read frames mix
    # real values and nulls within one ion. The fallback must be per row: a
    # noisy M+1 (SNR 3) keeps the mass-width concession its OWN SNR grants even
    # when the base row's stored SNR is null - the null rows must not drag the
    # whole group into no-SNR mode.
    noisy = _ion(110.0, snr=[None, 3.0], me=(0.0, 1.5))
    all_null = _ion(110.0, snr=[None, None], me=(0.0, 1.5))
    assert ion_score_v2(noisy, sigma_ppm=0.5) > ion_score_v2(all_null, sigma_ppm=0.5)


def test_noise_argument_does_not_change_the_score():
    # `noise` is retained for the call sites that still pass a sample_noise_floor, and is
    # inert: nothing about the score may depend on it any more.
    scores = {
        ion_score_v2(_ion(110.0), sigma_ppm=0.5, noise=n) for n in (1.0, 5.0, 1e6)
    }
    assert len(scores) == 1


def test_raw_vs_calibrated():
    g = _ion(110.0, snr=[500, 55])
    raw = ion_score_v2(g, sigma_ppm=0.5, calibrate=False)
    cal = ion_score_v2(
        g, sigma_ppm=0.5, calibrate=True, calibration=DEFAULT_CALIBRATION_V2
    )
    assert 0.0 < raw <= 1.0 and 0.0 < cal < 1.0
    # The default (raw fit) IS the headline score; calibration only compresses it.
    assert ion_score_v2(g, sigma_ppm=0.5) == raw
    assert cal < raw  # a good fit's calibrated probability sits below its fit quality


def test_calibrate_refuses_an_instrument_agnostic_curve():
    # The library default is a demo-Orbitrap Platt curve; applying it to whatever
    # instrument produced this frame is the mismatch per-instrument calibration exists to
    # prevent, so the caller must name the curve.
    with pytest.raises(ValueError, match="calibration"):
        ion_score_v2(_ion(110.0, snr=[500, 55]), sigma_ppm=0.5, calibrate=True)


def test_fit_sample_mass_accuracy():
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            "match_mz_error": rng.normal(0.1, 0.3, 50),
            "sample_peak_intensity": [100.0] * 50,
        }
    )
    mu, sigma = fit_sample_mass_accuracy(df)
    assert abs(mu - 0.1) < 0.2 and 0.1 < sigma < 0.6
    # too few anchors -> sigma None (caller falls back)
    assert fit_sample_mass_accuracy(df.head(3))[1] is None


def test_sample_noise_floor_positive():
    df = pd.DataFrame({"sample_peak_intensity": [1.0, 10.0, 100.0, 1000.0]})
    assert sample_noise_floor(df) > 0


def test_satellite_matched_isotopologue_treated_as_absent():
    base = dict(_ion(110.0, snr=[500, 55]))
    normal = ion_score_v2(
        pd.DataFrame({**base, "is_satellite": [False, False]}), sigma_ppm=0.5
    )
    # M+1 "matched" a satellite artifact -> treated as absent -> detectability penalty
    sat = ion_score_v2(
        pd.DataFrame({**base, "is_satellite": [False, True]}), sigma_ppm=0.5
    )
    assert sat < normal


def test_satellite_penalty_applies_without_snr_column():
    # The satellite rule zeroes the row and hands it to the detectability gate -- which
    # the proxy SNR had disabled, so on every DB-read path the rule was dead (0.9755
    # instead of 0.8561). The no-SNR gate reproduces the real-SNR penalty exactly.
    base = dict(_ion(110.0))
    sat = ion_score_v2(
        pd.DataFrame({**base, "is_satellite": [False, True]}), sigma_ppm=0.5, noise=5.0
    )
    assert sat == pytest.approx(0.8561, abs=1e-3)
    with_snr = dict(_ion(110.0, snr=[500, 55]))
    assert sat == pytest.approx(
        ion_score_v2(
            pd.DataFrame({**with_snr, "is_satellite": [False, True]}), sigma_ppm=0.5
        ),
        abs=1e-3,
    )


def test_match_score_version_env(monkeypatch):
    monkeypatch.delenv("MASCOPE_MATCH_SCORE_VERSION", raising=False)
    assert match_score_version() == 1  # legacy targeted path defaults to v1
    monkeypatch.setenv("MASCOPE_MATCH_SCORE_VERSION", "2")
    assert match_score_version() == 2  # opt into the fit score
    monkeypatch.setenv("MASCOPE_MATCH_SCORE_VERSION", "garbage")
    assert match_score_version() == 1  # malformed -> default


def test_category_thresholds_are_score_version_aware(monkeypatch):
    """Categories use version-aware default bands: v1 (0.8/0.7) vs v2 (0.8/0.5 on the
    raw fit-quality scale)."""
    import asyncio

    from mascope_backend.api.controllers.match.lib.match_aggregate import (
        set_ions_match_category,
    )

    def cats(version):
        monkeypatch.setenv("MASCOPE_MATCH_SCORE_VERSION", str(version))
        df = pd.DataFrame(
            {"match_score": [0.85, 0.5, 0.1], "instrument": ["orbitrap"] * 3}
        )
        out = asyncio.run(set_ions_match_category(df, None))
        return list(out["match_category"])

    assert cats(2) == [2, 1, 0]  # v2 0.8/0.5: 0.85>=0.8; 0.5<=0.5<0.8; 0.1<0.5
    assert cats(1) == [2, 0, 0]  # v1 0.8/0.7: 0.85>=0.8; 0.5<0.7; 0.1<0.7
