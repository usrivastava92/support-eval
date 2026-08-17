from __future__ import annotations

from typing import Any

from ._chat_completions import ChatCompletionsTransport


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self._transport = ChatCompletionsTransport(api_key)
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, *, stage: str, case_id: str | None, prompt: str) -> Any:
        return self._transport.complete(
            base_url=self.base_url,
            model=self.model,
            prompt=prompt,
            error_label="DeepSeek",
        )
