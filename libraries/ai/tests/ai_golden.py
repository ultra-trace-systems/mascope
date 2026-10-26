"""
The evaluation fixture: golden questions over the published demo dataset.

The acceptance test of the toolkit's tool surface (``docs/dev/ai_layer.md``,
"The evaluation fixture"). Each question is one a user could ask of the demo
data, with its known answer:

- **metadata**: which batches, how many samples, which collections, when -
  exact answers from the demo data;
- **analysis**: a time series' maximum, a normalised ratio, a
  background-subtracted peak - numbers computed through the toolkit's
  Frames, with tolerances;
- **interpretation**: what the peak at an m/z is - Mascope's scored verdict,
  candidates from the mass query, and the suggestions Mascope did not score
  kept apart from it.

Each question has a solver: a short program over the tool surface alone
(:data:`SURFACE`, through :class:`Surface`), standing in for the model. The
harness (:func:`run`) runs the solvers on a toolkit, checks each answer
against the known one, and reports which questions the surface answered,
answered wrongly, or cannot answer at all: a solver raises
:class:`Unanswerable` with what the surface lacks. A run by a model is a
separate, manual check (``golden_transcripts/README.md``).

The numbers come from the demo bundle's goldens (``expected/peaks.parquet``
and ``expected/ions.parquet``): each question that can be checked against
them says how (``golden``), and ``test_ai_golden.py`` holds the answers to
the goldens whenever the bundle is on disk. The answers are those of bundle
:data:`BUNDLE`, the registry's default, which CI's demo stack loads.

Run it against a demo stack, outside pytest::

    uv run python libraries/ai/tests/ai_golden.py run --url http://127.0.0.1:8080
    uv run python libraries/ai/tests/ai_golden.py list      # the note's table
    uv run python libraries/ai/tests/ai_golden.py prompts   # for a model's run
"""

import argparse
import inspect
import json
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from mascope_ai import Frame, Ref, Session, Toolkit
from mascope_ai.frame import jsonable
from mascope_ai.oracle import STATUS


#: The demo bundle whose data the known answers are.
BUNDLE = "1.4.1"

#: The demo stack's workspace the questions are about.
WORKSPACE = "Acquisitions Orbion"

#: The kinds of question, in the order the note lists them.
CATEGORIES = ("metadata", "analysis", "interpretation")

#: What a solver may call: the toolkit's tool surface, as a model has it.
#: Its SDK client is not on it, nor anything private.
SURFACE = (
    "find",
    "resolve",
    "describe",
    "load_peaks",
    "load_timeseries",
    "load_spectrum",
    "load_peaks_by_stage",
    "query_by_mz",
    "match_compounds",
    "derive",
)

#: The bundle's tolerances (its ``manifest.json``): an intensity within 1%,
#: an m/z within 0.1 ppm.
INTENSITY_REL = 0.01
MZ_PPM = 0.1

BROMIDE = "2025-08-11 Bromide m/z 82-750 acquisition"
URONIUM = "2025-08-11 Uronium m/z 122-740 acquisition"


class Unanswerable(Exception):
    """The tool surface cannot answer the question: the message says what
    it lacks."""


class Incomplete(Exception):
    """A Frame or a listing an answer rests on is not the whole answer."""


class Surface:
    """A toolkit narrowed to its tool surface: what a solver may call.

    Anything off :data:`SURFACE` - the SDK client, the session's internals -
    raises, so an answer the harness gets is one the surface gave.
    """

    def __init__(self, kit: Toolkit):
        object.__setattr__(self, "_kit", kit)

    def __getattr__(self, name: str) -> Any:
        if name not in SURFACE:
            raise AttributeError(
                f"{name!r} is not on the tool surface ({', '.join(SURFACE)})"
            )
        return getattr(object.__getattribute__(self, "_kit"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("the tool surface is read-only")


# --- known answers -------------------------------------------------------------


@dataclass(frozen=True)
class Close:
    """A number known to within a tolerance: *rel* of it, or *abs*."""

    value: float
    rel: float = 0.0
    abs: float = 0.0

    def matches(self, actual: Any) -> bool:
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
        return math.isclose(actual, self.value, rel_tol=self.rel, abs_tol=self.abs)

    def __str__(self) -> str:
        spread = f"{self.rel:.0%}" if self.rel else f"{self.abs:g}"
        if self.rel and self.rel < 0.01:
            spread = f"{self.rel * 1e6:g} ppm"
        return f"{self.value:.6g} (within {spread})"


@dataclass(frozen=True)
class Instant:
    """A point in time, however its text is written."""

    text: str

    def matches(self, actual: Any) -> bool:
        try:
            return pd.Timestamp(actual) == pd.Timestamp(self.text)
        except (TypeError, ValueError):
            return False

    def __str__(self) -> str:
        return self.text


def compare(
    expected: Any, actual: Any, where: str = "answer", *, partial: bool = False
) -> list[str]:
    """Where *actual* differs from *expected*, one line each.

    *expected* is plain JSON with :class:`Close` and :class:`Instant` among
    its values. With *partial*, *actual* may leave out keys of a dict: the
    goldens give part of an answer, not all of it.
    """
    if isinstance(expected, (Close, Instant)):
        return (
            [] if expected.matches(actual) else [f"{where}: {actual!r}, not {expected}"]
        )
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{where}: {actual!r}, not a mapping"]
        problems = [
            f"{where}: has {key!r}, which the known answer has not"
            for key in actual
            if key not in expected
        ]
        if not partial:
            problems += [
                f"{where}: lacks {key!r}" for key in expected if key not in actual
            ]
        for key, value in expected.items():
            if key in actual:
                problems += compare(
                    value, actual[key], f"{where}[{key!r}]", partial=partial
                )
        return problems
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
            return [
                f"{where}: {actual!r}, not {len(expected)} item(s) like {expected!r}"
            ]
        problems = []
        for index, (want, got) in enumerate(zip(expected, actual)):
            problems += compare(want, got, f"{where}[{index}]", partial=partial)
        return problems
    return [] if actual == expected else [f"{where}: {actual!r}, not {expected!r}"]


# --- the goldens -----------------------------------------------------------------

#: A raw file's name: its local acquisition time and its polarity.
_FILENAME = re.compile(r"_(\d{4}\.\d\d\.\d\d-\d\dh\d\dm\d\ds)_(pos|neg)_")


@dataclass(frozen=True)
class Goldens:
    """The demo bundle's goldens, each row with the sample it is of.

    A demo sample is named by its local acquisition time, which its raw
    file's name carries; ``sample`` is that name, ``polarity`` the file's
    ``pos`` or ``neg``. Files without either (the MS2 acquisitions) are left
    out: no question is about them yet.

    :ivar peaks: The found isotopologue peaks: ``target_isotope_formula``,
        ``mz``, ``height``, ``match_score``.
    :ivar ions: The ions' ``target_ion_formula``, ``match_score`` and
        ``match_category``.
    """

    peaks: pd.DataFrame
    ions: pd.DataFrame

    @classmethod
    def load(cls, bundle: Path) -> "Goldens":
        def read(name: str) -> pd.DataFrame:
            df = pd.read_parquet(bundle / "expected" / f"{name}.parquet")
            named = df["filename"].str.extract(_FILENAME)
            df["sample"] = pd.to_datetime(
                named[0], format="%Y.%m.%d-%Hh%Mm%Ss"
            ).dt.strftime("%Y-%m-%d %H:%M:%S")
            df["polarity"] = named[1]
            return df.dropna(subset=["polarity"])

        return cls(read("peaks"), read("ions"))

    def heights(self, isotope: str, polarity: str) -> pd.Series:
        """An isotopologue's height in each sample it was found in, by
        sample name (which sorts by time)."""
        rows = self.peaks[
            (self.peaks["target_isotope_formula"] == isotope)
            & (self.peaks["polarity"] == polarity)
        ]
        return rows.set_index("sample")["height"].sort_index()

    def peak(self, isotope: str, sample: str) -> pd.Series:
        rows = self.peaks[
            (self.peaks["target_isotope_formula"] == isotope)
            & (self.peaks["sample"] == sample)
        ]
        (index,) = rows.index
        return rows.loc[index]

    def ion(self, ion: str, sample: str) -> pd.Series:
        rows = self.ions[
            (self.ions["target_ion_formula"] == ion) & (self.ions["sample"] == sample)
        ]
        (index,) = rows.index
        return rows.loc[index]


def bundle_dir(version: str = BUNDLE) -> Path | None:
    """Where the demo bundle *version* is on disk, or None.

    ``MASCOPE_DEMO_BUNDLE_DIR`` names it outright; otherwise the CLI's cache
    under ``MASCOPE_PATH`` (CI's demo-stack job keeps it there), or under
    this checkout.
    """
    named = os.environ.get("MASCOPE_DEMO_BUNDLE_DIR")
    homes = [Path(named)] if named else []
    if os.environ.get("MASCOPE_PATH"):
        homes.append(Path(os.environ["MASCOPE_PATH"]) / ".runtime" / "demo" / version)
    homes.append(Path(__file__).resolve().parents[3] / ".runtime" / "demo" / version)
    for home in homes:
        if (home / "expected" / "peaks.parquet").is_file():
            return home
    return None


# --- the questions -----------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """One golden question.

    :ivar id: Its ID: the category's initial and a number.
    :ivar category: ``metadata``, ``analysis`` or ``interpretation``.
    :ivar text: The question, as a user would ask it.
    :ivar solve: Answers it through the tool surface, as plain JSON.
    :ivar expected: The known answer.
    :ivar summary: The known answer in a few words, for the note.
    :ivar tools: The surface's calls the solver makes.
    :ivar golden: Part of the known answer, from the bundle's goldens.
    :ivar check: More it asks of the answer, given the answer and the
        manifest's record of it: the problems, one line each.
    """

    id: str
    category: str
    text: str
    solve: Callable[[Surface], Any]
    expected: Any
    summary: str
    tools: tuple[str, ...]
    golden: Callable[[Goldens], Any] | None = None
    check: Callable[[Any, dict[str, Any]], list[str]] | None = None


def _whole(found: Any) -> Any:
    """*found* - a Frame or ``find``'s listing - when it is all there is."""
    if not found.complete:
        raise Incomplete(f"not the whole answer: {list(found.failures)[:3]}")
    return found


def _sample(kit: Surface, batch: str, name: str) -> Ref:
    """The sample named *name* in *batch*, looked for within the batch.

    A demo sample is named by its acquisition time, so two of one name in a
    batch are two samples of one raw file. A demo stack can hold such a
    copy: CI's demo-stack job runs the end-to-end suite before these
    questions, and its upload test leaves a second sample of a demo raw
    file (``server/frontend/tests/e2e/upload.spec.js``). The published
    sample is the one recorded first.
    """
    within = kit.resolve(batch, "batch")
    named = [
        ref
        for ref in _whole(kit.find(name, kind="sample", within=within))
        if ref.name == name
    ]
    if not named:
        # The surface's own answer: NotFound, with what to do next
        return kit.resolve(name, "sample", within=within)
    return min(
        named,
        key=lambda ref: (
            ref.modified is None,
            ref.modified.timestamp() if ref.modified is not None else 0.0,
            ref.id,
        ),
    )


def _found(frame: Frame) -> pd.DataFrame:
    """A peak load's rows of found isotopologues: matched, scored above 0
    (the goldens' rows)."""
    df = _whole(frame).df
    return df[df["match_score_isotope"] > 0]


# The metadata questions: exact answers from the demo data.


def _datasets(kit: Surface) -> dict[str, int]:
    datasets = _whole(kit.find("", kind="dataset"))
    return {ref.name: kit.describe(ref)["n_batches"] for ref in datasets}


def _batches_of_2025(kit: Surface) -> list[str]:
    dataset = kit.resolve("2025", "dataset")
    return sorted(
        ref.name for ref in _whole(kit.find("", kind="batch", within=dataset))
    )


def _acquisitions_per_batch(kit: Surface) -> dict[str, int]:
    dataset = kit.resolve("2025", "dataset")
    batches = _whole(kit.find("", kind="batch", within=dataset))
    # A sample's name is its acquisition time, which a copy shares (_sample)
    return {
        batch.name: len(
            {
                sample.name
                for sample in _whole(
                    kit.find("", kind="sample", within=batch, limit=100_000)
                )
            }
        )
        for batch in batches
    }


def _golden_acquisitions_per_batch(goldens: Goldens) -> dict[str, int]:
    # A batch per polarity, as the files' names say
    files = goldens.peaks.groupby("polarity")["filename"].nunique()
    return {BROMIDE: int(files["neg"]), URONIUM: int(files["pos"])}


def _collections_of_bromide(kit: Surface) -> list[str]:
    about = kit.describe(kit.resolve(BROMIDE, "batch"))
    if about["n_collections"] != len(about["collections"]):
        raise Incomplete("describe() listed only some of the collections")
    return sorted(collection["name"] for collection in about["collections"])


def _time_span_of_uronium(kit: Surface) -> dict[str, str]:
    about = kit.describe(kit.resolve(URONIUM, "batch"))
    return {"first": about["first_measured"], "last": about["last_measured"]}


def _batches_named_uronium(kit: Surface) -> list[str]:
    return sorted(ref.name for ref in _whole(kit.find("Uronium", kind="batch")))


def _uronium_monitor_size(kit: Surface) -> int:
    return kit.describe(kit.resolve("Uronium monitor", "collection"))["n_compounds"]


def _ionization_of_bromide(kit: Surface) -> dict[str, Any]:
    batch = kit.resolve(BROMIDE, "batch")
    sample = _whole(kit.find("", kind="sample", within=batch, limit=1))[0]
    # The keys a describe() that answered would have; neither has them yet
    for about in (kit.describe(batch), kit.describe(sample)):
        if "ionization_mode" in about and "ionization_mechanisms" in about:
            return {
                "mode": about["ionization_mode"],
                "mechanisms": sorted(about["ionization_mechanisms"]),
            }
    raise Unanswerable(
        "describe() names no ionization mode or mechanism of a batch or a "
        "sample: a batch says only how many modes its samples have"
    )


# The analysis questions: numbers computed through Frames.

#: C10H16O2's protonated ion, the uronium batch's largest event.
PROTONATED = "C10H17O2+"


def _timeseries_maximum(kit: Surface) -> dict[str, Any]:
    batch = kit.resolve(URONIUM, "batch")
    series = _whole(kit.load_timeseries(isotope=PROTONATED, batches=batch))
    # A sample's point is the mean of its scans: its averaged peak's height
    points = series.df.groupby("sample_item_name", as_index=False)["height"].mean()
    batch_series = kit.derive(
        points,
        series,
        code="series.df.groupby('sample_item_name')['height'].mean()",
        computed="height",
    )
    points = batch_series.df.set_index("sample_item_name")["height"]
    return {
        "sample": points.idxmax(),
        "height": float(points.max()),
        "n_samples": len(points),
    }


def _golden_timeseries_maximum(goldens: Goldens) -> dict[str, Any]:
    heights = goldens.heights(PROTONATED, "pos")
    return {
        "sample": heights.idxmax(),
        "height": float(heights.max()),
        "n_samples": len(heights),
    }


#: Formic acid's bromide adduct (its 79Br line), and the reagent cluster
#: Br3-'s most abundant line.
FORMIC = "CH2BrO2-"
REAGENT = "[81Br]Br2-"


def _normalised_ratio(kit: Surface) -> dict[str, Any]:
    batch = kit.resolve(BROMIDE, "batch")
    peaks = kit.load_peaks(batches=batch)
    found = _found(peaks)
    lines = found[found["target_isotope_formula"].isin([FORMIC, REAGENT])]
    wide = lines.pivot_table(
        index="sample_item_name",
        columns="target_isotope_formula",
        values="height",
        aggfunc="max",
    )
    ratio = (wide[FORMIC] / wide[REAGENT]).dropna().rename("normalised")
    normalised = kit.derive(
        ratio.reset_index(),
        peaks,
        code="height of CH2BrO2- / height of [81Br]Br2-, per sample",
    )
    ratio = normalised.df.set_index("sample_item_name")["normalised"]
    return {
        "mean": float(ratio.mean()),
        "n_samples": len(ratio),
        "max": float(ratio.max()),
        "max_sample": ratio.idxmax(),
    }


def _golden_normalised_ratio(goldens: Goldens) -> dict[str, Any]:
    ratio = (goldens.heights(FORMIC, "neg") / goldens.heights(REAGENT, "neg")).dropna()
    return {
        "mean": float(ratio.mean()),
        "n_samples": len(ratio),
        "max": float(ratio.max()),
        "max_sample": ratio.idxmax(),
    }


#: The uronium batch's samples before its event: the background.
BACKGROUND_SAMPLES = 5


def _background_subtracted(kit: Surface) -> dict[str, Any]:
    batch = kit.resolve(URONIUM, "batch")
    peaks = kit.load_peaks(batches=batch)
    found = _found(peaks)
    rows = found[found["target_isotope_formula"] == PROTONATED]
    series = rows.groupby(["datetime_utc", "sample_item_name"])["height"].max()
    background = series.iloc[:BACKGROUND_SAMPLES].mean()
    above = (series - background).rename("above_background").reset_index()
    subtracted = kit.derive(
        above, peaks, code=f"height - mean height of the first {BACKGROUND_SAMPLES}"
    )
    above = subtracted.df.set_index("sample_item_name")["above_background"]
    return {
        "background": float(background),
        "peak": float(above.max()),
        "sample": above.idxmax(),
    }


def _golden_background_subtracted(goldens: Goldens) -> dict[str, Any]:
    heights = goldens.heights(PROTONATED, "pos")
    background = heights.iloc[:BACKGROUND_SAMPLES].mean()
    above = heights - background
    return {
        "background": float(background),
        "peak": float(above.max()),
        "sample": above.idxmax(),
    }


#: The uronium sample at its event's maximum.
URONIUM_PEAK_SAMPLE = "2025-08-11 14:34:03"


def _spectrum_apex(kit: Surface) -> dict[str, Any]:
    sample = _sample(kit, URONIUM, URONIUM_PEAK_SAMPLE)
    spectrum = kit.load_spectrum(sample, mz_range=(169.10, 169.15))
    df = _whole(spectrum).df
    apex = df.loc[df["intensity"].idxmax()]
    # The window's points too: the apex alone is also the whole spectrum's
    return {
        "mz": float(apex["mz"]),
        "intensity": float(apex["intensity"]),
        "n_points": len(df),
    }


def _golden_spectrum_apex(goldens: Goldens) -> dict[str, float]:
    peak = goldens.peak(PROTONATED, URONIUM_PEAK_SAMPLE)
    return {"mz": float(peak["mz"]), "intensity": float(peak["height"])}


def _whole_acquisition_stage(kit: Surface) -> dict[str, float]:
    sample = _sample(kit, URONIUM, URONIUM_PEAK_SAMPLE)
    first, last = kit.describe(sample)["t_range_s"]
    # A stage reads only the peaks whose time series has been computed, and
    # a series is computed when it is first asked for (the stage load's
    # warning says so): ask for it first, whatever ran before
    kit.load_timeseries(isotope=PROTONATED, samples=sample)
    found = _found(kit.load_peaks_by_stage(sample, [(first, last, "whole")]))
    rows = found[found["target_isotope_formula"] == PROTONATED]
    return {"height": float(rows["height"].max()) if not rows.empty else None}


def _golden_whole_acquisition_stage(goldens: Goldens) -> dict[str, float]:
    return {"height": float(goldens.peak(PROTONATED, URONIUM_PEAK_SAMPLE)["height"])}


# The interpretation questions: Mascope's verdict on the peak at an m/z.


def _peak_at(
    kit: Surface, batch: str, sample_name: str, mz: float, suggested: list[str]
):
    """What the peak at *mz* in the sample *sample_name* of *batch* is.

    The peak is the one nearest *mz* within 5 ppm in the sample's peak
    list. The mass query proposes candidates for its m/z with the sample's
    mechanisms - those its stored matches used, as ``describe`` names none -
    and Mascope scores them, and the model's own *suggested*, in the sample.
    The answer is the scored verdicts whose attributed peaks include this
    peak; the rest are listed apart, and what Mascope did not score is
    ``unscored``, never a verdict.
    """
    sample = _sample(kit, batch, sample_name)
    peaks = _whole(kit.load_peaks(samples=sample)).df
    near = peaks[(peaks["mz"] - mz).abs() <= mz * 5e-6]
    if near.empty:
        return {"peak_mz": None, "assigned": []}
    peak = near.loc[(near["mz"] - mz).abs().idxmin()]
    found = peaks[peaks["match_score_isotope"] > 0]
    mechanisms = sorted(found["ionization_mechanism"].dropna().unique())
    candidates = kit.query_by_mz(mz, mechanisms)
    result = kit.match_compounds(sample, candidates, suggested=suggested)
    return {
        "peak_mz": float(peak["mz"]),
        "assigned": [
            {
                "formula": verdict.formula,
                "status": verdict.status,
                "ion": ion.formula,
                "mechanism": ion.mechanism,
                "ion_score": ion.score,
            }
            for verdict in result.verdicts
            if peak["peak_id"] in verdict.peak_ids
            for ion in verdict.ions
            if peak["peak_id"] in ion.peak_ids
        ],
        "candidates": sorted(candidate.formula for candidate in candidates),
        "scored": {verdict.formula: verdict.status for verdict in result.verdicts},
        "unscored": sorted(unscored.formula for unscored in result.unscored),
    }


def _nothing_unscored_is_a_verdict(answer: Any, record: dict[str, Any]) -> list[str]:
    """An unscored candidate is never presented as a verdict, and the
    manifest's answer record lists it as unscored."""
    unscored = set(answer.get("unscored", ()))
    problems = [
        f"{formula!r} is unscored, yet presented as a verdict"
        for formula in sorted(unscored)
        if formula in answer.get("scored", {})
        or formula in {a["formula"] for a in answer.get("assigned", ())}
    ]
    problems += [
        f"{entry['formula']!r} is assigned, yet Mascope gave it no score"
        for entry in answer.get("assigned", ())
        if entry["formula"] not in answer.get("scored", {})
    ]
    recorded = sorted(entry["formula"] for entry in record["values"]["unscored"])
    if recorded != sorted(unscored):
        problems.append(
            f"the manifest records {recorded} as unscored, the answer {sorted(unscored)}"
        )
    return problems


#: The bromide sample whose formic acid peak is asked about.
BROMIDE_SAMPLE = "2025-08-11 14:34:57"


def _formic_acid_peak(kit: Surface) -> dict[str, Any]:
    # The model's own suggestions: acetic acid, which Mascope finds elsewhere
    # in the sample, and the 13C-labelled formic acid it cannot score
    return _peak_at(kit, BROMIDE, BROMIDE_SAMPLE, 124.9244, ["C2H4O2", "[13C]H2O2"])


def _golden_peak(goldens: Goldens, ion: str, sample: str) -> dict[str, Any]:
    """The goldens' part of an interpretation answer: the peak of *ion*'s
    first line in *sample*, and the ion's score and status there."""
    scored = goldens.ion(ion, sample)
    return {
        "peak_mz": float(goldens.peak(ion, sample)["mz"]),
        "assigned": [
            {
                "ion": ion,
                "status": STATUS[int(scored["match_category"])],
                "ion_score": float(scored["match_score"]),
            }
        ],
    }


def _golden_formic_acid_peak(goldens: Goldens) -> dict[str, Any]:
    return _golden_peak(goldens, FORMIC, BROMIDE_SAMPLE)


def _uronium_peak(kit: Surface) -> dict[str, Any]:
    # The model's own suggestion: the neutral's mass, which Mascope refuses
    return _peak_at(kit, URONIUM, URONIUM_PEAK_SAMPLE, 169.1223, ["168.1150"])


def _golden_uronium_peak(goldens: Goldens) -> dict[str, Any]:
    return _golden_peak(goldens, PROTONATED, URONIUM_PEAK_SAMPLE)


def _peak_mz(value: float) -> Close:
    return Close(value, rel=MZ_PPM * 1e-6)


def _height(value: float) -> Close:
    return Close(value, rel=INTENSITY_REL)


QUESTIONS: tuple[Question, ...] = (
    Question(
        "M1",
        "metadata",
        "Which datasets does the workspace hold, and how many batches has each?",
        _datasets,
        {"2025": 2, "2026": 3},
        "2025 (2 batches), 2026 (3)",
        ("describe", "find"),
    ),
    Question(
        "M2",
        "metadata",
        "Which batches are in the 2025 dataset?",
        _batches_of_2025,
        [BROMIDE, URONIUM],
        "the bromide and the uronium acquisitions of 2025-08-11",
        ("find", "resolve"),
    ),
    Question(
        "M3",
        "metadata",
        "How many acquisitions does each batch of the 2025 dataset hold?",
        _acquisitions_per_batch,
        {BROMIDE: 80, URONIUM: 81},
        "bromide 80, uronium 81",
        ("find", "resolve"),
        golden=_golden_acquisitions_per_batch,
    ),
    Question(
        "M4",
        "metadata",
        "Which target collections is the 2025 bromide batch matched against?",
        _collections_of_bromide,
        ["Bromide high m/z calibrants", "Bromide monitor"],
        "Bromide high m/z calibrants, Bromide monitor",
        ("describe", "resolve"),
    ),
    Question(
        "M5",
        "metadata",
        "Over what time span was the 2025 uronium batch measured?",
        _time_span_of_uronium,
        {
            "first": Instant("2025-08-11T11:23:12.911572+00:00"),
            "last": Instant("2025-08-11T13:00:32.575039+00:00"),
        },
        "2025-08-11 11:23:12 to 13:00:32 UTC",
        ("describe", "resolve"),
    ),
    Question(
        "M6",
        "metadata",
        "Which batches have 'Uronium' in their name?",
        _batches_named_uronium,
        [URONIUM, "2026-10-15 Uronium RI acquisition"],
        "the 2025-08-11 uronium and the 2026-10-15 Uronium RI acquisitions",
        ("find",),
    ),
    Question(
        "M7",
        "metadata",
        "How many compounds does the Uronium monitor collection have?",
        _uronium_monitor_size,
        50,
        "50",
        ("describe", "resolve"),
    ),
    Question(
        "M8",
        "metadata",
        "Which ionization mode and mechanisms do the 2025 bromide batch's samples use?",
        _ionization_of_bromide,
        {"mode": "Bromide m/z 82-750", "mechanisms": ["[M+Br]-", "[M-H]-"]},
        "Bromide m/z 82-750: [M+Br]- and [M-H]-",
        ("describe", "find", "resolve"),
    ),
    Question(
        "A1",
        "analysis",
        "When did C10H16O2's protonated ion (C10H17O2+) peak in the 2025 uronium "
        "batch's time series, and how high?",
        _timeseries_maximum,
        {
            "sample": URONIUM_PEAK_SAMPLE,
            "height": _height(1176170.32),
            "n_samples": 81,
        },
        "at the 14:34:03 sample, height 1.176e6",
        ("derive", "load_timeseries", "resolve"),
        golden=_golden_timeseries_maximum,
    ),
    Question(
        "A2",
        "analysis",
        "In the 2025 bromide batch, what is formic acid's signal (CH2BrO2-) "
        "normalised to the reagent ion Br3- (its [81Br]Br2- line): its mean, "
        "and its maximum and where?",
        _normalised_ratio,
        {
            "mean": Close(0.0958985, rel=2 * INTENSITY_REL),
            "n_samples": 80,
            "max": Close(0.1074057, rel=2 * INTENSITY_REL),
            "max_sample": "2025-08-11 15:21:13",
        },
        "mean 0.0959 over 80 samples, maximum 0.1074 at 15:21:13",
        ("derive", "load_peaks", "resolve"),
        golden=_golden_normalised_ratio,
    ),
    Question(
        "A3",
        "analysis",
        "How far above its background did C10H17O2+ rise in the 2025 uronium batch, "
        "the background being the mean of the batch's first five samples?",
        _background_subtracted,
        {
            "background": _height(45926.66),
            "peak": _height(1130243.66),
            "sample": URONIUM_PEAK_SAMPLE,
        },
        "1.130e6 above a background of 4.59e4, at 14:34:03",
        ("derive", "load_peaks", "resolve"),
        golden=_golden_background_subtracted,
    ),
    Question(
        "A4",
        "analysis",
        "Where is the apex of the 14:34:03 uronium sample's spectrum between "
        "m/z 169.10 and 169.15, how intense is it, and how many points has "
        "the window?",
        _spectrum_apex,
        # The profile's highest point, not the fitted peak the goldens hold:
        # within 5 ppm and 2% of it
        {
            "mz": Close(169.122318, rel=5e-6),
            "intensity": Close(1176170.32, rel=0.02),
            "n_points": 102,
        },
        "m/z 169.1223, about the peak's height (1.18e6); 102 points in the window",
        ("find", "load_spectrum", "resolve"),
        golden=_golden_spectrum_apex,
    ),
    Question(
        "A5",
        "analysis",
        "Read as one stage over its whole acquisition, how high is C10H17O2+ "
        "in the 14:34:03 uronium sample?",
        _whole_acquisition_stage,
        {"height": _height(1176170.32)},
        "1.176e6, the averaged peak list's height",
        ("describe", "find", "load_peaks_by_stage", "load_timeseries", "resolve"),
        golden=_golden_whole_acquisition_stage,
    ),
    Question(
        "I1",
        "interpretation",
        "What is the peak at m/z 124.9244 in the bromide sample measured at 14:34:57?",
        _formic_acid_peak,
        {
            "peak_mz": _peak_mz(124.924364),
            "assigned": [
                {
                    "formula": "CH2O2",
                    "status": "probable",
                    "ion": FORMIC,
                    "mechanism": "[M+Br]-",
                    "ion_score": Close(0.963558, abs=1e-6),
                }
            ],
            "candidates": ["CH2O2"],
            "scored": {"C2H4O2": "probable", "CH2O2": "probable"},
            "unscored": ["[13C]H2O2"],
        },
        "formic acid, CH2O2 as CH2BrO2- [M+Br]-, probable; "
        "the 13C-labelled suggestion unscored",
        ("find", "load_peaks", "match_compounds", "query_by_mz", "resolve"),
        golden=_golden_formic_acid_peak,
        check=_nothing_unscored_is_a_verdict,
    ),
    Question(
        "I2",
        "interpretation",
        "What is the peak at m/z 169.1223 in the uronium sample measured at 14:34:03?",
        _uronium_peak,
        {
            "peak_mz": _peak_mz(169.122318),
            "assigned": [
                {
                    "formula": "C10H16O2",
                    "status": "probable",
                    "ion": PROTONATED,
                    "mechanism": "[M+H]+",
                    "ion_score": Close(0.995059, abs=1e-6),
                }
            ],
            "candidates": ["C10H16O2", "CH14N2O6", "CH17N3O6", "H13NO5"],
            "scored": {
                "C10H16O2": "probable",
                "CH14N2O6": "unmatched",
                "CH17N3O6": "unmatched",
                "H13NO5": "unmatched",
            },
            "unscored": ["168.1150"],
        },
        "C10H16O2 as C10H17O2+ [M+H]+, probable; three other candidates "
        "unmatched; the numeric-mass suggestion unscored",
        ("find", "load_peaks", "match_compounds", "query_by_mz", "resolve"),
        golden=_golden_uronium_peak,
        check=_nothing_unscored_is_a_verdict,
    ),
)


def question(question_id: str) -> Question:
    """The question with this ID."""
    for each in QUESTIONS:
        if each.id == question_id:
            return each
    raise KeyError(question_id)


# --- the harness -----------------------------------------------------------------

#: What became of a question: answered as known, answered otherwise, not
#: answerable through the surface, or failed along the way.
STATUSES = ("answered", "wrong", "unanswerable", "error")


@dataclass
class Outcome:
    """What the harness made of one question.

    :ivar question: The question.
    :ivar status: One of :data:`STATUSES`.
    :ivar answer: The solver's answer, as plain JSON (None without one).
    :ivar problems: Where the answer differs from the known one, or what
        else is wrong with it.
    :ivar reason: Why the surface cannot answer it, or the error.
    :ivar tools: The surface's calls the solver made, as the manifest
        recorded them.
    :ivar seconds: How long it took.
    """

    question: Question
    status: str
    answer: Any = None
    problems: list[str] = field(default_factory=list)
    reason: str = ""
    tools: tuple[str, ...] = ()
    seconds: float = 0.0

    def describe(self) -> str:
        """The outcome in a few lines."""
        head = f"{self.question.id} {self.status}: {self.question.text}"
        if self.status == "answered":
            return head
        lines = [head]
        lines += [f"  {problem}" for problem in self.problems]
        if self.reason:
            lines.append(f"  {self.reason}")
        if self.answer is not None and self.status == "wrong":
            lines.append(f"  answer: {json.dumps(self.answer, sort_keys=True)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.question.id,
            "category": self.question.category,
            "status": self.status,
            "answer": self.answer,
            "problems": self.problems,
            "reason": self.reason,
            "tools": list(self.tools),
            "seconds": round(self.seconds, 1),
        }


def run_question(target: Question, kit: Toolkit) -> Outcome:
    """Answer *target* through *kit*'s tool surface and check the answer.

    Each outcome closes an answer in the session's manifest - the answer,
    or why there is none - so the manifest holds the run question by
    question, each with the calls it made.
    """
    started = time.monotonic()
    answer = None
    problems: list[str] = []
    reason = ""
    try:
        answer = jsonable(target.solve(Surface(kit)))
    except Unanswerable as e:
        status, reason = "unanswerable", str(e)
    except Exception as e:  # anything else the surface or the solver raised
        status, reason = "error", f"{type(e).__name__}: {e}"
    else:
        status = "answered"

    if status == "answered":
        text = f"[{target.id}] {json.dumps(answer, sort_keys=True)}"
    else:
        text = f"[{target.id}] The tool surface gave no answer ({status}): {reason}"
    record = kit.answer(text, code=inspect.getsource(target.solve))
    calls = {call["seq"]: call["op"] for call in kit.session.manifest.records("call")}
    tools = tuple(sorted({calls[seq] for seq in record["calls"] if seq in calls}))

    if status == "answered":
        problems = compare(target.expected, answer)
        if target.check is not None:
            problems += target.check(answer, record)
        if problems:
            status = "wrong"
    return Outcome(
        target,
        status,
        answer=answer,
        problems=problems,
        reason=reason,
        tools=tools,
        seconds=time.monotonic() - started,
    )


@dataclass
class Report:
    """A run's outcomes, question by question."""

    outcomes: list[Outcome]

    def by_status(self, status: str) -> list[Outcome]:
        return [outcome for outcome in self.outcomes if outcome.status == status]

    @property
    def passed(self) -> bool:
        """Whether every question the surface can answer was answered as
        known."""
        return not self.by_status("wrong") and not self.by_status("error")

    def table(self) -> str:
        """The outcomes as a text table, then what the surface cannot
        answer and what went wrong."""
        lines = [f"{'id':<4} {'category':<15} {'status':<13} {'s':>6}  question"]
        for outcome in self.outcomes:
            text = outcome.question.text
            lines.append(
                f"{outcome.question.id:<4} {outcome.question.category:<15} "
                f"{outcome.status:<13} {outcome.seconds:6.1f}  "
                f"{text if len(text) <= 70 else text[:69] + '...'}"
            )
        counts = ", ".join(
            f"{len(self.by_status(status))} {status}" for status in STATUSES
        )
        lines.append(f"{len(self.outcomes)} question(s): {counts}")
        gaps = self.by_status("unanswerable")
        if gaps:
            lines.append("The tool surface cannot answer:")
            lines += [f"  {gap.question.id}: {gap.reason}" for gap in gaps]
        failed = self.by_status("wrong") + self.by_status("error")
        if failed:
            lines.append("Answered wrongly or failed:")
            lines += [
                "  " + line
                for outcome in failed
                for line in outcome.describe().splitlines()
            ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle": BUNDLE,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


def run(kit: Toolkit, questions: Iterable[Question] = QUESTIONS) -> Report:
    """Answer each question through *kit*'s tool surface, in order."""
    return Report([run_question(each, kit) for each in questions])


#: The outcomes of a test session's run on a demo stack, question by
#: question: ``conftest.py`` prints them as a report at the session's end.
OUTCOMES: list[Outcome] = []


# --- the note's list -----------------------------------------------------------------


def markdown(questions: Iterable[Question] = QUESTIONS) -> str:
    """The question set as the note lists it: a Markdown table."""
    rows = [
        "| ID | Kind | Question | Known answer | Through |",
        "| --- | --- | --- | --- | --- |",
    ]
    for each in questions:
        tools = ", ".join(f"`{tool}`" for tool in each.tools)
        cells = [each.id, each.category, each.text, each.summary, tools]
        rows.append(
            "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"
        )
    return "\n".join(rows)


# --- the command line -----------------------------------------------------------------


def _toolkit(url: str, token: str, workspace: str, root: Path) -> Toolkit:
    from mascope_sdk import MascopeClient

    client = MascopeClient(
        url=url, access_token=token, workspace=workspace, service_name="mascope_sdk"
    )
    return Toolkit(client, session=Session(root=root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="The golden questions over the demo dataset (docs/dev/ai_layer.md)."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser(
        "run", help="answer them through the tool surface of a demo stack"
    )
    runner.add_argument(
        "--url",
        default=os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080"),
    )
    runner.add_argument(
        "--token",
        default=os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token"),
    )
    runner.add_argument("--workspace", default=WORKSPACE)
    runner.add_argument("--only", help="question IDs, comma-separated")
    runner.add_argument("--json", action="store_true", help="the report as JSON")
    runner.add_argument(
        "--session-root",
        type=Path,
        help="where the run's session (its Frames and manifest) is kept; "
        "a temporary directory when not given",
    )
    commands.add_parser("list", help="the question set as the note lists it")
    commands.add_parser("prompts", help="the questions alone, one per line")
    args = parser.parse_args(argv)

    if args.command == "list":
        print(markdown())
        return 0
    if args.command == "prompts":
        for each in QUESTIONS:
            print(f"{each.id}. {each.text}")
        return 0

    chosen = (
        [question(qid.strip()) for qid in args.only.split(",")]
        if args.only
        else list(QUESTIONS)
    )
    root = args.session_root or Path(tempfile.mkdtemp(prefix="mascope-golden-"))
    kit = _toolkit(args.url, args.token, args.workspace, root)
    report = run(kit, chosen)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(report.table())
        print(f"The run's session, its manifest included: {kit.session.path}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
