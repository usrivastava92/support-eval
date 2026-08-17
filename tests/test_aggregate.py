from support_eval.aggregate import build_failure_summary


def test_summary_recomputes_counts_and_orders_ties_without_fixtures():
    summary = build_failure_summary(
        [
            {"case_id": "z", "policy_compliant": True, "tool_use_valid": True, "final_response_supported": True, "primary_classification": "correct", "subtype": None, "tools_used": ["lookup"]},
            {"case_id": "a", "policy_compliant": False, "tool_use_valid": False, "final_response_supported": True, "primary_classification": "policy_violation", "subtype": "bad_claim", "tools_used": ["search", "lookup"]},
        ],
        [
            {"case_id": "a", "has_blocking_findings": True, "checks": [
                {"code": "unsupported_success_claim", "severity": "critical", "message": "unsupported", "tool_name": "search", "call_index": 0},
                {"code": "unusable_identifier", "severity": "critical", "message": "identifier", "tool_name": "search", "call_index": 1},
            ]},
            {"case_id": "z", "has_blocking_findings": False, "checks": [
                {"code": "unknown_tool", "severity": "critical", "message": "unknown", "tool_name": "archive", "call_index": 0},
            ]},
        ],
        [{"case_id": "a", "escalation": None}],
    )

    assert summary["total_cases"] == 2
    assert summary["compliant_cases"] == 1
    assert summary["unsafe_cases"] == 1
    assert sum(summary["counts_by_classification"].values()) == 2
    assert summary["counts_by_tool"] == {"lookup": 2, "search": 1}
    assert summary["unsupported_claims"] == 1
    assert summary["missing_identifier_errors"] == 1
    assert summary["escalation_worthy_cases"] == ["a", "z"]
    assert summary["top_policy_gaps"] == [
        {"gap": "unknown_tool", "count": 1},
        {"gap": "unsupported_success_claim", "count": 1},
        {"gap": "unusable_identifier", "count": 1},
    ]
    assert summary["subtype_counts"] == {"bad_claim": 1}


def test_summary_uses_guardrail_tool_only_when_evaluation_lacks_tools_metadata():
    summary = build_failure_summary(
        [{"case_id": "x", "policy_compliant": True, "tool_use_valid": False, "final_response_supported": True, "primary_classification": "invalid_tool_usage", "subtype": "unknown"}],
        [{"case_id": "x", "has_blocking_findings": True, "checks": [{"code": "unknown_tool", "severity": "critical", "message": "unknown", "tool_name": "dynamic_tool", "call_index": 0}]}],
        [],
    )

    assert summary["counts_by_tool"] == {"dynamic_tool": 1}
