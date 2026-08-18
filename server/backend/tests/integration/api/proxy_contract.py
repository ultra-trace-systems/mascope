"""The emulated nginx proxy contract, shared by the origin security suites.

Both the Socket.IO handshake tests and the REST write-origin guard tests
drive the app the way production traffic arrives: through the headers the
shipped nginx attaches. Holding the emulation in one place keeps the two
surfaces tested against the same proxy model - the drift these paired suites
exist to catch would otherwise creep into the tests themselves.
"""

#: The fake public hostname production cases are served under.
PROD_HOST = "mascope.example.com"

#: What proxy_pass leaves as the Host: the upstream block's name, not the
#: browser's. Driving the production-shaped cases through this makes them
#: depend on X-Forwarded-Host rather than passing by accident.
UPSTREAM_URL = "http://backend"

#: Headers nginx attaches, from which the backend rebuilds the browser-visible
#: origin. nginx overwrites X-Forwarded-Host outright; X-Forwarded-Proto it
#: normalizes to the two real schemes but honours from a client (for TLS
#: terminated in front of it), so only the proto half is client-influenceable.
PROXY_HEADERS = {
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": PROD_HOST,
}
