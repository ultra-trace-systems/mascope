"""
The deployment's rule for who must hold a second factor.

One setting, ``backend.mfa_required_min_role`` in the config TOML, naming the
lowest role the requirement applies to. Unset means nobody is required to
enroll, which is the default and what every existing deployment gets.

Imports the role table, the runtime, and ``mfa.secrets`` (which itself imports
only the runtime), so it stays usable from the user schemas without pulling the
rest of the auth package in behind it.
"""

from typing import Optional, cast

from mascope_backend.api.new.auth.mfa.secrets import mfa_configured
from mascope_backend.roles import ROLE_ACCESS_LEVELS
from mascope_backend.runtime import runtime
from mascope_runtime.config import BackendConfig


class InvalidMfaPolicyError(ValueError):
    """The configured minimum role is not a role this deployment has."""


def resolve_required_level(configured: Optional[str]) -> Optional[int]:
    """
    Turn the configured role name into the access level it means.

    Raises on an unrecognized name rather than falling back to "nobody". A typo
    in this setting would otherwise leave an operator believing the requirement
    is in force while no account is ever asked to enrol - a security control
    that silently does nothing, which is worse than one that is visibly off.
    Refusing to start is loud, immediate, and attributable to the value just
    edited.

    Pure, and separate from reading the config, so the mapping can be exercised
    without a runtime: ``runtime.config`` is a read-only property.

    :param configured: The value from the config, or ``None`` when unset.
    :raises InvalidMfaPolicyError: If it names no known role.
    :return: The access level, or ``None`` when the requirement is off.
    """
    if configured is None or str(configured).strip() == "":
        return None

    name = str(configured).strip().lower()
    if name not in ROLE_ACCESS_LEVELS:
        raise InvalidMfaPolicyError(
            f"mfa_required_min_role is set to '{configured}', which is not a "
            f"Mascope role. Use one of: {', '.join(sorted(ROLE_ACCESS_LEVELS))}, "
            "or remove the setting to require no second factor."
        )
    return ROLE_ACCESS_LEVELS[name]


def _configured_value() -> Optional[str]:
    """
    The raw setting for this deployment.

    Tolerates a config object without the field: an older layer on disk simply
    means the requirement is unset.

    :return: The configured role name, or ``None``.
    """
    return getattr(cast(BackendConfig, runtime.config), "mfa_required_min_role", None)


def require_key_when_active(required_level: Optional[int], key_present: bool) -> None:
    """
    Refuse a policy that requires a factor nobody can enrol.

    A deployment that requires a second factor but has no MFA encryption key
    would hold every covered account - including every administrator, the only
    accounts that could reset the others - at an enrolment screen whose "Set up"
    button can only fail. That is a lockout with no way back inside the
    application, so it is refused at startup for the same reason a bad role name
    is: loud and immediate beats a control that looks configured and traps
    everyone it covers.

    Separate from ``resolve_required_level`` and pure, so the two startup
    failures can be exercised independently.

    :param required_level: The resolved threshold, or ``None`` when off.
    :param key_present: Whether the deployment can store seeds.
    :raises InvalidMfaPolicyError: If the policy is active but no key exists.
    """
    if required_level is not None and not key_present:
        raise InvalidMfaPolicyError(
            "mfa_required_min_role is set, but this deployment has no MFA "
            "encryption key, so no account can enrol - every covered account "
            "would be locked out. Create .runtime/secrets/mfa_encryption_key.txt "
            "(see docs/hosting.md), or remove the setting."
        )


#: Resolved once at import, so a bad value stops the process at startup rather
#: than on whichever request first happens to consult it.
REQUIRED_LEVEL: Optional[int] = resolve_required_level(_configured_value())

#: Checked at import for the same reason: an active policy with no key is a
#: startup-time misconfiguration, not something to discover at the first enrol.
require_key_when_active(REQUIRED_LEVEL, mfa_configured())


def policy_active() -> bool:
    """Whether this deployment requires a second factor of anyone."""
    return REQUIRED_LEVEL is not None


def required_min_role_name() -> Optional[str]:
    """
    The configured threshold as a role name, for showing the policy to a user.

    Read back from the resolved level rather than from the config string, so
    what is displayed is what is being enforced - a display that re-read the
    setting could disagree with the running policy after an edit that has not
    been restarted into.

    :return: The role name, or ``None`` when the requirement is off.
    """
    if REQUIRED_LEVEL is None:
        return None
    return next(
        (name for name, level in ROLE_ACCESS_LEVELS.items() if level == REQUIRED_LEVEL),
        None,
    )


def required_for_role(role_id: Optional[int]) -> bool:
    """
    Whether an account at this role must hold a second factor.

    An account with no role is treated as not covered: it cannot use the
    application either way, and the role checks refuse it before this matters.

    :param role_id: The account's access level.
    :return: ``True`` when the requirement applies.
    """
    if REQUIRED_LEVEL is None or role_id is None:
        return False
    return role_id >= REQUIRED_LEVEL


def enrollment_required(role_id: Optional[int], mfa_enabled: bool) -> bool:
    """
    Whether this account is being held out of the application until it enrolls.

    :param role_id: The account's access level.
    :param mfa_enabled: Whether a confirmed factor already exists.
    :return: ``True`` when the account owes an enrollment.
    """
    return not mfa_enabled and required_for_role(role_id)
