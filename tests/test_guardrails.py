import json
from pathlib import Path

from support_eval.guardrails import run_guardrails, run_repair_guardrails
from support_eval.schemas import validate_tool_specs


ROOT = Path(__file__).resolve().parents[1]


def _sample_inputs() -> tuple[list[dict], dict]:
    cases = [json.loads(line) for line in (ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    tool_specs = json.loads((ROOT / "tool_specs.json").read_text(encoding="utf-8"))
    return cases, tool_specs


def test_assignment_tool_specs_and_assistant_traces_are_supported():
    cases, tool_specs = _sample_inputs()

    assert validate_tool_specs(tool_specs) is tool_specs
    decisions = {item["case_id"]: item["checks"] for item in run_guardrails(cases, tool_specs)}

    assert [item["code"] for item in decisions["C2"]] == ["unsupported_success_claim"]
    assert [item["code"] for item in decisions["C3"]] == ["unusable_identifier"]
    assert not any(item["code"] == "unsupported_success_claim" for item in decisions["C1"])
    assert not any(item["code"] == "unsupported_success_claim" for item in decisions["C5"])


def test_result_payload_without_flag_is_success_but_error_is_not():
    specs = {"tools": [{"name": "lookup", "description": "Looks up a record.", "input_schema": {"type": "object", "properties": {}, "required": []}}]}
    cases = [{
        "case_id": "x",
        "assistant_trace": {
            "tool_calls": [{"name": "lookup", "arguments": {}}, {"name": "lookup", "arguments": {}}],
            "tool_results": [{"name": "lookup", "result": {"id": "one"}}, {"name": "lookup", "error": "not found"}],
            "success_claims": [{"tool_name": "lookup", "call_index": 0}, {"tool_name": "lookup", "call_index": 1}],
        },
    }]

    assert [check["code"] for check in run_guardrails(cases, specs)[0]["checks"]] == ["unsupported_success_claim"]


def test_assignment_tool_specs_are_strictly_validated():
    invalid = {"tools": [{"name": "lookup", "description": "Looks up a record.", "input_schema": {"type": "object", "properties": {}, "required": ["missing"]}}]}

    try:
        validate_tool_specs(invalid)
    except ValueError as error:
        assert "declared properties" in str(error)
    else:
        raise AssertionError("invalid assignment tool specifications were accepted")


def test_repair_guardrails_validate_corrected_calls_with_shared_schema_rules():
    cases = [{"case_id": "x", "tool_calls": [], "tool_results": []}]
    specs = {
        "lookup": {
            "parameters": {
                "required": ["ticket_id"],
                "properties": {"ticket_id": {"type": "string"}},
            }
        }
    }
    repairs = [
        {
            "case_id": "x",
            "next_action_type": "call_tool",
            "corrected_tool_call": {"name": "undeclared", "arguments": {}},
            "escalation": None,
            "safer_final_response": "I will look into this.",
            "explanation": "Use the supported tool.",
        }
    ]
    assert [check["code"] for check in run_repair_guardrails(cases, specs, repairs)[0]["checks"]] == ["unknown_repair_tool"]

    repairs[0]["corrected_tool_call"] = {"name": "lookup", "arguments": {"ticket_id": 7}}
    assert [check["code"] for check in run_repair_guardrails(cases, specs, repairs)[0]["checks"]] == ["repair_primitive_type_error"]

    repairs[0]["corrected_tool_call"] = {"name": "lookup", "arguments": {}}
    assert [check["code"] for check in run_repair_guardrails(cases, specs, repairs)[0]["checks"]] == ["missing_repair_required_arg"]

    repairs[0]["corrected_tool_call"] = {"name": "lookup", "arguments": {"ticket_id": "unknown"}}
    assert [check["code"] for check in run_repair_guardrails(cases, specs, repairs)[0]["checks"]] == ["unusable_repair_identifier"]


def test_repair_guardrails_require_original_evidence_for_completion_claims():
    repair = {
        "case_id": "x",
        "next_action_type": "call_tool",
        "corrected_tool_call": {"name": "lookup", "arguments": {}},
        "escalation": None,
        "safer_final_response": "I successfully completed the lookup.",
        "explanation": "The request is resolved.",
    }
    ungrounded = [{"case_id": "x", "tool_calls": [{"name": "lookup", "arguments": {}}], "tool_results": []}]
    grounded = [{"case_id": "x", "tool_calls": [{"name": "lookup", "arguments": {}}], "tool_results": [{"name": "lookup", "success": True}]}]

    assert [check["code"] for check in run_repair_guardrails(ungrounded, {"lookup": {}}, [repair])[0]["checks"]] == ["unsupported_repair_success_claim"]
    assert run_repair_guardrails(grounded, {"lookup": {}}, [repair])[0]["checks"] == []


    repair.update({
        "next_action_type": "respond_only",
        "corrected_tool_call": None,
        "safer_final_response": "Your request has been successfully completed.",
    })
    assert [check["code"] for check in run_repair_guardrails(ungrounded, {"lookup": {}}, [repair])[0]["checks"]] == ["unsupported_repair_success_claim"]
    repair["safer_final_response"] = "I will complete the lookup after your confirmation."
    assert run_repair_guardrails(ungrounded, {"lookup": {}}, [repair])[0]["checks"] == []
    repair["safer_final_response"] = "I will verify the status. Your request has been successfully completed."
    assert [check["code"] for check in run_repair_guardrails(ungrounded, {"lookup": {}}, [repair])[0]["checks"]] == ["unsupported_repair_success_claim"]


def test_repair_guardrails_ignore_retrospective_explanation_claims():
    repair = {
        "case_id": "x",
        "next_action_type": "ask_clarifying_question",
        "corrected_tool_call": None,
        "escalation": None,
        "safer_final_response": "I need the account ID to confirm the freeze.",
        "explanation": "The original agent claimed the account was frozen successfully without tool evidence.",
    }

    decision = run_repair_guardrails([{"case_id": "x"}], {}, [repair])[0]

    assert decision["checks"] == []
    assert decision["has_blocking_findings"] is False


def test_guardrails_derive_dynamic_tool_and_parameter_checks():
    decisions = run_guardrails(
        [{
            "case_id": "opaque",
            "tool_calls": [
                {"name": "lookup", "arguments": {"record_key": 7, "ticket_id": "  "}},
                {"name": "not_declared", "arguments": {}},
            ],
            "success_claims": [{"tool_name": "lookup"}],
        }],
        {"lookup": {"parameters": {"required": ["record_key", "ticket_id"], "properties": {"record_key": {"type": "string"}, "ticket_id": {"type": "string"}}}}},
    )

    assert decisions == [{
        "case_id": "opaque",
        "has_blocking_findings": True,
        "checks": [
            {"code": "primitive_type_error", "severity": "high", "message": "Argument 'record_key' has an incompatible primitive type; expected string.", "tool_name": "lookup", "call_index": 0},
            {"code": "unknown_tool", "severity": "critical", "message": "The proposed tool is not declared in the supplied tool specifications.", "tool_name": "not_declared", "call_index": 1},
            {"code": "unsupported_success_claim", "severity": "critical", "message": "The success claim has no successful unused result for the same tool.", "tool_name": "lookup", "call_index": None},
            {"code": "unusable_identifier", "severity": "critical", "message": "Identifier argument 'ticket_id' is unusable.", "tool_name": "lookup", "call_index": 0},
        ],
    }]


def test_success_claim_needs_unused_same_name_success_result():
    base = {"case_id": "x", "tool_calls": [{"name": "dynamic", "arguments": {}}], "success_claims": ["dynamic"]}
    used = {**base, "tool_results": [{"name": "dynamic", "success": True, "used": True}]}
    unused = {**base, "tool_results": [{"name": "dynamic", "success": True, "used": False}]}

    assert [check["code"] for check in run_guardrails([used], {"dynamic": {}})[0]["checks"]] == ["unsupported_success_claim"]
    assert run_guardrails([unused], {"dynamic": {}})[0]["checks"] == []
