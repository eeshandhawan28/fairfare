"""Provider-agnostic LLM access.

Every agent asks for a completion by *agent name*. Which model answers is decided
by config/models.yaml (or the FAIRFARE_MODEL env var), so swapping Ollama for
DeepSeek, Moonshot or any OpenRouter model is a config change, not a code change.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

import yaml

DEFAULT_MODEL = "ollama/qwen2.5:7b"
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


class LLM(Protocol):
    def complete(self, agent: str, system: str, user: str) -> str: ...


class LiteLLMClient:
    def __init__(self, config_path: Path | None = None) -> None:
        path = config_path or DEFAULT_CONFIG
        self.config: dict[str, Any] = {}
        if path.exists():
            self.config = yaml.safe_load(path.read_text()) or {}

    def model_for(self, agent: str) -> str:
        override = os.getenv("FAIRFARE_MODEL")
        if override:
            return override
        agents = self.config.get("agents", {})
        return agents.get(agent) or self.config.get("default") or DEFAULT_MODEL

    def complete(self, agent: str, system: str, user: str) -> str:
        from litellm import completion  # imported lazily: keeps tests offline

        response = completion(
            model=self.model_for(agent),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            api_base=os.getenv("OLLAMA_API_BASE") or None,
        )
        return response.choices[0].message.content or ""


def extract_json(text: str) -> Any:
    """Pull the first JSON object or array out of a model reply."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                value, _ = decoder.raw_decode(text[i:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model output")
