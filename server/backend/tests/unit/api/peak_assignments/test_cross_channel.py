"""What a sample's channels corroborate, and the readings of one ion that
nothing measured tells apart."""

from __future__ import annotations

from mascope_backend.api.new.peak_assignments.cross_channel import (
    CHANNELS_FOR_CORROBORATION,
    REASON_AMBIGUOUS_ADDUCT,
    REASON_AMBIGUOUS_NITROGEN,
    SAME_ION_SETTLED,
    SETTLED_BY_PARTNER,
    SETTLED_BY_RADICAL,
    SETTLED_BY_SECOND_CHANNEL,
    SETTLED_BY_TARGET_LIBRARY,
    apply_cross_channel,
    channels_by_neutral,
    donates_nitrogen,
    neutral_key,
    nitrogen_donating_channels,
    partner_tier,
    same_ion_readings,
)
from mascope_backend.api.new.peak_assignments.cross_channel import (
    same_ion_question as _same_ion_question,
)


PROTON = "prot"
AMMONIUM = "amm"
UREA = "urea"
NITRATE = "nit"
DEPROT = "deprot"
BROMIDE = "brom"
CARBONATE = "carb"

POSITIVE = {
    PROTON: "[M+H]+",
    AMMONIUM: "[M+NH4]+",
    UREA: "[M+CH4N2O+H]+",
}
NEGATIVE = {
    DEPROT: "[M-H]-",
    NITRATE: "[M+NO3]-",
    BROMIDE: "[M+Br]-",
    CARBONATE: "[M+CO3]-",
}
SODIUM = "sod"


def test_a_reading_the_partner_gate_displaced_is_no_rival():
    """The sample was asked to bear the formate reading out and did not, so it
    is no doubt about the acid reading it did bear out."""
    notation_by_id = {"im-deprot": "[M-H]-", "im-formate": "[M+HCOO]-"}
    committed = {
        "assigned_formula": "C11H20O7",
        "ionization_mechanism_id": "im-deprot",
        "alternatives": [
            {
                "assigned_formula": "C10H18O5",
                "ionization_mechanism_id": "im-formate",
                "same_ion": True,
                "partner_gate": "unmet",
            }
        ],
    }
    assert _same_ion_question(committed, notation_by_id, corroborated=False) is None
    committed["alternatives"][0].pop("partner_gate")
    reason, found = _same_ion_question(committed, notation_by_id, corroborated=False)
    assert reason == "ambiguous_adduct" and found["alternative"] == "C10H18O5"


def row(
    row_id: str,
    formula: str,
    mechanism: str,
    *,
    tier: str = "assigned",
    role: str = "M0",
    source: str = "untargeted",
    owner: str | None = None,
    displaced: tuple[str, str] | list[tuple[str, str]] | None = None,
    compound: str | None = None,
    intensity: float = 1.0e5,
) -> dict:
    """One committed row.

    ``displaced`` is the reading of the same ion the row's own reading set
    aside, as ``(neutral, mechanism id)``, or a list of them - what
    ``elect_same_ion_families`` stores on an election, what
    ``record_mirror_same_ion_readings`` writes on a reference mirror's row, and
    what the same-ion rule reads. A row without one is a row whose ion had only
    one reading. ``compound`` is the target library compound a Stage A row was
    committed for; a ``database`` row without one is a reference mirror's.
    """
    alternatives = None
    if displaced is not None:
        readings = [displaced] if isinstance(displaced, tuple) else displaced
        alternatives = [
            {
                "assigned_formula": neutral,
                "ionization_mechanism_id": mechanism_id,
                "same_ion": True,
            }
            for neutral, mechanism_id in readings
        ]
    return {
        "peak_assignment_id": row_id,
        "assigned_formula": formula,
        "ionization_mechanism_id": mechanism,
        "role": role,
        "tier": tier,
        "source": source,
        "owner_peak_assignment_id": owner,
        "target_compound_id": compound,
        "alternatives": alternatives,
        "sample_peak_intensity": intensity,
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
        assert donates_nitrogen("[M+NH4]+")

    def test_so_could_the_urea_adduct(self):
        assert donates_nitrogen("[M+CH4N2O+H]+")

    def test_and_a_nitrate_cluster(self):
        # The negative-mode form: the reagent's nitrogen and an analyte nitrogen
        # are the same atoms in the same ion, split differently.
        assert donates_nitrogen("[M+NO3]-")

    def test_a_bromide_cluster_carries_none(self):
        assert not donates_nitrogen("[M+Br]-")

    def test_neither_does_protonation_or_deprotonation(self):
        assert not donates_nitrogen("[M+H]+")
        assert not donates_nitrogen("[M-H]-")

    def test_a_labelled_reagent_carries_none_either(self):
        # The reason anyone runs a labelled reagent. Its 15N is 0.997 Da from an
        # analyte's own nitrogen, so the deprotonated nitrate ester is a
        # different ion at a different mass and the spectrum chooses between
        # them - there is nothing for a prior to decide.
        #
        # Both spellings, because production sends the bracketed one: mechanism
        # notations reach the finder through `to_explicit_isotope_format`, and
        # the caret form is what `parse_ionization` re-spells it to.
        assert not donates_nitrogen("[M+[15N]O3]-")
        assert not donates_nitrogen("[M+^NO3]-")

    def test_an_unreadable_mechanism_donates_nothing(self):
        assert not donates_nitrogen("not a mechanism")
        assert not donates_nitrogen(None)

    def test_the_run_finds_its_own_donors_without_being_told_them(self):
        assert nitrogen_donating_channels(list(POSITIVE.values())) == {
            "[M+NH4]+",
            "[M+CH4N2O+H]+",
        }
        assert nitrogen_donating_channels(list(NEGATIVE.values())) == {"[M+NO3]-"}
        assert (
            nitrogen_donating_channels(["[M-H]-", "[M+[15N]O3]-", "[M+CO3]-"])
            == frozenset()
        )


class TestWhatCorroboratesANeutral:
    def test_one_neutral_through_two_channels_is_corroborated(self):
        rows = [row("a", "C6H12O6", PROTON), row("b", "C6H12O6", AMMONIUM)]
        summary = gate(rows)
        assert summary["corroborated"] == 2
        assert rows[0]["provenance"]["cross_channel"]["corroborated"]
        assert rows[0]["provenance"]["cross_channel"]["channels"] == [
            "[M+H]+",
            "[M+NH4]+",
        ]

    def test_a_lone_channel_is_not(self):
        rows = [row("a", "C6H12O6", PROTON)]
        assert gate(rows)["corroborated"] == 0
        assert not rows[0]["provenance"]["cross_channel"]["corroborated"]

    def test_two_readings_through_the_same_channel_are_one_observation(self):
        # The same neutral committed on two peaks of one channel - an in-source
        # fragment, a second charge state - is one chemistry, not two.
        rows = [row("a", "C6H12O6", PROTON), row("b", "C6H12O6", PROTON)]
        assert gate(rows)["corroborated"] == 0

    def test_an_isotopologue_is_not_a_second_channel(self):
        # Its parent's ion on a second line of one envelope. Counting it would
        # let a reading corroborate itself.
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", PROTON, role="iso_child", owner="a"),
        ]
        assert gate(rows)["corroborated"] == 0

    def test_an_orphan_isotopologue_brings_no_channel_with_it(self):
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
        assert rows[0]["provenance"]["cross_channel"]["channels"] == ["[M+H]+"]

    def test_an_uncommitted_row_corroborates_nothing(self):
        rows = [
            row("a", "C6H12O6", PROTON),
            {**row("b", "C6H12O6", AMMONIUM), "assigned_formula": None},
        ]
        assert gate(rows)["corroborated"] == 0

    def test_the_bar_is_two_channels(self):
        assert CHANNELS_FOR_CORROBORATION == 2


class TestTheSameIonRule:
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
        assert record["ambiguous_nitrogen"]["via"] == "[M+H]+"

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

    def test_two_donors_leave_the_count_in_question_too(self):
        # Urea through its proton is ammonium plus isocyanic acid, so the urea
        # adduct of C6H10O5 is the ammonium adduct of C7H11NO6: one more
        # nitrogen on the analyte, which neither donor settles.
        rows = [row("a", "C6H10O5", UREA, displaced=("C7H11NO6", AMMONIUM))]
        summary = gate(rows)
        assert (summary["ambiguous_nitrogen"], summary["capped"]) == (1, 1)
        record = rows[0]["provenance"]["cross_channel"]
        assert record["reason"] == REASON_AMBIGUOUS_NITROGEN
        assert record["ambiguous_nitrogen"] == {
            "alternative": "C7H11NO6",
            "via": "[M+NH4]+",
        }

    def test_the_target_library_settles_it(self):
        # The workspace named the compound, so which reading the ion is was its
        # curation's decision rather than the sort key's.
        rows = [
            ammoniated("a", "C6H12O6", "C6H15NO6", source="database", compound="tc-1")
        ]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED] == {
            "alternative": "C6H15NO6",
            "via": "[M+H]+",
            "by": SETTLED_BY_TARGET_LIBRARY,
        }
        assert summary["settled"][SETTLED_BY_TARGET_LIBRARY] == 1

    def test_a_second_channel_settles_it_and_says_which(self):
        rows = [ammoniated("a", "C6H12O6", "C6H15NO6"), row("b", "C6H12O6", PROTON)]
        summary = gate(rows)
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED] == {
            "alternative": "C6H15NO6",
            "via": "[M+H]+",
            "by": SETTLED_BY_SECOND_CHANNEL,
            "through": ["[M+H]+"],
        }
        assert summary["settled"][SETTLED_BY_SECOND_CHANNEL] == 1

    def test_the_cap_only_goes_downwards(self):
        rows = [ammoniated("a", "C6H12O6", "C6H15NO6", tier="below_assignability")]
        summary = gate(rows)
        assert summary["ambiguous_nitrogen"] == 1
        assert summary["capped"] == 0
        assert rows[0]["tier"] == "below_assignability"
        # The rival is recorded all the same: the row says what it is in doubt
        # with, whatever its evidence already took from it.
        record = rows[0]["provenance"]["cross_channel"]
        assert record["ambiguous_nitrogen"]["alternative"] == "C6H15NO6"
        assert "capped" not in record

    def test_a_radical_reading_is_no_rival(self):
        # C5H8O5 through [M-H]- is the radical C4H7O2 through the carbonate radical
        # anion, and a radical is refused the top tier on its own rule.
        rows = [row("a", "C5H8O5", DEPROT, displaced=("C4H7O2", CARBONATE))]
        summary = apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert summary["capped"] == 0
        assert rows[0]["tier"] == "assigned"
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED] == {
            "alternative": "C4H7O2",
            "via": "[M+CO3]-",
            "by": SETTLED_BY_RADICAL,
        }
        assert summary["settled"][SETTLED_BY_RADICAL] == 1

    def test_a_molecule_beside_a_radical_is_still_a_rival(self):
        # C5H8O4 through nitrate is the radical C4H8NO4 through carbonate and
        # the molecule C5H9NO7 through [M-H]-; the molecule is the question.
        rows = [
            row(
                "a",
                "C5H8O4",
                NITRATE,
                displaced=[("C4H8NO4", CARBONATE), ("C5H9NO7", DEPROT)],
            )
        ]
        summary = apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert summary["capped"] == 1
        assert rows[0]["provenance"]["cross_channel"]["ambiguous_nitrogen"] == {
            "alternative": "C5H9NO7",
            "via": "[M-H]-",
        }

    def test_the_rows_own_reading_again_is_no_other_reading(self):
        # A channel searched twice proposes every neutral through it twice, and
        # the election keeps the twin as another reading of the ion.
        rows = [
            row(
                "a",
                "C15H32O",
                CARBONATE,
                displaced=[("C15H32O", CARBONATE), ("C16H33O4", DEPROT)],
            )
        ]
        summary = apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert summary["capped"] == 0
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED] == {
            "alternative": "C16H33O4",
            "via": "[M-H]-",
            "by": SETTLED_BY_RADICAL,
        }

    def test_the_nitrogen_doubt_is_named_whatever_order_the_family_is_in(self):
        # DMF clustered with hydronium is C3H9NO2 through a proton (the same
        # nitrogen) and C3H6O2 through ammonium (one fewer). The count is the
        # sharper doubt, and it is named even though the family lists the
        # same-nitrogen reading first.
        rows = [
            row(
                "a",
                "C3H7NO",
                "h3o",
                displaced=[("C3H9NO2", PROTON), ("C3H6O2", AMMONIUM)],
            )
        ]
        summary = apply_cross_channel(
            rows, notation_by_id={**POSITIVE, "h3o": "[M+H3O]+"}
        )
        record = rows[0]["provenance"]["cross_channel"]
        assert record["reason"] == REASON_AMBIGUOUS_NITROGEN
        assert record["ambiguous_nitrogen"] == {
            "alternative": "C3H6O2",
            "via": "[M+NH4]+",
        }
        assert (summary["ambiguous_nitrogen"], summary["ambiguous_adduct"]) == (1, 0)

    def test_a_reading_of_the_rows_own_neutral_is_the_row_again_on_any_channel(self):
        # One ion and one neutral leave one moiety between them, so the same
        # neutral through another channel's name is the row's own reading.
        rows = [row("a", "C15H32O", CARBONATE, displaced=("C15H32O", DEPROT))]
        summary = apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert summary["capped"] == 0
        assert set(rows[0]["provenance"]["cross_channel"]) == {
            "channels",
            "corroborated",
            "partner_tier",
        }

    def test_a_row_whose_channel_the_run_cannot_name_is_not_asked(self):
        rows = [row("a", "C6H12O6", "unnamed", displaced=("C6H15NO6", PROTON))]
        summary = gate(rows)
        assert summary["capped"] == 0
        assert rows[0]["tier"] == "assigned"
        assert "ambiguous_nitrogen" not in rows[0]["provenance"]["cross_channel"]

    def test_a_reading_that_puts_a_labelled_atom_on_the_analyte_is_not_weighed(self):
        # The label is the labelled reagent's: an analyte does not carry it, so
        # the deprotonated ester of the labelled acid is no rival reading.
        rows = [
            row("a", "C5H8O4", "labelled", displaced=("C5H9[15N]O7", DEPROT)),
        ]
        summary = apply_cross_channel(
            rows, notation_by_id={DEPROT: "[M-H]-", "labelled": "[M+[15N]O3]-"}
        )
        assert summary["capped"] == 0
        assert set(rows[0]["provenance"]["cross_channel"]) == {
            "channels",
            "corroborated",
            "partner_tier",
        }

    def test_a_twin_alone_raises_no_question(self):
        rows = [row("a", "C15H32O", CARBONATE, displaced=("C15H32O", CARBONATE))]
        apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert set(rows[0]["provenance"]["cross_channel"]) == {
            "channels",
            "corroborated",
            "partner_tier",
        }

    def test_a_reading_nothing_can_read_is_not_weighed(self):
        # A rule that caps fails open: an unreadable formula is not a rival.
        rows = [row("a", "C6H12O6", AMMONIUM, displaced=("not a formula", PROTON))]
        summary = gate(rows)
        assert summary["capped"] == 0
        assert set(rows[0]["provenance"]["cross_channel"]) == {
            "channels",
            "corroborated",
            "partner_tier",
        }

    def test_the_nitrate_channel_is_gated_the_same_way(self):
        rows = [row("a", "C5H8O4", NITRATE, displaced=("C5H9NO7", DEPROT))]
        summary = apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert summary["capped"] == 1
        assert (
            rows[0]["provenance"]["cross_channel"]["ambiguous_nitrogen"]["alternative"]
            == "C5H9NO7"
        )

    def test_a_bromide_cluster_is_asked_the_same(self):
        # `[M+Br]-` on M is `[M-H]-` on M+HBr by the same arithmetic. The reference
        # engine prefers the cluster by a policy of its own, so its agreeing is
        # no measurement of which reading the spectrum holds.
        rows = [row("a", "CH2O2", BROMIDE, displaced=("CH3BrO2", DEPROT))]
        summary = apply_cross_channel(
            rows, notation_by_id={DEPROT: "[M-H]-", BROMIDE: "[M+Br]-"}
        )
        assert summary["reagent_rule_applied"] is False
        assert (summary["ambiguous_adduct"], summary["capped"]) == (1, 1)
        assert rows[0]["tier"] == "candidate"
        record = rows[0]["provenance"]["cross_channel"]
        assert record["reason"] == REASON_AMBIGUOUS_ADDUCT
        assert record["ambiguous_adduct"] == {"alternative": "CH3BrO2", "via": "[M-H]-"}

    def test_its_isotopologues_carry_the_reason_they_were_capped_for(self):
        rows = [
            row("a", "CH2O2", BROMIDE, displaced=("CH3BrO2", DEPROT)),
            row("b", "CH2O2", BROMIDE, role="iso_child", owner="a"),
        ]
        summary = apply_cross_channel(
            rows, notation_by_id={DEPROT: "[M-H]-", BROMIDE: "[M+Br]-"}
        )
        assert summary["capped_isotopologues"] == 1
        assert rows[1]["tier"] == "candidate"
        assert rows[1]["provenance"]["cross_channel"]["reason"] == (
            REASON_AMBIGUOUS_ADDUCT
        )


#: A charge-transfer source: electron transfer, proton transfer and hydride
#: abstraction.
CT = "ct"
PROTON_TRANSFER = "pt"
HYDRIDE = "hyd"
CHARGE_TRANSFER = {CT: "+", PROTON_TRANSFER: "[M+H]+", HYDRIDE: "[M-H]+"}


def benzyl(formula: str, mechanism: str, other, **kwargs) -> dict:
    """The benzyl cation at 91.054: protonated C7H6 or toluene less a hydride."""
    return row("benzyl", formula, mechanism, displaced=other, **kwargs)


def through_electron_transfer(row_id: str, formula: str, **kwargs) -> dict:
    """A molecule the sample commits through the charge-transfer source's own
    channel."""
    return row(row_id, formula, CT, **kwargs)


class TestARivalTheSampleShows:
    """A second channel settles which reading an ion is only where the sample
    does not show the other reading's molecule as well; where it does, the
    two are weighed as the partner gate weighs two opportunistic readings."""

    def gate(self, rows: list[dict], opportunistic=("[M-H]+",)) -> dict:
        return apply_cross_channel(
            rows,
            notation_by_id=dict(CHARGE_TRANSFER),
            minor_channels=frozenset(opportunistic),
        )

    def test_a_second_channel_does_not_settle_a_rival_the_sample_shows(self):
        # A proton-transfer source declares protonation, so C7H6 through [M+H]+
        # is the mode's own reading, and C7H6 is also seen through electron
        # transfer. So is toluene, thirty times as brightly: the hydride reading
        # has a second observation of its own.
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE)),
            through_electron_transfer("c7h6", "C7H6", intensity=1.0e5),
            through_electron_transfer("toluene", "C7H8", intensity=3.0e6),
        ]
        summary = self.gate(rows)
        assert rows[0]["tier"] == "candidate"
        assert rows[0]["assigned_formula"] == "C7H6"
        record = rows[0]["provenance"]["cross_channel"]
        assert record["corroborated"] is True
        assert record["reason"] == REASON_AMBIGUOUS_ADDUCT
        assert record[REASON_AMBIGUOUS_ADDUCT] == {
            "alternative": "C7H8",
            "via": "[M-H]+",
            "shown": True,
            "ratio": 0.03,
        }
        assert (summary["shown_rival"], summary["capped"]) == (1, 1)
        assert summary["partner_margin"] == 10.0

    def test_a_rival_the_sample_shows_far_more_faintly_is_settled(self):
        # The same row, with C7H6 seen a thousand times as brightly as
        # toluene: the stronger partner settles it, as it would a contest.
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE)),
            through_electron_transfer("c7h6", "C7H6", intensity=1.0e6),
            through_electron_transfer("toluene", "C7H8", intensity=1.0e3),
        ]
        summary = self.gate(rows)
        assert rows[0]["tier"] == "assigned"
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED] == {
            "alternative": "C7H8",
            "via": "[M-H]+",
            "by": SETTLED_BY_PARTNER,
            "ratio": 1000.0,
        }
        assert summary["settled"][SETTLED_BY_PARTNER] == 1
        assert summary["shown_rival"] == 0

    def test_the_rows_own_peak_is_no_partner_of_its_own(self):
        # The row's own peak is the one ion both readings explain, whatever its
        # height. C7H6 is seen again only through an opportunistic channel,
        # which shows nothing, so the row has no partner to weigh against the
        # faint toluene the sample does commit through electron transfer.
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE), intensity=5.0e6),
            row("c7h6", "C7H6", HYDRIDE, intensity=1.0e6),
            through_electron_transfer("toluene", "C7H8", intensity=1.0e3),
        ]
        self.gate(rows)
        assert rows[0]["tier"] == "candidate"
        assert rows[0]["provenance"]["cross_channel"][REASON_AMBIGUOUS_ADDUCT] == {
            "alternative": "C7H8",
            "via": "[M-H]+",
            "shown": True,
            "ratio": None,
        }

    def test_where_the_sample_does_not_show_it_the_second_channel_settles(self):
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE)),
            through_electron_transfer("c7h6", "C7H6"),
        ]
        summary = self.gate(rows)
        assert rows[0]["tier"] == "assigned"
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED]["by"] == (
            SETTLED_BY_SECOND_CHANNEL
        )
        assert summary["shown_rival"] == 0

    def test_a_molecule_seen_only_through_an_opportunistic_channel_is_not_shown(self):
        # Toluene less a hydride on another peak is the channel's own reading
        # again, not the sample showing toluene.
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE)),
            through_electron_transfer("c7h6", "C7H6"),
            row("toluene", "C7H8", HYDRIDE),
        ]
        self.gate(rows)
        assert rows[0]["tier"] == "assigned"

    def test_a_molecule_seen_only_below_assignability_is_not_shown(self):
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE)),
            through_electron_transfer("c7h6", "C7H6"),
            through_electron_transfer("toluene", "C7H8", tier="below_assignability"),
        ]
        self.gate(rows)
        assert rows[0]["tier"] == "assigned"

    def test_every_channel_is_the_modes_own_unless_the_run_says_otherwise(self):
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE)),
            through_electron_transfer("c7h6", "C7H6"),
            row("toluene", "C7H8", HYDRIDE),
        ]
        apply_cross_channel(rows, notation_by_id=dict(CHARGE_TRANSFER))
        assert rows[0]["tier"] == "candidate"

    def test_a_typed_formula_is_the_molecule_it_spells(self):
        # A target library holds what a person typed: its C6H4(CH3)2 is the
        # xylene a search writes C8H10, and it shows that molecule as well.
        rows = [
            row(
                "methylbenzyl",
                "C8H8",
                PROTON_TRANSFER,
                displaced=("C8H10", HYDRIDE),
            ),
            through_electron_transfer("styrene", "C8H8", intensity=1.0e5),
            through_electron_transfer(
                "xylene",
                "C6H4(CH3)2",
                intensity=7.0e5,
                source="database",
                compound="tc-xylene",
            ),
        ]
        self.gate(rows)
        assert neutral_key("C6H4(CH3)2") == neutral_key("C8H10")
        assert rows[0]["tier"] == "candidate"
        assert rows[0]["provenance"]["cross_channel"][REASON_AMBIGUOUS_ADDUCT] == {
            "alternative": "C8H10",
            "via": "[M-H]+",
            "shown": True,
            "ratio": 0.14,
        }

    #: Dimethylformamide clustered with hydronium: C3H9NO2 through a proton
    #: (the same nitrogen) and C3H6O2 through ammonium (one fewer).
    DMF_HYDRONIUM = [("C3H9NO2", PROTON), ("C3H6O2", AMMONIUM)]

    def test_a_shown_rival_is_named_before_a_sharper_one_the_sample_does_not_show(
        self,
    ):
        # The nitrogen doubt is the sharper one, but the second channel
        # settles it; the rival the sample shows is the doubt that stands.
        rows = [
            row("a", "C3H7NO", "h3o", displaced=self.DMF_HYDRONIUM),
            row("b", "C3H7NO", PROTON, intensity=1.0e5),
            row("c", "C3H9NO2", AMMONIUM, intensity=4.0e5),
        ]
        apply_cross_channel(rows, notation_by_id={**POSITIVE, "h3o": "[M+H3O]+"})
        record = rows[0]["provenance"]["cross_channel"]
        assert record["reason"] == REASON_AMBIGUOUS_ADDUCT
        assert record[REASON_AMBIGUOUS_ADDUCT] == {
            "alternative": "C3H9NO2",
            "via": "[M+H]+",
            "shown": True,
            "ratio": 0.25,
        }

    def test_a_row_nothing_corroborates_names_the_sharper_doubt_as_before(self):
        # And says nothing of what the sample shows: no second channel was
        # there to settle anything.
        rows = [
            row("a", "C3H7NO", "h3o", displaced=self.DMF_HYDRONIUM),
            row("c", "C3H9NO2", AMMONIUM),
        ]
        apply_cross_channel(rows, notation_by_id={**POSITIVE, "h3o": "[M+H3O]+"})
        assert rows[0]["provenance"]["cross_channel"][REASON_AMBIGUOUS_NITROGEN] == {
            "alternative": "C3H6O2",
            "via": "[M+NH4]+",
        }

    def test_the_target_library_still_settles_a_shown_rival(self):
        # Corroborated through electron transfer, so the rule that a second
        # channel does not settle a shown rival is reached, and the library's
        # curation settles it first.
        rows = [
            benzyl(
                "C7H6",
                PROTON_TRANSFER,
                ("C7H8", HYDRIDE),
                source="database",
                compound="tc-1",
            ),
            through_electron_transfer("c7h6", "C7H6", intensity=1.0e5),
            through_electron_transfer("toluene", "C7H8", intensity=3.0e6),
        ]
        self.gate(rows)
        assert rows[0]["provenance"]["cross_channel"]["corroborated"] is True
        assert rows[0]["tier"] == "assigned"
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED]["by"] == (
            SETTLED_BY_TARGET_LIBRARY
        )

    def test_the_isotopologues_of_a_row_a_shown_rival_holds_say_so(self):
        rows = [
            benzyl("C7H6", PROTON_TRANSFER, ("C7H8", HYDRIDE)),
            row(
                "benzyl-13c", "C7H6", PROTON_TRANSFER, role="iso_child", owner="benzyl"
            ),
            through_electron_transfer("c7h6", "C7H6", intensity=1.0e5),
            through_electron_transfer("toluene", "C7H8", intensity=3.0e6),
        ]
        summary = self.gate(rows)
        assert rows[1]["tier"] == "candidate"
        assert rows[1]["provenance"]["cross_channel"] == {
            "inherited_from": "benzyl",
            "shown": True,
            "capped": "candidate",
            "reason": REASON_AMBIGUOUS_ADDUCT,
        }
        assert summary["capped_isotopologues"] == 1


class TestTheStrongerPartner:
    """The contest the partner gate held, as the cross-channel pass reads it:
    weighed again on the same partners, so the mark the gate left and the
    reason the row gives cannot disagree."""

    OPENED = frozenset({"[M+H]+", "[M-H]+"})

    def gate(self, rows: list[dict]) -> dict:
        return apply_cross_channel(
            rows, notation_by_id=dict(CHARGE_TRANSFER), minor_channels=self.OPENED
        )

    def test_the_brighter_partner_settles_the_ion(self):
        # The gate gave the benzyl cation toluene's reading. Electron transfer's
        # reading of the ion is a radical beside it, and no rival either.
        rows = [
            benzyl("C7H8", HYDRIDE, [("C7H6", PROTON_TRANSFER), ("C7H7", CT)]),
            through_electron_transfer("c7h6", "C7H6", intensity=1.0e5),
            through_electron_transfer("toluene", "C7H8", intensity=3.0e6),
        ]
        summary = self.gate(rows)
        assert rows[0]["tier"] == "assigned"
        assert rows[0]["provenance"]["cross_channel"][SAME_ION_SETTLED] == {
            "alternative": "C7H6",
            "via": "[M+H]+",
            "by": SETTLED_BY_PARTNER,
            "ratio": 30.0,
        }
        assert summary["settled"][SETTLED_BY_PARTNER] == 1
        assert summary["capped"] == 0

    def test_within_the_margin_the_rival_the_sample_shows_is_a_doubt(self):
        rows = [
            benzyl("C5H8", HYDRIDE, [("C5H6", PROTON_TRANSFER), ("C5H7", CT)]),
            through_electron_transfer("c5h6", "C5H6", intensity=1.0e5),
            through_electron_transfer("isoprene", "C5H8", intensity=7.0e5),
        ]
        summary = self.gate(rows)
        assert rows[0]["tier"] == "candidate"
        assert rows[0]["assigned_formula"] == "C5H8"
        assert rows[0]["provenance"]["cross_channel"][REASON_AMBIGUOUS_ADDUCT] == {
            "alternative": "C5H6",
            "via": "[M+H]+",
            "shown": True,
            "ratio": 7.0,
        }
        assert (summary["shown_rival"], summary["capped"]) == (1, 1)

    def test_an_outweighed_reading_is_one_the_sample_bore_out(self):
        # Unlike a reading the gate set aside as unmet, it is still a reading
        # of the ion, and the row says what settled it.
        committed = benzyl("C7H8", HYDRIDE, [("C7H6", PROTON_TRANSFER), ("C7H7", CT)])
        committed["alternatives"][0]["partner_gate"] = "outweighed"
        readings = same_ion_readings(committed, dict(CHARGE_TRANSFER))
        assert [r["assigned_formula"] for r in readings] == ["C7H6", "C7H7"]
        committed["alternatives"][0]["partner_gate"] = "unmet"
        readings = same_ion_readings(committed, dict(CHARGE_TRANSFER))
        assert [r["assigned_formula"] for r in readings] == ["C7H7"]


class TestWhatHappensToTheIsotopologues:
    def test_a_capped_reading_takes_its_own_isotopologues_with_it(self):
        # The child carries the parent's neutral, so it carries the parent's
        # doubt; a run that demoted the M0 and left its isotopologue at assigned
        # would be reporting two confidences for one reading.
        rows = [
            ammoniated("a", "C6H12O6", "C6H15NO6"),
            row("b", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
        ]
        summary = gate(rows)
        assert summary["capped"] == 1
        assert summary["capped_isotopologues"] == 1
        assert rows[1]["tier"] == "candidate"
        assert rows[1]["provenance"]["cross_channel"]["inherited_from"] == "a"

    def test_they_are_counted_apart_from_the_analytes(self):
        # A rule's reach over analytes and its reach over their isotopologues are
        # different numbers, and reporting the sum as one hides which it moved.
        rows = [
            ammoniated("a", "C6H12O6", "C6H15NO6"),
            row("b", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
            row("c", "C6H12O6", AMMONIUM, role="iso_child", owner="a"),
        ]
        summary = gate(rows)
        assert (summary["capped"], summary["capped_isotopologues"]) == (1, 2)

    def test_an_isotopologue_of_an_untouched_parent_is_untouched(self):
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", PROTON, role="iso_child", owner="a"),
        ]
        gate(rows)
        assert rows[1]["tier"] == "assigned"


class TestTheRunsRecord:
    def test_the_run_records_what_it_searched_and_what_could_hide_nitrogen(self):
        summary = gate([row("a", "C6H12O6", PROTON)])
        assert summary["channels"] == ["[M+CH4N2O+H]+", "[M+H]+", "[M+NH4]+"]
        assert summary["reagent_channels"] == ["[M+CH4N2O+H]+", "[M+NH4]+"]

    def test_a_run_with_no_donor_channel_says_so(self):
        # The flag says whether a channel of this mode donates nitrogen - what
        # the nitrogen reason can be about. Corroboration and the same-ion rule
        # run on every sample whatever it says.
        rows = [row("a", "CH2O2", DEPROT), row("b", "CH2O2", BROMIDE)]
        summary = apply_cross_channel(
            rows, notation_by_id={DEPROT: "[M-H]-", BROMIDE: "[M+Br]-"}
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

    def test_a_second_row_on_the_same_channel_is_not_a_partner(self):
        # The lookup excludes the row's whole channel, not just the row: two
        # peaks read the same way through one chemistry are one observation.
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", PROTON, tier="below_assignability"),
        ]
        gate(rows)
        assert rows[0]["provenance"]["cross_channel"]["partner_tier"] is None

    def test_the_index_keeps_the_best_tier_a_channel_reached(self):
        # Two peaks on one partner channel: the flag is worth what the BEST of
        # them is worth, and the index has to carry that rather than whichever
        # row the ledger happened to end on.
        rows = [
            row("a", "C6H12O6", PROTON),
            row("b", "C6H12O6", AMMONIUM, tier="below_assignability"),
            row("c", "C6H12O6", AMMONIUM, tier="candidate"),
        ]
        gate(rows)
        assert rows[0]["provenance"]["cross_channel"]["partner_tier"] == "candidate"

    def test_the_index_is_built_once_not_scanned_per_row(self):
        # A regression guard on the shape rather than the timing: the map has
        # to answer the partner question on its own, because rescanning the
        # ledger per row made the pass quadratic and doubled the run time on
        # the dense sets.
        rows = [
            row("a", "C6H12O6", PROTON, tier="candidate"),
            row("b", "C6H12O6", AMMONIUM),
        ]
        index = channels_by_neutral(rows, dict(POSITIVE))
        assert index == {
            neutral_key("C6H12O6"): {"[M+H]+": "candidate", "[M+NH4]+": "assigned"}
        }
        assert partner_tier(index[neutral_key("C6H12O6")], "[M+H]+") == "assigned"
        assert partner_tier(index[neutral_key("C6H12O6")], "[M+NH4]+") == "candidate"


def mirror(row_id: str, formula: str, mechanism: str, **kwargs) -> dict:
    """A reference mirror's row: a Stage A commit for no target compound."""
    return row(row_id, formula, mechanism, source="database", **kwargs)


class TestAReferenceMirrorsRow:
    """A list's formula, matched rather than elected, asked from both sides."""

    def test_read_through_a_plain_channel_its_nitrogen_is_in_question(self):
        # Dimethylformamide through [M+H]+ is acrolein through [M+NH4]+. The list put
        # the nitrogen on the analyte where the election would have given it to
        # the ammonium, and nothing measured says which.
        rows = [mirror("a", "C3H7N1O1", PROTON, displaced=("C3H4O", AMMONIUM))]
        summary = gate(rows)
        assert (summary["ambiguous_nitrogen"], summary["capped"]) == (1, 1)
        assert rows[0]["tier"] == "candidate"
        assert rows[0]["assigned_formula"] == "C3H7N1O1"
        record = rows[0]["provenance"]["cross_channel"]
        assert record["reason"] == REASON_AMBIGUOUS_NITROGEN
        assert record["ambiguous_nitrogen"] == {
            "alternative": "C3H4O",
            "via": "[M+NH4]+",
        }

    def test_read_through_a_donor_it_is_asked_what_an_election_is(self):
        rows = [mirror("a", "C10H14O7", UREA, displaced=("C11H18N2O8", PROTON))]
        assert gate(rows)["capped"] == 1
        assert rows[0]["provenance"]["cross_channel"]["ambiguous_nitrogen"] == {
            "alternative": "C11H18N2O8",
            "via": "[M+H]+",
        }

    def test_the_run_counts_its_reach_apart_from_the_elections(self):
        rows = [
            mirror("a", "C3H7NO", PROTON, displaced=("C3H4O", AMMONIUM)),
            ammoniated("b", "C6H12O6", "C6H15NO6"),
            mirror(
                "c", "C8H19N", PROTON, tier="candidate", displaced=("C8H16", AMMONIUM)
            ),
        ]
        summary = gate(rows)
        assert (summary["ambiguous_nitrogen"], summary["capped"]) == (3, 2)
        assert (summary["ambiguous_nitrogen_mirror"], summary["capped_mirror"]) == (
            2,
            1,
        )

    def test_the_run_counts_a_lists_other_doubts_apart_too(self):
        rows = [
            mirror("a", "CH2O2", BROMIDE, displaced=("CH3BrO2", DEPROT)),
            row("b", "C2H4O2", BROMIDE, displaced=("C2H5BrO2", DEPROT)),
        ]
        summary = apply_cross_channel(rows, notation_by_id=dict(NEGATIVE))
        assert (summary["ambiguous_adduct"], summary["ambiguous_adduct_mirror"]) == (
            2,
            1,
        )
        assert (summary["capped"], summary["capped_mirror"]) == (2, 1)

    def test_a_donor_channel_on_the_same_neutral_fixes_the_count(self):
        # That ion would need an alternative analyte of its own, one urea
        # heavier where the first is one ammonia lighter: one neutral explains
        # both.
        rows = [
            mirror("a", "C3H7NO", PROTON, displaced=("C3H4O", AMMONIUM)),
            mirror("b", "C3H7NO", UREA),
        ]
        assert gate(rows)["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_so_does_a_second_plain_channel(self):
        rows = [
            mirror("a", "C3H7NO", PROTON, displaced=("C3H4O", AMMONIUM)),
            mirror("b", "C3H7NO", SODIUM),
        ]
        summary = apply_cross_channel(
            rows, notation_by_id={**POSITIVE, SODIUM: "[M+Na]+"}
        )
        assert summary["ambiguous_nitrogen"] == 0

    def test_its_own_channel_is_not_a_second_one(self):
        rows = [
            mirror("a", "C3H7NO", PROTON, displaced=("C3H4O", AMMONIUM)),
            mirror("b", "C3H7NO", PROTON),
        ]
        assert gate(rows)["ambiguous_nitrogen"] == 1

    def test_an_election_through_a_plain_channel_is_asked_too(self):
        # Whichever reading the ion was committed as, the other is a molecule
        # nothing measured tells apart from it.
        rows = [row("a", "C3H7NO", PROTON, displaced=("C3H4O", AMMONIUM))]
        assert gate(rows)["ambiguous_nitrogen"] == 1
        assert rows[0]["tier"] == "candidate"

    def test_a_target_library_row_is_exempt_from_either_side(self):
        rows = [
            mirror(
                "a", "C3H7NO", PROTON, compound="tc-1", displaced=("C3H4O", AMMONIUM)
            )
        ]
        assert gate(rows)["ambiguous_nitrogen"] == 0
        assert rows[0]["tier"] == "assigned"

    def test_its_isotopologues_follow_it_down(self):
        rows = [
            mirror("a", "C3H7NO", PROTON, displaced=("C3H4O", AMMONIUM)),
            mirror("b", "C3H7NO", PROTON, role="iso_child", owner="a"),
        ]
        summary = gate(rows)
        assert summary["capped_isotopologues"] == 1
        assert rows[1]["tier"] == "candidate"


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
        neutral_key("C6H12O6"): {"[M+H]+": "assigned"}
    }
