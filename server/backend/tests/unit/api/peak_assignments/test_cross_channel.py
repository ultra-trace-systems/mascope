"""What a sample's channels corroborate, and the nitrogen a reagent can hide."""

from __future__ import annotations

from mascope_backend.api.new.peak_assignments.cross_channel import (
    CHANNELS_FOR_CORROBORATION,
    REASON_AMBIGUOUS_NITROGEN,
    apply_cross_channel,
    channels_by_neutral,
    donates_nitrogen,
    fixes_nitrogen,
    neutral_key,
    nitrogen_donating_channels,
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


def row(
    row_id: str,
    formula: str,
    mechanism: str,
    *,
    tier: str = "assigned",
    role: str = "M0",
    source: str = "untargeted",
    owner: str | None = None,
    displaced: tuple[str, str] | None = None,
) -> dict:
    """One committed row.

    ``displaced`` is the reading of the same ion the finder's election set
    aside, as ``(neutral, mechanism id)`` - what
    ``elect_same_ion_families`` stores and what the reagent-N rule reads. A row
    without one is a row whose ion the finder had only one reading of.
    """
    alternatives = None
    if displaced is not None:
        alternatives = [
            {
                "assigned_formula": displaced[0],
                "ionization_mechanism_id": displaced[1],
                "same_ion": True,
            }
        ]
    return {
        "peak_assignment_id": row_id,
        "assigned_formula": formula,
        "ionization_mechanism_id": mechanism,
        "role": role,
        "tier": tier,
        "source": source,
        "owner_peak_assignment_id": owner,
        "alternatives": alternatives,
    }


def gate(rows: list[dict], notation_by_id: dict[str, str] | None = None) -> dict:
    return apply_cross_channel(
        rows,
        notation_by_id=dict(POSITIVE if notation_by_id is None else notation_by_id),
    )


def ammoniated(row_id: str, formula: str, displaced_neutral: str, **kwargs) -> dict:
    """An ammonium reading whose protonated alternative the finder displaced."""
    return row(
        row_id, formula, AMMONIUM, displaced=(displaced_neutral, PROTON), **kwargs
    )


class TestWhichChannelsCouldBeHidingNitrogen:
    def test_ammonium_could_be_carrying_the_reported_nitrogen(self):
        assert donates_nitrogen("+NH4+")

    def test_so_could_the_urea_adduct(self):
        assert donates_nitrogen("+(CH4N2O)H+")

    def test_and_a_nitrate_cluster(self):
        # The negative-mode form: the reagent's nitrogen and an analyte nitrogen
        # are the same atoms in the same ion, split differently.
        assert donates_nitrogen("+NO3-")

    def test_a_bromide_cluster_carries_none(self):
        assert not donates_nitrogen("+Br-")

    def test_neither_does_protonation_or_deprotonation(self):
        assert not donates_nitrogen("+H+")
        assert not donates_nitrogen("-H+")

    def test_a_labelled_reagent_carries_none_either(self):
        # The reason anyone runs a labelled reagent. Its 15N is 0.997 Da from an
        # analyte's own nitrogen, so the deprotonated nitrate ester is a
        # different ion at a different mass and the spectrum chooses between
        # them - there is nothing for a prior to decide.
        #
        # Both spellings, because production sends the bracketed one: mechanism
        # notations reach the finder through `to_explicit_isotope_format`, and
        # the caret form is what `parse_ionization` re-spells it to.
        assert not donates_nitrogen("+[15N]O3-")
        assert not donates_nitrogen("+^NO3-")

    def test_an_unreadable_mechanism_donates_nothing(self):
        assert not donates_nitrogen("not a mechanism")
        assert not donates_nitrogen(None)

    def test_the_run_finds_its_own_donors_without_being_told_them(self):
        assert nitrogen_donating_channels(list(POSITIVE.values())) == {
            "+NH4+",
            "+(CH4N2O)H+",
        }
        assert nitrogen_donating_channels(list(NEGATIVE.values())) == {"+NO3-"}
        assert nitrogen_donating_channels(["-H+", "+[15N]O3-", "+CO3-"]) == frozenset()


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
        rows = [ammoniated("a", "C6H12O6", "C6H15NO6")]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 1
        assert summary["capped"] == 1
        assert rows[0]["tier"] == "candidate"
        record = rows[0]["provenance"]["cross_channel"]
        assert record["reason"] == REASON_AMBIGUOUS_NITROGEN
        # The alternative is named, because a reason a reader cannot check is
        # not a reason: this is the neutral the plain channel would have called
        # the same peak.
        # The alternative is named off the row's own family rather than rebuilt
        # from the element grid: it is what the finder proposed and the election
        # displaced, which is the thing the tier is doubting.
        assert record["ambiguous_nitrogen"]["alternative"] == "C6H15NO6"
        assert record["ambiguous_nitrogen"]["via"] == "+H+"

    def test_the_formula_stays_on_the_row(self):
        rows = [ammoniated("a", "C6H12O6", "C6H15NO6")]
        gate(rows)
        assert rows[0]["assigned_formula"] == "C6H12O6"

    def test_a_plain_channel_on_the_same_neutral_fixes_the_count(self):
        rows = [ammoniated("a", "C6H12O6", "C6H15NO6"), row("b", "C6H12O6", PROTON)]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_the_ammonium_and_urea_pair_fixes_it_too(self):
        # The alternative reading would need a different analyte for each
        # channel, the two differing by exactly urea less ammonia. One neutral
        # explains both; two coincidences are needed to avoid it.
        rows = [
            ammoniated("a", "C6H12O6", "C6H15NO6"),
            row("b", "C6H12O6", UREA, displaced=("C7H16N2O7", PROTON)),
        ]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_a_second_reagent_is_needed_not_just_a_second_peak(self):
        rows = [
            ammoniated("a", "C6H12O6", "C6H15NO6"),
            ammoniated("b", "C6H12O6", "C6H15NO6"),
        ]
        assert gate(rows)["ambiguous_nitrogen"] == 2

    def test_a_plain_channel_reading_is_never_ambiguous(self):
        rows = [row("a", "C6H12O6", PROTON)]
        assert gate(rows)["ambiguous_nitrogen"] == 0

    def test_a_reading_the_finder_had_only_one_of_is_not_in_question(self):
        # No same-ion family means the election never chose anything: the
        # ammoniated reading is the only one this run proposed for the ion, so
        # the nitrogen count is not a prior's answer.
        rows = [row("a", "C6H12O6", AMMONIUM)]
        assert gate(rows)["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_a_family_of_two_donors_leaves_the_count_in_question(self):
        # A displaced reading through ANOTHER nitrogen donor moves the nitrogen
        # between reagents, not onto the analyte, so it settles nothing - the
        # rule needs a reading that donates none.
        rows = [row("a", "C6H12O6", AMMONIUM, displaced=("C5H8O5", UREA))]
        assert gate(rows)["ambiguous_nitrogen"] == 0

    def test_a_curated_identity_is_exempt(self):
        # The formula came from a library that named the compound, so the
        # nitrogen sits where the curation put it rather than where the sort
        # key did.
        rows = [ammoniated("a", "C6H12O6", "C6H15NO6", source="database")]
        assert gate(rows)["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_the_cap_only_goes_downwards(self):
        rows = [ammoniated("a", "C6H12O6", "C6H15NO6", tier="below_assignability")]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 1
        assert summary["capped"] == 0
        assert rows[0]["tier"] == "below_assignability"

    def test_the_nitrate_channel_is_gated_the_same_way(self):
        rows = [row("a", "C5H8O4", NITRATE, displaced=("C5H9NO7", DEPROT))]
        summary = apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert summary["capped"] == 1
        assert (
            rows[0]["provenance"]["cross_channel"]["ambiguous_nitrogen"]["alternative"]
            == "C5H9NO7"
        )

    def test_a_bromide_run_has_nothing_to_gate(self):
        # `+Br-` on M is `-H+` on M+HBr by the same arithmetic, and the finder
        # displaces the one for the other - but the gate measures that prior to
        # be borne out (89% of lone bromide readings confirmed against 19% of
        # lone nitrogen ones), so the rule is scoped to nitrogen.
        rows = [row("a", "CH2O2", BROMIDE, displaced=("CH3BrO2", DEPROT))]
        summary = apply_cross_channel(
            rows, notation_by_id={DEPROT: "-H+", BROMIDE: "+Br-"}
        )
        assert summary["reagent_rule_applied"] is False
        assert summary["capped"] == 0
        assert rows[0]["tier"] == "assigned"


class TestWhatHappensToTheSatellites:
    def test_a_capped_reading_takes_its_own_satellites_with_it(self):
        # The child carries the parent's neutral, so it carries the parent's
        # doubt; a run that demoted the M0 and left its isotopologue at assigned
        # would be reporting two confidences for one reading.
        rows = [
            ammoniated("a", "C6H12O6", "C6H15NO6"),
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
            ammoniated("a", "C6H12O6", "C6H15NO6"),
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
        assert summary["reagent_channels"] == ["+(CH4N2O)H+", "+NH4+"]

    def test_a_run_with_no_donor_channel_names_which_half_stood_down(self):
        # The corroboration half runs on every sample whatever the mode is, so
        # the flag has to say the REAGENT RULE found nothing to gate rather
        # than reading as though the pass did not run.
        rows = [row("a", "CH2O2", DEPROT), row("b", "CH2O2", BROMIDE)]
        summary = apply_cross_channel(
            rows, notation_by_id={DEPROT: "-H+", BROMIDE: "+Br-"}
        )
        assert summary["reagent_rule_applied"] is False
        assert summary["corroborated"] == 2

    def test_the_counts_are_of_committed_monoisotopic_rows(self):
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", PROTON, role="iso_child", owner="a"),
        ]
        assert gate(rows)["committed_m0"] == 1

    def test_the_record_carries_the_partners_best_tier(self):
        # What the flag is worth depends on how confident the OTHER reading is,
        # and 2.4 weighs that rather than counting rows.
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", AMMONIUM, tier="candidate"),
            row("c", "C6H12O6", UREA, tier="below_assignability"),
        ]
        gate(rows)
        assert rows[0]["provenance"]["cross_channel"]["partner_tier"] == "candidate"
        assert rows[1]["provenance"]["cross_channel"]["partner_tier"] == "assigned"

    def test_a_lone_reading_has_no_partner_tier(self):
        rows = [row("a", "C6H12O6", PROTON)]
        gate(rows)
        assert rows[0]["provenance"]["cross_channel"]["partner_tier"] is None


class TestFixingTheCount:
    DONORS = frozenset({"+NH4+", "+(CH4N2O)H+"})

    def test_a_non_donor_channel_fixes_it(self):
        assert fixes_nitrogen(frozenset({"+NH4+", "+H+"}), self.DONORS)

    def test_two_donors_fix_it(self):
        assert fixes_nitrogen(frozenset({"+NH4+", "+(CH4N2O)H+"}), self.DONORS)

    def test_one_donor_alone_does_not(self):
        assert not fixes_nitrogen(frozenset({"+NH4+"}), self.DONORS)


class TestTheNeutralsIdentity:
    def test_two_spellings_of_one_neutral_are_one_neutral(self):
        # A curated row spells its formula explicit where an untargeted row does
        # not, and the same compound seen through Stage A and Stage B has to
        # group or the corroboration is lost exactly where it is best evidenced.
        assert neutral_key("C1H4N2O1") == neutral_key("CH4N2O")

    def test_different_neutrals_stay_different(self):
        assert neutral_key("C6H12O6") != neutral_key("C6H12O5")

    def test_the_two_spellings_corroborate_each_other(self):
        rows = [
            row("a", "C1H4N2O1", PROTON, source="database"),
            row("b", "CH4N2O", AMMONIUM),
        ]
        assert gate(rows)["corroborated"] == 2


def test_channels_by_neutral_reads_only_committed_monoisotopic_rows():
    rows = [
        row("a", "C6H12O6", PROTON),
        row("b", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
        {**row("c", "C6H12O6", UREA), "assigned_formula": None},
    ]
    assert channels_by_neutral(rows, dict(POSITIVE)) == {
        neutral_key("C6H12O6"): frozenset({"+H+"})
    }
