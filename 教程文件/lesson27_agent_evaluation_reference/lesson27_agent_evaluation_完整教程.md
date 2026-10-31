# 第 27 课：Agent Evaluation / Regression Testing（完整代码教程）

**课程位置：Mini Coding Agent 第 27/27 课；参考源码：mini-coding-agent(6).zip。**

本课程以用户提交的第六版 Mini Coding Agent 为基线，讲解如何区分 Agent 自己的 Completion Protocol 与独立外部评估。源码引用自现有 `agent_metrics.py`, `agent_trace.py`, `checkpoint.py`, `agent.py`, `runtime_lifecycle.py`。其他代码为本课新增的参考实现，不是原项目既有功能。

## 0. 开始之前：确认课程目标

此前的课程使 Runtime 能控制规划、依赖、恢复、终止、Checkpoint、审批；本课不再引入复杂的 Agent 执行架构。我们新增 Evaluation Harness（评估执行器），用固定案例、外部 Oracle、指标和回归比较，来确认 Agent 对用户目标的实际达成情况。

**完成这一课，你应该能够解释：**

1. 为什么 `CompletionReport.COMPLETE` 不等于用户任务客观成功；
2. 为什么评估测试不能放在 Agent 可以随意修改的工作区；
3. 为什么效率指标必须在正确性指标之后比较；
4. 如何基于 `CheckpointStore.load()` 获取运行 Metrics，如何以单独的外部测试判断工作区正确性；
5. 如何将不同版本同一测试集的结果对齐，并找出回归；
6. 如何检测 `false_complete`（错误宣布完成）。

## 1. 第 6 版代码实际状况和基线

本次检查发现：原有 `tests/` 下 77 项测试通过（执行环境没有 OpenAI SDK，使用临时导入替身，不请求网络，不等于真实模型测试）。

`AgentMetrics` 已记录 `model_turns`, `tool_calls`, `replans`, `tool_executions`, `blocked_tool_calls`, `tests_run`, `run_outcome`, `stop_reaseon`。保留了拼写 `stop_reaseon`，避免此次课程引入 Checkpoint Schema 不兼容。

`finish_agent()` 的部分调用路径未传入 `session` / `checkpoint_store`，因此可能不持久化最终结果；`runtime_lifecycle.evaluate_runtime()` 调用 `check_lifecycle()` 使用错误参数 `last_meaningful_progress_turn`。这两处作为本课回归修复。

## 2. Evaluation 分三层

- **Unit Tests**：逐一验证算法（比如 Task Dependency 的 DAG 校验）；
- **Integration Tests**：验证多个模块的调用链（如工具被 Guard 拦截后，Checkpoint 的 `pending_tool` 仍然是 `None`）；
- **Agent Evaluation**：运行完整任务，在 Runner 可编辑工作区之外保存独立 Oracle，客观判断工作成果与不允许的副作用。

正确性与效率要分开判断，避免低轮次却未完成任务的模型被错误认定为优胜者。

示例 `false_complete`：AgentMetrics 的 `run_outcome='complete'`，但 `divide(1,0)` 未按需求抛出 `ValueError`。此时 `external_success=False`、`false_complete=True`。

## 3. 结构与调用链

```text
Agent Runner（用户任务）
  ↓ 更新可编辑的 fixture workspace
  ↓ CheckpointStore.save(RunSession)
Checkpoint(JSON) + Modified Workspace
  ↓ CheckpointStore.load()
  ↓ evaluate_case(EvalCase, workspace, session, oracle_root=...)
外部文件 Oracle + 独立 Pytest (明确 --run-tests 才执行)
  ↓ EvalResult JSON
  ↓ collect → summarize → compare
版本回归表 / False Complete 数量 / 成功任务平均 Model Turns
```

`RunSession` 通过第 25 课 `CheckpointStore` 恢复，里面已有 `AgentMetrics`。不必更改 `run_agent()` 的返回接口。

**安全限制：** 测试文件即使在工作区之外，Pytest 执行本身也可能运行任意代码，只应执行自己信任的测试，并在隔离环境中完成。该工具不提供操作系统沙箱。评估器绝不自动调用模型，也不会自行修改 Agent 的工作区。

## 4. 完整代码：evaluation.py

`EvalCase` 是预先写好的考试题。`EvalCheck` 表示一个外部断言。`EvalResult` 保留 Runtime 结果与外部正确性分离，`evaluate_case()` 只评价已完成或停止的一个 RunSession。`compare_results()` 用 `(case_id, run_id)` 对齐两版本相同的测试尝试。`run_id` 可以是 01、02、03 等重复实验编号。

特别注意 `pytest_paths` 必须相对于 **oracle_root**（评估器信任的独立目录），不能放到 Runner 可编辑的 workspace 里。

```python
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

```

## 5. 完整代码：eval_cli.py

这是一个独立 CLI，与 `mini-coding-agent` 的主 CLI 无冲突。只负责**运行后评价**。`inspect` 从 Checkpoint 读取 session，`collect` 合并单次试验的 JSON，`summarize` 计算汇总，`compare` 比较两个版本的同一批次结果。

```python
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

```

## 6. 示例：Calculator 任务的独立测试

### 6.1 初始损坏代码（每次运行都重置）

`evals/fixtures/calculator_before/calculator.py`

```python
"""Deliberately faulty evaluation fixture. Copy this into an isolated workspace."""

def divide(a: float, b: float) -> float:
    return a / b

```

### 6.2 外部 Oracle（Agent 不可编辑）

`evals/oracles/calculator/test_calculator.py`

```python
"""Trusted hidden tests. Keep OUTSIDE the Agent's editable workspace."""
import pytest

from calculator import divide


def test_normal_division():
    assert divide(10, 2) == 5


def test_division_by_zero_raises_value_error():
    with pytest.raises(ValueError):
        divide(10, 0)

```

### 6.3 固定 EvalCase

`evals/cases/calculator_divide_by_zero.json`

```json
{
  "case_id": "calculator_divide_by_zero",
  "task": "修改 calculator.py：除数为 0 时抛出 ValueError，并保持正常除法可用。",
  "expected_outcome": "complete",
  "must_exist": ["calculator.py"],
  "file_contains": {"calculator.py": "raise ValueError"},
  "pytest_paths": ["calculator/test_calculator.py"],
  "max_model_turns": 60,
  "max_tool_calls": 90
}

```

这一案例中，字符串 `raise ValueError` 只是一项弱的静态提示；真正的行为判断来自独立的 `test_normal_division` 和 `test_division_by_zero_raises_value_error`。如果开发者只在文件里写注释含有 `raise ValueError`，静态检查可能通过，但独立测试应该失败。

## 7. 怎样实际启动一次评价？

### 7.1 先建立独立测试工作区

在 Mini Coding Agent 源码根目录执行（工作区不要和 Agent 的源码目录混在一起）：

```bash
mkdir -p /tmp/mini-agent-eval-001
cp evals/fixtures/calculator_before/calculator.py /tmp/mini-agent-eval-001/calculator.py
mkdir -p "$HOME/.mini-agent-checkpoints"
mkdir -p "$HOME/.mini-agent-evals"
```

### 7.2 用已有的 Agent 执行任务

**必须在测试工作区内执行 Agent**；注意 `WORKSPACE = Path.cwd().resolve()`，当前工作目录就是 Agent 文件操作范围。你可以先在虚拟环境安装本包（`uv pip install -e /path/to/mini-coding-agent`），再切换测试目录。

```bash
cd /tmp/mini-agent-eval-001
mini-coding-agent --checkpoint "$HOME/.mini-agent-checkpoints/calculator-001.json"
```

输入任务（参考 EvalCase 的 task）：

> 修改 calculator.py：除数为 0 时抛出 ValueError，并保持正常除法可用。

执行结束以后，不要手动修改这次的 workspace 或 checkpoint，否则会影响结果解释。

### 7.3 运行独立评价

在装有 Mini Coding Agent 包、能找到 `evals/` 的源码根目录执行：

```bash
uv run python -m mini_coding_agent.eval_cli inspect \
  --case evals/cases/calculator_divide_by_zero.json \
  --checkpoint "$HOME/.mini-agent-checkpoints/calculator-001.json" \
  --workspace /tmp/mini-agent-eval-001 \
  --oracle-root evals/oracles \
  --run-tests \
  --run-id 01 \
  --output "$HOME/.mini-agent-evals/calculator-001.json"
```

如果 Outcome COMPLETE 且文件验证和独立 Pytest 均通过，报告含 `"passed": true`。如果模型宣称完成却没修复，则 `"false_complete": true`。成功率与耗时是不同的字段。

注意：示例测试路径是由评估器提供，确保 Agent 不知道或无法修改 Oracle。Path Validation 防止相对路径逃逸，但不替代隔离容器。

### 7.4 比较两个版本

每个 Agent 版本用相同初始 fixture 运行三次，分别输出 JSON（`run_id` 固定对应 01、02、03；本课程没有控制模型随机性的 Seed）。注意每次都必须使用新的 fixture 和 checkpoint，不能基于上一次修改后的源码继续跑。

```bash
uv run python -m mini_coding_agent.eval_cli collect \
  --inputs "$HOME/.mini-agent-evals/v1_01.json" \
           "$HOME/.mini-agent-evals/v1_02.json" \
           "$HOME/.mini-agent-evals/v1_03.json" \
  --output "$HOME/.mini-agent-evals/baseline.json"

uv run python -m mini_coding_agent.eval_cli collect \
  --inputs "$HOME/.mini-agent-evals/v2_01.json" \
           "$HOME/.mini-agent-evals/v2_02.json" \
           "$HOME/.mini-agent-evals/v2_03.json" \
  --output "$HOME/.mini-agent-evals/current.json"

uv run python -m mini_coding_agent.eval_cli summarize \
  --results "$HOME/.mini-agent-evals/current.json"

uv run python -m mini_coding_agent.eval_cli compare \
  --baseline "$HOME/.mini-agent-evals/baseline.json" \
  --current "$HOME/.mini-agent-evals/current.json"
```

比较脚本要求两个文件拥有完全相同的 `(case_id, run_id)` 集合，避免将少跑测试误当成成功率提升。最初只比较几个固定、可信的小任务，之后再扩展到 10–20 项，才能逐渐积累回归基准。输出中 `regressions` 表示基线成功、新版失败的任务尝试，`improvements` 表示基线失败、新版成功。**重复模型运行结果具有随机性，单次比较不表示必然性能提升。**

## 8. 现有项目必须修复的两个集成问题

### 8.1 agent.py 的两处 finish_agent()

当前第六版源码在“模型没有请求工具”的结束路径和“整批工具执行后的结束路径”没有传 `session`、`checkpoint_store`。本课最小改法是在这两处调用添加：

```python
return finish_agent(
    state=state,
    metrics=metrics,
    trace=trace,
    decision=decision,
    session=session,
    checkpoint_store=checkpoint_store,
)
```

模型返回无工具文本的路径还要保留 `preferred_text=message.content`。这样 `finish_agent()` 才会保存最终 `run_outcome` 和 Metrics，供 Evaluation CLI 读取。

此外，本课顺带修复了“工具参数 JSON 解析错误”分支将未定义的 `result` 塞进 Tool Response 的问题，改为：

```python
"content": f"Invalid tool arguments: {error}",
```

### 8.2 runtime_lifecycle.py 的 evaluate_runtime()

原代码传给 `check_lifecycle()` 的参数名称不匹配。修正后的完整函数：

```python
def evaluate_runtime(
        state: AgentState,
        metrics: AgentMetrics,
        progress: ProgressTracker,
        budget: RuntimeBudget,
) -> LifecycleDecision:
    """Convenience wrapper using the same ProgressTracker as run_agent()."""
    return check_lifecycle(
        state=state,
        model_turns=metrics.model_turns,
        progress=progress,
        budget=budget,
    )

```

## 9. 完整回归测试代码

`tests/test_lesson27_evaluation.py` 覆盖：外部 Oracle 成功、False Complete、Runtime 错误结束、无测试选项不允许蒙混过关、独立 Pytest 执行、路径穿越阻断、跨版本比较、重复试验、Checkpoint 最终状态持久化，以及真实 Agent Loop 的无工具/工具批次结束分支。

测试中模拟模型只用于 **Runtime 流程回归**；它不代表真实模型完成了用户任务。

```python
"""Lesson 27: evaluation correctness and the two discovered regression paths."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.agent import finish_agent
from mini_coding_agent.checkpoint import CheckpointStore, RunSession
from mini_coding_agent.evaluation import (
    EvalCase, EvaluationError, compare_results, evaluate_case,
    load_results, summarize_results, write_result,
)
from mini_coding_agent.planning import create_agent_state
from mini_coding_agent.runtime_lifecycle import (
    LifecycleAction, LifecycleDecision, ProgressTracker,
    RunOutcome, RuntimeBudget, StopReason, evaluate_runtime,
)
from mini_coding_agent.task_completion import evaluate_completion


def _session(outcome: str = "complete", turns: int = 8) -> RunSession:
    session = RunSession(state=create_agent_state("fix a calculator"))
    session.metrics.run_outcome = outcome
    session.metrics.model_turns = turns
    session.metrics.tool_calls = 12
    session.metrics.replans = 1
    session.metrics.end_time = session.metrics.start_time + 10.0
    return session


def _case(**kwargs):
    return EvalCase(case_id="calc", task="fix a calculator", **kwargs)


def test_independent_oracle_passes_successful_artifact(tmp_path):
    (tmp_path / "calc.py").write_text("raise ValueError('zero')", encoding="utf-8")
    case = _case(must_exist=["calc.py"], file_contains={"calc.py": "raise ValueError"},
                 max_model_turns=10)
    result = evaluate_case(case, tmp_path, _session())
    assert result.passed
    assert result.external_success
    assert result.runtime_matches_expected
    assert result.efficiency_warnings == []
    assert result.duration_seconds == pytest.approx(10.0)


def test_runtime_complete_but_external_failure_is_false_complete(tmp_path):
    (tmp_path / "calc.py").write_text("return a / b", encoding="utf-8")
    result = evaluate_case(_case(file_contains={"calc.py": "raise ValueError"}),
                           tmp_path, _session("complete"))
    assert not result.passed
    assert result.false_complete
    assert not result.external_success


def test_external_success_but_runtime_stopped_is_not_pass(tmp_path):
    (tmp_path / "calc.py").write_text("raise ValueError()", encoding="utf-8")
    result = evaluate_case(_case(file_contains={"calc.py": "raise ValueError"}),
                           tmp_path, _session("stopped"))
    assert not result.passed
    assert result.external_success
    assert not result.false_complete


def test_absence_oracle(tmp_path):
    result = evaluate_case(_case(must_not_exist=["old.py"]), tmp_path, _session())
    assert result.passed
    (tmp_path / "old.py").write_text("old", encoding="utf-8")
    assert not evaluate_case(_case(must_not_exist=["old.py"]), tmp_path, _session()).passed


def test_file_not_contains_requires_existing_file(tmp_path):
    result = evaluate_case(_case(file_not_contains={"missing.py": "bad"}),
                           tmp_path, _session())
    assert not result.external_success
    (tmp_path / "missing.py").write_text("good", encoding="utf-8")
    assert evaluate_case(_case(file_not_contains={"missing.py": "bad"}),
                         tmp_path, _session()).passed


def test_missing_opt_in_tests_never_silently_pass(tmp_path):
    oracle = tmp_path.parent / (tmp_path.name + "_oracle")
    oracle.mkdir()
    (oracle / "test_math.py").write_text("def test_one(): assert 1 == 1", encoding="utf-8")
    case = _case(pytest_paths=["test_math.py"])
    result = evaluate_case(case, tmp_path, _session())
    assert not result.passed
    assert any(check.name == "pytest" and not check.passed for check in result.checks)
    result = evaluate_case(case, tmp_path, _session(), run_tests=True, oracle_root=oracle)
    assert result.passed


def test_bad_pytest_fails(tmp_path):
    oracle = tmp_path.parent / (tmp_path.name + "_oracle")
    oracle.mkdir()
    (oracle / "test_bad.py").write_text("def test_bad(): assert False", encoding="utf-8")
    result = evaluate_case(_case(pytest_paths=["test_bad.py"]),
                           tmp_path, _session(), run_tests=True, oracle_root=oracle)
    assert not result.external_success


@pytest.mark.parametrize("unsafe", ["../escape.py", "/etc/passwd"])
def test_refuse_paths_outside_workspace(tmp_path, unsafe):
    with pytest.raises(EvaluationError):
        evaluate_case(_case(must_exist=[unsafe]), tmp_path, _session())


def test_refuse_symlink_outside_workspace(tmp_path):
    outside = tmp_path.parent / "private.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    with pytest.raises(EvaluationError):
        evaluate_case(_case(must_exist=["link.txt"]), tmp_path, _session())


def test_case_requires_external_oracle(tmp_path):
    with pytest.raises(EvaluationError):
        evaluate_case(_case(), tmp_path, _session())


def test_case_json_rejects_unknown_keys(tmp_path):
    file = tmp_path / "case.json"
    file.write_text(json.dumps({"case_id": "x", "task": "x", "must_exist": ["x"],
                                "prompt_injection": "anything"}))
    with pytest.raises(EvaluationError):
        EvalCase.from_file(file)


def test_efficiency_budget_is_warning_not_correctness_failure(tmp_path):
    (tmp_path / "ok.py").write_text("ok", encoding="utf-8")
    result = evaluate_case(_case(must_exist=["ok.py"], max_model_turns=5,
                                 max_tool_calls=10), tmp_path, _session())
    assert result.passed
    assert len(result.efficiency_warnings) == 2


def test_result_roundtrip_and_suite_comparison(tmp_path):
    (tmp_path / "ok.py").write_text("yes", encoding="utf-8")
    good = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session())
    bad = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session("stopped"))
    result_path = tmp_path / "one.json"
    write_result(result_path, good)
    assert json.loads(result_path.read_text())["case_id"] == "calc"
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(json.dumps([bad.to_json()]), encoding="utf-8")
    new.write_text(json.dumps([good.to_json()]), encoding="utf-8")
    diff = compare_results(load_results(new), load_results(old))
    assert diff["improvements"] == ["calc"]
    assert diff["regressions"] == []
    assert summarize_results(load_results(new)).pass_rate == 1
    diff_reversed = compare_results(load_results(old), load_results(new))
    assert diff_reversed["regressions"] == ["calc"]


def test_suite_different_cases_not_comparable(tmp_path):
    (tmp_path / "ok.py").write_text("yes", encoding="utf-8")
    old = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session())
    new = evaluate_case(EvalCase("other", "task", must_exist=["ok.py"]),
                        tmp_path, _session())
    with pytest.raises(EvaluationError):
        compare_results([old], [new])


def test_evaluate_runtime_wrapper_uses_tracker_not_old_kwarg():
    session = _session("stopped")
    decision = evaluate_runtime(session.state, session.metrics,
                                ProgressTracker(), RuntimeBudget())
    assert decision.action == LifecycleAction.CONTINUE


def test_finish_agent_persists_final_outcome(tmp_path):
    session = _session("stopped")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CheckpointStore(tmp_path / "checkpoint.json", workspace)
    decision = LifecycleDecision(
        action=LifecycleAction.TERMINATE,
        report=evaluate_completion(session.state),
        outcome=RunOutcome.STOPPED,
        reason=StopReason.NO_PROGRESS,
    )
    result = finish_agent(
        state=session.state, metrics=session.metrics, trace=session.trace,
        decision=decision, session=session, checkpoint_store=store,
    )
    assert "no_progress" in result
    assert store.load().metrics.run_outcome == "stopped"
    assert store.load().metrics.stop_reaseon == "no_progress"

class FakeMessage:
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []
        self.content = "model summary"

    def model_dump(self, **_kwargs):
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [{"id": x.id, "type": "function",
                                      "function": {"name": x.function.name,
                                                   "arguments": x.function.arguments}}
                                     for x in self.tool_calls]
        return message


@pytest.mark.parametrize("with_tool", [False, True])
def test_agent_end_paths_persist_final_checkpoint(tmp_path, monkeypatch, with_tool):
    """Regression: both no-tool and tool-batch termination must save outcome."""
    from types import SimpleNamespace
    from mini_coding_agent import agent

    def fake_check(state, model_turns, progress, budget):
        final = model_turns >= 1
        return LifecycleDecision(
            action=LifecycleAction.FINALIZE if final else LifecycleAction.CONTINUE,
            report=evaluate_completion(state),
            outcome=RunOutcome.COMPLETE if final else None,
        )

    calls = ([SimpleNamespace(id="call_1", function=SimpleNamespace(
        name="read_file", arguments='{"path":"missing"}'))] if with_tool else [])
    monkeypatch.setattr(agent, "check_lifecycle", fake_check)
    monkeypatch.setattr(agent, "call_model", lambda *args, **kwargs: FakeMessage(calls))
    monkeypatch.setattr(agent, "compress_history", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_fixed_context_breakdown", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_context_debug", lambda **kwargs: None)
    monkeypatch.setattr(agent, "print_state_debug", lambda state: None)
    # Running task must not touch actual workspace files. A guard rejection
    # is sufficient to complete one full, persisted tool-response turn.
    checkpoint = tmp_path / "run.json"
    agent.run_agent("test safe completion", checkpoint_path=str(checkpoint))
    stored = CheckpointStore(checkpoint, agent.WORKSPACE).load()
    assert stored.metrics.run_outcome == "complete"
    assert stored.metrics.model_turns == 1
    if with_tool:
        assert len(stored.history_turns) == 1
        assert stored.history_turns[0][-1]["role"] == "tool"


def test_compare_multiple_trials_by_run_id(tmp_path):
    (tmp_path / "ok.py").write_text("pass", encoding="utf-8")
    result_1 = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session())
    result_2 = evaluate_case(_case(must_exist=["ok.py"]), tmp_path, _session("stopped"))
    result_1.run_id = "1"
    result_2.run_id = "2"
    assert len({(r.case_id, r.run_id) for r in (result_1, result_2)}) == 2
    assert compare_results([result_1, result_2], [result_1, result_2])["regressions"] == []
    with pytest.raises(EvaluationError):
        compare_results([result_1, result_1], [result_1, result_2])

```

## 10. 本地验收记录与限制

- 第六版原有 77 项测试通过（使用临时 OpenAI SDK 导入替身）。
- 加入本课新增的 20 项测试后，共 97 项通过。
- 使用一个**人为修好的 Calculator Fixture** 和**模拟的 COMPLETED Checkpoint** 做了 CLI `inspect --run-tests` 冒烟测试：外部 Oracle 两项测试通过、`passed=true`。这不是 Agent 实际自主完成任务的数据，不能记作真实任务成功率。
- 没有运行真实模型，也没有验证真实多次独立任务成功率、模型费用或生产级并发安全。
- 工具可能通过 run_command 间接修改文件而未更新 Revision；外部 Oracle 可以发现部分结果错误，但这类 Verification 一致性问题仍需要单独治理。
- `--run-tests` 会执行用户提供的测试代码，只适用于可信测试目录和隔离环境。Pytest 不是沙箱。

## 11. 核心架构毕业标准

- 77 项已有测试 + 本课新增回归测试全部通过；
- 第 22–23 课 Task Dependency / Replanning 具备可重复测试；
- 第 24 课 Lifecycle 不再反复在 COMPLETE 后请求模型；
- 第 25 课 Checkpoint 可以安全恢复完整 Turn，对不确定工具拒绝盲目重放；
- 第 26 课 Tool Safety 拒绝操作不能靠重复提示绕过；
- 对至少 3 种独立任务（修改函数、跨文件重构、权限拒绝）有固定 fixture、外部 oracle、重复评估记录；
- False Complete 数量为 0，再考虑改善 Model Turns、Tool Calls、Replans。

完成以上标准后可以转入 MCP：MCP 不需要替代现有 Agent Loop，只需要让已有 Tool Runner 通过 MCP Client 发现和调用 MCP Server 工具。

## 12. 思考题

1. 如果 `AgentMetrics.run_outcome == 'complete'`，但独立 Pytest 失败，结果为什么必须是 `false_complete`？
2. 为什么要把评估用 `test_calculator.py` 放在 Agent 无法修改的目录？
3. 为什么版本 A 平均 18 次模型调用、版本 B 平均 30 次模型调用，不能直接推断 A 更优秀？
4. 如果相同一个 case 跑三次，两个版本各自通过 2 次，只凭这些结果能否断言版本性能相等？
5. 为什么 Checkpoint 和评估结果 JSON 都最好保存在被测工作区之外？
