#!/usr/bin/env python3
"""Independently validate a published support-evaluation artifact bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

INPUTS = ("cases.jsonl", "tool_specs.json", "policy.md")
JSON_OUTPUTS = ("policy_analysis.json", "case_evaluations.json", "guardrail_checks.json", "case_repairs.json", "repair_reviews.json", "failure_summary.json")
OUTPUTS = (*JSON_OUTPUTS, "agent_improvement_plan.md", "llm_calls.jsonl")
CALL_STAGES = ("policy_analysis", "case_evaluation", "case_repair", "repair_review")
EVENT_STAGES = ("INIT", "INPUTS_LOADED", "TOOLS_AND_POLICY_PARSED", "CASES_NORMALISED", "CASES_EVALUATED", "CASES_REPAIRED", "FAILURE_PATTERNS_AGGREGATED", "POLICY_PLAN_GENERATED", "RESULTS_FINALISED")
REPAIR_ACTIONS = {"respond_only", "ask_clarifying_question", "call_tool", "escalate", "refuse"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON {path.name}: {exc}")
        return None


def _jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"cannot read {path.name}: {exc}")
        return []
    records = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            errors.append(f"blank JSONL record {path.name}:{number}")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSONL {path.name}:{number}: {exc.msg}")
            continue
        if not isinstance(record, dict):
            errors.append(f"non-object JSONL record {path.name}:{number}")
        else:
            records.append(record)
    return records

def _exact_record(label: str, record: dict[str, Any], fields: set[str], errors: list[str]) -> None:
    if set(record) != fields:
        errors.append(f"{label} has incorrect fields")


def _validate_shapes(data: dict[str, Any], evaluations: dict[str, dict[str, Any]], guardrails: dict[str, dict[str, Any]], repairs: dict[str, dict[str, Any]], errors: list[str]) -> None:
    policy = data["policy_analysis.json"]
    if not isinstance(policy, dict) or set(policy) != {"schema_version", "findings"} or policy.get("schema_version") != "1" or not isinstance(policy.get("findings"), list):
        errors.append("policy_analysis has invalid schema")
    elif any(not isinstance(item, dict) or set(item) != {"category", "subject", "severity", "rule", "rationale"} for item in policy["findings"]):
        errors.append("policy_analysis has invalid finding")
    for label, records, fields in (
        ("case_evaluations", evaluations, {"case_id", "policy_compliant", "tool_use_valid", "final_response_supported", "primary_classification", "subtype", "explanation", "tools_used"}),
        ("guardrail_checks", guardrails, {"case_id", "checks", "has_blocking_findings"}),
        ("case_repairs", repairs, {"case_id", "next_action_type", "corrected_tool_call", "escalation", "safer_final_response", "explanation"}),
    ):
        for case_id, record in records.items():
            _exact_record(f"{label}[{case_id}]", record, fields, errors)
    reviews = data["repair_reviews.json"]
    if isinstance(reviews, list):
        for record in reviews:
            if isinstance(record, dict):
                _exact_record("repair_reviews record", record, {"case_id", "approved", "remaining_risk", "severity"}, errors)


def _by_case(label: str, data: Any, case_ids: list[str], errors: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(data, list):
        errors.append(f"{label} must be an array")
        return {}
    result: dict[str, dict[str, Any]] = {}
    expected = set(case_ids)
    for index, row in enumerate(data):
        case_id = row.get("case_id") if isinstance(row, dict) else None
        if not isinstance(row, dict) or case_id not in expected:
            errors.append(f"{label}[{index}] references an unknown case")
        elif case_id in result:
            errors.append(f"{label} duplicates case_id {case_id}")
        else:
            result[case_id] = row
    missing = sorted(expected - result.keys())
    if missing:
        errors.append(f"{label} missing cases: {', '.join(missing)}")
    return result


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _workspace_candidates(root: Path, run_id: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    for base in (root / "runs", root / ".support_eval" / "runs"):
        if not base.is_dir():
            continue
        paths = [base / run_id] if run_id is not None else sorted(base.iterdir())
        candidates.extend(path for path in paths if path.is_dir() and (path / "manifest.json").is_file())
    return sorted(candidates)


def _published_pointer(root: Path, errors: list[str]) -> tuple[Path | None, dict[str, Any] | None]:
    pointers = [
        path
        for path in (root / "run_manifest.json", root / ".support_eval" / "run_manifest.json")
        if path.is_file()
    ]
    if not pointers:
        return None, None
    if len(pointers) != 1:
        errors.append("expected exactly one published run pointer")
        return None, None
    pointer = _json(pointers[0], errors)
    if not isinstance(pointer, dict):
        errors.append("published run pointer must be an object")
        return pointers[0], None
    return pointers[0], pointer


def _published_run(root: Path, errors: list[str]) -> Path | None:
    pointer_path, pointer = _published_pointer(root, errors)
    if pointer_path is None:
        candidates = _workspace_candidates(root)
        return candidates[0] if len(candidates) == 1 else None
    if not isinstance(pointer, dict) or not isinstance(pointer.get("run_id"), str) or not pointer["run_id"]:
        errors.append("published run pointer lacks a valid run_id")
        return None
    workspace = pointer.get("workspace")
    if not isinstance(workspace, str):
        errors.append("published run pointer lacks a workspace manifest path")
        return None
    expected = (root / workspace).resolve()
    permitted = {
        (root / "runs" / pointer["run_id"] / "manifest.json").resolve(),
        (root / ".support_eval" / "runs" / pointer["run_id"] / "manifest.json").resolve(),
    }
    if expected not in permitted:
        errors.append("published run pointer workspace does not match run_id")
        return None
    candidates = _workspace_candidates(root, pointer["run_id"])
    if candidates != [expected.parent]:
        errors.append("published run pointer does not identify exactly one workspace manifest")
        return None
    return expected.parent

def _run_dir(root: Path, run: Path | None, errors: list[str]) -> Path | None:
    if run is not None:
        return run
    selected = _published_run(root, errors)
    if selected is None:
        errors.append("expected a published run pointer or exactly one run manifest (or pass --run)")
    return selected

def _validate_published_bundle(root: Path, run: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    pointer_path, pointer = _published_pointer(root, errors)
    if pointer_path is None:
        return
    if not isinstance(pointer, dict):
        return
    if manifest.get("run_id") != pointer.get("run_id") or run.name != pointer.get("run_id"):
        errors.append("published run pointer and workspace manifest identify different runs")
    pointer_inputs = pointer.get("input_hashes")
    if not isinstance(pointer_inputs, dict):
        errors.append("published run pointer lacks input_hashes")
    elif pointer_inputs != manifest.get("input_hashes"):
        errors.append("published run pointer input hashes do not match workspace manifest")
    artifact_hashes = pointer.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        errors.append("published run pointer lacks artifact_hashes")
        return
    for name in OUTPUTS:
        expected = artifact_hashes.get(name)
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            errors.append(f"published run pointer has no valid hash for {name}")
        elif _sha256(root / name) != expected:
            errors.append(f"published artifact hash mismatch: {name}")



def _validate_workspace(root: Path, requested_run: Path | None, errors: list[str]) -> None:
    run = _run_dir(root, requested_run, errors)
    if run is None:
        return
    manifest = _json(run / "manifest.json", errors) if (run / "manifest.json").is_file() else None
    if not isinstance(manifest, dict):
        errors.append("missing valid run manifest")
        return
    if requested_run is None:
        _validate_published_bundle(root, run, manifest, errors)
    hashes = manifest.get("input_hashes")
    if not isinstance(hashes, dict):
        errors.append("manifest lacks input_hashes")
    else:
        for name in INPUTS:
            expected = hashes.get(name)
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest() if (root / name).is_file() else None
            if not isinstance(expected, str) or not SHA256.fullmatch(expected):
                errors.append(f"manifest has no valid hash for {name}")
            elif actual != expected:
                errors.append(f"input hash mismatch: {name}")
    event_path = run / "stage_events.jsonl"
    events = _jsonl(event_path, errors) if event_path.is_file() else []
    if not event_path.is_file():
        errors.append("missing stage_events.jsonl")
    completed: dict[str, int] = {}
    previous = 0
    for index, event in enumerate(events):
        sequence, stage, status = event.get("sequence"), event.get("stage"), event.get("status")
        if not isinstance(sequence, int) or sequence != previous + 1:
            errors.append(f"stage event {index} sequence is not contiguous from 1")
        previous = sequence if isinstance(sequence, int) else previous
        if stage not in EVENT_STAGES:
            errors.append(f"stage event {index} has invalid stage {stage!r}")
        if status not in {"started", "completed", "failed"}:
            errors.append(f"stage event {index} has invalid status {status!r}")
        if status == "completed" and stage in EVENT_STAGES:
            completed.setdefault(stage, index)
    if "RESULTS_FINALISED" not in completed:
        errors.append("no completed RESULTS_FINALISED event")
    else:
        final = completed["RESULTS_FINALISED"]
        missing = [stage for stage in EVENT_STAGES[:-1] if stage not in completed]
        if missing:
            errors.append("finalization missing prerequisite stages: " + ", ".join(missing))
        elif any(completed[stage] > final for stage in EVENT_STAGES[:-1]):
            errors.append("root results finalized before a required stage")


def _validate_calls(calls: list[dict[str, Any]], case_ids: list[str], errors: list[str]) -> None:
    expected = Counter({"policy_analysis": 1, "case_evaluation": len(case_ids), "case_repair": len(case_ids), "repair_review": len(case_ids)})
    actual: Counter[str] = Counter()
    coverage = {stage: Counter() for stage in CALL_STAGES[1:]}
    last_sequence = 0
    artifacts: set[str] = set()
    for index, call in enumerate(calls):
        stage = call.get("stage")
        if stage not in CALL_STAGES:
            errors.append(f"call log record {index} has invalid stage")
            continue
        actual[stage] += 1
        for field in ("sequence", "provider", "model", "base_url", "prompt_sha256", "response_hash", "output_artifact"):
            if field not in call:
                errors.append(f"call log record {index} lacks {field}")
        sequence = call.get("sequence")
        if not isinstance(sequence, int) or sequence <= last_sequence:
            errors.append(f"call log sequence is not strictly increasing at {index}")
        elif isinstance(sequence, int):
            last_sequence = sequence
        for field in ("prompt_sha256", "response_hash"):
            if not isinstance(call.get(field), str) or not SHA256.fullmatch(call[field]):
                errors.append(f"call log record {index} has invalid {field}")
        artifact = call.get("output_artifact")
        if not isinstance(artifact, str) or not artifact or artifact in artifacts:
            errors.append(f"call log record {index} lacks unique output_artifact")
        else:
            artifacts.add(artifact)
        case_id = call.get("case_id")
        if stage == "policy_analysis" and case_id not in (None, ""):
            errors.append("policy_analysis call may not have a case_id")
        elif stage != "policy_analysis":
            if case_id not in case_ids:
                errors.append(f"call log {stage} references unknown case")
            else:
                coverage[stage][case_id] += 1
    for stage, count in expected.items():
        if actual[stage] != count:
            errors.append(f"call cardinality {stage}: expected {count}, got {actual[stage]}")
    for stage, values in coverage.items():
        if values != Counter(case_ids):
            errors.append(f"{stage} calls do not cover every case exactly once")


def validate(root: Path, run: Path | None = None) -> list[str]:
    errors: list[str] = []
    for name in (*INPUTS, *OUTPUTS):
        if not (root / name).is_file():
            errors.append(f"missing required artifact: {name}")
    if errors:
        return errors
    cases = _jsonl(root / "cases.jsonl", errors)
    _json(root / "tool_specs.json", errors)
    if not (root / "policy.md").read_text(encoding="utf-8").strip():
        errors.append("policy.md is empty")
    data = {name: _json(root / name, errors) for name in JSON_OUTPUTS}
    case_ids = [case.get("case_id") for case in cases if isinstance(case.get("case_id"), str) and case["case_id"].strip()]
    if len(case_ids) != len(cases) or len(set(case_ids)) != len(case_ids):
        errors.append("case IDs must be present, opaque, and unique")
    evaluations = _by_case("case_evaluations", data["case_evaluations.json"], case_ids, errors)
    guardrails = _by_case("guardrail_checks", data["guardrail_checks.json"], case_ids, errors)
    repairs = _by_case("case_repairs", data["case_repairs.json"], case_ids, errors)
    reviews = _by_case("repair_reviews", data["repair_reviews.json"], case_ids, errors)
    _validate_shapes(data, evaluations, guardrails, repairs, errors)
    for case_id, repair in repairs.items():
        if repair.get("next_action_type") not in REPAIR_ACTIONS:
            errors.append(f"repair {case_id} has illegal next_action_type")
    summary = data["failure_summary.json"]
    if not isinstance(summary, dict):
        errors.append("failure_summary must be an object")
    else:
        if summary.get("total_cases") != len(case_ids):
            errors.append("failure_summary total_cases is inconsistent")
        classifications = Counter(str(row.get("primary_classification")) for row in evaluations.values())
        supplied_classifications = summary.get("counts_by_classification")
        if not isinstance(supplied_classifications, dict) or any(supplied_classifications.get(name) != count for name, count in classifications.items()):
            errors.append("failure_summary counts_by_classification is inconsistent")
        tools = Counter()
        for case_id, row in evaluations.items():
            used = row.get("tools_used")
            if isinstance(used, list):
                tools.update(name for name in used if isinstance(name, str))
            else:
                tools.update(check["tool_name"] for check in guardrails.get(case_id, {}).get("checks", []) if isinstance(check, dict) and isinstance(check.get("tool_name"), str))
        supplied_tools = summary.get("counts_by_tool")
        if not isinstance(supplied_tools, dict) or any(supplied_tools.get(name) != count for name, count in tools.items()) or any(name not in tools and count != 0 for name, count in supplied_tools.items()):
            errors.append("failure_summary counts_by_tool is inconsistent")
    _validate_calls(_jsonl(root / "llm_calls.jsonl", errors), case_ids, errors)
    _validate_workspace(root, run, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run", type=Path)
    args = parser.parse_args(argv)
    errors = validate(args.root.resolve(), args.run.resolve() if args.run else None)
    if errors:
        print(f"INVALID: {len(errors)} invariant failure(s)")
        print(*[f"- {error}" for error in errors], sep="\n")
        return 1
    print("VALID: artifact bundle invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
