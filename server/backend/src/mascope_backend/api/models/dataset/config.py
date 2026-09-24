"""
Dataset configuration settings.

Centralized configuration for dataset types and rules.
"""

from pydantic import BaseModel


class DatasetConfig(BaseModel):
    """
    Configuration settings for dataset types and rules.
    """

    DATASET_TYPES: list = ["ACQUISITION", "ANALYSIS"]

    # Default values
    DEFAULT_DATASET_TYPE: str = "ANALYSIS"
    DEFAULT_LOCKED_STATUS: int = 0

    # Additional configurable rules
    ACQUISITION_AUTO_LOCK: bool = True

    # Naming conventions
    ACQUISITION_NAME_PREFIX: str = "Acquisitions"


# Global dataset configuration instance
dataset_config = DatasetConfig()


def acquisition_workspace_name(instrument: str) -> str:
    """The name of an instrument's system acquisition workspace.

    Workspaces are found by it case-insensitively (``ix_workspace_name_ci`` is
    unique on its lower case), so every case variant of an instrument, and
    one with stray whitespace, shares one workspace.

    :param instrument: The instrument, as a file records it.
    :return: The workspace name.
    """
    return f"{dataset_config.ACQUISITION_NAME_PREFIX} {instrument.strip()}"
