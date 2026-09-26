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
    DEFAULT_FRAGMENT_MAX_EXCESS,
    DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE,
    FAMILIES,
    FAMILY_AIR,
    FAMILY_CALIBRANT,
    FAMILY_REAGENT,
    FRAGMENT_LADDERS,
    KIND_OXIDE,
    REAGENT_CLUSTERS,
    SOURCE_ION_REFERENCES,
    ReagentCluster,
    fragment_ladders,
    ion_mz,
    match_reagent_clusters,
    reagent_library,
    secondary_channels,
)
from mascope_tools.composition.utils import parse_composition


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
            ("IODIDE", "[I3]-", 380.7140),
            ("UR", "[CH4N2O+H]+", 61.0396),
            ("UR", "[(CH4N2O)2+H]+", 121.0720),
            ("UR", "[(CH4N2O)3+H]+", 181.1044),
            ("EASYIC_POS", "[C16H10]+", 202.0777),
            ("EASYIC_POS", "[C16H10+H]+", 203.0855),
            ("EASYIC_POS", "[C16H10-C2H2]+", 176.0621),
            ("EASYIC_NEG", "[C16H10]-", 202.0788),
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
        for profile in ("BR", "IODIDE", "NO3", "NO3_15N", "UR", "EASYIC_POS"):
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

    def test_the_charge_transfer_library_is_its_beam_and_the_air(self):
        """Every atom of the calibrant family is fluoranthene's, and the rest is
        the air's: the discharge's cations and protonated water in positive
        mode, the anions it makes of oxygen, water, carbon dioxide and the
        nitrogen oxides in negative. The beam alone anchors the pass."""
        for profile in ("EASYIC_POS", "EASYIC_NEG"):
            for cluster in reagent_library(profile):
                if cluster.family == FAMILY_CALIBRANT:
                    assert set(cluster.formula) <= set("CH0123456789"), cluster.label
                else:
                    assert cluster.family == FAMILY_AIR, cluster.label
        anchors = [c.label for c in reagent_library("EASYIC_POS") if c.anchor]
        assert anchors == ["[C16H10]+"]
        anchors = [c.label for c in reagent_library("EASYIC_NEG") if c.anchor]
        assert anchors == ["[C16H10]-"]

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

    def test_no_urea_rung_carries_an_ammonium(self):
        """The monomer's is ammonia's reading. The multimers' are named by no
        work found and absent from the test spectra, so they are left to the
        stages rather than claimed on a grammar's say-so."""
        assert not any("NH4" in label for label in _by_label("UR"))

    def test_no_organic_acid_clusters_anywhere(self):
        """``[Br+HCOOH]-`` is ``[formic acid+Br]-``: the [M+Br]- channel.

        Carbon in a halide or nitrate reagent ion is the tell - none of those
        reagents contains any - so a carbon-bearing entry would be a cluster
        with something the sample supplied. One exception, named rather than
        pattern-matched so that adding a second needs a deliberate edit here:
        the air's carbonate family, whose one carbon is the carbon dioxide of
        the gas the source ionizes.
        """
        carbonates = {
            "[CO3]-",
            "[CO3+H2O]-",
            "[CO3+2xH2O]-",
            "[HCO3]-",
            "[HCO3+HNO3]-",
            "[O2+CO2]-",
        }
        for profile in ("BR", "IODIDE", "NO3", "NO3_15N", "EASYIC_NEG"):
            for cluster in reagent_library(profile):
                if cluster.label in carbonates:
                    assert cluster.family == FAMILY_AIR
                    assert parse_composition(cluster.formula)["C"] == 1
                    continue
                if cluster.family == FAMILY_CALIBRANT:
                    continue
                assert "C" not in cluster.formula, f"{profile}: {cluster.label}"

    @pytest.mark.parametrize(
        "profile, named, observed",
        [
            (
                "BR",
                {"[Br]-", "[Br+H2O]-", "[Br+2xH2O]-", "[Br2]-"},
                {"[Br3]-", "[Br+HBr]-", "[BrO]-", "[BrO3]-"},
            ),
            ("IODIDE", {"[I]-", "[I+H2O]-", "[I2]-", "[I3]-"}, set()),
            ("UR", {"[CH4N2O+H]+", "[(CH4N2O)2+H]+"}, {"[(CH4N2O)3+H]+"}),
        ],
    )
    def test_a_ladder_is_what_a_work_names_or_the_test_spectra_show(
        self, profile, named, observed
    ):
        """Being reagent all the way through is not enough: a rung is claimed
        where a work names it, or where the test spectra show it in every file
        of a set, which it then says. The rest of a grammar's rungs - higher
        clusters and their hydrates, oxide clusters, a precursor's anion - are
        left to the stages."""
        ladder = [c for c in reagent_library(profile) if c.family == FAMILY_REAGENT]
        assert {c.label for c in ladder if c.references} == named
        assert {c.label for c in ladder if c.observed} == observed
        for cluster in ladder:
            if cluster.observed:
                assert cluster.observed.startswith("no work found names it; ")
                assert "test spectra show it" in cluster.observed

    def test_bromide_claims_its_oxides_and_iodide_does_not(self):
        """IO3- is iodate: deprotonated iodic acid, and the signature analyte of
        an iodide deployment. BrO3- is reagent."""
        assert any(c.kind == KIND_OXIDE for c in reagent_library("BR"))
        assert not any(c.kind == KIND_OXIDE for c in reagent_library("IODIDE"))

    def test_the_iodide_library_carries_no_other_halogen(self):
        """The shed acid is the reagent's own - HI here, never a phantom HBr."""
        assert not any("Br" in c.formula for c in reagent_library("IODIDE"))

    def test_an_ion_two_families_name_is_the_first_familys(self):
        """Nitrate is an ion of the air and a nitrate source's reagent. On that
        source it is the reagent, and anchors the pass as the reagent does."""
        nitrate = _by_label("NO3")["[NO3]-"]
        assert (nitrate.family, nitrate.anchor) == (FAMILY_REAGENT, True)
        assert _by_label("BR")["[NO3]-"].family == FAMILY_AIR
        formulas = [cluster.formula for cluster in reagent_library("NO3")]
        assert len(formulas) == len(set(formulas))

    def test_a_labelled_source_leaves_its_14n_lines_to_the_reagent(self):
        """Plain nitrate on a 15N-nitrate source is first the reagent's 14N
        remainder, which the ladder's envelope claims where its height fits
        the label's purity. Listed as an air ion it would take the line as a
        monoisotopic claim before the envelope was asked."""
        labels = set(_by_label("NO3_15N"))
        assert "[NO3]-" not in labels
        assert "[NO3+H2O]-" not in labels
        assert "[NO3+HNO3]-" not in labels
        # Nitrite is not on the labelled ladder, so the air's is the air's.
        assert {"[^NO3]-", "[NO2]-", "[CO3]-", "[HCO3]-"} <= labels

    def test_every_ion_names_its_family_and_its_literature(self):
        """A reader checks a claim against a paper, not against this table: an
        ion carries the works that name it, and one no work names says what
        shows it instead of borrowing a citation that does not name it."""
        for profile, library in REAGENT_CLUSTERS.items():
            for cluster in library:
                assert cluster.family in FAMILIES, f"{profile}: {cluster.label}"
                assert bool(cluster.references) != bool(cluster.observed), (
                    f"{profile}: {cluster.label}"
                )
                for key in cluster.references:
                    assert key in SOURCE_ION_REFERENCES, f"{cluster.label}: {key}"

    def test_every_work_cited_resolves_and_is_cited(self):
        """Each work has a DOI or, where it has none, somewhere to read it or a
        book's ISBN; and a work no ion cites is not in the table."""
        cited = {
            key
            for library in REAGENT_CLUSTERS.values()
            for cluster in library
            for key in cluster.references
        } | {
            key
            for ladders in FRAGMENT_LADDERS.values()
            for ladder in ladders
            for key in ladder.references
        }
        assert cited == set(SOURCE_ION_REFERENCES)
        for key, work in SOURCE_ION_REFERENCES.items():
            assert work.doi or work.url or "ISBN" in work.citation, key
            assert work.doi is None or work.doi.startswith("10."), key

    def test_the_air_family_is_the_same_on_every_source_of_a_polarity(self):
        """The air's ions are the air's whatever the reagent, but where the
        reagent's own ladder holds one it is the reagent's there."""
        for profile in ("BR", "IODIDE", "NO3", "EASYIC_NEG"):
            labels = {c.label for c in reagent_library(profile)}
            assert {"[O2]-", "[CO3]-", "[HCO3]-", "[NO2]-"} <= labels, profile
        for profile in ("UR", "EASYIC_POS"):
            labels = {c.label for c in reagent_library(profile)}
            assert {"[N3]+", "[N4]+.", "[NO2]+", "[H3O+2xH2O]+"} <= labels, profile

    def test_the_air_ions_land_where_they_should(self):
        by_label = {
            **_by_label("EASYIC_POS"),
            **_by_label("EASYIC_NEG"),
        }
        for label, expected in (
            ("[N3]+", 42.0087),
            ("[N4]+.", 56.0117),
            ("[NO2]+", 45.9924),
            ("[H3O+H2O]+", 37.0284),
            ("[C16H12]+", 204.0934),
            ("[CO3]-", 59.9853),
            ("[HCO3]-", 60.9931),
            ("[CO3+H2O]-", 77.9959),
            ("[NO3+HNO3]-", 124.9840),
        ):
            assert by_label[label].mz == pytest.approx(expected, abs=5e-4), label


def _ion(neutral: str, *, add: str = "", remove: str = "") -> str:
    """An ion's composition: a neutral with a moiety added or removed."""
    counts = parse_composition(neutral)
    if add:
        counts += parse_composition(add)
    if remove:
        counts -= parse_composition(remove)
    return "".join(f"{element}{n}" for element, n in counts.items() if n > 0)


#: Ions a source must never claim, by the profile whose spectra they are read
#: in: a trace species' own reading, which the air, the reagent or the beam
#: does not make, and the certified cylinder's components through every
#: channel the charge-transfer source reads them through.
_ANALYTE_IONS: dict[str, list[tuple[str, str, int]]] = {
    "positive": [
        # Ammonia, protonated and hydrated: how a water-cluster source measures
        # it, and the reason the urea library leaves [urea+NH4]+ alone.
        ("ammonia", _ion("NH3", add="H"), 1),
        ("ammonia hydrate", _ion("NH3", add="H3O"), 1),
        ("ammonia dihydrate", _ion("NH3", add="H5O2"), 1),
        *[
            (f"{name} {how}", ion, 1)
            for name, neutral in (
                ("benzene", "C6H6"),
                ("toluene", "C7H8"),
                ("xylene", "C8H10"),
                ("styrene", "C8H8"),
                ("isoprene", "C5H8"),
                ("acetone", "C3H6O"),
                ("hexanal", "C6H12O"),
                ("alpha-pinene", "C10H16"),
                ("methanol", "CH4O"),
                ("acetaldehyde", "C2H4O"),
                ("acetonitrile", "C2H3N"),
                ("trimethylbenzene", "C9H12"),
            )
            for how, ion in (
                ("radical cation", neutral),
                ("protonated", _ion(neutral, add="H")),
                ("less a hydride", _ion(neutral, remove="H")),
            )
        ],
    ],
    "negative": [
        ("sulfuric acid", _ion("H2SO4", remove="H"), -1),
        ("methanesulfonic acid", _ion("CH4O3S", remove="H"), -1),
        ("iodic acid", _ion("HIO3", remove="H"), -1),
        ("formic acid", _ion("CH2O2", remove="H"), -1),
        ("formic acid dimer", _ion("C2H4O4", remove="H"), -1),
        ("acetic acid", _ion("C2H4O2", remove="H"), -1),
        ("pyruvic acid", _ion("C3H4O3", remove="H"), -1),
        ("trifluoroacetic acid", _ion("C2HF3O2", remove="H"), -1),
        ("pinonic acid", _ion("C10H16O3", remove="H"), -1),
        ("HO2 with bromide", _ion("HO2", add="Br"), -1),
        ("nitric acid with bromide", _ion("HNO3", add="Br"), -1),
        ("nitric acid with iodide", _ion("HNO3", add="I"), -1),
        ("nitrous acid with iodide", _ion("HNO2", add="I"), -1),
        ("formic acid with nitrate", _ion("CH2O2", add="NO3"), -1),
        ("formic acid with bromide", _ion("CH2O2", add="Br"), -1),
    ],
}


class TestNoAnalyteIsClaimed:
    """The stage-1 guard, restated for the whole library: a claim takes the
    peak out of both stages, so a library ion that is an analyte's reading
    buries the analyte wherever the two are in one spectrum."""

    @pytest.mark.parametrize(
        "profile",
        ["BR", "IODIDE", "NO3", "NO3_15N", "UR", "EASYIC_POS", "EASYIC_NEG"],
    )
    def test_no_library_ion_sits_on_an_analytes_reading(self, profile):
        library = reagent_library(profile)
        polarity = "positive" if library[0].charge > 0 else "negative"
        for name, formula, charge in _ANALYTE_IONS[polarity]:
            target = ion_mz(formula, charge)
            for cluster in library:
                separation = abs(cluster.mz - target) / target * 1e6
                assert separation > 5.0, f"{profile}: {cluster.label} on {name}"


def _spectrum(*ions: tuple[str, int, float]) -> tuple[np.ndarray, np.ndarray]:
    """A spectrum holding each ion's full predicted envelope at a given height.

    Predicted down to the pass's own isotopologue floor rather than the scoring
    default, so the faint lines a real spectrum carries - the 18O among them -
    are present to be claimed or left.
    """
    mz: list[float] = []
    intensity: list[float] = []
    for formula, charge, height in ions:
        predicted_mz, predicted_intensity, _ = predict_isotopes(
            formula, charge, None, DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE
        )
        base = max(predicted_intensity)
        for one_mz, one_intensity in zip(predicted_mz, predicted_intensity):
            mz.append(float(one_mz))
            intensity.append(height * float(one_intensity) / base)
    order = np.argsort(mz)
    return np.asarray(mz)[order], np.asarray(intensity)[order]


class TestClaimingPeaks:
    def test_it_claims_a_cluster_and_its_isotopologues(self):
        """The heavy-halogen isotopologues come out of the predicted envelope, so
        no table of isotopologue combinations has to be maintained."""
        mz, intensity = _spectrum(("Br", -1, 1e6), ("Br2", -1, 3e5))
        hits, _ = claim(reagent_library("BR"), mz, intensity)

        assert len(hits) == len(mz)
        assert {hit.isotope_label for hit in hits if hit.is_isotopologue} == {
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

        assert [hit.index for hit in hits if not hit.is_isotopologue] == [1]

    def test_an_isotopologue_with_an_analyte_on_top_is_left(self):
        """A peak far taller than the envelope predicts has something else in
        it, and claiming it would bury that."""
        mz, intensity = _spectrum(("Br", -1, 1e6))
        loaded = intensity.copy()
        loaded[1] = intensity[1] * 50
        claimed, _ = claim(reagent_library("BR"), mz, loaded)

        assert [hit.index for hit in claimed] == [0]
        assert len(claim(reagent_library("BR"), mz, intensity)[0]) == 2

    def test_an_isotopologue_needs_the_parent_it_is_an_isotopologue_of(self):
        """The evidence for an isotopologue is the cluster it belongs to, so an
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

    A single wide window cannot do this job. An ambient compound can sit 20-30
    ppm above a rung's mass - alone in a 40 ppm window, and within a ppm of its
    OWN exact mass - while the sample's reagent ions sit within 5 ppm of theirs;
    one of the gate's uronium sets has such compounds above the urea tetramer
    and pentamer masses. Being alone in a wide window is not evidence; being
    where the anchors say the reagent is, is.
    """

    def test_a_uniformly_drifted_ladder_is_still_claimed(self):
        """Set E's bromide ladder sits at -10.8 ppm across every rung: a plain
        calibration offset, which a bare instrument window would lose."""
        mz, intensity = _drifted(("Br", -1, 1e6), ("Br2", -1, 3e5), ppm=-10.8)
        hits, calibration = claim(reagent_library("BR"), mz, intensity)

        assert calibration.offset_ppm == pytest.approx(-10.8, abs=0.3)
        assert len(hits) == len(mz)

    def test_an_analyte_beyond_the_anchors_is_not_claimed(self):
        """The trimer mass +27 ppm, with the anchors on their own masses. A
        peak that far off is not this ladder's, however alone it sits."""
        mz, intensity = _spectrum(("CH5N2O", 1, 1e7), ("C2H9N4O2", 1, 2e7))
        trimer = _by_label("UR")["[(CH4N2O)3+H]+"].mz
        mz = np.append(mz, trimer * (1 + 27.5e-6))
        intensity = np.append(intensity, 3e4)
        hits, calibration = claim(reagent_library("UR"), mz, intensity)

        assert abs(calibration.offset_ppm) < 1.0
        assert not any(h.cluster.label == "[(CH4N2O)3+H]+" for h in hits)

    def test_the_same_peak_is_claimed_when_the_ladder_agrees(self):
        """The identical peak, on a spectrum whose anchors are drifted with it,
        IS the trimer. The difference between the two tests is the whole rule:
        not how far the peak sits from the nominal mass, but whether it sits
        where this spectrum's own reagent ions say the reagent is.
        """
        mz, intensity = _drifted(("CH5N2O", 1, 1e7), ("C2H9N4O2", 1, 2e7), ppm=15.0)
        trimer = _by_label("UR")["[(CH4N2O)3+H]+"].mz
        mz = np.append(mz, trimer * (1 + 15.0e-6))
        intensity = np.append(intensity, 3e4)
        hits, calibration = claim(reagent_library("UR"), mz, intensity)

        assert calibration.offset_ppm == pytest.approx(15.0, abs=0.5)
        assert any(h.cluster.label == "[(CH4N2O)3+H]+" for h in hits)

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
    reference - and then the ion is read as an isotopologue of it at 49x, every relative
    is 50x too large for the intensity gate to bite, and the real 14N line is
    skipped as if it were the monoisotopic one.
    """

    def test_the_impurity_line_is_below_the_ion(self):
        """The premise, pinned so the rest of this class cannot silently stop
        testing anything."""
        predicted_mz, _, labels = predict_isotopes("O3^N", -1, 0.98)
        assert labels[int(np.argmin(predicted_mz))] == "14N"

    def test_the_ion_is_not_an_isotopologue_of_its_own_impurity(self):
        """The failure this guards: a peak beside the base peak claimed as an
        'M0' isotope line, which on the gate was a ringing line of the base peak.
        """
        core = _by_label("NO3_15N")["[^NO3]-"]
        mz = np.array([core.mz, core.mz * (1 + 14.5e-6)])
        intensity = np.array([1e6, 8e3])
        hits, _ = claim(reagent_library("NO3_15N"), mz, intensity, purity=0.98)

        assert [h.isotope_label for h in hits] == [None]
        assert not any(h.isotope_label == "M0" for h in hits)

    def test_the_impurity_line_is_claimed_as_an_isotopologue(self):
        """It is the reagent's own 14N, 2% of the ion, and the brightest
        unclaimed peak of the ladder region until it is claimed."""
        predicted_mz, predicted_intensity, labels = predict_isotopes("O3^N", -1, 0.98)
        m0 = int(np.argmax(predicted_intensity))
        impurity = 1 - m0
        mz = np.array([float(predicted_mz[impurity]), float(predicted_mz[m0])])
        intensity = np.array([2.0e4, 1.0e6])
        hits, _ = claim(reagent_library("NO3_15N"), mz, intensity, purity=0.98)

        isotopologues = [h for h in hits if h.is_isotopologue]
        assert [h.isotope_label for h in isotopologues] == ["14N"]
        assert isotopologues[0].predicted_relative == pytest.approx(0.02, abs=0.005)


class TestTheEnvelopeReachesTheFloorItSearches:
    """A line the prediction omits is not a rounding error for a pass that
    claims peaks: it is a peak left in the residual for another stage to fit an
    analyte to. The scoring path's 1% cutoff is wrong here, and the 18O line is
    where it shows.
    """

    def test_the_default_envelope_omits_the_18o_line(self):
        """The premise: at the scoring threshold it is not there at all."""
        _, _, labels = predict_isotopes("C2H9N4O2", 1)

        assert "18O" not in labels

    def test_the_pass_predicts_down_to_its_own_floor(self):
        _, intensity, labels = predict_isotopes(
            "C2H9N4O2", 1, None, DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE
        )
        share = dict(zip(labels, intensity))

        assert "18O" in share
        assert share["18O"] / max(intensity) == pytest.approx(0.004, abs=5e-4)

    def test_the_18o_line_of_a_claimed_cluster_is_claimed(self):
        """The gate's case: the urea dimer's 18O line is the 19th brightest peak
        of a set A sample, and before this it was read as ethylene glycol on the
        urea channel once the pre-pass had claimed its parent.
        """
        mz, intensity = _spectrum(("CH5N2O", 1, 1e7), ("C2H9N4O2", 1, 2e7))
        hits, _ = claim(reagent_library("UR"), mz, intensity)

        assert "18O" in {h.isotope_label for h in hits}

    def test_the_old_floor_sat_between_the_two_18o_lines(self):
        """Why the floor moved as well as the threshold. A two-oxygen ion's 18O
        line is 0.411% of its parent and a one-oxygen ion's is 0.206%, so the
        previous 0.4% floor fell between them: it would have kept the urea
        dimer's and dropped the monomer's, which is not a distinction anything
        about the chemistry supports.
        """
        mz, intensity = _spectrum(("CH5N2O", 1, 2e7))
        old_floor, _ = claim(
            reagent_library("UR"), mz, intensity, isotopologue_min_relative=4e-3
        )
        now, _ = claim(reagent_library("UR"), mz, intensity)

        assert "18O" not in {h.isotope_label for h in old_floor}
        assert "18O" in {h.isotope_label for h in now}


class TestTheAnchorFloor:
    """An anchor moves every other mass in the pass, so it is held to a higher
    bar than a peak that only claims itself."""

    def test_a_dim_stray_does_not_become_an_anchor(self):
        """Otherwise a stray peak inside an absent anchor's wide window pulls
        the median offset and widens the tolerance for everything."""
        core = _by_label("BR")["[Br]-"]
        mz = np.array([core.mz * (1 + 15e-6), 300.0])
        intensity = np.array([50.0, 1e6])  # the stray is 5e-5 of the base peak
        _, calibration = claim(reagent_library("BR"), mz, intensity)

        assert calibration.anchors == ()

    def test_a_real_anchor_still_anchors(self):
        """`Br2-` runs 1.1-1.5% of the base peak on the gate's bromide sets,
        comfortably above the floor."""
        core = _by_label("BR")["[Br2]-"]
        mz = np.array([core.mz * (1 - 9e-6), 300.0])
        intensity = np.array([1.2e4, 1e6])  # 1.2% of the base peak
        _, calibration = claim(reagent_library("BR"), mz, intensity)

        assert [label for label, _ in calibration.anchors] == ["[Br2]-"]
        assert calibration.offset_ppm == pytest.approx(-9.0, abs=0.5)


class TestTheFragmentLadders:
    """The fourth family, which the pass claims after the stages: what the
    literature says a source breaks an analyte into, and how far."""

    def test_only_the_charge_transfer_source_names_one(self):
        assert [ladder.label for ladder in fragment_ladders("EASYIC_POS")] == [
            "monoterpene"
        ]
        for profile in ("BR", "IODIDE", "NO3", "NO3_15N", "UR", "EASYIC_NEG", "none"):
            assert fragment_ladders(profile) == (), profile

    def test_the_monoterpene_fragments_land_where_they_should(self):
        (ladder,) = fragment_ladders("EASYIC_POS")
        assert ladder.parent == "C10H16"
        masses = {fragment.label: fragment.mz for fragment in ladder.fragments}
        for label, expected in (
            ("[C7H9]+", 93.0699),
            ("[C6H8]+.", 80.0621),
            ("[C6H7]+", 79.0542),
            ("[C6H5]+", 77.0386),
            ("[C5H7]+", 67.0542),
        ):
            assert masses[label] == pytest.approx(expected, abs=5e-4), label

    def test_the_ratios_are_the_electron_ionization_spectrums(self):
        """Alpha-pinene's NIST spectrum: m/z 93 is the base peak and the radical
        cation 7.4% of it, so C7H9+ stands at 13.5 times the parent's ion."""
        (ladder,) = fragment_ladders("EASYIC_POS")
        ratios = {fragment.label: fragment for fragment in ladder.fragments}
        assert ratios["[C7H9]+"].literature_ratio == pytest.approx(13.51, abs=0.01)
        assert ratios["[C6H8]+."].literature_ratio == pytest.approx(1.35, abs=0.01)
        assert ratios["[C7H9]+"].max_ratio == pytest.approx(
            13.51 * DEFAULT_FRAGMENT_MAX_EXCESS, abs=0.05
        )

    def test_an_aromatics_own_ions_are_not_on_it(self):
        """Toluene's radical cation and hydride-abstraction ion, xylene's, and
        benzene's: the ladder must leave a component a peak of its own to be
        shown on, or the claim buries it whenever the parent is there too."""
        (ladder,) = fragment_ladders("EASYIC_POS")
        formulas = {fragment.formula for fragment in ladder.fragments}
        assert not formulas & {"C7H8", "C7H7", "C8H9", "C8H10", "C6H6"}

    def test_every_fragment_names_its_literature(self):
        for ladders in FRAGMENT_LADDERS.values():
            for ladder in ladders:
                assert ladder.references
                for fragment in ladder.fragments:
                    assert fragment.references, fragment.label
                    assert set(fragment.references) <= set(ladder.references)
                    assert set(fragment.references) <= set(SOURCE_ION_REFERENCES)
