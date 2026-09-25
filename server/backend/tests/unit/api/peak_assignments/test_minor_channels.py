"""How an opportunistic secondary channel is treated once it is searched.

The fingerprint that decides whether a channel is searched at all is covered in
``libraries/tools``. What is pinned here is what the engine does with a channel
it opened for itself: it must not let one win a peak the mode's own chemistry
explains equally well, and it must not let one hand out the ledger's strongest
word on its own authority.
"""

from types import SimpleNamespace

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import engine as engine_module
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    apply_partner_gates,
    untargeted_matches_to_peak_assignments,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    ResolvedProfile,
    resolve_profile,
    with_secondary_channels,
)
from mascope_backend.api.new.peak_assignments.service import _searched_mechanisms
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
)


MECHANISM_IDS = {"+H+": "im-h", "+NH4+": "im-nh4"}
UREA = ["+H+", "+(CH4N2O)H+"]


def _peaks(*specs):
    return pd.DataFrame(
        [
            {"sample_peak_id": pid, "mz": mz, "intensity": intensity}
            for pid, mz, intensity in specs
        ]
    )


def _match(mz, formula, ion, mechanism, score, isotope_label="M0"):
    return {
        "mz": mz,
        "formula": formula,
        "ion": ion,
        "ionization_mechanism": mechanism,
        "isotopic_pattern_score": score,
        "isotope_label": isotope_label,
        "other_candidates": "",
        "mz_error_ppm": 0.0,
        "intensity_error": 0.0,
    }


def _assign(matches, peaks, minor=frozenset({"+NH4+"})):
    return untargeted_matches_to_peak_assignments(
        pd.DataFrame(matches),
        peaks_df=peaks,
        sample_item_id="si-1",
        peak_assignment_run_id="run-1",
        candidate_threshold=0.45,
        assigned_threshold=0.75,
        mechanism_id_by_notation=MECHANISM_IDS,
        max_alternatives=5,
        minor_channels=minor,
    )


class TestTheCrossCompositionContest:
    """Two compositions landing on one observed peak, where an opportunistic
    channel loses a tie.

    Which READING of a peak wins - the same ion split as X.[M+NH4]+ or as
    (X+NH3).[M+H]+ - is the same-ion policy's decision in the finder, not this
    rule's, and the two must not re-rank each other."""

    def test_a_tie_goes_to_the_modes_own_channel(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.9),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.9),
            ],
            peaks,
        )
        assert [r["assigned_formula"] for r in rows] == ["C6H9NO2"]
        # The loser is kept, so the peak still shows both readings.
        assert rows[0]["alternatives"][0]["assigned_formula"] == "C9H14O3"

    def test_a_secondary_channel_still_wins_on_better_evidence(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.95),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.60),
            ],
            peaks,
        )
        assert [r["assigned_formula"] for r in rows] == ["C9H14O3"]

    def test_without_a_minor_set_the_order_is_unchanged(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.9),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.9),
            ],
            peaks,
            minor=frozenset(),
        )
        assert [r["assigned_formula"] for r in rows] == ["C6H9NO2"]


class TestTheCorroborationCap:
    def test_an_uncorroborated_secondary_winner_is_capped(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.99)], peaks)
        assert rows[0]["tier"] == TIER_CANDIDATE
        assert rows[0]["provenance"]["minor_channel"] == {
            "corroborated_by": None,
            "capped": True,
        }

    def test_a_confirmed_isotopologue_corroborates(self):
        peaks = _peaks(("p1", 200.0, 1.0e6), ("p2", 201.0034, 1.0e5))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.99),
                _match(201.0034, "C9H14O3", "C9H13O3+", "+NH4+", 0.99, "13C"),
            ],
            peaks,
        )
        m0 = [r for r in rows if r["role"] == "M0"][0]
        assert m0["tier"] == TIER_ASSIGNED
        assert m0["provenance"]["minor_channel"]["corroborated_by"] == "isotopologue"

    def test_the_same_neutral_on_a_primary_channel_corroborates(self):
        # Two different peaks: the secondary channel owns one that no primary
        # contender reached, and the same neutral won another through +H+.
        peaks = _peaks(("p1", 200.0, 1.0e6), ("p2", 300.0, 5.0e5))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.99),
                _match(300.0, "C9H14O3", "C9H15O3+", "+H+", 0.99),
            ],
            peaks,
        )
        minor = [r for r in rows if r["ionization_mechanism_id"] == "im-nh4"][0]
        assert minor["tier"] == TIER_ASSIGNED
        assert (
            minor["provenance"]["minor_channel"]["corroborated_by"] == "second_channel"
        )

    def test_a_primary_channel_row_carries_no_minor_verdict(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.99)], peaks)
        assert rows[0]["tier"] == TIER_ASSIGNED
        assert "minor_channel" not in rows[0]["provenance"]

    def test_the_cap_never_promotes(self):
        # A row that was already below the assigned band stays where it is; the
        # rule only demotes.
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.50)], peaks)
        assert rows[0]["tier"] == TIER_CANDIDATE
        assert rows[0]["provenance"]["minor_channel"]["capped"] is False


class TestThePartnerGate:
    """An opportunistic reading of an ion the mode's own channel also reads
    stands only where the sample commits its neutral through a mode channel.

    The finder's election prefers the heavier mechanism, which is the
    opportunistic channel's every time; what tells the C11 acid the chamber
    does not contain from the C10 product it does, and protonated C7H6 from
    toluene less a hydride, is whether the sample shows the neutral elsewhere.
    """

    IDS = {"+NO3-": "im-no3", "-H+": "im-deprot", "+HCOO-": "im-formate"}
    CT_IDS = {"+": "im-ct", "+H+": "im-h", "-H-": "im-hydride"}

    BANDS = {TIER_ASSIGNED: 0.75, TIER_CANDIDATE: 0.45}

    def _gate(self, rows, ids, minor, gated):
        apply_partner_gates(
            rows,
            notation_by_id={mid: n for n, mid in ids.items()},
            minor_channels=frozenset(minor),
            partner_gated_channels=frozenset(gated),
            tier_bands=self.BANDS,
        )
        return rows

    def _assign(self, matches, peaks, ids, minor, gated, stage_a=()):
        rows = untargeted_matches_to_peak_assignments(
            pd.DataFrame(matches),
            peaks_df=peaks,
            sample_item_id="si-1",
            peak_assignment_run_id="run-1",
            candidate_threshold=0.45,
            assigned_threshold=0.75,
            mechanism_id_by_notation=ids,
            max_alternatives=5,
            minor_channels=frozenset(minor),
        )
        return self._gate(list(stage_a) + rows, ids, minor, gated)

    @staticmethod
    def _family(*readings):
        return [
            {
                "formula": formula,
                "ion": ion,
                "ionization_mechanism": mechanism,
                "neutral_mass": 0.0,
                "unsaturation": None,
            }
            for formula, ion, mechanism in readings
        ]

    def test_without_a_partner_the_modes_own_reading_is_the_rows(self):
        # 263.1136 reads as C10H18O5 with formate or as C11H20O7 deprotonated;
        # nothing else in the sample shows C10H18O5, so the acid stands, the
        # formate reading stays on the row as displaced, and no cap applies.
        peaks = _peaks(("p1", 263.1136, 1.0e6))
        rows = self._assign(
            [
                {
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "+HCOO-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "-H+")
                    ),
                }
            ],
            peaks,
            self.IDS,
            minor={"+HCOO-"},
            gated={"+HCOO-"},
        )
        [row] = rows
        assert row["assigned_formula"] == "C11H20O7"
        assert row["ionization_mechanism_id"] == "im-deprot"
        assert row["tier"] == TIER_ASSIGNED
        assert "minor_channel" not in row["provenance"]
        gate = row["provenance"]["partner_gate"]
        assert gate["partner"] is False
        assert gate["displaced"] == "C10H18O5" and gate["through"] == "-H+"
        first = row["alternatives"][0]
        assert first["assigned_formula"] == "C10H18O5"
        assert first["same_ion"] is True and first["partner_gate"] == "unmet"

    def test_with_a_partner_the_formate_reading_stands_and_is_corroborated(self):
        peaks = _peaks(("p1", 263.1136, 1.0e6), ("p2", 280.1032, 8.0e5))
        rows = self._assign(
            [
                {
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "+HCOO-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "-H+")
                    ),
                },
                _match(280.1032, "C10H18O5", "C10H18NO8-", "+NO3-", 0.99),
            ],
            peaks,
            self.IDS,
            minor={"+HCOO-"},
            gated={"+HCOO-"},
        )
        formate = [r for r in rows if r["ionization_mechanism_id"] == "im-formate"][0]
        assert formate["assigned_formula"] == "C10H18O5"
        assert formate["tier"] == TIER_ASSIGNED
        assert formate["provenance"]["partner_gate"] == {
            "channel": "+HCOO-",
            "partner": True,
        }
        assert (
            formate["provenance"]["minor_channel"]["corroborated_by"]
            == "second_channel"
        )

    def test_a_reading_with_no_other_reading_is_left_to_the_cap(self):
        peaks = _peaks(("p1", 263.1136, 1.0e6))
        [row] = self._assign(
            [_match(263.1136, "C10H18O5", "C11H19O7-", "+HCOO-", 0.99)],
            peaks,
            self.IDS,
            minor={"+HCOO-"},
            gated={"+HCOO-"},
        )
        assert row["assigned_formula"] == "C10H18O5"
        assert row["tier"] == TIER_CANDIDATE
        assert (
            row["provenance"]["partner_gate"]["kept"] == "no other reading of the ion"
        )
        assert row["provenance"]["minor_channel"]["capped"] is True

    def test_a_partner_a_swap_uncovers_is_seen(self):
        # The C10 acid that partners the C11 pseudo-acid was itself elected as
        # a C9 formate adduct; its own gate turns it back to the acid, and only
        # then is it a partner. One reading of the ledger misses that.
        peaks = _peaks(("p1", 263.1136, 1.0e6), ("p2", 217.1081, 6.0e5))
        rows = self._assign(
            [
                {
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "+HCOO-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "-H+")
                    ),
                },
                {
                    **_match(217.1081, "C9H16O3", "C10H17O5-", "+HCOO-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C10H18O5", "C10H17O5-", "-H+")
                    ),
                },
            ],
            peaks,
            self.IDS,
            minor={"+HCOO-"},
            gated={"+HCOO-"},
        )
        by_peak = {r["sample_peak_id"]: r for r in rows}
        # C9H16O3 has no partner, so p2 is the C10 acid through -H+ ...
        assert by_peak["p2"]["assigned_formula"] == "C10H18O5"
        assert by_peak["p2"]["ionization_mechanism_id"] == "im-deprot"
        # ... which is exactly the partner p1's formate reading needed.
        assert by_peak["p1"]["assigned_formula"] == "C10H18O5"
        assert by_peak["p1"]["ionization_mechanism_id"] == "im-formate"
        assert by_peak["p1"]["provenance"]["partner_gate"]["partner"] is True
        assert by_peak["p1"]["tier"] == TIER_ASSIGNED
        # The acid reading the swap back displaced is one the sample
        # settled, not one it failed to bear out: it stays a rival for the
        # cross-channel pass to record as settled.
        [acid] = by_peak["p1"]["alternatives"]
        assert acid["assigned_formula"] == "C11H20O7"
        assert "partner_gate" not in acid

    def test_a_reference_list_row_is_the_partner_and_lifts_the_cap(self):
        # The C10 product is a Stage A match, not a search row: the policy
        # capped the formate reading for want of a partner among the search's
        # rows, and the gate, reading both stages, lifts it.
        peaks = _peaks(("p1", 263.1136, 1.0e6))
        stage_a = [
            {
                "peak_assignment_id": "a1",
                "role": "M0",
                "assigned_formula": "C10H18O5",
                "ion_formula": "C10H18NO8-",
                "ionization_mechanism_id": "im-no3",
                "tier": TIER_ASSIGNED,
                "source": "database",
                "alternatives": [],
                "provenance": {},
            }
        ]
        rows = self._assign(
            [
                {
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "+HCOO-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "-H+")
                    ),
                }
            ],
            peaks,
            self.IDS,
            minor={"+HCOO-"},
            gated={"+HCOO-"},
            stage_a=stage_a,
        )
        formate = [r for r in rows if r.get("ionization_mechanism_id") == "im-formate"]
        assert formate[0]["assigned_formula"] == "C10H18O5"
        assert formate[0]["tier"] == TIER_ASSIGNED
        assert formate[0]["provenance"]["partner_gate"]["partner"] is True
        assert formate[0]["provenance"]["minor_channel"] == {
            "corroborated_by": "second_channel",
            "capped": False,
        }

    def test_a_reference_list_acid_is_not_doubted_for_an_unmet_formate_reading(self):
        # A mirror row carries the readings the search would have held; the
        # formate one is set aside where nothing shows its neutral, so the
        # cross-channel pass has no rival to cap the acid on.
        mirror = {
            "peak_assignment_id": "a1",
            "role": "M0",
            "assigned_formula": "C11H20O7",
            "ion_formula": "C11H19O7-",
            "ionization_mechanism_id": "im-deprot",
            "tier": TIER_ASSIGNED,
            "source": "database",
            "alternatives": [
                {
                    "assigned_formula": "C10H18O5",
                    "ionization_mechanism_id": "im-formate",
                    "same_ion": True,
                }
            ],
            "provenance": {},
        }
        [row] = self._gate([mirror], self.IDS, minor={"+HCOO-"}, gated={"+HCOO-"})
        assert row["alternatives"][0]["partner_gate"] == "unmet"
        assert row["tier"] == TIER_ASSIGNED

    def test_tropylium_is_toluene_less_a_hydride_where_toluene_is_seen(self):
        # C7H7+ reads as protonated C7H6 or as toluene less a hydride; the
        # election takes the proton, the sample shows toluene through the bare
        # sign, and only the hydride reading has that partner.
        peaks = _peaks(("p1", 91.0542, 1.0e6), ("p2", 92.0621, 3.0e6))
        rows = self._assign(
            [
                {
                    **_match(91.0542, "C7H6", "C7H7+", "+H+", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C7H8", "C7H7+", "-H-"), ("C7H7", "C7H7+", "+")
                    ),
                },
                _match(92.0621, "C7H8", "C7H8+", "+", 0.99),
            ],
            peaks,
            self.CT_IDS,
            minor={"+H+", "-H-"},
            gated={"+H+", "-H-"},
        )
        tropylium = [r for r in rows if r["sample_peak_id"] == "p1"][0]
        assert tropylium["assigned_formula"] == "C7H8"
        assert tropylium["ionization_mechanism_id"] == "im-hydride"
        assert tropylium["tier"] == TIER_ASSIGNED
        assert tropylium["provenance"]["partner_gate"]["through"] == "-H-"
        assert (
            tropylium["provenance"]["minor_channel"]["corroborated_by"]
            == "second_channel"
        )

    def test_an_isotopologue_follows_its_owners_reading(self):
        peaks = _peaks(("p1", 263.1136, 1.0e6), ("p2", 264.1170, 1.1e5))
        rows = self._assign(
            [
                {
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "+HCOO-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "-H+")
                    ),
                },
                _match(264.1170, "C10H18O5", "C11H19O7-", "+HCOO-", 0.99, "13C"),
            ],
            peaks,
            self.IDS,
            minor={"+HCOO-"},
            gated={"+HCOO-"},
        )
        child = [r for r in rows if r["role"] == "iso_child"][0]
        assert child["assigned_formula"] == "C11H20O7"
        assert child["ionization_mechanism_id"] == "im-deprot"

    @staticmethod
    def _row(
        row_id,
        formula,
        mechanism_id,
        *,
        alternatives=(),
        tier=TIER_ASSIGNED,
        capped=None,
        source="untargeted",
    ):
        """A monoisotopic row as a stage built and the policy judged it."""
        provenance = {"plausibility": 1.0, "evidence": 0.99}
        if capped is not None:
            provenance["minor_channel"] = {"corroborated_by": None, "capped": capped}
        return {
            "peak_assignment_id": row_id,
            "role": "M0",
            "sample_peak_id": f"peak-{row_id}",
            "assigned_formula": formula,
            "ion_formula": None,
            "ionization_mechanism_id": mechanism_id,
            "tier": tier,
            "fit_score": 0.99,
            "source": source,
            "alternatives": [
                {
                    "assigned_formula": alt_formula,
                    "ionization_mechanism_id": alt_mechanism,
                    "same_ion": True,
                    "plausibility": 1.0,
                }
                for alt_formula, alt_mechanism in alternatives
            ],
            "provenance": provenance,
        }

    def _formate(self, row_id, formula, acid=None):
        """A formate reading the policy capped, with its acid reading if any."""
        return self._row(
            row_id,
            formula,
            "im-formate",
            tier=TIER_CANDIDATE,
            capped=True,
            alternatives=() if acid is None else ((acid, "im-deprot"),),
        )

    def test_a_reading_with_no_family_is_lifted_by_a_partner_a_swap_makes(self):
        # r1's ion reads no other way, so the first round leaves it to the
        # cap; r2's own gate then turns r2 into the C10 acid, which is exactly
        # r1's partner, and r1 is judged again.
        r1 = self._formate("r1", "C10H18O5")
        r2 = self._formate("r2", "C9H16O3", acid="C10H18O5")
        self._gate([r1, r2], self.IDS, minor={"+HCOO-"}, gated={"+HCOO-"})
        assert (r2["assigned_formula"], r2["ionization_mechanism_id"]) == (
            "C10H18O5",
            "im-deprot",
        )
        assert r1["provenance"]["partner_gate"] == {
            "channel": "+HCOO-",
            "partner": True,
            "uncapped": True,
        }
        assert r1["tier"] == TIER_ASSIGNED
        assert r1["provenance"]["minor_channel"] == {
            "corroborated_by": "second_channel",
            "capped": False,
        }

    def test_the_walk_says_when_its_rounds_ran_out(self, monkeypatch):
        monkeypatch.setattr(engine_module, "MAX_PARTNER_GATE_ROUNDS", 1)
        r1 = self._formate("r1", "C10H18O5")
        r2 = self._formate("r2", "C9H16O3", acid="C10H18O5")
        summary = apply_partner_gates(
            [r1, r2],
            notation_by_id={mid: n for n, mid in self.IDS.items()},
            minor_channels=frozenset({"+HCOO-"}),
            partner_gated_channels=frozenset({"+HCOO-"}),
            tier_bands=self.BANDS,
        )
        assert summary["settled"] is False and summary["rounds"] == 1
        assert r1["provenance"]["partner_gate"]["kept"]

    def test_a_row_whose_partner_a_swap_back_takes_is_judged_again(self):
        # Round one turns r to its acid, which partners s, and t to its acid,
        # which bears out r's set-aside reading. Round two swaps r back, and
        # that takes away the C10H18O5 s stood on: s is judged again and
        # reads its acid. Every formate reading left standing has its neutral
        # committed through a mode channel.
        r = self._formate("r", "C9H16O3", acid="C10H18O5")
        s = self._formate("s", "C10H18O5", acid="C11H20O7")
        t = self._formate("t", "C8H14O", acid="C9H16O3")
        summary = apply_partner_gates(
            [r, s, t],
            notation_by_id={mid: n for n, mid in self.IDS.items()},
            minor_channels=frozenset({"+HCOO-"}),
            partner_gated_channels=frozenset({"+HCOO-"}),
            tier_bands=self.BANDS,
        )
        assert (r["assigned_formula"], r["ionization_mechanism_id"]) == (
            "C9H16O3",
            "im-formate",
        )
        assert r["provenance"]["partner_gate"] == {
            "channel": "+HCOO-",
            "partner": True,
            "returned": True,
        }
        assert (s["assigned_formula"], s["ionization_mechanism_id"]) == (
            "C11H20O7",
            "im-deprot",
        )
        assert s["provenance"]["partner_gate"]["displaced"] == "C10H18O5"
        assert (t["assigned_formula"], t["ionization_mechanism_id"]) == (
            "C9H16O3",
            "im-deprot",
        )
        assert summary["settled"] is True
        assert summary["partnered"] == 1 and summary["swapped"] == 2

    def test_a_partner_that_is_gone_re_imposes_the_cap(self):
        # The policy corroborated the formate reading on a search row that the
        # gate does not count as a partner: nothing else reads the ion, so the
        # row is left to the cap, and the cap is put back.
        below = self._row("b", "C10H18O5", "im-deprot", tier="below_assignability")
        formate = self._row("f", "C10H18O5", "im-formate")
        formate["provenance"]["minor_channel"] = {
            "corroborated_by": "second_channel",
            "capped": False,
        }
        self._gate([below, formate], self.IDS, minor={"+HCOO-"}, gated={"+HCOO-"})
        assert formate["tier"] == TIER_CANDIDATE
        assert formate["provenance"]["minor_channel"] == {
            "corroborated_by": None,
            "capped": True,
        }
        assert formate["provenance"]["partner_gate"]["recapped"] is True

    def test_a_partner_is_matched_by_composition_not_spelling(self):
        # A target library holds what a person typed; the gate reads it as
        # the composition it is.
        library = self._row("a1", "CH3COOH", "im-deprot", source="database")
        formate = self._formate("f1", "C2H4O2")
        self._gate([library, formate], self.IDS, minor={"+HCOO-"}, gated={"+HCOO-"})
        assert formate["provenance"]["partner_gate"]["partner"] is True
        assert formate["tier"] == TIER_ASSIGNED

    def test_a_swapped_reading_stays_under_the_mass_gates_ceiling(self):
        # The mass gate judged the ion's line, which the acid reading shares.
        # The policy held the row at candidate, so the gate recorded the
        # ceiling it would have held the row to, and lowered nothing.
        row = self._formate("r", "C10H18O5", acid="C11H20O7")
        row["provenance"]["mass_gate"] = {
            "corroborated_by": None,
            "ceiling": TIER_CANDIDATE,
            "reason": "off_calibration",
        }
        self._gate([row], self.IDS, minor={"+HCOO-"}, gated={"+HCOO-"})
        assert row["assigned_formula"] == "C11H20O7"
        assert row["tier"] == TIER_CANDIDATE
        # ...and the row now says the mass gate holds it there.
        assert row["provenance"]["mass_gate"]["capped"] == TIER_CANDIDATE

    def test_a_lifted_cap_stays_under_the_mass_gates_ceiling(self):
        partner = self._row("p", "C10H18O5", "im-deprot")
        formate = self._formate("f", "C10H18O5")
        formate["provenance"]["mass_gate"] = {
            "corroborated_by": None,
            "ceiling": TIER_CANDIDATE,
            "reason": "off_calibration",
        }
        summary = apply_partner_gates(
            [partner, formate],
            notation_by_id={mid: n for n, mid in self.IDS.items()},
            minor_channels=frozenset({"+HCOO-"}),
            partner_gated_channels=frozenset({"+HCOO-"}),
            tier_bands=self.BANDS,
        )
        assert formate["provenance"]["partner_gate"]["uncapped"] is True
        assert formate["provenance"]["minor_channel"]["capped"] is False
        assert formate["tier"] == TIER_CANDIDATE
        assert formate["provenance"]["mass_gate"]["capped"] == TIER_CANDIDATE
        assert summary["held"] == 1


class TestResolution:
    def _resolved(self) -> ResolvedProfile:
        return resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        )

    def test_a_channel_the_spectrum_shows_and_the_deployment_has_is_searched(self):
        resolved = with_secondary_channels(
            self._resolved(),
            [100.0, 138.0986],
            [1.0e6, 1.0e4],
            ["+NH4+"],
        )
        assert resolved.minor_channels == frozenset({"+NH4+"})
        assert resolved.unavailable_channels == ()

    def test_a_channel_the_spectrum_does_not_show_is_not_searched(self):
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 200.0], [1.0e6, 1.0e4], ["+NH4+"]
        )
        assert resolved.minor_channels == frozenset()

    def test_a_channel_the_deployment_cannot_express_is_reported_not_searched(self):
        # Different from "the source does not run it", and only this one is
        # worth fixing by configuration - so the run says which it was.
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], []
        )
        assert resolved.minor_channels == frozenset()
        assert resolved.unavailable_channels == ("+NH4+",)

    def test_a_channel_the_mode_declares_is_still_secondary(self):
        # Declaring a channel lets the run search and match through it; it does
        # not make an opportunistic reagent the mode's own. So it is capped and
        # loses a tie as a secondary channel does, and is not added to the
        # search again, since the mode's own mechanism already searches it.
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(),
                UREA + ["+NH4+"],
                instrument_type="orbi",
                polarity="+",
            ),
            [100.0, 138.0986],
            [1.0e6, 1.0e4],
            ["+NH4+"],
        )
        assert resolved.minor_channels == frozenset({"+NH4+"})
        assert resolved.added_channels == frozenset()
        assert resolved.snapshot()["secondary_channels"] == ["+NH4+"]

    def test_so_is_one_whose_carrier_the_spectrum_does_not_show(self):
        # The mode searches it either way, so the spectrum's silence cannot make
        # a reading through it any less opportunistic.
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(),
                UREA + ["+NH4+"],
                instrument_type="orbi",
                polarity="+",
            ),
            [50.0, 100.0, 400.0],
            [1.0e6, 1.0e5, 1.0e4],
            ["+NH4+"],
        )
        assert resolved.channel_evidence[0].present is False
        assert resolved.minor_channels == frozenset({"+NH4+"})
        assert resolved.added_channels == frozenset()

    def test_carbonate_on_a_labelled_nitrate_mode_that_declares_it(self):
        # A window starting above every carbonate line cannot show the channel,
        # and the nitrate profiles take that silence as no evidence either way.
        declared = ["+^NO3-", "-H+", "+CO3-"]
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(profile="NO3_15N"),
                declared,
                instrument_type="orbi",
                polarity="-",
            ),
            [131.0, 210.0898, 300.0],
            [1.0e4, 1.0e6, 1.0e5],
            ["+CO3-"],
        )
        assert resolved.channel_evidence[0].status == "unobservable"
        assert resolved.minor_channels == frozenset({"+CO3-"})
        assert resolved.added_channels == frozenset()

    def test_a_declared_channel_the_profile_does_not_name_is_the_modes_own(self):
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], ["+NH4+"]
        )
        assert resolved.minor_channels == frozenset({"+NH4+"})
        assert resolved.added_channels == frozenset({"+NH4+"})

    def test_a_profile_with_no_secondary_channels_is_untouched(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="none"), UREA, instrument_type="orbi"
        )
        extended = with_secondary_channels(
            resolved, [100.0, 138.0986], [1.0e6, 1.0e4], ["+NH4+"]
        )
        assert extended is resolved
        assert extended.minor_channels == frozenset()

    def test_the_snapshot_records_every_channel_considered(self):
        snapshot = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], ["+NH4+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == ["+NH4+"]
        assert snapshot["channel_evidence"][0]["channel"] == "+NH4+"
        assert snapshot["channel_evidence"][0]["present"] is True
        assert snapshot["unavailable_channels"] == []

    def test_the_snapshot_of_a_run_that_opened_nothing_says_so(self):
        # A spectrum that spans the probes and does not carry them: the source
        # is not running the channel, which is a real answer.
        snapshot = with_secondary_channels(
            self._resolved(), [50.0, 100.0, 400.0], [1.0e6, 1.0e5, 1.0e4], ["+NH4+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == []
        assert snapshot["channel_evidence"] == [
            {"channel": "+NH4+", "present": False, "status": "not_found"}
        ]

    def test_a_channel_the_window_could_not_show_is_recorded_as_such(self):
        # And is not searched, because the urea profile does not default it on.
        snapshot = with_secondary_channels(
            self._resolved(), [300.0, 400.0], [1.0e6, 1.0e4], ["+NH4+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == []
        assert snapshot["channel_evidence"] == [
            {"channel": "+NH4+", "present": False, "status": "unobservable"}
        ]


@pytest.mark.parametrize("profile,expected", [("BR", 2), ("NO3", 1), ("UR", 1)])
def test_the_gate_sets_profiles_declare_their_channels(profile, expected):
    from mascope_tools.composition.reagents import secondary_channels

    assert len(secondary_channels(profile)) == expected


def _mechanism(mechanism_id: str, notation: str) -> SimpleNamespace:
    return SimpleNamespace(
        ionization_mechanism_id=mechanism_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity="+",
    )


class TestTheChannelsASampleIsSearchedThrough:
    PROTON = _mechanism("im-h", "+H+")
    UREA_ADDUCT = _mechanism("im-urea", "+(CH4N2O)H+")
    AMMONIUM = _mechanism("im-nh4", "+NH4+")
    #: The ammonium carrier beside a base line, so the urea profile opens +NH4+.
    SHOWS_AMMONIUM = ([100.0, 138.0986], [1.0e6, 1.0e4])

    def _resolved(self, declared: list[str], spectrum=SHOWS_AMMONIUM):
        return with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(), declared, instrument_type="orbi", polarity="+"
            ),
            *spectrum,
            ["+NH4+"],
        )

    def test_a_secondary_channel_the_source_runs_is_added(self):
        searched = _searched_mechanisms(
            [self.PROTON, self.UREA_ADDUCT], [self.AMMONIUM], self._resolved(UREA)
        )
        assert [m.ionization_mechanism_id for m in searched] == [
            "im-h",
            "im-urea",
            "im-nh4",
        ]

    def test_one_the_mode_declares_itself_is_searched_once(self):
        # Searched twice, it proposes every neutral through it twice, and the
        # election keeps the twin as another reading of the row's own ion.
        searched = _searched_mechanisms(
            [self.PROTON, self.UREA_ADDUCT, self.AMMONIUM],
            [self.AMMONIUM],
            self._resolved(UREA + ["+NH4+"]),
        )
        assert [m.ionization_mechanism_id for m in searched] == [
            "im-h",
            "im-urea",
            "im-nh4",
        ]

    def test_one_the_source_does_not_run_is_left_out(self):
        searched = _searched_mechanisms(
            [self.PROTON, self.UREA_ADDUCT],
            [self.AMMONIUM],
            self._resolved(UREA, spectrum=([100.0, 200.0], [1.0e6, 1.0e4])),
        )
        assert [m.ionization_mechanism_id for m in searched] == ["im-h", "im-urea"]

    def test_a_mode_that_declares_nothing_searches_nothing(self):
        assert _searched_mechanisms([], [self.AMMONIUM], self._resolved([])) == []
