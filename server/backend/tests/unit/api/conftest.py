"""
Fixtures specific to API unit tests.

This module provides fixtures for unit testing API controllers and services
without relying on the actual API endpoints. It focuses on testing the business
logic of API controllers in isolation from the HTTP layer.

Key components:
- Test data fixtures (datasets, models, etc.)
- Mock fixtures for Socket.IO and other external dependencies
- Factory fixtures for creating controller-specific mocks

Unit testing approach for API components:
- Test controllers directly, bypassing FastAPI's request/response cycle
- Mock external services and dependencies
- Focus on business logic correctness
- Verify database interactions work as expected
"""

from collections.abc import Callable
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from test_utils import gen_test_id

from mascope_backend.db import Dataset, IonizationMechanism, TargetCompound, Workspace


# Stable IDs for session-scoped dataset fixtures that are referenced
# by value in parametrized tests (e.g. test_get_dataset_existence).
_DATASET_ID_1 = "unit-test-1"
_DATASET_ID_2 = "unit-test-2"


@pytest_asyncio.fixture(scope="session")
async def test_ionization_mechanisms(
    async_session_factory,
) -> list[IonizationMechanism]:
    """Create test ionization mechanism records in the unit test database.

    This fixture populates the database with ionization mechanisms that can be used
    by multiple unit tests.

    :param async_session_factory: Factory for creating database sessions
    :return: List of created ionization mechanism objects
    :rtype: list
    """
    async with async_session_factory() as session:
        ionization_mechanisms = [
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="-",
                ionization_mechanism="[M-H]-",
            ),
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="-",
                ionization_mechanism="[M+Br]-",
            ),
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="-",
                ionization_mechanism="[M+NO3]-",
            ),
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="+",
                ionization_mechanism="[M+H]+",
            ),
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="+",
                ionization_mechanism="[M+CH4N2O+H]+",
            ),
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="+",
                ionization_mechanism="[M]+.",
            ),
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="-",
                ionization_mechanism="[M]-.",
            ),
            IonizationMechanism(
                ionization_mechanism_id=gen_test_id(),
                ionization_mechanism_polarity="-",
                ionization_mechanism="[M+^NO3]-",
            ),
        ]

        for ionization_mechanism in ionization_mechanisms:
            session.add(ionization_mechanism)

        await session.commit()

        # Refresh to get all attributes
        for ionization_mechanism in ionization_mechanisms:
            await session.refresh(ionization_mechanism)

        return ionization_mechanisms


@pytest_asyncio.fixture(scope="session")
async def test_target_compounds_by_composition(
    async_session_factory: Callable,
) -> list[TargetCompound]:
    """Create test target compound records by composition in the unit test database.

    :param async_session_factory: Factory for creating database sessions
    :type async_session_factory: Callable

    :return: List of created target compound objects
    :rtype: list[TargetCompound]
    """

    async with async_session_factory() as session:
        # These are example target compounds created for testing purposes.
        # They are not exhaustive and can be adjusted as needed for your tests.
        target_compounds = [
            TargetCompound(
                target_compound_id=gen_test_id(),
                target_compound_name=None,
                target_compound_formula="()",
                cas_number=None,
            ),
            TargetCompound(
                target_compound_id=gen_test_id(),
                target_compound_name="Water",
                target_compound_formula="H2O",
                cas_number="7732-18-5",
            ),
            TargetCompound(
                target_compound_id=gen_test_id(),
                target_compound_name="Urea",
                target_compound_formula="CH4N2O",
                cas_number="57-13-6",
            ),
            TargetCompound(
                target_compound_id=gen_test_id(),
                target_compound_name="a-Pinene",
                target_compound_formula="C10H16",
                cas_number="80-56-8",
            ),
            TargetCompound(
                target_compound_id=gen_test_id(),
                target_compound_name="Nitric acid dimer",
                target_compound_formula="(HNO3)2",
                cas_number=None,
            ),
            TargetCompound(
                target_compound_id=gen_test_id(),
                target_compound_name="Isotopically labeled nitric acid",
                target_compound_formula="H^NO3",
                cas_number=None,
            ),
        ]
        for target_compound in target_compounds:
            session.add(target_compound)
        await session.commit()

        # Refresh to get all attributes
        for target_compound in target_compounds:
            await session.refresh(target_compound)

        return target_compounds


@pytest_asyncio.fixture(scope="session")
async def unit_test_workspace(async_session_factory):
    """Create a workspace for unit tests that need datasets."""
    async with async_session_factory() as session:
        ws = Workspace(
            workspace_id="unit-test-ws",
            workspace_name="Unit Test Workspace",
            workspace_utc_created=datetime.now(timezone.utc),
        )
        session.add(ws)
        await session.commit()
        await session.refresh(ws)
        return ws


@pytest_asyncio.fixture(scope="session")
async def test_datasets(async_session_factory, unit_test_workspace) -> list[Dataset]:
    """Create test dataset records in the unit test database.

    Session-scoped: created once and reused across all unit tests that need
    dataset data. IDs are stable module-level constants because parametrized
    tests reference them by value (e.g. `test_get_dataset_existence`).

    :param async_session_factory: Factory for creating database sessions
    :return: List of created dataset objects
    :rtype: list[Dataset]
    """
    async with async_session_factory() as session:
        ws_id = unit_test_workspace.workspace_id
        datasets = [
            Dataset(
                dataset_id=_DATASET_ID_1,
                workspace_id=ws_id,
                dataset_name="Unit Test Dataset 1",
                dataset_description="This is a unit test dataset",
                dataset_type="ANALYSIS",
                instrument=None,
                locked=False,
                icon=None,
                dataset_utc_created=datetime.now(timezone.utc),
            ),
            Dataset(
                dataset_id=_DATASET_ID_2,
                workspace_id=ws_id,
                dataset_name="Unit Test Dataset 2",
                dataset_description="This is another unit test dataset",
                dataset_type="ANALYSIS",
                instrument=None,
                locked=False,
                icon=None,
                dataset_utc_created=datetime.now(timezone.utc),
            ),
        ]
        for dataset in datasets:
            session.add(dataset)
        await session.commit()
        for dataset in datasets:
            await session.refresh(dataset)
        return datasets


@pytest.fixture
def mock_emit_record_factory():
    """Factory fixture for creating emit_record_* function mocks for
    different controllers.

    Returns a callable that patches `emit_record_created`, `emit_record_updated`,
    and `emit_record_deleted` at the given module path and returns a container with
    all three as `AsyncMock` attributes.

    Usage:
        @pytest.fixture
        def mock_emit_dataset(mock_emit_record_factory):
            return mock_emit_record_factory(
                "mascope_backend.api.controllers.dataset.dataset_controller"
            )

    Verification:
        mock_emit_dataset.created.assert_called_once()
        mock_emit_dataset.updated.assert_called_with(record_type="dataset", ...)
        assert mock_emit_dataset.deleted.call_count == 2

    :return: A function that creates and configures emit_record mocks
    :rtype: callable
    """

    def _make_mock_emit(module_path):
        """Create emit_record function mocks for a specific module path.

        :param module_path: Full import path to the module using emit_record functions
        :type module_path: str
        :return: MagicMock object with mocked created, updated, deleted functions
        :rtype: MagicMock
        """
        # Create patches for all three emit functions
        mock_created = patch(
            f"{module_path}.emit_record_created", new_callable=AsyncMock
        ).start()
        mock_updated = patch(
            f"{module_path}.emit_record_updated", new_callable=AsyncMock
        ).start()
        mock_deleted = patch(
            f"{module_path}.emit_record_deleted", new_callable=AsyncMock
        ).start()

        # Create a MagicMock container to hold all three mocks
        mock_container = MagicMock()
        mock_container.created = mock_created
        mock_container.updated = mock_updated
        mock_container.deleted = mock_deleted

        return mock_container

    yield _make_mock_emit
    patch.stopall()
