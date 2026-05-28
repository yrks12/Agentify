"""Per-site registry: load/save recipes/<slug>.tools.json, convert to OpenAI tools."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .recipe import Recipe


DEFAULT_RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"


@dataclass
class SiteRegistry:
    site: str
    base_url: str
    tools: list[Recipe] = field(default_factory=list)
    mapped_at: str = ""

    def find(self, name: str) -> Optional[Recipe]:
        for r in self.tools:
            if r.name == name:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "base_url": self.base_url,
            "mapped_at": self.mapped_at or _dt.datetime.utcnow().isoformat() + "Z",
            "tools": [t.to_dict() for t in self.tools],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SiteRegistry":
        return cls(
            site=d["site"],
            base_url=d.get("base_url", ""),
            tools=[Recipe.from_dict(t) for t in d.get("tools", [])],
            mapped_at=d.get("mapped_at", ""),
        )


def registry_path(slug: str, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> Path:
    return recipes_dir / f"{slug}.tools.json"


def load(slug: str, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> SiteRegistry:
    path = registry_path(slug, recipes_dir)
    with open(path, "r", encoding="utf-8") as f:
        return SiteRegistry.from_dict(json.load(f))


def save(registry: SiteRegistry, recipes_dir: Path = DEFAULT_RECIPES_DIR) -> Path:
    recipes_dir.mkdir(parents=True, exist_ok=True)
    path = registry_path(registry.site, recipes_dir)
    if not registry.mapped_at:
        registry.mapped_at = _dt.datetime.utcnow().isoformat() + "Z"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry.to_dict(), f, indent=2)
    return path


def to_openai_tools(registry: SiteRegistry) -> list[dict]:
    """Translate each Recipe to the OpenAI function-calling tool shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": r.name,
                "description": r.description,
                "parameters": r.parameters or {"type": "object", "properties": {}},
            },
        }
        for r in registry.tools
    ]
