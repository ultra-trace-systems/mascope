"""The plan builders and ``apply``, behind the toolkit's methods of the same
names (:class:`~mascope_ai.toolkit.Toolkit`).

A builder reads, checks and describes; it writes nothing. What it checks is
what makes a doomed apply fail at planning instead: that each object exists
and is not locked against the change, that the user's role in each
workspace the change touches is enough, and that nothing shared by every
workspace is written unless the user asked for it (``allow_global``).

``apply`` is the one function here that writes. In order, it:

1. loads the plan from the session and checks the confirm token is an
   approval of it as it stands;
2. claims it (:meth:`~mascope_ai.plans.PlanStore.claim`), unless it is
   expired, so that of two overlapping applies only one goes on; a second
   apply with the same approval gets the first one's receipt;
3. reads every ``before`` again and stops, with a diff, if one changed;
4. sends the plan's writes in order, through the SDK's write methods, and
   records which were applied: a write that fails stops the rest, and the
   plan is not applied again.
"""

import hashlib
import time
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .errors import (
    AccessDenied,
    ApprovalRefused,
    GlobalNamespace,
    InvalidRequest,
    NotFound,
    PartiallyApplied,
    PlanConsumed,
    PlanDrifted,
    PlanExpired,
    ToolkitError,
    Unavailable,
)
from .frame import jsonable
from .oracle import Candidate
from .plans import (
    Change,
    Move,
    Plan,
    Receipt,
    new_plan_id,
    now,
    token_fingerprint,
)
from .refs import Ref


if TYPE_CHECKING:
    from .toolkit import Toolkit


#: The workspace roles, by their access level (the server's ``roles.py``).
ROLE_LEVELS = {"guest": 100, "editor": 200, "admin": 300, "owner": 400}

#: A matching run over at least this many samples is called a long job in
#: the plan's cost.
LONG_MATCH_SAMPLES = 50

#: The match levels an aggregate run answers with.
MATCH_LEVELS = ("match_ions", "match_compounds", "match_collections", "match_samples")

#: The fields a compound of ``plan_collection`` may give. A compound that
#: also gives one of :data:`IDENTITY_FIELDS` can rewrite the shared record
#: of a compound that exists: the server fills a missing CAS number or
#: structure into it.
COMPOUND_FIELDS = frozenset({"target_compound_formula", "target_compound_name"})
IDENTITY_FIELDS = frozenset({"cas_number", "inchikey", "inchi", "smiles"})

#: The SDK write methods a plan's steps may name: nothing else is sent.
WRITES: dict[str, Callable[..., Any]] = {
    "datasets.move": lambda client, **args: client.datasets.move(**args),
    "samples.move": lambda client, **args: client.samples.move(**args),
    "collections.create": lambda client, **args: client.collections.create(**args),
    "collections.associate": lambda client, **args: client.collections.associate(
        **args
    ),
    "matching.save_sample": lambda client, **args: client.matching.save_sample(**args),
    "matching.save_batch": lambda client, **args: client.matching.save_batch(**args),
    "matching.resave_batch": lambda client, **args: client.matching.resave_batch(
        **args
    ),
}

#: What a receipt keeps of the server's answer to a write.
_RESPONSE_KEYS = ("message", "status", "process_id", "target_collection_id")

#: How often an apply waiting for another one's outcome looks again, in
#: seconds.
_POLL = 0.2


# --- what the builders read ----------------------------------------------------


def _fresh(kit: "Toolkit") -> None:
    """Read past the client's metadata cache from here on: a plan records
    what the server holds now, and the role the user has now."""
    kit.client.clear_cache()


def _home(kit: "Toolkit") -> Ref:
    """The client's workspace: where the toolkit finds what it plans on."""
    return Ref(
        id=str(kit.client.workspace_id),
        kind="workspace",
        name=str(kit.client.workspace_name or ""),
    )


def _roles(kit: "Toolkit") -> dict[str, str | None]:
    """The user's role in each workspace they belong to, by its ID."""
    with kit._errors("list the workspaces"):
        rows = kit.client.workspaces.list()
    if rows is None or rows.empty:
        return {}
    return {
        str(row["workspace_id"]): (str(row["my_role"]) if row.get("my_role") else None)
        for row in rows.to_dict("records")
    }


def _need(
    roles: dict[str, str | None],
    workspace: Ref,
    role: str,
    operation: str,
    target: Ref | None,
) -> None:
    """Refuse at planning what the user's role in *workspace* cannot do."""
    have = roles.get(workspace.id)
    if ROLE_LEVELS.get(have or "", 0) < ROLE_LEVELS[role]:
        raise AccessDenied(
            operation,
            target,
            f"{role} in workspace {workspace.name!r}",
            have,
            workspace.name,
        )


def _locked(value: Any) -> bool:
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def _row(rows: Any, column: str, value: str) -> dict[str, Any]:
    if rows is None or rows.empty:
        return {}
    match = rows[rows[column] == value]
    return match.iloc[0].to_dict() if not match.empty else {}


def _sample_record(kit: "Toolkit", sample: Ref) -> dict[str, Any]:
    with kit._errors("read", sample):
        record = kit.client.samples.get(sample.id)
    if not record:
        raise NotFound(sample.id, "sample")
    return record


def _collection_record(kit: "Toolkit", collection: Ref) -> dict[str, Any]:
    with kit._errors("read", collection):
        record = kit.client.collections.get(collection.id)
    if not record:
        raise NotFound(collection.id, "collection")
    return record


def _batch_ids(record: dict[str, Any]) -> list[str]:
    return sorted(
        str(batch["sample_batch_id"]) for batch in record.get("sample_batches") or ()
    )


def _home_batches(kit: "Toolkit") -> dict[str, Ref]:
    """The batches of the client's workspace, by ID."""
    return {batch.id: batch for batch in kit._all_batches()}


def _at_home(batches: dict[str, Ref], batch_id: str, home: Ref, target: Ref) -> Ref:
    """The batch *batch_id*, which holds or is *target*, refused when it is
    not in the client's workspace.

    A builder checks the user's role in the client's workspace, so it plans
    nothing elsewhere: a sample found by its ID, or a Ref passed in, can be
    in another workspace, where the role checked would not be the one the
    server asks for.
    """
    batch = batches.get(batch_id)
    if batch is None:
        raise InvalidRequest(
            f"{target} is not in workspace {home.name!r}, the client's: the "
            "toolkit plans writes in its client's workspace alone. Open a "
            "client on the workspace it is in to plan this."
        )
    return batch


def _match_inputs(kit: "Toolkit", scope: Ref) -> dict[str, Any]:
    """What a matching save of *scope* is computed from: its samples and the
    collections associated with its batch. ``apply`` checks it again."""
    if scope.kind == "batch":
        samples = sorted(
            str(s) for s in kit._sample_rows(scope).get("sample_item_id", ())
        )
        batch_id = scope.id
        inputs: dict[str, Any] = {
            "n_samples": len(samples),
            "samples_sha256": hashlib.sha256(",".join(samples).encode()).hexdigest()[
                :16
            ],
        }
    else:
        batch_id = str(_sample_record(kit, scope)["sample_batch_id"])
        inputs = {"batch": batch_id}
    with kit._errors("list the target collections of", scope):
        rows = kit.client.collections.list(batch_id)
    inputs["collections"] = (
        sorted(str(c) for c in rows["target_collection_id"])
        if rows is not None and not rows.empty
        else []
    )
    return inputs


def _named_collection(
    kit: "Toolkit", workspace_id: str | None, name: str
) -> str | None:
    """The ID of a collection named *name* (case aside) in the workspace, or
    among the global ones for None; None when there is none."""
    with kit._errors("list the target collections"):
        if workspace_id is None:
            rows = kit.client.collections.list()
        else:
            rows = kit.client.collections.list(workspace_id=workspace_id)
    if rows is None or rows.empty:
        return None
    for row in rows.to_dict("records"):
        same_place = (row.get("workspace_id") or None) == workspace_id
        if same_place and str(row.get("target_collection_name", "")).casefold() == (
            name.casefold()
        ):
            return str(row["target_collection_id"])
    return None


def _plan(kit: "Toolkit", **fields: Any) -> Plan:
    """A plan made now, expiring after the toolkit's ``plan_ttl``, kept in
    the session."""
    made = now()
    plan = Plan(
        id=new_plan_id(), created_at=made, expires_at=made + kit.plan_ttl, **fields
    )
    kit.session.plans.save(plan)
    return plan


def _step(op: str, args: dict[str, Any], changes: Iterable[int], results=()) -> dict:
    if op not in WRITES:
        raise ValueError(f"Not a write a plan may make: {op!r}")
    return {
        "op": op,
        "args": jsonable(args),
        "changes": list(changes),
        "results": [ref.to_dict() for ref in results],
    }


def _distinct(refs: Iterable[Ref]) -> tuple[Ref, ...]:
    seen: set[tuple[str, str]] = set()
    out = []
    for ref in refs:
        if (ref.kind, ref.id) not in seen:
            seen.add((ref.kind, ref.id))
            out.append(ref)
    return tuple(out)


def _moved(ref: Ref, parent: str) -> Ref:
    return Ref(id=ref.id, kind=ref.kind, name=ref.name, parent=parent)


# --- the builders ------------------------------------------------------------------


def plan_organize(kit: "Toolkit", moves: Sequence[Any]) -> Plan:
    if isinstance(moves, Move) or not isinstance(moves, Sequence) or not moves:
        raise InvalidRequest("moves is a non-empty list of Move(item, to)")
    _fresh(kit)
    home = _home(kit)
    roles = _roles(kit)
    changes: list[Change] = []
    steps: list[dict[str, Any]] = []
    affected: list[Ref] = []
    warnings: list[str] = []
    needs: list[str] = []
    into_batch: dict[str, tuple[Ref, list[tuple[Ref, int]]]] = {}
    seen: set[tuple[str, str]] = set()
    batches: dict[str, Ref] | None = None  # listed at the first sample move

    for move in moves:
        if isinstance(move, (tuple, list)) and len(move) in (2, 3):
            move = Move(*move)
        if not isinstance(move, Move):
            raise InvalidRequest(f"A move is a Move(item, to), not {move!r}")
        kind = move.item.kind if isinstance(move.item, Ref) else move.kind
        if kind is None:
            raise InvalidRequest(
                f"Move of {move.item!r}: a name or ID needs its kind "
                "(Move(item, to, kind='dataset'))"
            )
        if kind == "batch":
            raise InvalidRequest(
                "Mascope has no route that moves a batch into another dataset, "
                "so the toolkit plans none. Move its samples into a batch of "
                "that dataset instead (Move(sample, batch))."
            )
        if kind not in ("dataset", "sample"):
            raise InvalidRequest(
                f"plan_organize moves datasets and samples, not {kind}s"
            )

        if kind == "dataset":
            dataset = kit.resolve(move.item, "dataset")
            to = kit.resolve(move.to, "workspace")
            if (dataset.kind, dataset.id) in seen:
                raise InvalidRequest(f"{dataset} is moved twice in one plan")
            seen.add((dataset.kind, dataset.id))
            with kit._errors("read", dataset):
                row = _row(kit.client.datasets.list(), "dataset_id", dataset.id)
            if not row:
                # A Ref passed in can name a dataset of another workspace,
                # whose move the role checked here would not cover
                raise InvalidRequest(
                    f"{dataset} is not in workspace {home.name!r}, the client's: "
                    "the toolkit plans writes in its client's workspace alone. "
                    "Open a client on the workspace it is in to plan this."
                )
            if to.id == home.id:
                raise InvalidRequest(f"{dataset} is in workspace {to.name!r} already")
            operation = f"move into workspace {to.name!r}"
            _need(roles, home, "editor", operation, dataset)
            _need(roles, to, "editor", operation, dataset)
            if _locked(row.get("locked")):
                warnings.append(
                    f"Dataset {dataset.name!r} is locked: moving it needs the "
                    "global admin role, which the toolkit cannot see."
                )
                needs.append(
                    f"the global admin role for locked dataset {dataset.name!r}"
                )
            index = len(changes)
            changes.append(
                Change(
                    dataset,
                    "workspace",
                    home.id,
                    to.id,
                    f"Move dataset {dataset.name!r}, with everything in it, from "
                    f"workspace {home.name!r} into workspace {to.name!r}",
                )
            )
            steps.append(
                _step(
                    "datasets.move",
                    {
                        "dataset_id": dataset.id,
                        "target_workspace_id": to.id,
                    },
                    [index],
                    [_moved(dataset, to.id)],
                )
            )
            needs.append(
                f"editor in workspace {home.name!r} and in workspace {to.name!r}"
            )
            affected += [dataset, home, to]
            continue

        sample = kit.resolve(move.item, "sample")
        to = kit.resolve(move.to, "batch")
        if (sample.kind, sample.id) in seen:
            raise InvalidRequest(f"{sample} is moved twice in one plan")
        seen.add((sample.kind, sample.id))
        if batches is None:
            batches = _home_batches(kit)
        record = _sample_record(kit, sample)
        before = str(record.get("sample_batch_id"))
        source = _at_home(batches, before, home, sample)
        _at_home(batches, to.id, home, to)
        if before == to.id:
            raise InvalidRequest(f"{sample} is in batch {to.name!r} already")
        if _locked(record.get("locked")):
            raise InvalidRequest(
                f"{sample} is locked: Mascope moves no locked sample, whatever "
                "the role. Copy it instead, or unlock it in the app."
            )
        if to.id not in into_batch:
            with kit._errors("read", to):
                row = _row(kit.client.batches.list(to.parent), "sample_batch_id", to.id)
            if _locked(row.get("locked")):
                raise InvalidRequest(
                    f"Batch {to.name!r} is locked: Mascope moves no sample into "
                    "a locked batch, whatever the role."
                )
            _need(roles, home, "editor", f"move samples into batch {to.name!r}", sample)
            into_batch[to.id] = (to, [])
        index = len(changes)
        changes.append(
            Change(
                sample,
                "batch",
                before,
                to.id,
                f"Move sample {sample.name!r} from batch "
                f"{source.name!r} into batch {to.name!r}",
            )
        )
        into_batch[to.id][1].append((sample, index))
        affected += [sample, to]

    for batch, moved in into_batch.values():
        # The server makes each moved sample anew in the batch and deletes
        # the old one: the result is the batch, the samples' IDs are new
        steps.append(
            _step(
                "samples.move",
                {"sample_ids": [s.id for s, _ in moved], "batch_id": batch.id},
                [index for _, index in moved],
                [batch],
            )
        )
    if into_batch:
        needs.append(
            f"editor in workspace {home.name!r} (the samples and their batches)"
        )
        warnings.append(
            "Mascope moves samples in the background, and a moved sample gets "
            "a new ID: find it by name in its new batch once the move is done."
        )
    n_datasets = sum(1 for c in changes if c.ref.kind == "dataset")
    n_samples = len(changes) - n_datasets
    said = [
        f"{n} {what}(s)"
        for n, what in ((n_datasets, "dataset"), (n_samples, "sample"))
        if n
    ]
    return _plan(
        kit,
        kind="organize",
        summary=f"Move {' and '.join(said)}",
        changes=tuple(changes),
        affected=_distinct(affected),
        reversible=True,
        estimated_cost=None,
        required_role="; ".join(dict.fromkeys(needs)),
        gate="confirm",
        warnings=tuple(warnings),
        steps=tuple(steps),
    )


def _compound(value: Any, allow_global: bool) -> dict[str, Any]:
    if isinstance(value, Candidate):
        value = value.formula
    if isinstance(value, str):
        value = {"target_compound_formula": value}
    if not isinstance(value, dict):
        raise InvalidRequest(
            f"A compound is a formula, a Candidate or a dict, not {value!r}"
        )
    unknown = set(value) - COMPOUND_FIELDS - IDENTITY_FIELDS
    if unknown:
        raise InvalidRequest(
            f"A compound gives {sorted(COMPOUND_FIELDS | IDENTITY_FIELDS)}, "
            f"not {sorted(unknown)}"
        )
    formula = " ".join(str(value.get("target_compound_formula") or "").split())
    if not formula:
        raise InvalidRequest(f"A compound needs its formula: {value!r}")
    identity = sorted(field for field in IDENTITY_FIELDS if value.get(field))
    if identity and not allow_global:
        raise GlobalNamespace(
            f"Compound {formula!r} gives {', '.join(identity)}: Mascope fills "
            "that into the shared record of a compound that exists."
        )
    return {
        **{k: v for k, v in value.items() if v is not None},
        "target_compound_formula": formula,
    }


def plan_collection(
    kit: "Toolkit",
    name: str,
    compounds: Sequence[Any] = (),
    *,
    workspace: "Ref | str | None" = None,
    base: "Ref | str | None" = None,
    batches: Sequence["Ref | str"] = (),
    description: str | None = None,
    collection_type: str | None = None,
    is_global: bool = False,
    allow_global: bool = False,
) -> Plan:
    if not isinstance(name, str) or not name.strip():
        raise InvalidRequest("A collection needs a name")
    _fresh(kit)
    name = name.strip()
    if isinstance(compounds, (str, dict, Candidate)):
        compounds = [compounds]
    if is_global and not allow_global:
        raise GlobalNamespace(
            f"A global collection {name!r} would be in no one workspace."
        )
    home = _home(kit)
    roles = _roles(kit)
    where = (
        None
        if is_global
        else (home if workspace is None else kit.resolve(workspace, "workspace"))
    )
    batch_refs = [kit.resolve(batch, "batch") for batch in batches]
    if batch_refs and where is not None and where.id != home.id:
        raise InvalidRequest(
            f"A collection of workspace {where.name!r} is associated only with "
            f"its batches; the toolkit's batches are workspace {home.name!r}'s."
        )
    made = [_compound(compound, allow_global) for compound in compounds]
    compound_ids: list[str] = []
    affected: list[Ref] = []
    if base is not None:
        base_ref = kit.resolve(base, "collection")
        compound_ids = [
            str(compound["target_compound_id"])
            for compound in _collection_record(kit, base_ref).get("target_compounds")
            or ()
        ]
        affected.append(base_ref)
    if not made and not compound_ids:
        raise InvalidRequest(
            "A collection needs compounds: formulas, or a base collection to start from"
        )
    if _named_collection(kit, where.id if where else None, name):
        raise InvalidRequest(
            f"{'The global collections have' if where is None else f'Workspace {where.name!r} has'} "
            f"a collection named {name!r} already. Pick another name, or "
            "associate that one with plan_associate."
        )

    gate = "confirm"
    warnings = []
    if where is None:
        required = "the global admin role (a global collection)"
        gate = "hard"
        warnings.append(
            "The collection is global: every workspace sees it. Creating it "
            "needs the global admin role, which the toolkit cannot see."
        )
    else:
        _need(roles, where, "editor", "create a target collection in", where)
        required = f"editor in workspace {where.name!r}"
        affected.append(where)
    if any(set(compound) & IDENTITY_FIELDS for compound in made):
        gate = "hard"
        warnings.append(
            "Compounds give a CAS number or a structure: Mascope fills it into "
            "the shared record of a compound of the same formula and name, "
            "which every workspace's collections use."
        )
    if batch_refs:
        _need(roles, home, "editor", "associate a collection with", batch_refs[0])
        required += f", and editor on its batches (workspace {home.name!r})"
        affected += batch_refs
    new = Ref(
        id="(new)", kind="collection", name=name, parent=where.id if where else None
    )
    after = {
        "compounds": len(made) + len(compound_ids),
        "batches": sorted(b.id for b in batch_refs),
    }
    place = (
        "as a global collection" if where is None else f"in workspace {where.name!r}"
    )
    names = ", ".join(repr(b.name) for b in batch_refs)
    change = Change(
        new,
        "collection",
        None,
        after,
        f"Create collection {name!r} {place} with {after['compounds']} "
        f"compound(s){f', associated with batch(es) {names}' if names else ''}",
    )
    args = {
        "name": name,
        "description": description,
        "collection_type": collection_type,
        "compounds": made or None,
        "compound_ids": compound_ids or None,
        "batch_ids": [b.id for b in batch_refs] or None,
        "workspace_id": where.id if where else None,
        "is_global": where is None,
    }
    return _plan(
        kit,
        kind="collection",
        summary=f"Create target collection {name!r} {place}",
        changes=(change,),
        affected=_distinct(affected),
        reversible=True,
        estimated_cost=None,
        required_role=required,
        gate=gate,
        warnings=tuple(warnings),
        steps=(_step("collections.create", args, [0]),),
    )


def plan_associate(
    kit: "Toolkit",
    collection: "Ref | str",
    batches: Sequence["Ref | str"] = (),
    *,
    detach: Sequence["Ref | str"] = (),
    allow_global: bool = False,
) -> Plan:
    if isinstance(batches, (str, Ref)):
        batches = [batches]
    if isinstance(detach, (str, Ref)):
        detach = [detach]
    _fresh(kit)
    target = kit.resolve(collection, "collection")
    record = _collection_record(kit, target)
    is_global = record.get("workspace_id") is None
    if is_global and not allow_global:
        raise GlobalNamespace(f"Collection {target.name!r} is global.", target)
    home = _home(kit)
    roles = _roles(kit)
    added = [kit.resolve(batch, "batch") for batch in batches]
    removed = [kit.resolve(batch, "batch") for batch in detach]
    both = {b.id for b in added} & {b.id for b in removed}
    if both:
        raise InvalidRequest(f"Batch(es) {sorted(both)} are both added and detached")
    before = _batch_ids(record)
    after = sorted((set(before) | {b.id for b in added}) - {b.id for b in removed})
    if after == before:
        raise InvalidRequest(
            f"The plan changes nothing: collection {target.name!r} is associated "
            "with those batches, and not with those to detach, already."
        )
    changed = [b for b in added if b.id not in before] + [
        b for b in removed if b.id in before
    ]
    batches = _home_batches(kit)
    for batch in changed:
        _at_home(batches, batch.id, home, batch)
    _need(
        roles, home, "editor", f"associate collection {target.name!r} with", changed[0]
    )
    parts = []
    if any(b.id not in before for b in added):
        parts.append(
            "associate it with "
            + ", ".join(repr(b.name) for b in added if b.id not in before)
        )
    if any(b.id in before for b in removed):
        parts.append(
            "detach it from "
            + ", ".join(repr(b.name) for b in removed if b.id in before)
        )
    change = Change(
        target,
        "batches",
        before,
        after,
        f"Collection {target.name!r}: {'; '.join(parts)}",
    )
    warnings = [
        "Mascope marks each batch added or detached for a rematch with its new "
        "collections."
    ]
    if is_global:
        warnings.append(
            f"Collection {target.name!r} is global: every workspace sees it."
        )
    return _plan(
        kit,
        kind="associate",
        summary=f"Change the batches of target collection {target.name!r}",
        changes=(change,),
        affected=_distinct([target, *changed]),
        reversible=True,
        estimated_cost=None,
        required_role=(
            f"guest on the collection, and editor in workspace {home.name!r} "
            "(the batches added or detached)"
        ),
        gate="hard" if is_global else "confirm",
        warnings=tuple(warnings),
        steps=(
            _step(
                "collections.associate",
                {"collection_id": target.id, "batch_ids": after},
                [0],
                [target],
            ),
        ),
    )


def plan_match(
    kit: "Toolkit",
    scope: "Ref | str",
    targets: str | None = None,
    params: dict[str, Any] | None = None,
    save: bool = False,
    *,
    kind: str | None = None,
    rebuild: bool = False,
) -> Plan:
    kind = scope.kind if isinstance(scope, Ref) else (kind or "batch")
    if kind not in ("batch", "sample"):
        raise InvalidRequest(f"Matching runs on a batch or a sample, not a {kind}")
    if targets is not None and not isinstance(targets, str):
        raise InvalidRequest(
            "targets is one target ion's ID, the only filter the matching "
            "routes take, or None for every target of the batch's collections"
        )
    if params is not None and not isinstance(params, dict):
        raise InvalidRequest(f"params is a dict of match parameters, not {params!r}")
    if rebuild and not save:
        raise InvalidRequest("rebuild=True rebuilds saved matches: it needs save=True")
    if rebuild and kind != "batch":
        raise InvalidRequest("Only a batch's saved matches are rebuilt (rebuild=True)")
    _fresh(kit)
    ref = kit.resolve(scope, kind)
    args = {
        f"{kind}_id": ref.id,
        "target_ion_id": targets,
        "match_params": params,
    }

    if not save:
        aggregate = (
            kit.client.matching.aggregate_batch
            if kind == "batch"
            else kit.client.matching.aggregate_sample
        )
        with kit._errors("run matching on", ref):
            result = aggregate(**args) or {}
        preview = {level: len(result.get(level) or ()) for level in MATCH_LEVELS}
        return _plan(
            kit,
            kind="match",
            summary=(
                f"Preview the matching of {kind} {ref.name!r}: "
                f"{preview['match_compounds']} compound match(es) in "
                f"{preview['match_samples']} sample(s); nothing is saved"
            ),
            changes=(),
            affected=(ref,),
            reversible=True,
            estimated_cost=None,
            required_role=f"guest on the {kind}",
            gate="none",
            preview=preview,
        )

    home = _home(kit)
    batch_id = (
        ref.id if kind == "batch" else str(_sample_record(kit, ref)["sample_batch_id"])
    )
    _at_home(_home_batches(kit), batch_id, home, ref)
    _need(
        _roles(kit),
        home,
        "editor",
        "rebuild the matches of" if rebuild else "save the matches of",
        ref,
    )
    inputs = _match_inputs(kit, ref)
    n = inputs.get("n_samples", 1)
    cost = f"runs Mascope's matching over {n} sample(s) on the server"
    if n >= LONG_MATCH_SAMPLES:
        cost += ": a long job"
    if rebuild:
        op, after = "matching.resave_batch", "rebuilt: every saved level removed first"
        note = (
            f"Remove every saved match of batch {ref.name!r}, then save a fresh run's"
        )
    else:
        op = f"matching.save_{kind}"
        after = "replaced by a fresh run's"
        note = f"Replace the saved matches of {kind} {ref.name!r} with a fresh run's"
    return _plan(
        kit,
        kind="match",
        summary=f"{'Rebuild' if rebuild else 'Save'} the matches of {kind} {ref.name!r}",
        changes=(Change(ref, "matches", inputs, after, note),),
        affected=(ref,),
        reversible=False,
        estimated_cost=cost,
        required_role=f"editor in workspace {home.name!r}",
        gate="hard",
        warnings=("The matches saved before are replaced and cannot be restored.",),
        steps=(_step(op, args, [0], [ref]),),
    )


# --- apply -------------------------------------------------------------------------


def _current(kit: "Toolkit", change: Change) -> Any:
    """What *change*'s ``before`` reads as now."""
    ref, field = change.ref, change.field
    try:
        if (ref.kind, field) == ("dataset", "workspace"):
            with kit._errors("read", ref):
                rows = kit.client.datasets.list()
            return (
                str(kit.client.workspace_id)
                if _row(rows, "dataset_id", ref.id)
                else None
            )
        if (ref.kind, field) == ("sample", "batch"):
            return str(_sample_record(kit, ref).get("sample_batch_id"))
        if (ref.kind, field) == ("collection", "batches"):
            return _batch_ids(_collection_record(kit, ref))
        if (ref.kind, field) == ("collection", "collection"):
            return _named_collection(kit, ref.parent, ref.name)
        if field == "matches":
            return _match_inputs(kit, ref)
    except (NotFound, AccessDenied):
        return None
    raise ValueError(f"No way to read {field!r} of a {ref.kind}")


def _drift(kit: "Toolkit", plan: Plan) -> list[dict[str, Any]]:
    _fresh(kit)
    diff = []
    for change in plan.changes:
        current = jsonable(_current(kit, change))
        if current != jsonable(change.before):
            diff.append(
                {
                    "ref": change.ref.to_dict(),
                    "field": change.field,
                    "planned_before": jsonable(change.before),
                    "now": current,
                }
            )
    return diff


def _response(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict):
        return {}
    return {key: jsonable(answer[key]) for key in _RESPONSE_KEYS if key in answer}


def _results(step: dict[str, Any], answer: Any) -> list[Ref]:
    results = [Ref.from_dict(ref) for ref in step.get("results", ())]
    if step["op"] == "collections.create" and isinstance(answer, dict):
        made = answer.get("target_collection_id")
        if made:
            results.append(
                Ref(
                    id=str(made),
                    kind="collection",
                    name=str(
                        answer.get("target_collection_name") or step["args"]["name"]
                    ),
                    parent=answer.get("workspace_id"),
                )
            )
    return results


def _execute(kit: "Toolkit", plan: Plan) -> Receipt:
    store = kit.session.plans
    try:
        diff = _drift(kit, plan)
    except Exception:
        # Nothing was written: let the same approval try again
        store.release(plan.id)
        raise
    if diff:
        store.set_state(plan.id, "drifted", diff=diff)
        raise PlanDrifted(plan.id, diff)
    service = str(kit.client._service_name)
    applied: list[int] = []
    results: list[Ref] = []
    responses: list[dict[str, Any]] = []
    for index, step in enumerate(plan.steps):
        try:
            with kit._errors(f"apply plan {plan.id}: {step['op']}"):
                answer = WRITES[step["op"]](kit.client, **step["args"])
        except Exception as e:
            failure = {"step": index, "op": step["op"], "changes": step["changes"]}
            failure.update(
                e.to_dict()
                if isinstance(e, ToolkitError)
                else {"error": type(e).__name__, "message": str(e)[:500]}
            )
            receipt = Receipt(
                plan_id=plan.id,
                applied_at=now(),
                results=_distinct(results),
                service_name=service,
                applied=tuple(applied),
                complete=False,
                failure=failure,
                responses=tuple(responses),
            )
            store.set_state(plan.id, "failed", receipt=receipt.to_dict())
            raise PartiallyApplied(receipt) from e
        applied += step["changes"]
        results += _results(step, answer)
        responses.append(_response(answer))
    receipt = Receipt(
        plan_id=plan.id,
        applied_at=now(),
        results=_distinct(results),
        service_name=service,
        applied=tuple(applied),
        responses=tuple(responses),
    )
    store.set_state(plan.id, "applied", receipt=receipt.to_dict())
    return receipt


def _outcome(kit: "Toolkit", plan: Plan, wait: float) -> Receipt:
    """The outcome of the apply that claimed *plan* with this approval:
    its receipt, or its error again; waited for while it is applying."""
    store = kit.session.plans
    deadline = time.monotonic() + wait
    while True:
        state = store.state(plan.id)
        if state["state"] != "applying" or time.monotonic() >= deadline:
            break
        time.sleep(_POLL)
    if state["state"] == "applied":
        return Receipt.from_dict(state["receipt"]).again()
    if state["state"] == "failed":
        raise PartiallyApplied(Receipt.from_dict(state["receipt"]))
    if state["state"] == "drifted":
        raise PlanDrifted(plan.id, state.get("diff", ()))
    if state["state"] == "pending":
        raise Unavailable(
            f"The earlier apply of plan {plan.id} stopped before writing "
            "anything; apply it again."
        )
    raise Unavailable(
        f"Plan {plan.id} is still being applied by another call with this "
        "approval; ask again for its receipt in a moment."
    )


def apply(
    kit: "Toolkit", plan_id: "str | Plan", confirm_token: str | None, wait: float
) -> Receipt:
    if isinstance(plan_id, Plan):
        plan_id = plan_id.id
    if not isinstance(plan_id, str):
        raise InvalidRequest(f"plan_id is a plan's ID, not {plan_id!r}")
    store = kit.session.plans
    try:
        plan = store.load(plan_id)
    except ValueError:
        plan = None
    if plan is None:
        raise NotFound(
            plan_id, "plan", "A plan lives in the session that made it; plan it again."
        )
    if plan.gate == "none":
        raise InvalidRequest(
            f"Plan {plan.id} is a preview: it writes nothing, so there is "
            "nothing to apply. Plan with save=True to save."
        )
    if not isinstance(confirm_token, str) or not confirm_token:
        raise ApprovalRefused(plan.id, "it was given no confirm token.")
    if not kit.approver.verify(plan, confirm_token):
        raise ApprovalRefused(
            plan.id,
            "the confirm token is not an approval of this plan as it stands: "
            "it is another plan's, the plan changed since, or it was not "
            "minted by the transport.",
        )
    fingerprint = token_fingerprint(confirm_token)
    holder = store.claimed_by(plan.id)
    if holder is None:
        if plan.expired():
            raise PlanExpired(plan.id, plan.expires_at.isoformat(timespec="seconds"))
        if store.claim(plan.id, confirm_token):
            return _execute(kit, plan)
        holder = store.claimed_by(plan.id)
    if holder != fingerprint:
        raise PlanConsumed(plan.id, store.state(plan.id)["state"])
    return _outcome(kit, plan, wait)
