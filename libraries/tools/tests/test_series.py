"""The homologous-series walk and its decoys (step 4.1 of the assignment plan)."""

import math

import pytest

from mascope_tools.composition.series import (
    CONTAMINANT_UNITS,
    DECOY_OFFSETS_DA,
    MAX_DEPTH,
    MIN_ENRICHMENT,
    MIN_LINKS,
    ORGANIC_UNITS,
    SERIES_UNITS,
    Proposal,
    SeriesEvidence,
    SeriesSource,
    SeriesTarget,
    SeriesUnit,
    committed_proposals,
    has_label_alias,
    ion_mz,
    series_evidence,
    step_formula,
    walk_series,
)
from mascope_tools.composition.utils import parse_formula_tokens


CH2 = next(unit for unit in SERIES_UNITS if unit.formula == "CH2")
OXYGEN = next(unit for unit in SERIES_UNITS if unit.formula == "O")
SILOXANE = next(unit for unit in SERIES_UNITS if unit.formula == "C2H6OSi")
CF2 = next(unit for unit in SERIES_UNITS if unit.formula == "CF2")

#: An oxidised compound read through deprotonation, and its exact ion m/z.
ANCHOR = SeriesSource("a", "C8H10O5", "[M-H]-")
ANCHOR_MZ = 185.0455470


def mz_of(formula: str, notation: str = "[M-H]-") -> float:
    return ion_mz(parse_formula_tokens(formula), notation)


def target(key: str, formula: str, *, ppm: float = 0.0, reads: str | None = None):
    """A target at ``formula``'s exact ion m/z, moved by ``ppm``."""
    mz = mz_of(formula)
    return SeriesTarget(key, mz * (1 + ppm * 1e-6), reads)


class TestTheUnits:
    def test_the_organic_units_are_the_reference_s(self):
        assert [unit.formula for unit in ORGANIC_UNITS] == [
            "CH2",
            "O",
            "H2O",
            "CO",
            "CO2",
            "C2H2O",
        ]

    def test_the_contaminant_units_need_their_element(self):
        assert [(u.formula, u.requires) for u in CONTAMINANT_UNITS] == [
            ("C2H6OSi", "Si"),
            ("CF2", "F"),
        ]
        assert SERIES_UNITS == ORGANIC_UNITS + CONTAMINANT_UNITS

    @pytest.mark.parametrize(
        "formula, mass",
        [
            ("CH2", 14.0156501),
            ("O", 15.9949146),
            ("H2O", 18.0105647),
            ("CO", 27.9949146),
            ("CO2", 43.9898292),
            ("C2H2O", 42.0105647),
            ("C2H6OSi", 74.0187913),
            ("CF2", 49.9968064),
        ],
    )
    def test_each_unit_s_exact_mass(self, formula, mass):
        unit = next(u for u in SERIES_UNITS if u.formula == formula)
        assert unit.mass == pytest.approx(mass, abs=1e-6)

    def test_the_thresholds_are_the_measured_ones(self):
        # The numbers, not the names: they were measured on the testbed's
        # batches (the module's constants say how), and they move on purpose.
        assert (MIN_LINKS, MIN_ENRICHMENT, MAX_DEPTH) == (12, 10.0, 3)
        assert DECOY_OFFSETS_DA == (-0.0925, -0.067, -0.031, 0.0305, 0.0665, 0.0895)


class TestAStep:
    def test_a_step_up_adds_the_unit(self):
        assert step_formula({"C": 8, "H": 10, "O": 5}, CH2, 1) == {
            "C": 9,
            "H": 12,
            "O": 5,
        }

    def test_a_step_down_takes_it_away(self):
        assert step_formula({"C": 8, "H": 10, "O": 5}, OXYGEN, -1) == {
            "C": 8,
            "H": 10,
            "O": 4,
        }

    def test_a_count_below_zero_is_no_formula(self):
        assert step_formula({"C": 6, "H": 6}, OXYGEN, -1) is None

    def test_a_formula_that_loses_its_carbon_is_none(self):
        assert step_formula({"C": 1, "H": 4}, CH2, -1) is None

    def test_an_element_stepped_to_zero_leaves_the_formula(self):
        assert step_formula({"C": 6, "H": 10, "O": 1}, OXYGEN, -1) == {"C": 6, "H": 10}

    def test_a_contaminant_unit_steps_only_from_its_element(self):
        assert step_formula({"C": 10, "H": 16, "O": 3}, SILOXANE, 1) is None
        assert step_formula({"C": 8, "H": 24, "O": 4, "Si": 4}, SILOXANE, 1) == {
            "C": 10,
            "H": 30,
            "O": 5,
            "Si": 5,
        }
        assert step_formula({"C": 7, "H": 1, "F": 13, "O": 2}, CF2, 1) == {
            "C": 8,
            "H": 1,
            "F": 15,
            "O": 2,
        }


class TestTheIonsMass:
    @pytest.mark.parametrize(
        "formula, notation, mz",
        [
            ("C8H10O5", "[M-H]-", 185.0455470),
            ("C11H20O3", "[M+CH4N2O+H]+", 261.1808837),
            ("C8H8", "[M]+.", 104.0620517),
            ("C6H12O6", "+H+", 181.0706646),
        ],
    )
    def test_the_exact_m_z(self, formula, notation, mz):
        assert ion_mz(parse_formula_tokens(formula), notation) == pytest.approx(
            mz, abs=2e-6
        )


class TestTheWalk:
    def walk(self, targets, *, sources=(ANCHOR,), unit=CH2, **kwargs):
        kwargs.setdefault("tolerance_ppm", 3.0)
        return walk_series(list(sources), list(targets), unit, **kwargs)

    def test_a_homologue_at_its_exact_mass_is_proposed(self):
        proposals = self.walk([target("b", "C9H12O5", ppm=0.4)])
        proposal = proposals["b"]
        assert (proposal.formula, proposal.mechanism, proposal.unit) == (
            "C9H12O5",
            "[M-H]-",
            "CH2",
        )
        assert (proposal.step, proposal.anchor, proposal.root, proposal.depth) == (
            1,
            "a",
            "a",
            1,
        )
        assert proposal.ppm == pytest.approx(0.4, abs=1e-6)
        assert proposal.exact_mz == pytest.approx(mz_of("C9H12O5"), abs=1e-7)

    def test_both_directions_are_walked(self):
        proposals = self.walk([target("up", "C9H12O5"), target("down", "C7H8O5")])
        assert {key: p.step for key, p in proposals.items()} == {"up": 1, "down": -1}

    def test_a_target_outside_the_window_is_not_reached(self):
        assert self.walk([target("b", "C9H12O5", ppm=3.5)]) == {}
        assert "b" in self.walk([target("b", "C9H12O5", ppm=3.5)], tolerance_ppm=4.0)

    def test_the_walk_steps_on_from_a_proposal(self):
        proposals = self.walk(
            [target("b", "C9H12O5"), target("c", "C10H14O5"), target("d", "C11H16O5")]
        )
        assert {key: (p.depth, p.anchor, p.root) for key, p in proposals.items()} == {
            "b": (1, "a", "a"),
            "c": (2, "b", "a"),
            "d": (3, "c", "a"),
        }

    def test_the_depth_is_bounded(self):
        targets = [target("b", "C9H12O5"), target("c", "C10H14O5")]
        assert set(self.walk(targets, max_depth=1)) == {"b"}
        assert set(self.walk(targets + [target("d", "C11H16O5")])) == {"b", "c", "d"}
        assert MAX_DEPTH == 3
        assert "e" not in self.walk(
            targets + [target("d", "C11H16O5"), target("e", "C12H18O5")]
        )

    def test_a_gap_ends_the_chain(self):
        proposals = self.walk([target("b", "C9H12O5"), target("d", "C11H16O5")])
        assert set(proposals) == {"b"}

    def test_the_next_step_starts_from_the_exact_mass(self):
        # Each target sits 2.5 ppm high. Stepped from the measured m/z the
        # error would ride along and the third target would sit near 5 ppm off
        # the chain; stepped from the exact mass each is 2.5 ppm off its own.
        proposals = self.walk(
            [
                target("b", "C9H12O5", ppm=2.5),
                target("c", "C10H14O5", ppm=2.5),
                target("d", "C11H16O5", ppm=2.5),
            ]
        )
        assert [round(proposals[k].ppm, 3) for k in "bcd"] == [2.5, 2.5, 2.5]

    def test_a_target_is_reached_at_its_shortest_distance(self):
        # c is two steps from a and one from a second assigned anchor.
        second = SeriesSource("z", "C11H16O5", "[M-H]-")
        proposals = self.walk(
            [target("b", "C9H12O5"), target("c", "C10H14O5")], sources=[ANCHOR, second]
        )
        assert (proposals["c"].depth, proposals["c"].root) == (1, "z")

    @pytest.mark.parametrize("far, near", [(2.0, -0.5), (-2.0, 0.5)])
    def test_of_two_targets_in_the_window_the_nearer_is_taken(self, far, near):
        proposals = self.walk(
            [target("far", "C9H12O5", ppm=far), target("near", "C9H12O5", ppm=near)]
        )
        assert set(proposals) == {"near"}

    def test_the_order_of_the_sources_decides_nothing(self):
        # Two anchors reach one target with two formulas: the nearer exact
        # mass takes it, whichever anchor came first.
        one = SeriesSource("a", "C8H10O5", "[M-H]-")
        other = SeriesSource("b", "C7H6O6", "[M-H]-")
        # C8H8O6 sits 36 mDa (183 ppm) above C9H12O5: both in a 300 ppm
        # window, and the second source's key sorts last.
        hit = SeriesTarget("t", mz_of("C9H12O5") * (1 + 1.0e-6))
        forward = walk_series([one, other], [hit], CH2, tolerance_ppm=300.0)
        backward = walk_series([other, one], [hit], CH2, tolerance_ppm=300.0)
        assert forward == backward
        assert (forward["t"].formula, forward["t"].anchor) == ("C9H12O5", "a")

    def test_a_target_already_reading_a_formula_is_proposed_too(self):
        proposals = self.walk([target("b", "C9H12O5", reads="C9H12O5")])
        assert proposals["b"].formula == "C9H12O5"

    def test_the_source_s_own_mechanism_is_kept(self):
        source = SeriesSource("a", "C6H12O6", "[M+H]+")
        hit = SeriesTarget("b", ion_mz(parse_formula_tokens("C7H14O6"), "[M+H]+"))
        proposals = walk_series([source], [hit], CH2, tolerance_ppm=3.0)
        assert proposals["b"].mechanism == "[M+H]+"

    def test_a_decoy_offset_shifts_the_step(self):
        offset = 0.0305
        shifted = SeriesTarget("b", mz_of("C9H12O5") + offset)
        assert self.walk([shifted]) == {}
        proposals = self.walk([shifted], offset_da=offset)
        # The formula arithmetic is the unit's: a decoy takes the steps the
        # real walk could, on a mass chemistry does not sit at.
        assert proposals["b"].formula == "C9H12O5"

    def test_a_mechanism_it_cannot_mass_steps_nowhere(self):
        source = SeriesSource("a", "C8H10O5", "not a mechanism")
        assert (
            walk_series([source], [target("b", "C9H12O5")], CH2, tolerance_ppm=3.0)
            == {}
        )

    def test_a_contaminant_ladder_runs_between_its_own(self):
        d4 = SeriesSource("d4", "C8H24O4Si4", "[M+H]+")
        d5 = SeriesTarget("d5", ion_mz(parse_formula_tokens("C10H30O5Si5"), "[M+H]+"))
        assert set(walk_series([d4], [d5], SILOXANE, tolerance_ppm=3.0)) == {"d5"}
        organic = SeriesSource("x", "C10H16O3", "[M+H]+")
        silylated = SeriesTarget(
            "s", ion_mz(parse_formula_tokens("C12H22O4Si"), "[M+H]+")
        )
        assert walk_series([organic], [silylated], SILOXANE, tolerance_ppm=3.0) == {}

    def test_a_labelled_source_proposes_no_reading_its_alias_takes(self):
        # On a 15N nitrate source C8S and H ^N O7 make the same ion within
        # 0.27 mDa, and the alias takes every step the reading does.
        sulfur = SeriesSource("s", "C9H14O5S", "[M-H]-")
        homologue = target("t", "C10H16O5S")
        assert set(walk_series([sulfur], [homologue], CH2, tolerance_ppm=3.0)) == {"t"}
        assert (
            walk_series([sulfur], [homologue], CH2, tolerance_ppm=3.0, labelled=True)
            == {}
        )


class TestTheLabelAlias:
    @pytest.mark.parametrize(
        "formula, alias",
        [
            ("C8H10O4S", True),
            ("C7H10O4S", False),
            ("C2HF3O2", True),
            ("C2H2F2O2", False),
            ("C10H16O3", False),
        ],
    )
    def test_the_two_swaps(self, formula, alias):
        assert has_label_alias(parse_formula_tokens(formula)) is alias


class TestTheEvidence:
    def test_enrichment_and_fdr_are_against_the_decoys_mean(self):
        evidence = SeriesEvidence("CH2", "organic", 30, (1, 2, 3, 2, 1, 3))
        assert evidence.decoy_mean == 2.0
        assert evidence.enrichment == 15.0
        assert evidence.fdr == pytest.approx(2 / 30)
        assert evidence.significant

    def test_too_few_proposals_are_not_a_family(self):
        evidence = SeriesEvidence("CH2", "organic", MIN_LINKS - 1, (0,) * 6)
        assert evidence.enrichment == math.inf
        assert not evidence.significant
        assert SeriesEvidence("CH2", "organic", MIN_LINKS, (0,) * 6).significant

    def test_too_little_above_chance_is_not_a_family(self):
        decoys = (3, 3, 3, 3, 3, 3)
        below = SeriesEvidence("CH2", "organic", 29, decoys)
        at = SeriesEvidence("CH2", "organic", 30, decoys)
        assert below.enrichment < MIN_ENRICHMENT <= at.enrichment
        assert not below.significant
        assert at.significant

    def test_nothing_reached_has_no_rate(self):
        evidence = SeriesEvidence("CH2", "organic", 0, (2, 2))
        assert (evidence.enrichment, evidence.fdr, evidence.significant) == (
            None,
            None,
            False,
        )

    def test_the_rate_is_at_most_one(self):
        assert SeriesEvidence("CH2", "organic", 2, (5, 5)).fdr == 1.0

    def test_no_decoys_reach_nothing(self):
        evidence = SeriesEvidence("CH2", "organic", 12, ())
        assert (evidence.decoy_mean, evidence.enrichment) == (0.0, math.inf)

    def test_the_floors_can_be_set(self):
        evidence = SeriesEvidence(
            "CH2", "organic", 3, (1,), min_links=3, min_enrichment=3.0
        )
        assert evidence.significant

    def test_the_record_a_run_keeps(self):
        assert SeriesEvidence("O", "organic", 24, (0, 2, 1, 1, 0, 2)).to_json() == {
            "unit": "O",
            "family": "organic",
            "n_links": 24,
            "decoy_links": [0, 2, 1, 1, 0, 2],
            "decoy_mean": 1.0,
            "enrichment": 24.0,
            "fdr": 0.0417,
            "significant": True,
        }
        assert SeriesEvidence("O", "organic", 12, (0,)).to_json()["enrichment"] == "inf"
        assert SeriesEvidence("O", "organic", 0, (0,)).to_json()["fdr"] is None

    def test_each_decoy_is_walked_over_the_same_anchors(self):
        targets = [
            target("b", "C9H12O5"),
            SeriesTarget("decoy", mz_of("C9H12O5") + DECOY_OFFSETS_DA[2]),
        ]
        evidence = series_evidence([ANCHOR], targets, CH2, tolerance_ppm=3.0)
        assert evidence.n_links == 1
        assert evidence.decoy_links == (0, 0, 1, 0, 0, 0)
        assert set(evidence.proposals) == {"b"}

    def test_the_depth_reaches_the_walk(self):
        targets = [target("b", "C9H12O5"), target("c", "C10H14O5")]
        shallow = series_evidence(
            [ANCHOR], targets, CH2, tolerance_ppm=3.0, max_depth=1
        )
        assert (shallow.n_links, set(shallow.proposals)) == (1, {"b"})
        assert series_evidence([ANCHOR], targets, CH2, tolerance_ppm=3.0).n_links == 2

    def test_the_labelled_flag_reaches_the_decoys(self):
        sulfur = SeriesSource("s", "C9H14O5S", "[M-H]-")
        decoy = SeriesTarget("d", mz_of("C10H16O5S") + DECOY_OFFSETS_DA[0])
        plain = series_evidence([sulfur], [decoy], CH2, tolerance_ppm=3.0)
        labelled = series_evidence(
            [sulfur], [decoy], CH2, tolerance_ppm=3.0, labelled=True
        )
        assert (plain.decoy_links[0], labelled.decoy_links[0]) == (1, 0)


def evidence_for(unit: SeriesUnit, proposals: dict, *, significant=True):
    return SeriesEvidence(
        unit.formula,
        unit.family,
        len(proposals),
        (0,),
        min_links=0 if significant else 10**6,
        proposals=proposals,
    )


class TestTheCommittedProposals:
    def proposal(self, unit, target_key, formula, *, depth=1, ppm=0.0):
        return Proposal(
            target=target_key,
            formula=formula,
            mechanism="[M-H]-",
            unit=unit.formula,
            step=1,
            anchor="a",
            root="a",
            depth=depth,
            ppm=ppm,
            exact_mz=0.0,
        )

    def test_only_a_committed_family_s_proposals_count(self):
        ch2 = {"t": self.proposal(CH2, "t", "C9H12O5")}
        assert committed_proposals([evidence_for(CH2, ch2, significant=False)]) == {}
        chosen = committed_proposals([evidence_for(CH2, ch2)])
        assert chosen["t"][0].formula == "C9H12O5"
        assert chosen["t"][1] == ("CH2",)

    def test_two_units_naming_one_formula_agree(self):
        ch2 = {"t": self.proposal(CH2, "t", "C9H12O5", depth=2)}
        oxygen = {"t": self.proposal(OXYGEN, "t", "C9H12O5", depth=1)}
        chosen = committed_proposals(
            [evidence_for(CH2, ch2), evidence_for(OXYGEN, oxygen)]
        )
        proposal, units = chosen["t"]
        assert units == ("CH2", "O")
        assert (proposal.unit, proposal.depth) == ("O", 1)

    def test_more_units_outweigh_a_nearer_formula(self):
        ch2 = {"t": self.proposal(CH2, "t", "C9H12O5", depth=2, ppm=2.0)}
        oxygen = {"t": self.proposal(OXYGEN, "t", "C9H12O5", depth=2, ppm=2.0)}
        water = {"t": self.proposal(OXYGEN, "t", "C8H8N2O4", depth=1, ppm=0.1)}
        chosen = committed_proposals(
            [
                evidence_for(CH2, ch2),
                evidence_for(OXYGEN, oxygen),
                evidence_for(OXYGEN, water),
            ]
        )
        assert chosen["t"][0].formula == "C9H12O5"

    @pytest.mark.parametrize(
        "first, second, wins",
        [
            ((1, 2.0), (2, 0.1), "C9H12O5"),  # the shorter distance
            ((1, 0.1), (1, 2.0), "C9H12O5"),  # then the nearer exact mass
            ((1, 0.5), (1, -0.5), "C8H8N2O4"),  # then the formula
        ],
    )
    def test_two_formulas_are_settled_whatever_the_order(self, first, second, wins):
        a = {"t": self.proposal(CH2, "t", "C9H12O5", depth=first[0], ppm=first[1])}
        b = {
            "t": self.proposal(OXYGEN, "t", "C8H8N2O4", depth=second[0], ppm=second[1])
        }
        forward = committed_proposals([evidence_for(CH2, a), evidence_for(OXYGEN, b)])
        backward = committed_proposals([evidence_for(OXYGEN, b), evidence_for(CH2, a)])
        assert forward["t"][0].formula == backward["t"][0].formula == wins
