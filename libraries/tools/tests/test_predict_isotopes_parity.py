"""Parity: ``mascope_tools`` ``predict_isotopes`` vs frozen molmass reference
envelopes, for non-labelled ions AND labelled custom-element ('^N') ions.

The reference envelopes in ``REFERENCE_ENVELOPES`` were computed with molmass
(``Formula.spectrum()``) -- an independent isotope-pattern implementation -- and
frozen here so the check survives the retirement of the mascope_molmass fork. This
guards the self-contained custom-element convolution in ``predict_isotopes`` against
drift, and the non-labelled cases guard the standard IsoSpec path against regressions.

We assert parity on the chemically significant peaks (>= 1.5% relative, which
includes the ~2% 14N isotopologue of a labelled reagent). Trace peaks below that are
threshold/grouping-convention dependent between IsoSpec and molmass and are not
asserted -- they are negligible for scoring (mass-dominated 0.6/0.2/0.2).

To regenerate the reference (e.g. if a custom element's mass/purity changes), run
``predict_isotopes``'s molmass equivalent ``Formula(mm).spectrum()`` and paste the
neutral-mass -> m/z converted, base-normalised peaks below.
"""

import numpy as np
import pytest

from mascope_tools.composition.heuristic_filter import predict_isotopes


SIGNIFICANT = 0.015  # relative-abundance floor for asserted peaks
MZ_TOL = 0.003
INT_TOL = 0.04

# (ion, charge) -> full molmass reference envelope as base-normalised (m/z, rel)
# peaks. Frozen from molmass Formula.spectrum(); see module docstring.
REFERENCE_ENVELOPES = {
    ("C6H12O6", -1): [
        (180.063937, 1.000000),
        (181.067379, 0.068560),
        (182.068554, 0.014329),
        (183.071721, 0.000873),
        (184.073182, 0.000088),
        (185.076089, 0.000005),
    ],
    ("C6H12BrO6", -1): [
        (258.982274, 1.000000),
        (259.985717, 0.068560),
        (260.980323, 0.987104),
        (261.983751, 0.067566),
        (262.984886, 0.014027),
        (263.988046, 0.000854),
        (264.989495, 0.000086),
    ],
    ("C5H8O6^N", -1): [
        (178.035711, 0.020384),
        (179.032753, 1.000000),
        (180.036201, 0.057496),
        (181.037259, 0.013676),
        (182.040495, 0.000719),
    ],
    ("C10H15O3^N", -1): [
        (197.105742, 0.020362),
        (198.102791, 1.000000),
        (199.106197, 0.111013),
        (200.108258, 0.011734),
        (201.110949, 0.000849),
    ],
    ("C3H4O3^N2", -1): [
        (116.022741, 0.000416),
        (117.019778, 0.040774),
        (118.016819, 1.000000),
        (119.020265, 0.034271),
        (120.021227, 0.006568),
        (121.024508, 0.000210),
    ],
    ("C8H10O3^N", 1): [
        (168.065520, 0.020371),
        (169.062566, 1.000000),
        (170.065969, 0.088854),
        (171.067736, 0.009636),
        (172.070535, 0.000623),
    ],
}


def _tools_envelope(ion: str, charge: int):
    mz, pr, _ = predict_isotopes(ion, charge)
    mz, pr = np.asarray(mz, float), np.asarray(pr, float)
    assert mz.size > 0, f"{ion}: predict_isotopes returned empty"
    return mz, pr / pr.max()


@pytest.mark.parametrize("ion,charge", list(REFERENCE_ENVELOPES))
def test_predict_isotopes_matches_molmass(ion, charge):
    mz, rel = _tools_envelope(ion, charge)
    gt = REFERENCE_ENVELOPES[(ion, charge)]

    # every significant reference peak has a matching tools peak (mass + intensity)
    for gmz, gf in gt:
        if gf < SIGNIFICANT:
            continue
        j = int(np.argmin(np.abs(mz - gmz)))
        assert abs(mz[j] - gmz) < MZ_TOL, f"{ion}: no tools peak near {gmz:.4f}"
        assert abs(rel[j] - gf) < INT_TOL, (
            f"{ion}: intensity at {gmz:.4f}: tools {rel[j]:.3f} vs reference {gf:.3f}"
        )

    # and tools predicts no spurious significant peak that the reference lacks
    gt_mz = np.array([m for m, _ in gt])
    for k in np.where(rel >= SIGNIFICANT)[0]:
        assert np.min(np.abs(gt_mz - mz[k])) < MZ_TOL, (
            f"{ion}: tools predicts spurious peak at {mz[k]:.4f} ({rel[k]:.3f})"
        )


def test_15n_isotopologue_present_and_base_is_15n():
    """The defining 15N behaviour: the dominant (base) peak is the 15N form, with a
    ~2% 14N isotopologue ~0.997 Da below it (purity default 0.98)."""
    mz, rel = _tools_envelope("C5H8O6^N", -1)
    base = int(np.argmax(rel))
    isotopologue = np.where((mz < mz[base] - 0.9) & (mz > mz[base] - 1.1))[0]
    assert isotopologue.size == 1, (
        "expected exactly one 14N isotopologue below the base"
    )
    assert 0.01 < rel[isotopologue[0]] < 0.035, "14N isotopologue should be ~2%"
