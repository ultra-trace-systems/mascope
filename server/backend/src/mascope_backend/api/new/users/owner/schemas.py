from typing import Literal

from pydantic import BaseModel, Field


class RequirePasswordChange(BaseModel):
    """Acknowledgement body for the deployment-wide password-change trigger."""

    #: ``Literal[True]`` rather than ``bool`` plus a validator: it says the same
    #: thing in the type, and is stricter than the validator it replaces, which
    #: inherited pydantic's lax bool coercion and so accepted ``1`` and
    #: ``"true"`` as acknowledgement.
    confirm: Literal[True] = Field(
        ...,
        description=(
            "Must be true. Required so the endpoint cannot be fired by an empty "
            "or accidental request; the consequences are spelled out in the "
            "interface that offers it."
        ),
    )
