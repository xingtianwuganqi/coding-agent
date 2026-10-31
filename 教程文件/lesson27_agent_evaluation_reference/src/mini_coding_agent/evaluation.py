"""Lesson 27: independent, post-run evaluation of Mini Coding Agent.

This module checks workspace artifacts and opt-in tests *independently* of
AgentState's own CompletionReport. It never calls an LLM or executes shell.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .checkpoint import RunSession


class EvaluationError(ValueError):
    """Invalid evaluation specification or unsafe target path."""


def _require_relative_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise EvaluationError("File path must be a non-empty string")
    path = Path(raw)
    if path.is_absolute() or any(part in ("..", "") for part in path.parts):
        raise EvaluationError(f"Unsafe evaluation path: {raw!r}")
    return path


def _inside_workspace(workspace: Path, raw: str) -> Path:
    relative = _require_relative_path(raw)
    workspace = workspace.resolve()
    target = (workspace / relative).resolve()
    if not target.is_relative_to(workspace):
        raise EvaluationError(f"Path escapes evaluation workspace: {raw!r}")
    return target


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    task: str
    expected_outcome: str = "complete"
    must_exist: list[str] = field(default_factory=list)
    must_not_exist: list[str] = field(default_factory=list)
    file_contains: dict[str, str] = field(default_factory=dict)
    file_not_contains: dict[str, str] = field(default_factory=dict)
    pytest_paths: list[str] = field(default_factory=list)
    max_model_turns: int | None = None
    max_tool_calls: int | None = None

    @classmethod
    def from_file(cls, path: Path) -> "EvalCase":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise EvaluationError("Evaluation case must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise EvaluationError(f"Unknown case keys: {sorted(unknown)}")
        try:
            case = cls(**data)
        except TypeError as exc:
            raise EvaluationError(f"Invalid evaluation case: {exc}") from exc
        case.validate()
        return case

    def validate(self) -> None:
        if not self.case_id or not self.task:
            raise EvaluationError("case_id and task are required")
        if self.expected_outcome not in {"complete", "blocked", "stopped"}:
            raise EvaluationError("Invalid expected_outcome")
        if not any((self.must_exist, self.must_not_exist,
                    self.file_contains, self.file_not_contains,
                    self.pytest_paths)):
            raise EvaluationError("At least one independent oracle check is required")
        for files in (self.must_exist, self.must_not_exist, self.pytest_paths):
            if not isinstance(files, list) or not all(isinstance(x, str) for x in files):
                raise EvaluationError("Path entries must be lists of strings")
            for path in files:
                _require_relative_path(path)
        for files in (self.file_contains, self.file_not_contains):
            if not isinstance(files, dict):
                raise EvaluationError("File text checks must be objects")
            for path, text in files.items():
                _require_relative_path(path)
                if not isinstance(text, str) or not text:
                    raise EvaluationError("Text conditions must be nonempty strings")
        for limit in (self.max_model_turns, self.max_tool_calls):
            if limit is not None and (type(limit) is not int or limit < 0):
                raise EvaluationError("Budget limits must be nonnegative integers")


@dataclass(frozen=True)
class EvalCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalResult:
    case_id: str
    runtime_outcome: str | None
    expected_outcome: str
    external_success: bool
    runtime_matches_expected: bool
    passed: bool
    false_complete: bool
    model_turns: int
    tool_calls: int
    replans: int
    duration_seconds: float | None
    checks: list[EvalCheck]
    efficiency_warnings: list[str]
    run_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _check_files(case: EvalCase, workspace: Path) -> list[EvalCheck]:
    checks: list[EvalCheck] = []
    for raw in case.must_exist:
        target = _inside_workspace(workspace, raw)
        checks.append(EvalCheck(f"exists:{raw}", target.is_file(), "file must exist"))
    for raw in case.must_not_exist:
        target = _inside_workspace(workspace, raw)
        checks.append(EvalCheck(f"absent:{raw}", not target.exists(), "path must be absent"))
    for mapping, should_contain in ((case.file_contains, True),
                                    (case.file_not_contains, False)):
        for raw, needle in mapping.items():
            target = _inside_workspace(workspace, raw)
            text = target.read_text(encoding="utf-8") if target.is_file() else None
            passed = text is not None and ((needle in text) == should_contain)
            kind = "contains" if should_contain else "not_contains"
            checks.append(EvalCheck(f"{kind}:{raw}", passed,
                                    "matched" if passed else "file missing or content mismatch"))
    return checks


def _run_trusted_tests(
    workspace: Path, oracle_root: Path, paths: list[str], timeout: int
) -> EvalCheck:
    # These test files belong to the evaluator, not the editable Agent workspace.
    oracle_root = oracle_root.resolve()
    test_paths = [_inside_workspace(oracle_root, raw) for raw in paths]
    for path in test_paths:
        if not path.is_file():
            return EvalCheck("pytest", False, f"Oracle test missing: {path.name}")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(workspace) + os.pathsep + environment.get("PYTHONPATH", "")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *(str(x) for x in test_paths)],
            cwd=workspace, env=environment, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return EvalCheck("pytest", False, f"Test execution failed: {type(exc).__name__}")
    # Keep a bounded, user-inspectable error preview; avoid dumping files/logs.
    tail = (result.stdout + "\n" + result.stderr)[-1200:]
    return EvalCheck("pytest", result.returncode == 0, tail)


def evaluate_case(
    case: EvalCase,
    workspace: Path,
    session: RunSession,
    *,
    run_tests: bool = False,
    oracle_root: Path | None = None,
    test_timeout: int = 120,
) -> EvalResult:
    """Evaluate an already-finished session; never ask the Agent to grade itself.

    Only explicitly opted-in, trusted pytest paths are executed. The path
    guard prevents traversal; it is NOT a sandbox for malicious Python tests.
    """
    case.validate()
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise EvaluationError("Workspace directory does not exist")
    checks = _check_files(case, workspace)
    if case.pytest_paths:
        if run_tests and oracle_root is None:
            raise EvaluationError("--run-tests requires a separate --oracle-root")
        checks.append(
            _run_trusted_tests(workspace, Path(oracle_root), case.pytest_paths, test_timeout)
            if run_tests else
            EvalCheck("pytest", False, "Not run: provide external --oracle-root and --run-tests")
        )
    metrics = session.metrics
    outcome = metrics.run_outcome
    external_success = bool(checks) and all(c.passed for c in checks)
    outcome_match = outcome == case.expected_outcome
    warnings: list[str] = []
    if case.max_model_turns is not None and metrics.model_turns > case.max_model_turns:
        warnings.append(f"model_turns {metrics.model_turns} > {case.max_model_turns}")
    if case.max_tool_calls is not None and metrics.tool_calls > case.max_tool_calls:
        warnings.append(f"tool_calls {metrics.tool_calls} > {case.max_tool_calls}")
    # Success and budget measurements are kept separate: budget regressions
    # should not turn a correct file result into a false claim of failure.
    return EvalResult(
        case_id=case.case_id,
        runtime_outcome=outcome,
        expected_outcome=case.expected_outcome,
        external_success=external_success,
        runtime_matches_expected=outcome_match,
        passed=external_success and outcome_match,
        false_complete=(outcome == "complete" and not external_success),
        model_turns=metrics.model_turns,
        tool_calls=metrics.tool_calls,
        replans=metrics.replans,
        duration_seconds=(metrics.duration if metrics.end_time is not None else None),
        checks=checks,
        efficiency_warnings=warnings,
    )


@dataclass(frozen=True)
class SuiteSummary:
    case_count: int
    passed_count: int
    pass_rate: float
    false_complete_count: int
    avg_successful_turns: float | None


def summarize_results(results: list[EvalResult]) -> SuiteSummary:
    passed_count = sum(result.passed for result in results)
    successful_turns = [result.model_turns for result in results if result.passed]
    return SuiteSummary(
        case_count=len(results),
        passed_count=passed_count,
        pass_rate=passed_count / len(results) if results else 0.0,
        false_complete_count=sum(result.false_complete for result in results),
        avg_successful_turns=(sum(successful_turns) / len(successful_turns)
                              if successful_turns else None),
    )


def compare_results(current: list[EvalResult], baseline: list[EvalResult]) -> dict[str, Any]:
    """Compare the same case set. A missing case is an error, not a free pass."""
    def key(r: EvalResult) -> tuple[str, str | None]:
        return (r.case_id, r.run_id)

    current_ids = {key(r) for r in current}
    baseline_ids = {key(r) for r in baseline}
    if len(current_ids) != len(current) or len(baseline_ids) != len(baseline):
        raise EvaluationError("Duplicate (case_id, run_id) in evaluation results")
    if current_ids != baseline_ids:
        raise EvaluationError("Cannot compare different evaluation case/run sets")
    old = {key(r): r for r in baseline}
    regressions = sorted(r.case_id + (f"#{r.run_id}" if r.run_id else "")
                         for r in current if old[key(r)].passed and not r.passed)
    improvements = sorted(r.case_id + (f"#{r.run_id}" if r.run_id else "")
                          for r in current if not old[key(r)].passed and r.passed)
    return {
        "baseline": asdict(summarize_results(baseline)),
        "current": asdict(summarize_results(current)),
        "regressions": regressions,
        "improvements": improvements,
    }


def write_result(path: Path, result: EvalResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_results(path: Path) -> list[EvalResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise EvaluationError("Results file must contain a JSON array")
    result: list[EvalResult] = []
    for item in data:
        item = dict(item)
        item["checks"] = [EvalCheck(**c) for c in item["checks"]]
        result.append(EvalResult(**item))
    return result
