"""Resource modules for the Mascope SDK.

Each resource module provides methods for interacting with a specific
area of the Mascope API.
"""

from .batches import BatchesResource
from .cheminfo import ChemInfoResource
from .datasets import DatasetsResource
from .ionization import IonizationResource
from .matching import MatchingResource
from .ms2 import Ms2Resource
from .sample_files import SampleFilesResource
from .samples import SamplesResource
from .workspaces import WorkspacesResource


__all__ = [
    "BatchesResource",
    "ChemInfoResource",
    "IonizationResource",
    "MatchingResource",
    "Ms2Resource",
    "SampleFilesResource",
    "SamplesResource",
    "DatasetsResource",
    "WorkspacesResource",
]
