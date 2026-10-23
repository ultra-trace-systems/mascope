"""Reference implementation of the per-file Orbitrap 'axis fingerprint' (schema mascope.axis_fingerprint v1).

Reader-agnostic: every function takes plain numpy arrays.  The input is the corrected profile m/z, which the
RAW stores as frequency bins plus a per-chunk f32 'fudge' (PacketHeader.layout & 0xFF != 0):
    m/z = B/f^2 + C/f^4 + fudge
That is what Thermo's RawFileReader returns and what OpenTFRaw >= 1.4.1 profile() returns, bit for bit on the
stored points (SPEC section 1).  Feed OpenTFRaw >= 1.4.1 with PARAMS_OPENTFRAW (stored points only; Thermo pads each
chunk with zero-intensity points, which PARAMS counts).  Never feed OpenTFRaw 1.4.0: it returns law(f + fudge),
every chunk then sits on the exact FFT grid and every s is 0 by construction (minimal_document('unavailable')).

Dependencies: numpy only.  Deterministic (the random-placement test uses numpy.random.default_rng(seed)).

Terms
  law frequency  f(m) = sqrt((B + sqrt(B^2 + 4 m C)) / (2 m))  [kHz], inverse m = B/f^2 + C/f^4,
                 with the scan's trailer 'Conversion Parameter B' and 'Conversion Parameter C'.
  chunk          a run of stored profile points between gaps (Thermo stores profile only around peaks).
  FFT step       the frequency step between adjacent stored points inside a chunk, measured per scan.
  s              the relative deviation (ppm) of a chunk's regular step from the nominal FFT bin width
                 (a power of two in kHz).  Takes ONE exact value per frequency segment of ~16.28 kHz on the
                 Q Exactive data tested, identical in every scan of a calibration.  With r = fudge / law(f)
                 (ppm), s = ((1 + r 1e-6)^-1.5 - 1) 1e6, about -1.5 r (s_from_fudge_ratio).

Details that decide results (this code prevails wherever the specification's prose is silent)
  FFT step       log10 histogram of the positive |diff f| with 0.001-decade bins from min(log10) to max + 0.002;
                 the first bin with the highest count wins; then the median of the steps within +-0.2 % of that
                 bin's centre.  Fewer than 20 positive steps -> NaN -> the scan yields no chunks.
  level runs     pieces are accepted greedily by chunk count, ties by the higher f_hi; a piece's level is the median
                 of its chunk s values.
  grid search    periods are scanned in increasing order and phases from 0 upwards; the FIRST maximum is kept
                 (lowest period, then lowest phase).  The fixed-frequency hypothesis wins ties.  The random test
                 searches only the winning hypothesis's +-3 % period range.  The refine runs only with >= 2 hits at
                 >= 2 distinct k; otherwise period and phase stay at the coarse search values and
                 edge_uncertainty_khz is null.
  grid_weak      n_hits < 10, or k_max - k_min of the hit brackets < 20 (so 20 distinct k are not required).
  segment s      the median of the modal chain (the largest chain of sorted values at < level_tol_ppm; ties ->
                 the chain with the lower values); scan_sd_ppm = sample sd (ddof = 1) of the per-scan modal levels
                 of scans with >= 2 chunks at their level, null with fewer than 2 such scans.
  modes          only FTMS MS1 full scans form modes (parse_filter); modes are ordered by scan count, equal counts
                 keep first-seen (scan) order; scan_share counts scans in modes only.
  scan pick      profile scans only (IsCentroidScan False); see pick_scans.
  lock 'both'    counts half to each of the first two configured locks of the mode, sorted ascending.
"""

from __future__ import annotations

import math
import re

import numpy as np


SCHEMA = "mascope.axis_fingerprint"
VERSION = 1

PARAMS = dict(
    n_scans=8,  # profile scans read per mode
    min_chunk_points=6,  # a chunk needs at least this many stored points
    chunk_break_x=3.0,  # a gap > chunk_break_x * FFT step ends a chunk
    step_window_rel=2e-3,  # candidate regular intervals: within +-0.2 % of the scan's FFT step
    min_candidates=4,  # a chunk needs >= 4 candidate intervals
    reg_rel_tol=2e-7,  # regular interval: within 2e-7 (relative) of the chunk's candidate median
    nreg_min=6,  # a chunk needs >= 6 regular intervals
    low_mass_margin=5.0,  # first point of a chunk at m/z >= scan low mass + 5
    weak_rule="none",  # 'none' (default); 'absolute': Imax < weak_abs_counts (the prototype's rule);
    # 'relative': Imax <= the scan's median chunk Imax (drops the lock segments)
    weak_abs_counts=2000.0,
    min_chunks_per_segment=3,
    level_tol_ppm=0.003,  # sorted chunk s values closer than this chain into one level
    run_split_khz=8.0,  # a level whose chunks leave a gap > 8 kHz is split into separate runs
    run_min_chunks=3,  # a run needs >= 3 chunks
    bracket_fit_max_khz=4.0,  # brackets wider than this are stored but not used for the grid fit
    grid_seed_period_khz=16.2846,  # search seed only (one Q Exactive at R 280k)
    grid_seed_fft_step_hz=0.244140625,  # 2^-12 kHz (the "fixed_bins" period hypothesis scales with it)
    grid_period_search_rel=0.03,
    grid_period_step_khz=0.002,
    grid_phase_step_khz=0.05,
    grid_hit_margin_khz=0.05,
    grid_min_hits=5,
    grid_random_draws=50,
    grid_random_period_step_khz=0.01,
    grid_random_phase_step_khz=0.2,
    grid_random_seed=1,
    grid_default_phase_khz=7.945,  # used only when the grid is not measured (source 'seed')
    grid_weak_min_hits=10,  # a measured grid with n_hits < 10, or whose hit indices give k_max - k_min < 20,
    grid_weak_min_k_span=20,  # is flagged 'grid_weak' (advisory; checks/c8, c9: small minority modes fit wrong periods)
    lock_match_ppm=20.0,  # absence rule window (fallback when centroid flags are not available)
)

# For readers that return the STORED profile points only (OpenTFRaw >= 1.4.1 profile()).  Thermo's RawFileReader adds
# zero-intensity points around every stored chunk (up to 4 + 4, gaps <= 8 bins filled), which PARAMS counts; on the
# stored points alone the default thresholds drop most chunks and the grid loses brackets (checks/c16).  Only these
# three thresholds differ.  reference_fingerprints.json was built with PARAMS on Thermo's profile.
PARAMS_OPENTFRAW = dict(PARAMS, min_chunk_points=4, min_candidates=3, nreg_min=3)

MIN_OPENTFRAW = (
    1,
    4,
    1,
)  # 1.4.0 profile() returns law(f + fudge): never compute from it


def opentfraw_version_ok(version):
    """True when an OpenTFRaw version string (e.g. '1.6.0') returns corrected profile m/z (>= 1.4.1)."""
    try:
        parts = tuple(int(x) for x in re.findall(r"\d+", str(version))[:3])
    except ValueError:
        return False
    return len(parts) == 3 and parts >= MIN_OPENTFRAW


def s_from_fudge_ratio(r_ppm):
    """Direct route: the spacing anomaly s (ppm) of a chunk whose profile m/z carry fudge = r * law(f) (r in ppm).
    Inverting m = law(f) + fudge with the law gives f' with df'/df = (1 + r 1e-6)^-1.5 (C/f^4 term second order)."""
    return ((1.0 + np.asarray(r_ppm, float) * 1e-6) ** -1.5 - 1.0) * 1e6


# ------------------------------------------------------------------------------------------------ #
# law
# ------------------------------------------------------------------------------------------------ #
def law_khz(m, B, C):
    m = np.asarray(m, float)
    return np.sqrt((B + np.sqrt(B * B + 4.0 * m * C)) / (2.0 * m))


def mz_of_f(f, B, C):
    f = np.asarray(f, float)
    return B / f**2 + C / f**4


# ------------------------------------------------------------------------------------------------ #
# scan-level helpers
# ------------------------------------------------------------------------------------------------ #
_FTMS_RE = re.compile(r"^\s*FTMS(?=\s|\{|$)")
_POL_RE = re.compile(r"(?:^|\s)([+-])(?=\s)")  # a standalone '+' or '-' token
_TYPE_RE = re.compile(
    r"(?:^|\s)([pc])(?=\s)"
)  # a standalone 'p' (profile) or 'c' (centroid) token
_FULL_MS1_RE = re.compile(
    r"(?:^|\s)Full(?:\s+[A-Za-z]+)*?\s+ms\s*\["
)  # 'Full [lock ...] ms [' -- not ms2/msx/SIM
_RANGE_RE = re.compile(r"\[([^\]]*)\]")
_PAIR_RE = re.compile(r"([0-9]+(?:\.[0-9]*)?)\s*-\s*([0-9]+(?:\.[0-9]*)?)")


def filter_info(filter_string):
    """Parse a Thermo filter string.  Returns dict(ftms, polarity, data_type, full_ms1, low, high):
    ftms      the analyzer token is FTMS (first token; an optional '{...}' group may follow it);
    polarity  the first standalone '+' / '-' token (None if absent);
    data_type 'p' / 'c' from the first standalone p/c token (None if absent; prefer IsCentroidScan from the reader);
    full_ms1  'Full' followed (after optional words such as 'lock') by the token 'ms' and the range bracket, so
              'Full ms2 ...@hcd...', 'Full msx', 'SIM ms' and 'Full ms3' are all False;
    low/high  min start and max end over the ranges inside the first [...] (several ranges allowed)."""
    s = filter_string or ""
    out = dict(
        ftms=bool(_FTMS_RE.search(s)),
        polarity=None,
        data_type=None,
        full_ms1=bool(_FULL_MS1_RE.search(s)),
        low=None,
        high=None,
    )
    mp = _POL_RE.search(s)
    if mp:
        out["polarity"] = mp.group(1)
    mt = _TYPE_RE.search(s)
    if mt:
        out["data_type"] = mt.group(1)
    mr = _RANGE_RE.search(s)
    if mr:
        pairs = [(float(a), float(b)) for a, b in _PAIR_RE.findall(mr.group(1))]
        if pairs:
            out["low"], out["high"] = min(p[0] for p in pairs), max(p[1] for p in pairs)
    return out


def parse_filter(filter_string):
    """('-' | '+', low, high) for an FTMS MS1 full scan, such as 'FTMS - p ESI Full lock ms [50.0000-750.0000]' or
    'FTMS {1,1}  + p NSI Full ms [133.0000-2000.0000]'.  Any other scan (MS2/MSn, SIM, msx, ion trap, or a string
    without polarity or range) returns (None, None, None): it belongs to no mode and is not used."""
    d = filter_info(filter_string)
    if not (d["ftms"] and d["full_ms1"]) or d["polarity"] is None or d["low"] is None:
        return None, None, None
    return d["polarity"], d["low"], d["high"]


def mode_id(polarity, resolution, lo, hi):
    return "%s|R%d|%.4f-%.4f" % (polarity, int(round(resolution)), lo, hi)


def measured_fft_step_hz(f_hz):
    """Mode of the positive point-to-point steps (log histogram, 0.001-decade bins), refined by the median
    inside +-0.2 % of the modal bin.  f_hz: law frequency of the profile points in Hz (any order)."""
    df = np.abs(np.diff(np.asarray(f_hz, float)))
    df = df[df > 0]
    if df.size < 20:
        return float("nan")
    lg = np.log10(df)
    edges = np.arange(lg.min(), lg.max() + 0.002, 0.001)
    if edges.size < 2:
        return float(np.median(df))
    h, e = np.histogram(lg, bins=edges)
    c = 10 ** (0.5 * (e[np.argmax(h)] + e[np.argmax(h) + 1]))
    sel = np.abs(df / c - 1) < 2e-3
    return float(np.median(df[sel])) if sel.any() else float(c)


def scan_chunks(mz, inten, B, C, low_mass, p=PARAMS):
    """Chunks of one profile scan.  Returns (fft_step_hz, rows) with rows = list of
    (m_apex, f_apex_khz, nreg, step_hz, imax, n_points)."""
    mz = np.asarray(mz, float)
    inten = np.asarray(inten, float)
    order = np.argsort(mz)
    mz, inten = mz[order], inten[order]
    f = law_khz(mz, B, C) * 1e3  # Hz, descending in mz order
    df = -np.diff(f)  # > 0
    delta = measured_fft_step_hz(f)
    rows = []
    if not np.isfinite(delta):
        return delta, rows
    brk = np.where(df > p["chunk_break_x"] * delta)[0]
    starts = np.r_[0, brk + 1]
    ends = np.r_[brk + 1, len(f)]
    for a, b in zip(starts, ends):
        if b - a < p["min_chunk_points"] or mz[a] < low_mass + p["low_mass_margin"]:
            continue
        dd = df[a : b - 1]
        cand = np.abs(dd / delta - 1) < p["step_window_rel"]
        if cand.sum() < p["min_candidates"]:
            continue
        st0 = np.median(dd[cand])
        reg = np.abs(dd - st0) < p["reg_rel_tol"] * st0
        nreg = int(reg.sum())
        if nreg < p["nreg_min"]:
            continue
        j = a + int(np.argmax(inten[a:b]))
        rows.append(
            (
                float(mz[j]),
                float(f[j] / 1e3),
                nreg,
                float(np.median(dd[reg])),
                float(inten[a:b].max()),
                int(b - a),
            )
        )
    return delta, rows


def weak_mask(imax, p=PARAMS):
    imax = np.asarray(imax, float)
    if p["weak_rule"] == "none":
        return np.ones(imax.size, bool)
    if p["weak_rule"] == "relative":
        return imax <= np.median(imax) if imax.size else imax.astype(bool)
    return imax < p["weak_abs_counts"]


def nominal_fft_step_hz(delta_hz):
    """The FFT bin width the stored points sit on: the power of two (in kHz) nearest to the measured step
    (2^-12 kHz = 0.244140625 Hz at R 280k, 2^-10 at 70k, 2^-9 at 35k on the Q Exactive tested).  If the measured
    step is not within 1e-3 of a power of two, the measured step itself (flag fft_step_not_power_of_two)."""
    if not np.isfinite(delta_hz) or delta_hz <= 0:
        return float("nan"), False
    nom = 1000.0 * 2.0 ** round(math.log2(delta_hz / 1000.0))
    if abs(delta_hz / nom - 1) < 1e-3:
        return nom, True
    return float(delta_hz), False


def scan_s(rows, delta_hz, p=PARAMS):
    """Apply the weak-chunk rule and express each chunk's regular step as s = (step / nominal step - 1) * 1e6.
    Returns dict of arrays (m, f_khz, s, imax, nreg) or None."""
    if not rows:
        return None
    nom, pow2 = nominal_fft_step_hz(delta_hz)
    if not np.isfinite(nom):
        return None
    r = np.asarray(rows, float)
    m, fk, nreg, step, imax = r[:, 0], r[:, 1], r[:, 2], r[:, 3], r[:, 4]
    keep = weak_mask(imax, p)
    m, fk, nreg, step, imax = m[keep], fk[keep], nreg[keep], step[keep], imax[keep]
    if m.size == 0:
        return None
    return dict(
        m=m,
        f_khz=fk,
        s=(step / nom - 1.0) * 1e6,
        imax=imax,
        nreg=nreg,
        nominal_step_hz=nom,
        nominal_is_power_of_two=pow2,
    )


# ------------------------------------------------------------------------------------------------ #
# boundaries and grid
# ------------------------------------------------------------------------------------------------ #
def level_runs(f_khz, s_ppm, p=PARAMS):
    """The step function s(f) as level runs [(f_lo, f_hi, s_ppm, n_chunks)], sorted by f descending.
    1. cluster the chunk s values: sorted values chained while consecutive values differ by < level_tol_ppm;
    2. split each cluster where its chunks leave a frequency gap > run_split_khz (one value in two places);
    3. keep pieces with >= run_min_chunks chunks; accept them greedily by chunk count, skipping a piece whose
       [f_lo, f_hi] overlaps an accepted run (an outlier value inside a populated segment).
    The level is the median of the run's chunks.  On the Q Exactive tested every segment carries one exact value,
    so runs are segments (or several adjacent segments that happen to share one value)."""
    f = np.asarray(f_khz, float)
    s = np.asarray(s_ppm, float)
    if f.size == 0:
        return []
    o = np.argsort(s, kind="mergesort")
    fs, ss = f[o], s[o]
    cut = np.where(np.diff(ss) >= p["level_tol_ppm"])[0]
    pieces = []
    for a, b in zip(np.r_[0, cut + 1], np.r_[cut + 1, ss.size]):
        ff, sv = fs[a:b], ss[a:b]
        oo = np.argsort(ff)
        ff, sv = ff[oo], sv[oo]
        gap = np.where(np.diff(ff) > p["run_split_khz"])[0]
        for c, d in zip(np.r_[0, gap + 1], np.r_[gap + 1, ff.size]):
            if d - c >= p["run_min_chunks"]:
                pieces.append(
                    (
                        float(ff[c]),
                        float(ff[d - 1]),
                        float(np.median(sv[c:d])),
                        int(d - c),
                    )
                )
    pieces.sort(key=lambda x: (-x[3], -x[1]))
    runs = []
    for pc in pieces:
        if all(pc[1] < r[0] or pc[0] > r[1] for r in runs):
            runs.append(pc)
    runs.sort(key=lambda x: -x[1])
    return runs


def brackets(f_khz, s_ppm, p=PARAMS, runs=None):
    """Level-change brackets between consecutive level runs (sorted by f) whose levels differ:
    [(f_lo, f_hi, step_ppm)] with f_lo = the lower run's highest chunk, f_hi = the upper run's lowest chunk,
    step = s(lower f side) - s(higher f side).  A boundary between two segments with the same s is invisible
    and harmless (it changes nothing)."""
    if runs is None:
        runs = level_runs(f_khz, s_ppm, p)
    out = []
    for up, low in zip(runs[:-1], runs[1:]):
        if abs(low[2] - up[2]) >= p["level_tol_ppm"]:
            out.append((low[1], up[0], low[2] - up[2]))
    return out


def _best_grid(f_lo, f_hi, periods, phase_step, margin):
    """Exhaustive search: the (period, phase) that puts a grid edge inside the most brackets."""
    f_lo = np.asarray(f_lo, float)
    f_hi = np.asarray(f_hi, float)
    mid = 0.5 * (f_lo + f_hi)
    best = (-1, None, None)
    for P in periods:
        ph = np.arange(0.0, P, phase_step)[:, None]
        k = np.round((mid[None, :] - ph) / P)
        fk = ph + k * P
        hits = ((fk >= f_lo[None, :] - margin) & (fk <= f_hi[None, :] + margin)).sum(
            axis=1
        )
        j = int(np.argmax(hits))
        if hits[j] > best[0]:
            best = (int(hits[j]), float(P), float(ph[j, 0]))
    return best


def _feasible_center(f_lo, f_hi, k, P, ph, p=PARAMS):
    """Refine a grid that puts an edge inside every given bracket.  The grids (P', phase') doing so form a convex
    set; sample P' within +-0.2 % of P (step 5e-4 kHz), take for each P' the interval of edge positions at the
    middle hit index k_ref allowed by all brackets, and return the length-weighted centre (period, phase) and the
    edge uncertainty = the largest half-range of any edge f_k over the set, for k in the hit range."""
    m = p["grid_hit_margin_khz"]
    k = np.asarray(k, float)
    k_ref = float(np.round(np.median(k)))
    pts = []
    for Pp in np.arange(P * 0.998, P * 1.002, 5e-4):
        lo = np.max(f_lo - m - (k - k_ref) * Pp)
        hi = np.min(f_hi + m - (k - k_ref) * Pp)
        if hi >= lo:
            pts.append((Pp, lo, hi))
    if not pts:
        return P, ph, None
    a = np.asarray(pts)
    w = np.maximum(a[:, 2] - a[:, 1], 1e-9)
    Pc = float(np.sum(a[:, 0] * w) / w.sum())
    fref = float(np.sum(0.5 * (a[:, 1] + a[:, 2]) * w) / w.sum())
    phase = (fref - k_ref * Pc) % Pc
    ks = np.array([k.min(), k_ref, k.max()])
    lo_e = np.min(a[:, 1][:, None] + (ks[None, :] - k_ref) * a[:, 0][:, None], axis=0)
    hi_e = np.max(a[:, 2][:, None] + (ks[None, :] - k_ref) * a[:, 0][:, None], axis=0)
    return Pc, float(phase), float(np.max(hi_e - lo_e) / 2)


def fit_grid(brk, fft_step_hz, p=PARAMS):
    """Measured grid (period, phase) in law-kHz with a random-placement significance test.
    Two period hypotheses: fixed frequency (seed period) and fixed bin count (seed scaled by the FFT step)."""
    res = dict(
        source="seed",
        period_khz=p["grid_seed_period_khz"],
        phase_khz=p["grid_default_phase_khz"],
        period_hypothesis=None,
        n_brackets=len(brk),
        n_hits=0,
        hit_fraction=None,
        random_hit_fraction_q95=None,
        edge_uncertainty_khz=None,
        k_hit_range=None,
        significant=False,
    )
    if len(brk) < p["grid_min_hits"]:
        res["reason"] = "too_few_brackets"
        return res
    b = np.asarray(brk, float)
    b = b[(b[:, 1] - b[:, 0]) <= p["bracket_fit_max_khz"]]
    if len(b) < p["grid_min_hits"]:
        res["reason"] = "too_few_brackets"
        return res
    res["n_brackets"] = int(len(b))
    f_lo, f_hi = b[:, 0], b[:, 1]
    mid = 0.5 * (f_lo + f_hi)
    seeds = {
        "fixed_frequency": p["grid_seed_period_khz"],
        "fixed_bins": p["grid_seed_period_khz"]
        * fft_step_hz
        / p["grid_seed_fft_step_hz"],
    }
    cand = []
    for hyp, P0 in seeds.items():
        lo, hi = (
            P0 * (1 - p["grid_period_search_rel"]),
            P0 * (1 + p["grid_period_search_rel"]),
        )
        periods = np.arange(lo, hi, p["grid_period_step_khz"])
        h, P, ph = _best_grid(
            f_lo, f_hi, periods, p["grid_phase_step_khz"], p["grid_hit_margin_khz"]
        )
        cand.append((h, hyp, P, ph, lo, hi))
    cand.sort(key=lambda c: (-c[0], c[1] != "fixed_frequency"))
    h, hyp, P, ph, lo, hi = cand[0]
    k = np.round((mid - ph) / P)
    fk = ph + k * P
    hit = (fk >= f_lo - p["grid_hit_margin_khz"]) & (
        fk <= f_hi + p["grid_hit_margin_khz"]
    )
    P_fit, f0 = P, ph
    if hit.sum() >= 2 and np.unique(k[hit]).size >= 2:
        P_fit, f0, unc = _feasible_center(f_lo[hit], f_hi[hit], k[hit], P, ph, p)
        res["edge_uncertainty_khz"] = unc
        res["k_hit_range"] = [int(k[hit].min()), int(k[hit].max())]
    rng = np.random.default_rng(p["grid_random_seed"])
    w = f_hi - f_lo
    span = (mid.min(), mid.max())
    rnd = []
    periods_r = np.arange(lo, hi, p["grid_random_period_step_khz"])
    for _ in range(p["grid_random_draws"]):
        c = rng.uniform(span[0], span[1], len(w))
        hr, _, _ = _best_grid(
            c - w / 2,
            c + w / 2,
            periods_r,
            p["grid_random_phase_step_khz"],
            p["grid_hit_margin_khz"],
        )
        rnd.append(hr / len(w))
    q95 = float(np.quantile(rnd, 0.95))
    frac = float(hit.sum() / len(w))
    res.update(
        period_hypothesis=hyp,
        n_hits=int(hit.sum()),
        hit_fraction=frac,
        random_hit_fraction_q95=q95,
    )
    if hit.sum() >= p["grid_min_hits"] and frac > q95:
        res.update(
            source="measured",
            significant=True,
            period_khz=P_fit,
            phase_khz=float(f0 % P_fit),
        )
    else:
        res["reason"] = "not_significant"
    return res


# ------------------------------------------------------------------------------------------------ #
# segment table
# ------------------------------------------------------------------------------------------------ #
def modal_level(v, tol):
    """Largest group of values chained at < tol (sorted); ties -> the group with the lower value.
    Returns (median of the group, group size)."""
    v = np.sort(np.asarray(v, float))
    if v.size == 0:
        return None, 0
    cut = np.where(np.diff(v) >= tol)[0]
    best = None
    for a, b in zip(np.r_[0, cut + 1], np.r_[cut + 1, v.size]):
        if best is None or b - a > best[1] - best[0]:
            best = (a, b)
    a, b = best
    return float(np.median(v[a:b])), int(b - a)


SEGMENT_COLUMNS = [
    "k",
    "f_lo_khz",
    "f_hi_khz",
    "mz_lo",
    "mz_hi",
    "s_ppm",
    "n_chunks",
    "n_level",
    "level_share",
    "n_scans",
    "scan_sd_ppm",
    "interp",
]


def segment_table(per_scan, period, phase, B, C, p=PARAMS):
    """Grid-segment table.  per_scan: list of dicts from scan_s, each with key 'scan'.
    s_ppm = the modal level of the segment's chunks (they carry one exact value on the data tested);
    n_level = chunks at that level; level_share = n_level / n_chunks; scan_sd_ppm = sd over scans of the
    per-scan modal levels.  A segment with n_level < min_chunks_per_segment is not measured; interior gaps are
    interpolated linearly in k (interp = true); the ends are not extrapolated.
    Returns (rows as lists in SEGMENT_COLUMNS order, k_range_measured or None)."""
    tol = p["level_tol_ppm"]
    ks, ss, sc = [], [], []
    for d in per_scan:
        k = np.floor((d["f_khz"] - phase) / period).astype(int)
        ks.append(k)
        ss.append(d["s"])
        sc.append(np.full(k.size, d["scan"]))
    if not ks:
        return [], None
    k = np.concatenate(ks)
    s = np.concatenate(ss)
    sc = np.concatenate(sc)
    out = {}
    for kk in np.unique(k):
        sel = k == kk
        lev, nl = modal_level(s[sel], tol)
        per = []
        for x in np.unique(sc[sel]):
            lv, n_ = modal_level(s[sel & (sc == x)], tol)
            if n_ >= 2:
                per.append(lv)
        sd = float(np.std(per, ddof=1)) if len(per) >= 2 else None
        n = int(sel.sum())
        out[int(kk)] = [
            lev if nl >= p["min_chunks_per_segment"] else None,
            n,
            nl,
            nl / n,
            len(per),
            sd,
        ]
    meas = sorted(kk for kk, v in out.items() if v[0] is not None)
    if not meas:
        return [], None
    k0, k1 = meas[0], meas[-1]
    rows = []
    for kk in range(k0, k1 + 1):
        lev, n, nl, share, nsc, sd = out.get(kk, [None, 0, 0, 0.0, 0, None])
        interp = False
        if lev is None:
            lo = max(x for x in meas if x < kk)
            hi = min(x for x in meas if x > kk)
            vlo, vhi = out[lo][0], out[hi][0]
            lev = vlo + (vhi - vlo) * (kk - lo) / (hi - lo)
            interp = True
        f_lo, f_hi = phase + period * kk, phase + period * (kk + 1)
        rows.append(
            [
                kk,
                round(f_lo, 4),
                round(f_hi, 4),
                round(float(mz_of_f(f_hi, B, C)), 4),
                round(float(mz_of_f(f_lo, B, C)), 4),
                round(lev, 4),
                n,
                nl,
                round(share, 3),
                nsc,
                None if sd is None else round(sd, 4),
                interp,
            ]
        )
    return rows, [k0, k1]


def s_at_mz(rows, m, B, C, period, phase):
    """Table value at m/z m (None outside the measured range)."""
    k = int(math.floor((float(law_khz(m, B, C)) - phase) / period))
    for r in rows:
        if r[0] == k:
            return r[5], k
    return None, k


# ------------------------------------------------------------------------------------------------ #
# lock used per scan
# ------------------------------------------------------------------------------------------------ #
def lock_used_from_flags(
    configured, centroid_mz, centroid_flags, tol_ppm=PARAMS["lock_match_ppm"]
):
    """Primary rule: the configured locks within tol_ppm of a centroid whose Flags contain 'Reference'.
    Returns a label: one lock as '%.4f', 'both' (two found), or 'none'."""
    ref = [
        float(m) for m, fl in zip(centroid_mz, centroid_flags) if "Reference" in str(fl)
    ]
    used = [
        L
        for L in configured
        if L > 0 and any(abs(r / L - 1) * 1e6 < tol_ppm for r in ref)
    ]
    return _label(used)


def lock_used_from_absence(
    configured, n_lm_found, centroid_mz_no_reference, tol_ppm=PARAMS["lock_match_ppm"]
):
    """Fallback (reader returns centroids WITHOUT reference peaks): the configured lock missing from the list.
    'Number of LM Found' 0 -> 'none'; 2 -> 'both'; 1 -> the single absent lock; otherwise 'ambiguous'."""
    locks = [float(L) for L in configured if L and L > 0]
    if n_lm_found is not None and int(n_lm_found) == 0:
        return "none"
    if n_lm_found is not None and int(n_lm_found) >= 2:
        return "both"
    cm = np.sort(np.asarray(centroid_mz_no_reference, float))
    absent = []
    for L in locks:
        i = np.searchsorted(cm, L)
        near = [cm[j] for j in (i - 1, i) if 0 <= j < cm.size]
        if not any(abs(x / L - 1) * 1e6 < tol_ppm for x in near):
            absent.append(L)
    if len(locks) == 1:
        return _label(locks) if absent else "ambiguous"
    if len(absent) == 1:
        return _label(absent)
    return "ambiguous"


def _label(used):
    if not used:
        return "none"
    if len(used) >= 2:
        return "both"
    return "%.4f" % used[0]


def lock_summary(labels, configured, B, C, s_rows, period, phase):
    """Shares per label, the share-weighted effective lock m/z and s at the lock (over scans with a single lock,
    'both' counted half to each).  s_rows may be empty (census only)."""
    labels = list(labels)
    n = len(labels)
    shares = {}
    for lab in labels:
        shares[lab] = shares.get(lab, 0) + 1
    shares = {k: round(v / n, 6) for k, v in sorted(shares.items())} if n else {}
    counts = {k: int(round(v * n)) for k, v in shares.items()}
    w = {}
    locks = [float(L) for L in configured if L and L > 0]
    for lab in labels:
        if lab == "both":
            for L in locks[:2]:
                w[L] = w.get(L, 0) + 0.5
        elif lab not in ("none", "ambiguous"):
            w[float(lab)] = w.get(float(lab), 0) + 1.0
    tot = sum(w.values())
    if tot == 0:
        return dict(
            used_share=shares,
            used_count=counts,
            m_lock_eff=None,
            s_lock_eff_ppm=None,
            k_lock={},
        )
    m_eff = sum(L * v for L, v in w.items()) / tot
    k_lock, s_eff, ok = {}, 0.0, bool(s_rows)
    for L, v in w.items():
        val, k = s_at_mz(s_rows, L, B, C, period, phase) if s_rows else (None, None)
        k_lock["%.4f" % L] = k
        if val is None:
            ok = False
        else:
            s_eff += val * v / tot
    return dict(
        used_share=shares,
        used_count=counts,
        m_lock_eff=round(m_eff, 5),
        s_lock_eff_ppm=round(s_eff, 4) if ok else None,
        k_lock=k_lock,
    )


# ------------------------------------------------------------------------------------------------ #
# assembly
# ------------------------------------------------------------------------------------------------ #
def pick_scans(census, first_scan, n=PARAMS["n_scans"]):
    """Up to n PROFILE scans evenly spaced over the mode's profile scans (endpoints included, in scan order),
    skipping the file's first scan and, when others exist, scans with 'Number of LM Found' == 0.
    census: list of dicts with 'scan', 'n_lm_found' and optionally 'centroid' (True for a centroid scan, from
    IsCentroidScan).  Centroid scans are never picked; a mode with only centroid scans returns [] (-> centroid_only).
    Index i of the pick is cand[round(i * (N - 1) / (n - 1))] with Python's round (half to even)."""
    prof = [c for c in census if not c.get("centroid")]
    cand = [
        c["scan"]
        for c in prof
        if c["scan"] != first_scan and (c.get("n_lm_found") or 0) > 0
    ]
    if not cand:
        cand = [c["scan"] for c in prof if c["scan"] != first_scan] or [
            c["scan"] for c in prof
        ]
    if len(cand) <= n:
        return cand
    step = (len(cand) - 1) / (n - 1)
    return [cand[int(round(i * step))] for i in range(n)]


def minimal_document(status, error=None):
    """The whole document for status 'unavailable' (this server's reader cannot return corrected profile m/z, e.g.
    OpenTFRaw older than 1.4.1), 'missing' (served only: not computed yet) or 'error' (the computation raised;
    error = '<type>: <message>').  Nothing else is stored.  A file without profile data is not one of these: it gets
    the full document with status 'no_fingerprint' and its modes flagged 'centroid_only'."""
    if status not in ("unavailable", "missing", "error"):
        raise ValueError(status)
    doc = dict(schema=SCHEMA, version=VERSION, status=status)
    if status == "error":
        doc["error"] = str(error or "unknown error")
    return doc


def fingerprint_mode(
    mode_key, census, profiles, configured_locks, lock_method, p=PARAMS
):
    """One mode's fingerprint.
    mode_key: (polarity, resolution, lo, hi);
    census: list of dicts for EVERY scan of the mode: scan, rt_min, B, C, n_lm_found, lm_correction_ppm,
            microscans, lock_used (label from lock_used_from_flags / _from_absence);
    profiles: {scan: (mz array, intensity array)} for the picked scans (corrected profile m/z: Thermo's reader with
              PARAMS, or OpenTFRaw >= 1.4.1 profile() with PARAMS_OPENTFRAW);
    configured_locks: the trailer 'Lock Mass #1..3 (m/z)' values (> 0);
    lock_method: 'reference_flag' or 'absence_in_centroids'."""
    pol, res, lo, hi = mode_key
    flags = []
    B = np.array([c["B"] for c in census], float)
    C = np.array([c["C"] for c in census], float)
    q = C / B**2
    Bm, Cm = float(np.median(B)), float(np.median(C))
    if q.max() - q.min() > 1e-12:
        flags.append("calibration_changes_within_mode")
    us = {}
    for c in census:
        us[str(int(c["microscans"]))] = us.get(str(int(c["microscans"])), 0) + 1
    by_scan = {c["scan"]: c for c in census}
    per, deltas, checks, nchunks = [], [], [], 0
    for s in sorted(profiles):
        mz, inten = profiles[s]
        c = by_scan[s]
        delta, rows = scan_chunks(mz, inten, c["B"], c["C"], lo, p)
        mz_arr = np.asarray(mz, float)
        checks.append(
            [
                int(s),
                int(mz_arr.size),
                round(float(mz_arr.min()), 6) if mz_arr.size else None,
                round(float(mz_arr.max()), 6) if mz_arr.size else None,
                round(float(mz_arr.sum()), 3),
            ]
        )
        d = scan_s(rows, delta, p)
        deltas.append(delta)
        if d is None:
            continue
        d["scan"] = int(s)
        per.append(d)
        nchunks += int(d["s"].size)
    nominal = sorted({round(d["nominal_step_hz"], 12) for d in per})
    if per and not all(d["nominal_is_power_of_two"] for d in per):
        flags.append("fft_step_not_power_of_two")
    out = dict(
        mode_id=mode_id(pol, res, lo, hi),
        polarity=pol,
        resolution=int(res),
        scan_range=[lo, hi],
        n_scans=len(census),
        microscans=us,
        calibration=dict(
            B_median=round(Bm, 4),
            C_median=round(Cm, 4),
            C_over_B2=float("%.6e" % float(np.median(q))),
            C_over_B2_min=float("%.6e" % q.min()),
            C_over_B2_max=float("%.6e" % q.max()),
        ),
        profile=dict(
            scans_used=[int(s) for s in sorted(profiles)],
            fft_step_hz_measured=[
                None if not np.isfinite(x) else round(float(x), 7) for x in deltas
            ],
            nominal_fft_step_hz=nominal[0] if len(nominal) == 1 else nominal,
            n_chunks=nchunks,
            scan_check_columns=["scan", "n_points", "mz_min", "mz_max", "sum_mz"],
            scan_check=checks,
        ),
    )
    if not per:
        flags.append("no_profile_chunks" if profiles else "centroid_only")
        lk = lock_summary(
            [c["lock_used"] for c in census], configured_locks, Bm, Cm, [], 1.0, 0.0
        )
        lk.update(configured_mz=configured_locks, method=lock_method)
        out.update(
            lock=lk,
            levels=None,
            brackets=None,
            grid=None,
            segments=None,
            k_range_measured=None,
            flags=sorted(set(flags)),
        )
        return out
    f_all = np.concatenate([d["f_khz"] for d in per])
    s_all = np.concatenate([d["s"] for d in per])
    runs = level_runs(f_all, s_all, p)
    brk = brackets(f_all, s_all, p, runs=runs)
    grid = fit_grid(brk, float(per[0]["nominal_step_hz"]), p)
    if not grid["significant"]:
        flags.append("grid_unmeasured")
    elif (
        grid["n_hits"] < p["grid_weak_min_hits"]
        or grid["k_hit_range"] is None
        or grid["k_hit_range"][1] - grid["k_hit_range"][0] < p["grid_weak_min_k_span"]
    ):
        flags.append("grid_weak")
    P, F0 = grid["period_khz"], grid["phase_khz"]
    rows, krange = segment_table(per, P, F0, Bm, Cm, p)
    if not rows:
        flags.append(
            "no_segment_table"
        )  # chunks exist, but no segment reaches min_chunks_per_segment
    if rows:
        ninterp = sum(1 for r in rows if r[-1])
        if ninterp > 0.2 * len(rows):
            flags.append("sparse_segments")
        shares = [r[8] for r in rows if not r[-1]]
        if shares and float(np.median(shares)) < 0.5:
            flags.append("not_stepwise")
    lk = lock_summary(
        [c["lock_used"] for c in census], configured_locks, Bm, Cm, rows, P, F0
    )
    lk.update(configured_mz=configured_locks, method=lock_method)
    singles = {
        k: v
        for k, v in lk["used_share"].items()
        if k not in ("none", "both", "ambiguous")
    }
    if len(singles) >= 2 and sorted(singles.values())[-2] > 0.1:
        flags.append("lock_alternates")
    if lk["used_share"].get("none", 0) > 0.05:
        flags.append("lock_missing")
    if lk["used_share"].get("ambiguous", 0) > 0:
        flags.append("lock_ambiguous")
    lmc = {}
    for c in census:
        lmc.setdefault(c["lock_used"], []).append(c["lm_correction_ppm"])
    lk["lm_correction_ppm_median_by_lock"] = {
        k: round(float(np.median(v)), 3) for k, v in sorted(lmc.items())
    }
    # period and phase keep 7 decimals: segment edges f_k = phase + k * period at k ~ 70 must agree with the
    # segment rows (computed with the unrounded values) to < 1e-4 kHz
    grid_out = {
        k: (
            round(v, 7 if k in ("period_khz", "phase_khz") else 4)
            if isinstance(v, float)
            else v
        )
        for k, v in grid.items()
    }
    out.update(
        lock=lk,
        levels=dict(
            columns=["f_lo_khz", "f_hi_khz", "s_ppm", "n_chunks"],
            rows=[[round(a, 4), round(b, 4), round(c, 4), n] for a, b, c, n in runs],
        ),
        brackets=dict(
            columns=["f_lo_khz", "f_hi_khz", "step_ppm"],
            rows=[[round(a, 4), round(b, 4), round(c, 4)] for a, b, c in brk],
        ),
        grid=grid_out,
        segments=dict(columns=SEGMENT_COLUMNS, rows=rows) if rows else None,
        k_range_measured=krange,
        flags=sorted(set(flags)),
    )
    return out
