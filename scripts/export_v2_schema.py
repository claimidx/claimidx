"""Generate or verify the public Claimidx protocol v2 JSON Schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

from claimidx.graph import Bundle, Failure, Observation, Proof, ProtocolEvent, Relation, Remedy

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "schema" / "protocol.v2.json"
Envelope = Failure | Remedy | Proof | Observation | Relation | ProtocolEvent | Bundle


def render() -> str:
    schema = TypeAdapter(Envelope).json_schema(mode="validation")
    schema["$id"] = "https://github.com/claimidx/claimidx/blob/main/schema/protocol.v2.json"
    schema["title"] = "Claimidx protocol v2 envelope"
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the checked-in schema is stale")
    args = parser.parse_args(argv)
    expected = render()
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if args.check:
        if current != expected:
            print(f"stale: {TARGET.relative_to(ROOT)}", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
