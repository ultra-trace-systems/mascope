"""
Non-finite floats in payloads sent to the browser.

NaN and +/-Infinity are Python floats but not JSON. A strict encoder
(Starlette's ``JSONResponse`` renders with ``allow_nan=False``) raises on
them, and a lenient one (the stdlib default) writes the bare literals ``NaN``
and ``Infinity``, which ``JSON.parse`` in the browser rejects. Both transports
map them to ``null`` with :func:`non_finite_to_none`: the Socket.IO codec on
every packet, an ``@api_route`` response only when its strict render fails.
"""

import math
from typing import Any


def non_finite_to_none(value: Any) -> Any:
    """
    Replace every non-finite float in a JSON-shaped payload with ``None``.

    Walks dict values, lists and tuples; a tuple comes back as a list, which
    is what JSON makes of it anyway. Only floats are mapped, numpy's
    ``float64`` included since it subclasses ``float``. Text is left alone,
    so a formula string "NaN" survives, and so are dict keys.

    :param value: A payload of dicts, lists, tuples and scalars
    :type value: Any
    :return: The payload with NaN and +/-Infinity replaced by ``None``
    :rtype: Any
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: non_finite_to_none(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [non_finite_to_none(item) for item in value]
    return value
