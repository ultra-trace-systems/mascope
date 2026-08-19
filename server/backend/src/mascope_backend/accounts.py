"""
Account kinds: a person, or a machine.

Kept deliberately import-light (no secrets, no ORM) so it can be imported from
anywhere - schemas, the login path, the pairing service - without pulling the
rest of the auth package in behind it, the same reason ``roles.py`` is kept
lean.

A ``person`` account is the default and behaves exactly as every account did
before machine accounts existed. A ``machine`` account is the subject of an
instrument agent's credential: it never signs in interactively, holds no
password anyone knows, is capped at the editor role, and is exempt from the
password-change and second-factor requirements that only make sense for a human
at a browser. It is created by pairing approval and vouched for by a sponsor
(the approving user), recorded on the device rather than on the account.
"""

ACCOUNT_TYPE_PERSON = "person"
ACCOUNT_TYPE_MACHINE = "machine"


def refuse_machine_account(user) -> None:
    """
    Reject an attempt to manage a machine account as a human user.

    Machine accounts are created, and only ever removed, through the pairing
    and device flows; letting the human user routes rename, re-role, deactivate,
    delete or strip the credentials of one would break the agent behind it or
    dislodge the attribution other records point at. It would also side-step the
    sponsor ceiling device revocation enforces: every machine account sits at
    editor regardless of who sponsors its device, so an admin-level check on the
    target's own role never sees an owner's agent as out of reach.

    Lives here, beside the account kinds themselves, so every user-management
    mutation can reach it - ``users.util`` cannot, because importing the auth
    exceptions from there closes an import cycle through ``auth.__init__``.

    :param user: The target account - an ORM row or a read schema; only
        ``account_type`` is read.
    :raises ForbiddenAccessException: When the account is a machine account.
    """
    # Imported here rather than at module scope to keep this module free of the
    # auth package, whose __init__ imports the user manager, which imports the
    # very services that call this.
    from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException

    if user.account_type == ACCOUNT_TYPE_MACHINE:
        raise ForbiddenAccessException(
            "This is a machine (instrument agent) account. Manage it through "
            "Paired machines, not user management."
        )
