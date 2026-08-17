"""Small command-line interface for live runs, replay, and deterministic scoring."""
from __future__ import annotations
import argparse
from pathlib import Path
from typing import Sequence
from .aggregate import build_failure_summary
from .artifacts import canonical_json, read_json, read_jsonl
from .guardrails import run_guardrails
from .pipeline import StageFailure, run_pipeline

def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(prog="support-eval")
    commands=parser.add_subparsers(dest="command",required=True)
    for name in ("run","replay"):
        item=commands.add_parser(name); item.add_argument("--root",type=Path,default=Path(".")); item.add_argument("--provider",choices=("openai","deepseek")); item.add_argument("--model"); item.add_argument("--base-url")
        if name=="replay": item.add_argument("--capture-dir",type=Path,required=True)
    score=commands.add_parser("score"); score.add_argument("--root",type=Path,default=Path("."))
    return parser

def main(argv: Sequence[str] | None = None) -> int:
    args=_parser().parse_args(argv)
    try:
        if args.command == "score":
            root=args.root; summary=build_failure_summary(read_json(root / "case_evaluations.json"),run_guardrails(read_jsonl(root / "cases.jsonl"),read_json(root / "tool_specs.json")),read_json(root / "case_repairs.json")); print(canonical_json(summary)); return 0
        result=run_pipeline(args.root,provider_name=args.provider,model=args.model,base_url=args.base_url,capture_dir=getattr(args,"capture_dir",None)); print(canonical_json({"run_id":result.run_id,"workspace":str(result.workspace)})); return 0
    except (OSError, ValueError, StageFailure) as error:
        print(f"support-eval: {error}",file=__import__("sys").stderr); return 1
