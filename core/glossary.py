"""Поиск терминов глоссария в тексте (с учётом русских окончаний)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .config import load_glossary

_LATIN_SHORT = re.compile(r"^[a-z0-9/]{1,4}$")


def term_regex(term: str) -> re.Pattern:
    """«мобильн робот» -> мобильн\\w*\\s+робот\\w*; короткие латинские сокращения — целым словом."""
    words = term.lower().split()
    parts = []
    for w in words:
        esc = re.escape(w)
        if _LATIN_SHORT.match(w):
            parts.append(rf"{esc}(?![a-zа-яё0-9])")
        else:
            parts.append(rf"{esc}[\w\-]*")
    return re.compile(r"(?<![\w])" + r"[\s\-]+".join(parts), re.IGNORECASE)


@dataclass
class GroupMatch:
    group: str
    title: str
    weight: float
    count: int


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, str, float, list[re.Pattern]]]:
    g = load_glossary().get("groups", {})
    return [(k, v.get("title", k), float(v.get("weight", 1)), [term_regex(t) for t in v.get("terms", [])])
            for k, v in g.items()]


def match_groups(text: str) -> list[GroupMatch]:
    text = text.lower()
    out = []
    for key, title, weight, patterns in _compiled():
        count = sum(len(p.findall(text)) for p in patterns)
        if count:
            out.append(GroupMatch(key, title, weight, count))
    return out


def relevance(text: str) -> tuple[int, float]:
    """Уровень релевантности AMR/RMS 0..3 и взвешенный счёт."""
    matches = match_groups(text)
    core = sum(m.count for m in matches if m.group in ("robots", "rms"))
    score = sum(m.weight * m.count for m in matches)
    if core >= 10:
        level = 3
    elif core >= 3:
        level = 2
    elif core >= 1 or score >= 8:
        level = 1
    else:
        level = 0
    return level, score


def is_relevant_fragment(text: str) -> bool:
    return any(m.group in ("robots", "rms", "transport") for m in match_groups(text))


def expand_query(query: str) -> list[str]:
    """Синонимы из глоссария для расширения поискового запроса."""
    syn = load_glossary().get("synonyms", {}) or {}
    q = query.lower()
    extra: list[str] = []
    for key, values in syn.items():
        if term_regex(key).search(q):
            extra.extend(values)
    return extra
