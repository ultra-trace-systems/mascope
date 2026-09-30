"""The bound on the formulas a known source contributes to a sample's known set.

Stage A of peak assignment matches known formulas - a reference list, a public
database mirror - against every peak of a sample, so what each source brings in
has to be bounded by what the source is and by the chemistry the sample was
drawn from. :class:`KnownWindow` is that bound: the elements a formula may carry,
its largest carbon count and its largest monoisotopic mass, any of which can be
left unbounded. A reference source records one on its row, and a chemistry
context (:mod:`mascope_tools.composition.profiles`) sets a ceiling over all of
them; what Stage A applies to a source is the two intersected.
"""

import re
from dataclasses import dataclass

from mascope_tools.composition.utils import assert_valid_formula, parse_composition


_SYMBOL = re.compile(r"[A-Z][a-z]?")


def is_element(symbol: str) -> bool:
    """Whether a string is one element's symbol, as a formula would spell it."""
    if not _SYMBOL.fullmatch(symbol):
        return False
    try:
        assert_valid_formula(symbol)
    except ValueError:
        return False
    return dict(parse_composition(symbol)) == {symbol: 1}


@dataclass(frozen=True)
class KnownWindow:
    """The formulas a source may contribute: elements, carbon count, mass.

    :param elements: The elements a formula may carry, or None for any.
    :param max_carbon: The largest carbon count, or None for no cap.
    :param max_mass: The largest monoisotopic mass in Da, or None for no cap.
    :raises ValueError: For a symbol that is not an element, or a cap no formula
        could meet.
    """

    elements: frozenset[str] | None = None
    max_carbon: int | None = None
    max_mass: float | None = None

    def __post_init__(self) -> None:
        if self.elements is not None:
            object.__setattr__(self, "elements", frozenset(self.elements))
            unknown = sorted(
                symbol for symbol in self.elements if not is_element(symbol)
            )
            if unknown:
                raise ValueError(f"not element symbols: {', '.join(unknown)}")
        if self.max_carbon is not None and self.max_carbon < 0:
            raise ValueError(f"a carbon cap cannot be negative: {self.max_carbon}")
        if self.max_mass is not None and self.max_mass <= 0:
            raise ValueError(f"a mass cap must be positive: {self.max_mass}")

    @property
    def is_unbounded(self) -> bool:
        """Whether the window bounds nothing at all."""
        return (
            self.elements is None and self.max_carbon is None and self.max_mass is None
        )

    def intersect(self, other: "KnownWindow | None") -> "KnownWindow":
        """The window both bound, axis by axis; None bounds nothing.

        :param other: A second window, or None for none.
        :return: The narrower of the two on every axis.
        """
        if other is None:
            return self
        if self.elements is None or other.elements is None:
            elements = self.elements if other.elements is None else other.elements
        else:
            elements = self.elements & other.elements
        return KnownWindow(
            elements=elements,
            max_carbon=_lower(self.max_carbon, other.max_carbon),
            max_mass=_lower(self.max_mass, other.max_mass),
        )

    def admits(self, formula: str, mass: float | None) -> bool:
        """Whether a neutral formula falls inside the window.

        A mass cap drops a formula whose mass is not known, and a formula that
        cannot be read is outside any window that bounds its elements or carbon.

        :param formula: The neutral formula.
        :param mass: Its monoisotopic mass, or None where it was not computed.
        :return: Whether the window admits it.
        """
        if self.max_mass is not None and (mass is None or mass > self.max_mass):
            return False
        if self.elements is None and self.max_carbon is None:
            return True
        try:
            composition = parse_composition(formula)
        except Exception:  # noqa: BLE001 - an unreadable formula is simply outside
            return False
        present = {symbol for symbol, count in composition.items() if count > 0}
        if self.elements is not None and not present <= self.elements:
            return False
        if self.max_carbon is not None and composition.get("C", 0) > self.max_carbon:
            return False
        return True

    def to_json(self) -> dict:
        """The window as a JSON object, every axis written and empty ones null."""
        return {
            "elements": None if self.elements is None else sorted(self.elements),
            "max_carbon": self.max_carbon,
            "max_mass": self.max_mass,
        }

    @classmethod
    def from_json(cls, data: dict) -> "KnownWindow":
        """Read a window written by :meth:`to_json`.

        :param data: The stored object.
        :raises ValueError: For a value that is not a window.
        :return: The window.
        """
        if not isinstance(data, dict):
            raise ValueError(f"a stored window is an object, not {data!r}")
        elements = data.get("elements")
        max_carbon = data.get("max_carbon")
        max_mass = data.get("max_mass")
        return cls(
            elements=None if elements is None else frozenset(elements),
            max_carbon=None if max_carbon is None else int(max_carbon),
            max_mass=None if max_mass is None else float(max_mass),
        )

    def describe(self) -> str:
        """One line for a person: ``C, H, N, O, S; C <= 40; <= 700 Da``."""
        if self.is_unbounded:
            return "unbounded"
        parts = [
            "any element"
            if self.elements is None
            else ", ".join(_hill_sorted(self.elements)),
            "any carbon count"
            if self.max_carbon is None
            else f"C <= {self.max_carbon}",
            "any mass" if self.max_mass is None else f"<= {self.max_mass:g} Da",
        ]
        return "; ".join(parts)


def _lower(first, second):
    """The smaller of two caps, where None is no cap."""
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)


def _hill_sorted(elements) -> list[str]:
    """Carbon and hydrogen first, the rest alphabetically, as a formula reads."""
    head = [symbol for symbol in ("C", "H") if symbol in elements]
    return head + sorted(symbol for symbol in elements if symbol not in ("C", "H"))
