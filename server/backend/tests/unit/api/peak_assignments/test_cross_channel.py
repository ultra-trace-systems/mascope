"""What a sample's channels corroborate, and the nitrogen a reagent can hide."""

from __future__ import annotations

import pytest

from mascope_backend.api.new.peak_assignments.cross_channel import (
    CHANNELS_FOR_CORROBORATION,
    REASON_AMBIGUOUS_NITROGEN,
    apply_cross_channel,
    channels_by_neutral,
    fixes_nitrogen,
    reagent_substitutions,
    substitution_for,
    within_search_space,
)


PROTON = "prot"
AMMONIUM = "amm"
UREA = "urea"
NITRATE = "nit"
DEPROT = "deprot"
BROMIDE = "brom"

POSITIVE = {
    PROTON: "+H+",
    AMMONIUM: "+NH4+",
    UREA: "+(CH4N2O)H+",
}
NEGATIVE = {
    DEPROT: "-H+",
    NITRATE: "+NO3-",
    BROMIDE: "+Br-",
}
RANGES = "C1-40 H0-90 N0-5 O0-15 S0-2"


def row(
    row_id: str,
    formula: str,
    mechanism: str,
    *,
    tier: str = "assigned",
    role: str = "M0",
    source: str = "untargeted",
    owner: str | None = None,
) -> dict:
    return {
        "peak_assignment_id": row_id,
        "assigned_formula": formula,
        "ionization_mechanism_id": mechanism,
        "role": role,
        "tier": tier,
        "source": source,
        "owner_peak_assignment_id": owner,
    }


def gate(rows: list[dict], notation_by_id: dict[str, str] | None = None) -> dict:
    return apply_cross_channel(
        rows,
        notation_by_id=dict(POSITIVE if notation_by_id is None else notation_by_id),
        element_ranges=RANGES,
    )


class TestWhichChannelsCouldBeHidingNitrogen:
    def test_ammonium_restates_as_protonation_of_an_amine(self):
        substitution = substitution_for("+NH4+", "+H+")
        assert substitution is not None
        assert substitution.label == "H3N"
        assert substitution.donates_nitrogen

    def test_the_urea_adduct_carries_two(self):
        substitution = substitution_for("+(CH4N2O)H+", "+H+")
        assert substitution is not None
        assert substitution.delta["N"] == 2

    def test_a_nitrate_cluster_restates_as_a_deprotonated_nitrate_ester(self):
        # The negative-mode form of the same arithmetic: adding NO3 to M is the
        # same ion as taking H off M+HNO3, so the reagent's nitrogen and an
        # analyte nitrogen are the same measurement.
        substitution = substitution_for("+NO3-", "-H+")
        assert substitution is not None
        assert substitution.label == "HNO3"
        assert substitution.donates_nitrogen

    def test_a_labelled_reagent_hides_nothing(self):
        # The reason anyone runs a labelled reagent. The 15N of "+^NO3-" is
        # 0.997 Da from an analyte's own nitrogen, so a deprotonated nitrate
        # ester is a different ion at a different mass rather than the same one
        # read differently - and the spectrum, not a sort key, chooses.
        assert substitution_for("+^NO3-", "-H+") is None
        assert reagent_substitutions(["-H+", "+^NO3-", "+CO3-"]) == {}

    def test_a_bromide_cluster_hides_no_nitrogen(self):
        substitution = substitution_for("+Br-", "-H+")
        assert substitution is not None
        assert not substitution.donates_nitrogen

    def test_channels_of_different_polarity_cannot_explain_one_peak(self):
        assert substitution_for("+NH4+", "-H+") is None

    def test_the_run_finds_its_own_donors_without_being_told_them(self):
        assert set(reagent_substitutions(list(POSITIVE.values()))) == {
            "+NH4+",
            "+(CH4N2O)H+",
        }
        assert set(reagent_substitutions(list(NEGATIVE.values()))) == {"+NO3-"}

    def test_a_mode_with_no_plain_channel_has_nothing_to_restate_against(self):
        # Without protonation or deprotonation in the searched set there is no
        # reading to compare a cluster with, so nothing is ambiguous.
        assert reagent_substitutions(["+NH4+", "+(CH4N2O)H+"]) == {}


class TestWhatCorroboratesANeutral:
    def test_one_neutral_through_two_channels_is_corroborated(self):
        rows = [row("a", "C6H12O6", PROTON), row("b", "C6H12O6", AMMONIUM)]
        summary = gate(rows)
        assert summary["corroborated"] == 2
        assert rows[0]["provenance"]["cross_channel"]["corroborated"]
        assert rows[0]["provenance"]["cross_channel"]["channels"] == ["+H+", "+NH4+"]

    def test_a_lone_channel_is_not(self):
        rows = [row("a", "C6H12O6", PROTON)]
        assert gate(rows)["corroborated"] == 0
        assert not rows[0]["provenance"]["cross_channel"]["corroborated"]

    def test_two_readings_through_the_same_channel_are_one_observation(self):
        # The same neutral committed on two peaks of one channel - an in-source
        # fragment, a second charge state - is one chemistry, not two.
        rows = [row("a", "C6H12O6", PROTON), row("b", "C6H12O6", PROTON)]
        assert gate(rows)["corroborated"] == 0

    def test_a_satellite_is_not_a_second_channel(self):
        # Its parent's ion on a second line of one envelope. Counting it would
        # let a reading corroborate itself.
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", PROTON, role="iso_child", owner="a"),
        ]
        assert gate(rows)["corroborated"] == 0

    def test_an_orphan_satellite_brings_no_channel_with_it(self):
        # Where the M0-only rule bites: an isotopologue whose own monoisotopic
        # row was never committed. Step 2.2 measured what those are on a
        # crowded spectrum - a peak the matching window reached, paired to an
        # ion nothing else in the ledger supports - so letting one carry a
        # channel would corroborate a neutral on a coincidence.
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", AMMONIUM, role="iso_child", owner="gone"),
        ]
        summary = gate(rows)
        assert summary["corroborated"] == 0
        assert rows[0]["provenance"]["cross_channel"]["channels"] == ["+H+"]

    def test_an_uncommitted_row_corroborates_nothing(self):
        rows = [
            row("a", "C6H12O6", PROTON),
            {**row("b", "C6H12O6", AMMONIUM), "assigned_formula": None},
        ]
        assert gate(rows)["corroborated"] == 0

    def test_the_bar_is_two_channels(self):
        assert CHANNELS_FOR_CORROBORATION == 2


class TestTheReagentNRule:
    def test_a_lone_ammonium_reading_cannot_fix_its_nitrogen_count(self):
        rows = [row("a", "C6H12O6", AMMONIUM)]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 1
        assert summary["capped"] == 1
        assert rows[0]["tier"] == "candidate"
        record = rows[0]["provenance"]["cross_channel"]
        assert record["reason"] == REASON_AMBIGUOUS_NITROGEN
        # The alternative is named, because a reason a reader cannot check is
        # not a reason: this is the neutral the plain channel would have called
        # the same peak.
        assert record["ambiguous_nitrogen"]["alternative"] == "C6H15NO6"
        assert record["ambiguous_nitrogen"]["via"] == "+H+"

    def test_the_formula_stays_on_the_row(self):
        rows = [row("a", "C6H12O6", AMMONIUM)]
        gate(rows)
        assert rows[0]["assigned_formula"] == "C6H12O6"

    def test_a_plain_channel_on_the_same_neutral_fixes_the_count(self):
        rows = [row("a", "C6H12O6", AMMONIUM), row("b", "C6H12O6", PROTON)]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_the_ammonium_and_urea_pair_fixes_it_too(self):
        # The alternative reading would need a different analyte for each
        # channel, the two differing by exactly urea less ammonia. One neutral
        # explains both; two coincidences are needed to avoid it.
        rows = [row("a", "C6H12O6", AMMONIUM), row("b", "C6H12O6", UREA)]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_a_second_reagent_is_needed_not_just_a_second_peak(self):
        rows = [row("a", "C6H12O6", AMMONIUM), row("b", "C6H12O6", AMMONIUM)]
        assert gate(rows)["ambiguous_nitrogen"] == 2

    def test_a_plain_channel_reading_is_never_ambiguous(self):
        rows = [row("a", "C6H12O6", PROTON)]
        assert gate(rows)["ambiguous_nitrogen"] == 0

    def test_a_curated_identity_is_exempt(self):
        # The formula came from a library that named the compound, so the
        # nitrogen sits where the curation put it rather than where the sort
        # key did.
        rows = [row("a", "C6H12O6", AMMONIUM, source="database")]
        assert gate(rows)["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_an_alternative_the_grid_cannot_reach_is_no_alternative(self):
        # This run's own window stops at five nitrogens, so a neutral already
        # holding five has no ammoniated alternative the search could have
        # written and its count is the run's answer rather than a coin toss.
        rows = [row("a", "C6H12N5O6", AMMONIUM)]
        assert gate(rows)["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_the_cap_only_goes_downwards(self):
        rows = [row("a", "C6H12O6", AMMONIUM, tier="below_assignability")]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 1
        assert summary["capped"] == 0
        assert rows[0]["tier"] == "below_assignability"

    def test_the_nitrate_channel_is_gated_the_same_way(self):
        rows = [row("a", "C5H8O4", NITRATE)]
        summary = apply_cross_channel(
            rows, notation_by_id=dict(NEGATIVE), element_ranges=RANGES
        )
        assert summary["capped"] == 1
        assert (
            rows[0]["provenance"]["cross_channel"]["ambiguous_nitrogen"]["alternative"]
            == "C5H9NO7"
        )

    def test_a_bromide_run_has_nothing_to_gate(self):
        rows = [row("a", "CH2O2", BROMIDE)]
        summary = apply_cross_channel(
            rows,
            notation_by_id={DEPROT: "-H+", BROMIDE: "+Br-"},
            element_ranges=RANGES,
        )
        assert summary["applied"] is False
        assert summary["capped"] == 0
        assert rows[0]["tier"] == "assigned"


class TestWhatHappensToTheSatellites:
    def test_a_capped_reading_takes_its_own_satellites_with_it(self):
        # The child carries the parent's neutral, so it carries the parent's
        # doubt; a run that demoted the M0 and left its isotopologue at assigned
        # would be reporting two confidences for one reading.
        rows = [
            row("a", "C6H12O6", AMMONIUM),
            row("b", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
        ]
        summary = gate(rows)
        assert summary["capped"] == 1
        assert summary["capped_satellites"] == 1
        assert rows[1]["tier"] == "candidate"
        assert rows[1]["provenance"]["cross_channel"]["inherited_from"] == "a"

    def test_they_are_counted_apart_from_the_analytes(self):
        # A rule's reach over analytes and its reach over their satellites are
        # different numbers, and reporting the sum as one hides which it moved.
        rows = [
            row("a", "C6H12O6", AMMONIUM),
            row("b", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
            row("c", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
        ]
        summary = gate(rows)
        assert (summary["capped"], summary["capped_satellites"]) == (1, 2)

    def test_a_satellite_of_an_untouched_parent_is_untouched(self):
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", PROTON, role="iso_child", owner="a"),
        ]
        gate(rows)
        assert rows[1]["tier"] == "assigned"


class TestTheRunsRecord:
    def test_the_run_records_what_it_searched_and_what_could_hide_nitrogen(self):
        summary = gate([row("a", "C6H12O6", PROTON)])
        assert summary["channels"] == ["+(CH4N2O)H+", "+H+", "+NH4+"]
        assert summary["reagent_channels"] == {
            "+NH4+": "H3N",
            "+(CH4N2O)H+": "CH4N2O",
        }

    def test_a_run_with_no_donor_channel_records_that_it_stood_down(self):
        summary = apply_cross_channel(
            [row("a", "CH2O2", DEPROT)],
            notation_by_id={DEPROT: "-H+", BROMIDE: "+Br-"},
            element_ranges=RANGES,
        )
        assert summary["applied"] is False

    def test_the_counts_are_of_committed_monoisotopic_rows(self):
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", PROTON, role="iso_child", owner="a"),
        ]
        assert gate(rows)["committed_m0"] == 1

    @pytest.mark.parametrize(
        "counts, expected",
        [
            ({"C": 6, "H": 12, "O": 6}, True),
            ({"C": 6, "H": 12, "N": 6, "O": 6}, False),
            ({"H": 12, "O": 6}, False),
        ],
    )
    def test_the_window_is_the_runs_own(self, counts, expected):
        ranges = {"C": (1, 40), "H": (0, 90), "N": (0, 5), "O": (0, 15), "S": (0, 2)}
        assert within_search_space(counts, ranges) is expected


class TestFixingTheCount:
    def test_a_non_donor_channel_fixes_it(self):
        substitutions = reagent_substitutions(list(POSITIVE.values()))
        assert fixes_nitrogen(frozenset({"+NH4+", "+H+"}), substitutions)

    def test_two_donors_fix_it(self):
        substitutions = reagent_substitutions(list(POSITIVE.values()))
        assert fixes_nitrogen(frozenset({"+NH4+", "+(CH4N2O)H+"}), substitutions)

    def test_one_donor_alone_does_not(self):
        substitutions = reagent_substitutions(list(POSITIVE.values()))
        assert not fixes_nitrogen(frozenset({"+NH4+"}), substitutions)


def test_channels_by_neutral_reads_only_committed_monoisotopic_rows():
    rows = [
        row("a", "C6H12O6", PROTON),
        row("b", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
        {**row("c", "C6H12O6", UREA), "assigned_formula": None},
    ]
    assert channels_by_neutral(rows, dict(POSITIVE)) == {"C6H12O6": frozenset({"+H+"})}
