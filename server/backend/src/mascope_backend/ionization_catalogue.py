"""
Canonical, system-owned ionization chemistries shipped with Mascope.

Every deployment names its chemistries itself, so the same reagent is spelled a
dozen ways across a fleet and nothing can refer to "nitrate" as such. These
rows give each chemistry one identity, stable across servers: ``system_key`` is
the identity, ``ionization_mode_id`` is fixed so the rows read alike wherever
they are inspected, and a mode's mechanisms are what make it that chemistry.

A seeded mode carries no filename token, so nothing routes to it by name. Its
target collections ship where its chemistry has a reagent of its own
(:func:`seeded_collections`): a calibrant collection, the reagent ion and its
own clusters, which a file of the chemistry is calibrated on, and a diagnostic
collection, every ion the reagent makes on its own, which its files are
matched against to watch the source. Both are read off the reagent-ion library
(``mascope_tools.composition.reagents``). Which collections a seeded mode
points at is still the deployment's to set, the one part of it that is: a mode
pointed at a collection of the deployment's keeps it, and a seeded collection
the deployment edits is left as it was edited.

The reagent's ions are the first of the tiers the chemistry-profile proposal
named for a profile's anchors, and the only one built. The further tiers - the
ions an environment's every spectrum carries in its background, and a fit on
the sample's own assignments (self-consistency) - stay out until the reagent
ions prove insufficient for a chemistry. Where they fall short is known
already. Protonation, deprotonation and the two ammonium chemistries have no
reagent in the library and ship without collections. An ambient mode's reagent
is an Orbitrap's EASY-IC beam, whose one ion cannot fit a TOF axis, which
needs three points. And a file whose scan window holds none of its chemistry's
anchors is not calibrated on them.

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

Kept secret-free and import-light, like ``roles``: the mechanism list and the
collections are read off the assignment profiles and the reagent-ion library
the first time they are asked for, not on import.
"""

import hashlib
import json
import re
from collections.abc import Iterable
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

# What a deployment may change on a seeded mode: which target collections it
# points at, the seeded ones or its own, and its labelled reagent's isotopic
# purity, which is the bottle's - one site's 15N nitrate is 98% and another's
# 99%. Everything else is the chemistry's definition.
#
# `instrument` is deliberately not here. The scope filters which modes a file
# name's tokens are matched against, and a seeded mode ships with no token and
# cannot be given one - so scoping one could not change anything, and a
# permission for behaviour that does not exist is worse than none. It belongs
# here on the day a seeded mode can carry a token, with a test.
SYSTEM_MODE_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"calibration_collection_id", "diagnostic_collection_id", "label_purity"}
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


# --- the seeded collections -----------------------------------------------------

#: What a seeded calibrant collection says it is, in the collection's own
#: description, where the person who opens it reads it.
_CALIBRANTS_DESCRIPTION = (
    "Shipped by Mascope: the reagent ion and its own clusters, the m/z anchors "
    "files of this chemistry are calibrated on. Each compound is named for the "
    "ion it stands for. Mascope brings it up to date with each release until it "
    "is edited here; to calibrate on a collection of your own, point the "
    "ionization mode at that one instead."
)
_DIAGNOSTICS_DESCRIPTION = (
    "Shipped by Mascope: the ions this chemistry's reagent makes on its own, "
    "matched in every file of the chemistry to watch the source. Each compound "
    "is named for the ion it stands for. Mascope brings it up to date with each "
    "release until it is edited here."
)

#: The digests of what earlier releases seeded a collection with
#: (:attr:`SeededCollection.digest`). A collection that still holds one is
#: brought to what this release seeds; one that holds neither that nor the
#: current digest was edited by the deployment and is left as it is. So a
#: change to what a collection seeds - its name, its description, or its
#: compounds, which follow the reagent-ion library - adds the digest the
#: collection had before it here. The catalogue's tests pin the current digest
#: of every collection, so such a change fails them until it does.
SEEDED_BEFORE: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SeededCompound:
    """A compound of a seeded collection: one ion of the reagent-ion library,
    written as the neutral its mode's mechanism turns into that ion.

    A collection holds compounds, and a compound becomes ions through every
    mechanism of the mode a file is processed under. So an ion of the library
    is seeded as the neutral one of its mode's mechanisms makes it of: the ion
    less the reagent the mechanism adds, ``"()"`` where the ion is the reagent
    itself.

    :param name: The library's label of the ion it is there for (``"[Br2]-"``),
        which is how the collection says what each compound stands for.
    :param formula: The neutral (``"Br"`` for ``[Br2]-`` under ``[M+Br]-``).
    """

    name: str
    formula: str


@dataclass(frozen=True)
class SeededCollection:
    """A target collection Mascope ships for a seeded chemistry.

    :param collection_id: Its fixed id: the mode's own and a suffix, so the
        rows read alike on every server, and so that the seed knows its own
        collection from one the deployment made.
    :param system_key: The chemistry it belongs to.
    :param mode_field: The column of the mode that points at it,
        ``calibration_collection_id`` or ``diagnostic_collection_id``.
    :param name: Its name.
    :param description: Its description.
    :param collection_type: ``CALIBRANTS`` or ``DIAGNOSTICS``.
    :param compounds: Its compounds.
    """

    collection_id: str
    system_key: str
    mode_field: str
    name: str
    description: str
    collection_type: str
    compounds: tuple[SeededCompound, ...]

    @property
    def digest(self) -> str:
        """What the collection holds as the seed writes it, as a short hash."""
        return collection_digest(
            name=self.name,
            description=self.description,
            collection_type=self.collection_type,
            workspace_id=None,
            compounds=(
                (compound.name, compound.formula) for compound in self.compounds
            ),
        )


def collection_digest(
    *,
    name: str | None,
    description: str | None,
    collection_type: str | None,
    workspace_id: str | None,
    compounds: Iterable[tuple[str | None, str]],
) -> str:
    """A collection's content, as a short hash: how the seed tells its own
    collection, as it wrote it, from one the deployment has edited.

    Every field a person can change through the collection editor is in it:
    the name, the description, the type, the workspace it is scoped to, and
    each compound by name and formula, which is also how a compound edited in
    place reads as an edit of every collection holding it. The compounds are
    compared as the library identifies one when a compound is created - by
    name and formula, case and runs of spaces aside - so a seed that reuses a
    compound the library already held does not read as an edit.

    :param name: The collection's name.
    :param description: Its description.
    :param collection_type: Its type.
    :param workspace_id: The workspace it is scoped to, None when global.
    :param compounds: ``(name, formula)`` of each compound it holds.
    :return: Sixteen hex digits of the content's SHA-256.
    :rtype: str
    """

    def fold(text: str | None) -> str:
        return " ".join((text or "").lower().split())

    content = {
        "name": name,
        "description": description,
        "type": collection_type,
        "workspace_id": workspace_id,
        "compounds": sorted([fold(name), fold(formula)] for name, formula in compounds),
    }
    encoded = json.dumps(content, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


@lru_cache(maxsize=1)
def seeded_collections() -> tuple[SeededCollection, ...]:
    """The calibrant and diagnostic collection of every seeded chemistry that
    has a reagent of its own, read off the reagent-ion library.

    The calibrants are the reagent's own cluster series: the bare reagent ion
    and its clusters with itself (``[Br]-``, ``[Br2]-``, ``[Br3]-``;
    ``[NO3]-`` and the nitrate core with one and two acids). The series spans
    the low end of the range, so it gives a TOF the three anchors spread over
    the range its axis needs, and an Orbitrap the one it needs wherever its
    window starts. The diagnostics are every line of the reagent: the series,
    its hydrates, its oxides and what else the source makes of it alone. The
    air's ions are in neither, being the discharge's rather than the reagent's.

    The chemistry is read off the mode's mechanisms as an Orbitrap's file
    would be (``detect_reagent_profile``). The reagents read the same on any
    instrument, but an ambient mode names a reagent only on an Orbitrap, where
    it is the EASY-IC beam, whose one ion is the lock mass. On a TOF the same
    mode has no reagent, and that one ion cannot fit a TOF axis, so a TOF file
    under it is not calibrated, as it was not before.

    :return: Two collections per seeded chemistry with a reagent, calibrants
        first, in the order of :data:`SYSTEM_MODES`.
    :rtype: tuple[SeededCollection, ...]
    :raises ValueError: An ion of a library cannot be written through its
        mode's mechanisms (pinned against by the catalogue's tests).
    """
    from mascope_tools.composition.profiles import detect_reagent_profile
    from mascope_tools.composition.reagents import (
        KIND_CLUSTER,
        REAGENT_LINE_FAMILIES,
        reagent_library,
    )

    seeded = []
    for system_key, mode_id, mode_name, polarity, mechanisms in SYSTEM_MODES:
        profile = detect_reagent_profile(mechanisms, polarity, instrument_type="orbi")
        lines = [
            ion
            for ion in reagent_library(profile.name)
            if ion.family in REAGENT_LINE_FAMILIES
        ]
        if not lines:
            continue
        anchors = [ion for ion in lines if ion.kind == KIND_CLUSTER]
        for mode_field, suffix, name, collection_type, description, ions in (
            (
                "calibration_collection_id",
                "Cal",
                f"{mode_name} calibrants",
                "CALIBRANTS",
                _CALIBRANTS_DESCRIPTION,
                anchors,
            ),
            (
                "diagnostic_collection_id",
                "Dia",
                f"{mode_name} reagent ions",
                "DIAGNOSTICS",
                _DIAGNOSTICS_DESCRIPTION,
                lines,
            ),
        ):
            seeded.append(
                SeededCollection(
                    collection_id=f"{mode_id}{suffix}",
                    system_key=system_key,
                    mode_field=mode_field,
                    name=name,
                    description=description,
                    collection_type=collection_type,
                    compounds=tuple(
                        SeededCompound(
                            name=ion.label,
                            formula=_neutral(ion.formula, ion.charge, mechanisms),
                        )
                        for ion in ions
                    ),
                )
            )
    return tuple(seeded)


def seeded_collection_ids() -> frozenset[str]:
    """The ids of every collection the catalogue seeds.

    :return: The ids.
    :rtype: frozenset[str]
    """
    return frozenset(collection.collection_id for collection in seeded_collections())


def _neutral(formula: str, charge: int, mechanisms: tuple[str, ...]) -> str:
    """The neutral one of a mode's mechanisms turns into an ion.

    The mechanisms are tried in the mode's order, which puts the reagent's own
    adduct first, so a reagent's cluster is written as what is clustered on
    the reagent ion: ``[Br3]-`` is ``Br2`` under ``[M+Br]-``, and ``[Br]-``
    itself is ``"()"``, nothing at all under it. The inverse of the target ion
    generator (``target_ions_compute``), which applies neither a removal nor
    an electron transfer to ``"()"``, so neither may yield it here.

    :param formula: The ion's composition, charge excluded.
    :param charge: The ion's charge.
    :param mechanisms: The mode's mechanisms, in the standard notation.
    :return: The neutral in Hill order, or ``"()"``.
    :raises ValueError: No mechanism of the mode makes the ion of anything.
    """
    from mascope_tools.composition.mechanism_notation import parse_mechanism
    from mascope_tools.composition.utils import parse_composition, to_hill_notation

    ion = dict(parse_composition(formula))
    for notation in mechanisms:
        parts = parse_mechanism(notation)
        if parts.charge != charge:
            continue
        neutral = dict(ion)
        if not parts.electron_transfer:
            # Adding a moiety makes the ion, so the neutral is the ion less
            # it; removing one makes it, so the neutral is the ion with it.
            sign = -1 if parts.addition else 1
            for element, count in parse_composition(parts.moiety).items():
                neutral[element] = neutral.get(element, 0) + sign * count
        if any(count < 0 for count in neutral.values()):
            continue
        neutral = {element: count for element, count in neutral.items() if count}
        if neutral:
            return to_hill_notation(neutral)
        if parts.addition and not parts.electron_transfer:
            return "()"
    raise ValueError(
        f"No mechanism of {', '.join(mechanisms)} makes the ion {formula} "
        f"(charge {charge:+d}) of a neutral."
    )
