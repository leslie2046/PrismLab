"""Compatibility helpers for provider records with typed model endpoints."""

from typing import Any


def enabled_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Missing flags in legacy records mean enabled; parent state never overwrites children."""
    if config.get("enabled", True) is False:
        return []
    return [model for model in configured_models(config) if model.get("enabled", True) is not False]


def configured_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_models = config.get("models") or []
    if raw_models and isinstance(raw_models[0], dict):
        return raw_models
    if raw_models:
        return [
            {"name": name, "benchmark": config["benchmark"], "base_url": config.get("base_url")}
            for name in raw_models
        ]
    if config.get("benchmark"):
        return [{
            "name": config.get("model"),
            "benchmark": config["benchmark"],
            "base_url": config.get("base_url"),
        }]
    return []
