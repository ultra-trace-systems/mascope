"""Why a committed row holds the tier it holds, and what takes it away."""

from __future__ import annotations

import pytest

from mascope_backend.api.new.peak_assignments.tiering import (
    DENSITY_LIMIT,
    ENVELOPE_HEIGHT_TOLERANCE,
    REASON_AMBIGUOUS_NITROGEN,
    REASON_CANDIDATE_DENSITY,
    REASON_CORROBORATED,
    REASON_ENVELOPE_NEIGHBOUR,
    REASON_INHERITED,
    REASON_MINOR_CHANNEL,
    REASON_NO_CLOSE_RIVAL,
    REASON_NOT_MEASURED,
    REASON_ODD_ELECTRON,
    REASON_OFF_CALIBRATION,
    TIERING_RULES_VERSION,
    apply_tiering,
    envelope_neighbours,
)


#: A closed-shell neutral nothing in this module has an opinion about.
PLAIN = "C6H12O6"

#: The same mass as its ion, read as a radical: C6H11O6 under a proton is an
#: odd-electron neutral, which the nitrogen rule catches.
RADICAL = "C6H11O6"


def row(
    row_id: str,
    formula: str = PLAIN,
    *,
    tier: str = "assigned",
    role: str = "M0",
    source: str = "untargeted",
    owner: str | None = None,
    density: int | None = 1,
    channels: list[str] | None = None,
    mz: float = 181.0707,
    peak: str | None = None,
    intensity: float = 1000.0,
    ion: str | None = "C6H13O6+",
    provenance: dict | None = None,
) -> dict:
    """One committed row, with the provenance the pass reads."""
    blob: dict = dict(provenance or {})
    if density is not None:
        blob["candidate_density"] = density
    blob.setdefault("cross_channel", {"channels": channels or ["+H+"]})
    return {
        "peak_assignment_id": row_id,
        "assigned_formula": formula,
        "role": role,
        "tier": tier,
        "source": source,
        "owner_peak_assignment_id": owner,
        "sample_peak_id": peak or f"peak-{row_id}",
        "sample_peak_mz": mz,
        "sample_peak_intensity": intensity,
        "ion_formula": ion,
        "provenance": blob,
    }


def tier_of(rows: list[dict], row_id: str) -> str:
    return next(r["tier"] for r in rows if r["peak_assignment_id"] == row_id)


def rules_on(rows: list[dict], row_id: str) -> set[str]:
    entry = next(r for r in rows if r["peak_assignment_id"] == row_id)
    return {reason["rule"] for reason in entry["provenance"]["tier_reasons"]}


def run(rows: list[dict], **kwargs) -> dict:
    return apply_tiering(
        rows,
        mz_tolerance_ppm=kwargs.pop("mz_tolerance_ppm", 5.0),
        abundance_floor=kwargs.pop("abundance_floor", 0.01),
    )


class TestEveryCommittedRowSaysWhy:
    def test_a_row_that_keeps_its_tier_says_what_it_kept_it_on(self):
        rows = [row("pa-1", channels=["+H+", "+NH4+"])]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"
        assert rules_on(rows, "pa-1")

    def test_a_row_nothing_was_measured_for_still_carries_a_reason(self):
        # An imported ledger, or one written before this pass existed. The
        # reason is honest about being an absence rather than a finding.
        rows = [row("pa-1", density=None, provenance={"cross_channel": None})]
        rows[0]["provenance"]["cross_channel"] = None
        run(rows)
        assert rules_on(rows, "pa-1") == {REASON_NOT_MEASURED}
        assert tier_of(rows, "pa-1") == "assigned"

    def test_an_isotopologue_with_no_owner_still_carries_a_reason(self):
        # Curated isotopologue rows can be committed with no owner recorded.
        # There is no owner's answer to carry, so the row says so - and keeps
        # the tier its evidence earned, since an absence takes nothing.
        rows = [row("pa-kid", RADICAL, role="iso_child", source="curated")]
        summary = run(rows)
        assert rules_on(rows, "pa-kid") == {REASON_NOT_MEASURED}
        assert tier_of(rows, "pa-kid") == "assigned"
        assert summary["capped_isotopologues"] == 0
        assert summary["capped_isotopologues_after_earlier_pass"] == 0

    def test_an_uncommitted_row_is_left_alone(self):
        rows = [
            {"peak_assignment_id": "pa-1", "role": "unassigned", "tier": "unassigned"}
        ]
        summary = run(rows)
        assert summary["committed_m0"] == 0
        assert "provenance" not in rows[0]


class TestTheRadicalRule:
    def test_an_odd_electron_neutral_loses_the_top_tier(self):
        rows = [row("pa-1", RADICAL)]
        run(rows)
        assert tier_of(rows, "pa-1") == "candidate"
        assert REASON_ODD_ELECTRON in rules_on(rows, "pa-1")

    def test_no_amount_of_corroboration_rescues_one(self):
        # The gate finds none that does: 0 of 1,794 such rows are confirmed,
        # whether or not a second channel saw the neutral.
        rows = [row("pa-1", RADICAL, channels=["+H+", "+NH4+", "-H+"])]
        run(rows)
        assert tier_of(rows, "pa-1") == "candidate"

    def test_a_curated_row_is_exempt(self):
        # The rule doubts an ELECTION - the finder's prior choosing a radical
        # reading of an ion over a closed-shell one. A curated row was matched
        # to an identity somebody authored, and real radical anions are exactly
        # what such a library holds.
        rows = [row("pa-1", RADICAL, source="database")]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"
        assert REASON_ODD_ELECTRON not in rules_on(rows, "pa-1")

    def test_a_closed_shell_neutral_is_untouched(self):
        rows = [row("pa-1", PLAIN)]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_an_unreadable_formula_is_never_demoted_on_a_test_that_could_not_run(self):
        rows = [row("pa-1", "O3^N", ion=None)]
        run(rows)
        assert REASON_ODD_ELECTRON not in rules_on(rows, "pa-1")


class TestTheDensityRule:
    def test_a_contested_lone_reading_loses_the_top_tier(self):
        rows = [row("pa-1", density=DENSITY_LIMIT)]
        run(rows)
        assert tier_of(rows, "pa-1") == "candidate"
        assert REASON_CANDIDATE_DENSITY in rules_on(rows, "pa-1")

    def test_a_second_channel_keeps_it(self):
        # Density says the peak alone cannot decide. A second channel is
        # evidence from outside the peak, which is exactly what settles it.
        rows = [row("pa-1", density=DENSITY_LIMIT, channels=["+H+", "+NH4+"])]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_an_uncontested_reading_keeps_it(self):
        rows = [row("pa-1", density=DENSITY_LIMIT - 1)]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_a_row_with_no_density_recorded_is_not_demoted(self):
        rows = [row("pa-1", density=None)]
        run(rows)
        assert REASON_CANDIDATE_DENSITY not in rules_on(rows, "pa-1")

    def test_the_count_is_in_the_reason(self):
        rows = [row("pa-1", density=4)]
        run(rows)
        entry = rows[0]["provenance"]["tier_reasons"]
        detail = next(
            r["detail"] for r in entry if r["rule"] == REASON_CANDIDATE_DENSITY
        )
        assert "4" in detail


class TestTheImplausibleFormula:
    def test_an_oxygen_lattice_loses_the_top_tier(self):
        rows = [row("pa-1", "C8H10O11", ion="C8H11O11+")]
        run(rows)
        assert tier_of(rows, "pa-1") == "candidate"
        assert "oxygen_lattice" in rules_on(rows, "pa-1")

    def test_a_small_organic_acid_does_not(self):
        rows = [row("pa-1", "C3H4O4", ion="C3H5O4+")]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_a_carbon_free_formula_off_the_list_loses_it(self):
        rows = [row("pa-1", "HS3", ion="S3-")]
        run(rows)
        assert tier_of(rows, "pa-1") == "candidate"


class TestTheNeighboursEnvelope:
    @staticmethod
    def pair(child_intensity: float) -> list[dict]:
        """A committed row on the 13C line of another committed row.

        C6H13O6+ predicts its 13C line 6.6% up at 182.0740; the second row sits
        there with a formula of its own.
        """
        return [
            row("pa-owner", PLAIN, mz=181.0707, intensity=1000.0, ion="C6H13O6+"),
            # A closed-shell neutral, so nothing but this rule has an opinion
            # about the second row.
            row(
                "pa-child",
                PLAIN,
                mz=182.07404,
                intensity=child_intensity,
                ion="C6H13O6+",
            ),
        ]

    def test_a_peak_a_neighbour_predicts_loses_the_top_tier(self):
        rows = self.pair(child_intensity=40.0)
        run(rows)
        assert tier_of(rows, "pa-child") == "candidate"
        assert REASON_ENVELOPE_NEIGHBOUR in rules_on(rows, "pa-child")
        assert tier_of(rows, "pa-owner") == "assigned"

    def test_a_peak_far_taller_than_the_line_is_its_own_compound(self):
        # Height is what makes this a rule rather than a mass coincidence: a
        # spectrum is dense enough that some committed peak sits one 13C spacing
        # above another most of the time.
        rows = self.pair(child_intensity=1000.0)
        run(rows)
        assert tier_of(rows, "pa-child") == "assigned"

    def test_the_tolerance_is_where_the_line_stops_accounting_for_the_peak(self):
        # The owner predicts its 13C line at 6.6% of its own, so 66 counts here.
        predicted = 1000.0 * 0.066
        inside = self.pair(child_intensity=predicted * ENVELOPE_HEIGHT_TOLERANCE * 0.9)
        run(inside)
        assert tier_of(inside, "pa-child") == "candidate"

        outside = self.pair(child_intensity=predicted * ENVELOPE_HEIGHT_TOLERANCE * 1.5)
        run(outside)
        assert tier_of(outside, "pa-child") == "assigned"

    def test_a_lone_reading_predicts_nothing_for_anyone(self):
        rows = [row("pa-1")]
        run(rows)
        assert REASON_ENVELOPE_NEIGHBOUR not in rules_on(rows, "pa-1")

    def test_a_reading_is_never_its_own_isotope_line(self, monkeypatch):
        # Nothing in the rule makes that true - it holds because every isotope
        # substitution adds mass, so a predicted line cannot land back on the
        # peak it came from. That is a property of the predictor, so this pins
        # the rule against a predictor that returned a lighter line.
        import numpy as np

        from mascope_backend.api.new.peak_assignments import tiering as module

        def lighter(ion_formula, charge, threshold=None):
            return (
                np.array([181.0707, 181.0707]),
                np.array([1.0, 1.0]),
                ["M0", "impossible"],
            )

        monkeypatch.setattr(module, "predict_isotopes", lighter)
        monkeypatch.setattr(
            module,
            "anchor_on_monoisotopic",
            lambda mzs, ints, labels: (mzs, ints, labels),
        )
        rows = [row("pa-1", PLAIN, mz=181.0707, intensity=1000.0)]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_two_rows_on_one_peak_are_not_each_other_s_isotope_line(self, monkeypatch):
        # And the same guard, keyed on the peak: an imported ledger may commit
        # two readings of one peak, and the second is that peak read differently
        # rather than the first one's isotope line.
        import numpy as np

        from mascope_backend.api.new.peak_assignments import tiering as module

        def lighter(ion_formula, charge, threshold=None):
            return (
                np.array([181.0707, 181.0707]),
                np.array([1.0, 1.0]),
                ["M0", "impossible"],
            )

        monkeypatch.setattr(module, "predict_isotopes", lighter)
        monkeypatch.setattr(
            module,
            "anchor_on_monoisotopic",
            lambda mzs, ints, labels: (mzs, ints, labels),
        )
        rows = [
            row("pa-a", PLAIN, peak="shared", mz=181.0707, intensity=1000.0),
            row("pa-b", PLAIN, peak="shared", mz=181.0707, intensity=1.0),
        ]
        run(rows)
        assert tier_of(rows, "pa-a") == "assigned"
        assert tier_of(rows, "pa-b") == "assigned"

    def test_an_unparseable_ion_demotes_nothing(self):
        found = envelope_neighbours(
            [row("pa-1", ion="not an ion")],
            mz_tolerance_ppm=5.0,
            abundance_floor=0.01,
        )
        assert found == {}


class TestWhatTheEarlierPassesDecided:
    @pytest.mark.parametrize(
        "block,expected",
        [
            ({"mass_gate": {"capped": True}}, REASON_OFF_CALIBRATION),
            (
                {"cross_channel": {"channels": ["+NH4+"], "capped": True}},
                REASON_AMBIGUOUS_NITROGEN,
            ),
            ({"minor_channel": {"capped": True}}, REASON_MINOR_CHANNEL),
        ],
    )
    def test_their_reasons_are_restated_in_one_vocabulary(self, block, expected):
        rows = [row("pa-1", tier="candidate", provenance=block)]
        run(rows)
        assert expected in rules_on(rows, "pa-1")

    def test_this_pass_does_not_count_their_caps_as_its_own(self):
        # They already took the tier. Counting it again would report the run
        # demoting a row twice.
        rows = [
            row("pa-1", tier="candidate", provenance={"mass_gate": {"capped": True}})
        ]
        summary = run(rows)
        assert summary["capped"] == 0
        assert summary["capped_by_rule"] == {}

    def test_this_pass_never_takes_a_tier_on_another_pass_s_reason(self):
        # An inconsistent ledger - an imported one, say - can carry an earlier
        # pass's cap flag on a row still at the top tier. The flag is recorded
        # because it is what that pass said; acting on it here would be this
        # pass demoting a row for a rule it does not own.
        rows = [
            row("pa-1", tier="assigned", provenance={"mass_gate": {"capped": True}})
        ]
        summary = run(rows)
        assert tier_of(rows, "pa-1") == "assigned"
        assert REASON_OFF_CALIBRATION in rules_on(rows, "pa-1")
        assert summary["capped"] == 0

    def test_a_row_they_capped_gets_no_standing_reason(self):
        # A row that was capped says what took it, and nothing else. Listing why
        # it might have stood beside the reason it did not is a ledger arguing
        # with itself.
        rows = [
            row("pa-1", tier="candidate", provenance={"mass_gate": {"capped": True}})
        ]
        run(rows)
        reasons = rows[0]["provenance"]["tier_reasons"]
        assert all(reason["caps"] for reason in reasons)


class TestOnlyEverDown:
    def test_a_row_below_the_top_tier_is_not_promoted(self):
        rows = [row("pa-1", tier="candidate", channels=["+H+", "+NH4+"])]
        run(rows)
        assert tier_of(rows, "pa-1") == "candidate"

    def test_a_capping_rule_on_an_already_capped_row_changes_nothing(self):
        rows = [row("pa-1", RADICAL, tier="below_assignability")]
        run(rows)
        assert tier_of(rows, "pa-1") == "below_assignability"

    def test_the_row_still_says_what_the_rule_found(self):
        rows = [row("pa-1", RADICAL, tier="below_assignability")]
        run(rows)
        assert REASON_ODD_ELECTRON in rules_on(rows, "pa-1")


class TestTheIsotopologueRows:
    def test_an_isotopologue_row_follows_its_owner_down(self):
        rows = [
            row("pa-owner", RADICAL),
            row("pa-kid", RADICAL, role="iso_child", owner="pa-owner"),
        ]
        summary = run(rows)
        assert tier_of(rows, "pa-kid") == "candidate"
        assert summary["capped_isotopologues"] == 1
        assert summary["capped"] == 1

    def test_an_isotopologue_of_a_standing_owner_stands(self):
        rows = [
            row("pa-owner"),
            row("pa-kid", role="iso_child", owner="pa-owner"),
        ]
        run(rows)
        assert tier_of(rows, "pa-kid") == "assigned"

    def test_an_isotopologue_row_carries_the_owner_s_answer_not_a_copy(self):
        rows = [
            row("pa-owner", RADICAL),
            row("pa-kid", RADICAL, role="iso_child", owner="pa-owner"),
        ]
        run(rows)
        assert rules_on(rows, "pa-kid") == {REASON_INHERITED}

    def test_isotopologue_rows_are_counted_apart_from_the_analytes(self):
        rows = [
            row("pa-owner", RADICAL),
            row("pa-kid", RADICAL, role="iso_child", owner="pa-owner"),
        ]
        summary = run(rows)
        assert (summary["capped"], summary["capped_isotopologues"]) == (1, 1)


class TestTheRunsRecord:
    def test_the_rule_version_is_recorded(self):
        assert run([row("pa-1")])["version"] == TIERING_RULES_VERSION

    def test_the_thresholds_are_recorded_with_it(self):
        summary = run([row("pa-1")])
        assert summary["density_limit"] == DENSITY_LIMIT
        assert summary["envelope_height_tolerance"] == ENVELOPE_HEIGHT_TOLERANCE

    def test_each_rule_reports_what_it_found(self):
        rows = [row("pa-1", RADICAL), row("pa-2", density=DENSITY_LIMIT, mz=200.0)]
        summary = run(rows)
        assert summary["capped_by_rule"][REASON_ODD_ELECTRON] == 1
        assert summary["capped_by_rule"][REASON_CANDIDATE_DENSITY] == 1
        assert summary["capped"] == 2

    def test_a_row_two_rules_name_is_capped_once_and_counted_twice(self):
        # The per-rule counts say what each rule found, not how many rows it
        # alone was responsible for, so they sum ABOVE the capped count.
        rows = [row("pa-1", RADICAL, density=DENSITY_LIMIT)]
        summary = run(rows)
        assert summary["capped"] == 1
        assert sum(summary["capped_by_rule"].values()) == 2


class TestWhatAStandingRowClaims:
    def test_a_separated_winner_says_so_without_claiming_uniqueness(self):
        # A density of 1 says the evidence separated the winner from the peak's
        # other candidates. It does NOT say the run's element box held no other
        # formula for the mass - that is a wider question, and one the ledger
        # would be overstating if this reason answered it.
        rows = [row("pa-1", density=1)]
        run(rows)
        assert REASON_NO_CLOSE_RIVAL in rules_on(rows, "pa-1")
        detail = next(
            r["detail"]
            for r in rows[0]["provenance"]["tier_reasons"]
            if r["rule"] == REASON_NO_CLOSE_RIVAL
        )
        assert "unique" not in detail.lower()

    def test_a_corroborated_row_names_its_channels(self):
        rows = [row("pa-1", channels=["+H+", "+NH4+", "-H+"])]
        run(rows)
        detail = next(
            r["detail"]
            for r in rows[0]["provenance"]["tier_reasons"]
            if r["rule"] == REASON_CORROBORATED
        )
        assert "3" in detail

    def test_a_standing_reason_never_caps(self):
        rows = [row("pa-1", channels=["+H+", "+NH4+"])]
        run(rows)
        assert not any(r["caps"] for r in rows[0]["provenance"]["tier_reasons"])


class TestAnOwnerTheRunDoesNotStandBehind:
    """The envelope rule predicts the line FROM the neighbour's formula, so the
    neighbour has to be a reading the run is prepared to show."""

    @staticmethod
    def pair(owner_tier: str) -> list[dict]:
        # C6H13O6+ predicts its 13C line 6.6% up at 182.0740; the second row
        # sits there, small enough for the line to account for it.
        return [
            row("pa-owner", PLAIN, tier=owner_tier, mz=181.0707, intensity=1000.0),
            row("pa-child", PLAIN, mz=182.07404, intensity=40.0),
        ]

    def test_a_neighbour_below_assignability_takes_nothing(self):
        rows = self.pair("below_assignability")
        run(rows)
        assert tier_of(rows, "pa-child") == "assigned"
        assert REASON_ENVELOPE_NEIGHBOUR not in rules_on(rows, "pa-child")

    @pytest.mark.parametrize("owner_tier", ["candidate", "assigned"])
    def test_a_neighbour_the_run_shows_still_does(self, owner_tier):
        rows = self.pair(owner_tier)
        run(rows)
        assert tier_of(rows, "pa-child") == "candidate"
        assert REASON_ENVELOPE_NEIGHBOUR in rules_on(rows, "pa-child")


class TestAnIsotopologueOfARowAnEarlierPassCapped:
    """An isotopologue row follows its owner down whichever pass took the
    owner's tier. The mass gate and the reagent-N rule already cap the
    isotopologues of the rows they cap; the minor-channel cap touches M0 rows
    only. This pass states the rule once."""

    @staticmethod
    def family() -> list[dict]:
        return [
            row(
                "pa-owner",
                tier="candidate",
                provenance={"mass_gate": {"capped": True}},
            ),
            row("pa-kid", role="iso_child", owner="pa-owner"),
        ]

    def test_it_follows_the_owner_down(self):
        rows = self.family()
        run(rows)
        assert tier_of(rows, "pa-kid") == "candidate"
        assert rules_on(rows, "pa-kid") == {REASON_INHERITED}

    def test_it_is_counted_apart_from_the_ones_this_pass_capped(self):
        # The owner's tier was an earlier pass's to take, so neither the owner
        # nor its isotopologue is this pass's own cap - the run says which is which.
        summary = run(self.family())
        assert summary["capped"] == 0
        assert summary["capped_isotopologues"] == 0
        assert summary["capped_isotopologues_after_earlier_pass"] == 1

    def test_an_isotopologue_already_capped_by_that_pass_is_not_counted_again(self):
        rows = self.family()
        rows[1]["tier"] = "candidate"
        summary = run(rows)
        assert summary["capped_isotopologues_after_earlier_pass"] == 0

    def test_an_isotopologue_of_this_pass_s_own_cap_is_counted_there(self):
        rows = [
            row("pa-owner", RADICAL),
            row("pa-kid", RADICAL, role="iso_child", owner="pa-owner"),
        ]
        summary = run(rows)
        assert summary["capped_isotopologues"] == 1
        assert summary["capped_isotopologues_after_earlier_pass"] == 0
