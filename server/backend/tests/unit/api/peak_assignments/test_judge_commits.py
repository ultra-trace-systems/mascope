"""The passes that judge a sample's commits, run again after lines are claimed.

``judge_commits`` runs the mass gate, the cross-channel pass and the tiering
pass, reads the rows the tiering pass finds on an assigned neighbour's line as
that neighbour's isotopologues, and runs all three again over the ledger the
claims leave. These pin what the second reading changes that a single one would
get wrong: the calibration without the claimed row as an anchor, a row flagged
only by a row the claim took, and a chain of claims.

The envelopes are stated rather than predicted, so each test's geometry is
exactly what it says.
"""

from __future__ import annotations

import copy

import numpy as np
import pytest

from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments import tiering as tiering_module
from mascope_backend.api.new.peak_assignments.engine import SampleMassAccuracy
from mascope_backend.api.new.peak_assignments.envelope_claims import ENVELOPE_CLAIM
from mascope_backend.api.new.peak_assignments.mass_gate import (
    TRACKING_TRACKS,
    apply_mass_gate,
)
from mascope_backend.api.new.peak_assignments.service import judge_commits
from mascope_backend.api.new.peak_assignments.tiering import (
    REASON_ENVELOPE_CLAIM,
    REASON_ENVELOPE_NEIGHBOUR,
    REASON_INHERITED,
)


#: Each ion's lines, heights relative to its own, as the tests state them.
ENVELOPES = {
    # An owner at m/z 200 with lines at 201 and 202.
    "C6H13O6": ([200.0, 201.0, 202.0], [1.0, 0.1, 0.01], ["M0", "13C", "13C2"]),
    # A row committed on the owner's 201 line, whose own line is at 202.
    "C7H12NO4": ([201.0, 202.0], [1.0, 0.1], ["M0", "13C"]),
    # A chain: V's line holds W, W's line and X's line both hold Z.
    "C5H11O5": ([150.0, 150.5], [1.0, 0.1], ["M0", "13C"]),
    "C4H9O4": ([150.5, 151.3], [1.0, 0.5], ["M0", "18O"]),
    "C3H7O3": ([150.8, 151.3], [1.0, 0.5], ["M0", "13C"]),
}


@pytest.fixture(autouse=True)
def stated_envelopes(monkeypatch):
    def predict(ion_formula, charge, threshold=None):
        mzs, heights, labels = ENVELOPES.get(ion_formula, ([0.0], [1.0], ["M0"]))
        return np.array(mzs), np.array(heights), list(labels)

    monkeypatch.setattr(tiering_module, "predict_isotopes", predict)


def commit(
    row_id: str,
    formula: str,
    ion: str,
    mz: float,
    intensity: float,
    *,
    role: str = "M0",
    owner: str | None = None,
    source: str = "untargeted",
    compound: str | None = None,
    ppm: float = 0.0,
    label: str = "M0",
) -> dict:
    """A committed row as a stage builds it."""
    return {
        "peak_assignment_id": row_id,
        "peak_assignment_run_id": "run-1",
        "sample_item_id": "si-1",
        "sample_peak_id": f"peak-{row_id}",
        "sample_peak_mz": mz,
        "sample_peak_intensity": intensity,
        "sample_peak_tof": None,
        "role": role,
        "assigned_formula": formula,
        "ion_formula": ion,
        "ionization_mechanism_id": "im-1",
        "isotope_label": label,
        "isotope_formula": None,
        "source": source,
        "fit_score": 0.95,
        "mz_error_ppm": ppm,
        "abundance_error": 0.0,
        "tier": "assigned",
        "target_compound_id": compound,
        "target_ion_id": None,
        "owner_peak_assignment_id": owner,
        "alternatives": None,
        "provenance": (
            {"evidence": 0.95, "plausibility": 1.0, "candidate_density": 1}
            if role == "M0"
            else {"evidence": 0.95, "plausibility": 1.0}
        ),
    }


def anchors() -> list[dict]:
    """Twelve target library lines on calibration, away from every envelope."""
    return [
        commit(
            f"anchor-{i}",
            "C9H10O2",
            "anchor+",
            300.0 + i,
            5000.0,
            source="database",
            compound=f"compound-{i}",
            ppm=0.1 if i % 2 else -0.1,
        )
        for i in range(12)
    ]


def owner() -> dict:
    return commit("pa-x", "C6H12O6", "C6H13O6+", 200.0, 1000.0)


def on_its_line(**fields) -> dict:
    """The row a search committed on the owner's 201 line."""
    return commit("pa-y", "C7H11NO4", "C7H12NO4+", 201.0, 150.0, **fields)


def judge(rows: list[dict], channel: str = "[M+H]+"):
    return judge_commits(
        rows,
        stage_a_accuracy=SampleMassAccuracy(),
        fallback_sigma_ppm=0.3,
        notation_by_id={"im-1": channel},
        mz_tolerance_ppm=5.0,
        abundance_floor=0.01,
        max_alternatives=5,
    )


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["peak_assignment_id"]: row for row in rows}


def rules(row: dict) -> list[str]:
    return [reason["rule"] for reason in row["provenance"]["tier_reasons"]]


class TestAClaimIsJudgedAgain:
    def test_the_line_is_read_as_the_owner_s_and_every_pass_runs_over_it(self):
        rows = anchors() + [owner(), on_its_line()]
        before = copy.deepcopy(rows)

        judged = judge(rows)

        line = by_id(judged.rows)["pa-y"]
        assert (line["role"], line["owner_peak_assignment_id"]) == ("iso_child", "pa-x")
        assert (line["isotope_label"], line["tier"]) == ("13C", "candidate")
        assert line["provenance"][ENVELOPE_CLAIM]["tracking"] == TRACKING_TRACKS
        # The second round's passes read it as the isotopologue it now is.
        assert line["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "tracking": TRACKING_TRACKS,
        }
        assert rules(line) == [REASON_ENVELOPE_CLAIM, REASON_INHERITED]
        assert judged.cross_channel["committed_m0"] == 13
        assert judged.tiering["committed_m0"] == 13
        # The owner keeps its tier, and a claimed line is not its corroboration.
        owner_row = by_id(judged.rows)["pa-x"]
        assert owner_row["tier"] == "assigned"
        assert owner_row["provenance"]["mass_gate"]["corroborated_by"] is None
        assert {
            key: judged.tiering[key]
            for key in ("claimed", "claim_rounds", "held", "released", "unapplied")
        } == {
            "claimed": 1,
            "claim_rounds": 2,
            "held": {},
            "released": 0,
            "unapplied": 0,
        }
        # What it was given is left as the stages built it.
        assert rows == before

    def test_a_ledger_with_nothing_to_claim_is_judged_once(self):
        rows = anchors() + [owner()]

        judged = judge(rows)

        assert (judged.tiering["claimed"], judged.tiering["claim_rounds"]) == (0, 1)
        assert [row["peak_assignment_id"] for row in judged.rows] == [
            row["peak_assignment_id"] for row in rows
        ]

    def test_the_calibration_is_fitted_without_the_row_the_claim_took(self):
        # The claimed row had an isotopologue of its own that tracked it, which
        # made it an anchor. As the owner's line it anchors nothing, and its
        # isotopologue is the owner's 202 line, which goes with it.
        rows = anchors() + [
            owner(),
            on_its_line(),
            commit(
                "pa-y-13c",
                "C7H11NO4",
                "C7H12NO4+",
                202.0,
                12.0,
                role="iso_child",
                owner="pa-y",
                label="13C",
            ),
        ]
        unclaimed = apply_mass_gate(
            copy.deepcopy(rows), stage_a_accuracy=None, fallback_sigma_ppm=0.3
        )

        judged = judge(rows)

        assert unclaimed["anchors"] == 13
        assert judged.mass_calibration["anchors"] == 12
        carried = by_id(judged.rows)["pa-y-13c"]
        assert (carried["owner_peak_assignment_id"], carried["isotope_label"]) == (
            "pa-x",
            "13C2",
        )
        assert carried["provenance"][ENVELOPE_CLAIM]["carried_with"] == "pa-y"
        assert (
            judged.tiering["claimed"],
            judged.tiering["claimed_with_their_lines"],
            judged.tiering["released"],
        ) == (1, 1, 0)

    def test_a_line_the_owner_does_not_predict_leaves_the_ledger(self):
        rows = anchors() + [
            owner(),
            on_its_line(),
            commit(
                "pa-y-far",
                "C7H11NO4",
                "C7H12NO4+",
                205.0,
                12.0,
                role="iso_child",
                owner="pa-y",
                label="13C",
            ),
        ]

        judged = judge(rows)

        assert "pa-y-far" not in by_id(judged.rows)
        assert judged.tiering["released"] == 1
        assert len(judged.rows) == len(rows) - 1

    def test_a_row_flagged_only_by_the_row_a_claim_took_stands_again(self):
        # Too tall for the owner's 202 line, short enough for the claimed row's:
        # the first reading caps it for sitting on a line of a row the second
        # reading no longer holds as a compound.
        rows = anchors() + [
            owner(),
            on_its_line(),
            commit("pa-z", "C8H10N2O3", "C8H11N2O3+", 202.0, 25.0),
        ]

        judged = judge(rows)

        standing = by_id(judged.rows)["pa-z"]
        assert standing["role"] == "M0"
        assert standing["tier"] == "assigned"
        assert REASON_ENVELOPE_NEIGHBOUR not in rules(standing)
        assert (judged.tiering["claimed"], judged.tiering["held"]) == (1, {})


def chain() -> list[dict]:
    return anchors() + [
        commit("pa-v", "C5H10O5", "C5H11O5+", 150.0, 1000.0),
        commit("pa-w", "C4H8O4", "C4H9O4+", 150.5, 100.0),
        commit("pa-x", "C3H6O3", "C3H7O3+", 150.8, 1000.0),
        commit("pa-z", "C2H4O2", "C2H5O2+", 151.3, 50.0),
    ]


class TestAChainOfClaims:
    """Z sits on W's line and on X's. W is flagged first, so Z is too, and W is
    at candidate: Z can only be read as X's line once W is V's."""

    def test_it_is_read_over_as_many_rounds_as_it_takes(self):
        judged = judge(chain())

        rows = by_id(judged.rows)
        assert rows["pa-w"]["owner_peak_assignment_id"] == "pa-v"
        assert rows["pa-z"]["owner_peak_assignment_id"] == "pa-x"
        assert rows["pa-z"]["isotope_label"] == "13C"
        assert (judged.tiering["claimed"], judged.tiering["claim_rounds"]) == (2, 3)
        assert judged.tiering["unapplied"] == 0

    def test_the_rounds_stop_at_their_limit_and_say_what_they_left(self, monkeypatch):
        monkeypatch.setattr(service, "MAX_CLAIM_ROUNDS", 2)

        judged = judge(chain())

        rows = by_id(judged.rows)
        assert rows["pa-w"]["owner_peak_assignment_id"] == "pa-v"
        assert rows["pa-z"]["role"] == "M0"
        assert rows["pa-z"]["tier"] == "candidate"
        assert (
            judged.tiering["claimed"],
            judged.tiering["claim_rounds"],
            judged.tiering["unapplied"],
        ) == (1, 2, 1)


class TestTheRunsChannels:
    def test_the_tiering_pass_reads_them(self):
        # A hydrocarbon clustered with nitrate is not held at assigned, so the
        # line its envelope predicts is not read as its isotopologue.
        rows = anchors() + [
            commit("pa-x", "C10H16", "C6H13O6+", 200.0, 1000.0),
            on_its_line(),
        ]

        judged = judge(rows, channel="[M+NO3]-")

        judged_rows = by_id(judged.rows)
        assert judged_rows["pa-x"]["tier"] == "candidate"
        assert judged_rows["pa-y"]["role"] == "M0"
        assert judged.tiering["held"] == {"neighbour_not_assigned": 1}


class TestTheBandsReachTheReasons:
    def test_a_row_under_the_top_band_names_it_first(self):
        low = commit("pa-low", "C6H12O6", "C6H13O6+", 200.0, 1000.0)
        low["fit_score"] = 0.3
        low["tier"] = "below_assignability"
        low["provenance"]["evidence"] = 0.3
        judged = judge_commits(
            anchors() + [low],
            stage_a_accuracy=SampleMassAccuracy(),
            fallback_sigma_ppm=0.3,
            notation_by_id={"im-1": "[M+H]+"},
            mz_tolerance_ppm=5.0,
            abundance_floor=0.01,
            max_alternatives=5,
            tier_bands={"assigned": 0.75, "candidate": 0.45},
        )
        row = by_id(judged.rows)["pa-low"]
        assert rules(row)[0] == "evidence_band"
        assert judged.tiering["under_band"] == 1

    def test_a_caller_that_states_no_bands_gets_no_band_line(self):
        low = commit("pa-low", "C6H12O6", "C6H13O6+", 200.0, 1000.0)
        low["provenance"]["evidence"] = 0.3
        judged = judge(anchors() + [low])
        assert "evidence_band" not in rules(by_id(judged.rows)["pa-low"])


class TestThePartnerGateReadsTheJudgedLedger:
    """The gate runs after the mass gate: a partner is a reading that gate
    left committed, so one it sends below assignability is no partner."""

    IDS = {"im-1": "[M-H]-", "im-formate": "[M+HCOO]-"}
    BANDS = {"assigned": 0.75, "candidate": 0.45}

    def _formate(self) -> dict:
        row = commit("pa-f", "C10H18O5", "C10H19O7-", 263.1136, 1000.0)
        row["ionization_mechanism_id"] = "im-formate"
        row["alternatives"] = [
            {
                "assigned_formula": "C11H20O7",
                "ionization_mechanism_id": "im-1",
                "same_ion": True,
                "plausibility": 1.0,
            }
        ]
        row["provenance"]["minor_channel"] = {
            "corroborated_by": "second_channel",
            "capped": False,
        }
        return row

    def _judge(self, rows: list[dict]):
        return judge_commits(
            rows,
            stage_a_accuracy=SampleMassAccuracy(),
            fallback_sigma_ppm=0.3,
            notation_by_id=self.IDS,
            mz_tolerance_ppm=5.0,
            abundance_floor=0.01,
            max_alternatives=5,
            tier_bands=self.BANDS,
            minor_channels=frozenset({"[M+HCOO]-"}),
            partner_gated_channels=frozenset({"[M+HCOO]-"}),
        )

    def test_a_partner_the_mass_gate_sends_below_assignability_is_none(self):
        # The C10 product is committed through deprotonation 5 ppm off a run
        # whose anchors sit within 0.1: the mass gate puts it below
        # assignability, and the formate reading that stood on it reads as
        # the acid.
        partner = commit("pa-p", "C10H18O5", "C10H17O5-", 217.1081, 800.0, ppm=5.0)
        rows = by_id(self._judge(anchors() + [partner, self._formate()]).rows)
        assert rows["pa-p"]["tier"] == "below_assignability"
        assert rows["pa-f"]["assigned_formula"] == "C11H20O7"
        assert rows["pa-f"]["ionization_mechanism_id"] == "im-1"
        assert rows["pa-f"]["provenance"]["partner_gate"]["partner"] is False

    def test_a_partner_on_calibration_stands(self):
        partner = commit("pa-p", "C10H18O5", "C10H17O5-", 217.1081, 800.0)
        rows = by_id(self._judge(anchors() + [partner, self._formate()]).rows)
        assert rows["pa-p"]["tier"] == "assigned"
        assert rows["pa-f"]["assigned_formula"] == "C10H18O5"
        assert rows["pa-f"]["provenance"]["partner_gate"]["partner"] is True

    def _capped_formate(self, ppm: float) -> dict:
        """A formate reading the policy held at candidate, at this offset."""
        row = self._formate()
        row["mz_error_ppm"] = ppm
        row["tier"] = "candidate"
        row["provenance"]["minor_channel"] = {"corroborated_by": None, "capped": True}
        return row

    @pytest.mark.parametrize("ppm", [2.0, 2.5, 3.0])
    def test_a_lifted_reading_stays_under_the_mass_gates_ceiling(self, ppm):
        # The policy held the formate reading at candidate, so the mass gate
        # lowered nothing and recorded only the ceiling; the partner gate then
        # lifts the policy's cap, and the reading stays where a mode-channel
        # row at the same offset is held.
        partner = commit("pa-p", "C10H18O5", "C10H17O5-", 217.1081, 800.0)
        control = commit("pa-c", "C8H14O4", "C8H13O4-", 173.0819, 700.0, ppm=ppm)
        rows = by_id(
            self._judge(anchors() + [partner, control, self._capped_formate(ppm)]).rows
        )
        assert rows["pa-c"]["tier"] == "candidate"
        assert rows["pa-c"]["provenance"]["mass_gate"]["reason"] == "off_calibration"
        formate = rows["pa-f"]
        assert formate["provenance"]["partner_gate"]["uncapped"] is True
        assert formate["tier"] == "candidate"
        assert formate["provenance"]["mass_gate"]["ceiling"] == "candidate"
        assert formate["provenance"]["mass_gate"]["capped"] == "candidate"

    def test_a_swapped_reading_stays_under_the_mass_gates_ceiling(self):
        # No partner, so the formate reading swaps to its acid: the acid is
        # the same ion on the same line, and the line is off calibration.
        rows = by_id(self._judge(anchors() + [self._capped_formate(2.5)]).rows)
        formate = rows["pa-f"]
        assert formate["assigned_formula"] == "C11H20O7"
        assert formate["tier"] == "candidate"
        assert formate["provenance"]["mass_gate"]["capped"] == "candidate"


class TestTheStrongerPartnerOnTheJudgedLedger:
    """A charge-transfer source's ion read two opportunistic ways, each with a
    partner through electron transfer: the stronger partner takes it, and every
    pass after the gate reads what the contest left."""

    IDS = {"im-1": "+", "im-h": "[M+H]+", "im-hydride": "[M-H]+"}
    OPENED = frozenset({"[M+H]+", "[M-H]+"})

    def _judge(self, ion: str, mz: float, lighter: str, heavier: str, height: float):
        """The ion elected through proton transfer as the lighter molecule,
        with the heavier one less a hydride on the row, and both molecules
        committed through electron transfer, the heavier ``height`` counts high."""
        elected = commit("pa-ion", lighter, ion, mz, 4.0e5)
        elected["ionization_mechanism_id"] = "im-h"
        elected["alternatives"] = [
            {
                "assigned_formula": heavier,
                "ionization_mechanism_id": "im-hydride",
                "same_ion": True,
                "plausibility": 1.0,
            }
        ]
        elected["provenance"]["minor_channel"] = {
            "corroborated_by": "second_channel",
            "capped": False,
        }
        rows = anchors() + [
            elected,
            commit("pa-lighter", lighter, f"{lighter}+", mz - 1.0078, 1.0e5),
            commit("pa-heavier", heavier, f"{heavier}+", mz + 1.0078, height),
        ]
        judged = judge_commits(
            rows,
            stage_a_accuracy=SampleMassAccuracy(),
            fallback_sigma_ppm=0.3,
            notation_by_id=self.IDS,
            mz_tolerance_ppm=5.0,
            abundance_floor=0.01,
            max_alternatives=5,
            tier_bands={"assigned": 0.75, "candidate": 0.45},
            minor_channels=self.OPENED,
            partner_gated_channels=self.OPENED,
        )
        return by_id(judged.rows)["pa-ion"], judged

    def test_the_benzyl_cation_is_toluene_less_a_hydride_and_settled(self):
        row, judged = self._judge("C7H7+", 91.0542, "C7H6", "C7H8", 3.0e6)
        assert (row["assigned_formula"], row["ionization_mechanism_id"]) == (
            "C7H8",
            "im-hydride",
        )
        assert row["tier"] == "assigned"
        assert row["provenance"]["cross_channel"]["same_ion_settled"]["by"] == (
            "partner"
        )
        reasons = {r["rule"]: r["detail"] for r in row["provenance"]["tier_reasons"]}
        assert reasons["same_ion_settled"] == (
            "the same ion also reads as C7H6 through [M+H]+; the sample commits C7H8 "
            "through one of the mode's own channels on a peak 30 times as bright "
            "as C7H6's, which settles it"
        )
        assert judged.cross_channel["settled"]["partner"] == 1

    def test_within_the_margin_isoprene_less_a_hydride_is_a_candidate(self):
        row, judged = self._judge("C5H7+", 67.0542, "C5H6", "C5H8", 7.0e5)
        assert (row["assigned_formula"], row["ionization_mechanism_id"]) == (
            "C5H8",
            "im-hydride",
        )
        assert row["tier"] == "candidate"
        reasons = {r["rule"]: r for r in row["provenance"]["tier_reasons"]}
        assert reasons["ambiguous_adduct"]["caps"] is True
        assert reasons["ambiguous_adduct"]["detail"] == (
            "the same ion reads as C5H6 through [M+H]+, another molecule the spectrum "
            "cannot tell from this one; the sample commits both molecules through "
            "the mode's own channels, C5H8 on a peak only 7.0 times as bright as "
            "C5H6's, short of the 10 times that would settle which"
        )
        assert judged.cross_channel["shown_rival"] == 1


class TestWhatTheSampleShowsOnTheJudgedLedger:
    """The run tells the cross-channel pass which of its channels are
    opportunistic: a molecule seen only through one of those is not one the
    sample shows."""

    IDS = {"im-1": "[M+H]+", "im-nh4": "[M+NH4]+", "im-na": "[M+Na]+"}

    def test_an_opportunistic_channel_shows_no_molecule_of_its_own(self):
        # An ESI source opens ammonium and sodium adducts. C6H15NO6 through a
        # proton is C6H12O6 with ammonium, and a second channel commits
        # C6H15NO6; C6H12O6 is seen only with sodium, itself an opportunistic
        # channel, so the sample does not show it and the second channel
        # settles the ion.
        protonated = commit("pa-h", "C6H15NO6", "C6H16NO6+", 198.0972, 4.0e5)
        protonated["alternatives"] = [
            {
                "assigned_formula": "C6H12O6",
                "ionization_mechanism_id": "im-nh4",
                "same_ion": True,
                "plausibility": 1.0,
            }
        ]
        ammoniated = commit("pa-nh4", "C6H15NO6", "C6H19N2O6+", 215.1238, 1.0e5)
        ammoniated["ionization_mechanism_id"] = "im-nh4"
        sodiated = commit("pa-na", "C6H12O6", "C6H12NaO6+", 203.0526, 1.0e6)
        sodiated["ionization_mechanism_id"] = "im-na"
        judged = judge_commits(
            anchors() + [protonated, ammoniated, sodiated],
            stage_a_accuracy=SampleMassAccuracy(),
            fallback_sigma_ppm=0.3,
            notation_by_id=self.IDS,
            mz_tolerance_ppm=5.0,
            abundance_floor=0.01,
            max_alternatives=5,
            minor_channels=frozenset({"[M+NH4]+", "[M+Na]+"}),
        )
        row = by_id(judged.rows)["pa-h"]
        assert row["tier"] == "assigned"
        assert row["provenance"]["cross_channel"]["same_ion_settled"]["by"] == (
            "second_channel"
        )
