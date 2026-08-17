from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


class _FakeProvider:
    name = "fake"
    model = "fake-model"
    base_url = "fake://local"

    def __init__(self, case_id: str) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.responses = {
            (record["stage"], record["case_id"]): record["response"]
            for record in _capture(case_id)
        }

    def complete(self, *, stage: str, case_id: str | None, prompt: str) -> object:
        self.calls.append((stage, case_id))
        return self.responses[(stage, case_id)]

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def _capture(case_id: str) -> list[dict]:
    return [
        {"stage": "policy_analysis", "case_id": None, "response": {"schema_version": "1", "findings": []}},
        {"stage": "case_evaluation", "case_id": case_id, "response": {"case_id": case_id, "policy_compliant": True, "tool_use_valid": True, "final_response_supported": True, "primary_classification": "correct", "subtype": None, "explanation": "supported"}},
        {"stage": "case_repair", "case_id": case_id, "response": {"case_id": case_id, "next_action_type": "respond_only", "corrected_tool_call": None, "escalation": None, "safer_final_response": "safe", "explanation": "none needed"}},
        {"stage": "repair_review", "case_id": case_id, "response": {"case_id": case_id, "approved": True, "remaining_risk": "none", "severity": "low"}},
    ]


def _fixture_root(tmp_path: Path, case_ids: list[str]) -> Path:
    _write_jsonl(tmp_path / "cases.jsonl", [{"case_id": case_id, "messages": [], "tool_calls": [], "tool_results": [], "final_response": "safe"} for case_id in case_ids])
    (tmp_path / "tool_specs.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "policy.md").write_text("Always be safe.\n", encoding="utf-8")
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    captures = [record for case_id in case_ids for record in _capture(case_id) if record["stage"] != "policy_analysis"]
    captures.insert(0, _capture(case_ids[0])[0])
    _write_jsonl(capture_dir / "captures.jsonl", captures)
    return capture_dir


def test_replay_cli_executes_exact_cardinality_without_network(tmp_path: Path, monkeypatch) -> None:
    capture_dir = _fixture_root(tmp_path, ["opaque-a", "opaque-b"])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    completed = subprocess.run(
        [sys.executable, "-m", "support_eval", "replay", "--root", str(tmp_path), "--capture-dir", str(capture_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    workspace = next((tmp_path / ".support_eval/runs").iterdir())
    calls = [json.loads(line) for line in (workspace / "llm_calls.jsonl").read_text().splitlines()]
    assert [call["stage"] for call in calls].count("policy_analysis") == 1
    for stage in ("case_evaluation", "case_repair", "repair_review"):
        assert [call["stage"] for call in calls].count(stage) == 2
    assert (workspace / "repair_guardrail_checks.json").is_file()
    assert {call["provider"] for call in calls} == {"replay"}


def test_rejected_response_is_retained_as_raw_workspace_artifact(tmp_path: Path) -> None:
    from support_eval import pipeline

    _fixture_root(tmp_path, ["opaque-a"])
    provider = _FakeProvider("opaque-a")
    rejected = {"schema_version": "1", "findings": "not-an-array"}
    provider.responses[("policy_analysis", None)] = rejected

    try:
        pipeline.run_pipeline(tmp_path, provider=provider)
    except pipeline.StageFailure as error:
        assert "CASES_EVALUATED" in str(error)
    else:
        raise AssertionError("invalid provider response must stop the pipeline")

    workspace = next((tmp_path / ".support_eval" / "runs").iterdir())
    raw = next((workspace / "raw").glob("*policy_analysis*.json"))
    assert json.loads(raw.read_text()) == rejected
    assert not (workspace / "captures.jsonl").exists()


def test_replay_missing_capture_fails_closed_before_live_fallback(tmp_path: Path, monkeypatch) -> None:
    from support_eval import pipeline
    from support_eval.providers.replay import ReplayProvider

    capture_dir = _fixture_root(tmp_path, ["opaque-a"])
    records = [json.loads(line) for line in (capture_dir / "captures.jsonl").read_text().splitlines()]
    _write_jsonl(capture_dir / "captures.jsonl", [record for record in records if record["stage"] != "repair_review"])
    monkeypatch.setattr(pipeline, "resolve_provider", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("missing replay capture must not resolve a live provider")))
    try:
        pipeline.run_pipeline(tmp_path, provider=ReplayProvider(capture_dir))
    except pipeline.StageFailure as error:
        assert "CASES_REPAIRED" in str(error)
    else:
        raise AssertionError("missing replay response must fail closed")


def test_identical_runs_use_fresh_workspaces_and_stable_digests(tmp_path: Path) -> None:
    from support_eval.pipeline import run_pipeline

    _fixture_root(tmp_path, ["opaque-a"])
    first_provider = _FakeProvider("opaque-a")
    second_provider = _FakeProvider("opaque-a")
    first = run_pipeline(tmp_path, provider=first_provider)
    second = run_pipeline(tmp_path, provider=second_provider)

    assert first.workspace != second.workspace
    first_manifest = json.loads((first.workspace / "manifest.json").read_text())
    second_manifest = json.loads((second.workspace / "manifest.json").read_text())
    assert first_manifest["snapshot_digest"] == second_manifest["snapshot_digest"]
    assert first_manifest["config_digest"] == second_manifest["config_digest"]
    assert len(list((tmp_path / ".support_eval" / "runs").iterdir())) == 2
    root_manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    assert root_manifest["run_id"] == second.run_id
    assert root_manifest["workspace"] == (
        f".support_eval/runs/{second.run_id}/manifest.json"
    )
    assert set(root_manifest["artifact_hashes"]) == {
        "policy_analysis.json",
        "case_evaluations.json",
        "guardrail_checks.json",
        "case_repairs.json",
        "repair_reviews.json",
        "failure_summary.json",
        "agent_improvement_plan.md",
        "llm_calls.jsonl",
        "repair_guardrail_checks.json",
    }
    assert len(first_provider.calls) == len(second_provider.calls) == 4
    assert (first.workspace / "captures.jsonl").read_text() == (
        second.workspace / "captures.jsonl"
    ).read_text()


def test_workspace_captures_replay_without_network(tmp_path: Path) -> None:
    from support_eval.pipeline import run_pipeline
    from support_eval.providers.replay import ReplayProvider

    _fixture_root(tmp_path, ["opaque-a"])
    live = run_pipeline(tmp_path, provider=_FakeProvider("opaque-a"))
    replay = ReplayProvider(live.workspace)
    replayed = run_pipeline(tmp_path, provider=replay)

    captures = [
        json.loads(line)
        for line in (live.workspace / "captures.jsonl").read_text().splitlines()
    ]
    assert [(capture["stage"], capture["case_id"]) for capture in captures] == [
        ("policy_analysis", None),
        ("case_evaluation", "opaque-a"),
        ("case_repair", "opaque-a"),
        ("repair_review", "opaque-a"),
    ]
    assert replayed.workspace != live.workspace
    calls = [
        json.loads(line)
        for line in (replayed.workspace / "llm_calls.jsonl").read_text().splitlines()
    ]
    assert [call["stage"] for call in calls] == [
        "policy_analysis",
        "case_evaluation",
        "case_repair",
        "repair_review",
    ]
    assert {call["provider"] for call in calls} == {"replay"}


def test_blocking_repair_guardrail_prevents_review_and_publication(tmp_path: Path) -> None:
    from support_eval import pipeline

    _fixture_root(tmp_path, ["opaque-a"])
    provider = _FakeProvider("opaque-a")
    provider.responses[("case_repair", "opaque-a")] = {
        "case_id": "opaque-a",
        "next_action_type": "call_tool",
        "corrected_tool_call": {"name": "undeclared", "arguments": {}},
        "escalation": None,
        "safer_final_response": "I will investigate this.",
        "explanation": "Use the corrected tool call.",
    }

    try:
        pipeline.run_pipeline(tmp_path, provider=provider)
    except pipeline.StageFailure as error:
        assert "CASES_REPAIRED" in str(error)
    else:
        raise AssertionError("blocking repair guardrail must stop the pipeline")

    workspace = next((tmp_path / ".support_eval/runs").iterdir())
    checks = json.loads((workspace / "repair_guardrail_checks.json").read_text())
    assert checks[0]["has_blocking_findings"] is True
    assert ("repair_review", "opaque-a") not in provider.calls
    assert not (tmp_path / "run_manifest.json").exists()
