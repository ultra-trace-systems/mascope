"""
Provisioning the machine account behind a paired device.

A machine account is the subject of an instrument agent's credential. It exists
so that acquisition never authenticates as a human: the sponsor vouches for the
machine, but the token belongs to the machine, so the sponsor leaving does not
revoke it. The account is capped at editor (enough to upload, never to manage
users), carries a random password nobody knows (login is refused for machine
accounts regardless), and is created directly rather than through the user
registration flow, which would broadcast it into the human user views and add
it to every system workspace.
"""

import secrets

from fastapi_users.password import PasswordHelper

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.db import User, async_session
from mascope_backend.runtime import runtime


_password_helper = PasswordHelper()


async def create_machine_account(machine_name: str, device_id: int) -> User:
    """
    Create the machine account for a freshly paired device.

    :param machine_name: The machine's reported name, used for a readable
        (but non-login) username.
    :type machine_name: str
    :param device_id: The device this account belongs to, used to make the
        synthetic username and email unique and non-routable.
    :type device_id: int
    :return: The created machine ``User``.
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

    async with async_session() as session:
        machine_user = User(
            email=email,
            username=username,
            hashed_password=unusable_password,
            is_active=True,
            is_verified=True,
            is_superuser=False,
            role_id=auth_settings.ROLE_ACCESS_LEVELS["editor"],
            account_type=ACCOUNT_TYPE_MACHINE,
            must_change_password=False,
        )
        session.add(machine_user)
        await session.commit()
        await session.refresh(machine_user)

    runtime.logger.info(
        f"Created machine account '{username}' (id {machine_user.id}) "
        f"for device {device_id}"
    )
    return machine_user
