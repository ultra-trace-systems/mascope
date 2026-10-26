"""Write peaky_readings.json: peaky's own readings of its public MS2 fixtures.

Not a test, and not run by one: the generator of the golden that
``test_ms2_readings.py`` reads, kept so the golden can be remade. It needs a
peaky checkout (https://github.com/ultra-trace-systems/peaky; the golden was
made at main aa119ac) whose dependencies are installed, which this repository's
environment provides:

    uv run python libraries/tools/tests/fixtures/ms2/make_peaky_readings.py \
        <peaky checkout> libraries/tools/tests/fixtures/ms2/peaky_readings.json

For each window of peaky's three public fixtures (``tests/fixtures/ms2``: the
exploratory 15N-ammonium file and the two monoterpene standards, read against
the window ions peaky's own tests name), ``peaky ms2 analyze``'s windowing picks
the target and the co-isolates and measures the window's offset; each spectrum
is then read by peaky's ``assign_fragments`` at k_max 3, which is also what the
ladder gate at k_max 4 reads on these files (asserted here). The golden holds
the inputs (window ions, offset, every spectrum's peaks) and peaky's best
readings, in peaky's notation.
"""

import json
import math
import sys
from pathlib import Path


peaky_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])
sys.path.insert(0, str(peaky_root))
from peaky.ms2 import analyze as AN  # noqa: E402
from peaky.ms2 import sources as S  # noqa: E402
from peaky.ms2 import subformula as SF  # noqa: E402


FIX = peaky_root / "tests" / "fixtures" / "ms2"
NAN = float("nan")
#: the window ions peaky's tests/test_ms2_breakdown.py names (MT_IONS, NH4_IONS)
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


def _ions(*formulas):
    return AN.collect_ions((f, NAN, AN.ion_mz(f)) for f in formulas)


def _num(x):
    return None if isinstance(x, float) and math.isnan(x) else x


out = {"windows": []}
for stem, ions in (
    ("ms2_nh4_exploratory", NH4_IONS),
    ("ms2_monoterpene_a", MT_IONS),
    ("ms2_monoterpene_b", ("C10H17+",)),
):
    t, ii = _tables(stem), _ions(*ions)
    strict = AN.analyze_run(t, ii, ladder=False, harmonics=False)
    gated = AN.analyze_run(t, ii, harmonics=False)
    for w, g in zip(strict.windows, gated.windows):
        if w.readings is None or not len(w.readings):
            continue
        assert g.readings.equals(w.readings), (stem, w.parent_mz)
        order = [w.target] + list(w.co_isolates)
        spectra = []
        for key, sp in w.readings.groupby("spectrum", sort=False):
            peaks = sp.drop_duplicates("peak").sort_values("peak")
            assert list(peaks["peak"]) == list(range(len(peaks)))
            again = SF.assign_fragments(
                peaks[["mz", "intensity"]].reset_index(drop=True),
                {f: f for f in order},
                offset_ppm=w.offset_ppm,
            )
            cols = list(SF.OUTPUT_COLUMNS)
            assert (
                again[cols]
                .reset_index(drop=True)
                .equals(sp[cols].reset_index(drop=True))
            )
            best = SF.best_readings(again)
            spectra.append(
                {
                    "spectrum": key,
                    "mz": [float(x) for x in peaks["mz"]],
                    "intensity": [float(x) for x in peaks["intensity"]],
                    "n_rows": int(len(again)),
                    "best": [
                        [
                            int(r.peak),
                            r.parent,
                            r.formula,
                            r.loss,
                            r.loss_name,
                            int(r.unnamed_pieces),
                            bool(r.radical_loss),
                            bool(r.is_precursor),
                            int(r.rank),
                            int(r.n_candidates),
                            int(r.n_parents),
                            _num(round(float(r.ppm), 4)),
                        ]
                        for r in best.itertuples()
                    ],
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
