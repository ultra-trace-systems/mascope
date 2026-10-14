from pydantic import BaseModel


class PairingConfig(BaseModel):
    """
    Configuration for the agent device-pairing flow.
    """

    # Services that may obtain a token via pairing: the on-instrument agents.
    # Deliberately excludes mascope_sdk (interactive users copy their token
    # from the web app) and internal services like file-converter.
    # instrument-agent is the File Agent embedded in a program that controls
    # the instrument; it pairs and uploads exactly as file-agent does, under a
    # name of its own so a paired machine says which of the two it runs.
    ALLOWED_SERVICES: tuple[str, ...] = (
        "tof-agent",
        "file-agent",
        "export-agent",
        "instrument-agent",
    )

    # User-facing code: 6 chars from an alphabet without ambiguous characters
    # (no 0/O, 1/I/L, A/E/U to avoid accidental words). 28^6 ~ 4.8e8
    # combinations for a 10-minute window; approval additionally requires an
    # authenticated editor, so the code is a rendezvous label, not a secret.
    CODE_LENGTH: int = 6
    CODE_ALPHABET: str = "BCDFGHJKMNPQRSTVWXYZ23456789"

    # Seconds a pending pairing stays approvable.
    CODE_TTL_SECONDS: int = 600
    # Seconds the agent has to pick up the token after approval.
    PICKUP_TTL_SECONDS: int = 300
    # Suggested agent poll interval, returned to the agent.
    POLL_INTERVAL_SECONDS: int = 5


pairing_settings = PairingConfig()
