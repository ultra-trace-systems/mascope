"""How an opportunistic secondary channel is treated once it is searched.

The fingerprint that decides whether a channel is searched at all is covered in
``libraries/tools``. What is pinned here is what the engine does with a channel
it opened for itself: it must not let one win a peak the mode's own chemistry
explains equally well, and it must not let one hand out the ledger's strongest
word on its own authority.
"""

import itertools
from types import SimpleNamespace

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import engine as engine_module
from mascope_backend.api.new.peak_assignments import service as service_module
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
from mascope_backend.api.new.peak_assignments.service import (
    _notation_by_id,
    _readable,
    _searched_mechanisms,
    _untargeted_ionization_notations,
    _without_unreadable,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
)


MECHANISM_IDS = {"[M+H]+": "im-h", "[M+NH4]+": "im-nh4"}
UREA = ["[M+H]+", "[M+CH4N2O+H]+"]


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


def _assign(matches, peaks, minor=frozenset({"[M+NH4]+"})):
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
                _match(200.0, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.9),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "[M+H]+", 0.9),
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
                _match(200.0, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.95),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "[M+H]+", 0.60),
            ],
            peaks,
        )
        assert [r["assigned_formula"] for r in rows] == ["C9H14O3"]

    def test_without_a_minor_set_the_order_is_unchanged(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.9),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "[M+H]+", 0.9),
            ],
            peaks,
            minor=frozenset(),
        )
        assert [r["assigned_formula"] for r in rows] == ["C6H9NO2"]


class TestTheCorroborationCap:
    def test_an_uncorroborated_secondary_winner_is_capped(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.99)], peaks)
        assert rows[0]["tier"] == TIER_CANDIDATE
        assert rows[0]["provenance"]["minor_channel"] == {
            "corroborated_by": None,
            "capped": True,
        }

    def test_a_confirmed_isotopologue_corroborates(self):
        peaks = _peaks(("p1", 200.0, 1.0e6), ("p2", 201.0034, 1.0e5))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.99),
                _match(201.0034, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.99, "13C"),
            ],
            peaks,
        )
        m0 = [r for r in rows if r["role"] == "M0"][0]
        assert m0["tier"] == TIER_ASSIGNED
        assert m0["provenance"]["minor_channel"]["corroborated_by"] == "isotopologue"

    def test_the_same_neutral_on_a_primary_channel_corroborates(self):
        # Two different peaks: the secondary channel owns one that no primary
        # contender reached, and the same neutral won another through [M+H]+.
        peaks = _peaks(("p1", 200.0, 1.0e6), ("p2", 300.0, 5.0e5))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.99),
                _match(300.0, "C9H14O3", "C9H15O3+", "[M+H]+", 0.99),
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
        rows = _assign([_match(200.0, "C6H9NO2", "C6H10NO2+", "[M+H]+", 0.99)], peaks)
        assert rows[0]["tier"] == TIER_ASSIGNED
        assert "minor_channel" not in rows[0]["provenance"]

    def test_the_cap_never_promotes(self):
        # A row that was already below the assigned band stays where it is; the
        # rule only demotes.
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C9H14O3", "C9H13O3+", "[M+NH4]+", 0.50)], peaks)
        assert rows[0]["tier"] == TIER_CANDIDATE
        assert rows[0]["provenance"]["minor_channel"]["capped"] is False


#: A charge-transfer source: electron transfer, and proton transfer and hydride
#: abstraction beside it.
CT_IDS = {"[M]+.": "im-ct", "[M+H]+": "im-h", "[M-H]+": "im-hydride"}

BANDS = {TIER_ASSIGNED: 0.75, TIER_CANDIDATE: 0.45}


def ledger_row(
    row_id,
    formula,
    mechanism_id,
    *,
    alternatives=(),
    tier=TIER_ASSIGNED,
    capped=None,
    source="untargeted",
    intensity=1.0e5,
):
    """A monoisotopic row as a stage built and the policy judged it."""
    provenance = {"plausibility": 1.0, "evidence": 0.99}
    if capped is not None:
        provenance["minor_channel"] = {"corroborated_by": None, "capped": capped}
    return {
        "peak_assignment_id": row_id,
        "role": "M0",
        "sample_peak_id": f"peak-{row_id}",
        "sample_peak_intensity": intensity,
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


class TestThePartnerGate:
    """An opportunistic reading of an ion the mode's own channel also reads
    stands only where the sample commits its neutral through a mode channel.

    The finder's election prefers the heavier mechanism, which is the
    opportunistic channel's every time; what tells the C11 acid the chamber
    does not contain from the C10 product it does, and protonated C7H6 from
    toluene less a hydride, is whether the sample shows the neutral elsewhere.
    """

    IDS = {"[M+NO3]-": "im-no3", "[M-H]-": "im-deprot", "[M+HCOO]-": "im-formate"}

    def _gate(self, rows, ids, minor, gated):
        apply_partner_gates(
            rows,
            notation_by_id={mid: n for n, mid in ids.items()},
            minor_channels=frozenset(minor),
            partner_gated_channels=frozenset(gated),
            tier_bands=BANDS,
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
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "[M+HCOO]-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "[M-H]-")
                    ),
                }
            ],
            peaks,
            self.IDS,
            minor={"[M+HCOO]-"},
            gated={"[M+HCOO]-"},
        )
        [row] = rows
        assert row["assigned_formula"] == "C11H20O7"
        assert row["ionization_mechanism_id"] == "im-deprot"
        assert row["tier"] == TIER_ASSIGNED
        assert "minor_channel" not in row["provenance"]
        gate = row["provenance"]["partner_gate"]
        assert gate["partner"] is False
        assert gate["displaced"] == "C10H18O5" and gate["through"] == "[M-H]-"
        first = row["alternatives"][0]
        assert first["assigned_formula"] == "C10H18O5"
        assert first["same_ion"] is True and first["partner_gate"] == "unmet"

    def test_with_a_partner_the_formate_reading_stands_and_is_corroborated(self):
        peaks = _peaks(("p1", 263.1136, 1.0e6), ("p2", 280.1032, 8.0e5))
        rows = self._assign(
            [
                {
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "[M+HCOO]-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "[M-H]-")
                    ),
                },
                _match(280.1032, "C10H18O5", "C10H18NO8-", "[M+NO3]-", 0.99),
            ],
            peaks,
            self.IDS,
            minor={"[M+HCOO]-"},
            gated={"[M+HCOO]-"},
        )
        formate = [r for r in rows if r["ionization_mechanism_id"] == "im-formate"][0]
        assert formate["assigned_formula"] == "C10H18O5"
        assert formate["tier"] == TIER_ASSIGNED
        assert formate["provenance"]["partner_gate"] == {
            "channel": "[M+HCOO]-",
            "partner": True,
        }
        assert (
            formate["provenance"]["minor_channel"]["corroborated_by"]
            == "second_channel"
        )

    def test_a_reading_with_no_other_reading_is_left_to_the_cap(self):
        peaks = _peaks(("p1", 263.1136, 1.0e6))
        [row] = self._assign(
            [_match(263.1136, "C10H18O5", "C11H19O7-", "[M+HCOO]-", 0.99)],
            peaks,
            self.IDS,
            minor={"[M+HCOO]-"},
            gated={"[M+HCOO]-"},
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
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "[M+HCOO]-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "[M-H]-")
                    ),
                },
                {
                    **_match(217.1081, "C9H16O3", "C10H17O5-", "[M+HCOO]-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C10H18O5", "C10H17O5-", "[M-H]-")
                    ),
                },
            ],
            peaks,
            self.IDS,
            minor={"[M+HCOO]-"},
            gated={"[M+HCOO]-"},
        )
        by_peak = {r["sample_peak_id"]: r for r in rows}
        # C9H16O3 has no partner, so p2 is the C10 acid through [M-H]- ...
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
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "[M+HCOO]-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "[M-H]-")
                    ),
                }
            ],
            peaks,
            self.IDS,
            minor={"[M+HCOO]-"},
            gated={"[M+HCOO]-"},
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
        [row] = self._gate([mirror], self.IDS, minor={"[M+HCOO]-"}, gated={"[M+HCOO]-"})
        assert row["alternatives"][0]["partner_gate"] == "unmet"
        assert row["tier"] == TIER_ASSIGNED

    def test_tropylium_is_toluene_less_a_hydride_where_toluene_is_seen(self):
        # C7H7+ reads as protonated C7H6 or as toluene less a hydride; the
        # election takes the proton, the sample shows toluene through electron
        # transfer, and only the hydride reading has that partner.
        peaks = _peaks(("p1", 91.0542, 1.0e6), ("p2", 92.0621, 3.0e6))
        rows = self._assign(
            [
                {
                    **_match(91.0542, "C7H6", "C7H7+", "[M+H]+", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C7H8", "C7H7+", "[M-H]+"), ("C7H7", "C7H7+", "[M]+.")
                    ),
                },
                _match(92.0621, "C7H8", "C7H8+", "[M]+.", 0.99),
            ],
            peaks,
            CT_IDS,
            minor={"[M+H]+", "[M-H]+"},
            gated={"[M+H]+", "[M-H]+"},
        )
        tropylium = [r for r in rows if r["sample_peak_id"] == "p1"][0]
        assert tropylium["assigned_formula"] == "C7H8"
        assert tropylium["ionization_mechanism_id"] == "im-hydride"
        assert tropylium["tier"] == TIER_ASSIGNED
        assert tropylium["provenance"]["partner_gate"]["through"] == "[M-H]+"
        assert (
            tropylium["provenance"]["minor_channel"]["corroborated_by"]
            == "second_channel"
        )

    def test_an_isotopologue_follows_its_owners_reading(self):
        peaks = _peaks(("p1", 263.1136, 1.0e6), ("p2", 264.1170, 1.1e5))
        rows = self._assign(
            [
                {
                    **_match(263.1136, "C10H18O5", "C11H19O7-", "[M+HCOO]-", 0.99),
                    "same_ion_alternatives": self._family(
                        ("C11H20O7", "C11H19O7-", "[M-H]-")
                    ),
                },
                _match(264.1170, "C10H18O5", "C11H19O7-", "[M+HCOO]-", 0.99, "13C"),
            ],
            peaks,
            self.IDS,
            minor={"[M+HCOO]-"},
            gated={"[M+HCOO]-"},
        )
        child = [r for r in rows if r["role"] == "iso_child"][0]
        assert child["assigned_formula"] == "C11H20O7"
        assert child["ionization_mechanism_id"] == "im-deprot"

    _row = staticmethod(ledger_row)

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
        self._gate([r1, r2], self.IDS, minor={"[M+HCOO]-"}, gated={"[M+HCOO]-"})
        assert (r2["assigned_formula"], r2["ionization_mechanism_id"]) == (
            "C10H18O5",
            "im-deprot",
        )
        assert r1["provenance"]["partner_gate"] == {
            "channel": "[M+HCOO]-",
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
            minor_channels=frozenset({"[M+HCOO]-"}),
            partner_gated_channels=frozenset({"[M+HCOO]-"}),
            tier_bands=BANDS,
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
            minor_channels=frozenset({"[M+HCOO]-"}),
            partner_gated_channels=frozenset({"[M+HCOO]-"}),
            tier_bands=BANDS,
        )
        assert (r["assigned_formula"], r["ionization_mechanism_id"]) == (
            "C9H16O3",
            "im-formate",
        )
        assert r["provenance"]["partner_gate"] == {
            "channel": "[M+HCOO]-",
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
        self._gate([below, formate], self.IDS, minor={"[M+HCOO]-"}, gated={"[M+HCOO]-"})
        assert formate["tier"] == TIER_CANDIDATE
        assert formate["provenance"]["minor_channel"] == {
            "corroborated_by": None,
            "capped": True,
        }
        assert formate["provenance"]["partner_gate"]["recapped"] is True

    @pytest.mark.parametrize(
        "ceiling", [TIER_CANDIDATE, "below_assignability"], ids=["at", "below"]
    )
    def test_a_cap_put_back_is_the_policys_under_the_ceiling(self, ceiling):
        # s stands on the acid r's gate turns r into, which lifts s's cap
        # under the mass gate's ceiling; r then swaps back and takes that
        # partner away. The cap s gets back is the policy's, read off its
        # evidence rather than off the tier the ceiling held the lifted
        # reading at, and the mass gate holds s only where its ceiling is
        # below that cap, as it did before the lift.
        below = ceiling != TIER_CANDIDATE
        r = self._formate("r", "C9H16O3", acid="C10H18O5")
        s = self._formate("s", "C10H18O5")
        s["provenance"]["mass_gate"] = {
            "corroborated_by": None,
            "ceiling": ceiling,
            "reason": "off_calibration",
        }
        if below:
            s["tier"] = s["provenance"]["mass_gate"]["capped"] = ceiling
        t = self._formate("t", "C8H14O", acid="C9H16O3")
        summary = apply_partner_gates(
            [r, s, t],
            notation_by_id={mid: n for n, mid in self.IDS.items()},
            minor_channels=frozenset({"[M+HCOO]-"}),
            partner_gated_channels=frozenset({"[M+HCOO]-"}),
            tier_bands=BANDS,
        )
        assert s["provenance"]["partner_gate"]["recapped"] is True
        assert s["provenance"]["minor_channel"] == {
            "corroborated_by": None,
            "capped": True,
        }
        assert s["tier"] == ceiling
        assert s["provenance"]["mass_gate"].get("capped") == (
            ceiling if below else None
        )
        assert summary["held"] == 0

    def test_a_reading_the_ledger_bears_out_is_not_left_set_aside(self):
        # Formate and acetate gated beside a third opportunistic channel that
        # is not, on an ion no mode channel reads as a molecule. r takes the
        # acetate reading, turns back to formate when q's swap bears that
        # out, and ends on the propionate reading when q turns back and takes
        # the partner away. The acetate reading its swap back set aside is
        # borne out all along, so it is a reading of the ion again, which the
        # cross-channel pass weighs; the formate one is not, and stays aside.
        ids = {
            "[M-H]-": "im-deprot",
            "[M+HCOO]-": "im-formate",
            "[M+CH3COO]-": "im-acetate",
            "[M+C2H5COO]-": "im-propionate",
        }
        r = self._row(
            "r",
            "C10H18O5",
            "im-formate",
            alternatives=(("C9H16O5", "im-acetate"), ("C8H14O5", "im-propionate")),
        )
        q = self._row(
            "q", "C9H16O3", "im-formate", alternatives=(("C10H18O5", "im-deprot"),)
        )
        t = self._row(
            "t", "C8H14O", "im-formate", alternatives=(("C9H16O3", "im-deprot"),)
        )
        s = self._row(
            "s",
            "C7H12O3",
            "im-formate",
            alternatives=(("C8H14O5", "im-deprot"),),
            intensity=1.0e6,
        )
        acetate_partner = self._row("p", "C9H16O5", "im-deprot", intensity=3.0e5)
        summary = apply_partner_gates(
            [r, q, t, s, acetate_partner],
            notation_by_id={mid: n for n, mid in ids.items()},
            minor_channels=frozenset({"[M+HCOO]-", "[M+CH3COO]-", "[M+C2H5COO]-"}),
            partner_gated_channels=frozenset({"[M+HCOO]-", "[M+CH3COO]-"}),
            tier_bands=BANDS,
        )
        assert (r["assigned_formula"], r["ionization_mechanism_id"]) == (
            "C8H14O5",
            "im-propionate",
        )
        assert {
            alt["assigned_formula"]: alt.get("partner_gate")
            for alt in r["alternatives"]
        } == {"C10H18O5": "unmet", "C9H16O5": None}
        assert summary["set_aside"] == 3

    def test_a_partner_is_matched_by_composition_not_spelling(self):
        # A target library holds what a person typed; the gate reads it as
        # the composition it is.
        library = self._row("a1", "CH3COOH", "im-deprot", source="database")
        formate = self._formate("f1", "C2H4O2")
        self._gate(
            [library, formate], self.IDS, minor={"[M+HCOO]-"}, gated={"[M+HCOO]-"}
        )
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
        self._gate([row], self.IDS, minor={"[M+HCOO]-"}, gated={"[M+HCOO]-"})
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
            minor_channels=frozenset({"[M+HCOO]-"}),
            partner_gated_channels=frozenset({"[M+HCOO]-"}),
            tier_bands=BANDS,
        )
        assert formate["provenance"]["partner_gate"]["uncapped"] is True
        assert formate["provenance"]["minor_channel"]["capped"] is False
        assert formate["tier"] == TIER_CANDIDATE
        assert formate["provenance"]["mass_gate"]["capped"] == TIER_CANDIDATE
        assert summary["held"] == 1


class TestTheStrongerPartnerDecides:
    """Two opportunistic readings of one ion, each with a partner.

    The benzyl cation at 91.054 is protonated C7H6 or toluene less a hydride,
    and on the certified cylinder the sample commits both molecules through
    electron transfer: C7H6 faintly, toluene at 27 to 31 times its height. The
    election's prior for the heavier mechanism takes the proton; the sample
    says toluene, and how strongly it says so decides whether the other
    reading is still a doubt. The contest is read once, after the walk, on
    the partners' peak heights.
    """

    OPENED = frozenset({"[M+H]+", "[M-H]+"})

    def _gate(self, rows, *, opened=OPENED):
        return apply_partner_gates(
            rows,
            notation_by_id={mid: n for n, mid in CT_IDS.items()},
            minor_channels=opened,
            partner_gated_channels=opened,
            tier_bands=BANDS,
        )

    @staticmethod
    def _benzyl(elected="C7H6", mechanism="im-h", other=("C7H8", "im-hydride")):
        """The benzyl cation as the election or a stage left it."""
        return ledger_row(
            "benzyl", elected, mechanism, alternatives=(other,), intensity=4.0e5
        )

    @staticmethod
    def _seen(row_id, formula, intensity, tier=TIER_ASSIGNED):
        """A molecule the sample commits through electron transfer."""
        return ledger_row(row_id, formula, "im-ct", tier=tier, intensity=intensity)

    def test_the_brighter_partner_takes_the_ion_and_settles_it(self):
        benzyl = self._benzyl()
        summary = self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e5),
                self._seen("tol", "C7H8", 3.0e6),
            ]
        )
        assert (benzyl["assigned_formula"], benzyl["ionization_mechanism_id"]) == (
            "C7H8",
            "im-hydride",
        )
        # The ion's fit stays, and the tier is the new reading's own.
        assert benzyl["tier"] == TIER_ASSIGNED
        assert benzyl["provenance"]["minor_channel"] == {
            "corroborated_by": "second_channel",
            "capped": False,
        }
        assert benzyl["provenance"]["partner_gate"] == {
            "channel": "[M-H]+",
            "partner": True,
            "took_from": "C7H6",
            "contest": [
                {"reading": "C7H6", "via": "[M+H]+", "ratio": 30.0, "decisive": True}
            ],
        }
        # The sample did bear the proton's reading out: outweighed, not unmet.
        [proton] = benzyl["alternatives"]
        assert proton["assigned_formula"] == "C7H6"
        assert proton["partner_gate"] == "outweighed"
        assert {
            key: summary[key]
            for key in (
                "contested",
                "contest_swapped",
                "outweighed",
                "within_margin",
                "margin",
            )
        } == {
            "contested": 1,
            "contest_swapped": 1,
            "outweighed": 1,
            "within_margin": 0,
            "margin": 10.0,
        }

    def test_a_partners_tier_is_its_bar_and_not_its_weight(self):
        # C7H6's row is a candidate and twenty times as bright as toluene's
        # assigned one. A tier read at the gate is not the tier the row ends
        # on, and a contest that weighed it would corroborate the partner it
        # chose: the peak decides.
        benzyl = self._benzyl()
        self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e6, tier=TIER_CANDIDATE),
                self._seen("tol", "C7H8", 5.0e4),
            ]
        )
        assert benzyl["assigned_formula"] == "C7H6"
        assert benzyl["provenance"]["partner_gate"]["contest"] == [
            {"reading": "C7H8", "via": "[M-H]+", "ratio": 20.0, "decisive": True}
        ]
        assert benzyl["alternatives"][0]["partner_gate"] == "outweighed"

    def test_a_row_under_the_candidate_bar_is_no_partner(self):
        benzyl = self._benzyl()
        summary = self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e5),
                self._seen("tol", "C7H8", 3.0e6, tier="below_assignability"),
            ]
        )
        assert benzyl["assigned_formula"] == "C7H6"
        assert "contest" not in benzyl["provenance"]["partner_gate"]
        assert benzyl["alternatives"][0]["partner_gate"] == "unmet"
        assert summary["contested"] == 0

    def test_within_the_margin_the_other_reading_is_left_a_rival(self):
        # C5H7+ at 67.054: isoprene is seen seven times as strongly as C5H6,
        # the better reading and not a certain one.
        c5h7 = self._benzyl(elected="C5H6", other=("C5H8", "im-hydride"))
        summary = self._gate(
            [c5h7, self._seen("c5h6", "C5H6", 1.0e5), self._seen("iso", "C5H8", 7.0e5)]
        )
        assert (c5h7["assigned_formula"], c5h7["ionization_mechanism_id"]) == (
            "C5H8",
            "im-hydride",
        )
        assert c5h7["provenance"]["partner_gate"]["contest"] == [
            {"reading": "C5H6", "via": "[M+H]+", "ratio": 7.0, "decisive": False}
        ]
        [proton] = c5h7["alternatives"]
        assert "partner_gate" not in proton
        assert (summary["outweighed"], summary["within_margin"]) == (0, 1)

    @pytest.mark.parametrize(
        "isoprene, ratio, decisive",
        [(1.0e6, 10.0, True), (9.999e5, 9.99, False), (9.99999999e5, 9.99, False)],
    )
    def test_the_margin_is_ten_times_and_reads_as_it_is_decided(
        self, isoprene, ratio, decisive
    ):
        # The record rounds down and decides on what it records, so a ratio
        # short of the margin never reads as the margin.
        assert engine_module.PARTNER_MARGIN == 10.0
        c5h7 = self._benzyl(elected="C5H6", other=("C5H8", "im-hydride"))
        self._gate(
            [
                c5h7,
                self._seen("c5h6", "C5H6", 1.0e5),
                self._seen("iso", "C5H8", isoprene),
            ]
        )
        [entry] = c5h7["provenance"]["partner_gate"]["contest"]
        assert (entry["ratio"], entry["decisive"]) == (ratio, decisive)

    def test_a_row_holding_the_stronger_reading_keeps_it_and_says_so(self):
        benzyl = self._benzyl(
            elected="C7H8", mechanism="im-hydride", other=("C7H6", "im-h")
        )
        summary = self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e5),
                self._seen("tol", "C7H8", 3.0e6),
            ]
        )
        assert benzyl["assigned_formula"] == "C7H8"
        gate = benzyl["provenance"]["partner_gate"]
        assert "took_from" not in gate
        assert gate["contest"][0]["decisive"] is True
        assert benzyl["alternatives"][0]["partner_gate"] == "outweighed"
        assert (summary["contested"], summary["contest_swapped"]) == (1, 0)

    def test_a_tie_leaves_the_row_its_reading(self):
        benzyl = self._benzyl()
        summary = self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e5),
                self._seen("tol", "C7H8", 1.0e5),
            ]
        )
        assert benzyl["assigned_formula"] == "C7H6"
        gate = benzyl["provenance"]["partner_gate"]
        assert "took_from" not in gate
        assert gate["contest"] == [
            {"reading": "C7H8", "via": "[M-H]+", "ratio": 1.0, "decisive": False}
        ]
        assert summary["contest_swapped"] == 0

    def test_the_brightest_partner_is_the_one_weighed(self):
        # Two rows commit C7H6 through electron transfer: the brighter one is its
        # partner, and toluene has to be ten times that one.
        benzyl = self._benzyl()
        self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e4),
                self._seen("c7h6-2", "C7H6", 5.0e5),
                self._seen("tol", "C7H8", 3.0e6),
            ]
        )
        [entry] = benzyl["provenance"]["partner_gate"]["contest"]
        assert (entry["ratio"], entry["decisive"]) == (6.0, False)

    def test_a_partner_a_swap_makes_is_weighed(self):
        # Toluene's row was elected through proton transfer as a reading
        # nothing bears out, so the walk turns it to toluene through electron
        # transfer; the contest reads the ledger the walk settled on.
        toluene = ledger_row(
            "tol",
            "C7H7",
            "im-h",
            alternatives=(("C7H8", "im-ct"),),
            intensity=3.0e6,
        )
        benzyl = self._benzyl()
        summary = self._gate([benzyl, self._seen("c7h6", "C7H6", 1.0e5), toluene])
        assert (toluene["assigned_formula"], toluene["ionization_mechanism_id"]) == (
            "C7H8",
            "im-ct",
        )
        assert toluene["provenance"]["partner_gate"] == {
            "channel": "[M+H]+",
            "partner": False,
            "displaced": "C7H7",
            "through": "[M]+.",
        }
        assert benzyl["assigned_formula"] == "C7H8"
        assert benzyl["provenance"]["partner_gate"]["took_from"] == "C7H6"
        assert benzyl["provenance"]["partner_gate"]["contest"][0]["ratio"] == 30.0
        assert summary["settled"] is True

    def test_a_partner_a_swap_takes_away_is_not_weighed(self):
        # Toluene's only partner is a row the walk had turned to toluene
        # through electron transfer, setting aside a reading nothing bore out. The
        # sample bears that reading out, so the row turns back and toluene is
        # left with no partner: against the settled ledger the benzyl cation
        # keeps the reading the sample still shows, and toluene's is set aside.
        partner = ledger_row("tol", "C7H8", "im-ct", intensity=3.0e6)
        partner["provenance"]["partner_gate"] = {
            "channel": "[M+H]+",
            "partner": False,
            "displaced": "C7H7",
            "through": "[M]+.",
        }
        partner["alternatives"] = [
            {
                "assigned_formula": "C7H7",
                "ionization_mechanism_id": "im-h",
                "same_ion": True,
                "plausibility": 1.0,
                "partner_gate": "unmet",
            }
        ]
        benzyl = self._benzyl()
        summary = self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e5),
                partner,
                self._seen("c7h7", "C7H7", 2.0e5),
            ]
        )
        assert (partner["assigned_formula"], partner["ionization_mechanism_id"]) == (
            "C7H7",
            "im-h",
        )
        assert (benzyl["assigned_formula"], benzyl["ionization_mechanism_id"]) == (
            "C7H6",
            "im-h",
        )
        assert benzyl["provenance"]["partner_gate"] == {
            "channel": "[M+H]+",
            "partner": True,
        }
        assert benzyl["alternatives"][0]["assigned_formula"] == "C7H8"
        assert benzyl["alternatives"][0]["partner_gate"] == "unmet"
        assert (summary["contested"], summary["settled"]) == (0, True)

    def test_a_set_aside_reading_the_ledger_bears_out_is_weighed(self):
        # Nothing shows C7H6 when the benzyl cation is first judged, so it
        # takes toluene's reading and sets the proton's aside. A later swap
        # gives C7H6 a partner - a faint one - and the row turns back to it;
        # the contest then weighs the two, and toluene's partner is brighter.
        benzyl = self._benzyl()
        c7h6 = ledger_row(
            "c7h6", "C7H5", "im-h", alternatives=(("C7H6", "im-ct"),), intensity=1.0e5
        )
        self._gate([benzyl, self._seen("tol", "C7H8", 3.0e6), c7h6])
        assert c7h6["assigned_formula"] == "C7H6"
        assert benzyl["assigned_formula"] == "C7H8"
        assert benzyl["provenance"]["partner_gate"] == {
            "channel": "[M-H]+",
            "partner": True,
            "took_from": "C7H6",
            "contest": [
                {"reading": "C7H6", "via": "[M+H]+", "ratio": 30.0, "decisive": True}
            ],
        }
        assert benzyl["alternatives"][0]["partner_gate"] == "outweighed"

    @pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
    def test_the_row_order_decides_nothing(self, order):
        # The C11 ion reads as C10H18O5 with formate, C9H16O5 with acetate
        # (both held to a partner here) and the C11 acid. The row that
        # partners C9H16O5 turns back to a reading it had set aside, so on the
        # settled ledger only the formate reading has a partner, whichever row
        # the walk reached first.
        ids = {
            "[M-H]-": "im-deprot",
            "[M+HCOO]-": "im-formate",
            "[M+CH3COO]-": "im-acetate",
        }
        row = ledger_row(
            "r",
            "C10H18O5",
            "im-formate",
            alternatives=(("C9H16O5", "im-acetate"), ("C11H20O7", "im-deprot")),
        )
        acetate_partner = ledger_row(
            "b",
            "C9H16O5",
            "im-deprot",
            alternatives=(("C8H14O3", "im-formate"),),
            intensity=5.0e6,
        )
        acetate_partner["provenance"]["partner_gate"] = {
            "channel": "[M+HCOO]-",
            "partner": False,
            "displaced": "C8H14O3",
            "through": "[M-H]-",
        }
        acetate_partner["alternatives"][0]["partner_gate"] = "unmet"
        rows = [
            row,
            ledger_row("a", "C10H18O5", "im-deprot", intensity=1.0e5),
            acetate_partner,
            ledger_row("c", "C8H14O3", "im-deprot"),
        ]
        opened = frozenset({"[M+HCOO]-", "[M+CH3COO]-"})
        summary = apply_partner_gates(
            [rows[i] for i in order],
            notation_by_id={mid: n for n, mid in ids.items()},
            minor_channels=opened,
            partner_gated_channels=opened,
            tier_bands=BANDS,
        )
        assert (row["assigned_formula"], row["ionization_mechanism_id"]) == (
            "C10H18O5",
            "im-formate",
        )
        marks = {
            alt["assigned_formula"]: alt.get("partner_gate")
            for alt in row["alternatives"]
        }
        assert marks == {"C9H16O5": "unmet", "C11H20O7": None}
        assert summary["contested"] == 0

    def test_a_contest_swap_asks_the_mass_gate_again(self):
        # The policy capped the proton's reading; the walk lifts the cap on
        # C7H6's partner, and the mass gate's ceiling holds the row there.
        # Toluene less a hydride is the less plausible reading here and does
        # not reach the ceiling, so once the contest takes it the row is no
        # longer recorded as held there.
        benzyl = ledger_row(
            "benzyl",
            "C7H6",
            "im-h",
            alternatives=(("C7H8", "im-hydride"),),
            tier=TIER_CANDIDATE,
            capped=True,
            intensity=4.0e5,
        )
        benzyl["provenance"]["mass_gate"] = {
            "corroborated_by": None,
            "ceiling": TIER_CANDIDATE,
            "reason": "off_calibration",
        }
        benzyl["alternatives"][0]["plausibility"] = 0.5
        summary = self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e5),
                self._seen("tol", "C7H8", 3.0e6),
            ]
        )
        assert benzyl["assigned_formula"] == "C7H8"
        assert benzyl["tier"] == TIER_CANDIDATE
        assert benzyl["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "ceiling": TIER_CANDIDATE,
            "reason": "off_calibration",
        }
        assert summary["held"] == 0

    def test_a_modes_own_reading_is_not_contested(self):
        # A proton-transfer source declares protonation beside electron transfer,
        # so the proton's reading is the mode's own and keeps its formula
        # however strongly the sample shows toluene. The hydride reading has a
        # partner, so it is not set aside either: the cross-channel pass reads
        # it as the rival it is.
        benzyl = self._benzyl()
        summary = self._gate(
            [
                benzyl,
                self._seen("c7h6", "C7H6", 1.0e5),
                self._seen("tol", "C7H8", 3.0e6),
            ],
            opened=frozenset({"[M-H]+"}),
        )
        assert (benzyl["assigned_formula"], benzyl["ionization_mechanism_id"]) == (
            "C7H6",
            "im-h",
        )
        assert "partner_gate" not in benzyl["provenance"]
        assert "partner_gate" not in benzyl["alternatives"][0]
        assert summary["contested"] == 0

    def test_a_reading_with_no_partner_takes_the_strongest_one_offered(self):
        # Nothing shows C7H4, so the row reads another way; of the two
        # opportunistic readings the sample bears out, the one it shows more
        # strongly is taken.
        row = ledger_row(
            "r",
            "C7H4",
            "im-h",
            alternatives=(("C7H6", "im-hydride"), ("C7H8", "im-other")),
        )
        ids = {**CT_IDS, "[M+X]+": "im-other"}
        apply_partner_gates(
            [
                row,
                self._seen("c7h6", "C7H6", 1.0e5),
                self._seen("tol", "C7H8", 3.0e6),
            ],
            notation_by_id={mid: n for n, mid in ids.items()},
            minor_channels=frozenset({"[M+H]+", "[M-H]+", "[M+X]+"}),
            partner_gated_channels=frozenset({"[M+H]+", "[M-H]+"}),
            tier_bands=BANDS,
        )
        assert (row["assigned_formula"], row["ionization_mechanism_id"]) == (
            "C7H8",
            "im-other",
        )

    def test_a_gated_channel_has_to_be_a_minor_one(self):
        # A gated reading is never a partner; that is what lets the contest be
        # read once, after the walk.
        with pytest.raises(ValueError, match="minor"):
            apply_partner_gates(
                [self._benzyl()],
                notation_by_id={mid: n for n, mid in CT_IDS.items()},
                minor_channels=frozenset({"[M-H]+"}),
                partner_gated_channels=self.OPENED,
                tier_bands=BANDS,
            )


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
            ["[M+NH4]+"],
        )
        assert resolved.minor_channels == frozenset({"[M+NH4]+"})
        assert resolved.unavailable_channels == ()

    def test_a_channel_the_spectrum_does_not_show_is_not_searched(self):
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 200.0], [1.0e6, 1.0e4], ["[M+NH4]+"]
        )
        assert resolved.minor_channels == frozenset()

    def test_a_channel_the_deployment_cannot_express_is_reported_not_searched(self):
        # Different from "the source does not run it", and only this one is
        # worth fixing by configuration - so the run says which it was.
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], []
        )
        assert resolved.minor_channels == frozenset()
        assert resolved.unavailable_channels == ("[M+NH4]+",)

    def test_a_channel_the_mode_declares_is_still_secondary(self):
        # Declaring a channel lets the run search and match through it; it does
        # not make an opportunistic reagent the mode's own. So it is capped and
        # loses a tie as a secondary channel does, and is not added to the
        # search again, since the mode's own mechanism already searches it.
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(),
                UREA + ["[M+NH4]+"],
                instrument_type="orbi",
                polarity="+",
            ),
            [100.0, 138.0986],
            [1.0e6, 1.0e4],
            ["[M+NH4]+"],
        )
        assert resolved.minor_channels == frozenset({"[M+NH4]+"})
        assert resolved.added_channels == frozenset()
        assert resolved.snapshot()["secondary_channels"] == ["[M+NH4]+"]

    def test_so_is_one_whose_carrier_the_spectrum_does_not_show(self):
        # The mode searches it either way, so the spectrum's silence cannot make
        # a reading through it any less opportunistic.
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(),
                UREA + ["[M+NH4]+"],
                instrument_type="orbi",
                polarity="+",
            ),
            [50.0, 100.0, 400.0],
            [1.0e6, 1.0e5, 1.0e4],
            ["[M+NH4]+"],
        )
        assert resolved.channel_evidence[0].present is False
        assert resolved.minor_channels == frozenset({"[M+NH4]+"})
        assert resolved.added_channels == frozenset()

    def test_carbonate_on_a_labelled_nitrate_mode_that_declares_it(self):
        # A window starting above every carbonate line cannot show the channel,
        # and the nitrate profiles take that silence as no evidence either way.
        declared = ["[M+^NO3]-", "[M-H]-", "[M+CO3]-"]
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(profile="NO3_15N"),
                declared,
                instrument_type="orbi",
                polarity="-",
            ),
            [131.0, 210.0898, 300.0],
            [1.0e4, 1.0e6, 1.0e5],
            ["[M+CO3]-"],
        )
        assert resolved.channel_evidence[0].status == "unobservable"
        assert resolved.minor_channels == frozenset({"[M+CO3]-"})
        assert resolved.added_channels == frozenset()

    def test_a_declared_channel_the_profile_does_not_name_is_the_modes_own(self):
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], ["[M+NH4]+"]
        )
        assert resolved.minor_channels == frozenset({"[M+NH4]+"})
        assert resolved.added_channels == frozenset({"[M+NH4]+"})

    def test_a_profile_with_no_secondary_channels_is_untouched(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="none"), UREA, instrument_type="orbi"
        )
        extended = with_secondary_channels(
            resolved, [100.0, 138.0986], [1.0e6, 1.0e4], ["[M+NH4]+"]
        )
        assert extended is resolved
        assert extended.minor_channels == frozenset()

    def test_the_snapshot_records_every_channel_considered(self):
        snapshot = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], ["[M+NH4]+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == ["[M+NH4]+"]
        assert snapshot["channel_evidence"][0]["channel"] == "[M+NH4]+"
        assert snapshot["channel_evidence"][0]["present"] is True
        assert snapshot["unavailable_channels"] == []

    def test_the_snapshot_of_a_run_that_opened_nothing_says_so(self):
        # A spectrum that spans the probes and does not carry them: the source
        # is not running the channel, which is a real answer.
        snapshot = with_secondary_channels(
            self._resolved(), [50.0, 100.0, 400.0], [1.0e6, 1.0e5, 1.0e4], ["[M+NH4]+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == []
        assert snapshot["channel_evidence"] == [
            {"channel": "[M+NH4]+", "present": False, "status": "not_found"}
        ]

    def test_a_channel_the_window_could_not_show_is_recorded_as_such(self):
        # And is not searched, because the urea profile does not default it on.
        snapshot = with_secondary_channels(
            self._resolved(), [300.0, 400.0], [1.0e6, 1.0e4], ["[M+NH4]+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == []
        assert snapshot["channel_evidence"] == [
            {"channel": "[M+NH4]+", "present": False, "status": "unobservable"}
        ]


@pytest.mark.parametrize("profile,expected", [("BR", 3), ("NO3", 2), ("UR", 1)])
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
    PROTON = _mechanism("im-h", "[M+H]+")
    UREA_ADDUCT = _mechanism("im-urea", "[M+CH4N2O+H]+")
    AMMONIUM = _mechanism("im-nh4", "[M+NH4]+")
    #: The ammonium carrier beside a base line, so the urea profile opens [M+NH4]+.
    SHOWS_AMMONIUM = ([100.0, 138.0986], [1.0e6, 1.0e4])

    def _resolved(self, declared: list[str], spectrum=SHOWS_AMMONIUM):
        return with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(), declared, instrument_type="orbi", polarity="+"
            ),
            *spectrum,
            ["[M+NH4]+"],
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
            self._resolved(UREA + ["[M+NH4]+"]),
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


class TestEachMechanismIsSearchedOnce:
    PROTON = _mechanism("im-h", "[M+H]+")

    def test_two_rows_of_one_mechanism_are_searched_through_the_first(self):
        # Spellings of one mechanism stored before it had one spelling read
        # alike through the column; searched twice, every neutral would be
        # proposed through it twice.
        notations, mechanism_id_by_notation = _untargeted_ionization_notations(
            [
                _mechanism("im-nh4-a", "[M+NH4]+"),
                _mechanism("im-h", "[M+H]+"),
                _mechanism("im-nh4-b", "[M+NH4]+"),
            ]
        )
        assert notations == ["[M+NH4]+", "[M+H]+"]
        assert mechanism_id_by_notation == {"[M+NH4]+": "im-nh4-a", "[M+H]+": "im-h"}

    def test_a_row_in_neither_notation_is_left_out_and_reported_once(self, monkeypatch):
        warnings = []
        monkeypatch.setattr(
            service_module.runtime.logger,
            "warning",
            lambda message: warnings.append(message),
        )
        monkeypatch.setattr(service_module, "_reported_unreadable", set())
        label = _mechanism("im-label", "+H+ (a free-text label)")

        assert not _readable(label)
        assert not _readable(label)
        assert _readable(self.PROTON)
        assert len(warnings) == 1
        assert "im-label" in warnings[0]

    def test_the_second_row_of_a_pair_reads_as_the_same_channel(self):
        # Its own target ions still reach Stage A: a reading through them has
        # to count as, and be questioned as, a reading through that channel.
        assert _notation_by_id(
            [
                _mechanism("im-nh4-a", "[M+NH4]+"),
                self.PROTON,
                _mechanism("im-nh4-b", "[M+NH4]+"),
            ]
        ) == {"im-nh4-a": "[M+NH4]+", "im-h": "[M+H]+", "im-nh4-b": "[M+NH4]+"}

    def test_a_row_in_neither_notation_leaves_with_its_id(self, monkeypatch):
        # Stage A fetches target ions by these ids, and ions an older version
        # generated for the row read its text as some other mechanism. The
        # other polarity's ids stay: Stage A fetches by polarity too.
        monkeypatch.setattr(service_module.runtime.logger, "warning", lambda _: None)
        monkeypatch.setattr(service_module, "_reported_unreadable", set())

        mechanism_ids, mechanisms = _without_unreadable(
            ["im-h", "im-label", "im-other-polarity"],
            [self.PROTON, _mechanism("im-label", "+H+ (a free-text label)")],
        )

        assert mechanism_ids == ["im-h", "im-other-polarity"]
        assert [m.ionization_mechanism_id for m in mechanisms] == ["im-h"]
        assert mechanisms[0] is not self.PROTON
