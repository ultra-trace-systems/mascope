"""
A synthetic campaign: a month of one instrument at one sample a minute.

The demo dataset is a handful of files over two days and cannot stand in for
a campaign, so the package's views, charts and digests are tested on this
instead (``docs/dev/visual_digest.md``, section 4, phase 0). :func:`generate`
emits the campaign store's tables (section 2.2) and, beside them, the answers
they were built from (:class:`Truth`), so a test asserts against known
numbers rather than against whatever the code computed last time:

- fifty species, each with a named diurnal cycle: ``day`` (peaking between
  11 and 15 local time), ``night`` (between 22 and 03) or ``flat``;
- two episodes, each multiplying a few species by a known factor between a
  known start and end;
- one day whose files were acquired but filed into no batch: no samples,
  the files listed with ``in_batch`` false;
- a TIC with a step of a known factor at a known time;
- an assignment run that changes mid-month, under which three batch peaks
  read as other formulas (the formula keys change, the ion keys do not);
- for the halide-negative chemistry, a reagent ion falling at a known slope,
  the species' sensitivity following it;
- below-detection values as NaN, never zero.

The two chemistries are two parameter sets, :data:`HALIDE_NEGATIVE` and
:data:`POSITIVE`; :func:`dataclasses.replace` derives others.

Determinism: the same parameters and seed give the same tables, and
:func:`parquet_bytes` the same bytes, on every platform. The generator
draws from ``numpy.random.Generator.random`` alone, whose stream is integer
arithmetic, and computes with ``+``, ``-``, ``*``, ``/`` and ``sqrt``, which
IEEE 754 rounds identically everywhere; no ``exp``, ``log``, ``sin`` or
``pow``, whose last bit depends on the platform's maths library. Its noise is
an Irwin-Hall sum of four uniforms rather than a normal draw for the same
reason. Keep it that way when extending it.
"""

import hashlib
import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


TABLES = ("samples", "files", "ion_series", "peak_series", "species")

DAY = pd.Timedelta(days=1)
_SQRT3 = math.sqrt(3.0)

# Monoisotopic masses (u) for the species' theoretical m/z.
_MASS = {
    "C": 12.0,
    "H": 1.00782503207,
    "I": 126.904473,
    "N": 14.0030740048,
    "O": 15.99491461956,
    "S": 31.97207100,
}
_ELECTRON = 0.000548579909


# --- Parameters -------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeSpec:
    """An episode to build: offsets from the campaign start, species by index."""

    start: pd.Timedelta
    end: pd.Timedelta
    factor: float
    species: tuple[int, ...]


@dataclass(frozen=True)
class CampaignParams:
    """One chemistry's campaign. Offsets count from ``start``."""

    name: str
    polarity: str
    instrument_type: str
    # The ionization mechanism every species is read under, and what it adds
    # to the neutral formula; ``charge`` is the ion's sign.
    mechanism: str
    adduct: str
    charge: int
    # The reagent ion (an ion formula) and its level at the start; None for a
    # chemistry without one. The species' sensitivity follows the reagent,
    # so normalising by it takes the drift out.
    reagent: str | None
    reagent_level: float = 2.0e6
    reagent_slope: float = -0.012  # fraction of reagent_level per day
    tic_level: float = 5.0e7
    start: pd.Timestamp = pd.Timestamp("2026-06-01T00:00:00Z")
    days: int = 30
    timezone: str = "Europe/Helsinki"
    instrument: str = "instrument X"
    file_minutes: int = 10
    n_species: int = 50
    # The first n_targets species are also a target collection's ions.
    n_targets: int = 10
    noise_cv: float = 0.08
    detection_limit: float = 50.0
    gap_day: int = 20
    tic_step_at: pd.Timedelta = pd.Timedelta(days=11, hours=12)
    tic_step_factor: float = 0.6
    run_change_day: int = 15
    n_renamed: int = 3
    episodes: tuple[EpisodeSpec, ...] = (
        EpisodeSpec(
            pd.Timedelta(days=6, hours=9),
            pd.Timedelta(days=7, hours=3),
            6.0,
            (3, 8, 13, 21, 34),
        ),
        EpisodeSpec(
            pd.Timedelta(days=24, hours=14),
            pd.Timedelta(days=25, hours=2),
            3.0,
            (5, 17, 29),
        ),
    )


HALIDE_NEGATIVE = CampaignParams(
    name="halide_negative",
    polarity="negative",
    instrument_type="TOF",
    mechanism="[M+I]-",
    adduct="I",
    charge=-1,
    reagent="I-",
)

POSITIVE = CampaignParams(
    name="positive",
    polarity="positive",
    instrument_type="Orbitrap",
    mechanism="[M+H]+",
    adduct="H",
    charge=1,
    reagent=None,
    tic_level=2.0e8,
)

CHEMISTRIES = {p.name: p for p in (HALIDE_NEGATIVE, POSITIVE)}


# --- Known answers ----------------------------------------------------------


@dataclass(frozen=True)
class Cycle:
    """A species' diurnal cycle: ``peak_hour`` is local, None when flat.

    ``amplitude`` is the peak over the trough of the noiseless signal.
    """

    kind: str
    peak_hour: int | None
    amplitude: float


@dataclass(frozen=True)
class Episode:
    start: pd.Timestamp
    end: pd.Timestamp
    factor: float
    series_keys: tuple[str, ...]


@dataclass(frozen=True)
class Gap:
    """A day acquired but filed into no batch: ``files`` files, no samples."""

    start: pd.Timestamp
    end: pd.Timestamp
    files: int


@dataclass(frozen=True)
class Drift:
    """The reagent ion's noiseless intensity: ``intercept + slope * days``."""

    series_key: str
    intercept: float
    slope_per_day: float


@dataclass(frozen=True)
class Step:
    """The TIC's noiseless level before and after ``at``."""

    at: pd.Timestamp
    before: float
    after: float


@dataclass(frozen=True)
class RunChange:
    """From ``at`` on, batches carry ``after``; ``readings`` are old -> new keys."""

    at: pd.Timestamp
    before: str
    after: str
    readings: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Truth:
    start: pd.Timestamp
    end: pd.Timestamp
    cadence: pd.Timedelta
    timezone: str
    detection_limit: float
    # Every species' series key, ion and formula alike, to its cycle and to
    # its noiseless level at the cycle's peak (before drift and episodes).
    cycles: dict[str, Cycle]
    levels: dict[str, float]
    episodes: tuple[Episode, ...]
    gap: Gap
    tic_step: Step
    run_change: RunChange
    drift: Drift | None


@dataclass(frozen=True)
class Campaign:
    params: CampaignParams
    seed: int
    tables: dict[str, pd.DataFrame] = field(repr=False)
    truth: Truth


# --- Generation -------------------------------------------------------------


def _hill(counts: dict[str, int]) -> str:
    """A formula in Hill order: C, H, then the rest alphabetically."""
    order = ["C", "H"] + sorted(e for e in counts if e not in ("C", "H"))
    parts = []
    for element in order:
        n = counts.get(element, 0)
        if n:
            parts.append(element if n == 1 else f"{element}{n}")
    return "".join(parts)


def _mass(counts: dict[str, int]) -> float:
    total = 0.0
    for element in sorted(counts):
        total = total + counts[element] * _MASS[element]
    return total


def _draw_formulas(rng: np.random.Generator, n: int) -> list[dict[str, int]]:
    """``n`` distinct closed-shell CHO, CHON and CHOS formulas."""
    formulas: list[dict[str, int]] = []
    seen: set[str] = set()
    while len(formulas) < n:
        c = int(rng.integers(2, 16))
        hetero = int(rng.integers(0, 3))  # 0 CHO, 1 CHON, 2 CHOS
        o = int(rng.integers(1, min(c + 2, 9) + 1))
        dbe = int(rng.integers(0, 5))
        n_n = 1 if hetero == 1 else 0
        n_s = 1 if hetero == 2 else 0
        h = 2 * c + 2 + n_n - 2 * dbe
        if h < 2:
            continue
        counts = {"C": c, "H": h, "O": o}
        if n_n:
            counts["N"] = 1
        if n_s:
            counts["S"] = 1
        key = _hill(counts)
        if key in seen:
            continue
        seen.add(key)
        formulas.append(counts)
    return formulas


def _class(counts: dict[str, int]) -> str:
    return "CHO" + ("N" if counts.get("N") else "") + ("S" if counts.get("S") else "")


def _ion(params: CampaignParams, counts: dict[str, int]) -> tuple[str, float]:
    """The ion formula and theoretical m/z of a neutral under the mechanism."""
    ion = dict(counts)
    ion[params.adduct] = ion.get(params.adduct, 0) + 1
    sign = "-" if params.charge < 0 else "+"
    mz = _mass(ion) - params.charge * _ELECTRON
    return _hill(ion) + sign, mz


def _noise(rng: np.random.Generator, shape: tuple[int, ...], cv: float) -> np.ndarray:
    """``1 + cv * g``, g an Irwin-Hall sum of four uniforms scaled to unit
    variance: bounded at 3.46, so a cv under 0.25 never goes negative."""
    u = rng.random((4, *shape))
    g = (u[0] + u[1] + u[2] + u[3] - 2.0) * _SQRT3
    return 1.0 + cv * g


def generate(params: CampaignParams = HALIDE_NEGATIVE, seed: int = 0) -> Campaign:
    """The campaign ``params`` describe, drawn from ``seed``."""
    _validate(params)
    rng = np.random.default_rng(seed)
    J = params.n_species
    start = params.start
    end = start + params.days * DAY
    cadence = pd.Timedelta(minutes=1)
    mechanism = params.mechanism

    # Species: formulas, levels and cycles, drawn in a fixed order.
    formulas = _draw_formulas(rng, J)
    kinds = ["day", "night", "flat"]
    levels = np.empty(J)
    peak_hours: list[int | None] = []
    amplitudes = np.empty(J)
    for j in range(J):
        mantissa = int(rng.integers(1, 10))
        exponent = int(rng.integers(2, 5))
        levels[j] = float(mantissa * 10**exponent)
        kind = kinds[j % 3]
        if kind == "day":
            peak_hours.append(int(rng.integers(11, 16)))
            amplitudes[j] = float(rng.integers(2, 9))
        elif kind == "night":
            peak_hours.append(int(rng.integers(22, 28)) % 24)
            amplitudes[j] = float(rng.integers(2, 9))
        else:
            peak_hours.append(None)
            amplitudes[j] = 1.0

    # The three batch peaks the second run reads differently: the first
    # species outside every episode whose formula has a near-isobar one CH4
    # lighter for an O (CH4 vs O, 36 mDa apart) not already in the list.
    in_episodes = {j for e in params.episodes for j in e.species}
    taken = {_hill(f) for f in formulas}
    renamed: dict[int, dict[str, int]] = {}
    for j, counts in enumerate(formulas):
        if len(renamed) == params.n_renamed:
            break
        if j in in_episodes or counts["C"] < 2 or counts["H"] < 6:
            continue
        other = dict(counts, C=counts["C"] - 1, H=counts["H"] - 4, O=counts["O"] + 1)
        if _hill(other) in taken:
            continue
        taken.add(_hill(other))
        renamed[j] = other
    if len(renamed) < params.n_renamed:
        raise ValueError("not enough species to rename under the second run")

    def formula_key(counts: dict[str, int]) -> str:
        return f"formula:{_hill(counts)}|{mechanism}"

    old_keys = [formula_key(f) for f in formulas]
    new_keys = [formula_key(renamed.get(j, f)) for j, f in enumerate(formulas)]
    ion_keys = [f"ion:ti{j + 1:04d}" for j in range(params.n_targets)]
    reagent_key = "ion:ti0000" if params.reagent else None

    # Time: one sample a minute, ``file_minutes`` samples to a file; the gap
    # day keeps its files and loses its samples.
    n_minutes = params.days * 1440
    minute = np.arange(n_minutes, dtype=np.int64)
    day_of = minute // 1440
    filed = day_of != params.gap_day
    m = minute[filed]
    n = m.size
    file_of = m // params.file_minutes
    t_mid = start + pd.to_timedelta(m * 60 + 30, unit="s")
    local = t_mid.tz_convert(params.timezone)
    local_sec = (local.hour * 3600 + local.minute * 60 + local.second).to_numpy(
        dtype=np.int64
    )
    days_in = (m * 60 + 30) / 86400.0

    # The noiseless signal: level x cycle x sensitivity x episodes.
    signal = np.empty((n, J))
    for j in range(J):
        if peak_hours[j] is None:
            cycle = np.ones(n)
        else:
            d = (local_sec - peak_hours[j] * 3600 + 43200) % 86400 - 43200
            s = d / 43200.0
            shape = (1.0 - s * s) * (1.0 - s * s)
            trough = 1.0 / amplitudes[j]
            cycle = trough + (1.0 - trough) * shape
        signal[:, j] = levels[j] * cycle
    if params.reagent:
        drift = 1.0 + params.reagent_slope * days_in
        signal = signal * drift[:, None]
    for e in params.episodes:
        rows = np.asarray((t_mid >= start + e.start) & (t_mid < start + e.end))
        for j in e.species:
            signal[rows, j] = signal[rows, j] * e.factor

    intensity = signal * _noise(rng, (n, J), params.noise_cv)
    intensity[intensity < params.detection_limit] = np.nan

    reagent = None
    if params.reagent:
        reagent = (
            params.reagent_level
            * (1.0 + params.reagent_slope * days_in)
            * _noise(rng, (n,), 0.02)
        )
    step_at = start + params.tic_step_at
    step = np.where(np.asarray(t_mid >= step_at), params.tic_step_factor, 1.0)
    tic = params.tic_level * step * _noise(rng, (n,), 0.02)

    # Batches: one per UTC day, the gap day none.
    batch_days = [d for d in range(params.days) if d != params.gap_day]
    batch_ids = [f"batch-{d + 1:02d}" for d in batch_days]
    batch_index = np.full(params.days, -1, dtype=np.int64)
    batch_index[batch_days] = np.arange(len(batch_days))
    b_of = batch_index[day_of[filed]]
    runs = ["run-1", "run-2"]
    run_of_batch = np.array(
        [0 if d < params.run_change_day else 1 for d in batch_days], dtype=np.int64
    )

    # files
    n_files = n_minutes // params.file_minutes
    f_start = start + pd.to_timedelta(
        np.arange(n_files, dtype=np.int64) * params.file_minutes * 60, unit="s"
    )
    f_day = (np.arange(n_files) * params.file_minutes) // 1440
    file_ids = np.array([f"f{i + 1:05d}" for i in range(n_files)], dtype=object)
    file_names = np.array(
        [f"campaign_{t:%Y%m%d_%H%M}.raw" for t in f_start], dtype=object
    )
    files = pd.DataFrame(
        {
            "sample_file_id": pd.array(file_ids, dtype="str"),
            "datetime_utc": f_start,
            "length": np.full(n_files, params.file_minutes * 60.0),
            "instrument": pd.array([params.instrument] * n_files, dtype="str"),
            "acquisition_timezone": pd.array([params.timezone] * n_files, dtype="str"),
            "in_batch": f_day != params.gap_day,
        }
    )

    # samples
    sample_ids = np.array([f"s{i + 1:06d}" for i in range(n)], dtype=object)
    t0 = (m % params.file_minutes) * 60.0
    samples = pd.DataFrame(
        {
            "sample_item_id": pd.array(sample_ids, dtype="str"),
            "sample_batch_id": pd.Categorical.from_codes(b_of, batch_ids),
            "sample_file_id": pd.array(file_ids[file_of], dtype="str"),
            "t_mid_utc": t_mid,
            "datetime_utc": f_start[file_of],
            "t0": t0,
            "t1": t0 + 60.0,
            "tic": tic,
            "polarity": pd.array([params.polarity] * n, dtype="str"),
            "instrument": pd.array([params.instrument] * n, dtype="str"),
            "instrument_type": pd.array([params.instrument_type] * n, dtype="str"),
            "filename": pd.array(file_names[file_of], dtype="str"),
        }
    )

    # peak_series: one row per (sample, batch peak), sample-major.
    rows_sample = np.repeat(np.arange(n), J)
    rows_species = np.tile(np.arange(J), n)
    rows_run = run_of_batch[b_of][rows_sample]
    all_keys = list(dict.fromkeys(old_keys + new_keys))
    key_code = {k: i for i, k in enumerate(all_keys)}
    old_codes = np.array([key_code[k] for k in old_keys])
    new_codes = np.array([key_code[k] for k in new_keys])
    series_codes = np.where(
        rows_run == 1, new_codes[rows_species], old_codes[rows_species]
    )
    flat = intensity.ravel()
    tiers = ["assigned", "candidate"]
    tier_codes = np.where(rows_species % 10 == 9, 1, 0)
    tier_codes = np.where(np.isnan(flat), -1, tier_codes)
    bp_ids = [f"bp-{d + 1:02d}-{j + 1:02d}" for d in batch_days for j in range(J)]
    peak_series = pd.DataFrame(
        {
            "sample_item_id": pd.Categorical.from_codes(rows_sample, sample_ids),
            "series_key": pd.Categorical.from_codes(series_codes, all_keys),
            "intensity": flat,
            "tier": pd.Categorical.from_codes(tier_codes, tiers),
            "batch_peak_id": pd.Categorical.from_codes(
                b_of[rows_sample] * J + rows_species, bp_ids
            ),
            "run_id": pd.Categorical.from_codes(rows_run, runs),
        }
    )

    # ion_series: the reagent first, then the targeted species, which read
    # the same peaks as their batch peaks do.
    ion_columns = [intensity[:, j] for j in range(params.n_targets)]
    keys = list(ion_keys)
    if reagent is not None:
        ion_columns.insert(0, reagent)
        keys.insert(0, reagent_key)
    K = len(keys)
    ion_values = np.column_stack(ion_columns).ravel()
    ion_series = pd.DataFrame(
        {
            "sample_item_id": pd.Categorical.from_codes(
                np.repeat(np.arange(n), K), sample_ids
            ),
            "series_key": pd.Categorical.from_codes(np.tile(np.arange(K), n), keys),
            "intensity": ion_values,
            "match_category": np.where(np.isnan(ion_values), 0, 2).astype(np.int64),
        }
    )

    # species: one row per series key.
    rows = []
    if reagent_key:
        rows.append(_species_row(reagent_key, "reagent ion", None, params))
    for j in range(params.n_targets):
        rows.append(
            _species_row(ion_keys[j], f"compound {j + 1:02d}", formulas[j], params)
        )
    for j in range(J):
        rows.append(_species_row(old_keys[j], _hill(formulas[j]), formulas[j], params))
    for j, counts in renamed.items():
        rows.append(_species_row(new_keys[j], _hill(counts), counts, params))
    species = pd.DataFrame(rows)
    for column in (
        "series_key",
        "name",
        "formula",
        "ion_formula",
        "mechanism",
        "class",
    ):
        species[column] = species[column].astype("str")

    # The answers.
    cycles: dict[str, Cycle] = {}
    level_of: dict[str, float] = {}

    def known(key: str, j: int) -> None:
        cycles[key] = Cycle(kinds[j % 3], peak_hours[j], float(amplitudes[j]))
        level_of[key] = float(levels[j])

    for j in range(J):
        known(old_keys[j], j)
        known(new_keys[j], j)
    for j in range(params.n_targets):
        known(ion_keys[j], j)

    episodes = tuple(
        Episode(
            start=start + e.start,
            end=start + e.end,
            factor=e.factor,
            series_keys=tuple(
                [old_keys[j] for j in e.species]
                + [ion_keys[j] for j in e.species if j < params.n_targets]
            ),
        )
        for e in params.episodes
    )
    gap_start = start + params.gap_day * DAY
    truth = Truth(
        start=start,
        end=end,
        cadence=cadence,
        timezone=params.timezone,
        detection_limit=params.detection_limit,
        cycles=cycles,
        levels=level_of,
        episodes=episodes,
        gap=Gap(gap_start, gap_start + DAY, int((f_day == params.gap_day).sum())),
        tic_step=Step(
            step_at, params.tic_level, params.tic_level * params.tic_step_factor
        ),
        run_change=RunChange(
            at=start + params.run_change_day * DAY,
            before=runs[0],
            after=runs[1],
            readings=tuple((old_keys[j], new_keys[j]) for j in renamed),
        ),
        drift=(
            Drift(
                reagent_key,
                params.reagent_level,
                params.reagent_level * params.reagent_slope,
            )
            if reagent_key
            else None
        ),
    )
    tables = {
        "samples": samples,
        "files": files,
        "ion_series": ion_series,
        "peak_series": peak_series,
        "species": species,
    }
    return Campaign(params=params, seed=seed, tables=tables, truth=truth)


def _species_row(
    key: str, name: str, counts: dict[str, int] | None, params: CampaignParams
) -> dict:
    if counts is None:  # the reagent ion
        mz = _MASS["I"] + _ELECTRON
        return {
            "series_key": key,
            "name": name,
            "formula": params.reagent.rstrip("+-"),
            "ion_formula": params.reagent,
            "mechanism": "reagent",
            "class": "reagent",
            "mz": mz,
            **{f"n_{e.lower()}": 0 for e in ("C", "H", "N", "O", "S")},
            "o_c": np.nan,
            "h_c": np.nan,
            "osc": np.nan,
        }
    ion_formula, mz = _ion(params, counts)
    c, h, o = counts["C"], counts["H"], counts["O"]
    return {
        "series_key": key,
        "name": name,
        "formula": _hill(counts),
        "ion_formula": ion_formula,
        "mechanism": params.mechanism,
        "class": _class(counts),
        "mz": mz,
        **{f"n_{e.lower()}": counts.get(e, 0) for e in ("C", "H", "N", "O", "S")},
        "o_c": o / c,
        "h_c": h / c,
        "osc": 2.0 * o / c - h / c,
    }


def _validate(params: CampaignParams) -> None:
    if params.reagent not in (None, "I-"):
        raise ValueError("the only reagent ion modelled is iodide, 'I-'")
    if 1440 % params.file_minutes:
        raise ValueError("file_minutes must divide a day")
    if not 0 <= params.gap_day < params.days:
        raise ValueError("gap_day is outside the campaign")
    if not 0 < params.run_change_day < params.days:
        raise ValueError("run_change_day is outside the campaign")
    if params.noise_cv >= 0.25:
        raise ValueError("noise_cv must stay under 0.25 to keep values positive")
    gap = (params.gap_day * DAY, (params.gap_day + 1) * DAY)
    for e in params.episodes:
        if not pd.Timedelta(0) <= e.start < e.end <= params.days * DAY:
            raise ValueError("an episode is outside the campaign")
        if e.start < gap[1] and e.end > gap[0]:
            raise ValueError("an episode overlaps the gap day")
        if any(not 0 <= j < params.n_species for j in e.species):
            raise ValueError("an episode names a species that does not exist")


# --- Parquet ----------------------------------------------------------------


def parquet_bytes(df: pd.DataFrame) -> bytes:
    """``df`` as parquet, without pandas' metadata (which names its version).

    Column types survive the round trip in the arrow schema itself:
    categoricals as dictionaries, timestamps with their zone.
    """
    table = pa.Table.from_pandas(df, preserve_index=False).replace_schema_metadata(None)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def write_parquet(campaign: Campaign, directory: Path) -> dict[str, Path]:
    """Write each table as ``<name>.parquet`` under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in TABLES:
        path = directory / f"{name}.parquet"
        path.write_bytes(parquet_bytes(campaign.tables[name]))
        paths[name] = path
    return paths


def content_digest(campaign: Campaign) -> str:
    """A digest of the tables' values, column names and types.

    Unlike the parquet bytes, it does not move with the writer's version, so
    a test can pin it: a change to the generator that moves any value shows.
    """
    digest = hashlib.sha256()
    for name in TABLES:
        df = campaign.tables[name]
        digest.update(name.encode())
        for column in df.columns:
            digest.update(f"{column}:{df[column].dtype}".encode())
        rows = pd.util.hash_pandas_object(df, index=False).to_numpy()
        digest.update(rows.astype("<u8").tobytes())
    return digest.hexdigest()
