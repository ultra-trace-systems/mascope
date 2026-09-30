"""What a row becomes when a claim reads it as its neighbour's line."""

from __future__ import annotations

import pytest

from mascope_backend.api.new.peak_assignments.envelope_claims import (
    ENVELOPE_CLAIM,
    ClaimedLine,
    EnvelopeClaim,
    apply_claims,
    is_claimed,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    TRACKING_IN_DOUBT,
    TRACKING_TRACKS,
)


#: The 13C line of C6H13O6+, 6.54% of the ion's own, and its 18O line.
C13_MZ, C13_SHARE = 182.07402, 0.06543
O18_MZ, O18_SHARE = 183.07491, 0.01234


def owner(**fields) -> dict:
    """An assigned M0 the untargeted search committed."""
    return {
        "peak_assignment_id": "pa-owner",
        "sample_peak_id": "peak-owner",
        "sample_peak_mz": 181.0707,
        "sample_peak_intensity": 1000.0,
        "role": "M0",
        "assigned_formula": "C6H12O6",
        "ion_formula": "C6H13O6+",
        "ionization_mechanism_id": "mech-proton",
        "isotope_label": "M0",
        "isotope_formula": None,
        "source": "untargeted",
        "fit_score": 0.93,
        "mz_error_ppm": 0.1,
        "abundance_error": 0.0,
        "tier": "assigned",
        "target_compound_id": None,
        "target_ion_id": None,
        "owner_peak_assignment_id": None,
        "alternatives": None,
        "provenance": {"plausibility": 1.0, "evidence": 0.93, "score_version": 2},
        **fields,
    }


def elsewhere(row_id: str = "pa-line", **fields) -> dict:
    """The M0 a search committed on the owner's 13C line."""
    return {
        "peak_assignment_id": row_id,
        "sample_peak_id": f"peak-{row_id}",
        "sample_peak_mz": C13_MZ * (1 + 0.4e-6),
        "sample_peak_intensity": 50.0,
        "role": "M0",
        "assigned_formula": "C7H11NO4",
        "ion_formula": "C7H12NO4+",
        "ionization_mechanism_id": "mech-proton",
        "isotope_label": "M0",
        "isotope_formula": None,
        "source": "untargeted",
        "fit_score": 0.41,
        "mz_error_ppm": 2.3,
        "abundance_error": 0.2,
        "tier": "below_assignability",
        "target_compound_id": None,
        "target_ion_id": None,
        "owner_peak_assignment_id": None,
        "alternatives": [{"assigned_formula": "C8H15NO3", "source": "untargeted"}],
        "provenance": {
            "plausibility": 0.9,
            "evidence": 0.37,
            "candidate_density": 1,
            "tier_reasons": [{"rule": "envelope_neighbour"}],
        },
        **fields,
    }


def claim_of(row_id: str = "pa-line", **fields) -> EnvelopeClaim:
    return EnvelopeClaim(
        owner_id="pa-owner",
        line=ClaimedLine(row_id, "13C", C13_MZ, C13_SHARE, TRACKING_IN_DOUBT),
        **fields,
    )


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["peak_assignment_id"]: row for row in rows}


class TestAClaimedRow:
    def test_it_becomes_the_owner_s_isotopologue_at_candidate(self):
        rows = [owner(), elsewhere()]

        kept = apply_claims(rows, [claim_of()], max_alternatives=5)

        line = by_id(kept)["pa-line"]
        assert line["role"] == "iso_child"
        assert line["owner_peak_assignment_id"] == "pa-owner"
        assert line["assigned_formula"] == "C6H12O6"
        assert line["ionization_mechanism_id"] == "mech-proton"
        assert line["tier"] == "candidate"
        assert line["source"] == "untargeted"
        # Measured by the owner's fit, as every isotopologue the stages commit.
        assert line["fit_score"] == 0.93
        # Against the line it is read as, not the formula it was committed for.
        assert line["mz_error_ppm"] == pytest.approx(0.4, abs=1e-3)
        assert line["abundance_error"] == pytest.approx(0.05 / C13_SHARE - 1.0)
        # The peak itself is untouched.
        assert line["sample_peak_id"] == "peak-pa-line"
        assert line["sample_peak_mz"] == pytest.approx(C13_MZ * (1 + 0.4e-6))

    def test_an_untargeted_owner_s_line_is_named_by_its_substitution(self):
        rows = [owner(), elsewhere()]

        apply_claims(rows, [claim_of()], max_alternatives=5)

        line = by_id(rows)["pa-line"]
        assert line["isotope_label"] == "13C"
        assert line["ion_formula"] == "[13C]C5H13O6+"
        assert line["isotope_formula"] is None

    def test_a_listed_owner_s_line_is_named_by_its_mass_offset(self):
        rows = [
            owner(
                source="database",
                target_compound_id="compound-1",
                target_ion_id="ion-1",
            ),
            elsewhere(),
        ]

        apply_claims(rows, [claim_of()], max_alternatives=5)

        line = by_id(rows)["pa-line"]
        assert line["isotope_label"] == "M+1"
        assert line["ion_formula"] == "C6H13O6+"
        assert line["isotope_formula"] == "[13C]C5H13O6+"
        assert line["source"] == "database"
        assert (line["target_compound_id"], line["target_ion_id"]) == (
            "compound-1",
            "ion-1",
        )

    def test_an_owner_whose_ion_cannot_take_the_line_keeps_its_formula(self):
        rows = [owner(ion_formula="C6H13O6+"), elsewhere()]
        claim = EnvelopeClaim(
            owner_id="pa-owner",
            line=ClaimedLine("pa-line", "81Br", C13_MZ, 0.9, TRACKING_TRACKS),
        )

        apply_claims(rows, [claim], max_alternatives=5)

        assert by_id(rows)["pa-line"]["ion_formula"] == "C6H13O6+"

    def test_the_reading_it_displaced_is_its_first_alternative(self):
        rows = [owner(), elsewhere()]

        apply_claims(rows, [claim_of()], max_alternatives=5)

        first, second = by_id(rows)["pa-line"]["alternatives"]
        assert first == {
            "assigned_formula": "C7H11NO4",
            "ion_formula": "C7H12NO4+",
            "ionization_mechanism_id": "mech-proton",
            "isotope_label": "M0",
            "target_compound_id": None,
            "target_ion_id": None,
            "fit_score": 0.41,
            "mz_error_ppm": 2.3,
            "plausibility": 0.9,
            "source": "untargeted",
            "displaced_by_claim": True,
        }
        assert second == {"assigned_formula": "C8H15NO3", "source": "untargeted"}

    def test_the_alternatives_keep_the_run_s_cap(self):
        rows = [owner(), elsewhere()]

        apply_claims(rows, [claim_of()], max_alternatives=1)
        assert [
            a["assigned_formula"] for a in by_id(rows)["pa-line"]["alternatives"]
        ] == ["C7H11NO4"]

        rows = [owner(), elsewhere()]
        apply_claims(rows, [claim_of()], max_alternatives=0)
        assert by_id(rows)["pa-line"]["alternatives"] is None

    def test_a_listed_identity_rides_along_with_the_displaced_reading(self):
        identities = [{"name": "a known compound", "source": "list-1"}]
        rows = [
            owner(),
            elsewhere(
                source="database",
                provenance={"plausibility": 0.9, "reference_identities": identities},
            ),
        ]

        apply_claims(rows, [claim_of()], max_alternatives=5)

        displaced = by_id(rows)["pa-line"]["alternatives"][0]
        assert displaced["reference_identities"] == identities
        assert displaced["source"] == "database"

    def test_its_provenance_is_the_claim_and_the_owner_s_measurement(self):
        rows = [owner(), elsewhere()]

        apply_claims(rows, [claim_of()], max_alternatives=5)

        provenance = by_id(rows)["pa-line"]["provenance"]
        assert provenance == {
            "plausibility": 1.0,
            "evidence": 0.93,
            "score_version": 2,
            ENVELOPE_CLAIM: {
                "line": "13C",
                "predicted_mz": pytest.approx(C13_MZ),
                "predicted_share": pytest.approx(C13_SHARE),
                "observed_share": pytest.approx(0.05),
                "tracking": TRACKING_IN_DOUBT,
                "displaced": {
                    "assigned_formula": "C7H11NO4",
                    "ion_formula": "C7H12NO4+",
                    "isotope_label": "M0",
                    "source": "untargeted",
                    "tier": "below_assignability",
                    "evidence": 0.37,
                },
            },
        }
        assert is_claimed(by_id(rows)["pa-line"])
        assert not is_claimed(by_id(rows)["pa-owner"])

    def test_the_owner_is_left_as_it_was(self):
        before = owner()
        rows = [owner(), elsewhere()]

        apply_claims(rows, [claim_of()], max_alternatives=5)

        assert by_id(rows)["pa-owner"] == before

    def test_an_owner_with_no_height_leaves_the_heights_unread(self):
        rows = [owner(sample_peak_intensity=0.0), elsewhere()]

        apply_claims(rows, [claim_of()], max_alternatives=5)

        line = by_id(rows)["pa-line"]
        assert line["abundance_error"] is None
        assert line["provenance"][ENVELOPE_CLAIM]["observed_share"] is None

    def test_a_claim_naming_a_row_the_ledger_does_not_hold_changes_nothing(self):
        rows = [owner(), elsewhere()]

        kept = apply_claims(
            rows,
            [claim_of("pa-gone"), EnvelopeClaim("pa-nobody", claim_of().line)],
            max_alternatives=5,
        )

        assert kept == [owner(), elsewhere()]


class TestTheClaimedRowsOwnLines:
    @staticmethod
    def family() -> list[dict]:
        return [
            owner(),
            elsewhere(),
            elsewhere(
                "pa-line-18o",
                role="iso_child",
                owner_peak_assignment_id="pa-line",
                isotope_label="13C",
                sample_peak_mz=O18_MZ,
                sample_peak_intensity=10.0,
                tier="below_assignability",
            ),
            elsewhere(
                "pa-line-lost",
                role="iso_child",
                owner_peak_assignment_id="pa-line",
                isotope_label="18O",
                sample_peak_mz=185.5,
                sample_peak_intensity=4.0,
            ),
        ]

    def test_a_line_the_owner_predicts_goes_with_the_claim(self):
        claim = claim_of(
            carried=(
                ClaimedLine("pa-line-18o", "18O", O18_MZ, O18_SHARE, TRACKING_TRACKS),
            ),
            released=("pa-line-lost",),
        )

        kept = apply_claims(self.family(), [claim], max_alternatives=5)

        carried = by_id(kept)["pa-line-18o"]
        assert carried["owner_peak_assignment_id"] == "pa-owner"
        assert carried["assigned_formula"] == "C6H12O6"
        assert carried["isotope_label"] == "18O"
        assert carried["tier"] == "candidate"
        assert carried["mz_error_ppm"] == pytest.approx(0.0, abs=1e-6)
        block = carried["provenance"][ENVELOPE_CLAIM]
        assert block["carried_with"] == "pa-line"
        assert block["tracking"] == TRACKING_TRACKS
        assert block["displaced"]["assigned_formula"] == "C7H11NO4"
        assert block["displaced"]["isotope_label"] == "13C"
        assert (
            "carried_with" not in by_id(kept)["pa-line"]["provenance"][ENVELOPE_CLAIM]
        )

    def test_a_line_it_does_not_predict_leaves_the_ledger(self):
        claim = claim_of(released=("pa-line-lost",))
        rows = self.family()

        kept = apply_claims(rows, [claim], max_alternatives=5)

        assert set(by_id(kept)) == {"pa-owner", "pa-line", "pa-line-18o"}
        # Nothing claimed it, so it is still the isotopologue it was - of a row
        # that is now itself an isotopologue. The claim finder never leaves a
        # row in that state: it carries or releases every line of a claimed row.
        assert by_id(kept)["pa-line-18o"]["owner_peak_assignment_id"] == "pa-line"
