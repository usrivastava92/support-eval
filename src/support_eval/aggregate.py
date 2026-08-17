"""Pure deterministic aggregation of validated pipeline records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any


_REQUIRED_CLASSIFICATIONS = (
    "correct",
    "invalid_tool_usage",
    "missing_clarification",
    "other",
    "policy_violation",
    "should_escalate",
    "unnecessary_tool_call",
    "unsupported_claim",
)
_CRITICAL_CHECKS = frozenset({"unknown_tool", "primitive_type_error", "unsupported_success_claim", "unusable_identifier"})
_IDENTIFIER_CODES = frozenset({"unusable_identifier", "missing_required_arg"})
_IDENTIFIER_NAMES = frozenset({"id", "identifier", "case_id", "ticket_id", "user_id", "account_id", "order_id"})


def _records(values: list[dict]) -> list[Mapping[str, Any]]:
    return [value for value in values if isinstance(value, Mapping)]


def _checks(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = record.get("checks")
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _classification(record: Mapping[str, Any]) -> str:
    supplied = record.get("primary_classification")
    if isinstance(supplied, str) and supplied:
        return supplied
    failed = [
        not bool(record.get("policy_compliant")),
        not bool(record.get("tool_use_valid")),
        not bool(record.get("final_response_supported")),
    ]
    if not any(failed):
        return "correct"
    if sum(failed) > 1:
        return "other"
    return ("policy_violation", "invalid_tool_usage", "unsupported_claim")[failed.index(True)]


def _ordered_counts(counter: Counter[str]) -> dict[str, int]:
    return {name: counter[name] for name in sorted(counter, key=lambda name: (-counter[name], name))}


def build_failure_summary(
    evaluations: list[dict],
    guardrails: list[dict],
    repairs: list[dict],
    repair_guardrails: list[dict] | None = None,
) -> dict:
    """Build the frozen summary only from validated record collections."""
    evaluation_records = _records(evaluations)
    guardrail_by_case = {record.get("case_id"): record for record in _records(guardrails)}
    repair_guardrail_by_case = {
        record.get("case_id"): record for record in _records(repair_guardrails or [])
    }
    repair_by_case = {record.get("case_id"): record for record in _records(repairs)}
    classifications: Counter[str] = Counter()
    subtype_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    policy_gaps: Counter[str] = Counter()
    unsupported_claims = 0
    repair_guardrail_failures = 0
    unsupported_repair_claims = 0
    missing_identifier_errors = 0
    escalation_cases: set[Any] = set()
    cases_with_tools_metadata: set[Any] = set()

    for evaluation in evaluation_records:
        case_id = evaluation.get("case_id")
        classification = _classification(evaluation)
        classifications[classification] += 1
        subtype = evaluation.get("subtype")
        if isinstance(subtype, str) and subtype:
            subtype_counts[subtype] += 1
        tools_used = evaluation.get("tools_used")
        if isinstance(tools_used, list):
            for tool_name in tools_used:
                if isinstance(tool_name, str):
                    tool_counts[tool_name] += 1
            cases_with_tools_metadata.add(case_id)
        if classification != "correct":
            escalation_cases.add(case_id)

    for name in _REQUIRED_CLASSIFICATIONS:
        classifications.setdefault(name, 0)

    for case_id, guardrail in guardrail_by_case.items():
        for check in _checks(guardrail):
            code = str(check.get("code", "unknown"))
            policy_gaps[code] += 1
            tool_name = check.get("tool_name")
            if not isinstance(tool_name, str) and isinstance(check.get("tool"), str):
                tool_name = check["tool"]
            if isinstance(tool_name, str) and case_id not in cases_with_tools_metadata:
                tool_counts[tool_name] += 1
            if code == "unsupported_success_claim":
                unsupported_claims += 1
            if code in _IDENTIFIER_CODES:
                message = str(check.get("message", "")).lower()
                if code == "unusable_identifier" or any(name in message for name in _IDENTIFIER_NAMES):
                    missing_identifier_errors += 1
            if code in _CRITICAL_CHECKS or check.get("severity") == "critical":
                escalation_cases.add(case_id)
        if guardrail.get("has_blocking_findings") is True:
            escalation_cases.add(case_id)

    for case_id, repair_guardrail in repair_guardrail_by_case.items():
        checks = _checks(repair_guardrail)
        repair_guardrail_failures += len(checks)
        unsupported_repair_claims += sum(
            check.get("code") == "unsupported_repair_success_claim"
            for check in checks
        )
        if repair_guardrail.get("has_blocking_findings") is True:
            escalation_cases.add(case_id)
    # A repair which explicitly escalates is itself an auditable escalation signal.
    escalation_cases.update(case_id for case_id, repair in repair_by_case.items() if repair.get("escalation") not in (None, False, ""))
    total_cases = len(evaluation_records)
    compliant_cases = classifications["correct"]
    return {
        "schema_version": "1",
        "total_cases": total_cases,
        "compliant_cases": compliant_cases,
        "unsafe_cases": total_cases - compliant_cases,
        "counts_by_classification": _ordered_counts(classifications),
        "counts_by_tool": _ordered_counts(tool_counts),
        "unsupported_claims": unsupported_claims,
        "missing_identifier_errors": missing_identifier_errors,
        "escalation_worthy_cases": sorted(escalation_cases, key=lambda value: str(value)),
        "top_policy_gaps": [
            {"gap": gap, "count": count}
            for gap, count in sorted(policy_gaps.items(), key=lambda item: (-item[1], item[0]))
        ],
        "repair_guardrail_failures": repair_guardrail_failures,
        "unsupported_repair_claims": unsupported_repair_claims,
        "subtype_counts": _ordered_counts(subtype_counts),
    }
