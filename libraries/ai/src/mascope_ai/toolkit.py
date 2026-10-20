"""The toolkit: find, describe and load Mascope's data as handles.

Every call here is safe to put in front of a model. ``find`` answers with
:class:`~mascope_ai.refs.Ref` lists, ``describe`` with a small dict, and the
loaders with :class:`~mascope_ai.frame.Frame` handles whose rows stay in
Python. A failure is a :class:`~mascope_ai.errors.ToolkitError` that says
what to do next.

Example::

    from mascope_ai import Toolkit

    kit = Toolkit(workspace="My Workspace")   # an AI agent's token, from .env
    kit.find("spring")                        # candidates, not an error
    kit.describe(kit.find("spring", kind="batch")[0])
    peaks = kit.load_peaks(batches="spring 2026", max_rows=200_000)
    peaks                                     # schema, size, completeness, head
    peaks.df.groupby("target_compound_formula")["height"].mean()   # in Python
"""

import re
from collections.abc import Callable, Iterable
from typing import Any

import pandas as pd

from mascope_sdk import MascopeClient, _loaders

from .errors import (
    LISTED,
    AccessDenied,
    AmbiguousName,
    InvalidRequest,
    NotFound,
    RowCapExceeded,
    Unavailable,
    sdk_errors,
)
from .frame import Frame, jsonable
from .refs import KINDS, Ref
from .session import Session


#: The row cap of a load unless the caller sets another.
DEFAULT_MAX_ROWS = 500_000

#: The rows a sample's (or a stage's) peak list is taken to hold when a load
#: is estimated before it starts. A rough guess, set low - a measured
#: sample's peak list is usually far longer, a blank's can be shorter - so
#: that the estimate refuses only a load clearly too big for the cap; the
#: running count during the load is what holds the cap.
ROWS_PER_SAMPLE_ESTIMATE = 100

#: The order ``find`` lists kinds in, among matches ranked alike.
_KIND_ORDER = {kind: index for index, kind in enumerate(KINDS)}

#: What ``find`` looks for ``within`` a dataset or a batch. A collection is
#: within the batches it is associated with.
_INSIDE = {"dataset": ("batch", "sample"), "batch": ("sample", "collection")}

#: The loaders whose queries a Frame records, and the session runs again.
_OPS = ("load_peaks", "load_timeseries", "load_spectrum", "load_peaks_by_stage")

#: A string that can be an object ID: worth one direct read before listing.
_ID_LIKE = re.compile(r"[A-Za-z0-9_-]+")


class Found(list):
    """What ``find`` returns: the matching Refs, best first.

    A plain list of :class:`Ref` - with how many matched in all (it may be
    more than were returned), and whether every listing searched could be
    read.

    :ivar total: How many objects matched.
    :ivar complete: False when some listing failed, so matches may be
        missing; :attr:`failures` says which.
    :ivar failures: Each listing that failed, as a dict.
    """

    def __init__(
        self,
        refs: Iterable[Ref],
        *,
        total: int,
        complete: bool = True,
        failures: Iterable[dict[str, Any]] = (),
    ):
        super().__init__(refs)
        self.total = total
        self.complete = complete
        self.failures = list(failures)

    def __repr__(self) -> str:
        head = f"{self.total} match(es)"
        if self.total > len(self):
            head += f", the first {len(self)} shown"
        if not self.complete:
            head += f", INCOMPLETE ({len(self.failures)} listing(s) failed)"
        return f"Found({head}: {list.__repr__(self)})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "complete": self.complete,
            "failures": self.failures,
            "refs": [ref.to_dict() for ref in self],
        }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _modified(value: Any) -> Any:
    stamp = pd.Timestamp(value) if value is not None else None
    if stamp is None or pd.isna(stamp):
        return None
    return stamp.to_pydatetime()


def _refs(
    rows: pd.DataFrame | None,
    kind: str,
    id_column: str,
    name_column: str,
    parent_column: str | None,
    modified_column: str | None,
) -> list[Ref]:
    if rows is None or rows.empty:
        return []
    refs = []
    for row in rows.to_dict("records"):
        parent = row.get(parent_column) if parent_column else None
        refs.append(
            Ref(
                id=str(row[id_column]),
                kind=kind,  # type: ignore[arg-type]
                name=str(row.get(name_column) or ""),
                parent=None if parent is None or pd.isna(parent) else str(parent),
                modified=_modified(row.get(modified_column))
                if modified_column
                else None,
            )
        )
    return refs


def _rank(query: "str | re.Pattern", ref: Ref) -> int | None:
    """How well *ref* matches *query*: 0 its ID, 1 its whole name, 2 part of
    it (or a regex hit); None when it does not match.

    A string matches as a case-insensitive literal - ``(`` and ``.`` are
    just characters - and a compiled pattern as the regex it is.
    """
    if isinstance(query, re.Pattern):
        return 2 if query.search(ref.name) else None
    if ref.id == query:
        return 0
    name, wanted = ref.name.casefold(), query.casefold()
    if name == wanted:
        return 1
    return 2 if wanted in name else None


def _sample_item(row: pd.Series, batch_name: str) -> dict[str, Any]:
    return {
        "sample_item_id": row["sample_item_id"],
        "sample_item_name": row.get("sample_item_name"),
        "sample_batch_name": batch_name,
    }


class _RowCap:
    """Counts a load's rows as they arrive, and stops it past the cap.

    Handed to the SDK's loaders as their ``on_result`` hook, which runs in
    the thread that collects the results, so it needs no lock.
    """

    def __init__(
        self,
        cap: int,
        unit: str,
        items: dict[Any, dict[str, Any]],
        key: Callable[[tuple[Any, ...]], Any],
        hint: str,
    ):
        self.cap = cap
        self.unit = unit
        self.items = items
        self.key = key
        self.hint = hint
        self.rows = 0
        self.done: set[Any] = set()

    def __call__(self, args: tuple[Any, ...], frame: pd.DataFrame) -> None:
        self.rows += len(frame)
        self.done.add(self.key(args))
        if self.rows > self.cap:
            raise RowCapExceeded(
                self.rows,
                self.cap,
                estimated=False,
                unit=self.unit,
                done=len(self.done),
                left=[item for k, item in self.items.items() if k not in self.done],
                hint=self.hint,
            )


def _check_cap(max_rows: Any) -> int:
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise InvalidRequest(f"max_rows must be a positive int, not {max_rows!r}")
    return max_rows


def _encode(value: Any) -> Any:
    """A loader's parameters as JSON, Refs marked so they come back as Refs."""
    if isinstance(value, Ref):
        return {"$ref": value.to_dict()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return Ref.from_dict(value["$ref"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


class Toolkit:
    """Mascope's data for an agent: find, describe, load - as handles.

    Searches and listings cover the workspaces the token's user belongs to,
    and the datasets, batches, samples and collections of the client's
    workspace (collections: the global ones too).

    :ivar client: The SDK client it reads through.
    :ivar session: Where its Frames are kept.
    """

    def __init__(
        self,
        client: MascopeClient | None = None,
        *,
        session: Session | None = None,
        workspace: str | None = None,
        service_name: str = "ai-agent",
        max_workers: int = 8,
    ):
        """Open the toolkit over a client.

        :param client: The SDK client to read through. When None one is made
            from the environment (``MASCOPE_URL``, ``MASCOPE_ACCESS_TOKEN``,
            or a ``.env`` file), on *workspace* and under *service_name*.
        :param session: Where Frames are kept; a new session when None.
        :param workspace: The workspace of the client made here.
        :param service_name: The service the client made here presents: an
            AI agent's token is generated for ``ai-agent`` and refused under
            any other name.
        :param max_workers: Concurrent reads of a load, at most 8.
        """
        self.client = client or MascopeClient(
            workspace=workspace, service_name=service_name
        )
        self.session = session if session is not None else Session()
        self.session.reloader = self._rerun
        self.max_workers = max_workers

    def __repr__(self) -> str:
        return (
            f"Toolkit(workspace={self.client.workspace_name!r}, "
            f"session={self.session.id!r})"
        )

    # --- errors --------------------------------------------------------------

    def _role(self) -> tuple[str | None, str | None]:
        """The user's role in the client's workspace, and its name."""
        try:
            workspaces = self.client.workspaces.list()
            row = workspaces[workspaces["workspace_id"] == self.client.workspace_id]
            role = row.iloc[0].get("my_role") if not row.empty else None
            return (str(role) if role else None), self.client.workspace_name
        except Exception:
            return None, self.client.workspace_name

    def _errors(self, operation: str, target: Ref | None = None):
        return sdk_errors(operation, target, current_role=self._role)

    # --- listings ------------------------------------------------------------

    def _workspaces(self) -> list[Ref]:
        with self._errors("list the workspaces"):
            rows = self.client.workspaces.list()
        return _refs(
            rows,
            "workspace",
            "workspace_id",
            "workspace_name",
            None,
            "workspace_utc_modified",
        )

    def _datasets(self) -> list[Ref]:
        with self._errors("list the datasets of the workspace"):
            rows = self.client.datasets.list()
        return _refs(
            rows,
            "dataset",
            "dataset_id",
            "dataset_name",
            "workspace_id",
            "dataset_utc_modified",
        )

    def _batches(self, dataset: Ref) -> list[Ref]:
        with self._errors("list the batches of", dataset):
            rows = self.client.batches.list(dataset.id)
        return _refs(
            rows,
            "batch",
            "sample_batch_id",
            "sample_batch_name",
            "dataset_id",
            "sample_batch_utc_modified",
        )

    def _all_batches(self) -> list[Ref]:
        return [
            batch for dataset in self._datasets() for batch in self._batches(dataset)
        ]

    def _batch(self, batch_id: str) -> Ref:
        for batch in self._all_batches():
            if batch.id == batch_id:
                return batch
        raise NotFound(batch_id, "batch")

    def _sample_rows(self, batch: Ref) -> pd.DataFrame:
        with self._errors("list the samples of", batch):
            rows = self.client.samples.list(batch=batch.id, dataset=batch.parent)
        return rows if rows is not None else pd.DataFrame()

    def _samples(
        self, within: Ref | None, among: list[Ref] | None = None
    ) -> tuple[list[Ref], list[dict[str, Any]]]:
        """The samples of a batch, a dataset or the workspace - or of the
        batches *among*, when given - and the listings that failed."""
        columns = (
            "sample_item_id",
            "sample_item_name",
            "sample_batch_id",
            "sample_item_utc_modified",
        )
        if among is not None:
            return [
                ref
                for batch in among
                for ref in _refs(self._sample_rows(batch), "sample", *columns)
            ], []
        if within is not None and within.kind == "batch":
            return _refs(self._sample_rows(within), "sample", *columns), []
        dataset = within.id if within is not None and within.kind == "dataset" else None
        with self._errors("list the samples of", within):
            try:
                rows = self.client.samples.list(
                    batches=re.compile(""), dataset=dataset, drop_columns=[]
                )
            except ValueError:  # the SDK's "No batches found."
                return [], []
        if rows is None:
            return [], []
        failures = [jsonable(record) for record in rows.attrs.get("failures", ())]
        return _refs(rows, "sample", *columns), failures

    def _collections(self, within: Ref | None) -> list[Ref]:
        batch = within.id if within is not None and within.kind == "batch" else None
        with self._errors("list the target collections"):
            rows = self.client.collections.list(batch)
        return _refs(
            rows,
            "collection",
            "target_collection_id",
            "target_collection_name",
            "workspace_id",
            None,
        )

    def _listing(
        self, kind: str, within: Ref | None, among: list[Ref] | None = None
    ) -> tuple[list[Ref], list[dict[str, Any]]]:
        if kind == "workspace":
            return self._workspaces(), []
        if kind == "dataset":
            return self._datasets(), []
        if kind == "batch":
            if within is not None:  # a dataset (_INSIDE)
                return self._batches(within), []
            return self._all_batches(), []
        if kind == "sample":
            return self._samples(within, among)
        return self._collections(within), []

    # --- find ----------------------------------------------------------------

    def _matches(
        self,
        query: "str | re.Pattern",
        kind: str | None,
        within: Ref | None,
        among: list[Ref] | None = None,
    ) -> tuple[list[tuple[int, Ref]], list[dict[str, Any]]]:
        if not isinstance(query, (str, re.Pattern)):
            raise InvalidRequest(
                f"query is a name (a string) or a compiled re.Pattern, not {query!r}"
            )
        if kind is not None and kind not in KINDS:
            raise InvalidRequest(f"kind is one of {KINDS} or None, not {kind!r}")
        if within is not None:
            if not isinstance(within, Ref) or within.kind not in _INSIDE:
                raise InvalidRequest("within is a dataset's or a batch's Ref")
            if kind not in _INSIDE[within.kind]:
                raise InvalidRequest(
                    f"Within a {within.kind}, find "
                    f"{' or '.join(_INSIDE[within.kind])}: pass that kind"
                )

        # Samples are listed batch by batch, a request each: only on request.
        kinds = [kind] if kind else [k for k in KINDS if k != "sample"]
        seen: set[tuple[str, str]] = set()
        ranked: list[tuple[int, Ref]] = []
        failures: list[dict[str, Any]] = []
        for each in kinds:
            refs, failed = self._listing(each, within, among)
            failures += failed
            for ref in refs:
                rank = _rank(query, ref)
                if rank is None or (ref.kind, ref.id) in seen:
                    continue
                seen.add((ref.kind, ref.id))
                ranked.append((rank, ref))
        ranked.sort(
            key=lambda item: (
                item[0],
                _KIND_ORDER[item[1].kind],
                item[1].name.casefold(),
                item[1].id,
            )
        )
        return ranked, failures

    def find(
        self,
        query: "str | re.Pattern",
        kind: str | None = None,
        limit: int = 50,
        *,
        within: Ref | None = None,
    ) -> Found:
        """Find Mascope objects by name or ID.

        A string matches a name as a case-insensitive **literal** substring:
        ``"Sample (A)"`` finds the batch named so, its brackets no regex
        group. Pass a compiled ``re.Pattern`` to match by regex. An object
        whose ID is the string, then those whose whole name it is, come
        first. Several matches are an answer, not an error: pick one.

        :param query: A name, part of one, or an ID; or a compiled pattern.
            ``""`` matches everything.
        :param kind: ``workspace``, ``dataset``, ``batch``, ``sample`` or
            ``collection``; every kind but ``sample`` when None. Samples are
            listed batch by batch, so ask for them by kind, ideally
            ``within`` a dataset or batch.
        :param limit: How many to return at most; ``total`` says how many
            matched.
        :param within: A dataset's Ref, to find its batches or samples, or a
            batch's, to find its samples or the collections associated with
            it; *kind* says which.
        :return: The matches, best first.
        :raises InvalidRequest: On an unknown kind or a bad ``within``.
        """
        ranked, failures = self._matches(query, kind, within)
        return Found(
            [ref for _, ref in ranked[: max(limit, 0)]],
            total=len(ranked),
            complete=not failures,
            failures=failures,
        )

    def _sample_by_id(self, sample_id: str) -> Ref | None:
        """The sample with this ID, by one direct read; None when there is
        none (or none this user may see).

        The server answers a sample ID that does not exist with a 403, not a
        404, for anyone but a superuser: it cannot tell whose the sample
        would be. So a refusal here means only that *sample_id* names no
        sample this user may read - most often, that it is a name.
        """
        try:
            with self._errors("read sample", None):
                record = self.client.samples.get(sample_id)
        except (NotFound, InvalidRequest, AccessDenied):
            return None
        if not record:
            return None
        return Ref(
            id=str(record["sample_item_id"]),
            kind="sample",
            name=str(record.get("sample_item_name") or ""),
            parent=record.get("sample_batch_id"),
            modified=_modified(record.get("sample_item_utc_modified")),
        )

    def resolve(
        self, value: "Ref | str | re.Pattern", kind: str, *, within: Ref | None = None
    ) -> Ref:
        """The one object *value* names.

        A Ref passes through (it must be of *kind*). A string is an ID, a
        whole name, or the only name it is part of.

        :raises AmbiguousName: When it matches several, with the candidates.
        :raises NotFound: When it matches none.
        :raises Unavailable: When a listing it searched failed, so that the
            match it would answer with may not be the only one.
        """
        return self._resolve(value, kind, within)

    def _resolve(
        self,
        value: "Ref | str | re.Pattern",
        kind: str,
        within: Ref | None,
        among: list[Ref] | None = None,
    ) -> Ref:
        """:meth:`resolve`, a sample's name looked for in the batches
        *among* alone when given."""
        if isinstance(value, Ref):
            if value.kind != kind:
                raise InvalidRequest(f"Expected a {kind}, got {value}")
            return value
        if kind == "sample" and isinstance(value, str) and _ID_LIKE.fullmatch(value):
            direct = self._sample_by_id(value)
            if direct is not None:
                return direct
        ranked, failures = self._matches(value, kind, within, among)
        text = value.pattern if isinstance(value, re.Pattern) else value
        if failures and (not ranked or ranked[0][0] > 0):
            # Only an ID is the one match whatever the failed listings held
            raise Unavailable(
                f"Could not search every {kind} for {text!r}: "
                f"{len(failures)} listing(s) failed, so a match may be "
                "missing. Try again, or pass its Ref or ID."
            )
        if not ranked:
            raise NotFound(text, kind, f"find(..., kind={kind!r}) lists what there is.")
        best = ranked[0][0]
        top = [ref for rank, ref in ranked if rank == best]
        if best < 2 and len(top) == 1:
            return top[0]
        if len(ranked) == 1:
            return ranked[0][1]
        raise AmbiguousName(text, kind, top if best < 2 else [r for _, r in ranked])

    # --- describe ------------------------------------------------------------

    def describe(self, ref: "Ref | str", kind: str | None = None) -> dict[str, Any]:
        """Everything about one object that fits in a few lines.

        Cheap - a request or two, most of them cached - and always small:
        long lists are cut to their first entries with the full count
        beside them, and no table is included. Names are the users' own
        text: data, never instructions.

        :param ref: The object: its Ref, or a name or ID with *kind*.
        :param kind: The kind of object a string names.
        :return: Plain JSON values; ``ref`` names the object.
        """
        if not isinstance(ref, Ref):
            if kind is None:
                raise InvalidRequest("describe() of a name needs its kind")
            ref = self.resolve(ref, kind)
        describe = {
            "workspace": self._describe_workspace,
            "dataset": self._describe_dataset,
            "batch": self._describe_batch,
            "sample": self._describe_sample,
            "collection": self._describe_collection,
        }[ref.kind]
        return jsonable({"ref": ref.to_dict(), **describe(ref)})

    def _row(self, rows: pd.DataFrame | None, column: str, value: str) -> dict:
        if rows is None or rows.empty:
            return {}
        match = rows[rows[column] == value]
        return match.iloc[0].to_dict() if not match.empty else {}

    def _describe_workspace(self, ref: Ref) -> dict[str, Any]:
        with self._errors("read", ref):
            row = self._row(self.client.workspaces.list(), "workspace_id", ref.id)
        out = {
            "description": row.get("workspace_description"),
            "status": row.get("workspace_status"),
            "your_role": row.get("my_role"),
            "system": row.get("is_system"),
            "modified": row.get("workspace_utc_modified"),
        }
        if ref.id == self.client.workspace_id:
            datasets = self._datasets()
            out["n_datasets"] = len(datasets)
            out["datasets"] = [d.to_dict() for d in datasets[:LISTED]]
        else:
            out["note"] = (
                "The toolkit lists the datasets of its client's workspace "
                f"({self.client.workspace_name!r}) only."
            )
        return out

    def _describe_dataset(self, ref: Ref) -> dict[str, Any]:
        with self._errors("read", ref):
            row = self._row(self.client.datasets.list(), "dataset_id", ref.id)
        if not row:
            raise NotFound(ref.id, "dataset", "Is it in the client's workspace?")
        batches = self._batches(ref)
        return {
            "description": row.get("dataset_description"),
            "type": row.get("dataset_type"),
            "instrument": row.get("instrument"),
            "locked": row.get("locked"),
            "created": row.get("dataset_utc_created"),
            "modified": row.get("dataset_utc_modified"),
            "n_batches": len(batches),
            "batches": [b.to_dict() for b in batches[:LISTED]],
        }

    def _describe_batch(self, ref: Ref) -> dict[str, Any]:
        if ref.parent is None:
            ref = self._batch(ref.id)
        with self._errors("read", ref):
            row = self._row(
                self.client.batches.list(ref.parent), "sample_batch_id", ref.id
            )
        samples = self._sample_rows(ref)
        times = (
            pd.to_datetime(samples["datetime_utc"], utc=True, errors="coerce")
            if "datetime_utc" in samples
            else pd.Series(dtype="datetime64[ns, UTC]")
        )

        def counts(column: str) -> dict[str, int]:
            if column not in samples:
                return {}
            return {
                str(key): int(n)
                for key, n in samples[column].value_counts(dropna=True).items()
            }

        matched = None
        if "match" in samples:
            matched = int(
                sum(
                    isinstance(m, dict) and m.get("match_sample_id") is not None
                    for m in samples["match"]
                )
            )
        with self._errors("list the target collections of", ref):
            collections = self.client.collections.list(ref.id)
        collection_refs = _refs(
            collections,
            "collection",
            "target_collection_id",
            "target_collection_name",
            "workspace_id",
            None,
        )
        return {
            "description": row.get("sample_batch_description"),
            "type": row.get("sample_batch_type"),
            "status": row.get("status"),
            "polarity": row.get("polarity"),
            "locked": row.get("locked"),
            "created": row.get("sample_batch_utc_created"),
            "modified": row.get("sample_batch_utc_modified"),
            "n_samples": len(samples),
            "first_measured": times.min() if times.notna().any() else None,
            "last_measured": times.max() if times.notna().any() else None,
            "sample_polarities": counts("polarity"),
            "instruments": counts("instrument"),
            "n_ionization_modes": int(samples["ionization_mode_id"].nunique())
            if "ionization_mode_id" in samples
            else None,
            "n_samples_matched": matched,
            "n_collections": len(collection_refs),
            "collections": [c.to_dict() for c in collection_refs[:LISTED]],
        }

    def _describe_sample(self, ref: Ref) -> dict[str, Any]:
        with self._errors("read", ref):
            record = self.client.samples.get(ref.id) or {}
        calibration = record.get("mz_calibration") or {}
        match = record.get("match") or {}
        attributes = record.get("sample_item_attributes") or {}
        return {
            "batch_id": record.get("sample_batch_id"),
            "type": record.get("sample_item_type"),
            "measured": record.get("datetime_utc"),
            "polarity": record.get("polarity"),
            "instrument": record.get("instrument"),
            "instrument_type": record.get("instrument_type"),
            "duration_s": record.get("length"),
            "t_range_s": [record.get("t0"), record.get("t1")],
            "mz_range": record.get("range"),
            "tic": record.get("tic"),
            "locked": record.get("locked"),
            "mz_calibration": {
                "status": calibration.get("status"),
                "verified": calibration.get("verified"),
            },
            "match": {
                "matched": match.get("match_sample_id") is not None,
                "score": match.get("match_score"),
                "category": match.get("match_category"),
            },
            "n_attributes": len(attributes),
            "attributes": dict(list(attributes.items())[:LISTED]),
        }

    def _describe_collection(self, ref: Ref) -> dict[str, Any]:
        with self._errors("read", ref):
            record = self.client.collections.get(ref.id) or {}
        compounds = record.get("target_compounds") or []
        batches = record.get("sample_batches") or []
        return {
            "description": record.get("target_collection_description"),
            "type": record.get("target_collection_type"),
            "workspace_id": record.get("workspace_id"),
            "global": record.get("workspace_id") is None,
            "n_compounds": record.get("target_compounds_count", len(compounds)),
            "compounds": [
                {
                    "id": c.get("target_compound_id"),
                    "formula": c.get("target_compound_formula"),
                    "name": c.get("target_compound_name"),
                }
                for c in compounds[:LISTED]
            ],
            "n_batches": record.get("sample_batches_count", len(batches)),
            "batches": batches[:LISTED],
        }

    # --- loading -------------------------------------------------------------

    def _load(
        self, op: str, params: dict[str, Any]
    ) -> tuple[pd.DataFrame | None, list[Ref]]:
        """Run loader *op*; a load every read of which was refused is a 403.

        The SDK's strict loader keeps a read that failed as a failure of the
        frame, a refused one included: a load that read some of what it
        asked for is still an answer. One that read nothing because every
        read was refused is not, and says so as :class:`AccessDenied`.
        """
        df, sources = getattr(self, f"_{op}")(**params)
        failures = df.attrs.get("failures", []) if df is not None else []
        if (
            df is not None
            and df.empty
            and failures
            and all(f.get("error_type") == "PermissionDeniedError" for f in failures)
        ):
            role, workspace = self._role()
            raise AccessDenied(
                "read", sources[0] if sources else None, "guest", role, workspace
            )
        return df, sources

    def _run(self, op: str, params: dict[str, Any]) -> Frame:
        """Run loader *op* and keep what it returns as a Frame."""
        df, sources = self._load(op, params)
        if df is None:
            df = pd.DataFrame()
            df.attrs.update(complete=True, failures=[])
        # The SDK's stamp: best effort, and the same block for every frame of
        # one build (MascopeClient._with_provenance)
        df = self.client._with_provenance(df)
        frame = Frame.from_df(
            df,
            source_refs=sources,
            query={"op": op, "params": _encode(params)},
            session=self.session,
        )
        self.session.save(frame, df)
        return frame

    def _rerun(self, query: dict[str, Any]) -> pd.DataFrame:
        """A recorded query's rows, loaded again (the session's reloader)."""
        op = query["op"]
        if op not in _OPS:
            raise InvalidRequest(f"Not a query the toolkit recorded: {op!r}")
        df, _ = self._load(op, _decode(query["params"]))
        return df if df is not None else pd.DataFrame()

    def _scope(
        self,
        dataset: Ref | None,
        batches: list[Ref] | None,
        samples: list[Ref] | None,
    ) -> tuple[list[tuple[pd.Series, str]], list[Ref]]:
        """The samples a load reads, as the SDK's per-sample tasks, and the
        objects it was asked for."""
        if dataset is None and not batches and not samples:
            raise InvalidRequest("Name a dataset, batches or samples to load")
        batches = batches or []
        sources = ([dataset] if dataset else []) + batches + (samples or [])
        tasks: list[tuple[pd.Series, str]] = []
        seen: set[str] = set()

        def add(rows: pd.DataFrame, batch: Ref, only: set[str] | None) -> None:
            for _, row in rows.iterrows():
                sample_id = row["sample_item_id"]
                if sample_id in seen or (only is not None and sample_id not in only):
                    continue
                seen.add(sample_id)
                tasks.append((row, batch.name))

        if samples:
            batch_ids = {b.id for b in batches}
            by_batch: dict[str, set[str]] = {}
            for sample in samples:
                if sample.parent is None:
                    raise InvalidRequest(f"{sample} names no batch")
                if batch_ids and sample.parent not in batch_ids:
                    raise InvalidRequest(f"{sample} is in none of the batches named")
                by_batch.setdefault(sample.parent, set()).add(sample.id)
            known = {b.id: b for b in batches}
            for batch_id, ids in by_batch.items():
                batch = known.get(batch_id) or self._batch(batch_id)
                if dataset is not None and batch.parent != dataset.id:
                    raise InvalidRequest(f"{batch} is not in {dataset}")
                rows = self._sample_rows(batch)
                listed = (
                    set(rows["sample_item_id"]) if "sample_item_id" in rows else set()
                )
                missing = sorted(ids - listed)
                if missing:
                    # A stale Ref, or one whose parent is wrong: never an
                    # empty answer that says it is complete
                    raise NotFound(
                        missing[0],
                        "sample",
                        f"{batch} lists no such sample. "
                        "find(..., kind='sample') lists what there is.",
                    )
                add(rows, batch, ids)
        else:
            for batch in batches or self._batches(dataset):  # type: ignore[arg-type]
                if dataset is not None and batch.parent != dataset.id:
                    raise InvalidRequest(f"{batch} is not in {dataset}")
                add(self._sample_rows(batch), batch, None)
        return tasks, sources

    def _resolve_scope(
        self, dataset: Any, batches: Any, samples: Any
    ) -> dict[str, Any]:
        dataset_ref = self.resolve(dataset, "dataset") if dataset is not None else None
        batch_refs = [
            self.resolve(b, "batch", within=dataset_ref) for b in _as_list(batches)
        ]
        within = batch_refs[0] if len(batch_refs) == 1 else dataset_ref
        # Of several batches, their own samples: not the dataset's or the
        # workspace's, which may have more of the same name
        among = batch_refs if len(batch_refs) > 1 else None
        sample_refs = [
            self._resolve(s, "sample", within, among) for s in _as_list(samples)
        ]
        return {
            "dataset": dataset_ref,
            "batches": batch_refs or None,
            "samples": sample_refs or None,
        }

    def load_peaks(
        self,
        dataset: "Ref | str | None" = None,
        batches: "Ref | str | list | None" = None,
        samples: "Ref | str | list | None" = None,
        *,
        matches: bool = True,
        areas: bool = True,
        heights: bool = True,
        average: bool = True,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> Frame:
        """Load the peaks of a dataset, of batches, or of samples.

        Samples, when given, are what is loaded; otherwise every sample of
        the batches, otherwise of the dataset. Each may be a Ref, an ID or a
        name (:meth:`resolve`).

        The cap holds during the load: an estimate first refuses a load that
        is over it at :data:`ROWS_PER_SAMPLE_ESTIMATE` rows per sample (a
        rough guess: a load of samples with very short peak lists may be
        refused under a small cap), then a running count stops the load as
        soon as the rows read pass it. A Frame above the cap is never made. ``matches=True`` gives a
        peak one row per isotope it matches, so it can multiply the rows.

        :return: The Frame, with ``complete`` and ``failures`` from the
            SDK's strict loader.
        :raises RowCapExceeded: With the rows reached and the samples left.
        """
        params = {
            **self._resolve_scope(dataset, batches, samples),
            "matches": matches,
            "areas": areas,
            "heights": heights,
            "average": average,
            "max_rows": _check_cap(max_rows),
        }
        return self._run("load_peaks", params)

    def _load_peaks(
        self,
        dataset: Ref | None,
        batches: list[Ref] | None,
        samples: list[Ref] | None,
        matches: bool,
        areas: bool,
        heights: bool,
        average: bool,
        max_rows: int,
    ) -> tuple[pd.DataFrame | None, list[Ref]]:
        tasks, sources = self._scope(dataset, batches, samples)
        items = {
            row["sample_item_id"]: _sample_item(row, batch) for row, batch in tasks
        }
        hint = "Load fewer samples (name batches or samples), or raise max_rows."
        estimate = len(tasks) * ROWS_PER_SAMPLE_ESTIMATE
        if estimate > max_rows:
            raise RowCapExceeded(
                estimate,
                max_rows,
                estimated=True,
                unit="sample",
                done=0,
                left=list(items.values()),
                hint=hint,
            )
        cap = _RowCap(
            max_rows, "sample", items, lambda args: args[0]["sample_item_id"], hint
        )
        with self._errors("read the peaks of", sources[0] if sources else None):
            df = _loaders.load_sample_peaks(
                self.client,
                tasks,
                matches=matches,
                areas=areas,
                heights=heights,
                average=average,
                max_workers=self.max_workers,
                on_result=cap,
            )
        return df, sources

    def load_timeseries(
        self,
        compound: "str | list[str] | None" = None,
        ion: "str | list[str] | None" = None,
        isotope: "str | list[str] | None" = None,
        *,
        dataset: "Ref | str | None" = None,
        batches: "Ref | str | list | None" = None,
        samples: "Ref | str | list | None" = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> Frame:
        """Load the per-scan time series of the peaks matched to a target.

        Give exactly one of *compound* (formulas or names), *ion* or
        *isotope* (formulas), and the samples as for :meth:`load_peaks`.
        The running count stops the load as soon as the points read pass
        *max_rows*; the samples it names as left are those none of whose
        series had been read.

        :raises RowCapExceeded: With the points reached and the samples left.
        """
        given = {
            level: value
            for level, value in (
                ("compound", compound),
                ("ion", ion),
                ("isotope", isotope),
            )
            if value is not None
        }
        if len(given) != 1:
            raise InvalidRequest(
                "Give exactly one of compound, ion or isotope, "
                f"not {sorted(given) or 'none'}"
            )
        ((level, values),) = given.items()
        params = {
            "level": level,
            "values": [str(v) for v in _as_list(values)],
            **self._resolve_scope(dataset, batches, samples),
            "max_rows": _check_cap(max_rows),
        }
        return self._run("load_timeseries", params)

    def _load_timeseries(
        self,
        level: str,
        values: list[str],
        dataset: Ref | None,
        batches: list[Ref] | None,
        samples: list[Ref] | None,
        max_rows: int,
    ) -> tuple[pd.DataFrame | None, list[Ref]]:
        tasks, sources = self._scope(dataset, batches, samples)
        items = {
            row["sample_item_id"]: _sample_item(row, batch) for row, batch in tasks
        }
        cap = _RowCap(
            max_rows,
            "sample",
            items,
            lambda args: args[0]["sample_item_id"],
            "The samples left include any with no peak matched to the targets, "
            "which have nothing to load. Load fewer samples or targets, or "
            "raise max_rows.",
        )
        with self._errors("read the time series of", sources[0] if sources else None):
            df = _loaders.load_sample_timeseries(
                self.client,
                tasks,
                level,
                values,
                max_workers=self.max_workers,
                on_result=cap,
            )
        return df, sources

    def load_spectrum(
        self,
        sample: "Ref | str",
        mz_range: "tuple[float, float] | None" = None,
        *,
        t_range: "tuple[float, float] | None" = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> Frame:
        """Load a sample's averaged spectrum (``mz``, ``intensity``).

        :param mz_range: ``(mz_min, mz_max)``; the whole spectrum when None.
        :param t_range: ``(t_min, t_max)`` in seconds; the whole sample when
            None.
        :raises RowCapExceeded: If the spectrum has more points than
            *max_rows*; narrow *mz_range*.
        """
        params = {
            "sample": self.resolve(sample, "sample"),
            "mz_range": list(mz_range) if mz_range is not None else None,
            "t_range": list(t_range) if t_range is not None else None,
            "max_rows": _check_cap(max_rows),
        }
        return self._run("load_spectrum", params)

    def _load_spectrum(
        self,
        sample: Ref,
        mz_range: list[float] | None,
        t_range: list[float] | None,
        max_rows: int,
    ) -> tuple[pd.DataFrame | None, list[Ref]]:
        mz_min, mz_max = mz_range or (None, None)
        t_min, t_max = t_range or (None, None)
        with self._errors("read the spectrum of", sample):
            df = self.client.samples.get_spectrum(
                sample.id, mz_min=mz_min, mz_max=mz_max, t_min=t_min, t_max=t_max
            )
        if df is not None and len(df) > max_rows:
            # One read: it cannot be stopped part way, only not kept
            raise RowCapExceeded(
                len(df),
                max_rows,
                estimated=False,
                unit="spectrum",
                done=0,
                left=[{"sample_item_id": sample.id, "sample_item_name": sample.name}],
                hint="Narrow mz_range (or t_range), or raise max_rows.",
            )
        if df is not None:
            df.attrs.update(complete=True, failures=[])
        return df, [sample]

    def load_peaks_by_stage(
        self,
        sample: "Ref | str",
        stages: "list[tuple[float, float] | tuple[float, float, str]]",
        *,
        matches: bool = True,
        areas: bool = True,
        heights: bool = True,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> Frame:
        """Load a sample's averaged peaks for each time range (stage).

        :param stages: ``(t_min, t_max)`` or ``(t_min, t_max, name)`` each.
        :raises RowCapExceeded: With the rows reached and the stages left.
        """
        if not stages:
            raise InvalidRequest("stages is a non-empty list of (t_min, t_max[, name])")
        for stage in stages:
            if not isinstance(stage, (list, tuple)) or len(stage) not in (2, 3):
                raise InvalidRequest(
                    f"A stage is (t_min, t_max) or (t_min, t_max, name), not {stage!r}"
                )
        params = {
            "sample": self.resolve(sample, "sample"),
            "stages": [list(stage) for stage in stages],
            "matches": matches,
            "areas": areas,
            "heights": heights,
            "max_rows": _check_cap(max_rows),
        }
        return self._run("load_peaks_by_stage", params)

    def _load_peaks_by_stage(
        self,
        sample: Ref,
        stages: list[list[Any]],
        matches: bool,
        areas: bool,
        heights: bool,
        max_rows: int,
    ) -> tuple[pd.DataFrame | None, list[Ref]]:
        items = {
            index: {
                "stage": index,
                "stage_name": stage[2] if len(stage) == 3 else None,
                "t_min": stage[0],
                "t_max": stage[1],
            }
            for index, stage in enumerate(stages)
        }
        hint = "Load fewer stages, or raise max_rows."
        estimate = len(stages) * ROWS_PER_SAMPLE_ESTIMATE
        if estimate > max_rows:
            raise RowCapExceeded(
                estimate,
                max_rows,
                estimated=True,
                unit="stage",
                done=0,
                left=list(items.values()),
                hint=hint,
            )
        cap = _RowCap(max_rows, "stage", items, lambda args: args[0], hint)
        with self._errors("read the stage peaks of", sample):
            try:
                df = _loaders.load_peaks_by_stage(
                    self.client,
                    sample.id,
                    [tuple(stage) for stage in stages],
                    matches=matches,
                    areas=areas,
                    heights=heights,
                    max_workers=self.max_workers,
                    on_result=cap,
                )
            except ValueError as e:  # the SDK's "Sample ... not found."
                raise NotFound(
                    sample.id,
                    "sample",
                    "It may have been deleted, or never been visible to this user.",
                ) from e
        return df, [sample]
