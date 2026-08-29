"""Claimidx — prior art for agents."""

__version__ = "0.4.1"

from .models import Claim, Fix, EvalSpec
from .fingerprint import fingerprint, normalize_error, classify
from .store import Store
from .dense import encode, decode
from .match import rank
from .team import resolve_owner, whoami

__all__ = [
    "Claim",
    "Fix",
    "EvalSpec",
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
