"""Name matching and name-to-ID resolution utilities for the Mascope SDK.

All name matching in the SDK follows a single contract, implemented by
:func:`_name_mask`: a plain string is a case-insensitive **literal substring**
(regex metacharacters carry no special meaning), and a compiled ``re.Pattern``
is used as a regex as-is (case-sensitivity comes from its flags).
"""

import re
import warnings

import pandas as pd


def _name_mask(
    series: "pd.Series", pattern: "str | re.Pattern", *, exact: bool
) -> "pd.Series":
    """Boolean mask selecting rows whose name matches ``pattern``.

    A plain string is matched case-insensitively as a **literal substring**:
    regex metacharacters carry no special meaning, so ``"Sample (A)"`` matches
    the batch literally named ``"Sample (A)"`` rather than being interpreted as
    a regex group. Set ``exact`` to require the whole name to equal the string.

    To match with a regular expression, pass a **compiled pattern**
    (``re.compile(...)``); it is used as-is, so control case-sensitivity through
    its flags (e.g. ``re.compile("2025|2026", re.IGNORECASE)``). ``exact`` does
    not apply to compiled patterns -- anchor them instead (``r"^name$"``).

    :param series: Series of names to match against.
    :param pattern: Literal substring (``str``) or compiled regex.
    :param exact: Require a full-string match instead of a substring match.
                  Only valid for string patterns.
    :return: Boolean mask aligned with ``series``.
    :raises ValueError: If ``exact`` is combined with a compiled pattern.
    """
    if isinstance(pattern, re.Pattern):
        if exact:
            raise ValueError(
                "exact=True cannot be combined with a compiled regex pattern; "
                "anchor the pattern instead, e.g. re.compile(r'^name$')."
            )
        return series.str.contains(pattern, na=False)
    if exact:
        return series.str.casefold() == pattern.casefold()
    return series.str.contains(re.escape(pattern), case=False, na=False)


def _pattern_text(pattern: "str | re.Pattern") -> str:
    """Human-readable form of a name pattern for error messages."""
    return pattern.pattern if isinstance(pattern, re.Pattern) else pattern


def _match_names(series: "pd.Series", pattern: "str | re.Pattern") -> "pd.Series":
    """Mask of names matching ``pattern`` under the unified contract.

    Delegates to :func:`_name_mask` (plain string = literal substring, compiled
    pattern = regex). Deprecation shim: name resolution historically treated
    plain strings as raw regexes, so if the literal match finds nothing and the
    string works as a regex that does match something, that match is returned
    with a ``DeprecationWarning``. Pass ``re.compile(...)`` to match by regex
    without the warning.
    """
    mask = _name_mask(series, pattern, exact=False)
    if not isinstance(pattern, str) or mask.any() or re.escape(pattern) == pattern:
        return mask
    try:
        legacy = series.str.contains(pattern, case=False, na=False, regex=True)
    except re.error:
        return mask
    if legacy.any():
        warnings.warn(
            f"The string {pattern!r} only matches when treated as a regular "
            "expression. Plain strings are now matched as literal substrings; "
            "pass a compiled pattern (re.compile(...)) to match by regex. "
            "This fallback will be removed in a future release.",
            DeprecationWarning,
            stacklevel=3,
        )
        return legacy
    return mask


def resolve_id(
    value: "str | re.Pattern",
    items: pd.DataFrame | None,
    id_column: str,
    name_column: str,
    entity_label: str,
) -> str:
    """Resolve a name or substring to a unique ID.

    Checks for an exact ID match first (string values only), then falls back to
    case-insensitive matching on the name column. A plain string matches as a
    **literal substring** (regex metacharacters carry no special meaning); pass
    a compiled ``re.Pattern`` to match with a regular expression
    (e.g. ``re.compile("2026-01|2026-02")``), with case-sensitivity controlled
    by its flags.

    Raises if zero or multiple matches.

    :param value: The name or substring to resolve (or an exact ID), or a
                  compiled regex pattern.
    :type value: str | re.Pattern
    :param items: DataFrame of available items to match against.
    :type items: pd.DataFrame | None
    :param id_column: Column name containing the IDs.
    :type id_column: str
    :param name_column: Column name containing the names.
    :type name_column: str
    :param entity_label: Human-readable label for error messages (e.g. "dataset").
    :type entity_label: str
    :return: The resolved ID.
    :rtype: str
    :raises ValueError: If no items exist, or no match / multiple matches found.
    """
    if items is None or items.empty:
        raise ValueError(f"No {entity_label}s found.")

    # Exact ID match
    if isinstance(value, str) and value in items[id_column].values:
        return value

    # Substring (or compiled-regex) match on name
    matches = items[_match_names(items[name_column], value)]
    if len(matches) == 0:
        available = items[name_column].tolist()
        raise ValueError(
            f"No {entity_label} matching '{_pattern_text(value)}'. "
            f"Available {entity_label}s: {available}"
        )
    if len(matches) > 1:
        matched = matches[name_column].tolist()
        raise ValueError(
            f"Multiple {entity_label}s matching '{_pattern_text(value)}': {matched}. "
            f"Please be more specific."
        )
    return matches.iloc[0][id_column]
