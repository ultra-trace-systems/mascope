"""
Canonical, system-owned ionization chemistries shipped with Mascope.

Every deployment names its chemistries itself, so the same reagent is spelled a
dozen ways across a fleet and nothing can refer to "nitrate" as such. These
rows give each chemistry one identity, stable across servers: ``system_key`` is
the identity, ``ionization_mode_id`` is fixed so the rows read alike wherever
they are inspected, and a mode's mechanisms are what make it that chemistry.

A seeded mode carries no filename token and no target collections. Without a
token nothing routes to it by name; without collections it calibrates and
matches nothing. The collections are the deployment's own data and are the one
part of a seeded mode it may set.

The mechanisms ship too (:func:`shipped_mechanisms`): every one a shipped mode
declares and every secondary channel an assignment profile can open, so the
chemistry a run searches under is the same on every server, a fresh one
included, rather than whatever an operator happened to create. A mechanism's
identity across servers is its standard adduct notation, which is unique and
has one spelling. Its id is the server's own: fixed where the seed creates the
row, and kept where the server already holds the mechanism, because the ion,
assignment and verification tables reference it and nothing that crosses
servers needs it equal.

Adding, renaming or removing an entry here is safe at any time:
:mod:`mascope_backend.db.admin.ionization.ensure_system_modes` reads this list
at every start and creates what is missing, so a new chemistry arrives with
the release that adds it and needs no migration. A renamed entry leaves the
old row behind under its old key, to be retired deliberately rather than by a
rewrite of history.

Kept secret-free and import-light, like ``roles``: the mechanism list is read
off the assignment profiles the first time it is asked for, not on import.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from mascope_tools.composition.mechanism_notation import MechanismParts


# (system_key, ionization_mode_id, name, polarity, mechanism names).
#
# The names end in their polarity because deployments use the bare reagent name
# - "Nitrate", "Bromide", "Ambient", "Uronium" are all taken on real servers -
# and a mode name has to stay free for them to use. A name is not unique in the
# database, but ``create_ionization_mode`` refuses a duplicate and reads the row
# with ``scalar_one_or_none``, so two rows sharing one turn a later create into
# a 500. Seeding skips a name already in use rather than adding a second row.
SYSTEM_MODES: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    (
        "nitrate",
        "sysNitrate",
        "Nitrate, negative",
        "-",
        ("[M+NO3]-", "[M-H]-"),
    ),
    (
        "nitrate-15n",
        "sysNitrate15N",
        "Nitrate 15N, negative",
        "-",
        ("[M+^NO3]-", "[M-H]-"),
    ),
    (
        "bromide",
        "sysBromide",
        "Bromide, negative",
        "-",
        ("[M+Br]-", "[M-H]-"),
    ),
    (
        "iodide",
        "sysIodide",
        "Iodide, negative",
        "-",
        ("[M+I]-", "[M-H]-"),
    ),
    (
        "deprotonation",
        "sysDeprotonate",
        "Deprotonation, negative",
        "-",
        ("[M-H]-",),
    ),
    (
        "ambient-negative",
        "sysAmbientNeg",
        "Ambient, negative",
        "-",
        ("[M]-.",),
    ),
    (
        "uronium",
        "sysUronium",
        "Uronium, positive",
        "+",
        ("[M+CH4N2O+H]+", "[M+H]+"),
    ),
    (
        "ammonium",
        "sysAmmonium",
        "Ammonium, positive",
        "+",
        ("[M+NH4]+", "[M+H]+"),
    ),
    (
        "ammonium-15n",
        "sysAmmonium15N",
        "Ammonium 15N, positive",
        "+",
        ("[M+^NH4]+", "[M+H]+"),
    ),
    (
        "protonation",
        "sysProtonate",
        "Protonation, positive",
        "+",
        ("[M+H]+",),
    ),
    (
        "ambient-positive",
        "sysAmbientPos",
        "Ambient, positive",
        "+",
        ("[M]+.",),
    ),
)

# What a deployment may change on a seeded mode: its target collections, which
# are its own data. Everything else is the chemistry's definition.
SYSTEM_MODE_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"calibration_collection_id", "diagnostic_collection_id"}
)


@dataclass(frozen=True)
class ShippedMechanism:
    """An ionization mechanism Mascope ships.

    :param notation: The mechanism in the standard adduct notation, the ion's
        charge last: ``[M+Br]-`` adds a bromide, ``[M-H]-`` removes a proton and
        leaves an anion, ``[M-H]+`` removes a hydride and leaves a cation, and
        ``[M]+.`` / ``[M]-.`` are electron transfer. Its identity on every
        server; a row stored in the legacy spelling (``+Br-``) is the same
        mechanism.
    :param polarity: The polarity of the ion it makes, read from the notation.
    :param mechanism_id: The id the seed creates the row under on a server that
        holds no row of the mechanism.
    """

    notation: str
    polarity: str
    mechanism_id: str


@lru_cache(maxsize=1)
def shipped_mechanisms() -> tuple[ShippedMechanism, ...]:
    """Every ionization mechanism Mascope ships, ordered by notation.

    The mechanisms the shipped modes declare, and every secondary channel an
    assignment profile can open, read off the profiles' own channel tables
    rather than restated here so that the list cannot drift from them. A run
    searches a channel only where the server holds its mechanism, so a channel
    added to a profile is a mechanism every server creates at its next start.

    :return: The mechanisms, each once.
    :rtype: tuple[ShippedMechanism, ...]
    """
    # Imported here: the composition package pulls in the finder's
    # dependencies, which nothing that only reads the modes needs.
    from mascope_tools.composition.mechanism_notation import (
        mechanism_key,
        parse_mechanism,
    )
    from mascope_tools.composition.profiles import REAGENT_PROFILES
    from mascope_tools.composition.reagents import SECONDARY_CHANNELS

    notations = {
        mechanism_key(mechanism)
        for *_, mechanisms in SYSTEM_MODES
        for mechanism in mechanisms
    }
    notations |= {
        mechanism_key(notation)
        for profile in REAGENT_PROFILES.values()
        for notation in profile.secondary_adducts
    }
    notations |= {
        mechanism_key(channel.notation)
        for channels in SECONDARY_CHANNELS.values()
        for channel in channels
    }
    shipped = []
    for notation in sorted(notations):
        parts = parse_mechanism(notation)
        shipped.append(
            ShippedMechanism(
                notation=notation,
                polarity=parts.polarity,
                mechanism_id=_fixed_mechanism_id(parts),
            )
        )
    return tuple(shipped)


def _fixed_mechanism_id(parts: "MechanismParts") -> str:
    """The id the seed creates a mechanism under.

    ``sys``, whether the moiety is added or lost, the moiety, and the ion's
    polarity: ``[M+Br]-`` is ``sysAddBrNeg``, ``[M-H]+`` is ``sysLossHPos``,
    and electron transfer is an electron, ``E``, so ``[M]+.`` is
    ``sysLossEPos``. A labelled atom's caret reads ``Lab``. Letters and digits
    only, like a generated id, because an id travels in URL paths and query
    strings.

    :param parts: The mechanism, read.
    :return: The id, at most the column's 16 characters for every shipped
        mechanism (pinned by the catalogue's tests).
    """
    moiety = "E" if parts.electron_transfer else "".join(parts.terms)
    moiety = re.sub(r"[^A-Za-z0-9]", "", moiety.replace("^", "Lab"))
    operation = "Add" if parts.addition else "Loss"
    polarity = "Pos" if parts.charge > 0 else "Neg"
    return f"sys{operation}{moiety}{polarity}"


@lru_cache(maxsize=1)
def _shipped_by_notation() -> dict[str, ShippedMechanism]:
    return {mechanism.notation: mechanism for mechanism in shipped_mechanisms()}


def is_shipped_mechanism(notation: str, polarity: str) -> bool:
    """Whether a stored mechanism row is one Mascope ships.

    Its notation, in either spelling, is on the list, and it is stored under
    the polarity that notation makes. A row under the other polarity is a
    broken copy of the mechanism rather than the mechanism: no mode can hold it
    and no run can search it, so it is the deployment's to delete, after which
    the next start creates the mechanism as shipped.

    :param notation: The row's mechanism, in either notation.
    :param polarity: The polarity the row is stored under.
    :return: Whether the row is a shipped mechanism.
    :rtype: bool
    """
    from mascope_tools.composition.mechanism_notation import mechanism_key

    shipped = _shipped_by_notation().get(mechanism_key(notation))
    return shipped is not None and shipped.polarity == polarity
