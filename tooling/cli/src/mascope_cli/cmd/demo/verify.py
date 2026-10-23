"""
Reproducibility comparison: produced peaks vs. golden expected outputs.

The core :func:`compare_peaks` is pure DataFrame logic shared by the
``mascope demo verify`` command and the end-to-end reproducibility test, so the
two can never drift. It takes the golden ``expected`` peaks (shipped in the
bundle) and the ``actual`` peaks produced by re-running the pipeline, and
reports every difference outside the manifest's tolerances.

Each peak is identified by a **stable key** - by default ``(filename,
target_isotope_id)`` - that survives a rebuild (the raw filename is unique and
the target isotope comes from the restored seed), unlike ``sample_item_id``
which is regenerated on every ingestion. The comparison is a keyed merge on that
key: every expected peak must have an actual peak with the same key whose m/z and
intensity are within tolerance, and there must be no unexpected extra peaks.

:func:`compare_ms2` is the same for the MS2 goldens: the averaged centroids
of every (precursor, activation) group of each sample with fragmentation
scans. A centroid has no stable id, so within a group the two sides are
matched by m/z, nearest first, within the m/z tolerance; what is left on
either side is missing or unexpected.

Obtaining the ``actual`` peaks requires a live, freshly-rebuilt demo stack;
that export seam is ``mascope_backend.db.scripts.export_goldens``. This module
only owns the comparison.
"""

from typing import Any


# Canonical default tolerances: written into a freshly built bundle's manifest
# and used as the fallback when a manifest omits them. The m/z tolerance is
# sub-ppm because the demo data is high-resolution Orbitrap - the Thermo reader
# reproduces matched-peak m/z to sub-0.1 ppm (see libraries/thermo), so 1 ppm
# would be far too loose to catch a real mass-accuracy regression.
DEFAULT_TOLERANCES: dict[str, float] = {
    "mz_ppm": 0.1,
    "intensity_rel": 0.01,
    "area_rel": 0.02,
}


def compare_peaks(
    expected: "Any",
    actual: "Any",
    tolerances: dict[str, float] | None = None,
    *,
    key_cols: tuple[str, ...] = ("filename", "target_isotope_id"),
    mz_col: str = "mz",
    intensity_col: str = "height",
    formula_col: str = "target_isotope_formula",
) -> list[str]:
    """
    Compare produced peaks against golden peaks by stable key, within tolerances.

    Expected and actual are joined on ``key_cols``. For each peak present in both,
    m/z is compared in ppm and intensity by relative difference. Peaks present in
    only one side are reported as missing/unexpected.

    :param expected: Golden peaks (``pandas.DataFrame``).
    :param actual: Produced peaks (``pandas.DataFrame``).
    :param tolerances: Dict with ``mz_ppm`` and ``intensity_rel``. Missing keys
                       fall back to :data:`DEFAULT_TOLERANCES`.
    :param key_cols: Columns forming the stable per-peak key.
    :param mz_col: Column name for m/z in both frames.
    :param intensity_col: Column name for peak height/intensity.
    :param formula_col: Column name for the isotope formula (used only to make
                        missing/unexpected messages readable).
    :return: List of human-readable difference messages (empty = reproduced).
    """
    import numpy as np  # local import: keep CLI import-light

    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    keys = list(key_cols)
    problems: list[str] = []

    # Duplicate keys make the join ambiguous - flag rather than silently pick one.
    for label, frame in (("expected", expected), ("actual", actual)):
        dupes = int(frame.duplicated(keys).sum())
        if dupes:
            problems.append(f"{label} has {dupes} duplicate key(s) on {keys}")
    exp = expected.drop_duplicates(keys)
    act = actual.drop_duplicates(keys)

    merged = exp.merge(
        act, on=keys, how="outer", suffixes=("_exp", "_act"), indicator=True
    )

    # Peaks on only one side.
    f_exp = f"{formula_col}_exp" if formula_col != keys[-1] else formula_col
    f_act = f"{formula_col}_act" if formula_col != keys[-1] else formula_col
    for _, row in merged[merged["_merge"] == "left_only"].iterrows():
        problems.append(f"missing peak: {_key_str(keys, row)} ({row.get(f_exp, '')})")
    for _, row in merged[merged["_merge"] == "right_only"].iterrows():
        problems.append(
            f"unexpected peak: {_key_str(keys, row)} ({row.get(f_act, '')})"
        )

    # Peaks on both sides: vectorized m/z + intensity tolerance checks.
    both = merged[merged["_merge"] == "both"]
    if not both.empty:
        emz = both[f"{mz_col}_exp"].to_numpy(dtype=float)
        amz = both[f"{mz_col}_act"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ppm = np.where(emz != 0, np.abs(amz - emz) / np.abs(emz) * 1e6, 0.0)
        for _, row, e, a, p in _violations(both, ppm > tol["mz_ppm"], emz, amz, ppm):
            problems.append(
                f"{_key_str(keys, row)}: m/z off by {p:.2f} ppm "
                f"(expected {e:.5f}, got {a:.5f}, tol {tol['mz_ppm']} ppm)"
            )

        ev = both[f"{intensity_col}_exp"].to_numpy(dtype=float)
        av = both[f"{intensity_col}_act"].to_numpy(dtype=float)
        denom = np.where(ev != 0, np.abs(ev), 1.0)
        rel = np.abs(av - ev) / denom
        int_tol = tol["intensity_rel"]
        for _, row, e, a, r in _violations(both, rel > int_tol, ev, av, rel):
            problems.append(
                f"{_key_str(keys, row)}: intensity differs by {r * 100:.2f}% "
                f"(expected {e:.4g}, got {a:.4g}, tol {int_tol * 100:.1f}%)"
            )

    return problems


def _violations(frame, mask, expected_vals, actual_vals, metric_vals):
    """Yield ``(idx, row, expected, actual, metric)`` for rows failing ``mask``."""
    idxs = mask.nonzero()[0]
    for i in idxs:
        yield (
            i,
            frame.iloc[i],
            float(expected_vals[i]),
            float(actual_vals[i]),
            float(metric_vals[i]),
        )


def _key_str(keys: list[str], row: "Any") -> str:
    """Render a peak's key columns as ``col=value`` pairs for messages."""
    return ", ".join(f"{k}={row[k]}" for k in keys)


#: The columns that identify one MS2 spectrum: one sample's one precursor at
#: one activation. ``parent_peak_mz`` is the precursor the scan filter names,
#: so it reads the same on a rebuild; it is compared as text, rounded, so a
#: float that round-trips through parquet and JSON still keys the same group.
MS2_GROUP_COLS: tuple[str, ...] = (
    "filename",
    "polarity",
    "parent_peak_mz",
    "activation",
)


def _ms2_group_key(row: "Any") -> tuple[str, str, str, str]:
    """A group's key as text: filename, polarity, precursor (4 decimals), activation."""
    return (
        str(row["filename"]),
        str(row["polarity"]),
        f"{float(row['parent_peak_mz']):.4f}",
        str(row["activation"] or ""),
    )


def _ms2_group_label(key: tuple[str, str, str, str]) -> str:
    """Render a group key for messages: ``file [+] 233.1160@hcd15.00``."""
    filename, polarity, parent, activation = key
    group = f"{parent}@{activation}" if activation else parent
    return f"{filename} [{polarity}] {group}"


def _ms2_groups(frame: "Any") -> dict[tuple[str, str, str, str], "Any"]:
    """Split an MS2 golden frame into its groups, each sorted by m/z."""
    groups: dict[tuple[str, str, str, str], "Any"] = {}
    if frame is None or len(frame) == 0:
        return groups
    keyed = frame.assign(_key=[_ms2_group_key(row) for _, row in frame.iterrows()])
    for key, rows in keyed.groupby("_key", sort=True):
        groups[key] = rows.sort_values("mz", kind="stable").reset_index(drop=True)
    return groups


def _match_centroids(
    expected_mz: "Any", actual_mz: "Any", mz_ppm: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair expected and actual centroids by m/z, nearest first.

    Every pair is within ``mz_ppm``; each centroid is in at most one pair.
    Both inputs are sorted by m/z.

    :return: ``(pairs, unmatched_expected, unmatched_actual)`` as indexes.
    """
    import numpy as np

    expected_mz = np.asarray(expected_mz, dtype=float)
    actual_mz = np.asarray(actual_mz, dtype=float)
    if expected_mz.size == 0 or actual_mz.size == 0:
        return [], list(range(expected_mz.size)), list(range(actual_mz.size))
    # Every candidate pair within tolerance, closest first, so a centroid that
    # two neighbours could claim goes to the nearer one.
    candidates = []
    for i, mz in enumerate(expected_mz):
        lo = np.searchsorted(actual_mz, mz * (1 - mz_ppm * 1e-6), side="left")
        hi = np.searchsorted(actual_mz, mz * (1 + mz_ppm * 1e-6), side="right")
        for j in range(lo, hi):
            candidates.append((abs(actual_mz[j] - mz) / mz * 1e6, i, j))
    candidates.sort()
    taken_e: set[int] = set()
    taken_a: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, i, j in candidates:
        if i in taken_e or j in taken_a:
            continue
        pairs.append((i, j))
        taken_e.add(i)
        taken_a.add(j)
    unmatched_e = [i for i in range(expected_mz.size) if i not in taken_e]
    unmatched_a = [j for j in range(actual_mz.size) if j not in taken_a]
    return pairs, unmatched_e, unmatched_a


def compare_ms2(
    expected: "Any",
    actual: "Any",
    tolerances: dict[str, float] | None = None,
) -> list[str]:
    """
    Compare produced MS2 spectra against golden ones, group by group.

    A group is one sample's one precursor at one activation
    (:data:`MS2_GROUP_COLS`); a group on only one side is reported as missing
    or unexpected. Within a group that is on both sides, the scan count and
    isolation width must agree, and the centroids are paired by m/z within
    ``mz_ppm`` (nearest first). A centroid left unpaired on either side is
    missing or unexpected; a pair's intensity is compared by relative
    difference, within ``intensity_rel``.

    The frames are what ``export_goldens.get_golden_ms2`` returns: one row per
    centroid with its group's columns beside it.

    :param expected: Golden MS2 rows (``pandas.DataFrame``).
    :param actual: Produced MS2 rows (``pandas.DataFrame``).
    :param tolerances: ``mz_ppm`` and ``intensity_rel``, as for
                       :func:`compare_peaks`; missing keys fall back to
                       :data:`DEFAULT_TOLERANCES`.
    :return: Human-readable difference messages (empty = reproduced).
    """
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    problems: list[str] = []

    exp_groups = _ms2_groups(expected)
    act_groups = _ms2_groups(actual)
    for key in sorted(set(exp_groups) - set(act_groups)):
        problems.append(f"missing MS2 group: {_ms2_group_label(key)}")
    for key in sorted(set(act_groups) - set(exp_groups)):
        problems.append(f"unexpected MS2 group: {_ms2_group_label(key)}")

    for key in sorted(set(exp_groups) & set(act_groups)):
        label = _ms2_group_label(key)
        exp = exp_groups[key]
        act = act_groups[key]

        # The group's own numbers, carried on every row: read off the first.
        for col in ("scan_count", "isolation_width"):
            if col not in exp.columns or col not in act.columns:
                continue
            e, a = exp[col].iloc[0], act[col].iloc[0]
            if _differs(e, a):
                problems.append(f"{label}: {col} differs (expected {e}, got {a})")

        pairs, unmatched_e, unmatched_a = _match_centroids(
            exp["mz"].to_numpy(), act["mz"].to_numpy(), tol["mz_ppm"]
        )
        for i in unmatched_e:
            problems.append(
                f"{label}: missing MS2 centroid m/z {float(exp['mz'].iloc[i]):.5f} "
                f"(intensity {float(exp['intensity'].iloc[i]):.4g})"
            )
        for j in unmatched_a:
            problems.append(
                f"{label}: unexpected MS2 centroid m/z {float(act['mz'].iloc[j]):.5f} "
                f"(intensity {float(act['intensity'].iloc[j]):.4g})"
            )
        int_tol = tol["intensity_rel"]
        for i, j in pairs:
            e = float(exp["intensity"].iloc[i])
            a = float(act["intensity"].iloc[j])
            rel = abs(a - e) / (abs(e) if e != 0 else 1.0)
            if rel > int_tol:
                problems.append(
                    f"{label}: m/z {float(exp['mz'].iloc[i]):.5f} intensity differs "
                    f"by {rel * 100:.2f}% (expected {e:.4g}, got {a:.4g}, "
                    f"tol {int_tol * 100:.1f}%)"
                )

    return problems


def _differs(expected: "Any", actual: "Any") -> bool:
    """Whether two group-level values differ, with None and NaN read as absent."""
    import math

    def _absent(value) -> bool:
        return value is None or (isinstance(value, float) and math.isnan(value))

    if _absent(expected) and _absent(actual):
        return False
    if _absent(expected) or _absent(actual):
        return True
    try:
        return float(expected) != float(actual)
    except (TypeError, ValueError):
        return expected != actual
