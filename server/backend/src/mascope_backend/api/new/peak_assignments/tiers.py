"""The confidence-tier vocabulary of peak assignment, declared once.

A tier records how strong the evidence behind a peak's formula is - a richer
replacement for the targeted matcher's ``match_category`` 0/1/2. "Evidence" is
meant literally: it is ``fit x chemical plausibility``, the quantity both engine
stages already arbitrate a contested peak in, and the tier is read off that
product rather than off the fit alone (see ``engine.tier_for_evidence``).

- ``assigned`` - the evidence clears the run's upper band; the formula is the
  one the ledger commits to for that peak.
- ``candidate`` - a plausible formula whose evidence leaves real alternatives
  open.
- ``below_assignability`` - a formula was considered and nothing reaches even
  the candidate band, so the peak is not assignable on this evidence.
- ``unassigned`` - nothing was proposed for the peak at all.

The top tier is ``assigned`` and not ``identified`` because identification is a
stronger claim than accurate mass and an isotope pattern can support: in mass
spectrometry an identification is read as MS2 or reference-standard evidence,
while what this engine does is assign a molecular formula from a fit. Naming the
tier after the weaker evidence keeps the ledger's strongest word honest.

The bands themselves are run configuration rather than constants (see
``PeakAssignmentConfig`` and the ``tier_bands`` a run records), so a tier is only
comparable across runs together with the thresholds that produced it.

Kept deliberately dependency-light - no numpy, pandas or database imports - so
the engine, the API schemas and the import path can all share one declaration
without importing each other.
"""

from typing import Any


TIER_ASSIGNED = "assigned"
TIER_CANDIDATE = "candidate"
TIER_BELOW_ASSIGNABILITY = "below_assignability"
TIER_UNASSIGNED = "unassigned"

#: Every tier, most confident first.
TIERS = (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    TIER_BELOW_ASSIGNABILITY,
    TIER_UNASSIGNED,
)

#: Tier -> comparable rank, ASCENDING with confidence: a higher rank is a
#: stronger claim, so ``max`` over a set of tiers picks the most confident one.
#: That is the opposite direction from the position a tier holds in ``TIERS``
#: (and from the frontend's presentation rank, which is that position), so read
#: this map rather than an index when comparing evidence.
TIER_RANK = {
    TIER_UNASSIGNED: 0,
    TIER_BELOW_ASSIGNABILITY: 1,
    TIER_CANDIDATE: 2,
    TIER_ASSIGNED: 3,
}

#: Tier spellings this server still accepts on the wire, mapped onto the
#: vocabulary it stores. ``identified`` was the top tier's name before it was
#: narrowed to ``assigned``, and dropping it would break readers that never see
#: this rename: an external engine publishes ledgers built against the older
#: spec, an SDK client filters the ledger with the tier its own documentation
#: taught it, and previously exported ledgers are re-imported as they were
#: written. The stored rows are migrated, but payloads already in flight are
#: not, so the alias is applied on the way in and nothing legacy is ever
#: written. A later rename adds an entry here rather than a second mechanism.
LEGACY_TIER_ALIASES = {"identified": TIER_ASSIGNED}


def normalize_tier(value: Any) -> Any:
    """Map a legacy tier spelling onto the stored vocabulary.

    Anything that is not a legacy string is returned untouched, including
    non-strings and ``None``: this runs ahead of validation, so a wrongly typed
    tier has to reach the validator to be reported as the type error it is
    rather than be swallowed here.

    :param value: The tier as supplied by a client.
    :return: The current spelling, or the value unchanged.
    """
    if isinstance(value, str):
        return LEGACY_TIER_ALIASES.get(value, value)
    return value


def normalize_tier_bands(bands: dict | None) -> dict | None:
    """Rename legacy tier KEYS in a ``tier_bands`` mapping.

    The bands are keyed by tier, so a mapping written before the rename names
    its upper band with the old spelling. Used on bands read back from the
    database, where a row predating the data migration - or an import that was
    already in flight when it ran - still carries the old key.

    :param bands: A tier-keyed mapping of evidence thresholds, or None.
    :return: A new mapping under the current spellings, or None.
    """
    if bands is None:
        return None
    normalized: dict = {}
    for key, value in bands.items():
        canonical = LEGACY_TIER_ALIASES.get(key, key)
        # Both spellings in one mapping are the same band named twice, not two
        # bands, so the current one wins whichever order they arrive in.
        if key in LEGACY_TIER_ALIASES and canonical in normalized:
            continue
        normalized[canonical] = value
    return normalized
