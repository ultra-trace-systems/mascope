"""``get_target_isotopes`` must not treat an empty compound list as no filter.

Calibration resolves the compounds of the ionization mode's calibration
collection and asks for their isotopes. When the collection is empty that list
is empty, and the falsy check that used to guard the filter dropped it
altogether: the query then returned every target isotope in the database -
other collections, other workspaces included - and the sample was fitted
against whatever of them happened to fall in the refine window.

The distinction under test is None (no compound filter) versus [] (match
nothing). The fixture puts two populated collections in two different
workspaces beside one empty collection, so a filter that leaks would be caught
by rows it must never return.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from mascope_backend.api.controllers.target.associations.target_compound_in_target_collection_controller import (
    get_target_compound_in_target_collection,
)
from mascope_backend.api.controllers.target.isotopes.target_isotopes_controller import (
    get_target_isotopes,
)
from mascope_backend.db import (
    IonizationMechanism,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _isotope_ids(response: dict) -> set[str]:
    return {row["target_isotope_id"] for row in response["data"]}


@pytest_asyncio.fixture
async def collections(async_session_factory):
    """Two populated collections in separate workspaces, plus an empty one.

    Each populated collection holds one compound with one ion carrying two
    isotopes; the empty collection holds nothing at all - the state an
    ionization mode can be left in by configuring a calibration collection and
    never filling it.
    """
    ids = SimpleNamespace(
        workspace_a=gen_id(),
        workspace_b=gen_id(),
        collection_a=gen_id(),
        collection_b=gen_id(),
        collection_empty=gen_id(),
        compound_a=gen_id(),
        compound_b=gen_id(),
        mechanism=gen_id(),
        ion_a=gen_id(),
        ion_b=gen_id(),
        iso_a1=gen_id(),
        iso_a2=gen_id(),
        iso_b1=gen_id(),
        iso_b2=gen_id(),
    )
    async with async_session_factory() as session:
        for workspace_id in (ids.workspace_a, ids.workspace_b):
            session.add(
                Workspace(
                    workspace_id=workspace_id,
                    workspace_name=f"Isotope filter WS {workspace_id}",
                    workspace_description="Compound filter test workspace",
                    workspace_status="active",
                    workspace_utc_created=_NOW,
                    workspace_utc_modified=_NOW,
                )
            )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {ids.mechanism}",
            )
        )
        session.add(
            TargetCollection(
                target_collection_id=ids.collection_empty,
                target_collection_name=f"Empty collection {ids.collection_empty}",
                workspace_id=ids.workspace_a,
            )
        )
        populated = (
            (ids.collection_a, ids.workspace_a, ids.compound_a, ids.ion_a),
            (ids.collection_b, ids.workspace_b, ids.compound_b, ids.ion_b),
        )
        for collection_id, workspace_id, compound_id, ion_id in populated:
            session.add(
                TargetCollection(
                    target_collection_id=collection_id,
                    target_collection_name=f"Collection {collection_id}",
                    workspace_id=workspace_id,
                )
            )
            session.add(
                TargetCompound(
                    target_compound_id=compound_id,
                    target_compound_name=f"Compound {compound_id}",
                    target_compound_formula="C6H12O6",
                )
            )
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound_id,
                    target_collection_id=collection_id,
                )
            )
            session.add(
                TargetIon(
                    target_ion_id=ion_id,
                    target_compound_id=compound_id,
                    ionization_mechanism_id=ids.mechanism,
                    target_ion_formula="C6H13O6+",
                )
            )
        isotopes = (
            (ids.iso_a1, ids.ion_a, 181.07, 1.0),
            (ids.iso_a2, ids.ion_a, 182.07, 0.1),
            (ids.iso_b1, ids.ion_b, 203.05, 1.0),
            (ids.iso_b2, ids.ion_b, 204.05, 0.06),
        )
        for isotope_id, ion_id, mz, abundance in isotopes:
            session.add(
                TargetIsotope(
                    target_isotope_id=isotope_id,
                    target_ion_id=ion_id,
                    target_isotope_formula="C6H13O6+",
                    mz=mz,
                    relative_abundance=abundance,
                    resolution="HIGH",
                )
            )
        await session.commit()
    return ids


class TestTargetCompoundIdsFilter:
    @pytest.mark.asyncio
    async def test_empty_list_matches_nothing(self, collections):
        """[] is a filter that nothing satisfies, not an absent filter."""
        response = await get_target_isotopes(target_compound_ids=[])

        assert response["results"] == 0
        assert response["data"] == []

    @pytest.mark.asyncio
    async def test_none_applies_no_compound_filter(self, collections):
        """None keeps the old meaning: every isotope the other filters allow."""
        response = await get_target_isotopes(target_compound_ids=None)

        assert _isotope_ids(response) >= {
            collections.iso_a1,
            collections.iso_a2,
            collections.iso_b1,
            collections.iso_b2,
        }

    @pytest.mark.asyncio
    async def test_named_compounds_stay_within_their_collection(self, collections):
        """A populated collection resolves to its own isotopes only."""
        response = await get_target_isotopes(
            target_compound_ids=[collections.compound_a],
        )

        assert _isotope_ids(response) == {collections.iso_a1, collections.iso_a2}


class TestEmptyCalibrationCollection:
    @pytest.mark.asyncio
    async def test_empty_collection_yields_no_isotopes(self, collections):
        """The calibration lookup, end to end, on an empty collection.

        Resolving the collection's compounds and querying their isotopes - the
        two steps ``_resolve_calibration_isotopes`` performs - must not reach a
        single isotope of the collections next to it.
        """
        compounds = await get_target_compound_in_target_collection(
            target_collection_id=collections.collection_empty,
        )
        compound_ids = [row["target_compound_id"] for row in compounds["data"]]
        assert compound_ids == []

        response = await get_target_isotopes(
            target_compound_ids=compound_ids,
            ionization_mechanism_ids=[collections.mechanism],
            resolution="HIGH",
        )

        assert response["results"] == 0
        assert _isotope_ids(response) == set()
