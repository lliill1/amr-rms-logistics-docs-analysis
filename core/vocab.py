"""Справочник модели логистики (config/model.yaml): системы, функции, роли AMR, обязательные обмены."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from .config import CONFIG_DIR

OTHER = "Другое"


@dataclass(frozen=True)
class Function:
    key: str
    title: str
    required: bool


@dataclass
class Vocab:
    systems: list[str]
    tiers: dict[str, int]
    aliases: dict[str, list[str]]
    functions: list[Function]
    amr_roles: list[str]
    required_links: list[dict] = field(default_factory=list)

    @property
    def function_titles(self) -> list[str]:
        return [f.title for f in self.functions]

    def function_by_key(self, key: str) -> Function | None:
        return next((f for f in self.functions if f.key == key), None)

    def function_key(self, value: str | None) -> str | None:
        """Ключ функции по ключу или по названию (модель отвечает названием)."""
        if not value:
            return None
        v = value.strip().lower()
        for f in self.functions:
            if v in (f.key.lower(), f.title.lower()):
                return f.key
        return None

    def system(self, value: str | None) -> str | None:
        """Каноническое название системы по любому написанию; неизвестное — «Другое», пустое — None."""
        if not value or not str(value).strip():
            return None
        v = str(value).strip().lower()
        for name in self.systems:
            if v == name.lower():
                return name
        for name in self.systems:
            for a in self.aliases.get(name, []):
                if re.search(r"(?<![\w])" + re.escape(a), v):
                    return name
        return OTHER if OTHER in self.systems else None

    def amr_role(self, value: str | None) -> str:
        v = (value or "").strip().lower()
        return v if v in self.amr_roles else (self.amr_roles[-1] if self.amr_roles else "")


@lru_cache(maxsize=1)
def load_vocab() -> Vocab:
    raw = yaml.safe_load((CONFIG_DIR / "model.yaml").read_text(encoding="utf-8")) or {}
    systems_raw = raw.get("systems") or {}
    systems = list(systems_raw)
    if OTHER not in systems:
        systems.append(OTHER)
    tiers = {k: int((v or {}).get("tier", 1)) for k, v in systems_raw.items()}
    tiers.setdefault(OTHER, 1)
    aliases = {k: [a.lower() for a in ((v or {}).get("aliases") or [])] for k, v in systems_raw.items()}
    functions = [Function(k, (v or {}).get("title", k), bool((v or {}).get("required", False)))
                 for k, v in (raw.get("functions") or {}).items()]
    return Vocab(systems=systems, tiers=tiers, aliases=aliases, functions=functions,
                 amr_roles=list(raw.get("amr_roles") or []), required_links=list(raw.get("required_links") or []))
