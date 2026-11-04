"""The precursor of an MS2 parent peak: located once, then followed.

The rule is peaky's (``peaky.ms2.sources.locate_precursor``, its PR #79), and
so are the known answers here, its own tests at m/z 100 and 600 ported as
they are: among the peaks within max(10 ppm, 5 mDa) of the key that hold at
least 10 % of the strongest of them the nearest wins, in the lowest-energy
spectrum of the parent that holds one, and it is followed within
max(5 ppm, 0.5 mDa) across the energies, none where it is spent.

Then on the hermetic MS2 fixture's spectra (``tests/ms2_fixture.json``): the
stepped shape's precursor A is located at its lowest energy and followed down
its survival, and a key typed with two decimals, which sits millidaltons off
the ion, still finds the precursor and not a satellite nearer the key.
"""

import numpy as np
import pytest
from thermo_test_support import ms2_fixture

import mascope_thermo.thermo as m_thermo


fixture = ms2_fixture()
ANSWERS = fixture.answers()


def _spectra(parent: float, steps: dict, signal_to_noise: float = 50.0) -> dict:
    """Spectra of one parent peak, as the reader averages them: ``{(parent,
    activation): (masses, intensities, resolutions, signal_to_noise)}``."""
    return {
        (parent, activation): (
            np.asarray(mz, dtype=np.float64),
            np.asarray(intensity, dtype=np.float64),
            np.full(len(mz), 1e5),
            np.full(len(mz), signal_to_noise),
        )
        for activation, (mz, intensity) in steps.items()
    }


def _fixture_spectrum(step: str) -> tuple[np.ndarray, np.ndarray]:
    centroids = ANSWERS["spectra"][step]["centroids"]
    return (
        np.array([c["mz"] for c in centroids]),
        np.array([c["intensity"] for c in centroids]),
    )


class TestTheRule:
    def test_the_nearest_of_the_strong_enough_wins(self):
        """At m/z 100 the search window is 5 mDa: a near peak just under 10 %
        of a far one is no candidate, one just over it wins."""
        mz = [100.001, 100.003]

        assert m_thermo.locate_precursor(mz, [9.9, 100.0], 100.0) == 1
        assert m_thermo.locate_precursor(mz, [10.1, 100.0], 100.0) == 0

    def test_a_tie_goes_to_the_stronger(self):
        mz = [100 - 2**-10, 100 + 2**-10]

        assert m_thermo.locate_precursor(mz, [5.0, 6.0], 100.0) == 1
        assert m_thermo.locate_precursor(mz, [6.0, 5.0], 100.0) == 0

    def test_nothing_near_enough_is_none(self):
        assert m_thermo.locate_precursor([100.0051, 99.9949], [1.0, 1.0], 100.0) is None
        assert m_thermo.locate_precursor([], [], 100.0) is None

    def test_above_mz_500_the_ppm_clause_is_the_wider(self):
        """10 ppm of 600 is 6 mDa."""
        assert m_thermo.locate_precursor([600.0058], [1.0], 600.0) == 0
        assert m_thermo.locate_precursor([600.0061], [1.0], 600.0) is None

    def test_its_tolerances(self):
        assert (m_thermo.PRECURSOR_SEARCH_PPM, m_thermo.PRECURSOR_SEARCH_MDA) == (
            10.0,
            5.0,
        )
        assert m_thermo.PRECURSOR_MIN_SHARE == 0.10
        assert (m_thermo.PRECURSOR_TRACK_PPM, m_thermo.PRECURSOR_TRACK_MDA) == (
            5.0,
            0.5,
        )


class TestLocatedOnceAndFollowed:
    def test_in_the_lowest_energy_that_holds_it(self):
        """No candidate at NCE 1: the precursor is located at NCE 5, the
        next energy that holds one, and followed to NCE 9."""
        precursors = m_thermo.locate_precursors(
            _spectra(
                100.0,
                {
                    "hcd1.00": ([60.0], [1.0]),
                    "hcd5.00": ([60.0, 100.0002], [1.0, 5.0]),
                    "hcd9.00": ([60.0, 100.0004], [3.0, 1.0]),
                },
            )
        )

        for activation in ("hcd1.00", "hcd5.00", "hcd9.00"):
            precursor = precursors[(100.0, activation)]
            assert precursor["located_precursor_mz"] == 100.0002
            assert precursor["located_precursor_intensity"] == 5.0
            assert precursor["located_precursor_activation"] == "hcd5.00"
        assert precursors[(100.0, "hcd1.00")]["precursor_mz"] is None
        assert precursors[(100.0, "hcd1.00")]["precursor_intensity"] is None
        assert precursors[(100.0, "hcd5.00")]["precursor_mz"] == 100.0002
        assert precursors[(100.0, "hcd9.00")]["precursor_mz"] == 100.0004
        assert precursors[(100.0, "hcd9.00")]["precursor_intensity"] == 1.0

    def test_never_jumps_to_a_neighbour(self):
        """Followed within max(5 ppm, 0.5 mDa): a peak 0.4 mDa off is the
        precursor, one 1.9 mDa off is not; at m/z 600 the ppm clause widens it
        to 3 mDa."""
        precursors = m_thermo.locate_precursors(
            _spectra(
                100.0,
                {
                    "hcd1.00": ([100.0], [10.0]),
                    "hcd5.00": ([100.0004], [5.0]),
                    "hcd9.00": ([100.0019], [5.0]),
                },
            )
        )
        assert precursors[(100.0, "hcd5.00")]["precursor_mz"] == 100.0004
        assert precursors[(100.0, "hcd9.00")]["precursor_mz"] is None
        assert precursors[(100.0, "hcd9.00")]["precursor_intensity"] is None

        precursors = m_thermo.locate_precursors(
            _spectra(
                600.0,
                {
                    "hcd1.00": ([600.0], [10.0]),
                    "hcd5.00": ([600.0028], [5.0]),
                    "hcd9.00": ([600.0032], [5.0]),
                },
            )
        )
        assert precursors[(600.0, "hcd5.00")]["precursor_mz"] == 600.0028
        assert precursors[(600.0, "hcd9.00")]["precursor_mz"] is None

    def test_energies_are_ordered_by_their_number(self):
        """NCE 100 comes after NCE 20, not between 10 and 20 as the
        activations sort as text; one with no one energy comes last."""
        precursors = m_thermo.locate_precursors(
            _spectra(
                100.0,
                {
                    "cid30.00@hcd20.00": ([100.0001], [5.0]),
                    "hcd100.00": ([100.0003], [5.0]),
                    "hcd20.00": ([100.0002], [5.0]),
                },
            )
        )

        assert {p["located_precursor_activation"] for p in precursors.values()} == {
            "hcd20.00"
        }
        precursors = m_thermo.locate_precursors(
            _spectra(
                100.0,
                {"cid30.00@hcd20.00": ([100.0001], [5.0]), "hcd100.00": ([], [])},
            )
        )
        assert {p["located_precursor_activation"] for p in precursors.values()} == {
            "cid30.00@hcd20.00"
        }

    def test_the_parent_peaks_are_located_each_on_its_own(self):
        spectra = {
            **_spectra(100.0, {"hcd5.00": ([100.0002], [5.0])}),
            **_spectra(150.0, {"hcd5.00": ([150.0001], [5.0])}),
        }

        precursors = m_thermo.locate_precursors(spectra)

        assert precursors[(100.0, "hcd5.00")]["located_precursor_mz"] == 100.0002
        assert precursors[(150.0, "hcd5.00")]["located_precursor_mz"] == 150.0001

    def test_one_located_nowhere_is_none_in_every_group(self):
        precursors = m_thermo.locate_precursors(
            _spectra(100.0, {"hcd1.00": ([60.0], [1.0]), "hcd5.00": ([], [])})
        )

        for precursor in precursors.values():
            assert set(precursor.values()) == {None}

    def test_only_peaks_above_the_noise_floor_are_read(self):
        """A store keeps its spectra at no S/N floor; a peak under the floor
        the precursor is read at is neither located nor followed."""
        floor = m_thermo.PRECURSOR_SIGNAL_TO_NOISE
        spectra = _spectra(100.0, {"hcd1.00": ([100.0001], [5.0])}, floor)
        spectra.update(_spectra(100.0, {"hcd5.00": ([100.0002], [5.0])}, floor - 0.1))

        precursors = m_thermo.locate_precursors(spectra)

        assert precursors[(100.0, "hcd5.00")]["located_precursor_mz"] == 100.0001
        assert precursors[(100.0, "hcd5.00")]["precursor_mz"] is None
        spent = m_thermo.locate_precursors(
            _spectra(100.0, {"hcd1.00": ([100.0001], [5.0])}, floor - 0.1)
        )
        assert spent[(100.0, "hcd1.00")]["located_precursor_mz"] is None


class TestOnTheFixture:
    def test_the_stepped_precursor_is_followed_down_its_survival(self):
        """Precursor A at four energies: located at the lowest, and each
        step's own peak is the precursor at that step's intensity, not the
        stronger co-isolate's or a fragment's."""
        precursor = ANSWERS["precursors"]["A"]
        steps = {
            spectrum["activation"]: _fixture_spectrum(step)
            for step, spectrum in ANSWERS["spectra"].items()
            if spectrum["precursor"] == "A" and step != "A@30"
        }

        precursors = m_thermo.locate_precursors(
            _spectra(precursor["isolation_mz"], steps)
        )

        for activation in steps:
            step = f"A@{int(float(activation[3:]))}"
            (expected,) = [
                c
                for c in ANSWERS["spectra"][step]["centroids"]
                if c["role"] == "precursor"
            ]
            found = precursors[(precursor["isolation_mz"], activation)]
            assert found["located_precursor_mz"] == precursor["mz"]
            assert found["located_precursor_intensity"] == 90000.0
            assert found["located_precursor_activation"] == "hcd10.00"
            assert found["precursor_mz"] == expected["mz"]
            assert found["precursor_intensity"] == expected["intensity"]

    def test_a_key_with_two_decimals_finds_the_precursor_not_its_satellite(self):
        """Typed 195.09, the key sits 2.3 mDa above precursor A. A weak
        satellite at 1 mDa from the key is nearer to it than the precursor,
        and nearest alone would pick it; the precursor holds the share the
        satellite lacks, and is located and followed."""
        precursor = ANSWERS["precursors"]["A"]
        key = round(precursor["mz"], 2)
        satellite = key - 0.001
        steps = {}
        for step in ("A@10", "A@20", "A@40", "A@60"):
            mz, intensity = _fixture_spectrum(step)
            at = int(np.searchsorted(mz, satellite))
            steps[ANSWERS["spectra"][step]["activation"]] = (
                np.insert(mz, at, satellite),
                np.insert(intensity, at, 0.005 * intensity.max()),
            )
        mz, _ = steps["hcd10.00"]
        assert abs(satellite - key) < abs(precursor["mz"] - key) < 0.005
        assert mz[np.argmin(np.abs(mz - key))] == satellite

        precursors = m_thermo.locate_precursors(_spectra(key, steps))

        for activation in steps:
            found = precursors[(key, activation)]
            assert found["located_precursor_mz"] == precursor["mz"]
            assert found["precursor_mz"] == precursor["mz"]

    def test_the_store_locates_each_groups_precursor(self):
        """As the converter reads a stepped acquisition into its MS2 store."""
        with fixture.presented("stepped") as path:
            contents = m_thermo.get_ms2_store_contents(str(path))

        precursors = contents["polarities"]["+"]["precursors"]
        assert set(precursors) == set(contents["polarities"]["+"]["groups"])
        for group in ANSWERS["shapes"]["stepped"]["answers"]["groups"]:
            spectrum = ANSWERS["spectra"][group["spectrum"]]
            ion = ANSWERS["precursors"][spectrum["precursor"]]
            (peak,) = [c for c in spectrum["centroids"] if c["role"] == "precursor"]
            found = precursors[(group["parent_peak_mz"], group["activation"])]
            assert found["located_precursor_mz"] == pytest.approx(ion["mz"], abs=1e-6)
            assert found["precursor_mz"] == pytest.approx(ion["mz"], abs=1e-6)
            assert found["precursor_intensity"] == pytest.approx(peak["intensity"])
        lowest = {"A": "hcd10.00", "B": "hcd30.00"}
        for (parent, _), found in precursors.items():
            name = "A" if parent == ANSWERS["precursors"]["A"]["isolation_mz"] else "B"
            assert found["located_precursor_activation"] == lowest[name]
