"""
Maintenance script to give target compounds with a CAS number the structure
identity (InChIKey, InChI, SMILES) the reference chemistry holds for it.

Target compounds gained ``inchikey``, ``inchi`` and ``smiles`` with FAIR
Phase 1; the compounds stored before carry at most a CAS number. The active
reference sources (``reference_compound`` rows of an active
``reference_source``) record CAS numbers in their cross-references - under
``casrn`` for CompTox, ``cas`` for a custom list - next to an InChIKey.

For each compound with a CAS number and no structure identity at all (no
InChIKey, InChI or SMILES), the candidates are the active reference rows
whose CAS number is the compound's, compared as digits only so ``50-99-7``
and ``50997`` agree, **and** whose formula is the compound's, compared as
compositions. A CAS number alone is not enough: a
number entered against the wrong compound would otherwise hand it another
substance's structure. Then:

- one distinct InChIKey among the candidates fills all three fields (the InChI
  and the SMILES from the first candidate row that has each, an InChI only
  when its formula layer is the compound's formula);
- several distinct InChIKeys leave the compound as it is, and are logged;
- none leaves it as it is.

Names are never matched on. A compound with any of the three set is left
alone, so a SMILES or an InChI a user entered without a key is never replaced
or cleared; nor is one given an identity since it was read.

Pass ``--dry-run`` (or set DRY_RUN=1, as ``mascope ... db script run`` takes
it) to report what would change without writing.

Usage:
    DRY_RUN=1 mascope dev db script run backfill_target_compound_identity
    mascope dev db script run backfill_target_compound_identity
    python -m mascope_backend.db.scripts.backfill_target_compound_identity --dry-run

Date: 2026-10-21
"""

import argparse
import asyncio
import os
import re
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text

from mascope_backend.api.controllers.target.compounds.target_compound_identity import (
    check_inchi_formula,
    formula_composition,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    validate_inchi,
    validate_inchikey,
    validate_smiles,
)
from mascope_backend.db import (
    ReferenceCompound,
    ReferenceSource,
    TargetCompound,
    async_session,
    configure_database_engine,
)
from mascope_backend.runtime import runtime


# The cross-reference keys a CAS number is recorded under: CompTox's, then a
# custom list's.
CAS_XREF_KEYS = ("casrn", "cas")

# Maximum number of compounds to log individually per outcome.
_PREVIEW_LIMIT = 20

# CAS numbers per reference lookup statement.
_CAS_CHUNK = 5000


def cas_digits(cas_number: str | None) -> str:
    """A CAS number as its digits alone, "" for none."""
    return re.sub(r"\D", "", cas_number or "")


async def _candidates() -> list[dict]:
    """Target compounds with a CAS number and no InChIKey, InChI or SMILES.

    :return: One dict per compound: target_compound_id,
        target_compound_formula, cas_number.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            select(
                TargetCompound.target_compound_id,
                TargetCompound.target_compound_formula,
                TargetCompound.cas_number,
            )
            .where(
                TargetCompound.cas_number.is_not(None),
                TargetCompound.inchikey.is_(None),
                TargetCompound.inchi.is_(None),
                TargetCompound.smiles.is_(None),
            )
            .order_by(TargetCompound.target_compound_id)
        )
        return [dict(row._mapping) for row in result]


def _xref_digits(key: str):
    """SQL for the digits of one cross-reference CAS number."""
    return func.regexp_replace(
        ReferenceCompound.xrefs[key].as_string(), "[^0-9]", "", "g"
    )


async def _reference_rows(digits: set[str]) -> list[dict]:
    """Active reference rows with an InChIKey whose CAS number is among ``digits``.

    :param digits: CAS numbers, digits only.
    :return: One dict per row: reference_compound_id, formula, inchikey, inchi,
        smiles, and ``cas`` - the digits of each of its CAS numbers.
    :rtype: list[dict]
    """
    wanted = sorted(digits)
    columns = [_xref_digits(key).label(key) for key in CAS_XREF_KEYS]
    rows: dict[int, dict] = {}
    async with async_session() as session:
        # Chunked: each number is a bind parameter, and the wire protocol caps
        # a statement at 32767 of them.
        for start in range(0, len(wanted), _CAS_CHUNK):
            chunk = wanted[start : start + _CAS_CHUNK]
            result = await session.execute(
                select(
                    ReferenceCompound.reference_compound_id,
                    ReferenceCompound.formula,
                    ReferenceCompound.inchikey,
                    ReferenceCompound.inchi,
                    ReferenceCompound.smiles,
                    *columns,
                )
                .join(ReferenceSource)
                .where(
                    ReferenceSource.is_active.is_(True),
                    ReferenceCompound.inchikey.is_not(None),
                    or_(*(column.in_(chunk) for column in columns)),
                )
            )
            for row in result:
                mapping = row._mapping
                rows[mapping["reference_compound_id"]] = {
                    "reference_compound_id": mapping["reference_compound_id"],
                    "formula": mapping["formula"],
                    "inchikey": mapping["inchikey"],
                    "inchi": mapping["inchi"],
                    "smiles": mapping["smiles"],
                    "cas": {mapping[key] for key in CAS_XREF_KEYS if mapping[key]},
                }
    return [rows[key] for key in sorted(rows)]


def _valid(validator, value: str | None) -> str | None:
    """``value`` as the request models would store it, or None if they would
    refuse it."""
    try:
        return validator(value)
    except ValueError:
        return None


def _identity(compound: dict, rows: list[dict]) -> dict:
    """The InChI and SMILES to store with the one InChIKey ``rows`` share.

    :param compound: The compound, as :func:`_candidates` reads it.
    :param rows: Its candidate reference rows, in reference id order.
    :return: ``{"inchi", "smiles"}``, either None when no row has a usable one.
    """
    inchi = None
    for row in rows:
        candidate = _valid(validate_inchi, row["inchi"])
        if candidate is None:
            continue
        try:
            check_inchi_formula(candidate, compound["target_compound_formula"])
        except HTTPException:
            continue
        inchi = candidate
        break
    smiles = next(
        (
            smiles
            for row in rows
            if (smiles := _valid(validate_smiles, row["smiles"])) is not None
        ),
        None,
    )
    return {"inchi": inchi, "smiles": smiles}


def _plan(compounds: list[dict], reference_rows: list[dict]) -> dict:
    """Decide every compound to fill.

    :param compounds: Rows from :func:`_candidates`.
    :param reference_rows: Rows from :func:`_reference_rows`, in reference id
        order.
    :return: ``updates`` (``{"id", "inchikey", "inchi", "smiles"}`` per
        compound to fill), ``ambiguous`` (``{"id", "cas_number", "inchikeys"}``
        per compound left alone for several keys) and the ``unmatched`` count.
    :rtype: dict
    """
    by_cas: dict[str, list[dict]] = defaultdict(list)
    for row in reference_rows:
        for digits in row["cas"]:
            by_cas[digits].append(row)

    updates: list[dict] = []
    ambiguous: list[dict] = []
    unmatched = 0
    for compound in compounds:
        composition = formula_composition(compound["target_compound_formula"])
        candidates = [
            row
            for row in by_cas.get(cas_digits(compound["cas_number"]), [])
            if formula_composition(row["formula"]) == composition
            and _valid(validate_inchikey, row["inchikey"]) is not None
        ]
        inchikeys = sorted({row["inchikey"].strip() for row in candidates})
        if not inchikeys:
            unmatched += 1
        elif len(inchikeys) > 1:
            ambiguous.append(
                {
                    "id": compound["target_compound_id"],
                    "cas_number": compound["cas_number"],
                    "inchikeys": inchikeys,
                }
            )
        else:
            updates.append(
                {
                    "id": compound["target_compound_id"],
                    "inchikey": inchikeys[0],
                    **_identity(compound, candidates),
                }
            )
    return {"updates": updates, "ambiguous": ambiguous, "unmatched": unmatched}


async def _apply(updates: list[dict]) -> None:
    """Write the planned identities, in one transaction.

    The guard on all three fields keeps a compound given any identity since it
    was read - by a user, or an earlier run - from being overwritten.

    :param updates: ``{"id", "inchikey", "inchi", "smiles"}`` per compound.
    """
    async with async_session() as session:
        await session.execute(
            text("""
                UPDATE target_compound
                SET inchikey = :inchikey, inchi = :inchi, smiles = :smiles
                WHERE target_compound_id = :id
                  AND inchikey IS NULL
                  AND inchi IS NULL
                  AND smiles IS NULL
            """),
            updates,
        )
        await session.commit()


async def run(dry_run: bool) -> None:
    """Find, match and fill the compounds' structure identities.

    :param dry_run: Report what would change without writing.
    """
    await configure_database_engine()

    compounds = await _candidates()
    if not compounds:
        runtime.logger.info(
            "No target compound has a CAS number and no structure identity."
        )
        return
    runtime.logger.info(
        f"Target compounds with a CAS number and no structure identity: "
        f"{len(compounds)}"
    )

    digits = {cas_digits(c["cas_number"]) for c in compounds} - {""}
    reference_rows = await _reference_rows(digits) if digits else []
    plan = _plan(compounds, reference_rows)

    for update in plan["updates"][:_PREVIEW_LIMIT]:
        runtime.logger.info(f"  {update['id']}: {update['inchikey']}")
    if len(plan["updates"]) > _PREVIEW_LIMIT:
        runtime.logger.info(f"  ... and {len(plan['updates']) - _PREVIEW_LIMIT} more")
    for skipped in plan["ambiguous"]:
        runtime.logger.info(
            f"  {skipped['id']} (CAS {skipped['cas_number']}) left alone: "
            f"several InChIKeys {', '.join(skipped['inchikeys'])}"
        )

    if dry_run:
        runtime.logger.info(
            f"Dry run: {len(plan['updates'])} target compounds would be filled."
        )
    elif plan["updates"]:
        await _apply(plan["updates"])

    filled = "would fill" if dry_run else "filled"
    runtime.logger.info("=" * 80)
    runtime.logger.info("BACKFILL TARGET COMPOUND IDENTITY COMPLETE")
    runtime.logger.info(
        f"Candidates: {len(compounds)}, {filled}: {len(plan['updates'])}, "
        f"several InChIKeys: {len(plan['ambiguous'])}, "
        f"no reference match: {plan['unmatched']}"
    )
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the target compound identity backfill script."""
    parser = argparse.ArgumentParser(
        prog="backfill_target_compound_identity",
        description=(
            "Fill the InChIKey, InChI and SMILES of target compounds whose CAS "
            "number and formula match one active reference compound."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN") == "1",
        help="Report what would change without writing (also DRY_RUN=1).",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(dry_run=args.dry_run))
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Script failed")
        raise


if __name__ == "__main__":
    main()
