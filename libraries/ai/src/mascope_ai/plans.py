"""Writes as plans: a plan is built, a person approves it, it is applied once.

A write tool never mutates. It returns a :class:`Plan`: what would change,
before and after, what it touches, whether it can be undone, the role it
needs and when it expires. :meth:`~mascope_ai.toolkit.Toolkit.apply` is the
one function that writes, and only with a confirm token for that very plan.

The token is minted by the transport, never by the model: the transport
shows the plan to a person (Claude Code's permission prompt, an approval
card in the app) and, when they approve it, asks its :class:`Approver` for
one. The token is an HMAC of the plan's ID, its digest and a nonce, under a
key the approver keeps in memory: it names one plan in one version, and no
string a model could write passes for it.

Pending plans are kept in the session directory (:class:`PlanStore`)::

    <session>/plans/<plan id>.json         the plan
    <session>/plans/<plan id>.claim        the approval that consumed it
    <session>/plans/<plan id>.state.json   applying, applied, failed, drifted

The claim file is created exclusively, so of two overlapping applies only
one claims the plan, in this process or another one sharing the session.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .frame import jsonable
from .refs import Ref


#: How long a plan stays appliable: minutes, not hours. What it recorded as
#: ``before`` is checked again when it is applied, but a plan approved long
#: after it was shown is a plan nobody looked at for a while.
PLAN_TTL = timedelta(minutes=10)

#: How hard the transport's gate presses: ``none`` for a plan that writes
#: nothing (a matching preview), ``confirm`` for a change that can be undone,
#: ``hard`` for one that cannot (a delete, a save that rebuilds matches) or
#: that writes what every workspace shares.
Gate = Literal["none", "confirm", "hard"]

#: The states of a plan in its store.
STATES = ("pending", "applying", "applied", "failed", "drifted")

_PLAN_ID_HEX = 12


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_plan_id() -> str:
    return f"p_{secrets.token_hex(_PLAN_ID_HEX // 2)}"


def _canonical(value: Any) -> str:
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Change:
    """One value a plan changes.

    :ivar ref: The object it belongs to.
    :ivar field: What of it changes: ``workspace``, ``batch``, ``batches``,
        ``collection``, ``matches``.
    :ivar before: Its value when the plan was made. ``apply`` reads it again
        and stops, with a diff, if it no longer holds.
    :ivar after: Its value once applied.
    :ivar note: The change in words, for the person approving it. Names in
        it are quoted: they are the users' text, never instructions.
    """

    ref: Ref
    field: str
    before: Any
    after: Any
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "field": self.field,
            "before": jsonable(self.before),
            "after": jsonable(self.after),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Change":
        return cls(
            ref=Ref.from_dict(data["ref"]),
            field=data["field"],
            before=data["before"],
            after=data["after"],
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class Move:
    """One move for :meth:`~mascope_ai.toolkit.Toolkit.plan_organize`.

    :ivar item: What moves: a dataset or a sample, as its Ref, or its name
        or ID with *kind*.
    :ivar to: Where: a workspace for a dataset, a batch for a sample; a Ref,
        a name or an ID.
    :ivar kind: The kind *item* names when it is a string.
    """

    item: "Ref | str"
    to: "Ref | str"
    kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        def side(value: Any) -> Any:
            return value.to_dict() if isinstance(value, Ref) else value

        return {"item": side(self.item), "to": side(self.to), "kind": self.kind}


@dataclass(frozen=True)
class Plan:
    """A write, described and not yet made.

    :ivar id: The plan's ID, ``p_`` and twelve hex digits.
    :ivar kind: ``organize``, ``collection``, ``associate`` or ``match``.
    :ivar summary: One line on what it does.
    :ivar changes: Every value it changes, before and after.
    :ivar affected: Every object it touches.
    :ivar reversible: Whether the change can be undone from the API.
    :ivar estimated_cost: What it costs the server, when that is more than
        a request or two (a matching run over a batch's samples).
    :ivar expires_at: After this it cannot be applied (:data:`PLAN_TTL`).
    :ivar required_role: The role it needs, and where. The builder checked
        what it can see of it; a refusal would have come at planning.
    :ivar gate: How hard the transport's approval presses (:data:`Gate`).
    :ivar warnings: What the person approving it should know besides.
    :ivar steps: The writes ``apply`` makes, in order: each an SDK write
        method, its arguments, and the changes it makes.
    :ivar preview: What a plan that writes nothing found (a matching
        preview's counts); None otherwise.
    :ivar created_at: When it was made.
    """

    id: str
    kind: str
    summary: str
    changes: tuple[Change, ...]
    affected: tuple[Ref, ...]
    reversible: bool
    estimated_cost: str | None
    expires_at: datetime
    required_role: str
    gate: Gate = "confirm"
    warnings: tuple[str, ...] = ()
    steps: tuple[dict[str, Any], ...] = ()
    preview: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=now)

    @property
    def digest(self) -> str:
        """SHA-256 of everything the plan says and does: an approval names
        it, so a plan edited after it was approved is not that plan."""
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()

    def expired(self, at: datetime | None = None) -> bool:
        return (at or now()) >= self.expires_at

    def __repr__(self) -> str:
        return (
            f"Plan({self.id} {self.kind}: {self.summary}; "
            f"{len(self.changes)} change(s), gate={self.gate}, "
            f"reversible={self.reversible}, expires {self.expires_at:%H:%M:%S}Z)"
        )

    def render(self) -> str:
        """The plan as a person approving it reads it: plain lines, every
        name quoted."""
        lines = [f"Plan {self.id}: {self.summary}"]
        for change in self.changes:
            lines.append(
                f"  - {change.note}"
                if change.note
                else f"  - {change.ref} {change.field}: "
                f"{change.before!r} -> {change.after!r}"
            )
        lines.append(f"Needs: {self.required_role}")
        lines.append(
            "Can be undone." if self.reversible else "CANNOT BE UNDONE from Mascope."
        )
        if self.estimated_cost:
            lines.append(f"Cost: {self.estimated_cost}")
        lines += [f"Note: {warning}" for warning in self.warnings]
        lines.append(f"Expires {self.expires_at.isoformat(timespec='seconds')}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "changes": [change.to_dict() for change in self.changes],
            "affected": [ref.to_dict() for ref in self.affected],
            "reversible": self.reversible,
            "estimated_cost": self.estimated_cost,
            "expires_at": self.expires_at.isoformat(),
            "required_role": self.required_role,
            "gate": self.gate,
            "warnings": list(self.warnings),
            "steps": jsonable(list(self.steps)),
            "preview": jsonable(self.preview),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        return cls(
            id=data["id"],
            kind=data["kind"],
            summary=data["summary"],
            changes=tuple(Change.from_dict(c) for c in data["changes"]),
            affected=tuple(Ref.from_dict(r) for r in data["affected"]),
            reversible=data["reversible"],
            estimated_cost=data.get("estimated_cost"),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            required_role=data["required_role"],
            gate=data.get("gate", "confirm"),
            warnings=tuple(data.get("warnings", ())),
            steps=tuple(data.get("steps", ())),
            preview=data.get("preview"),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True)
class Receipt:
    """What ``apply`` did.

    :ivar plan_id: The plan applied.
    :ivar applied_at: When its writes ended.
    :ivar results: The objects the writes made or changed.
    :ivar service_name: The service every write was sent as, which the
        server's access log records beside each request.
    :ivar applied: The indexes, into the plan's ``changes``, of those
        applied. All of them, unless the writes stopped part-way.
    :ivar complete: False when a write failed part-way: :attr:`failure`
        says which and why, and the plan is not applied again.
    :ivar failure: The failed write and its error; None when none failed.
    :ivar responses: What the server answered each write: its message, and
        a background task's ``process_id`` for a write that runs on.
    :ivar repeated: True when this is the receipt of an earlier ``apply``
        with the same approval, returned without writing again.
    """

    plan_id: str
    applied_at: datetime
    results: tuple[Ref, ...]
    service_name: str
    applied: tuple[int, ...] = ()
    complete: bool = True
    failure: dict[str, Any] | None = None
    responses: tuple[dict[str, Any], ...] = ()
    repeated: bool = False

    def __repr__(self) -> str:
        state = "complete" if self.complete else "STOPPED PART-WAY"
        again = ", repeated" if self.repeated else ""
        return (
            f"Receipt({self.plan_id}: {len(self.applied)} change(s) applied, "
            f"{state}, as {self.service_name!r}{again})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "applied_at": self.applied_at.isoformat(),
            "results": [ref.to_dict() for ref in self.results],
            "service_name": self.service_name,
            "applied": list(self.applied),
            "complete": self.complete,
            "failure": jsonable(self.failure),
            "responses": jsonable(list(self.responses)),
            "repeated": self.repeated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Receipt":
        return cls(
            plan_id=data["plan_id"],
            applied_at=datetime.fromisoformat(data["applied_at"]),
            results=tuple(Ref.from_dict(r) for r in data["results"]),
            service_name=data["service_name"],
            applied=tuple(data.get("applied", ())),
            complete=data.get("complete", True),
            failure=data.get("failure"),
            responses=tuple(data.get("responses", ())),
            repeated=data.get("repeated", False),
        )

    def again(self) -> "Receipt":
        """This receipt, marked as returned for a repeated ``apply``."""
        return replace(self, repeated=True)


class Approver:
    """Mints and checks confirm tokens: the transport's, never the model's.

    A transport keeps one and hands the same one to its toolkit
    (``Toolkit(approver=...)``). When a person approves a plan the transport
    showed them, it calls :meth:`approve` and passes the token to
    ``apply``. The key lives in this object's memory alone: nothing in the
    session directory, and nothing a tool returns, carries it.
    """

    def __init__(self, key: bytes | None = None):
        self._key = key if key is not None else secrets.token_bytes(32)

    def __repr__(self) -> str:
        return "Approver()"

    def _mac(self, plan_id: str, digest: str, nonce: str) -> str:
        message = f"{plan_id}\n{digest}\n{nonce}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def approve(self, plan: Plan, *, irreversible: bool = False) -> str:
        """A confirm token for *plan*, as it is now: call it only once a
        person approved it.

        :param irreversible: Must be True for a plan gated ``hard``: the
            transport says so when it has shown the person that the change
            cannot be undone, or writes what every workspace shares, and they
            approved it all the same.
        :raises ValueError: For a plan gated ``none`` (nothing to approve),
            or one gated ``hard`` without *irreversible*.
        """
        if plan.gate == "none":
            raise ValueError(
                f"Plan {plan.id} writes nothing: there is nothing to approve"
            )
        if plan.gate == "hard" and not irreversible:
            raise ValueError(
                f"Plan {plan.id} is gated hard; approve it with irreversible=True "
                "once the person was shown that it cannot be undone"
            )
        nonce = secrets.token_hex(8)
        return f"{nonce}.{self._mac(plan.id, plan.digest, nonce)}"

    def verify(self, plan: Plan, token: str) -> bool:
        """Whether *token* is this approver's approval of *plan*, as it is."""
        nonce, _, mac = token.partition(".")
        if not nonce or not mac:
            return False
        return hmac.compare_digest(mac, self._mac(plan.id, plan.digest, nonce))


def token_fingerprint(token: str) -> str:
    """What the store keeps of a token: its hash, never the token."""
    return hashlib.sha256(token.encode()).hexdigest()


class PlanStore:
    """A session's plans, their claims and their states, as files.

    :ivar path: The directory, ``<session>/plans``.
    """

    def __init__(self, path: Path):
        self.path = path

    def _file(self, plan_id: str, suffix: str) -> Path:
        if not (
            plan_id.startswith("p_")
            and len(plan_id) == 2 + _PLAN_ID_HEX
            and all(c in "0123456789abcdef" for c in plan_id[2:])
        ):
            raise ValueError(f"Not a plan ID: {plan_id!r}")
        return self.path / f"{plan_id}{suffix}"

    def save(self, plan: Plan) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            self._file(plan.id, ".json"), json.dumps(plan.to_dict(), indent=1)
        )

    def load(self, plan_id: str) -> Plan | None:
        """The plan, or None when the session has none by that ID."""
        path = self._file(plan_id, ".json")
        if not path.exists():
            return None
        return Plan.from_dict(json.loads(path.read_text()))

    def plans(self) -> list[Plan]:
        """Every plan of the session, oldest first."""
        if not self.path.exists():
            return []
        found = [
            Plan.from_dict(json.loads(path.read_text()))
            for path in self.path.glob("p_*.json")
            if not path.name.endswith(".state.json")
        ]
        return sorted(found, key=lambda plan: plan.created_at)

    def claim(self, plan_id: str, token: str) -> bool:
        """Claim the plan for one approval: True for the one call that does,
        False for every other, in any process sharing the session.

        The claim file is created exclusively (``O_EXCL``): the
        compare-and-set that moves a plan from pending to applying.
        """
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self._file(plan_id, ".claim"), os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w") as out:
            out.write(token_fingerprint(token))
        self.set_state(plan_id, "applying")
        return True

    def release(self, plan_id: str) -> None:
        """Undo a claim that wrote nothing: the plan is pending again.

        Only the call holding the claim releases it, when reading the
        plan's objects again failed before any write.
        """
        self._file(plan_id, ".state.json").unlink(missing_ok=True)
        self._file(plan_id, ".claim").unlink(missing_ok=True)

    def claimed_by(self, plan_id: str) -> str | None:
        """The fingerprint of the approval that claimed the plan, if any.

        A claim being written by another process may read empty for a
        moment; this waits a little for it.
        """
        path = self._file(plan_id, ".claim")
        for _ in range(50):
            if not path.exists():
                return None
            text = path.read_text().strip()
            if text:
                return text
            time.sleep(0.01)
        return None

    def state(self, plan_id: str) -> dict[str, Any]:
        """``{"state": ..., ...}``: ``pending`` until a claim, then what
        :meth:`set_state` wrote last."""
        path = self._file(plan_id, ".state.json")
        if not path.exists():
            claimed = self._file(plan_id, ".claim").exists()
            return {"state": "applying" if claimed else "pending"}
        return json.loads(path.read_text())

    def set_state(self, plan_id: str, state: str, **extra: Any) -> None:
        if state not in STATES:
            raise ValueError(f"Not a plan state: {state!r}")
        _write_atomic(
            self._file(plan_id, ".state.json"),
            json.dumps(jsonable({"state": state, **extra}), indent=1),
        )


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
