from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class ChatCompletionsTransport:
    """Small transport for providers implementing the OpenAI Chat API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(self, *, base_url: str, model: str, prompt: str, error_label: str) -> Any:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"{error_label} request failed with HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"{error_label} request failed") from error

        body = json.loads(raw)
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError(f"{error_label} response lacks message content") from error
