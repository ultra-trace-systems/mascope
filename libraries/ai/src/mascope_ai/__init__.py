"""mascope_ai - Mascope's toolkit for AI agents.

Handles, not tables: ``find`` returns references, ``describe`` small dicts,
and the loaders :class:`Frame` handles whose repr is a bounded summary and
whose rows stay in Python. The oracle - ``query_by_mz`` and
``match_compounds`` - answers with Mascope's candidates and condensed
verdicts, and every call and answer is written to the session's provenance
manifest. See ``docs/dev/ai_layer.md`` for the design.
"""

from importlib.metadata import version

from .errors import (
    AccessDenied,
    AmbiguousName,
    InvalidRequest,
    NotAuthenticated,
    NotFound,
    RowCapExceeded,
    StaleFrame,
    ToolkitError,
    Unavailable,
)
from .frame import FailureNote, Frame
from .manifest import Manifest
from .oracle import (
    Candidate,
    Candidates,
    IonVerdict,
    MatchResult,
    Unscored,
    Verdict,
)
from .refs import KINDS, Kind, Ref
from .session import Session
from .toolkit import DEFAULT_MAX_ROWS, Found, Toolkit


__version__ = version("mascope_ai")

__all__ = [
    "Toolkit",
    "Found",
    "DEFAULT_MAX_ROWS",
    "Ref",
    "Kind",
    "KINDS",
    "Frame",
    "FailureNote",
    "Session",
    "Manifest",
    "Candidate",
    "Candidates",
    "MatchResult",
    "Verdict",
    "IonVerdict",
    "Unscored",
    "ToolkitError",
    "AmbiguousName",
    "NotFound",
    "RowCapExceeded",
    "StaleFrame",
    "AccessDenied",
    "NotAuthenticated",
    "InvalidRequest",
    "Unavailable",
]
