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

    hits = kit.query_by_mz(124.9244, ["[M+Br]-"])   # Mascope's candidates
    result = kit.match_compounds("Sample 1", hits, suggested=["CH2O3"])
    result                                    # verdicts, never the tree
    kit.answer("CH2O2 is probable at m/z 124.924", code=..., inferred=[...])

Every call is written to the session's provenance manifest
(:mod:`mascope_ai.manifest`); :meth:`Toolkit.answer` closes an answer there.
"""

import functools
import inspect
import re
from collections.abc import Callable, Iterable
from typing import Any

import pandas as pd

from mascope_sdk import MascopeClient, _loaders
from mascope_sdk.exceptions import ValidationError

from .errors import (
    LISTED,
    AccessDenied,
    AmbiguousName,
    InvalidRequest,
    NotFound,
    RowCapExceeded,
    ToolkitError,
    Unavailable,
    sdk_errors,
)
from .frame import Frame, jsonable
from .manifest import (
    CLIENT_COMPUTED,
    CLIENT_OPS,
    frame_values,
    ref_list,
    toolkit_record,
)
from .oracle import (
    NOT_IN_ANSWER,
    ORIGINS,
    Candidate,
    Candidates,
    MatchResult,
    Unscored,
    condense,
    formula_key,
    tree_rows,
)
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
_OPS = (
    "load_peaks",
    "load_timeseries",
    "load_spectrum",
    "load_peaks_by_stage",
    "match_compounds",
)

#: The formula a refusal of the match route names: its 422 says which.
_REFUSED_FORMULA = re.compile(r"Formula '(.*?)' ")

#: The match route's refusals of a formula that do not say which: a numeric
#: mass, and bracket isotope notation such as ``[13C]``.
_UNNAMED_REFUSALS = (
    "Mass-based target compounds are no longer supported",
    "Explicit isotope notation is not allowed",
)

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
    """*value* as a list: the items of any iterable but a string, a mapping
    or a table (a NumPy array or a Series of names, a generator), else
    *value* alone."""
    if value is None:
        return []
    if isinstance(value, (str, bytes, dict, pd.DataFrame)) or not isinstance(
        value, Iterable
    ):
        return [value]
    return list(value)


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


def _check_cap(max_rows: Any, name: str = "max_rows") -> int:
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise InvalidRequest(f"{name} must be a positive int, not {max_rows!r}")
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


def _param(value: Any) -> Any:
    """A call's argument as the manifest records it: plain JSON, a table by
    its size, never its rows."""
    if isinstance(value, Ref):
        return value.to_dict()
    if isinstance(value, Frame):
        return {"$frame": value.id}
    if isinstance(value, Candidate):
        return value.to_dict()
    if isinstance(value, re.Pattern):
        return {"$regex": value.pattern}
    if isinstance(value, pd.DataFrame):
        return {"$dataframe": {"rows": len(value), "columns": len(value.columns)}}
    if isinstance(value, (list, tuple)):
        return [_param(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _param(item) for key, item in value.items()}
    return jsonable(value)


def _outcome(error: Exception) -> dict[str, Any]:
    if isinstance(error, ToolkitError):
        return error.to_dict()
    return {"error": type(error).__name__, "message": str(error)[:500]}


def _result(record: dict[str, Any], result: Any) -> None:
    """What a call's *result* touched and returned, into its *record*."""
    if isinstance(result, Frame):
        record["refs"] = ref_list(result.source_refs)
        record["frame"] = result.id
        record["values"] = frame_values(result)
        record["provenance"] = result.provenance
    elif isinstance(result, MatchResult):
        record["refs"] = ref_list([result.sample])
        record["frame"] = result.tree.id
        record["values"] = {
            "mascope": [
                "verdict score and status (Mascope's compound score and match "
                "category)",
                "ion scores and statuses",
                "attributed peak ids",
                *result.tree.columns,
            ],
            "client": {},
            "unscored": [unscored.to_dict() for unscored in result.unscored],
        }
        record["provenance"] = result.provenance
    elif isinstance(result, Candidates):
        record["refs"] = []
        record["values"] = {
            "mascope": ["candidate formula, mechanism, m/z and ppm error"],
            "client": {},
        }
    elif isinstance(result, Found):
        record["refs"] = ref_list(result)
    elif isinstance(result, Ref):
        record["refs"] = ref_list([result])
    elif isinstance(result, dict) and isinstance(result.get("ref"), dict):
        record["refs"] = [result["ref"]]
        record["values"] = {"mascope": sorted(result), "client": {}}


def _recorded(op: str):
    """Write each call of the method into the session's manifest.

    Only the outermost call is written: a loader's own ``resolve`` is part
    of the load, not a call of its own.
    """

    def wrap(method):
        signature = inspect.signature(method)

        @functools.wraps(method)
        def recorded(self: "Toolkit", *args, **kwargs):
            if self._recording:
                return method(self, *args, **kwargs)
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            record: dict[str, Any] = {
                "type": "call",
                "op": op,
                "params": {
                    name: _param(value)
                    for name, value in list(bound.arguments.items())[1:]
                },
            }
            self._recording = True
            try:
                result = method(self, *args, **kwargs)
            except Exception as e:
                record["outcome"] = _outcome(e)
                self._write(record)
                raise
            finally:
                self._recording = False
            _result(record, result)
            record["outcome"] = "ok"
            self._write(record)
            return result

        return recorded

    return wrap


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
        self._recording = False
        self._opened = False

    def __repr__(self) -> str:
        return (
            f"Toolkit(workspace={self.client.workspace_name!r}, "
            f"session={self.session.id!r})"
        )

    # --- the manifest --------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append *record* to the session's manifest, after this toolkit's
        own record the first time."""
        manifest = self.session.manifest
        if not self._opened:
            manifest.append(toolkit_record(self.client, self.session.id))
            self._opened = True
        return manifest.append(record)

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

    @_recorded("find")
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

    @_recorded("resolve")
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

    @_recorded("describe")
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
        modes = self._modes_of(samples)
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
            "n_ionization_modes": len(modes)
            if "ionization_mode_id" in samples
            else None,
            "ionization_modes": modes,
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
        mode_id = record.get("ionization_mode_id")
        mode = self._ionization_modes([mode_id]).get(mode_id, {}) if mode_id else {}
        return {
            "batch_id": record.get("sample_batch_id"),
            "type": record.get("sample_item_type"),
            "measured": record.get("datetime_utc"),
            "polarity": record.get("polarity"),
            "instrument": record.get("instrument"),
            "instrument_type": record.get("instrument_type"),
            "ionization_mode": mode.get("name"),
            "ionization_mode_id": mode_id,
            "ionization_mechanisms": mode.get("mechanisms"),
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

    def _modes_of(self, samples: pd.DataFrame) -> list[dict[str, Any]]:
        """The ionization modes of *samples*, most samples first: each with
        its mechanisms and how many of the samples it has."""
        if "ionization_mode_id" not in samples:
            return []
        counts = samples["ionization_mode_id"].value_counts(dropna=True)
        modes = self._ionization_modes(list(counts.index))
        return [
            {
                "id": mode_id,
                "name": modes.get(mode_id, {}).get("name"),
                "mechanisms": modes.get(mode_id, {}).get("mechanisms"),
                "n_samples": int(n),
            }
            for mode_id, n in counts.items()
        ]

    def _ionization_modes(self, mode_ids: list[str]) -> dict[str, dict[str, Any]]:
        """The modes *mode_ids* name, by ID: their name and the names of
        their mechanisms, sorted. A mode the server does not list is left
        out.

        The adopted modes are listed first and the system ones only for an
        ID they lack: a sample processed under a mode the server since
        stopped adopting still names it.
        """
        found: dict[str, dict[str, Any]] = {}
        with self._errors("list the ionization modes"):
            for include_system in (False, True):
                if all(mode_id in found for mode_id in mode_ids):
                    break
                rows = self.client.ionization.modes(include_system=include_system)
                for row in [] if rows is None else rows.to_dict("records"):
                    if row["ionization_mode_id"] in mode_ids:
                        found[row["ionization_mode_id"]] = row
            names = self._mechanism_names() if found else {}
        return {
            mode_id: {
                "name": row.get("ionization_mode_name"),
                "mechanisms": sorted(
                    names.get(mechanism, mechanism)
                    for mechanism in row.get("ionization_mechanism_ids") or []
                ),
            }
            for mode_id, row in found.items()
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
        return self._keep(op, params, df, sources)

    def _keep(
        self,
        op: str,
        params: dict[str, Any],
        df: pd.DataFrame | None,
        sources: list[Ref],
    ) -> Frame:
        """Keep what loader *op* returned as a Frame of the session."""
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
            client_columns=tuple(CLIENT_COMPUTED) if op in CLIENT_OPS else (),
        )
        self.session.save(frame, df)
        return frame

    def _rerun(self, query: dict[str, Any]) -> pd.DataFrame:
        """A recorded query's rows, loaded again (the session's reloader)."""
        op = query["op"]
        if op == "derive":
            raise InvalidRequest(
                "A Frame derived in Python (from "
                f"{', '.join(query['params'].get('from', [])) or 'other Frames'}) "
                "cannot be loaded again: its rows were the session's only copy. "
                "Derive it again from its sources."
            )
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

    @_recorded("load_peaks")
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

    @_recorded("load_timeseries")
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

    @_recorded("load_spectrum")
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

    @_recorded("load_peaks_by_stage")
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

    # --- the oracle ----------------------------------------------------------

    def _mechanisms(self, values: "str | list[str]") -> list[tuple[str, str]]:
        """The ionization mechanisms *values* name, by name or ID, as
        ``(id, name)``."""
        wanted = [str(v) for v in _as_list(values)]
        if not wanted:
            raise InvalidRequest(
                "Name at least one ionization mechanism, e.g. '[M+H]+'"
            )
        with self._errors("list the ionization mechanisms"):
            known = self._mechanism_names()
        by_name = {name: mechanism_id for mechanism_id, name in known.items()}
        found = []
        for value in wanted:
            if value in known:
                found.append((value, known[value]))
            elif value in by_name:
                found.append((by_name[value], value))
            else:
                raise NotFound(
                    value,
                    "ionization mechanism",
                    f"Mascope has {len(known)}: "
                    f"{', '.join(sorted(by_name)[:LISTED])}"
                    f"{' ...' if len(by_name) > LISTED else ''}.",
                )
        return found

    @_recorded("query_by_mz")
    def query_by_mz(
        self,
        mz: float,
        mechanisms: "str | list[str]",
        tolerance_ppm: float = 5.0,
        *,
        formula_ranges: str | None = None,
        limit: int = 20,
        isotopologues: bool = True,
        known_only: bool = False,
    ) -> Candidates:
        """The formulas Mascope's mass query proposes for an m/z.

        Candidates, not verdicts: :meth:`match_compounds` scores them in a
        sample. By default *mz* may be any line of a candidate's ion above
        Mascope's floor, not only its monoisotopic one, as a peak's m/z may
        be a 13C or 81Br line: a candidate's ``mz`` and ``error_ppm`` are
        taken against the line it was read at. With ``isotopologues=False``
        the query reads *mz* as the ion's monoisotopic line only.

        :param mz: The m/z, e.g. a peak's.
        :param mechanisms: The ionization mechanisms to consider, by name
            (``"[M+Br]-"``) or ID.
        :param tolerance_ppm: The window around *mz*, in ppm.
        :param formula_ranges: The elements and counts allowed, e.g.
            ``"C0-20 H0-40 O0-10 N0-5"``; the SDK's C, H, O and N ranges
            when None.
        :param limit: How many to return at most, the closest first;
            ``total`` says how many Mascope found.
        :param isotopologues: Read *mz* at any line of a candidate's ion,
            as above; False for the monoisotopic line only. A server that
            predates it searches the monoisotopic line only, whatever this
            says.
        :param known_only: Keep only the formulas of a known reference
            compound. A server that predates it returns every candidate.
        :raises NotFound: On a mechanism Mascope does not have.
        """
        if isinstance(mz, bool) or not isinstance(mz, (int, float)) or mz <= 0:
            raise InvalidRequest(f"mz is a positive number, not {mz!r}")
        if (
            isinstance(tolerance_ppm, bool)
            or not isinstance(tolerance_ppm, (int, float))
            or tolerance_ppm <= 0
        ):
            raise InvalidRequest(
                f"tolerance_ppm is a positive number, not {tolerance_ppm!r}"
            )
        limit = _check_cap(limit, "limit")
        for name, flag in (
            ("isotopologues", isotopologues),
            ("known_only", known_only),
        ):
            if not isinstance(flag, bool):
                raise InvalidRequest(f"{name} is True or False, not {flag!r}")
        ids = [mechanism_id for mechanism_id, _ in self._mechanisms(mechanisms)]
        options = {"formula_ranges": formula_ranges} if formula_ranges else {}
        with self._errors("query the formulas of an m/z"):
            records = self.client.cheminfo.query_by_mz(
                float(mz),
                ids,
                mz_tolerance=float(tolerance_ppm),
                limit=limit,
                isotopologues=isotopologues,
                known_only=known_only,
                **options,
            )
        candidates = sorted(
            (
                Candidate.from_record(record)
                for record in records or []
                if record.get("target_compound_formula")
            ),
            key=lambda c: (
                abs(c.error_ppm) if c.error_ppm is not None else float("inf"),
                c.formula,
                c.mechanism or "",
            ),
        )
        return Candidates(
            candidates[:limit],
            mz=float(mz),
            tolerance_ppm=float(tolerance_ppm),
            total=len(candidates),
        )

    @_recorded("match_compounds")
    def match_compounds(
        self,
        sample: "Ref | str",
        formulas: "Iterable[str | Candidate]",
        params: dict[str, Any] | None = None,
        *,
        suggested: "Iterable[str]" = (),
        mechanisms: "str | list[str] | None" = None,
    ) -> MatchResult:
        """Mascope's scores for candidate formulas in a sample, condensed.

        Each candidate becomes a :class:`~mascope_ai.oracle.Verdict`:
        Mascope's compound score and match category, its ions' scores, and
        the peaks attributed to it. The model's own suggestions go in
        *suggested* and are scored the same way; every verdict says where
        its candidate came from. A candidate Mascope does not score - a
        formula it refuses, or one it returns nothing for - is
        :attr:`~mascope_ai.oracle.MatchResult.unscored`: speculation, not a
        verdict. Mascope's whole tree is the result's ``tree``, a Frame.

        Nothing is saved: this is Mascope's matching preview.

        :param sample: The sample: its Ref, ID or name.
        :param formulas: Candidates from the caller (formulas) or from
            :meth:`query_by_mz` (Candidates).
        :param params: Match parameters (``mz_tolerance``,
            ``isotope_ratio_tolerance``, ``peak_min_intensity``,
            ``isotope_abundance_threshold``, ``possible_match_threshold``,
            ``probable_match_threshold``), all of them; None for the
            instrument's defaults, which the app's matches use.
        :param suggested: The model's own candidates, by formula.
        :param mechanisms: The ionization mechanisms, by name or ID; None
            for the sample's own, as the app matches with.
        :raises InvalidRequest: If no candidate is given, or the server
            refuses the parameters.
        """
        sample_ref = self.resolve(sample, "sample")
        asked: list[tuple[str, str]] = []
        seen: set[str] = set()
        for candidate, origin in [
            *((c, "given") for c in _as_list(formulas)),
            *((c, "model") for c in _as_list(suggested)),
        ]:
            if isinstance(candidate, Candidate):
                candidate, origin = candidate.formula, candidate.origin
            if not isinstance(candidate, str) or not formula_key(candidate):
                raise InvalidRequest(
                    f"A candidate is a formula or a Candidate, not {candidate!r}"
                )
            if formula_key(candidate) not in seen:
                seen.add(formula_key(candidate))
                asked.append((candidate, origin if origin in ORIGINS else "given"))
        if not asked:
            raise InvalidRequest("Give at least one formula to score")
        if params is not None and not isinstance(params, dict):
            raise InvalidRequest(f"params is a dict or None, not {params!r}")
        query = {
            "sample": sample_ref,
            "candidates": [list(pair) for pair in asked],
            "params": params,
            "mechanisms": [
                mechanism_id for mechanism_id, _ in self._mechanisms(mechanisms)
            ]
            if mechanisms is not None
            else None,
        }
        df, sources = self._load("match_compounds", query)
        refused: dict[str, str] = dict(df.attrs.get("refused", {}))
        tree = self._keep("match_compounds", query, df, sources)
        verdicts, missing = condense(df, asked)
        origin = {formula_key(f): (f, o) for f, o in asked}
        unscored = tuple(
            Unscored(origin[key][0], origin[key][1], refused.get(key, NOT_IN_ANSWER))
            for key in missing
        )
        return MatchResult(sample_ref, verdicts, unscored, tree, params)

    def _match_compounds(
        self,
        sample: Ref,
        candidates: list[list[str]],
        params: dict[str, Any] | None,
        mechanisms: list[str] | None,
    ) -> tuple[pd.DataFrame, list[Ref]]:
        """Mascope's tree for the candidates, as rows (:func:`tree_rows`).

        The route refuses the whole request over one formula it cannot
        read. When it says which, that one is set aside, with the server's
        reason, and the rest sent again; when it does not (a numeric mass,
        bracket isotope notation), each is sent alone to find the refused.
        """
        origins = {formula_key(f): (f, o) for f, o in candidates}
        pending = [f for f, _ in candidates]
        refused: dict[str, str] = {}
        compounds: list[dict[str, Any]] = []

        def score(formulas: list[str]) -> list[dict[str, Any]]:
            return (
                self.client.matching.match_compounds(
                    sample.id,
                    formulas,
                    match_params=params,
                    ionization_mechanism_ids=mechanisms,
                )
                or []
            )

        def refuse(formula: str, message: str) -> None:
            reason = message.split("Value error, ", 1)[-1]
            refused[formula_key(formula)] = f"Mascope refused it: {reason}"
            pending.remove(formula)

        with self._errors("score candidates in", sample):
            while pending:
                try:
                    compounds = score(list(pending))
                    break
                except ValidationError as e:
                    named = _refused(e.message, pending)
                    if named is not None:
                        refuse(named, e.message)
                    elif not _refuses_unnamed(e.message):
                        raise
                    elif len(pending) == 1:
                        refuse(pending[0], e.message)
                    else:
                        before = len(pending)
                        for formula in list(pending):
                            try:
                                score([formula])
                            except ValidationError as alone:
                                if _refused(
                                    alone.message, [formula]
                                ) is None and not _refuses_unnamed(alone.message):
                                    raise
                                refuse(formula, alone.message)
                        if len(pending) == before:
                            raise
            names = self._mechanism_names()
        df = tree_rows(compounds, origins, names)
        df.attrs.update(
            complete=True,
            failures=[],
            warnings=[
                f"{origins[key][0]!r}: {reason}" for key, reason in refused.items()
            ],
            refused=refused,
        )
        return df, [sample]

    def _mechanism_names(self) -> dict[str, str]:
        """Every ionization mechanism Mascope has, by ID: its name."""
        rows = self.client.ionization.list()
        if rows is None or rows.empty:
            return {}
        return dict(zip(rows["ionization_mechanism_id"], rows["ionization_mechanism"]))

    # --- derived Frames ------------------------------------------------------

    @_recorded("derive")
    def derive(
        self,
        df: pd.DataFrame,
        inputs: "Frame | Iterable[Frame]",
        *,
        code: str | None = None,
        computed: "str | Iterable[str]" = (),
    ) -> Frame:
        """Keep a table computed in Python from Frames as a Frame of its own.

        What the inputs were loaded from carries over: the new Frame's
        ``source_refs`` are theirs, so an answer built on it still names
        every Mascope object behind it, and its client-side columns, its
        completeness and its failures are theirs too. Its rows are the
        session's only copy: it cannot be loaded again from a query.

        The manifest records its columns by who produced them. A column no
        input has, or one an input computed in Python, was computed by
        *code*; so was one named in *computed*, such as an aggregate that
        kept its input column's name. Any other column is its input's: by
        name, so a column the SDK computes in one input (a peak load's
        ``match_score_ion``) counts as the SDK's even where another input,
        Mascope's match tree, has a column of that name from Mascope.

        :param df: The table, e.g. ``peaks.df.groupby(...).mean()``.
        :param inputs: The Frames it was computed from.
        :param code: The code that computed it.
        :param computed: Columns of *df* that keep an input column's name
            but were computed anew by *code*.
        :raises InvalidRequest: Without a DataFrame or an input Frame, or
            with a *computed* column *df* does not have.
        """
        if not isinstance(df, pd.DataFrame):
            raise InvalidRequest(f"derive() keeps a DataFrame, not {type(df).__name__}")
        frames = [inputs] if isinstance(inputs, Frame) else list(inputs)
        if not frames or not all(isinstance(f, Frame) for f in frames):
            raise InvalidRequest("derive() needs the Frames the table came from")
        sources: list[Ref] = []
        for frame in frames:
            sources += [ref for ref in frame.source_refs if ref not in sources]
        recomputed = [computed] if isinstance(computed, str) else list(computed)
        columns = [str(column) for column in df.columns]
        if not all(isinstance(c, str) and c in columns for c in recomputed):
            raise InvalidRequest(
                f"computed names columns of the table, not {recomputed!r}"
            )
        loaded = {c for f in frames for c in f.columns}
        new = [c for c in columns if c not in loaded]
        new += [c for f in frames for c in f.computed_columns]
        new = list(dict.fromkeys([*new, *recomputed]))
        client = [c for f in frames for c in f.client_columns if c not in new]
        rows = df.copy()
        rows.attrs = {
            "complete": all(f.complete for f in frames),
            "failures": [note.to_dict() for f in frames for note in f.failures],
            "warnings": list(dict.fromkeys(w for f in frames for w in f.warnings)),
        }
        provenance = next((f.provenance for f in frames if f.provenance), None)
        if provenance is not None:
            rows.attrs["provenance"] = provenance
        frame = Frame.from_df(
            rows,
            source_refs=sources,
            query={
                "op": "derive",
                "params": {"from": [f.id for f in frames], "code": code},
            },
            session=self.session,
            client_columns=tuple(dict.fromkeys(client)),
            computed_columns=tuple(new),
        )
        self.session.save(frame, rows)
        return frame

    # --- answers -------------------------------------------------------------

    def answer(
        self,
        text: str,
        *,
        code: str | None = None,
        inferred: "str | Iterable[str]" = (),
        frames: "Iterable[Frame | str]" = (),
    ) -> dict[str, Any]:
        """Record an answer in the session's manifest, and what it rests on.

        The answer covers the toolkit's calls since the answer before it.
        The record lists them, every Mascope object they touched, and the
        values the answer rests on, split by who produced them: Mascope
        (``mascope``), the SDK on the client (``client``: the ion and
        compound scores of a peak load), code in the session (``computed``:
        the columns a :meth:`derive` computed), nobody (``unscored``:
        candidates Mascope did not score) and the model (``inferred``).

        :param text: The answer, as given to the user.
        :param code: The code that produced it.
        :param inferred: The answer's statements that no value backs: the
            model's own reading, said as such.
        :param frames: Frames made before the previous answer that this one
            rests on too, or their IDs.
        :return: The record, as written.
        """
        if not isinstance(text, str):
            raise InvalidRequest("text is the answer, as a string")
        statements = [inferred] if isinstance(inferred, str) else list(inferred)
        if not all(isinstance(s, str) for s in statements):
            raise InvalidRequest("inferred is a statement or a list of statements")
        calls = self.session.manifest.since_last_answer()
        made = {call["frame"]: call for call in calls if call.get("frame")}
        cited = []
        for frame in frames:
            frame = frame if isinstance(frame, Frame) else self.session.frame(frame)
            if frame.id not in made:
                cited.append(frame)

        refs: list[dict[str, Any]] = []
        split: dict[str, list[dict[str, Any]]] = {
            "mascope": [],
            "client": [],
            "computed": [],
        }
        unscored = []

        def add(source: str, op: str | None, values: dict[str, Any]) -> None:
            for kind, entries in split.items():
                if kind == "mascope" or values.get(kind):
                    entries.append({"source": source, "op": op, "fields": values[kind]})

        for call in calls:
            refs += [r for r in call.get("refs", ()) if r not in refs]
            values = call.get("values")
            if call.get("outcome") != "ok" or not values:
                continue
            add(call.get("frame") or f"call {call['seq']}", call["op"], values)
            unscored += values.get("unscored", [])
        for frame in cited:
            refs += [r for r in ref_list(frame.source_refs) if r not in refs]
            add(frame.id, frame.query.get("op"), frame_values(frame))
        return self._write(
            {
                "type": "answer",
                "text": text,
                "code": code,
                "calls": [call["seq"] for call in calls],
                "frames": [*made, *(frame.id for frame in cited)],
                "refs": refs,
                "values": {
                    **split,
                    "unscored": unscored,
                    "inferred": statements,
                },
            }
        )


def _refused(message: str, pending: list[str]) -> str | None:
    """The pending formula a refusal of the match route names, if any."""
    match = _REFUSED_FORMULA.search(message or "")
    if match is None:
        return None
    named = formula_key(match.group(1))
    return next((f for f in pending if formula_key(f) == named), None)


def _refuses_unnamed(message: str) -> bool:
    """Whether a refusal of the match route is of a formula it does not name."""
    return any(text in (message or "") for text in _UNNAMED_REFUSALS)
