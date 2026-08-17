from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReplayProvider:
    """One-use captured responses, with no credentials or live-client construction."""
    name = "replay"

    def __init__(self, capture_dir: Path, model: str = "captured", base_url: str = "capture://local") -> None:
        self.model = model
        self.base_url = base_url
        self._responses = self._load(capture_dir)
        self._used: set[tuple[str, str | None]] = set()

    @staticmethod
    def _load(directory: Path) -> dict[tuple[str, str | None], Any]:
        path = directory / "captures.jsonl"
        if not path.exists():
            path = directory / "captures.json"
        if not path.exists():
            raise ValueError(f"replay capture not found: {directory}/captures.jsonl or captures.json")
        parsed = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if isinstance(parsed, dict):
            parsed = [{"stage": key.split("/", 1)[0], "case_id": key.split("/", 1)[1] if "/" in key else None, "response": value} for key, value in parsed.items()]
        if not isinstance(parsed, list):
            raise ValueError("replay capture must be an object or list of records")
        captures: dict[tuple[str, str | None], Any] = {}
        for item in parsed:
            if not isinstance(item, dict) or set(item) != {"stage", "case_id", "response"} or not isinstance(item["stage"], str) or (item["case_id"] is not None and not isinstance(item["case_id"], str)):
                raise ValueError("each capture must contain exactly stage, case_id, response")
            key = (item["stage"], item["case_id"])
            if key in captures:
                raise ValueError(f"duplicate replay capture for {key}")
            captures[key] = item["response"]
        return captures

    def complete(self, *, stage: str, case_id: str | None, prompt: str) -> Any:
        key = (stage, case_id)
        if key not in self._responses:
            raise ValueError(f"missing replay capture for stage={stage!r}, case_id={case_id!r}")
        if key in self._used:
            raise RuntimeError(f"replay capture reused for stage={stage!r}, case_id={case_id!r}")
        self._used.add(key)
        return self._responses[key]
