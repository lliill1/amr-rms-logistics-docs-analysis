"""Восстановление маршрута процесса («откуда → куда»), если модель его не заполнила.

Небольшие модели (qwen3:8b) часто оставляют from_location / to_location пустыми, хотя маршрут есть
прямо в названии процесса («Подвоз материалов со склада к участкам сборки») или в цитате
(«…забирает тележку в зоне комплектации склада и доставляет её в зону подачи PSA…»).
Здесь — простые правила русского языка для типичных мест внутризаводской логистики.
Найденный маршрут записывается в процесс и дальше правится аналитиком как обычно.
"""
from __future__ import annotations

import re

# основа слова → (единственное число, множественное число) в именительном падеже
PLACES: dict[str, tuple[str, str]] = {
    "склад": ("Склад", "Склады"),
    "участ": ("Участок", "Участки"),
    "зон": ("Зона", "Зоны"),
    "буфер": ("Буфер", "Буферы"),
    "цех": ("Цех", "Цеха"),
    "лини": ("Линия", "Линии"),
    "ворот": ("Ворота", "Ворота"),
    "площадк": ("Площадка", "Площадки"),
    "стеллаж": ("Стеллаж", "Стеллажи"),
    "станци": ("Станция", "Станции"),
    "рабоч": ("Рабочее место", "Рабочие места"),
    "конвейер": ("Конвейер", "Конвейеры"),
    "рамп": ("Рампа", "Рампы"),
    "корпус": ("Корпус", "Корпуса"),
    "пост": ("Пост", "Посты"),
    "ячейк": ("Ячейка", "Ячейки"),
    "psa": ("PSA", "PSA"),
}
_STEM_RX = "|".join(sorted(PLACES, key=len, reverse=True))
# слово места + до 5 следующих слов (определения в родительном падеже), до знака препинания или союза
_STOP = (r"(?:и|а|где|затем|после|для|с|со|в|во|к|ко|на|по|из|от|до|через|при|если|вручную|роботом|роботами|"
         r"автоматически|[\wё]+(?:ется|ится|ются|ятся|ает|яет|ует|ает|ит|ет|ют|ут|ят|ал|ала|али|ит))")
_PHRASE = rf"((?:{_STEM_RX})[а-яёa-z]*(?:\s+(?!{_STOP}(?![\wё]))[\wё/\-]+){{0,5}})"
_FROM = re.compile(rf"(?<![\wё])(?:из|со|с|от)\s+{_PHRASE}", re.IGNORECASE)
_TO = re.compile(rf"(?<![\wё])(?:к|ко|до|на)\s+{_PHRASE}", re.IGNORECASE)
_IN = re.compile(rf"(?<![\wё])(?:в|во)\s+{_PHRASE}", re.IGNORECASE)
# окончания множественного числа у первого слова (складам, участкам, зонах, складов…)
_PLURAL = re.compile(r"(ам|ями|ах|ях|ов|ей|и|ы)$")
# предложный падеж («в зоне», «на складе») — место, где груз забирают; винительный («в зону») — куда везут
_LOCATIVE = re.compile(r"(е|и)$")


def _nominative(phrase: str) -> str:
    words = phrase.split()
    first = words[0].lower()
    stem = next((s for s in sorted(PLACES, key=len, reverse=True) if first.startswith(s)), None)
    if stem is None:
        return phrase.strip()
    ending = first[len(stem):]
    sing, plur = PLACES[stem]
    plural = bool(_PLURAL.search(ending)) and not (stem in ("лини", "станци") and ending in ("и",)) and ending not in ("а", "у", "е", "ом", "ой", "ю")
    if stem in ("лини", "станци", "зон", "площадк", "ячейк", "рамп") and ending in ("и", "ы"):
        plural = False                     # «линии сборки», «зоны» в родительном ед. числе — чаще единственное
    if stem == "участ" and ending in ("ка", "ку", "ке", "ком", "ок"):
        plural = False
    if stem == "рабоч" and ending in ("им", "их", "ими", "ие"):
        plural = True
    head = plur if plural else sing
    if stem == "рабоч" and len(words) > 1 and words[1].lower().startswith("мест"):
        words = words[1:]                  # «рабочим местам сборки» → «Рабочие места сборки»
    rest = " ".join(words[1:]).strip(" ,.;:")
    return f"{head} {rest}".strip() if head != "PSA" else phrase.strip(" ,.;:")


def guess_route(*texts: str) -> tuple[str | None, str | None]:
    """(откуда, куда) по первому тексту, где маршрут находится; None — если не нашли."""
    for text in texts:
        if not text:
            continue
        t = " ".join(str(text).split())
        src = _FROM.search(t)
        dst = _TO.search(t, src.end()) if src else _TO.search(t)
        if src and dst is None:            # «к рабочим местам сборки со склада» — «куда» раньше «откуда»
            dst = next((m for m in _TO.finditer(t) if m.end() <= src.start()), None)
        ins = list(_IN.finditer(t))
        if src is None:
            # «забирает в зоне комплектации … доставляет в зону подачи …»
            loc = next((m for m in ins if _LOCATIVE.search(m.group(1).split()[0].lower())), None)
            if loc:
                src = loc
        if dst is None:
            after = src.end() if src else 0
            dst = next((m for m in ins if m.start() >= after and m is not src
                        and not _LOCATIVE.search(m.group(1).split()[0].lower())), None)
        if src and dst:
            a, b = _nominative(src.group(1)), _nominative(dst.group(1))
            if a.lower() != b.lower():
                return a, b
    return None, None
