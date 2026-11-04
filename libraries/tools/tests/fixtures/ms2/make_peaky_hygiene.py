"""Write peaky_hygiene.json: what peaky's peak hygiene makes of its public MS2
fixtures.

Not a test, and not run by one: the generator of the golden that
``test_ms2_hygiene.py`` reads, kept so the golden can be remade. It needs a
peaky checkout (https://github.com/ultra-trace-systems/peaky; the golden was
made at main aa119ac, as ``peaky_readings.json``) whose dependencies are
installed, which this repository's environment provides:

    uv run python libraries/tools/tests/fixtures/ms2/make_peaky_hygiene.py \
        <peaky checkout> libraries/tools/tests/fixtures/ms2/peaky_hygiene.json

The windows are those of ``make_peaky_readings.py`` (peaky's three public
fixtures, read against the window ions peaky's own tests name) plus the
exploratory file's 179.1069 window in the server's S/N-3 spectra
(``ms2_nh4_exploratory_179_sn3.json``, read against the two ions
``tests/test_ms2_replicates.py`` names), the one fixture that holds harmonic
ghosts. ``peaky ms2 analyze`` reads each window, harmonic rule on; the golden
holds every spectrum's peaks as ``read_window`` saw them (m/z, intensity, S/N,
resolution), each peak's role (``attribute.peak_roles`` over the readings: a
ghost is 'harmonic'), peaky's note on each ghost, and the peaks of the
fragments ``attribute.satellite_fragments`` sets aside as side lobes.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np


peaky_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])
sys.path.insert(0, str(peaky_root))
from peaky.ms2 import analyze as AN  # noqa: E402
from peaky.ms2 import attribute as AT  # noqa: E402
from peaky.ms2 import sources as S  # noqa: E402


FIX = peaky_root / "tests" / "fixtures" / "ms2"
NAN = float("nan")
#: the window ions peaky's tests name (test_ms2_breakdown.py's MT_IONS and
#: NH4_IONS, test_ms2_replicates.py's 179 window)
MT_IONS = ("C10H17+", "C10H20^N+", "C10H19O+")
NH4_IONS = (
    "C7H6NS+",
    "C4H10NS2+",
    "C8H10NO+",
    "C4H10^NO4+",
    "C8H9O2+",
    "C5H14^NO3+",
    "C6H18^NO2+",
    "C9H14^N+",
    "C11H13O+",
    "C11H15O2+",
    "C11H16^NO+",
    "C11H18^NO2+",
    "C12H21O2+",
    "C12H22^NO2+",
    "C8H28^NO4Si4+",
)
X179_IONS = ("C11H15O2+", "C11H16^NO+")


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _tables(stem):
    """What `ms2 extract` builds for a fixture (peaky's tests' `_tables`)."""
    cent = _load(f"{stem}_centroids.json")
    p = FIX / f"{stem}_summary.json"
    summ = _load(p.name) if p.exists() else None
    spectra = S.spectra_table("S", cent, summ)
    parents = S.parents_table("S", cent, summ)
    width = (summ or {}).get("isolation_width")
    window = (
        S.window_table(
            "S", _load(f"{stem}_ms1.json"), sorted(set(parents["parent_mz"])), width
        )
        if width
        else S._frame({}, S.WINDOW_COLUMNS)
    )
    return AN.RunTables(
        spectra=spectra,
        parents=parents,
        window=window,
        manifest={"sample_id": "S", "parameters": {"isolation_width_used": width}},
    )


def _x179():
    """The exploratory file's 179.1069 window in the S/N-3 spectra (peaky's
    tests/test_ms2_replicates.py `_x179`)."""
    cen = _load("ms2_nh4_exploratory_179_sn3.json")
    summ = _load("ms2_nh4_exploratory_summary.json")
    summ = {
        **summ,
        "parent_peaks": [179.1069],
        "groups": [
            g for g in summ["groups"] if np.isclose(g["parent_peak_mz"], 179.1069)
        ],
    }
    par = S.parents_table("S", cen, summ)
    win = S.window_table(
        "S",
        _load("ms2_nh4_exploratory_ms1.json"),
        [179.1069],
        summ["isolation_width"],
    )
    return AN.RunTables(
        spectra=S.spectra_table("S", cen, summ),
        parents=par,
        window=win,
        manifest={
            "sample_id": "S",
            "parameters": {"isolation_width_used": summ["isolation_width"]},
        },
    )


def _ions(*formulas):
    return AN.collect_ions((f, NAN, AN.ion_mz(f)) for f in formulas)


def _num(x):
    return None if isinstance(x, float) and math.isnan(x) else x


out = {"windows": []}
for stem, t, ions in (
    ("ms2_nh4_exploratory", _tables("ms2_nh4_exploratory"), NH4_IONS),
    ("ms2_monoterpene_a", _tables("ms2_monoterpene_a"), MT_IONS),
    ("ms2_monoterpene_b", _tables("ms2_monoterpene_b"), ("C10H17+",)),
    ("ms2_nh4_exploratory_179_sn3", _x179(), X179_IONS),
):
    res = AN.analyze_run(t, _ions(*ions))
    for w in res.windows:
        if w.readings is None or not len(w.readings):
            continue
        order = [w.target] + list(w.co_isolates)
        par = AN._window_parents(t, w.parent_mz)[0]
        spec_rows, resolution = AN._spectrum_rows(t, par, w.parent_mz)
        assert [m["spectrum"] for m, _ in spec_rows] == list(
            dict.fromkeys(w.readings["spectrum"])
        ), (stem, w.parent_mz)
        roles = AT.peak_roles(w.readings, order)
        # the side lobes analyze_window set aside, by peak: the fragments of
        # its energy-resolved spectra, merged across them
        resolved = w.readings[w.readings["spectrum"].isin(set(w.roles["spectrum"]))]
        merged = AT.merge_fragment_peaks(
            AT.peak_roles(resolved, order), tol_ppm=5.0, tol_mda=0.3
        )
        lobes = AT.satellite_fragments(merged, resolution)
        assert sorted(lobes.values()) == sorted(
            n[len("side lobe: ") :] for n in w.notes if n.startswith("side lobe: ")
        )
        lobe_peaks = {
            (r.spectrum, int(r.peak))
            for r in merged.itertuples()
            if r.role == "fragment" and r.key in lobes
        }
        spectra = []
        for meta, peaks in spec_rows:
            key = meta["spectrum"]
            sp = w.readings[w.readings["spectrum"] == key].drop_duplicates("peak")
            assert list(sp["peak"]) == list(range(len(peaks)))
            assert np.array_equal(sp["mz"].to_numpy(), peaks["mz"].to_numpy())
            rl = roles[roles["spectrum"] == key].sort_values("peak")
            assert list(rl["peak"]) == list(range(len(peaks)))
            spectra.append(
                {
                    "spectrum": key,
                    "mz": [float(x) for x in peaks["mz"]],
                    "intensity": [float(x) for x in peaks["intensity"]],
                    "signal_to_noise": [
                        _num(float(x)) for x in peaks["signal_to_noise"]
                    ],
                    "resolution": [
                        _num(resolution.get((key, i), NAN)) for i in range(len(peaks))
                    ],
                    "roles": list(rl["role"]),
                    "harmonic": {
                        str(int(r.peak)): r.harmonic
                        for r in sp.itertuples()
                        if r.harmonic
                    },
                    "satellite": sorted(p for s, p in lobe_peaks if s == key),
                }
            )
        out["windows"].append(
            {
                "fixture": stem,
                "parent_mz": float(w.parent_mz),
                "window_ions": order,
                "offset_ppm": float(w.offset_ppm),
                "spectra": spectra,
            }
        )
out_path.write_text(json.dumps(out, separators=(",", ":")) + "\n")
