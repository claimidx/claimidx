"""Claimidx — prior art for agents."""

__version__ = "0.5.9"

from .dense import decode, encode
from .fingerprint import classify, fingerprint, normalize_error
from .match import rank
from .models import Claim, EvalSpec, Fix
from .query import ask, ingest, verify
from .store import Store
from .team import resolve_owner, whoami

__all__ = [
    "Claim",
    "Fix",
    "EvalSpec",
    "ask",
    "ingest",
    "verify",
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
