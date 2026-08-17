"""
Breached-password blocklist used by the password policy.

Kept in its own module so it can be loaded and tested without constructing a
``UserManager``. The data file is generated - see
``tooling/vendor-common-passwords.py`` for its provenance and licence.
"""

from functools import lru_cache
from pathlib import Path


#: Generated alongside the frontend's smaller copy; see the file's own header.
COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")


@lru_cache(maxsize=1)
def load_common_passwords() -> frozenset[str]:
    """
    Load the blocklist into memory, once per process.

    :return: Lowercased entries.
    """
    entries = set()
    with COMMON_PASSWORDS_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            entry = line.strip()
            if entry and not entry.startswith("#"):
                entries.add(entry)
    return frozenset(entries)


def is_common_password(password: str) -> bool:
    """
    Whether a password appears in the blocklist, ignoring case.

    Normalises with ``lower()`` rather than ``casefold()``: the frontend mirror
    checks the same way through JavaScript's ``toLowerCase()``, and the two must
    agree. Surrounding whitespace is left alone - a space is a real character in
    a password.

    :param password: Candidate plaintext password.
    :return: True when the password is a known common or breached one.
    """
    return password.lower() in load_common_passwords()
