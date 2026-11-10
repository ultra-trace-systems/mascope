"""The views: a campaign store's tables as the tidy frames charts draw.

Views are the only place the package aggregates (``docs/dev/visual_digest.md``,
section 2.4): pure pandas functions from a :class:`~.store.CampaignStore`'s
tables to long frames, which every chart draws from and whose numbers a
digest's summary may cite.

- :func:`series`: each key's values over time, binned at a cadence.
- :func:`diurnal`: the hour-of-day cycle, optionally split by month, season
  or weekday and weekend.
- :func:`periods`: the distribution per day, week or month.
- :func:`coverage`: files and samples per hour by polarity and mode, the
  gaps, the files acquired but in no batch.
- :func:`composition`: the assigned formulas per period, with their class,
  element counts and ratios.
- :func:`spectrum`: a period's average spectrum on the batch-peak grid, and
  its difference to another period.
- :func:`top`: the brightest keys, the biggest changers, the new ones.

What the views share:

- **Keys** are series keys as the store spells them (``ion:<target_ion_id>``,
  ``formula:<formula>|<mechanism>``), or ``tic`` for the samples' TIC. A key
  the store does not hold is an error.
- **A sample's value of a key** is the sum of the key's values in it (two
  batch peaks can carry one formula; the SDK notebooks sum a label's peaks
  per sample the same way). A key read in a batch is NaN in the batch's
  samples where it was not matched or not detected: below detection is NaN,
  never zero, and no view fills it in.
- **Normalisation** (decision 7) is ``none``; ``tic``, each value divided by
  its sample's TIC; or a series key such as a reagent ion's ``ion:<id>``,
  each value divided by that key's value in the same sample. It is applied
  per sample, before any binning; a sample whose normaliser is NaN or 0 has
  NaN values.
- **Time** (decision 6): the store is UTC, and a view shows times in a zone,
  ``tz``: None for the store's display zone (the instrument's acquisition
  zone, unless the scope named another), ``"instrument"`` for the
  instrument's own, or an IANA name. Bins, hours of day, days, weeks and
  months are that zone's, clock changes included.
- **A window**: ``start`` and ``end`` keep the samples timed in
  ``[start, end)``; a bound without a zone is in ``tz``.
- **attrs**: every frame says what it is, for a chart to print: ``view``,
  ``tz``, ``cadence`` (what one row stands for: ``1min``, ``raw``, ``hour of
  day``, ``week``, ...), ``normalise`` and ``normalise_name`` (what the
  values are divided by, by name), ``keys``, ``start`` and ``end`` (the
  window, ISO in UTC), ``mixed_runs`` and ``run_changes`` (whether the
  window's batches read formulas with more than one ledger run, and where
  the reading changes: a run change is flagged, never silently joined; both
  empty without a formula key or a formula to normalise by) and the store's
  ``provenance``.
"""

import math
import zoneinfo
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pandas.tseries.frequencies import to_offset

from .scope import INSTRUMENT_TZ, _zone_of
from .store import CampaignStore


#: The key of the samples' TIC.
TIC = "tic"

#: The statistics :func:`series` takes for a bin's value.
STATS = ("median", "mean", "min", "max")

#: The calendar periods, as pandas names them: days, weeks from Monday,
#: months.
PERIODS = {"day": "D", "week": "W-SUN", "month": "M"}

#: What :func:`diurnal` splits by.
DIURNAL_BY = ("month", "season", "weekday")

#: What :func:`top` ranks by.
TOP_BY = ("median", "ratio", "new")

#: What :func:`composition` sums the formulas by.
COMPOSITION_BY = ("class", "n_c")

#: Month abbreviations, fixed rather than the locale's.
MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

#: The meteorological seasons, in order, by their months.
SEASONS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}

#: The species columns :func:`composition`, :func:`spectrum` and :func:`top`
#: carry beside a key.
SPECIES_COLUMNS = (
    "name",
    "formula",
    "ion_formula",
    "mechanism",
    "class",
    "mz",
    "n_c",
    "n_h",
    "n_n",
    "n_o",
    "n_s",
    "o_c",
    "h_c",
    "osc",
)

#: :func:`auto_cadence` of a series with too few samples for a spacing:
#: its span over this many bins.
_TARGET_BINS = 50

_NS_PER_MINUTE = 60 * 10**9

_NS_PER_HOUR = 60 * _NS_PER_MINUTE

_NS_PER_DAY = 24 * _NS_PER_HOUR

#: A window's ``(start, end)`` in UTC, None for an open bound.
Window = tuple[pd.Timestamp | None, pd.Timestamp | None]


# --- time --------------------------------------------------------------------


def display_zone(store: CampaignStore, tz: str | None = None) -> str:
    """The zone a view of *store* shows times in.

    :param tz: None for the store's display zone, ``"instrument"`` for the
        instrument's acquisition zone (UTC when the store does not know
        it), or an IANA name.
    :raises ValueError: When *tz* is no zone.
    """
    meta = store.meta
    if tz is None:
        tz = meta.get("display_tz") or INSTRUMENT_TZ
    if tz == INSTRUMENT_TZ:
        return meta.get("timezone") or _zone_of(store.table("files")) or "UTC"
    try:
        zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError) as e:
        raise ValueError(
            f"tz={tz!r} is no time zone: give an IANA name such as "
            "'Europe/Helsinki', 'UTC' or 'instrument'"
        ) from e
    return tz


def _bound(moment: Any, zone: str) -> pd.Timestamp | None:
    """A window's bound in UTC; one without a zone is in *zone*."""
    if moment is None:
        return None
    moment = pd.Timestamp(moment)
    if moment.tzinfo is None:
        try:
            moment = moment.tz_localize(zone, ambiguous="raise", nonexistent="raise")
        except (ValueError, OverflowError) as e:
            raise ValueError(
                f"{moment} is not one time in {zone}: the clocks change around "
                "it. Give it with its UTC offset."
            ) from e
    return moment.tz_convert("UTC")


def _window(period: Any, zone: str) -> Window:
    """A ``(start, end)`` pair, or None for no bounds."""
    if period is None:
        return None, None
    try:
        start, end = period
    except (TypeError, ValueError) as e:
        raise ValueError(f"{period!r} is no (start, end) pair") from e
    start, end = _bound(start, zone), _bound(end, zone)
    if start is not None and end is not None and end <= start:
        raise ValueError(f"{period!r} ends before it starts")
    return start, end


def _ns(times: Any) -> np.ndarray:
    """Times as nanoseconds since the epoch."""
    return pd.DatetimeIndex(times).as_unit("ns").asi8


def _times(ns: np.ndarray, zone: str) -> pd.DatetimeIndex:
    """Nanoseconds since the epoch as times in *zone*."""
    return pd.DatetimeIndex(pd.to_datetime(ns, utc=True)).as_unit("ns").tz_convert(zone)


def _localize(
    naive: pd.DatetimeIndex, zone: str, *, first: bool = True
) -> pd.DatetimeIndex:
    """Wall-clock times in *zone*; one the clocks skip moves forward, one
    they repeat is the first (the second with *first* False)."""
    return naive.tz_localize(
        zone,
        ambiguous=np.full(len(naive), first, dtype=bool),
        nonexistent="shift_forward",
    )


def _offset(cadence: Any, *, auto_raw: bool = True) -> pd.DateOffset:
    """*cadence* as a pandas offset; *auto_raw* when the caller also takes
    ``auto`` and ``raw``, for the error to offer them."""
    try:
        offset = to_offset(cadence)
    except (ValueError, TypeError) as e:
        either = "'auto', 'raw' or " if auto_raw else ""
        raise ValueError(
            f"cadence={cadence!r} is none: give {either}a pandas offset such "
            "as '10min', '1h' or '1D'"
        ) from e
    if offset.n <= 0:
        raise ValueError(f"cadence={cadence!r} is not a positive step")
    return offset


def _fixed(offset: pd.DateOffset) -> bool:
    """Whether *offset* is a fixed duration (hours, minutes, seconds), not a
    calendar step (days, weeks, months), which a clock change stretches."""
    return isinstance(offset, pd.offsets.Tick) and not isinstance(
        offset, pd.offsets.Day
    )


def _cadence_name(offset: pd.DateOffset) -> str:
    return f"{offset.n}{offset.rule_code}"


#: Calendar offsets anchored on a period's end, besides the weeks.
_PERIOD_ENDS = (
    pd.offsets.MonthEnd,
    pd.offsets.QuarterEnd,
    pd.offsets.YearEnd,
    pd.offsets.SemiMonthEnd,
)


def _ends_periods(offset: pd.DateOffset) -> bool:
    """Whether *offset*'s dates are the last days of its periods: ``W``
    (``W-SUN``) the Sundays its weeks end on, ``ME`` the months' last."""
    if isinstance(offset, pd.offsets.Week):
        return offset.weekday is not None
    return isinstance(offset, _PERIOD_ENDS)


def _edges(
    first: pd.Timestamp,
    last: pd.Timestamp,
    offset: pd.DateOffset,
    zone: str,
    *,
    clock: bool = True,
) -> np.ndarray:
    """Bin edges, ns since the epoch, from the start of the bin holding
    *first* to past *last*.

    - A fixed step of whole hours that divides a day (``1h``, ``6h``,
      ``24h``) follows the zone's clock, unless *clock* is False: its bins
      start at the same local hours each day, counted from midnight. A bin
      across a clock change is shorter or longer by the change, and both
      times of a repeated hour start one.
    - Any other fixed step, and every one without *clock*, counts elapsed
      time from the local midnight of *first*'s day, so that every bin is as
      long as the step. One that divides an hour (``1min``, ``10min``) still
      starts on the clock's times, since the clocks change by whole hours;
      one that does not (``9min``, ``90min``) would leave a bin of a few
      minutes at a change if it followed the clock.
    - A calendar step starts on the zone's days, weeks or months. One
      anchored on a period's end bins the periods that end on its dates, as
      pandas' periods do: ``W`` (``W-SUN``) weeks run Monday to Sunday, and
      ``ME``, ``QE`` and ``YE`` are the calendar months, quarters and years.
    """
    local_last = last.tz_convert(zone).tz_localize(None)
    day = first.tz_convert(zone).tz_localize(None).normalize()
    if _fixed(offset):
        step = pd.Timedelta(offset).value
        if clock and step % _NS_PER_HOUR == 0 and _NS_PER_DAY % step == 0:
            naive = pd.date_range(
                day,
                local_last.normalize() + pd.Timedelta(days=2),
                freq=pd.Timedelta(step),
                inclusive="left",
            )
            edges = np.unique(
                np.concatenate(
                    [
                        _ns(_localize(naive, zone)),
                        _ns(_localize(naive, zone, first=False)),
                    ]
                )
            )
        else:
            start = _ns(_localize(pd.DatetimeIndex([day]), zone))[0]
            start += step * ((first.value - start) // step)
            edges = np.arange(start, last.value + step + 1, step, dtype="int64")
    elif _ends_periods(offset):
        # The period holding the first day starts the day after the last
        # date before it
        naive = pd.date_range(
            offset.rollback(day - pd.Timedelta(days=1)),
            local_last + offset,
            freq=offset,
        ) + pd.Timedelta(days=1)
        edges = _ns(_localize(naive, zone))
    else:
        naive = pd.date_range(offset.rollback(day), local_last + offset, freq=offset)
        edges = _ns(_localize(naive, zone))
    return edges[max(np.searchsorted(edges, first.value, side="right") - 1, 0) :]


def auto_cadence(times: Any) -> pd.Timedelta:
    """The bin a series of *times* is drawn at when its cadence is ``auto``.

    Peaky's rule (``peaky.batch.timeseries.auto_bin_minutes``): the native
    median spacing of the samples, rounded **up** to whole minutes and at
    least one, so that a bin is never narrower than the spacing. A narrower
    one beats against slightly irregular sample times and now and then
    catches none, a regular comb of empty bins; a 73 s spacing bins at two
    minutes, not one. With fewer than three samples, the span over fifty
    bins (thirty minutes over fifty without a span).

    :param times: The samples' times, with a zone or in UTC; duplicates
        count once.
    """
    stamps = pd.Series(pd.to_datetime(pd.Series(times), utc=True)).dropna()
    return _auto(np.unique(_ns(stamps)))


def _auto(t: np.ndarray) -> pd.Timedelta:
    """:func:`auto_cadence` of sorted, distinct ns times."""
    if len(t) >= 3:
        spacing = float(np.median(np.diff(t))) / _NS_PER_MINUTE
        if spacing > 0:
            return pd.Timedelta(minutes=max(1, math.ceil(spacing)))
    span = (t[-1] - t[0]) / _NS_PER_MINUTE if len(t) >= 2 else 30.0
    return pd.Timedelta(minutes=max(1, round(span / _TARGET_BINS)))


def _period_codes(
    ns: np.ndarray, zone: str, period: str
) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """Each time's calendar period in *zone*, as a code counting from the
    first, and every period's start from the first to the last.

    ``all`` is one period, which starts at the first time.
    """
    if not len(ns):
        return np.array([], dtype="int64"), pd.DatetimeIndex([], tz=zone)
    if period == "all":
        return np.zeros(len(ns), dtype="int64"), _times(ns[:1], zone)
    local = _times(ns, zone).tz_localize(None).to_period(PERIODS[period])
    ordinals = local.asi8
    first = ordinals.min()
    starts = pd.period_range(
        local[ordinals.argmin()], local[ordinals.argmax()], freq=PERIODS[period]
    ).start_time
    return ordinals - first, _localize(starts, zone).as_unit("ns")


# --- reading -----------------------------------------------------------------


def _positions(index: pd.Index, column: pd.Series) -> np.ndarray:
    """Each value of *column*'s position in *index*; -1 where it is not."""
    if isinstance(column.dtype, pd.CategoricalDtype):
        where = index.get_indexer(column.cat.categories.astype(str))
        codes = column.cat.codes.to_numpy()
        return np.where(codes >= 0, where[codes], -1)
    return index.get_indexer(column.astype(str))


def _check_keys(store: CampaignStore, keys: Any, *, tic: bool = True) -> list[str]:
    """*keys* checked against the store, duplicates dropped, order kept."""
    if isinstance(keys, str):
        keys = [keys]
    keys = list(dict.fromkeys(str(k) for k in (() if keys is None else keys)))
    if not keys:
        raise ValueError("No keys: name at least one series key")
    held = set(store.table("species")["series_key"].astype(str))
    for key in keys:
        if key == TIC and tic:
            continue
        if key not in held:
            raise ValueError(
                f"The store holds no series {key!r}: a key is "
                "ion:<target_ion_id> or formula:<formula>|<mechanism> as "
                "store.table('species') lists them" + (", or tic" if tic else "")
            )
    return keys


def _check_normalise(normalise: Any) -> str:
    if not isinstance(normalise, str) or not (
        normalise in ("none", TIC) or normalise.startswith(("ion:", "formula:"))
    ):
        raise ValueError(
            f"normalise={normalise!r} is none of 'none', 'tic' or a series key "
            "such as 'ion:<target_ion_id>'"
        )
    return normalise


def _key_rows(
    store: CampaignStore, keys: list[str], samples: pd.DataFrame, index: pd.Index
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The store's rows of *keys*: sample positions in *index*, key
    positions in *keys* and values. Rows of other samples are dropped."""
    key_index = pd.Index(keys)
    parts = []
    for table, prefix in (("ion_series", "ion:"), ("peak_series", "formula:")):
        wanted = [k for k in keys if k.startswith(prefix)]
        if not wanted:
            continue
        rows = store.table(table, keys=wanted)
        pos = _positions(index, rows["sample_item_id"])
        key = _positions(key_index, rows["series_key"])
        kept = (pos >= 0) & (key >= 0)
        parts.append(
            (pos[kept], key[kept], rows["intensity"].to_numpy("float64")[kept])
        )
    if TIC in keys:
        n = len(samples)
        parts.append(
            (
                np.arange(n),
                np.full(n, keys.index(TIC)),
                samples["tic"].to_numpy("float64"),
            )
        )
    if not parts:
        return np.array([], "int64"), np.array([], "int64"), np.array([], "float64")
    pos, key, value = (np.concatenate(column) for column in zip(*parts))
    return pos.astype("int64"), key.astype("int64"), value


def _per_sample(
    pos: np.ndarray, key: np.ndarray, value: np.ndarray, n_keys: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One value per (sample, key): the sum of its values, NaN when they
    all are."""
    if not len(pos):
        return pos, key, value
    summed = pd.Series(value).groupby(pos * n_keys + key, sort=True).sum(min_count=1)
    codes = summed.index.to_numpy("int64")
    return codes // n_keys, codes % n_keys, summed.to_numpy("float64")


def _normaliser_name(store: CampaignStore, normalise: str) -> str | None:
    """What the values are divided by, by name; None for ``none``.

    :raises ValueError: When *normalise* is a key the store does not hold.
    """
    if normalise == "none":
        return None
    if normalise == TIC:
        return "TIC"
    keys = _check_keys(store, [normalise], tic=False)
    named = store.table("species", keys=keys)["name"].dropna()
    return str(named.iloc[0]) if not named.empty else normalise


def _normaliser(
    store: CampaignStore, normalise: str, samples: pd.DataFrame, index: pd.Index
) -> tuple[np.ndarray | None, str | None]:
    """What each sample's values are divided by, and its name; None for
    ``none``."""
    name = _normaliser_name(store, normalise)
    if name is None:
        return None, None
    if normalise == TIC:
        divisor = samples["tic"].to_numpy("float64").copy()
    else:
        pos, _, value = _per_sample(*_key_rows(store, [normalise], samples, index), 1)
        divisor = np.full(len(index), np.nan)
        divisor[pos] = value
    divisor[divisor == 0] = np.nan
    return divisor, name


def _run_attrs(
    store: CampaignStore, keys: list[str], windows: Sequence[Window]
) -> dict[str, Any]:
    """Whether the batches of *windows* (``(start, end)`` pairs, None for
    open) read formulas with more than one ledger reading, and where it
    changes from the earliest start to the latest end; neither when no key
    of *keys* is a formula's."""
    none = {"mixed_runs": False, "run_changes": []}
    if not any(k.startswith("formula:") for k in keys):
        return none
    status = store.status()
    batches = status.batches
    if batches.empty:
        return none
    inside = pd.Series(False, index=batches.index)
    for start, end in windows:
        held = pd.Series(True, index=batches.index)
        if start is not None:
            held &= batches["last_datetime_utc"] >= start
        if end is not None:
            held &= batches["first_datetime_utc"] < end
        inside |= held
    starts, ends = zip(*windows)
    start = None if None in starts else min(starts)
    end = None if None in ends else max(ends)
    readings = set(batches.loc[inside, "reading"].dropna())
    changes = [
        change.to_dict()
        for change in status.run_changes
        if (start is None or change.at >= start) and (end is None or change.at < end)
    ]
    return {"mixed_runs": len(readings) > 1, "run_changes": changes}


def _attrs(
    store: CampaignStore,
    view: str,
    zone: str,
    *,
    keys: Sequence[str] = (),
    normalise: str = "none",
    normalise_name: str | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    windows: Sequence[Window] | None = None,
) -> dict[str, Any]:
    """What every frame of a view says (see the module); the run flags are
    over *keys* and a formula *normalise*, and over *windows* when given,
    else over the one from *start* to *end*."""
    return {
        "view": view,
        "tz": zone,
        "normalise": normalise,
        "normalise_name": normalise_name,
        "keys": list(keys),
        "start": None if start is None else start.isoformat(),
        "end": None if end is None else end.isoformat(),
        # A formula normaliser divides every value by what a run reads
        **_run_attrs(store, [*keys, normalise], windows or [(start, end)]),
        "provenance": store.meta.get("provenance"),
    }


@dataclass(frozen=True)
class _Values:
    """A window's samples, and one value per (sample, key) the store holds.

    :ivar zone: The display zone.
    :ivar keys: The keys, in the order asked.
    :ivar samples: The window's samples, oldest first.
    :ivar t: Their times, ns since the epoch.
    :ivar batch: Each sample's batch, as a code.
    :ivar present: ``[batch, key]``: whether the batch reads the key (holds
        a row of it); its samples without a value of it are NaN.
    :ivar pos: Per (sample, key) the store holds a row for: the sample's
        position,
    :ivar key: the key's,
    :ivar value: and the value, normalised.
    :ivar attrs: What every frame of these values says.
    """

    zone: str
    keys: list[str]
    samples: pd.DataFrame
    t: np.ndarray
    batch: np.ndarray
    present: np.ndarray
    pos: np.ndarray
    key: np.ndarray
    value: np.ndarray
    attrs: dict[str, Any]

    def reads(self, k: int) -> np.ndarray:
        """Which samples are of a batch that reads key *k*."""
        return self.present[self.batch, k]

    @property
    def reads_any(self) -> np.ndarray:
        """Which samples are of a batch that reads any of the keys."""
        return self.present[self.batch].any(axis=1)

    def matrix(self) -> np.ndarray:
        """The values as ``[key, sample]``, NaN where there is none."""
        out = np.full((len(self.keys), len(self.samples)), np.nan)
        out[self.key, self.pos] = self.value
        return out


def _read(
    store: CampaignStore,
    keys: Any,
    *,
    normalise: str,
    tz: str | None,
    start: Any,
    end: Any,
    view: str,
    window: Window | None = None,
) -> _Values:
    """The values a view draws, and the attrs its frames carry."""
    normalise = _check_normalise(normalise)
    zone = display_zone(store, tz)
    lo, hi = window if window is not None else (_bound(start, zone), _bound(end, zone))
    keys = _check_keys(store, keys)

    samples = store.table("samples")
    when = samples["t_mid_utc"]
    kept = when.notna()
    if lo is not None:
        kept &= when >= lo
    if hi is not None:
        kept &= when < hi
    samples = (
        samples[kept]
        .sort_values(["t_mid_utc", "sample_item_id"], kind="stable")
        .reset_index(drop=True)
    )
    index = pd.Index(samples["sample_item_id"].astype(str))
    batches = pd.Categorical(samples["sample_batch_id"].astype(str))
    batch = np.asarray(batches.codes, dtype="int64")

    pos, key, value = _key_rows(store, keys, samples, index)
    present = np.zeros((max(len(batches.categories), 1), len(keys)), dtype=bool)
    present[batch[pos], key] = True
    pos, key, value = _per_sample(pos, key, value, len(keys))
    divisor, name = _normaliser(store, normalise, samples, index)
    if divisor is not None:
        value = value / divisor[pos]

    return _Values(
        zone=zone,
        keys=keys,
        samples=samples,
        t=_ns(samples["t_mid_utc"]),
        batch=batch,
        present=present,
        pos=pos,
        key=key,
        value=value,
        attrs=_attrs(
            store,
            view,
            zone,
            keys=keys,
            normalise=normalise,
            normalise_name=name,
            start=lo,
            end=hi,
        ),
    )


def _stamp(frame: pd.DataFrame, attrs: dict[str, Any]) -> pd.DataFrame:
    frame.attrs.update(attrs)
    return frame


def _key_column(keys: list[str], codes: np.ndarray) -> pd.Categorical:
    return pd.Categorical.from_codes(np.asarray(codes, "int64"), categories=keys)


def _empty(columns: dict[str, str], zone: str) -> pd.DataFrame:
    """A frame of no rows with *columns* of their types (``time`` for a
    time in *zone*)."""
    return pd.DataFrame(
        {
            name: pd.DatetimeIndex([], tz=zone).as_unit("ns")
            if kind == "time"
            else pd.Series([], dtype=kind)
            for name, kind in columns.items()
        }
    )


def _grouped(code: np.ndarray, value: np.ndarray) -> Any:
    return pd.Series(value).groupby(code, sort=True)


def _fill(
    size: int, at: np.ndarray, found: pd.Series, fill: float = np.nan
) -> np.ndarray:
    """An array of *size*, *found* at *at* and *fill* elsewhere."""
    out = np.full(size, fill, dtype="float64" if np.isnan(fill) else "int64")
    out[at] = found.to_numpy()
    return out


# --- series ------------------------------------------------------------------


def _check_band(band: Any) -> tuple[float, float] | None:
    if band is None:
        return None
    try:
        lo, hi = (float(b) for b in band)
    except (TypeError, ValueError) as e:
        raise ValueError(f"band={band!r} is no pair of percentiles") from e
    if not 0 <= lo <= hi <= 100:
        raise ValueError(f"band={band!r}: percentiles go from 0 to 100, low first")
    return lo, hi


def _series_cadence(values: _Values) -> pd.Timedelta:
    """The auto cadence of all the keys: the widest of their own, so that
    no key's bins comb. Keys read in the same batches share theirs."""
    widest = pd.Timedelta(minutes=1)
    seen: dict[bytes, pd.Timedelta] = {}
    for k in range(len(values.keys)):
        pattern = values.present[:, k].tobytes()
        if pattern not in seen:
            reads = values.reads(k)
            seen[pattern] = _auto(np.unique(values.t[reads])) if reads.any() else widest
        widest = max(widest, seen[pattern])
    return widest


_SERIES_COLUMNS = {
    "t": "time",
    "key": "object",
    "value": "float64",
    "lo": "float64",
    "hi": "float64",
    "n": "int64",
    "n_samples": "int64",
}


def series(
    store: CampaignStore,
    keys: Any,
    *,
    cadence: str = "auto",
    normalise: str = "none",
    stat: str = "median",
    band: tuple[float, float] | None = (10, 90),
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> pd.DataFrame:
    """Each key's values over time, binned at a cadence.

    :param keys: The series keys (``tic`` for the TIC); a string is one.
    :param cadence: ``auto`` (the widest of the keys' :func:`auto_cadence`,
        each over the samples of the batches reading it), ``raw`` (a row
        per sample), or a pandas offset (``10min``, ``1h``, ``1D``, ``W``,
        ``MS``). A step of whole hours that divides a day (``1h``, ``6h``)
        starts its bins at the same local hours every day, from midnight,
        so a bin across a clock change is an hour shorter or longer. Any
        other fixed step, and the auto cadence always, counts from the
        first day's local midnight in bins of one length, which a step
        that divides an hour (``10min``) starts on the clock's times.
        ``W`` weeks run Monday to Sunday, as in :func:`periods`, and
        ``ME``, ``QE`` and ``YE`` are calendar months, quarters and years,
        as ``MS``, ``QS`` and ``YS`` are.
    :param normalise: ``none``, ``tic`` or a series key.
    :param stat: A bin's ``value``: one of :data:`STATS` of its values.
    :param band: The percentiles of a bin's values that ``lo`` and ``hi``
        are; None for none.
    :param tz: The zone the bins are in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    :return: A long frame, key by key, oldest first: ``t`` (the bin's start
        in *tz*), ``key``, ``value``, ``lo``, ``hi``, ``n`` (the values in
        the bin) and ``n_samples`` (the samples in it of the batches that
        read the key). Every bin from the first sample's to the last's is a
        row for every key: one without a value, for want of samples or of a
        detection, is NaN, never interpolated. At ``raw``, ``t`` is the
        sample's time, every sample of a batch reading the key is a row
        (NaN when the key has no value in it), ``lo`` and ``hi`` are NaN,
        and where the auto cadence would leave a bin without samples a NaN
        row breaks the line as a bin would. ``attrs`` adds ``cadence``
        (``1min``, ``1h``, ``raw``, ...), ``auto`` (whether the auto rule
        chose it), ``stat`` and ``band``.
    """
    if stat not in STATS:
        raise ValueError(f"stat={stat!r} is none of {', '.join(STATS)}")
    band = _check_band(band)
    offset = None if cadence in ("auto", "raw") else _offset(cadence)
    values = _read(
        store, keys, normalise=normalise, tz=tz, start=start, end=end, view="series"
    )
    attrs = dict(values.attrs, stat=stat, band=band, auto=cadence == "auto")
    reads_any = values.reads_any
    if not reads_any.any():
        name = "raw" if cadence == "raw" else _cadence_name(offset or to_offset("1min"))
        return _stamp(_empty(_SERIES_COLUMNS, values.zone), dict(attrs, cadence=name))
    if cadence == "raw":
        return _stamp(_raw(values), dict(attrs, cadence="raw", band=None))
    if offset is None:
        offset = to_offset(_series_cadence(values))
    attrs["cadence"] = _cadence_name(offset)

    t = values.t
    edges = _edges(
        pd.Timestamp(t[reads_any].min(), tz="UTC"),
        pd.Timestamp(t[reads_any].max(), tz="UTC"),
        offset,
        values.zone,
        # A bin the clock shortens could fall between samples
        clock=cadence != "auto",
    )
    sample_bin = np.searchsorted(edges, t, side="right") - 1
    first = sample_bin[reads_any].min()
    n_bins = sample_bin[reads_any].max() - first + 1
    n_keys = len(values.keys)
    size = n_keys * n_bins
    sample_bin = sample_bin - first

    n_samples = np.concatenate(
        [
            np.bincount(sample_bin[values.reads(k)], minlength=n_bins)[:n_bins]
            for k in range(n_keys)
        ]
    )
    grouped = _grouped(values.key * n_bins + sample_bin[values.pos], values.value)
    at = grouped.size().index.to_numpy("int64")
    frame = pd.DataFrame(
        {
            "t": _times(np.tile(edges[first : first + n_bins], n_keys), values.zone),
            "key": _key_column(values.keys, np.repeat(np.arange(n_keys), n_bins)),
            "value": _fill(size, at, grouped.agg(stat)),
            "lo": _fill(size, at, grouped.quantile(band[0] / 100)) if band else np.nan,
            "hi": _fill(size, at, grouped.quantile(band[1] / 100)) if band else np.nan,
            "n": _fill(size, at, grouped.count(), 0),
            "n_samples": n_samples.astype("int64"),
        }
    )
    return _stamp(frame, attrs)


def _raw(values: _Values) -> pd.DataFrame:
    """:func:`series` at ``raw``."""
    matrix = values.matrix()
    step = _series_cadence(values)
    frames = []
    for k in range(len(values.keys)):
        reads = np.flatnonzero(values.reads(k))
        if not len(reads):
            continue
        t = values.t[reads]
        edges = _edges(
            pd.Timestamp(t[0], tz="UTC"),
            pd.Timestamp(t[-1], tz="UTC"),
            to_offset(step),
            values.zone,
            clock=False,
        )
        bins = np.searchsorted(edges, t, side="right") - 1
        empty = np.flatnonzero(np.diff(bins) > 1)
        breaks = edges[bins[empty] + 1]
        frames.append(
            pd.DataFrame(
                {
                    "t": np.concatenate([t, breaks]),
                    "key": k,
                    "value": np.concatenate(
                        [matrix[k, reads], np.full(len(breaks), np.nan)]
                    ),
                    "n_samples": np.concatenate(
                        [np.ones(len(reads), "int64"), np.zeros(len(breaks), "int64")]
                    ),
                }
            ).sort_values("t", kind="stable")
        )
    rows = pd.concat(frames, ignore_index=True)
    return pd.DataFrame(
        {
            "t": _times(rows["t"].to_numpy("int64"), values.zone),
            "key": _key_column(values.keys, rows["key"].to_numpy()),
            "value": rows["value"].to_numpy(),
            "lo": np.nan,
            "hi": np.nan,
            "n": rows["value"].notna().to_numpy().astype("int64"),
            "n_samples": rows["n_samples"].to_numpy(),
        }
    )


# --- diurnal -----------------------------------------------------------------


def _groups(local: pd.DatetimeIndex, by: str | None) -> tuple[np.ndarray, list[str]]:
    """Each time's group under *by*, as a code, and the groups' labels in
    calendar order: only the groups that occur."""
    if by is None:
        return np.zeros(len(local), dtype="int64"), [None]
    if by == "month":
        labels = list(MONTHS)
        code = local.month.to_numpy() - 1
    elif by == "season":
        labels = list(SEASONS)
        of_month = {m: i for i, months in enumerate(SEASONS.values()) for m in months}
        code = np.array([of_month[m] for m in local.month], dtype="int64")
    else:
        labels = ["weekday", "weekend"]
        code = (local.day_of_week.to_numpy() >= 5).astype("int64")
    occur = np.unique(code)
    remap = np.full(len(labels), -1)
    remap[occur] = np.arange(len(occur))
    return remap[code], [labels[i] for i in occur]


def diurnal(
    store: CampaignStore,
    keys: Any,
    *,
    by: str | None = None,
    normalise: str = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> pd.DataFrame:
    """The hour-of-day cycle of each key: the median and quartiles of its
    sample values by the hour they were taken at, in *tz*.

    :param keys: The series keys.
    :param by: Split by ``month`` (``Jan`` to ``Dec``; the same month of two
        years together), ``season`` (``DJF``, ``MAM``, ``JJA``, ``SON``) or
        ``weekday`` (``weekday`` and ``weekend``, Saturday and Sunday); None
        for no split.
    :param normalise: ``none``, ``tic`` or a series key.
    :param tz: The zone whose hours they are.
    :return: One row per group (when *by* splits), key and hour 0 to 23:
        ``group`` (with *by*), ``key``, ``hour``, ``value`` (the median),
        ``lo`` and ``hi`` (the 25th and 75th percentiles) and ``n``. An
        hour without a value is NaN. ``attrs`` adds ``by``.
    """
    if by is not None and by not in DIURNAL_BY:
        raise ValueError(f"by={by!r} is none of {', '.join(DIURNAL_BY)}")
    values = _read(
        store, keys, normalise=normalise, tz=tz, start=start, end=end, view="diurnal"
    )
    attrs = dict(values.attrs, cadence="hour of day", by=by)
    reads_any = values.reads_any
    if not reads_any.any():
        columns = {"group": "object"} if by else {}
        columns.update(key="object", hour="int64", value="float64", lo="float64")
        columns.update(hi="float64", n="int64")
        frame = _empty(columns, values.zone)
        return _stamp(frame, attrs)

    local = _times(values.t, values.zone)
    hour = local.hour.to_numpy().astype("int64")
    group = np.full(len(local), -1, dtype="int64")
    group[reads_any], labels = _groups(local[reads_any], by)
    n_keys, n_groups = len(values.keys), len(labels)
    size = n_groups * n_keys * 24
    grouped = _grouped(
        (group[values.pos] * n_keys + values.key) * 24 + hour[values.pos], values.value
    )
    at = grouped.size().index.to_numpy("int64")
    frame = pd.DataFrame(
        {
            "group": np.repeat(np.array(labels, dtype=object), n_keys * 24),
            "key": _key_column(
                values.keys, np.tile(np.repeat(np.arange(n_keys), 24), n_groups)
            ),
            "hour": np.tile(np.arange(24), n_groups * n_keys),
            "value": _fill(size, at, grouped.median()),
            "lo": _fill(size, at, grouped.quantile(0.25)),
            "hi": _fill(size, at, grouped.quantile(0.75)),
            "n": _fill(size, at, grouped.count(), 0),
        }
    )
    if by is None:
        frame = frame.drop(columns="group")
    return _stamp(frame, attrs)


# --- periods -----------------------------------------------------------------


#: The percentiles :func:`periods` gives, by column.
_PERIOD_QUANTILES = {"p5": 0.05, "p25": 0.25, "median": 0.5, "p75": 0.75, "p95": 0.95}


def periods(
    store: CampaignStore,
    keys: Any,
    period: str,
    *,
    normalise: str = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> pd.DataFrame:
    """Each key's distribution per calendar period, for a box per period.

    :param keys: The series keys.
    :param period: ``day``, ``week`` (Monday to Sunday) or ``month``, in
        *tz*.
    :param normalise: ``none``, ``tic`` or a series key.
    :return: One row per period and key, key by key, from the first
        sample's period to the last's: ``period`` (its start in *tz*),
        ``key``, ``n``, ``mean``, ``min``, ``p5``, ``p25``, ``median``,
        ``p75``, ``p95`` and ``max`` of the sample values in it; NaN, and
        ``n`` 0, in a period without one.
    """
    if period not in PERIODS:
        raise ValueError(f"period={period!r} is none of {', '.join(PERIODS)}")
    values = _read(
        store, keys, normalise=normalise, tz=tz, start=start, end=end, view="periods"
    )
    attrs = dict(values.attrs, cadence=period, period=period)
    stats = ["mean", "min", *_PERIOD_QUANTILES, "max"]
    reads_any = values.reads_any
    if not reads_any.any():
        columns = {"period": "time", "key": "object", "n": "int64"}
        columns.update({stat: "float64" for stat in stats})
        return _stamp(_empty(columns, values.zone), attrs)

    code = np.full(len(values.t), -1, dtype="int64")
    code[reads_any], starts = _period_codes(values.t[reads_any], values.zone, period)
    n_keys, n_periods = len(values.keys), len(starts)
    size = n_keys * n_periods
    grouped = _grouped(values.key * n_periods + code[values.pos], values.value)
    at = grouped.size().index.to_numpy("int64")
    frame = {
        "period": _times(np.tile(starts.asi8, n_keys), values.zone),
        "key": _key_column(values.keys, np.repeat(np.arange(n_keys), n_periods)),
        "n": _fill(size, at, grouped.count(), 0),
        "mean": _fill(size, at, grouped.mean()),
        "min": _fill(size, at, grouped.min()),
    }
    for column, q in _PERIOD_QUANTILES.items():
        frame[column] = _fill(size, at, grouped.quantile(q))
    frame["max"] = _fill(size, at, grouped.max())
    return _stamp(pd.DataFrame(frame), attrs)


# --- coverage ----------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """When the instrument acquired, and what of it a batch holds.

    :ivar bins: One row per bin and (polarity, mode): ``t`` (the bin's
        start), ``polarity``, ``mode`` (the ionization mode's ID),
        ``mode_name`` (its name, as the store's newest batch of it records
        it; None when the server did not name it), ``n_files`` (files that started in it), ``n_unfiled`` (those of
        them no batch of the scope holds) and ``n_samples``. A file counts
        under each (polarity, mode) its samples in the store have, so a
        polarity-switching file counts under ``+`` and ``-``. A file no
        sample of the store comes from has mode None, and the polarities
        its listing gives: both of a switching file's (``+-``), or the
        scope's alone when the scope names one.
    :ivar gaps: One row per stretch of at least ``min_gap`` in which no
        sample was acquired, from the first file or sample to the last:
        ``start``, ``end``, ``duration``, ``n_files`` (files acquired over
        some of it) and ``n_unfiled`` (those no batch holds: acquired, but
        filed nowhere).
    :ivar n_files: Files acquired in the window.
    :ivar n_unfiled: Those no batch of the scope holds.
    :ivar n_samples: Samples in the window.
    """

    bins: pd.DataFrame
    gaps: pd.DataFrame
    n_files: int
    n_unfiled: int
    n_samples: int


def coverage(
    store: CampaignStore,
    *,
    cadence: str = "1h",
    min_gap: str = "1h",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Coverage:
    """Files and samples over time by polarity and mode, the gaps, and the
    files acquired but filed into no batch.

    A file's time is its start; a sample acquires from its file's start
    plus ``t0`` to plus ``t1``, and is timed at the middle. A file's
    polarity and mode are those of its samples in the store, inside the
    window or not (:attr:`Coverage.bins`).

    :param cadence: The bins, a pandas offset (an hour by default).
    :param min_gap: The shortest stretch without a sample that is a gap.
    :param tz: The zone the bins are in.
    :param start: The window's start, if any: files that started and
        samples timed in ``[start, end)``.
    :param end: The window's end, if any.
    """
    offset = _offset(cadence, auto_raw=False)
    shortest = pd.Timedelta(min_gap)
    zone = display_zone(store, tz)
    lo, hi = _bound(start, zone), _bound(end, zone)
    attrs = dict(
        _attrs(store, "coverage", zone, start=lo, end=hi),
        cadence=_cadence_name(offset),
        min_gap=str(shortest),
    )

    def inside(times: pd.Series) -> pd.Series:
        kept = times.notna()
        if lo is not None:
            kept &= times >= lo
        if hi is not None:
            kept &= times < hi
        return kept

    files = store.table("files")
    files = files[inside(files["datetime_utc"])].reset_index(drop=True)
    every_sample = store.table("samples")
    samples = every_sample[inside(every_sample["t_mid_utc"])].reset_index(drop=True)
    unfiled = ~files["in_batch"].fillna(False).astype(bool)
    scope_polarity = (store.meta.get("scope") or {}).get("polarity")
    of_file, file_polarity, file_mode = _file_groups(
        files, every_sample, scope_polarity
    )

    file_ns = _ns(files["datetime_utc"])
    sample_ns = _ns(samples["t_mid_utc"])
    gaps = _gaps(files, samples, unfiled, shortest, zone)
    gaps.attrs.update(attrs)
    summary = {
        "n_files": len(files),
        "n_unfiled": int(unfiled.sum()),
        "n_samples": len(samples),
    }
    every = np.concatenate([file_ns[of_file], sample_ns])
    if not len(every):
        columns = {"t": "time", "polarity": "object", "mode": "object"}
        columns.update(mode_name="object")
        columns.update(n_files="int64", n_unfiled="int64", n_samples="int64")
        return Coverage(_stamp(_empty(columns, zone), attrs), gaps, **summary)

    pairs = list(zip(file_polarity, file_mode))
    pairs += zip(
        map(_label, samples["polarity"].astype(object)),
        map(_label, samples["ionization_mode_id"].astype(object)),
    )
    labels = sorted(set(pairs), key=lambda g: tuple((v is None, v or "") for v in g))
    group_of = {g: i for i, g in enumerate(labels)}
    name_of = _mode_names(every_sample)
    group = np.array([group_of[g] for g in pairs], dtype="int64")
    edges = _edges(
        pd.Timestamp(every.min(), tz="UTC"),
        pd.Timestamp(every.max(), tz="UTC"),
        offset,
        zone,
    )
    bins = np.searchsorted(edges, every, side="right") - 1
    n_bins = bins.max() + 1
    n_groups = len(labels)
    code = group * n_bins + bins
    n_file = len(of_file)
    counts = {
        "n_files": np.bincount(code[:n_file], minlength=n_groups * n_bins),
        "n_unfiled": np.bincount(
            code[:n_file][unfiled.to_numpy()[of_file]], minlength=n_groups * n_bins
        ),
        "n_samples": np.bincount(code[n_file:], minlength=n_groups * n_bins),
    }
    frame = pd.DataFrame(
        {
            "t": _times(np.tile(edges[:n_bins], n_groups), zone),
            # Object columns, so that a file of no mode reads None
            "polarity": pd.Series(
                np.repeat([g[0] for g in labels], n_bins), dtype=object
            ),
            "mode": pd.Series(np.repeat([g[1] for g in labels], n_bins), dtype=object),
            "mode_name": pd.Series(
                np.repeat([name_of.get(g[1]) for g in labels], n_bins), dtype=object
            ),
            **{name: count.astype("int64") for name, count in counts.items()},
        }
    )
    return Coverage(_stamp(frame, attrs), gaps, **summary)


def _label(value: Any) -> str | None:
    return None if value is None or pd.isna(value) else str(value)


def _mode_names(samples: pd.DataFrame) -> dict[str, str]:
    """The modes' names by ID, as *samples* record them: the store reads its
    batches oldest first, so a mode renamed between two batches' fetches
    takes the newest batch's name."""
    named = samples[["ionization_mode_id", "ionization_mode_name"]].astype(object)
    named = named[
        named["ionization_mode_id"].notna() & named["ionization_mode_name"].notna()
    ]
    return dict(
        zip(
            named["ionization_mode_id"].map(str), named["ionization_mode_name"].map(str)
        )
    )


def _file_groups(
    files: pd.DataFrame, samples: pd.DataFrame, polarity: str | None
) -> tuple[np.ndarray, list[str | None], list[str | None]]:
    """The (polarity, mode) pairs :func:`coverage` counts each file under
    (:attr:`Coverage.bins`), one row per pair.

    :param samples: The store's samples, all of them.
    :param polarity: The scope's polarity, if it names one.
    :return: Each row's file, as its position in *files*, its polarity and
        its mode, file by file.
    """
    position = pd.Series(
        np.arange(len(files)), index=files["sample_file_id"].astype(str).to_numpy()
    )
    own = (
        samples[["sample_file_id", "polarity", "ionization_mode_id"]]
        .astype(object)
        .drop_duplicates()
    )
    at = own["sample_file_id"].astype(str).map(position)
    kept = at.notna().to_numpy()
    rows = set(
        zip(
            at[kept].astype("int64"),
            map(_label, own.loc[kept, "polarity"]),
            map(_label, own.loc[kept, "ionization_mode_id"]),
        )
    )
    sampled = {row[0] for row in rows}
    for file, listed in enumerate(files["polarity"].astype(object)):
        if file in sampled:
            continue
        # "+-" or "-+": a polarity-switching file
        names = sorted(set(listed) & {"+", "-"}) if isinstance(listed, str) else []
        if polarity in names:
            names = [polarity]
        rows.update((file, name, None) for name in names or [None])
    ordered = sorted(rows, key=lambda row: tuple(map(str, row)))
    return (
        np.array([r[0] for r in ordered], dtype="int64"),
        [r[1] for r in ordered],
        [r[2] for r in ordered],
    )


def _gaps(
    files: pd.DataFrame,
    samples: pd.DataFrame,
    unfiled: pd.Series,
    shortest: pd.Timedelta,
    zone: str,
) -> pd.DataFrame:
    """:attr:`Coverage.gaps`."""
    start = _ns(samples["datetime_utc"])
    begin = start + np.nan_to_num(samples["t0"].to_numpy("float64") * 1e9).astype(
        "int64"
    )
    finish = start + np.nan_to_num(samples["t1"].to_numpy("float64") * 1e9).astype(
        "int64"
    )
    # A sample without a window acquired at its time
    timed = _ns(samples["t_mid_utc"])
    windowless = samples["t0"].isna().to_numpy() | samples["t1"].isna().to_numpy()
    begin = np.where(windowless, timed, begin)
    finish = np.where(windowless, timed, np.maximum(begin, finish))
    file_start = _ns(files["datetime_utc"])
    length = np.nan_to_num(files["length"].to_numpy("float64") * 1e9).astype("int64")
    file_end = file_start + np.maximum(length, 0)

    spans = []
    if len(begin):
        order = np.argsort(begin, kind="stable")
        begin, finish = begin[order], np.maximum.accumulate(finish[order])
        spans = list(zip(finish[:-1], begin[1:]))
        if len(file_start):
            spans = [(file_start.min(), begin[0]), *spans, (finish[-1], file_end.max())]
    elif len(file_start):
        spans = [(file_start.min(), file_end.max())]
    rows = []
    for gap_start, gap_end in spans:
        if gap_end - gap_start < shortest.value or gap_end <= gap_start:
            continue
        over = (file_start < gap_end) & (
            (file_end > gap_start)
            | ((file_end == file_start) & (file_start >= gap_start))
        )
        rows.append(
            {
                "start": gap_start,
                "end": gap_end,
                "n_files": int(over.sum()),
                "n_unfiled": int((over & unfiled.to_numpy()).sum()),
            }
        )
    frame = pd.DataFrame(rows, columns=["start", "end", "n_files", "n_unfiled"])
    frame = pd.DataFrame(
        {
            "start": _times(frame["start"].to_numpy("int64"), zone),
            "end": _times(frame["end"].to_numpy("int64"), zone),
            "duration": pd.to_timedelta(
                frame["end"].to_numpy("int64") - frame["start"].to_numpy("int64"),
                unit="ns",
            ),
            "n_files": frame["n_files"].to_numpy("int64"),
            "n_unfiled": frame["n_unfiled"].to_numpy("int64"),
        }
    )
    return frame


# --- composition, spectrum, top ----------------------------------------------


def _formula_keys(store: CampaignStore) -> list[str]:
    keys = store.table("species")["series_key"].astype(str)
    return sorted(keys[keys.str.startswith("formula:")])


def _species(store: CampaignStore, keys: list[str]) -> pd.DataFrame:
    """The species columns of *keys*, indexed by key."""
    wanted = [k for k in keys if k != TIC]
    if wanted:
        species = store.table("species", keys=wanted)
    else:
        # The parquet filter takes no empty list of keys
        species = store.table("species").iloc[:0]
    return species.set_index(species["series_key"].astype(str))[list(SPECIES_COLUMNS)]


def composition(
    store: CampaignStore,
    period: str,
    *,
    keys: Any = None,
    by: str | None = None,
    normalise: str = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> pd.DataFrame:
    """What the assigned formulas were, per period: the data of a Van
    Krevelen, an oxidation-state or a class-budget chart.

    :param period: ``all`` (one period, from the first sample), ``day``,
        ``week`` or ``month``, in *tz*.
    :param keys: The series keys; None for every formula key of the store.
    :param by: None for a row per key, or ``class`` or ``n_c`` to sum the
        keys of each compound class or carbon number.
    :param normalise: ``none``, ``tic`` or a series key.
    :return: With *by* None, one row per period and key with a value in
        it, key order within a period: ``period`` (its start), ``key``,
        ``median``, ``mean`` and ``n`` of its sample values, ``share`` (its
        mean over the sum of the period's means) and the species columns
        (:data:`SPECIES_COLUMNS`). With *by*, one row per period and group:
        ``period``, the group (``class`` or ``n_c``), ``mean`` and
        ``share`` (the sums over its keys) and ``n_keys``.
    """
    if by is not None and by not in COMPOSITION_BY:
        raise ValueError(f"by={by!r} is none of {', '.join(COMPOSITION_BY)}")
    if period != "all" and period not in PERIODS:
        raise ValueError(f"period={period!r} is none of all, {', '.join(PERIODS)}")
    normalise = _check_normalise(normalise)
    keys = _formula_keys(store) if keys is None else _check_keys(store, keys)
    if keys:
        values = _read(
            store,
            keys,
            normalise=normalise,
            tz=tz,
            start=start,
            end=end,
            view="composition",
        )
        zone, attrs, keys = values.zone, values.attrs, values.keys
        frame = _composed(values, period)
    else:
        # A store without a batch ledger has no formula to compose
        zone = display_zone(store, tz)
        lo, hi = _bound(start, zone), _bound(end, zone)
        name = _normaliser_name(store, normalise)
        attrs = _attrs(
            store,
            "composition",
            zone,
            normalise=normalise,
            normalise_name=name,
            start=lo,
            end=hi,
        )
        columns = {"period": "time", "key": "object", "median": "float64"}
        frame = _empty(dict(columns, mean="float64", n="int64"), zone)
    frame["share"] = frame["mean"] / frame.groupby("period")["mean"].transform("sum")
    species = _species(store, keys)
    for column in SPECIES_COLUMNS:
        frame[column] = frame["key"].map(species[column]).to_numpy()
    if by is not None:
        frame = (
            frame.groupby(["period", by], sort=True, dropna=False)
            .agg(mean=("mean", "sum"), share=("share", "sum"), n_keys=("key", "size"))
            .reset_index()
        )
    frame["period"] = pd.DatetimeIndex(frame["period"]).tz_convert(zone)
    return _stamp(frame, dict(attrs, cadence=period, period=period, by=by))


def _composed(values: _Values, period: str) -> pd.DataFrame:
    """:func:`composition`'s ``period``, ``key``, ``median``, ``mean`` and
    ``n``: a row per period and key with a value in it."""
    code = np.full(len(values.t), -1, dtype="int64")
    reads_any = values.reads_any
    code[reads_any], starts = _period_codes(values.t[reads_any], values.zone, period)
    n_keys = len(values.keys)
    grouped = _grouped(code[values.pos] * n_keys + values.key, values.value)
    stats = pd.DataFrame(
        {"median": grouped.median(), "mean": grouped.mean(), "n": grouped.count()}
    )
    stats = stats[stats["n"] > 0]
    at = stats.index.to_numpy("int64")
    return pd.DataFrame(
        {
            "period": _times(starts.asi8[at // n_keys], values.zone),
            "key": [values.keys[k] for k in at % n_keys],
            "median": stats["median"].to_numpy(),
            "mean": stats["mean"].to_numpy(),
            "n": stats["n"].to_numpy("int64"),
        }
    )


def spectrum(
    store: CampaignStore,
    period: tuple[Any, Any] | None = None,
    other: tuple[Any, Any] | None = None,
    *,
    keys: Any = None,
    normalise: str = "none",
    tz: str | None = None,
) -> pd.DataFrame:
    """The average spectrum of a period on the batch-peak grid, and its
    difference to another period.

    :param period: ``(start, end)``: the samples timed in it, a bound
        without a zone in *tz* and None for open; None for every sample.
    :param other: Another ``(start, end)`` to compare with: an episode
        against its background, a night against its day.
    :param keys: The series keys, each with an m/z; None for every formula
        key of the store.
    :param normalise: ``none``, ``tic`` or a series key.
    :return: One row per key with a value in *period* or *other*, by m/z:
        ``key``, ``mz``, ``name``, ``formula``, ``ion_formula``, ``value``
        (the mean of its sample values in *period*) and ``n``; with
        *other*, also ``other_value``, ``other_n``, ``diff`` (``value``
        less ``other_value``, a key without a value in one of them counting
        0 there) and ``ratio`` (``value`` over ``other_value``, NaN without
        both). ``attrs`` adds ``period`` and ``other``, ISO in UTC; its
        ``mixed_runs`` and ``run_changes`` are over both windows and the
        time between them.
    """
    normalise = _check_normalise(normalise)
    zone = display_zone(store, tz)
    windows = {"period": _window(period, zone)}
    if other is not None:
        windows["other"] = _window(other, zone)
    if keys is None:
        keys = _formula_keys(store)
        species = _species(store, keys)
        # The grid is the batch peaks': a formula key without one has no place
        keys = [k for k in keys if not pd.isna(species.loc[k, "mz"])]
    else:
        keys = _check_keys(store, keys, tic=False)
        species = _species(store, keys)
        without = [k for k in keys if pd.isna(species.loc[k, "mz"])]
        if without:
            raise ValueError(
                f"No m/z to draw a spectrum on for {', '.join(without)}: the "
                "batch peaks' formula keys have one, target ions do not"
            )

    # A comparison of two windows joins what each reads: flag a run change
    # over both, and between them
    attrs = _attrs(
        store,
        "spectrum",
        zone,
        keys=keys,
        normalise=normalise,
        normalise_name=_normaliser_name(store, normalise),
        windows=list(windows.values()),
    )
    attrs.update(cadence="period", other=None)
    for name, window in windows.items():
        attrs[name] = [None if w is None else w.isoformat() for w in window]
    if not keys:
        columns = {"key": "object", "mz": "float64", "name": "object"}
        columns.update(formula="object", ion_formula="object", value="float64")
        columns.update(n="int64")
        if other is not None:
            columns.update(other_value="float64", other_n="int64", diff="float64")
            columns.update(ratio="float64")
        return _stamp(_empty(columns, zone), attrs)

    means = {}
    for name, window in windows.items():
        values = _read(
            store,
            keys,
            normalise=normalise,
            tz=tz,
            start=None,
            end=None,
            view="spectrum",
            window=window,
        )
        grouped = _grouped(values.key, values.value)
        means[name] = pd.DataFrame(
            {"value": grouped.mean(), "n": grouped.count()}
        ).reindex(range(len(keys)))
    frame = pd.DataFrame(
        {
            "key": keys,
            "mz": species.loc[keys, "mz"].to_numpy("float64"),
            "name": species.loc[keys, "name"].to_numpy(),
            "formula": species.loc[keys, "formula"].to_numpy(),
            "ion_formula": species.loc[keys, "ion_formula"].to_numpy(),
            "value": means["period"]["value"].to_numpy(),
            "n": means["period"]["n"].fillna(0).to_numpy("int64"),
        }
    )
    seen = frame["n"] > 0
    if other is not None:
        frame["other_value"] = means["other"]["value"].to_numpy()
        frame["other_n"] = means["other"]["n"].fillna(0).to_numpy("int64")
        frame["diff"] = frame["value"].fillna(0) - frame["other_value"].fillna(0)
        frame["ratio"] = frame["value"] / frame["other_value"]
        seen |= frame["other_n"] > 0
    frame = frame[seen].sort_values(["mz", "key"], kind="stable").reset_index(drop=True)
    return _stamp(frame, attrs)


def top(
    store: CampaignStore,
    n: int,
    by: str,
    *,
    keys: Any = None,
    period: str = "week",
    normalise: str = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> pd.DataFrame:
    """The keys that stand out: the brightest, the biggest changers, the
    new ones.

    The first and last periods are the calendar periods (*period*, in
    *tz*) of the window's first and last samples.

    :param n: How many keys.
    :param by: ``median``, the highest median sample value over the
        window; ``ratio``, the largest change from the first period to the
        last, up or down (the ratio of their medians, ranked by its log's
        size; keys without a value in both, and every key in a window of
        one period, are not ranked); or ``new``,
        the keys with a value in the last period and none before it, the
        highest last first.
    :param keys: The series keys; None for every key of the store.
    :param period: ``day``, ``week`` or ``month``.
    :param normalise: ``none``, ``tic`` or a series key.
    :return: Up to *n* rows, best first: ``rank``, ``key``, ``value`` (what
        it is ranked by: the median, the ratio, the last), ``median``,
        ``first`` and ``last`` (the medians in the first and last periods),
        ``ratio`` (``last`` over ``first``), ``new``, ``name`` and
        ``formula``. ``attrs`` adds ``by``, ``period``, ``first_period``
        and ``last_period`` (their starts, ISO in *tz*).
    """
    if by not in TOP_BY:
        raise ValueError(f"by={by!r} is none of {', '.join(TOP_BY)}")
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 1:
        raise ValueError(f"n={n!r} is no positive count")
    if period not in PERIODS:
        raise ValueError(f"period={period!r} is none of {', '.join(PERIODS)}")
    if keys is None:
        keys = sorted(store.table("species")["series_key"].astype(str))
    values = _read(
        store, keys, normalise=normalise, tz=tz, start=start, end=end, view="top"
    )
    attrs = dict(values.attrs, cadence=period, by=by, period=period)
    n_keys = len(values.keys)
    code = np.full(len(values.t), -1, dtype="int64")
    reads_any = values.reads_any
    code[reads_any], starts = _period_codes(values.t[reads_any], values.zone, period)
    last = len(starts) - 1
    attrs.update(
        first_period=starts[0].isoformat() if len(starts) else None,
        last_period=starts[-1].isoformat() if len(starts) else None,
    )

    def median_where(selected: np.ndarray) -> np.ndarray:
        grouped = _grouped(values.key[selected], values.value[selected])
        return grouped.median().reindex(range(n_keys)).to_numpy("float64")

    detected = ~np.isnan(values.value)
    of_value = code[values.pos]
    frame = pd.DataFrame(
        {
            "key": values.keys,
            "median": median_where(np.ones(len(values.pos), dtype=bool)),
            "first": median_where(of_value == 0),
            "last": median_where(of_value == last),
        }
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        frame["ratio"] = frame["last"] / frame["first"]
    before = np.zeros(n_keys, dtype=bool)
    np.logical_or.at(before, values.key[detected & (of_value < last)], True)
    frame["new"] = (last > 0) & frame["last"].notna().to_numpy() & ~before
    species = _species(store, values.keys)
    frame["name"] = frame["key"].map(species["name"])
    frame["formula"] = frame["key"].map(species["formula"])

    if by == "median":
        frame["value"] = frame["median"]
        score = frame["median"]
    elif by == "ratio":
        frame["value"] = frame["ratio"]
        # Up or down alike; without a value in both periods, or with the
        # one period as both, no change
        score = np.abs(np.log(frame["ratio"]))
        if last < 1:
            score = pd.Series(np.nan, index=frame.index)
    else:
        frame["value"] = frame["last"]
        score = frame["last"].where(frame["new"])
    frame["score"] = score
    frame = (
        frame[frame["score"].notna()]
        .sort_values(["score", "key"], ascending=[False, True], kind="stable")
        .head(n)
        .reset_index(drop=True)
    )
    frame.insert(0, "rank", np.arange(1, len(frame) + 1))
    columns = ["rank", "key", "value", "median", "first", "last", "ratio", "new"]
    return _stamp(frame[[*columns, "name", "formula"]], attrs)
