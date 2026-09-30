"""
Seed the reference lists Mascope ships into the active database, for the demo.

Used by the local demo (`mascope demo`) so the peak-assignment "Identity" column
and the ``known_only`` suspect-screening prior are visible out of the box, and so
the shipped lists are exercised on every demo start. It loads exactly what
``mascope reference seed`` loads by default - each list as its own versioned
source, left alone when its version is already active - so starting the demo
twice changes nothing.

It also removes the ``demo`` source an older build seeded, a short hand-picked
list the shipped lists replace, so a demo database does not carry both.

Usage:
    mascope dev db script run seed_reference_demo
    (also invoked automatically by `mascope demo`)
"""

from sqlalchemy import delete, select

from mascope_backend.db.scripts.reference_sync import _sync_engine
from mascope_backend.runtime import runtime
from mascope_reference.schema import reference_compound, reference_source
from mascope_reference.seed import seed


#: The source an older build loaded its hand-picked demo list under.
LEGACY_DEMO_SOURCE = "demo"


def seed_reference_demo() -> None:
    """Load the shipped lists that load by default, and drop the legacy list."""
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            legacy = (
                conn.execute(
                    select(reference_source.c.reference_source_id).where(
                        reference_source.c.name == LEGACY_DEMO_SOURCE
                    )
                )
                .scalars()
                .all()
            )
            if legacy:
                conn.execute(
                    delete(reference_compound).where(
                        reference_compound.c.reference_source_id.in_(legacy)
                    )
                )
                conn.execute(
                    delete(reference_source).where(
                        reference_source.c.reference_source_id.in_(legacy)
                    )
                )
        outcomes = seed(engine)
    finally:
        engine.dispose()

    loaded = [outcome for outcome in outcomes if outcome.loaded]
    runtime.logger.success(
        f"Reference lists: {len(loaded)} loaded "
        f"({sum(outcome.ingested for outcome in loaded):,} compounds), "
        f"{len(outcomes) - len(loaded)} already current. Peak assignment now "
        "shows known-compound identities."
    )


def main() -> None:
    """Entry point for the seed script (discovered by the CLI script runner)."""
    try:
        seed_reference_demo()
    except KeyboardInterrupt:
        runtime.logger.info("\nDemo reference seed cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Demo reference seed script failed")
        raise


if __name__ == "__main__":
    main()
