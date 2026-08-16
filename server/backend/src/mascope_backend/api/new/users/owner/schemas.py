from typing import Literal

from pydantic import BaseModel, Field


class RequirePasswordChange(BaseModel):
    """Acknowledgement body for the deployment-wide password-change trigger."""

    #: ``Literal[True]`` rather than ``bool`` plus a validator: it says the same
    #: thing in the type, and narrows what counts as acknowledgement. The
    #: validator it replaces ran after pydantic had already coerced, and a plain
    #: ``bool`` field accepts ``"true"``, ``"yes"`` and ``"on"`` - so a client
    #: sending a string could fire a deployment-wide action.
    #:
    #: Not absolute: JSON ``1`` still coerces to ``True`` here. A literal schema
    #: cannot carry ``strict`` (pydantic raises at schema build), so refusing it
    #: would mean ``StrictBool`` plus a validator again. Both ``1`` and ``true``
    #: are deliberate values, and the case this field exists for is the empty or
    #: accidental request.
    confirm: Literal[True] = Field(
        ...,
        description=(
            "Must be true. Required so the endpoint cannot be fired by an empty "
            "or accidental request; the consequences are spelled out in the "
            "interface that offers it."
        ),
    )
