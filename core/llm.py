"""Клиент локальной LLM (Ollama). Всё общение — только с localhost (или адресом из настроек).

Чтобы перейти на сервер (vLLM/Ollama на GPU-сервере), достаточно поменять llm.base_url и модель в настройках.
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

import numpy as np
import requests


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def chat(self, prompt: str, schema: dict | None = None) -> str: ...
    def chat_json(self, prompt: str, schema: dict) -> Any: ...
    def embed(self, texts: list[str]) -> np.ndarray: ...


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", chat_model: str = "qwen3:8b",
                 embed_model: str = "bge-m3", temperature: float = 0.1, num_ctx: int = 8192, timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.timeout = timeout
        # прокси из окружения Windows не должен перехватывать запросы к локальной модели
        self.session = requests.Session()
        self.session.trust_env = False

    @classmethod
    def from_settings(cls, settings) -> "OllamaClient":
        return cls(
            base_url=settings.get("llm.base_url", "http://localhost:11434"),
            chat_model=settings.get("llm.chat_model", "qwen3:8b"),
            embed_model=settings.get("llm.embed_model", "bge-m3"),
            temperature=float(settings.get("llm.temperature", 0.1)),
            num_ctx=int(settings.get("llm.num_ctx", 8192)),
            timeout=int(settings.get("llm.timeout_sec", 600)),
        )

    # --- служебное ---
    def status(self) -> dict:
        """{'ok': bool, 'models': [...], 'missing': [...], 'error': str|None}"""
        try:
            r = self.session.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "models": [], "missing": [self.chat_model, self.embed_model], "error": str(e)}

        def has(model: str) -> bool:
            base = model if ":" in model else model + ":latest"
            return model in names or base in names

        missing = [m for m in (self.chat_model, self.embed_model) if not has(m)]
        return {"ok": not missing, "models": names, "missing": missing, "error": None}

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self.session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise LLMError(f"Ollama недоступна по адресу {self.base_url}: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Ollama вернула {r.status_code}: {r.text[:300]}")
        return r.json()

    # --- генерация ---
    def chat(self, prompt: str, schema: dict | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self.chat_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,  # для qwen3: без «рассуждений» — быстрее и чище JSON
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if schema is not None:
            payload["format"] = schema
        data = self._post("/api/chat", payload)
        return strip_think(data.get("message", {}).get("content", ""))

    def chat_json(self, prompt: str, schema: dict) -> Any:
        return parse_json(self.chat(prompt, schema=schema))

    # --- эмбеддинги ---
    def embed(self, texts: list[str], batch: int = 16) -> np.ndarray:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch):
            data = self._post("/api/embed", {"model": self.embed_model, "input": texts[i:i + batch]})
            vectors.extend(data.get("embeddings", []))
        arr = np.asarray(vectors, dtype=np.float32)
        if arr.size == 0:
            return arr.reshape(0, 0)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.clip(norms, 1e-9, None)


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    return _THINK.sub("", text).strip()


def parse_json(text: str) -> Any:
    text = strip_think(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start >= 0:
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(f"модель вернула не JSON: {text[:200]}")
