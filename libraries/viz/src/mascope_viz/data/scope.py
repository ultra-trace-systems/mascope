"""What a digest is about: a :class:`Scope`, and what it resolves to.

A scope names data in Mascope's own terms - a dataset, some batches, an
instrument - plus a time range, a polarity, an ionization mode, a target
collection and the time zone to show times in
(``docs/dev/visual_digest.md``, section 2.2). :func:`resolve` turns it into a
:class:`ResolvedScope` through the SDK's listings: the batches it covers, the
files acquired in its range whether or not a batch holds them, the
collections on those batches and the instrument's time zone.

Names match as everywhere in the SDK: a plain string is a case-insensitive
**literal** substring (``"Blank (A)"`` is no regex group), a compiled
``re.Pattern`` a regex. An object's ID, then its whole name, win over a part
of a name. A string that names several objects is not an error: the scope
resolves to their candidates, as the AI layer's ``find`` answers with them,
and is resolved again with one of them. Of ``batches``, a string names one
batch and a compiled pattern every batch it matches.

Time is the sample file's start (``datetime_utc``): a sample is in the range
when its file started in it, from ``start`` up to but not including
``end``. A bound without a zone is in the scope's ``tz``, which is the
instrument's acquisition zone unless another is named.
"""

import hashlib
import json
import re
import zoneinfo
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..errors import InvalidScope, NotFound, listed, sdk_errors


if TYPE_CHECKING:
    from mascope_sdk import MascopeClient


#: The polarities a scope takes, and how each is written once resolved.
POLARITIES = {
    "+": "+",
    "pos": "+",
    "positive": "+",
    "-": "-",
    "neg": "-",
    "negative": "-",
}

#: ``tz`` for the instrument's own acquisition time zone.
INSTRUMENT_TZ = "instrument"

#: The columns of :attr:`ResolvedScope.batches`, in order.
BATCH_COLUMNS = [
    "sample_batch_id",
    "sample_batch_name",
    "dataset_id",
    "dataset_name",
    "polarity",
    "n_samples",
    "first_datetime_utc",
    "last_datetime_utc",
    "sample_batch_utc_modified",
]

#: The columns of :attr:`ResolvedScope.collections`, in order.
COLLECTION_COLUMNS = [
    "target_collection_id",
    "target_collection_name",
    "target_collection_type",
    "workspace_id",
    "sample_batch_ids",
]

#: The columns :attr:`ResolvedScope.files` has at least, as the files listing
#: names them, with ``in_batch`` last.
FILE_COLUMNS = [
    "sample_file_id",
    "filename",
    "instrument",
    "instrument_type",
    "datetime_utc",
    "datetime",
    "acquisition_timezone",
    "length",
    "polarity",
    "in_batch",
]

#: How far a range is widened, each way, to find the instrument's zone before
#: its bounds are known in UTC: more than any zone's offset.
_ZONE_MARGIN = pd.Timedelta(days=1)


def _timestamp(value: Any, name: str) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        moment = pd.Timestamp(value)
    except (TypeError, ValueError) as e:
        raise InvalidScope(
            f"{name}={value!r} is not a time: give an ISO 8601 string, a "
            "datetime or a pandas Timestamp"
        ) from e
    if moment is pd.NaT:
        raise InvalidScope(f"{name} is NaT: give a time, or None for no bound")
    return moment


def _check_name(value: Any, name: str) -> None:
    if not isinstance(value, (str, re.Pattern)):
        raise InvalidScope(
            f"{name} is a name, part of one or an ID (a string), or a compiled "
            f"re.Pattern, not {value!r}"
        )


def _name_content(value: "str | re.Pattern | None") -> Any:
    if isinstance(value, re.Pattern):
        return {"re": value.pattern, "flags": int(value.flags)}
    return value


@dataclass(frozen=True)
class Scope:
    """What a digest is about.

    Every field narrows the scope; one left at None does not. A scope needs
    something to start from: a dataset, batches, an instrument or a time
    range.

    :ivar dataset: The dataset, by name, part of one, ID or pattern.
    :ivar batches: One batch, or several: each a string naming one batch,
        or a compiled pattern taking every batch it matches. Looked for in
        ``dataset`` when one is given, else in the instrument's datasets,
        else in every dataset of the client's workspace.
    :ivar instrument: The instrument, by its name or part of it: only its
        samples and files.
    :ivar start: Only what was acquired at this time or later.
    :ivar end: Only what was acquired before this time.
    :ivar polarity: ``"+"`` or ``"-"`` (``"positive"``, ``"negative"``,
        ``"pos"`` and ``"neg"`` say the same): only samples and files of it.
    :ivar mode: Only the samples of this ionization mode, by its ID. The
        modes listing is not open to an SDK token, so a mode's name cannot
        be looked up yet.
    :ivar collection: The target collection the digest draws, by name, part
        of one, ID or pattern, among those on the scope's batches.
    :ivar tz: The time zone to show times in, and the one a bound without a
        zone is in: an IANA name such as ``"UTC"`` or ``"Europe/Helsinki"``,
        or ``"instrument"`` (the default) for the instrument's acquisition
        zone.
    """

    dataset: "str | re.Pattern | None" = None
    batches: "str | re.Pattern | Sequence[str | re.Pattern] | None" = None
    instrument: str | None = None
    start: "str | datetime | pd.Timestamp | None" = None
    end: "str | datetime | pd.Timestamp | None" = None
    polarity: str | None = None
    mode: str | None = None
    collection: "str | re.Pattern | None" = None
    tz: str = INSTRUMENT_TZ

    def __post_init__(self):
        for name in ("dataset", "collection"):
            value = getattr(self, name)
            if value is not None:
                _check_name(value, name)
        for name in ("instrument", "mode"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise InvalidScope(f"{name} is a non-empty string, not {value!r}")

        batches = self.batches
        if batches is not None:
            if isinstance(batches, (str, re.Pattern)):
                batches = (batches,)
            elif isinstance(batches, Sequence):
                batches = tuple(batches)
            else:
                _check_name(batches, "batches")
            if not batches:
                raise InvalidScope(
                    "batches is empty: name at least one, or None for every "
                    "batch of the dataset"
                )
            for entry in batches:
                _check_name(entry, "each of batches")
            object.__setattr__(self, "batches", batches)

        for name in ("start", "end"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        start, end = self.start, self.end
        if (
            start is not None
            and end is not None
            and (start.tzinfo is None) == (end.tzinfo is None)
            and start >= end
        ):
            raise InvalidScope(f"start ({start}) is not before end ({end})")

        if self.polarity is not None:
            polarity = POLARITIES.get(str(self.polarity).strip().lower())
            if polarity is None:
                raise InvalidScope(
                    f"polarity is '+' or '-' (or positive, negative), not "
                    f"{self.polarity!r}"
                )
            object.__setattr__(self, "polarity", polarity)

        if self.tz != INSTRUMENT_TZ:
            try:
                zoneinfo.ZoneInfo(self.tz)
            except (zoneinfo.ZoneInfoNotFoundError, TypeError, ValueError) as e:
                raise InvalidScope(
                    f"tz={self.tz!r} is no time zone: give an IANA name such "
                    f"as 'UTC' or 'Europe/Helsinki', or {INSTRUMENT_TZ!r}"
                ) from e

        if not any(
            value is not None
            for value in (self.dataset, self.batches, self.instrument, start, end)
        ):
            raise InvalidScope(
                "A scope needs something to start from: a dataset, batches, "
                "an instrument, or a time range (start, end)"
            )

    def _bound(self, moment: pd.Timestamp | None) -> str | None:
        if moment is None:
            return None
        if moment.tzinfo is None:
            # A naive bound means a time in tz, and is one only with it
            return f"{moment.isoformat()} in {self.tz}"
        return moment.tz_convert("UTC").isoformat()

    def content(self) -> dict[str, Any]:
        """What the scope selects, as plain JSON values: what :attr:`key`
        is the hash of.

        ``tz`` is in it only as the zone of a bound written without one:
        the zone times are shown in selects nothing.
        """
        return {
            "dataset": _name_content(self.dataset),
            "batches": (
                None
                if self.batches is None
                else [_name_content(entry) for entry in self.batches]
            ),
            "instrument": self.instrument,
            "start": self._bound(self.start),
            "end": self._bound(self.end),
            "polarity": self.polarity,
            "mode": self.mode,
            "collection": _name_content(self.collection),
        }

    @property
    def key(self) -> str:
        """A short, stable hash of :meth:`content`.

        The key of a scope as written changes with its spelling; the key of
        the scope it resolves to (:attr:`ResolvedScope.key`) does not, and
        is the one the campaign store names its directory by.
        """
        text = json.dumps(self.content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Candidate:
    """One of the objects an ambiguous name matches.

    :ivar kind: ``dataset``, ``batch``, ``instrument`` or ``collection``.
    :ivar id: Its ID (an instrument's is its name).
    :ivar name: Its name.
    :ivar parent: The dataset of a batch; None for the rest.
    """

    kind: str
    id: str
    name: str
    parent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "parent": self.parent,
        }


@dataclass(frozen=True, eq=False)
class ResolvedScope:
    """What a scope covers, as the server had it when it was resolved.

    :ivar scope: The scope resolved: names replaced by the IDs they named
        (a pattern of ``batches`` kept as it is, so that a batch added later
        is in it too), bounds in UTC. Its key is :attr:`key`.
    :ivar requested: The scope as it was given.
    :ivar batches: One row per batch, oldest first (by its first sample):
        ``sample_batch_id``, ``sample_batch_name``, ``dataset_id``,
        ``dataset_name``, ``polarity``, ``n_samples`` (its samples in the
        scope), ``first_datetime_utc`` and ``last_datetime_utc`` (theirs),
        ``sample_batch_utc_modified``. A batch with no sample in the scope
        is not in it.
    :ivar files: One row per file acquired in the range, oldest first,
        whether or not a batch holds a sample of it, with the columns of
        ``client.sample_files.list`` and ``in_batch``: whether a sample of
        it is in one of :attr:`batches`. Without a time range the range is
        that of the batches' samples.
    :ivar collections: One row per target collection associated with some
        of :attr:`batches`: ``target_collection_id``,
        ``target_collection_name``, ``target_collection_type``,
        ``workspace_id`` (None for a global one) and ``sample_batch_ids``.
    :ivar collection: The ID of the collection the scope names, if it does.
    :ivar timezone: The instrument's acquisition time zone, as its files in
        range record it; None when they do not.
    :ivar display_tz: The zone to show times in: the scope's ``tz``, or
        :attr:`timezone` for ``"instrument"`` (UTC when that is unknown).
    :ivar start_utc: The range's start in UTC, if it has one.
    :ivar end_utc: The range's end in UTC, if it has one.
    :ivar candidates: For each field holding an ambiguous name (``dataset``,
        ``instrument``, ``batches[0]``, ``collection``), the objects it
        matches. Empty when the scope resolved; when not, nothing past the
        ambiguity was read and the frames it would have filled are empty.
    """

    scope: Scope
    requested: Scope
    batches: pd.DataFrame
    files: pd.DataFrame
    collections: pd.DataFrame
    collection: str | None = None
    timezone: str | None = None
    display_tz: str = "UTC"
    start_utc: pd.Timestamp | None = None
    end_utc: pd.Timestamp | None = None
    candidates: dict[str, tuple[Candidate, ...]] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """The resolved scope's key: the same for every spelling of it."""
        return self.scope.key

    @property
    def ambiguous(self) -> bool:
        """Whether some name matched several objects (see
        :attr:`candidates`)."""
        return bool(self.candidates)

    def __repr__(self) -> str:
        if self.ambiguous:
            fields = ", ".join(
                f"{name}: {len(found)} candidates"
                for name, found in self.candidates.items()
            )
            return f"<ResolvedScope ambiguous ({fields})>"
        return (
            f"<ResolvedScope {self.key}: {len(self.batches)} batch(es), "
            f"{len(self.files)} file(s), tz {self.display_tz}>"
        )


# --- matching ----------------------------------------------------------------


def _rank(query: "str | re.Pattern", candidate: Candidate) -> int | None:
    """0 its ID, 1 its whole name, 2 part of it (or a regex hit); None when
    *candidate* does not match *query*."""
    if isinstance(query, re.Pattern):
        return 2 if query.search(candidate.name) else None
    if candidate.id == query:
        return 0
    name, wanted = candidate.name.casefold(), query.casefold()
    if name == wanted:
        return 1
    return 2 if wanted in name else None


def _choose(
    query: "str | re.Pattern", candidates: Sequence[Candidate], kind: str
) -> list[Candidate]:
    """What *query* names among *candidates*: one, or several when it is
    ambiguous (or a pattern).

    An ID, then a whole name, wins over a part of a name; two objects of the
    same whole name are both returned.

    :raises NotFound: When it matches none, naming those there are.
    """
    ranked = [
        (rank, candidate)
        for candidate in candidates
        if (rank := _rank(query, candidate)) is not None
    ]
    if not ranked:
        text = query.pattern if isinstance(query, re.Pattern) else query
        names = sorted({candidate.name for candidate in candidates}, key=str.casefold)
        raise NotFound(text, kind, names)
    best = min(rank for rank, _ in ranked)
    return [candidate for rank, candidate in ranked if rank == best]


# --- the listings ------------------------------------------------------------


def _datasets(client: "MascopeClient") -> pd.DataFrame:
    with sdk_errors("list the datasets of the workspace"):
        rows = client.datasets.list()
    if rows is None:
        return pd.DataFrame(columns=["dataset_id", "dataset_name", "instrument"])
    if "instrument" not in rows:
        rows = rows.assign(instrument=None)
    return rows


def _batches(client: "MascopeClient", datasets: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for dataset_id, dataset_name in zip(
        datasets["dataset_id"], datasets["dataset_name"]
    ):
        with sdk_errors(f"list the batches of dataset {dataset_name!r}"):
            rows = client.batches.list(dataset_id)
        if rows is not None and not rows.empty:
            frames.append(rows.assign(dataset_id=dataset_id, dataset_name=dataset_name))
    if not frames:
        return pd.DataFrame(
            columns=[
                "sample_batch_id",
                "sample_batch_name",
                "dataset_id",
                "dataset_name",
                "polarity",
                "sample_batch_utc_modified",
            ]
        )
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        "sample_batch_id", ignore_index=True
    )


def _samples(client: "MascopeClient", batches: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for batch_id, batch_name, dataset_id in zip(
        batches["sample_batch_id"], batches["sample_batch_name"], batches["dataset_id"]
    ):
        with sdk_errors(f"list the samples of batch {batch_name!r}"):
            rows = client.samples.list(batch=batch_id, dataset=dataset_id)
        if rows is not None and not rows.empty:
            frames.append(rows.assign(sample_batch_id=batch_id))
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    for column in (
        "sample_item_id",
        "sample_batch_id",
        "sample_file_id",
        "instrument",
        "polarity",
        "ionization_mode_id",
        "datetime_utc",
    ):
        if column not in frame:
            frame[column] = None
    frame["datetime_utc"] = pd.to_datetime(frame["datetime_utc"], utc=True)
    return frame


def _files(
    client: "MascopeClient",
    instruments: Sequence[str | None],
    lo: pd.Timestamp | None,
    hi: pd.Timestamp | None,
) -> pd.DataFrame:
    frames = []
    for instrument in instruments:
        whose = f" of instrument {instrument!r}" if instrument else ""
        with sdk_errors(f"list the files{whose} acquired in the range"):
            frames.append(
                client.sample_files.list(
                    datetime_min=lo, datetime_max=hi, instrument=instrument
                )
            )
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    for column in FILE_COLUMNS[:-1]:
        if column not in frame:
            frame[column] = None
    frame["datetime_utc"] = pd.to_datetime(frame["datetime_utc"], utc=True)
    return (
        frame.drop_duplicates("sample_file_id")
        .sort_values(["datetime_utc", "sample_file_id"], kind="stable")
        .reset_index(drop=True)
    )


def _zone_of(files: pd.DataFrame) -> str | None:
    """The acquisition zone most of *files* record; None when none does."""
    zones = files["acquisition_timezone"].dropna()
    zones = zones[zones.astype(str) != ""]
    if zones.empty:
        return None
    counts = zones.astype(str).value_counts()
    return str(sorted(counts.index, key=lambda zone: (-counts[zone], zone))[0])


def _in_utc(moment: pd.Timestamp | None, zone: str) -> pd.Timestamp | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        try:
            moment = moment.tz_localize(zone, ambiguous="raise", nonexistent="raise")
        except (ValueError, OverflowError) as e:
            # A wall time the clocks skip or repeat when they change
            raise InvalidScope(
                f"{moment} is not one time in {zone}: the clocks change around "
                "it. Give it with its UTC offset."
            ) from e
    return moment.tz_convert("UTC")


def _in_range(
    times: pd.Series, start: pd.Timestamp | None, end: pd.Timestamp | None
) -> pd.Series:
    mask = pd.Series(True, index=times.index)
    if start is not None:
        mask &= times >= start
    if end is not None:
        mask &= times < end
    return mask


def _collections(client: "MascopeClient", batches: pd.DataFrame) -> pd.DataFrame:
    found: dict[str, dict[str, Any]] = {}
    for batch_id, batch_name in zip(
        batches["sample_batch_id"], batches["sample_batch_name"]
    ):
        with sdk_errors(f"list the collections of batch {batch_name!r}"):
            rows = client.collections.list(batch_id=batch_id)
        if rows is None:
            continue
        for row in rows.to_dict("records"):
            entry = found.setdefault(
                row["target_collection_id"],
                {
                    "target_collection_id": row["target_collection_id"],
                    "target_collection_name": row.get("target_collection_name"),
                    "target_collection_type": row.get("target_collection_type"),
                    "workspace_id": row.get("workspace_id"),
                    "sample_batch_ids": [],
                },
            )
            entry["sample_batch_ids"].append(batch_id)
    rows = [
        {**entry, "sample_batch_ids": tuple(entry["sample_batch_ids"])}
        for entry in found.values()
    ]
    frame = pd.DataFrame(rows, columns=COLLECTION_COLUMNS)
    return frame.sort_values(
        "target_collection_name", key=lambda names: names.str.casefold()
    ).reset_index(drop=True)


def _empty(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _undecided(
    scope: Scope, candidates: dict[str, tuple[Candidate, ...]]
) -> ResolvedScope:
    return ResolvedScope(
        scope=scope,
        requested=scope,
        batches=_empty(BATCH_COLUMNS),
        files=_empty(FILE_COLUMNS),
        collections=_empty(COLLECTION_COLUMNS),
        display_tz=scope.tz if scope.tz != INSTRUMENT_TZ else "UTC",
        candidates=candidates,
    )


# --- resolve -----------------------------------------------------------------


def resolve(client: "MascopeClient", scope: Scope) -> ResolvedScope:
    """What *scope* covers on the server *client* reads, in its workspace.

    Reads the datasets, the batches it may name, their samples and
    collections, and the files acquired in its range. A name that matches
    several objects stops the resolution there: the result's
    :attr:`~ResolvedScope.candidates` lists them, and the scope is resolved
    again with one.

    :param client: An SDK client; its workspace is the one searched.
    :param scope: The scope.
    :return: The batches, files and collections it covers.
    :raises NotFound: When a name or ID matches nothing; it lists what
        there is.
    :raises InvalidScope: When the instrument's time zone is needed and
        unknown, or the range is empty once in UTC.
    :raises AccessDenied: When the user may not read what it names.
    :raises NotAuthenticated: When the server refuses the token.
    :raises Unavailable: When the server fails or cannot be reached.
    """
    candidates: dict[str, tuple[Candidate, ...]] = {}
    datasets = _datasets(client)

    instrument = None
    if scope.instrument is not None:
        names = sorted(
            {str(name) for name in datasets["instrument"].dropna() if name},
            key=str.casefold,
        )
        chosen = _choose(
            scope.instrument,
            [Candidate("instrument", name, name) for name in names],
            "instrument",
        )
        if len(chosen) > 1:
            candidates["instrument"] = tuple(chosen)
        else:
            instrument = chosen[0].id

    dataset = None
    if scope.dataset is not None:
        chosen = _choose(
            scope.dataset,
            [
                Candidate("dataset", str(row.dataset_id), str(row.dataset_name))
                for row in datasets.itertuples()
            ],
            "dataset",
        )
        if len(chosen) > 1:
            candidates["dataset"] = tuple(chosen)
        else:
            dataset = chosen[0]
    if candidates:
        return _undecided(scope, candidates)

    # The batches to choose among
    if dataset is not None:
        among = datasets[datasets["dataset_id"] == dataset.id]
    elif instrument is not None:
        among = datasets[datasets["instrument"] == instrument]
    else:
        # Batches by name, or a time range alone: every dataset there is
        among = datasets
    pool = _batches(client, among)

    named: list[Any] | None = None
    if scope.batches is not None:
        named = []
        chosen_ids: list[str] = []
        options = [
            Candidate(
                "batch",
                str(row.sample_batch_id),
                str(row.sample_batch_name),
                str(row.dataset_id),
            )
            for row in pool.itertuples()
        ]
        for index, entry in enumerate(scope.batches):
            chosen = _choose(entry, options, "batch")
            if isinstance(entry, re.Pattern):
                chosen_ids += [candidate.id for candidate in chosen]
                named.append(entry)
            elif len(chosen) > 1:
                candidates[f"batches[{index}]"] = tuple(chosen)
            else:
                chosen_ids.append(chosen[0].id)
                named.append(chosen[0].id)
        if candidates:
            return _undecided(scope, candidates)
        pool = pool[pool["sample_batch_id"].isin(chosen_ids)]

    samples = _samples(client, pool)
    of_batches = set(samples["sample_file_id"].dropna())
    keep = pd.Series(True, index=samples.index)
    if instrument is not None:
        keep &= samples["instrument"] == instrument
    if scope.polarity is not None:
        keep &= samples["polarity"] == scope.polarity
    if scope.mode is not None:
        keep &= samples["ionization_mode_id"] == scope.mode
    samples = samples[keep]

    if instrument is not None:
        instruments: list[str | None] = [instrument]
    else:
        instruments = sorted(
            {str(name) for name in samples["instrument"].dropna()}
        ) or [None]

    # The bounds in UTC, and the instrument's zone from its files
    naive = [
        moment
        for moment in (scope.start, scope.end)
        if moment is not None and moment.tzinfo is None
    ]
    files = None
    if naive and scope.tz == INSTRUMENT_TZ:
        wide = _files(
            client,
            instruments,
            None if scope.start is None else _in_utc(scope.start, "UTC") - _ZONE_MARGIN,
            None if scope.end is None else _in_utc(scope.end, "UTC") + _ZONE_MARGIN,
        )
        timezone = _zone_of(wide)
        if timezone is None:
            raise InvalidScope(
                "start and end have no time zone, and tz='instrument', but no "
                f"file of {listed([i or 'any instrument' for i in instruments])} "
                "around that range records the instrument's time zone. Give "
                "start and end with a zone, or tz= the zone they are in."
            )
        zone = timezone
        files = wide
    else:
        zone = scope.tz if scope.tz != INSTRUMENT_TZ else "UTC"
    start_utc, end_utc = _in_utc(scope.start, zone), _in_utc(scope.end, zone)
    if start_utc is not None and end_utc is not None and start_utc >= end_utc:
        raise InvalidScope(f"start ({start_utc} UTC) is not before end ({end_utc} UTC)")

    samples = samples[_in_range(samples["datetime_utc"], start_utc, end_utc)]

    if files is None:
        lo, hi = start_utc, end_utc
        if lo is None and hi is None and not samples.empty:
            # No range: the span of the batches' samples
            lo, hi = samples["datetime_utc"].min(), samples["datetime_utc"].max()
        if lo is None and hi is None:
            files = _files(client, [], None, None)
        else:
            files = _files(client, instruments, lo, hi)
        timezone = _zone_of(files)
    if start_utc is not None or end_utc is not None:
        files = files[_in_range(files["datetime_utc"], start_utc, end_utc)]
    if scope.polarity is not None:
        files = files[files["polarity"] == scope.polarity]
    files = files.assign(in_batch=files["sample_file_id"].isin(of_batches))
    files = files.reset_index(drop=True)

    spans = (
        samples.groupby("sample_batch_id")["datetime_utc"]
        .agg(n_samples="size", first_datetime_utc="min", last_datetime_utc="max")
        .reset_index()
    )
    batches = pool.merge(spans, on="sample_batch_id")
    for column in BATCH_COLUMNS:
        if column not in batches:
            batches[column] = None
    batches = (
        batches.sort_values(
            ["first_datetime_utc", "sample_batch_name", "sample_batch_id"],
            kind="stable",
        )[BATCH_COLUMNS]
        .astype({"n_samples": "int64"})
        .reset_index(drop=True)
    )
    # The listing gives it as text: the store compares it as a time
    batches["sample_batch_utc_modified"] = pd.to_datetime(
        batches["sample_batch_utc_modified"], utc=True
    )

    collections = _collections(client, batches)
    collection = None
    if scope.collection is not None:
        chosen = _choose(
            scope.collection,
            [
                Candidate(
                    "collection",
                    str(row.target_collection_id),
                    str(row.target_collection_name),
                )
                for row in collections.itertuples()
            ],
            "collection",
        )
        if len(chosen) > 1:
            candidates["collection"] = tuple(chosen)
        else:
            collection = chosen[0].id

    resolved = Scope(
        dataset=None if dataset is None else dataset.id,
        batches=(
            None
            if named is None
            else tuple(
                sorted(
                    named,
                    key=lambda entry: (
                        isinstance(entry, re.Pattern),
                        json.dumps(_name_content(entry), sort_keys=True),
                    ),
                )
            )
        ),
        instrument=instrument,
        start=start_utc,
        end=end_utc,
        polarity=scope.polarity,
        mode=scope.mode,
        collection=collection if not candidates else scope.collection,
        tz=scope.tz,
    )
    if scope.tz != INSTRUMENT_TZ:
        display_tz = scope.tz
    else:
        display_tz = timezone or "UTC"
    return ResolvedScope(
        scope=resolved,
        requested=scope,
        batches=batches,
        files=files,
        collections=collections,
        collection=collection,
        timezone=timezone,
        display_tz=display_tz,
        start_utc=start_utc,
        end_utc=end_utc,
        candidates=candidates,
    )
