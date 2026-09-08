"""The reagent-cluster library, and what it is allowed to claim.

Two things are being pinned here. The masses, because a library ion claims the
peak it matches and a wrong mass claims the wrong peak. And the membership,
because the dangerous failure of a library like this one is not a missing entry
but an extra one: an ion that is really the analyte's adduct channel, claimed as
reagent, takes a real measurement out of the ledger and puts it beyond reach.
Those exclusions are lessons the reference engine learned the hard way, so they
are tested as behaviour rather than left to a comment.
"""

import numpy as np
import pytest

from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.reagents import (
    DEFAULT_ANCHOR_PPM,
    KIND_BACKGROUND,
    KIND_OXIDE,
    ReagentCluster,
    ion_mz,
    match_reagent_clusters,
    reagent_library,
    secondary_channels,
)


#: The instrument window an Orbitrap run claims in, as the profiles resolve it.
ORBI_PPM = 3.0


def claim(library, mz, intensity, **kwargs):
    """Match at Orbitrap precision unless a test says otherwise."""
    kwargs.setdefault("claim_ppm", ORBI_PPM)
    return match_reagent_clusters(library, mz, intensity, **kwargs)


def _by_label(profile: str) -> dict[str, ReagentCluster]:
    return {cluster.label: cluster for cluster in reagent_library(profile)}


class TestTheLibraryMasses:
    """Against values a spectroscopist reads off a nitrate or bromide source."""

    @pytest.mark.parametrize(
        "profile, label, expected",
        [
            ("NO3", "[NO3]-", 61.9884),
            ("NO3", "[NO3+HNO3]-", 124.9840),
            ("NO3", "[NO3+2xHNO3]-", 187.9796),
            ("BR", "[Br]-", 78.9189),
            ("BR", "[Br2]-", 157.8372),
            ("BR", "[BrO3]-", 126.9036),
            ("IODIDE", "[I]-", 126.9050),
            ("IODIDE", "[I2O]-", 269.8044),
            ("UR", "[CH4N2O+H]+", 61.0396),
            ("UR", "[(CH4N2O)2+H]+", 121.0720),
            ("UR", "[(CH4N2O)3+H]+", 181.1044),
        ],
    )
    def test_a_known_reagent_ion_lands_where_it_should(self, profile, label, expected):
        assert _by_label(profile)[label].mz == pytest.approx(expected, abs=5e-4)

    def test_an_anion_carries_its_extra_electron(self):
        """And a cation is short one, which is the sign of the correction."""
        assert ion_mz("Br", -1) > ion_mz("Br", 1)

    def test_the_labelled_reagent_builds_a_labelled_ladder(self):
        """The label follows into every rung rather than being restated.

        A 15N reagent puts one heavy nitrogen in the core and one more in each
        acid it clusters with, so the rungs sit 0.997 Da per nitrogen above the
        unlabelled ones.
        """
        light, heavy = _by_label("NO3"), _by_label("NO3_15N")
        assert heavy["[^NO3]-"].mz - light["[NO3]-"].mz == pytest.approx(
            0.997, abs=1e-3
        )
        assert heavy["[^NO3+H^NO3]-"].mz - light["[NO3+HNO3]-"].mz == pytest.approx(
            2 * 0.997, abs=1e-3
        )

    def test_no_two_ions_of_one_library_share_a_mass(self):
        """Or a claim would depend on the order the table happens to be in."""
        for profile in ("BR", "IODIDE", "NO3", "NO3_15N", "UR"):
            masses = sorted(cluster.mz for cluster in reagent_library(profile))
            for lower, upper in zip(masses, masses[1:]):
                separation = (upper - lower) / lower * 1e6
                assert separation > DEFAULT_ANCHOR_PPM, profile


class TestWhatTheLibraryRefusesToClaim:
    """The exclusions. Each one is an ion that is really an analyte."""

    def test_a_source_with_no_single_reagent_has_no_library(self):
        """An electrospray makes no one carrier whose clusters could be listed."""
        assert reagent_library("ESI_POS") == ()
        assert reagent_library("ESI_NEG") == ()
        assert reagent_library("none") == ()

    def test_the_urea_monomer_ammonium_is_a_probe_but_not_a_claim(self):
        """``[(CH4N2O)+NH4]+`` is ``[NH3+(CH4N2O)H]+``: ambient ammonia.

        The two lists disagree here on purpose. As evidence the ion is fine -
        it says ammonium is clustering with urea in the source - but claiming
        the peak would bury an ammonia measurement.
        """
        monomer = "CH8N3O"
        probes = {
            probe.formula
            for channel in secondary_channels("UR")
            for probe in channel.probes
        }
        assert monomer in probes
        assert monomer not in {c.formula for c in reagent_library("UR")}

    def test_the_ammonium_series_starts_at_the_dimer(self):
        labels = set(_by_label("UR"))
        assert "[(CH4N2O)2+NH4]+" in labels
        assert "[CH4N2O+NH4]+" not in labels

    def test_no_organic_acid_clusters_anywhere(self):
        """``[Br+HCOOH]-`` is ``[formic acid+Br]-``: the [M+Br]- channel.

        Carbon in a halide or nitrate reagent ion is the tell - none of those
        reagents contains any - so a carbon-bearing entry would be a cluster
        with something the sample supplied. The bromide source's own precursors
        are the one exception, and they are named rather than pattern-matched so
        that adding a third needs a deliberate edit here.
        """
        allowed = {"[CH2Br2-H]-", "[CHBr3-H]-"}
        for profile in ("BR", "IODIDE", "NO3", "NO3_15N"):
            for cluster in reagent_library(profile):
                if cluster.label in allowed:
                    assert cluster.kind == KIND_BACKGROUND
                    continue
                assert "C" not in cluster.formula, f"{profile}: {cluster.label}"

    def test_the_bromide_precursors_are_claimed(self):
        """Dibromomethane and bromoform are what the source is dosed with, so
        their deprotonated ions are the reagent's rather than the sample's."""
        labels = _by_label("BR")

        assert labels["[CH2Br2-H]-"].mz == pytest.approx(170.84506, abs=5e-4)
        assert labels["[CHBr3-H]-"].mz == pytest.approx(248.75556, abs=5e-4)

    def test_bromide_claims_its_oxides_and_iodide_does_not(self):
        """IO3- is iodate: deprotonated iodic acid, and the signature analyte of
        an iodide deployment. BrO3- is reagent."""
        assert any(c.kind == KIND_OXIDE for c in reagent_library("BR"))
        assert not any(c.kind == KIND_OXIDE for c in reagent_library("IODIDE"))

    def test_the_iodide_library_carries_no_other_halogen(self):
        """The shed acid is the reagent's own - HI here, never a phantom HBr."""
        assert not any("Br" in c.formula for c in reagent_library("IODIDE"))


def _spectrum(*ions: tuple[str, int, float]) -> tuple[np.ndarray, np.ndarray]:
    """A spectrum holding each ion's full predicted envelope at a given height."""
    mz: list[float] = []
    intensity: list[float] = []
    for formula, charge, height in ions:
        predicted_mz, predicted_intensity, _ = predict_isotopes(formula, charge)
        base = max(predicted_intensity)
        for one_mz, one_intensity in zip(predicted_mz, predicted_intensity):
            mz.append(float(one_mz))
            intensity.append(height * float(one_intensity) / base)
    order = np.argsort(mz)
    return np.asarray(mz)[order], np.asarray(intensity)[order]


class TestClaimingPeaks:
    def test_it_claims_a_cluster_and_its_isotopologues(self):
        """The heavy-halogen satellites come out of the predicted envelope, so
        no table of isotopologue combinations has to be maintained."""
        mz, intensity = _spectrum(("Br", -1, 1e6), ("Br2", -1, 3e5))
        hits, _ = claim(reagent_library("BR"), mz, intensity)

        assert len(hits) == len(mz)
        assert {hit.isotope_label for hit in hits if hit.is_satellite} == {
            "81Br",
            "81Br2",
        }

    def test_an_analyte_peak_is_left_alone(self):
        mz, intensity = _spectrum(("Br", -1, 1e6))
        mz = np.append(mz, 200.12345)
        intensity = np.append(intensity, 5e5)
        hits, _ = claim(reagent_library("BR"), mz, intensity)

        assert 200.12345 not in {hit.mz for hit in hits}

    def test_a_peak_is_claimed_once(self):
        mz, intensity = _spectrum(("Br", -1, 1e6), ("Br2", -1, 3e5))
        hits, _ = claim(reagent_library("BR"), mz, intensity)

        indices = [hit.index for hit in hits]
        assert len(indices) == len(set(indices))

    def test_the_claim_does_not_depend_on_the_table_order(self):
        mz, intensity = _spectrum(("Br", -1, 1e6), ("Br2", -1, 3e5))
        library = list(reagent_library("BR"))
        forwards, _ = claim(library, mz, intensity)
        backwards, _ = claim(library[::-1], mz, intensity)

        assert [(h.index, h.cluster.label) for h in forwards] == [
            (h.index, h.cluster.label) for h in backwards
        ]

    def test_it_takes_the_brightest_peak_in_the_window(self):
        """At a reagent mass that peak is the reagent ion. It is what makes a
        window this wide safe."""
        target = ion_mz("NO3", -1)
        mz = np.array([target - 1e-4, target, target + 1e-4])
        intensity = np.array([10.0, 1e6, 20.0])
        hits, _ = claim(reagent_library("NO3"), mz, intensity)

        assert [hit.index for hit in hits if not hit.is_satellite] == [1]

    def test_a_satellite_with_an_analyte_on_top_is_left(self):
        """A peak far taller than the envelope predicts has something else in
        it, and claiming it would bury that."""
        mz, intensity = _spectrum(("Br", -1, 1e6))
        loaded = intensity.copy()
        loaded[1] = intensity[1] * 50
        claimed, _ = claim(reagent_library("BR"), mz, loaded)

        assert [hit.index for hit in claimed] == [0]
        assert len(claim(reagent_library("BR"), mz, intensity)[0]) == 2

    def test_a_satellite_needs_the_parent_it_is_a_satellite_of(self):
        """The evidence for a satellite is the cluster it belongs to, so an
        envelope whose monoisotopic peak is absent claims nothing."""
        predicted_mz, predicted_intensity, _ = predict_isotopes("Br", -1)
        heavy = int(np.argmax(predicted_mz))
        mz = np.array([float(predicted_mz[heavy])])
        intensity = np.array([1e6])

        assert claim(reagent_library("BR"), mz, intensity)[0] == []

    def test_an_empty_library_or_spectrum_claims_nothing(self):
        mz, intensity = _spectrum(("Br", -1, 1e6))
        assert claim((), mz, intensity)[0] == []
        assert claim(reagent_library("BR"), [], [])[0] == []


def _drifted(
    *ions: tuple[str, int, float], ppm: float
) -> tuple[np.ndarray, np.ndarray]:
    """A spectrum whose whole ladder sits ``ppm`` off, as a miscalibration does."""
    mz, intensity = _spectrum(*ions)
    return mz * (1.0 + ppm * 1e-6), intensity


class TestTheAnchoredWindow:
    """The anchors are the library's base ions: bright, and shared with nothing.
    They are found in a wide window and then say where this spectrum puts the
    reagent's masses, so every other rung can be claimed at the instrument's own
    precision against a corrected mass.

    A single wide window cannot do this job. The gate's uronium set has an
    ambient compound 21-28 ppm above the urea tetramer and pentamer masses -
    alone in a 40 ppm window, and within a ppm of its OWN exact mass - while
    that sample's reagent ions sit within 5 ppm of theirs. Being alone in a wide
    window is not evidence; being where the anchors say the reagent is, is.
    """

    def test_a_uniformly_drifted_ladder_is_still_claimed(self):
        """Set E's bromide ladder sits at -10.8 ppm across every rung: a plain
        calibration offset, which a bare instrument window would lose."""
        mz, intensity = _drifted(("Br", -1, 1e6), ("Br2", -1, 3e5), ppm=-10.8)
        hits, calibration = claim(reagent_library("BR"), mz, intensity)

        assert calibration.offset_ppm == pytest.approx(-10.8, abs=0.3)
        assert len(hits) == len(mz)

    def test_an_analyte_beyond_the_anchors_is_not_claimed(self):
        """The tetramer mass +27 ppm, with the anchors on their own masses. A
        peak that far off is not this ladder's, however alone it sits."""
        mz, intensity = _spectrum(("CH5N2O", 1, 1e7), ("C2H9N4O2", 1, 2e7))
        tetramer = _by_label("UR")["[(CH4N2O)4+H]+"].mz
        mz = np.append(mz, tetramer * (1 + 27.5e-6))
        intensity = np.append(intensity, 3e3)
        hits, calibration = claim(reagent_library("UR"), mz, intensity)

        assert abs(calibration.offset_ppm) < 1.0
        assert not any(h.cluster.label == "[(CH4N2O)4+H]+" for h in hits)

    def test_the_same_peak_is_claimed_when_the_ladder_agrees(self):
        """The identical peak, on a spectrum whose anchors are drifted with it,
        IS the tetramer. The difference between the two tests is the whole rule:
        not how far the peak sits from the nominal mass, but whether it sits
        where this spectrum's own reagent ions say the reagent is.
        """
        mz, intensity = _drifted(("CH5N2O", 1, 1e7), ("C2H9N4O2", 1, 2e7), ppm=15.0)
        tetramer = _by_label("UR")["[(CH4N2O)4+H]+"].mz
        mz = np.append(mz, tetramer * (1 + 15.0e-6))
        intensity = np.append(intensity, 3e3)
        hits, calibration = claim(reagent_library("UR"), mz, intensity)

        assert calibration.offset_ppm == pytest.approx(15.0, abs=0.5)
        assert any(h.cluster.label == "[(CH4N2O)4+H]+" for h in hits)

    def test_a_ladder_drifted_past_the_anchor_window_claims_nothing(self):
        """The conservative end of the rule, worth stating because it is a
        deliberate limit rather than an oversight: the anchors are what license
        a correction, so a spectrum whose own base ions are further out than the
        anchor window claims nothing at all instead of guessing an offset.
        """
        drift = DEFAULT_ANCHOR_PPM + 10.0
        mz, intensity = _drifted(("CH5N2O", 1, 1e7), ("C2H9N4O2", 1, 2e7), ppm=drift)
        hits, calibration = claim(reagent_library("UR"), mz, intensity)

        assert calibration.anchors == ()
        assert hits == []

    def test_a_spectrum_with_no_anchor_searches_its_nominal_masses(self):
        """Conservative rather than clever: an unanchored spectrum still claims
        a rung that is on its exact mass, and claims nothing that is not."""
        trimer = _by_label("UR")["[(CH4N2O)3+H]+"].mz
        mz = np.array([trimer, trimer * (1 + 25e-6) + 40.0])
        intensity = np.array([1e6, 1e6])
        hits, calibration = claim(reagent_library("UR"), mz, intensity)

        assert calibration.anchors == ()
        assert [h.cluster.label for h in hits] == ["[(CH4N2O)3+H]+"]

    def test_a_trace_on_a_reagent_mass_is_not_the_reagent(self):
        """A reagent ion is one of the brightest things in the spectrum, so a
        peak at the noise floor sitting on its mass is a coincidence."""
        mz, intensity = _spectrum(("Br", -1, 1e6), ("Br2", -1, 3e5))
        oxide = _by_label("BR")["[BrO3]-"].mz
        mz = np.append(mz, oxide)
        intensity = np.append(intensity, 1.0)  # 1e-6 of the base peak
        hits, _ = claim(reagent_library("BR"), mz, intensity)

        assert not any(h.cluster.label == "[BrO3]-" for h in hits)


class TestTheLabelledEnvelope:
    """A 98% 15N reagent predicts its 14N impurity one mass unit BELOW the ion,
    at 2% of it. Reading the envelope by mass therefore makes the impurity the
    reference - and then the ion is a 'satellite' of it at 49x, every relative
    is 50x too large for the intensity gate to bite, and the real 14N line is
    skipped as if it were the monoisotopic one.
    """

    def test_the_impurity_line_is_below_the_ion(self):
        """The premise, pinned so the rest of this class cannot silently stop
        testing anything."""
        predicted_mz, _, labels = predict_isotopes("O3^N", -1, 0.98)
        assert labels[int(np.argmin(predicted_mz))] == "14N"

    def test_the_ion_is_not_a_satellite_of_its_own_impurity(self):
        """The failure this guards: a peak beside the base peak claimed as an
        'M0' satellite, which on the gate was a ringing line of the base peak.
        """
        core = _by_label("NO3_15N")["[^NO3]-"]
        mz = np.array([core.mz, core.mz * (1 + 14.5e-6)])
        intensity = np.array([1e6, 8e3])
        hits, _ = claim(reagent_library("NO3_15N"), mz, intensity, purity=0.98)

        assert [h.isotope_label for h in hits] == [None]
        assert not any(h.isotope_label == "M0" for h in hits)

    def test_the_impurity_line_is_claimed_as_a_satellite(self):
        """It is the reagent's own 14N, 2% of the ion, and the brightest
        unclaimed peak of the ladder region until it is claimed."""
        predicted_mz, predicted_intensity, labels = predict_isotopes("O3^N", -1, 0.98)
        m0 = int(np.argmax(predicted_intensity))
        impurity = 1 - m0
        mz = np.array([float(predicted_mz[impurity]), float(predicted_mz[m0])])
        intensity = np.array([2.0e4, 1.0e6])
        hits, _ = claim(reagent_library("NO3_15N"), mz, intensity, purity=0.98)

        satellites = [h for h in hits if h.is_satellite]
        assert [h.isotope_label for h in satellites] == ["14N"]
        assert satellites[0].predicted_relative == pytest.approx(0.02, abs=0.005)
