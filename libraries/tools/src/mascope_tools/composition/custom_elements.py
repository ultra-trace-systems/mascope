"""Single source of truth for labelled custom elements ('^X' notation).

A labelled reagent atom (e.g. the ``15N`` in the 15N-nitrate reagent ``[M+^NO3]-``)
is modelled as a custom element ``^X`` whose isotope distribution is the
*labelled* distribution rather than natural abundance. This module is the one
place that knows about these elements; both ``mascope_tools`` (mass computation,
isotope prediction) and the Mascope backend (target-ion generation, cheminfo
formula conversion) consume it.

Historically this knowledge lived in the ``mascope_molmass`` fork's element
table. Consolidating it here is what allows that fork to be retired: nothing
depends on molmass's custom ``^N`` element any more.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class CustomElement:
    """A labelled custom element referenced in formulas as ``^<symbol>``."""

    symbol: str
    """Caret notation, e.g. ``"^N"``."""

    base_element: str
    """Underlying element symbol, e.g. ``"N"``."""

    isotopes: tuple[tuple[float, int], ...]
    """``(monoisotopic mass, mass number)`` per isotope, lightest first."""

    default_purity: float
    """Heaviest-isotope fraction of the labelled reagent (e.g. 0.98 for 98% 15N)."""

    @property
    def labelled_massnumber(self) -> int:
        """Mass number of the labelled (heaviest) isotope, e.g. ``15`` for ``^N``."""
        return self.isotopes[-1][1]

    @property
    def pyteomics_isotope(self) -> str:
        """Element-first isotope token for the label, e.g. ``"N[15]"``.

        pyteomics can mass this token but cannot mass the bare ``^N`` symbol.
        """
        return f"{self.base_element}[{self.labelled_massnumber}]"


# Monoisotopic masses are the CODATA/NIST values (identical to those in the
# retired mascope_molmass element table), so downstream m/z is unchanged.
CUSTOM_ELEMENTS: dict[str, CustomElement] = {
    "^N": CustomElement(
        symbol="^N",
        base_element="N",
        isotopes=((14.00307400443, 14), (15.00010889888, 15)),
        default_purity=0.98,
    ),
}


def is_custom_element(symbol: str) -> bool:
    """Return True if ``symbol`` (e.g. ``"^N"``) is a known labelled custom element."""
    return symbol in CUSTOM_ELEMENTS


#: The purity a labelled element is read at, where a caller has set one.
_LABEL_PURITY: ContextVar[float | None] = ContextVar("label_purity", default=None)


def label_purity(symbol: str) -> float:
    """The isotopic purity a labelled element is read at.

    An ionization setup carries the purity of its labelled reagent, and a run
    reads its sample's setup inside :func:`labelled_purity`; there this answers
    the setup's value. Elsewhere it is the element's ``default_purity``. A setup
    names one labelled reagent, so one value serves every labelled element in
    the formulas it searches.

    :param symbol: A labelled element, ``"^N"``.
    :return: Its heaviest isotope's fraction.
    """
    configured = _LABEL_PURITY.get()
    return CUSTOM_ELEMENTS[symbol].default_purity if configured is None else configured


@contextmanager
def labelled_purity(purity: float | None) -> Iterator[None]:
    """Read labelled elements at ``purity`` inside the block.

    Context-local, so concurrent runs each read their own setup's, and carried
    into ``asyncio.to_thread`` and the tasks the block starts. ``None`` keeps
    each element's default.

    :param purity: The labelled reagent's heaviest-isotope fraction, or None.
    """
    token = _LABEL_PURITY.set(purity)
    try:
        yield
    finally:
        _LABEL_PURITY.reset(token)
