from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _build_bundle(tmp_path: Path, *, cases: int = 2) -> Path:
    records = [{"case_id": f"opaque-{index}", "messages": []} for index in range(cases)]
    (tmp_path / "cases.jsonl").write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    _json(tmp_path / "tool_specs.json", {})
    (tmp_path / "policy.md").write_text("policy", encoding="utf-8")
    _json(tmp_path / "policy_analysis.json", {"schema_version": "1", "findings": []})
    guardrails = [{"case_id": item["case_id"], "checks": [], "has_blocking_findings": False} for item in records]
    _json(tmp_path / "guardrail_checks.json", guardrails)
    evaluations = [{"case_id": item["case_id"], "policy_compliant": True, "tool_use_valid": True, "final_response_supported": True, "primary_classification": "correct", "subtype": None, "explanation": "ok", "tools_used": []} for item in records]
    reviews = [{"case_id": item["case_id"], "approved": True, "remaining_risk": "none", "severity": "low"} for item in records]
    _json(tmp_path / "case_evaluations.json", evaluations)
    repairs = [{"case_id": item["case_id"], "next_action_type": "respond_only", "corrected_tool_call": None, "escalation": None, "safer_final_response": "ok", "explanation": "ok"} for item in records]
    _json(tmp_path / "case_repairs.json", repairs)
    repair_guardrails = [{"case_id": item["case_id"], "checks": [], "has_blocking_findings": False} for item in records]
    _json(tmp_path / "repair_guardrail_checks.json", repair_guardrails)
    _json(tmp_path / "repair_reviews.json", reviews)
    _json(tmp_path / "failure_summary.json", {"total_cases": cases, "counts_by_classification": {"correct": cases}, "counts_by_tool": {}, "unsupported_claims": [], "missing_identifier_errors": [], "escalation_worthy_cases": [], "top_policy_gaps": [], "repair_guardrail_failures": 0, "unsupported_repair_claims": 0})
    (tmp_path / "agent_improvement_plan.md").write_text("# plan\n", encoding="utf-8")
    calls = [{"sequence": 1, "timestamp": "2026-01-01T00:00:00Z", "stage": "policy_analysis", "case_id": None}]
    sequence = 2
    for stage in ("case_evaluation", "case_repair", "repair_review"):
        for item in records:
            calls.append({"sequence": sequence, "timestamp": "2026-01-01T00:00:00Z", "stage": stage, "case_id": item["case_id"]})
            sequence += 1
    for call in calls:
        call.update({"provider": "replay", "model": "fixture", "base_url": "capture://", "logical_call_id": f"{call['stage']}:{call['case_id'] or 'global'}", "prompt_sha256": "a" * 64, "response_hash": "b" * 64, "request_artifact": f"requests/{call['sequence']}.txt", "output_artifact": f"parsed/{call['sequence']}.json", "raw_response_artifact": f"raw/{call['sequence']}.json", "status": "completed"})
        _json(tmp_path / call["output_artifact"], {})
        _json(tmp_path / call["raw_response_artifact"], {})
        (tmp_path / call["request_artifact"]).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / call["request_artifact"]).write_text("fixture", encoding="utf-8")
    (tmp_path / "llm_calls.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "llm_calls.jsonl").write_text("\n".join(json.dumps(item) for item in calls) + "\n", encoding="utf-8")
    run = tmp_path / "runs" / "fixture"
    stages = ("INIT", "INPUTS_LOADED", "TOOLS_AND_POLICY_PARSED", "CASES_NORMALISED", "CASES_EVALUATED", "CASES_REPAIRED", "FAILURE_PATTERNS_AGGREGATED", "POLICY_PLAN_GENERATED", "RESULTS_FINALISED")
    _json(run / "manifest.json", {"run_id": "fixture", "status": "completed", "current_stage": "RESULTS_FINALISED", "case_count": cases, "provider": "replay", "model": "fixture", "prompt_version": "1", "schema_version": "1", "artifacts": {name: name for name in ("policy_analysis.json", "case_evaluations.json", "guardrail_checks.json", "case_repairs.json", "repair_guardrail_checks.json", "repair_reviews.json", "failure_summary.json", "agent_improvement_plan.md", "llm_calls.jsonl")}, "input_hashes": {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in ("cases.jsonl", "tool_specs.json", "policy.md")}})
    (run / "stage_events.jsonl").write_text("\n".join(json.dumps({"sequence": index, "stage": stage, "status": "completed", "timestamp": "2026-01-01T00:00:00Z"}) for index, stage in enumerate(stages, 1)) + "\n", encoding="utf-8")
    return tmp_path


def _validate(bundle: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "validate.py"), "--root", str(bundle)], text=True, capture_output=True, check=False)


def _publish_run_pointer(bundle: Path, *, run_id: str = "fixture") -> None:
    manifest = json.loads((bundle / "runs" / run_id / "manifest.json").read_text())
    _json(
        bundle / "run_manifest.json",
        {
            "run_id": run_id,
            "workspace": f"runs/{run_id}/manifest.json",
            "input_hashes": manifest["input_hashes"],
            "artifact_hashes": {
                name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
                for name in (
                    "policy_analysis.json",
                    "case_evaluations.json",
                    "guardrail_checks.json",
                    "case_repairs.json",
                    "repair_guardrail_checks.json",
                    "repair_reviews.json",
                    "failure_summary.json",
                    "agent_improvement_plan.md",
                    "llm_calls.jsonl",
                )
            },
        },
    )


def _add_historical_run(bundle: Path) -> None:
    source = bundle / "runs" / "fixture"
    historical = bundle / "runs" / "historical"
    historical.mkdir()
    for name in ("manifest.json", "stage_events.jsonl"):
        (historical / name).write_bytes((source / name).read_bytes())


def test_validator_accepts_complete_bundle(tmp_path: Path) -> None:
    result = _validate(_build_bundle(tmp_path))
    assert result.returncode == 0, result.stdout



def test_validator_uses_published_pointer_with_historical_runs(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    _add_historical_run(bundle)
    _publish_run_pointer(bundle)
    result = _validate(bundle)
    assert result.returncode == 0, result.stdout


def test_validator_rejects_multiple_runs_without_published_pointer(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    _add_historical_run(bundle)
    result = _validate(bundle)
    assert result.returncode == 1
    assert "published run pointer" in result.stdout


def test_validator_run_override_allows_a_historical_workspace(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    _add_historical_run(bundle)
    result = subprocess.run(
        [sys.executable, str(ROOT / "validate.py"), "--root", str(bundle), "--run", str(bundle / "runs" / "fixture")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_validator_rejects_mismatched_published_pointer(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    _publish_run_pointer(bundle)
    pointer = json.loads((bundle / "run_manifest.json").read_text())
    pointer["artifact_hashes"]["repair_guardrail_checks.json"] = "0" * 64
    _json(bundle / "run_manifest.json", pointer)
    result = _validate(bundle)
    assert result.returncode == 1
    assert "published artifact hash mismatch: repair_guardrail_checks.json" in result.stdout


def test_validator_rejects_missing_or_incomplete_repair_guardrails(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    (bundle / "repair_guardrail_checks.json").unlink()
    result = _validate(bundle)
    assert result.returncode == 1
    assert "missing required artifact: repair_guardrail_checks.json" in result.stdout

    bundle = _build_bundle(tmp_path)
    checks = json.loads((bundle / "repair_guardrail_checks.json").read_text())
    checks[0]["checks"] = [{"code": "unknown_repair_tool", "severity": "critical", "message": "unknown", "tool_name": "tool"}]
    _json(bundle / "repair_guardrail_checks.json", checks)
    result = _validate(bundle)
    assert result.returncode == 1
    assert "repair_guardrail_checks[opaque-0] check 0 has incorrect fields" in result.stdout


def test_validator_rejects_blocking_repair_guardrails_and_summary_tampering(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    checks = json.loads((bundle / "repair_guardrail_checks.json").read_text())
    checks[0]["checks"] = [{"code": "unsupported_repair_success_claim", "severity": "critical", "message": "unsupported", "tool_name": None, "call_index": None}]
    checks[0]["has_blocking_findings"] = True
    _json(bundle / "repair_guardrail_checks.json", checks)
    result = _validate(bundle)
    assert result.returncode == 1
    assert "repair_guardrail_checks[opaque-0] has blocking findings" in result.stdout

    bundle = _build_bundle(tmp_path)
    summary = json.loads((bundle / "failure_summary.json").read_text())
    summary["repair_guardrail_failures"] = 1
    summary["unsupported_repair_claims"] = 1
    _json(bundle / "failure_summary.json", summary)
    result = _validate(bundle)
    assert result.returncode == 1
    assert "repair_guardrail_failures" in result.stdout
    assert "unsupported_repair_claims" in result.stdout


def test_validator_recomputes_repair_guardrail_summary_counts(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    checks = json.loads((bundle / "repair_guardrail_checks.json").read_text())
    checks[0]["checks"] = [{"code": "unsupported_repair_success_claim", "severity": "warning", "message": "unsupported", "tool_name": None, "call_index": None}]
    _json(bundle / "repair_guardrail_checks.json", checks)
    summary = json.loads((bundle / "failure_summary.json").read_text())
    summary["repair_guardrail_failures"] = 1
    summary["unsupported_repair_claims"] = 1
    _json(bundle / "failure_summary.json", summary)
    result = _validate(bundle)
    assert result.returncode == 0, result.stdout


def test_validator_rejects_summary_tampering(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    summary = json.loads((bundle / "failure_summary.json").read_text())
    summary["total_cases"] = 99
    _json(bundle / "failure_summary.json", summary)
    result = _validate(bundle)
    assert result.returncode == 1
    assert "total_cases" in result.stdout


def test_validator_rejects_illegal_repair_and_hidden_call(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    repairs = json.loads((bundle / "case_repairs.json").read_text())
    repairs[0]["next_action_type"] = "shell"
    _json(bundle / "case_repairs.json", repairs)
    hidden_call = {"sequence": 99, "timestamp": "2026-01-01T00:00:00Z", "stage": "case_evaluation", "case_id": "opaque-0", "provider": "replay", "model": "fixture", "base_url": "capture://", "logical_call_id": "case_evaluation:opaque-0:hidden", "prompt_sha256": "a" * 64, "response_hash": "b" * 64, "request_artifact": "requests/99.txt", "output_artifact": "parsed/99.json", "raw_response_artifact": "raw/99.json", "status": "completed"}
    with (bundle / "llm_calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(hidden_call) + "\n")
    result = _validate(bundle)
    assert result.returncode == 1
    assert "illegal next_action_type" in result.stdout
    assert "call cardinality" in result.stdout


def test_validator_rejects_early_finalization_and_input_replacement(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    events = (bundle / "runs/fixture/stage_events.jsonl").read_text().splitlines()
    (bundle / "runs/fixture/stage_events.jsonl").write_text("\n".join(events[:-2] + [events[-1]]) + "\n", encoding="utf-8")
    (bundle / "policy.md").write_text("replacement policy", encoding="utf-8")
    result = _validate(bundle)
    assert result.returncode == 1
    assert "input hash mismatch" in result.stdout
    assert "finalization missing prerequisite" in result.stdout
