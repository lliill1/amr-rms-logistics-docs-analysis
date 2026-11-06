"""Поддельная LLM для тестов: без Ollama, детерминированно. Разбирает фрагмент простыми правилами,
настроенными на вымышленный демо-архив (scripts/make_demo_archive.py)."""
from __future__ import annotations

import hashlib
import re

import numpy as np

SYSTEM_WORDS = [("SAP EWM", r"\bEWM\b"), ("MES", r"\bMES\b"), ("ERP", r"\bERP\b"),
                ("RMS", r"\bRMS\b|[Сс]истема управления роботами"), ("AMR", r"\bAMR\b|робот"),
                ("Оператор", r"оператор|водител"), ("PLC / автоматика", r"контроллер")]


def _sentences(fragment: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", fragment) if s.strip()]


def _system(text: str) -> str | None:
    for name, rx in SYSTEM_WORDS:
        if re.search(rx, text):
            return name
    return None


class FakeLLM:
    chat_model = "fake-chat"
    embed_model = "fake-embed"

    def __init__(self):
        self.prompts: list[str] = []

    def chat(self, prompt: str, schema=None) -> str:
        self.prompts.append(prompt)
        return "По актуальной редакции задание формирует SAP EWM [1]."

    def chat_json(self, prompt: str, schema):
        self.prompts.append(prompt)
        fragment = prompt.split('"""')[1] if '"""' in prompt else prompt
        props = (schema or {}).get("properties", {})
        if "processes" in props:
            return {"processes": self._processes(fragment)}
        if "exchanges" in props:
            return self._integration(fragment)
        return {"requirements": self._requirements(fragment)}

    # --- требования (как в версии 1) ---
    def _requirements(self, fragment: str) -> list[dict]:
        items = []
        for sentence in _sentences(fragment):
            if re.search(r"должн|желательно", sentence, re.IGNORECASE):
                items.append({
                    "text": sentence, "quote": sentence,
                    "target": "RMS" if "RMS" in sentence or "управления роботами" in sentence else "AMR",
                    "category": "Функции",
                    "obligation": "желательно" if "елательно" in sentence else "обязательно",
                })
        # «выдуманное» требование — должно быть отброшено проверкой цитаты
        items.append({"text": "Робот должен летать", "quote": "Робот обязан летать над складом на высоте 10 м",
                      "target": "AMR", "category": "Функции", "obligation": "обязательно"})
        return items

    # --- процессы ---
    def _processes(self, fragment: str) -> list[dict]:
        out = []
        for s in _sentences(fragment):
            m = re.search(r"Процесс «([^»]+)» выполняется роботом AMR: (.+)", s)
            if m:
                steps = [{"actor": _system(part) or "Другое", "action": part.strip(" .")} for part in m.group(2).split(", ")]
                out.append({"name": m.group(1), "trigger": "вызов оператора на терминале", "initiator": "Оператор",
                            "amr_role": "роботизирован", "from_location": "Зона комплектации склада",
                            "to_location": "Зона подачи PSA участка сборки", "cargo": "тележка с комплектующими",
                            "steps": steps, "quote": s[:280]})
            m = re.search(r"^(Вывоз [^.]+?) с ([^.]+?) в ([^.]+?) выполняется вручную", s)
            if m:
                out.append({"name": m.group(1).capitalize(), "trigger": "", "initiator": "Оператор", "amr_role": "вручную",
                            "from_location": m.group(2).replace("участка", "Участок"),
                            "to_location": m.group(3).replace("зону", "Зона"), "cargo": "пустая тара",
                            "steps": [{"actor": "Оператор", "action": "водитель погрузчика вывозит тару"}], "quote": s})
        # «выдуманный» процесс без цитаты в тексте
        out.append({"name": "Телепортация паллет", "initiator": "AMR", "amr_role": "роботизирован", "steps": [],
                    "quote": "Паллеты телепортируются между цехами"})
        return out

    # --- обмены и ответственность ---
    def _integration(self, fragment: str) -> dict:
        ex, resp = [], []
        for s in _sentences(fragment):
            if re.search(r"обсудили|вынесен", s, re.IGNORECASE):
                continue   # нерешённый вопрос — не факт
            m = re.search(r"задани\w*.*?формирует (SAP EWM|EWM|MES)", s)
            if m:
                resp.append({"function": "Формирование транспортного задания", "system": _system(m.group(1)), "quote": s})
            m = re.search(r"Подтверждение доставки фиксируется в (EWM|MES)", s)
            if m:
                resp.append({"function": "Подтверждение выполнения перевозки", "system": _system(m.group(1)), "quote": s})
            if re.search(r"RMS должна обеспечивать обход препятствий", s):
                resp.append({"function": "Маршрутизация и управление движением", "system": "RMS", "quote": s})
            if re.search(r"RMS назначает", s):
                resp.append({"function": "Назначение робота на задание", "system": "RMS", "quote": s})
            if re.search(r"принимать транспортные задания от EWM", s):
                ex.append({"source": "SAP EWM", "target": "RMS", "message": "транспортное задание", "quote": s})
                ex.append({"source": "RMS", "target": "SAP EWM", "message": "статус выполнения", "quote": s})
            if re.search(r"RMS передаёт роботу AMR", s):
                ex.append({"source": "RMS", "target": "AMR", "message": "маршрут и команда на выполнение", "quote": s})
            if re.search(r"контроллер\w* ворот", s):
                ex.append({"source": "RMS", "target": "PLC / автоматика", "message": "команда на открытие ворот", "quote": s})
        # «выдуманный» обмен
        ex.append({"source": "ERP", "target": "AMR", "message": "прямое управление", "quote": "ERP напрямую управляет роботами"})
        return {"exchanges": ex, "responsibilities": resp}

    def embed(self, texts, batch: int = 16):
        dim = 64
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w{4,}", t.lower()):
                h = int(hashlib.md5(w[:6].encode()).hexdigest(), 16)
                out[i, h % dim] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.clip(norms, 1e-9, None)
