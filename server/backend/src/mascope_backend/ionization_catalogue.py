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

Adding, renaming or removing an entry here is safe at any time:
:mod:`mascope_backend.db.admin.ionization.ensure_system_modes` reads this list
at every start and creates what is missing, so a new chemistry arrives with
the release that adds it and needs no migration. A renamed entry leaves the
old row behind under its old key, to be retired deliberately rather than by a
rewrite of history.

Kept secret-free and import-light, like ``roles``.
"""

# Mechanism name -> the polarity of the ion it yields. The name is the
# standard adduct notation, the ion's charge last: "[M+Br]-" adds a bromide,
# "[M-H]-" removes a proton and leaves an anion, "[M-H]+" removes a hydride and
# leaves a cation, and "[M]+." / "[M]-." are electron transfer. A row stored in
# the legacy spelling ("+Br-") reads as the same mechanism.
MECHANISM_POLARITIES: dict[str, str] = {
    "[M]+.": "+",
    "[M]-.": "-",
    "[M+H]+": "+",
    "[M-H]-": "-",
    "[M-H]+": "+",
    "[M+NO3]-": "-",
    "[M+^NO3]-": "-",
    "[M+Br]-": "-",
    "[M+I]-": "-",
    "[M+NH4]+": "+",
    "[M+^NH4]+": "+",
    "[M+CH4N2O+H]+": "+",
}

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
