"""
Generation of hand-over passwords.

Its own module, importable from both the user CRUD service and the password
service: the latter imports the former, so a shared helper living in either
would close a cycle.
"""

import secrets
import string


def generate_random_password(length: int = 16) -> str:
    """
    Generates a random password containing uppercase, lowercase, and digits.

    :param length: Length of the password.
    :return: A random password string.
    """
    choices = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
    ]
    characters = string.ascii_letters + string.digits
    choices.extend(secrets.choice(characters) for _ in range(length - 3))

    # Shuffle for randomness
    secrets.SystemRandom().shuffle(choices)

    return "".join(choices)
