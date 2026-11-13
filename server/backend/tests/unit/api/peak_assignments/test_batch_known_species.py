"""Unit tests for the batch's known-species decision, its pure parts (step 4.2h).

No DB: which batch peaks are known species, what each file of a present one
becomes and why, whether the claim turns the vote, the co-variation with the
core, what the records, the run's summary and its outcome say, how the
consensus keeps a record, what the ledger and a snapshot serve, how a sample's
row is rewritten as the species, and how a file's reagent pre-pass takes its
batch's word. The verdict's thresholds are the library's
(``libraries/tools/tests/test_known_species.py``). See
``batch_known_species.py``.
"""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    ALREADY,
    BACKGROUND_TAG,
    BATCH_CLAIM,
    HELD_CURATED,
    HELD_TWIN,
    HELD_VOTE,
    KEPT_CARRIES_LINES,
    KEPT_CLAIMED_OTHERWISE,
    KEPT_HAND_ASSIGNED,
    KEPT_LINE_OF_ANOTHER,
    KEPT_ON_TWIN,
    KEPT_TARGET_LIBRARY,
    LISTED_CLAIM_TIER,
    OWN_CLAIM,
    Member,
    Plan,
    Row,
    Species,
    Written,
    claim_turns,
    decide_row,
    deciding_outcome,
    earlier_own,
    library_species,
    listed_species,
    listed_species_row,
    plan_batch,
    row_record,
    sample_decision,
    species_of,
    species_record,
    summarise,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    KNOWN_SPECIES_KEY,
    compute_consensus,
    known_species_record_of,
    known_species_summary,
    reading_of,
    standing_known_species_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_backend.api.new.peak_assignments.engine import (
    DISPLACED_BY_CLAIM,
    REFERENCE_IDENTITIES_COL,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    BatchSpecies,
    batch_species_from,
    batch_species_row,
    build_reagent_assignments,
    claim_batch_species,
)
from mascope_tools.composition.known_species import (
    REASON_ONE_FILE,
    REASON_TEST_NEVER_HELD,
    VERDICT_ABSENT,
    VERDICT_PRESENT,
    VERDICT_UNPOOLED,
)
from mascope_tools.composition.reagents import (
    ReagentCalibration,
    ReagentHit,
    ion_mz,
    reagent_library,
)


SAMPLES = [f"s{i}" for i in range(6)]
MECH = "mech-h"
#: The urea trimer, a uronium source's library ion.
TRIMER = "C3H13N6O3+"
TRIMER_MZ = 181.10437
#: A listed siloxane, through the proton.
D4, D4_ION, D4_MZ = "C8H24O4Si4", "C8H25O4Si4+", 297.08246
LISTING = {"name": "D4", "source": "siloxanes", "tags": [BACKGROUND_TAG], "total": 1}


def anchor(key, mz, *, formula=None, ion=None, role=None, candidates=None, **extra):
    fields = dict(
        batch_peak_id=key,
        mz=mz,
        mz_tol_ppm=4.0,
        ionization_mode_id="mode-1",
        intensity_variable="sum_peak_heights",
        consensus_formula=formula,
        consensus_ion_formula=ion,
        ionization_mechanism_id=MECH if formula else None,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=role,
        isotopologue_of=None,
        max_intensity=1e6,
        provenance=None,
        candidates=candidates or [],
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def member(sample, mz=TRIMER_MZ, **fields):
    defaults = dict(
        sample_item_id=sample,
        sample_peak_id=f"p-{sample}",
        intensity=1e4,
        mz=mz,
        role="unassigned",
        peak_assignment_id=f"pa-{sample}",
    )
    defaults.update(fields)
    return Member(**defaults)


def claimed(sample, ion=TRIMER, **fields):
    return member(sample, role="reagent", ion_formula=ion, tier="unassigned", **fields)


def analyte(sample, formula="C3H12N6O3", ion=TRIMER, tier="assigned", **fields):
    defaults = dict(
        role="M0",
        formula=formula,
        ion_formula=ion,
        ionization_mechanism_id=MECH,
        tier=tier,
        fit_score=0.8,
    )
    defaults.update(fields)
    return member(sample, **defaults)


#: What a pre-pass claim of the trimer records on its row.
TRIMER_CLAIM = {
    "ion": "[(CH4N2O)3+H]+",
    "kind": "cluster",
    "family": "reagent",
    "references": [],
    "observed": "no work found names it; the test spectra show it",
    "mz": TRIMER_MZ,
}


def trimer_row(samples, key="T"):
    """The trimer claimed in ``s0`` only, read as an analyte in ``s1`` and left
    unread elsewhere."""
    found = {}
    for sample in samples:
        if sample == "s0":
            found[sample] = claimed(sample)
        elif sample == "s1":
            found[sample] = analyte(sample)
        else:
            found[sample] = member(sample)
    return Row(anchor(key, TRIMER_MZ, ion=TRIMER, role="reagent"), found)


LIBRARY = Species(
    kind="library",
    ion_formula=TRIMER,
    label="[(CH4N2O)3+H]+",
    family="reagent",
    ion_kind="cluster",
    exact_mz=TRIMER_MZ,
)
LISTED = Species(
    kind="listed",
    ion_formula=D4_ION,
    formula=D4,
    ionization_mechanism_id=MECH,
    listing=LISTING,
)


def d4_candidates():
    return [
        {
            "formula": D4,
            "ion_formula": D4_ION,
            "ionization_mechanism_id": MECH,
            "source": "database",
            "listing": LISTING,
        },
        {
            "formula": "C9H24N2O4Si3",
            "ion_formula": D4_ION,
            "ionization_mechanism_id": MECH,
        },
    ]


class TestTheSpecies:
    def test_a_claimed_ion_is_a_library_species_read_off_its_claims_rows(self):
        row = trimer_row(SAMPLES)
        species = library_species(row, {"pa-s0": TRIMER_CLAIM})

        assert species.kind == "library"
        assert species.ion_formula == TRIMER
        assert species.label == "[(CH4N2O)3+H]+"
        assert species.family == "reagent"
        assert species.exact_mz == TRIMER_MZ
        assert species.observed

    def test_a_ladder_rung_names_the_ion_it_grew_from(self):
        record = {**TRIMER_CLAIM, "ladder": {"grew_from": {"ion": "[H3O]+"}}}
        species = library_species(trimer_row(SAMPLES), {"pa-s0": record})

        assert species.grew_from == "[H3O]+"

    def test_with_no_row_left_the_ion_is_read_off_the_libraries(self):
        """A sample folded without a run links no row."""
        species = library_species(trimer_row(SAMPLES), {})

        assert species.ion_formula == TRIMER
        assert species.label == "[(CH4N2O)3+H]+"
        assert species.family == "reagent"
        assert species.exact_mz == pytest.approx(TRIMER_MZ, abs=1e-4)

    def test_an_ion_no_library_holds_keeps_its_formula_as_its_label(self):
        row = Row(anchor("A", 100.0), {"s0": claimed("s0", ion="C7H9O9+")})
        species = library_species(row, {})

        assert species.label == "C7H9O9+"
        assert species.family is None
        assert species.exact_mz == pytest.approx(ion_mz("C7H9O9", 1))

    def test_a_fragment_is_no_species(self):
        row = trimer_row(SAMPLES)
        fragment = {**TRIMER_CLAIM, "family": "fragment"}

        assert library_species(row, {"pa-s0": fragment}) is None

    def test_a_fragment_ion_with_no_row_is_no_species(self):
        row = Row(anchor("F", 81.07), {"s0": claimed("s0", ion="C6H9+")})

        assert library_species(row, {}) is None

    def test_a_line_or_an_analyte_is_no_claim(self):
        row = Row(
            anchor("L", 182.1),
            {
                "s0": member(
                    "s0", role="reagent", ion_formula=TRIMER, owner_batch_peak_id="T"
                ),
                "s1": analyte("s1"),
            },
        )

        assert library_species(row, {}) is None

    def test_an_isotope_line_anchor_is_no_species(self):
        row = trimer_row(SAMPLES)
        row.anchor.isotopologue_of = "X"

        assert species_of(row, {}) is None

    def test_a_listed_compound_is_the_formula_a_list_tags_as_background(self):
        members = {
            "s0": analyte("s0", D4, D4_ION, mz=D4_MZ),
            "s1": analyte("s1", D4, D4_ION, mz=D4_MZ),
            "s2": analyte("s2", "C9H24N2O4Si3", D4_ION, mz=D4_MZ),
        }
        row = Row(anchor("D", D4_MZ, candidates=d4_candidates()), members)
        species = species_of(row, {})

        assert species.kind == "listed"
        assert species.formula == D4
        assert species.ion_formula == D4_ION
        assert species.ionization_mechanism_id == MECH
        assert species.listing == LISTING

    def test_a_tie_over_a_reading_that_names_no_mechanism_is_broken(self):
        """An imported row may name no mechanism: a tie between it and one that
        does picks one, and does not fail the run."""
        members = {
            "s0": analyte("s0", D4, D4_ION, mz=D4_MZ),
            "s1": analyte("s1", D4, D4_ION, mz=D4_MZ, ionization_mechanism_id=None),
        }
        row = Row(anchor("D", D4_MZ, candidates=d4_candidates()), members)
        species = listed_species(row)

        assert species.formula == D4
        assert species.ionization_mechanism_id is None
        members["s1"], members["s0"] = members["s0"], members["s1"]
        assert listed_species(row).ionization_mechanism_id is None

    def test_a_list_without_the_background_tag_names_no_species(self):
        candidates = d4_candidates()
        candidates[0]["listing"] = {**LISTING, "tags": ["indoor"]}
        row = Row(
            anchor("D", D4_MZ, candidates=candidates),
            {"s0": analyte("s0", D4, D4_ION)},
        )

        assert listed_species(row) is None

    def test_a_listed_compound_no_file_committed_is_still_read(self):
        row = Row(
            anchor("D", D4_MZ, candidates=d4_candidates()),
            {"s0": analyte("s0", D4, D4_ION, tier="below_assignability")},
        )

        assert listed_species(row).formula == D4


class TestEachFile:
    def test_a_file_whose_own_test_held(self):
        assert sample_decision(claimed("s0"), LIBRARY) == ALREADY
        assert sample_decision(analyte("s0", D4, D4_ION), LISTED) == ALREADY

    def test_a_listed_reading_below_candidate_is_no_test_held(self):
        below = analyte("s0", D4, D4_ION, tier="below_assignability")

        assert sample_decision(below, LISTED) is None

    def test_a_hand_assignment_is_kept(self):
        assert sample_decision(analyte("s1", hand=True), LIBRARY) == (
            KEPT_HAND_ASSIGNED
        )

    def test_a_target_compound_is_kept(self):
        assert sample_decision(analyte("s1", target=True), LIBRARY) == (
            KEPT_TARGET_LIBRARY
        )

    def test_another_ions_line_is_kept(self):
        line = analyte("s1", role="iso_child", owner_batch_peak_id="X")
        reagent_line = member(
            "s1", role="reagent", ion_formula="CH5N2O+", owner_batch_peak_id="X"
        )

        assert sample_decision(line, LIBRARY) == KEPT_LINE_OF_ANOTHER
        assert sample_decision(reagent_line, LIBRARY) == KEPT_LINE_OF_ANOTHER

    def test_another_source_ions_claim_is_kept(self):
        assert sample_decision(claimed("s1", ion="C2H9N4O2+"), LIBRARY) == (
            KEPT_CLAIMED_OTHERWISE
        )
        assert sample_decision(member("s1", role="artifact"), LIBRARY) == (
            KEPT_CLAIMED_OTHERWISE
        )

    def test_a_reading_that_carries_lines_keeps_a_listed_compound_off(self):
        other = analyte("s1", "C9H24N2O4Si3", D4_ION)

        assert sample_decision(other, LISTED, ("13C",)) == KEPT_CARRIES_LINES
        assert sample_decision(other, LISTED) is None

    def test_a_listed_compounds_own_reading_below_candidate_is_lifted_lines_and_all(
        self,
    ):
        own = analyte("s1", D4, D4_ION, tier="below_assignability")

        assert LISTED.lifts(own)
        assert sample_decision(own, LISTED, ("13C",)) is None
        assert not LISTED.lifts(analyte("s1", D4, D4_ION))
        assert not LISTED.lifts(analyte("s1", "C9H24N2O4Si3", D4_ION))
        assert not LIBRARY.lifts(analyte("s1", tier="below_assignability"))

    def test_a_library_ions_claim_takes_the_lines_with_it(self):
        assert sample_decision(analyte("s1"), LIBRARY, ("13C",)) is None
        assert sample_decision(analyte("s1"), LIBRARY, ("13C", "15N", "18O")) is None

    def test_a_line_a_library_ion_cannot_have_keeps_the_file(self):
        """A carbon-free ion takes no 13C line: the file read an analyte that
        has carbon there, and keeps it, its lines and all."""
        hydrate = Species(kind="library", ion_formula="H5O2+", label="[H3O+H2O]+")
        reading = analyte("s1", "C2H4O", "C2H5O+")

        assert sample_decision(reading, hydrate, ("13C",)) == KEPT_CARRIES_LINES
        assert sample_decision(reading, hydrate, ("18O",)) is None
        assert sample_decision(reading, hydrate, ("18O", "13C")) == KEPT_CARRIES_LINES
        assert sample_decision(reading, LIBRARY, ("81Br",)) == KEPT_CARRIES_LINES
        # A line no row labels is not one the ion can be shown to have.
        assert sample_decision(reading, LIBRARY, (None,)) == KEPT_CARRIES_LINES

    def test_the_isotopes_a_library_ion_can_have(self):
        assert LIBRARY.can_have("13C")
        assert LIBRARY.can_have("13C2+15N")
        assert LIBRARY.can_have("2H")
        assert not LIBRARY.can_have("34S")
        assert not LIBRARY.can_have("13C+81Br")
        assert not LIBRARY.can_have("")
        assert not LIBRARY.can_have(None)

    def test_a_line_needs_as_many_atoms_as_its_label_names(self):
        carbonate = Species(kind="library", ion_formula="CO3-")
        bromide = Species(kind="library", ion_formula="Br-")
        dibromide = Species(kind="library", ion_formula="HBr2-")

        assert carbonate.can_have("13C")
        assert not carbonate.can_have("13C2")
        assert carbonate.can_have("18O3")
        assert not carbonate.can_have("18O4")
        assert not carbonate.can_have("13C+13C")
        assert LIBRARY.can_have("13C3")
        assert not LIBRARY.can_have("13C4")
        # 2x81Br and 81Br2 both name two atoms; a pair's label names one.
        assert not bromide.can_have("81Br2")
        assert not bromide.can_have("2x81Br")
        assert bromide.can_have("81Br(pair)")
        assert dibromide.can_have("81Br2")
        assert dibromide.can_have("2x81Br")
        # A label read as no isotope is not one the ion can be shown to have.
        assert not LIBRARY.can_have("M+1")
        assert not LIBRARY.can_have("13C+M")

    def test_a_two_atom_line_under_a_one_atom_ion_keeps_the_file(self):
        carbonate = Species(kind="library", ion_formula="CO3-")
        reading = analyte("s1", "C2H2O4", "C2HO4-")

        assert sample_decision(reading, carbonate, ("13C",)) is None
        assert sample_decision(reading, carbonate, ("13C2",)) == KEPT_CARRIES_LINES

    def test_an_unread_peak_and_an_analyte_are_claimed(self):
        assert sample_decision(member("s2"), LIBRARY) is None
        assert sample_decision(analyte("s1"), LIBRARY) is None


class TestTheDecision:
    def test_a_species_in_most_files_is_claimed_in_all(self):
        decision = decide_row(trimer_row(SAMPLES[:5]), LIBRARY, 6, {})

        assert decision.verdict.verdict == VERDICT_PRESENT
        assert decision.samples == {
            "s0": ALREADY,
            "s1": None,
            "s2": None,
            "s3": None,
            "s4": None,
        }
        assert decision.held is None
        assert decision.claims

    def test_a_species_in_one_file_is_not_pooled(self):
        row = Row(anchor("T", TRIMER_MZ), {"s0": claimed("s0")})
        decision = decide_row(row, LIBRARY, 6, {})

        assert decision.verdict.verdict == VERDICT_UNPOOLED
        assert decision.verdict.reason == REASON_ONE_FILE
        assert decision.samples == {}
        assert not decision.claims

    def test_a_species_no_file_claimed_is_absent(self):
        row = Row(anchor("T", TRIMER_MZ), {s: member(s) for s in SAMPLES})
        decision = decide_row(row, LIBRARY, 6, {})

        assert decision.verdict.verdict == VERDICT_ABSENT
        assert decision.verdict.reason == REASON_TEST_NEVER_HELD
        assert not decision.claims

    def test_the_evidence_counts_each_file(self):
        row = trimer_row(SAMPLES)
        row.members["s5"] = analyte("s5", hand=True)
        decision = decide_row(row, LIBRARY, 8, {})

        evidence = decision.verdict.evidence
        assert (evidence.files, evidence.present, evidence.test_held) == (8, 6, 1)
        assert (evidence.claimable, evidence.kept) == (4, 1)

    def test_a_curated_batch_peak_is_held(self):
        row = trimer_row(SAMPLES)
        row.anchor.provenance = {"manual": {"formula": "C3H12N6O3"}}
        decision = decide_row(row, LIBRARY, 6, {})

        assert decision.held == HELD_CURATED
        assert not decision.claims

    def test_a_claim_the_vote_would_not_follow_is_held(self):
        """Four files read another compound by hand: two claims do not turn
        the batch peak's reading."""
        row = trimer_row(SAMPLES[:2])
        for sample in SAMPLES[2:]:
            row.members[sample] = analyte(sample, hand=True)
        decision = decide_row(row, LIBRARY, 6, {})

        assert decision.verdict.present
        assert decision.held == HELD_VOTE

    def test_a_present_species_with_nothing_to_claim_is_not_held(self):
        row = Row(anchor("T", TRIMER_MZ), {s: claimed(s) for s in SAMPLES})
        decision = decide_row(row, LIBRARY, 6, {})

        assert decision.verdict.present
        assert decision.held is None
        assert not decision.claims


def _earlier(own, species=LIBRARY):
    """A record an earlier decision left, naming the samples it read as their
    file's own."""
    return {
        KNOWN_SPECIES_KEY: {
            "species": species.to_json(),
            "verdict": VERDICT_PRESENT,
            "reads": READS_TRIMER,
            "own_test_samples": list(own),
        }
    }


def batch_claimed(sample, **fields):
    """A member a decision claimed: its row says so."""
    return claimed(
        sample,
        row_reading=("reagent", None, TRIMER, "unassigned"),
        **{
            "row_batch": True,
            **fields,
        },
    )


class TestTheOwnTest:
    """The evidence counts each file's own test, never the batch's own claims."""

    def test_a_row_written_on_the_batchs_word_is_the_batchs(self):
        assert not LIBRARY.own_test(batch_claimed("s1"), None)
        assert LIBRARY.reads(batch_claimed("s1"))
        assert sample_decision(batch_claimed("s1"), LIBRARY) == ALREADY

    def test_a_row_that_reads_the_species_is_its_files(self):
        own = claimed("s0", row_reading=("reagent", None, TRIMER, "unassigned"))

        assert LIBRARY.own_test(own, set())
        listed = analyte("s0", D4, D4_ION, row_reading=("M0", D4, D4_ION, "candidate"))
        assert LISTED.own_test(listed, set())
        # A row that names a formula is no source ion's claim, whatever its role.
        named = claimed("s0", row_reading=("reagent", "C3H12N6O3", TRIMER, "assigned"))
        assert not LIBRARY.own_test(named, set())

    def test_a_member_with_no_row_to_say_so_goes_by_the_earlier_decision(self):
        """No row linked, or an import's row the decision left as it read."""
        rowless = claimed("s3", peak_assignment_id=None)
        imported = claimed("s1", row_reading=("M0", "C3H12N6O3", TRIMER, "assigned"))

        for one in (rowless, imported):
            assert LIBRARY.own_test(one, None)
            assert not LIBRARY.own_test(one, {"s0"})
        assert LIBRARY.own_test(rowless, {"s3"})

    def test_the_earlier_decision_is_the_anchors_record_of_this_species(self):
        bp = anchor("T", TRIMER_MZ, provenance=_earlier(["s0", "s2"]))

        assert earlier_own(bp, LIBRARY) == {"s0", "s2"}
        assert earlier_own(anchor("T", TRIMER_MZ), LIBRARY) is None
        other = anchor("T", TRIMER_MZ, provenance=_earlier(["s0"], LISTED))
        assert earlier_own(other, LIBRARY) is None

    def _decided_once(self):
        """The trimer claimed in s0 by its own run and by the batch in the
        rest: s1's row rewritten, s2 an import's, s3 to s4 no row."""
        row = Row(
            anchor("T", TRIMER_MZ, ion=TRIMER, role="reagent"),
            {
                "s0": claimed(
                    "s0", row_reading=("reagent", None, TRIMER, "unassigned")
                ),
                "s1": batch_claimed("s1"),
                "s2": claimed(
                    "s2", row_reading=("M0", "C3H12N6O3", TRIMER, "assigned")
                ),
                "s3": claimed("s3", peak_assignment_id=None),
                "s4": claimed("s4", peak_assignment_id=None),
            },
        )
        row.anchor.provenance = _earlier(["s0"])
        return row

    def test_a_second_decision_keeps_the_evidence_of_the_first(self):
        decision = decide_row(self._decided_once(), LIBRARY, 6, {})

        assert decision.verdict.present
        assert decision.verdict.evidence.test_held == 1
        assert decision.own == ["s0"]
        assert set(decision.samples.values()) == {ALREADY}
        assert species_record(decision, reads={}, batch_peak_run_id=None)[
            "own_test_samples"
        ] == ["s0"]

    def test_the_batchs_claims_alone_are_no_evidence(self):
        """The one file whose own test held is gone: the batch's earlier claims
        do not keep the species present, and the record says they stand."""
        row = self._decided_once()
        del row.members["s0"]
        decision = decide_row(row, LIBRARY, 6, {})

        assert decision.verdict.verdict == VERDICT_ABSENT
        assert decision.verdict.evidence.test_held == 0
        record = species_record(decision, reads={}, batch_peak_run_id=None)
        assert record["files_by_reason"] == {BATCH_CLAIM: 4}
        assert record["own_test_samples"] == []

    def test_a_file_whose_own_test_held_stands_against_the_verdict(self):
        row = self._decided_once()
        del row.members["s0"]
        row.members["s5"] = claimed(
            "s5", row_reading=("reagent", None, TRIMER, "unassigned")
        )
        record = species_record(
            decide_row(row, LIBRARY, 12, {}), reads={}, batch_peak_run_id=None
        )

        assert record["verdict"] != VERDICT_PRESENT
        assert record["files_by_reason"] == {BATCH_CLAIM: 4, OWN_CLAIM: 1}


class TestTheVote:
    def test_a_library_ion_turns_on_the_count_of_its_claims(self):
        row = trimer_row(SAMPLES)
        samples = {s: (ALREADY if s == "s0" else None) for s in SAMPLES}

        assert claim_turns(row, LIBRARY, samples)

    def test_a_listed_compound_turns_on_the_weighted_vote(self):
        members = {"s0": analyte("s0", D4, D4_ION, fit_score=0.9)}
        for sample in SAMPLES[1:]:
            members[sample] = member(sample, mz=D4_MZ)
        row = Row(anchor("D", D4_MZ, candidates=d4_candidates()), members)
        samples = {s: (ALREADY if s == "s0" else None) for s in SAMPLES}

        assert claim_turns(row, LISTED, samples)

    def test_well_fit_readings_a_hold_keeps_outweigh_fitless_claims(self):
        """A claimed member carries no fit, so it votes at the floor weight:
        two well-fit readings a hold keeps outweigh four that name the
        compound, three of them claimed."""
        members = {"s0": analyte("s0", D4, D4_ION, fit_score=0.9)}
        for sample in SAMPLES[1:4]:
            members[sample] = member(sample, mz=D4_MZ)
        for sample in SAMPLES[4:]:
            members[sample] = analyte(
                sample, "C9H24N2O4Si3", D4_ION, fit_score=0.95, hand=True
            )
        row = Row(anchor("D", D4_MZ, candidates=d4_candidates()), members)
        samples = {
            "s0": ALREADY,
            "s1": None,
            "s2": None,
            "s3": None,
            "s4": KEPT_HAND_ASSIGNED,
            "s5": KEPT_HAND_ASSIGNED,
        }

        assert not claim_turns(row, LISTED, samples)


def _co_varying_batch():
    """Twelve files, six analyte traces for the common mode, the trimer
    following the urea monomer and a listed compound that does not."""
    rng = np.random.default_rng(7)
    samples = [f"s{i}" for i in range(12)]
    drive = rng.normal(0.0, 0.3, len(samples))
    anchors, members = [], {}

    def add(key, mz, values, factory, **extra):
        anchors.append(anchor(key, mz, **extra))
        members[key] = {
            s: factory(s, mz=mz, intensity=float(10**v))
            for s, v in zip(samples, values)
        }

    for k in range(6):
        add(
            f"A{k}",
            200.0 + k,
            5 + rng.normal(0.0, 0.2, len(samples)),
            lambda s, **kw: analyte(s, f"C{k + 5}H10O4", f"C{k + 5}H11O4+", **kw),
            formula=f"C{k + 5}H10O4",
            ion=f"C{k + 5}H11O4+",
        )
    add(
        "M",
        61.04,
        7 + drive + rng.normal(0.0, 0.02, len(samples)),
        lambda s, **kw: claimed(s, ion="CH5N2O+", **kw),
        ion="CH5N2O+",
        role="reagent",
        max_intensity=1e9,
    )
    add(
        "T",
        TRIMER_MZ,
        4 + drive + rng.normal(0.0, 0.02, len(samples)),
        lambda s, **kw: claimed(s, **kw),
        ion=TRIMER,
        role="reagent",
        max_intensity=1e5,
    )
    return anchors, members, samples


class TestTheCore:
    def test_a_library_ion_is_read_against_the_sources_primary_ion(self):
        anchors, members, _ = _co_varying_batch()
        plan = plan_batch(anchors, members, {})
        by_id = {d.row.batch_peak_id: d for d in plan.decisions}

        trimer = by_id["T"].co_variation
        assert trimer["core"] == "ion"
        assert trimer["core_batch_peak_id"] == "M"
        assert trimer["core_ion"] == "[CH4N2O+H]+"
        assert trimer["verdict"] == "co_varying"
        assert trimer["r"] > 0.9
        # The primary ion is its own core: nothing to read it against.
        assert by_id["M"].co_variation is None

    def test_a_ladder_rung_is_read_against_the_ion_it_grew_from(self):
        anchors, members, _ = _co_varying_batch()
        anchors.append(
            anchor("R", 160.0, ion="C6H13O2+", role="reagent", max_intensity=1)
        )
        members["R"] = {
            s: claimed(s, ion="C6H13O2+", mz=160.0, peak_assignment_id=f"r-{s}")
            for s in members["T"]
        }
        claims = {
            f"r-{s}": {
                "ion": "[(C3H6O)2+H]+",
                "ladder": {"grew_from": {"ion": TRIMER_CLAIM["ion"]}},
            }
            for s in members["T"]
        }
        claims.update({f"pa-{s}": TRIMER_CLAIM for s in members["T"]})
        plan = plan_batch(anchors, members, claims)
        rung = next(d for d in plan.decisions if d.row.batch_peak_id == "R")

        assert rung.co_variation["core_batch_peak_id"] == "T"

    def test_a_listed_compound_carries_its_own_channels_reading(self):
        record = {
            "formula": D4,
            "ionization_mechanism_id": MECH,
            "partner_batch_peak_id": "D2",
            "partner_channel": "[M+CH4N2O+H]+",
            "verdict": "co_varying",
            "r": 0.81,
            "n_spectra": 14,
            "reason": None,
        }
        row_anchor = anchor(
            "D",
            D4_MZ,
            formula=D4,
            ion=D4_ION,
            candidates=d4_candidates(),
            provenance={"co_variation": record},
        )
        members = {"D": {s: analyte(s, D4, D4_ION, mz=D4_MZ) for s in SAMPLES}}
        plan = plan_batch([row_anchor], members, {})

        assert plan.decisions[0].co_variation == {
            "core": "channel",
            "partner_batch_peak_id": "D2",
            "partner_channel": "[M+CH4N2O+H]+",
            "verdict": "co_varying",
            "r": 0.81,
            "n_spectra": 14,
            "reason": None,
        }


class TestThePlan:
    def test_each_partition_is_decided_on_its_own_files(self):
        tof = anchor("U", TRIMER_MZ, ion=TRIMER, intensity_variable="sum_peak_areas")
        anchors = [trimer_row(SAMPLES).anchor, tof]
        members = {
            "T": trimer_row(SAMPLES).members,
            "U": {"t0": claimed("t0")},
        }
        plan = plan_batch(anchors, members, {})
        by_id = {d.row.batch_peak_id: d for d in plan.decisions}

        assert by_id["T"].verdict.verdict == VERDICT_PRESENT
        assert by_id["U"].verdict.verdict == VERDICT_UNPOOLED
        counts = sorted(plan.counts.values(), key=lambda c: c["files"])
        assert [c["files"] for c in counts] == [1, 6]
        assert counts[1]["claimed"] == 1
        assert counts[1]["library"] == 1

    def test_an_ordinary_analyte_is_no_species(self):
        row_anchor = anchor("A", 200.0, formula="C10H16O4", ion="C10H17O4+")
        plan = plan_batch(
            [row_anchor], {"A": {s: analyte(s, "C10H16O4") for s in SAMPLES}}, {}
        )

        assert plan.decisions == []


def _twin(samples, key="W"):
    """The trimer's trace split by the fold: a second batch peak of it, 3 ppm
    higher, with ``samples`` -> its member there."""
    mz = TRIMER_MZ * (1 + 3e-6)
    return Row(
        anchor(key, mz, ion=TRIMER, role="reagent"),
        {s: found(s) for s, found in samples.items()},
    )


def _peak(sample, fn, **fields):
    """A member of a twin, on a peak of its own in the sample."""
    return fn(sample, mz=TRIMER_MZ * (1 + 3e-6), sample_peak_id=f"q-{sample}", **fields)


class TestTheTwins:
    """The fold split a species' trace over two batch peaks: one claims it."""

    def _plan(self, *rows):
        plan = plan_batch(
            [row.anchor for row in rows],
            {row.batch_peak_id: row.members for row in rows},
            {},
        )
        return plan, {d.row.batch_peak_id: d for d in plan.decisions}

    def _split(self):
        """``T`` in s0-s3, claimed in s0; ``W`` in s0 and s2-s4, claimed in s3,
        an analyte at assigned in s0."""
        first = Row(
            anchor("T", TRIMER_MZ, ion=TRIMER, role="reagent"),
            {
                "s0": claimed("s0"),
                "s1": member("s1"),
                "s2": member("s2"),
                "s3": member("s3"),
            },
        )
        second = _twin(
            {
                "s0": lambda s: _peak(s, analyte, formula="C4H12N4O4"),
                "s2": lambda s: _peak(s, member),
                "s3": lambda s: _peak(s, claimed),
                "s4": lambda s: _peak(s, member),
            }
        )
        return first, second

    def test_one_batch_peak_per_ion_claims_and_keeps_a_sample_that_reads_it_on_the_other(
        self,
    ):
        plan, by_id = self._plan(*self._split())

        claiming, twin = by_id["T"], by_id["W"]
        assert claiming.verdict.present and twin.verdict.present
        assert claiming.held is None
        assert claiming.twins == ["W"]
        assert claiming.samples == {
            "s0": ALREADY,
            "s1": None,
            "s2": None,
            "s3": KEPT_ON_TWIN,
        }
        assert (
            claiming.verdict.evidence.claimable,
            claiming.verdict.evidence.kept,
        ) == (
            2,
            1,
        )
        # The twin claims nothing: s0's analyte stands.
        assert twin.held == HELD_TWIN
        assert twin.twin_of == "T"
        assert not twin.claims
        [counts] = plan.counts.values()
        assert (counts["claimed"], counts["held"]) == (1, 1)

    def test_the_batch_peak_in_the_most_files_claims(self):
        first, second = self._split()
        second.members["s5"] = _peak("s5", member)
        _, by_id = self._plan(first, second)

        assert by_id["W"].held is None
        assert by_id["W"].twins == ["T"]
        # s0 reads the ion on T, its own claim: the analyte there stands.
        assert by_id["W"].samples["s0"] == KEPT_ON_TWIN
        assert by_id["T"].held == HELD_TWIN
        assert by_id["T"].twin_of == "W"

    def test_the_twin_claims_where_the_larger_one_would_hold_on_the_vote(self):
        """``T`` is in more samples, but its analytes in s1 and s2 read the ion
        on ``W``: kept, they hold ``T``'s vote, so ``W`` claims in its place."""
        twin_mz = TRIMER_MZ * (1 + 3e-6)
        first = Row(
            anchor("T", TRIMER_MZ, ion=TRIMER, role="reagent"),
            {
                "s0": claimed("s0"),
                "s1": analyte("s1", formula="C4H12N4O4"),
                "s2": analyte("s2", formula="C4H12N4O4"),
                "s3": member("s3"),
            },
        )
        second = _twin(
            {
                "s1": lambda s: _peak(s, claimed),
                "s2": lambda s: _peak(s, claimed),
                "s4": lambda s: _peak(s, member),
            }
        )
        alone, _ = self._plan(first)
        assert alone.decisions[0].claims
        plan, by_id = self._plan(first, second)

        assert by_id["W"].held is None
        assert by_id["W"].twins == ["T"]
        assert by_id["W"].samples == {"s1": ALREADY, "s2": ALREADY, "s4": None}
        assert by_id["T"].held == HELD_TWIN
        assert by_id["T"].twin_of == "W"
        assert by_id["T"].twin_of_mz == pytest.approx(twin_mz, abs=1e-6)
        [counts] = plan.counts.values()
        assert (counts["claimed"], counts["held"]) == (1, 1)

    def test_where_neither_twin_claims_beside_the_other_neither_is_held_for_it(self):
        """Each reads the ion in two samples and an analyte where the other
        reads it: kept, neither has a sample left to claim."""
        twin = lambda s: _peak(s, analyte, formula="C4H12N4O4")  # noqa: E731
        first = Row(
            anchor("T", TRIMER_MZ, ion=TRIMER, role="reagent"),
            {
                "s0": claimed("s0"),
                "s1": claimed("s1"),
                "s2": analyte("s2", formula="C4H12N4O4"),
                "s3": analyte("s3", formula="C4H12N4O4"),
            },
        )
        second = _twin(
            {
                "s0": twin,
                "s1": twin,
                "s2": lambda s: _peak(s, claimed),
                "s3": lambda s: _peak(s, claimed),
            }
        )
        plan, by_id = self._plan(first, second)

        for key, other in (("T", "W"), ("W", "T")):
            assert by_id[key].verdict.present
            assert by_id[key].held is None
            assert by_id[key].twin_of is None
            assert by_id[key].twins == [other]
            assert not by_id[key].claims
        assert by_id["T"].samples == {
            "s0": ALREADY,
            "s1": ALREADY,
            "s2": KEPT_ON_TWIN,
            "s3": KEPT_ON_TWIN,
        }
        [counts] = plan.counts.values()
        assert (counts["claimed"], counts["held"]) == (0, 0)

    def test_a_held_batch_peak_leaves_the_claim_to_its_twin(self):
        first, second = self._split()
        second.members["s5"] = _peak("s5", member)
        second.anchor.provenance = {"manual": {"formula": "C4H12N4O4"}}
        _, by_id = self._plan(first, second)

        assert by_id["W"].held == HELD_CURATED
        assert by_id["T"].held is None
        assert by_id["T"].samples["s3"] == KEPT_ON_TWIN

    def test_a_twin_that_is_not_pooled_still_keeps_the_sample_it_reads_in(self):
        first = trimer_row(SAMPLES[:5])
        second = _twin({"s2": lambda s: _peak(s, claimed)})
        _, by_id = self._plan(first, second)

        assert by_id["W"].verdict.verdict == VERDICT_UNPOOLED
        assert by_id["W"].held is None
        assert by_id["T"].held is None
        assert by_id["T"].samples["s2"] == KEPT_ON_TWIN
        assert by_id["T"].samples["s3"] is None

    def test_a_species_with_no_twin_is_left_as_decided(self):
        _, by_id = self._plan(trimer_row(SAMPLES[:5]))

        assert by_id["T"].twins == []
        assert by_id["T"].twin_of is None

    def test_another_ion_is_no_twin(self):
        first = trimer_row(SAMPLES[:5])
        other = Row(
            anchor("W", 199.11, ion="C3H15N6O4+", role="reagent"),
            {s: claimed(s, ion="C3H15N6O4+", mz=199.11) for s in SAMPLES[2:5]},
        )
        _, by_id = self._plan(first, other)

        assert by_id["T"].twins == []
        assert by_id["W"].held is None

    def test_two_batch_peaks_of_one_listed_formula(self):
        """A twin's member that reads the listed formula, even below
        candidate, keeps the sample."""
        first = Row(
            anchor("D", D4_MZ, formula=D4, ion=D4_ION, candidates=d4_candidates()),
            {
                "s0": analyte("s0", D4, D4_ION, mz=D4_MZ),
                "s1": member("s1", mz=D4_MZ),
                "s2": member("s2", mz=D4_MZ),
                "s3": member("s3", mz=D4_MZ),
            },
        )
        twin_mz = D4_MZ * (1 + 3e-6)
        second = Row(
            anchor("E", twin_mz, formula=D4, ion=D4_ION, candidates=d4_candidates()),
            {
                "s1": analyte("s1", D4, D4_ION, tier="below_assignability", mz=twin_mz),
                "s2": analyte("s2", D4, D4_ION, mz=twin_mz),
                "s4": member("s4", mz=twin_mz),
            },
        )
        _, by_id = self._plan(first, second)

        assert by_id["D"].held is None
        assert by_id["D"].samples == {
            "s0": ALREADY,
            "s1": KEPT_ON_TWIN,
            "s2": KEPT_ON_TWIN,
            "s3": None,
        }
        assert by_id["E"].held == HELD_TWIN

    def test_the_record_names_the_batch_peak_that_claims(self):
        _, by_id = self._plan(*self._split())
        record = species_record(by_id["W"], reads={}, batch_peak_run_id=None)

        assert record["held"] == HELD_TWIN
        assert record["twin_of"] == "T"
        assert record["twin_of_mz"] == pytest.approx(TRIMER_MZ, abs=1e-6)
        assert species_record(by_id["T"], reads={}, batch_peak_run_id=None)[
            "files_by_reason"
        ] == {KEPT_ON_TWIN: 1}


class TestTheRecords:
    def _present(self):
        row = trimer_row(SAMPLES)
        row.members["s5"] = analyte("s5", hand=True)
        return decide_row(row, LIBRARY, 6, {})

    def test_a_present_species_lists_the_files_it_kept_with_their_readings(self):
        record = species_record(
            self._present(),
            reads={
                "role": "reagent",
                "formula": None,
                "ion_formula": TRIMER,
                "ionization_mechanism_id": None,
            },
            batch_peak_run_id="run-1",
            displaced_members={"s1": {"tier": 3}},
            rows_rewritten=1,
            lines_rewritten=2,
            rows_kept={"no_row": 3},
        )

        assert record["verdict"] == VERDICT_PRESENT
        assert record["species"]["ion_formula"] == TRIMER
        assert record["claimed"] is True
        assert record["members_rewritten"] == 1
        assert (record["rows_rewritten"], record["lines_rewritten"]) == (1, 2)
        assert record["rows_kept"] == {"no_row": 3}
        assert record["files_by_reason"] == {KEPT_HAND_ASSIGNED: 1}
        assert record["files_recorded"] == [
            {
                "sample_item_id": "s5",
                "reason": KEPT_HAND_ASSIGNED,
                "role": "M0",
                "formula": "C3H12N6O3",
                "ion_formula": TRIMER,
                "tier": "assigned",
            }
        ]
        assert record["batch_peak_run_id"] == "run-1"

    def test_a_species_not_pooled_lists_the_files_whose_claim_stands(self):
        row = Row(
            anchor("T", TRIMER_MZ),
            {"s0": claimed("s0"), "s1": member("s1")},
        )
        decision = decide_row(row, LIBRARY, 6, {})
        record = species_record(decision, reads={}, batch_peak_run_id=None)

        assert record["verdict"] == VERDICT_UNPOOLED
        assert record["claimed"] is False
        assert record["files_by_reason"] == {OWN_CLAIM: 1}
        assert [entry["sample_item_id"] for entry in record["files_recorded"]] == ["s0"]

    def test_a_rows_record_names_the_batch_peak_and_the_verdict(self):
        assert row_record(self._present(), "run-1") == {
            "batch_peak_id": "T",
            "verdict": VERDICT_PRESENT,
            "files": 6,
            "present": 6,
            "test_held": 1,
            "batch_peak_run_id": "run-1",
        }


def _standing(reads, verdict=VERDICT_PRESENT, **extra):
    return {
        "species": LIBRARY.to_json(),
        "verdict": verdict,
        "held": None,
        "reads": reads,
        "displaced_members": {"s1": {"tier": 3}},
        "own_test_samples": ["s0"],
        "batch_peak_run_id": "run-1",
        **extra,
    }


READS_TRIMER = {
    "role": "reagent",
    "formula": None,
    "ion_formula": TRIMER,
    "ionization_mechanism_id": None,
}


class TestTheConsensusAndTheLedger:
    def _source_members(self):
        return [
            {"role": "reagent", "ion_formula": TRIMER, "intensity": 1e4}
            for _ in SAMPLES
        ]

    def test_the_consensus_keeps_the_record_while_it_reads_the_species(self):
        record = _standing(READS_TRIMER)
        consensus = compute_consensus(self._source_members(), "T", known_species=record)

        assert consensus.provenance[KNOWN_SPECIES_KEY] == record

    def test_a_reading_that_moved_takes_the_record_off(self):
        record = _standing(READS_TRIMER)
        moved = [
            {
                "assigned_formula": "C3H12N6O3",
                "ion_formula": TRIMER,
                "ionization_mechanism_id": MECH,
                "tier": "assigned",
                "fit_score": 0.9,
                "intensity": 1e4,
            }
            for _ in SAMPLES
        ]

        assert (
            KNOWN_SPECIES_KEY
            not in compute_consensus(moved, "T", known_species=record).provenance
        )

    def test_only_a_record_with_a_verdict_and_a_reading_is_one(self):
        assert (
            known_species_record_of(
                anchor("T", 1.0, provenance={KNOWN_SPECIES_KEY: {}})
            )
            is None
        )
        assert (
            known_species_record_of(
                anchor("T", 1.0, provenance={KNOWN_SPECIES_KEY: {"verdict": "present"}})
            )
            is None
        )
        assert known_species_record_of(anchor("T", 1.0, provenance=None)) is None

    def test_the_ledger_and_a_snapshot_serve_it_without_the_member_states(self):
        record = _standing(READS_TRIMER)
        bp = anchor(
            "T",
            TRIMER_MZ,
            ion=TRIMER,
            role="reagent",
            provenance={KNOWN_SPECIES_KEY: record},
        )
        bp.engine_tier = bp.engine_claim = None
        bp.continuity_grade = bp.continuity = None
        bp.sample_batch_id = "b"
        bp.best_fit_score = None
        bp.support_fraction = None
        bp.n_present = 6
        bp.is_ambiguous = 0
        served = _batch_peak_meta(bp)["known_species"]

        assert served["verdict"] == VERDICT_PRESENT
        assert "displaced_members" not in served
        assert "own_test_samples" not in served
        assert known_species_record_of(bp)["displaced_members"]
        assert known_species_record_of(bp)["own_test_samples"] == ["s0"]
        snapshot = SimpleNamespace(**anchor_snapshot_row("run-1", bp, []))
        assert snapshot_anchor_meta(snapshot, "b")["known_species"] == served

    def test_a_record_the_anchor_moved_off_is_not_served(self):
        bp = anchor(
            "T",
            TRIMER_MZ,
            formula="C3H12N6O3",
            ion=TRIMER,
            provenance={KNOWN_SPECIES_KEY: _standing(READS_TRIMER)},
        )

        assert standing_known_species_record(bp) is None
        assert known_species_summary(bp) is None

    def test_an_anchors_reading_is_its_role_formula_ion_and_mechanism(self):
        bp = anchor("D", D4_MZ, formula=D4, ion=D4_ION)

        assert reading_of(bp) == {
            "role": None,
            "formula": D4,
            "ion_formula": D4_ION,
            "ionization_mechanism_id": MECH,
        }


class TestTheSummary:
    def test_it_counts_each_species_as_the_write_left_it(self):
        anchors = [trimer_row(SAMPLES).anchor]
        plan = plan_batch(anchors, {"T": trimer_row(SAMPLES).members}, {})
        [decision] = plan.decisions
        written = [
            Written(
                decision,
                True,
                members_rewritten=5,
                rows_rewritten=1,
                lines_rewritten=1,
                rows_kept={"no_row": 4},
            )
        ]
        summary = summarise(plan, written, cleared=2)

        assert (summary["species"], summary["present"], summary["claimed"]) == (1, 1, 1)
        assert summary["members_rewritten"] == 5
        assert summary["rows_rewritten"] == 1
        assert summary["lines_rewritten"] == 1
        assert summary["rows_kept"] == {"no_row": 4}
        assert summary["records_cleared"] == 2
        assert summary["species_listed"][0]["batch_peak_id"] == "T"
        assert summary["species_listed"][0]["claimed"] == 5
        assert summary["partitions"][0]["files"] == 6

    def test_a_species_the_write_held_counts_as_held(self):
        plan = plan_batch(
            [trimer_row(SAMPLES).anchor], {"T": trimer_row(SAMPLES).members}, {}
        )
        [decision] = plan.decisions
        decision.held = HELD_VOTE
        summary = summarise(plan, [Written(decision, False)])

        assert (summary["claimed"], summary["held"]) == (0, 1)
        assert summary["held_by_reason"] == {HELD_VOTE: 1}

    def test_the_outcome_says_what_was_decided(self):
        plan = plan_batch(
            [trimer_row(SAMPLES).anchor], {"T": trimer_row(SAMPLES).members}, {}
        )
        [decision] = plan.decisions
        summary = summarise(plan, [Written(decision, True, members_rewritten=5)])
        outcome = deciding_outcome(summary, "b")

        assert outcome["status"] == "success"
        assert "1 present" in outcome["message"]
        assert "5 more samples" in outcome["message"]
        assert outcome["_notification_data"] == {"sample_batch_id": "b"}

    def test_a_batch_with_no_species_is_partial(self):
        outcome = deciding_outcome(summarise(Plan(), []), "b")

        assert outcome["status"] == "partial"


class TestTheRowRewrite:
    def _record(self):
        return {
            "batch_peak_id": "T",
            "verdict": "present",
            "files": 6,
            "present": 6,
            "test_held": 1,
            "batch_peak_run_id": "run-1",
        }

    def _species(self):
        [cluster] = [c for c in reagent_library("UR") if c.formula == "C3H13N6O3"]
        return BatchSpecies(cluster, self._record())

    def test_a_library_ions_row_is_the_pre_passs_claim(self):
        row = dict(
            peak_assignment_id="pa-1",
            sample_peak_mz=TRIMER_MZ * (1 + 2e-6),
            role="M0",
            assigned_formula="C3H12N6O3",
            ion_formula=TRIMER,
            ionization_mechanism_id=MECH,
            source="untargeted",
            tier="assigned",
            fit_score=0.8,
            mz_error_ppm=0.4,
            alternatives=[{"assigned_formula": "C4H12N4O3"}],
            provenance={"evidence": 0.8},
        )
        values = batch_species_row(row, self._species(), exact_mz=TRIMER_MZ)

        assert values["role"] == "reagent"
        assert values["assigned_formula"] is None
        assert values["ion_formula"] == TRIMER
        assert values["ionization_mechanism_id"] is None
        assert values["source"] == "reagent"
        assert values["tier"] == "unassigned"
        assert values["fit_score"] is None
        assert values["owner_peak_assignment_id"] is None
        assert values["mz_error_ppm"] == pytest.approx(2.0, abs=0.01)
        reagent = values["provenance"]["reagent"]
        assert reagent["ion"] == "[(CH4N2O)3+H]+"
        assert reagent["reason"] == REASON_BATCH_KNOWN_SPECIES
        assert reagent[REASON_BATCH_KNOWN_SPECIES] == self._record()
        assert reagent["read_as"]["formula"] == "C3H12N6O3"
        assert reagent["read_as"]["tier"] == "assigned"
        displaced, earlier = values["alternatives"]
        assert displaced["assigned_formula"] == "C3H12N6O3"
        assert displaced[DISPLACED_BY_CLAIM] is True
        assert earlier == {"assigned_formula": "C4H12N4O3"}

    def test_an_unread_peak_brings_no_alternative(self):
        values = batch_species_row(
            dict(sample_peak_mz=TRIMER_MZ, role="unassigned"), self._species()
        )

        assert values["alternatives"] is None

    def test_its_lines_keep_their_label_errors_and_owner(self):
        line = dict(
            sample_peak_mz=182.1,
            role="iso_child",
            assigned_formula="C3H12N6O3",
            ion_formula="[13C]C2H13N6O3+",
            isotope_label="13C",
            mz_error_ppm=0.7,
            abundance_error=-0.1,
            owner_peak_assignment_id="pa-1",
            tier="assigned",
        )
        values = batch_species_row(line, self._species())

        assert values["role"] == "reagent"
        assert values["isotope_label"] == "13C"
        assert values["ion_formula"] == TRIMER
        assert values["mz_error_ppm"] == 0.7
        assert values["abundance_error"] == -0.1
        assert values["owner_peak_assignment_id"] == "pa-1"
        assert values["alternatives"] is None

    def test_a_listed_compounds_row_is_the_lists_reading_at_candidate(self):
        exemplar = dict(
            sample_peak_mz=D4_MZ * (1 + 1e-6),
            mz_error_ppm=1.0,
            source="database",
            target_compound_id=None,
            target_ion_id=None,
            provenance={REFERENCE_IDENTITIES_COL: [{"name": "D4"}], "evidence": 0.9},
        )
        row = dict(
            sample_peak_mz=D4_MZ * (1 + 3e-6),
            role="M0",
            assigned_formula="C9H24N2O4Si3",
            ion_formula=D4_ION,
            ionization_mechanism_id=MECH,
            source="untargeted",
            tier="candidate",
            alternatives=None,
        )
        values = listed_species_row(row, exemplar, LISTED, self._record())

        assert values["assigned_formula"] == D4
        assert values["ion_formula"] == D4_ION
        assert values["ionization_mechanism_id"] == MECH
        assert values["tier"] == LISTED_CLAIM_TIER
        assert values["source"] == "database"
        assert values["fit_score"] is None
        assert values["mz_error_ppm"] == pytest.approx(3.0, abs=0.01)
        provenance = values["provenance"]
        assert provenance[REFERENCE_IDENTITIES_COL] == [{"name": "D4"}]
        assert provenance[REASON_BATCH_KNOWN_SPECIES]["batch_peak_id"] == "T"
        assert provenance[REASON_BATCH_KNOWN_SPECIES]["read_as"]["formula"] == (
            "C9H24N2O4Si3"
        )
        assert provenance["tier_reasons"][0]["rule"] == REASON_BATCH_KNOWN_SPECIES
        assert "evidence" not in provenance
        [displaced] = values["alternatives"]
        assert displaced["assigned_formula"] == "C9H24N2O4Si3"


class TestTheListedLift:
    def test_a_lifted_row_keeps_its_reading_and_takes_the_tier_and_reason(self):
        row = dict(
            role="M0",
            assigned_formula=D4,
            ion_formula=D4_ION,
            ionization_mechanism_id=MECH,
            tier="below_assignability",
            fit_score=0.4,
            provenance={"evidence": 0.3, "tier_reasons": [{"rule": "lone_peak"}]},
        )
        record = {"batch_peak_id": "D", "verdict": "present"}
        values = listed_species_row(row, {}, LISTED, record)

        assert set(values) == {"tier", "provenance"}
        assert values["tier"] == LISTED_CLAIM_TIER
        provenance = values["provenance"]
        assert provenance["evidence"] == 0.3
        assert [r["rule"] for r in provenance["tier_reasons"]] == [
            "lone_peak",
            REASON_BATCH_KNOWN_SPECIES,
        ]
        assert provenance[REASON_BATCH_KNOWN_SPECIES]["read_as"] == {
            "tier": "below_assignability"
        }
        assert row["provenance"]["tier_reasons"] == [{"rule": "lone_peak"}]

    def test_a_lifted_member_votes_with_its_own_fit(self):
        """Four files read the compound below candidate with good fits; two
        read another compound by hand: the lift turns the vote."""
        members = {
            s: analyte(s, D4, D4_ION, tier="below_assignability", fit_score=0.9)
            for s in SAMPLES[:4]
        }
        members["s0"] = analyte("s0", D4, D4_ION, fit_score=0.9)
        for sample in SAMPLES[4:]:
            members[sample] = analyte(
                sample, "C9H24N2O4Si3", D4_ION, fit_score=0.95, hand=True
            )
        row = Row(anchor("D", D4_MZ, candidates=d4_candidates()), members)
        samples = {s: None for s in SAMPLES[1:4]}
        samples.update(
            {"s0": ALREADY, "s4": KEPT_HAND_ASSIGNED, "s5": KEPT_HAND_ASSIGNED}
        )

        assert claim_turns(row, LISTED, samples)


class TestTheFilesClaim:
    """A file's pre-pass takes its batch's word for a library ion."""

    def _record(self, **extra):
        return {
            "species": LIBRARY.to_json(),
            "verdict": VERDICT_PRESENT,
            "held": None,
            "files": 6,
            "present": 6,
            "test_held": 1,
            "batch_peak_run_id": "run-1",
            "reads": READS_TRIMER,
            **extra,
        }

    def test_a_present_library_ion_is_taken(self):
        species = batch_species_from(self._record(), "T")

        assert species.cluster.formula == "C3H13N6O3"
        assert species.cluster.charge == 1
        assert species.cluster.label == "[(CH4N2O)3+H]+"
        assert species.cluster.mz == pytest.approx(TRIMER_MZ, abs=1e-4)
        assert species.record == {
            "batch_peak_id": "T",
            "verdict": VERDICT_PRESENT,
            "files": 6,
            "present": 6,
            "test_held": 1,
            "batch_peak_run_id": "run-1",
        }

    @pytest.mark.parametrize(
        "change",
        [
            {"verdict": VERDICT_UNPOOLED},
            {"held": HELD_VOTE},
            {"species": LISTED.to_json()},
        ],
        ids=["not present", "held", "listed"],
    )
    def test_any_other_record_is_not(self, change):
        assert batch_species_from(self._record(**change), "T") is None

    def _peaks(self, *mzs):
        return pd.DataFrame(
            {
                "sample_peak_id": [f"p{i}" for i in range(len(mzs))],
                "mz": list(mzs),
                "intensity": [1e3] * len(mzs),
            }
        )

    def test_the_file_claims_it_where_its_own_claim_did_not(self):
        species = batch_species_from(self._record(), "T")
        hits = claim_batch_species(
            self._peaks(TRIMER_MZ),
            [species],
            calibration=ReagentCalibration(0.0, 3.0),
            claimed=[],
            claim_ppm=3.0,
        )

        assert [hit.index for hit in hits] == [0]

    def test_an_ion_the_files_own_claim_took_is_its_own(self):
        species = batch_species_from(self._record(), "T")
        own = ReagentHit(
            cluster=species.cluster,
            index=1,
            mz=TRIMER_MZ,
            intensity=1e3,
            mz_error_ppm=0,
        )
        hits = claim_batch_species(
            self._peaks(TRIMER_MZ, TRIMER_MZ * (1 + 1e-6)),
            [species],
            calibration=ReagentCalibration(0.0, 3.0),
            claimed=[own],
            claim_ppm=3.0,
        )

        assert hits == []

    def test_a_peak_another_ion_of_the_file_took_is_left(self):
        species = batch_species_from(self._record(), "T")
        [other] = [c for c in reagent_library("UR") if c.formula == "CH5N2O"]
        taken = ReagentHit(
            cluster=other, index=0, mz=TRIMER_MZ, intensity=1e3, mz_error_ppm=0
        )
        hits = claim_batch_species(
            self._peaks(TRIMER_MZ),
            [species],
            calibration=ReagentCalibration(0.0, 3.0),
            claimed=[taken],
            claim_ppm=3.0,
        )

        assert hits == []

    def test_a_file_whose_anchors_said_nothing_claims_nothing(self):
        species = batch_species_from(self._record(), "T")

        assert (
            claim_batch_species(
                self._peaks(TRIMER_MZ),
                [species],
                calibration=None,
                claimed=[],
                claim_ppm=3.0,
            )
            == []
        )

    def test_its_rows_name_the_batchs_decision_as_their_reason(self):
        species = batch_species_from(self._record(), "T")
        peaks = self._peaks(TRIMER_MZ)
        hit = ReagentHit(
            cluster=species.cluster,
            index=0,
            mz=TRIMER_MZ,
            intensity=1e3,
            mz_error_ppm=0,
        )
        [row] = build_reagent_assignments(
            [hit], peaks, "s", "run", batch={"C3H13N6O3": species.record}
        )
        [own] = build_reagent_assignments([hit], peaks, "s", "run")

        assert row["provenance"]["reagent"]["reason"] == REASON_BATCH_KNOWN_SPECIES
        assert (
            row["provenance"]["reagent"][REASON_BATCH_KNOWN_SPECIES] == species.record
        )
        assert "reason" not in own["provenance"]["reagent"]

    def test_the_pre_pass_claims_the_batchs_ions_and_records_why(self):
        species = batch_species_from(self._record(), "T")
        resolved = SimpleNamespace(
            profile=SimpleNamespace(name="UR", label_purity=None),
            mz_precision_ppm=3.0,
            fallback_sigma_ppm=1.0,
        )
        peaks = self._peaks(TRIMER_MZ)
        with (
            patch.object(
                service,
                "claim_reagent_peaks",
                return_value=([], ReagentCalibration(0.0, 3.0)),
            ),
            patch.object(service, "_claimed_lines_offset", return_value=None),
            patch.object(service, "claim_ladder_peaks", return_value=([], None)),
        ):
            rows, taken, _ = service._reagent_assignments(
                peaks,
                resolved,
                "sample1",
                "run1",
                resolution=None,
                own_mechanisms=[],
                sample_name="sample",
                batch_species=[species],
            )
            alone, _, _ = service._reagent_assignments(
                peaks,
                resolved,
                "sample1",
                "run1",
                resolution=None,
                own_mechanisms=[],
                sample_name="sample",
            )

        [row] = [r for r in rows if r["isotope_label"] is None]
        assert row["ion_formula"] == TRIMER
        assert row["provenance"]["reagent"]["reason"] == REASON_BATCH_KNOWN_SPECIES
        assert "p0" in taken
        assert alone == []
