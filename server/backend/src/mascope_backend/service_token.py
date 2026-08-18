"""
Per-deployment secrets derived from the JWT secret via HMAC domain separation.

Each derived secret is keyed with the deployment's JWT secret over a stable
purpose label, making it unique per deployment, unpredictable, and
cryptographically independent of the other derived secrets and of the JWT
signing key itself - without extra secret files for operators to provision.

This module is a leaf on purpose: it imports only the runtime. The
file-converter service runs as a separate process from the backend application
and presents ``FILE_CONVERTER_SERVICE_TOKEN`` when it opens its Socket.IO
namespace; importing the token from here keeps that process free of the
backend's api/db/socket-server import graph (and their config and secret
requirements). ``mascope_backend.api.new.auth.config`` builds its derived
secrets through :func:`derive_token_secret` as well.
"""

import hashlib
import hmac

from mascope_backend.runtime import runtime


_jwt_secret_key = runtime.secret("JWT_SECRET_KEY_FILE", "jwt_secret_key.txt")


def derive_token_secret(purpose: str) -> str:
    """
    Derive a purpose-specific token secret from the deployment's JWT secret.

    :param purpose: Stable label separating one derived secret from another.
    :return: Hex-encoded 32-byte secret.
    """
    return hmac.new(
        _jwt_secret_key.encode("utf-8"), purpose.encode("utf-8"), hashlib.sha256
    ).hexdigest()


# The file-converter service proves it is the genuine converter - rather than an
# arbitrary client that supplied the public ``X-Service-Name: file-converter``
# string - by presenting this secret when it opens the ``/file-converter``
# namespace. The backend and the converter mount the same ``jwt_secret_key``, so
# both derive an identical value while a client without the secret cannot.
FILE_CONVERTER_SERVICE_TOKEN: str = derive_token_secret("file-converter-service")
