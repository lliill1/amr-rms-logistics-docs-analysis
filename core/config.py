"""Загрузка настроек, глоссария и промптов."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class Settings:
    raw: dict[str, Any]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, dotted: str) -> Path:
        """Путь из настроек; относительные пути считаются от корня репозитория."""
        p = Path(self.get(dotted))
        return p if p.is_absolute() else ROOT / p

    @property
    def data_dir(self) -> Path:
        return self.path("paths.data_dir")

    @property
    def output_dir(self) -> Path:
        return self.path("paths.output_dir")

    @property
    def db_file(self) -> Path:
        return self.path("paths.db_file")


def load_settings(overrides: dict | None = None) -> Settings:
    raw = yaml.safe_load((CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8")) or {}
    local = CONFIG_DIR / "settings.local.yaml"
    if local.exists():
        raw = _deep_merge(raw, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    if overrides:
        raw = _deep_merge(raw, overrides)
    return Settings(raw)


@lru_cache(maxsize=1)
def load_glossary() -> dict:
    return yaml.safe_load((CONFIG_DIR / "glossary.yaml").read_text(encoding="utf-8")) or {}


def load_prompt(name: str) -> str:
    return (CONFIG_DIR / "prompts" / f"{name}.txt").read_text(encoding="utf-8")
