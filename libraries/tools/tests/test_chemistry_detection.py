"""The chemistry detector: what a spectrum's reagent ions name, and when not.

One test per guard, each on a synthetic spectrum built from the library's own
masses, so that each decision is pinned to the rule that makes it: coverage,
dominance (the spectrum's size, the score, the anchors and the brightest ion's
rank), the label ratio and the margin, each threshold tested on both of its
sides. The spectra are what a source makes - the reagent's ladder as the
brightest peaks and a scatter of analytes below it - and what an ambient-ion or
electrospray spectrum is: many peaks of one size, none of them a reagent's.
"""

import json

import numpy as np
import pytest

from mascope_tools.composition.chemistry_detection import (
    DECISION_AMBIGUOUS,
    DECISION_NONE,
    DECISION_PROFILE,
    DECISION_UNOBSERVABLE,
    DETECTOR_VERSION,
    DetectionRules,
    candidate_profiles,
    detect_chemistry,
    fingerprint,
    label_partner,
)
from mascope_tools.composition.profiles import REAGENT_PROFILES
from mascope_tools.composition.reagents import (
    FAMILY_AIR,
    ion_mz,
    reagent_library,
)


#: The 81Br line of a bromide ion, above its 79Br one.
BR81 = 1.99795


def _analytes(count: int, start: float, height: float, seed: int = 0):
    """Peaks of no reagent: masses with a mass defect no library ion has."""
    rng = np.random.default_rng(seed)
    masses = start + np.arange(count) * 7.0 + 0.0371
    heights = height * rng.uniform(0.5, 1.0, count)
    return list(zip(masses.tolist(), heights.tolist()))


def spectrum(*peaks, shift_ppm: float = 0.0):
    """``(mz, intensity)`` arrays from ``(m/z or (formula, charge), height)``.

    :param shift_ppm: Moves every peak, as an axis acquired that far out does.
    """
    mz, intensity = [], []
    for where, height in peaks:
        value = ion_mz(*where) if isinstance(where, tuple) else float(where)
        mz.append(value * (1.0 + shift_ppm * 1e-6))
        intensity.append(float(height))
    order = np.argsort(mz)
    return np.asarray(mz)[order], np.asarray(intensity)[order]


def within(peaks, low, high):
    """What a scan of that range records of a spectrum."""
    mz, intensity = peaks
    kept = (mz >= low) & (mz <= high)
    return mz[kept], intensity[kept]


def joined(*spectra):
    """Several spectra as one."""
    return (
        np.concatenate([mz for mz, _ in spectra]),
        np.concatenate([intensity for _, intensity in spectra]),
    )


def nitrate_cims(label: str = "", remainder: float = 0.0, **kwargs):
    """A nitrate source: core, acid rungs and hydrate, analytes below.

    :param label: ``"^"`` for the 15N reagent.
    :param remainder: The other form's core and dimer, relative to these.
    """
    n = f"{label}N"
    other = "N" if label else "^N"
    peaks = [
        ((f"{n}O3", -1), 1.0e7),
        ((f"H{n}2O6", -1), 4.0e6),
        ((f"H2{n}3O9", -1), 6.0e5),
        ((f"H2{n}O4", -1), 2.0e5),
        *_analytes(40, 140.0, 4.0e4),
    ]
    if remainder:
        peaks += [
            ((f"{other}O3", -1), 1.0e7 * remainder),
            ((f"H{other}2O6", -1), 4.0e6 * remainder),
        ]
    return spectrum(*peaks, **kwargs)


def bromide_cims(anchors: bool = True, **kwargs):
    """A bromide source: both lines of the monomer, hydrate, dimer, trimer.

    :param anchors: False leaves out the monomer and the dimer.
    """
    ladder = [(("BrH2O", -1), 1.5e6), (("Br3", -1), 3.0e5)]
    if anchors:
        ladder += [
            (("Br", -1), 1.0e7),
            (ion_mz("Br", -1) + BR81, 9.7e6),
            (("Br2", -1), 1.5e5),
        ]
    return spectrum(*ladder, *_analytes(40, 140.0, 3.0e4), **kwargs)


def uronium_cims(**kwargs):
    """A uronium source: protonated urea monomer and dimer, analytes below."""
    return spectrum(
        (("CH5N2O", 1), 1.0e7),
        (("C2H9N4O2", 1), 5.0e6),
        (("C3H13N6O3", 1), 1.0e4),
        *_analytes(40, 130.0, 5.0e4),
        **kwargs,
    )


def ambient_negative(**kwargs):
    """An ambient-ion spectrum: natural nitrate among other ions of its size."""
    return spectrum(
        (("NO3", -1), 1.0e6),
        (("HN2O6", -1), 3.0e5),
        (("HSO4", -1), 9.0e5),
        (("CO3", -1), 6.0e5),
        *_analytes(40, 140.0, 5.0e5),
        **kwargs,
    )


def detect(peaks, polarity="-", instrument_type="orbi", **kwargs):
    mz, intensity = peaks
    return detect_chemistry(
        mz, intensity, polarity=polarity, instrument_type=instrument_type, **kwargs
    )


def evidence_of(found, profile):
    return next(item for item in found.evidence if item.profile == profile)


class TestTheFingerprint:
    def test_it_is_the_profiles_own_ions_not_the_airs(self):
        """Every negative library carries the air's nitrate: it names no reagent."""
        for name in ("BR", "NO3", "UR", "EASYIC_POS"):
            ions = fingerprint(name)
            assert ions
            assert all(cluster.family != FAMILY_AIR for cluster in ions)
            assert any(cluster.anchor for cluster in ions)
        assert any(c.family == FAMILY_AIR for c in reagent_library("BR"))
        assert "[NO3]-" not in {cluster.label for cluster in fingerprint("BR")}

    def test_a_profile_without_a_reagent_has_none(self):
        assert fingerprint("ESI_POS") == ()
        assert fingerprint("none") == ()

    def test_charge_transfer_competes_only_on_an_orbitrap(self):
        on_orbi = {p.name for p in candidate_profiles("+", "orbi")}
        on_tof = {p.name for p in candidate_profiles("+", "tof")}
        assert on_orbi == {"UR", "EASYIC_POS"}
        assert on_tof == {"UR"}
        assert "EASYIC_NEG" not in {p.name for p in candidate_profiles("-", "tof")}

    def test_the_labelled_nitrates_partner_is_read_off_the_reagents(self):
        assert label_partner(REAGENT_PROFILES["NO3_15N"]).name == "NO3"
        assert label_partner(REAGENT_PROFILES["NO3"]) is None


class TestACall:
    @pytest.mark.parametrize(
        "peaks, polarity, expected",
        [
            (nitrate_cims(), "-", "NO3"),
            (nitrate_cims("^"), "-", "NO3_15N"),
            (bromide_cims(), "-", "BR"),
            (uronium_cims(), "+", "UR"),
        ],
    )
    def test_a_reagent_source_names_its_profile(self, peaks, polarity, expected):
        found = detect(peaks, polarity=polarity)
        assert found.decision == DECISION_PROFILE, found.reasons
        assert found.profile == expected
        assert found.score > 0.8

    def test_the_isotope_lines_of_its_ions_count_as_its_own(self):
        """Bromide's 81Br line is half its monomer's signal."""
        found = detect(bromide_cims())
        br = evidence_of(found, "BR")
        mz, intensity = bromide_cims()
        line = intensity[np.argmin(np.abs(mz - (ion_mz("Br", -1) + BR81)))]
        assert br.score * intensity.sum() >= line + 1.0e7

    def test_the_answer_stores_as_json_with_its_version(self):
        stored = json.loads(json.dumps(detect(nitrate_cims()).to_json()))
        assert stored["detector"] == DETECTOR_VERSION
        assert stored["decision"] == DECISION_PROFILE
        assert stored["profile"] == "NO3"
        by_profile = {item["profile"]: item for item in stored["candidates"]}
        assert by_profile["NO3"]["anchors_found"][0]["label"] == "[NO3]-"
        assert by_profile["NO3"]["brightest"]["rank"] == 1
        assert stored["rules"] == DetectionRules().to_json()

    def test_the_stored_rules_are_all_of_them(self):
        """The windows too: they decide which anchors are found and which ions
        are claimed. Every field differs from its default, so one left out of
        the record comes back as the default."""
        rules = DetectionRules(
            min_peaks=10,
            min_score=0.3,
            top_rank=3,
            margin=3.0,
            labelled_ratio=4.0,
            natural_ratio=0.05,
            anchor_ppm={"orbi": 40.0, "tof": 150.0},
            claim_ppm={"orbi": 4.0, "tof": 15.0},
            default_anchor_ppm=150.0,
            default_claim_ppm=15.0,
        )
        stored = json.loads(json.dumps(detect(nitrate_cims(), rules=rules).to_json()))
        assert DetectionRules(**stored["rules"]) == rules


class TestCoverage:
    def test_a_range_above_every_anchor_is_unobservable_not_absent(self):
        found = detect(
            within(nitrate_cims(), 300.0, 600.0), scan_ranges=[(300.0, 600.0)]
        )
        assert found.decision == DECISION_UNOBSERVABLE
        assert all(not item.observable for item in found.evidence)

    def test_only_observable_fingerprints_compete(self):
        """The nitrate anchors are below the range; the bromide dimer is not."""
        found = detect(
            within(nitrate_cims(), 128.0, 600.0), scan_ranges=[(128.0, 600.0)]
        )
        observable = {item.profile for item in found.evidence if item.observable}
        assert "NO3" not in observable
        assert "BR" in observable
        assert found.decision == DECISION_NONE
        assert any(
            "NO3" in reason and "scan ranges" in reason for reason in found.reasons
        )

    def test_one_anchor_in_range_is_enough_to_read(self):
        found = detect(
            within(nitrate_cims(), 100.0, 600.0), scan_ranges=[(100.0, 600.0)]
        )
        assert found.decision == DECISION_PROFILE
        assert found.profile == "NO3"
        assert evidence_of(found, "NO3").anchors_in_range == ("[NO3+HNO3]-",)


class TestDominance:
    def test_natural_nitrate_among_its_peers_is_no_call(self):
        """The guard that separates nitrate CIMS from an ambient spectrum."""
        found = detect(ambient_negative())
        assert found.decision == DECISION_NONE
        assert evidence_of(found, "NO3").score < DetectionRules().min_score

    def test_the_score_threshold_decides(self):
        peaks = ambient_negative()
        score = evidence_of(detect(peaks), "NO3").score
        assert detect(peaks, rules=DetectionRules(min_score=score * 0.99)).called
        assert not detect(peaks, rules=DetectionRules(min_score=score * 1.01)).called

    def test_a_spectrum_of_a_few_peaks_is_not_read(self):
        """An ambient acquisition at its noise floor: nitrate is all it holds."""
        peaks = spectrum((("NO3", -1), 160.0), (("HN2O6", -1), 35.0), (99.0371, 30.0))
        found = detect(peaks)
        assert found.decision == DECISION_NONE
        assert evidence_of(found, "NO3").score > 0.8
        assert any("holds 3 peaks" in reason for reason in found.reasons)
        assert detect(peaks, rules=DetectionRules(min_peaks=3)).profile == "NO3"

    def test_the_brightest_ion_must_rank_near_the_top(self):
        """A ladder that claims enough but sits under bigger peaks."""
        peaks = joined(nitrate_cims(), spectrum(*_analytes(6, 300.0, 3.0e7, seed=1)))
        loose = DetectionRules(min_score=0.05, top_rank=7)
        found = detect(peaks, rules=loose)
        assert found.decision == DECISION_PROFILE
        assert evidence_of(found, "NO3").brightest.rank == 7
        strict = DetectionRules(min_score=0.05, top_rank=6)
        assert detect(peaks, rules=strict).decision == DECISION_NONE

    def test_the_brightest_ion_need_not_be_an_anchor(self):
        """Bromide's monomer below a range that starts at m/z 82: its hydrate
        leads, and the dimer, far down, still says where the masses sit."""
        found = detect(within(bromide_cims(), 82.0, 750.0), scan_ranges=[(82.0, 750.0)])
        assert found.profile == "BR"
        br = evidence_of(found, "BR")
        assert br.brightest.label == "[Br+H2O]-"
        assert [anchor.label for anchor in br.anchors_found] == ["[Br2]-"]

    def test_a_ladder_without_its_anchors_is_no_call(self):
        found = detect(bromide_cims(anchors=False))
        assert evidence_of(found, "BR").score >= DetectionRules().min_score
        assert found.decision == DECISION_NONE
        assert any("none of its anchors" in reason for reason in found.reasons)

    def test_an_electrospray_spectrum_is_no_call(self):
        found = detect(spectrum(*_analytes(40, 100.0, 1.0e6)), polarity="+")
        assert found.decision == DECISION_NONE


class TestTheLabelRatio:
    def test_a_15n_source_with_its_14n_remainder_is_15n(self):
        found = detect(nitrate_cims("^", remainder=0.05))
        assert found.profile == "NO3_15N"
        assert any("NO3_15N over NO3" in reason for reason in found.reasons)

    def test_natural_15n_on_a_14n_source_is_14n(self):
        """Nitrate's natural 15N line sits on the labelled reagent's mass."""
        found = detect(nitrate_cims(remainder=0.0037))
        assert found.profile == "NO3"
        labelled = evidence_of(found, "NO3_15N")
        assert labelled.anchors_found, "the natural line is found, and outweighed"
        assert any("natural abundance" in reason for reason in found.reasons)

    @pytest.mark.parametrize(
        "peaks",
        [nitrate_cims("^", remainder=0.5), nitrate_cims(remainder=0.19)],
        ids=["a 15N source half 14N", "a 14N source with 15N fifty times natural"],
    )
    def test_a_mixture_is_ambiguous(self, peaks):
        found = detect(peaks)
        assert found.decision == DECISION_AMBIGUOUS
        assert any("mixed" in reason for reason in found.reasons)

    @pytest.mark.parametrize(
        "anchors, hydrate, called, ruled_out",
        [
            (
                [(("^NO3", -1), 1.0e6), (("H^N2O6", -1), 4.0e5), (("NO3", -1), 1.0e5)],
                (("H2NO4", -1), 5.0e6),
                "NO3_15N",
                "NO3",
            ),
            (
                [(("NO3", -1), 1.0e6), (("HN2O6", -1), 4.0e5), (("^NO3", -1), 3.0e4)],
                (("H2^NO4", -1), 5.0e6),
                "NO3",
                "NO3_15N",
            ),
        ],
        ids=["15N anchors, 14N hydrate", "14N anchors, 15N hydrate"],
    )
    def test_the_form_the_ratio_rules_out_never_leads(
        self, anchors, hydrate, called, ruled_out
    ):
        """Whatever its other rungs claim: here the ruled-out form's hydrate
        out-weighs the whole ladder of the form its anchors lose to."""
        found = detect(
            spectrum(*anchors, (hydrate[0], hydrate[1]), *_analytes(40, 140.0, 1.0e3))
        )
        assert evidence_of(found, ruled_out).score > evidence_of(found, called).score
        assert found.profile == called

    def test_only_anchor_pairs_with_both_masses_recorded_are_compared(self):
        """A 14N source scanned from m/z 62.5: its monomer is not recorded, the
        monomer's natural 15N line is. Set against the dimer alone, that line
        weighs fifteen percent of it; the dimers, both recorded, say 14N."""
        peaks = spectrum(
            (("NO3", -1), 4.0e7),
            (("^NO3", -1), 4.0e7 * 0.0037),
            (("HN2O6", -1), 1.0e6),
            (("H2N3O9", -1), 1.5e5),
            (("H2NO4", -1), 2.0e5),
            *_analytes(40, 140.0, 4.0e4),
        )
        found = detect(within(peaks, 62.5, 600.0), scan_ranges=[(62.5, 600.0)])
        assert evidence_of(found, "NO3").anchors_in_range == ("[NO3+HNO3]-",)
        assert [
            anchor.label for anchor in evidence_of(found, "NO3_15N").anchors_found
        ] == ["[^NO3]-"]
        assert found.profile == "NO3", found.reasons
        assert any("natural abundance" in reason for reason in found.reasons)

    def test_a_labelled_anchor_not_recorded_drops_its_pair_too(self):
        """A 15N source, dimer-heavy, whose segments skip m/z 126-128: its
        labelled dimer is not recorded, its 14N remainder's dimer is. Set
        against both remainder anchors, the monomer weighs twice them; the
        monomers, both recorded, say 15N."""
        peaks = spectrum(
            (("^NO3", -1), 1.0e6),
            (("H2^N3O9", -1), 6.0e5),
            (("H2^NO4", -1), 2.0e5),
            (("NO3", -1), 1.0e5),
            (("HN2O6", -1), 4.0e5),
            *_analytes(40, 140.0, 4.0e4),
        )
        found = detect(peaks, scan_ranges=[(50.0, 126.0), (128.0, 600.0)])
        assert evidence_of(found, "NO3_15N").anchors_in_range == ("[^NO3]-",)
        assert found.profile == "NO3_15N", found.reasons

    def test_a_weak_mixture_does_not_hold_up_another_reagent(self):
        """Traces of both nitrates in a bromide spectrum, half and half."""
        traces = spectrum(
            (("NO3", -1), 2.0e4),
            (("^NO3", -1), 2.0e4),
            (("HN2O6", -1), 5.0e3),
            (("H^N2O6", -1), 5.0e3),
        )
        found = detect(joined(bromide_cims(), traces))
        assert found.profile == "BR"
        assert evidence_of(found, "NO3").anchors_found
        assert evidence_of(found, "NO3_15N").anchors_found

    def test_the_labelled_threshold_decides(self):
        peaks = nitrate_cims("^", remainder=0.25)
        assert detect(peaks, rules=DetectionRules(labelled_ratio=3.9)).profile == (
            "NO3_15N"
        )
        assert detect(peaks, rules=DetectionRules(labelled_ratio=4.1)).decision == (
            DECISION_AMBIGUOUS
        )

    def test_the_natural_threshold_decides(self):
        peaks = nitrate_cims(remainder=0.19)
        assert detect(peaks, rules=DetectionRules(natural_ratio=0.2)).profile == "NO3"
        assert detect(peaks, rules=DetectionRules(natural_ratio=0.18)).decision == (
            DECISION_AMBIGUOUS
        )


class TestTheMargin:
    def test_two_reagents_alike_are_ambiguous(self):
        """A source dosed with bromide and nitrate at once."""
        found = detect(joined(nitrate_cims(), bromide_cims()))
        assert found.decision == DECISION_AMBIGUOUS
        assert any("BR claims" in reason for reason in found.reasons)

    def test_the_margin_threshold_decides(self):
        bromide = spectrum((("Br", -1), 2.0e6), (ion_mz("Br", -1) + BR81, 1.9e6))
        peaks = joined(nitrate_cims(), bromide)
        found = detect(peaks)
        ratio = evidence_of(found, "NO3").score / evidence_of(found, "BR").score
        assert detect(peaks, rules=DetectionRules(margin=ratio * 0.99)).profile == "NO3"
        assert detect(peaks, rules=DetectionRules(margin=ratio * 1.01)).decision == (
            DECISION_AMBIGUOUS
        )


class TestTheWindows:
    """On the axis as acquired: a parked file has no calibration to read."""

    def test_a_tof_axis_150_ppm_out_is_still_read(self):
        found = detect(bromide_cims(shift_ppm=150.0), instrument_type="tof")
        assert found.profile == "BR"

    def test_an_orbitrap_axis_30_ppm_out_is_still_read(self):
        found = detect(bromide_cims(shift_ppm=-30.0), instrument_type="orbi")
        assert found.profile == "BR"

    def test_an_orbitrap_axis_150_ppm_out_is_not(self):
        found = detect(bromide_cims(shift_ppm=150.0), instrument_type="orbi")
        assert not found.called

    def test_an_unknown_class_takes_the_widest_window(self):
        found = detect(bromide_cims(shift_ppm=150.0), instrument_type=None)
        assert found.profile == "BR"

    @pytest.mark.parametrize(
        "instrument_type, off_ppm, claimed",
        [
            ("orbi", 3.0, True),
            ("orbi", 10.0, False),
            ("tof", 15.0, True),
            ("tof", 30.0, False),
            (None, 15.0, True),
            (None, 30.0, False),
        ],
    )
    def test_the_other_ions_are_claimed_in_the_claim_window(
        self, instrument_type, off_ppm, claimed
    ):
        """The anchors on their masses and the hydrate that far off its own:
        within 5 ppm on an Orbitrap and 20 on a TOF it is the reagent's, past
        that it is not, however alone it sits."""
        ladder = [
            (("NO3", -1), 1.0e7),
            (("HN2O6", -1), 4.0e6),
            (("H2N3O9", -1), 6.0e5),
            *_analytes(40, 140.0, 4.0e4),
        ]
        hydrate = (ion_mz("H2NO4", -1) * (1.0 + off_ppm * 1e-6), 2.0e5)
        without = detect(spectrum(*ladder), instrument_type=instrument_type)
        found = detect(spectrum(*ladder, hydrate), instrument_type=instrument_type)
        assert evidence_of(found, "NO3").claimed == (
            evidence_of(without, "NO3").claimed + int(claimed)
        )


class TestNothingToRead:
    def test_a_missing_or_negative_intensity_counts_as_nothing(self):
        mz, intensity = nitrate_cims()
        broken = intensity.copy()
        broken[np.argsort(mz)[-5:]] = np.nan
        broken[np.argsort(mz)[-10:-5]] = -1.0e9
        found = detect((mz, broken))
        assert found.profile == "NO3"
        assert 0.0 < found.score <= 1.0

    def test_peaks_of_no_intensity_are_no_peaks(self):
        mz, intensity = nitrate_cims()
        found = detect((mz, np.zeros_like(intensity)))
        assert found.decision == DECISION_NONE
        assert found.reasons == ("the spectrum holds no peaks",)

    def test_no_peaks_is_no_call(self):
        found = detect((np.array([]), np.array([])))
        assert found.decision == DECISION_NONE
        assert found.peaks == 0

    def test_a_polarity_without_fingerprints_is_no_call(self):
        found = detect(uronium_cims(), polarity="x")
        assert found.decision == DECISION_NONE
