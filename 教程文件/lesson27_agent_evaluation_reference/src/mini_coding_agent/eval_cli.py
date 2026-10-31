"""Post-run evaluation CLI; it never starts or controls a live Agent."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .checkpoint import CheckpointStore, CheckpointError
from .evaluation import (
    EvalCase, EvaluationError, compare_results, evaluate_case,
    load_results, summarize_results, write_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini Coding Agent evaluation")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="Evaluate a finished checkpoint")
    inspect.add_argument("--case", type=Path, required=True)
    inspect.add_argument("--checkpoint", type=Path, required=True)
    inspect.add_argument("--workspace", type=Path, required=True)
    inspect.add_argument("--oracle-root", type=Path,
                         help="Trusted tests outside the editable Agent workspace")
    inspect.add_argument("--output", type=Path)
    inspect.add_argument("--run-id", help="Trial label, e.g. 01, 02, 03")
    inspect.add_argument("--run-tests", action="store_true",
                         help="Opt-in: execute trusted pytest files")

    summarize = commands.add_parser("summarize", help="Summarize result JSON array")
    summarize.add_argument("--results", type=Path, required=True)

    collect = commands.add_parser("collect", help="Combine individual results into array")
    collect.add_argument("--inputs", type=Path, nargs="+", required=True)
    collect.add_argument("--output", type=Path, required=True)

    compare = commands.add_parser("compare", help="Compare two result JSON arrays")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--current", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "inspect":
            workspace = args.workspace.resolve()
            if args.output is not None and args.output.resolve().is_relative_to(workspace):
                raise EvaluationError("--output must be outside workspace to avoid checkpoint drift")
            session = CheckpointStore(args.checkpoint, workspace).load()
            case = EvalCase.from_file(args.case)
            result = evaluate_case(case, workspace, session,
                                   run_tests=args.run_tests, oracle_root=args.oracle_root)
            result.run_id = args.run_id
            if args.output is not None:
                write_result(args.output, result)
            print(json.dumps(result.to_json(), indent=2, ensure_ascii=False))
            return 0 if result.passed else 1
        if args.command == "summarize":
            print(json.dumps(asdict(summarize_results(load_results(args.results))),
                             indent=2, ensure_ascii=False))
            return 0
        if args.command == "collect":
            items = [json.loads(path.read_text(encoding="utf-8")) for path in args.inputs]
            if not all(isinstance(x, dict) and "checks" in x for x in items):
                raise EvaluationError("Inputs must be individual evaluation results")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
            return 0
        if args.command == "compare":
            print(json.dumps(compare_results(load_results(args.current),
                                             load_results(args.baseline)),
                             indent=2, ensure_ascii=False))
            return 0
    except (OSError, ValueError, KeyError, TypeError, CheckpointError) as exc:
        parser.exit(2, f"evaluation error: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
