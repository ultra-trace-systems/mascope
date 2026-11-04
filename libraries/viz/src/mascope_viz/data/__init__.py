"""The data layer: what a digest is about, and the data it reads."""

from .scope import Candidate, ResolvedScope, Scope, resolve
from .store import CampaignStore, StoreStatus, SyncReport, sync


__all__ = [
    "CampaignStore",
    "Candidate",
    "ResolvedScope",
    "Scope",
    "StoreStatus",
    "SyncReport",
    "resolve",
    "sync",
]
