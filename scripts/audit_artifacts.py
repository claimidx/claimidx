"""Fail if a Claimidx wheel or sdist contains non-public material."""

from __future__ import annotations

import argparse
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_PATH_PARTS = {"enterprise", "pricing", "checkout", "customer", "billing", "social", "worker", "bot", "tests"}
FORBIDDEN_TEXT = (
    "remedy" + "ai",
    "rdna" + "vm",
    "old-" + "remedy",
    "sales" + "@claimidx.com",
    "support" + "@claimidx.com",
    "contact" + "@claimidx.com",
    "security" + "@claimidx.com",
    "home.claimidx.com/" + "operator",
)
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def _members(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [(name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")]
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            out = []
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                source = archive.extractfile(member)
                if source is not None:
                    out.append((member.name, source.read()))
            return out
    raise ValueError(f"unsupported artifact: {path}")


def audit(path: Path) -> list[str]:
    errors: list[str] = []
    names: list[str] = []
    for raw_name, payload in _members(path):
        name = raw_name.replace("\\", "/")
        names.append(name)
        parts = {part.lower() for part in PurePosixPath(name).parts}
        bad_parts = sorted(parts & FORBIDDEN_PATH_PARTS)
        if bad_parts:
            errors.append(f"{name}: forbidden path component {bad_parts}")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        lowered = text.lower()
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in lowered:
                errors.append(f"{name}: forbidden text {forbidden!r}")
        emails = {value.lower() for value in EMAIL.findall(text)}
        unexpected = {value for value in emails if not value.endswith("@example.com") and "@users.noreply.github.com" not in value}
        if unexpected:
            errors.append(f"{name}: unexpected email addresses {sorted(unexpected)}")
    if path.suffix == ".whl":
        for required in ("claimidx/graph.py", "claimidx/proofs.py", "claimidx/identity.py"):
            if not any(name.endswith(required) for name in names):
                errors.append(f"missing {required}")
    else:
        if not any(name.endswith("/schema/protocol.v2.json") for name in names):
            errors.append("missing schema/protocol.v2.json")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(argv)
    failed = False
    for artifact in args.artifacts:
        errors = audit(artifact)
        if errors:
            failed = True
            for error in errors:
                print(f"{artifact}: {error}")
        else:
            print(f"ok: {artifact}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
