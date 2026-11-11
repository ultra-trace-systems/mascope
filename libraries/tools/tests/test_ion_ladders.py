"""The ladders the reagent pass climbs: a source's solvent clusters and the
nitrate reagent's water ladder (step 3.3d of the assignment plan).

A ladder claims a rung on its own shape, not on a work naming the ion, so what
is pinned here is each rule of that shape: the core the ladder grows from, the
rung below, the mass and the step, the falling heights, and the bar a rung
clears before it takes a peak a stage could read as a molecule.
"""

import numpy as np
import pytest

from mascope_tools.composition.heuristic_filter import (
    predict_isotopes,
    predict_resolved_isotopes,
)
from mascope_tools.composition.profiles import (
    POSITIVE_SOURCE_SOLVENTS,
    REAGENT_PROFILES,
)
from mascope_tools.composition.reagents import (
    DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE,
    FAMILIES,
    FAMILY_REAGENT,
    FAMILY_SOLVENT,
    HELD_MOLECULE_SHOWN,
    HELD_NO_RUNG_BELOW,
    HELD_RISES,
    HELD_SPACING,
    HELD_WATER_DISPLACES,
    ION_LADDERS,
    SOURCE_ION_REFERENCES,
    ReagentCalibration,
    covalent_readings,
    ion_ladders,
    ion_mz,
    match_ion_ladders,
    match_reagent_clusters,
    reagent_library,
    solvent_ladders,
)


#: The instrument window an Orbitrap run claims in.
ORBI_PPM = 3.0

#: The channels a charge-transfer run searches once its fingerprints open them.
CHARGE_TRANSFER = ("[M]+.", "[M+H]+", "[M-H]+", "[M-CH3]+")

#: The fluoranthene beam, the base peak of every spectrum below, so the
#: heights the tests set are read against a fixed base.
BEAM = ("C16H10", 1, 1e7)


def _spectrum(*ions: tuple[str, int, float], shift_ppm: float = 0.0):
    """Each ion's predicted envelope at a given height, its masses moved."""
    mz: list[float] = []
    intensity: list[float] = []
    for formula, charge, height in ions:
        predicted_mz, predicted_intensity, _ = predict_isotopes(
            formula, charge, None, DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE
        )
        base = max(predicted_intensity)
        for one_mz, one_intensity in zip(predicted_mz, predicted_intensity):
            mz.append(float(one_mz) * (1.0 + shift_ppm * 1e-6))
            intensity.append(height * float(one_intensity) / base)
    order = np.argsort(mz)
    return np.asarray(mz)[order], np.asarray(intensity)[order]


def _climb(
    *ions: tuple[str, int, float],
    profile: str = "EASYIC_POS",
    channels=CHARGE_TRANSFER,
    shift_ppm: float = 0.0,
    **kwargs,
):
    """The pass as the backend runs it: the library first, the ladders on what
    it left and on the masses its anchors corrected."""
    mz, intensity = _spectrum(*ions, shift_ppm=shift_ppm)
    hits, calibration = match_reagent_clusters(
        reagent_library(profile), mz, intensity, claim_ppm=ORBI_PPM
    )
    rungs, summary = match_ion_ladders(
        ion_ladders(profile),
        mz,
        intensity,
        calibration=calibration,
        taken=[hit.index for hit in hits],
        channels=channels,
        claim_ppm=ORBI_PPM,
        **kwargs,
    )
    return rungs, summary, hits


def _claimed(rungs) -> dict[str, dict]:
    """The rungs claimed, by label, with what licensed each."""
    return {hit.cluster.label: hit.ladder for hit in rungs if not hit.is_isotopologue}


class TestTheLadders:
    def test_the_positive_sources_list_their_solvents(self):
        """The profiles carry the solvent lists, per source."""
        for name, profile in REAGENT_PROFILES.items():
            expected = POSITIVE_SOURCE_SOLVENTS if name in ("EASYIC_POS", "UR") else ()
            assert profile.source_solvents == expected, name

    def test_which_profiles_climb_which_ladders(self):
        assert {
            name: [ladder.name for ladder in ladders]
            for name, ladders in ION_LADDERS.items()
        } == {
            "EASYIC_POS": ["methanol", "ethanol", "acetone"],
            "UR": ["methanol", "ethanol", "acetone"],
            "NO3": ["[NO3+2xHNO3]- hydrates"],
            "NO3_15N": ["[^NO3+2xH^NO3]- hydrates"],
        }
        assert ion_ladders("BR") == ion_ladders("ESI_POS") == ()

    @pytest.mark.parametrize(
        "label, expected",
        [
            ("[(C2H6O)2+H]+", 93.0910),
            ("[C2H6O+H3O]+", 65.0597),
            ("[(C2H6O)2-H]+", 91.0754),
            ("[(C3H6O)2+H]+", 117.0910),
            ("[(CH4O)2+H]+", 65.0597),
        ],
    )
    def test_a_rung_lands_where_it_should(self, label, expected):
        """The masses the reference engine's reading and the brief name."""
        rungs = {
            rung.cluster.label: rung.cluster
            for ladder in ion_ladders("EASYIC_POS")
            for rung in ladder.rungs
        }
        assert rungs[label].mz == pytest.approx(expected, abs=5e-4)

    def test_every_solvent_rung_names_its_literature(self):
        """Held to the library's rule: each rung carries the works that name
        it, and every work resolves."""
        for ladder in ion_ladders("EASYIC_POS"):
            for rung in ladder.rungs:
                assert rung.cluster.family == FAMILY_SOLVENT in FAMILIES
                assert rung.cluster.references and not rung.cluster.observed
                assert set(rung.cluster.references) <= set(SOURCE_ION_REFERENCES)

    def test_the_reagents_hydrates_say_what_shows_them(self):
        """No work found names the dimer core's hydrates, so they say the
        ladder shows them, as a library ion no work names says what does."""
        (ladder,) = ion_ladders("NO3")
        assert [core.cluster.label for core in ladder.cores] == ["[NO3+2xHNO3]-"]
        for rung in ladder.rungs:
            assert rung.cluster.family == FAMILY_REAGENT
            assert rung.water and rung.cluster.observed and not rung.cluster.references

    def test_the_labelled_reagents_ladder_is_labelled(self):
        (ladder,) = ion_ladders("NO3_15N")
        assert ladder.cores[0].cluster.formula == "H2O9^N3"
        assert ladder.rungs[0].cluster.mz == pytest.approx(
            ion_mz("H4O10^N3", -1), abs=1e-6
        )

    def test_water_alone_is_no_ladder(self):
        """The protonated water ladder is the air's; water on the list puts
        the hydrated rungs on the others."""
        assert solvent_ladders(("H2O",)) == ()
        dry = solvent_ladders(("C2H6O",))[0]
        assert not any(rung.water for rung in dry.rungs)
        assert any(
            rung.water for rung in solvent_ladders(POSITIVE_SOURCE_SOLVENTS)[1].rungs
        )

    def test_no_rung_is_a_cylinder_components_ion(self):
        """The certified components' own ions, through every channel the
        charge-transfer source reads them through, are no rung's."""
        components = (
            "C6H6",
            "C7H8",
            "C8H10",
            "C8H8",
            "C5H8",
            "C3H6O",
            "C6H12O",
            "C10H16",
            "CH4O",
            "C2H4O",
            "C2H3N",
            "C9H12",
        )
        targets = []
        for neutral in components:
            for notation in ("[M]+.", "[M+H]+", "[M-H]+"):
                counts = dict(_counts(neutral))
                counts["H"] += {"[M]+.": 0, "[M+H]+": 1, "[M-H]+": -1}[notation]
                targets.append(ion_mz("".join(f"{k}{v}" for k, v in counts.items()), 1))
        for ladder in ion_ladders("EASYIC_POS"):
            for rung in ladder.rungs:
                for target in targets:
                    assert abs(rung.cluster.mz - target) / target * 1e6 > 5.0


def _counts(formula: str) -> dict[str, int]:
    from mascope_tools.composition.utils import parse_composition

    return dict(parse_composition(formula))


class TestCovalentReadings:
    def test_the_ethanol_dimer_reads_as_no_molecule(self):
        """Protonated, its neutral C4H12O2 has a double-bond equivalent of -1."""
        assert covalent_readings("C4H13O2", 1, CHARGE_TRANSFER) == ()

    def test_the_acetone_dimer_reads_as_an_acid(self):
        assert covalent_readings("C6H13O2", 1, CHARGE_TRANSFER) == (
            ("[M+H]+", "C6H12O2"),
            ("[M-H]+", "C6H14O2"),
            ("[M-CH3]+", "C7H16O2"),
        )

    def test_electron_transfer_leaves_a_radical(self):
        """The neutral of an even-electron cation through electron transfer is
        a radical, which no stage names as the molecule."""
        assert covalent_readings("C6H13O2", 1, ("[M]+.",)) == ()

    def test_a_carbon_free_neutral_is_no_reading(self):
        """No grid searches one: the nitrate core's hydrate through the
        reagent's own channel is nitric acid and water."""
        assert covalent_readings("H4N3O10", -1, ("[M+NO3]-",)) == ()
        assert covalent_readings("H4O10^N3", -1, ("[M+^NO3]-",)) == ()

    def test_a_channel_of_the_other_polarity_or_an_unreadable_one_reads_nothing(self):
        assert covalent_readings("C6H13O2", 1, ("[M-H]-", "not a mechanism")) == ()

    def test_a_channel_with_a_loss_is_none_a_stage_searches(self):
        assert covalent_readings("C6H13O2", 1, ("[M+H-H2O]+",)) == ()

    def test_each_channel_once_in_either_notation(self):
        assert covalent_readings("C6H13O2", 1, ("[M+H]+", "+H+")) == (
            ("[M+H]+", "C6H12O2"),
        )


class TestClaimingARung:
    def test_a_rung_is_claimed_above_its_core_with_its_lines(self):
        """The ethanol dimer, an ion no stage can read, above protonated
        ethanol. The core is not claimed: it is the sample's ethanol to the
        stages, and the ladder's evidence."""
        rungs, summary, _ = _climb(BEAM, ("C2H7O", 1, 1e6), ("C4H13O2", 1, 2e6))

        claimed = _claimed(rungs)
        assert list(claimed) == ["[(C2H6O)2+H]+"]
        record = claimed["[(C2H6O)2+H]+"]
        assert record["ladder"] == "ethanol"
        assert record["grew_from"]["ion"] == "[C2H6O+H]+"
        assert record["below"]["step"] == "C2H6O"
        assert record["below"]["ratio"] == pytest.approx(2.0)
        assert record["displaced"] == []
        lines = [hit for hit in rungs if hit.is_isotopologue]
        assert {hit.isotope_label for hit in lines} >= {"13C"}
        assert summary["cores"]["[C2H6O+H]+"] is True
        assert (summary["claimed"], summary["claimed_isotopologues"]) == (
            1,
            len(lines),
        )

    def test_no_core_no_ladder(self):
        """A dimer's peak alone is not the solvent's."""
        rungs, summary, _ = _climb(BEAM, ("C4H13O2", 1, 2e6))

        assert rungs == []
        assert summary["held"][HELD_NO_RUNG_BELOW] == 1

    def test_a_rung_at_the_noise_floor_is_not_claimed(self):
        """A rung is a claim in its own right, held to the library's floor: a
        ten-thousandth of the base peak."""
        rungs, _, _ = _climb(BEAM, ("C2H7O", 1, 1e6), ("C4H13O2", 1, 5e2))

        assert rungs == []

    @pytest.mark.parametrize(
        "dimer, hydrate, below",
        [
            (5e5, 2e5, ("[(CH4O)2+H]+", "H2O")),
            (2e5, 5e5, ("[CH4O+H3O]+", "CH4O")),
            (None, 5e5, ("[CH4O+H3O]+", "CH4O")),
        ],
        ids=["dimer", "hydrate", "hydrate-alone"],
    )
    def test_a_rung_steps_from_the_tallest_rung_below_it(self, dimer, hydrate, below):
        """Methanol's dimer on a hydronium stands one water above the dimer
        and one methanol above methanol on a hydronium; the record names the
        taller, and either alone holds it."""
        ions = [BEAM, ("CH5O", 1, 1e6), ("CH7O2", 1, hydrate), ("C2H11O3", 1, 1e5)]
        if dimer is not None:
            ions.append(("C2H9O2", 1, dimer))
        rungs, _, _ = _climb(*ions)

        record = _claimed(rungs)["[(CH4O)2+H3O]+"]
        assert (record["below"]["ion"], record["below"]["step"]) == below

    def test_a_trace_of_the_solvent_is_no_core(self):
        """A core is a loud ion: under an anchor's share of the base peak it
        is the sample's ethanol, not a source's vapour."""
        rungs, _, _ = _climb(BEAM, ("C2H7O", 1, 5e3), ("C4H13O2", 1, 1e4))

        assert rungs == []

    def test_the_ladder_is_contiguous(self):
        """The acetone trimer stands on the dimer, or on nothing."""
        rungs, summary, _ = _climb(BEAM, ("C3H7O", 1, 3e6), ("C9H19O3", 1, 5e5))
        assert rungs == []
        assert summary["held"][HELD_NO_RUNG_BELOW] == 1

        rungs, _, _ = _climb(
            BEAM, ("C3H7O", 1, 3e6), ("C6H13O2", 1, 1e6), ("C9H19O3", 1, 5e5)
        )
        claimed = _claimed(rungs)
        assert list(claimed) == ["[(C3H6O)2+H]+", "[(C3H6O)3+H]+"]
        assert claimed["[(C3H6O)3+H]+"]["below"]["ion"] == "[(C3H6O)2+H]+"
        assert claimed["[(C3H6O)3+H]+"]["grew_from"]["ion"] == "[C3H6O+H]+"

    def test_a_rung_off_its_mass_is_not_the_ladders(self):
        """A peak 5 ppm off a rung's corrected mass is another ion."""
        mz, intensity = _spectrum(BEAM, ("C2H7O", 1, 1e6), ("C4H13O2", 1, 2e6))
        dimer = int(np.argmin(np.abs(mz - ion_mz("C4H13O2", 1))))
        mz[dimer] *= 1.0 + 5e-6
        rungs, _ = match_ion_ladders(
            ion_ladders("EASYIC_POS"),
            mz,
            intensity,
            calibration=ReagentCalibration(0.0, ORBI_PPM),
            taken=[],
            channels=CHARGE_TRANSFER,
            claim_ppm=ORBI_PPM,
        )
        assert _claimed(rungs) == {}

    def test_the_step_is_one_neutrals_exact_mass(self):
        """Each peak inside the window, one high and one low: the step between
        them is 3.6 ppm of the rung's mass off the solvent's, outside it."""
        mz, intensity = _spectrum(BEAM, ("C2H7O", 1, 1e6), ("C4H13O2", 1, 2e6))
        core = int(np.argmin(np.abs(mz - ion_mz("C2H7O", 1))))
        dimer = int(np.argmin(np.abs(mz - ion_mz("C4H13O2", 1))))
        mz[core] *= 1.0 - 2.9e-6
        mz[dimer] *= 1.0 + 2.1e-6
        rungs, summary = match_ion_ladders(
            ion_ladders("EASYIC_POS"),
            mz,
            intensity,
            calibration=ReagentCalibration(0.0, ORBI_PPM),
            taken=[],
            channels=CHARGE_TRANSFER,
            claim_ppm=ORBI_PPM,
        )
        assert _claimed(rungs) == {}
        assert summary["held"][HELD_SPACING] == 1

    def test_the_heights_fall_along_the_ladder(self):
        """A dimer ten times its monomer carries something else."""
        rungs, summary, _ = _climb(BEAM, ("C2H7O", 1, 1e5), ("C4H13O2", 1, 1e6))

        assert _claimed(rungs) == {}
        assert summary["held"][HELD_RISES] == 1

    def test_the_masses_the_anchors_corrected(self):
        """A spectrum 6 ppm high, its beam anchoring the pass: the ladder is
        claimed against the corrected masses, as the library is."""
        rungs, _, hits = _climb(
            BEAM, ("C2H7O", 1, 1e6), ("C4H13O2", 1, 2e6), shift_ppm=6.0
        )
        assert any(hit.cluster.label == "[C16H10]+" for hit in hits)
        assert list(_claimed(rungs)) == ["[(C2H6O)2+H]+"]
        assert rungs[0].mz_error_ppm == pytest.approx(6.0, abs=0.1)

    def test_a_peak_the_library_took_is_no_rung(self):
        mz, intensity = _spectrum(BEAM, ("C2H7O", 1, 1e6), ("C4H13O2", 1, 2e6))
        dimer = int(np.argmin(np.abs(mz - ion_mz("C4H13O2", 1))))
        rungs, _ = match_ion_ladders(
            ion_ladders("EASYIC_POS"),
            mz,
            intensity,
            calibration=ReagentCalibration(0.0, ORBI_PPM),
            taken=[dimer],
            channels=CHARGE_TRANSFER,
            claim_ppm=ORBI_PPM,
        )
        assert rungs == []

    def test_a_peak_the_library_took_is_no_rungs_line(self):
        """The rung is claimed, but its 13C line's peak, taken before the
        ladders, stays the library's: one peak, one reagent row."""
        mz, intensity = _spectrum(BEAM, ("C2H7O", 1, 1e6), ("C4H13O2", 1, 2e6))

        def climb(taken):
            rungs, _ = match_ion_ladders(
                ion_ladders("EASYIC_POS"),
                mz,
                intensity,
                calibration=ReagentCalibration(0.0, ORBI_PPM),
                taken=taken,
                channels=CHARGE_TRANSFER,
                claim_ppm=ORBI_PPM,
            )
            assert list(_claimed(rungs)) == ["[(C2H6O)2+H]+"]
            return rungs

        (line,) = [
            hit.index
            for hit in climb([])
            if hit.is_isotopologue and hit.isotope_label == "13C"
        ]
        assert line not in {hit.index for hit in climb([line])}

    def test_nothing_to_climb(self):
        mz, intensity = _spectrum(BEAM)
        for ladders, mz_values in (((), mz), (ion_ladders("EASYIC_POS"), mz[:0])):
            rungs, summary = match_ion_ladders(
                ladders,
                mz_values,
                intensity[: len(mz_values)],
                calibration=ReagentCalibration(0.0, ORBI_PPM),
                taken=[],
                channels=CHARGE_TRANSFER,
                claim_ppm=ORBI_PPM,
            )
            assert rungs == [] and summary["claimed"] == 0


class TestDisplacingAMolecule:
    """The acetone dimer is also protonated C6H12O2. The rung displaces that
    reading only where its ladder outweighs what the spectrum shows of the
    molecule elsewhere: C6H12O2's radical cation through electron transfer,
    at m/z 116.083, beside a protonated acetone core of 3e6."""

    RADICAL = "C6H12O2"

    def test_a_molecule_shown_nowhere_else_is_the_ladders(self):
        """A faint dimer of a loud solvent: nothing but the shared peak speaks
        for the molecule."""
        rungs, _, _ = _climb(BEAM, ("C3H7O", 1, 3e6), ("C6H13O2", 1, 1e4))

        record = _claimed(rungs)["[(C3H6O)2+H]+"]
        assert {
            "ionization": "[M+H]+",
            "formula": "C6H12O2",
            "shown": None,
        } in record["displaced"]
        assert record["share"] < 1e-3

    def test_a_molecule_its_own_peak_speaks_for_holds_the_rung(self):
        """The radical cation at 5e5: the core is six times it, under the
        order of magnitude a ladder must stand over the molecule."""
        rungs, summary, _ = _climb(
            BEAM, ("C3H7O", 1, 3e6), ("C6H13O2", 1, 1e5), (self.RADICAL, 1, 5e5)
        )

        assert _claimed(rungs) == {}
        assert summary["held"][HELD_MOLECULE_SHOWN] == 1

    def test_a_ladder_that_outweighs_the_molecule_takes_the_peak(self):
        rungs, _, _ = _climb(
            BEAM, ("C3H7O", 1, 3e6), ("C6H13O2", 1, 1e5), (self.RADICAL, 1, 1e5)
        )

        record = _claimed(rungs)["[(C3H6O)2+H]+"]
        (protonated,) = [
            reading
            for reading in record["displaced"]
            if reading["ionization"] == "[M+H]+"
        ]
        assert protonated["shown"]["ionization"] == "[M]+."
        assert protonated["shown"]["core_ratio"] == pytest.approx(30.0, rel=1e-3)

    def test_the_margin_is_the_callers(self):
        rungs, _, _ = _climb(
            BEAM,
            ("C3H7O", 1, 3e6),
            ("C6H13O2", 1, 1e5),
            (self.RADICAL, 1, 5e5),
            displacement_margin=5.0,
        )

        assert list(_claimed(rungs)) == ["[(C3H6O)2+H]+"]

    def test_a_peak_a_claim_took_speaks_for_no_molecule(self):
        """The molecule's other ion is the hydride-bound dimer's peak, which
        the ladder claimed below: C6H12O2 less a hydride is
        [(C3H6O)2-H]+, so it is no evidence for the molecule."""
        rungs, _, _ = _climb(
            BEAM,
            ("C3H7O", 1, 3e6),
            ("C3H5O", 1, 3e6),
            ("C6H11O2", 1, 5e5),
            ("C6H13O2", 1, 1e5),
        )

        claimed = _claimed(rungs)
        assert set(claimed) == {"[(C3H6O)2-H]+", "[(C3H6O)2+H]+"}

    def test_a_run_that_reads_no_molecule_displaces_none(self):
        rungs, _, _ = _climb(
            BEAM,
            ("C3H7O", 1, 3e6),
            ("C6H13O2", 1, 1e5),
            (self.RADICAL, 1, 5e5),
            channels=("[M]+.",),
        )

        record = _claimed(rungs)["[(C3H6O)2+H]+"]
        assert record["displaced"] == []

    def test_a_water_rung_never_displaces_a_molecule(self):
        """Acetone on a hydronium is protonated C3H8O2 too, and holds water:
        left to the stages however loud it is."""
        rungs, summary, _ = _climb(BEAM, ("C3H7O", 1, 3e6), ("C3H9O2", 1, 2e6))

        assert _claimed(rungs) == {}
        assert summary["held"][HELD_WATER_DISPLACES] == 1

    def test_a_water_rung_no_molecule_reads_is_claimed(self):
        """Ethanol on a hydronium implies C2H8O2, no molecule."""
        rungs, _, _ = _climb(BEAM, ("C2H7O", 1, 1e6), ("C2H9O2", 1, 1e5))

        assert list(_claimed(rungs)) == ["[C2H6O+H3O]+"]
        assert _claimed(rungs)["[C2H6O+H3O]+"]["below"]["step"] == "H2O"


class TestTheHydrideBoundLadder:
    def test_it_needs_a_run_that_abstracts_a_hydride(self):
        """Ethanol less a hydride is the core; on a run with no hydride
        abstraction the same ion is no core."""
        ions = (BEAM, ("C2H5O", 1, 1e6), ("C4H11O2", 1, 5e5))
        rungs, summary, _ = _climb(*ions)
        assert list(_claimed(rungs)) == ["[(C2H6O)2-H]+"]
        assert summary["cores"]["[C2H6O-H]+"] is True

        rungs, summary, _ = _climb(*ions, channels=("[M]+.", "[M+H]+"))
        assert _claimed(rungs) == {}
        assert summary["cores"]["[C2H6O-H]+"] is False


class TestOneIonOnTwoLadders:
    """Methanol's dimer and ethanol on a hydronium are both C2H9O2+."""

    def test_the_ladder_that_holds_takes_it(self):
        rungs, summary, _ = _climb(BEAM, ("CH5O", 1, 1e6), ("C2H9O2", 1, 5e5))

        claimed = _claimed(rungs)
        assert list(claimed) == ["[(CH4O)2+H]+"]
        assert claimed["[(CH4O)2+H]+"]["also_reads_as"] == []
        # The ethanol reading had no core, and the ion is not held for it.
        assert sum(summary["held"].values()) == 0

    def test_another_ladder_that_holds_is_recorded_beside_it(self):
        rungs, _, _ = _climb(
            BEAM, ("CH5O", 1, 1e6), ("C2H7O", 1, 1e6), ("C2H9O2", 1, 5e5)
        )

        claimed = _claimed(rungs)
        assert list(claimed) == ["[(CH4O)2+H]+"]
        assert claimed["[(CH4O)2+H]+"]["also_reads_as"] == ["[C2H6O+H3O]+"]

    def test_a_rung_one_ladder_claimed_is_a_rung_below_for_the_other(self):
        """Methanol's trimer climbs from its dimer, which the ethanol hydrate's
        reading of the same ion took."""
        rungs, _, _ = _climb(
            BEAM,
            ("CH5O", 1, 1e6),
            ("C2H7O", 1, 2e6),
            ("C2H9O2", 1, 5e5),
            ("C3H13O3", 1, 2e5),
        )

        claimed = _claimed(rungs)
        assert claimed["[(CH4O)3+H]+"]["below"]["ion"] == "[(CH4O)2+H]+"


class TestTheReagentsWaterLadder:
    def _nitrate(self, *ions, profile="NO3", reagent="NO3", acid="HN2O6"):
        core = {"NO3": "HN2O6", "^NO3": "HO6^N2"}
        base = (reagent, -1, 1e7)
        return _climb(
            base,
            (core[reagent], -1, 3e6),
            *ions,
            profile=profile,
            channels=(f"[M+{reagent}]-",),
        )

    def test_it_climbs_from_the_dimer_core_the_library_claimed(self):
        """(HNO3)2.NO3- is a library ion, and the core of the water ladder."""
        rungs, _, hits = self._nitrate(
            ("H2N3O9", -1, 1e6), ("H4N3O10", -1, 2e5), ("H6N3O11", -1, 5e4)
        )

        assert any(hit.cluster.label == "[NO3+2xHNO3]-" for hit in hits)
        claimed = _claimed(rungs)
        assert list(claimed) == ["[NO3+2xHNO3+H2O]-", "[NO3+2xHNO3+2xH2O]-"]
        assert claimed["[NO3+2xHNO3+2xH2O]-"]["grew_from"]["ion"] == "[NO3+2xHNO3]-"

    def test_a_hydrate_with_no_core_is_not_claimed(self):
        rungs, _, _ = self._nitrate(("H4N3O10", -1, 2e5))

        assert _claimed(rungs) == {}

    def test_the_labelled_ladder(self):
        rungs, _, _ = self._nitrate(
            ("H2O9^N3", -1, 1e6),
            ("H4O10^N3", -1, 2e5),
            profile="NO3_15N",
            reagent="^NO3",
        )

        assert list(_claimed(rungs)) == ["[^NO3+2xH^NO3+H2O]-"]


class TestARungsLines:
    """A rung's isotope lines are claimed as a library ion's are: as the
    peaks the file resolves, at a labelled reagent's purity."""

    @staticmethod
    def tof(mz: float) -> float:
        return 5000.0

    def test_a_tof_line_is_one_peak_of_the_file(self):
        """The nitrate dimer core's hydrate on a TOF: its peak one mass unit up
        holds the 15N and 17O lines, 10 ppm above the 15N line's own mass."""
        hydrate = "H4N3O10"
        mz, intensity = _spectrum(("NO3", -1, 1e7), ("H2N3O9", -1, 3e6))
        lines_mz, lines_share, labels = predict_resolved_isotopes(
            hydrate, -1, DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE, self.tof
        )
        mz = np.append(mz, lines_mz)
        intensity = np.append(intensity, 3e5 * lines_share / lines_share[0])
        order = np.argsort(mz)

        def lines(resolution):
            rungs, _ = match_ion_ladders(
                ion_ladders("NO3"),
                mz[order],
                intensity[order],
                calibration=ReagentCalibration(0.0, ORBI_PPM),
                taken=[],
                channels=("[M+NO3]-",),
                claim_ppm=ORBI_PPM,
                resolution=resolution,
            )
            assert "[NO3+2xHNO3+H2O]-" in _claimed(rungs)
            return [hit.isotope_label for hit in rungs if hit.is_isotopologue]

        assert "15N" in lines(self.tof)
        assert "15N" not in lines(None)

    def test_a_labelled_rungs_lines_are_read_at_the_purity(self):
        """At 90% 15N the labelled dimer core's hydrate carries a 14N line a
        third of its height, over three times what the default 98% predicts."""
        core, hydrate = "H2O9^N3", "H4O10^N3"
        mz, intensity = _spectrum(("^NO3", -1, 1e7), (core, -1, 3e6))
        lines_mz, lines_share, labels = predict_resolved_isotopes(
            hydrate, -1, DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE, None, 0.9
        )
        mz = np.append(mz, lines_mz)
        intensity = np.append(intensity, 3e5 * lines_share / lines_share[0])
        order = np.argsort(mz)

        def lines(purity):
            rungs, _ = match_ion_ladders(
                ion_ladders("NO3_15N"),
                mz[order],
                intensity[order],
                calibration=ReagentCalibration(0.0, ORBI_PPM),
                taken=[],
                channels=("[M+^NO3]-",),
                claim_ppm=ORBI_PPM,
                purity=purity,
            )
            assert _claimed(rungs) and "[^NO3+2xH^NO3+H2O]-" in _claimed(rungs)
            return [hit.isotope_label for hit in rungs if hit.is_isotopologue]

        assert "14N" in lines(0.9)
        assert "14N" not in lines(None)
