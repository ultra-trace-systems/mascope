"""
Seed a small, illustrative reference-compound set into the active database.

Used by the local demo (`mascope demo`) so the peak-assignment "Identity" column
and the ``known_only`` suspect-screening prior are visible out of the box -
without downloading a multi-gigabyte public-database dump. This is NOT a real
mirror: it is a couple dozen well-known compounds (common atmospheric oxidation
products and acids, a few environmental contaminants incl. PFAS, and some
recognizable molecules) so that some de novo candidates in the demo light up
with a named identity.

Idempotent by replacement: any existing ``demo`` reference source is deleted and
re-seeded, so a `mascope demo` start - which runs this every time - always ends
up with the curated set in this file rather than whatever an older build left
behind. Formulas are canonicalized and masses computed on insert via
``mascope_reference`` exactly as a real ``mascope reference sync`` load would, so
the demo rows are queried the same way.

Usage:
    mascope dev db script run seed_reference_demo
    (also invoked automatically by `mascope demo`)
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete

from mascope_backend.db import (
    ReferenceCompound,
    ReferenceSource,
    async_session,
    configure_database_engine,
)
from mascope_backend.runtime import runtime
from mascope_reference import canonical_formula, monoisotopic_mass


# Fixed name for the demo reference load. Stable so a re-seed can find and
# replace the previous one rather than stacking a second demo source beside it.
DEMO_SOURCE = "demo"

# (name, neutral formula, CAS or None). Every formula here is one the demo
# dataset actually assigns (verified against the demo database), so the identity
# column lights up on real demo peaks. Each name is a genuine known compound
# sharing that formula - which is exactly what a formula lookup against a real
# reference database returns. The demo is monoterpene (alpha-pinene) oxidation
# chemistry, so most entries are its canonical SOA markers; a few recognizable
# contaminants and small molecules are included too. Not a real database.
DEMO_COMPOUNDS = [
    # Canonical alpha-pinene / monoterpene oxidation products (the demo's core)
    ("Pinonic acid", "C10H16O3", "473-72-3"),
    ("Pinic acid", "C9H14O4", "473-73-4"),
    ("Pinonaldehyde", "C10H16O2", "2704-78-1"),
    ("10-Hydroxypinonic acid", "C10H16O4", None),
    ("Terpenylic acid", "C8H12O4", None),
    ("Camphor", "C10H16O", "76-22-2"),
    ("Thymol", "C10H14O", "89-83-8"),
    # Recognizable contaminants / plasticizers (suspect-screening angle)
    ("Dibutyl phthalate", "C16H22O4", "84-74-2"),
    ("Bis(2-ethylhexyl) adipate", "C22H42O4", "103-23-1"),
    # Common named small molecules present in the demo
    ("Formic acid", "CH2O2", "64-18-6"),
    ("Nitric acid", "HNO3", "7697-37-2"),
    ("Glycerol", "C3H8O3", "56-81-5"),
    ("Urea", "CH4N2O", "57-13-6"),
    ("Triethylene glycol", "C6H14O4", "112-27-6"),
    ("Sorbic acid", "C6H8O2", "110-44-1"),
    ("gamma-Butyrolactone", "C4H6O2", "96-48-0"),
]


async def seed_reference_demo() -> None:
    """Seed the small demo reference-compound set, replacing any previous load.

    Idempotent by replacement: any existing ``demo`` source (and its compounds,
    via the FK cascade) is dropped first, so re-running always applies the
    current curated set rather than skipping a stale one.
    """
    await configure_database_engine()
    async with async_session() as session:
        # Drop any prior demo load so the latest list always wins. The
        # reference_compound FK is ON DELETE CASCADE, so its rows go too.
        await session.execute(
            delete(ReferenceSource).where(ReferenceSource.name == DEMO_SOURCE)
        )

        source = ReferenceSource(
            name=DEMO_SOURCE,
            version="demo-seed",
            license="public-domain",
            record_count=0,
            is_active=True,
            ingested_at=datetime.now(timezone.utc),
        )
        session.add(source)
        await session.flush()  # assign source.reference_source_id

        count = 0
        for name, formula, cas in DEMO_COMPOUNDS:
            canonical = canonical_formula(formula)
            if canonical is None:
                runtime.logger.warning(f"Skipping demo compound '{name}' ({formula})")
                continue
            session.add(
                ReferenceCompound(
                    reference_source_id=source.reference_source_id,
                    formula=canonical,
                    monoisotopic_mass=monoisotopic_mass(canonical),
                    inchikey=None,
                    name=name,
                    source_native_id=cas or name,
                    xrefs={"cas": cas} if cas else {},
                    license="public-domain",
                )
            )
            count += 1

        source.record_count = count
        await session.commit()

    runtime.logger.success(
        f"Seeded {count} demo reference compounds (source '{DEMO_SOURCE}'). "
        "Peak assignment now shows known-compound identities."
    )


def main() -> None:
    """Entry point for the seed script (discovered by the CLI script runner)."""
    try:
        asyncio.run(seed_reference_demo())
    except KeyboardInterrupt:
        runtime.logger.info("\nDemo reference seed cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Demo reference seed script failed")
        raise


if __name__ == "__main__":
    main()
