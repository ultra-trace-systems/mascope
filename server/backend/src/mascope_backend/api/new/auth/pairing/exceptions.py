from fastapi import HTTPException, status


class PairingCodeInvalidException(HTTPException):
    """The pairing code does not exist or has expired."""

    def __init__(
        self,
        detail: str = "Pairing code not found or expired. "
        "Ask the agent for a fresh code and try again.",
    ):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class PairingCodeAlreadyApprovedException(HTTPException):
    """The pairing code was already approved."""

    def __init__(
        self,
        detail: str = "This pairing code has already been approved.",
    ):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class PairingCodeUnavailableException(HTTPException):
    """No free pairing code could be drawn; the agent should start again."""

    def __init__(
        self,
        detail: str = "No pairing code is free right now. Please try again.",
    ):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


class PairingServiceNotAllowedException(HTTPException):
    """The requested service cannot obtain tokens via pairing."""

    def __init__(self, service_name: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Service '{service_name}' cannot be paired.",
        )
