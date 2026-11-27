"""The one line model (MAS-977): one predictor, one matcher, one line check.

The unit tests pin the four questions the module answers. The import test
holds every reader of an ion's isotope lines to it: a reader that imports a
predictor, a matcher or a check from anywhere else, or calls IsoSpec itself,
fails here.
"""

import ast
from pathlib import Path

import numpy as np
import pytest

from mascope_tools.composition import line_model
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.line_model import (
    DETECT_SNR_K,
    REL_DETECT_NO_SNR,
    TRACKING_WIDTHS,
    check_line,
    detect_threshold,
    height_deviation,
    height_within,
    line_detectable,
    line_groups,
    nearest_peak,
    nearest_peaks,
    predict_resolved_isotopes,
    resolve_lines,
    tracks,
)


REPO = Path(__file__).resolve().parents[3]
LIBRARY = REPO / "libraries/tools/src/mascope_tools/composition"
BACKEND = REPO / "server/backend/src/mascope_backend/api"
PEAK_ASSIGNMENTS = BACKEND / "new/peak_assignments"

#: Every reader of an ion's isotope lines the brief names, and the modules that
#: predict lines for one: the fit score, the composition search, the batch's
#: isotope readers, the reagent claims, the target library, Stage A and the
#: seeded chain, the chart, and the tiering pass's rules with the passes they
#: hand lines to.
READERS = {
    "fit score": LIBRARY / "heuristic_filter.py",
    "finder": LIBRARY / "finder.py",
    "diagnostic_isotopologues": LIBRARY / "diagnostic_isotopologues.py",
    "merged_isotopologues": LIBRARY / "merged_isotopologues.py",
    "batch_checks": LIBRARY / "batch_checks.py",
    "reagents": LIBRARY / "reagents.py",
    "target library": BACKEND / "controllers/target/lib/compute/target_ions_compute.py",
    "tiering": PEAK_ASSIGNMENTS / "tiering.py",
    "line_reading": PEAK_ASSIGNMENTS / "line_reading.py",
    "envelope_claims": PEAK_ASSIGNMENTS / "envelope_claims.py",
    "mass_gate": PEAK_ASSIGNMENTS / "mass_gate.py",
    "halogen_twins": PEAK_ASSIGNMENTS / "halogen_twins.py",
    "curation": PEAK_ASSIGNMENTS / "curation.py",
    "envelope_fit": PEAK_ASSIGNMENTS / "envelope_fit.py",
    "seeded_scoring": PEAK_ASSIGNMENTS / "seeded_scoring.py",
    "Stage A": PEAK_ASSIGNMENTS / "service.py",
    "chart": PEAK_ASSIGNMENTS / "visualization.py",
}

#: The readers that predict, match or check a line themselves, so read the
#: line model directly. The others hand the question on: the envelope claims
#: rewrite the rows tiering's claim found, the curation asks tiering's
#: absent-line rule, Stage A and the chart resolve through the target library.
DIRECT = {
    "fit score",
    "finder",
    "diagnostic_isotopologues",
    "merged_isotopologues",
    "batch_checks",
    "reagents",
    "target library",
    "tiering",
    "line_reading",
    "mass_gate",
    "halogen_twins",
    "envelope_fit",
}

#: What the line model answers: a reader imports these from it or not at all.
LINE_MODEL_NAMES = frozenset(line_model.__all__) | {
    "_fold_under_floor",
    "_merged_line",
    "_predict_isotopes_custom",
}

#: IsoSpec's prediction entry points. Its isotope tables (``PeriodicTbl``) are
#: data a reader may read, as the halogen twins read an isotope's mass.
ISOSPEC_PREDICTORS = frozenset(
    {
        "",
        "Iso",
        "IsoDistribution",
        "IsoLayered",
        "IsoLayeredGenerator",
        "IsoOrderedGenerator",
        "IsoStochasticGenerator",
        "IsoThreshold",
        "IsoThresholdGenerator",
        "IsoTotalProb",
        "IsoTotalProbGenerator",
    }
)


def _predicts_with_isospec(imports: list[tuple[str, str]]) -> bool:
    return any(
        module.startswith("IsoSpecPy") and name in ISOSPEC_PREDICTORS
        for module, name in imports
    )


def _imports(path: Path) -> list[tuple[str, str]]:
    """Every ``(module, name)`` a file imports by ``from ... import``, and
    ``(module, "")`` for a plain ``import``."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            found.extend((node.module or "", alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend((alias.name, "") for alias in node.names)
    return found


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_reader_predicts_and_matches_lines_through_the_line_model(reader):
    imports = _imports(READERS[reader])

    elsewhere = [
        f"{name} from {module}"
        for module, name in imports
        if name in LINE_MODEL_NAMES and module != "mascope_tools.composition.line_model"
    ]
    assert not elsewhere, f"{reader} imports line-model names elsewhere: {elsewhere}"
    assert not _predicts_with_isospec(imports), f"{reader} calls IsoSpec itself"
    reads_it = any(
        module == "mascope_tools.composition.line_model" for module, _ in imports
    )
    assert reads_it == (reader in DIRECT)


def test_only_the_line_model_and_the_search_proposer_call_isospec():
    # The search's proposer (isotopologues.py) combines each element's own
    # lines to find which of thousands of formulas could put any line on a
    # peak; the candidates it proposes are then scored on the line model's
    # envelope.
    callers = {
        path.name
        for path in [*LIBRARY.rglob("*.py"), *BACKEND.rglob("*.py")]
        if _predicts_with_isospec(_imports(path))
    }
    assert callers == {"line_model.py", "isotopologues.py"}


class TestDetectable:
    def test_the_runs_fitted_threshold_decides_where_it_set_one(self):
        fitted = EnvelopeTolerance(detect_snr=1.5)

        assert detect_threshold(None) == DETECT_SNR_K
        assert detect_threshold(fitted) == 1.5
        # An expected signal-to-noise of 2: under the default, over the fit.
        assert not line_detectable(0.02, 100.0)
        assert line_detectable(0.02, 100.0, tolerance=fitted)

    def test_a_threshold_the_caller_recorded_overrides_the_tolerances(self):
        fitted = EnvelopeTolerance(detect_snr=1.5)

        assert not line_detectable(0.02, 100.0, tolerance=fitted, detect_snr=3.0)

    @pytest.mark.parametrize("snr", [None, float("nan"), 0.0, -1.0])
    def test_without_a_signal_to_noise_a_line_counts_by_its_share(self, snr):
        assert line_detectable(REL_DETECT_NO_SNR, snr)
        assert not line_detectable(REL_DETECT_NO_SNR * 0.99, snr)

    def test_each_line_can_carry_its_own_anchors_signal_to_noise(self):
        answer = line_detectable(
            np.array([0.05, 0.05, 0.2]), np.array([100.0, np.nan, np.nan])
        )

        assert answer.tolist() == [True, False, True]


class TestHeight:
    def test_the_bounds_are_factors_of_the_lines_height(self):
        assert height_within(2.0, 1.0, at_most=2.0)
        assert not height_within(2.01, 1.0, at_most=2.0)
        assert height_within(0.5, 1.0, at_least=0.5)
        assert not height_within(0.49, 1.0, at_least=0.5)
        assert height_within(100.0, 1.0)

    def test_inside_the_admitted_band_a_share_is_no_deviation(self):
        assert height_deviation(0.1, 0.1, 50.0) == 0.0

    def test_outside_it_the_distance_is_read_in_the_lines_width(self):
        # 0.2 against 0.1, with no noise: the width is the 5% floor of the
        # share, 0.005, and the share sits 0.1 over the band.
        assert height_deviation(0.2, 0.1, 50.0) == pytest.approx(20.0)
        # Its noise widens it: 0.2 at a relative noise of 0.5 is 0.1 wide.
        assert height_deviation(0.2, 0.1, 50.0, inverse_snr=0.5) == pytest.approx(1.0)


class TestTracks:
    def test_a_line_tracks_within_the_bar_of_its_parents_error(self):
        assert TRACKING_WIDTHS == 3.0
        assert tracks(1.4, 0.1, 0.5)
        assert not tracks(1.7, 0.1, 0.5)
        assert tracks(1.7, 0.1, 0.5, widths=4.0)

    @pytest.mark.parametrize("error", [None, float("nan"), "x"])
    def test_an_unread_error_does_not_track(self, error):
        assert not tracks(error, 0.0, 0.5)
        assert not tracks(0.0, error, 0.5)


class TestTheMatcher:
    def test_a_line_takes_the_nearest_peak_in_its_window(self):
        mzs = np.array([99.9995, 100.0002, 100.0008])

        # The brighter or the lighter peak does not matter: the nearer does.
        assert nearest_peak(mzs, 100.0, 10.0) == 1
        assert nearest_peak(mzs, 100.0, 10.0, skip={1}) == 0
        assert nearest_peak(mzs, 100.0, 3.0, skip={1}) is None
        assert nearest_peak(mzs, 101.0, 10.0) is None

    def test_it_reads_a_line_as_the_vectorised_matcher_does(self):
        rng = np.random.default_rng(7)
        mzs = np.sort(rng.uniform(99.99, 100.01, 40))
        lines = rng.uniform(99.99, 100.01, 25)
        found, index = nearest_peaks(mzs, lines, 20.0)

        for line, hit, at in zip(lines, found, index):
            assert nearest_peak(mzs, line, 20.0) == (int(at) if hit else None)

    def test_the_four_answers(self):
        mzs = np.array([100.0, 101.0034])
        intensities = np.array([1000.0, 150.0])

        def ask(line_mz, share, **kwargs):
            return check_line(
                line_mz,
                share,
                mzs,
                intensities,
                anchor_intensity=1000.0,
                anchor_snr=100.0,
                tolerance_ppm=5.0,
                **kwargs,
            )

        present = ask(101.0034, 0.11, at_most=2.0)
        assert (present.expected, present.present, present.within) == (True,) * 3
        assert present.detectable and present.peak == 1 and not present.missing
        assert not ask(101.0034, 0.05, at_most=2.0).within
        missing = ask(102.0, 0.05)
        assert missing.missing and missing.peak is None and not missing.within
        assert not ask(102.0, 0.005).expected
        assert not ask(102.0, 0.02).detectable


class TestResolvedLines:
    def test_the_target_library_keeps_a_faint_line_nothing_took(self):
        # At m/z 100 and 1000 a FWHM is 0.1: the 0.5% line 0.5 away is nobody's.
        fine = ([100.0, 100.5, 101.0], [1.0, 0.005, 0.1], ["M0", "A", "13C"])

        def power(_mz):
            return 1000.0

        envelope = resolve_lines(*fine, power, 0.01)
        library = resolve_lines(*fine, power, 0.01, keep_under_floor=True)

        assert [line.label for line in envelope] == ["M0", "13C"]
        assert [line.label for line in library] == ["M0", "A", "13C"]
        assert [line.members for line in library] == [(0,), (1,), (2,)]

    def test_a_line_names_the_fine_lines_it_holds(self):
        lines = resolve_lines(
            [100.0, 101.0, 101.0063],
            [1.0, 0.1, 0.004],
            ["M0", "13C", "15N"],
            lambda _mz: 1000.0,
            0.01,
        )

        assert [line.members for line in lines] == [(0,), (1, 2)]
        assert lines[1].abundance == pytest.approx(0.104)

    def test_it_is_the_envelopes_prediction(self):
        def power(mz):
            return 5000.0

        mzs, shares, labels = predict_resolved_isotopes("C10H15NO3", -1, 0.01, power)
        lines = resolve_lines(
            *line_model.predict_isotopes("C10H15NO3", -1, None, 1e-5), power, 0.01
        )

        assert labels == [line.label for line in lines]
        np.testing.assert_allclose(mzs, [line.mz for line in lines])
        np.testing.assert_allclose(shares, [line.abundance for line in lines])

    def test_a_number_or_a_function_groups_alike(self):
        mzs, shares = [100.0, 100.002, 101.0], [1.0, 0.01, 0.1]

        assert line_groups(mzs, shares, 1e4) == line_groups(mzs, shares, lambda _: 1e4)
        assert line_groups(mzs, shares, None) == [[0], [1], [2]]
        assert line_groups(mzs, shares, 0.0) == [[0], [1], [2]]
