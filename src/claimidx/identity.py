"""Portable Ed25519 identity for signed Claimidx v2 records."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ED25519_PUB_CODEC = b"\xed\x01"


def _b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    chars = bytearray()
    while n:
        n, rem = divmod(n, 58)
        chars.append(_ALPHABET[rem])
    pad = len(raw) - len(raw.lstrip(b"\0"))
    return (b"1" * pad + bytes(reversed(chars or b"1"))).decode("ascii")


def _b58decode(value: str) -> bytes:
    n = 0
    for char in value.encode("ascii"):
        try:
            digit = _ALPHABET.index(char)
        except ValueError as exc:
            raise ValueError("invalid base58 key") from exc
        n = n * 58 + digit
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + raw


def did_from_public_key(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return "did:key:z" + _b58encode(_ED25519_PUB_CODEC + public_key)


def public_key_from_did(did: str) -> bytes:
    prefix = "did:key:z"
    if not did.startswith(prefix):
        raise ValueError("only did:key Ed25519 identities are cryptographically verifiable")
    raw = _b58decode(did[len(prefix) :])
    if not raw.startswith(_ED25519_PUB_CODEC) or len(raw) != 34:
        raise ValueError("unsupported did:key codec")
    return raw[2:]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_record(record: dict[str, Any]) -> bytes:
    payload = dict(record)
    payload.pop("signature", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def generate_identity(path: str | os.PathLike[str], *, overwrite: bool = False) -> dict[str, str]:
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    payload = {"v": "claimidx-key-1", "did": did_from_public_key(public_raw), "private": _b64(private_raw)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return {"v": payload["v"], "did": payload["did"], "path": str(target)}


def load_identity(path: str | os.PathLike[str]) -> tuple[str, Ed25519PrivateKey]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("v") != "claimidx-key-1":
        raise ValueError("unsupported Claimidx key version")
    private = Ed25519PrivateKey.from_private_bytes(_unb64(str(payload["private"])))
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    did = did_from_public_key(public_raw)
    if did != payload.get("did"):
        raise ValueError("identity public and private keys do not match")
    return did, private


def sign_record(record: dict[str, Any], path: str | os.PathLike[str]) -> dict[str, Any]:
    did, private = load_identity(path)
    signed = dict(record)
    signed["key_id"] = did
    signed["signature"] = ""
    signed["signature"] = _b64(private.sign(canonical_record(signed)))
    return signed


def verify_record(record: dict[str, Any]) -> bool:
    did = str(record.get("key_id") or "")
    signature = str(record.get("signature") or "")
    if not did or not signature:
        return False
    try:
        public = Ed25519PublicKey.from_public_bytes(public_key_from_did(did))
        public.verify(_unb64(signature), canonical_record(record))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
