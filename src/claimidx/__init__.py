"""Claimidx — prior art for agents."""

__version__ = "0.6.1"

from .dense import decode, encode
from .fingerprint import classify, fingerprint, normalize_error
from .graph import Bundle, Failure, Observation, Proof, ProofStep, ProtocolEvent, Relation, Remedy
from .identity import generate_identity, sign_record, verify_record
from .match import rank
from .models import Claim, EvalSpec, Fix
from .query import ask, ingest, verify
from .store import Store
from .team import resolve_owner, whoami

__all__ = [
    "Claim",
    "Fix",
    "EvalSpec",
    "Failure",
    "Remedy",
    "Proof",
    "ProofStep",
    "Observation",
    "Relation",
    "Bundle",
    "ProtocolEvent",
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
    "generate_identity",
    "sign_record",
    "verify_record",
    "__version__",
]
