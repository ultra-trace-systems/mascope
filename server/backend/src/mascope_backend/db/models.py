"""
SQLAlchemy ORM models for the Mascope application.

This module defines all database models including user management, datasets,
samples, targets, and analysis matches.
"""

from datetime import datetime as dt
from datetime import timezone
from typing import Optional

from fastapi_users.db import (
    SQLAlchemyBaseUserTable,
)
from fastapi_users_db_sqlalchemy.access_token import (
    SQLAlchemyBaseAccessTokenTable,
)
from sqlalchemy import (
    JSON,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship
from sqlalchemy.sql.schema import CheckConstraint

from mascope_backend.api.models.dataset.config import dataset_config
from mascope_backend.api.models.sample.batches.config import sample_batch_config
from mascope_backend.api.models.sample.items.config import sample_item_config
from mascope_backend.api.models.target.collections.config import (
    target_collection_config,
)
from mascope_backend.runtime import runtime


# Naming convention for all constraints and indexes.
# Provides predictable names in Alembic migrations (required for DROP/ALTER operations).
# Convention:
#   ix_ : indexes (auto-generated via index=True or Index())
#   uq_ : unique constraints
#   ck_ : check constraints  (ck_<table>_<constraint_name>)
#   fk_ : foreign keys
#   pk_ : primary keys
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class BaseMixin(object):
    """Mixin providing common utility methods for all models."""

    def to_dict(
        self,
    ):
        data = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return data


Base = declarative_base(
    cls=BaseMixin,
    metadata=MetaData(naming_convention=NAMING_CONVENTION),
)


# ---------------------------------------------------------------------------
# Workspace & membership
# ---------------------------------------------------------------------------


class Workspace(Base):
    """Workspace is the primary access-control and data-sharing boundary.

    Contains datasets. User access is managed via WorkspaceMember.
    """

    __tablename__ = "workspace"

    workspace_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    workspace_name: Mapped[str] = mapped_column(String(256))
    workspace_description: Mapped[Optional[str]] = mapped_column(Text)
    workspace_status: Mapped[str] = mapped_column(
        String(20), server_default=text("'active'")
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )

    workspace_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    workspace_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    __table_args__ = (
        Index(
            "ix_workspace_name_ci",
            func.lower(workspace_name),
            unique=True,
        ),
    )

    # Relationships
    datasets = relationship(
        "Dataset",
        back_populates="workspace",
        cascade="all, delete, delete-orphan",
    )
    members = relationship(
        "WorkspaceMember",
        back_populates="workspace",
        cascade="all, delete, delete-orphan",
    )
    target_collections = relationship(
        "TargetCollection",
        back_populates="workspace",
    )


class WorkspaceMember(Base):
    """Junction table granting a user access to a workspace with a specific role.

    workspace_role values: 'guest', 'editor', 'admin', 'owner'.
    """

    __tablename__ = "workspace_member"

    workspace_member_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("workspace.workspace_id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_role: Mapped[str] = mapped_column(
        String(20), server_default=text("'guest'")
    )
    granted_at: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: dt.now(timezone.utc),
        nullable=False,
    )
    granted_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member_pair"),
    )

    # Relationships
    workspace = relationship("Workspace", back_populates="members")
    user = relationship(
        "User", foreign_keys=[user_id], back_populates="workspace_memberships"
    )
    granted_by_user = relationship("User", foreign_keys=[granted_by])


# ---------------------------------------------------------------------------
# Auth / Users / Roles
# ---------------------------------------------------------------------------


class User(SQLAlchemyBaseUserTable[int], Base):
    """User authentication and authorization model."""

    __tablename__ = "user"

    # User table fields required for FastAPI Users.
    # Kept unchanged for easier compatibility.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(
        String(length=320), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(length=1024), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Custom fields
    username: Mapped[str] = mapped_column(
        String(length=100), unique=True, nullable=False
    )
    # Whether this account is a person or an instrument agent's machine identity
    # ("person" | "machine", see mascope_backend.accounts). A machine account
    # never signs in interactively, has no usable password, and is exempt from
    # the human-only password-change and MFA requirements; it is created by
    # pairing approval and vouched for by a sponsor recorded on its device.
    account_type: Mapped[str] = mapped_column(
        String(16), default="person", server_default=text("'person'"), nullable=False
    )
    role_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("role.role_id", ondelete="SET NULL"), nullable=True
    )
    registered_at: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=True), default=lambda: dt.now(timezone.utc), nullable=False
    )
    # While True the account still authenticates, but every cookie-authenticated
    # route is closed to it until it stores a password that passes the current
    # policy. Armed by any password someone else writes (see UserManager._update)
    # and by the owner's deployment-wide sweep; cleared only by
    # UserManager.set_own_password. Deliberately absent from the user API write
    # schemas - see the field whitelists in api/new/users/schemas.py.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Why the change is pending: "policy", "reset" or "new_account". Drives the
    # wording on the password screen. NULL when nothing is pending.
    password_change_reason: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    # When this account's password was last written. NULL means it has not been
    # written since this column was introduced, which is also the selector for
    # "never set under the current policy".
    password_changed_at: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # TOTP seed, encrypted at rest with the deployment's MFA key (see
    # api/new/auth/mfa/secrets.py). NULL when the account has never begun
    # enrollment. Present but with mfa_enabled False means enrollment was
    # started and never confirmed, which must not gate a login.
    mfa_secret: Mapped[Optional[str]] = mapped_column(String(length=512), nullable=True)
    # Whether a confirmed second factor gates this account's interactive logins.
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    mfa_confirmed_at: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Counter of the last accepted TOTP code. Verification tolerates clock drift
    # by accepting a window of counters, which leaves a code usable for about 90
    # seconds - long enough to replay if it is observed. Accepting only counters
    # strictly greater than this one closes that window at first use.
    mfa_last_timestep: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Relationships
    role = relationship("Role", back_populates="user")
    access_token = relationship(
        "AccessToken", back_populates="user", cascade="all, delete, delete-orphan"
    )
    recovery_code = relationship(
        "UserRecoveryCode", back_populates="user", cascade="all, delete, delete-orphan"
    )
    workspace_memberships = relationship(
        "WorkspaceMember",
        back_populates="user",
        foreign_keys="WorkspaceMember.user_id",
        cascade="all, delete, delete-orphan",
    )

    @classmethod
    async def count_other_owners(cls, session, current_user_id: int) -> int:
        """
        Count number of owner users excluding specified user.

        :param session: SQLAlchemy session
        :param current_user_id: User ID to exclude from count
        :return: Count of other owner users
        """
        from mascope_backend.api.new.auth.config import auth_settings

        query = (
            select(func.count())
            .select_from(User)
            .where(
                User.role_id == auth_settings.ROLE_ACCESS_LEVELS["owner"],
                User.id != current_user_id,
            )
        )
        result = await session.execute(query)
        return result.scalar()


class Role(Base):
    """User role and permissions model."""

    __tablename__ = "role"

    role_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_name: Mapped[str] = mapped_column(String(50), unique=True)
    permissions: Mapped[Optional[dict]] = mapped_column(JSON)

    # Relationships
    user = relationship("User", back_populates="role")


class AccessToken(SQLAlchemyBaseAccessTokenTable[int], Base):
    """
    AccessToken model for storing access tokens linked to user accounts.
    Supports different services for authentication.
    """

    __tablename__ = "access_token"

    token: Mapped[str] = mapped_column(String(length=43), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    service_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Optional label, e.g. the paired machine's hostname (set by device pairing)
    description: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # The paired machine holding this token. NULL for personal tokens
    # (mascope_sdk) and for agent tokens issued before the device registry;
    # the require_device_tokens deployment flag refuses the latter.
    device_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_device.device_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=True),
        index=True,
        nullable=False,
        default=lambda: dt.now(timezone.utc),
    )

    # Relationships
    user = relationship("User", back_populates="access_token")
    device = relationship("AgentDevice", back_populates="access_tokens")

    @classmethod
    async def clean_invalid_tokens(cls, session) -> int:
        """
        Clean up tokens with NULL/invalid service names that are not allowed in
        AccessTokenConfig.

        :param session: SQLAlchemy async session
        :type session: AsyncSession
        :return: Number of deleted tokens
        :rtype: int
        """
        from mascope_backend.api.new.auth.config import auth_settings

        allowed_services = auth_settings.access_token.ALLOWED_SERVICES

        # Find tokens with NULL service names or invalid service names
        stmt = select(cls).where(
            or_(cls.service_name.is_(None), cls.service_name.notin_(allowed_services))
        )

        result = await session.execute(stmt)
        invalid_tokens = result.scalars().all()

        if invalid_tokens:
            for token in invalid_tokens:
                await session.delete(token)
            await session.commit()

        # Return number of deleted tokens
        return len(invalid_tokens)


class UserRecoveryCode(Base):
    """
    Single-use codes that stand in for a TOTP code when the authenticator is
    lost.

    Stored as a plain SHA-256 digest rather than a password hash: these are
    high-entropy values the server generates, so there is no brute-force margin
    a slow KDF would buy, and a digest makes redemption an indexed lookup
    instead of a hash comparison against every unused row.

    Rows are kept after redemption (``used_at`` set) so a code cannot be
    re-issued into the same slot, and so an operator can see that recovery
    happened.
    """

    __tablename__ = "user_recovery_code"
    __table_args__ = (
        UniqueConstraint("user_id", "code_hash", name="uq_recovery_code_user_hash"),
        Index("ix_user_recovery_code_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(length=64), nullable=False)
    created_at: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: dt.now(timezone.utc),
    )
    used_at: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    user = relationship("User", back_populates="recovery_code")


class AgentDevice(Base):
    """
    A machine paired to hold an agent credential (e.g. the File Agent on an
    instrument PC).

    Each row is one paired machine: created when a pairing is approved, named
    after the hostname the agent reported (renameable), and sponsored by the
    approving user. The sponsor vouches for the machine; ``ON DELETE SET
    NULL`` keeps the device (and the attribution of everything it uploaded)
    when the sponsor's account is removed, mirroring
    ``workspace_member.granted_by``.

    Revocation deletes the device's access tokens and sets ``revoked_at``;
    the row itself is kept so uploads attributed to the device stay
    explainable. Deleting a device row cascades to its tokens (fail closed:
    a credential must never outlive its device record).
    """

    __tablename__ = "agent_device"

    device_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    service_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # The person who approved the pairing and vouches for this machine.
    sponsor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The machine account this device authenticates as (the subject of its
    # tokens). SET NULL rather than CASCADE: deleting the account must not
    # erase the device row, which is attribution history.
    machine_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: dt.now(timezone.utc),
    )
    # Updated on authenticated use, throttled in SQL so a busy agent costs one
    # write per throttle window, not one per request.
    last_seen_at: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships. Two FKs point at user (sponsor, machine account), so each
    # names its own column.
    sponsor = relationship("User", foreign_keys=[sponsor_user_id])
    machine_user = relationship("User", foreign_keys=[machine_user_id])
    # passive_deletes=True: the DB's ON DELETE CASCADE removes the tokens.
    access_tokens = relationship(
        "AccessToken",
        back_populates="device",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )


class Dataset(Base):
    """Dataset container for organizing sample batches. Belongs to a Workspace."""

    __tablename__ = "dataset"

    dataset_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("workspace.workspace_id", ondelete="CASCADE"),
        index=True,
    )
    dataset_name: Mapped[str] = mapped_column(String(256))
    dataset_description: Mapped[Optional[str]] = mapped_column(Text)
    dataset_type: Mapped[str] = mapped_column(
        String(64),
        server_default=text(f"'{dataset_config.DEFAULT_DATASET_TYPE}'"),
    )
    locked: Mapped[int] = mapped_column(
        Integer,
        server_default=text(f"'{dataset_config.DEFAULT_LOCKED_STATUS}'"),
    )
    instrument: Mapped[Optional[str]] = mapped_column(String(64))
    icon: Mapped[Optional[dict]] = mapped_column(JSON)
    dataset_utc_created: Mapped[Optional[dt]] = mapped_column(TIMESTAMP(timezone=True))
    dataset_utc_modified: Mapped[Optional[dt]] = mapped_column(TIMESTAMP(timezone=True))

    # Relationships
    workspace = relationship("Workspace", back_populates="datasets")
    sample_batch = relationship(
        "SampleBatch",
        back_populates="dataset",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # ACQUISITION datasets are auto-created per (workspace, instrument,
        # year) by a read-then-write get-or-create that can race under
        # concurrent ingest; constrain the natural key so the race fails
        # loudly (and is recovered in get_acquisition_dataset) instead of
        # inserting duplicates. Partial: the other dataset types are keyed on
        # (workspace, name) instead, by the complementary index below.
        Index(
            "uq_dataset_acquisition_natural_key",
            "workspace_id",
            "instrument",
            "dataset_name",
            unique=True,
            postgresql_where=text("dataset_type = 'ACQUISITION'"),
        ),
        # A dataset name is unique within its workspace, compared under the
        # canonical key `lower(btrim(dataset_name))` - two datasets differing
        # only in case or in surrounding padding read as one entry in the
        # workspace list, which is the bug. Backs the `_assert_name_available`
        # check in the dataset controller, whose read-then-write cannot close
        # the race on its own.
        #
        # That key is THE definition of "same name" and it is evaluated by
        # Postgres in all three places that need it: here, in
        # `_assert_name_available`, and in the migration that introduced the
        # index. Python's `str.lower()` is not the same function as Postgres
        # `lower()` (they disagree on 35 BMP codepoints, and Python alone
        # applies the Greek final-sigma rule), so a Python-side key would let
        # the check pass on a name the index then rejects - a 500 for input
        # the user cannot see anything wrong with.
        #
        # Partial, and it has to stay partial. ACQUISITION datasets are named
        # after the calendar year and auto-created per (workspace, instrument,
        # year) by get_acquisition_dataset, which recovers from a duplicate-key
        # insert only by re-finding an ACQUISITION row. Covering every type
        # here would let a user-created dataset named e.g. "2027" in an
        # instrument workspace turn that year's rollover into an
        # IntegrityError nothing can recover from, stopping auto-processing
        # for the instrument.
        Index(
            "uq_dataset_workspace_name_ci",
            "workspace_id",
            func.lower(func.btrim(dataset_name)),
            unique=True,
            postgresql_where=text("dataset_type <> 'ACQUISITION'"),
        ),
    )


class SampleBatch(Base):
    """Sample batch grouping related samples for analysis."""

    __tablename__ = "sample_batch"

    sample_batch_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("dataset.dataset_id", ondelete="CASCADE"),
    )
    sample_batch_name: Mapped[str] = mapped_column(String(256))
    sample_batch_description: Mapped[Optional[str]] = mapped_column(Text)
    sample_batch_type: Mapped[str] = mapped_column(
        String(64),
        server_default=text(f"'{sample_batch_config.DEFAULT_SAMPLE_BATCH_TYPE}'"),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        server_default=text(f"'{sample_batch_config.DEFAULT_SAMPLE_BATCH_STATUS}'"),
    )
    locked: Mapped[int] = mapped_column(
        Integer,
        server_default=text(f"'{sample_batch_config.DEFAULT_LOCKED_STATUS}'"),
    )
    polarity: Mapped[str] = mapped_column(
        String(4),
        server_default=text(f"'{sample_batch_config.ANALYSIS_POLARITY}'"),
    )
    sample_batch_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    sample_batch_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    dataset = relationship("Dataset", back_populates="sample_batch")
    sample_items = relationship(
        "SampleItem",
        back_populates="sample_batch",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    target_collection = relationship(
        "TargetCollectionInSampleBatch",
        back_populates="sample_batch",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    batch_peak = relationship(
        "BatchPeak",
        back_populates="sample_batch",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # Daily ACQUISITION batches are auto-created per (dataset, day,
        # ionization mode) by a read-then-write get-or-create that races under
        # concurrent ingest - three files of one watcher scan share a day and a
        # mode, so they resolve to one batch name. Constrain the natural key so
        # the race fails loudly and is recovered in
        # get_or_create_acquisition_batch instead of inserting duplicates that
        # split the day's samples across two batches.
        #
        # `polarity` is in the key because the name alone does not identify the
        # mode: it embeds `ionization_mode_name`, which carries no uniqueness
        # (only `ionization_mode_token` does), so an admin who names the
        # positive and negative variant alike renders one name for both. Two
        # modes sharing a name AND a polarity still collapse onto one batch -
        # separating those needs the mode id on the batch.
        #
        # Partial: ANALYSIS batches are user-named and have no such invariant.
        Index(
            "uq_sample_batch_acquisition_natural_key",
            "dataset_id",
            "sample_batch_name",
            "polarity",
            unique=True,
            postgresql_where=text("sample_batch_type = 'ACQUISITION'"),
        ),
    )


@event.listens_for(SampleBatch, "after_insert")
@event.listens_for(SampleBatch, "after_update")
@event.listens_for(SampleBatch, "after_delete")
def update_dataset_on_sample_batch_change(mapper, connection, target):
    """Update Dataset timestamp when SampleBatch changes"""
    if target.dataset_id:
        stmt = (
            update(Dataset)
            .where(Dataset.dataset_id == target.dataset_id)
            .values(dataset_utc_modified=dt.now(timezone.utc))
        )
        connection.execute(stmt)
        runtime.logger.debug(
            f"Updated Dataset '{target.dataset_id}' timestamp due to SampleBatch change"
        )


@event.listens_for(SampleBatch, "before_update")
def update_modified_timestamp(mapper, connection, target):
    """Automatically update modification timestamp when SampleBatch is updated."""
    target.sample_batch_utc_modified = dt.now(timezone.utc)


class SampleFile(Base):
    """
    Represents raw acquisition files.

    Each sample file corresponds to a single data file in the filestore.
    Contains metadata about the instrument, calibration, and measurement parameters.

    Datetime columns:
      - datetime:     Instrument local time, stored as TIMESTAMP WITHOUT TIME ZONE.
                      Preserves the literal value recorded by the instrument.
      - datetime_utc: UTC equivalent, stored as TIMESTAMP WITH TIME ZONE.
                      Use this for all time-based calculations and comparisons.
    """

    __tablename__ = "sample_file"

    sample_file_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    instrument_function_id: Mapped[Optional[str]] = mapped_column(
        String(32),
        ForeignKey("instrument_function.instrument_function_id", ondelete="SET NULL"),
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(256), unique=True)
    instrument: Mapped[str] = mapped_column(String(64))
    method_file: Mapped[Optional[str]] = mapped_column(String(512))
    datetime: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,  # stored literal local time
    )
    datetime_utc: Mapped[dt] = mapped_column(TIMESTAMP(timezone=True))  # stored as UTC
    length: Mapped[float] = mapped_column(Float)
    range: Mapped[list] = mapped_column(JSON)
    mz_calibration: Mapped[Optional[dict]] = mapped_column(JSON)
    polarity: Mapped[str] = mapped_column(String(4))
    # Attribution, recorded at creation. NULL on rows that predate these
    # columns; SET NULL keeps the file when the account or device goes away.
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_device_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("agent_device.device_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # How datetime_utc was derived from the instrument-local datetime:
    # the IANA zone the uploading agent reported (NULL when none was sent),
    # and which source determined the applied offset - "file" (an offset
    # embedded in the raw file), "agent" (the reported zone), or "guess"
    # (the converter host's own clock, the legacy fallback).
    acquisition_timezone: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    utc_offset_source: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    # Relationships
    instrument_function = relationship(
        "InstrumentFunction", back_populates="sample_file"
    )
    uploaded_by_user = relationship("User", foreign_keys=[uploaded_by_user_id])
    uploaded_by_device = relationship(
        "AgentDevice", foreign_keys=[uploaded_by_device_id]
    )
    sample_items = relationship(
        "SampleItem", back_populates="sample_file", cascade="all, delete, delete-orphan"
    )


class SampleItem(Base):
    """
    Represents a processed sample derived from a sample file.

    Each sample_item is a time-windowed segment of a sample_file that has been
    analyzed and matched against target collections. Multiple sample_items can
    be created from a single sample_file.
    """

    __tablename__ = "sample_item"

    sample_item_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    sample_batch_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_batch.sample_batch_id", ondelete="CASCADE"),
        index=True,
    )
    sample_file_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_file.sample_file_id", ondelete="CASCADE"),
        index=True,
    )
    sample_item_name: Mapped[str] = mapped_column(String(256))
    sample_item_type: Mapped[str] = mapped_column(String(64))
    locked: Mapped[int] = mapped_column(
        Integer,
        server_default=text(f"'{sample_item_config.DEFAULT_LOCKED_STATUS}'"),
    )
    sample_item_attributes: Mapped[Optional[dict]] = mapped_column(JSON)
    filter_id: Mapped[Optional[str]] = mapped_column(String(6))
    tic: Mapped[Optional[float]] = mapped_column(Float)
    polarity: Mapped[Optional[str]] = mapped_column(String(1))
    ionization_mode_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey(
            "ionization_mode.ionization_mode_id",
            ondelete="SET NULL",
        ),
    )
    t0: Mapped[Optional[float]] = mapped_column(Float)
    t1: Mapped[Optional[float]] = mapped_column(Float)
    sample_item_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    sample_item_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_batch = relationship("SampleBatch", back_populates="sample_items")
    sample_file = relationship("SampleFile", back_populates="sample_items")
    # passive_deletes=True: rely on the DB's ON DELETE CASCADE (defined on every
    # match_*.sample_item_id FK) instead of loading every child row into the ORM
    # to delete it. Essential for deleting samples/batches/datasets with large
    # match tables (match_isotope) without timing out.
    match_sample = relationship(
        "MatchSample",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    match_collection = relationship(
        "MatchCollection",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    match_compound = relationship(
        "MatchCompound",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    match_ion = relationship(
        "MatchIon",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    match_isotope = relationship(
        "MatchIsotope",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    match_rating = relationship(
        "MatchRating",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    peak_assignment_run = relationship(
        "PeakAssignmentRun",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    peak_assignment = relationship(
        "PeakAssignment",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )
    batch_peak_occurrence = relationship(
        "BatchPeakOccurrence",
        back_populates="sample_item",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )


@event.listens_for(SampleItem, "after_update")
def update_sample_batch_on_sample_item_change(mapper, connection, target):
    """Update SampleBatch timestamp when SampleItem changes."""
    if target.sample_batch_id:
        stmt = (
            update(SampleBatch)
            .where(SampleBatch.sample_batch_id == target.sample_batch_id)
            .values(sample_batch_utc_modified=dt.now(timezone.utc))
        )
        connection.execute(stmt)
        runtime.logger.debug(
            f"Updated SampleBatch '{target.sample_batch_id}' "
            "timestamp due to SampleItem change."
        )


class TargetCollection(Base):
    """Collection of target compounds for analysis."""

    __tablename__ = "target_collection"

    target_collection_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_collection_name: Mapped[str] = mapped_column(String(256))
    target_collection_description: Mapped[Optional[str]] = mapped_column(Text)
    target_collection_type: Mapped[str] = mapped_column(
        String(64),
        server_default=text(
            f"'{target_collection_config.DEFAULT_TARGET_COLLECTION_TYPE}'"
        ),
    )
    workspace_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("workspace.workspace_id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Relationships
    workspace = relationship("Workspace", back_populates="target_collections")
    sample_batch = relationship(
        "TargetCollectionInSampleBatch",
        back_populates="target_collection",
        cascade="all, delete, delete-orphan",
    )
    target_compound = relationship(
        "TargetCompoundInTargetCollection",
        back_populates="target_collection",
        cascade="all, delete, delete-orphan",
    )
    match_collection = relationship(
        "MatchCollection",
        back_populates="target_collection",
        cascade="all, delete, delete-orphan",
    )
    calibration_ionization_modes = relationship(
        "IonizationMode",
        foreign_keys="IonizationMode.calibration_collection_id",
        back_populates="calibration_collection",
    )
    diagnostic_ionization_modes = relationship(
        "IonizationMode",
        foreign_keys="IonizationMode.diagnostic_collection_id",
        back_populates="diagnostic_collection",
    )


class TargetCollectionInSampleBatch(Base):
    """Junction table linking target collections to sample batches."""

    __tablename__ = "target_collection_in_sample_batch"

    target_collection_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_collection.target_collection_id", ondelete="CASCADE"),
        primary_key=True,
    )
    sample_batch_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_batch.sample_batch_id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Relationships
    target_collection = relationship("TargetCollection", back_populates="sample_batch")
    sample_batch = relationship("SampleBatch", back_populates="target_collection")


class TargetCompoundInTargetCollection(Base):
    """Junction table linking target compounds to target collections."""

    __tablename__ = "target_compound_in_target_collection"

    target_compound_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_compound.target_compound_id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_collection_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_collection.target_collection_id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Relationships
    target_collection = relationship(
        "TargetCollection", back_populates="target_compound"
    )
    target_compound = relationship("TargetCompound", back_populates="target_collection")


class TargetCompound(Base):
    """Target compound definition."""

    __tablename__ = "target_compound"

    # Mass-based compounds (a bare number instead of a composition) were retired
    # with the molmass fork - ions and isotopes are computed from the formula, so
    # a mass alone can never yield an isotope pattern. The Pydantic models
    # already refuse them, but only for requests arriving over HTTP: a db
    # script, hand-written SQL or a restored dump bypasses them entirely, so
    # the rule lives here too. Mirrors _NUMERIC_MASS in the request model; note
    # it is a regex and not a float() parse because "NaN" is sodium nitride, a
    # formula that must keep working. Added NOT VALID by migration
    # e2d4a91c7b06, so legacy rows survive an upgrade while no new one lands.
    # Bracket isotope notation is rejected alongside it: isotopes are always
    # generated from the formula, so pinning one on the compound asks for a
    # monoisotopic species where a full pattern gets computed anyway. Caret
    # isotopes ('^N') stay allowed - those name a labelled reagent, a different
    # substance, and are in active production use.
    __table_args__ = (
        CheckConstraint(
            r"target_compound_formula !~ "
            r"'^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$'",
            name="formula_not_a_mass",
        ),
        CheckConstraint(
            r"target_compound_formula !~ '\[[0-9]+[A-Za-z]|[A-Za-z]\[[0-9]+\]'",
            name="formula_no_bracket_isotope",
        ),
    )

    target_compound_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_compound_name: Mapped[Optional[str]] = mapped_column(Text)
    target_compound_formula: Mapped[str] = mapped_column(String(256))
    cas_number: Mapped[Optional[str]] = mapped_column(String(12))

    # Relationships
    target_collection = relationship(
        "TargetCompoundInTargetCollection",
        back_populates="target_compound",
        cascade="all, delete, delete-orphan",
    )
    target_ion = relationship(
        "TargetIon",
        back_populates="target_compound",
        cascade="all, delete, delete-orphan",
    )
    match_compound = relationship(
        "MatchCompound",
        back_populates="target_compound",
        cascade="all, delete, delete-orphan",
    )


class TargetIon(Base):
    """Target ion derived from target compound and ionization mechanism."""

    __tablename__ = "target_ion"

    target_ion_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_compound_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_compound.target_compound_id", ondelete="CASCADE"),
    )
    ionization_mechanism_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("ionization_mechanism.ionization_mechanism_id", ondelete="CASCADE"),
        index=True,
    )
    target_ion_formula: Mapped[str] = mapped_column(String(256))
    filter_params: Mapped[Optional[dict]] = mapped_column(JSON)

    # Relationships
    target_compound = relationship("TargetCompound", back_populates="target_ion")
    ionization_mechanism = relationship(
        "IonizationMechanism", back_populates="target_ion"
    )
    target_isotope = relationship(
        "TargetIsotope",
        back_populates="target_ion",
        cascade="all, delete, delete-orphan",
    )
    match_ion = relationship(
        "MatchIon",
        back_populates="target_ion",
        cascade="all, delete, delete-orphan",
    )
    match_rating = relationship(
        "MatchRating",
        back_populates="target_ion",
        cascade="all, delete, delete-orphan",
    )


class IonizationMechanism(Base):
    """Ionization mechanism table."""

    __tablename__ = "ionization_mechanism"

    ionization_mechanism_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    ionization_mechanism_polarity: Mapped[str] = mapped_column(String(1))
    ionization_mechanism: Mapped[str] = mapped_column(String(256), unique=True)

    # Relationships
    target_ion = relationship(
        "TargetIon",
        back_populates="ionization_mechanism",
        cascade="all, delete, delete-orphan",
    )


class IonizationMode(Base):
    """Ionization mode configuration."""

    __tablename__ = "ionization_mode"

    ionization_mode_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    ionization_mode_name: Mapped[str] = mapped_column(String(256))
    ionization_mode_token: Mapped[Optional[str]] = mapped_column(
        String(256), unique=True
    )
    ionization_mode_polarity: Mapped[str] = mapped_column(String(1))
    ionization_mechanism_ids: Mapped[list[str]] = mapped_column(JSON)
    calibration_collection_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("target_collection.target_collection_id", ondelete="SET NULL"),
    )
    diagnostic_collection_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("target_collection.target_collection_id", ondelete="SET NULL"),
    )

    # Relationships
    calibration_collection = relationship(
        "TargetCollection",
        foreign_keys=[calibration_collection_id],
        back_populates="calibration_ionization_modes",
    )
    diagnostic_collection = relationship(
        "TargetCollection",
        foreign_keys=[diagnostic_collection_id],
        back_populates="diagnostic_ionization_modes",
    )


class TargetIsotope(Base):
    """Target isotope table."""

    __tablename__ = "target_isotope"

    target_isotope_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_ion_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey(
            "target_ion.target_ion_id",
            ondelete="CASCADE",
        ),
        index=True,
    )
    target_isotope_formula: Mapped[str] = mapped_column(
        String(4096)
    )  # lower length limit #1360 https://github.com/ultra-trace-systems/mascope/issues/1360
    mz: Mapped[float] = mapped_column(Float)
    relative_abundance: Mapped[float] = mapped_column(Float)
    resolution: Mapped[str] = mapped_column(String(8))

    # Relationships
    target_ion = relationship("TargetIon", back_populates="target_isotope")
    match_isotope = relationship(
        "MatchIsotope",
        back_populates="target_isotope",
        cascade="all, delete, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "relative_abundance >= 0 AND relative_abundance <= 1",
            name="relative_abundance_range",
        ),
    )


class MatchSample(Base):
    """Sample-level match result."""

    __tablename__ = "match_sample"

    match_sample_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    match_score: Mapped[float] = mapped_column(Float)
    match_category: Mapped[int] = mapped_column(Integer)
    sample_peak_intensity_sum: Mapped[float] = mapped_column(Float)
    match_sample_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    match_sample_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_item = relationship("SampleItem", back_populates="match_sample")

    __table_args__ = (
        CheckConstraint("match_score BETWEEN 0 AND 1", name="match_score_range"),
        CheckConstraint("match_category BETWEEN 0 AND 2", name="match_category_range"),
        # One aggregate row per sample; concurrent aggregations must never
        # duplicate it (create_match_samples reads with one_or_none)
        UniqueConstraint("sample_item_id", name="uq_match_sample_sample_item"),
    )


class MatchCollection(Base):
    """Collection-level match result."""

    __tablename__ = "match_collection"

    match_collection_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    target_collection_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_collection.target_collection_id", ondelete="CASCADE"),
        index=True,
    )
    match_score: Mapped[float] = mapped_column(Float)
    match_category: Mapped[int] = mapped_column(Integer)
    sample_peak_intensity_sum: Mapped[float] = mapped_column(Float)
    match_collection_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    match_collection_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_item = relationship("SampleItem", back_populates="match_collection")
    target_collection = relationship(
        "TargetCollection", back_populates="match_collection"
    )

    # Indexes
    __table_args__ = (
        CheckConstraint("match_score BETWEEN 0 AND 1", name="match_score_range"),
        CheckConstraint("match_category BETWEEN 0 AND 2", name="match_category_range"),
        # Natural key of the aggregate; guards concurrent upserts
        UniqueConstraint(
            "sample_item_id",
            "target_collection_id",
            name="uq_match_collection_sample_item_target_collection",
        ),
    )


class MatchCompound(Base):
    """Compound-level match result."""

    __tablename__ = "match_compound"

    match_compound_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    target_compound_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_compound.target_compound_id", ondelete="CASCADE"),
        index=True,
    )
    match_score: Mapped[float] = mapped_column(Float)
    match_category: Mapped[int] = mapped_column(Integer)
    sample_peak_intensity_sum: Mapped[float] = mapped_column(Float)
    match_compound_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    match_compound_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_item = relationship("SampleItem", back_populates="match_compound")
    target_compound = relationship("TargetCompound", back_populates="match_compound")

    # Indexes
    __table_args__ = (
        CheckConstraint("match_score BETWEEN 0 AND 1", name="match_score_range"),
        CheckConstraint("match_category BETWEEN 0 AND 2", name="match_category_range"),
        # Natural key of the aggregate; guards concurrent upserts
        UniqueConstraint(
            "sample_item_id",
            "target_compound_id",
            name="uq_match_compound_sample_item_target_compound",
        ),
    )


class MatchIon(Base):
    """Ion-level match result."""

    __tablename__ = "match_ion"

    match_ion_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    target_ion_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_ion.target_ion_id", ondelete="CASCADE"),
        index=True,
    )
    match_score: Mapped[float] = mapped_column(Float)
    match_category: Mapped[int] = mapped_column(Integer)
    sample_peak_intensity_sum: Mapped[float] = mapped_column(Float)
    match_ion_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    match_ion_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_item = relationship("SampleItem", back_populates="match_ion")
    target_ion = relationship("TargetIon", back_populates="match_ion")

    __table_args__ = (
        CheckConstraint("match_score BETWEEN 0 AND 1", name="match_score_range"),
        CheckConstraint("match_category BETWEEN 0 AND 2", name="match_category_range"),
        # Ordered (backward) scan for best-score-per-ion aggregation
        # (batch match records)
        Index(
            "ix_match_ion_target_ion_id_match_score",
            "target_ion_id",
            "match_score",
        ),
        # Natural key of the aggregate; guards concurrent upserts
        UniqueConstraint(
            "sample_item_id",
            "target_ion_id",
            name="uq_match_ion_sample_item_target_ion",
        ),
    )


class MatchRating(Base):
    """User rating for match quality."""

    __tablename__ = "match_rating"

    match_rating_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    target_ion_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_ion.target_ion_id", ondelete="CASCADE"),
        index=True,
    )
    match_rating_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    rating: Mapped[int] = mapped_column(Integer)
    checklist: Mapped[Optional[dict]] = mapped_column(JSON)
    environment: Mapped[Optional[dict]] = mapped_column(JSON)

    # Relationships
    sample_item = relationship("SampleItem", back_populates="match_rating")
    target_ion = relationship("TargetIon", back_populates="match_rating")

    __table_args__ = (CheckConstraint("rating BETWEEN 0 AND 2", name="rating_range"),)


class MatchIsotope(Base):
    """Isotope-level match result."""

    __tablename__ = "match_isotope"

    match_isotope_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_isotope_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("target_isotope.target_isotope_id", ondelete="CASCADE"),
        index=True,
    )
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    sample_peak_id: Mapped[str] = mapped_column(
        String(20),
        index=True,
    )
    sample_peak_mz: Mapped[float] = mapped_column(Float)
    sample_peak_intensity: Mapped[float] = mapped_column(Float)
    sample_peak_intensity_relative: Mapped[float] = mapped_column(Float)
    sample_peak_tof: Mapped[float] = mapped_column(Float)
    match_abundance_error: Mapped[float] = mapped_column(Float)
    match_mz_error: Mapped[float] = mapped_column(Float)
    match_score: Mapped[float] = mapped_column(Float)
    # Per-peak signal-to-noise of the matched sample peak, when the sample file
    # carries noise data. NULL means "no SNR for this row" (files without noise
    # data, sentinel rows, and every row stored before the column existed); the
    # v2 fit score then falls back to its no-SNR mode for that row.
    signal_to_noise: Mapped[Optional[float]] = mapped_column(Float)
    match_isotope_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    match_isotope_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_item = relationship("SampleItem", back_populates="match_isotope")
    target_isotope = relationship("TargetIsotope", back_populates="match_isotope")

    __table_args__ = (
        CheckConstraint("match_score BETWEEN 0 AND 1", name="match_score_range"),
        # One row per evaluated isotope per sample; guards concurrent computes
        UniqueConstraint(
            "sample_item_id",
            "target_isotope_id",
            name="uq_match_isotope_sample_item_target_isotope",
        ),
    )


class PeakAssignmentRun(Base):
    """One peak-centric assignment run over a sample.

    Stores the producing engine and the full configuration (search ranges,
    heuristics, ppm tolerances, stage toggles) so runs are reproducible and
    comparable. PeakAssignment rows belong to exactly one run.

    status values: 'pending', 'running', 'importing', 'completed', 'failed',
    'cancelled'. 'cancelled' is terminal like 'failed' - the read model serves
    only 'completed' runs, and retention reclaims both after the failed grace -
    but kept distinct so an interrupted run is not reported as an engine error.
    'importing' is the non-terminal state an externally computed run assembles
    under while its rows arrive over several requests: unlike 'running' no
    server task owns it, which is why the startup reaper leaves it alone and
    retention holds it under its own grace instead.

    A run is either computed in-app or imported, and ``engine`` is what says
    which. It is never NULL - existing rows were backfilled to the in-app
    identity - so every consumer can compare it without handling a sentinel.
    """

    __tablename__ = "peak_assignment_run"

    peak_assignment_run_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    # Which engine produced this run: the in-app engine ('mascope', reserved
    # from client payloads) or an external one that imported its ledger.
    engine: Mapped[str] = mapped_column(String(64), server_default=text("'mascope'"))
    engine_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    config: Mapped[Optional[dict]] = mapped_column(JSON)
    # The assigned/candidate fit-score thresholds this run tiered with. Its
    # own column, not a key in the opaque config, because every row's tier is
    # validated against it. NULL for runs predating the column; an in-app run
    # stamps the thresholds from its config.
    tier_bands: Mapped[Optional[dict]] = mapped_column(JSON)
    # The producing engine's calibration state, disclosed at import time. An
    # import bypasses the server-side m/z verification gate because it
    # calibrates client-side, so this is what a reader judges its mass accuracy
    # by. NULL for in-app runs, whose calibration state is the sample's own.
    calibration: Mapped[Optional[dict]] = mapped_column(JSON)
    # The importing client's own id for the logical import. What makes the
    # request that *creates* a run idempotent: the row-offset check covers every
    # later chunk, but the first one has no run id yet to be idempotent about,
    # so a retried create would otherwise mint a second run. NULL for in-app
    # runs and for imports that supplied none.
    import_key: Mapped[Optional[str]] = mapped_column(String(64))
    error: Mapped[Optional[str]] = mapped_column(Text)
    peak_assignment_run_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    peak_assignment_run_utc_completed: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_item = relationship("SampleItem", back_populates="peak_assignment_run")
    peak_assignment = relationship(
        "PeakAssignment",
        back_populates="peak_assignment_run",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # Admission asks "does this sample have a non-terminal run" before every
        # import and every in-app assign, which is exactly this lookup.
        Index(
            "ix_peak_assignment_run_sample_item_id_status",
            "sample_item_id",
            "status",
        ),
        # One run per client-supplied import id, so a retried create resolves to
        # the run it already made instead of a second one. Postgres treats NULLs
        # here as distinct, so the runs that carry no key are unconstrained.
        UniqueConstraint(
            "sample_item_id",
            "import_key",
            name="uq_peak_assignment_run_sample_item_id_import_key",
        ),
    )


class PeakAssignment(Base):
    """Per-peak assignment result for one observed sample peak in a run.

    The unit of result is the observed peak (identified by sample_item_id +
    sample_peak_id, with mz/intensity/tof denormalized as in MatchIsotope).
    The assignment may reference a known target (target_compound_id /
    target_ion_id set, source='database') or a discovered composition
    (source='untargeted'). Every peak of the sample gets exactly one row per
    run - the single-owner-per-peak invariant is enforced by the unique
    constraint on (peak_assignment_run_id, sample_peak_id).

    role values: 'M0', 'iso_child', 'reagent', 'artifact', 'unassigned'.
    source values: 'database', 'untargeted' (NULL when unassigned).
    tier values: 'assigned', 'candidate', 'below_assignability', 'unassigned'.
    """

    __tablename__ = "peak_assignment"

    peak_assignment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # No index of its own: the (run, peak) unique constraint below leads with
    # this column, so every lookup by run - the ledger read, the batch fold,
    # the run-delete cascade - already has one. A second index here cost
    # 1.5 MB per 200k rows for nothing.
    peak_assignment_run_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey(
            "peak_assignment_run.peak_assignment_run_id",
            ondelete="CASCADE",
        ),
    )
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    # Not indexed on its own either. Nothing queries the ledger by a bare peak
    # id - verification identity, occurrence and import lookups filter other
    # tables or go through the run-scoped constraint - and an index over a
    # 20-char random string on every row was the third-largest structure on
    # the table.
    sample_peak_id: Mapped[str] = mapped_column(String(20))
    sample_peak_mz: Mapped[float] = mapped_column(Float)
    sample_peak_intensity: Mapped[float] = mapped_column(Float)
    sample_peak_tof: Mapped[Optional[float]] = mapped_column(Float)
    role: Mapped[str] = mapped_column(String(16), server_default=text("'unassigned'"))
    assigned_formula: Mapped[Optional[str]] = mapped_column(String(256))
    ion_formula: Mapped[Optional[str]] = mapped_column(String(4096))
    # Indexed only where set - see the partial indexes in __table_args__.
    ionization_mechanism_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey(
            "ionization_mechanism.ionization_mechanism_id",
            ondelete="SET NULL",
        ),
    )
    isotope_label: Mapped[Optional[str]] = mapped_column(String(64))
    # Full isotopologue formula of the matched isotope (e.g. "[15N]CH5BrNO+"),
    # from which the UI renders the compact substitution label ("[15N]").
    # NULL for untargeted isotopologues without a predicted formula and for
    # unassigned peaks. Mirrors target_isotope.target_isotope_formula.
    isotope_formula: Mapped[Optional[str]] = mapped_column(String(256))
    source: Mapped[Optional[str]] = mapped_column(String(16))
    # The fit score (mascope_tools score_pattern_v2): how well the observed data
    # fit this assignment's predicted pattern. [0, 1], 1.0 = perfect; NULL for an
    # unassigned peak. Named `fit_score` (not match/probability) deliberately -- it
    # is a measurement, not an identification confidence. See fit_score.md.
    fit_score: Mapped[Optional[float]] = mapped_column(Float)
    # Signed m/z error in ppm, (observed - predicted)/predicted * 1e6, in BOTH
    # stages (targeted match_mz_error and the untargeted finder's composition /
    # isotope m/z error share this convention). Consumers recover the predicted
    # m/z as observed_mz / (1 + mz_error_ppm/1e6) - keep it signed.
    mz_error_ppm: Mapped[Optional[float]] = mapped_column(Float)
    # Signed relative abundance error, observed/predicted - 1, in BOTH stages
    # (targeted match_abundance_error and the untargeted finder's intensity
    # error share this convention). Consumers recover the predicted relative
    # abundance as observed_rel / (1 + abundance_error) - keep it signed.
    abundance_error: Mapped[Optional[float]] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(24), server_default=text("'unassigned'"))
    # The three below are indexed only where set - see __table_args__.
    target_compound_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("target_compound.target_compound_id", ondelete="SET NULL"),
    )
    target_ion_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("target_ion.target_ion_id", ondelete="SET NULL"),
    )
    owner_peak_assignment_id: Mapped[Optional[str]] = mapped_column(
        String(32),
        ForeignKey("peak_assignment.peak_assignment_id", ondelete="SET NULL"),
    )
    # The owner reference as an imported row expressed it: the owning row's
    # sample_peak_id, which identifies it uniquely within a run. A client cannot
    # supply owner_peak_assignment_id - the server mints those - and an import
    # arrives over several requests, so the reference is staged here and
    # resolved into owner_peak_assignment_id when the import finalizes. NULL for
    # in-app runs, which build the owner link directly.
    owner_sample_peak_id: Mapped[Optional[str]] = mapped_column(String(20))
    alternatives: Mapped[Optional[list]] = mapped_column(JSON)
    provenance: Mapped[Optional[dict]] = mapped_column(JSON)

    # Relationships
    peak_assignment_run = relationship(
        "PeakAssignmentRun", back_populates="peak_assignment"
    )
    sample_item = relationship("SampleItem", back_populates="peak_assignment")

    __table_args__ = (
        UniqueConstraint(
            "peak_assignment_run_id",
            "sample_peak_id",
            name="uq_peak_assignment_run_id_sample_peak_id",
        ),
        CheckConstraint(
            "fit_score IS NULL OR fit_score BETWEEN 0 AND 1",
            name="fit_score_range",
        ),
        # The four nullable references, indexed only where they are set. They
        # exist for the foreign keys' SET NULL actions and the family/target
        # lookups, none of which ever asks for a NULL - while most of a
        # ledger's rows carry one (every unassigned peak, every peak that is
        # not an isotopologue), so indexing the NULLs too made each index two
        # to six times the size for entries no query can use. A strict
        # `col = $1` implies `col IS NOT NULL`, so the planner - and the
        # referential-action queries - still reach these.
        Index(
            "ix_peak_assignment_ionization_mechanism_id",
            "ionization_mechanism_id",
            postgresql_where=text("ionization_mechanism_id IS NOT NULL"),
        ),
        Index(
            "ix_peak_assignment_target_compound_id",
            "target_compound_id",
            postgresql_where=text("target_compound_id IS NOT NULL"),
        ),
        Index(
            "ix_peak_assignment_target_ion_id",
            "target_ion_id",
            postgresql_where=text("target_ion_id IS NOT NULL"),
        ),
        Index(
            "ix_peak_assignment_owner_peak_assignment_id",
            "owner_peak_assignment_id",
            postgresql_where=text("owner_peak_assignment_id IS NOT NULL"),
        ),
    )


class BatchPeak(Base):
    """A cross-sample "batch peak": a frozen m/z anchor that gives an assigned
    species one stable identity across a sample batch, so the batch overview can
    draw one trace per species (the peak-centric replacement for the target-ion
    identity of the legacy targeted overview).

    Identity is m/z, not formula: every observed peak in the batch -- assigned or
    not -- folds into exactly one batch peak, so unassigned m/z still get a
    batch-level trend. The anchor ``mz`` is FROZEN at creation and its membership
    tolerance (resolution-adaptive, stored as ``mz_tol_ppm``) never widens, so
    ``batch_peak_id`` stays a stable identity under incremental sample arrival.
    Formula and tier are an EVIDENCE-WEIGHTED CONSENSUS of the member peaks'
    per-sample ``PeakAssignment`` rows (never a fresh assignment of a synthetic
    consensus spectrum, which cannot be scored honestly).

    Batch peaks are partitioned per ionization mode (the m/z axis and intensity
    units differ between modes/instruments). Design:
    ``docs/dev/peak_assignment_batch.md``.

    consensus_tier values mirror ``PeakAssignment.tier``:
    'assigned' | 'candidate' | 'below_assignability' | 'unassigned'.
    """

    __tablename__ = "batch_peak"

    batch_peak_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    sample_batch_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_batch.sample_batch_id", ondelete="CASCADE"),
        index=True,
    )
    ionization_mode_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("ionization_mode.ionization_mode_id", ondelete="SET NULL"),
        index=True,
    )
    # Frozen anchor centre (m/z) and the resolution-adaptive half-window (ppm)
    # captured when the anchor was created. Membership never re-widens, so a
    # later sample's peak cannot silently redraw this bin.
    mz: Mapped[float] = mapped_column(Float)
    mz_tol_ppm: Mapped[float] = mapped_column(Float)
    intensity_variable: Mapped[Optional[str]] = mapped_column(String(32))
    # Evidence-weighted consensus over DETECTED members (see batch_peaks engine).
    consensus_formula: Mapped[Optional[str]] = mapped_column(String(256))
    consensus_ion_formula: Mapped[Optional[str]] = mapped_column(String(4096))
    ionization_mechanism_id: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("ionization_mechanism.ionization_mechanism_id", ondelete="SET NULL"),
        index=True,
    )
    consensus_tier: Mapped[str] = mapped_column(
        String(24), server_default=text("'unassigned'")
    )
    best_fit_score: Mapped[Optional[float]] = mapped_column(Float)
    # Fraction of DETECTED members whose assignment agrees with consensus_formula.
    support_fraction: Mapped[Optional[float]] = mapped_column(Float)
    # Prevalence: number of samples in which this batch peak is observed. Kept
    # SEPARATE from confidence -- an absent sample is a gap in the trace, never
    # evidence against the formula.
    n_present: Mapped[int] = mapped_column(Integer, server_default=text("'0'"))
    # 1 when the top consensus candidates are within a tie tolerance, or the
    # member disagreement looks like a co-eluting blend.
    is_ambiguous: Mapped[int] = mapped_column(Integer, server_default=text("'0'"))
    # Brightest member: the largest occurrence intensity, in the unit
    # ``intensity_variable`` names. A member aggregate materialized here for the
    # same reason ``n_present`` is -- the ledger reads batch peaks alone and
    # never joins the occurrence table.
    max_intensity: Mapped[Optional[float]] = mapped_column(Float)
    # The batch peak this one is an isotopologue of, derived from the
    # members' per-sample ``PeakAssignment`` family links (see
    # ``resolve_isotopologue_of``). NULL for an M0 anchor, for an unassigned one,
    # and whenever the members do not agree that this is an isotopologue. One hop
    # only: a chain is left for the reader to flatten.
    isotopologue_of: Mapped[Optional[str]] = mapped_column(
        String(16),
        ForeignKey("batch_peak.batch_peak_id", ondelete="SET NULL"),
        index=True,
    )
    alternatives: Mapped[Optional[list]] = mapped_column(JSON)
    provenance: Mapped[Optional[dict]] = mapped_column(JSON)
    batch_peak_utc_created: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    batch_peak_utc_modified: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    # Relationships
    sample_batch = relationship("SampleBatch", back_populates="batch_peak")
    batch_peak_occurrence = relationship(
        "BatchPeakOccurrence",
        back_populates="batch_peak",
        cascade="all, delete, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # Range-scan support for the fold-in hot path: scope to a batch + mode,
        # then binary-search the anchor m/z axis.
        Index(
            "ix_batch_peak_sample_batch_id_mz",
            "sample_batch_id",
            "ionization_mode_id",
            "mz",
        ),
        CheckConstraint(
            "best_fit_score IS NULL OR best_fit_score BETWEEN 0 AND 1",
            name="best_fit_score_range",
        ),
    )


class BatchPeakOccurrence(Base):
    """One observed sample peak folded into a batch peak -- the sparse per-sample
    matrix behind the batch overview (batch peak x sample -> intensity/tier).

    Membership is captured append-only at fold-in time. ``sample_peak_id`` equals
    ``PeakAssignment.sample_peak_id``, so a member's per-sample assignment joins
    for free (``peak_assignment_id`` records the specific row folded in). Keyed
    on (batch_peak_id, sample_item_id) - a member's identity, and the only key
    the row needs: a batch peak has at most one member per sample, one y-value
    per trace per sample, and nothing addresses an occurrence any other way.
    """

    __tablename__ = "batch_peak_occurrence"

    # A composite key rather than a surrogate: a 32-char random id cost a
    # primary-key index the size of the unique constraint it duplicated plus
    # 33 bytes on every row, and nothing ever read it. Leading with the anchor,
    # the key also serves the series fan-out (batch_peak_id -> points), which
    # used to need an index of its own.
    batch_peak_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("batch_peak.batch_peak_id", ondelete="CASCADE"),
        primary_key=True,
    )
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    sample_peak_id: Mapped[str] = mapped_column(String(20))
    peak_assignment_id: Mapped[Optional[str]] = mapped_column(
        String(32),
        ForeignKey("peak_assignment.peak_assignment_id", ondelete="SET NULL"),
        index=True,
    )
    # Denormalized member fields (as MatchIsotope / PeakAssignment do): the peak's
    # own m/z in this sample (for jitter/QC), its intensity (the chart y-value),
    # and its per-sample assignment tier / fit / formula folded in.
    sample_peak_mz: Mapped[float] = mapped_column(Float)
    intensity: Mapped[Optional[float]] = mapped_column(Float)
    tier: Mapped[Optional[str]] = mapped_column(String(24))
    fit_score: Mapped[Optional[float]] = mapped_column(Float)
    assigned_formula: Mapped[Optional[str]] = mapped_column(String(256))

    # Relationships
    batch_peak = relationship("BatchPeak", back_populates="batch_peak_occurrence")
    sample_item = relationship("SampleItem", back_populates="batch_peak_occurrence")


class AttributeTemplate(Base):
    """Attribute template for additional sample metadata."""

    __tablename__ = "attribute_template"

    attribute_template_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    type: Mapped[Optional[str]] = mapped_column(String(64))
    template: Mapped[Optional[dict]] = mapped_column(JSON)


class InstrumentFunction(Base):
    """Instrument function parameters."""

    __tablename__ = "instrument_function"

    instrument_function_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument: Mapped[str] = mapped_column(String(64))
    method_file: Mapped[str] = mapped_column(String(512))
    datetime_utc: Mapped[dt] = mapped_column(TIMESTAMP(timezone=True))
    peakshape: Mapped[Optional[dict]] = mapped_column(JSON)
    resolution_function: Mapped[Optional[dict]] = mapped_column(JSON)

    # Relationships
    sample_file = relationship("SampleFile", back_populates="instrument_function")


# ---------------------------------------------------------------------------
# Reference chemistry databases (public-database integration)
# ---------------------------------------------------------------------------


class ReferenceSource(Base):
    """One ingested public-database source at one version.

    Records provenance for the mirrored reference compounds: which source, which
    release, under what license, and how many records. A source can have several
    rows over time (versioned loads for reproducibility); ``is_active`` marks the
    one that queries read, and re-ingesting a source flips the previous load
    inactive.
    """

    __tablename__ = "reference_source"

    reference_source_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(128))
    license: Mapped[str] = mapped_column(String(64))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ingested_at: Mapped[dt] = mapped_column(TIMESTAMP(timezone=True))

    # Relationships
    reference_compound = relationship(
        "ReferenceCompound",
        back_populates="reference_source",
        cascade="all, delete",
        passive_deletes=True,
    )


class ReferenceCompound(Base):
    """One compound as it appears in one source version.

    Landing table for annotation lookups: ``formula`` (canonical Hill order) and
    ``monoisotopic_mass`` are computed on ingest and indexed so annotation is an
    indexed lookup rather than a scan. ``inchikey`` is the cross-source dedup key.
    One row per (compound, source) preserves provenance and the per-record
    ``license`` (load-bearing for mixed-license sources and commercial use).
    """

    __tablename__ = "reference_compound"

    reference_compound_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    reference_source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reference_source.reference_source_id", ondelete="CASCADE"),
        index=True,
    )
    formula: Mapped[str] = mapped_column(String(512), index=True)
    monoisotopic_mass: Mapped[Optional[float]] = mapped_column(Float, index=True)
    # Intrinsic charge of the stored species; NULL/0 means neutral. Recorded so
    # permanently charged species (choline, quaternary ammoniums) can be
    # represented, but deliberately NOT matched: Stage A pairs neutral formulas
    # with ionization mechanisms, so charged rows are excluded from
    # iter_known_compositions until intrinsic-charge analytes are supported
    # (issue #1726). Nothing writes it yet - ingest still rejects
    # charge-suffixed formulas.
    charge: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    inchikey: Mapped[Optional[str]] = mapped_column(String(27), index=True)
    name: Mapped[Optional[str]] = mapped_column(Text)
    smiles: Mapped[Optional[str]] = mapped_column(Text)
    inchi: Mapped[Optional[str]] = mapped_column(Text)
    source_native_id: Mapped[str] = mapped_column(String(128))
    xrefs: Mapped[Optional[dict]] = mapped_column(JSON)
    license: Mapped[str] = mapped_column(String(64))

    # Relationships
    reference_source = relationship(
        "ReferenceSource", back_populates="reference_compound"
    )


class AssignmentCalibration(Base):
    """Stored score -> P(correct) calibration per instrument (the D6 calibration store).

    Moves the assignment-confidence calibration out of the in-code registry so a curve can be
    (re)fit per deployment -- e.g. a user runs known standards + near-mass decoys on their
    instrument -- without a code change. Holds the Platt parameters ``a``/``b`` plus the
    per-adduct corroboration log-odds (keyed by adduct notation, e.g. ``{"+Br-": 2.28}``) and the
    provenance mirrored from :class:`mascope_tools.composition.calibration.Calibration`.

    Keyed by ``(instrument, score_version)`` because a curve is only valid for the fit-score
    version it was fit against; ``is_active`` marks the row the loader reads (refitting flips the
    previous one inactive). When the table has no active row the loader falls back to the in-code
    provisional curve, so this is additive and safe to ship empty.
    """

    __tablename__ = "assignment_calibration"

    assignment_calibration_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    instrument: Mapped[str] = mapped_column(String(32), index=True)
    score_version: Mapped[int] = mapped_column(Integer, index=True)
    a: Mapped[float] = mapped_column(Float)
    b: Mapped[float] = mapped_column(Float)
    n_pos: Mapped[int] = mapped_column(Integer, default=0)
    n_neg: Mapped[int] = mapped_column(Integer, default=0)
    ece: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    corroboration_weights: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    fit_utc: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_utc: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=True), default=lambda: dt.now(timezone.utc)
    )

    __table_args__ = (
        Index(
            "ix_assignment_calibration_active",
            "instrument",
            "score_version",
            "is_active",
        ),
    )


class AssignmentVerification(Base):
    """A user's verdict on a peak-centric assignment (verification-calibration loop, V1).

    Human-in-the-loop confirmation/rejection of an identification: the honest source of the
    labelled golden set that later refits the confidence calibration
    (``docs/dev/verification_calibration_loop.md``). Append-only -- every verdict is kept for
    audit, and the score snapshot on an earlier one stays a valid calibration pair -- but the
    **current** verdict is marked rather than re-derived: exactly one row per identity has
    ``superseded_utc IS NULL``, and recording a new verdict stamps the one it replaces in the same
    transaction. The partial unique index below enforces that invariant in the database, so a
    reader filters ``superseded_utc IS NULL`` instead of taking a max by ``verified_utc``, and no
    consumer can silently count a retracted verdict.

    Keyed to the **stable identity** of what was judged -- ``sample_item_id`` + ``sample_peak_id``
    (an observed-peak id, stable across assignment runs) + ``assigned_formula`` +
    ``ionization_mechanism_id`` -- so a label survives re-runs that churn run-scoped rows. The
    run-scoped ``peak_assignment_id`` / ``peak_assignment_run_id`` are provenance (the row deletes to
    NULL on a re-run). ``fit_score`` / ``evidence`` / ``p_correct`` are **snapshotted at verification
    time**: the calibration pair must be pinned to the score the user actually judged.

    ``evidence_level`` records *why* the user is confident (the guardrail against a
    confirmation-bias loop): a reference-standard confirmation is weighted far above a visual guess.
    """

    __tablename__ = "assignment_verification"

    assignment_verification_id: Mapped[str] = mapped_column(
        String(32), primary_key=True
    )
    sample_item_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("sample_item.sample_item_id", ondelete="CASCADE"),
        index=True,
    )
    # Provenance link to the judged assignment row; SET NULL so the label outlives a re-run.
    peak_assignment_id: Mapped[Optional[str]] = mapped_column(
        String(32),
        ForeignKey("peak_assignment.peak_assignment_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    peak_assignment_run_id: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )
    # Stable identity (survives re-runs): observed peak + judged formula/adduct.
    sample_peak_id: Mapped[str] = mapped_column(String(20), index=True)
    assigned_formula: Mapped[Optional[str]] = mapped_column(String(256))
    ionization_mechanism_id: Mapped[Optional[str]] = mapped_column(String(16))
    verdict: Mapped[str] = mapped_column(String(16))
    evidence_level: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    # Score snapshot at verification time (p_correct null when uncalibrated).
    fit_score: Mapped[Optional[float]] = mapped_column(Float)
    evidence: Mapped[Optional[float]] = mapped_column(Float)
    p_correct: Mapped[Optional[float]] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(Text)
    context: Mapped[Optional[dict]] = mapped_column(JSON)
    verified_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    verified_utc: Mapped[dt] = mapped_column(
        TIMESTAMP(timezone=True), default=lambda: dt.now(timezone.utc)
    )
    # NULL on the one live verdict per identity; on a replaced verdict, the moment it was
    # replaced (the successor's verified_utc). Never cleared -- a superseded row is history.
    superseded_utc: Mapped[Optional[dt]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('confirmed', 'rejected', 'unsure')", name="verdict_valid"
        ),
        CheckConstraint(
            "evidence_level IS NULL OR evidence_level IN "
            "('reference_standard', 'msms', 'orthogonal', 'pattern', 'visual')",
            name="evidence_level_valid",
        ),
        Index(
            "ix_assignment_verification_identity",
            "sample_item_id",
            "sample_peak_id",
        ),
        # One live verdict per stable identity. NULLS NOT DISTINCT because both
        # assigned_formula and ionization_mechanism_id are nullable, and under the default
        # NULLS DISTINCT two live verdicts on a formula-less peak would both be accepted --
        # exactly the case this index exists to reject. Partial, so superseded history is
        # unconstrained and a peak can accumulate any number of past verdicts.
        Index(
            "uq_assignment_verification_current",
            "sample_item_id",
            "sample_peak_id",
            "assigned_formula",
            "ionization_mechanism_id",
            unique=True,
            postgresql_where=text("superseded_utc IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )


__all__ = [
    "Base",
    "Workspace",
    "WorkspaceMember",
    "User",
    "Role",
    "AccessToken",
    "UserRecoveryCode",
    "AgentDevice",
    "Dataset",
    "SampleBatch",
    "SampleFile",
    "SampleItem",
    "TargetCollection",
    "TargetCollectionInSampleBatch",
    "TargetCompound",
    "TargetCompoundInTargetCollection",
    "TargetIon",
    "TargetIsotope",
    "IonizationMechanism",
    "IonizationMode",
    "MatchSample",
    "MatchCollection",
    "MatchCompound",
    "MatchIon",
    "MatchIsotope",
    "MatchRating",
    "PeakAssignmentRun",
    "PeakAssignment",
    "BatchPeak",
    "BatchPeakOccurrence",
    "AttributeTemplate",
    "InstrumentFunction",
    "ReferenceSource",
    "ReferenceCompound",
    "AssignmentCalibration",
    "AssignmentVerification",
]
