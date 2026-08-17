"""Deterministic Markdown rendering for the production improvement plan."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any


def _records(records: list[dict]) -> list[Mapping[str, Any]]:
    return [record for record in records if isinstance(record, Mapping)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _rank(values: list[str]) -> list[str]:
    counter = Counter(values)
    return [value for value, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]


def _bullet_lines(values: list[str], fallback: str) -> list[str]:
    return [f"- {value}" for value in values] or [f"- {fallback}"]


def render_improvement_plan(policy_analysis: dict, evaluations: list[dict], repairs: list[dict], summary: dict) -> str:
    """Render a stable, input-derived plan without fixture or case identifiers."""
    analysis = policy_analysis if isinstance(policy_analysis, Mapping) else {}
    aggregate = summary if isinstance(summary, Mapping) else {}
    findings = analysis.get("findings") if isinstance(analysis.get("findings"), list) else []
    policy_fixes = _rank([
        " ".join(
            str(item[key]).strip()
            for key in ("severity", "category", "subject", "rule", "rationale")
            if isinstance(item.get(key), str) and item[key].strip()
        )
        for item in findings
        if isinstance(item, Mapping) and any(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("severity", "category", "subject", "rule", "rationale")
        )
    ])
    failed_explanations = _rank([
        explanation
        for record in _records(evaluations)
        if record.get("primary_classification") != "correct"
        for explanation in _strings(record.get("explanation"))
    ])
    repair_changes = _rank([
        change
        for record in _records(repairs)
        for change in _strings(record.get("explanation"))
    ])
    gaps = aggregate.get("top_policy_gaps") if isinstance(aggregate.get("top_policy_gaps"), list) else []
    gap_actions = [
        f"Add a blocking check for {item.get('gap')} ({item.get('count', 0)} observed findings)."
        for item in gaps
        if isinstance(item, Mapping) and isinstance(item.get("gap"), str)
    ]
    total = aggregate.get("total_cases", len(_records(evaluations)))
    compliant = aggregate.get("compliant_cases", 0)
    unsafe = aggregate.get("unsafe_cases", 0)
    unsupported = aggregate.get("unsupported_claims", 0)
    missing_identifiers = aggregate.get("missing_identifier_errors", 0)
    repair_guardrail_failures = aggregate.get("repair_guardrail_failures", 0)
    unsupported_repair_claims = aggregate.get("unsupported_repair_claims", 0)
    escalations = aggregate.get("escalation_worthy_cases", [])
    escalation_count = len(escalations) if isinstance(escalations, list) else 0

    lines = [
        "# Production Improvement Plan",
        "",
        "## Immediate critical fixes",
        *_bullet_lines(policy_fixes + failed_explanations, "No critical policy findings or unsafe evaluations were supplied."),
        f"- Resolve {repair_guardrail_failures} deterministic repair-guardrail failures before any repair reaches review or publication.",
        "",
        "## Pre-execution guardrails",
        *_bullet_lines(gap_actions, "Retain the current dynamic tool, argument, primitive type, success-claim, and identifier checks."),
        "",
        "## Escalation rules",
        f"- Escalate all unsafe classifications and critical guardrail findings. Current escalation volume: {escalation_count}/{total} cases.",
        f"- Require review for unsupported success claims ({unsupported}) and missing identifier errors ({missing_identifiers}).",
        f"- Monitor unsupported repair completion claims ({unsupported_repair_claims}) separately from original unsupported claims ({unsupported}).",
        "",
        "## Production metrics",
        f"- Report compliant and unsafe outcomes with denominator {total}: {compliant} compliant and {unsafe} unsafe.",
        "- Report per-tool usage, classification counts, subtype counts, and every policy-gap count with the same frozen input scope.",
        "",
        "## Over-blocking vs under-blocking",
        "- Measure over-blocking by reviewing blocked proposals that replay to compliant outcomes.",
        "- Measure under-blocking by reviewing unsafe outcomes that were not blocked before execution, especially unsupported claims and unusable identifiers.",
        "",
        "## Replay regression testing",
        "- Replay frozen inputs after each policy change and compare canonical summaries and Markdown byte-for-byte.",
        *_bullet_lines([f"Add a replay regression for repair guidance: {change}." for change in repair_changes], "Add a replay regression whenever a newly labeled failure or repair is captured."),
        "",
    ]
    return "\n".join(lines)
