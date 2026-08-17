"""Application-owned schemas for model responses and evaluation inputs."""

from __future__ import annotations

from typing import Any, Callable


class SchemaError(ValueError):
    """A response does not satisfy the frozen JSON contract."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{label} must be a JSON object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"{label} must be a string")
    return value


def _nullable_string(value: Any, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise SchemaError(f"{label} must be a string or null")
    return value


def _keys(record: dict[str, Any], required: set[str], label: str) -> None:
    if set(record) != required:
        raise SchemaError(f"{label} keys must be exactly {sorted(required)}")


def validate_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for index, case in enumerate(cases):
        _object(case, f"case {index}")
        case_id = _string(case.get("case_id"), f"case {index}.case_id")
        if not case_id or case_id in seen:
            raise SchemaError(f"case {index}.case_id must be a unique non-empty string")
        seen.add(case_id)
    return cases


def validate_tool_specs(tool_specs: Any) -> dict[str, Any]:
    specs = _object(tool_specs, "tool_specs")
    tools = specs.get("tools")
    if "tools" in specs:
        if set(specs) != {"tools"} or not isinstance(tools, list):
            raise SchemaError("tool_specs.tools must be the only field and must be a list")
        names: set[str] = set()
        for index, tool in enumerate(tools):
            record = _object(tool, f"tool_specs.tools[{index}]")
            name = _string(record.get("name"), f"tool_specs.tools[{index}].name")
            if not name or name in names:
                raise SchemaError(f"tool_specs.tools[{index}].name must be a unique non-empty string")
            names.add(name)
            _string(record.get("description"), f"tool_specs.tools[{index}].description")
            schema = _object(record.get("input_schema"), f"tool_specs.tools[{index}].input_schema")
            if schema.get("type") != "object":
                raise SchemaError(f"tool_specs.tools[{index}].input_schema.type must be 'object'")
            if not isinstance(schema.get("properties"), dict):
                raise SchemaError(f"tool_specs.tools[{index}].input_schema.properties must be an object")
            required = schema.get("required", [])
            if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
                raise SchemaError(f"tool_specs.tools[{index}].input_schema.required must be a list of strings")
            if any(item not in schema["properties"] for item in required):
                raise SchemaError(f"tool_specs.tools[{index}].input_schema.required must name declared properties")
        return specs
    if any(not isinstance(name, str) or not isinstance(spec, dict) for name, spec in specs.items()):
        raise SchemaError("tool_specs must map tool names to objects")
    return specs


def policy_analysis(value: Any) -> dict[str, Any]:
    record = _object(value, "policy analysis")
    _keys(record, {"schema_version", "findings"}, "policy analysis")
    if record["schema_version"] != "1":
        raise SchemaError("policy analysis.schema_version must be '1'")
    findings = record["findings"]
    if not isinstance(findings, list):
        raise SchemaError("policy analysis.findings must be a list")
    for index, finding in enumerate(findings):
        finding = _object(finding, f"policy finding {index}")
        _keys(finding, {"category", "subject", "severity", "rule", "rationale"}, f"policy finding {index}")
        for field in finding:
            _string(finding[field], f"policy finding {index}.{field}")
        if finding["category"] not in {"high_impact_tool", "parameter_validation", "escalation_rule", "clarifying_question_rule", "unsafe_behavior_pattern"}:
            raise SchemaError("policy finding.category is not supported")
        if finding["severity"] not in {"critical", "high", "medium", "low"}:
            raise SchemaError("policy finding.severity is not supported")
    return record


def evaluation(value: Any, case_id: str) -> dict[str, Any]:
    record = _object(value, "case evaluation")
    _keys(record, {"case_id", "policy_compliant", "tool_use_valid", "final_response_supported", "primary_classification", "subtype", "explanation"}, "case evaluation")
    if _string(record["case_id"], "case evaluation.case_id") != case_id:
        raise SchemaError("case evaluation.case_id does not match requested case")
    for field in ("policy_compliant", "tool_use_valid", "final_response_supported"):
        if not isinstance(record[field], bool):
            raise SchemaError(f"case evaluation.{field} must be a boolean")
    if _string(record["primary_classification"], "case evaluation.primary_classification") not in {"policy_violation", "invalid_tool_usage", "unsupported_claim", "missing_clarification", "should_escalate", "unnecessary_tool_call", "correct", "other"}:
        raise SchemaError("case evaluation.primary_classification is not supported")
    subtype = _nullable_string(record["subtype"], "case evaluation.subtype")
    if subtype is not None and subtype not in {"action_without_confirmation", "advice_outside_policy", "schema_mismatch", "fabricated_tool_result", "premature_resolution", "other"}:
        raise SchemaError("case evaluation.subtype is not supported")
    _string(record["explanation"], "case evaluation.explanation")
    return record


def repair(value: Any, case_id: str) -> dict[str, Any]:
    record = _object(value, "case repair")
    _keys(record, {"case_id", "next_action_type", "corrected_tool_call", "escalation", "safer_final_response", "explanation"}, "case repair")
    if _string(record["case_id"], "case repair.case_id") != case_id:
        raise SchemaError("case repair.case_id does not match requested case")
    action = _string(record["next_action_type"], "case repair.next_action_type")
    if action not in {"respond_only", "ask_clarifying_question", "call_tool", "escalate", "refuse"}:
        raise SchemaError("case repair.next_action_type is not supported")
    if record["corrected_tool_call"] is not None and not isinstance(record["corrected_tool_call"], dict):
        raise SchemaError("case repair.corrected_tool_call must be an object or null")
    if record["escalation"] is not None and not isinstance(record["escalation"], dict):
        raise SchemaError("case repair.escalation must be an object or null")
    if action == "call_tool" and record["corrected_tool_call"] is None:
        raise SchemaError("case repair.corrected_tool_call is required for call_tool")
    if action != "call_tool" and record["corrected_tool_call"] is not None:
        raise SchemaError("case repair.corrected_tool_call is only valid for call_tool")
    if action == "escalate" and record["escalation"] is None:
        raise SchemaError("case repair.escalation is required for escalate")
    if action != "escalate" and record["escalation"] is not None:
        raise SchemaError("case repair.escalation is only valid for escalate")
    _string(record["safer_final_response"], "case repair.safer_final_response")
    _string(record["explanation"], "case repair.explanation")
    return record


def review(value: Any, case_id: str) -> dict[str, Any]:
    record = _object(value, "repair review")
    _keys(record, {"case_id", "approved", "remaining_risk", "severity"}, "repair review")
    if _string(record["case_id"], "repair review.case_id") != case_id:
        raise SchemaError("repair review.case_id does not match requested case")
    if not isinstance(record["approved"], bool):
        raise SchemaError("repair review.approved must be a boolean")
    _string(record["remaining_risk"], "repair review.remaining_risk")
    if record["severity"] not in {"none", "low", "medium", "high"}:
        raise SchemaError("repair review.severity is not supported")
    return record


VALIDATORS: dict[str, Callable[..., dict[str, Any]]] = {
    "policy_analysis": policy_analysis,
    "case_evaluation": evaluation,
    "case_repair": repair,
    "repair_review": review,
}
