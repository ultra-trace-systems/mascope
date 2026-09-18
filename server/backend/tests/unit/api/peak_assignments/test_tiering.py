"""Why a committed row holds the tier it holds, and what takes it away."""

from __future__ import annotations

import pytest

from mascope_backend.api.new.peak_assignments.envelope_claims import (
    ENVELOPE_CLAIM,
    ClaimedLine,
    EnvelopeClaim,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    REASON_ISOTOPOLOGUE_IN_DOUBT,
    REASON_ISOTOPOLOGUE_UNTRACKED,
    TRACKING_IN_DOUBT,
    TRACKING_TRACKS,
    TRACKING_UNTRACKED,
    SpectrumLines,
)
from mascope_backend.api.new.peak_assignments.tiering import (
    DENSITY_LIMIT,
    ENVELOPE_HEIGHT_TOLERANCE,
    HELD_CORROBORATED,
    HELD_LINE_TAKEN,
    HELD_NEIGHBOUR_NOT_ASSIGNED,
    HELD_TARGET_LIBRARY,
    HELD_UNTRACKED,
    REASON_AMBIGUOUS_ADDUCT,
    REASON_AMBIGUOUS_NITROGEN,
    REASON_CANDIDATE_DENSITY,
    REASON_CORROBORATED,
    REASON_ENVELOPE_CLAIM,
    REASON_ENVELOPE_NEIGHBOUR,
    REASON_EVIDENCE_BAND,
    REASON_INHERITED,
    REASON_MINOR_CHANNEL,
    REASON_NO_CLOSE_RIVAL,
    REASON_NOT_MEASURED,
    REASON_ODD_ELECTRON,
    REASON_OFF_CALIBRATION,
    REASON_OXYGEN_FREE_CLUSTER,
    REASON_POLYHALIDE_CLUSTER,
    REASON_SAME_ION_SETTLED,
    TIERING_RULES_VERSION,
    apply_tiering,
    envelope_neighbours,
    find_envelope_claims,
    predicted_envelope,
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
    ppm: float | None = 0.0,
    compound: str | None = None,
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
        "mz_error_ppm": ppm,
        "target_compound_id": compound,
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
        notation_by_id=kwargs.pop("notation_by_id", None),
        tier_bands=kwargs.pop("tier_bands", None),
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


#: A run's mechanisms as the tiering pass is handed them: the finder's
#: spelling, by the id a row carries.
CHANNELS = {
    "m-nitrate": "+NO3-",
    "m-labelled": "+[15N]O3-",
    "m-acid-cluster": "+(HNO3)NO3-",
    "m-deprotonation": "-H+",
    "m-bromide": "+Br-",
    "m-carbonate": "+CO3-",
    "m-iodide": "+I-",
    "m-dibromide": "+Br2-",
}


def cluster(
    row_id: str, formula: str = "C10H16", mechanism: str = "m-nitrate", **kwargs
) -> dict:
    """A committed row read through one of :data:`CHANNELS`."""
    entry = row(row_id, formula, ion=None, **kwargs)
    entry["ionization_mechanism_id"] = mechanism
    return entry


class TestAClusterWithNothingToHoldOnTo:
    def test_nitrate_on_a_neutral_with_no_oxygen_loses_the_top_tier(self):
        rows = [cluster("pa-1")]
        summary = run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "candidate"
        assert REASON_OXYGEN_FREE_CLUSTER in rules_on(rows, "pa-1")
        assert summary["capped"] == 1
        assert summary["capped_by_rule"] == {REASON_OXYGEN_FREE_CLUSTER: 1}

    @pytest.mark.parametrize("mechanism", ["m-labelled", "m-acid-cluster"])
    def test_every_nitrate_cluster_is_asked(self, mechanism):
        rows = [cluster("pa-1", mechanism=mechanism)]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "candidate"

    def test_the_row_keeps_its_reading(self):
        # What was measured on the peak still fits: the reading stays, and only
        # the tier the run stands behind it at goes.
        rows = [cluster("pa-1")]
        run(rows, notation_by_id=CHANNELS)
        assert rows[0]["assigned_formula"] == "C10H16"
        assert rows[0]["ionization_mechanism_id"] == "m-nitrate"

    def test_one_oxygen_is_enough_to_hold_on_to(self):
        rows = [cluster("pa-1", "C10H16O")]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "assigned"
        assert REASON_OXYGEN_FREE_CLUSTER not in rules_on(rows, "pa-1")

    @pytest.mark.parametrize(
        "mechanism", ["m-deprotonation", "m-bromide", "m-carbonate"]
    )
    def test_a_reading_through_another_channel_is_not_asked(self, mechanism):
        rows = [cluster("pa-1", mechanism=mechanism)]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "assigned"
        assert REASON_OXYGEN_FREE_CLUSTER not in rules_on(rows, "pa-1")

    def test_a_reference_list_s_row_is_asked(self):
        # A list names a compound, not the channel it is seen through.
        rows = [cluster("pa-1", source="database")]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "candidate"

    def test_a_target_library_row_is_exempt(self):
        # The workspace named the compound for the modes its collection is
        # attached to: hydrogen bromide on a nitrate source's monitor list is
        # the cluster somebody chose to watch.
        rows = [cluster("pa-1", "HBr", source="database", compound="compound-7")]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "assigned"
        assert REASON_OXYGEN_FREE_CLUSTER not in rules_on(rows, "pa-1")

    def test_a_second_channel_does_not_rescue_it(self):
        # What the rule doubts is the ion, not whether the neutral was seen.
        rows = [cluster("pa-1", channels=["+NO3-", "-H+"])]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "candidate"

    def test_a_channel_the_run_does_not_name_is_not_asked(self):
        rows = [cluster("pa-1", mechanism="m-unknown")]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_without_the_run_s_mechanisms_no_row_is_asked(self):
        rows = [cluster("pa-1")]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_its_isotopologues_follow_it_down(self):
        rows = [
            cluster("pa-1"),
            cluster("pa-kid", role="iso_child", owner="pa-1", mz=182.07),
        ]
        summary = run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-kid") == "candidate"
        assert rules_on(rows, "pa-kid") == {REASON_INHERITED}
        assert summary["capped_isotopologues"] == 1

    def test_the_reason_names_the_neutral_and_the_channel(self):
        rows = [cluster("pa-1", mechanism="m-labelled")]
        run(rows, notation_by_id=CHANNELS)
        (detail,) = [
            reason["detail"]
            for reason in rows[0]["provenance"]["tier_reasons"]
            if reason["rule"] == REASON_OXYGEN_FREE_CLUSTER
        ]
        assert "C10H16" in detail
        assert "+[15N]O3-" in detail


def halide(
    row_id: str, formula: str = "BrI", mechanism: str = "m-bromide", **kwargs
) -> dict:
    """A reference list's row read through one of :data:`CHANNELS`."""
    kwargs.setdefault("source", "database")
    return cluster(row_id, formula, mechanism, **kwargs)


class TestAPolyhalideTheSourceCanMakeToo:
    def test_a_halogen_through_a_halide_loses_the_top_tier(self):
        rows = [halide("pa-1")]
        summary = run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "candidate"
        assert REASON_POLYHALIDE_CLUSTER in rules_on(rows, "pa-1")
        assert summary["capped"] == 1
        assert summary["capped_by_rule"] == {REASON_POLYHALIDE_CLUSTER: 1}

    @pytest.mark.parametrize(
        "formula, mechanism",
        [("I2", "m-bromide"), ("ClI", "m-iodide"), ("BrI", "m-dibromide")],
    )
    def test_every_halogen_through_every_halide_is_asked(self, formula, mechanism):
        rows = [halide("pa-1", formula, mechanism)]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "candidate"

    def test_the_row_keeps_its_reading(self):
        # The air's IBr is measured this way too: the reading stays, and only
        # the tier the run stands behind it at goes.
        rows = [halide("pa-1")]
        run(rows, notation_by_id=CHANNELS)
        assert rows[0]["assigned_formula"] == "BrI"
        assert rows[0]["ionization_mechanism_id"] == "m-bromide"

    @pytest.mark.parametrize("formula", ["HBr", "HOI", "CH2Br2"])
    def test_a_neutral_with_anything_but_halogens_is_not_asked(self, formula):
        rows = [halide("pa-1", formula)]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "assigned"
        assert REASON_POLYHALIDE_CLUSTER not in rules_on(rows, "pa-1")

    @pytest.mark.parametrize(
        "mechanism", ["m-nitrate", "m-deprotonation", "m-carbonate"]
    )
    def test_a_reading_through_another_channel_is_not_asked(self, mechanism):
        rows = [halide("pa-1", mechanism=mechanism)]
        run(rows, notation_by_id=CHANNELS)
        assert REASON_POLYHALIDE_CLUSTER not in rules_on(rows, "pa-1")

    def test_a_target_library_row_is_exempt(self):
        rows = [halide("pa-1", compound="compound-7")]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "assigned"
        assert REASON_POLYHALIDE_CLUSTER not in rules_on(rows, "pa-1")

    def test_a_second_channel_does_not_rescue_it(self):
        # What the rule doubts is where the ion came from, not whether the
        # neutral was seen.
        rows = [halide("pa-1", channels=["+Br-", "+I-"])]
        run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-1") == "candidate"

    def test_without_the_run_s_mechanisms_no_row_is_asked(self):
        rows = [halide("pa-1")]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"

    def test_its_isotopologues_follow_it_down(self):
        rows = [
            halide("pa-1"),
            halide("pa-kid", role="iso_child", owner="pa-1", mz=286.74),
        ]
        summary = run(rows, notation_by_id=CHANNELS)
        assert tier_of(rows, "pa-kid") == "candidate"
        assert rules_on(rows, "pa-kid") == {REASON_INHERITED}
        assert summary["capped_isotopologues"] == 1

    def test_the_reason_names_the_neutral_and_the_channel(self):
        rows = [halide("pa-1")]
        run(rows, notation_by_id=CHANNELS)
        (detail,) = [
            reason["detail"]
            for reason in rows[0]["provenance"]["tier_reasons"]
            if reason["rule"] == REASON_POLYHALIDE_CLUSTER
        ]
        assert "BrI" in detail
        assert "+Br-" in detail


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
        # Closed-shell, so the radical rule has no opinion and the cap is this
        # signature's alone.
        rows = [row("pa-1", "H2S3", ion="HS3-")]
        run(rows)
        assert tier_of(rows, "pa-1") == "candidate"
        assert "carbon_free" in rules_on(rows, "pa-1")
        assert REASON_ODD_ELECTRON not in rules_on(rows, "pa-1")

    def test_a_curated_carbon_free_row_keeps_its_tier(self):
        # Trisulfur, as a curated library holds it. Every signature names what
        # a mass search produces, and a curated row was matched to an authored
        # identity - the radical rule's exemption, for the same reason.
        rows = [row("pa-1", "HS3", ion="S3-", source="database")]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"
        assert rules_on(rows, "pa-1") == {REASON_NO_CLOSE_RIVAL}

    def test_a_curated_oxygen_lattice_keeps_its_tier(self):
        # Peroxyacetyl nitrate has the lattice's shape and is a species these
        # sources are built to see. The same formula found by the untargeted
        # search is still capped.
        curated = [row("pa-1", "C2H3NO5", ion="C2H2NO5-", source="database")]
        found = [row("pa-2", "C2H3NO5", ion="C2H2NO5-")]
        run(curated)
        run(found)
        assert tier_of(curated, "pa-1") == "assigned"
        assert "oxygen_lattice" not in rules_on(curated, "pa-1")
        assert tier_of(found, "pa-2") == "candidate"
        assert "oxygen_lattice" in rules_on(found, "pa-2")


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

    def test_an_envelope_is_read_relative_to_the_ion_s_own_line(self):
        # A dibromide's own line is a quarter of its envelope, and its
        # 79Br81Br line twice as tall as it: the heights the rule compares a
        # peak with are the ion's own line's multiples, not shares of the whole.
        mzs, shares, labels = predicted_envelope("C2H3Br2-", 0.01)
        assert labels[0] == "M0"
        assert shares[0] == pytest.approx(1.0)
        assert shares[labels.index("81Br")] == pytest.approx(1.95, abs=0.03)
        assert predicted_envelope("not an ion", 0.01) is None
        assert predicted_envelope("C2H3Br2", 0.01) is None


class TestWhatTheEarlierPassesDecided:
    @pytest.mark.parametrize(
        "block,expected",
        [
            ({"mass_gate": {"capped": True}}, REASON_OFF_CALIBRATION),
            (
                {
                    "cross_channel": {
                        "channels": ["+NH4+"],
                        "ambiguous_nitrogen": {"alternative": "C6H15NO6", "via": "+H+"},
                    }
                },
                REASON_AMBIGUOUS_NITROGEN,
            ),
            (
                {
                    "cross_channel": {
                        "channels": ["+Br-"],
                        "ambiguous_adduct": {"alternative": "C6H13BrO6", "via": "-H+"},
                    }
                },
                REASON_AMBIGUOUS_ADDUCT,
            ),
            (
                {
                    "cross_channel": {
                        "inherited_from": "pa-0",
                        "capped": "candidate",
                        "reason": "ambiguous_adduct",
                    }
                },
                REASON_AMBIGUOUS_ADDUCT,
            ),
            ({"minor_channel": {"capped": True}}, REASON_MINOR_CHANNEL),
        ],
    )
    def test_their_reasons_are_restated_in_one_vocabulary(self, block, expected):
        rows = [row("pa-1", tier="candidate", provenance=block)]
        run(rows)
        assert expected in rules_on(rows, "pa-1")

    @pytest.mark.parametrize(
        "formula, alternative, via, detail",
        [
            (
                "C6H12O6",
                "C6H15NO6",
                "+H+",
                "the same ion reads as C6H15NO6 through +H+, which puts one more "
                "nitrogen on the analyte, and no second channel of this run "
                "settles the count",
            ),
            # Dimethylformamide through +H+ is acrolein through +NH4+: the doubt
            # runs the other way, and the sentence says which.
            (
                "C3H7NO",
                "C3H4O",
                "+NH4+",
                "the same ion reads as C3H4O through +NH4+, which puts one fewer "
                "nitrogen on the analyte, and no second channel of this run "
                "settles the count",
            ),
        ],
    )
    def test_the_nitrogen_reason_says_which_way_the_count_moves(
        self, formula, alternative, via, detail
    ):
        cross_channel = {
            "channels": ["+H+"],
            "capped": True,
            "ambiguous_nitrogen": {"alternative": alternative, "via": via},
        }
        rows = [
            row(
                "pa-1",
                formula,
                tier="candidate",
                provenance={"cross_channel": cross_channel},
            )
        ]
        run(rows)
        (reason,) = [
            reason
            for reason in rows[0]["provenance"]["tier_reasons"]
            if reason["rule"] == REASON_AMBIGUOUS_NITROGEN
        ]
        assert reason["detail"] == detail

    def test_the_adduct_reason_names_the_other_molecule(self):
        cross_channel = {
            "channels": ["+Br-"],
            "ambiguous_adduct": {"alternative": "CH3BrO2", "via": "-H+"},
        }
        rows = [
            row(
                "pa-1",
                "CH2O2",
                tier="candidate",
                provenance={"cross_channel": cross_channel},
            )
        ]
        run(rows)
        (reason,) = [
            reason
            for reason in rows[0]["provenance"]["tier_reasons"]
            if reason["rule"] == REASON_AMBIGUOUS_ADDUCT
        ]
        assert reason["detail"] == (
            "the same ion reads as CH3BrO2 through -H+, another molecule the "
            "spectrum cannot tell from this one, and no second channel of this "
            "run settles which"
        )
        assert reason["caps"] is True

    def test_a_rival_on_a_row_already_lower_is_stated_all_the_same(self):
        # The pass recorded the rival without lowering anything, and the row
        # still says what it is in doubt with.
        cross_channel = {
            "channels": ["+NH4+"],
            "ambiguous_nitrogen": {"alternative": "C6H15NO6", "via": "+H+"},
        }
        rows = [
            row(
                "pa-1",
                tier="below_assignability",
                provenance={"cross_channel": cross_channel},
            )
        ]
        summary = run(rows)
        assert REASON_AMBIGUOUS_NITROGEN in rules_on(rows, "pa-1")
        assert summary["capped"] == 0

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

    def test_the_rule_set_is_6(self):
        # The number, not the imported constant: a tier is comparable across
        # runs only under the same rules, so the set moves on purpose and this
        # test moves with it.
        assert run([row("pa-1")])["version"] == 6

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


BANDS = {"assigned": 0.75, "candidate": 0.45}


def banded(row_id: str, evidence: float, **kwargs) -> dict:
    """A row the bands put where its evidence says, fit and plausibility shown."""
    entry = row(row_id, **kwargs)
    entry["fit_score"] = evidence
    entry["provenance"].update(evidence=evidence, plausibility=1.0)
    return entry


class TestTheBandComesFirst:
    """A row's tier is its band's before any rule lowers it, and a row under the
    top band says so before anything else."""

    def test_a_row_under_the_candidate_band_names_it_first(self):
        rows = [banded("pa-1", 0.081, tier="below_assignability")]
        run(rows, tier_bands=BANDS)
        assert rows[0]["provenance"]["tier_reasons"][0] == {
            "rule": REASON_EVIDENCE_BAND,
            "detail": (
                "evidence 8% (fit 8% x plausibility 100%) is under the candidate "
                "band of 45%"
            ),
            "caps": True,
            "band": "below_assignability",
        }

    def test_a_row_under_the_assigned_band_names_that_one(self):
        rows = [banded("pa-1", 0.62, tier="candidate")]
        run(rows, tier_bands=BANDS)
        first = rows[0]["provenance"]["tier_reasons"][0]
        assert first["detail"] == (
            "evidence 62% (fit 62% x plausibility 100%) is under the assigned band "
            "of 75%"
        )
        assert first["band"] == "candidate"

    def test_a_row_the_top_band_holds_names_no_band(self):
        rows = [banded("pa-1", 0.91)]
        run(rows, tier_bands=BANDS)
        assert REASON_EVIDENCE_BAND not in rules_on(rows, "pa-1")

    def test_a_row_just_under_a_band_is_not_shown_at_it(self):
        # 74.96% rounds to the band it misses; the sentence is precise enough
        # not to read as "75% is under 75%".
        rows = [banded("pa-1", 0.7496, tier="candidate")]
        run(rows, tier_bands=BANDS)
        assert rows[0]["provenance"]["tier_reasons"][0]["detail"] == (
            "evidence 74.96% (fit 74.96% x plausibility 100.00%) is under the "
            "assigned band of 75.00%"
        )

    def test_it_does_not_hide_what_the_row_stands_on(self):
        # The DMF case: held below by its fit, seen through a second channel and
        # separated from every other ion. The band says why it is low; the rest
        # still say what it has.
        rows = [
            banded("pa-1", 0.081, tier="below_assignability", channels=["+H+", "+U+"])
        ]
        run(rows, tier_bands=BANDS)
        assert [r["rule"] for r in rows[0]["provenance"]["tier_reasons"]] == [
            REASON_EVIDENCE_BAND,
            REASON_CORROBORATED,
            REASON_NO_CLOSE_RIVAL,
        ]

    def test_it_is_not_a_cap_of_this_pass(self):
        rows = [banded("pa-1", 0.081, tier="below_assignability")]
        summary = run(rows, tier_bands=BANDS)
        assert (summary["capped"], summary["capped_by_rule"]) == (0, {})
        assert summary["under_band"] == 1

    def test_nor_does_it_take_an_isotopologue_down_with_its_owner(self):
        # An isotopologue carries its ion's fit and is banded on it, so the band
        # reached it already; following the owner down is for the rules.
        rows = [
            banded("pa-owner", 0.62, tier="candidate"),
            row("pa-kid", role="iso_child", owner="pa-owner"),
        ]
        run(rows, tier_bands=BANDS)
        assert tier_of(rows, "pa-kid") == "assigned"

    def test_a_run_that_states_no_bands_names_none(self):
        rows = [banded("pa-1", 0.081, tier="below_assignability")]
        summary = run(rows)
        assert REASON_EVIDENCE_BAND not in rules_on(rows, "pa-1")
        assert summary["under_band"] == 0

    def test_a_row_with_no_evidence_names_none(self):
        rows = [row("pa-1", tier="candidate")]
        run(rows, tier_bands=BANDS)
        assert REASON_EVIDENCE_BAND not in rules_on(rows, "pa-1")


def settled(by: str, **extra) -> dict:
    return {
        "channels": ["+H+", "+(CH4N2O)H+"] if by == "second_channel" else ["+H+"],
        "same_ion_settled": {
            "alternative": "C3H4O",
            "via": "+NH4+",
            "by": by,
            **extra,
        },
    }


class TestAReadingOfTheSameIonThatSomethingSettled:
    def detail_of(self, rows: list[dict], rule: str) -> str:
        return next(
            reason["detail"]
            for reason in rows[0]["provenance"]["tier_reasons"]
            if reason["rule"] == rule
        )

    def test_a_second_channel_says_which(self):
        rows = [
            row(
                "pa-1",
                "C3H7NO",
                provenance={
                    "cross_channel": settled("second_channel", through=["+(CH4N2O)H+"])
                },
            )
        ]
        run(rows)
        assert self.detail_of(rows, REASON_SAME_ION_SETTLED) == (
            "the same ion also reads as C3H4O through +NH4+; C3H7NO is also "
            "committed through +(CH4N2O)H+, which settles it"
        )

    def test_the_target_library_says_its_curation_chose(self):
        rows = [
            row(
                "pa-1",
                "C3H7NO",
                source="database",
                compound="tc-1",
                provenance={"cross_channel": settled("target_library")},
            )
        ]
        run(rows)
        assert self.detail_of(rows, REASON_SAME_ION_SETTLED) == (
            "the same ion also reads as C3H4O through +NH4+; this row is a "
            "compound of the target library, whose curation chose the reading"
        )

    def test_a_radical_is_no_rival(self):
        rows = [row("pa-1", "C3H7NO", provenance={"cross_channel": settled("radical")})]
        run(rows)
        assert self.detail_of(rows, REASON_SAME_ION_SETTLED) == (
            "the same ion also reads as C3H4O through +NH4+, a radical rather "
            "than a molecule, so it is no rival"
        )

    def test_no_close_rival_does_not_claim_the_readings_apart(self):
        # The density weighs the formulas the run competed for the peak, and
        # another reading of the ion is the same measurement, not one of them.
        rows = [row("pa-1", "C3H7NO", provenance={"cross_channel": settled("radical")})]
        run(rows)
        assert self.detail_of(rows, REASON_NO_CLOSE_RIVAL) == (
            "the evidence separates this ion from every other the run competed "
            "for the peak; which reading of the ion it is, the evidence cannot say"
        )

    def test_a_row_whose_ion_reads_one_way_keeps_the_formula_sentence(self):
        rows = [row("pa-1")]
        run(rows)
        assert self.detail_of(rows, REASON_NO_CLOSE_RIVAL) == (
            "the evidence separates this formula from every other candidate the "
            "run competed for the peak"
        )

    def test_it_stands_between_the_channels_and_the_rivals(self):
        rows = [
            row(
                "pa-1",
                "C3H7NO",
                provenance={
                    "cross_channel": settled("second_channel", through=["+(CH4N2O)H+"])
                },
            )
        ]
        run(rows)
        assert [r["rule"] for r in rows[0]["provenance"]["tier_reasons"]] == [
            REASON_CORROBORATED,
            REASON_SAME_ION_SETTLED,
            REASON_NO_CLOSE_RIVAL,
        ]

    def test_it_never_caps(self):
        rows = [row("pa-1", "C3H7NO", provenance={"cross_channel": settled("radical")})]
        run(rows)
        assert tier_of(rows, "pa-1") == "assigned"
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


def reasons_of(rows: list[dict], row_id: str) -> list[dict]:
    return next(r for r in rows if r["peak_assignment_id"] == row_id)["provenance"][
        "tier_reasons"
    ]


class TestWhatAnIsotopologueSaysOfItsOwnLine:
    """What the gate and a claim found of an isotopologue's peak, beside its
    owner's answer."""

    @staticmethod
    def family(child_provenance: dict, *, child_tier: str = "candidate") -> list[dict]:
        return [
            row("pa-owner"),
            row(
                "pa-kid",
                role="iso_child",
                owner="pa-owner",
                tier=child_tier,
                provenance=child_provenance,
            ),
        ]

    def test_a_line_in_doubt_says_so_and_still_follows_its_owner(self):
        rows = self.family(
            {
                "mass_gate": {
                    "corroborated_by": None,
                    "tracking": TRACKING_IN_DOUBT,
                    "capped": "candidate",
                    "reason": REASON_ISOTOPOLOGUE_IN_DOUBT,
                }
            }
        )
        summary = run(rows)
        reasons = reasons_of(rows, "pa-kid")
        assert [r["rule"] for r in reasons] == [
            REASON_ISOTOPOLOGUE_IN_DOUBT,
            REASON_INHERITED,
        ]
        assert [r["caps"] for r in reasons] == [True, False]
        # The gate took that tier, not this pass.
        assert summary["capped_isotopologues"] == 0

    def test_a_line_that_does_not_track_says_so_whatever_took_its_tier(self):
        # Its evidence already had it at candidate, so the gate lowered
        # nothing, and the finding is still the row's to show.
        rows = self.family(
            {"mass_gate": {"corroborated_by": None, "tracking": TRACKING_UNTRACKED}}
        )
        run(rows)
        assert [r["rule"] for r in reasons_of(rows, "pa-kid")] == [
            REASON_ISOTOPOLOGUE_UNTRACKED,
            REASON_INHERITED,
        ]

    def test_a_line_capped_for_its_distance_says_both(self):
        rows = self.family(
            {
                "mass_gate": {
                    "corroborated_by": None,
                    "tracking": TRACKING_UNTRACKED,
                    "capped": "below_assignability",
                    "reason": REASON_OFF_CALIBRATION,
                },
                "mass_z": 7.1,
            },
            child_tier="below_assignability",
        )
        run(rows)
        assert [r["rule"] for r in reasons_of(rows, "pa-kid")] == [
            REASON_OFF_CALIBRATION,
            REASON_ISOTOPOLOGUE_UNTRACKED,
            REASON_INHERITED,
        ]

    def test_a_line_that_tracks_says_only_what_its_owner_says(self):
        rows = self.family(
            {
                "mass_gate": {
                    "corroborated_by": "isotopologue",
                    "tracking": TRACKING_TRACKS,
                }
            },
            child_tier="assigned",
        )
        run(rows)
        assert rules_on(rows, "pa-kid") == {REASON_INHERITED}
        assert tier_of(rows, "pa-kid") == "assigned"

    def test_a_claimed_line_says_what_it_was_read_as_before(self):
        claim = {
            "line": "13C",
            "predicted_share": 0.0662,
            "observed_share": 0.04,
            "tracking": TRACKING_TRACKS,
            "displaced": {
                "assigned_formula": "C7H11NO4",
                "tier": "below_assignability",
            },
        }
        rows = self.family({ENVELOPE_CLAIM: claim})
        summary = run(rows)
        reasons = reasons_of(rows, "pa-kid")
        assert [r["rule"] for r in reasons] == [REASON_ENVELOPE_CLAIM, REASON_INHERITED]
        assert reasons[0]["caps"] is True
        assert "13C line of C6H12O6" in reasons[0]["detail"]
        assert "C7H11NO4" in reasons[0]["detail"]
        assert "6.62%" in reasons[0]["detail"]
        assert (summary["claimed"], summary["claimed_with_their_lines"]) == (1, 0)

    def test_a_line_carried_with_a_claim_says_whose_it_was(self):
        claim = {
            "line": "18O",
            "tracking": TRACKING_TRACKS,
            "carried_with": "pa-claimed",
            "displaced": {"assigned_formula": "C7H11NO4"},
        }
        rows = self.family({ENVELOPE_CLAIM: claim})
        summary = run(rows)
        detail = reasons_of(rows, "pa-kid")[0]["detail"]
        assert "isotopologue of C7H11NO4" in detail
        assert "18O line of C6H12O6" in detail
        assert (summary["claimed"], summary["claimed_with_their_lines"]) == (0, 1)


#: The ion the claim tests read lines of, and the floor they predict it to.
CLAIMING_ION, FLOOR = "C6H13O6+", 0.01

#: A closed-shell neutral a search could have committed on the owner's 13C line.
ELSEWHERE = "C7H11NO4"


def predicted_line(label: str) -> tuple[float, float]:
    """Where the claiming ion's envelope puts a line, and how tall."""
    mzs, shares, labels = predicted_envelope(CLAIMING_ION, FLOOR)
    index = labels.index(label)
    return float(mzs[index]), float(shares[index])


def on_the_line(
    row_id: str,
    label: str = "13C",
    *,
    ppm: float = 0.0,
    intensity: float = 40.0,
    **fields,
) -> dict:
    """A committed row on one of the owner's predicted lines, `ppm` off it."""
    line_mz, _ = predicted_line(label)
    fields.setdefault("ion", "C7H12NO4+")
    return row(
        row_id,
        fields.pop("formula", ELSEWHERE),
        mz=line_mz * (1 + ppm * 1e-6),
        intensity=intensity,
        ppm=0.1,
        **fields,
    )


def claiming_owner(**fields) -> dict:
    fields.setdefault("tier", "assigned")
    return row("pa-owner", PLAIN, mz=181.0707, intensity=1000.0, ppm=0.0, **fields)


def claims_in(rows: list[dict], lines: SpectrumLines | None = None):
    run(rows, abundance_floor=FLOOR)
    return find_envelope_claims(
        rows,
        mz_tolerance_ppm=5.0,
        abundance_floor=FLOOR,
        precision_ppm=0.3,
        lines=lines,
    )


def envelope_entry(rows: list[dict], row_id: str) -> dict:
    return next(
        r for r in reasons_of(rows, row_id) if r["rule"] == REASON_ENVELOPE_NEIGHBOUR
    )


class TestReadingALineAsTheNeighbours:
    """A row on an assigned neighbour's line is read as that line."""

    def test_the_envelope_reason_names_the_neighbour_and_the_line(self):
        rows = [claiming_owner(), on_the_line("pa-child")]
        run(rows, abundance_floor=FLOOR)
        line_mz, share = predicted_line("13C")
        entry = envelope_entry(rows, "pa-child")
        assert entry["neighbour"] == "pa-owner"
        assert entry["line"] == "13C"
        assert entry["line_mz"] == pytest.approx(line_mz)
        assert entry["predicted_share"] == pytest.approx(share, abs=1e-6)

    def test_a_row_on_an_assigned_neighbour_s_line_is_read_as_it(self):
        rows = [claiming_owner(), on_the_line("pa-child", ppm=0.2)]
        claims, held = claims_in(rows)
        line_mz, share = predicted_line("13C")
        assert claims == [
            EnvelopeClaim(
                owner_id="pa-owner",
                line=ClaimedLine(
                    row_id="pa-child",
                    label="13C",
                    line_mz=pytest.approx(line_mz),
                    share=pytest.approx(share, abs=1e-6),
                    tracking=TRACKING_TRACKS,
                ),
            )
        ]
        assert held == {}
        # Finding it changes nothing on the rows: applying it is the service's.
        assert tier_of(rows, "pa-child") == "candidate"
        assert rows[1]["role"] == "M0"

    def test_under_a_neighbour_at_candidate_it_stays_and_says_why(self):
        rows = [claiming_owner(tier="candidate"), on_the_line("pa-child")]
        claims, held = claims_in(rows)
        assert claims == []
        assert held == {HELD_NEIGHBOUR_NOT_ASSIGNED: 1}
        assert envelope_entry(rows, "pa-child")["detail"].endswith(
            "it is not read as that line, because that reading is not held at assigned"
        )

    def test_the_neighbour_s_tier_is_read_after_this_pass_took_what_it_took(self):
        # Assigned on its evidence, and capped here as a radical.
        rows = [
            row("pa-owner", RADICAL, mz=181.0707, intensity=1000.0, ppm=0.0),
            on_the_line("pa-child"),
        ]
        claims, held = claims_in(rows)
        assert tier_of(rows, "pa-owner") == "candidate"
        assert (claims, held) == ([], {HELD_NEIGHBOUR_NOT_ASSIGNED: 1})

    def test_a_compound_of_the_target_library_stays_what_it_is(self):
        rows = [
            claiming_owner(),
            on_the_line("pa-child", source="database", compound="compound-7"),
        ]
        assert claims_in(rows) == ([], {HELD_TARGET_LIBRARY: 1})

    def test_a_reference_list_s_match_can_be_read_as_the_line(self):
        rows = [claiming_owner(), on_the_line("pa-child", source="database")]
        claims, _ = claims_in(rows)
        assert [claim.row_id for claim in claims] == ["pa-child"]

    def test_a_neutral_another_channel_committed_stays(self):
        rows = [claiming_owner(), on_the_line("pa-child", channels=["+H+", "+NH4+"])]
        assert claims_in(rows) == ([], {HELD_CORROBORATED: 1})

    def test_a_line_the_neighbour_already_holds_is_not_taken_twice(self):
        line_mz, _ = predicted_line("13C")
        rows = [
            claiming_owner(),
            row(
                "pa-owner-13c",
                PLAIN,
                role="iso_child",
                owner="pa-owner",
                mz=line_mz * (1 - 3e-6),
                intensity=60.0,
            ),
            on_the_line("pa-child", ppm=1.0),
        ]
        assert claims_in(rows) == ([], {HELD_LINE_TAKEN: 1})

    def test_a_peak_whose_error_does_not_follow_the_neighbour_s_stays(self):
        # 3 ppm from where a bright neighbour's line sits, with nothing about
        # the line to explain it.
        rows = [claiming_owner(), on_the_line("pa-child", ppm=3.0)]
        assert claims_in(rows) == ([], {HELD_UNTRACKED: 1})
        assert envelope_entry(rows, "pa-child")["detail"].endswith(
            "because its mass error does not follow that reading's, even allowing "
            "for what its line can deliver"
        )

    def test_a_faint_peak_that_misses_by_its_noise_is_read_as_the_line(self):
        line_mz, _ = predicted_line("13C")
        lines = SpectrumLines(
            ["peak-pa-owner", "peak-pa-child"],
            [181.0707, line_mz * (1 + 1.3e-6)],
            [1000.0, 40.0],
            [400.0, 3.0],
        )
        rows = [claiming_owner(), on_the_line("pa-child", ppm=1.3)]
        claims, held = claims_in(rows, lines)
        assert [(claim.row_id, claim.line.tracking) for claim in claims] == [
            ("pa-child", TRACKING_IN_DOUBT)
        ]
        assert held == {}

    def test_of_two_peaks_on_one_line_the_nearer_is_read_as_it(self):
        rows = [
            claiming_owner(),
            on_the_line("pa-far", ppm=-0.8, peak="far"),
            on_the_line("pa-near", ppm=0.3, peak="near"),
        ]
        claims, held = claims_in(rows)
        assert [claim.row_id for claim in claims] == ["pa-near"]
        assert held == {HELD_LINE_TAKEN: 1}

    def test_the_row_s_own_lines_go_with_it_where_the_neighbour_predicts_them(self):
        o18_mz, o18_share = predicted_line("18O")
        rows = [
            claiming_owner(),
            on_the_line("pa-child"),
            row(
                "pa-child-18o",
                ELSEWHERE,
                role="iso_child",
                owner="pa-child",
                mz=o18_mz,
                intensity=10.0,
                ppm=0.0,
            ),
            row(
                "pa-child-lost",
                ELSEWHERE,
                role="iso_child",
                owner="pa-child",
                mz=185.5,
                intensity=5.0,
                ppm=0.0,
            ),
        ]
        (claim,), _ = claims_in(rows)
        assert claim.carried == (
            ClaimedLine(
                row_id="pa-child-18o",
                label="18O",
                line_mz=pytest.approx(o18_mz),
                share=pytest.approx(o18_share, abs=1e-6),
                tracking=TRACKING_TRACKS,
            ),
        )
        assert claim.released == ("pa-child-lost",)

    def test_a_line_of_the_row_the_neighbour_already_holds_is_released(self):
        o18_mz, _ = predicted_line("18O")
        rows = [
            claiming_owner(),
            row(
                "pa-owner-18o",
                PLAIN,
                role="iso_child",
                owner="pa-owner",
                mz=o18_mz * (1 - 3e-6),
                intensity=12.0,
            ),
            on_the_line("pa-child"),
            row(
                "pa-child-18o",
                ELSEWHERE,
                role="iso_child",
                owner="pa-child",
                mz=o18_mz,
                intensity=10.0,
                ppm=0.0,
            ),
        ]
        (claim,), _ = claims_in(rows)
        assert claim.carried == ()
        assert claim.released == ("pa-child-18o",)

    def test_a_line_of_the_row_that_does_not_follow_the_neighbour_is_released(self):
        # On the neighbour's 18O line within the window, and 3 ppm off it.
        o18_mz, _ = predicted_line("18O")
        rows = [
            claiming_owner(),
            on_the_line("pa-child"),
            row(
                "pa-child-18o",
                ELSEWHERE,
                role="iso_child",
                owner="pa-child",
                mz=o18_mz * (1 + 3e-6),
                intensity=10.0,
                ppm=0.0,
            ),
        ]
        (claim,), _ = claims_in(rows)
        assert claim.carried == ()
        assert claim.released == ("pa-child-18o",)

    def test_a_row_the_rule_did_not_flag_is_not_asked(self):
        rows = [claiming_owner(), on_the_line("pa-child", intensity=1000.0)]
        assert claims_in(rows) == ([], {})
