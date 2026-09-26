"""What the reagent pre-pass writes into the ledger.

The library half is tested in ``libraries/tools/tests/test_reagent_library.py``;
what matters here is the shape of the row it produces, because that shape is
what decides whether a reagent peak stays out of the analyte ledger or quietly
re-enters it as an assignment nobody made.
"""

import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.cross_channel import (
    REASON_AMBIGUOUS_ADDUCT,
    SAME_ION_SETTLED,
    SETTLED_BY_SECOND_CHANNEL,
    apply_cross_channel,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_REAGENT,
    SOURCE_REAGENT,
    drop_ions_claimed_elsewhere,
)
from mascope_backend.api.new.peak_assignments.import_validation import (
    coherent_tiers,
    owner_link_errors,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    build_reagent_assignments,
    claim_fragments,
    claim_reagent_peaks,
    fragment_ladders_for,
    reagent_library_for,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.reagents import (
    FAMILY_FRAGMENT,
    KIND_FRAGMENT,
    FragmentIon,
    FragmentLadder,
    ion_mz,
)


def _peaks(*ions: tuple[str, int, float]) -> pd.DataFrame:
    """A peak frame holding each ion's predicted envelope at a given height."""
    rows = []
    for formula, charge, height in ions:
        predicted_mz, predicted_intensity, _ = predict_isotopes(formula, charge)
        base = max(predicted_intensity)
        for one_mz, one_intensity in zip(predicted_mz, predicted_intensity):
            rows.append(
                {"mz": float(one_mz), "intensity": height * float(one_intensity) / base}
            )
    frame = pd.DataFrame(sorted(rows, key=lambda row: row["mz"]))
    frame.insert(0, "sample_peak_id", [f"peak{index}" for index in range(len(frame))])
    return frame


#: The Orbitrap window a run claims in; the pass matches at the instrument's
#: own precision against a mass the sample's anchor ions have corrected.
ORBI_PPM = 3.0


def _rows(profile: str = "BR", *ions: tuple[str, int, float]) -> list[dict]:
    peaks = _peaks(*(ions or (("Br", -1, 1e6),)))
    hits, _ = claim_reagent_peaks(
        peaks, reagent_library_for(profile), claim_ppm=ORBI_PPM
    )
    return build_reagent_assignments(hits, peaks, "sample1", "run1")


class TestTheReagentRow:
    def test_it_names_the_ion_and_no_analyte(self):
        """The composition is known exactly, and it is the source's, not the
        sample's. An `assigned_formula` here would put a reagent cluster into
        every cross-sample formula vote it touches."""
        row = _rows()[0]

        assert row["role"] == ROLE_REAGENT
        assert row["source"] == SOURCE_REAGENT
        assert row["ion_formula"] == "Br"
        assert row["assigned_formula"] is None
        assert row["ionization_mechanism_id"] is None

    def test_its_tier_says_no_analyte_was_assigned(self):
        """And it is the tier the import path's own coherence rule requires of
        a row that names no formula, so the engine writes what it would accept.
        """
        row = _rows()[0]

        assert row["tier"] == TIER_UNASSIGNED
        assert row["tier"] in coherent_tiers(None, 0.6, 0.3)

    def test_it_records_how_far_off_the_peak_sat(self):
        """On a peak placed at the ion's own mass the error is ~0.013 ppm, the
        residual between IsoSpec's masses and the composition arithmetic this
        library computes its targets with. Recorded on every row, so a claim
        that matched far off its mass says so rather than passing silently."""
        row = _rows()[0]

        assert row["mz_error_ppm"] == pytest.approx(0.0, abs=0.1)

    def test_it_says_which_reagent_ion_claimed_the_peak(self):
        provenance = _rows()[0]["provenance"]["reagent"]

        assert provenance["ion"] == "[Br]-"
        assert provenance["kind"] == "cluster"
        assert provenance["mz"] == pytest.approx(78.9189, abs=5e-4)

    def test_it_names_the_ions_family_and_the_works_that_name_it(self):
        """So a reader checks the claim against a paper, not against the table."""
        provenance = _rows()[0]["provenance"]["reagent"]

        assert provenance["family"] == "reagent"
        assert provenance["references"] == ["san16", "ris19", "wa21"]
        assert "observed" not in provenance

    def test_an_ion_no_work_names_says_what_shows_it(self):
        """Not a citation that names the reagent's family but not the ion."""
        rows = _rows("BR", ("Br", -1, 1e6), ("Br2", -1, 3e5), ("Br3", -1, 1e5))
        tribromide = next(
            row for row in rows if row["provenance"]["reagent"]["ion"] == "[Br3]-"
        )

        observed = tribromide["provenance"]["reagent"]["observed"]
        assert tribromide["provenance"]["reagent"]["references"] == []
        assert observed.startswith("no work found names it; ")
        assert "test spectra show it" in observed

    def test_every_row_lands_on_a_real_peak_of_the_sample(self):
        peaks = _peaks(("Br", -1, 1e6), ("Br2", -1, 3e5))
        hits, _ = claim_reagent_peaks(
            peaks, reagent_library_for("BR"), claim_ppm=ORBI_PPM
        )
        rows = build_reagent_assignments(hits, peaks, "sample1", "run1")

        assert {row["sample_peak_id"] for row in rows} <= set(peaks["sample_peak_id"])
        assert len({row["peak_assignment_id"] for row in rows}) == len(rows)


class TestTheIsotopologueRows:
    def test_an_isotopologue_names_its_label_and_its_predicted_share(self):
        isotopologues = [row for row in _rows() if row["isotope_label"]]

        assert [row["isotope_label"] for row in isotopologues] == ["81Br"]
        assert isotopologues[0]["provenance"]["reagent"]["predicted_relative"] > 0.9

    def test_an_isotopologue_carries_the_reagent_role_too(self):
        """The peak is the source's chemistry as much as its parent is, and G4
        counts it: the reference engine labels these reagent as well."""
        assert {row["role"] for row in _rows()} == {ROLE_REAGENT}

    def test_no_reagent_row_names_an_owner(self):
        """Owner linkage models one thing in this ledger - an isotopologue
        naming the M0 analyte it belongs to - and the import path enforces it,
        so the engine must not write a shape it would then refuse. The parent is
        recorded as provenance instead.
        """
        rows = _rows("BR", ("Br", -1, 1e6), ("Br2", -1, 3e5))

        assert all(row["owner_peak_assignment_id"] is None for row in rows)
        assert (
            owner_link_errors(
                [
                    SimpleNamespace(
                        sample_peak_id=row["sample_peak_id"],
                        owner_sample_peak_id=None,
                        role=row["role"],
                    )
                    for row in rows
                ]
            )
            == []
        )


def _run_pre_pass(peaks: pd.DataFrame, profile: str, instrument_type: str | None):
    """The pre-pass as a run calls it, with the chemistry a run would resolve."""
    from mascope_backend.api.new.peak_assignments.config import (
        PeakAssignmentConfig,
    )
    from mascope_backend.api.new.peak_assignments.profiles import resolve_profile
    from mascope_backend.api.new.peak_assignments.service import (
        _reagent_assignments,
    )

    resolved = resolve_profile(
        PeakAssignmentConfig(profile=profile),
        mechanism_notations=["[M+H]+"],
        instrument_type=instrument_type,
        polarity="+",
    )
    return _reagent_assignments(peaks, resolved, "sample1", "run1")


class TestWhereTheClaimedLinesPutTheAxis:
    """The offset a thin library falls back to is read off every claimed line."""

    @staticmethod
    def _urea(parent_ppm: float, isotopologue_ppm: float) -> pd.DataFrame:
        """The protonated urea ladder, its rungs and their isotopologue lines
        each placed off their own mass by the given amount."""
        rows = []
        for formula, height in (
            ("CH5N2O", 2e6),
            ("C2H9N4O2", 3e6),
            ("C3H13N6O3", 3e5),
        ):
            predicted_mz, predicted_intensity, labels = predict_isotopes(formula, 1)
            base = max(predicted_intensity)
            for one_mz, one_intensity, label in zip(
                predicted_mz, predicted_intensity, labels
            ):
                ppm = parent_ppm if label == "M0" else isotopologue_ppm
                rows.append(
                    {
                        "mz": float(one_mz) * (1.0 + ppm * 1e-6),
                        "intensity": height * float(one_intensity) / base,
                    }
                )
        frame = pd.DataFrame(sorted(rows, key=lambda row: row["mz"]))
        frame.insert(0, "sample_peak_id", [f"p{index}" for index in range(len(frame))])
        return frame

    def test_the_isotopologue_lines_count_as_much_as_their_parents(self):
        # A source's brightest lines are the ones an Orbitrap moves. On the
        # gate's labelled-nitrate set the core ion and its first rung sit 1.3 to
        # 2.5 ppm above their own isotopologue lines, and those sit where the
        # sample's commits do. Here the rungs sit at +1.0 ppm and their five
        # isotopologue lines at -1.3; the rungs alone would say +1.0.
        rows, claimed, offset = _run_pre_pass(self._urea(1.0, -1.3), "UR", "orbi")

        isotopologues = sum(1 for row in rows if row["isotope_label"])
        assert (len(rows) - isotopologues, isotopologues) == (3, 5)
        assert offset.lines == len(rows) == len(claimed)
        assert offset.mu_ppm == pytest.approx(-1.3, abs=0.05)
        assert offset.beyond_width

    def test_the_airs_ions_do_not_move_it(self):
        # Hydronium's ladder and the discharge's N4+ are claimed beside the urea
        # ladder, and sit where the axis bends at the low-mass end, here 3 ppm
        # high. Counted, they would put the median at the rungs' +1.0; the
        # offset stays the reagent's own lines'.
        frame = self._urea(1.0, -1.3)
        air = []
        for formula in ("H7O3", "H9O4", "N4"):
            predicted_mz, _, _ = predict_isotopes(formula, 1)
            air.append(
                {
                    "mz": float(predicted_mz[0]) * (1.0 + 3.0e-6),
                    "intensity": 4.0e5,
                }
            )
        frame = pd.concat([frame, pd.DataFrame(air)], ignore_index=True)
        frame = frame.sort_values("mz", ignore_index=True)
        frame["sample_peak_id"] = [f"p{index}" for index in range(len(frame))]
        rows, _, offset = _run_pre_pass(frame, "UR", "orbi")

        families = [row["provenance"]["reagent"]["family"] for row in rows]
        assert families.count("air") == 3
        assert offset.lines == 8
        assert offset.mu_ppm == pytest.approx(-1.3, abs=0.05)

    def test_the_width_it_must_clear_is_the_instrument_classs(self):
        # The same lines on a TOF, whose class is scored at 3 ppm: read, and
        # inside the width.
        _, _, offset = _run_pre_pass(self._urea(-1.3, -1.3), "UR", "tof")

        assert offset.mu_ppm == pytest.approx(-1.3, abs=0.05)
        assert not offset.beyond_width

    def test_a_pass_that_claimed_nothing_reads_no_offset(self):
        _, claimed, offset = _run_pre_pass(_peaks(("C6H13O6", 1, 1e6)), "UR", "orbi")

        assert not claimed
        assert (offset.mu_ppm, offset.lines, offset.beyond_width) == (None, 0, False)

    def test_a_profile_with_no_reagent_has_no_pass_to_ask(self):
        rows, _, offset = _run_pre_pass(self._urea(-1.3, -1.3), "none", "orbi")

        assert rows == []
        assert offset is None


class TestTheChargeTransferBeam:
    def test_the_fluoranthene_ion_is_claimed_as_the_reagent(self):
        # The EASY-IC source's own beam, in a window that reaches it: a reagent
        # row that names the ion and no analyte, so the brightest aromatic in
        # the spectrum never becomes a fluoranthene assignment.
        rows = _rows("EASYIC_POS", ("C16H10", 1, 1e6))
        assert rows, "the reagent cation must be claimed"
        assert rows[0]["role"] == ROLE_REAGENT
        assert rows[0]["ion_formula"] == "C16H10"
        assert rows[0]["assigned_formula"] is None
        assert rows[0]["provenance"]["reagent"]["ion"] == "[C16H10]+"

    def test_the_negative_beam_is_the_anion(self):
        rows = _rows("EASYIC_NEG", ("C16H10", -1, 1e6))
        assert rows[0]["provenance"]["reagent"]["ion"] == "[C16H10]-"

    def test_the_beam_is_the_calibrant_family(self):
        record = _rows("EASYIC_POS", ("C16H10", 1, 1e6))[0]["provenance"]["reagent"]
        assert record["family"] == "calibrant"
        assert "easyic" in record["references"]


class TestTheAirsIons:
    """What the discharge makes of the air: the source on a charge-transfer
    source, and a reagent source's background."""

    def test_the_discharges_cations_are_claimed_on_a_charge_transfer_source(self):
        # The wide window of the cylinder's charge-transfer set: N3+ is its
        # base peak, with nitronium and N4+ beside it.
        rows = _rows("EASYIC_POS", ("N3", 1, 1e7), ("NO2", 1, 5e4), ("N4", 1, 3e3))
        ions = {
            row["provenance"]["reagent"]["ion"]: row
            for row in rows
            if not row["isotope_label"]
        }

        assert set(ions) == {"[N3]+", "[NO2]+", "[N4]+."}
        for row in ions.values():
            assert row["role"] == ROLE_REAGENT
            assert row["assigned_formula"] is None
            assert row["provenance"]["reagent"]["family"] == "air"
            assert row["provenance"]["reagent"]["references"]

    def test_protonated_water_is_the_airs_on_a_urea_source(self):
        rows = _rows("UR", ("CH5N2O", 1, 1e7), ("H7O3", 1, 2e4))
        families = {
            row["provenance"]["reagent"]["ion"]: row["provenance"]["reagent"]["family"]
            for row in rows
            if not row["isotope_label"]
        }

        assert families == {"[CH4N2O+H]+": "reagent", "[H3O+2xH2O]+": "air"}

    def test_carbonate_and_bicarbonate_are_the_airs_on_a_negative_source(self):
        rows = _rows("EASYIC_NEG", ("CO3", -1, 1e6), ("CHO3", -1, 4e5))
        ions = {
            row["provenance"]["reagent"]["ion"]
            for row in rows
            if not row["isotope_label"]
        }

        assert ions == {"[CO3]-", "[HCO3]-"}

    def test_ammonia_is_left_to_the_stages(self):
        # NH4+ and its hydrates are ammonia's own reading, which a
        # protonated-water source is run to measure.
        assert _rows("EASYIC_POS", ("NH6O", 1, 1e6)) == []


class TestTheFluorinatedIons:
    """Trifluoroacetic acid is an analyte a nitrate or iodide source measures,
    and CF3- and CF3O- ride with it, so none of the three is claimed before the
    stages: a claim there cannot ask whether the acid is in the sample."""

    @pytest.mark.parametrize("profile", ["NO3", "IODIDE", "BR", "EASYIC_NEG"])
    @pytest.mark.parametrize("formula", ["C2F3O2", "CF3O", "CF3"])
    def test_it_is_left_to_the_stages(self, profile, formula):
        assert _rows(profile, (formula, -1, 1e6)) == []


class TestWhenThereIsNothingToClaim:
    def test_a_profile_with_no_reagent_writes_no_rows(self):
        assert _rows("ESI_POS") == []

    def test_an_empty_peak_frame_writes_no_rows(self):
        empty = pd.DataFrame({"sample_peak_id": [], "mz": [], "intensity": []})

        assert (
            claim_reagent_peaks(empty, reagent_library_for("BR"), claim_ppm=ORBI_PPM)[0]
            == []
        )

    def test_a_spectrum_of_analytes_only_writes_no_rows(self):
        peaks = pd.DataFrame(
            {
                "sample_peak_id": ["p1", "p2"],
                "mz": np.array([200.12345, 301.54321]),
                "intensity": np.array([1e6, 5e5]),
            }
        )

        assert (
            claim_reagent_peaks(peaks, reagent_library_for("BR"), claim_ppm=ORBI_PPM)[0]
            == []
        )


class TestWhatStageALosesWithTheClaim:
    """An ion is inverted as a family: one M0 and its isotopologue children,
    the children naming the M0 as their owner. So excluding the single ROW that
    landed on a claimed peak is not enough - it leaves the children behind with
    nothing to belong to, and they invert as ownerless `iso_child` rows with one
    of them relabelled M0. The whole ion has to go.
    """

    @staticmethod
    def _frame() -> pd.DataFrame:
        """One target ion's family: an M0 and two isotopologues."""
        return pd.DataFrame(
            {
                "target_ion_id": ["ion-1", "ion-1", "ion-1", "ion-2"],
                "target_isotope_formula": [
                    "C2H9N4O2",
                    "C1[13C]H9N4O2",
                    "C2H9[15N]N3O2",
                    "C6H13O6",
                ],
                "sample_peak_id": ["p1", "p2", "p3", "p4"],
                "mz": [121.0720, 122.0754, 122.0690, 181.0707],
            }
        )

    def test_claiming_an_m0_drops_its_whole_family(self):
        kept = drop_ions_claimed_elsewhere(self._frame(), {"p1"})

        assert kept["target_ion_id"].tolist() == ["ion-2"]

    def test_another_ion_is_untouched(self):
        kept = drop_ions_claimed_elsewhere(self._frame(), {"p1"})

        assert kept["sample_peak_id"].tolist() == ["p4"]

    def test_a_claimed_child_takes_only_itself(self):
        """Its M0 is still in the ledger, so the rest of the family still has
        something to be children of."""
        kept = drop_ions_claimed_elsewhere(self._frame(), {"p2"})

        assert kept["sample_peak_id"].tolist() == ["p1", "p3", "p4"]

    def test_no_row_survives_on_a_claimed_peak(self):
        """The invariant the ledger needs whichever part of a family was hit:
        one row per peak, and the reagent pass owns these."""
        claimed = {"p1", "p4"}
        kept = drop_ions_claimed_elsewhere(self._frame(), claimed)

        assert not set(kept["sample_peak_id"]) & claimed

    def test_nothing_claimed_changes_nothing(self):
        frame = self._frame()

        assert len(drop_ions_claimed_elsewhere(frame, set())) == len(frame)


#: The charge-transfer source's channels, by the mechanism ids the rows carry.
ELECTRON = "et"
PROTON = "pt"
HYDRIDE = "ha"
CHANNELS = {ELECTRON: "[M]+.", PROTON: "[M+H]+", HYDRIDE: "[M-H]+"}

#: A monoterpene's ladder as these tests state it: three fragments, each
#: claimable up to three times its stated ratio to the parent's ion.
LADDER = FragmentLadder(
    parent="C10H16",
    label="monoterpene",
    fragments=(
        FragmentIon("C6H8", 1, "[C6H8]+.", literature_ratio=1.0),
        FragmentIon("C6H9", 1, "[C6H9]+", literature_ratio=1.0),
        FragmentIon("C6H7", 1, "[C6H7]+", literature_ratio=1.0),
    ),
    references=("nist",),
)


def committed(
    row_id: str,
    formula: str,
    ion: str,
    mechanism: str,
    *,
    mz: float,
    intensity: float,
    tier: str = "assigned",
    role: str = "M0",
    owner: str | None = None,
    displaced: tuple[str, str] | None = None,
    compound: str | None = None,
    label: str | None = None,
) -> dict:
    """A committed row as a stage builds it; ``displaced`` is a same-ion
    reading the election set aside, as ``(neutral, mechanism id)``."""
    return {
        "peak_assignment_id": row_id,
        "peak_assignment_run_id": "run1",
        "sample_item_id": "sample1",
        "sample_peak_id": f"peak-{row_id}",
        "sample_peak_mz": mz,
        "sample_peak_intensity": intensity,
        "sample_peak_tof": None,
        "role": role,
        "assigned_formula": formula,
        "ion_formula": ion,
        "ionization_mechanism_id": mechanism,
        "isotope_label": label,
        "isotope_formula": None,
        "source": "database" if compound else "untargeted",
        "fit_score": 0.9,
        "mz_error_ppm": 0.3,
        "abundance_error": 0.0,
        "tier": tier,
        "target_compound_id": compound,
        "target_ion_id": None,
        "owner_peak_assignment_id": owner,
        "alternatives": (
            None
            if displaced is None
            else [
                {
                    "assigned_formula": displaced[0],
                    "ionization_mechanism_id": displaced[1],
                    "same_ion": True,
                }
            ]
        ),
        "provenance": {},
    }


def pinene(**kwargs) -> dict:
    """The parent: a monoterpene's radical cation, under its band as it sits
    on the proton-transfer cylinder set, where a bright line reads high."""
    kwargs.setdefault("tier", "below_assignability")
    kwargs.setdefault("intensity", 1.0e5)
    return committed("pinene", "C10H16", "C10H16+", ELECTRON, mz=136.1247, **kwargs)


def c6h8(**kwargs) -> dict:
    """Its fragment at 80.062, which the grid reads as a C6H8 molecule."""
    kwargs.setdefault("intensity", 8.0e4)
    return committed("c6h8", "C6H8", "C6H8+", ELECTRON, mz=80.0621, **kwargs)


def claim(rows: list[dict], minor: frozenset[str] = frozenset()):
    return claim_fragments(
        rows, (LADDER,), notation_by_id=CHANNELS, minor_channels=minor
    )


class TestTheFragmentClaim:
    """A fragment read as a molecule is a partner of the wrong kind: it
    corroborates, doubts and outweighs other readings on the strength of an
    analyte that is not there. It is claimed only where the parent is
    committed, where it is no taller than the parent can make it, and where no
    reading of its ion names a molecule the sample shows on a peak of its own."""

    def test_a_fragment_of_a_committed_parent_is_the_sources_ion(self):
        claims = claim([pinene(), c6h8()])

        assert [row["peak_assignment_id"] for row in claims.rows] == ["pinene"]
        (fragment,) = claims.fragments
        assert fragment["role"] == ROLE_REAGENT
        assert fragment["source"] == SOURCE_REAGENT
        assert fragment["assigned_formula"] is None
        assert fragment["ion_formula"] == "C6H8"
        assert fragment["ionization_mechanism_id"] is None
        assert fragment["tier"] == TIER_UNASSIGNED
        assert fragment["sample_peak_id"] == "peak-c6h8"
        assert claims.peak_ids == {"peak-c6h8"}
        record = fragment["provenance"]["reagent"]
        assert (record["kind"], record["family"]) == (KIND_FRAGMENT, FAMILY_FRAGMENT)
        assert record["ion"] == "[C6H8]+."
        assert record["references"] == ["nist"]
        assert record["parent"]["formula"] == "C10H16"
        assert record["parent"]["ratio"] == pytest.approx(0.8)
        assert record["read_as"] == {
            "formula": "C6H8",
            "ionization": "[M]+.",
            "tier": "assigned",
        }
        assert claims.summary["claimed"] == 1
        # A parent under its band is still the parent: the claim reads its
        # height, not its fit.
        assert claims.summary["parents"] == [
            {"formula": "C10H16", "peak_mz": 136.1247, "tier": "below_assignability"}
        ]

    def test_the_fragments_own_line_is_measured_against_its_mass(self):
        # Not against the molecule the stage read it as, whose ion is the same
        # composition, but as a reagent row is measured: from the ion's mass.
        row = c6h8()
        row["sample_peak_mz"] = ion_mz("C6H8", 1) * (1 + 1.5e-6)
        (fragment,) = claim([pinene(), row]).fragments

        assert fragment["mz_error_ppm"] == pytest.approx(1.5, abs=1e-3)

    def test_its_isotopologue_lines_go_with_it(self):
        line = committed(
            "c6h8-13c",
            "C6H8",
            "[13C]C5H8+",
            ELECTRON,
            mz=81.0655,
            intensity=5.0e3,
            role="iso_child",
            owner="c6h8",
            label="13C",
        )
        claims = claim([pinene(), c6h8(), line])

        assert [row["peak_assignment_id"] for row in claims.rows] == ["pinene"]
        assert [row["isotope_label"] for row in claims.fragments] == [None, "13C"]
        assert all(row["role"] == ROLE_REAGENT for row in claims.fragments)
        assert all(row["owner_peak_assignment_id"] is None for row in claims.fragments)
        assert "read_as" not in claims.fragments[1]["provenance"]["reagent"]
        assert claims.summary["claimed_isotopologues"] == 1

    def test_without_its_parent_nothing_is_claimed(self):
        rows = [c6h8()]
        claims = claim(rows)

        assert claims.rows == rows
        assert claims.fragments == []
        assert claims.summary["parents"] == []

    def test_an_opportunistic_channel_makes_no_parent(self):
        # Pinene read only as protonated, where proton transfer is a channel
        # the run opened for itself: no partner, and no parent either.
        parent = committed(
            "pinene", "C10H16", "C10H17+", PROTON, mz=137.1325, intensity=1.0e5
        )
        claims = claim([parent, c6h8()], minor=frozenset({"[M+H]+"}))

        assert claims.fragments == []

    def test_a_peak_taller_than_the_parent_can_make_it_is_left(self):
        # Three times the stated ratio is the allowance; five is something else
        # standing on the fragment's mass.
        claims = claim([pinene(), c6h8(intensity=5.0e5)])

        assert claims.fragments == []
        assert claims.summary["held"]["taller_than_ladder"] == 1

    def test_a_molecule_the_sample_shows_on_a_peak_of_its_own_keeps_its_ion(self):
        # C6H7+ is the ladder's and protonated benzene's. Benzene's own radical
        # cation is a peak outside the ladder, so the ion stays benzene's.
        protonated_benzene = committed(
            "c6h7",
            "C6H6",
            "C6H7+",
            PROTON,
            mz=79.0542,
            intensity=5.0e4,
            displaced=("C6H8", HYDRIDE),
        )
        benzene = committed(
            "benzene", "C6H6", "C6H6+", ELECTRON, mz=78.0464, intensity=2.0e4
        )
        claims = claim([pinene(), protonated_benzene, benzene])

        assert claims.fragments == []
        assert claims.summary["held"]["shown_elsewhere"] == 1

    def test_so_does_a_reading_the_election_displaced(self):
        # The ion elected as C6H8 less a hydride, with protonated benzene the
        # reading it set aside: benzene shown on a peak of its own keeps the
        # ion off the ladder whichever reading the election took.
        row = committed(
            "c6h7",
            "C6H8",
            "C6H7+",
            HYDRIDE,
            mz=79.0542,
            intensity=5.0e4,
            displaced=("C6H6", PROTON),
        )
        benzene = committed(
            "benzene", "C6H6", "C6H6+", ELECTRON, mz=78.0464, intensity=2.0e4
        )
        claims = claim([pinene(), row, benzene])

        assert claims.fragments == []
        assert claims.summary["held"]["shown_elsewhere"] == 1

    def test_a_molecule_the_sample_shows_only_below_candidate_does_not(self):
        protonated_benzene = committed(
            "c6h7", "C6H6", "C6H7+", PROTON, mz=79.0542, intensity=5.0e4
        )
        benzene = committed(
            "benzene",
            "C6H6",
            "C6H6+",
            ELECTRON,
            mz=78.0464,
            intensity=2.0e4,
            tier="below_assignability",
        )
        claims = claim([pinene(), protonated_benzene, benzene])

        assert [row["sample_peak_id"] for row in claims.fragments] == ["peak-c6h7"]

    def test_the_ladders_own_peaks_show_nothing(self):
        # C6H8 read through a proton on 81.070 is another of the parent's
        # fragments, not the sample showing C6H8: both are claimed.
        protonated = committed(
            "c6h9", "C6H8", "C6H9+", PROTON, mz=81.0699, intensity=9.0e4
        )
        claims = claim([pinene(), c6h8(), protonated])

        assert sorted(row["sample_peak_id"] for row in claims.fragments) == [
            "peak-c6h8",
            "peak-c6h9",
        ]
        assert [row["peak_assignment_id"] for row in claims.rows] == ["pinene"]

    def test_the_parents_own_peaks_are_never_taken(self):
        protonated_parent = committed(
            "pinene-h", "C10H16", "C10H17+", PROTON, mz=137.1325, intensity=4.0e4
        )
        claims = claim([pinene(), protonated_parent, c6h8()])

        assert [row["peak_assignment_id"] for row in claims.rows] == [
            "pinene",
            "pinene-h",
        ]

    def test_a_parents_own_ion_is_never_its_fragment(self):
        # A ladder may name an ion the parent is also read as: C10H15+ is a
        # monoterpene's hydrogen-loss fragment and the monoterpene less a
        # hydride. Read that way, through the mode's own channel, the row is the
        # parent, not a fragment of it.
        ladder = FragmentLadder(
            parent="C10H16",
            label="monoterpene",
            fragments=(FragmentIon("C10H15", 1, "[C10H15]+", literature_ratio=1.0),),
            references=("nist",),
        )
        less_a_hydride = committed(
            "pinene-h", "C10H16", "C10H15+", HYDRIDE, mz=135.1168, intensity=5.0e4
        )
        claims = claim_fragments(
            [pinene(), less_a_hydride], (ladder,), notation_by_id=CHANNELS
        )

        assert claims.fragments == []
        assert len(claims.rows) == 2

    def test_a_target_library_compound_keeps_its_peak(self):
        claims = claim([pinene(), c6h8(compound="tc-1")])

        assert claims.fragments == []
        assert claims.summary["held"]["target_library"] == 1

    def test_the_rows_passed_in_are_not_modified(self):
        rows = [pinene(), c6h8()]
        before = copy.deepcopy(rows)
        claim(rows)

        assert rows == before

    def test_a_profile_with_no_ladder_claims_nothing(self):
        rows = [pinene(), c6h8()]
        claims = claim_fragments(rows, (), notation_by_id=CHANNELS)

        assert claims.rows == rows
        assert (claims.fragments, claims.peak_ids) == ([], set())

    def test_the_run_records_what_the_claim_took(self):
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.engine import FRAGMENTS_KEY

        claims = claim([pinene(), c6h8()])
        stored = service._stored_run_config(
            PeakAssignmentConfig(), fragments=claims.summary
        )
        assert stored[FRAGMENTS_KEY]["claims"] == [
            {
                "ion": "[C6H8]+.",
                "mz": 80.0621,
                "parent": "C10H16",
                "ratio": 0.8,
                "read_as": "C6H8",
            }
        ]
        assert FRAGMENTS_KEY not in service._stored_run_config(PeakAssignmentConfig())

    def test_the_shipped_ladder_on_the_proton_transfer_cylinder(self):
        # A proton-transfer file of the certified cylinder, heights as the
        # pass-9 ledger shows them: pinene's radical cation under its band,
        # its C6H8+. read as a molecule the bottle does not hold, and three
        # fragment masses that are also a certified component's ion, each with
        # the component's own radical cation on a peak of its own.
        rows = [
            pinene(intensity=1.5e4),
            c6h8(intensity=1.2e4),
            committed(
                "c6h7",
                "C6H6",
                "C6H7+",
                PROTON,
                mz=79.0542,
                intensity=3.6e4,
                displaced=("C6H8", HYDRIDE),
            ),
            committed(
                "benzene", "C6H6", "C6H6+", ELECTRON, mz=78.0464, intensity=8.8e3
            ),
            committed("c7h9", "C7H8", "C7H9+", PROTON, mz=93.0699, intensity=1.5e5),
            committed(
                "toluene", "C7H8", "C7H8+", ELECTRON, mz=92.0621, intensity=1.8e5
            ),
            committed(
                "c5h7",
                "C5H6",
                "C5H7+",
                PROTON,
                mz=67.0542,
                intensity=6.4e4,
                displaced=("C5H8", HYDRIDE),
            ),
            committed(
                "isoprene", "C5H8", "C5H8+", ELECTRON, mz=68.0621, intensity=5.0e4
            ),
        ]
        claims = claim_fragments(
            rows,
            fragment_ladders_for("EASYIC_POS"),
            notation_by_id=CHANNELS,
            minor_channels=frozenset({"[M-H]+"}),
        )

        assert [row["sample_peak_id"] for row in claims.fragments] == ["peak-c6h8"]
        # C5H7+ stands 4.3 times pinene's ion: inside the three-fold allowance
        # over the spectrum's 1.5, so it is the reading, not the ratio, that
        # keeps it isoprene's.
        assert claims.summary["held"] == {
            "taller_than_ladder": 0,
            "shown_elsewhere": 3,
            "target_library": 0,
        }

    def test_a_fragment_claimed_is_no_rival_to_the_declared_reading(self):
        # The proton-transfer cylinder set's case: protonated benzene through
        # the mode's own proton transfer, whose hydride reading names C6H8, and
        # C6H8 seen fifty times as brightly as benzene through electron
        # transfer - on the monoterpene's fragment. Read as a molecule, the
        # fragment holds benzene at candidate; read as the fragment it is, it
        # doubts nothing, and benzene's own radical cation settles the ion.
        def ledger() -> list[dict]:
            return [
                pinene(),
                committed(
                    "c6h7",
                    "C6H6",
                    "C6H7+",
                    PROTON,
                    mz=79.0542,
                    intensity=1.0e4,
                    displaced=("C6H8", HYDRIDE),
                ),
                committed(
                    "benzene", "C6H6", "C6H6+", ELECTRON, mz=78.0464, intensity=1.0e3
                ),
                c6h8(intensity=5.0e4),
            ]

        def benzene_row(rows: list[dict]) -> dict:
            apply_cross_channel(
                rows,
                notation_by_id=CHANNELS,
                minor_channels=frozenset({"[M-H]+"}),
            )
            return next(row for row in rows if row["peak_assignment_id"] == "c6h7")

        unclaimed = benzene_row(ledger())
        assert unclaimed["tier"] == "candidate"
        assert unclaimed["provenance"]["cross_channel"]["reason"] == (
            REASON_AMBIGUOUS_ADDUCT
        )

        claims = claim(ledger(), minor=frozenset({"[M-H]+"}))
        assert [row["sample_peak_id"] for row in claims.fragments] == ["peak-c6h8"]
        claimed = benzene_row(claims.rows)
        assert claimed["tier"] == "assigned"
        assert claimed["provenance"]["cross_channel"][SAME_ION_SETTLED]["by"] == (
            SETTLED_BY_SECOND_CHANNEL
        )
