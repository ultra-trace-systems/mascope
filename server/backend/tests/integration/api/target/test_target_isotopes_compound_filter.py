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

The calibration resolver that reported the bug is then run against the same
data, so the production sequence is covered rather than a copy of it.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    EMPTY_CALIBRATION_COLLECTION_WARNING,
    NO_CALIBRATION_ISOTOPES_WARNING,
    calibration_params_factory,
    get_calibration_handler,
)
from mascope_backend.api.controllers.target.isotopes.target_isotopes_controller import (
    get_target_isotopes,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
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


def _calibration_handler(filename: str, collection_id: str, mechanism_id: str):
    """A calibration handler pointed at one collection, against the real DB.

    Only the two target lookups are exercised here, and both take everything
    they need from ``params`` and the filename - no sample file is touched.
    """
    defaults = calibration_params_factory(filename=filename)
    resolved = MzCalibrationParams(refine_window=100).with_defaults(defaults)
    params = CalibrationFitParams(
        calibration_collection_id=collection_id,
        ionization_mechanism_ids=[mechanism_id],
        polarity="+",
        **resolved.model_dump(),
    )
    return get_calibration_handler(filename, params, notification=None)


class TestResolveCalibrationIsotopesAgainstTheDatabase:
    """The production resolver itself, run against seeded collections.

    The query-level cases above pin ``get_target_isotopes``; these run the
    caller that the bug was reported through, so a future change to the
    resolver's own sequence is covered rather than a copy of it.
    """

    @pytest.mark.asyncio
    async def test_empty_collection_reaches_no_isotopes(self, collections):
        """An empty collection stops the resolver and leaks nothing."""
        handler = _calibration_handler(
            "orbitrap", collections.collection_empty, collections.mechanism
        )

        resolved = await handler._resolve_calibration_isotopes()

        assert resolved is None
        assert handler.warning == EMPTY_CALIBRATION_COLLECTION_WARNING
        assert handler.fit_result is None
        assert handler.stats is None

    @pytest.mark.asyncio
    async def test_populated_collection_resolves_its_own_isotopes(self, collections):
        """A populated collection resolves to its isotopes and no others."""
        handler = _calibration_handler(
            "orbitrap", collections.collection_a, collections.mechanism
        )

        resolved = await handler._resolve_calibration_isotopes()

        assert handler.warning is None
        assert set(resolved["target_isotope_id"]) == {
            collections.iso_a1,
            collections.iso_a2,
        }

    @pytest.mark.asyncio
    async def test_resolution_mismatch_is_reported_as_such(self, collections):
        """The seeded isotopes are HIGH only, so a TOF sample finds none.

        The mode's mechanisms match here; it is the instrument's resolution
        that excludes everything - which is why the warning names both.
        """
        handler = _calibration_handler(
            "tofwerk", collections.collection_a, collections.mechanism
        )

        resolved = await handler._resolve_calibration_isotopes()

        assert resolved is None
        assert handler.warning == NO_CALIBRATION_ISOTOPES_WARNING
        assert "resolution" in handler.warning
