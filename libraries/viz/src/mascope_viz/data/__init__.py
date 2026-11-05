"""The data layer: what a digest is about, the data it reads, and the
views every chart draws from (:mod:`.views`)."""

from . import views
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
    "views",
]
