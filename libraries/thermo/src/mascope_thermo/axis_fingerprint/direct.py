"""The direct route: a frequency band's deviation from the m/z correction the
raw file stores with each profile chunk.

The fingerprint (:mod:`.reference`) measures a chunk's spacing deviation
``s`` from the spacing of its points. The file also stores the correction
itself: OpenTFRaw's ``RawFile.profile_chunks(scan)`` gives each chunk's first
FFT bin, its number of points and its ``fudge``, the m/z added to every point
of the chunk after conversion. As a share of the law's m/z, ``r = fudge /
law(f)`` in ppm, it gives ``s`` directly (``reference.s_from_fudge_ratio``).

This route checks the spacing route; it does not replace it. On the 39
reference files of the request, every band of the spacing route's table
agrees with the stored corrections to 0.0031 ppm. Fed to the vendored
algorithm in place of the spacing, on the chunks the spacing route keeps,
the stored values move the band grid of 21 of 53 modes, and those grids
match the band edges the stored corrections show no better. On every
stored chunk instead, the grid is weak in 12 of the 39 dominant modes
against 7: a quarter of the chunks (10 to 34 % per file) store a
correction off their band's value, and the extra levels cost the grid
its brackets. So the spacing route, which the reference documents pin,
stays the fingerprint. This module reads the stored corrections of the
scans the fingerprint read and names the bands of its table that disagree
with them: a reader or an instrument on which the spacing no longer gives
back the stored correction.

Only a band structure is checked: a mode whose grid is measured, stepwise
and on a power-of-two FFT bin, and in it a band where both routes hold one
level. On an axis without bands (every Exploris in the regression corpus)
the corrections follow a smooth trend, a band's modal value depends on
which chunks it is taken over, and the two routes' differ by up to 10 ppm
while every chunk agrees. Where the FFT step is no power of two (an
Exploris MX), the spacing route reads ``s`` against the measured step, and
every chunk sits 2.4 to 4 ppm off its stored correction.
"""

from __future__ import annotations

import numpy as np

from mascope_thermo.axis_fingerprint import reference


#: How far a band's ``s_ppm`` may sit from the modal ``s`` of the stored
#: corrections in it. The spacing route reads the levels of a band to 0.003
#: ppm (``level_tol_ppm``); on the reference files the largest difference
#: is 0.0031 ppm.
TOLERANCE_PPM = 0.01

#: The flags of a mode whose table is not checked: no band structure was
#: measured, or ``s`` is read against a step that is not the FFT bin.
UNCHECKED_FLAGS = frozenset(
    {"grid_unmeasured", "not_stepwise", "fft_step_not_power_of_two"}
)

#: The share of a band's chunks that must sit at its level, in the table
#: and among the stored corrections, for the band to be compared: the
#: share below which ``reference`` calls a table not stepwise.
MIN_LEVEL_SHARE = 0.5


def chunk_s(chunks: dict, B: float, C: float, low_mass: float, params: dict):
    """Each stored chunk's frequency and ``s``, from its stored correction.

    The frequency is the law's of the chunk's middle point as the reader
    returns it, its m/z plus the correction, so that a chunk falls in the
    band the spacing route puts it in.

    :param chunks: ``RawFile.profile_chunks(scan)``: ``first_value``,
        ``step``, ``first_bin``, ``n_points``, ``fudge``.
    :param B: The scan's ``Conversion Parameter B``.
    :param C: The scan's ``Conversion Parameter C``.
    :param low_mass: The low end of the scan range: a chunk below it plus
        ``low_mass_margin`` is left out, as the spacing route leaves it out.
    :param params: The algorithm's parameters.
    :return: ``(f_khz, s_ppm)``, one value per chunk that stores a
        correction.
    """
    first_bin = np.asarray(chunks["first_bin"], float)
    n_points = np.asarray(chunks["n_points"], float)
    fudge = np.asarray(chunks["fudge"], float)
    first = chunks["first_value"] + first_bin * chunks["step"]
    middle = chunks["first_value"] + (first_bin + (n_points - 1) / 2) * chunks["step"]
    law_mz = reference.mz_of_f(middle, B, C)
    # The margin on the law's m/z: a correction moves it by ppm, not m/z
    keep = np.isfinite(fudge) & (
        reference.mz_of_f(first, B, C) >= low_mass + params["low_mass_margin"]
    )
    law_mz, fudge = law_mz[keep], fudge[keep]
    f_khz = reference.law_khz(law_mz + fudge, B, C)
    return f_khz, reference.s_from_fudge_ratio(fudge / law_mz * 1e6)


def check_mode(mode: dict, chunks_by_scan: dict, params: dict) -> dict:
    """How a mode's band table agrees with the corrections its scans store.

    Each measured band of the table (``interp`` false) is compared with the
    modal ``s`` of the stored corrections that fall in it on the mode's grid
    (``reference.modal_level``, as the table's own level), where at least
    ``min_chunks_per_segment`` corrections agree on it and both the table's
    ``level_share`` and the share of the corrections at that level reach
    :data:`MIN_LEVEL_SHARE`.

    :param mode: One mode of a document, with a band table.
    :param chunks_by_scan: ``{scan: (chunks, B, C)}`` for the scans the mode
        read its profile from, ``chunks`` as :func:`chunk_s` takes them.
    :param params: The algorithm's parameters.
    :return: ``n_bands``, the bands compared; ``max_abs_dev_ppm``, the
        largest difference, None when none was compared; ``bands_off``, the
        ``k`` of the bands more than :data:`TOLERANCE_PPM` off.
    """
    low_mass = mode["scan_range"][0]
    f_parts, s_parts = [], []
    for chunks, B, C in chunks_by_scan.values():
        f_khz, s_ppm = chunk_s(chunks, B, C, low_mass, params)
        f_parts.append(f_khz)
        s_parts.append(s_ppm)
    f_khz = np.concatenate(f_parts) if f_parts else np.zeros(0)
    s_ppm = np.concatenate(s_parts) if s_parts else np.zeros(0)
    period, phase = mode["grid"]["period_khz"], mode["grid"]["phase_khz"]
    k_of = np.floor((f_khz - phase) / period).astype(int)
    columns = mode["segments"]["columns"]
    at_k, at_s, at_share, at_interp = (
        columns.index(n) for n in ("k", "s_ppm", "level_share", "interp")
    )
    compared, worst, off = 0, 0.0, []
    for row in mode["segments"]["rows"]:
        if row[at_interp] or row[at_share] < MIN_LEVEL_SHARE:
            continue
        in_band = s_ppm[k_of == row[at_k]]
        level, n = reference.modal_level(in_band, params["level_tol_ppm"])
        if n < params["min_chunks_per_segment"] or n < MIN_LEVEL_SHARE * in_band.size:
            continue
        compared += 1
        deviation = abs(level - row[at_s])
        worst = max(worst, deviation)
        if deviation > TOLERANCE_PPM:
            off.append(row[at_k])
    return {
        "n_bands": compared,
        "max_abs_dev_ppm": round(worst, 4) if compared else None,
        "bands_off": off,
    }


def check_document(document: dict, read_chunks, params: dict) -> dict:
    """:func:`check_mode` for every mode of a document with a band table
    and none of :data:`UNCHECKED_FLAGS`.

    :param document: A document of any status but the minimal ones; its
        other modes are not read.
    :param read_chunks: Reads one scan's stored chunks,
        ``RawFile.profile_chunks``; None for a scan with no stored profile.
    :param params: The algorithm's parameters.
    :return: ``{mode_id: check}`` for the modes checked.
    """
    columns = document["scan_table"]["columns"]
    at_scan, at_b, at_c = (columns.index(n) for n in ("scan", "B", "C"))
    calibration = {
        row[at_scan]: (row[at_b], row[at_c]) for row in document["scan_table"]["rows"]
    }
    out = {}
    for mode in document["modes"]:
        if not mode.get("segments") or UNCHECKED_FLAGS & set(mode["flags"]):
            continue
        chunks_by_scan = {}
        for scan in mode["profile"]["scans_used"]:
            chunks = read_chunks(scan)
            if chunks is not None:
                chunks_by_scan[scan] = (chunks, *calibration[scan])
        out[mode["mode_id"]] = check_mode(mode, chunks_by_scan, params)
    return out
