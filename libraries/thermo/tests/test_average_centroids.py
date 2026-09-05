"""Averaged centroids when a peak's labels move between scans.

The OpenTFRaw backend averages centroids by pooling every scan's label peaks
and ppm-binning them. Two things move a peak's label m/z between scans. The
scan's own frequency->m/z calibration (Conversion Parameters B/C) steps by a
few ppm at once when a lock mass engages part-way through a file, which at low
m/z is wider than both the ppm bin and half a FWHM; the labels are keyed by
physical frequency against that. And a dominant ion's measured position itself
can jitter by about its own width with the calibration steady, so that each
scan holds one centroid landing in one of two bins; the scan-exclusive merge
handles that. These tests drive ``average_centroids`` with a fake reader whose
scans carry such a step or jitter.
"""

import numpy as np
import pytest

from mascope_thermo.backend import OpenTFRawBackend


# A real Orbitrap calibration (m/z = B/f^2 + C/f^4) and a resolution at which
# the FWHM at m/z 42 is ~4 ppm, so half a FWHM is below a 2.5 ppm step. At the
# high resolution the FWHM is ~1 ppm, so the step is also beyond the reach of
# the scan-exclusive merge and only the frequency key can bridge it.
B_REF = 169_413_136.0
C_REF = 25_075_658.0
RESOLUTION = 249_000.0
HIGH_RESOLUTION = 1_000_000.0
STEP_PPM = 2.5
SCANS = list(range(1, 8))


class _FakeRaw:
    """The slice of ``opentfraw.RawFile`` that ``average_centroids`` touches:
    centroid labels and trailer parameters per scan, and a profile (empty
    here, which keeps the profile-apex height refinement out of the picture).
    """

    def __init__(self, scans: dict[int, dict]):
        self._scans = scans

    def centroid_labels(self, scan_number):
        return self._scans[scan_number]["labels"]

    def scan_parameters(self, scan_number):
        return self._scans[scan_number]["params"]

    def profile(self, scan_number):
        return np.array([]), np.array([])


def _mz_on(b: float, c: float, f: np.ndarray) -> np.ndarray:
    return b / f**2 + c / f**4


def _labels(mzs, intensity=1e5, sn=4000.0, resolution=RESOLUTION) -> dict:
    mzs = np.asarray(mzs, dtype=float)
    return {
        "mz": mzs,
        "intensity": np.full(mzs.size, intensity),
        "resolution": np.full(mzs.size, resolution),
        "signal_to_noise": np.full(mzs.size, sn),
    }


def _add_label(scans: dict[int, dict], n: int, mz: float, intensity: float) -> None:
    """Append one extra label to scan ``n``, keeping the other columns."""
    labels = scans[n]["labels"]
    labels["mz"] = np.append(labels["mz"], mz)
    labels["intensity"] = np.append(labels["intensity"], intensity)
    labels["resolution"] = np.append(labels["resolution"], labels["resolution"][-1])
    labels["signal_to_noise"] = np.append(labels["signal_to_noise"], 10.0)


def _stepped_scans(peaks_mz, with_params=True, resolution=RESOLUTION) -> dict:
    """Seven scans of the same ions. Scans 1-3 sit on a calibration
    ``STEP_PPM`` above the reference (the lock mass not yet found), scans 4-7
    on the reference; ``peaks_mz`` are the ions' m/z on the reference."""
    freqs = OpenTFRawBackend._mz_to_freq(np.asarray(peaks_mz, float), B_REF, C_REF)
    scans = {}
    for n in SCANS:
        scale = 1 + STEP_PPM / 1e6 if n <= 3 else 1.0
        b, c = B_REF * scale, C_REF * scale
        scans[n] = {
            "labels": _labels(_mz_on(b, c, freqs), resolution=resolution),
            "params": (
                {"Conversion Parameter B:": b, "Conversion Parameter C:": c}
                if with_params
                else {}
            ),
        }
    return scans


# The jitter case: the calibration is the same in every scan, so the shift is
# in the ion's measured position itself. A resolution at which the FWHM at m/z
# 61 is ~2.9 ppm, so a 3.2 ppm swing is 1.1 FWHM: beyond the half-FWHM merge.
JITTER_RESOLUTION = 341_000.0
JITTER_PPM = 3.2
JITTER_SCANS = list(range(1, 9))
HIGH_SCANS = (1, 2, 5, 6)  # the scans in which the ion reads high


def _jitter_scans(peaks: list[tuple[float, str]], high_scans=HIGH_SCANS) -> dict:
    """Eight scans on one calibration. Each peak is ``(mz, mode)``: ``"jitter"``
    puts it ``JITTER_PPM`` higher in ``high_scans`` than elsewhere, ``"steady"``
    keeps it put in every scan, ``"high"`` / ``"low"`` make it appear only in
    the high scans / only in the others."""
    params = {"Conversion Parameter B:": B_REF, "Conversion Parameter C:": C_REF}
    scans = {}
    for n in JITTER_SCANS:
        high = n in high_scans
        mzs = []
        for mz, mode in peaks:
            if mode == "jitter":
                mzs.append(mz * (1 + JITTER_PPM / 1e6) if high else mz)
            elif mode == "steady" or (mode == "high") == high:
                mzs.append(mz)
        labels = _labels(mzs)
        labels["resolution"] = np.full(len(mzs), JITTER_RESOLUTION)
        scans[n] = {"labels": labels, "params": params}
    return scans


def _fwhm_apart(mz: float, fwhms: float) -> float:
    return mz * (1 + fwhms * (1e6 / JITTER_RESOLUTION) / 1e6)


def _backend(scans: dict[int, dict]) -> OpenTFRawBackend:
    backend = OpenTFRawBackend("unused.raw")
    backend._raw = _FakeRaw(scans)
    return backend


def _all_labels(scans: dict[int, dict]) -> np.ndarray:
    return np.concatenate([scans[n]["labels"]["mz"] for n in SCANS])


def _scans_of(per_scan: list[list[tuple[float, float]]]) -> dict:
    """One calibration throughout; ``per_scan[i]`` gives the ``(mz, intensity)``
    labels of scan ``i + 1``, so a peak's scans and per-scan intensity can be
    set one at a time. The number of scans is the length of ``per_scan``."""
    params = {"Conversion Parameter B:": B_REF, "Conversion Parameter C:": C_REF}
    scans = {}
    for n, peaks in enumerate(per_scan, start=1):
        labels = _labels([mz for mz, _ in peaks], resolution=JITTER_RESOLUTION)
        labels["intensity"] = np.array([i for _, i in peaks], dtype=float)
        scans[n] = {"labels": labels, "params": params}
    return scans


def _stepped_scans_of(present: list[list[float]], step_ppm: float, resolution: float):
    """Seven scans holding the ions listed per scan, given as reference-frame
    m/z. Scans 1-3 sit on a calibration ``step_ppm`` above the reference; B and
    C scale together there, so a label is written scaled by the same factor."""
    scans = {}
    for n, mz_ref in zip(SCANS, present):
        scale = 1 + step_ppm / 1e6 if n <= 3 else 1.0
        scans[n] = {
            "labels": _labels(np.asarray(mz_ref, float) * scale, resolution=resolution),
            "params": {
                "Conversion Parameter B:": B_REF * scale,
                "Conversion Parameter C:": C_REF * scale,
            },
        }
    return scans


@pytest.mark.parametrize("resolution", [RESOLUTION, HIGH_RESOLUTION])
def test_a_peak_split_by_a_calibration_step_is_one_centroid(resolution):
    scans = _stepped_scans([42.0086], resolution=resolution)
    labels = _all_labels(scans)
    # The step really is wider than the bin: as written, the labels form two
    # groups 2.5 ppm apart.
    assert (labels.max() - labels.min()) / labels.min() * 1e6 == pytest.approx(
        STEP_PPM, abs=0.01
    )

    masses, intensities, resolutions, sn = _backend(scans).average_centroids(
        SCANS, ppm=1
    )

    assert masses.size == 1
    # The m/z is the intensity-weighted mean of the labels as written, which is
    # what a merged peak has always reported.
    assert masses[0] == pytest.approx(labels.mean(), rel=1e-12)
    assert intensities[0] == pytest.approx(7e5)
    assert resolutions[0] == pytest.approx(resolution)
    # Present in all seven scans: mean S:N * n / sqrt(N).
    assert sn[0] == pytest.approx(4000.0 * 7 / np.sqrt(7))


def test_resolved_neighbours_stay_apart_across_the_step():
    # Two ions 1.5 FWHM (6 ppm) apart in every scan.
    scans = _stepped_scans([42.0086, 42.0086 * (1 + 6.0 / 1e6)])

    masses, intensities, _, _ = _backend(scans).average_centroids(SCANS, ppm=1)

    assert masses.size == 2
    assert np.all(np.diff(masses) > 0)
    np.testing.assert_allclose(intensities, [7e5, 7e5])


def test_without_conversion_parameters_the_step_is_beyond_the_merges():
    # A reader without B/C bins the labels as written: the step yields one bin
    # per calibration. At 2.5 FWHM the gap is beyond both merges, so the two
    # bins stay -- the case the frequency key exists for.
    scans = _stepped_scans([42.0086], with_params=False, resolution=HIGH_RESOLUTION)

    masses, intensities, _, _ = _backend(scans).average_centroids(SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(np.sort(intensities), [3e5, 4e5])


def test_without_conversion_parameters_a_step_is_left_to_the_key():
    # At 0.6 FWHM the same two bins share no scan, cover all seven and hold
    # three and four of them, but a step is a hand-over in time: the side
    # changes once, between scans 3 and 4, so the scan-exclusive merge leaves
    # the pair alone and only the frequency key rejoins it.
    scans = _stepped_scans([42.0086], with_params=False)

    masses, intensities, _, _ = _backend(scans).average_centroids(SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(np.sort(intensities), [3e5, 4e5])


def test_labels_on_one_calibration_agree_across_scans():
    scans = _stepped_scans([42.0086, 100.0])
    parts = [scans[n]["labels"]["mz"] for n in SCANS]

    keys = _backend(scans)._labels_on_one_calibration(SCANS, parts)

    assert keys is not None and len(keys) == len(parts)
    # The same ion keys to the same value from every scan, whatever the scan's
    # calibration did to its label.
    for key in keys:
        np.testing.assert_allclose(key, keys[0], rtol=1e-12)
    # A scan on the reference calibration is passed through unchanged.
    assert any(key is part for key, part in zip(keys, parts))


def test_labels_on_one_calibration_needs_every_scan_to_carry_parameters():
    scans = _stepped_scans([42.0086])
    scans[5]["params"] = {}
    parts = [scans[n]["labels"]["mz"] for n in SCANS]

    assert _backend(scans)._labels_on_one_calibration(SCANS, parts) is None


def test_a_peak_that_jitters_between_scans_is_one_centroid():
    scans = _jitter_scans([(61.0397, "jitter")])
    labels = np.concatenate([scans[n]["labels"]["mz"] for n in JITTER_SCANS])

    masses, intensities, _, sn = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 1
    assert masses[0] == pytest.approx(labels.mean(), rel=1e-12)
    assert intensities[0] == pytest.approx(8e5)
    assert sn[0] == pytest.approx(4000.0 * 8 / np.sqrt(8))


def test_a_doublet_present_in_the_same_scans_stays_two():
    # Two ions 1.1 FWHM apart, both in every scan: the instrument resolved them
    # within a scan, so their bins share scans and the exclusive rule does not
    # apply, however close they are.
    scans = _jitter_scans([(61.0397, "steady"), (_fwhm_apart(61.0397, 1.1), "steady")])

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [8e5, 8e5])


def test_exclusive_merge_is_bounded_by_the_window():
    # Two ions in complementary scans but 2 FWHM apart stay separate.
    scans = _jitter_scans([(61.0397, "high"), (_fwhm_apart(61.0397, 2.0), "low")])

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [4e5, 4e5])


def test_exclusive_merge_tracks_the_whole_cluster():
    # A (high scans) and B (low scans, 1.2 FWHM up) merge. C, another 1.2 FWHM
    # up and again in the high scans, is disjoint from B alone but not from
    # the merged cluster, so it stays a peak of its own.
    a = 61.0397
    scans = _jitter_scans(
        [(a, "high"), (_fwhm_apart(a, 1.2), "low"), (_fwhm_apart(a, 2.4), "high")]
    )

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [8e5, 4e5])


def test_exclusive_merge_tracks_the_cluster_on_the_right_too():
    # B (low scans, 1.2 FWHM up from A) and C (0.4 FWHM above B, in the high
    # scans) merge unconditionally, being closer than half a FWHM. Weighed as
    # that pair, the right side shares the high scans with A, so A stays a
    # peak of its own; weighed as B alone it would join A, and C would follow.
    a = 61.0397
    scans = _jitter_scans(
        [(a, "high"), (_fwhm_apart(a, 1.2), "low"), (_fwhm_apart(a, 1.6), "high")]
    )

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [4e5, 8e5])


def test_two_ions_that_hand_over_in_time_stay_two():
    # One ion in scans 1-4, another 1.2 FWHM up in scans 5-8: the sides share
    # no scan, cover every scan and hold half each, but the side changes only
    # once, between scans 4 and 5. Joining them would report a centroid about
    # 2 ppm off both ions, so they stay two peaks.
    a = 61.0397
    b = _fwhm_apart(a, 1.2)
    scans = _scans_of([[(a, 1e5)]] * 4 + [[(b, 1e5)]] * 4)

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(masses, [a, b], rtol=1e-12)
    np.testing.assert_allclose(intensities, [4e5, 4e5])


def test_exclusive_merge_survives_a_stray_label():
    # A weak stray label in one of the high scans lands in the low bin, so the
    # two bins are no longer strictly disjoint. It carries a twentieth of the
    # bin, well under the allowed share, so the jittering ion still merges.
    scans = _jitter_scans([(61.0397, "jitter")])
    _add_label(scans, HIGH_SCANS[0], 61.0397, 2e4)

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 1
    assert intensities[0] == pytest.approx(8.2e5)


def test_exclusive_merge_needs_the_sides_to_cover_most_scans():
    # The same alternation confined to four of the eight scans covers half of
    # them, under the required share: two labels from different scans are
    # exclusive by construction, so a jitter split of a transient ion is left
    # as it was rather than risk joining noise. Two scans a side is also under
    # the per-side floor, so both gates hold this pair apart;
    # test_coverage_alone_keeps_overlapping_sides_apart isolates the coverage
    # one.
    scans = _jitter_scans([(61.0397, "jitter")])
    for n in (5, 6, 7, 8):
        scans[n]["labels"] = _labels([])

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [2e5, 2e5])


def test_exclusive_merge_needs_both_sides_to_be_substantial():
    # The ion reads high in only two of the eight scans: the sides cover every
    # scan and share none, but a quarter of the scans is under the required
    # share for a side. The wobbling fragments beside an intense peak look
    # like this, present in a couple of scans, and stay the separate peaks
    # Thermo also reports.
    scans = _jitter_scans([(61.0397, "jitter")], high_scans=(1, 2))

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [6e5, 2e5])


def test_two_scans_cannot_establish_an_alternation():
    # Two labels 1 FWHM apart, one in each of two scans. They share no scan,
    # cover both and hold half each, but two scans can show only one side
    # change, under the two the rule asks for, so a pair of unrelated noise
    # labels on a window this short is never read as one alternating ion. The
    # floor of two scans a side holds them apart as well; with the alternation
    # count in place it no longer decides a case of its own.
    a = 61.0397
    scans = _scans_of([[(a, 1e5)], [(_fwhm_apart(a, 1.0), 1e5)]])

    masses, intensities, _, _ = _backend(scans).average_centroids([1, 2], ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [1e5, 1e5])


def test_coverage_alone_keeps_overlapping_sides_apart():
    # Two sides alternating over four scans, each carrying a hundredth of its
    # intensity in the other's scans: the intensity-weighted overlap test
    # passes, each side clears the per-side floor with four scans, and the
    # dominant side changes three times. Between them they occupy four of the
    # eight scans, under the required coverage, which is the only gate left
    # to keep them apart.
    a = 61.0397
    b = _fwhm_apart(a, 1.0)
    scans = _scans_of(
        [
            [(a, 1e5), (b, 1e3)],
            [(a, 1e3), (b, 1e5)],
            [(a, 1e5), (b, 1e3)],
            [(a, 1e3), (b, 1e5)],
            [],
            [],
            [],
            [],
        ]
    )

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [2.02e5, 2.02e5])


def test_bins_out_of_label_order_are_sorted_before_the_merge():
    # Two ions 2 ppm apart in frequency, each held by one side of a 6 ppm
    # calibration step: the lower-frequency one appears only in the stepped
    # scans, so its labels are written 6 ppm high and the two bins leave the
    # binning in key order with their label means the other way round. Read in
    # that order the gap between them is negative, which is below every merge
    # threshold and would collapse the pair into one peak.
    low, high = 100.0, 100.0 * (1 + 2.0 / 1e6)
    scans = _stepped_scans_of(
        [[low], [low], [low], [high], [high], [high], [high]],
        step_ppm=6.0,
        resolution=HIGH_RESOLUTION,
    )

    masses, intensities, _, _ = _backend(scans).average_centroids(SCANS, ppm=1)

    assert masses.size == 2
    assert np.all(np.diff(masses) > 0)
    # The ion in the four reference scans reads lower, its label untouched by
    # the step; the one in the three stepped scans reads 6 ppm above its own.
    np.testing.assert_allclose(masses, [high, low * (1 + 6.0 / 1e6)], rtol=1e-12)
    np.testing.assert_allclose(intensities, [4e5, 3e5])


def test_a_window_with_no_labels_is_empty_not_an_error():
    # Scans that carry conversion parameters but hold no label -- a blank
    # stretch of an acquisition, or a window that falls between two -- take
    # the frequency-keyed path with nothing in it.
    scans = _scans_of([[], [], []])

    masses, intensities, resolutions, sn = _backend(scans).average_centroids(
        [1, 2, 3], ppm=1
    )

    assert masses.size == 0
    assert intensities.size == resolutions.size == sn.size == 0


def test_a_window_whose_labels_are_all_dropped_is_empty():
    # Labels whose resolution or S:N is not finite are dropped before the
    # binning, which can leave a window that did hold labels with none.
    scans = _scans_of([[(100.0, 1e5)], [(100.0, 1e5)], [(100.0, 1e5)]])
    for n in (1, 2, 3):
        scans[n]["labels"]["signal_to_noise"] = np.array([np.nan])

    masses, intensities, _, _ = _backend(scans).average_centroids([1, 2, 3], ppm=1)

    assert masses.size == 0
    assert intensities.size == 0


def test_exclusive_merge_needs_a_substantial_side_however_it_alternates():
    # A fragment in two of the eight scans beside an ion holding the other
    # six. The sides share no scan, cover every one, and the dominant side
    # changes three times, so the per-side floor is the only gate left --
    # which is what the wobbling fragments beside an intense peak look like,
    # and Thermo reports them as the separate peaks they are.
    a = 61.0397
    b = _fwhm_apart(a, 1.0)
    scans = _scans_of([[(a, 1e5)] if n in (0, 4) else [(b, 1e5)] for n in range(8)])

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [2e5, 6e5])


def test_exclusive_merge_weighs_a_cluster_merged_before_it():
    # A and B are 0.4 FWHM apart and merge unconditionally; C sits 1.2 FWHM
    # above B, sharing every scan with A and none with B. Weighed as the A+B
    # cluster the pair overlaps and C stays a peak of its own; weighed as B
    # alone it reads as a clean alternation and all three would collapse into
    # one. Only the walk back over the merged pair tells them apart.
    a = 61.0397
    mid, top = _fwhm_apart(a, 0.4), _fwhm_apart(a, 1.6)
    scans = _scans_of(
        [[(a, 1e5), (top, 1e5)] if n % 2 == 0 else [(mid, 1e5)] for n in range(8)]
    )

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(intensities, [8e5, 4e5])


def test_two_side_changes_are_enough_to_merge():
    # The threshold is two changes, one more than a hand-over shows. An ion
    # reading high in scans 3 to 5 and low in the rest changes side exactly
    # twice, which is the shallowest alternation the rule accepts.
    a = 61.0397
    b = _fwhm_apart(a, 1.0)
    scans = _scans_of([[(b, 1e5)] if n in (2, 3, 4) else [(a, 1e5)] for n in range(8)])

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 1
    assert intensities[0] == pytest.approx(8e5)


def test_a_pair_after_a_wide_gap_is_weighed_on_its_own_cluster():
    # Two exclusive pairs with a gap wider than the window between them. A and
    # B share every scan and stay apart; C and D alternate cleanly and merge.
    # The second pair has to be weighed from the cluster it belongs to --
    # carrying the first pair's forward would test D against B, which holds
    # every one of D's scans, and leave C and D apart.
    a = 61.0397
    b = _fwhm_apart(a, 1.0)
    c, d = _fwhm_apart(a, 4.0), _fwhm_apart(a, 5.0)
    high = (0, 1, 4, 5)
    scans = _scans_of(
        [
            [(a, 1e5), (b, 2e5)] + ([(c, 1e5)] if n in high else [(d, 1e5)])
            for n in range(8)
        ]
    )

    masses, intensities, _, _ = _backend(scans).average_centroids(JITTER_SCANS, ppm=1)

    assert masses.size == 3
    np.testing.assert_allclose(intensities, [8e5, 1.6e6, 8e5])
