"""Unit tests for adduct co-occurrence corroboration (assignment-confidence P3).

Contract: per compound, count the distinct adduct channels that independently support it
and turn that into a bounded, saturating corroboration signal (0 for a lone adduct). See
docs/dev/assignment_confidence.md (P3).
"""

import math

import pytest

from mascope_tools.composition.corroboration import (
    IN_SOURCE_LOSSES,
    MIN_RATIO_REFERENCE,
    RATIO_CONSISTENT,
    RATIO_LOOSE,
    RATIO_OUTLYING,
    RATIO_SPREAD_FLOOR,
    AdductCorroboration,
    FragmentPair,
    RatioReference,
    adduct_corroboration,
    channel_log_ratio,
    channel_ratio_consistency,
    in_source_fragment_pairs,
    ratio_band,
    ratio_consistency,
    ratio_reference,
    subtract_loss,
)
from mascope_tools.composition.heuristic_filter import element_counts


def _rec(compound, adduct, tier="assigned"):
    return {
        "target_compound_id": compound,
        "ionization_mechanism_id": adduct,
        "tier": tier,
    }


def test_empty_returns_empty():
    assert adduct_corroboration([]) == {}


def test_single_adduct_has_zero_corroboration():
    out = adduct_corroboration([_rec("cmp1", "H+")])
    assert out["cmp1"].n_adducts == 1
    assert out["cmp1"].corroboration == 0.0


def test_corroboration_saturates_with_more_adducts():
    two = adduct_corroboration([_rec("c", "H+"), _rec("c", "NH4+")])["c"]
    three = adduct_corroboration(
        [_rec("c", "H+"), _rec("c", "NH4+"), _rec("c", "Na+")]
    )["c"]
    assert two.corroboration == pytest.approx(0.5)
    assert three.corroboration == pytest.approx(0.75)
    assert three.corroboration > two.corroboration


def test_distinct_adducts_are_deduped_and_sorted():
    out = adduct_corroboration(
        [_rec("c", "H+"), _rec("c", "H+"), _rec("c", "NH4+")]  # H+ twice
    )["c"]
    assert out.n_adducts == 2  # duplicate H+ counted once
    assert out.adducts == ("H+", "NH4+")


def test_untargeted_and_unassigned_rows_are_skipped():
    # rows with no compound id (untargeted / unassigned) contribute nothing
    out = adduct_corroboration([_rec("cmp1", "H+"), _rec(None, "H+"), _rec("", "Na+")])
    assert set(out) == {"cmp1"}


def test_accept_predicate_filters_low_confidence():
    recs = [
        _rec("c", "H+", tier="assigned"),
        _rec("c", "NH4+", tier="below_assignability"),
    ]
    accepted = adduct_corroboration(
        recs, accept=lambda r: r["tier"] in {"assigned", "candidate"}
    )
    # only the assigned adduct counts -> lone adduct -> no corroboration
    assert accepted["c"].n_adducts == 1
    assert accepted["c"].corroboration == 0.0
    # without the filter, both count
    both = adduct_corroboration(recs)
    assert both["c"].n_adducts == 2


def test_accepts_objects_not_just_dicts():
    class Row:
        def __init__(self, c, a):
            self.target_compound_id = c
            self.ionization_mechanism_id = a

    out = adduct_corroboration([Row("c", "H+"), Row("c", "Na+")])
    assert out["c"].corroboration == pytest.approx(0.5)


def test_result_type_and_bounds():
    out = adduct_corroboration([_rec("c", f"a{i}") for i in range(6)])["c"]
    assert isinstance(out, AdductCorroboration)
    assert 0.0 <= out.corroboration < 1.0  # bounded, never reaches 1


# ---------------------------------------------------------------------------
# Step 4.16: intensity consistency across a neutral's channels.
# ---------------------------------------------------------------------------


def test_channel_log_ratio_is_the_second_over_the_first_in_natural_log():
    assert channel_log_ratio(100.0, 250.0) == pytest.approx(math.log(2.5))
    assert channel_log_ratio(250.0, 100.0) == pytest.approx(-math.log(2.5))


@pytest.mark.parametrize(
    "a, b",
    [(0.0, 1.0), (1.0, 0.0), (-1.0, 2.0), (None, 1.0), (1.0, float("nan")), ("x", 1.0)],
)
def test_channel_log_ratio_refuses_a_height_it_cannot_read(a, b):
    assert channel_log_ratio(a, b) is None


def test_ratio_reference_is_the_median_and_the_robust_spread():
    reference = ratio_reference([0.0, 1.0, 2.0, 3.0, 4.0, None, float("nan")])
    assert reference.centre == pytest.approx(2.0)
    # MAD of [0, 1, 2, 3, 4] about 2 is 1.
    assert reference.spread == pytest.approx(1.4826)
    assert reference.n == 5


def test_ratio_reference_holds_its_spread_at_the_floor():
    reference = ratio_reference([1.0, 1.01, 0.99, 1.0, 1.02])
    assert reference.spread == RATIO_SPREAD_FLOOR


def test_ratio_reference_needs_enough_neutrals():
    assert ratio_reference([0.0] * (MIN_RATIO_REFERENCE - 1)) is None
    assert ratio_reference([0.0] * MIN_RATIO_REFERENCE) is not None


@pytest.mark.parametrize(
    "z, band",
    [
        (0.0, RATIO_CONSISTENT),
        (1.0, RATIO_CONSISTENT),
        (1.01, RATIO_LOOSE),
        (2.0, RATIO_LOOSE),
        (2.01, RATIO_OUTLYING),
        (50.0, RATIO_OUTLYING),
    ],
)
def test_ratio_band_edges(z, band):
    assert ratio_band(z) == band


def test_ratio_consistency_reads_the_distance_in_spreads():
    reading = ratio_consistency(
        1.6, RatioReference(centre=1.0, spread=0.4, n=7), pair=("a", "b")
    )
    assert reading.z == pytest.approx(1.5)
    assert reading.band == RATIO_LOOSE
    assert reading.as_record() == {
        "pair": ["a", "b"],
        "log_ratio": 1.6,
        "centre": 1.0,
        "spread": 0.4,
        "n_reference": 7,
        "z": 1.5,
        "band": RATIO_LOOSE,
    }


def _sample(ratios: dict[str, float]) -> dict[str, dict[str, float]]:
    """Neutrals seen through [M-H]- at 100 and [M+Br]- at 100 * e^ratio."""
    return {
        neutral: {"[M-H]-": 100.0, "[M+Br]-": 100.0 * math.exp(-ratio)}
        for neutral, ratio in ratios.items()
    }


def test_a_neutral_is_read_against_the_other_neutrals_alone():
    # Six neutrals: five agree, one is far off. Read against the others, the
    # far one is outlying; had it been in its own reference, its median would
    # still be the five's, but its n would be six.
    ratios = {f"n{i}": 0.1 * i for i in range(5)} | {"odd": 3.0}
    readings = channel_ratio_consistency(_sample(ratios))
    odd = readings["odd"]
    assert len(odd) == 1
    assert odd[0].n_reference == 5
    assert odd[0].centre == pytest.approx(0.2)
    assert odd[0].band == RATIO_OUTLYING
    assert readings["n2"][0].band == RATIO_CONSISTENT


def test_the_pair_is_read_in_channel_order():
    readings = channel_ratio_consistency(_sample({f"n{i}": 0.5 for i in range(6)}))
    reading = readings["n0"][0]
    # "[M+Br]-" sorts before "[M-H]-": the ratio is [M-H]- over [M+Br]-.
    assert reading.pair == ("[M+Br]-", "[M-H]-")
    assert reading.log_ratio == pytest.approx(0.5)


def test_a_pair_too_few_other_neutrals_share_is_not_read():
    heights = _sample({f"n{i}": 0.5 for i in range(MIN_RATIO_REFERENCE)})
    assert channel_ratio_consistency(heights) == {}
    heights["extra"] = {"[M-H]-": 100.0, "[M+Br]-": 60.0}
    assert set(channel_ratio_consistency(heights)) == set(heights)


def test_a_lone_channel_or_an_unreadable_height_gives_no_reading():
    heights = _sample({f"n{i}": 0.5 for i in range(6)})
    heights["lone"] = {"[M-H]-": 100.0}
    heights["zero"] = {"[M-H]-": 100.0, "[M+Br]-": 0.0}
    readings = channel_ratio_consistency(heights)
    assert "lone" not in readings and "zero" not in readings
    assert all(r[0].n_reference == 5 for r in readings.values())


def test_three_channels_give_one_reading_per_pair():
    heights = {
        f"n{i}": {"[M+H]+": 100.0, "[M+NH4]+": 50.0, "[M+CH4N2O+H]+": 20.0}
        for i in range(6)
    }
    readings = channel_ratio_consistency(heights)
    assert [r.pair for r in readings["n0"]] == [
        ("[M+CH4N2O+H]+", "[M+H]+"),
        ("[M+CH4N2O+H]+", "[M+NH4]+"),
        ("[M+H]+", "[M+NH4]+"),
    ]


# ---------------------------------------------------------------------------
# Step 4.16: in-source fragments.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("formula", sorted(IN_SOURCE_LOSSES))
def test_each_loss_holds_its_formulas_counts(formula):
    assert IN_SOURCE_LOSSES[formula] == element_counts(formula)


def test_subtract_loss():
    assert subtract_loss({"C": 5, "H": 8, "O": 3}, {"H": 2, "O": 1}) == {
        "C": 5,
        "H": 6,
        "O": 2,
    }
    assert subtract_loss({"C": 5, "H": 8, "O": 2}, {"C": 1, "O": 2}) == {
        "C": 4,
        "H": 8,
    }
    assert subtract_loss({"C": 5, "H": 8}, {"H": 2, "O": 1}) is None
    assert subtract_loss({"H": 2, "O": 1}, {"H": 2, "O": 1}) is None


def test_fragment_pairs_are_one_loss_apart():
    neutrals = {
        "C10H16O4": {"C": 10, "H": 16, "O": 4},
        "C10H14O3": {"C": 10, "H": 14, "O": 3},  # less H2O
        "C9H16O2": {"C": 9, "H": 16, "O": 2},  # less CO2
        "C5H8O2": {"C": 5, "H": 8, "O": 2},  # nothing
    }
    assert in_source_fragment_pairs(neutrals) == [
        FragmentPair("C10H16O4", "C9H16O2", "CO2"),
        FragmentPair("C10H16O4", "C10H14O3", "H2O"),
    ]


def test_fragment_pairs_read_only_the_losses_given():
    neutrals = {
        "a": {"C": 10, "H": 16, "O": 4},
        "b": {"C": 10, "H": 14, "O": 3},
    }
    assert in_source_fragment_pairs(neutrals, losses={"CO": {"C": 1, "O": 1}}) == []
    assert in_source_fragment_pairs(neutrals) == [FragmentPair("a", "b", "H2O")]
