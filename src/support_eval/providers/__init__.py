"""LLM provider protocol, resolution, live adapters, and strict replay."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from .deepseek import DeepSeekProvider
from .openai import OpenAIProvider
from .replay import ReplayProvider

OPENAI_MODEL = "gpt-5.6-terra"
DEEPSEEK_MODEL = "deepseek-v4-pro"


class Provider(Protocol):
    name: str
    model: str
    base_url: str

    def complete(self, *, stage: str, case_id: str | None, prompt: str) -> Any: ...


def _load_dotenv(root: Path) -> dict[str, str]:
    path = root / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            values[key] = value.strip().strip("'\"")
    return values


def resolve_provider(root: Path, provider_name: str | None = None, model: str | None = None, base_url: str | None = None, capture_dir: Path | None = None) -> Provider:
    if capture_dir is not None:
        return ReplayProvider(capture_dir, model=model or "captured", base_url=base_url or "capture://local")
    dotenv = _load_dotenv(root)
    def value(name: str) -> str | None:
        return os.environ.get(name) or dotenv.get(name)
    selected = (provider_name or value("EVAL_LLM_PROVIDER") or ("openai" if value("OPENAI_API_KEY") else None) or ("deepseek" if value("DEEPSEEK_API_KEY") else None))
    if selected not in {"openai", "deepseek"}:
        raise ValueError("no provider resolved: use --provider, EVAL_LLM_PROVIDER, OPENAI_API_KEY, or DEEPSEEK_API_KEY")
    if selected == "openai":
        key = value("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY is required for provider openai")
        return OpenAIProvider(key, model or OPENAI_MODEL, base_url or "https://api.openai.com/v1")
    key = value("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is required for provider deepseek")
    return DeepSeekProvider(key, model or DEEPSEEK_MODEL, base_url or "https://api.deepseek.com/v1")


def decode_response(response: Any) -> Any:
    if isinstance(response, (dict, list)):
        return response
    if not isinstance(response, str) or not response.strip():
        raise ValueError("provider returned an empty response")
    try:
        return json.loads(response)
    except json.JSONDecodeError as error:
        raise ValueError("provider returned malformed JSON") from error
