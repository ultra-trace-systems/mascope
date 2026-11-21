"""The chart kinds: what each answers, what it takes, what it draws.

A chart module registers each kind it defines (:func:`register`), and
:mod:`mascope_viz.charts` imports the chart modules, so that a kind is
known once the package is. The command line's ``list`` and ``describe``
read the registry (:func:`all`, :func:`get`), and so does any surface that
offers the charts to a person or a model: a kind says what it answers, its
parameters with their types, defaults and choices, the series it draws and
the fields of its summary, as plain data (:meth:`ChartKind.to_dict`).

The registry is empty until the chart modules land (the tier-1 charts,
``docs/dev/visual_digest.md`` section 3).
"""

import builtins
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .spec import Figure, _plain


#: A chart kind's id, as a figure's id takes it.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class Param:
    """A parameter a chart kind takes.

    :ivar name: Its name, as the chart's ``build`` takes it.
    :ivar type: What its value is, as the command line parses it
        (``text``, ``int``, ``number``, ``bool``, ``duration``, ``time``,
        ``keys``, ...).
    :ivar default: Its value when not given, a plain JSON value.
    :ivar choices: The values it may take, when they are few; empty for
        any of its type.
    :ivar help: What it does, in a line.
    """

    name: str
    type: str
    default: Any = None
    choices: tuple[Any, ...] = ()
    help: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.isidentifier():
            raise ValueError(f"param name={self.name!r} is not a Python name")
        if not isinstance(self.type, str) or not self.type:
            raise ValueError(f"param {self.name}: type={self.type!r} is not text")
        if not isinstance(self.help, str):
            raise ValueError(f"param {self.name}: help is not text")
        object.__setattr__(self, "choices", tuple(self.choices))
        _plain(self.default, f"param {self.name} default")
        _plain(list(self.choices), f"param {self.name} choices")
        if (
            self.choices
            and self.default is not None
            and self.default not in self.choices
        ):
            raise ValueError(
                f"param {self.name}: default={self.default!r} is none of its choices"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "default": _plain(self.default, self.name),
            "choices": _plain(list(self.choices), self.name),
            "help": self.help,
        }


@dataclass(frozen=True)
class ChartKind:
    """One kind of chart.

    :ivar id: Its id, which the figures it builds carry.
    :ivar question: What it answers, in a line.
    :ivar tier: Its tier in the catalogue (1 is the first).
    :ivar series: The series it draws, in a line (``each key of keys, and
        the reagent sum as one series``).
    :ivar params: Its parameters, in the order it takes them.
    :ivar summary: The fields of the summary its figures carry.
    :ivar build: ``(store, **params) -> Figure``.
    """

    id: str
    question: str
    tier: int
    series: str
    params: tuple[Param, ...] = ()
    summary: tuple[str, ...] = ()
    build: Callable[..., Figure] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID.match(self.id):
            raise ValueError(
                f"id={self.id!r} is not a name of letters, digits, '_', '.' and '-'"
            )
        for name in ("question", "series"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"chart {self.id}: {name} is not text")
        if isinstance(self.tier, bool) or not isinstance(self.tier, int):
            raise ValueError(f"chart {self.id}: tier={self.tier!r} is not a number")
        if self.tier < 1:
            raise ValueError(f"chart {self.id}: tier={self.tier!r} is not at least 1")
        params = tuple(self.params)
        if not builtins.all(isinstance(p, Param) for p in params):
            raise ValueError(f"chart {self.id}: params holds something not a Param")
        names = [p.name for p in params]
        if len(set(names)) != len(names):
            raise ValueError(f"chart {self.id}: a parameter is named twice")
        object.__setattr__(self, "params", params)
        summary = tuple(self.summary)
        if not builtins.all(isinstance(s, str) for s in summary):
            raise ValueError(f"chart {self.id}: summary is not a list of field names")
        object.__setattr__(self, "summary", summary)
        if self.build is not None and not callable(self.build):
            raise ValueError(f"chart {self.id}: build is not callable")

    def to_dict(self) -> dict[str, Any]:
        """The kind as plain JSON values, without its ``build``."""
        return {
            "id": self.id,
            "question": self.question,
            "tier": self.tier,
            "series": self.series,
            "params": [param.to_dict() for param in self.params],
            "summary": list(self.summary),
        }


_KINDS: dict[str, ChartKind] = {}


def register(kind: ChartKind) -> ChartKind:
    """Add *kind* to the registry; it returns it, so a module can keep it.

    :raises ValueError: A kind of that id is registered already.
    """
    if not isinstance(kind, ChartKind):
        raise TypeError(f"{kind!r} is not a ChartKind")
    if kind.id in _KINDS:
        raise ValueError(f"a chart kind {kind.id!r} is registered already")
    _KINDS[kind.id] = kind
    return kind


def get(id: str) -> ChartKind:
    """The kind of *id*.

    :raises KeyError: No kind of that id is registered; the message names
        those that are.
    """
    try:
        return _KINDS[id]
    except KeyError:
        known = ", ".join(sorted(_KINDS)) or "none yet"
        raise KeyError(f"no chart kind {id!r}; the kinds are {known}") from None


def all() -> list[ChartKind]:
    """Every registered kind, by tier, then by id."""
    return sorted(_KINDS.values(), key=lambda kind: (kind.tier, kind.id))


def ids() -> Sequence[str]:
    """Every registered kind's id, in :func:`all`'s order."""
    return [kind.id for kind in all()]
