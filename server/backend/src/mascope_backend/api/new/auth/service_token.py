"""
Shared secret authenticating the file-converter service on its Socket.IO namespace.

The file-converter is an internal, same-stack service with no user identity of
its own; it acts on behalf of whichever user triggered an upload or peak
detection. It proves it is the genuine converter - rather than an arbitrary
client that supplied the public ``X-Service-Name: file-converter`` string - by
presenting this secret when it opens the ``/file-converter`` namespace.

The secret is derived per-deployment from the JWT secret with the same HMAC
domain separation as the reset and verification token secrets, so no extra
secret file has to be provisioned: the backend and the converter run from the
same image and mount the same ``jwt_secret_key``, so both derive an identical
value while a client without the secret cannot.
"""

from mascope_backend.api.new.auth.config import _derive_token_secret


FILE_CONVERTER_SERVICE_TOKEN: str = _derive_token_secret("file-converter-service")
