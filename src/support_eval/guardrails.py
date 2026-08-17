"""Deterministic validation for inert support-tool proposals."""

from __future__ import annotations

import re

from collections.abc import Mapping
from typing import Any


_CALL_KEYS = ("tool_calls", "support_tool_calls", "proposed_tool_calls", "calls")
_RESULT_KEYS = ("tool_results", "results")
_CLAIM_KEYS = ("success_claims", "claims", "tool_success_claims")
_IDENTIFIER_NAMES = frozenset({"id", "identifier", "case_id", "ticket_id", "user_id", "account_id", "order_id"})
_PLACEHOLDER_IDENTIFIERS = frozenset({"", "unknown", "n/a", "na", "none", "null"})


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _trace(case: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(case.get("assistant_trace"))


def _records(case: Mapping[str, Any], keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    for source in (_trace(case), case):
        for key in keys:
            value = source.get(key)
            if isinstance(value, list):
                return [_mapping(item) for item in value]
    return []


def _calls(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _records(case, _CALL_KEYS)


def _results(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _records(case, _RESULT_KEYS)


def _claims(case: Mapping[str, Any]) -> list[Any]:
    for source in (_trace(case), case):
        for key in _CLAIM_KEYS:
            value = source.get(key)
            if isinstance(value, list):
                return value
    return []


def _normalise_specs(tool_specs: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    tools = tool_specs.get("tools")
    if isinstance(tools, list):
        return {
            tool["name"]: tool
            for tool in tools
            if isinstance(tool, Mapping) and isinstance(tool.get("name"), str)
        }
    return {name: _mapping(spec) for name, spec in tool_specs.items() if isinstance(name, str)}


def _specification(tool_specs: Mapping[str, Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    return tool_specs.get(name, {})


def _parameters(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    parameters = _mapping(spec.get("parameters") or spec.get("args") or spec.get("input_schema"))
    return _mapping(parameters.get("properties")) if "properties" in parameters else parameters


def _required(spec: Mapping[str, Any]) -> set[str]:
    parameters = _mapping(spec.get("parameters") or spec.get("input_schema"))
    value = spec.get("required") or parameters.get("required") or spec.get("required_args") or ()
    return {str(item) for item in value} if isinstance(value, (list, tuple, set, frozenset)) else set()


def _expected_types(parameter: Mapping[str, Any]) -> set[str]:
    value = parameter.get("type") or parameter.get("types")
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item) for item in value}
    return set()


def _has_type(value: Any, expected: str) -> bool:
    # bool is intentionally not accepted as an integer or number.
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "null":
        return value is None
    return True


def _usable_identifier(value: Any) -> bool:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return False
    return str(value).strip().lower() not in _PLACEHOLDER_IDENTIFIERS


def _claim_tool_name(claim: Any) -> str | None:
    if isinstance(claim, str):
        return claim
    if isinstance(claim, Mapping):
        for key in ("tool", "tool_name", "name"):
            value = claim.get(key)
            if isinstance(value, str):
                return value

def _claim_call_index(claim: Any) -> int | None:
    if isinstance(claim, Mapping):
        value = claim.get("call_index")
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return None


def _tool_name(record: Mapping[str, Any]) -> str | None:
    value = record.get("name") or record.get("tool") or record.get("tool_name")
    return value if isinstance(value, str) else None


def _is_successful_result(result: Mapping[str, Any]) -> bool:
    if result.get("used") is True or "error" in result:
        return False
    payload = result.get("result")
    if isinstance(payload, Mapping) and "error" in payload:
        return False
    if "result" in result:
        return True
    status = result.get("status")
    return result.get("success") is True or status in {"success", "succeeded", "ok"}


def _successful_result_occurrences(case: Mapping[str, Any], calls: list[Mapping[str, Any]]) -> dict[str, list[bool]]:
    result_occurrences: dict[str, list[bool]] = {}
    for result in _results(case):
        name = _tool_name(result)
        if name is not None:
            result_occurrences.setdefault(name, []).append(_is_successful_result(result))
    call_occurrences: dict[str, list[bool]] = {}
    seen: dict[str, int] = {}
    for call in calls:
        name = _tool_name(call)
        if name is None:
            continue
        index = seen.get(name, 0)
        seen[name] = index + 1
        embedded = _is_successful_result(call)
        matching = result_occurrences.get(name, [])
        call_occurrences.setdefault(name, []).append(embedded or (index < len(matching) and matching[index]))
    return call_occurrences

def _final_success_claims(case: Mapping[str, Any], calls: list[Mapping[str, Any]]) -> list[str]:
    final_response = _trace(case).get("final_response", case.get("final_response"))
    if not isinstance(final_response, str) or "successfully" not in final_response.lower():
        return []
    names = [_tool_name(call) for call in calls]
    unique_names = {name for name in names if name is not None}
    return list(unique_names) if len(calls) == 1 and len(unique_names) == 1 else []


def _call_arguments(call: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(call.get("arguments") or call.get("args") or call.get("parameters"))


def _call_checks(
    call: Mapping[str, Any],
    specs: Mapping[str, Mapping[str, Any]],
    *,
    unknown_code: str,
    missing_code: str,
    type_code: str,
    identifier_code: str,
    call_index: int,
) -> list[dict]:
    tool_name = _tool_name(call)
    if tool_name is None or tool_name not in specs:
        return [{
            "code": unknown_code,
            "severity": "critical",
            "message": "The proposed tool is not declared in the supplied tool specifications.",
            "tool_name": tool_name,
            "call_index": call_index,
        }]
    checks: list[dict] = []
    spec = _specification(specs, tool_name)
    args = _call_arguments(call)
    parameters = _parameters(spec)
    for parameter in sorted(_required(spec)):
        if parameter not in args or args[parameter] is None:
            checks.append({
                "code": missing_code,
                "severity": "high",
                "message": f"Required argument '{parameter}' is missing.",
                "tool_name": tool_name,
                "call_index": call_index,
            })
    for parameter in sorted(parameters):
        if parameter not in args or args[parameter] is None:
            continue
        expected = _expected_types(_mapping(parameters[parameter]))
        if expected and not any(_has_type(args[parameter], item) for item in expected):
            checks.append({
                "code": type_code,
                "severity": "high",
                "message": f"Argument '{parameter}' has an incompatible primitive type; expected {', '.join(sorted(expected))}.",
                "tool_name": tool_name,
                "call_index": call_index,
            })
        if parameter.lower() in _IDENTIFIER_NAMES and not _usable_identifier(args[parameter]):
            checks.append({
                "code": identifier_code,
                "severity": "critical",
                "message": f"Identifier argument '{parameter}' is unusable.",
                "tool_name": tool_name,
                "call_index": call_index,
            })
    return checks


def _repair_text(repair: Mapping[str, Any]) -> str:
    response = repair.get("safer_final_response")
    return response.lower() if isinstance(response, str) else ""


def _strong_completion_claim(text: str) -> bool:
    completion_words = ("successfully", "completed", "resolved", "processed", "finished", "done", "has been", "have been", "was escalated", "already escalated")
    future_markers = ("will ", "should ", "can ", "could ", "would ", "need to ", "must ")
    clauses = re.split(r"(?<=[.!?])\s+|[;\n]+", text)
    return any(
        any(word in clause for word in completion_words)
        and not any(marker in clause for marker in future_markers)
        for clause in clauses
    )

def _has_successful_escalation(case: Mapping[str, Any], calls: list[Mapping[str, Any]], successful: Mapping[str, list[bool]]) -> bool:
    for name, outcomes in successful.items():
        if "escalat" in name.lower() and any(outcomes):
            return True
    for source in (_trace(case), case):
        escalation = source.get("escalation")
        if isinstance(escalation, Mapping) and _is_successful_result(escalation):
            return True
    return False


def run_repair_guardrails(cases: list[dict], tool_specs: dict, repairs: list[dict]) -> list[dict]:
    """Return deterministic findings that prevent unsafe repairs from reaching review."""
    specs = _normalise_specs(_mapping(tool_specs))
    decisions: list[dict] = []
    for case_index, raw_case in enumerate(cases):
        case = _mapping(raw_case)
        repair = _mapping(repairs[case_index]) if case_index < len(repairs) else {}
        calls = _calls(case)
        successful = _successful_result_occurrences(case, calls)
        checks: list[dict] = []
        action = repair.get("next_action_type")
        corrected_call = _mapping(repair.get("corrected_tool_call"))
        if action == "call_tool":
            checks.extend(_call_checks(
                corrected_call,
                specs,
                unknown_code="unknown_repair_tool",
                missing_code="missing_repair_required_arg",
                type_code="repair_primitive_type_error",
                identifier_code="unusable_repair_identifier",
                call_index=0,
            ))
        text = _repair_text(repair)
        if _strong_completion_claim(text):
            tool_name = _tool_name(corrected_call) if action == "call_tool" else None
            grounded = (
                tool_name is not None and any(successful.get(tool_name, ()))
                if action == "call_tool"
                else _has_successful_escalation(case, calls, successful)
                if action == "escalate"
                else any(any(outcomes) for outcomes in successful.values())
            )
            if not grounded:
                checks.append({
                    "code": "unsupported_repair_success_claim",
                    "severity": "critical",
                    "message": "The repair claims completion without matching successful original evidence.",
                    "tool_name": tool_name,
                    "call_index": 0 if tool_name is not None else None,
                })
        checks.sort(key=lambda item: (item["code"], str(item["tool_name"]), -1 if item["call_index"] is None else item["call_index"], item["message"]))
        decisions.append({
            "case_id": case.get("case_id"),
            "checks": checks,
            "has_blocking_findings": any(item["severity"] in {"critical", "high"} for item in checks),
        })
    return decisions

def run_guardrails(cases: list[dict], tool_specs: dict) -> list[dict]:
    """Return deterministic, inert pre-execution findings for each supplied case."""
    specs = _normalise_specs(_mapping(tool_specs))
    decisions: list[dict] = []
    for raw_case in cases:
        case = _mapping(raw_case)
        calls = _calls(case)
        checks: list[dict] = []
        successful_results = _successful_result_occurrences(case, calls)
        for call_index, call in enumerate(calls):
            checks.extend(_call_checks(
                call,
                specs,
                unknown_code="unknown_tool",
                missing_code="missing_required_arg",
                type_code="primitive_type_error",
                identifier_code="unusable_identifier",
                call_index=call_index,
            ))
        claim_occurrences: dict[str, int] = {}
        for claim in _claims(case):
            tool_name = _claim_tool_name(claim)
            if tool_name is None:
                continue
            occurrence = _claim_call_index(claim)
            if occurrence is None:
                occurrence = claim_occurrences.get(tool_name, 0)
                claim_occurrences[tool_name] = occurrence + 1
            if occurrence >= len(successful_results.get(tool_name, ())) or not successful_results[tool_name][occurrence]:
                checks.append({"code": "unsupported_success_claim", "severity": "critical", "message": "The success claim has no successful unused result for the same tool.", "tool_name": tool_name, "call_index": None})
        for tool_name in _final_success_claims(case, calls):
            if not successful_results.get(tool_name, [False])[0]:
                checks.append({"code": "unsupported_success_claim", "severity": "critical", "message": "The success claim has no successful unused result for the same tool.", "tool_name": tool_name, "call_index": None})
        checks.sort(key=lambda item: (item["code"], str(item["tool_name"]), -1 if item["call_index"] is None else item["call_index"], item["message"]))
        decisions.append({
            "case_id": case.get("case_id"),
            "checks": checks,
            "has_blocking_findings": any(item["severity"] in {"critical", "high"} for item in checks),
        })
    return decisions
