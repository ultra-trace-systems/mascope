"""Reference lists as self-describing JSON files: the format Mascope ships.

A reference list is one published compilation - a thesis appendix, a
contaminant catalogue, the species a family of papers reports - held as one
file. What belongs to the whole list sits in its header: what it is, which
version, its licence, where it comes from, how the source detected its species,
which chemistry it applies to. The species sit beneath it as neutral formulas.
One file is one reference source, which is what lets a directory of them load
with no flags (:mod:`mascope_reference.seed`).

Schema 2 is the format of the lists Mascope ships, and :func:`list_problems` is
the bar a shipped list has to clear. Schema 1, the reference engine's own
format, still reads, so its files load unchanged; it carries no licence and no
radical allowance.

Radical status is read off the formula, never off a flag. A neutral whose DBE
is a half-integer has an unpaired electron - the test the tiering's
odd-electron rule applies - and a list may hold such species only when its
header says ``allow_radicals``. Anywhere else they are held back at ingest
(:func:`admitted_species`), which is what keeps radicals out of a default load.
A species may still say ``radical``, as a claim :func:`list_problems` checks
against its formula.
"""

import json
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from mascope_reference.normalize import canonical_formula
from mascope_reference.schema import (
    LICENSE_LENGTH,
    SOURCE_NAME_LENGTH,
    SOURCE_VERSION_LENGTH,
)
from mascope_tools.composition.heuristic_filter import (
    effective_counts,
    element_counts,
    neutral_is_closed_shell,
)


#: The schema Mascope ships, and the one :func:`list_problems` holds a list to.
SCHEMA_VERSION = 2
#: Every schema the reader accepts.
READABLE_SCHEMA_VERSIONS = frozenset({1, SCHEMA_VERSION})

#: How a species was identified, strongest first: against an authentic
#: standard, by its fragmentation, or by its formula alone.
EVIDENCE_GRADES = ("standard", "ms2", "formula")

#: The polarity a list's source measured in.
POLARITIES = frozenset({"positive", "negative", "both"})

_LIST_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "label",
        "data_version",
        "license",
        "references",
        "polarity",
        "native_detection",
        "applies_to_contexts",
        "always_active",
        "allow_radicals",
        "load_by_default",
        "provenance",
        "species",
    }
)
_SPECIES_KEYS = frozenset(
    {"formula", "name", "reference", "evidence", "conditions", "radical", "note"}
)
_REFERENCE_KEYS = frozenset({"citation", "doi", "isbn", "note"})

# A list's id becomes its source name: lower-case words joined by hyphens, so it
# is safe as a file name and reads the same in `mascope reference status`.
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DOI = re.compile(r"10\.\d{4,9}/\S+")


class PeakListError(ValueError):
    """A file that cannot be read as a reference list at all."""


@dataclass(frozen=True)
class Species:
    """One entry of a list: a neutral formula and what the list says of it."""

    formula: str
    name: str | None = None
    #: A DOI of the species' own, for a list that compiles several papers.
    reference: str | None = None
    #: One of :data:`EVIDENCE_GRADES`.
    evidence: str | None = None
    conditions: tuple[str, ...] = ()
    #: The author's claim, checked against the formula - never what decides.
    radical: bool | None = None
    note: str | None = None
    #: Keys schema 2 does not define, kept so a check can name them.
    unknown_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PeakList:
    """One reference list file, read."""

    path: Path
    schema_version: int
    id: str
    label: str | None
    data_version: str | None
    license: str | None
    references: tuple[dict, ...]
    species: tuple[Species, ...]
    polarity: str | None = None
    native_detection: str | None = None
    applies_to_contexts: tuple[str, ...] = ()
    always_active: bool = False
    allow_radicals: bool = False
    load_by_default: bool = True
    provenance: dict = field(default_factory=dict)
    #: Keys schema 2 does not define, kept so a check can name them.
    unknown_keys: tuple[str, ...] = ()


def _text(value) -> str | None:
    """A non-empty string with its surrounding space removed, else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _strings(value) -> tuple[str, ...]:
    """The non-empty strings of a JSON array; anything else reads as none."""
    if not isinstance(value, list):
        return ()
    return tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _species(entry, where: str) -> Species:
    """One species entry, or a :class:`PeakListError` naming where it is."""
    if not isinstance(entry, dict) or _text(entry.get("formula")) is None:
        raise PeakListError(f"{where}: a species needs a formula")
    radical = entry.get("radical")
    return Species(
        formula=_text(entry["formula"]),
        name=_text(entry.get("name")),
        reference=_text(entry.get("reference")),
        evidence=_text(entry.get("evidence")),
        conditions=_strings(entry.get("conditions")),
        radical=radical if isinstance(radical, bool) else None,
        note=_text(entry.get("note")),
        unknown_keys=tuple(sorted(set(entry) - _SPECIES_KEYS)),
    )


def read_peak_list(path: Path) -> PeakList:
    """Read one list file.

    Reading is lenient about what a list says and strict about whether it is a
    list: a missing licence or an unknown key reads fine and is
    :func:`list_problems`' to report, while a file with no id or no species is
    not a list at all.

    :param path: A schema 1 or schema 2 list file.
    :raises PeakListError: When the file is not JSON, not a JSON object, names a
        schema this reader does not know, has no id or no species array, or holds
        a species with no formula.
    :return: The list.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PeakListError(f"{path.name}: not valid JSON ({error})") from None
    if not isinstance(data, dict):
        raise PeakListError(f"{path.name}: a list file holds one JSON object")
    version = data.get("schema_version")
    if isinstance(version, bool) or version not in READABLE_SCHEMA_VERSIONS:
        known = ", ".join(str(v) for v in sorted(READABLE_SCHEMA_VERSIONS))
        raise PeakListError(
            f"{path.name}: schema_version {version!r} is not one this reader "
            f"knows ({known})"
        )
    list_id = _text(data.get("id"))
    if list_id is None:
        raise PeakListError(f"{path.name}: the list has no id")
    entries = data.get("species")
    if not isinstance(entries, list):
        raise PeakListError(f"{path.name}: the list has no species array")
    references = data.get("references")
    provenance = data.get("provenance")
    return PeakList(
        path=path,
        schema_version=version,
        id=list_id,
        label=_text(data.get("label")),
        data_version=_text(data.get("data_version")),
        license=_text(data.get("license")),
        references=(
            tuple(ref for ref in references if isinstance(ref, dict))
            if isinstance(references, list)
            else ()
        ),
        species=tuple(
            _species(entry, f"{path.name}, species {number}")
            for number, entry in enumerate(entries, start=1)
        ),
        polarity=_text(data.get("polarity")),
        native_detection=_text(data.get("native_detection")),
        applies_to_contexts=_strings(data.get("applies_to_contexts")),
        always_active=data.get("always_active") is True,
        allow_radicals=data.get("allow_radicals") is True,
        load_by_default=data.get("load_by_default", True) is not False,
        provenance=provenance if isinstance(provenance, dict) else {},
        unknown_keys=tuple(sorted(set(data) - _LIST_KEYS)),
    )


def is_odd_electron(formula: str) -> bool:
    """Whether a neutral formula has an unpaired electron: a half-integer DBE.

    False for a formula that cannot be read, which :func:`list_problems`
    reports separately.
    """
    return not neutral_is_closed_shell(formula)


def dbe(formula: str) -> float | None:
    """Double-bond equivalents of a neutral formula, or None when unreadable.

    Silicon counts with carbon and the halogens with hydrogen, the convention
    every chemistry rule of the engine reads a formula by.
    """
    counts = element_counts(formula)
    if counts is None:
        return None
    return effective_counts(counts)[2]


def admitted_species(peak_list: PeakList) -> Iterator[Species]:
    """The species a load takes: all of them, less the radicals of a list that
    does not allow them."""
    for species in peak_list.species:
        if peak_list.allow_radicals or not is_odd_electron(species.formula):
            yield species


def _isbn_ok(value: str) -> bool:
    """Whether a string is an ISBN-10 or ISBN-13 with a valid check digit."""
    digits = re.sub(r"[\s-]", "", value).upper()
    if re.fullmatch(r"\d{13}", digits):
        total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits))
        return total % 10 == 0
    if re.fullmatch(r"\d{9}[\dX]", digits):
        total = sum(
            (10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(digits)
        )
        return total % 11 == 0
    return False


def _reference_problems(reference: dict, where: str) -> list[str]:
    """What keeps one list-level reference from citing its source."""
    problems = []
    unknown = sorted(set(reference) - _REFERENCE_KEYS)
    if unknown:
        problems.append(f"{where}: unknown key(s) {', '.join(unknown)}")
    if _text(reference.get("citation")) is None:
        problems.append(f"{where}: no citation")
    doi = _text(reference.get("doi"))
    isbn = _text(reference.get("isbn"))
    if doi is None and isbn is None:
        problems.append(f"{where}: neither a DOI nor an ISBN")
    if doi is not None and not _DOI.fullmatch(doi):
        problems.append(f"{where}: '{doi}' is not a DOI")
    if isbn is not None and not _isbn_ok(isbn):
        problems.append(f"{where}: '{isbn}' is not a valid ISBN")
    return problems


def list_problems(
    peak_list: PeakList, *, licenses: Collection[str] | None = None
) -> list[str]:
    """Everything that keeps a list from shipping, one sentence each.

    Holds a list to schema 2. The header needs an id that names the file and
    fits a source name, a label, a version, a licence - one of ``licenses`` when
    given, the tags the Stage A licence gate knows - and at least one reference,
    each with a DOI or a valid ISBN. Every species needs a neutral molecular
    formula with a non-negative DBE, odd-electron only in a list that allows
    radicals, and agreeing with any radical claim of its own; and each identity
    is listed once - a formula may repeat only as isomers under different names.

    :param peak_list: The list, as :func:`read_peak_list` returns it.
    :param licenses: The licence tags a list may carry; None skips that check.
    :return: The problems in file order; empty when the list may ship.
    """
    problems: list[str] = []
    if peak_list.schema_version != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {peak_list.schema_version}; a shipped list is "
            f"schema {SCHEMA_VERSION}"
        )
    if not _ID.fullmatch(peak_list.id) or len(peak_list.id) > SOURCE_NAME_LENGTH:
        problems.append(
            f"id '{peak_list.id}' is not lower-case words joined by hyphens, "
            f"within {SOURCE_NAME_LENGTH} characters"
        )
    if peak_list.path.stem != peak_list.id:
        problems.append(
            f"the file is named '{peak_list.path.name}' but its id is '{peak_list.id}'"
        )
    if peak_list.unknown_keys:
        problems.append(f"unknown key(s) {', '.join(peak_list.unknown_keys)}")
    if peak_list.label is None:
        problems.append("no label")
    if peak_list.data_version is None:
        problems.append("no data_version")
    elif len(peak_list.data_version) > SOURCE_VERSION_LENGTH:
        problems.append(
            f"data_version is longer than {SOURCE_VERSION_LENGTH} characters"
        )
    if peak_list.license is None:
        problems.append("no licence")
    elif len(peak_list.license) > LICENSE_LENGTH or (
        licenses is not None and peak_list.license not in licenses
    ):
        problems.append(
            f"licence '{peak_list.license}' is not a tag the licence gate knows"
        )
    if not peak_list.references:
        problems.append("no reference")
    for number, reference in enumerate(peak_list.references, start=1):
        problems.extend(_reference_problems(reference, f"reference {number}"))
    if peak_list.polarity is not None and peak_list.polarity not in POLARITIES:
        problems.append(
            f"polarity '{peak_list.polarity}' is not one of "
            f"{', '.join(sorted(POLARITIES))}"
        )
    if not peak_list.species:
        problems.append("no species")

    # Identity is one-to-many on a formula, so isomers may share one; what
    # repeats by mistake is the same formula under the same name, or nameless.
    first_seen: dict[tuple[str, str | None], int] = {}
    for number, species in enumerate(peak_list.species, start=1):
        where = f"species {number} ({species.formula})"
        if species.unknown_keys:
            problems.append(
                f"{where}: unknown key(s) {', '.join(species.unknown_keys)}"
            )
        if species.evidence is not None and species.evidence not in EVIDENCE_GRADES:
            problems.append(
                f"{where}: evidence '{species.evidence}' is not one of "
                f"{', '.join(EVIDENCE_GRADES)}"
            )
        if species.reference is not None and not _DOI.fullmatch(species.reference):
            problems.append(
                f"{where}: its reference '{species.reference}' is not a DOI"
            )
        canonical = canonical_formula(species.formula)
        if canonical is None:
            problems.append(f"{where}: not a neutral molecular formula")
            continue
        value = dbe(canonical)
        if value is not None and value < 0:
            problems.append(
                f"{where}: its DBE is {value:g}, which no molecule has - an ion "
                "or a salt"
            )
        odd = is_odd_electron(canonical)
        if species.radical is not None and species.radical != odd:
            claimed = "a radical" if species.radical else "closed-shell"
            actual = "odd-electron" if odd else "even-electron"
            problems.append(f"{where}: marked {claimed}, but the formula is {actual}")
        if odd and not peak_list.allow_radicals:
            problems.append(
                f"{where}: an odd-electron formula in a list that does not allow "
                "radicals"
            )
        identity = (canonical, species.name)
        if identity in first_seen:
            same = "formula" if species.name is None else "formula and name"
            problems.append(
                f"{where}: the same {same} as species {first_seen[identity]}"
            )
        else:
            first_seen[identity] = number
    return problems
