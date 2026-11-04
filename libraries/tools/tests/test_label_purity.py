"""The purity a labelled reagent is read at: the setup's inside a run.

An ionization setup carries its labelled reagent's isotopic purity, and a run
reads its sample's inside ``labelled_purity``. Every place the engine read the
module constant reads the purity in effect instead - the isotope predictor's
default and the isotopologue search's lines - so a setup at the default reads
exactly as before, and one at another purity is read at its own.
"""

import asyncio

import pytest

from mascope_tools.composition import isotopologues
from mascope_tools.composition.custom_elements import (
    CUSTOM_ELEMENTS,
    label_purity,
    labelled_purity,
)
from mascope_tools.composition.heuristic_filter import (
    LABELLED_REAGENT_PURITY,
    predict_isotopes,
)
from mascope_tools.composition.models import CompositionSearchConfig
from mascope_tools.composition.utils import parse_ionization


def _light_share(purity: float) -> float:
    """A single labelled atom's unlabelled line, relative to its labelled one."""
    return (1.0 - purity) / purity


def test_outside_a_run_the_element_s_default_is_in_effect():
    assert label_purity("^N") == CUSTOM_ELEMENTS["^N"].default_purity
    assert LABELLED_REAGENT_PURITY == CUSTOM_ELEMENTS["^N"].default_purity


def test_inside_the_block_the_setup_s_purity_is_in_effect_and_then_is_not():
    with labelled_purity(0.9):
        assert label_purity("^N") == 0.9
        with labelled_purity(None):
            assert label_purity("^N") == CUSTOM_ELEMENTS["^N"].default_purity
        assert label_purity("^N") == 0.9
    assert label_purity("^N") == CUSTOM_ELEMENTS["^N"].default_purity


def test_concurrent_runs_each_read_their_own():
    async def run(purity):
        with labelled_purity(purity):
            await asyncio.sleep(0)
            return await asyncio.to_thread(label_purity, "^N")

    async def both():
        return await asyncio.gather(run(0.9), run(0.95))

    assert asyncio.run(both()) == [0.9, 0.95]


def test_the_predictor_reads_a_labelled_ion_at_the_purity_in_effect():
    def light_line(purity=None):
        _, intensities, labels = predict_isotopes("C2H3^NO3", -1, purity, 1e-4)
        share = dict(zip(labels, intensities))
        return share["14N"] / share["M0"]

    assert light_line() == pytest.approx(_light_share(0.98), rel=0.05)
    with labelled_purity(0.9):
        assert light_line() == pytest.approx(_light_share(0.9), rel=0.05)
        # A purity the caller names still wins.
        assert light_line(0.98) == pytest.approx(_light_share(0.98), rel=0.05)


def test_the_search_s_lines_read_the_purity_in_effect():
    def shares():
        return {
            round(delta, 4): share
            for delta, share in isotopologues._ion_lines({"^N": 1}, 1e-4)
        }

    default = shares()
    with labelled_purity(0.9):
        configured = shares()
    light = min(default)
    assert default[light] == pytest.approx(_light_share(0.98))
    assert configured[light] == pytest.approx(_light_share(0.9))


def test_the_cached_offsets_are_kept_per_purity():
    """Cached on the ranges and the purity: a run under another purity does
    not read the answer cached for the default."""
    ranges = (("^N", (1, 1)),)
    deep = 0.03  # between the light line's share at 98% and at 90%

    default = isotopologues._combined_offsets(ranges, deep, (("^N", 0.98),))
    configured = isotopologues._combined_offsets(ranges, deep, (("^N", 0.9),))

    assert -1 not in {nominal for nominal, _, _ in default}
    assert -1 in {nominal for nominal, _, _ in configured}


def test_the_search_s_windows_read_the_purity_in_effect():
    """Where a line of a 15N nitrate adduct's ion may sit: its 14N line, one
    unit below, is searched only when its share clears the floor, which it
    does at 90% purity and not at 98%."""
    nitrate = CompositionSearchConfig(
        ionizations="[M+[15N]O3]-",
        mass_range_ppm=3.0,
        element_count_ranges="C0-40 H0-80 O0-25",
    )
    mechanism = parse_ionization("[M+[15N]O3]-")
    floor = 0.03

    assert -1 not in isotopologues.line_offsets(nitrate, mechanism, 311.0750, floor)
    with labelled_purity(0.9):
        assert -1 in isotopologues.line_offsets(nitrate, mechanism, 311.0750, floor)
