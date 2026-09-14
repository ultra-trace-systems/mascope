"""What a source's row says about how its compounds may be matched.

Stage A of peak assignment matches every active reference formula against a
sample's peaks, so what a source contributes has to be bounded by what the
source is. Each ``reference_source`` row records three things, written when the
source is loaded, and Stage A applies all three
(:func:`mascope_reference.known.iter_known_compositions`):

- **Its window** (:class:`~mascope_tools.composition.known_window.KnownWindow`):
  the elements a formula may carry, and its largest carbon count and
  monoisotopic mass. A field left empty is unbounded on that axis. The sample's
  chemistry context sets a ceiling over it.
- **Whether its radicals may be matched.** Radical status is read from the
  formula, never from a flag, so this is an allowance, not a label.
- **The polarity its compounds are detected in**: ``positive``, ``negative``,
  or empty for both.

A cited list is its own bound: the lists Mascope ships, a list file and a
hand-authored CSV load unbounded, because someone chose every formula in them. A
database mirror is not: PubChem or CompTox hold hundreds of thousands of
formulas no sample of these chemistries carries, so a mirror loads at
:data:`MIRROR_WINDOW`, the window Stage A applied to every source before a source
carried one of its own. Either can be overridden when the source is loaded.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from mascope_tools.composition.known_window import KnownWindow, is_element


#: The polarities a row can record. Empty means both.
POLARITIES = ("positive", "negative")

#: The atmospheric-organics window: the elements a monoterpene-SOA or HOM study
#: works in, a generous carbon cap, and a mass ceiling above the dimers.
MIRROR_ELEMENTS = frozenset({"C", "H", "N", "O", "S"})
MIRROR_MAX_CARBON = 40
MIRROR_MAX_MASS = 700.0

#: What an argument or a flag writes to say "no bound on this axis".
UNBOUNDED_TOKEN = "any"

#: What a flag writes for a source detected in both polarities.
BOTH_POLARITIES = "both"

#: The two spellings of a sample's polarity, and the row's word for each.
SAMPLE_POLARITIES = {"+": "positive", "-": "negative"}


def parse_elements(text: str) -> frozenset[str] | None:
    """Read a comma-separated element list, or the token for any element.

    :param text: ``"C,H,N,O,S,Si"`` or :data:`UNBOUNDED_TOKEN`.
    :raises ValueError: For an empty list or a symbol that is not an element.
    :return: The elements, or None for any.
    """
    if text.strip().lower() == UNBOUNDED_TOKEN:
        return None
    symbols = frozenset(part.strip() for part in text.split(",") if part.strip())
    if not symbols:
        raise ValueError(
            f"name at least one element, or '{UNBOUNDED_TOKEN}' for no bound"
        )
    # Validated here as well as by the window, so the message names the flag's
    # own spelling rather than a set.
    unknown = sorted(symbol for symbol in symbols if not is_element(symbol))
    if unknown:
        raise ValueError(f"not element symbols: {', '.join(unknown)}")
    return symbols


def parse_polarity(text: str) -> str | None:
    """Read a polarity flag: ``positive``, ``negative`` or ``both``.

    :param text: The flag's value, in any case.
    :raises ValueError: For anything else.
    :return: The polarity, or None for both.
    """
    value = text.strip().lower()
    if value == BOTH_POLARITIES:
        return None
    if value not in POLARITIES:
        raise ValueError(
            f"a polarity is {', '.join(POLARITIES)} or '{BOTH_POLARITIES}', not '{text}'"
        )
    return value


def _parse_cap(text: str, kind, axis: str):
    """Read one numeric cap, or the token for none."""
    if text.strip().lower() == UNBOUNDED_TOKEN:
        return None
    try:
        return kind(text)
    except ValueError:
        raise ValueError(
            f"a {axis} cap is a number or '{UNBOUNDED_TOKEN}', not '{text}'"
        ) from None


#: The window a database mirror loads at.
MIRROR_WINDOW = KnownWindow(MIRROR_ELEMENTS, MIRROR_MAX_CARBON, MIRROR_MAX_MASS)
#: The window a cited list loads at.
UNBOUNDED = KnownWindow()


@dataclass(frozen=True)
class SourceScope:
    """What a source row records about how its compounds may be matched.

    :param known_window: The formulas the source may contribute.
    :param allow_radicals: Whether an odd-electron formula of the source may be
        matched.
    :param polarity: ``positive`` or ``negative``, or None for both.
    :raises ValueError: For a polarity the row cannot record.
    """

    known_window: KnownWindow
    allow_radicals: bool = False
    polarity: str | None = None

    def __post_init__(self) -> None:
        if self.polarity is not None and self.polarity not in POLARITIES:
            raise ValueError(
                f"polarity '{self.polarity}' is not one of {', '.join(POLARITIES)}"
            )

    def row_values(self) -> dict:
        """The three columns of a ``reference_source`` row this scope sets."""
        return {
            "known_window": self.known_window.to_json(),
            "allow_radicals": self.allow_radicals,
            "polarity": self.polarity,
        }

    @classmethod
    def from_row(cls, row) -> "SourceScope":
        """Read the scope a ``reference_source`` row records.

        :param row: A row carrying the three columns.
        :raises ValueError: For a window or a polarity the row cannot hold.
        :return: The scope.
        """
        # A row that records no window is bounded as the column's default says,
        # at the mirror window, rather than read as unbounded.
        return cls(
            known_window=(
                MIRROR_WINDOW
                if row.known_window is None
                else KnownWindow.from_json(row.known_window)
            ),
            allow_radicals=bool(row.allow_radicals),
            polarity=row.polarity,
        )

    def overridden(
        self,
        *,
        elements: str | None = None,
        max_carbon: str | None = None,
        max_mass: str | None = None,
        allow_radicals: bool | None = None,
        polarity: str | None = None,
    ) -> "SourceScope":
        """The scope with what a person asked for at load time put over it.

        The window's axes arrive as the text of a flag, where
        :data:`UNBOUNDED_TOKEN` lifts the bound; None leaves an axis as the
        source's own.

        :param elements: Comma-separated element symbols, or the token.
        :param max_carbon: A carbon cap, or the token.
        :param max_mass: A mass cap in Da, or the token.
        :param allow_radicals: Whether radicals may be matched; None keeps the
            source's own.
        :param polarity: ``positive``, ``negative`` or ``both``, as a flag
            writes it; None keeps the source's own.
        :raises ValueError: For a value none of the axes can take.
        :return: The new scope.
        """
        window = self.known_window
        if elements is not None:
            window = replace(window, elements=parse_elements(elements))
        if max_carbon is not None:
            window = replace(window, max_carbon=_parse_cap(max_carbon, int, "carbon"))
        if max_mass is not None:
            window = replace(window, max_mass=_parse_cap(max_mass, float, "mass"))
        return replace(
            self,
            known_window=window,
            allow_radicals=(
                self.allow_radicals if allow_radicals is None else allow_radicals
            ),
            polarity=self.polarity if polarity is None else parse_polarity(polarity),
        )

    def describe(self) -> str:
        """One line for a person, window first."""
        radicals = "radicals allowed" if self.allow_radicals else "no radicals"
        polarity = self.polarity or "both polarities"
        return f"{self.known_window.describe()}; {radicals}; {polarity}"


def scope_of(adapter, path: Path) -> SourceScope:
    """The scope a load of this dump writes, before any override.

    An adapter declares its ``known_window``: the mirror window for a database,
    unbounded for a list. An adapter whose dump says more about itself - a list
    file's header names its allowance and its polarity - reads it with a
    ``scope(path)`` method of its own.

    :param adapter: The source's adapter.
    :param path: The dump being loaded.
    :return: The scope.
    """
    read = getattr(adapter, "scope", None)
    if read is not None:
        return read(path)
    return SourceScope(known_window=adapter.known_window)
