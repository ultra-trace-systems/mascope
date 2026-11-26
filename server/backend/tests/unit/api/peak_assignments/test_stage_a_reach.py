"""Stage A resolves the lines of only the reference ions that can reach a peak.

Most of the reference lists' ions have no peak near any line of theirs in a
sample, and resolving their lines at the file was most of an arrival's Stage
A with the lists loaded (MAS-1282). Before any line is resolved, an ion is
kept where one of its fine-line stretches comes within the gate's mass window
of a peak, or within the matcher's own window of a claimed peak
(``target_ions_compute.ions_within_reach``). The ions left out are handed to
the readers that read them in Stage A's frame, so a run is the same either
way: the rows, the fits a re-score reads, the confidence curve's notations,
the lists' names for a formula and the neutrals the lists name.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    FileLines,
    ions_within_reach,
    lines_at_file,
)
from mascope_backend.api.new.peak_assignments import (
    judging_loop,
    sample_inputs,
    stage_a_pass,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.row_terms import (
    REFERENCE_IDENTITIES_COL,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    MatchReach,
    _build_reference_isotopes_df,
)
from mascope_match.compute import isotopes as match_isotopes
from mascope_match.params import TofMatchParams
from mascope_reference import KnownComposition, KnownIdentity
from mascope_tools.composition.calibration import Calibration
from mascope_tools.composition.line_model import HeightResolution
from mascope_tools.composition.mass_accuracy import SampleMassAccuracy
from mascope_tools.composition.measurement_model import MeasurementModel


def _mechanism(mech_id, notation, polarity="-"):
    return SimpleNamespace(
        ionization_mechanism_id=mech_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity=polarity,
    )


DEPROTONATED = _mechanism("m-deprot", "[M-H]-")
BROMIDE = _mechanism("m-br", "[M+Br]-")
CHLORIDE = _mechanism("m-cl", "[M+Cl]-")
NITRATE = _mechanism("m-no3", "[M+NO3]-")


def _known(formula, names=None):
    return KnownComposition(
        formula=formula,
        monoisotopic_mass=0.0,
        identities=[
            KnownIdentity(
                name=name,
                source="test-list",
                license="custom",
                inchikey=None,
                source_native_id=name,
                xrefs={},
            )
            for name in (names or [formula])
        ],
    )


def _fine(formulas, mechanisms=(DEPROTONATED, BROMIDE)):
    return _build_reference_isotopes_df(
        [_known(formula) for formula in formulas], list(mechanisms)
    )


def _ion(fine, formula, mechanism):
    return fine[
        (fine["target_compound_formula"] == formula)
        & (fine["ionization_mechanism_id"] == mechanism.ionization_mechanism_id)
    ]


def _monoisotopic_mz(ion) -> float:
    return float(ion["mz"].min())


def _reached(fine, file_lines, peaks, tolerance=5.0, **near):
    """The ion ids :func:`ions_within_reach` keeps."""
    kept = ions_within_reach(fine, file_lines, np.asarray(peaks), tolerance, **near)
    return set(fine.loc[kept, "target_ion_id"])


class TestTheWindow:
    """The edge of the gate's window, read off the fine lines."""

    def test_an_ion_just_inside_the_window_is_kept_and_just_outside_dropped(self):
        fine = _fine(["C10H16O3"], [DEPROTONATED])
        mz = _monoisotopic_mz(fine)
        ion = fine["target_ion_id"].iloc[0]
        for ppm, kept in ((4.99, True), (-4.99, True), (5.01, False), (-5.01, False)):
            peak = mz * (1 + ppm * 1e-6)
            assert (ion in _reached(fine, FileLines("HIGH"), [peak])) is kept, ppm

    def test_a_peak_on_the_window_s_edge_is_in_it(self):
        fine = _fine(["C10H16O3"], [DEPROTONATED])
        mz = _monoisotopic_mz(fine)
        assert _reached(fine, FileLines("HIGH"), [mz * (1 + 5e-6)])

    def test_a_peak_the_matcher_rounds_into_the_window_is_in_it(self):
        # The gate reads 1e6 * (peak - line) / line; one float step past the
        # window's edge can still read as on it.
        for mz in np.random.default_rng(1282).uniform(50.0, 2000.0, 5000):
            past = np.nextafter(mz * (1 + 20e-6), np.inf)
            if 1e6 * (past - mz) / mz <= 20.0:
                break
        else:
            pytest.skip("no float step rounds into the window")
        fine = pd.DataFrame(
            {
                "target_ion_id": ["ion-1"],
                "target_isotope_id": ["iso-1"],
                "target_isotope_formula": ["a"],
                "mz": [mz],
                "relative_abundance": [1.0],
            }
        )
        assert _reached(fine, FileLines("HIGH"), [past], tolerance=20.0)

    def test_an_ion_whose_only_peak_is_a_heavier_line_is_kept(self):
        # A dibromide's monoisotopic 79Br2 line has no peak; its 79Br81Br line,
        # its brightest, has one. The ion is a contender on that peak and
        # scores on its base line, so the test is any line, not the M0's.
        fine = _fine(["CH2Br2"], [DEPROTONATED])
        lines = lines_at_file(fine, FileLines("HIGH"))
        brightest = float(lines.loc[lines["relative_abundance"].idxmax(), "mz"])
        assert brightest > _monoisotopic_mz(fine) + 1.5
        assert _reached(fine, FileLines("HIGH"), [brightest * (1 + 3e-6)])
        assert not _reached(
            fine, FileLines("HIGH"), [_monoisotopic_mz(fine) * (1 - 50e-6)]
        )

    def test_a_merged_line_reaches_a_peak_none_of_its_members_does(self):
        # Two equal lines 4 mDa apart, one line at a resolving power of 2000:
        # at their abundance-weighted centre, 10 ppm from either of them. A
        # peak there is within the window of the line, though of neither of
        # the fine lines it holds.
        fine = pd.DataFrame(
            {
                "target_ion_id": ["ion-1", "ion-1"],
                "target_isotope_id": ["iso-1", "iso-2"],
                "target_isotope_formula": ["a", "b"],
                "mz": [199.998, 200.002],
                "relative_abundance": [1.0, 1.0],
            }
        )
        file_lines = FileLines("LOW", lambda mz: 2000.0)
        (line,) = lines_at_file(fine, file_lines)["mz"]
        assert line == pytest.approx(200.0)
        assert _reached(fine, file_lines, [200.0])
        assert not _reached(fine, FileLines("HIGH"), [200.0])

        # A resolving power that reads one m/z at a time is read so.
        def one_at_a_time(mz):
            return 2000.0 if mz > 0 else float("nan")

        assert _reached(fine, FileLines("LOW", one_at_a_time), [200.0])

    def test_a_line_joined_from_lines_under_one_width_apart_reaches_its_peak(
        self,
    ):
        # Two lines under the abundance floor, 0.095 Da apart: 0.95 of the
        # line width at a resolving power of 2000. The line model joins them
        # into one line, over the floor, at their centre. A peak there keeps
        # the ion, though it is 0.0475 Da from either of them: the gap the
        # reach test spans may not fall below the line model's one width.
        fine = pd.DataFrame(
            {
                "target_ion_id": ["ion-1"] * 3,
                "target_isotope_id": ["iso-1", "iso-2", "iso-3"],
                "target_isotope_formula": ["a", "b", "c"],
                "mz": [200.0, 201.0, 201.095],
                "relative_abundance": [1.0, 0.006, 0.006],
            }
        )
        file_lines = FileLines("LOW", lambda mz: 2000.0)
        joined = lines_at_file(fine, file_lines, 0.01)["mz"]
        assert joined.tolist() == pytest.approx([200.0, 201.0475])
        assert _reached(fine, file_lines, [201.0475])
        assert not _reached(fine, file_lines, [201.2])

    def test_a_claimed_peak_within_the_matcher_s_window_keeps_an_ion(self):
        # The claim drop reads a line's raw pairing, up to half a dalton off:
        # an ion that could be paired to a claimed peak stays to be dropped.
        fine = _fine(["C10H16O3"], [DEPROTONATED])
        mz = _monoisotopic_mz(fine)
        far = [mz + 3.0]
        assert not _reached(fine, FileLines("HIGH"), far)
        for offset, kept in ((0.49, True), (0.51, False)):
            near = {"near_mzs": np.array([mz - offset]), "near_window": 0.5}
            assert bool(_reached(fine, FileLines("HIGH"), far, **near)) is kept

    @pytest.mark.parametrize(
        "file_lines",
        [
            FileLines("HIGH"),
            FileLines("LOW"),
            FileLines("LOW", lambda mz: 2000.0),
            FileLines("LOW", lambda mz: 15.0 * mz),
            FileLines("HIGH", HeightResolution(lambda mz: 40000.0)),
            FileLines("HIGH", HeightResolution(lambda mz: 1500.0)),
            FileLines("HIGH", lambda mz: float("nan")),
        ],
    )
    def test_no_ion_it_drops_has_a_resolved_line_in_the_window(self, file_lines):
        """Every ion left out, resolved after all, has no line within the
        window of any peak, whatever the file; and a peak on one of its
        resolved lines keeps every ion."""
        fine = _fine(["C10H16O3", "C6Cl6", "CH2Br2", "C2HBr3", "C12H4Cl4O2", "C30H50O"])
        lines = lines_at_file(fine, file_lines)
        rng = np.random.default_rng(1282)
        spread = rng.uniform(-60e-6, 60e-6, size=len(lines))
        tolerance = 5.0
        for trial in range(20):
            chosen = rng.random(len(lines)) < 0.15
            peaks = (lines["mz"].to_numpy() * (1 + spread))[chosen]
            kept = _reached(fine, file_lines, peaks, tolerance)
            sorted_peaks = np.sort(peaks)
            for ion, own in lines.groupby("target_ion_id"):
                mz = own["mz"].to_numpy()
                position = np.searchsorted(sorted_peaks, mz)
                near = [
                    np.min(np.abs(sorted_peaks[max(p - 1, 0) : p + 1] - m)) / m * 1e6
                    for p, m in zip(position, mz)
                    if sorted_peaks.size
                ]
                if near and min(near) <= tolerance:
                    assert ion in kept, (trial, ion)
        on_lines = lines["mz"].to_numpy()
        assert _reached(fine, file_lines, on_lines) == set(lines["target_ion_id"])


# --- Stage A's first pass, with and without the ions left out -----------------

#: The lists' formulas: through both mechanisms each.
_FORMULAS = ["C10H16O3", "C9H14O4", "CH2Br2", "C8H12O5", "C7H10O4", "C6H6O3"]

#: The target library's formula, through a mechanism the lists are not read
#: through: its row takes the lists' names from their ions alone, which no
#: peak reaches.
_TARGET_FORMULA = "C7H10O4"


def _target_fine():
    fine = _build_reference_isotopes_df([_known(_TARGET_FORMULA)], [CHLORIDE])
    fine = fine.drop(columns=[REFERENCE_IDENTITIES_COL])
    fine["target_compound_id"] = "tc-1"
    fine["target_ion_id"] = "target-ion-1"
    fine["target_isotope_id"] = [f"target-iso-{i}" for i in range(len(fine))]
    return fine


def _spectrum(file_lines, fine, target):
    """Peaks on the lines of a few ions, a near miss of another, and the
    reagent's claimed peak beside a fourth: (ids, m/z, intensities), and the
    claimed ids."""
    lines = lines_at_file(fine, file_lines, 1e-4)
    target_lines = lines_at_file(target, file_lines, 1e-4)
    peaks = []

    def on(frame, formula, mechanism, scale, ppm=0.0, only=None):
        ion = frame[
            (frame["target_compound_formula"] == formula)
            & (frame["ionization_mechanism_id"] == mechanism.ionization_mechanism_id)
        ]
        ion = ion[ion["relative_abundance"] > 0.01]
        if only is not None:
            ion = ion.iloc[[only]]
        for mz, share in zip(ion["mz"], ion["relative_abundance"]):
            peaks.append((mz * (1 + ppm * 1e-6), scale * share))

    on(lines, "C10H16O3", DEPROTONATED, 1e6, ppm=0.4)
    on(lines, "C9H14O4", BROMIDE, 5e5, ppm=-0.6)
    # The dibromide's brightest line alone.
    brightest = lines[
        (lines["target_compound_formula"] == "CH2Br2")
        & (lines["ionization_mechanism_id"] == "m-deprot")
    ]["relative_abundance"].idxmax()
    peaks.append((float(lines.loc[brightest, "mz"]) * (1 + 1e-6), 3e5))
    # A near miss: every line of it 7 ppm off.
    on(lines, "C8H12O5", DEPROTONATED, 2e5, ppm=7.0)
    on(target_lines, _TARGET_FORMULA, CHLORIDE, 4e5, ppm=0.2)
    # The reagent's peak, 0.2 Da beside an ion no other peak reaches.
    c6 = lines[
        (lines["target_compound_formula"] == "C6H6O3")
        & (lines["ionization_mechanism_id"] == "m-br")
    ]
    peaks.append((float(c6["mz"].min()) + 0.2, 2e6))
    ids = [f"p{index}" for index in range(len(peaks))]
    mzs = np.array([mz for mz, _ in peaks])
    intensities = np.array([intensity for _, intensity in peaks])
    return ids, mzs, intensities, {ids[-1]}


def _timeseries(ids, mzs, intensities):
    """The peaks as ``mascope_match.compute.isotopes.load_peaks`` reads them."""
    return xr.DataArray(
        np.tile(intensities, (3, 1)),
        dims=("time", "mz"),
        coords={
            "time": np.arange(3),
            "mz": mzs,
            "peak_id": ("mz", np.array(ids, dtype=object)),
            "tof": ("mz", np.full(len(ids), -1.0)),
        },
    )


def _claimed_target_spectrum(file_lines, fine, target):
    """Peaks on the target ion's lines alone, every one claimed, and one free
    peak no ion reaches: what leaves the claim drop an empty frame."""
    lines = lines_at_file(target, file_lines, 1e-4)
    lines = lines[lines["relative_abundance"] > 0.01]
    ids = [f"p{index}" for index in range(len(lines))]
    mzs = np.r_[lines["mz"].to_numpy(float), 999.123]
    intensities = np.r_[4e5 * lines["relative_abundance"].to_numpy(float), 1e5]
    return [*ids, "free"], mzs, intensities, set(ids)


async def _first_pass(file_lines, *, filtered: bool, spectrum=_spectrum):
    fine = _fine(_FORMULAS, (DEPROTONATED, BROMIDE, NITRATE))
    target = _target_fine()
    ids, mzs, intensities, claimed = spectrum(file_lines, fine, target)
    series = _timeseries(ids, mzs, intensities)
    match_params = TofMatchParams()
    store = SimpleNamespace(
        mz=SimpleNamespace(values=mzs), peak_id=SimpleNamespace(values=np.array(ids))
    )

    async def matcher(*, filename, target_isotopes_df, polarity):
        return match_isotopes._match_on_peaks(target_isotopes_df, series, None, "tof")

    def every_ion(fine, *_args, **_kwargs):
        return np.ones(len(fine), dtype=bool)

    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="sample",
        filename="sample.h5",
        polarity="-",
    )
    target_lines = lines_at_file(
        target, file_lines, match_params.isotope_abundance_threshold
    )
    patches = [
        patch.object(sample_inputs.m_io, "load_peak_data", return_value=store),
        patch.object(
            sample_inputs,
            "_fetch_known_target_isotopes",
            AsyncMock(return_value=target_lines),
        ),
        patch.object(
            sample_inputs, "_reference_fine_lines", AsyncMock(return_value=fine)
        ),
        patch.object(sample_inputs, "get_instrument_type", return_value="tof"),
        patch.object(stage_a_pass, "get_instrument_type", return_value="tof"),
        patch.object(stage_a_pass, "compute_match_isotopes", matcher),
        patch.object(
            stage_a_pass,
            "load_calibration",
            AsyncMock(return_value=Calibration(a=4.0, b=-2.0, instrument="tof")),
        ),
    ]
    if not filtered:
        patches.append(patch.object(sample_inputs, "ions_within_reach", every_ion))
    for one in patches:
        one.start()
    try:
        return await stage_a_pass._stage_a_first_pass(
            sample,
            PeakAssignmentConfig(),
            match_params,
            ["m-cl"],
            [DEPROTONATED, BROMIDE, NITRATE],
            "run-1",
            claimed,
            fallback_sigma_ppm=1.0,
            known_window=None,
            resolution=file_lines.resolution,
        )
    finally:
        for one in reversed(patches):
            one.stop()


def _rows(first) -> list[dict]:
    """The rows, each assignment's id named by its peak."""
    by_id = {row["peak_assignment_id"]: row["sample_peak_id"] for row in first.rows}

    def named(value):
        if isinstance(value, dict):
            return {key: named(item) for key, item in value.items()}
        if isinstance(value, list):
            return [named(item) for item in value]
        return by_id.get(value, value) if isinstance(value, str) else value

    return sorted(
        (named(row) for row in first.rows), key=lambda row: str(row["sample_peak_id"])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_lines",
    [
        FileLines("LOW", lambda mz: 3000.0),
        FileLines("HIGH", HeightResolution(lambda mz: 60000.0)),
    ],
    ids=["tof", "orbitrap"],
)
async def test_the_first_pass_is_the_same_with_the_ions_left_out(file_lines):
    filtered = await _first_pass(file_lines, filtered=True)
    whole = await _first_pass(file_lines, filtered=False)

    # Most of the lists' ions were left out, and none of the whole run's.
    assert whole.absent.empty
    left_out = set(filtered.absent["target_ion_id"])
    assert len(left_out) > len(set(filtered.scored["target_ion_id"])) / 2
    # The ion beside the claimed peak stayed in, to be dropped as claimed.
    c6 = filtered.absent[filtered.absent["target_compound_formula"] == "C6H6O3"]
    assert set(c6["ionization_mechanism_id"]) == {"m-deprot", "m-no3"}

    assert filtered.rows
    assert _rows(filtered) == _rows(whole)
    assert filtered.listed_formulas == whole.listed_formulas
    assert filtered.curve.notation_by_mechanism == whole.curve.notation_by_mechanism
    assert filtered.mass_accuracy == whole.mass_accuracy
    for model in (
        MeasurementModel.v1(filtered.mass_accuracy, 1.0),
        MeasurementModel.v1(SampleMassAccuracy(mu_ppm=2.0, sigma_ppm=0.8), 1.0),
    ):
        assert judging_loop.fits_at(
            model, filtered.scored, None, filtered.absent
        ) == judging_loop.fits_at(model, whole.scored, None, whole.absent)
    # The target library's row takes the lists' names from the ions left out.
    (row,) = [
        r
        for r in filtered.rows
        if r.get("target_compound_id") == "tc-1" and r["role"] == "M0"
    ]
    assert row["provenance"]["reference_identities"]
    assert _TARGET_FORMULA in set(filtered.absent["target_compound_formula"])
    listed = filtered.scored[filtered.scored["target_compound_id"].isna()]
    assert _TARGET_FORMULA not in set(listed["target_compound_formula"])
    # The nitrate adducts reach no peak: the curve names it off them alone.
    assert "m-no3" not in set(filtered.scored["ionization_mechanism_id"])
    assert "m-no3" in filtered.curve.notation_by_mechanism


@pytest.mark.asyncio
async def test_a_frame_the_claim_drop_empties_still_reads_the_ions_left_out():
    # Every peak a kept ion reaches is claimed: the claim drop leaves the
    # filtered run no frame, where the whole run's held the left-out ions,
    # each at 0. The re-score reads them at 0 all the same.
    file_lines = FileLines("LOW", lambda mz: 3000.0)
    spectrum = _claimed_target_spectrum
    filtered = await _first_pass(file_lines, filtered=True, spectrum=spectrum)
    whole = await _first_pass(file_lines, filtered=False, spectrum=spectrum)
    assert filtered.scored is None or filtered.scored.empty
    assert not filtered.absent.empty
    assert _rows(filtered) == _rows(whole)
    model = MeasurementModel.v1(SampleMassAccuracy(mu_ppm=2.0, sigma_ppm=0.8), 1.0)
    fits = judging_loop.fits_at(model, filtered.scored, None, filtered.absent)
    assert fits[judging_loop.SOURCE_DATABASE]
    assert fits == judging_loop.fits_at(model, whole.scored, None, whole.absent)


class TestTheReadersOfTheIonsLeftOut:
    """Each reader of Stage A's frame reads the ions left out as it read them
    in the frame."""

    ABSENT = pd.DataFrame(
        {
            "target_ion_id": ["ion-b", "ion-d"],
            "target_compound_formula": ["C2H4O2", "C3H6O3"],
            "ionization_mechanism_id": ["m-deprot", "m-br"],
            "ionization_mechanism": ["[M-H]-", "[M+Br]-"],
            REFERENCE_IDENTITIES_COL: [[{"name": "b"}], [{"name": "d"}]],
        }
    )

    def test_a_re_score_reads_an_ion_left_out_at_zero(self, monkeypatch):
        frame = pd.DataFrame(
            {
                "target_compound_formula": ["C2H4O2"],
                "ionization_mechanism_id": ["m-br"],
                "target_ion_id": ["ion-a"],
            }
        )
        monkeypatch.setattr(
            judging_loop, "score_frame_at", lambda df, model: pd.Series(0.7, df.index)
        )
        fits = judging_loop.fits_at(None, frame, None, self.ABSENT)
        assert fits[judging_loop.SOURCE_DATABASE] == {
            ("C2H4O2", "m-br"): 0.7,
            ("C2H4O2", "m-deprot"): 0.0,
            ("C3H6O3", "m-br"): 0.0,
        }
        # A reading the frame scores keeps its own fit.
        same = self.ABSENT.assign(ionization_mechanism_id="m-br")
        fits = judging_loop.fits_at(None, frame, None, same)
        assert fits[judging_loop.SOURCE_DATABASE][("C2H4O2", "m-br")] == 0.7

    def test_the_curve_names_the_mechanisms_of_the_ions_left_out(self):
        frame = pd.DataFrame(
            {"ionization_mechanism_id": ["m-cl"], "ionization_mechanism": ["[M+Cl]-"]}
        )
        curve = stage_a_pass.ConfidenceCurve.of(
            Calibration(a=1.0, b=0.0), frame, self.ABSENT
        )
        assert curve.notation_by_mechanism == {
            "m-cl": "[M+Cl]-",
            "m-deprot": "[M-H]-",
            "m-br": "[M+Br]-",
        }

    def test_the_lists_names_come_in_the_known_set_s_order(self):
        # Two ions of one neutral, spelled two ways: the first in the known
        # set's order names it, whether it was left out or not.
        frame = pd.DataFrame(
            {
                "target_ion_id": ["ion-c", "ion-c"],
                "target_compound_formula": ["O2C2H4", "O2C2H4"],
                REFERENCE_IDENTITIES_COL: [[{"name": "c"}], [{"name": "c"}]],
            }
        )
        order = {"ion-b": 0, "ion-c": 1, "ion-d": 2}
        names = stage_a_pass.reference_names_with_absent(frame, self.ABSENT, order)
        assert names["C2H4O2"] == [{"name": "b"}]
        assert names["C3H6O3"] == [{"name": "d"}]
        later = {"ion-b": 3, "ion-c": 1, "ion-d": 2}
        assert stage_a_pass.reference_names_with_absent(frame, self.ABSENT, later)[
            "C2H4O2"
        ] == [{"name": "c"}]

    def test_the_lists_neutrals_include_the_ions_left_out(self):
        known = sample_inputs.StageAKnown(
            pd.DataFrame({"target_compound_formula": ["C9H14O4"]}), self.ABSENT, {}
        )
        assert stage_a_pass._listed_formulas(known) == {
            "C9H14O4",
            "C2H4O2",
            "C3H6O3",
        }


@pytest.mark.asyncio
async def test_the_reach_reads_the_store_on_the_run_s_axis():
    store = SimpleNamespace(
        mz=SimpleNamespace(values=np.array([300.0, 100.0, 200.0])),
        peak_id=SimpleNamespace(values=np.array([7, 5, 6])),
    )
    sample = SimpleNamespace(filename="sample.raw")
    with (
        patch.object(sample_inputs.m_io, "load_peak_data", return_value=store),
        patch.object(sample_inputs, "shift_mz", lambda mz: mz + 1.0),
    ):
        reach = await sample_inputs.match_reach(sample, 3.0, {"6", "7"})
    assert reach.peak_mzs.tolist() == [101.0, 201.0, 301.0]
    assert reach.claimed_mzs.tolist() == [201.0, 301.0]
    assert reach.tolerance_ppm == 3.0
    assert isinstance(reach, MatchReach)


@pytest.mark.asyncio
async def test_a_decoy_s_stage_a_hands_on_the_ions_left_out():
    from mascope_backend.api.new.peak_assignments import pipeline
    from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
    from mascope_tools.composition.envelope_tolerance import (
        FORM_DEFAULT,
        EnvelopeLines,
        fit_envelope_tolerance,
    )

    first = stage_a_pass._StageAFirstPass(
        rows=[],
        scored=pd.DataFrame(),
        matched=pd.DataFrame(),
        mass_accuracy=SampleMassAccuracy(),
        confidence_calibration=None,
        listed_formulas=frozenset(),
        absent=pd.DataFrame({"target_ion_id": ["ion-2"]}),
    )
    inherited = RunEnvelopeFit(fit_envelope_tolerance(FORM_DEFAULT, EnvelopeLines()))
    with patch.object(pipeline, "_stage_a_first_pass", AsyncMock(return_value=first)):
        result = await pipeline._stage_a_assignments(
            SimpleNamespace(sample_item_id="si-1", filename="sample.raw"),
            PeakAssignmentConfig(),
            SimpleNamespace(isotope_abundance_threshold=0.01),
            ["m-1"],
            [],
            "run-1",
            fallback_sigma_ppm=0.6,
            known_window=None,
            inherited_envelope=inherited,
        )
    assert result[3] is inherited
    assert result[7] is first.absent


class TestTheKnownSetStageAReads:
    """``sample_inputs._fetch_stage_a_known``: the frame, the ions left out
    and the known set's order of them."""

    SAMPLE = SimpleNamespace(sample_item_name="sample", filename="sample.h5")

    async def _known(self, target, fine, peaks, claimed=()):
        reach = MatchReach(np.sort(np.asarray(peaks, float)), 5.0, np.asarray(claimed))
        with (
            patch.object(
                sample_inputs,
                "_fetch_known_target_isotopes",
                AsyncMock(return_value=target),
            ),
            patch.object(
                sample_inputs, "_reference_fine_lines", AsyncMock(return_value=fine)
            ),
            patch.object(sample_inputs, "get_instrument_type", return_value="orbi"),
        ):
            return await sample_inputs._fetch_stage_a_known(
                self.SAMPLE,
                1e-4,
                ["m-cl"],
                [DEPROTONATED],
                known_window=None,
                reach=reach,
                resolution=None,
            )

    @pytest.mark.asyncio
    async def test_the_ions_out_of_reach_come_back_without_their_lines(self):
        fine = _fine(["C10H16O3", "C9H14O4", "C8H12O5"], [DEPROTONATED])
        target = lines_at_file(_target_fine(), FileLines("HIGH"))
        mz = _monoisotopic_mz(_ion(fine, "C9H14O4", DEPROTONATED))
        known = await self._known(target, fine, [mz])
        order = list(pd.unique(fine["target_ion_id"]))
        assert known.reference_order == {ion: i for i, ion in enumerate(order)}
        reference = known.frame[known.frame["target_compound_id"].isna()]
        assert set(reference["target_compound_formula"]) == {"C9H14O4"}
        assert known.frame["target_compound_id"].iloc[0] == "tc-1"
        assert known.absent["target_compound_formula"].tolist() == [
            "C10H16O3",
            "C8H12O5",
        ]
        assert "mz" not in known.absent.columns

    @pytest.mark.asyncio
    async def test_lists_alone_reaching_no_peak_keep_one_ion_to_match(self):
        # Stage A matches a known set that is not empty: one ion stays, so a
        # sample read against the lists alone runs Stage A as it did.
        fine = _fine(["C10H16O3", "C9H14O4"], [DEPROTONATED])
        known = await self._known(pd.DataFrame(), fine, [50.0])
        first = fine["target_ion_id"].iloc[0]
        assert set(known.frame["target_ion_id"]) == {first}
        assert first not in set(known.absent["target_ion_id"])
        # With the target library's lines there is no need.
        target = lines_at_file(_target_fine(), FileLines("HIGH"))
        known = await self._known(target, fine, [50.0])
        assert set(known.frame["target_ion_id"]) == {"target-ion-1"}
        assert len(known.absent) == 2

    @pytest.mark.asyncio
    async def test_no_lists_leave_the_target_library_as_the_whole_read_has_it(self):
        target = lines_at_file(_target_fine(), FileLines("HIGH"))
        known = await self._known(target, pd.DataFrame(), [50.0])
        assert REFERENCE_IDENTITIES_COL in known.frame.columns
        assert known.absent.empty and known.reference_order == {}
