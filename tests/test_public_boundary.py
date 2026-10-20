"""The tracked tree carries no private or business surface. The vocabulary lives in scripts/gate.py (sanitize stage)."""

from __future__ import annotations

import importlib.util

from claimidx.discovery import ROOT


def _gate():
    spec = importlib.util.spec_from_file_location("gate", ROOT / "scripts" / "gate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tracked_tree_has_no_private_or_business_surfaces() -> None:
    gate = _gate()
    assert gate.PRIVATE_PARTS >= {"enterprise", "pricing", "billing", "customer"}
    assert gate.sanitize_paths(gate.tracked_paths()) == []
