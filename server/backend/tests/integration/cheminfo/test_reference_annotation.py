"""
Integration: reference-database annotation flowing through the cheminfo engine.

Seeds a small reference set (aspirin + caffeine from a "pubchem-test" source)
and a deprotonation ionization mechanism, then drives the real
``retrieve_compositions_by_mz`` service at the [M-H]- m/z of aspirin. Verifies
that the de novo candidates are additively annotated with the known reference
compound sharing their formula, and that ``known_only=True`` keeps only the
reference-backed candidates (the suspect-screening prior).

Locks the wiring exercised end to end in the Phase 0-2 public-database
integration - a regression here would silently drop identity annotation from
the assignment engine.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.new.cheminfo.service import retrieve_compositions_by_mz
from mascope_backend.db import (
    IonizationMechanism,
    ReferenceCompound,
    ReferenceSource,
)
from mascope_backend.db.id import gen_id
from mascope_reference import canonical_formula, monoisotopic_mass
from mascope_tools.composition.utils import (
    composition_mass,
    parse_composition,
    parse_ionization,
)


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MECH = "-H-"  # deprotonation
_ASPIRIN = "C9H8O4"
_CAFFEINE = "C8H10N4O2"

# [M-H]- of aspirin, dead-center on the reference compound.
_ASPIRIN_NEUTRAL = composition_mass(parse_composition(_ASPIRIN))
_ASPIRIN_MZ = _ASPIRIN_NEUTRAL - parse_ionization(_MECH).mass
_FORMULA_RANGES = "C0-30 H0-40 O0-10 N0-6"


def _compound(source_id, formula, name, cid):
    return ReferenceCompound(
        reference_source_id=source_id,
        formula=canonical_formula(formula),
        monoisotopic_mass=monoisotopic_mass(canonical_formula(formula)),
        inchikey=None,
        name=name,
        smiles=None,
        inchi=None,
        source_native_id=cid,
        xrefs={"pubchem_cid": cid},
        license="public-domain",
    )


@pytest_asyncio.fixture
async def seeded(async_session_factory):
    """Seed a mechanism + one active reference source with two compounds.

    Yields the deprotonation mechanism id. Cleans up all seeded rows on
    teardown so the shared session-scoped test DB stays isolated between tests.
    """
    mech_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mech_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=_MECH,
            )
        )
        source = ReferenceSource(
            name="pubchem-test",
            version="test",
            license="public-domain",
            record_count=2,
            is_active=True,
            ingested_at=_NOW,
        )
        session.add(source)
        await session.flush()  # assign source.reference_source_id
        source_id = source.reference_source_id
        session.add(_compound(source_id, _ASPIRIN, "aspirin", "2244"))
        session.add(_compound(source_id, _CAFFEINE, "caffeine", "2519"))
        await session.commit()

    yield mech_id

    # Delete every seeded row so the session-scoped test DB stays isolated.
    async with async_session_factory() as session:
        await session.execute(
            delete(ReferenceCompound).where(
                ReferenceCompound.reference_source_id == source_id
            )
        )
        await session.execute(
            delete(ReferenceSource).where(
                ReferenceSource.reference_source_id == source_id
            )
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mech_id
            )
        )
        await session.commit()


def _by_formula(data, formula):
    return next((r for r in data if r["target_compound_formula"] == formula), None)


@pytest.fixture
def reference_in_play(monkeypatch):
    """Opt this deployment into the peak-centric surfaces.

    The mirror is only consulted when it is in play - either the caller asked for
    the suspect-screening prior (``known_only``) or the deployment enabled peak
    assignment. A search that does neither must keep the pre-feature response, which
    `test_annotation_is_gated_off_when_disabled` pins.
    """
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest.mark.asyncio
async def test_annotation_is_gated_off_when_disabled(seeded, monkeypatch):
    """With the feature off and no known_only, the response is left untouched.

    Peak assignment is on by default, so "off" has to be set rather than left
    alone: clearing the override would now resolve to the flag's own default and
    exercise the annotated path this test exists to rule out.
    """
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")
    resp = await retrieve_compositions_by_mz(
        mz=_ASPIRIN_MZ,
        ionization_mechanism_ids=[seeded],
        mz_precision=5,
        formula_ranges=_FORMULA_RANGES,
    )
    data = resp["data"]
    assert data, "de novo engine returned no candidates"
    # No new key, so an existing SDK client sees exactly the pre-feature shape.
    assert all("known_compounds" not in r for r in data)


@pytest.mark.asyncio
async def test_results_are_annotated_with_known_compounds(seeded, reference_in_play):
    """Every candidate carries known_compounds; the matching formula is named."""
    resp = await retrieve_compositions_by_mz(
        mz=_ASPIRIN_MZ,
        ionization_mechanism_ids=[seeded],
        mz_precision=5,
        formula_ranges=_FORMULA_RANGES,
    )
    data = resp["data"]
    assert data, "de novo engine returned no candidates"
    # Additive: the field is present on every result.
    assert all("known_compounds" in r for r in data)

    aspirin = _by_formula(data, _ASPIRIN)
    assert aspirin is not None, "expected C9H8O4 among candidates"
    assert len(aspirin["known_compounds"]) == 1
    known = aspirin["known_compounds"][0]
    assert known["name"] == "aspirin"
    assert known["source"] == "pubchem-test"
    assert known["license"] == "public-domain"


@pytest.mark.asyncio
async def test_annotation_is_selective(seeded, reference_in_play):
    """Isobaric de novo formulas with no reference match get an empty list."""
    resp = await retrieve_compositions_by_mz(
        mz=_ASPIRIN_MZ,
        ionization_mechanism_ids=[seeded],
        mz_precision=80,  # wide window -> many isobaric candidates
        formula_ranges=_FORMULA_RANGES,
    )
    data = resp["data"]
    assert len(data) > 1
    assert _by_formula(data, _ASPIRIN)["known_compounds"], "aspirin should be annotated"
    # At least one other candidate is de novo-only (no reference identity).
    assert any(
        r["target_compound_formula"] != _ASPIRIN and not r["known_compounds"]
        for r in data
    )


@pytest.mark.asyncio
async def test_known_only_prior_filters_unknowns(seeded):
    """known_only keeps only reference-backed candidates."""
    full = await retrieve_compositions_by_mz(
        mz=_ASPIRIN_MZ,
        ionization_mechanism_ids=[seeded],
        mz_precision=80,
        formula_ranges=_FORMULA_RANGES,
    )
    known = await retrieve_compositions_by_mz(
        mz=_ASPIRIN_MZ,
        ionization_mechanism_ids=[seeded],
        mz_precision=80,
        formula_ranges=_FORMULA_RANGES,
        known_only=True,
    )
    assert known["results"] < full["results"]
    assert known["results"] >= 1
    assert all(r["known_compounds"] for r in known["data"])
    assert _by_formula(known["data"], _ASPIRIN) is not None
