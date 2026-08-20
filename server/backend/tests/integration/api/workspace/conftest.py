"""
Fixtures for workspace hierarchy & ACL integration tests.

Provides:
- Two workspaces (ws_alpha, ws_beta) with different member sets
- An outsider user (valid account, not a member of any workspace)
- Pre-populated datasets, batches, items, and target collections for ACL tests
"""

from datetime import datetime, timezone

import pytest_asyncio

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    User,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


# ---------------------------------------------------------------------------
# Outsider user — valid account, NOT a workspace member
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def outsider_user(async_session_factory, roles):
    """A valid authenticated user who is not a member of any workspace."""
    async with async_session_factory() as session:
        user = User(
            email="outsider@test.com",
            username="outsider_user",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["editor"].role_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def outsider_client(outsider_user, create_jwt_auth_token):
    """AsyncClient authenticated as the outsider user (not a workspace member)."""
    from httpx import ASGITransport, AsyncClient

    from mascope_backend.app.fast import fast

    token = create_jwt_auth_token(outsider_user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Workspace Alpha — all four standard test users are members
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)


@pytest_asyncio.fixture(scope="session")
async def ws_alpha(async_session_factory, test_users):
    """Workspace with all four standard test users as members.

    Returns a dict with ``workspace_id`` and ``members`` (role→user mapping).
    Tests reference ``ws_alpha["workspace_id"]`` for URL construction.
    """
    workspace_id = gen_id()
    async with async_session_factory() as session:
        workspace = Workspace(
            workspace_id=workspace_id,
            workspace_name="Alpha Workspace",
            workspace_description="Primary test workspace",
            workspace_status="active",
            workspace_utc_created=_NOW,
            workspace_utc_modified=_NOW,
        )
        session.add(workspace)

        for role_name, user in test_users.items():
            member = WorkspaceMember(
                workspace_member_id=gen_id(),
                workspace_id=workspace_id,
                user_id=user.id,
                workspace_role=role_name,
                granted_at=_NOW,
                granted_by=user.id,
            )
            session.add(member)

        await session.commit()

    return {"workspace_id": workspace_id, "members": test_users}


# ---------------------------------------------------------------------------
# Workspace Beta — only the owner user is a member
# (used for cross-workspace isolation tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def ws_beta(async_session_factory, test_users):
    """Workspace where only the owner user is a member.

    All other roles (guest/editor/admin) are NOT members, simulating
    cross-workspace isolation.
    """
    workspace_id = gen_id()
    async with async_session_factory() as session:
        workspace = Workspace(
            workspace_id=workspace_id,
            workspace_name="Beta Workspace",
            workspace_description="Cross-workspace isolation test",
            workspace_status="active",
            workspace_utc_created=_NOW,
            workspace_utc_modified=_NOW,
        )
        session.add(workspace)

        # Only owner is a member
        member = WorkspaceMember(
            workspace_member_id=gen_id(),
            workspace_id=workspace_id,
            user_id=test_users["owner"].id,
            workspace_role="owner",
            granted_at=_NOW,
            granted_by=test_users["owner"].id,
        )
        session.add(member)

        await session.commit()

    return {"workspace_id": workspace_id}


# ---------------------------------------------------------------------------
# Dataset in ws_alpha
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def alpha_dataset(async_session_factory, ws_alpha):
    """A dataset inside workspace Alpha."""
    dataset_id = gen_id()
    async with async_session_factory() as session:
        dataset = Dataset(
            dataset_id=dataset_id,
            workspace_id=ws_alpha["workspace_id"],
            dataset_name="Alpha Dataset",
            dataset_description="Dataset in Alpha workspace",
            dataset_type="ANALYSIS",
            dataset_utc_created=_NOW,
        )
        session.add(dataset)
        await session.commit()
    return dataset_id


# ---------------------------------------------------------------------------
# Dataset in ws_beta
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def beta_dataset(async_session_factory, ws_beta):
    """A dataset inside workspace Beta (only owner has access)."""
    dataset_id = gen_id()
    async with async_session_factory() as session:
        dataset = Dataset(
            dataset_id=dataset_id,
            workspace_id=ws_beta["workspace_id"],
            dataset_name="Beta Dataset",
            dataset_description="Dataset in Beta workspace",
            dataset_type="ANALYSIS",
            dataset_utc_created=_NOW,
        )
        session.add(dataset)
        await session.commit()
    return dataset_id


# ---------------------------------------------------------------------------
# Sample batch in alpha_dataset
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def alpha_batch(async_session_factory, alpha_dataset):
    """A sample batch inside the Alpha dataset."""
    batch_id = gen_id()
    async with async_session_factory() as session:
        batch = SampleBatch(
            sample_batch_id=batch_id,
            dataset_id=alpha_dataset,
            sample_batch_name="Alpha Batch",
            sample_batch_utc_created=_NOW,
        )
        session.add(batch)
        await session.commit()
    return batch_id


# ---------------------------------------------------------------------------
# Sample batch in beta_dataset
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def beta_batch(async_session_factory, beta_dataset):
    """A sample batch inside the Beta dataset."""
    batch_id = gen_id()
    async with async_session_factory() as session:
        batch = SampleBatch(
            sample_batch_id=batch_id,
            dataset_id=beta_dataset,
            sample_batch_name="Beta Batch",
            sample_batch_utc_created=_NOW,
        )
        session.add(batch)
        await session.commit()
    return batch_id


# ---------------------------------------------------------------------------
# Sample file (instrument-level, needed by SampleItem FK)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def sample_file(async_session_factory):
    """A sample file record (instrument-level raw data)."""
    file_id = gen_id()
    async with async_session_factory() as session:
        sf = SampleFile(
            sample_file_id=file_id,
            filename=f"test-orbion_{file_id}.raw",
            instrument="test-orbion",
            datetime=_NOW_NAIVE,
            datetime_utc=_NOW,
            length=60.0,
            range={"min": 0, "max": 500},
            polarity="+",
        )
        session.add(sf)
        await session.commit()
    return file_id


# ---------------------------------------------------------------------------
# Sample item in alpha_batch
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def alpha_item(async_session_factory, alpha_batch, sample_file):
    """A sample item inside the Alpha batch."""
    item_id = gen_id()
    async with async_session_factory() as session:
        item = SampleItem(
            sample_item_id=item_id,
            sample_batch_id=alpha_batch,
            sample_file_id=sample_file,
            sample_item_name="Alpha Item",
            sample_item_type="ANALYSIS",
            sample_item_attributes={},
            polarity="+",
            tic=1000.0,
            t0=0.0,
            t1=60.0,
            sample_item_utc_created=_NOW,
        )
        session.add(item)
        await session.commit()
    return item_id


# ---------------------------------------------------------------------------
# Sample file for beta workspace (exposed separately for ACL tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def beta_sample_file(async_session_factory):
    """A sample file record that will be linked to beta_item.

    Exposed as a separate fixture so ACL tests can reference the file ID
    independently of the sample item.
    """
    file_id = gen_id()
    async with async_session_factory() as session:
        sf = SampleFile(
            sample_file_id=file_id,
            filename=f"test-orbion_{file_id}.raw",
            instrument="test-orbion",
            datetime=_NOW_NAIVE,
            datetime_utc=_NOW,
            length=60.0,
            range={"min": 0, "max": 500},
            polarity="+",
        )
        session.add(sf)
        await session.commit()
    return file_id


# ---------------------------------------------------------------------------
# Sample item in beta_batch
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def beta_item(async_session_factory, beta_batch, beta_sample_file):
    """A sample item inside the Beta batch (only owner can access)."""
    item_id = gen_id()
    async with async_session_factory() as session:
        item = SampleItem(
            sample_item_id=item_id,
            sample_batch_id=beta_batch,
            sample_file_id=beta_sample_file,
            sample_item_name="Beta Item",
            sample_item_type="ANALYSIS",
            sample_item_attributes={},
            polarity="+",
            tic=1000.0,
            t0=0.0,
            t1=60.0,
            sample_item_utc_created=_NOW,
        )
        session.add(item)
        await session.commit()
    return item_id


# ---------------------------------------------------------------------------
# Test compound (needed by target collection create API validation)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def test_compound(async_session_factory):
    """A target compound available for target collection tests."""
    compound_id = gen_id()
    async with async_session_factory() as session:
        compound = TargetCompound(
            target_compound_id=compound_id,
            target_compound_name="Test Compound",
            target_compound_formula="C6H12O6",
        )
        session.add(compound)
        await session.commit()
    return compound_id


# ---------------------------------------------------------------------------
# Target collection scoped to ws_alpha
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def alpha_target_collection(async_session_factory, ws_alpha):
    """A target collection scoped to workspace Alpha."""
    tc_id = gen_id()
    async with async_session_factory() as session:
        tc = TargetCollection(
            target_collection_id=tc_id,
            target_collection_name="Alpha Target Collection",
            workspace_id=ws_alpha["workspace_id"],
        )
        session.add(tc)
        await session.commit()
    return tc_id


# ---------------------------------------------------------------------------
# Target collection scoped to ws_beta (only owner has access)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def beta_target_collection(async_session_factory, ws_beta):
    """A target collection scoped to workspace Beta (only owner can access)."""
    tc_id = gen_id()
    async with async_session_factory() as session:
        tc = TargetCollection(
            target_collection_id=tc_id,
            target_collection_name="Beta Target Collection",
            workspace_id=ws_beta["workspace_id"],
        )
        session.add(tc)
        await session.commit()
    return tc_id


# ---------------------------------------------------------------------------
# Global target collection (no workspace)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def global_target_collection(async_session_factory):
    """A target collection with no workspace (global)."""
    tc_id = gen_id()
    async with async_session_factory() as session:
        tc = TargetCollection(
            target_collection_id=tc_id,
            target_collection_name="Global Target Collection",
            workspace_id=None,
        )
        session.add(tc)
        await session.commit()
    return tc_id


# ---------------------------------------------------------------------------
# Association: alpha_target_collection ↔ alpha_batch
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def alpha_collection_in_batch(
    async_session_factory, alpha_target_collection, alpha_batch
):
    """Link alpha_target_collection to alpha_batch for association tests."""
    async with async_session_factory() as session:
        link = TargetCollectionInSampleBatch(
            target_collection_id=alpha_target_collection,
            sample_batch_id=alpha_batch,
        )
        session.add(link)
        await session.commit()
    return (alpha_target_collection, alpha_batch)


# ---------------------------------------------------------------------------
# Orphan sample file — no sample items reference it
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def orphan_sample_file(async_session_factory):
    """A sample file with no sample items — invisible to non-superusers."""
    file_id = gen_id()
    async with async_session_factory() as session:
        sf = SampleFile(
            sample_file_id=file_id,
            filename=f"test-orbion_{file_id}.raw",
            instrument="test-orbion",
            datetime=_NOW_NAIVE,
            datetime_utc=_NOW,
            length=60.0,
            range={"min": 0, "max": 500},
            polarity="+",
        )
        session.add(sf)
        await session.commit()
    return file_id


# ---------------------------------------------------------------------------
# Acquisitions workspace: system workspace for sample file mutations
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def acquisitions_workspace(async_session_factory):
    """A per-instrument system Acquisitions workspace (is_system=True).

    Used by ``check_instrument_workspace_access`` and related helpers
    to enforce per-instrument ACL.
    """
    workspace_id = gen_id()
    async with async_session_factory() as session:
        workspace = Workspace(
            workspace_id=workspace_id,
            workspace_name="Acquisitions test-orbion",
            workspace_description="System acquisitions workspace for test-orbion",
            workspace_status="active",
            is_system=True,
            workspace_utc_created=_NOW,
            workspace_utc_modified=_NOW,
        )
        session.add(workspace)
        await session.commit()
    return workspace_id


# ---------------------------------------------------------------------------
# Users + clients for Acquisitions workspace ACL tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def acq_editor_user(async_session_factory, roles, acquisitions_workspace):
    """A user who is an editor of the Acquisitions workspace."""
    async with async_session_factory() as session:
        user = User(
            email="acq_editor@test.com",
            username="acq_editor_user",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["editor"].role_id,
        )
        session.add(user)
        await session.flush()

        member = WorkspaceMember(
            workspace_member_id=gen_id(),
            workspace_id=acquisitions_workspace,
            user_id=user.id,
            workspace_role="editor",
            granted_at=_NOW,
            granted_by=user.id,
        )
        session.add(member)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture(scope="session")
async def acq_guest_user(async_session_factory, roles, acquisitions_workspace):
    """A user who is a guest of the Acquisitions workspace."""
    async with async_session_factory() as session:
        user = User(
            email="acq_guest@test.com",
            username="acq_guest_user",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["guest"].role_id,
        )
        session.add(user)
        await session.flush()

        member = WorkspaceMember(
            workspace_member_id=gen_id(),
            workspace_id=acquisitions_workspace,
            user_id=user.id,
            workspace_role="guest",
            granted_at=_NOW,
            granted_by=user.id,
        )
        session.add(member)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture(scope="session")
async def acq_admin_user(async_session_factory, roles, acquisitions_workspace):
    """A user who is an admin of the Acquisitions workspace.

    Deliberately holds the *global* ``editor`` role, not ``admin``: the point
    of the fixture is to prove that operations writing to a raw file need a
    role in the instrument's workspace and nothing more. If this user could
    only calibrate by also being a global admin, the gate would be back where
    it started.
    """
    async with async_session_factory() as session:
        user = User(
            email="acq_admin@test.com",
            username="acq_admin_user",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["editor"].role_id,
        )
        session.add(user)
        await session.flush()

        member = WorkspaceMember(
            workspace_member_id=gen_id(),
            workspace_id=acquisitions_workspace,
            user_id=user.id,
            workspace_role="admin",
            granted_at=_NOW,
            granted_by=user.id,
        )
        session.add(member)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def acq_editor_client(acq_editor_user, create_jwt_auth_token):
    """AsyncClient authenticated as an Acquisitions workspace editor."""
    from httpx import ASGITransport, AsyncClient

    from mascope_backend.app.fast import fast

    token = create_jwt_auth_token(acq_editor_user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def acq_guest_client(acq_guest_user, create_jwt_auth_token):
    """AsyncClient authenticated as an Acquisitions workspace guest."""
    from httpx import ASGITransport, AsyncClient

    from mascope_backend.app.fast import fast

    token = create_jwt_auth_token(acq_guest_user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def acq_admin_client(acq_admin_user, create_jwt_auth_token):
    """AsyncClient authenticated as an Acquisitions workspace admin."""
    from httpx import ASGITransport, AsyncClient

    from mascope_backend.app.fast import fast

    token = create_jwt_auth_token(acq_admin_user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    ) as client:
        yield client
