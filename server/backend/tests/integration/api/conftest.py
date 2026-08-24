"""
API integration test fixtures.

This module provides fixtures for testing the full API request/response cycle
through `httpx.AsyncClient` with `ASGITransport`. It focuses on realistic
end-to-end testing of API endpoints with proper authentication and database
access.

Key components:
- Authentication fixtures (JWT token generation, authenticated clients)
- Role-based client fixtures (guest, editor, admin, owner)
- Test data fixtures for API requests
- Fundamental database fixtures (roles, users) used by authentication

Integration testing approach for API:
- Test API endpoints through the HTTP interface using httpx.AsyncClient
- Verify role-based access control (RBAC) works correctly
- Test realistic request/response flows
- Verify proper error handling and status codes
- Test with realistic authentication

Why AsyncClient instead of TestClient:
    TestClient runs the ASGI app in a separate thread with its own event loop.
    asyncpg connections are bound to the event loop they were created in and
    cannot be used from a different loop. This causes `InterfaceError: cannot
    perform operation: another operation is in progress` when TestClient's
    thread loop tries to use connections from the session-scoped test engine.
    AsyncClient runs in the same event loop as the test session, so all asyncpg
    operations stay on one loop.

NOTE: Authentication and RBAC
    These fixtures emulate the full Mascope authentication flow:
    1. JWT token is generated with the user's ID as subject
    2. Token is injected into the client's cookies
    3. On each request, the app validates the token and resolves the user's role
    4. Role-based access control returns 403 for insufficient permissions
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from mascope_backend.api.new.auth.access_token.service import create_access_token
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.devices.machine_account import create_machine_account
from mascope_backend.app.fast import fast
from mascope_backend.db import AgentDevice, Role, User


@pytest_asyncio.fixture(scope="session")
async def roles(async_session_factory):
    """Create role records for the integration test session.

    Session-scoped: roles are fundamental reference data that don't change
    between tests. Created once at session start and reused by all tests
    that need role-based access control.

    Uses `async_session_factory` directly — session-scoped fixtures cannot
    depend on narrower-scoped fixtures.

    :param async_session_factory: Factory for creating database sessions
    :type async_session_factory: async_sessionmaker
    :return: Dictionary mapping role name to Role object
    :rtype: dict[str, Role]
    """
    async with async_session_factory() as session:
        roles_config = {
            role_name: Role(role_id=access_level, role_name=role_name)
            for role_name, access_level in auth_settings.ROLE_ACCESS_LEVELS.items()
        }
        for role in roles_config.values():
            session.add(role)
        await session.commit()
        return roles_config


@pytest_asyncio.fixture(scope="session")
async def test_users(async_session_factory, roles):
    """Create test users with each role for the integration test session.

    Session-scoped: users are created once and reused across all tests.
    Consistent user identities are required for JWT token generation and
    RBAC verification across the session.

    :param async_session_factory: Factory for creating database sessions
    :type async_session_factory: async_sessionmaker
    :param roles: Dict of role objects keyed by role name
    :type roles: dict[str, Role]
    :return: Dictionary mapping role name to User object
    :rtype: dict[str, User]
    """
    async with async_session_factory() as session:
        users = {
            "guest": User(
                email="guest@test.com",
                username="guest_user",
                hashed_password="123456",
                is_active=True,
                is_verified=False,
                role_id=roles["guest"].role_id,
            ),
            "editor": User(
                email="editor@test.com",
                username="editor_user",
                hashed_password="123456",
                is_active=True,
                is_verified=False,
                role_id=roles["editor"].role_id,
            ),
            "admin": User(
                email="admin@test.com",
                username="admin_user",
                hashed_password="123456",
                is_active=True,
                is_verified=False,
                role_id=roles["admin"].role_id,
            ),
            "owner": User(
                email="owner@test.com",
                username="owner_user",
                hashed_password="123456",
                is_active=True,
                is_verified=False,
                is_superuser=True,
                role_id=roles["owner"].role_id,
            ),
        }
        for user in users.values():
            session.add(user)
        await session.commit()
        for user in users.values():
            await session.refresh(user)
        return users


@pytest.fixture
def create_jwt_auth_token():
    """Return a factory function that generates valid JWT tokens for test users.

    Tokens match the format expected by the application's authentication system:
    subject is the user UUID, audience and algorithm match `auth_settings`.

    :return: Callable that accepts a User and returns a JWT token string
    :rtype: Callable[[User], str]
    """

    def _create_token(user: User) -> str:
        payload = {
            "sub": str(user.id),
            "aud": auth_settings.JWT_AUDIENCE,
            "exp": datetime.now(timezone.utc) + timedelta(days=1),
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(
            payload, auth_settings.JWT_SECRET_KEY, algorithm=auth_settings.JWT_ALGORITHM
        )

    return _create_token


def _make_async_client(user: User, create_jwt_auth_token) -> AsyncClient:
    """Construct an AsyncClient with a JWT auth cookie for `user`.

    :param user: Authenticated test user
    :param create_jwt_auth_token: Token factory from the fixture
    :return: Configured AsyncClient (not yet entered as context manager)
    :rtype: AsyncClient
    """
    token = create_jwt_auth_token(user)
    client = AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    )
    return client


@pytest_asyncio.fixture
async def guest_client(test_users, create_jwt_auth_token):
    """AsyncClient authenticated as a guest user.

    Guest users can read datasets but cannot create, update, or delete.

    :param test_users: Dict of test user objects
    :param create_jwt_auth_token: JWT token factory
    :return: Authenticated AsyncClient with guest permissions
    :rtype: AsyncClient
    """
    async with _make_async_client(test_users["guest"], create_jwt_auth_token) as client:
        yield client


@pytest_asyncio.fixture
async def editor_client(test_users, create_jwt_auth_token):
    """AsyncClient authenticated as an editor user.

    Editor users can perform all CRUD operations on datasets.

    :param test_users: Dict of test user objects
    :param create_jwt_auth_token: JWT token factory
    :return: Authenticated AsyncClient with editor permissions
    :rtype: AsyncClient
    """
    async with _make_async_client(
        test_users["editor"], create_jwt_auth_token
    ) as client:
        yield client


@pytest_asyncio.fixture
async def admin_client(test_users, create_jwt_auth_token):
    """AsyncClient authenticated as an admin user.

    :param test_users: Dict of test user objects
    :param create_jwt_auth_token: JWT token factory
    :return: Authenticated AsyncClient with admin permissions
    :rtype: AsyncClient
    """
    async with _make_async_client(test_users["admin"], create_jwt_auth_token) as client:
        yield client


@pytest_asyncio.fixture
async def owner_client(test_users, create_jwt_auth_token):
    """AsyncClient authenticated as an owner user.

    Owner users have full access to all system capabilities.

    :param test_users: Dict of test user objects
    :param create_jwt_auth_token: JWT token factory
    :return: Authenticated AsyncClient with owner permissions
    :rtype: AsyncClient
    """
    async with _make_async_client(test_users["owner"], create_jwt_auth_token) as client:
        yield client


@pytest.fixture
def provision_device(async_session_factory):
    """Factory: pair a device, exactly as approving a pairing request does.

    Creates the ``AgentDevice`` row, the machine account it authenticates as
    (enrolled in the sponsor's acquisition workspaces), and a device-bound
    ``file-agent`` token - the three rows that always exist together in
    production, committed in one transaction like the real flow.

    Shared here because several suites need a paired device as a starting
    point: token renewal, strict mode, and upload attribution all begin with
    one, and a per-file copy would let them drift apart from the real pairing
    flow independently.

    :param async_session_factory: Session factory for the integration database
    :return: Async callable ``(sponsor_id, machine_name) ->
        (device_id, machine_user, device_bound_token)``
    :rtype: Callable
    """

    async def _provision(sponsor_id: int, machine_name: str = "ORBI-PC"):
        async with async_session_factory() as session:
            device = AgentDevice(
                name=machine_name,
                service_name="file-agent",
                sponsor_user_id=sponsor_id,
            )
            session.add(device)
            await session.flush()
            machine = await create_machine_account(
                session,
                machine_name=machine_name,
                device_id=device.device_id,
                sponsor_user_id=sponsor_id,
            )
            device.machine_user_id = machine.id
            await session.commit()
            device_id = device.device_id

        token = await create_access_token(
            user=machine,
            service_name="file-agent",
            description=f"Paired: {machine_name}",
            device_id=device_id,
        )
        return device_id, machine, token

    return _provision


@pytest.fixture
def dataset_create_data():
    """Sample data for dataset creation requests.

    :return: Dictionary with dataset creation data
    :rtype: dict
    """
    return {
        "dataset_name": "New Test Dataset",
        "dataset_description": "Created during integration test",
        "dataset_type": "ANALYSIS",
    }


@pytest.fixture
def dataset_update_data():
    """Sample data for dataset update requests.

    :return: Dictionary with dataset update data
    :rtype: dict
    """
    return {
        "dataset_name": "Updated Test Dataset",
        "dataset_description": "Updated during integration test",
    }


@pytest.fixture
def sample_batch_create_data():
    """Sample data for sample batch creation requests.

    :return: Dictionary with sample batch creation data
    :rtype: dict
    """
    return {
        "sample_batch_name": "New Test Sample Batch",
        "sample_batch_description": "Sample batch for testing",
        "target_collection_ids": [],
    }
