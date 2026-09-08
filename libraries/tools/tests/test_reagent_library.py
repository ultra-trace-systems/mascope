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
    DEFAULT_REAGENT_MATCH_PPM,
    KIND_OXIDE,
    ReagentCluster,
    ion_mz,
    match_reagent_clusters,
    reagent_library,
    secondary_channels,
)


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
                assert separation > DEFAULT_REAGENT_MATCH_PPM, profile


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
        with something the sample supplied.
        """
        for profile in ("BR", "IODIDE", "NO3", "NO3_15N"):
            for cluster in reagent_library(profile):
                assert "C" not in cluster.formula, f"{profile}: {cluster.label}"

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
        hits = match_reagent_clusters(reagent_library("BR"), mz, intensity)

        assert len(hits) == len(mz)
        assert {hit.isotope_label for hit in hits if hit.is_satellite} == {
            "81Br",
            "81Br2",
        }

    def test_an_analyte_peak_is_left_alone(self):
        mz, intensity = _spectrum(("Br", -1, 1e6))
        mz = np.append(mz, 200.12345)
        intensity = np.append(intensity, 5e5)
        hits = match_reagent_clusters(reagent_library("BR"), mz, intensity)

        assert 200.12345 not in {hit.mz for hit in hits}

    def test_a_peak_is_claimed_once(self):
        mz, intensity = _spectrum(("Br", -1, 1e6), ("Br2", -1, 3e5))
        hits = match_reagent_clusters(reagent_library("BR"), mz, intensity)

        indices = [hit.index for hit in hits]
        assert len(indices) == len(set(indices))

    def test_the_claim_does_not_depend_on_the_table_order(self):
        mz, intensity = _spectrum(("Br", -1, 1e6), ("Br2", -1, 3e5))
        library = list(reagent_library("BR"))
        forwards = match_reagent_clusters(library, mz, intensity)
        backwards = match_reagent_clusters(library[::-1], mz, intensity)

        assert [(h.index, h.cluster.label) for h in forwards] == [
            (h.index, h.cluster.label) for h in backwards
        ]

    def test_it_takes_the_brightest_peak_in_the_window(self):
        """At a reagent mass that peak is the reagent ion. It is what makes a
        window this wide safe."""
        target = ion_mz("NO3", -1)
        mz = np.array([target - 1e-4, target, target + 1e-4])
        intensity = np.array([10.0, 1e6, 20.0])
        hits = match_reagent_clusters(reagent_library("NO3"), mz, intensity)

        assert [hit.index for hit in hits if not hit.is_satellite] == [1]

    def test_a_satellite_with_an_analyte_on_top_is_left(self):
        """A peak far taller than the envelope predicts has something else in
        it, and claiming it would bury that."""
        mz, intensity = _spectrum(("Br", -1, 1e6))
        loaded = intensity.copy()
        loaded[1] = intensity[1] * 50
        claimed = match_reagent_clusters(reagent_library("BR"), mz, loaded)

        assert [hit.index for hit in claimed] == [0]
        assert len(match_reagent_clusters(reagent_library("BR"), mz, intensity)) == 2

    def test_a_satellite_needs_the_parent_it_is_a_satellite_of(self):
        """The evidence for a satellite is the cluster it belongs to, so an
        envelope whose monoisotopic peak is absent claims nothing."""
        predicted_mz, predicted_intensity, _ = predict_isotopes("Br", -1)
        heavy = int(np.argmax(predicted_mz))
        mz = np.array([float(predicted_mz[heavy])])
        intensity = np.array([1e6])

        assert match_reagent_clusters(reagent_library("BR"), mz, intensity) == []

    def test_an_empty_library_or_spectrum_claims_nothing(self):
        mz, intensity = _spectrum(("Br", -1, 1e6))
        assert match_reagent_clusters((), mz, intensity) == []
        assert match_reagent_clusters(reagent_library("BR"), [], []) == []
