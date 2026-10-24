"""The hermetic MS2 fixture: its answers hold, and the MS2 routes give them.

Every phase of the MS2 work tests on the fixture (``tests/ms2_fixture.py``),
so its known answers (``tests/ms2_fixture.json``) are checked here twice.

First against chemistry and against themselves: every m/z is its formula's,
every fragment a sub-formula of its precursor with the loss it names, every
survival and every voltage the one its definition gives, the co-isolate
inside every window its precursor is taken with, and the unexplained
centroid explained by no sub-formula. A later phase reads a verdict off these
as the truth, so a typo in them would be a wrong answer every phase agrees
with.

Then through the MS2 routes, on each shape as the converter stores it: the
summary and the spectra the routes serve are the ones the answers give.
Neither needs a running stack or a site file.
"""

import itertools
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from ms2_fixture import SHAPES, answers, converted

from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.utils import composition_mass, parse_composition


ANSWERS = answers()
PRECURSORS = ANSWERS["precursors"]
SPECTRA = ANSWERS["spectra"]

#: How close a sub-formula must come to explain a centroid [ppm]: wider than
#: any mass accuracy a later phase assigns at.
EXPLAINS_PPM = 20.0


def _composition(formula: str) -> dict[str, int]:
    return dict(parse_composition(formula.rstrip("+")))


def _ion_mz(formula: str) -> float:
    """The m/z of a singly charged cation of ``formula``."""
    return composition_mass(parse_composition(formula.rstrip("+"))) - ELECTRON_MASS


def _sub_formula_mzs(formula: str) -> list[float]:
    """The m/z of every singly charged sub-formula of ``formula``."""
    counts = _composition(formula)
    elements = list(counts)
    mzs = []
    for numbers in itertools.product(*(range(counts[e] + 1) for e in elements)):
        if any(numbers):
            sub = "".join(f"{e}{n}" for e, n in zip(elements, numbers) if n)
            mzs.append(_ion_mz(sub))
    return mzs


def _ppm(a: float, b: float) -> float:
    return abs(a - b) / b * 1e6


def _role(spectrum: dict, role: str) -> list[dict]:
    return [c for c in spectrum["centroids"] if c["role"] == role]


class TestTheAnswersAreChemistry:
    @pytest.mark.parametrize("name", PRECURSORS)
    def test_every_named_mz_is_its_formulas(self, name):
        precursor = PRECURSORS[name]
        named = [precursor, precursor["co_isolate"], *precursor["fragments"]]

        for ion in named:
            assert ion["mz"] == pytest.approx(_ion_mz(ion["formula"]), abs=1e-6)

    @pytest.mark.parametrize("name", PRECURSORS)
    def test_the_filter_names_the_precursor_at_four_decimals(self, name):
        precursor = PRECURSORS[name]

        assert precursor["isolation_mz"] == round(precursor["mz"], 4)

    @pytest.mark.parametrize("name", PRECURSORS)
    def test_each_fragment_is_the_precursor_less_its_loss(self, name):
        precursor = _composition(PRECURSORS[name]["formula"])

        for fragment in PRECURSORS[name]["fragments"]:
            loss = _composition(fragment["loss"])
            left = {e: precursor[e] - loss.get(e, 0) for e in precursor}
            assert {e: n for e, n in left.items() if n} == _composition(
                fragment["formula"]
            )

    @pytest.mark.parametrize("name", PRECURSORS)
    def test_the_co_isolate_is_no_sub_formula_and_in_every_window(self, name):
        precursor = PRECURSORS[name]
        own = _composition(precursor["formula"])
        other = _composition(precursor["co_isolate"]["formula"])
        widths = {
            scan["width"]
            for shape in ANSWERS["shapes"].values()
            for scan in shape["scans"]
            if scan.get("precursor") == name
        }

        assert any(n > own.get(e, 0) for e, n in other.items())
        offset = abs(precursor["co_isolate"]["mz"] - precursor["isolation_mz"])
        assert widths and offset < min(widths) / 2

    @pytest.mark.parametrize("name", PRECURSORS)
    def test_the_ghost_is_the_precursors_second_harmonic(self, name):
        precursor = PRECURSORS[name]

        assert precursor["harmonic_ghost"]["mz"] == pytest.approx(
            precursor["mz"] / 4, abs=1e-6
        )

    @pytest.mark.parametrize("name", PRECURSORS)
    def test_no_sub_formula_explains_the_unexplained_ghost_or_co_isolate(self, name):
        precursor = PRECURSORS[name]
        candidates = _sub_formula_mzs(precursor["formula"]) + _sub_formula_mzs(
            precursor["co_isolate"]["formula"]
        )
        unexplained = [
            precursor["unexplained"]["mz"],
            precursor["harmonic_ghost"]["mz"],
        ]

        for mz in unexplained:
            closest = min(_ppm(mz, candidate) for candidate in candidates)
            assert closest > EXPLAINS_PPM, mz
        own = _sub_formula_mzs(precursor["formula"])
        co_isolate = precursor["co_isolate"]["mz"]
        assert min(_ppm(co_isolate, m) for m in own) > EXPLAINS_PPM


class TestTheSpectraAreTheirDefinitions:
    @pytest.mark.parametrize("step", SPECTRA)
    def test_each_role_is_there_once_and_each_fragment(self, step):
        spectrum = SPECTRA[step]
        precursor = PRECURSORS[spectrum["precursor"]]

        for role in ("precursor", "co_isolate", "harmonic_ghost", "unexplained"):
            assert len(_role(spectrum, role)) == 1, role
        assert sorted(c["formula"] for c in _role(spectrum, "fragment")) == sorted(
            f["formula"] for f in precursor["fragments"]
        )
        mzs = [c["mz"] for c in spectrum["centroids"]]
        assert mzs == sorted(mzs)

    @pytest.mark.parametrize("step", SPECTRA)
    def test_each_centroid_sits_at_its_ions_mz(self, step):
        spectrum = SPECTRA[step]
        precursor = PRECURSORS[spectrum["precursor"]]
        at = {
            "precursor": [precursor["mz"]],
            "co_isolate": [precursor["co_isolate"]["mz"]],
            "harmonic_ghost": [precursor["harmonic_ghost"]["mz"]],
            "unexplained": [precursor["unexplained"]["mz"]],
            "fragment": [f["mz"] for f in precursor["fragments"]],
        }

        for centroid in spectrum["centroids"]:
            assert centroid["mz"] in at[centroid["role"]]

    @pytest.mark.parametrize("step", SPECTRA)
    def test_its_survival(self, step):
        spectrum = SPECTRA[step]
        (precursor,) = _role(spectrum, "precursor")
        derived = precursor["intensity"] + sum(
            c["intensity"] for c in _role(spectrum, "fragment")
        )

        assert precursor["intensity"] / derived == pytest.approx(spectrum["survival"])

    @pytest.mark.parametrize("step", SPECTRA)
    def test_its_ghost_follows_the_precursor(self, step):
        spectrum = SPECTRA[step]
        (precursor,) = _role(spectrum, "precursor")
        (ghost,) = _role(spectrum, "harmonic_ghost")

        assert ghost["intensity"] == pytest.approx(
            ANSWERS["ghost_ratio"] * precursor["intensity"]
        )
        # Weak enough for the ghost rule of the design note to read it as one
        # (a line at m/z / 4 under 0.5 % of the line it shadows)
        assert ghost["intensity"] < 0.005 * precursor["intensity"]

    @pytest.mark.parametrize("step", SPECTRA)
    def test_its_voltage(self, step):
        spectrum = SPECTRA[step]
        isolation_mz = PRECURSORS[spectrum["precursor"]]["isolation_mz"]

        assert spectrum["activation"] == f"hcd{spectrum['nce']:.2f}"
        assert spectrum["volts"] == round(spectrum["nce"] * isolation_mz / 500, 2)

    @pytest.mark.parametrize("step", SPECTRA)
    def test_every_centroid_clears_the_routes_noise_threshold(self, step):
        """So the centroid route serves all of them at its default."""
        weakest = min(c["intensity"] for c in SPECTRA[step]["centroids"])

        assert weakest / ANSWERS["noise"] >= 10.0

    def test_survival_falls_with_energy(self):
        steps = sorted(
            (s["nce"], s["survival"]) for s in SPECTRA.values() if s["precursor"] == "A"
        )

        assert [nce for nce, _ in steps] == [10, 20, 30, 40, 60]
        assert [s for _, s in steps] == sorted((s for _, s in steps), reverse=True)


class TestTheShapes:
    def test_there_are_three(self):
        assert SHAPES == ("manual", "interleaved", "stepped")

    def test_a_reader_of_the_answers_cannot_change_them(self):
        """Each test gets its own copy, so one that edits what it reads does
        not change what the next one is told."""
        answers()["shapes"]["manual"]["scans"].clear()

        assert (
            answers()["shapes"]["manual"]["scans"]
            == (ANSWERS["shapes"]["manual"]["scans"])
        )

    def test_the_stepped_shape_takes_one_precursor_at_four_energies(self):
        groups = ANSWERS["shapes"]["stepped"]["answers"]["groups"]
        energies = {}
        for group in groups:
            energies.setdefault(group["spectrum"].split("@")[0], []).append(
                group["activation"]
            )

        assert {name: len(steps) for name, steps in energies.items()} == {
            "A": 4,
            "B": 1,
        }

    def test_the_stepped_shapes_e50_is_where_survival_crosses_one_half(self):
        """On NCE and on volts, between the two steps where survival falls
        through 0.5; the precursor taken at one energy has none."""
        stepped = ANSWERS["shapes"]["stepped"]["answers"]
        steps = sorted(
            (SPECTRA[g["spectrum"]]["nce"], g["hcd_energy"][0], g["spectrum"])
            for g in stepped["groups"]
            if g["spectrum"].startswith("A@")
        )
        (low, high) = [
            (nce, volts, SPECTRA[step]["survival"])
            for nce, volts, step in steps
            if step in ("A@20", "A@40")
        ]
        assert low[2] >= 0.5 > high[2]
        share = (low[2] - 0.5) / (low[2] - high[2])

        assert stepped["e50"] == {
            "A": {
                "nce": pytest.approx(low[0] + share * (high[0] - low[0])),
                "volts": pytest.approx(low[1] + share * (high[1] - low[1])),
            }
        }
        assert [step for _, _, step in steps] == ["A@10", "A@20", "A@40", "A@60"]
        survivals = [SPECTRA[step]["survival"] for _, _, step in steps]
        assert [f >= 0.5 for f in survivals] == [True, True, False, False]

    def test_every_shape_is_positive(self):
        assert {
            polarity
            for shape in ANSWERS["shapes"].values()
            for polarity in shape["answers"]["polarities"]
        } == {"+"}

    @pytest.mark.parametrize("name", SHAPES)
    def test_its_groups_hold_its_fragment_scans(self, name):
        shape = ANSWERS["shapes"][name]
        fragment_scans = [
            n for n, scan in enumerate(shape["scans"], start=1) if "precursor" in scan
        ]

        assert (
            sorted(n for group in shape["answers"]["groups"] for n in group["scans"])
            == fragment_scans
        )
        assert shape["answers"]["ms2_scan_count"] == len(fragment_scans)
        assert shape["answers"]["scans"] == len(shape["scans"])

    @pytest.mark.parametrize("name", SHAPES)
    def test_its_groups_widths_are_their_scans_widths(self, name):
        """Each group's widths, narrowest first, with the number of its scans
        isolated at each; its one width only where they share one."""
        shape = ANSWERS["shapes"][name]
        for group in shape["answers"]["groups"]:
            widths = Counter(shape["scans"][n - 1]["width"] for n in group["scans"])

            assert group["isolation_widths"] == [
                {"isolation_width": width, "scan_count": count}
                for width, count in sorted(widths.items())
            ]
            assert group["isolation_width"] == (
                next(iter(widths)) if len(widths) == 1 else None
            )


@pytest.fixture(scope="module", params=SHAPES)
def stored(request, tmp_path_factory):
    """Each shape, converted into a filestore of its own, as a sample of it
    reads: the whole file, in its one polarity."""
    filestore = tmp_path_factory.mktemp(f"filestore_{request.param}")
    with converted(request.param, filestore) as conversion:
        sample = SimpleNamespace(
            filename=conversion.props.filename,
            sample_item_name="MS2 fixture",
            t0=None,
            t1=None,
            polarity="+",
        )
        with patch.object(ms2_service, "fetch_sample", AsyncMock(return_value=sample)):
            yield ANSWERS["shapes"][request.param]["answers"]


class TestTheRoutes:
    @pytest.mark.asyncio
    async def test_the_summary(self, stored):
        summary = (await ms2_service.get_ms2_summary("si-1"))["data"]

        assert summary["ms1_scan_count"] == stored["ms1_scan_count"]
        assert summary["ms2_scan_count"] == stored["ms2_scan_count"]
        assert summary["isolation_width"] == stored["isolation_width"]
        assert [
            (
                g["parent_peak_mz"],
                g["activation"],
                g["isolation_width"],
                g["isolation_widths"],
                g["scan_count"],
            )
            for g in summary["groups"]
        ] == [
            (
                g["parent_peak_mz"],
                g["activation"],
                g["isolation_width"],
                g["isolation_widths"],
                g["scan_count"],
            )
            for g in stored["groups"]
        ]
        assert [g["hcd_energy"] for g in summary["groups"]] == [
            g["hcd_energy"] for g in stored["groups"]
        ]

    @pytest.mark.asyncio
    async def test_the_spectrum_of_each_step(self, stored):
        served = (
            await ms2_service.get_ms2_averaged_centroids("si-1", by_activation=True)
        )["data"]

        assert list(served) == [
            ms2_service.ms2_group_key(g["parent_peak_mz"], g["activation"])
            for g in stored["groups"]
        ]
        for group in stored["groups"]:
            spectrum = served[
                ms2_service.ms2_group_key(group["parent_peak_mz"], group["activation"])
            ]
            centroids = SPECTRA[group["spectrum"]]["centroids"]
            assert spectrum["mz"] == pytest.approx([c["mz"] for c in centroids])
            assert spectrum["intensity"] == pytest.approx(
                [c["intensity"] for c in centroids]
            )
