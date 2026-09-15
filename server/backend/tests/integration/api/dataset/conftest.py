"""
Fixtures for dataset integration tests.

Provides test workspaces with all test users added as members,
so that workspace-scoped dataset routes pass ACL checks.
"""

from datetime import datetime, timezone

import pytest_asyncio

from mascope_backend.db import Workspace, WorkspaceMember
from mascope_backend.db.id import gen_id


async def _create_workspace_with_members(async_session_factory, test_users, name):
    """Create a workspace named `name` and add every test user as a member.

    - owner  → workspace role "owner"
    - admin  → workspace role "admin"
    - editor → workspace role "editor"
    - guest  → workspace role "guest"

    :param async_session_factory: Factory for creating database sessions
    :param test_users: Mapping of role name to User
    :param name: Workspace name (unique - workspace names are case-insensitively
                 unique across the database)
    :type name: str
    :return: The new workspace's ID
    :rtype: str
    """
    workspace_id = gen_id()
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        workspace = Workspace(
            workspace_id=workspace_id,
            workspace_name=name,
            workspace_description="Workspace for integration tests",
            workspace_status="active",
            workspace_utc_created=now,
            workspace_utc_modified=now,
        )
        session.add(workspace)

        for role_name, user in test_users.items():
            member = WorkspaceMember(
                workspace_member_id=gen_id(),
                workspace_id=workspace_id,
                user_id=user.id,
                workspace_role=role_name,
                granted_at=now,
                granted_by=user.id,
            )
            session.add(member)

        await session.commit()

    return workspace_id


@pytest_asyncio.fixture(scope="session")
async def test_workspace(async_session_factory, test_users):
    """Create a workspace and add all test users as members.

    Returns the workspace_id string, which tests embed in URL paths.
    """
    return await _create_workspace_with_members(
        async_session_factory, test_users, "Test Workspace"
    )


@pytest_asyncio.fixture(scope="session")
async def second_test_workspace(async_session_factory, test_users):
    """A second workspace with the same members as `test_workspace`.

    Dataset names are unique per workspace, not globally, so the duplicate
    name tests need a second workspace to prove the scope. Moving a dataset
    also requires editor rights on the target, hence the same member seeding.

    Returns the workspace_id string, which tests embed in URL paths.
    """
    return await _create_workspace_with_members(
        async_session_factory, test_users, "Second Test Workspace"
    )
