"""Поддельная LLM для тестов: без Ollama, детерминированно."""
from __future__ import annotations

import hashlib
import re

import numpy as np


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
        items = []
        for sentence in re.split(r"(?<=[.!?])\s+", fragment):
            if re.search(r"должн|желательно", sentence, re.IGNORECASE):
                items.append({
                    "text": sentence.strip(),
                    "quote": sentence.strip(),
                    "target": "RMS" if "RMS" in sentence or "управления роботами" in sentence else "AMR",
                    "category": "Функции",
                    "obligation": "желательно" if "елательно" in sentence else "обязательно",
                })
        # «выдуманное» требование — должно быть отброшено проверкой цитаты
        items.append({"text": "Робот должен летать", "quote": "Робот обязан летать над складом на высоте 10 м",
                      "target": "AMR", "category": "Функции", "obligation": "обязательно"})
        return {"requirements": items}

    def embed(self, texts, batch: int = 16):
        dim = 64
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w{4,}", t.lower()):
                h = int(hashlib.md5(w[:6].encode()).hexdigest(), 16)
                out[i, h % dim] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.clip(norms, 1e-9, None)
