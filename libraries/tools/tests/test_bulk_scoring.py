"""Candidates scored in bulk, equal to the rules they replace.

A file's first pass scores some 10^5 candidates, which one at a time costs a
millisecond each: the bulk paths score them on arrays, and are held here to
equal the one-at-a-time rules they replace, candidate for candidate, on random
spectra and on the edges a vectorised match gets wrong first (an empty peak
list, a window past either end, equal m/z values, a tie between two peaks, a
zero on the ion's own line).
"""

import numpy as np
import pandas as pd
import polars as pl
import pytest

from mascope_tools.composition import heuristic_filter
from mascope_tools.composition.heuristic_filter import (
    PATTERN_BASE_SNR,
    PATTERN_REQUIRED_LINES,
    _target_peak,
    envelope_floor_for_peak,
    match_isotopic_pattern,
    predict_isotopes,
    score_ion_envelope,
    score_ion_envelopes,
    score_pattern_v2,
    score_patterns_v2,
)
from mascope_tools.composition.models import PatternScoring
from mascope_tools.composition.scoring import (
    score_candidates,
)


#: Bulk and one-at-a-time sum the weighted mean in different orders.
ROUNDING = 1e-12

IONS = [
    ("C6H5O", -1),
    ("C10H15O4", -1),
    ("C5H11O2", 1),
    ("C2H2BrO2", -1),
    ("CH2Br2Cl", -1),
    ("C12H20NO6", -1),
    ("C8H13O5S", -1),
    ("C3H3ClO2", -1),
    ("C20H31O8", -1),
    ("NO3", -1),
    ("C4H5^NO5", -1),
    ("Br2", -1),
]


def _random_envelope(rng, n):
    # The anchor's abundance is 1 when a caller normalises to it, and the
    # one-envelope function takes any; so do these, a third of the time.
    anchor = 1.0 if rng.random() < 0.67 else rng.uniform(0.2, 3.0)
    pr = np.concatenate([[anchor], rng.uniform(0.0, 2.0, n - 1)])
    oi = np.where(rng.random(n) < 0.7, rng.uniform(0, 1e6, n), 0.0)
    me = rng.normal(0, 2, n)
    me[rng.random(n) < 0.05] = np.nan
    snr = rng.uniform(0.5, 500, n)
    snr[rng.random(n) < 0.2] = np.nan
    return me, oi, snr, pr


class TestManyEnvelopesScoreAsEachDoesAlone:
    """``score_patterns_v2`` against ``score_pattern_v2``, envelope by envelope."""

    @pytest.mark.parametrize("with_snr", [True, False], ids=["snr", "no snr"])
    @pytest.mark.parametrize("sigma", [None, 0.6, 5.0])
    def test_random_envelopes(self, with_snr, sigma):
        rng = np.random.default_rng(7)
        envelopes = [
            _random_envelope(rng, int(rng.integers(1, 30))) for _ in range(300)
        ]
        # The edges: an absent anchor, an anchor with no mass error, a lone
        # line, and every abundance unusable.
        envelopes[0][1][0] = 0.0
        envelopes[1][0][0] = np.nan
        envelopes[2] = tuple(part[:1] for part in envelopes[2])
        envelopes[3][3][:] = 0.0

        alone = [
            score_pattern_v2(
                me, oi, snr if with_snr else None, pr, sigma_ppm=sigma, k_detect=3.0
            )
            for me, oi, snr, pr in envelopes
        ]
        offsets = np.concatenate([[0], np.cumsum([len(e[3]) for e in envelopes])])
        together = score_patterns_v2(
            *(np.concatenate([e[part] for e in envelopes]) for part in (0, 1)),
            np.concatenate([e[2] for e in envelopes]) if with_snr else None,
            np.concatenate([e[3] for e in envelopes]),
            offsets,
            sigma_ppm=sigma,
            k_detect=3.0,
        )

        assert together == pytest.approx(alone, abs=ROUNDING, rel=0)
        assert together[0] == 0.0 and together[1] == 0.0

    def test_an_empty_envelope_scores_zero_among_the_others(self):
        me, oi, snr, pr = _random_envelope(np.random.default_rng(1), 6)
        oi[0] = 1e5
        me[0] = 0.1

        together = score_patterns_v2(me, oi, snr, pr, [0, 0, 6, 6])

        assert together[0] == 0.0 and together[2] == 0.0
        assert together[1] == pytest.approx(
            score_pattern_v2(me, oi, snr, pr), abs=ROUNDING
        )

    def test_arrays_that_disagree_with_the_offsets_are_refused(self):
        with pytest.raises(ValueError, match="one entry per predicted"):
            score_patterns_v2(np.zeros(3), np.zeros(3), None, np.ones(3), [0, 4])


def _spectrum(rng, *, with_snr=True, duplicates=False):
    """A peak list holding the envelopes of a few ions among random peaks."""
    mz, intensity = [], []
    for index in rng.choice(len(IONS), 5):
        formula, charge = IONS[index]
        lines, abundances, _ = predict_isotopes(formula, charge, None, 1e-4)
        lines, abundances = np.asarray(lines), np.asarray(abundances)
        if not lines.size:
            continue
        mz.extend(lines * (1 + rng.normal(0, 1e-6, lines.size)))
        scale = 10 ** rng.uniform(3, 7)
        intensity.extend(
            abundances / abundances.max() * scale * rng.uniform(0.7, 1.3, lines.size)
        )
    mz.extend(rng.uniform(40, 500, 300))
    intensity.extend(10 ** rng.uniform(2, 6, 300))
    mz, intensity = np.asarray(mz), np.asarray(intensity)
    if duplicates:
        mz = np.concatenate([mz, mz[:5]])
        intensity = np.concatenate([intensity, intensity[:5] * 0.5])
    order = np.argsort(mz, kind="stable")
    snr = None
    if with_snr:
        snr = intensity / 10 ** rng.uniform(1, 3)
        snr[rng.random(snr.size) < 0.1] = np.nan
        snr = snr[order]
    return mz[order], intensity[order], snr


def _scorings(rng):
    resolving_power = float(rng.choice([8000.0, 60000.0]))
    return PatternScoring(
        sigma_ppm=rng.choice([None, 0.6, 3.0]),
        mu_ppm=float(rng.normal(0, 0.5)),
        mz_tolerance_ppm=float(rng.choice([3.0, 5.0, 15.0])),
        abundance_floor=float(rng.choice([1e-5, 1e-3, 0.01])),
        resolution=(lambda mz: resolving_power) if rng.random() < 0.4 else None,
    )


def _assert_same(alone, together):
    score, required, isotopes = alone
    bulk_score, bulk_required, bulk_isotopes = together
    assert bulk_score == pytest.approx(score, abs=ROUNDING, rel=0)
    assert bool(bulk_required) is required
    assert set(bulk_isotopes) == set(isotopes)
    for key, value in isotopes.items():
        if key == "labels":
            assert list(bulk_isotopes[key]) == list(value)
        else:
            np.testing.assert_array_equal(np.asarray(bulk_isotopes[key]), value)


class TestManyCandidatesMatchAsEachDoesAlone:
    """``score_ion_envelopes`` against ``score_ion_envelope``, line for line."""

    @pytest.mark.parametrize("seed", range(6))
    def test_random_spectra(self, seed):
        rng = np.random.default_rng(seed)
        for _ in range(25):
            mzs, intensities, snrs = _spectrum(
                rng, with_snr=rng.random() < 0.7, duplicates=rng.random() < 0.3
            )
            scoring = _scorings(rng)
            picked = [IONS[i] for i in rng.choice(len(IONS), 8)]
            floors = rng.choice([scoring.abundance_floor, 1e-3, 0.01], len(picked))
            together = score_ion_envelopes(
                [f for f, _ in picked],
                [c for _, c in picked],
                floors,
                mzs,
                intensities,
                snrs,
                scoring,
            )
            for position, ((formula, charge), floor) in enumerate(zip(picked, floors)):
                _assert_same(
                    score_ion_envelope(
                        formula, charge, floor, mzs, intensities, snrs, scoring
                    ),
                    tuple(part[position] for part in together),
                )

    @pytest.mark.parametrize(
        "mzs",
        [
            np.array([]),
            np.array([50.0]),
            np.array([900.0]),
            np.array([92.9957, 92.9957, 92.9957]),
        ],
        ids=["no peaks", "all below", "all above", "equal m/z"],
    )
    def test_the_edges_of_the_peak_list(self, mzs):
        intensities = np.full(mzs.size, 1e5)
        scoring = PatternScoring(sigma_ppm=0.6)
        ions = [("C6H5O", -1), ("C2H2BrO2", -1)]

        together = score_ion_envelopes(
            [f for f, _ in ions],
            [c for _, c in ions],
            1e-3,
            mzs,
            intensities,
            None,
            scoring,
        )

        for position, (formula, charge) in enumerate(ions):
            _assert_same(
                score_ion_envelope(
                    formula, charge, 1e-3, mzs, intensities, None, scoring
                ),
                tuple(part[position] for part in together),
            )

    def test_a_tie_between_two_peaks_goes_to_the_lower_one(self):
        # argmin over a window takes the first of two equally near peaks.
        own = float(predict_isotopes("C6H5O", -1, None, 0.01)[0][0])
        mzs = np.array([own - 1e-4, own + 1e-4])
        intensities = np.array([1e5, 3e5])

        alone = score_ion_envelope(
            "C6H5O", -1, 0.01, mzs, intensities, None, PatternScoring()
        )
        together = score_ion_envelopes(
            ["C6H5O"], [-1], [0.01], mzs, intensities, None, PatternScoring()
        )

        _assert_same(alone, tuple(part[0] for part in together))
        assert alone[2]["masses"][0] == mzs[0]

    def test_a_zero_on_the_ions_own_line_matches_no_isotopologue(self):
        lines, abundances, _ = predict_isotopes("C10H15O4", -1, None, 0.001)
        mzs = np.asarray(lines)
        order = np.argsort(mzs)
        mzs = mzs[order]
        intensities = (np.asarray(abundances)[order] * 1e6).astype(np.int64)
        intensities[np.argmin(np.abs(mzs - lines[0]))] = 0

        alone = score_ion_envelope(
            "C10H15O4", -1, 0.001, mzs, intensities, None, PatternScoring()
        )
        together = score_ion_envelopes(
            ["C10H15O4"], [-1], [0.001], mzs, intensities, None, PatternScoring()
        )

        _assert_same(alone, tuple(part[0] for part in together))
        assert not alone[2]["masses"][1:].any()

    def test_an_ion_with_no_envelope_scores_zero(self):
        together = score_ion_envelopes(
            ["Xx", "C6H5O"],
            [-1, -1],
            [0.01, 0.01],
            np.array([92.0]),
            np.array([1.0]),
            None,
            PatternScoring(),
        )

        assert together[0][0] == 0.0 and not together[1][0]
        assert together[2][0]["masses"] == []

    def test_each_envelope_is_predicted_once(self, monkeypatch):
        calls = []
        predict = heuristic_filter.predict_resolved_isotopes

        def counting(*args, **kwargs):
            calls.append(args[:3])
            return predict(*args, **kwargs)

        monkeypatch.setattr(heuristic_filter, "predict_resolved_isotopes", counting)
        score_ion_envelopes(
            ["C6H5O", "C6H5O", "C6H5O", "NO3"],
            [-1, -1, -1, -1],
            [0.01, 0.01, 0.001, 0.01],
            np.array([92.0]),
            np.array([1.0]),
            None,
            PatternScoring(),
        )

        assert sorted(calls) == sorted(
            [("C6H5O", -1, 0.01), ("C6H5O", -1, 0.001), ("NO3", -1, 0.01)]
        )


class TestScoreCandidates:
    """The public bulk scorer: per-candidate depth, any order, a frame back."""

    @staticmethod
    def _peaks(rng):
        mzs, intensities, snrs = _spectrum(rng)
        return pd.DataFrame(
            {"mz": mzs, "intensity": intensities, "signal_to_noise": snrs}
        )

    def test_each_candidate_is_scored_as_alone_at_its_own_peaks_depth(self):
        rng = np.random.default_rng(3)
        peaks = self._peaks(rng)
        scoring = PatternScoring(sigma_ppm=0.6, abundance_floor=1e-5)
        targets = rng.choice(peaks["mz"].to_numpy(), 4)
        candidates = pd.DataFrame(
            [
                {"ion": f"{formula}{'+' if charge > 0 else '-'}", "observed_mass": mass}
                for mass in targets
                for formula, charge in IONS[:5]
            ],
            index=pd.RangeIndex(100, 120),
        )

        scored = score_candidates(candidates, peaks, scoring)

        mzs = peaks["mz"].to_numpy()
        intensities = peaks["intensity"].to_numpy()
        snrs = peaks["signal_to_noise"].to_numpy()
        faintest = float(intensities[intensities > 0].min())
        assert list(scored.index) == list(candidates.index)
        for index, candidate in candidates.iterrows():
            base, snr = _target_peak([candidate.to_dict()], mzs, intensities, snrs)
            floor = envelope_floor_for_peak(base, snr, faintest, scoring)
            score, required, isotopes = score_ion_envelope(
                candidate["ion"][:-1],
                1 if candidate["ion"][-1] == "+" else -1,
                floor,
                mzs,
                intensities,
                snrs,
                scoring,
            )
            row = scored.loc[index]
            assert row["envelope_floor"] == floor
            assert row["fit_score"] == pytest.approx(score, abs=ROUNDING, rel=0)
            assert row[PATTERN_REQUIRED_LINES] == required
            if snr is not None and np.isfinite(snr) and snr > 0:
                assert row[PATTERN_BASE_SNR] == pytest.approx(snr)
            else:
                assert np.isnan(row[PATTERN_BASE_SNR])
            assert row["matched_lines"] == int(np.count_nonzero(isotopes["masses"]))
            assert list(row["labels"]) == list(isotopes["labels"])
            for column, key in [
                ("predicted_mz", "predicted_masses"),
                ("predicted_rel", "predicted_intensities"),
                ("observed_mz", "masses"),
                ("mass_error_ppm", "mass_errors_ppm"),
                ("intensity_error", "intensity_errors"),
            ]:
                np.testing.assert_array_equal(row[column], isotopes[key])

    def test_the_peak_lists_order_does_not_matter(self):
        lines, abundances, _ = predict_isotopes("C10H15O4", -1, None, 1e-3)
        rng = np.random.default_rng(4)
        peaks = pd.DataFrame(
            {
                "mz": np.concatenate([lines, rng.uniform(100, 400, 200)]),
                "intensity": np.concatenate(
                    [np.asarray(abundances) * 1e6, rng.uniform(1e2, 1e4, 200)]
                ),
            }
        )
        candidates = [
            {"ion": "C10H15O4-", "observed_mass": float(lines[0])},
            {"ion": "C6H5O-"},
        ]

        in_order = score_candidates(
            candidates, peaks.sort_values("mz"), PatternScoring(sigma_ppm=0.6)
        )
        shuffled = score_candidates(
            candidates,
            peaks.sample(frac=1, random_state=0),
            PatternScoring(sigma_ppm=0.6),
        )

        assert in_order["fit_score"].iloc[0] > 0.9
        pd.testing.assert_frame_equal(
            in_order.drop(columns=list(in_order.columns[5:])),
            shuffled.drop(columns=list(shuffled.columns[5:])),
        )

    def test_a_polars_peak_list_scores_as_a_pandas_one(self):
        peaks = self._peaks(np.random.default_rng(5))
        candidates = [{"ion": "C10H15O4-", "observed_mass": float(peaks["mz"].iloc[3])}]

        assert score_candidates(candidates, pl.from_pandas(peaks))["fit_score"].equals(
            score_candidates(candidates, peaks)["fit_score"]
        )

    def test_a_candidate_with_no_peak_is_predicted_to_the_floor(self):
        peaks = self._peaks(np.random.default_rng(6))
        scoring = PatternScoring(abundance_floor=1e-4)

        scored = score_candidates([{"ion": "C6H5O-"}], peaks, scoring)

        assert scored["envelope_floor"].iloc[0] == 1e-4
        assert np.isnan(scored[PATTERN_BASE_SNR].iloc[0])

    def test_no_candidates_is_an_empty_frame(self):
        scored = score_candidates(
            pd.DataFrame(columns=["ion"]), pd.DataFrame({"mz": [], "intensity": []})
        )

        assert scored.empty
        assert "fit_score" in scored.columns

    def test_the_finder_predicts_a_peaks_readings_to_that_peaks_depth(self):
        # A weak peak's envelope stops where its noise does, however deep the
        # sample's floor would let it go: the finder hands the bulk scorer the
        # peak's depth, not the floor.
        lines, abundances, _ = predict_isotopes("C10H15O4", -1, None, 1e-5)
        peaks = pl.DataFrame(
            {
                "mz": np.asarray(lines, dtype=float),
                "intensity": np.asarray(abundances, dtype=float) * 1e4,
                "signal_to_noise": np.asarray(abundances, dtype=float) * 20.0,
            }
        ).sort("mz")
        scoring = PatternScoring(sigma_ppm=0.6, abundance_floor=1e-5)
        candidate = {
            "formula": "C10H16O4",
            "ion": "C10H15O4-",
            "observed_mass": float(lines[0]),
            "composition_error_ppm": 0.0,
        }

        _, isotopes = match_isotopic_pattern([candidate], peaks, scoring)

        depth = envelope_floor_for_peak(
            1e4 * float(np.max(abundances)),
            20.0 * float(np.max(abundances)),
            float(peaks["intensity"].min()),
            scoring,
        )
        assert depth > 10 * scoring.abundance_floor
        assert len(isotopes[0]["predicted_masses"]) == len(
            predict_isotopes("C10H15O4", -1, None, depth)[0]
        )

    def test_the_finder_ranks_on_the_same_numbers(self):
        # One peak's readings, scored by the finder and by the bulk scorer:
        # the finder's policy (election, cap, order) aside, the numbers agree.
        rng = np.random.default_rng(8)
        peaks = self._peaks(rng)
        target = float(peaks["mz"].iloc[int(np.argmax(peaks["intensity"]))])
        candidates = [
            {
                "formula": f"F{i}",
                "ion": f"{formula}{'+' if charge > 0 else '-'}",
                "observed_mass": target,
                "composition_error_ppm": float(i),
            }
            for i, (formula, charge) in enumerate(IONS)
        ]
        scoring = PatternScoring(sigma_ppm=0.6)

        ranked, _ = match_isotopic_pattern(
            [dict(c) for c in candidates], pl.from_pandas(peaks), scoring
        )
        scored = score_candidates(candidates, peaks, scoring)

        by_formula = dict(zip([c["formula"] for c in candidates], scored["fit_score"]))
        for reading in ranked:
            assert reading["isotopic_pattern_score"] == pytest.approx(
                by_formula[reading["formula"]], abs=ROUNDING, rel=0
            )
