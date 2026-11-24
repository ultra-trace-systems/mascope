"""The resolved lines are those of the line model as it read them line by line.

MAS-1190 made :func:`resolve_lines` and the steps it calls cheaper: the
resolving power read once per m/z, a group read from the lines near it, the
lines of one member read at once, the folds read for every line at once. None
of that may move a line by a bit. The reference below is the line model as it
was before (develop at 649a297bd), each line read on its own, and every line
it gives must be the same, to the last bit of its m/z and abundance.
"""

import math

import numpy as np
import pytest

from mascope_tools.composition import line_model
from mascope_tools.composition.line_model import (
    HeightResolution,
    fold_targets,
    group_unresolved_lines,
    height_sum_span,
    predict_isotopes,
    resolve_lines,
)


# --- The reference: the line model before MAS-1190 --------------------------


def _ref_groups(mzs, intensities, resolution, span=0.5):
    mzs = np.asarray(mzs, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    order = np.lexsort((np.arange(mzs.size), -intensities))
    taken = np.zeros(mzs.size, dtype=bool)
    groups = []
    for brightest in order:
        if taken[brightest]:
            continue
        centre_mz = float(mzs[brightest])
        try:
            resolving_power = float(resolution(centre_mz))
        except (TypeError, ValueError, ArithmeticError):
            resolving_power = float("nan")
        members = np.array([brightest])
        if np.isfinite(resolving_power) and resolving_power > 0:
            reach = centre_mz / resolving_power * span
            within = np.flatnonzero(~taken & (np.abs(mzs - centre_mz) < reach))
            if within.size:
                members = within
        taken[members] = True
        groups.append(members)
    return groups


def _ref_name(members, intensities, labels):
    own = [index for index in members if labels[index] == "M0"]
    if own:
        return int(own[0])
    return int(np.asarray(members)[np.argmax(intensities[members])])


def _ref_fold_targets(mzs, keep, resolution):
    mzs = np.asarray(mzs, dtype=float)
    keep = np.asarray(keep, dtype=bool)
    targets = np.full(mzs.size, -1, dtype=int)
    kept = np.flatnonzero(keep)
    dropped = np.flatnonzero(~keep)
    if not kept.size or not dropped.size:
        return targets
    fwhm = np.full(kept.size, np.nan)
    for position, index in enumerate(kept):
        try:
            resolving_power = float(resolution(float(mzs[index])))
        except (TypeError, ValueError, ArithmeticError):
            continue
        if np.isfinite(resolving_power) and resolving_power > 0:
            fwhm[position] = float(mzs[index]) / resolving_power
    for index in dropped:
        distance = np.abs(mzs[kept] - mzs[index])
        within = np.flatnonzero(distance < fwhm)
        if within.size:
            targets[index] = int(kept[within[np.argmin(distance[within])]])
    return targets


def _ref_line_of(members, mzs, intensities, resolution=None):
    members = np.asarray(members, dtype=int)
    weights = intensities[members]
    sum_span = height_sum_span(resolution)
    if sum_span is not None and members.size > 1:
        dominant = int(members[np.argmax(weights)])
        centre = float(mzs[dominant])
        try:
            resolving_power = float(resolution(centre))
        except (TypeError, ValueError, ArithmeticError):
            resolving_power = float("nan")
        if resolving_power > 0:
            reach = centre / resolving_power * sum_span
            adds = np.abs(mzs[members] - centre) < reach
            adds[members == dominant] = True
            return centre, float(weights[adds].sum())
        return centre, float(intensities[dominant])
    total = float(weights.sum())
    centre = (
        float(np.sum(mzs[members] * weights) / total)
        if total > 0
        else float(mzs[members].mean())
    )
    return centre, total


def _ref_join(fine, lines, resolution, level):
    mzs, intensities, line_labels = fine
    names, members, keep, centres, totals = lines
    under = np.flatnonzero(~keep)
    absorbed = np.zeros(len(members), dtype=bool)
    for group in _ref_groups(centres[under], totals[under], resolution, span=1.0):
        together = under[group]
        if height_sum_span(resolution) is None:
            joined = float(totals[together].sum())
        else:
            joined = _ref_line_of(
                [member for index in together for member in members[index]],
                mzs,
                intensities,
                resolution,
            )[1]
        if joined < level:
            continue
        head = _ref_name(together, totals, line_labels)
        for index in together:
            if index != head:
                members[head].extend(members[index])
                absorbed[index] = True
        members[head].sort()
        keep[head] = True
    stays = np.flatnonzero(~absorbed)
    return (
        [names[index] for index in stays],
        [members[index] for index in stays],
        keep[stays],
    )


def _ref_resolve(mzs, intensities, labels, resolution, floor, keep_under_floor=False):
    mzs = np.asarray(mzs, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    labels = list(labels)
    if not mzs.size:
        return []
    if resolution is None:
        groups = [np.array([index]) for index in range(mzs.size)]
    else:
        groups = _ref_groups(mzs, intensities, resolution)
    named = sorted(
        (
            (_ref_name(members, intensities, labels), sorted(map(int, members)))
            for members in groups
        ),
        key=lambda line: line[0],
    )
    names = [own for own, _ in named]
    members = [list(group) for _, group in named]

    def arrays():
        pairs = [_ref_line_of(group, mzs, intensities, resolution) for group in members]
        return (
            np.array([centre for centre, _ in pairs], dtype=float),
            np.array([total for _, total in pairs], dtype=float),
        )

    centres, totals = arrays()
    level = floor * float(totals.max())
    keep = totals >= level
    if resolution is not None:
        under = np.flatnonzero(~keep)
        if under.size and float(totals[under].sum()) >= level:
            names, members, keep = _ref_join(
                (mzs, intensities, [labels[own] for own in names]),
                (names, members, keep, centres, totals),
                resolution,
                level,
            )
            centres, totals = arrays()
        targets = _ref_fold_targets(centres, keep, resolution)
        for index in np.flatnonzero(targets >= 0):
            members[int(targets[index])].extend(members[index])
        for group in members:
            group.sort()
    else:
        targets = np.full(len(members), -1, dtype=int)
    lines = []
    for index, group in enumerate(members):
        if not keep[index] and (targets[index] >= 0 or not keep_under_floor):
            continue
        if height_sum_span(resolution) is not None:
            centre, total = float(centres[index]), float(totals[index])
        else:
            centre, total = _ref_line_of(group, mzs, intensities, resolution)
        lines.append((centre, total, labels[names[index]], tuple(group)))
    return lines


# --- The cases ---------------------------------------------------------------


def _orbitrap(power_at_200):
    a = power_at_200 * math.sqrt(200.0)
    return lambda mz: a / np.sqrt(mz)


def _tof(a, b):
    return lambda mz: mz / (a * mz + b)


def _raises(_mz):
    raise ZeroDivisionError


RESOLUTIONS = {
    "orbitrap 120k by heights": HeightResolution(_orbitrap(120_000.0)),
    "orbitrap 15k by heights": HeightResolution(_orbitrap(15_000.0)),
    "orbitrap 15k, a wide sum span": HeightResolution(_orbitrap(15_000.0), 1.5),
    "orbitrap 60k by areas": _orbitrap(60_000.0),
    "tof 9000": _tof(1 / 9000, 0.002),
    "tof 3000": _tof(1 / 3000, 0.01),
    "a legacy polynomial": np.poly1d([-0.05, 10.0, 5000.0]),
    "a constant 2000": lambda _mz: 2000.0,
    "a constant 2000 by heights": HeightResolution(lambda _mz: 2000.0),
    "none": None,
}

IONS = [
    ("C4H8NO", 1),
    ("C10H16NO3", 1),
    ("C6H12O6Na", 1),
    ("C10H16N5O13P3", -1),
    ("C20H40O2Br", -1),
    ("C12H22O11Cl", -1),
    ("C3H6Br2Cl", -1),
    ("C9H17N3O4S2", 1),
    ("C40H80NO8P", 1),
    ("C7H5Br3O", -1),
    ("C2Cl4", -1),
    ("C18H34O2I", -1),
]


def _same(fast, reference):
    """Each line equal to the reference's, the floats compared bit for bit."""
    assert [
        (line.mz.hex(), line.abundance.hex(), line.label, line.members) for line in fast
    ] == [
        (float(mz).hex(), float(total).hex(), label, members)
        for mz, total, label, members in reference
    ]
    assert all(
        type(line.mz) is float and type(line.abundance) is float for line in fast
    )


@pytest.mark.parametrize("resolution", RESOLUTIONS.values(), ids=RESOLUTIONS.keys())
@pytest.mark.parametrize("ion, charge", IONS, ids=[ion for ion, _ in IONS])
def test_an_ion_resolves_to_the_lines_it_resolved_to(ion, charge, resolution):
    for floor in (1e-2, 1e-3, 1e-4):
        fine = predict_isotopes(ion, charge, None, floor * 1e-3)
        for keep_under_floor in (False, True):
            _same(
                resolve_lines(
                    *fine, resolution, floor, keep_under_floor=keep_under_floor
                ),
                _ref_resolve(*fine, resolution, floor, keep_under_floor),
            )


def _clustered_lines(rng, size):
    """Lines in a few tight clusters, abundances over six decades, ties and
    lines of no abundance among them: what joins, folds and ties are made of."""
    centres = rng.uniform(50.0, 900.0, rng.integers(1, 6))
    mzs = rng.choice(centres, size) + rng.normal(0.0, 0.004, size)
    intensities = 10.0 ** rng.uniform(-6.0, 0.0, size)
    intensities[rng.random(size) < 0.1] = intensities[0]
    intensities[rng.random(size) < 0.05] = 0.0
    labels = [f"L{index}" for index in range(size)]
    labels[int(rng.integers(size))] = "M0"
    return mzs, intensities, labels


@pytest.mark.parametrize("seed", range(40))
def test_lines_of_any_shape_resolve_as_they_did(seed):
    rng = np.random.default_rng(seed)
    mzs, intensities, labels = _clustered_lines(rng, int(rng.integers(1, 80)))
    for resolution in RESOLUTIONS.values():
        for floor in (0.5, 1e-2, 1e-4):
            _same(
                resolve_lines(mzs, intensities, labels, resolution, floor),
                _ref_resolve(mzs, intensities, labels, resolution, floor),
            )
            _same(
                resolve_lines(
                    mzs, intensities, labels, resolution, floor, keep_under_floor=True
                ),
                _ref_resolve(mzs, intensities, labels, resolution, floor, True),
            )


#: Resolving powers that read nothing, or that reach no line.
UNREADABLE = {
    "nan": lambda _mz: float("nan"),
    "inf": lambda _mz: float("inf"),
    "zero": lambda _mz: 0.0,
    "negative": lambda _mz: -1.0,
    "raises": _raises,
    "nan by heights": HeightResolution(lambda _mz: float("nan")),
    "raises by heights": HeightResolution(_raises),
}


def test_each_line_reads_the_resolving_power_at_its_own_m_z():
    # A power that steps between two lines 0.04 Da apart: read at its own m/z
    # the brighter line resolves the other, read at 200.0 it would take it in.
    def stepped(mz):
        return 1_000.0 if mz < 200.02 else 1_000_000.0

    mzs = np.array([200.04, 200.0])
    intensities = np.array([1.0, 0.5])
    labels = ["M0", "L1"]

    lines = resolve_lines(mzs, intensities, labels, stepped, 1e-3)

    _same(lines, _ref_resolve(mzs, intensities, labels, stepped, 1e-3))
    assert len(lines) == 2


@pytest.mark.parametrize("resolution", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_a_resolving_power_that_reads_nothing_resolves_as_it_did(resolution):
    rng = np.random.default_rng(7)
    mzs, intensities, labels = _clustered_lines(rng, 40)
    for floor in (0.5, 1e-3):
        _same(
            resolve_lines(mzs, intensities, labels, resolution, floor),
            _ref_resolve(mzs, intensities, labels, resolution, floor),
        )


@pytest.mark.parametrize(
    "mzs",
    [
        [100.0, float("nan"), 100.001, 100.002],
        [100.0, float("inf"), 100.001],
        [0.0, 100.0, 100.001, -5.0],
        [-100.0, -100.001, 100.0],
    ],
    ids=["a NaN m/z", "an infinite m/z", "m/z at and under zero", "negative m/z"],
)
def test_an_m_z_that_reads_nothing_groups_as_it_did(mzs):
    intensities = np.linspace(1.0, 0.2, len(mzs))
    for resolution in (_orbitrap(15_000.0), lambda _mz: 2000.0, _tof(1 / 3000, 0.01)):
        assert [
            group.tolist()
            for group in group_unresolved_lines(mzs, intensities, resolution)
        ] == [group.tolist() for group in _ref_groups(mzs, intensities, resolution)]


def test_a_line_just_inside_the_reach_is_grouped_and_one_just_outside_is_not():
    # The reach is the brightest line's m/z over the resolving power, times the
    # span: 0.05 here. A group reads the lines between its edges as rounded,
    # so a line one ulp inside the reach joins and one at it does not, as
    # before, on either side.
    centre = 100.0
    reach = centre / 1000.0 * 0.5
    above, below = centre + reach, centre - reach
    while abs(above - centre) < reach:
        above = math.nextafter(above, math.inf)
    while abs(below - centre) < reach:
        below = math.nextafter(below, -math.inf)
    mzs = np.array(
        [centre, math.nextafter(above, 0.0), above, below, math.nextafter(below, 200.0)]
    )
    intensities = np.array([1.0, 0.5, 0.4, 0.3, 0.2])

    groups = group_unresolved_lines(mzs, intensities, lambda _mz: 1000.0)

    assert [group.tolist() for group in groups] == [
        group.tolist() for group in _ref_groups(mzs, intensities, lambda _mz: 1000.0)
    ]
    assert groups[0].tolist() == [0, 1, 4]


@pytest.mark.parametrize("seed", range(20))
def test_the_fold_targets_are_those_read_line_by_line(seed):
    rng = np.random.default_rng(100 + seed)
    size = int(rng.integers(1, 60))
    mzs = rng.uniform(100.0, 101.0, size)
    mzs[rng.random(size) < 0.1] = mzs[0]
    keep = rng.random(size) < 0.4
    for resolution in (_orbitrap(15_000.0), lambda _mz: 500.0, _raises):
        assert fold_targets(mzs, keep, resolution).tolist() == (
            _ref_fold_targets(mzs, keep, resolution).tolist()
        )


def test_a_line_one_fwhm_from_a_kept_line_is_not_folded():
    # 1/128 and 1/64 are exact: the line sits one FWHM away to the last bit.
    mzs = np.array([128.0, 129.0, 128.5])
    keep = np.array([True, False, False])

    targets = fold_targets(mzs, keep, lambda _mz: 128.0)

    assert targets.tolist() == [-1, -1, 0]


def test_the_resolving_power_is_read_once_per_m_z():
    # Each step reads it at the lines it starts from, the same lines again and
    # again: an ion's lines read it at no m/z twice.
    read = []

    def counting(mz):
        read.append(mz)
        return 15_000.0 * math.sqrt(200.0 / mz)

    fine = predict_isotopes("C12H22O11Cl", -1, None, 1e-6)
    resolve_lines(*fine, HeightResolution(counting), 1e-3)

    assert read
    assert len(read) == len(set(read))


def test_the_memo_answers_each_m_z_its_own_resolving_power():
    calls = []

    def identity(mz):
        calls.append(mz)
        return mz

    power = line_model._power_of(identity)
    next_up = math.nextafter(200.0, 300.0)
    for mz in (200.0, 200.04, next_up, 200.0, next_up):
        assert power(mz) == mz
    assert calls == [200.0, 200.04, next_up]

    unreadable = line_model._power_of(_raises)
    assert math.isnan(unreadable(200.0))
    assert math.isnan(unreadable(200.04))


def test_a_line_of_one_member_reads_as_a_line_of_several_does():
    # Its centre is its m/z times its share over its share, as the sum over
    # several members reads it; on a share of nothing, its m/z.
    mzs = np.array([100.1, 200.3, 300.7])
    intensities = np.array([0.3, 0.0, float("nan")])
    centres, totals = line_model._lines_at(
        [[0], [1], [2]], mzs, intensities, None, line_model._power_of(None)
    )

    for index in range(3):
        centre, total = _ref_line_of([index], mzs, intensities)
        assert centres[index].hex() == centre.hex()
        assert np.array_equal(totals[index], total, equal_nan=True)
