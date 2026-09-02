"""Additive diagnostic feature plugins.

Plugins can enrich v2 matching evidence.  They cannot alter v1 fingerprints,
classification, admission policy, or proof execution.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Protocol


class FeatureExtractor(Protocol):
    name: str
    version: str

    def extract(self, raw_error: str, context: dict[str, Any]) -> dict[str, Any]: ...


def installed_extractors() -> list[FeatureExtractor]:
    found: list[FeatureExtractor] = []
    for entry in entry_points(group="claimidx.extractors"):
        try:
            plugin = entry.load()
            candidate: Any = plugin() if isinstance(plugin, type) else plugin
            extract: Any = getattr(candidate, "extract", None)
            name: Any = getattr(candidate, "name", None)
            if callable(extract) and bool(name):
                found.append(candidate)
        except Exception:
            # Retrieval must remain available when an optional plugin is broken.
            continue
    return found


def extract_plugin_features(raw_error: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for plugin in installed_extractors():
        try:
            value = plugin.extract(raw_error, dict(context or {}))
            if isinstance(value, dict):
                out[f"{plugin.name}@{plugin.version}"] = value
        except Exception as exc:
            out[f"{plugin.name}@{plugin.version}"] = {"error": type(exc).__name__}
    return out


def plugin_inventory() -> list[dict[str, str]]:
    return [{"name": str(plugin.name), "version": str(plugin.version), "scope": "additive-features-only"} for plugin in installed_extractors()]
