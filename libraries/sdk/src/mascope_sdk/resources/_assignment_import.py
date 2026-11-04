"""The rows of a per-sample run import, checked, and the chunks they go in.

:meth:`~mascope_sdk.resources.peak_assignments.PeakAssignmentsResource.import_run`
sends a DataFrame to ``POST .../runs/import`` in one or more requests. This
module does the part that needs no server: it turns the frame into the rows
the route reads, refuses here what the route would refuse anyway, and cuts
the rows into requests under the route's caps. The contract is the user page
``docs/user/sdk/publishing-assignments.md``; the reasons behind each rule are
``docs/dev/sdk_peak_assignment.md`` sections 8.2 and 8.3.

Nothing here reaches for chemistry: the SDK cannot compute a formula's
plausibility, so the tier check is one-sided, and an adduct is resolved by
its exact notation or not at all.
"""

import json
import math
from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd
from loguru import logger

from ..exceptions import ImportValidationError


#: The columns of an imported row, as the route reads them: a ledger record
#: minus the ids the server mints and the confidence it computes itself.
ROW_COLUMNS = (
    "sample_peak_id",
    "sample_peak_mz",
    "sample_peak_intensity",
    "sample_peak_tof",
    "role",
    "assigned_formula",
    "ion_formula",
    "ionization_mechanism_id",
    "isotope_label",
    "isotope_formula",
    "source",
    "fit_score",
    "mz_error_ppm",
    "abundance_error",
    "tier",
    "engine_tier",
    "target_compound_id",
    "target_ion_id",
    "owner_sample_peak_id",
    "alternatives",
    "provenance",
)

#: The columns a row cannot do without.
REQUIRED_COLUMNS = ("sample_peak_id", "sample_peak_mz", "sample_peak_intensity", "role")

#: Numeric columns: a NaN in one of these cannot be sent, as JSON has none.
FLOAT_COLUMNS = (
    "sample_peak_mz",
    "sample_peak_intensity",
    "sample_peak_tof",
    "fit_score",
    "mz_error_ppm",
    "abundance_error",
)

#: Numeric columns that may not be null: the record and the table both
#: refuse it, and the in-app engine writes 0.0 rather than nothing. Which
#: number stands in for a missing one is the caller's to say.
NON_NULL_COLUMNS = ("sample_peak_mz", "sample_peak_intensity")

#: The column only a frame read with ``peak_assignments.get`` has.
EXPORTED_MARK = "peak_assignment_id"

#: A frame's adduct notations, as ``mascope.ionization.list()`` writes them.
ADDUCT_COLUMN = "adduct"

#: Columns of a frame read with ``peak_assignments.get`` that an import does
#: not send: the server mints the ids and computes or looks up the rest
#: itself. Left out without a word, so an exported ledger imports as it is.
READ_ONLY_COLUMNS = frozenset(
    {
        "peak_assignment_id",
        "peak_assignment_run_id",
        "sample_item_id",
        "owner_peak_assignment_id",
        "batch_peak_id",
        "known_compounds",
        "known_compounds_total",
        "evidence",
        "p_correct",
        "p_correct_provisional",
        "corroboration_adducts",
        "corroboration_channels",
        "candidate_density",
        "mass_z",
        "reference_listing",
    }
)

ROLES = ("M0", "iso_child", "reagent", "artifact", "unassigned")
SOURCES = ("database", "untargeted", "manual", "reagent", "artifact")
TIERS = ("assigned", "candidate", "below_assignability", "unassigned")

#: Tier spellings the server still reads, and the one it reads them as.
LEGACY_TIERS = {"identified": "assigned"}

#: The role a row naming an owner must have, and an owner must not.
OWNED_ROLE = "iso_child"

#: Rows one request may carry until the server says otherwise: the route's
#: documented cap, which every answer repeats as ``max_rows_per_request``.
DEFAULT_MAX_ROWS = 1000

#: Bytes one request body may take. The route refuses a body over 5 MiB with
#: a 413 (and on a deployed server, the proxy in front of it does), so this
#: stays a megabyte under it: rows carrying ``alternatives`` and
#: ``provenance`` run to several KB each, so the row cap alone does not
#: bound a body.
MAX_BODY_BYTES = 4 * 1024 * 1024

#: How many row labels an error names before it says how many more.
_NAMED = 5


def _labels(frame: pd.DataFrame, mask: pd.Series) -> list:
    return list(frame.index[mask.to_numpy()])


def _named(labels: list) -> str:
    shown = ", ".join(repr(label) for label in labels[:_NAMED])
    more = len(labels) - _NAMED
    return f"{shown} and {more} more" if more > 0 else shown


def _refuse(frame: pd.DataFrame, mask: pd.Series, column: str, why: str) -> None:
    """Raise for the rows *mask* picks out, if it picks out any."""
    if not mask.any():
        return
    labels = _labels(frame, mask)
    raise ImportValidationError(
        f"Column '{column}' {why}: row(s) {_named(labels)}.",
        column=column,
        rows=labels,
    )


def normalise_bands(tier_bands: Mapping[str, Any]) -> dict[str, float]:
    """The run's ``tier_bands``, checked, under the current key names.

    :param tier_bands: ``{"assigned": ..., "candidate": ...}`` on the
        evidence scale; ``identified`` is read as ``assigned``.
    :return: The two thresholds as floats.
    :raises ImportValidationError: If a band is missing, not a number in
        [0, 1], or ``candidate`` is above ``assigned``.
    """
    if not isinstance(tier_bands, Mapping):
        raise ImportValidationError(
            "tier_bands must be a mapping of 'assigned' and 'candidate' to "
            "evidence thresholds."
        )
    bands = {LEGACY_TIERS.get(key, key): value for key, value in tier_bands.items()}
    if "assigned" in tier_bands:
        bands["assigned"] = tier_bands["assigned"]
    out: dict[str, float] = {}
    for key in ("assigned", "candidate"):
        value = bands.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= value <= 1.0
        ):
            raise ImportValidationError(
                f"tier_bands['{key}'] must be a number between 0 and 1, not {value!r}."
            )
        out[key] = float(value)
    if out["candidate"] > out["assigned"]:
        raise ImportValidationError(
            f"tier_bands['candidate'] ({out['candidate']}) is above "
            f"tier_bands['assigned'] ({out['assigned']})."
        )
    return out


def _tier_for(score: float, bands: dict[str, float]) -> str:
    """The tier *score* earns under *bands*, as the server bands evidence."""
    if score <= 0:
        return "below_assignability"
    if score >= bands["assigned"]:
        return "assigned"
    if score >= bands["candidate"]:
        return "candidate"
    return "below_assignability"


#: The order of the banded tiers, lowest first.
_RANK = {"below_assignability": 0, "candidate": 1, "assigned": 2}


def _reachable(tier: Any, fit: Any, formula: Any, bands: dict[str, float]) -> bool:
    """Whether a stated tier can be the one the server derives for the row.

    The server reads a tier off the row's evidence, ``fit_score`` times the
    plausibility of ``assigned_formula``, and the SDK cannot compute the
    plausibility. It is never above 1, though, so the evidence is never
    above the fit, and a tier the bare fit does not reach cannot be
    coherent. A row the fit does clear may still be refused by the server,
    whose product has the last word.

    A row with no fit or no formula has no evidence, and is ``unassigned``
    or ``below_assignability``, the two tiers the in-app engine writes
    there: that much is exact.
    """
    if tier is None:
        return True
    if fit is None or not formula:
        return tier in ("unassigned", "below_assignability")
    if tier not in _RANK:
        return False  # 'unassigned' is never the tier of a row with evidence
    # The server rounds the product to four places; the fit rounded alike
    # is the most it can come to.
    ceiling = _tier_for(round(fit, 4), bands)
    return _RANK[tier] <= _RANK[ceiling]


def _mechanism_ids(listing: pd.DataFrame | None) -> dict[str, str]:
    """Notation to mechanism id, for the notations exactly one mechanism has."""
    names = ["ionization_mechanism", "ionization_mechanism_id"]
    if listing is None or not set(names) <= set(listing.columns):
        return {}
    pairs = listing[names].dropna()
    counts = pairs["ionization_mechanism"].value_counts()
    unique = pairs[pairs["ionization_mechanism"].map(counts) == 1]
    return dict(zip(unique["ionization_mechanism"], unique["ionization_mechanism_id"]))


def _resolve_adducts(
    frame: pd.DataFrame, mechanisms: Callable[[], pd.DataFrame | None]
) -> None:
    """Fill ``ionization_mechanism_id`` from the frame's ``adduct`` notations.

    Where a row names no mechanism id and does name an adduct, the id is the
    one mechanism this server lists under exactly that notation, and null
    otherwise: a notation spelled another way, one no mechanism has, or one
    two mechanisms share. The server does not need the id - the adduct
    travels in ``ion_formula`` - so a null loses nothing but the join.
    """
    if ADDUCT_COLUMN not in frame.columns:
        return
    if "ionization_mechanism_id" not in frame.columns:
        frame["ionization_mechanism_id"] = None
    wanted = frame["ionization_mechanism_id"].isna() & frame[ADDUCT_COLUMN].notna()
    if not wanted.any():
        return
    ids = _mechanism_ids(mechanisms())
    resolved = frame.loc[wanted, ADDUCT_COLUMN].map(ids)
    frame["ionization_mechanism_id"] = frame["ionization_mechanism_id"].astype(object)
    frame.loc[wanted, "ionization_mechanism_id"] = resolved.astype(object).where(
        resolved.notna(), None
    )
    missed = sorted(set(frame.loc[wanted, ADDUCT_COLUMN][resolved.isna()]))
    if missed:
        # INFO: nothing is lost, the adduct is in the ion formula
        logger.info(
            "{} of {} row(s) name an adduct this server lists under no single "
            "mechanism, and go without a mechanism id: {}",
            int(resolved.isna().sum()),
            int(wanted.sum()),
            ", ".join(map(str, missed[:_NAMED])),
        )


def _owner_references(frame: pd.DataFrame) -> None:
    """Translate an exported ledger's owner ids into peak references.

    A frame read with ``peak_assignments.get`` links an isotopologue to its
    owner by the owner's ``peak_assignment_id``, which an import cannot
    send: the server mints new ones. The owner's ``sample_peak_id`` names the
    same row within the import, so that is what is sent.

    Only an ``iso_child`` row may name an owner in an import. The in-app
    reagent pass also links a reagent's isotope lines to its monoisotopic
    row, and those rows go without the link, which the import has no way to
    carry.
    """
    if "owner_sample_peak_id" in frame.columns:
        return
    if not {"owner_peak_assignment_id", EXPORTED_MARK} <= set(frame.columns):
        return
    linked = frame["owner_peak_assignment_id"].notna()
    named = linked & (frame["role"] == OWNED_ROLE)
    if (linked & ~named).any():
        # INFO: an in-app reagent pass's links, which an import cannot carry
        logger.info(
            "{} row(s) link to an owner from a role other than '{}', and go "
            "without the link: an import carries owners on '{}' rows only.",
            int((linked & ~named).sum()),
            OWNED_ROLE,
            OWNED_ROLE,
        )
    if not named.any():
        return
    peak_of = dict(zip(frame["peak_assignment_id"], frame["sample_peak_id"]))
    owners = frame["owner_peak_assignment_id"].map(peak_of)
    _refuse(
        frame,
        named & owners.isna(),
        "owner_peak_assignment_id",
        "names an owner that is not a row of the frame",
    )
    frame["owner_sample_peak_id"] = owners.astype(object).where(named, None)


def _exported_tiers(frame: pd.DataFrame) -> None:
    """Send an exported ledger's tiers as its engine's own.

    The server reads each imported row's ``tier`` off its evidence and
    refuses any other, and the run a frame read with ``peak_assignments.get``
    came from need not have: an in-app run settles some rows below what
    their evidence earns, and says why in ``tier_reasons``. So the frame's
    ``tier`` is not sent, and the server derives it again. Where the frame
    states no ``engine_tier`` at all, it is an in-app run's, whose engine
    tier is its ``tier``, and that goes as ``engine_tier``.
    """
    if EXPORTED_MARK not in frame.columns or "tier" not in frame.columns:
        return
    if "engine_tier" not in frame.columns or frame["engine_tier"].isna().all():
        frame["engine_tier"] = frame["tier"]
    del frame["tier"]


def _check_numbers(frame: pd.DataFrame) -> None:
    for column in FLOAT_COLUMNS:
        if column not in frame.columns:
            continue
        try:
            values = pd.to_numeric(frame[column]).astype(float)
        except (TypeError, ValueError) as e:
            raise ImportValidationError(
                f"Column '{column}' holds a value that is not a number: {e}",
                column=column,
            ) from e
        if column in NON_NULL_COLUMNS:
            _refuse(
                frame,
                values.isna(),
                column,
                "may not be null, and no number stands in for a missing one "
                "here: fill it, or leave the rows out",
            )
        _refuse(frame, values.isin([math.inf, -math.inf]), column, "is not finite")
        if column == "fit_score":
            _refuse(frame, (values < 0) | (values > 1), column, "is outside 0 to 1")
        frame[column] = values


def _check_vocabulary(frame: pd.DataFrame) -> None:
    for column, accepted in (
        ("role", ROLES),
        ("source", SOURCES),
        ("tier", TIERS),
        ("engine_tier", TIERS),
    ):
        if column not in frame.columns:
            continue
        values = frame[column]
        if column in ("tier", "engine_tier"):
            values = values.map(lambda v: LEGACY_TIERS.get(v, v))
            frame[column] = values
        _refuse(
            frame,
            values.notna() & ~values.isin(accepted),
            column,
            f"holds a value that is none of {', '.join(accepted)}",
        )


def _check_peaks(frame: pd.DataFrame) -> None:
    _refuse(frame, frame["role"].isna(), "role", "may not be null")
    _refuse(frame, frame["sample_peak_id"].isna(), "sample_peak_id", "may not be null")
    frame["sample_peak_id"] = frame["sample_peak_id"].astype(str)
    _refuse(
        frame,
        frame["sample_peak_id"].duplicated(keep=False),
        "sample_peak_id",
        "names a peak twice; a run holds one row per peak",
    )


def _check_owners(frame: pd.DataFrame) -> None:
    """One isotopologue naming an owner row of the import, one level deep."""
    if "owner_sample_peak_id" not in frame.columns:
        return
    owner = frame["owner_sample_peak_id"]
    named = owner.notna()
    if not named.any():
        return
    owner = owner.where(~named, owner.astype(str))
    frame["owner_sample_peak_id"] = owner.astype(object).where(named, None)
    column = "owner_sample_peak_id"
    _refuse(
        frame, named & (owner == frame["sample_peak_id"]), column, "names its own row"
    )
    _refuse(
        frame,
        named & (frame["role"] != OWNED_ROLE),
        column,
        f"names an owner on a row whose role is not '{OWNED_ROLE}'",
    )
    peaks = set(frame["sample_peak_id"])
    _refuse(
        frame,
        named & ~owner.isin(peaks),
        column,
        "names a peak that is no row of the frame",
    )
    owned = set(frame.loc[frame["role"] == OWNED_ROLE, "sample_peak_id"])
    _refuse(
        frame,
        named & owner.isin(owned),
        column,
        f"names an owner that is itself an '{OWNED_ROLE}'; the link is one level deep",
    )


def _check_tiers(frame: pd.DataFrame, bands: dict[str, float]) -> None:
    if "tier" not in frame.columns:
        return
    fit = frame["fit_score"] if "fit_score" in frame.columns else None
    formula = frame["assigned_formula"] if "assigned_formula" in frame.columns else None
    bad = pd.Series(
        [
            not _reachable(
                None if pd.isna(tier) else tier,
                None if fit is None or pd.isna(fit.iloc[i]) else float(fit.iloc[i]),
                None
                if formula is None or pd.isna(formula.iloc[i])
                else formula.iloc[i],
                bands,
            )
            for i, tier in enumerate(frame["tier"])
        ],
        index=frame.index,
    )
    _refuse(
        frame,
        bad,
        "tier",
        f"states a tier the row's fit_score cannot reach under tier_bands "
        f"(assigned >= {bands['assigned']}, candidate >= {bands['candidate']}): "
        "evidence is never above the fit, and a row with no fit or no formula "
        "is 'unassigned' or 'below_assignability'. Leave the tier out and the "
        "server derives it",
    )


def prepare_rows(
    frame: pd.DataFrame,
    bands: dict[str, float],
    mechanisms: Callable[[], pd.DataFrame | None],
) -> tuple[list[dict[str, Any]], list[int], list]:
    """Check a frame and turn it into the rows an import sends.

    :param frame: One row per peak, in the columns of :data:`ROW_COLUMNS`
        plus, optionally, :data:`ADDUCT_COLUMN`; a frame read with
        ``peak_assignments.get`` is one, its tiers sent as the engine's.
    :param bands: The run's bands, from :func:`normalise_bands`.
    :param mechanisms: Reads this server's mechanism listing, called only
        when there is an adduct to resolve.
    :return: The rows, in the frame's order, each one's size in bytes as
        JSON, and each one's label in the frame's index.
    :raises ImportValidationError: On anything the server would refuse that
        can be told here, or that could not be sent at all.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"import_run takes a DataFrame, not {type(frame).__name__}.")
    if frame.empty:
        raise ImportValidationError(
            "The frame has no rows; an empty run is not a ledger."
        )
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ImportValidationError(
            f"The frame lacks the column(s) {', '.join(missing)}.",
            column=missing[0],
        )
    work = frame.copy()
    _owner_references(work)
    _exported_tiers(work)
    _resolve_adducts(work, mechanisms)

    unknown = [
        column
        for column in work.columns
        if column not in ROW_COLUMNS
        and column not in READ_ONLY_COLUMNS
        and column != ADDUCT_COLUMN
    ]
    if unknown:
        # WARNING: a misspelt column is dropped, not sent
        logger.warning(
            "Column(s) {} are not row fields of an import, and are not sent.",
            ", ".join(map(str, unknown)),
        )
    work = work[[column for column in ROW_COLUMNS if column in work.columns]]

    _check_peaks(work)
    _check_numbers(work)
    _check_vocabulary(work)
    _check_owners(work)
    _check_tiers(work, bands)

    rows = work.astype(object).where(work.notna(), None).to_dict("records")
    sizes: list[int] = []
    for label, row in zip(work.index, rows):
        try:
            sizes.append(len(json.dumps(row, allow_nan=False)))
        except (TypeError, ValueError) as e:
            raise ImportValidationError(
                f"Row {label!r} cannot be sent as JSON ({e}); look at its "
                "alternatives and provenance, which go as they are.",
                rows=[label],
            ) from e
    return rows, sizes, list(work.index)


def body_bytes(body: dict[str, Any]) -> int:
    """The size of *body* as the HTTP layer sends it."""
    return len(json.dumps(body, allow_nan=False))


def check_sizes(labels: list, sizes: list[int], budget: int) -> None:
    """Refuse a row no request can carry, however the rows are cut.

    :param labels: Each row's index label in the caller's frame.
    :param sizes: Each row's size as JSON.
    :param budget: Bytes one request's rows may take, the rest of the body
        apart.
    :raises ImportValidationError: Naming the rows over *budget*.
    """
    over = [label for label, size in zip(labels, sizes) if size > budget]
    if over:
        raise ImportValidationError(
            f"Row(s) {_named(over)} are more bytes as JSON than one request may "
            f"carry ({budget}); trim their alternatives or provenance.",
            rows=over,
        )


def chunk_end(sizes: list[int], start: int, max_rows: int, budget: int) -> int:
    """Where the chunk beginning at row *start* ends.

    :param sizes: Each row's size as JSON, none over *budget*
        (:func:`check_sizes`).
    :param start: The chunk's first row.
    :param max_rows: Rows one request may carry.
    :param budget: Bytes the chunk's rows may take, the rest of the body
        apart.
    :return: The index one past the chunk's last row, at least one row on.
    """
    end, used = start, -2
    while end < len(sizes) and end - start < max(max_rows, 1):
        # ", " between two rows, as json.dumps writes a list
        used += sizes[end] + 2
        if used > budget and end > start:
            break
        end += 1
    return end
