"""
Provisioning the machine account behind a paired device.

A machine account is the subject of an instrument agent's credential. It exists
so that acquisition never authenticates as a human: the sponsor vouches for the
machine, but the token belongs to the machine, so the sponsor leaving does not
revoke it. The account is capped at editor (enough to upload, never to manage
users), carries a random password nobody knows (login is refused for machine
accounts regardless), and is created directly rather than through the user
registration flow, which would broadcast it into the human user views.

It does get the one thing registration provides that acquisition depends on:
membership in the acquisition workspaces. Without it an agent is refused on
every upload to an instrument whose workspace already exists, which is every
instrument on a deployment that has been running. That membership is copied
from the sponsor rather than granted wholesale (see
``workspaces.system.mirror_system_workspaces``), so the device's token reaches
exactly as far as the approver's own token did and no further - it lives in
plaintext on a shared instrument PC, and enrolling it everywhere would hand it
instruments its sponsor cannot open.
"""

import secrets

from fastapi_users.password import PasswordHelper

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.workspaces.system import mirror_system_workspaces
from mascope_backend.db import User
from mascope_backend.runtime import runtime


_password_helper = PasswordHelper()

#: Workspace role a machine account holds in the acquisition workspaces:
#: enough to upload, never to manage the workspace or its members.
MACHINE_WORKSPACE_ROLE = "editor"


async def create_machine_account(
    session, machine_name: str, device_id: int, sponsor_user_id: int | None
) -> User:
    """
    Create the machine account for a freshly paired device.

    Adds to the given session and flushes so the caller has the account's id,
    but does not commit: the device row and the account it authenticates as are
    committed together, so a failure can never leave a device whose
    ``machine_user_id`` is NULL - one that could never renew its token and that
    revocation would only half-clean.

    :param session: An open async session owned by the caller.
    :param machine_name: The machine's reported name, used for a readable
        (but non-login) username.
    :type machine_name: str
    :param device_id: The device this account belongs to, used to make the
        synthetic username and email unique and non-routable.
    :type device_id: int
    :param sponsor_user_id: The approving account, whose acquisition-workspace
        access bounds the machine's.
    :type sponsor_user_id: int | None
    :return: The created machine ``User``, flushed so ``id`` is populated.
    :rtype: User
    """
    # A random password nobody holds: machine accounts never sign in
    # interactively (the login path refuses account_type == machine), so this
    # only needs to be a valid, unguessable hash rather than a usable secret.
    unusable_password = _password_helper.hash(secrets.token_urlsafe(32))

    # device_id is unique, so it keys a unique username and e-mail. The address
    # is a subdomain that receives no mail (a machine account has no
    # password-reset or verification path) but still has to parse as a valid
    # address: EmailStr, which UserRead uses, rejects reserved TLDs like
    # .invalid/.local, so a real-looking domain is used deliberately.
    username = f"{machine_name} (agent #{device_id})"
    email = f"device-{device_id}@agents.mascope.app"

    machine_user = User(
        email=email,
        username=username,
        hashed_password=unusable_password,
        is_active=True,
        is_verified=True,
        is_superuser=False,
        role_id=auth_settings.ROLE_ACCESS_LEVELS[MACHINE_WORKSPACE_ROLE],
        account_type=ACCOUNT_TYPE_MACHINE,
        must_change_password=False,
    )
    session.add(machine_user)
    await session.flush()

    # The membership registration would otherwise have granted, bounded by the
    # sponsor. Without it the agent authenticates fine and is then refused by
    # the workspace ACL on every instrument that was already in use.
    added = await mirror_system_workspaces(
        session, sponsor_user_id, machine_user.id, MACHINE_WORKSPACE_ROLE
    )

    runtime.logger.info(
        f"Created machine account '{username}' (id {machine_user.id}) "
        f"for device {device_id}; enrolled in {added} acquisition workspace(s) "
        f"from sponsor {sponsor_user_id}"
    )
    return machine_user
