# Agent guidance

## Repository responsibilities

This repository evaluates recorded support-agent behavior against a policy and tool specifications.
It produces evaluation, guardrail, repair, review, aggregate, and improvement-plan artifacts.
It is not an operational support-agent implementation and must not execute described support tools.

## Directory map and ownership

- `src/support_eval/cli.py` owns the public command-line surface for `run`, `replay`, and `score`.
- `src/support_eval/pipeline.py` owns stage ordering, barriers, input snapshots, workspace lifecycle, and root publication.
- `src/support_eval/providers/` owns provider resolution, live OpenAI-compatible Chat Completions adapters, response decoding, and replay capture semantics.
- `src/support_eval/schemas.py` owns input and model-output contracts.
- `src/support_eval/guardrails.py` owns deterministic tool and policy guardrail checks.
- `src/support_eval/aggregate.py` owns deterministic failure-summary aggregation.
- `src/support_eval/render_plan.py` owns deterministic improvement-plan rendering.
- `src/support_eval/artifacts.py` owns canonical serialization, atomic writes, workspaces, and publication.
- `validate.py` independently validates a published root bundle and its selected workspace.
- `tests/` owns observable pipeline, validation, aggregate, and guardrail contracts.
- `cases.jsonl`, `tool_specs.json`, and `policy.md` are the versioned evaluation inputs.

## Stage invariants

- Snapshot all three evaluation inputs before selecting a provider or issuing a provider call.
- Preserve the frozen stage order: `INIT`, `INPUTS_LOADED`, `TOOLS_AND_POLICY_PARSED`, `CASES_NORMALISED`, `CASES_EVALUATED`, `CASES_REPAIRED`, `FAILURE_PATTERNS_AGGREGATED`, `POLICY_PLAN_GENERATED`, and `RESULTS_FINALISED`.
- Treat stage failure as a hard barrier, so later provider calls and stages remain unreachable.
- Keep repair review as a required substep of case repair.
- Validate every model response against its stage contract before using or publishing it.
- Publish root artifacts transactionally only after all prerequisite work has completed.
- Preserve call logging, input hashes, artifact hashes, and workspace manifests as integrity evidence.
- Keep replay credential-free, single-use per `(stage, case_id)`, and driven only by captures.

## Validation and changes

Update or add tests when changing an observable contract, stage invariant, provider selection rule, schema, artifact shape, or validator rule.
Run the focused test coverage and the relevant CLI or validator path for implementation changes.
Keep `validate.py` independent of the pipeline so it can detect publication and integrity failures.

## Secret safety

Never commit, print, or document credential values.
Keep `.env` ignored and keep `.env.example` limited to variable names and non-secret comments.
Do not place secrets in captures, fixtures, generated artifacts, error messages, or test assertions.

## Generated artifacts

Treat `.support_eval/runs/` workspaces and root-level result artifacts as generated evidence rather than source inputs.
Do not hand-edit them to make validation pass.
Regenerate them through the pipeline, and keep generated outputs out of commits unless a task explicitly requires fixture material.

## Documentation

Read [README.md](README.md) for installation, provider configuration, input preparation, command usage, replay, scoring, validation, artifacts, reproducibility, and troubleshooting.
Keep this file focused on repository ownership and invariants instead of duplicating evaluator onboarding.
