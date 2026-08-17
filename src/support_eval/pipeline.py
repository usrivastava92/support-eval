"""Frozen, barriered support-evaluation state machine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .aggregate import build_failure_summary
from .artifacts import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    canonical_json,
    content_digest,
    immutable_workspace,
    publish_transactionally,
    read_json,
    read_jsonl,
)
from .guardrails import run_guardrails
from .prompts import PROMPT_VERSION, build_prompt
from .providers import Provider, decode_response, resolve_provider
from .render_plan import render_improvement_plan
from .schemas import VALIDATORS, validate_cases, validate_tool_specs

STAGES = (
    "INIT",
    "INPUTS_LOADED",
    "TOOLS_AND_POLICY_PARSED",
    "CASES_NORMALISED",
    "CASES_EVALUATED",
    "CASES_REPAIRED",
    "FAILURE_PATTERNS_AGGREGATED",
    "POLICY_PLAN_GENERATED",
    "RESULTS_FINALISED",
)


class StageFailure(RuntimeError):
    """A stage failed and subsequent stages were intentionally not entered."""


@dataclass(frozen=True)
class RunResult:
    run_id: str
    workspace: Path
    summary: dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _tools(case: dict[str, Any]) -> list[str]:
    trace = case.get("assistant_trace")
    sources = (trace, case) if isinstance(trace, dict) else (case,)
    calls: list[Any] = []
    for source in sources:
        for key in ("tool_calls", "support_tool_calls", "proposed_tool_calls", "calls"):
            candidate = source.get(key)
            if isinstance(candidate, list):
                calls = candidate
                break
        if calls:
            break
    return sorted(
        {
            name
            for call in calls
            if isinstance(call, dict)
            and isinstance((name := call.get("name") or call.get("tool") or call.get("tool_name")), str)
        }
    )


def run_pipeline(
    root: Path | str = ".",
    *,
    provider: Provider | None = None,
    provider_name: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    capture_dir: Path | str | None = None,
    review_repairs: bool = True,
) -> RunResult:
    """Evaluate an immutable input snapshot with strict, sequential stage barriers."""
    if not review_repairs:
        raise ValueError("repair reviews are required by the pipeline contract")

    root = Path(root).resolve()
    case_path = root / "cases.jsonl"
    specs_path = root / "tool_specs.json"
    policy_path = root / "policy.md"

    # Load bytes before selecting a provider. Malformed or missing input must never
    # reach a provider call.
    case_bytes = case_path.read_bytes()
    specs_bytes = specs_path.read_bytes()
    policy_bytes = policy_path.read_bytes()
    snapshot = {
        "input_hashes": {
            "cases.jsonl": sha256(case_bytes).hexdigest(),
            "tool_specs.json": sha256(specs_bytes).hexdigest(),
            "policy.md": sha256(policy_bytes).hexdigest(),
        },
        "prompt_version": PROMPT_VERSION,
        "schema_version": "1",
    }
    selected = provider or resolve_provider(
        root,
        provider_name,
        model,
        base_url,
        Path(capture_dir) if capture_dir else None,
    )
    provider_configuration = {
        "provider": selected.name,
        "model": selected.model,
        "base_url": selected.base_url,
    }
    workspace = immutable_workspace(root, snapshot, provider_configuration)
    manifest_path = workspace / "manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RuntimeError("workspace manifest must be an object")
    manifest.update(
        {
            "run_id": workspace.name,
            "status": "running",
            "current_stage": "INIT",
            "provider": selected.name,
            "model": selected.model,
            "prompt_version": PROMPT_VERSION,
            "schema_version": "1",
            "artifacts": {},
        }
    )
    atomic_write_json(manifest_path, manifest)

    events: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    captures: list[dict[str, Any]] = []

    def event(stage: str, status: str, *, substep: str | None = None) -> None:
        record: dict[str, Any] = {
            "sequence": len(events) + 1,
            "stage": stage,
            "status": status,
            "timestamp": _now(),
        }
        if substep is not None:
            record["substep"] = substep
        events.append(record)
        atomic_write_jsonl(workspace / "stage_events.jsonl", events)
        manifest["current_stage"] = stage
        atomic_write_json(manifest_path, manifest)

    def fail(stage: str, error: Exception, *, substep: str | None = None) -> None:
        event(stage, "failed", substep=substep)
        manifest.update({"status": "failed", "error": str(error)})
        atomic_write_json(manifest_path, manifest)
        raise StageFailure(f"{stage}: {error}") from error

    def enter(stage: str, action: Any) -> Any:
        event(stage, "started")
        try:
            value = action()
        except Exception as error:
            fail(stage, error)
        event(stage, "completed")
        return value

    def invoke(stage: str, case: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
        sequence = len(calls) + 1
        case_id = case["case_id"] if case is not None else None
        token = content_digest([stage, case_id])[:12]
        prompt = build_prompt(stage, policy, specs, case, **extra)
        request = f"requests/{sequence:04d}-{stage}-{token}.txt"
        raw_path = f"raw/{sequence:04d}-{stage}-{token}.json"
        parsed_path = f"parsed/{sequence:04d}-{stage}-{token}.json"
        atomic_write_text(workspace / request, prompt)
        log: dict[str, Any] = {
            "sequence": sequence,
            "timestamp": _now(),
            "stage": stage,
            "case_id": case_id,
            "provider": selected.name,
            "model": selected.model,
            "base_url": selected.base_url,
            "logical_call_id": f"{stage}:{case_id or 'global'}",
            "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
            "request_artifact": request,
            "raw_response_artifact": raw_path,
            "output_artifact": parsed_path,
            "status": "failed",
        }
        raw_written = False
        try:
            raw = selected.complete(stage=stage, case_id=case_id, prompt=prompt)
            atomic_write_json(workspace / raw_path, raw)
            raw_written = True
            parsed = decode_response(raw)
            validator = VALIDATORS[stage]
            value = validator(parsed) if stage == "policy_analysis" else validator(parsed, case_id)
            atomic_write_json(workspace / parsed_path, value)
            captures.append({"stage": stage, "case_id": case_id, "response": raw})
            atomic_write_jsonl(workspace / "captures.jsonl", captures)
            log.update(
                {
                    "status": "completed",
                    "response_hash": sha256(canonical_json(raw).encode("utf-8")).hexdigest(),
                }
            )
            return value
        except Exception as error:
            if not raw_written:
                atomic_write_json(workspace / raw_path, {"error": str(error)})
            log["error"] = str(error)
            raise
        finally:
            calls.append(log)
            atomic_write_jsonl(workspace / "llm_calls.jsonl", calls)

    def invoke_each(
        stage: str,
        cases_to_process: list[dict[str, Any]],
        extras: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases_to_process):
            # Do not collect errors. The first invalid model output is a hard barrier
            # and makes every later provider call unreachable.
            results.append(invoke(stage, case, **(extras[index] if extras else {})))
        return results

    event("INIT", "started")
    event("INIT", "completed")

    def load_inputs() -> None:
        atomic_write_bytes(workspace / "inputs/cases.jsonl", case_bytes)
        atomic_write_bytes(workspace / "inputs/tool_specs.json", specs_bytes)
        atomic_write_bytes(workspace / "inputs/policy.md", policy_bytes)

    enter("INPUTS_LOADED", load_inputs)

    def parse_tools_and_policy() -> tuple[dict[str, Any], str]:
        parsed_specs = validate_tool_specs(read_json(specs_path))
        parsed_policy = policy_bytes.decode("utf-8")
        if not parsed_policy.strip():
            raise ValueError("policy.md must not be empty")
        return parsed_specs, parsed_policy

    specs, policy = enter("TOOLS_AND_POLICY_PARSED", parse_tools_and_policy)
    cases = enter("CASES_NORMALISED", lambda: validate_cases(read_jsonl(case_path)))
    manifest["case_count"] = len(cases)
    atomic_write_json(manifest_path, manifest)

    def evaluate_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        analysis = invoke("policy_analysis", None)
        evaluated = invoke_each("case_evaluation", cases)
        return analysis, [
            {**value, "tools_used": _tools(case)}
            for value, case in zip(evaluated, cases, strict=True)
        ]

    analysis, evaluations = enter("CASES_EVALUATED", evaluate_cases)

    def repair_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        guardrails = run_guardrails(cases, specs)
        atomic_write_json(workspace / "guardrail_checks.json", guardrails)
        repair_extras = [
            {"evaluation": evaluation, "guardrail": guardrail}
            for evaluation, guardrail in zip(evaluations, guardrails, strict=True)
        ]
        repairs = invoke_each("case_repair", cases, repair_extras)
        review_extras = [
            {"evaluation": evaluation, "guardrail": guardrail, "repair": repair}
            for evaluation, guardrail, repair in zip(evaluations, guardrails, repairs, strict=True)
        ]
        # Reviews are a required substep of CASES_REPAIRED, not an independent
        # top-level stage.
        reviews = invoke_each("repair_review", cases, review_extras)
        return guardrails, repairs, reviews

    guardrails, repairs, reviews = enter("CASES_REPAIRED", repair_cases)
    summary = enter(
        "FAILURE_PATTERNS_AGGREGATED",
        lambda: build_failure_summary(evaluations, guardrails, repairs),
    )
    atomic_write_json(workspace / "failure_summary.json", summary)
    plan = enter(
        "POLICY_PLAN_GENERATED",
        lambda: render_improvement_plan(analysis, evaluations, repairs, summary),
    )
    atomic_write_text(workspace / "agent_improvement_plan.md", plan)

    outputs = {
        "policy_analysis.json": _json(analysis),
        "case_evaluations.json": _json(evaluations),
        "guardrail_checks.json": _json(guardrails),
        "case_repairs.json": _json(repairs),
        "repair_reviews.json": _json(reviews),
        "failure_summary.json": _json(summary),
        "agent_improvement_plan.md": plan.encode("utf-8"),
        "llm_calls.jsonl": b"".join(_json(call) for call in calls),
    }
    root_manifest = {
        "run_id": workspace.name,
        "workspace": str(workspace.relative_to(root) / "manifest.json"),
        "input_hashes": snapshot["input_hashes"],
        "artifact_hashes": {
            name: sha256(content).hexdigest()
            for name, content in outputs.items()
        },
    }
    outputs["run_manifest.json"] = _json(root_manifest)

    event("RESULTS_FINALISED", "started")
    try:
        # Root artifacts become visible only after every prerequisite result is complete.
        publish_transactionally(root, outputs)
    except Exception as error:
        fail("RESULTS_FINALISED", error)
    manifest["artifacts"] = {name: name for name in outputs}
    event("RESULTS_FINALISED", "completed")
    manifest["status"] = "completed"
    atomic_write_json(manifest_path, manifest)
    return RunResult(workspace.name, workspace, summary)
