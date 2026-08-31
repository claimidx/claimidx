"""Claimidx — prior art for agents."""

__version__ = "0.5.6"

from .models import Claim, Fix, EvalSpec
from .fingerprint import fingerprint, normalize_error, classify
from .store import Store
from .dense import encode, decode
from .match import rank
from .query import ask, ingest
from .team import resolve_owner, whoami

__all__ = [
    "Claim",
    "Fix",
    "EvalSpec",
    "ask",
    "ingest",
    "fingerprint",
    "normalize_error",
    "classify",
    "Store",
    "encode",
    "decode",
    "rank",
    "resolve_owner",
    "whoami",
    "__version__",
]
