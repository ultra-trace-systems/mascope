"""Chlorine and bromine read from their twin line (step 3.6, item 4 and its rider).

A chlorine atom puts a line 1.99705 Da above its own at a third of its height.
Where the search left such a peak unexplained, or read it as a formula with no
chlorine, the twin is the evidence: the peak is searched again over carbon,
hydrogen, oxygen and chlorine, and the reading replaces the peak's where it
passes every gate. Pinned here on a charge-transfer spectrum of chlorobenzene,
the certified cylinder's chlorinated component, with the real finder.
"""

from __future__ import annotations

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.halogen_twins import (
    ONE_ATOM_RATIO,
    TWIN_HALOGEN_RATIO_BAND,
    TWIN_OFFSET,
    TWIN_PPM,
    find_twins,
    record_twins,
    reread_twins,
    twin_halogens,
    twin_heuristics,
    twin_search_config,
)
from mascope_tools.composition import profiles as presets
from mascope_tools.composition.heuristic_filter import (
    _RATIO_BANDS,
    predict_resolved_isotopes,
)
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
    PatternScoring,
)


def envelope(ion: str, height: float) -> list[tuple[float, float]]:
    """An ion's lines at a given height of its tallest."""
    mzs, shares, _labels = predict_resolved_isotopes(ion, 1, 1e-3, None)
    return [
        (float(mz), round(height * float(share), 1)) for mz, share in zip(mzs, shares)
    ]


#: Chlorobenzene's radical cation, and toluene's beside it.
CHLOROBENZENE = envelope("C6H5Cl", 1.0e6)
TOLUENE = envelope("C7H8", 2.0e6)
M0 = CHLOROBENZENE[0][0]
TWIN = next(mz for mz, _ in CHLOROBENZENE if abs(mz - M0 - TWIN_OFFSET["Cl"]) < 1e-4)


def peaks(lines=None) -> pd.DataFrame:
    rows = sorted(lines if lines is not None else CHLOROBENZENE + TOLUENE)
    return pd.DataFrame(rows, columns=["mz", "intensity"])


def placeholder(mz: float) -> dict:
    return {
        "mz": mz,
        "formula": "---",
        "ion": "---",
        "isotope_label": "---",
        "ionization_mechanism": None,
    }


def reading(mz, formula, ion, label="M0", mechanism="[M]+.") -> dict:
    return {
        "mz": mz,
        "formula": formula,
        "ion": ion,
        "isotope_label": label,
        "ionization_mechanism": mechanism,
        "isotopic_pattern_score": 0.9,
        "mz_error_ppm": 0.0,
    }


def matches(frame: pd.DataFrame, readings=()) -> pd.DataFrame:
    """The search's rows: the given readings, a placeholder everywhere else."""
    taken = {row["mz"] for row in readings}
    rows = list(readings) + [placeholder(mz) for mz in frame["mz"] if mz not in taken]
    return pd.DataFrame(rows)


SEARCH = CompositionSearchConfig(
    ionizations="[M]+.",
    mass_range_ppm=3.0,
    element_count_ranges=presets.resolve_element_ranges(
        presets.EASYIC_POS, presets.AMBIENT_AIR
    ),
    use_unsaturation=True,
    min_unsaturation=-1000.0,
    max_unsaturation=10000.0,
)
HEURISTICS = HeuristicFilterConfig(
    use_senior=True, context_ratio_windows=presets.AMBIENT_AIR.ratio_windows()
)


def reread(frame, rows, twins, **kwargs):
    return reread_twins(
        rows,
        frame,
        twins,
        search_config=SEARCH,
        heuristics=HEURISTICS,
        scoring=kwargs.pop("scoring", None),
        candidate_threshold=kwargs.pop("candidate_threshold", 0.5),
        sigmas=kwargs.pop("sigmas", 3.0),
    )


class TestWhichHalogens:
    def test_a_context_that_opens_them_reads_both(self):
        assert twin_halogens(presets.AMBIENT_AIR) == ("Cl", "Br")

    def test_a_context_that_closes_them_reads_none_whatever_a_list_names(self):
        assert twin_halogens(presets.URONIUM, ["C6H5Cl", "C2H5Br"]) == ()

    def test_the_reagents_own_halogen_is_never_read(self):
        # A bromide source's one-bromine patterns are its own clusters.
        assert twin_halogens(presets.AMBIENT_AIR, reagent_formula="Br") == ("Cl",)
        assert twin_halogens(presets.AMBIENT_AIR, reagent_formula="NO3") == (
            "Cl",
            "Br",
        )

    def test_a_silent_context_leaves_it_to_the_lists(self):
        assert twin_halogens(presets.CHAMBER) == ()
        assert twin_halogens(presets.CHAMBER, ["C6H5Cl", "C7H8"]) == ("Cl",)
        assert twin_halogens(presets.NO_CONTEXT, ["CH2Br2"]) == ("Br",)


class TestTheTwin:
    def test_the_offsets_and_ratios_are_the_isotopes(self):
        assert TWIN_OFFSET["Cl"] == pytest.approx(1.99705, abs=1e-5)
        assert TWIN_OFFSET["Br"] == pytest.approx(1.99795, abs=1e-5)
        assert ONE_ATOM_RATIO["Cl"] == pytest.approx(0.32, abs=0.005)
        assert ONE_ATOM_RATIO["Br"] == pytest.approx(0.973, abs=0.005)

    def test_a_peak_the_search_left_unexplained_is_asked(self):
        frame = peaks()
        [twin] = find_twins(
            matches(frame), frame, halogens=("Cl", "Br"), searched_mzs=[M0]
        )
        assert (twin.mz, twin.twin_mz, twin.halogen, twin.incumbent) == (
            M0,
            TWIN,
            "Cl",
            None,
        )
        assert twin.ratio == pytest.approx(ONE_ATOM_RATIO["Cl"], rel=0.05)

    def test_a_halogen_free_reading_is_asked_and_named(self):
        frame = peaks()
        rows = matches(frame, [reading(M0, "C5H4N2", "C5H4N2+")])
        [twin] = find_twins(rows, frame, halogens=("Cl",), searched_mzs=[M0])
        assert twin.incumbent == {
            "formula": "C5H4N2",
            "ion": "C5H4N2+",
            "ionization_mechanism": "[M]+.",
        }

    @pytest.mark.parametrize(
        "case",
        [
            "not_searched",
            "claimed",
            "twin_explained",
            "twin_claimed",
            "ion_has_halogen",
            "ion_has_silicon",
            "own_nitrogen_line",
            "another_ions_line",
            "halogen_not_read",
        ],
    )
    def test_what_is_no_twin(self, case):
        frame = peaks()
        rows = [reading(M0, "C5H4N2", "C5H4N2+")]
        searched, claimed, halogens = [M0], [], ("Cl",)
        if case == "not_searched":
            searched = []
        elif case == "claimed":
            claimed = [M0]
        elif case == "twin_explained":
            rows.append(reading(TWIN, "C5H4N2", "[15N]C5H4N+", label="15N+18O"))
            rows[0] = reading(M0, "C5H5NO", "C5H5NO+")
        elif case == "twin_claimed":
            claimed = [TWIN]
        elif case == "ion_has_halogen":
            rows = [reading(M0, "C6H5", "C6H5Cl+", mechanism="[M+Cl]+")]
        elif case == "ion_has_silicon":
            # A 30Si line sits 0.2 mDa from the 37Cl offset: the incumbent's
            # own line, left uncommitted, is no chlorine twin.
            rows = [reading(M0, "C4H4O2Si", "C4H4O2Si+")]
        elif case == "own_nitrogen_line":
            rows.append(reading(M0 + 0.997, "C5H4N2", "C5H4N2+", label="15N"))
        elif case == "another_ions_line":
            # A committed M0 one 13C step under the twin: the twin may be its line.
            frame = peaks(CHLOROBENZENE + TOLUENE + [(TWIN - 1.0033548, 5.0e5)])
            rows.append(reading(TWIN - 1.0033548, "C5H4O2", "C5H4O2+"))
        elif case == "halogen_not_read":
            halogens = ("Br",)
        assert (
            find_twins(
                matches(frame, rows),
                frame,
                halogens=halogens,
                searched_mzs=searched,
                claimed_mzs=claimed,
            )
            == []
        )

    @pytest.mark.parametrize("ratio", [0.1, 0.9])
    def test_a_twin_off_one_atoms_ratio_is_none(self, ratio):
        lines = [(mz, h) for mz, h in CHLOROBENZENE if mz != TWIN]
        lines.append((TWIN, round(CHLOROBENZENE[0][1] * ratio, 1)))
        frame = peaks(lines + TOLUENE)
        assert (
            find_twins(matches(frame), frame, halogens=("Cl",), searched_mzs=[M0]) == []
        )

    def test_a_line_off_the_exact_offset_is_none(self):
        shifted = TWIN * (1 + 3 * TWIN_PPM * 1e-6)
        lines = [(shifted if mz == TWIN else mz, h) for mz, h in CHLOROBENZENE]
        frame = peaks(lines + TOLUENE)
        assert (
            find_twins(matches(frame), frame, halogens=("Cl",), searched_mzs=[M0]) == []
        )


class TestTheReread:
    def test_the_search_is_carbon_hydrogen_oxygen_and_one_halogen(self):
        config = twin_search_config(SEARCH, "Cl")
        assert config.element_count_ranges == "C1-40 H0-80 O0-15 Cl1-1"
        assert config.mass_range_ppm == SEARCH.mass_range_ppm

    def test_the_halogens_band_is_opened_to_the_common_range(self):
        opened = twin_heuristics(HEURISTICS, "Cl")
        assert TWIN_HALOGEN_RATIO_BAND == _RATIO_BANDS["Cl"][:2]
        assert opened.carbon_element_ratio_range["Cl/C"] == TWIN_HALOGEN_RATIO_BAND
        assert opened.context_ratio_windows == HEURISTICS.context_ratio_windows
        assert HEURISTICS.carbon_element_ratio_range["Cl/C"] == (0.0, 0.05)

    def test_an_unexplained_peak_is_read_as_chlorobenzene_with_its_37cl_line(self):
        frame = peaks()
        rows = matches(frame)
        twins = find_twins(rows, frame, halogens=("Cl",), searched_mzs=[M0])
        result = reread(frame, rows, twins)
        assert result.summary == {
            "twins": 1,
            "reread": 1,
            "replaced": 0,
            "proposed": 1,
            "by_halogen": {"Cl": 1},
        }
        at = {
            (row["mz"], row["isotope_label"]): row["formula"]
            for row in result.matches.to_dict("records")
            if row["formula"] != "---"
        }
        assert at[(M0, "M0")] == "C6H5Cl"
        assert at[(TWIN, "37Cl")] == "C6H5Cl"
        # No placeholder is left at a peak the reading took.
        left = result.matches[result.matches["mz"].isin([M0, TWIN])]
        assert set(left["formula"]) == {"C6H5Cl"}
        assert result.records[M0]["halogen"] == "Cl"
        assert result.records[M0]["replaced"] is None

    def test_a_halogen_free_reading_is_replaced_with_its_lines(self):
        frame = peaks()
        incumbent = [
            reading(M0, "C5H4N2", "C5H4N2+"),
            reading(CHLOROBENZENE[1][0], "C5H4N2", "[13C]C4H4N2+", label="13C"),
        ]
        rows = matches(frame, incumbent)
        twins = find_twins(rows, frame, halogens=("Cl",), searched_mzs=[M0])
        result = reread(frame, rows, twins)
        assert result.summary["replaced"] == 1
        assert "C5H4N2" not in set(result.matches["formula"])
        assert result.records[M0]["replaced"]["formula"] == "C5H4N2"

    def test_a_reading_under_the_candidate_bar_leaves_the_peak_as_it_was(self):
        frame = peaks()
        rows = matches(frame, [reading(M0, "C5H4N2", "C5H4N2+")])
        twins = find_twins(rows, frame, halogens=("Cl",), searched_mzs=[M0])
        result = reread(frame, rows, twins, candidate_threshold=1.01)
        assert result.summary["reread"] == 0
        assert result.matches.equals(rows)

    def test_a_reading_off_the_samples_calibration_leaves_the_peak(self):
        frame = peaks()
        rows = matches(frame)
        twins = find_twins(rows, frame, halogens=("Cl",), searched_mzs=[M0])
        # The sample sits 1.5 ppm off with a 0.3 ppm width: a reading at 0 is
        # five widths away. No evidence bar, so the mass is what decides.
        scoring = PatternScoring(sigma_ppm=0.3, mu_ppm=1.5)
        off = reread(frame, rows, twins, scoring=scoring, candidate_threshold=0.0)
        assert off.summary["reread"] == 0
        on = PatternScoring(sigma_ppm=0.3, mu_ppm=0.0)
        assert (
            reread(frame, rows, twins, scoring=on, candidate_threshold=0.0).summary[
                "reread"
            ]
            == 1
        )

    def test_a_twin_the_reading_does_not_claim_leaves_the_peak(self, monkeypatch):
        # The twin is there; the halogen reading the search elects claims some
        # other line as its M+2. Only a reading that explains the twin takes it.
        from mascope_backend.api.new.peak_assignments import halogen_twins as module

        frame = peaks()
        rows = matches(frame)
        twins = find_twins(rows, frame, halogens=("Cl",), searched_mzs=[M0])
        real = module.assign_compositions

        def without_the_twin(peaks_frame, *args, **kwargs):
            found, other = real(peaks_frame, *args, **kwargs)
            return found[found["mz"] != TWIN], other

        monkeypatch.setattr(module, "assign_compositions", without_the_twin)
        assert reread(frame, rows, twins).summary["reread"] == 0


class TestTheRecord:
    def test_the_row_names_its_twin_and_leads_with_what_it_replaced(self):
        rows = [
            {
                "role": "M0",
                "sample_peak_mz": M0,
                "source": "untargeted",
                "alternatives": [{"assigned_formula": "C5H5NO"}],
                "provenance": {},
            },
            {"role": "M0", "sample_peak_mz": 92.06, "provenance": {}},
        ]
        record = {
            "halogen": "Cl",
            "twin_mz": TWIN,
            "ratio": 0.32,
            "one_atom_ratio": 0.32,
            "replaced": {
                "formula": "C5H4N2",
                "ion": "C5H4N2+",
                "ionization_mechanism": "[M]+.",
            },
        }
        assert record_twins(rows, {M0: record}, max_alternatives=5) == 1
        assert rows[0]["provenance"]["halogen_twin"] == record
        assert rows[0]["alternatives"][0]["assigned_formula"] == "C5H4N2"
        assert rows[0]["alternatives"][0]["halogen_twin_replaced"] is True
        assert rows[0]["alternatives"][1] == {"assigned_formula": "C5H5NO"}
        assert "halogen_twin" not in rows[1]["provenance"]


class TestTheRunsReread:
    """``service._reread_halogen_twins``: which runs read twins, and the record."""

    async def run(self, profile, context="auto", listed=frozenset()):
        from mascope_backend.api.new.peak_assignments import service
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )
        from mascope_backend.api.new.peak_assignments.profiles import resolve_profile

        resolved = resolve_profile(
            PeakAssignmentConfig(profile=profile, context=context),
            ["[M]+."],
            instrument_type="orbi",
            polarity="+",
        )
        frame = peaks()
        scope: dict = {}
        rows, records = await service._reread_halogen_twins(
            matches(frame),
            frame,
            resolved_profile=resolved,
            listed_formulas=listed,
            searched_mzs=[M0],
            claimed_mzs=[],
            search_config=SEARCH,
            heuristics=HEURISTICS,
            scoring=None,
            candidate_threshold=0.5,
            search_scope=scope,
            sample_name="Sample One",
        )
        return rows, records, scope

    @pytest.mark.asyncio
    async def test_a_charge_transfer_run_in_ambient_air_reads_the_twin(self):
        rows, records, scope = await self.run("auto")
        assert records[M0]["halogen"] == "Cl"
        assert scope["halogen_twins"] == {
            "halogens": ["Cl", "Br"],
            "twins": 1,
            "reread": 1,
            "replaced": 0,
            "proposed": 1,
            "by_halogen": {"Cl": 1},
        }
        assert "C6H5Cl" in set(rows["formula"])

    @pytest.mark.asyncio
    async def test_a_bromide_run_reads_no_bromine_twin(self, monkeypatch):
        from mascope_backend.api.new.peak_assignments import service

        seen = {}
        real = service.twin_halogens

        def spy(*args, **kwargs):
            seen["halogens"] = real(*args, **kwargs)
            return seen["halogens"]

        monkeypatch.setattr(service, "twin_halogens", spy)
        await self.run("BR")
        assert seen["halogens"] == ("Cl",)

    @pytest.mark.asyncio
    async def test_the_identity_profile_reads_none(self):
        rows, records, scope = await self.run("none", context="ambient-air")
        assert (records, scope) == ({}, {})

    @pytest.mark.asyncio
    async def test_a_context_that_closes_chlorine_reads_none(self):
        _, records, scope = await self.run("auto", context="uronium", listed={"C6H5Cl"})
        assert (records, scope) == ({}, {})

    @pytest.mark.asyncio
    async def test_a_list_naming_chlorobenzene_opens_it_in_a_silent_context(self):
        _, records, scope = await self.run("auto", context="chamber", listed={"C6H5Cl"})
        assert scope["halogen_twins"]["halogens"] == ["Cl"]
        assert records[M0]["halogen"] == "Cl"
