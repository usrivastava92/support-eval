# support-eval

`support-eval` evaluates recorded support-agent interactions against a policy and tool specifications, then produces validated repair recommendations and an improvement plan.

It is a replayable, barriered pipeline rather than an operational support tool.
It never executes the tools described in `tool_specs.json`.

## Architecture

The pipeline snapshots `cases.jsonl`, `tool_specs.json`, and `policy.md` before making any provider call.
It then performs policy analysis, per-case evaluation, deterministic guardrail checks, per-case repair generation, deterministic repair guardrails, and repair review in that order.
Repair guardrails validate every generated repair from the recorded evidence without executing any described tool.
Their blocking findings are authoritative: reviewer approval cannot override them.
It aggregates the results and renders a Markdown improvement plan only after the preceding stages complete.
A stage failure stops later stages, and root-level result artifacts are published only after all required work succeeds.

Each run has an immutable workspace in `.support_eval/runs/<run-id>/`.
The workspace retains the input snapshot, manifest, stage-event log, prompts, raw and parsed model responses, call log, and replay captures.
Completed runs also publish the current result bundle at the selected root.

## Prerequisites

- Python 3.11 or later.
- An OpenAI-compatible Chat Completions endpoint for a live run, either OpenAI or DeepSeek.
- An API key for the selected live provider, unless running replay from captures.

## Install

Create and activate a virtual environment if desired, then install the package and its test extra:

```sh
python3 -m pip install -e ".[test]"
```

The installation provides the `support-eval` command.

## Configure a live provider

Copy the template and fill in only the values needed for the provider you intend to use:

```sh
cp .env.example .env
```

Keep `.env` local and untracked.
The repository ignores `.env`, and `.env.example` intentionally contains variable names only.

Both live providers receive the same OpenAI-compatible Chat Completions request shape.
The default OpenAI endpoint is `https://api.openai.com/v1` and its default model is `gpt-5.6-terra`.
The default DeepSeek endpoint is `https://api.deepseek.com/v1` and its default model is `deepseek-v4-pro`.
Use `OPENAI_BASE_URL` or `DEEPSEEK_BASE_URL` to override the corresponding endpoint, or use `--base-url` for one command.
Use `OPENAI_MODEL` or `DEEPSEEK_MODEL` to override the corresponding default model, or use `--model` for one command.

Provider selection uses this precedence:

1. `--provider openai` or `--provider deepseek`.
2. `EVAL_LLM_PROVIDER` from the environment or `.env`.
3. Presence of `OPENAI_API_KEY`.
4. Presence of `DEEPSEEK_API_KEY`.
5. An error that explains how to select a provider.

When both API keys are available and neither the CLI nor `EVAL_LLM_PROVIDER` selects a provider, OpenAI takes precedence.
Selecting a provider without its matching API key is an error.
Process environment values override values in `.env`.

For example, a live OpenAI run can be started with:

```sh
support-eval run --root . --provider openai
```

A one-off model or endpoint override can be supplied with the same command:

```sh
support-eval run --root . --provider deepseek --model deepseek-v4-pro --base-url https://api.deepseek.com/v1
```

## Input contract

Run commands read these three files from `--root`:

- `cases.jsonl` is newline-delimited JSON, with one object per case and a unique, non-empty string `case_id` in every record.
  The bundled cases also use `messages` and `assistant_trace` to describe the conversation and observed agent behavior supplied to the model.
- `tool_specs.json` is a JSON object describing the available tools.
  It may be a map of tool names to objects, or an object with only a `tools` array whose entries have unique `name`, string `description`, and object `input_schema` fields.
  In the `tools` array form, `input_schema.type` must be `"object"`, `properties` must be an object, and every `required` entry must name a declared property.
- `policy.md` is non-empty policy text.

Treat these inputs as the exact evidence set for a run.
Changing any of them creates a different input snapshot and prevents equivalence with an earlier completed workspace.

## Run, replay, score, validate, and test

Start a live evaluation from the repository root:

```sh
support-eval run --root .
```

The command prints a JSON object containing the run ID and workspace path.

Replay is strict and offline from a completed workspace.
Pass the completed workspace, not its `captures.jsonl` file, to `--capture-dir`:

```sh
support-eval replay --root . --capture-dir .support_eval/runs/<run-id>
```

Replay reads `captures.jsonl` from that directory and requires one response for every `(stage, case_id)` call reached by the pipeline.
Each record must contain exactly `stage`, `case_id`, and `response`, duplicate records fail, missing records fail, and a capture cannot be reused.
Replay does not need API keys and does not construct a live provider client.
It re-applies deterministic original and post-repair guardrails to the replayed model outputs, so a replay cannot publish a repair with blocking findings.

Print the deterministic aggregate from the root-level evaluation, guardrail, and repair artifacts:

```sh
support-eval score --root .
```

Validate the published root artifact bundle and its selected workspace:

```sh
python3 validate.py --root .
```

The root validator checks required artifacts, canonical result schemas, per-case coverage, deterministic original and repair guardrail results, failure-summary counters, call coverage and hashes, input hashes, ordered stage completion, and the published run pointer.
It rejects a completed bundle with missing, inconsistent, or blocking repair guardrail results even if the repair reviewer approved the repair.
Published pointer validation includes a hash for every root-level result artifact.
If a root has no published pointer, validation accepts exactly one discoverable workspace manifest, or use `--run` to select one explicitly:

```sh
python3 validate.py --root . --run .support_eval/runs/<run-id>
```

Run the test suite with:

```sh
pytest
```

## Artifacts

A completed run publishes these root-level artifacts:

- `policy_analysis.json`
- `case_evaluations.json`
- `guardrail_checks.json`
- `case_repairs.json`
- `repair_guardrail_checks.json`
- `repair_reviews.json`
- `failure_summary.json`
- `agent_improvement_plan.md`
- `llm_calls.jsonl`
- `run_manifest.json`

The workspace additionally contains `manifest.json`, `snapshot.json`, `stage_events.jsonl`, `captures.jsonl`, `inputs/`, `requests/`, `raw/`, and `parsed/` artifacts.
`repair_guardrail_checks.json` contains one canonical deterministic validation record per case for the generated repair.
`run_manifest.json` points to the completed workspace manifest and records hashes for every root-level published result, including repair guardrail checks.

## Reproducibility limits

The pipeline records immutable input bytes, prompt and schema versions, provider configuration, prompts, raw responses, parsed responses, stage events, and result hashes.
A strict replay can reproduce the captured response path without credentials, but it still uses the current local input files as the evaluation inputs and validates them against the workspace input hashes.
A new live run is not bit-for-bit reproducible because provider output, provider service behavior, timestamps, and generated workspace IDs can vary.
Do not edit a completed workspace or its captures when reproducibility matters.

## Troubleshooting

- `no provider resolved` means no CLI provider, `EVAL_LLM_PROVIDER`, or usable API key selected a live provider.
- A missing-key error means the selected provider does not have its matching API key in the process environment or `.env`.
- A replay-capture error means `--capture-dir` does not contain `captures.jsonl` or `captures.json` with the strict capture shape.
- A stage failure identifies the first failed stage, because later stages are intentionally not entered.
- A root validation hash mismatch means an input or published artifact differs from the selected completed workspace.
