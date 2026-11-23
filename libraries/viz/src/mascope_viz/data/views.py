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
  element counts, ratios and tier.
- :func:`spectrum`: a period's average spectrum on the batch-peak grid, and
  its difference to another period.
- :func:`spectrogram`: the assigned signal over time on a fixed m/z grid.
- :func:`top`: the brightest keys, the biggest changers, the new ones.
- :func:`amplitude`: how much each key's binned medians vary.

What the views share:

- **Keys** are series keys as the store spells them (``ion:<target_ion_id>``,
  ``formula:<formula>|<mechanism>``), or ``tic`` for the samples' TIC. A key
  the store does not hold is an error. A tuple of series keys among them is
  a **sum**: their per-sample sum as one series, keyed by the members joined
  by ``+`` and named by their names joined by `` + ``, so a reagent's total
  is drawn and binned like any other key. A sample's sum is that of the
  members with a value in it, NaN when none has. Its members must be read in
  one polarity, and no member twice.
- **A sample's value of a key** is the sum of the key's values in it (two
  batch peaks can carry one formula; the SDK notebooks sum a label's peaks
  per sample the same way). A key read in a batch is NaN in the batch's
  samples where it was not matched or not detected: below detection is NaN,
  never zero, and no view fills it in.
- **Normalisation** (decision 7) is ``none``; ``tic``, each value divided by
  its sample's TIC; a series key such as a reagent ion's ``ion:<id>``, each
  value divided by that key's value in the same sample; or a sequence of
  series keys, divided by their sum in the sample, as a sum key is summed. A
  reagent spread over clusters whose shares move with humidity (the bare ion
  and its water cluster, nitrate's monomer, dimer and trimer) is normalised
  by the sum of its ions: dividing by one of them leaves that one's share's
  cycle in every species. A sequence of one key is that key. Normalisation
  is applied per sample, before any binning; a sample whose normaliser is NaN
  or 0 has NaN values.
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
  values are divided by, by name: a list of keys for a sum, its members'
  names joined by `` + ``), ``normalise_members`` (the keys divided by, in
  the order given: one for a single key, none for ``none`` and ``tic``),
  ``normalise_complete`` (the fraction of the window's samples, of the
  batches that read a member, in which every member has a value; None
  without a member or such a sample), ``keys``, ``start`` and ``end`` (the
  window, ISO in UTC), ``mixed_runs`` and ``run_changes`` (whether the
  batches of the window's samples carry more than one ledger reading in a
  polarity the formulas are read in, and where the reading changes between
  them: a run change is flagged, never silently joined; both empty without
  a formula key or a formula to normalise by, a sum's members included)
  and the store's ``provenance``. A formula is read in one polarity only, so a change in
  the other polarity's batches is none of its keys'. :func:`series`,
  :func:`diurnal`, :func:`periods`, :func:`composition` and :func:`top`
  add ``polarities``: those of the window's samples, ``+`` before ``-``.
  With both, the TIC, a key's statistics and a ranking pool two
  chemistries, and a chart says so.
"""

import math
import zoneinfo
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd
from pandas.tseries.frequencies import to_offset

from .scope import INSTRUMENT_TZ, _zone_of
from .store import BINS, CampaignStore, _run_changes, _streams


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

#: The tiers of a batch peak's formula, most confident first, as the server
#: names them: a candidate is a plausible formula with real alternatives
#: open, not an identification.
TIERS = ("assigned", "candidate", "below_assignability", "unassigned")

#: The rows :func:`spectrogram`'s m/z grid holds at most: more, and its
#: rows widen.
SPECTROGRAM_MAX_ROWS = 2000

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


#: A key as a view takes it: a series key (or ``tic``), or a sum's members.
Key = str | tuple[str, ...]

#: A normaliser: ``none``, ``tic``, a series key, or a sum's members.
Normaliser = str | tuple[str, ...]


def _key_name(key: Key) -> str:
    """A key's name in a frame: a sum's members joined by ``+``."""
    return key if isinstance(key, str) else "+".join(key)


def _members(key: Key) -> tuple[str, ...]:
    """The series keys a key reads: itself, or a sum's members."""
    return (key,) if isinstance(key, str) else key


def _sum_members(given: Any, what: str) -> tuple[str, ...]:
    """A sum's members, checked for series keys and repeats; *what* says
    where the sum was given, for the error."""
    members = tuple(str(m) for m in given)
    if not members:
        raise ValueError(f"{what} is an empty sum: name at least one series key")
    for member in members:
        if not member.startswith(("ion:", "formula:")):
            raise ValueError(
                f"{what}: a sum's member {member!r} is no series key such as "
                "'ion:<target_ion_id>'"
            )
    seen: set[str] = set()
    for member in members:
        if member in seen:
            raise ValueError(f"{what} names {member!r} twice: a sum counts it once")
        seen.add(member)
    return members


def _check_keys(store: CampaignStore, keys: Any, *, tic: bool = True) -> list[Key]:
    """*keys* checked against the store, duplicates dropped, order kept.

    A key is a series key, or a tuple of them in the list (a sum; one of one
    key is that key). The keys themselves given as a tuple are several keys,
    as any other sequence of them is.
    """
    if isinstance(keys, str):
        keys = [keys]
    items: dict[str, Key] = {}
    for given in () if keys is None else keys:
        if isinstance(given, tuple):
            key: Key = _sum_members(given, f"The sum {given!r}")
            if len(key) == 1:
                key = key[0]
        else:
            key = str(given)
        items.setdefault(_key_name(key), key)
    if not items:
        raise ValueError("No keys: name at least one series key")
    held = set(store.table("species")["series_key"].astype(str))
    for key in items.values():
        for member in _members(key):
            if member == TIC and tic:
                continue
            if member not in held:
                raise ValueError(
                    f"The store holds no series {member!r}: a key is "
                    "ion:<target_ion_id> or formula:<formula>|<mechanism> as "
                    "store.table('species') lists them" + (", or tic" if tic else "")
                )
    return list(items.values())


def _check_normalise(normalise: Any) -> Normaliser:
    """*normalise* as a view takes it: ``none``, ``tic``, a series key, or a
    sum's members (a sequence of one key is that key)."""
    if not isinstance(normalise, str) and isinstance(
        normalise, (Sequence, np.ndarray, pd.Index, pd.Series)
    ):
        members = _sum_members(normalise, f"normalise={list(normalise)!r}")
        return members[0] if len(members) == 1 else members
    if not isinstance(normalise, str) or not (
        normalise in ("none", TIC) or normalise.startswith(("ion:", "formula:"))
    ):
        raise ValueError(
            f"normalise={normalise!r} is none of 'none', 'tic', a series key "
            "such as 'ion:<target_ion_id>' or a list of them to divide by their sum"
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


def _names(store: CampaignStore, keys: Sequence[str]) -> dict[str, str]:
    """The series keys' names: a species' name, else its formula (a target
    ion without a compound name), else its key."""
    named: dict[str, str] = {}
    wanted = [k for k in keys if k != TIC]
    if wanted:
        species = store.table("species", keys=wanted)
        for key, name, formula in zip(
            species["series_key"], species["name"], species["formula"]
        ):
            label = _label_text(name) or _label_text(formula)
            if label:
                named[str(key)] = label
    return {k: named.get(k, k) for k in keys}


def _label_text(value: Any) -> str | None:
    """*value* as a label; None when missing or blank (a store synced
    before blank names were dropped may still hold one)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value)
    return text if text.strip() else None


def _normaliser_name(store: CampaignStore, normalise: Normaliser) -> str | None:
    """What the values are divided by, by name: a sum's members' names
    joined by `` + ``; None for ``none``.

    :raises ValueError: When *normalise* is or holds a key the store does
        not hold.
    """
    if normalise == "none":
        return None
    if normalise == TIC:
        return "TIC"
    members = _members(normalise)
    _check_keys(store, list(members), tic=False)
    names = _names(store, members)
    return " + ".join(names[m] for m in members)


def _one_polarity(
    what: str,
    members: Sequence[str],
    pos: np.ndarray,
    member: np.ndarray,
    value: np.ndarray,
    samples: pd.DataFrame,
) -> None:
    """Refuse a sum whose members are read in more than one polarity: in
    the samples where they have a value, by each sample's own polarity, so
    a polarity-switching batch reads each ion in its own.

    :param pos: Rows' sample positions in *samples*,
    :param member: their members' positions in *members*,
    :param value: and their values.
    :raises ValueError: Naming each member's polarities.
    """
    if len(members) < 2:
        return
    polarity = samples["polarity"].astype(object).to_numpy()
    found = np.isfinite(value)
    read = {
        name: sorted(
            {str(p) for p in polarity[pos[found & (member == i)]] if not pd.isna(p)}
        )
        for i, name in enumerate(members)
    }
    if len(set().union(*read.values())) > 1:
        spans = "; ".join(
            f"{name} is read in {', '.join(read[name]) or 'no sample'}"
            for name in members
        )
        raise ValueError(
            f"{what} spans polarities ({spans}): a sum never spans "
            "chemistries; sum the ions of one polarity"
        )


@dataclass(frozen=True)
class _Divisor:
    """What each sample's values are divided by.

    :ivar value: Per sample; NaN where it is missing or 0.
    :ivar formula: ``[batch]``: the batches in which a formula member has a
        value, which a run reads; None without a formula member.
    :ivar complete: The samples of the batches that read a member in which
        every member has a value,
    :ivar reading: and all those batches' samples, in the window.
    """

    value: np.ndarray
    formula: np.ndarray | None = None
    complete: int = 0
    reading: int = 0


def _normaliser(
    store: CampaignStore, values: "_Values", index: pd.Index
) -> _Divisor | None:
    """What each sample of *values* is divided by; None for ``none``.

    A key's or a sum's divisor is the sum of the members with a value in
    the sample, NaN when none has.
    """
    normalise, samples = values.normalise, values.samples
    if normalise == "none":
        return None
    if normalise == TIC:
        divisor = samples["tic"].to_numpy("float64").copy()
        divisor[divisor == 0] = np.nan
        return _Divisor(divisor)
    members = _members(normalise)
    pos, member, value = _key_rows(store, list(members), samples, index)
    _one_polarity(f"normalise={list(members)!r}", members, pos, member, value, samples)
    read = np.zeros(len(values.present), dtype=bool)
    read[values.batch[pos]] = True
    pos, member, value = _per_sample(pos, member, value, len(members))
    matrix = np.full((len(members), len(samples)), np.nan)
    matrix[member, pos] = value
    has = np.isfinite(matrix)
    divisor = np.where(has, matrix, 0.0).sum(axis=0)
    # A sample in which no member has a value sums to 0, and divides nothing
    divisor[divisor == 0] = np.nan
    formula = [i for i, m in enumerate(members) if m.startswith("formula:")]
    divided = None
    if formula:
        divided = np.zeros(len(values.present), dtype=bool)
        divided[values.batch[has[formula].any(axis=0)]] = True
    reading = read[values.batch]
    return _Divisor(
        divisor,
        divided,
        complete=int((has.all(axis=0) & reading).sum()),
        reading=int(reading.sum()),
    )


def _run_attrs(
    store: CampaignStore,
    reading: Collection[str],
    drawn: Collection[str],
    between: bool = False,
) -> dict[str, Any]:
    """Whether the batches a view draws carry more than one ledger reading
    of a polarity a formula is read in, and where it changes between them;
    neither when no batch reads a formula.

    :param reading: The batches that read a formula key, or the formula
        normalised by, in the view's window: their polarities are the ones
        followed (:func:`~.store._streams`, so a ``+-`` batch's are both). A
        formula is read in one polarity only, so a change in another
        polarity's batches is none of its keys'.
    :param drawn: The batches of the window's samples, which the view
        keeps by ``t_mid_utc``: a batch whose files started before the
        window is drawn when its samples are.
    :param between: Also follow the batches between the first drawn and
        the last, so a change between two windows is put at the batch where
        the run changed.
    """
    none = {"mixed_runs": False, "run_changes": []}
    if not reading:
        return none
    batches = store.status().batches
    if batches.empty:
        return none
    ids = batches["sample_batch_id"]
    streams = {
        stream
        for polarity in batches.loc[ids.isin(list(reading)), "polarity"]
        for stream in _streams(polarity)
    }
    kept = ids.isin(list(drawn))
    if between and kept.any():
        # The batches are oldest first
        at = np.flatnonzero(kept.to_numpy())
        position = np.arange(len(batches))
        kept = pd.Series(
            (position >= at[0]) & (position <= at[-1]), index=batches.index
        )
    changes = _run_changes(batches[kept], streams)
    return {
        "mixed_runs": bool(changes),
        "run_changes": [change.to_dict() for change in changes],
    }


def _attrs(
    store: CampaignStore,
    view: str,
    zone: str,
    *,
    keys: Sequence[str] = (),
    normalise: Normaliser = "none",
    normalise_name: str | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """What every frame of a view says (see the module); the run flags say
    no change until the view's values are read (:func:`_with_present`)."""
    return {
        "view": view,
        "tz": zone,
        # JSON-safe: a sum's members as a list
        "normalise": normalise if isinstance(normalise, str) else list(normalise),
        "normalise_name": normalise_name,
        "normalise_members": []
        if normalise in ("none", TIC)
        else list(_members(normalise)),
        "normalise_complete": None,
        "keys": list(keys),
        "start": None if start is None else start.isoformat(),
        "end": None if end is None else end.isoformat(),
        **_run_attrs(store, (), ()),
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
    :ivar batch_ids: The batches, by code.
    :ivar window: The window, in UTC; None for an open bound.
    :ivar reading: The batches that read a formula key, or the formula
        normalised by, in the window.
    :ivar members: Each key's series keys: itself, or a sum's members.
    :ivar normalise: What the values are divided by (:func:`_check_normalise`).
    :ivar complete: Of the window's samples of the batches that read a
        member of the normaliser: those in which every member has a value,
        and all of them.
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
    batch_ids: tuple[str, ...] = ()
    window: Window = (None, None)
    reading: frozenset[str] = frozenset()
    members: tuple[tuple[str, ...], ...] = ()
    normalise: Normaliser = "none"
    complete: tuple[int, int] = (0, 0)

    @property
    def items(self) -> list[Key]:
        """The keys as :func:`_check_keys` gives them: a sum as its tuple."""
        return [m[0] if len(m) == 1 else m for m in self.members]

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
    values = _sampled(
        store,
        keys,
        normalise=normalise,
        tz=tz,
        start=start,
        end=end,
        view=view,
        window=window,
    )
    return _with_rows(store, values)


def _sampled(
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
    """A view's window and its samples, with no values read yet: no batch
    reads a key."""
    normalise = _check_normalise(normalise)
    zone = display_zone(store, tz)
    lo, hi = window if window is not None else (_bound(start, zone), _bound(end, zone))
    items = _check_keys(store, keys)
    keys = [_key_name(k) for k in items]

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
    batches = pd.Categorical(samples["sample_batch_id"].astype(str))
    empty = np.array([], "int64")

    return _Values(
        zone=zone,
        keys=keys,
        samples=samples,
        t=_ns(samples["t_mid_utc"]),
        batch=np.asarray(batches.codes, dtype="int64"),
        present=np.zeros((max(len(batches.categories), 1), len(keys)), dtype=bool),
        pos=empty,
        key=empty,
        value=np.array([], "float64"),
        attrs=_attrs(
            store,
            view,
            zone,
            keys=keys,
            normalise=normalise,
            normalise_name=_normaliser_name(store, normalise),
            start=lo,
            end=hi,
        )
        | {"polarities": _polarities(samples["polarity"])},
        batch_ids=tuple(batches.categories),
        window=(lo, hi),
        members=tuple(_members(k) for k in items),
        normalise=normalise,
    )


def _polarities(polarity: pd.Series) -> list[str]:
    """The polarities of samples: ``+``, ``-`` or both, a
    polarity-switching sample's (``+-``) both of its."""
    found = {
        sign
        for value in polarity.dropna().astype(str).unique()
        for sign in _streams(value)
    }
    return [sign for sign in ("+", "-") if sign in found]


def _with_rows(store: CampaignStore, values: _Values) -> _Values:
    """*values* with the store's rows of its keys in its samples: which
    batches read each key (a sum: any of its members), and one value per
    (sample, key), normalised."""
    samples = values.samples
    index = pd.Index(samples["sample_item_id"].astype(str))
    raw = list(dict.fromkeys(m for members in values.members for m in members))
    pos, member, value = _key_rows(store, raw, samples, index)
    read = np.zeros((len(values.present), len(raw)), dtype=bool)
    read[values.batch[pos], member] = True
    present = np.zeros_like(values.present)
    of = {name: i for i, name in enumerate(raw)}
    plain = np.full(len(raw), -1, dtype="int64")
    parts = []
    for k, members in enumerate(values.members):
        at = [of[m] for m in members]
        present[:, k] = read[:, at].any(axis=1)
        if len(members) == 1:
            plain[at[0]] = k
            continue
        local = np.full(len(raw), -1, dtype="int64")
        local[at] = np.arange(len(at))
        kept = local[member] >= 0
        _one_polarity(
            f"The sum {values.keys[k]!r}",
            members,
            pos[kept],
            local[member[kept]],
            value[kept],
            samples,
        )
        parts.append((pos[kept], np.full(int(kept.sum()), k), value[kept]))
    kept = plain[member] >= 0
    parts.insert(0, (pos[kept], plain[member[kept]], value[kept]))
    pos, key, value = (np.concatenate(column) for column in zip(*parts))
    pos, key, value = _per_sample(
        pos.astype("int64"), key.astype("int64"), value, len(values.keys)
    )
    # A run reads every formula member, of a sum or not
    formula = [i for i, name in enumerate(raw) if name.startswith("formula:")]
    reads = read[:, formula].any(axis=1)
    divisor = _normaliser(store, values, index)
    attrs = values.attrs
    if divisor is not None:
        value = value / divisor.value[pos]
        if divisor.formula is not None:
            # A formula normaliser divides every value by what a run reads
            reads |= divisor.formula
        # None for the TIC, which no batch reads as a member
        complete = divisor.complete / divisor.reading if divisor.reading else None
        attrs = dict(attrs, normalise_complete=complete)
    values = replace(
        values,
        pos=pos,
        key=key,
        value=value,
        attrs=attrs,
        complete=(divisor.complete, divisor.reading) if divisor else (0, 0),
    )
    return _with_present(store, values, present, reads)


def _with_present(
    store: CampaignStore,
    values: _Values,
    present: np.ndarray,
    reads: np.ndarray | None = None,
) -> _Values:
    """*values* with *present*, and the run flags (:func:`_run_attrs`) over
    its batches in the polarities a formula is read in: by the batches that
    *reads* marks (``[batch]``: those that read a formula key, a sum's
    member or the normaliser's), or without it, those *present* says read a
    formula key."""
    if reads is None:
        formula = [
            k
            for k, members in enumerate(values.members)
            if any(m.startswith("formula:") for m in members)
        ]
        reads = present[:, formula].any(axis=1)
    reading = frozenset(values.batch_ids[c] for c in np.flatnonzero(reads))
    attrs = dict(values.attrs, **_run_attrs(store, reading, values.batch_ids))
    return replace(values, present=present, attrs=attrs, reading=reading)


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

    :param keys: The series keys (``tic`` for the TIC); a string is one, and
        a tuple of keys among them is their sum.
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
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum.
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
        chose it), ``stat``, ``band`` and ``source``: ``rollups`` when the
        store's hourly or daily rollups gave the bins, ``rows`` when its
        rows did.

    The rollups give the bins only where they give the frame the rows
    give, values and counts alike: at a cadence named (not ``auto``) whose
    bins are UTC hours (``1h`` in a zone a whole number of hours from UTC)
    or UTC days (``1D`` in UTC), with the window's first and last bins
    inside the window, no normalisation and no sum, ``stat`` median or
    mean, ``band``
    (10, 90) or None, every rollup day computed (``status().pending_rollups``
    empty), no batch of the window with two batch peaks of one formula
    (``n_shared_keys``) when a key is a formula's, and no bin where a
    formula has two readings. The TIC is binned from the samples.
    """
    if stat not in STATS:
        raise ValueError(f"stat={stat!r} is none of {', '.join(STATS)}")
    band = _check_band(band)
    offset = None if cadence in ("auto", "raw") else _offset(cadence)
    values = _sampled(
        store, keys, normalise=normalise, tz=tz, start=start, end=end, view="series"
    )
    extra = {"stat": stat, "band": band, "auto": cadence == "auto"}
    if offset is not None and _rollable(store, values, offset, stat, band):
        frame = _rolled_series(store, values, offset, stat, extra)
        if frame is not None:
            return frame
    values = _with_rows(store, values)
    attrs = dict(values.attrs, **extra, source="rows")
    reads_any = values.reads_any
    if not reads_any.any():
        name = "raw" if cadence == "raw" else _cadence_name(offset or to_offset("1min"))
        return _stamp(_empty(_SERIES_COLUMNS, values.zone), dict(attrs, cadence=name))
    if cadence == "raw":
        return _stamp(_raw(values), dict(attrs, cadence="raw", band=None))
    offset, edges, first, n_bins, sample_bin = _series_bins(
        values, offset, cadence == "auto"
    )
    attrs["cadence"] = _cadence_name(offset)
    grouped = _grouped(values.key * n_bins + sample_bin[values.pos], values.value)
    stats = _stats(grouped, stat, band)
    return _series_frame(
        values, edges[first : first + n_bins], sample_bin, stats, attrs
    )


def _series_bins(
    values: _Values, offset: pd.DateOffset | None, auto: bool
) -> tuple[pd.DateOffset, np.ndarray, int, int, np.ndarray]:
    """The bins of :func:`series`: the offset (the auto cadence's when
    *offset* is None), the edges, the first bin holding a sample of a batch
    that reads a key, how many bins from it to the last such, and each
    sample's bin counted from the first."""
    if offset is None:
        offset = to_offset(_series_cadence(values))
    t = values.t
    reads_any = values.reads_any
    edges = _edges(
        pd.Timestamp(t[reads_any].min(), tz="UTC"),
        pd.Timestamp(t[reads_any].max(), tz="UTC"),
        offset,
        values.zone,
        # A bin the clock shortens could fall between samples
        clock=not auto,
    )
    sample_bin = np.searchsorted(edges, t, side="right") - 1
    first = int(sample_bin[reads_any].min())
    n_bins = int(sample_bin[reads_any].max()) - first + 1
    return offset, edges, first, n_bins, sample_bin - first


def _stats(grouped: Any, stat: str, band: tuple[float, float] | None) -> pd.DataFrame:
    """A bin's ``value``, ``lo``, ``hi`` and ``n`` per group of *grouped*,
    indexed by the group's code."""
    return pd.DataFrame(
        {
            "value": grouped.agg(stat),
            "lo": grouped.quantile(band[0] / 100) if band else np.nan,
            "hi": grouped.quantile(band[1] / 100) if band else np.nan,
            "n": grouped.count(),
        }
    )


def _series_frame(
    values: _Values,
    starts: np.ndarray,
    sample_bin: np.ndarray,
    stats: pd.DataFrame,
    attrs: dict[str, Any],
) -> pd.DataFrame:
    """:func:`series`' frame of bins starting at *starts*, *stats* indexed
    by ``key * len(starts) + bin``."""
    n_bins, n_keys = len(starts), len(values.keys)
    size = n_keys * n_bins
    n_samples = np.concatenate(
        [
            np.bincount(sample_bin[values.reads(k)], minlength=n_bins)[:n_bins]
            for k in range(n_keys)
        ]
    )
    at = stats.index.to_numpy("int64")
    frame = pd.DataFrame(
        {
            "t": _times(np.tile(starts, n_keys), values.zone),
            "key": _key_column(values.keys, np.repeat(np.arange(n_keys), n_bins)),
            "value": _fill(size, at, stats["value"]),
            "lo": _fill(size, at, stats["lo"]),
            "hi": _fill(size, at, stats["hi"]),
            "n": _fill(size, at, stats["n"], 0),
            "n_samples": n_samples.astype("int64"),
        }
    )
    return _stamp(frame, attrs)


# --- series from the rollups ---------------------------------------------------


#: The band the rollups hold, their ``p10`` and ``p90``.
_ROLLUP_BAND = (10, 90)


def _rollable(
    store: CampaignStore,
    values: _Values,
    offset: pd.DateOffset,
    stat: str,
    band: tuple[float, float] | None,
) -> bool:
    """Whether the store's rollups may give :func:`series` of *values* at
    *offset*, as far as is known before reading which batches read the
    keys: bins of UTC hours or days over the window's samples, no
    normalisation, no sum among the keys, a statistic and a band the
    rollups hold, every rollup day computed, and no batch of the window
    with two batch peaks of one formula when a key is a formula's (a
    sample's value is their sum, the rollups count each; a sum's bins are
    of its per-sample sums, which no rollup holds)."""
    if values.normalise != "none" or stat not in ("median", "mean"):
        return False
    if any(len(members) > 1 for members in values.members):
        return False
    if band not in (None, _ROLLUP_BAND) or not len(values.t):
        return False
    edges = _edges(
        pd.Timestamp(values.t.min(), tz="UTC"),
        pd.Timestamp(values.t.max(), tz="UTC"),
        offset,
        values.zone,
    )
    if _rollup_bin(edges, (None, None)) is None:
        return False
    meta = store.meta
    if meta.get("pending_rollups"):
        return False
    if any(k.startswith("formula:") for k in values.keys):
        stamps = meta.get("batches", {})
        if any((stamps.get(b) or {}).get("shared_keys", 0) for b in values.batch_ids):
            return False
    return True


def _rolled_present(store: CampaignStore, values: _Values) -> np.ndarray:
    """Which batches of *values* read each key, as :func:`_with_rows` finds
    it, without the rows: a batch holds a key's ``species`` row when it holds
    a series row of it. A batch with samples outside the window reads a key
    only if a sample inside does, which its own rows say."""
    keys = values.keys
    wanted = [k for k in keys if k != TIC]
    present = np.zeros_like(values.present)
    if TIC in keys:
        present[:, keys.index(TIC)] = True
    if not wanted:
        return present
    key_index = pd.Index(keys)
    total = store.table("samples")["sample_batch_id"].astype(str).value_counts()
    inside = pd.Series(values.batch).value_counts()
    ids = values.samples["sample_item_id"].astype(str)
    for code, batch_id in enumerate(values.batch_ids):
        if inside.get(code, 0) == total.get(batch_id, 0):
            held = store.table("species", batches=[batch_id], keys=wanted)
            present[code, _positions(key_index, held["series_key"])] = True
            continue
        index = pd.Index(ids[values.batch == code])
        for table, prefix in (("ion_series", "ion:"), ("peak_series", "formula:")):
            of_table = [k for k in wanted if k.startswith(prefix)]
            if not of_table:
                continue
            rows = store.table(table, batches=[batch_id], keys=of_table)
            rows = rows[_positions(index, rows["sample_item_id"]) >= 0]
            present[code, _positions(key_index, rows["series_key"])] = True
    return present


def _rollup_bin(edges: np.ndarray, window: Window) -> str | None:
    """The rollups' bin (``hour``, ``day``) that the bins between *edges*
    are, all inside *window*; None when they are no rollup's."""
    lo, hi = window
    if (lo is not None and edges[0] < lo.value) or (
        hi is not None and edges[-1] > hi.value
    ):
        return None
    steps = np.diff(edges)
    for freq, name in BINS.items():
        step = pd.Timedelta(freq).value
        if (steps == step).all() and (edges % step == 0).all():
            return name
    return None


def _rolled_series(
    store: CampaignStore,
    values: _Values,
    offset: pd.DateOffset,
    stat: str,
    extra: dict[str, Any],
) -> pd.DataFrame | None:
    """:func:`series` of *values* from the store's rollups, and the TIC from
    the samples, with *extra* in its attrs; None when the rollups would not
    give the frame the rows give: bins that are no UTC hours or days of the
    rollups, or a formula with two readings in a bin, which the rollups keep
    apart."""
    values = _with_present(store, values, _rolled_present(store, values))
    attrs = dict(values.attrs, **extra)
    if not values.reads_any.any():
        return None
    offset, edges, first, n_bins, sample_bin = _series_bins(values, offset, False)
    bounds = edges[first : first + n_bins + 1]
    name = _rollup_bin(bounds, values.window)
    if name is None:
        return None

    keys = values.keys
    parts = []
    wanted = [k for k in keys if k != TIC]
    if wanted:
        rows = store.table("rollups", keys=wanted)
        t = _ns(rows["t"])
        rows = rows[
            (rows["bin"].astype(str) == name).to_numpy()
            & (t >= bounds[0])
            & (t < bounds[-1])
        ]
        code = _positions(pd.Index(keys), rows["series_key"]) * n_bins + (
            np.searchsorted(bounds, _ns(rows["t"]), side="right") - 1
        )
        if pd.Index(code).has_duplicates:
            return None
        parts.append(
            pd.DataFrame(
                {
                    "value": rows[stat].to_numpy(),
                    "lo": rows["p10"].to_numpy(),
                    "hi": rows["p90"].to_numpy(),
                    "n": rows["n"].to_numpy("int64"),
                },
                index=code,
            )
        )
    if TIC in keys:
        grouped = _grouped(
            keys.index(TIC) * n_bins + sample_bin,
            values.samples["tic"].to_numpy("float64"),
        )
        parts.append(_stats(grouped, stat, attrs["band"]))
    stats = pd.concat(parts)
    if attrs["band"] is None:
        stats["lo"] = stats["hi"] = np.nan
    attrs = dict(attrs, cadence=_cadence_name(offset), source="rollups")
    return _series_frame(values, bounds[:-1], sample_bin, stats, attrs)


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


def _group_days(local: pd.DatetimeIndex, by: str) -> dict[str, int]:
    """Per group under *by* of the times *local*, the calendar days of it
    from the first time's day to the last's."""
    days = local.tz_localize(None).normalize()
    code, labels = _groups(pd.date_range(days.min(), days.max(), freq="D"), by)
    counts = np.bincount(code, minlength=len(labels))
    return dict(zip(labels, (int(n) for n in counts)))


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
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum.
    :param tz: The zone whose hours they are.
    :return: One row per group (when *by* splits), key and hour 0 to 23:
        ``group`` (with *by*), ``key``, ``hour``, ``value`` (the median),
        ``lo`` and ``hi`` (the 25th and 75th percentiles), ``n`` (the values
        in the hour) and ``n_samples`` (the samples in it of the batches
        that read the key, so ``n`` over ``n_samples`` is how often the key
        was detected there). An hour without a value is NaN. ``attrs`` adds
        ``by``; ``group_days``, with *by*, per key the calendar days of each
        group from the day of the first sample of a batch that reads the
        key to the last's (a month of two years counted in both; ``{}`` for
        a key no batch of the window reads), None without; and ``stat`` and
        ``band``, what ``value`` and ``lo`` and ``hi`` are: ``median`` and
        (25, 75).
    """
    if by is not None and by not in DIURNAL_BY:
        raise ValueError(f"by={by!r} is none of {', '.join(DIURNAL_BY)}")
    values = _read(
        store, keys, normalise=normalise, tz=tz, start=start, end=end, view="diurnal"
    )
    attrs = dict(
        values.attrs,
        cadence="hour of day",
        by=by,
        group_days={key: {} for key in values.keys} if by else None,
        stat="median",
        band=(25, 75),
    )
    reads_any = values.reads_any
    if not reads_any.any():
        columns = {"group": "object"} if by else {}
        columns.update(key="object", hour="int64", value="float64", lo="float64")
        columns.update(hi="float64", n="int64", n_samples="int64")
        frame = _empty(columns, values.zone)
        return _stamp(frame, attrs)

    local = _times(values.t, values.zone)
    hour = local.hour.to_numpy().astype("int64")
    group = np.full(len(local), -1, dtype="int64")
    group[reads_any], labels = _groups(local[reads_any], by)
    if by is not None:
        # Per key, over the samples of the batches that read it, as its
        # n_samples is counted
        for k, key in enumerate(values.keys):
            reads = values.reads(k)
            if reads.any():
                attrs["group_days"][key] = _group_days(local[reads], by)
    n_keys, n_groups = len(values.keys), len(labels)
    size = n_groups * n_keys * 24
    grouped = _grouped(
        (group[values.pos] * n_keys + values.key) * 24 + hour[values.pos], values.value
    )
    at = grouped.size().index.to_numpy("int64")
    # The samples each (group, key, hour) holds of the batches reading the key
    n_samples = np.zeros(size, dtype="int64")
    for k in range(n_keys):
        reads = values.reads(k)
        n_samples += np.bincount(
            (group[reads] * n_keys + k) * 24 + hour[reads], minlength=size
        )
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
            "n_samples": n_samples,
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
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum.
    :return: One row per period and key, key by key, from the first
        sample's period to the last's: ``period`` (its start in *tz*),
        ``key``, ``n`` (the values in it), ``n_samples`` (the samples in it
        of the batches that read the key), ``mean``, ``min``, ``p5``,
        ``p25``, ``median``, ``p75``, ``p95`` and ``max`` of the sample
        values in it; NaN, and ``n`` 0, in a period without one.
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
        columns["n_samples"] = "int64"
        columns.update({stat: "float64" for stat in stats})
        return _stamp(_empty(columns, values.zone), attrs)

    code = np.full(len(values.t), -1, dtype="int64")
    code[reads_any], starts = _period_codes(values.t[reads_any], values.zone, period)
    n_keys, n_periods = len(values.keys), len(starts)
    size = n_keys * n_periods
    grouped = _grouped(values.key * n_periods + code[values.pos], values.value)
    at = grouped.size().index.to_numpy("int64")
    n_samples = np.concatenate(
        [np.bincount(code[values.reads(k)], minlength=n_periods) for k in range(n_keys)]
    )
    frame = {
        "period": _times(np.tile(starts.asi8, n_keys), values.zone),
        "key": _key_column(values.keys, np.repeat(np.arange(n_keys), n_periods)),
        "n": _fill(size, at, grouped.count(), 0),
        "n_samples": n_samples.astype("int64"),
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
        ``mode_name`` (its name, as the batch of it the store fetched last
        records it; None when the server did not name it), ``n_files``
        (files that started in it), ``n_unfiled`` (those of them no batch
        of the scope holds) and ``n_samples``. A file counts
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
    :ivar start: The span the gaps are looked for in: the first file's or
        sample's acquisition start, in the display zone; None with neither.
    :ivar end: Its end: the last file's or sample's acquisition end.
    """

    bins: pd.DataFrame
    gaps: pd.DataFrame
    n_files: int
    n_unfiled: int
    n_samples: int
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None


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
    gaps, (first, last) = _gaps(files, samples, unfiled, shortest, zone)
    gaps.attrs.update(attrs)
    summary = {
        "n_files": len(files),
        "n_unfiled": int(unfiled.sum()),
        "n_samples": len(samples),
        "start": first,
        "end": last,
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
    name_of = _mode_names(store, every_sample)
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


def _mode_names(store: CampaignStore, samples: pd.DataFrame) -> dict[str, str]:
    """The modes' names by ID, as *samples* record them: a mode renamed
    between two batches' fetches takes the name of the batch fetched last,
    whatever its acquisition time."""
    stamps = store.meta.get("batches", {})
    named = samples[
        ["sample_batch_id", "ionization_mode_id", "ionization_mode_name"]
    ].astype(object)
    named = named[
        named["ionization_mode_id"].notna() & named["ionization_mode_name"].notna()
    ]
    fetched = pd.to_datetime(
        named["sample_batch_id"].map(
            lambda b: (stamps.get(str(b)) or {}).get("synced_utc")
        ),
        utc=True,
        format="ISO8601",
    )
    named = named.iloc[np.argsort(fetched.to_numpy(), kind="stable")]
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
) -> tuple[pd.DataFrame, tuple[pd.Timestamp | None, pd.Timestamp | None]]:
    """:attr:`Coverage.gaps`, and the span they are looked for in
    (:attr:`Coverage.start`, :attr:`Coverage.end`)."""
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
    ends: list[int] = []
    if len(begin):
        order = np.argsort(begin, kind="stable")
        begin, finish = begin[order], np.maximum.accumulate(finish[order])
        spans = list(zip(finish[:-1], begin[1:]))
        ends = [begin[0], finish[-1]]
        if len(file_start):
            spans = [(file_start.min(), begin[0]), *spans, (finish[-1], file_end.max())]
    elif len(file_start):
        spans = [(file_start.min(), file_end.max())]
    if len(file_start):
        ends += [file_start.min(), file_end.max()]
    span = (
        (_times(np.array([min(ends)]), zone)[0], _times(np.array([max(ends)]), zone)[0])
        if ends
        else (None, None)
    )
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
    return frame, span


# --- composition, spectrum, top ----------------------------------------------


def _formula_keys(store: CampaignStore) -> list[str]:
    keys = store.table("species")["series_key"].astype(str)
    return sorted(keys[keys.str.startswith("formula:")])


def _species(store: CampaignStore, keys: Sequence[Key]) -> pd.DataFrame:
    """The species columns of *keys*, indexed by key name: a sum's row has
    its members' names joined by `` + `` and no other column."""
    members = list(dict.fromkeys(m for k in keys for m in _members(k) if m != TIC))
    if members:
        species = store.table("species", keys=members)
    else:
        # The parquet filter takes no empty list of keys
        species = store.table("species").iloc[:0]
    species = species.set_index(species["series_key"].astype(str))[
        list(SPECIES_COLUMNS)
    ]
    sums = [k for k in keys if not isinstance(k, str)]
    if not sums:
        return species
    names = _names(store, members)
    rows = species.iloc[:0].reindex([_key_name(k) for k in sums])
    rows["name"] = [" + ".join(names[m] for m in k) for k in sums]
    return pd.concat([species, rows])


def _grid_keys(store: CampaignStore) -> tuple[list[str], pd.DataFrame]:
    """Every formula key of the store with an m/z, and their species
    columns: the batch-peak grid a spectrum is drawn on. A formula key
    without an m/z has no place on it."""
    keys = _formula_keys(store)
    species = _species(store, keys)
    return [k for k in keys if not pd.isna(species.loc[k, "mz"])], species


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
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum.
    :return: With *by* None, one row per period and key with a value in
        it, key order within a period: ``period`` (its start), ``key``,
        ``median``, ``mean`` and ``n`` of its sample values, ``n_samples``
        (the period's samples of the batches that read the key, so that
        ``n`` over it is how often the key was detected where it could
        have been), ``share`` (its mean over the sum of the period's
        means), ``tier`` (the tier most of a formula key's points in the
        period carry, a tie going to the less confident of
        :data:`TIERS`; None for a target ion and a sum) and the species
        columns (:data:`SPECIES_COLUMNS`). With *by*, one row per period
        and group: ``period``, the group (``class`` or ``n_c``), ``mean``
        and ``share`` (the sums over its keys) and ``n_keys``.
    """
    if by is not None and by not in COMPOSITION_BY:
        raise ValueError(f"by={by!r} is none of {', '.join(COMPOSITION_BY)}")
    if period != "all" and period not in PERIODS:
        raise ValueError(f"period={period!r} is none of all, {', '.join(PERIODS)}")
    normalise = _check_normalise(normalise)
    items = _formula_keys(store) if keys is None else _check_keys(store, keys)
    keys = []
    if items:
        values = _read(
            store,
            items,
            normalise=normalise,
            tz=tz,
            start=start,
            end=end,
            view="composition",
        )
        zone, attrs, items = values.zone, values.attrs, values.items
        frame = _composed(store, values, period)
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
        columns.update(mean="float64", n="int64", n_samples="int64")
        frame = _empty(dict(columns, tier="object"), zone)
    tier = frame.pop("tier")
    frame["share"] = frame["mean"] / frame.groupby("period")["mean"].transform("sum")
    frame["tier"] = tier
    species = _species(store, items)
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


def _composed(store: CampaignStore, values: _Values, period: str) -> pd.DataFrame:
    """:func:`composition`'s ``period``, ``key``, ``median``, ``mean``,
    ``n``, ``n_samples`` and ``tier``: a row per period and key with a value
    in it."""
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
    # Per period, the samples of each batch, then of the batches reading a key
    per_batch = np.zeros((len(starts), len(values.present)), dtype="int64")
    np.add.at(per_batch, (code[reads_any], values.batch[reads_any]), 1)
    n_samples = per_batch @ values.present.astype("int64")
    tiers = _tiers(store, values, code)
    return pd.DataFrame(
        {
            "period": _times(starts.asi8[at // n_keys], values.zone),
            "key": [values.keys[k] for k in at % n_keys],
            "median": stats["median"].to_numpy(),
            "mean": stats["mean"].to_numpy(),
            "n": stats["n"].to_numpy("int64"),
            "n_samples": n_samples[at // n_keys, at % n_keys].astype("int64"),
            "tier": pd.Series(tiers.reindex(at).to_numpy(), dtype=object),
        }
    )


def _tiers(store: CampaignStore, values: _Values, code: np.ndarray) -> pd.Series:
    """The tier most of each formula key's points carry, per period: indexed
    by ``period code * len(keys) + key``. A tie goes to the less confident
    tier of :data:`TIERS`, and a tier the server names that is none of them
    is less confident than all of them.

    :param code: Each sample's period, as a code; -1 for none.
    """
    n_keys = len(values.keys)
    formula = [
        k
        for k, members in enumerate(values.members)
        if len(members) == 1 and members[0].startswith("formula:")
    ]
    if not formula:
        return pd.Series([], dtype=object)
    rows = store.table("peak_series", keys=[values.keys[k] for k in formula])
    index = pd.Index(values.samples["sample_item_id"].astype(str))
    pos = _positions(index, rows["sample_item_id"])
    key = _positions(pd.Index(values.keys), rows["series_key"])
    # Integer codes, not text: a month holds hundreds of thousands of rows
    tier, names = pd.factorize(rows["tier"])
    kept = (pos >= 0) & (key >= 0) & (tier >= 0)
    if not kept.any():
        return pd.Series([], dtype=object)
    groups, group = np.unique(code[pos[kept]] * n_keys + key[kept], return_inverse=True)
    counts = np.bincount(
        group * len(names) + tier[kept], minlength=len(groups) * len(names)
    ).reshape(len(groups), len(names))
    # The tiers from the least confident, so that the first of the most
    # counted is the less confident of a tie
    rank = {name: i for i, name in enumerate(TIERS)}
    order = np.argsort(
        [-rank.get(str(name), len(TIERS)) for name in names], kind="stable"
    )
    best = order[np.argmax(counts[:, order], axis=1)]
    return pd.Series(
        np.asarray(names, dtype=object)[best].astype(str).astype(object), index=groups
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
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum.
    :return: One row per key with a value in *period* or *other*, by m/z:
        ``key``, ``mz``, ``name``, ``formula``, ``ion_formula``, ``value``
        (the mean of its sample values in *period*), ``n`` and
        ``n_samples`` (the period's samples of the batches that read the
        key: ``n`` over it is how often it was detected where it could have
        been); with *other*, also ``other_value``, ``other_n``,
        ``other_n_samples``, ``diff`` (``value`` less ``other_value``, a
        key without a value in one of them counting 0 there) and ``ratio``
        (``value`` over ``other_value``, NaN without both). ``attrs`` adds
        ``period`` and ``other``, ISO in UTC, and ``period_drawn`` and
        ``other_drawn``, the times of the first and the last sample each
        draws from (of a batch that reads a key), ISO in UTC, None without
        one; its ``mixed_runs`` and ``run_changes`` are over the batches
        both windows draw and those between them, so a change between them
        is flagged at the batch where the run changed.
    """
    normalise = _check_normalise(normalise)
    zone = display_zone(store, tz)
    windows = {"period": _window(period, zone)}
    if other is not None:
        windows["other"] = _window(other, zone)
    if keys is None:
        keys, species = _grid_keys(store)
        items = keys
    else:
        items = _check_keys(store, keys, tic=False)
        species = _species(store, items)
        keys = [_key_name(k) for k in items]
        without = [k for k in keys if pd.isna(species.loc[k, "mz"])]
        if without:
            raise ValueError(
                f"No m/z to draw a spectrum on for {', '.join(without)}: the "
                "batch peaks' formula keys have one, target ions and sums do not"
            )

    attrs = _attrs(
        store,
        "spectrum",
        zone,
        keys=keys,
        normalise=normalise,
        normalise_name=_normaliser_name(store, normalise),
    )
    attrs.update(cadence="period", other=None, period_drawn=None, other_drawn=None)
    for name, window in windows.items():
        attrs[name] = [None if w is None else w.isoformat() for w in window]
    if not keys:
        columns = {"key": "object", "mz": "float64", "name": "object"}
        columns.update(formula="object", ion_formula="object", value="float64")
        columns.update(n="int64", n_samples="int64")
        if other is not None:
            columns.update(other_value="float64", other_n="int64")
            columns.update(other_n_samples="int64", diff="float64", ratio="float64")
        return _stamp(_empty(columns, zone), attrs)

    means = {}
    complete = [0, 0]
    reading: set[str] = set()
    drawn: set[str] = set()
    for name, window in windows.items():
        values = _read(
            store,
            items,
            normalise=normalise,
            tz=tz,
            start=None,
            end=None,
            view="spectrum",
            window=window,
        )
        complete = [a + b for a, b in zip(complete, values.complete)]
        reading |= values.reading
        drawn.update(values.batch_ids)
        grouped = _grouped(values.key, values.value)
        means[name] = pd.DataFrame(
            {"value": grouped.mean(), "n": grouped.count()}
        ).reindex(range(len(keys)))
        # The window's samples of each batch, then of the batches reading a key
        per_batch = np.bincount(values.batch, minlength=len(values.present))
        means[name]["n_samples"] = per_batch @ values.present.astype("int64")
        times = values.t[values.reads_any]
        if len(times):
            attrs[f"{name}_drawn"] = [
                pd.Timestamp(t, tz="UTC").isoformat()
                for t in (times.min(), times.max())
            ]
    frame = pd.DataFrame(
        {
            "key": keys,
            "mz": species.loc[keys, "mz"].to_numpy("float64"),
            "name": species.loc[keys, "name"].to_numpy(),
            "formula": species.loc[keys, "formula"].to_numpy(),
            "ion_formula": species.loc[keys, "ion_formula"].to_numpy(),
            "value": means["period"]["value"].to_numpy(),
            "n": means["period"]["n"].fillna(0).to_numpy("int64"),
            "n_samples": means["period"]["n_samples"].to_numpy("int64"),
        }
    )
    seen = frame["n"] > 0
    if other is not None:
        frame["other_value"] = means["other"]["value"].to_numpy()
        frame["other_n"] = means["other"]["n"].fillna(0).to_numpy("int64")
        frame["other_n_samples"] = means["other"]["n_samples"].to_numpy("int64")
        frame["diff"] = frame["value"].fillna(0) - frame["other_value"].fillna(0)
        frame["ratio"] = frame["value"] / frame["other_value"]
        seen |= frame["other_n"] > 0
    frame = frame[seen].sort_values(["mz", "key"], kind="stable").reset_index(drop=True)
    # A comparison of two windows joins what each reads: flag a run change
    # over the batches of both, and between them
    attrs.update(_run_attrs(store, reading, drawn, between=True))
    if complete[1]:
        # Over the samples of both windows
        attrs["normalise_complete"] = complete[0] / complete[1]
    return _stamp(frame, attrs)


@dataclass(frozen=True)
class Spectrogram:
    """The assigned signal over time on a fixed m/z grid.

    Each grid row is an m/z range of :attr:`mz_width`, centred on a multiple
    of it, holding the formula keys whose batch peaks' m/z (the ion's, its
    adduct included) falls in it. A sample's value of a row is the sum of
    its keys' values in it; a cell's value is the **mean** of that over the
    bin's samples with a value, never their sum, which would grow with the
    samples in the bin and draw coverage instead of signal.

    :ivar cells: One row per cell with a value, oldest bin first, then by
        m/z: ``t`` (its bin's start), ``t_end`` (the next bin's), ``mz``
        (its row's centre), ``value``, ``n`` (the bin's samples with a
        value of the row) and ``n_samples`` (the bin's samples of the
        batches that read a key of the row). A cell whose samples hold no
        value of its row is no row: blank, not zero.
    :ivar bins: One row per time bin from the first sample's to the last's:
        ``t``, ``t_end``, ``n_samples`` (its samples of the batches that read
        a key; 0 for a bin without one, a gap) and ``total`` (the column
        sum: the sum of its cells' values, NaN without a cell).
    :ivar rows: One row per grid row from the lowest holding a key to the
        highest: ``mz``, ``n_keys``, ``n`` (the window's samples with a
        value of it), ``n_samples`` (those of the batches that read one of
        its keys), ``total`` (the sum of its cells' values, NaN without
        one), and the key that contributes most to it,
        the one whose values over the window's samples sum to the most:
        ``key``, ``name``, ``formula`` and ``share`` (its sum over the
        row's), None and NaN for a row without a value.
    :ivar mz_width: The rows' width, in Th.
    :ivar widened: Whether :attr:`mz_width` is wider than asked, to keep the
        grid under ``max_rows`` rows.
    """

    cells: pd.DataFrame
    bins: pd.DataFrame
    rows: pd.DataFrame
    mz_width: float
    widened: bool


_CELL_COLUMNS = {
    "t": "time",
    "t_end": "time",
    "mz": "float64",
    "value": "float64",
    "n": "int64",
    "n_samples": "int64",
}

_BIN_COLUMNS = {"t": "time", "t_end": "time", "n_samples": "int64", "total": "float64"}


def _check_width(mz_width: Any) -> float:
    if isinstance(mz_width, bool) or not isinstance(
        mz_width, (int, float, np.integer, np.floating)
    ):
        raise ValueError(f"mz_width={mz_width!r} is no width in Th")
    width = float(mz_width)
    if not math.isfinite(width) or width <= 0:
        raise ValueError(f"mz_width={mz_width!r} is no positive width in Th")
    return width


def _grid_rows(mz: np.ndarray, width: float) -> np.ndarray:
    """Each m/z's grid row, as the multiple of *width* nearest it."""
    return np.floor(mz / width + 0.5).astype("int64")


def _grid_width(mz: np.ndarray, width: float, max_rows: int) -> tuple[float, bool]:
    """*width*, or the smallest whole multiple of it whose grid over *mz*
    holds at most *max_rows* rows; whether it widened."""

    def rows(w: float) -> int:
        cells = _grid_rows(mz, w)
        return int(cells.max() - cells.min()) + 1

    if rows(width) <= max_rows:
        return width, False
    # The rows at width w lie between span / w and span / w + 2, so no
    # factor f with f * max_rows <= rows(width) - 2 can hold the grid:
    # start at the first that might, never past the smallest that does
    factor = max(2, (rows(width) - 2) // max_rows + 1)
    while rows(factor * width) > max_rows:
        factor += 1
    return factor * width, True


def spectrogram(
    store: CampaignStore,
    *,
    mz_width: float = 1.0,
    cadence: str = "1h",
    normalise: str = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
    max_rows: int = SPECTROGRAM_MAX_ROWS,
) -> Spectrogram:
    """The assigned signal over time, binned by time and onto a fixed m/z
    grid: the data of a spectrogram.

    The store holds the batch peaks with a consensus formula, so this is
    the assigned signal only (:class:`Spectrogram`).

    :param mz_width: The grid rows' width in Th; 1, the default, centres
        them on whole numbers. When the rows from the lowest m/z to the
        highest would pass *max_rows*, they widen to the smallest whole
        multiple of it that keeps under it (:attr:`Spectrogram.widened`).
    :param cadence: The time bins: a pandas offset, as :func:`series`
        takes one (``10min``, ``1h``, ``1D``); an hour by default.
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum; each value is divided by its own
        sample's before it is summed into a row.
    :param tz: The zone the bins are in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    :param max_rows: The rows the grid may hold.
    :return: The cells, the bins and the rows. Each frame's ``attrs`` are a
        view's, with ``cadence``, ``stat`` (``mean``), ``mz_width``,
        ``mz_width_asked``, ``widened`` and ``max_rows``.
    """
    width = _check_width(mz_width)
    if (
        isinstance(max_rows, bool)
        or not isinstance(max_rows, (int, np.integer))
        or max_rows < 1
    ):
        raise ValueError(f"max_rows={max_rows!r} is no positive count")
    if cadence in ("auto", "raw"):
        raise ValueError(
            f"cadence={cadence!r}: a spectrogram's cells are bins of one step; "
            "give a pandas offset such as '10min', '1h' or '1D'"
        )
    offset = _offset(cadence, auto_raw=False)
    normalise = _check_normalise(normalise)
    keys, species = _grid_keys(store)
    mz = species.loc[keys, "mz"].to_numpy("float64") if keys else np.array([])
    widened = False
    if keys:
        width, widened = _grid_width(mz, width, int(max_rows))
    extra = {
        "cadence": _cadence_name(offset),
        "stat": "mean",
        "mz_width": width,
        "mz_width_asked": _check_width(mz_width),
        "widened": widened,
        "max_rows": int(max_rows),
    }
    if not keys:
        # A store without a batch ledger has no formula on the grid
        zone = display_zone(store, tz)
        attrs = _attrs(
            store,
            "spectrogram",
            zone,
            normalise=normalise,
            normalise_name=_normaliser_name(store, normalise),
            start=_bound(start, zone),
            end=_bound(end, zone),
        )
        return _spectrogram_of(
            _empty(_CELL_COLUMNS, zone),
            _empty(_BIN_COLUMNS, zone),
            _grid_frame(np.array([]), np.array([], "int64")),
            dict(attrs, **extra),
            width,
            widened,
        )
    values = _read(
        store,
        keys,
        normalise=normalise,
        tz=tz,
        start=start,
        end=end,
        view="spectrogram",
    )
    attrs = dict(values.attrs, **extra)
    zone = values.zone
    cell = _grid_rows(mz, width)
    first_row = int(cell.min())
    n_rows = int(cell.max()) - first_row + 1
    row_of = cell - first_row
    centres = np.round((first_row + np.arange(n_rows)) * width, 10)
    n_keys = np.bincount(row_of, minlength=n_rows)
    reads_any = values.reads_any
    if not reads_any.any():
        return _spectrogram_of(
            _empty(_CELL_COLUMNS, zone),
            _empty(_BIN_COLUMNS, zone),
            _grid_frame(centres, n_keys),
            attrs,
            width,
            widened,
        )

    t = values.t
    edges = _edges(
        pd.Timestamp(t[reads_any].min(), tz="UTC"),
        pd.Timestamp(t[reads_any].max(), tz="UTC"),
        offset,
        zone,
    )
    sample_bin = np.searchsorted(edges, t, side="right") - 1
    first = int(sample_bin[reads_any].min())
    n_bins = int(sample_bin[reads_any].max()) - first + 1
    sample_bin = sample_bin - first
    starts = edges[first : first + n_bins]
    ends = edges[first + 1 : first + n_bins + 1]

    # A sample's value of a row: the sum of its keys' values in it
    found = np.isfinite(values.value)
    pos, key, value = values.pos[found], values.key[found], values.value[found]
    per_sample = pd.Series(value).groupby(pos * n_rows + row_of[key], sort=True).sum()
    code = per_sample.index.to_numpy("int64")
    # A cell's: the mean over its bin's samples with a value
    cells = pd.Series(per_sample.to_numpy()).groupby(
        sample_bin[code // n_rows] * n_rows + code % n_rows, sort=True
    )
    mean, count = cells.mean(), cells.count()
    at = mean.index.to_numpy("int64")

    # The samples that could hold a row's value: those of a batch reading
    # one of its keys
    reads_row = np.zeros((n_rows, len(values.present)), dtype=bool)
    np.logical_or.at(reads_row, row_of, values.present.T)
    per_batch = np.zeros((n_bins, len(values.present)), dtype="int64")
    np.add.at(per_batch, (sample_bin[reads_any], values.batch[reads_any]), 1)
    could = per_batch @ reads_row.T.astype("int64")

    cell_frame = pd.DataFrame(
        {
            "t": _times(starts[at // n_rows], zone),
            "t_end": _times(ends[at // n_rows], zone),
            "mz": centres[at % n_rows],
            "value": mean.to_numpy("float64"),
            "n": count.to_numpy("int64"),
            "n_samples": could[at // n_rows, at % n_rows].astype("int64"),
        }
    )
    by_bin = pd.Series(mean.to_numpy()).groupby(at // n_rows).sum()
    bin_frame = pd.DataFrame(
        {
            "t": _times(starts, zone),
            "t_end": _times(ends, zone),
            "n_samples": per_batch.sum(axis=1).astype("int64"),
            "total": _fill(n_bins, by_bin.index.to_numpy("int64"), by_bin),
        }
    )
    by_row = pd.Series(mean.to_numpy()).groupby(at % n_rows).sum()
    rows = _grid_frame(centres, n_keys)
    rows["n"] = np.bincount(code % n_rows, minlength=n_rows).astype("int64")
    rows["n_samples"] = could.sum(axis=0).astype("int64")
    rows["total"] = _fill(n_rows, by_row.index.to_numpy("int64"), by_row)
    # The key that contributes most to each row, over the window's samples
    summed = pd.Series(value).groupby(key).sum()
    per_key = _fill(len(keys), summed.index.to_numpy("int64"), summed)
    _contributors(rows, keys, species, row_of, per_key)
    return _spectrogram_of(cell_frame, bin_frame, rows, attrs, width, widened)


def _grid_frame(centres: np.ndarray, n_keys: np.ndarray) -> pd.DataFrame:
    """:attr:`Spectrogram.rows` before its values are read."""
    n = len(centres)
    return pd.DataFrame(
        {
            "mz": np.asarray(centres, dtype="float64"),
            "n_keys": np.asarray(n_keys, dtype="int64"),
            "n": np.zeros(n, dtype="int64"),
            "n_samples": np.zeros(n, dtype="int64"),
            "total": np.full(n, np.nan),
            "key": pd.Series([None] * n, dtype=object),
            "name": pd.Series([None] * n, dtype=object),
            "formula": pd.Series([None] * n, dtype=object),
            "share": np.full(n, np.nan),
        }
    )


def _contributors(
    rows: pd.DataFrame,
    keys: list[str],
    species: pd.DataFrame,
    row_of: np.ndarray,
    per_key: np.ndarray,
) -> None:
    """Fill *rows*' ``key``, ``name``, ``formula`` and ``share`` from each
    key's sum over the window (*per_key*, NaN without a value): its row's
    largest, a tie going to the key first in order."""
    order = pd.DataFrame({"row": row_of, "sum": per_key, "key": np.arange(len(keys))})
    order = order[np.isfinite(order["sum"].to_numpy())]
    if order.empty:
        return
    totals = order.groupby("row")["sum"].sum()
    best = order.sort_values(
        ["row", "sum", "key"], ascending=[True, False, True], kind="stable"
    ).drop_duplicates("row")
    chosen = [keys[k] for k in best["key"]]
    at = best["row"].to_numpy("int64")
    rows.loc[at, "key"] = chosen
    rows.loc[at, "name"] = species.loc[chosen, "name"].to_numpy()
    rows.loc[at, "formula"] = species.loc[chosen, "formula"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        rows.loc[at, "share"] = best["sum"].to_numpy() / totals.loc[at].to_numpy()


def _spectrogram_of(
    cells: pd.DataFrame,
    bins: pd.DataFrame,
    rows: pd.DataFrame,
    attrs: dict[str, Any],
    width: float,
    widened: bool,
) -> Spectrogram:
    for frame in (cells, bins, rows):
        _stamp(frame, dict(attrs))
    return Spectrogram(cells, bins, rows, mz_width=width, widened=widened)


def top(
    store: CampaignStore,
    n: int,
    by: str,
    *,
    keys: Any = None,
    period: str = "week",
    normalise: str = "none",
    min_present: float = 0.5,
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> pd.DataFrame:
    """The keys that stand out: the brightest, the biggest changers, the
    new ones.

    The first and last periods are the calendar periods (*period*, in
    *tz*) of the window's first and last samples. A key's presence in some
    samples is its values detected in them over those of them that could
    have held one (the samples of the batches that read it): below
    detection is missing, so a median of a few detections reads high, and a
    ratio of a handful of them is noise.

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
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum.
    :param min_present: Rank only the keys present in at least this share
        of the samples compared, from 0 to 1: over the window by
        ``median``, in each of the first and last periods by ``ratio``, in
        the last period by ``new``.
    :return: Up to *n* rows, best first: ``rank``, ``key``, ``value`` (what
        it is ranked by: the median, the ratio, the last), ``median``,
        ``first`` and ``last`` (the medians in the first and last periods),
        ``ratio`` (``last`` over ``first``), ``new``, ``n`` (its values
        detected in the window) and ``n_samples`` (the window's samples
        that could have held one), ``presence`` (the share *min_present*
        was held to: ``n`` over ``n_samples`` by ``median``, the lower of
        the two periods' by ``ratio``, the last period's by ``new``),
        ``name``, ``formula`` and ``ion_formula``. ``attrs`` adds ``by``,
        ``period``, ``min_present``, ``first_period`` and ``last_period``
        (their starts, ISO in *tz*).
    """
    if by not in TOP_BY:
        raise ValueError(f"by={by!r} is none of {', '.join(TOP_BY)}")
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 1:
        raise ValueError(f"n={n!r} is no positive count")
    if (
        isinstance(min_present, bool)
        or not isinstance(min_present, (int, float, np.number))
        or not 0 <= min_present <= 1
    ):
        raise ValueError(f"min_present={min_present!r} is no share from 0 to 1")
    if period not in PERIODS:
        raise ValueError(f"period={period!r} is none of {', '.join(PERIODS)}")
    if keys is None:
        keys = sorted(store.table("species")["series_key"].astype(str))
    values = _read(
        store, keys, normalise=normalise, tz=tz, start=start, end=end, view="top"
    )
    attrs = dict(
        values.attrs, cadence=period, by=by, period=period, min_present=min_present
    )
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
    # Per key: the samples that could have held a value, and those that do
    could = values.present[values.batch]

    def presence_in(selected: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        held = np.bincount(
            values.key[detected & selected[values.pos]], minlength=n_keys
        )
        return held, could[selected].sum(axis=0)

    def share(counts: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
        held, could_hold = counts
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(could_hold > 0, held / could_hold, np.nan)

    window = presence_in(np.ones(len(values.t), dtype=bool))
    frame["n"] = window[0].astype("int64")
    frame["n_samples"] = window[1].astype("int64")
    species = _species(store, values.items)
    frame["name"] = frame["key"].map(species["name"])
    frame["formula"] = frame["key"].map(species["formula"])
    frame["ion_formula"] = frame["key"].map(species["ion_formula"])

    if by == "median":
        frame["value"] = frame["median"]
        frame["presence"] = share(window)
        score = frame["median"]
    elif by == "ratio":
        frame["value"] = frame["ratio"]
        frame["presence"] = np.minimum(
            share(presence_in(code == 0)), share(presence_in(code == last))
        )
        # Up or down alike; without a value in both periods, or with the
        # one period as both, no change
        score = np.abs(np.log(frame["ratio"]))
        if last < 1:
            score = pd.Series(np.nan, index=frame.index)
    else:
        frame["value"] = frame["last"]
        frame["presence"] = share(presence_in(code == last))
        score = frame["last"].where(frame["new"])
    # A key too seldom detected is not ranked
    frame["score"] = score.where(frame["presence"] >= min_present)
    frame = (
        frame[frame["score"].notna()]
        .sort_values(["score", "key"], ascending=[False, True], kind="stable")
        .head(n)
        .reset_index(drop=True)
    )
    frame.insert(0, "rank", np.arange(1, len(frame) + 1))
    columns = ["rank", "key", "value", "median", "first", "last", "ratio", "new"]
    columns += ["n", "n_samples", "presence", "name", "formula", "ion_formula"]
    return _stamp(frame[columns], attrs)


def amplitude(
    store: CampaignStore,
    keys: Any = None,
    *,
    cadence: str = "1h",
    normalise: str = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> pd.DataFrame:
    """How much each key's binned medians vary: the coefficient of variation
    (their standard deviation over their mean) of its :func:`series`
    medians at *cadence*, over the bins with a value. A Van Krevelen colours
    by it. Under ``normalise="none"`` it includes the instrument's drift.

    :param keys: The series keys; None for every formula key of the store.
    :param cadence: The bins, a pandas offset; an hour by default.
    :param normalise: ``none``, ``tic``, a series key, or a sequence of
        them to divide by their sum.
    :return: One row per key, in the keys' order: ``key``, ``cv`` (NaN with
        fewer than two bins with a value), ``mean`` (of the
        medians) and ``n_bins`` (bins with a value). ``attrs`` are
        :func:`series`' with ``view`` ``amplitude``.
    """
    if keys is None:
        keys = _formula_keys(store)
    if not len(keys):
        zone = display_zone(store, tz)
        attrs = _attrs(
            store,
            "amplitude",
            zone,
            normalise=_check_normalise(normalise),
            normalise_name=_normaliser_name(store, _check_normalise(normalise)),
            start=_bound(start, zone),
            end=_bound(end, zone),
        )
        frame = _empty(
            {"key": "object", "cv": "float64", "mean": "float64", "n_bins": "int64"},
            zone,
        )
        return _stamp(frame, dict(attrs, cadence=_cadence_name(_offset(cadence))))
    binned = series(
        store,
        keys,
        cadence=cadence,
        normalise=normalise,
        stat="median",
        band=None,
        tz=tz,
        start=start,
        end=end,
    )
    names = list(binned.attrs["keys"])
    grouped = binned.groupby(
        pd.Categorical(binned["key"].astype(str), categories=names), observed=False
    )["value"]
    mean = grouped.mean().reindex(names)
    cv = grouped.std(ddof=1).reindex(names) / mean
    frame = pd.DataFrame(
        {
            "key": names,
            "cv": cv.to_numpy("float64"),
            "mean": mean.to_numpy("float64"),
            "n_bins": grouped.count().reindex(names).to_numpy("int64"),
        }
    )
    return _stamp(frame, dict(binned.attrs, view="amplitude"))
