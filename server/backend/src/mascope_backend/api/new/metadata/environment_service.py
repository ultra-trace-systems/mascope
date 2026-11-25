"""The environment registry: list, create, change, retire and delete environments.

What was sampled is recorded as environment tags, and the tags select the
reference lists through this registry (``docs/dev/
sample_filters_and_metadata.md``, section 4.6): a row joins the key a sample
is tagged with to a chemistry context and to regimes, which the list headers
declare. An environment is shared reference data, as a registry field is: the
global editor role or higher defines it, a guest reads it (decision 14,
MAS-104; the routes gate this). What the service guards:

- the key never changes, as samples are tagged with it;
- the shipped ``environment`` field's allowed values are the registry's keys,
  retired ones included, so what was tagged with a retired environment keeps
  its meaning. Every write here rewrites them in the same transaction, and so
  does every start (:func:`ensure_shipped_environments`); the field's own
  route refuses to change them;
- an environment Mascope ships (``system``) may be edited and retired, but not
  deleted, as a start would create it again.

Acquisition metadata, which is what a sample's tags will be stored in
(MAS-335), does not exist yet, so no environment is in use and any but a
shipped one may be deleted. Once it does, an environment a value names is
retired rather than deleted, as a field in use is.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.metadata.environment_catalogue import (
    SHIPPED_ENVIRONMENTS,
)
from mascope_backend.api.new.metadata.environment_schema import (
    EnvironmentCreate,
    EnvironmentUpdate,
)
from mascope_backend.api.new.metadata.service import (
    RECORD_TYPE as FIELD_RECORD_TYPE,
)
from mascope_backend.db import Environment, MetadataField, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import emit_record_reload
from mascope_reference.peaklist import REGIMES


#: The record type a change to the registry reloads in the clients.
RECORD_TYPE = "environment"
#: The key of the shipped tags field whose allowed values are the registry's keys.
ENVIRONMENT_FIELD_KEY = "environment"


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _record(environment: Environment) -> dict:
    record = environment.to_dict()
    record["retired"] = environment.retired_utc is not None
    return record


async def _fetch(session: AsyncSession, environment_id: str) -> Environment:
    environment = await session.get(Environment, environment_id)
    if environment is None:
        raise NotFoundException(f"Environment '{environment_id}' not found.")
    return environment


async def sync_environment_field(session: AsyncSession) -> bool:
    """Write the registry's keys as the shipped field's allowed values.

    Every key, retired ones included, in alphabetical order. Left alone when
    they are already the field's values, so a start that finds nothing to do
    writes nothing. Flushes the session first, so a change it holds counts;
    the caller commits.

    The field row is locked before the keys are read. Two writes at once
    would otherwise each read the committed keys and their own, and the later
    commit would drop the other's key (or bring a deleted one back). With the
    lock the second writer waits here until the first commits, and its read
    of the keys, a new snapshot under READ COMMITTED, holds the first's change.

    :param session: An open session.
    :return: Whether the field changed. False too when the field is missing,
        which only a database the migrations did not make lacks.
    """
    await session.flush()
    field = await session.scalar(
        select(MetadataField)
        .where(MetadataField.key == ENVIRONMENT_FIELD_KEY)
        .with_for_update()
    )
    if field is None:
        return False
    keys = list((await session.scalars(select(Environment.key))).all())
    keys.sort()
    if field.allowed_values == keys:
        return False
    field.allowed_values = keys
    field.modified_utc = datetime.now(timezone.utc)
    return True


async def _reload(field_changed: bool) -> None:
    await emit_record_reload(record_type=RECORD_TYPE)
    if field_changed:
        await emit_record_reload(record_type=FIELD_RECORD_TYPE)


@api_controller()
async def get_environments(include_retired: bool = False) -> dict:
    """The registry's environments, ordered by label.

    :param include_retired: Whether to list the retired ones too.
    :return: The environments, each with ``retired``.
    """
    async with async_session() as session:
        stmt = select(Environment).order_by(
            func.lower(Environment.label), Environment.key
        )
        if not include_retired:
            stmt = stmt.where(Environment.retired_utc.is_(None))
        environments = (await session.scalars(stmt)).all()
    return {
        "message": "Environments retrieved successfully.",
        "results": len(environments),
        "data": [_record(environment) for environment in environments],
    }


@api_controller()
async def get_environment(environment_id: str) -> dict:
    """One environment of the registry.

    :param environment_id: The environment's id.
    :raises NotFoundException: If there is no such environment.
    """
    async with async_session() as session:
        environment = await _fetch(session, environment_id)
    return {
        "message": f"Environment '{environment.key}' retrieved successfully.",
        "data": _record(environment),
    }


def get_regimes() -> dict:
    """The regimes an environment may name: the vocabulary of the list headers.

    :return: Each regime with what it names, ordered by key.
    """
    data = [
        {"key": key, "description": description}
        for key, description in sorted(REGIMES.items())
    ]
    return {
        "message": "Regimes retrieved successfully.",
        "results": len(data),
        "data": data,
    }


@api_controller()
async def create_environment(body: EnvironmentCreate) -> dict:
    """Add an environment to the registry, and its key to the field's values.

    :param body: The environment.
    :raises HTTPException: 409 if an environment, retired or not, has the key.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        taken = await session.scalar(
            select(Environment.environment_id).where(Environment.key == body.key)
        )
        if taken is not None:
            raise _conflict(
                f"An environment with the key '{body.key}' already exists. A "
                "retired environment keeps its key: restore it instead."
            )
        environment = Environment(
            environment_id=gen_id(16),
            key=body.key,
            label=body.label,
            chemistry_context=body.chemistry_context,
            regimes=list(body.regimes),
            system=False,
            created_utc=now,
            modified_utc=now,
        )
        session.add(environment)
        field_changed = await sync_environment_field(session)
        await session.commit()
    await _reload(field_changed)
    return {
        "message": f"Environment '{environment.key}' created successfully.",
        "data": _record(environment),
    }


@api_controller()
async def update_environment(environment_id: str, body: EnvironmentUpdate) -> dict:
    """Change an environment's label, chemistry context or regimes, or retire or
    restore it. Only what the body sends changes.

    :param environment_id: The environment's id.
    :param body: The change.
    :raises NotFoundException: If there is no such environment.
    """
    changes = body.model_dump(exclude_unset=True)
    retired = changes.pop("retired", None)
    async with async_session() as session:
        environment = await _fetch(session, environment_id)
        for name, value in changes.items():
            setattr(environment, name, value)
        if retired is not None:
            if retired and environment.retired_utc is None:
                environment.retired_utc = datetime.now(timezone.utc)
            elif not retired:
                environment.retired_utc = None
        environment.modified_utc = datetime.now(timezone.utc)
        await session.commit()
    await _reload(False)
    return {
        "message": f"Environment '{environment.key}' updated successfully.",
        "data": _record(environment),
    }


@api_controller()
async def delete_environment(environment_id: str) -> dict:
    """Remove an environment, and its key from the field's values.

    :param environment_id: The environment's id.
    :raises NotFoundException: If there is no such environment.
    :raises HTTPException: 409 for an environment Mascope ships, which is
        retired instead.
    """
    async with async_session() as session:
        environment = await _fetch(session, environment_id)
        if environment.system:
            raise _conflict(
                f"'{environment.key}' is an environment Mascope ships and cannot "
                "be deleted: the next start would create it again. Retire it "
                "instead."
            )
        await session.delete(environment)
        field_changed = await sync_environment_field(session)
        await session.commit()
    await _reload(field_changed)
    return {"message": f"Environment '{environment.key}' deleted successfully."}


async def ensure_shipped_environments() -> list[str]:
    """Create the shipped environments a server does not hold, and write the
    registry's keys as the field's values.

    Runs at every start, in the main process before any worker starts. An
    entry of :data:`SHIPPED_ENVIRONMENTS` is created under its fixed id where
    no row has its key. A row that has it is left as it is: a shipped one the
    deployment edited or retired keeps the edit, and one the deployment made
    itself under that key before the release shipped it stays the
    deployment's. So a second start changes nothing.

    :return: The keys of the environments it created.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        held = set((await session.scalars(select(Environment.key))).all())
        created = []
        for shipped in SHIPPED_ENVIRONMENTS:
            if shipped.key in held:
                continue
            session.add(
                Environment(
                    environment_id=shipped.environment_id,
                    key=shipped.key,
                    label=shipped.label,
                    chemistry_context=shipped.chemistry_context,
                    regimes=list(shipped.regimes),
                    system=True,
                    created_utc=now,
                    modified_utc=now,
                )
            )
            created.append(shipped.key)
        field_changed = await sync_environment_field(session)
        await session.commit()
    if created:
        runtime.logger.info(f"Created the shipped environments {', '.join(created)}")
    if field_changed:
        runtime.logger.info(
            "Wrote the registry's keys as the environment field's values"
        )
    return created
