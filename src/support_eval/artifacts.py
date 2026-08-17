"""Canonical, atomic artifact persistence for support evaluations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from uuid import uuid4
from pathlib import Path
from typing import Any, Iterable


def canonical_json(value: Any) -> str:
    """Serialize JSON reproducibly without losing Unicode content."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, (canonical_json(value) + "\n").encode("utf-8"))


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def atomic_write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    atomic_write_bytes(path, "".join(canonical_json(value) + "\n" for value in values).encode("utf-8"))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in {path}") from error


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL in {path} at line {line_number}") from error
        if not isinstance(value, dict):
            raise ValueError(f"JSONL record in {path} at line {line_number} must be an object")
        records.append(value)
    return records


def immutable_workspace(
    root: Path,
    snapshot: dict[str, Any],
    config: dict[str, Any],
) -> Path:
    """Create a fresh workspace while preserving deterministic run inputs."""
    snapshot_digest = content_digest(snapshot)
    config_digest = content_digest(config)
    runs = root / ".support_eval" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    workspace = runs / f"{snapshot_digest[:16]}-{uuid4().hex}"
    workspace.mkdir(exist_ok=False)
    atomic_write_json(
        workspace / "snapshot.json",
        {
            "snapshot": snapshot,
            "snapshot_digest": snapshot_digest,
            "config": config,
            "config_digest": config_digest,
        },
    )
    atomic_write_json(
        workspace / "manifest.json",
        {
            **snapshot,
            "run_id": workspace.name,
            "snapshot_digest": snapshot_digest,
            "config_digest": config_digest,
            "status": "created",
            "artifacts": {},
        },
    )
    return workspace


def publish_transactionally(root: Path, artifacts: dict[str, bytes]) -> None:
    staging = Path(tempfile.mkdtemp(prefix=".support-eval-publish-", dir=root))
    try:
        for name, content in artifacts.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        for name in artifacts:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / name, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
