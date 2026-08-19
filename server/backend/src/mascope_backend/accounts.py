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
