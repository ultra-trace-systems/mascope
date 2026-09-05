"""
Agent device-pairing service.

Lets an on-instrument agent obtain an API access token without anyone
copy-pasting it: the agent requests a short user-facing code, a signed-in
editor approves the code in the web app, and the agent polls until the
token is ready. Pairing state is ephemeral and lives in Redis (shared
across workers, expires automatically); the resulting token is a normal
service access token row.

Approval intentionally creates a NEW token without revoking the user's
existing tokens for the service, so pairing a second instrument machine
does not break the first one (unlike the manual "Regenerate" button,
which replaces all tokens for the service).
"""

import json
import secrets

from sqlalchemy import delete

from mascope_backend.api.new.auth.access_token.service import (
    create_access_token,
    regenerate_access_token,
)
from mascope_backend.api.new.auth.devices.machine_account import create_machine_account
from mascope_backend.api.new.auth.pairing.config import pairing_settings
from mascope_backend.api.new.auth.pairing.exceptions import (
    PairingCodeAlreadyApprovedException,
    PairingCodeInvalidException,
)
from mascope_backend.db import AgentDevice, User, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import redis_storage_client


def _code_key(user_code: str) -> str:
    return f"mascope:pairing:code:{user_code}"


def _device_key(device_code: str) -> str:
    return f"mascope:pairing:device:{device_code}"


def _redis():
    """The shared async Redis client (separate hook for tests)."""
    return redis_storage_client.client


def format_user_code(user_code: str) -> str:
    """Format a raw code for display, e.g. ``BCD234`` -> ``BCD-234``.

    :param user_code: Raw code, as stored.
    :type user_code: str
    :return: Display form with a dash in the middle.
    :rtype: str
    """
    half = len(user_code) // 2
    return f"{user_code[:half]}-{user_code[half:]}"


async def start_pairing(
    service_name: str,
    machine_name: str | None,
    instrument: str | None = None,
    agent_version: str | None = None,
) -> dict:
    """Create a pending pairing and return its codes.

    :param service_name: Agent service requesting a token (pre-validated).
    :type service_name: str
    :param machine_name: Optional machine hostname, shown to the approver.
    :type machine_name: str | None
    :param instrument: Optional instrument the agent watches (pre-validated),
        kept on the device once the pairing is approved.
    :type instrument: str | None
    :param agent_version: Optional agent release, kept on the device likewise.
    :type agent_version: str | None
    :return: user_code (display form), device_code, expires_in, interval.
    :rtype: dict
    """
    client = _redis()
    # Retry on the (unlikely) case of a code collision with a pending pairing.
    for _ in range(5):
        user_code = "".join(
            secrets.choice(pairing_settings.CODE_ALPHABET)
            for _ in range(pairing_settings.CODE_LENGTH)
        )
        if not await client.exists(_code_key(user_code)):
            break
    device_code = secrets.token_urlsafe(32)
    record = {
        "service_name": service_name,
        "machine_name": machine_name,
        "instrument": instrument,
        "agent_version": agent_version,
        "status": "pending",
        "device_code": device_code,
        "access_token": None,
    }
    ttl = pairing_settings.CODE_TTL_SECONDS
    await client.setex(_code_key(user_code), ttl, json.dumps(record))
    await client.setex(_device_key(device_code), ttl, user_code)
    runtime.logger.info(
        f"Pairing started for {service_name}"
        + (f" on '{machine_name}'" if machine_name else "")
        + (f" watching '{instrument}'" if instrument else "")
    )
    return {
        "user_code": format_user_code(user_code),
        "device_code": device_code,
        "expires_in": ttl,
        "interval": pairing_settings.POLL_INTERVAL_SECONDS,
        # What this server does with what the agent reports, so the agent's
        # setup can skip the questions an older server needed answered - the
        # upload prefix, in particular. An older server sends no such key.
        "capabilities": {"files_uploads_under_reported_instrument": True},
    }


async def _discard_failed_pairing(device_id: int, machine_user_id: int) -> None:
    """Remove a device and machine account whose credential could not be minted.

    Deleting the device cascades to any token already written for it, and the
    machine account carries its workspace memberships out with it, so
    re-approving the still-pending code starts clean instead of adding a second
    dead device. Cleanup failure is logged, not raised: the caller is already
    unwinding a more informative error.

    :param device_id: The device to remove.
    :type device_id: int
    :param machine_user_id: The machine account to remove.
    :type machine_user_id: int
    """
    try:
        async with async_session() as session:
            await session.execute(
                delete(AgentDevice).where(AgentDevice.device_id == device_id)
            )
            await session.execute(delete(User).where(User.id == machine_user_id))
            await session.commit()
    except Exception:
        runtime.logger.exception(
            f"Could not clean up the failed pairing (device {device_id}, "
            f"machine account {machine_user_id}). Revoke the device from "
            "Paired machines if it is still listed."
        )


async def approve_pairing(user: User, user_code: str) -> dict:
    """Approve a pending pairing: provision the machine account and its token.

    Creates the device, a dedicated machine account it authenticates as
    (sponsored by, but not owned by, the approver), and the service token bound
    to the device. The machine account also gets a file-converter token up
    front, so its first upload is not refused for the want of one - only a login
    would otherwise mint it, and a machine account never logs in.

    :param user: The approving user (editor role enforced at the route).
    :type user: User
    :param user_code: Normalized pairing code (no dash, uppercase).
    :type user_code: str
    :return: service_name, machine_name and instrument of the approved pairing.
    :rtype: dict
    :raises PairingCodeInvalidException: Unknown or expired code.
    :raises PairingCodeAlreadyApprovedException: Code approved before.
    """
    client = _redis()
    raw = await client.get(_code_key(user_code))
    if raw is None:
        raise PairingCodeInvalidException()
    record = json.loads(raw)
    if record["status"] != "pending":
        raise PairingCodeAlreadyApprovedException()

    # The device and the machine account it authenticates as are created in one
    # transaction. Committing them separately could leave a device whose
    # machine_user_id is NULL - one that can never renew its token, that
    # revocation only half-cleans, and that the operator cannot tell from a
    # healthy one.
    machine_name = record["machine_name"] or "Unnamed agent"
    async with async_session() as session:
        device = AgentDevice(
            name=machine_name,
            service_name=record["service_name"],
            # What the agent said about itself when it asked to pair; a
            # record written before these fields existed carries neither.
            instrument=record.get("instrument"),
            last_seen_version=record.get("agent_version"),
            sponsor_user_id=user.id,
        )
        session.add(device)
        # Assigns device_id, which keys the account's username and e-mail.
        await session.flush()

        machine_user = await create_machine_account(
            session,
            machine_name=machine_name,
            device_id=device.device_id,
            sponsor_user_id=user.id,
        )
        device.machine_user_id = machine_user.id
        await session.commit()
        device_id = device.device_id
        machine_user_id = machine_user.id

    # Token minting runs outside that transaction because the token strategy
    # owns its own session. If it fails, discard the pair rather than leave a
    # device holding no credential: the code is still pending, so approving
    # again yields a clean device instead of a second dead one.
    try:
        # Uploads resolve the machine account's file-converter token
        # server-side; mint it now (regenerate is create-if-absent here) so the
        # first upload works.
        await regenerate_access_token(user=machine_user, service_name="file-converter")

        token = await create_access_token(
            user=machine_user,
            service_name=record["service_name"],
            description=(
                f"Paired: {record['machine_name']}"
                if record["machine_name"]
                else "Paired"
            ),
            device_id=device_id,
        )
    except Exception:
        await _discard_failed_pairing(device_id, machine_user_id)
        raise

    record["status"] = "approved"
    record["access_token"] = token
    ttl = pairing_settings.PICKUP_TTL_SECONDS
    await client.setex(_code_key(user_code), ttl, json.dumps(record))
    await client.setex(_device_key(record["device_code"]), ttl, user_code)
    runtime.logger.info(
        f"Pairing {format_user_code(user_code)} approved by {user.username} "
        f"for {record['service_name']}"
    )
    return {
        "service_name": record["service_name"],
        "machine_name": record["machine_name"],
        "instrument": record.get("instrument"),
    }


async def poll_pairing(device_code: str) -> dict:
    """Report the pairing status to the polling agent.

    On approval the token is handed out exactly once and the pairing
    records are deleted.

    :param device_code: The agent's secret device code.
    :type device_code: str
    :return: status ("pending" | "approved" | "expired"), with
        access_token and service_name when approved.
    :rtype: dict
    """
    client = _redis()
    user_code = await client.get(_device_key(device_code))
    if user_code is None:
        return {"status": "expired"}
    raw = await client.get(_code_key(user_code))
    if raw is None:
        return {"status": "expired"}
    record = json.loads(raw)
    if record["status"] != "approved":
        return {
            "status": "pending",
            "interval": pairing_settings.POLL_INTERVAL_SECONDS,
        }
    # Hand the token out once, then forget the pairing.
    await client.delete(_code_key(user_code))
    await client.delete(_device_key(device_code))
    return {
        "status": "approved",
        "access_token": record["access_token"],
        "service_name": record["service_name"],
    }
