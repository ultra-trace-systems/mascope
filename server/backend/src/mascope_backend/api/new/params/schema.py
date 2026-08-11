from pydantic import BaseModel

from mascope_backend.api.new.cheminfo.config import ChemInfoConfig
from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    PeakAssignmentLimits,
)
from mascope_match.params import MatchParams


class Params(BaseModel):
    match: MatchParams = MatchParams()
    cheminfo_config: ChemInfoConfig = ChemInfoConfig()
    # Defaults and bounds for the peak-assignment run config, so the launcher
    # form starts from what the engine would use anyway and cannot offer a value
    # the API will reject.
    peak_assignment: PeakAssignmentConfig = PeakAssignmentConfig()
    peak_assignment_limits: PeakAssignmentLimits = PeakAssignmentLimits()
