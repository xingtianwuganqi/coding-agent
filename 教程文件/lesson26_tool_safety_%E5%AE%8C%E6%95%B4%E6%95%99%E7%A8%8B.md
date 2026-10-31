# 第 26 课：Tool Safety / Human-in-the-loop（完整实践教程）

**基线**：`mini-coding-agent(5).zip`。**验证**：98 个本地测试通过（原版 77 + 新增 21；使用离线 OpenAI SDK 导入替身；没有调用真实模型）。

## 1. 核心目标

你已经拥有 `Permission`、`ask_for_confirmation`、`ask_user`、`execute_tool_with_recovery` 和第 25 课的 Checkpoint。
本课不是重写它们，而是统一它们的调用顺序，并明确三种不同的决策：

- **Guard**：当前阶段能不能做（Goal / Requirement / Plan 等业务前置条件）。
- **Policy**：操作本身属于 SAFE、WRITE、CONFIRM 还是 BLOCKED。
- **Approval**：对 CONFIRM 操作，用户是否对此次调用明确同意。

**ask_user 不是审批。** `ask_user` 的“选择 0/输入关闭”仅表示未作选择，绝不构成危险操作的授权。

## 2. 为什么需要改动

现有代码中，`delete_file` 在 `tool_runner.py` 弹确认，`run_command` 在 `tools.py` 弹确认；拒绝后虽然没有自动重试，但下一轮模型可能重复请求同一个操作。
同时，原 `agent.py` 在解析参数、Guard 和审批之前就调用了 `CheckpointStore.before_tool()`，导致完全未执行的拒绝操作也会产生一次“可能执行过”的待处理标记（如果在输入中途崩溃）。

我们将修改为：

```
call_model
  -> parse arguments
  -> guard_tool_call
  -> guard_recent_tool_call
  -> authorize_tool_call
     -> BLOCKED / DENIED: skip_tool (no pending marker)
     -> ALLOWED / APPROVED:
          before_tool (write-ahead marker)
          execute_tool_with_recovery(preauthorized=True)
          after_tool
  -> finish_turn
  -> ProgressTracker.observe
  -> LifecycleController
```

这里的 `before_tool` 不可以晚于实际副作用；`after_tool` 也不能早于最终结果与 AgentState 可以一致持久化。

## 3. 结构化审批结果

`approval.py` 引入 `ApprovalStatus` 和 `ApprovalDecision`。`ALLOWED` 表示无需用户批准；`APPROVED` 表示用户明确批准；`DENIED` 表示拒绝或无法获取授权；`BLOCKED` 表示政策禁止。

`denied_signatures` 只记录当前 RunSession 中**完全相同的工具参数**的拒绝，防止 Agent 连续弹出相同询问；新参数仍需重新判断。审批不能跨操作复用。

## 4. Checkpoint 与拒绝衔接

```
# 用户拒绝：没有工具执行，安全保存 Tool Response
session.inflight_turn.append(tool_output)
checkpoint.save(session)

# 用户批准：工具可能执行，所以必须先持久化 pending_tool
checkpoint.before_tool(...)
execute_tool(...)
checkpoint.after_tool(...)
```

拒绝记录和已完成的 Tool Response 一同持久化，Safe Resume 后不再针对同一签名重复询问。
如果中断发生在真正的 `before_tool` 之后，保留 `RecoveryRequired`，不自动重放。

## 5. Policy 不能等于 Sandbox

本实现对命令参数采取保守策略：仅对少量固定的命令字符串免确认；其他 legacy SAFE 命令也要求批准，禁止的命令继续拒绝。
不过，即使是 `git diff` 或 `pytest`，在不可信工作区中也存在插件、Git 配置、测试脚本等额外风险。操作系统级隔离必须另做。

文件工具额外禁止直接访问 `.env`、`.git`、`.venv`、`.mini_agent` 目录或文件；但这**不等于** `run_command` 也受到同样路径保护。不要在不可信仓库中开放任意命令执行。

## 6. 一课掌握的集成位置

| 文件 | 主要改动 |
|---|---|
| `approval.py`（新增） | Policy + User Approval + Denial Signature |
| `agent.py` | 统一入口；审批通过才执行和保存 pending_tool |
| `checkpoint.py` | `denied_approvals` 持久化与 `skip_tool()` |
| `tool_runner.py` | `preauthorized` 避免第二次审批；拒绝不走 Recovery |
| `tools.py` | 命令重新校验；保护敏感路径 |
| `permission.py` | EOF/Ctrl-C 按拒绝处理 |
| `tool_result.py` | `failure_code`：区分拒绝与普通工具失败 |
| `tests/test_approval_gate.py` | 单元测试 + Agent Loop 模拟集成测试 |

## 7. 测试执行

在项目根目录：

```bash
git apply --check lesson26_tool_safety.patch
git apply lesson26_tool_safety.patch
uv run pytest -q
```

最好先备份现有 Runner，实际测试可用临时工作区。`tests/test_approval_gate.py` 全部为模拟测试，不会调用真实模型。

## 8. 思考题

1. 用户拒绝删除 `a.py` 后，模型再次请求完全相同操作，为什么不需要再次弹窗？
2. 用户批准删除 `a.py`，是否等于批准删除 `b.py`？为什么？
3. 为什么输入框 EOF 必须视为拒绝，而不是 `ask_user` 里的“选择 0，继续判断”？
4. 为什么对被拒绝的工具不能调用 `CheckpointStore.before_tool()`，而应该使用 `skip_tool()`？
5. 在 `before_tool` 和 `after_tool` 之间进程崩溃，为什么不能让模型猜测文件有没有删掉？

---

# 附录：本课参考实现的完整源码

下面所有文件均来自同一份本地通过测试的参考实现（基于用户上传第 5 版源码），可以逐个对照查看。可优先阅读新增的 `approval.py`、`tests/test_approval_gate.py`，再看 `agent.py` 中 `for call in tool_calls` 的变更；不建议直接整体覆盖你本地已修改过的文件。


## `src/mini_coding_agent/approval.py`

```python
"""Lesson 26: one policy decision and one explicit approval per tool invocation.

This is a teaching-grade gate, NOT an OS sandbox. Tool implementations
still need independent path checks; untrusted subprocesses need isolation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .permission import (
    Permission, TOOL_PERMISSIONS, classify_command, ask_for_confirmation,
)


class ApprovalStatus(str, Enum):
    ALLOWED = "allowed"
    APPROVED = "approved"
    DENIED = "denied"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ApprovalDecision:
    status: ApprovalStatus
    reason: str
    signature: str

    @property
    def allowed(self) -> bool:
        return self.status in {ApprovalStatus.ALLOWED, ApprovalStatus.APPROVED}


# Intentionally tiny allowlist of command forms in *trusted* workspaces.
# A test runner or arbitrary git option can execute code / have side effects.
READ_ONLY_COMMAND_FORMS = {"pwd", "ls", "ls -la", "git status", "git diff"}


def policy_for_tool(name: str, arguments: dict) -> Permission:
    policy = TOOL_PERMISSIONS.get(name, Permission.BLOCKED)
    if policy == Permission.BLOCKED:
        return policy
    if name == "run_command":
        command = arguments.get("command")
        if not isinstance(command, str):
            return Permission.BLOCKED
        original = classify_command(command)
        if original == Permission.BLOCKED:
            return Permission.BLOCKED
        # A command that was classified SAFE at the legacy layer might
        # still run project code, write a file, or have unexpected flags.
        if original == Permission.SAFE and command.strip() not in READ_ONLY_COMMAND_FORMS:
            return Permission.CONFIRM
        return original
    return policy


def approval_signature(name: str, arguments: dict) -> str:
    """Exact call signature. A denial persists for this call in one RunSession."""
    content = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def approval_detail(name: str, arguments: dict) -> str:
    if name == "delete_file":
        return f"Target file: {arguments.get('path', '<unknown>')}"
    if name == "run_command":
        return f"Command: {arguments.get('command', '<missing>')}"
    return "Arguments: " + json.dumps(arguments, ensure_ascii=False, default=str)


def authorize_tool_call(
    name: str,
    arguments: dict,
    *,
    denied_signatures: set[str] | None = None,
    confirm: Callable[[str, str | None], bool] | None = None,
) -> ApprovalDecision:
    """Fail closed: only a positive y/yes approves a CONFIRM tool.

    `denied_signatures` is scoped to the current RunSession; a previous
    rejection of exactly the same call must not re-prompt the user.
    """
    signature = approval_signature(name, arguments)
    policy = policy_for_tool(name, arguments)
    if policy == Permission.BLOCKED:
        return ApprovalDecision(
            ApprovalStatus.BLOCKED, f"POLICY_BLOCKED: {name}", signature
        )
    if policy in {Permission.SAFE, Permission.WRITE}:
        return ApprovalDecision(ApprovalStatus.ALLOWED, "Allowed by policy", signature)

    if denied_signatures is not None and signature in denied_signatures:
        return ApprovalDecision(
            ApprovalStatus.DENIED,
            "USER_DENIED: this exact operation was already rejected in this run.",
            signature,
        )

    if confirm is None:
        confirm = ask_for_confirmation
    try:
        approved = confirm(name, approval_detail(name, arguments))
    except (EOFError, KeyboardInterrupt):
        approved = False
    if approved:
        return ApprovalDecision(ApprovalStatus.APPROVED, "Explicit user approval", signature)

    if denied_signatures is not None:
        denied_signatures.add(signature)
    return ApprovalDecision(
        ApprovalStatus.DENIED,
        "USER_DENIED: user denied or did not approve the operation.",
        signature,
    )

```


## `src/mini_coding_agent/agent.py`

```python
"""Agent loop: drives the model, tools, planning and completion checks."""

import json
import os
from pathlib import Path
from .config import WORKSPACE
from .checkpoint import CheckpointStore, CheckpointError, RunSession
from .approval import authorize_tool_call, ApprovalStatus

from .config import MAX_MODEL_TURN
from .model_api import ModelCallError, call_model
from .context import (
    build_model_input,
    format_runtime_state,
    compress_history,
    print_context_debug,
    print_fixed_context_breakdown,
    truncate_text,
)
from .planning import (
    AgentState,
    create_agent_state,
    format_plan,
    print_state_debug,
    # record_progress,
    tool_caused_progress,
    is_stagnating,
    build_stagnation_feedback,
    build_runtime_instructions
)
from .task_completion import (
    CompletionStatus,
    get_completion_status,
    get_unfinished_tasks,
    evaluate_completion,
    build_completion_feedback
)
from .prompts import SYSTEM_PROMPT
from .tools_schema import TOOLS
from .tool_runner import (
    execute_tool_with_recovery
)
from .tool_result import ToolResult
from .agent_metrics import (
    AgentMetrics
)

from .agent_trace import (
    AgentTrace
)


from .guard import (
    RETRIEVAL_TOOLS,
    guard_tool_call,
    guard_recent_tool_call,
)
from .replanning import build_replan_feedback

from .runtime_lifecycle import (
    RuntimeBudget,
    LifecycleAction,
    LifecycleDecision,
    evaluate_runtime,
    RunOutcome,
    ProgressTracker,
    StopReason,
    check_lifecycle
)

def finish_agent(
        *,
        state: AgentState,
        metrics: AgentMetrics,
        trace: AgentTrace,
        decision: LifecycleDecision,
        preferred_text: str = "",
        session: RunSession | None = None,
        checkpoint_store: CheckpointStore | None = None,
) -> str:
    """
    统一的单词终止出口：无需模型继续决定是否停止。
    """
    metrics.run_outcome = decision.outcome.value if decision.outcome else None
    metrics.stop_reaseon = decision.reason.value if decision.reason else None
    trace.record(
        turn=metrics.model_turns,
        event_type="lifecycle_end",
        name=metrics.run_outcome or "unknown",
        detail=metrics.stop_reaseon or "requirements_satisfied",
    )
    metrics.end_runing()
    if checkpoint_store is not None and session is not None:
        checkpoint_store.save(session=session)
    trace.print_trace()

    # 已通过 Runtime 检查，才允许返回模型此前的总结文字
    if decision.outcome == RunOutcome.COMPLETE:
        return preferred_text.strip() or "Outcome: complete\nAll completion requirements satisfied."

    missing = [f"- {r.name}: {r.reason}" for r in decision.report.missing]
    changed = sorted(state.changed_files)
    lines = [
        f"Outcome: {metrics.run_outcome}",
        f"Reason: {metrics.stop_reaseon or 'requirements_satisfied'}",
        f"Model turns: {metrics.model_turns}",
        "Changed files: " + (", ".join(changed) if changed else "none"),
        "Unmet conditions:",
        *(missing or ["- none"]),
    ]
    return "\n".join(lines)


def run_agent(
        task: str,
        *,
        checkpoint_path: str | None = None, # 保存位置
        resume: bool = False, # 恢复， 决定创建新会话还是恢复旧会话
) -> str:

    """Run agent, optionally persisting a safe checkpoint after each tool.

    Resume NEVER auto-replays an uncertain tool call. Use a separate
    checkpoint path for each independent task.
    """

    if resume and not checkpoint_path:
        raise CheckpointError("--resume requires --checkpoint")

    checkpoint_store=(
        CheckpointStore(Path(checkpoint_path), WORKSPACE)
        if checkpoint_path else None
    )
    if resume:
        assert checkpoint_store is not None
        session = checkpoint_store.load()
        saved_task = session.state.goal.original_request
        if task and task != saved_task:
            raise CheckpointError("Task does not match saved checkpoint")
        task = saved_task
    else:
        if checkpoint_store is not None and checkpoint_store.path.exists():
            raise CheckpointError(
                "Checkpoint already exists. Resume it or use a new path; "
                "do not overwrite an unfinished run."
            )
        session = RunSession(state=create_agent_state(user_prompt=task))
        if checkpoint_store is not None:
            checkpoint_store.save(session=session)

    state = session.state
    metrics = session.metrics
    trace = session.trace
    progress = session.progress
    history_turns = session.history_turns

    # state = create_agent_state(
    #     user_prompt=task
    # )

    # # 指标对象
    # metrics = AgentMetrics()

    # # 追踪
    # trace = AgentTrace()

    # 24课单独追踪“真实里程碑”，不依赖旧的 record_progress()。
    budget = RuntimeBudget(
        max_model_turns=min(MAX_MODEL_TURN, 120),
        max_no_progress_turns=30,
        warn_no_progress_turns=12,
    )
    # progress = ProgressTracker()
    progress.observe(
        state=state,
        report=check_lifecycle(state, 0, progress, budget).report,
        turn=0
    )

    # allow_blocked_final = False
    
    # input_items = [
    #     {
    #         "role": "user",
    #         "content": task,
    #     }
    # ]

    # history_turns : list[list] = []
    if checkpoint_store is not None:
        checkpoint_store.save(session=session)

    # history_turns points to the restored session; never reset on resume.

    while True:
        # 模型调用之前先判断：已完成就不要继续消耗 Token。
        decision = check_lifecycle(state, metrics.model_turns, progress, budget)
        if decision.action != LifecycleAction.CONTINUE:
            return finish_agent(
                state=state,
                metrics=metrics,
                trace=trace,
                decision=decision,
                session=session,
                checkpoint_store=checkpoint_store,
            )
        # 仅警告一次；达到硬阈值则由上面的Controller 直接终止。
        if progress.should_warn(metrics.model_turns, budget):
            metrics.stagnation_warnings += 1
            state.stagnation_warnings += 1
            history_turns.append([{
                "role": "user",
                "content": build_stagnation_feedback(state)
            }])
        metrics.model_turns += 1
        turn = metrics.model_turns
        print(f"\n --- Agent step {turn} ---")
        trace.record(
            turn=metrics.model_turns,
            event_type="model_turn",
            name="call_model",
        )

        # 构建是否停滞
        # if is_stagnating(
        #     state=state,
        #     current_turn=metrics.model_turns
        # ):
        #     trace.record(
        #         turn=metrics.model_turns,
        #         event_type="stagnation",
        #         name="progress_guard",
        #         detail=(
        #             f"last_progress_turn="
        #             f"{state.last_progress_turn}"
        #         ),
        #     )
        #     metrics.stagnation_warnings += 1
        #     state.stagnation_warnings += 1
        #     stagnation_feedback = build_stagnation_feedback(
        #         state=state
        #     )
        #     history_turns.append(
        #         [
        #             {
        #                 "role": "user",
        #                 "content": stagnation_feedback
        #             }
        #         ]
        #     )

        # 先压缩旧history，会在内部进行删除
        try:
            compress_history(
                system_prompt=SYSTEM_PROMPT,
                tools=TOOLS,
                task=task,
                state=state,
                history_turns=history_turns,
                metrics=metrics,
                trace=trace
            )
        except ModelCallError as error:
            failed_report = check_lifecycle(
                state=state,
                model_turns=metrics.model_turns,
                progress=progress,
                budget=budget
            ).report
            decision = LifecycleDecision(
                action=LifecycleAction.TERMINATE,
                report=failed_report,
                outcome=RunOutcome.STOPPED,
                reason=StopReason.MODEL_ERROR,
            )
            finish_agent(
                state=state,
                metrics=metrics,
                trace=trace,
                decision=decision,
                session=session,
                checkpoint_store=checkpoint_store
            )
            return f"Agent stopped: {error}"

        if checkpoint_store is not None:
            checkpoint_store.save(session=session) # compress_history may change summary/history

        print_fixed_context_breakdown(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            state=state
        )

        print_context_debug(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            state=state,
            history_turns=history_turns
        )

        runtime_instructions = build_runtime_instructions(
            state=state
        )

        replan_context = build_replan_feedback(
            state=state
        ) or ""

        # -------------------------
        # 1. 构建有限长度 Context
        # -------------------------
        model_input = build_model_input(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            history_turns=history_turns,
            state=state
        )

        # -------------------------
        # 2. 注入最新 Runtime State
        # -------------------------
        runtime_instructions = (
            SYSTEM_PROMPT
            + "\n\n"
            + "CURRENT RUNTIME STATE:\n"
            + format_runtime_state(state)
            + runtime_instructions
            + replan_context
            + "\n\n"
        )

        print_state_debug(state)

        try:
            message = call_model(
                model_input,
                instructions=runtime_instructions,
                tools=TOOLS,
                thinking=False,
                debug=(
                    os.getenv("MINI_AGENT_DEBUG") == "1"
                ),
            )
        except ModelCallError as error:
            failed_report = check_lifecycle(
                state=state,
                model_turns=metrics.model_turns,
                progress=progress,
                budget=budget
            ).report
            decision = LifecycleDecision(
                action=LifecycleAction.TERMINATE,
                report=failed_report,
                outcome=RunOutcome.STOPPED,
                reason=StopReason.MODEL_ERROR,
            )
            finish_agent(
                state=state, 
                metrics=metrics, 
                trace=trace, 
                decision=decision,
                session=session,
                checkpoint_store=checkpoint_store
            )
            return f"Agent stopped: {error}"
        # Preserve reasoning_content alongside tool calls for GLM's next turn.
        # input_items.append(message.model_dump(mode="json", exclude_none=True))
        turn_items = [message.model_dump(mode="json", exclude_none=True)]
        tool_calls = message.tool_calls or []

        # v3
        if not tool_calls:
            # 模型停止调用工具，并不等于任务结束
            decision = check_lifecycle(
                state=state,
                model_turns=metrics.model_turns,
                progress=progress,
                budget=budget,
            )
            metrics.completion_checks += 1
            trace.record(
                turn=metrics.model_turns,
                event_type="completion_check",
                name="no_tools",
                detail=(
                    f"status={decision.report.complete.value}"
                )
            )
            if decision.action != LifecycleAction.CONTINUE:
                return finish_agent(
                    state=state, metrics=metrics, trace=trace,
                    decision=decision, preferred_text=message.content
                )

            metrics.completion_rejections += 1
            history_turns.append(turn_items + [{
                "role": "user",
                "content": build_completion_feedback(decision.report, state),
            }])
            if checkpoint_store is not None:
                checkpoint_store.save(session=session)
            continue

        # v2
        # if not tool_calls:
        #     # Completion Protocol
        #     report = evaluate_completion(state=state)
        #     metrics.completion_checks += 1
        #     missing_names = [
        #         item.name
        #         for item in report.missing
        #     ]
        #     trace.record(
        #         turn=metrics.model_turns,
        #         event_type="completion_check",
        #         name="completion_protocol",
        #         detail=(
        #             f"completion={report.complete}"
        #             f"missing={missing_names}"
        #         )
        #     )

        #     # 真正完成
        #     if report.complete == CompletionStatus.COMPLETE:
        #         metrics.end_runing()
        #         trace.print_trace()
        #         return message.content or ""

        #     if  report.complete == CompletionStatus.BLOCKED and allow_blocked_final:
        #         metrics.end_runing()
        #         trace.print_trace()
        #         return message.content or ""


        #     if report.complete == CompletionStatus.INCOMPLETE:

        #         metrics.completion_rejections += 1

        #         feed_back = build_completion_feedback(
        #             report=report,
        #             state=state
        #         )

        #         turn_items.append(
        #             {
        #                 'role': 'user',
        #                 'content': feed_back
        #             }
        #         )

        #         history_turns.append(
        #             turn_items
        #         )

        #         continue

        #     if report.complete == CompletionStatus.BLOCKED:
        #         allow_blocked_final = True
        #         feed_back = build_completion_feedback(
        #             report=report,
        #             state=state
        #         )
                
        #         turn_items.append(
        #             {
        #                 'role': 'user',
        #                 'content': feed_back
        #             }
        #         )

        #         history_turns.append(
        #             turn_items
        #         )

        #         continue

            # v1
            # completion_status = get_completion_status(
            #     state
            # )

            # if completion_status == CompletionStatus.COMPLETE:
            #     metrics.end_runing()
            #     trace.print_trace()
            #     return message.content or ""

            # if (
            #     completion_status == CompletionStatus.BLOCKED
            #     and allow_blocked_final == True
            # ):
            #     metrics.end_runing()
            #     trace.print_trace()
            #     return message.content or ""


            # if completion_status == CompletionStatus.INCOMPLETE:
            #     # unfinished = get_unfinished_tasks(state)

            #     # unfinished_text = "\n".join(
            #     #     f"- {todo.id}. {todo.content}"
            #     #     for todo in unfinished
            #     # )

            #     runtime_feedback = {
            #         "role": "user",
            #         "content": (
            #             "RUNTIME GUARD: no plan exists yet. "
            #             "Create a concise todo plan with set_plan before finishing."
            #         ),
            #     }

            #     turn_items.append(runtime_feedback)
            #     history_turns.append(
            #         turn_items
            #     )

            #     continue

            # if completion_status == CompletionStatus.BLOCKED:
            #     allow_blocked_final = True
            #     runtime_feedback = {
            #         "role": "user",
            #         "content": (
            #             "Some tasks are blocked.\n\n"
            #             f"{format_plan(state)}\n\n"
            #             "Provide a final answer "
            #             "explaining completed work "
            #             "and blocked tasks."
            #         ),
            #     }
            #     turn_items.append(
            #         runtime_feedback
            #     )

            #     history_turns.append(
            #         turn_items
            #     )

            #     continue

        # -------------------------
        # 5. 执行 Tool Calls
        # -------------------------
        if checkpoint_store is not None:
            checkpoint_store.begin_turn(session=session, assistant=turn_items[0])

        for call in tool_calls:
            name = call.function.name
            print(f"Tool: {name}")
            print("Arguments: " + truncate_text(str(call.function.arguments), 1000))

            # Important: only an actually dispatched tool gets a write-ahead
            # marker. A Guard/Policy/User rejection has NO tool side effect.
            execution_started = False
            try:
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")

                metrics.record_tool_call(name)
                trace.record(
                    turn=metrics.model_turns,
                    event_type="tool_requested",
                    name=name,
                    detail="arguments parsed",
                )

                guard_error = guard_tool_call(
                    name=name, arguments=arguments,
                    state=state, metrics=metrics, trace=trace,
                )
                if not guard_error:
                    guard_error = guard_recent_tool_call(
                        name=name, arguments=arguments, state=state,
                    )

                if guard_error:
                    metrics.blocked_tool_calls += 1
                    trace.record(
                        turn=metrics.model_turns,
                        event_type="guard_rejected",
                        name=name,
                        detail=guard_error,
                    )
                    result = ToolResult.fail(error=guard_error)
                else:
                    authorization = authorize_tool_call(
                        name=name,
                        arguments=arguments,
                        denied_signatures=session.denied_approvals,
                    )
                    trace.record(
                        turn=metrics.model_turns,
                        event_type="approval_" + authorization.status.value,
                        name=name,
                        # Never log raw command arguments or file contents here.
                        detail="signature=" + authorization.signature[:12],
                    )

                    if not authorization.allowed:
                        metrics.blocked_tool_calls += 1
                        result = ToolResult.fail(
                            error=authorization.reason,
                            failure_code=(
                                "USER_DENIED"
                                if authorization.status == ApprovalStatus.DENIED
                                else "POLICY_BLOCKED"
                            ),
                        )
                    else:
                        # A checkpoint now means the tool may have actually
                        # run. Save this marker as close as possible to execute.
                        if checkpoint_store is not None:
                            checkpoint_store.before_tool(
                                session, call.id, name, arguments,
                            )
                        execution_started = True
                        result = execute_tool_with_recovery(
                            name=name,
                            arguments=arguments,
                            state=state,
                            metrics=metrics,
                            trace=trace,
                            current_turn=metrics.model_turns,
                            preauthorized=True,
                        )

                tool_output = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result.content if result.success else result.error,
                }

            except (json.JSONDecodeError, ValueError) as error:
                # If the tool was already dispatched, it may have modified
                # the workspace before raising. Keep the pending marker and
                # require recovery; never commit it as a harmless parse error.
                if execution_started:
                    raise
                tool_output = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": f"Tool arguments error: {error}",
                }

            turn_items.append(tool_output)
            if checkpoint_store is not None:
                if execution_started:
                    checkpoint_store.after_tool(
                        session=session, tool_output=tool_output,
                    )
                else:
                    checkpoint_store.skip_tool(
                        session=session, tool_output=tool_output,
                    )

        # -------------------------
        # 6. 保存完整的这一轮
        # -------------------------
        # history_turns.append(
        #     turn_items
        # )
        if checkpoint_store is not None:
            checkpoint_store.finish_turn(session=session)
        else:
            history_turns.append(turn_items)

        # 一批工具全部执行、Tool Response 都保存以后，再统一检查
        # 由实际状态和有效证据判断进展，set_plan/replan 本身不自动算进展
        decision = check_lifecycle(
            state=state,
            model_turns=metrics.model_turns,
            progress=progress,
            budget=budget
        )
        new_milestones = progress.observe(
            state=state,
            report=decision.report,
            turn=metrics.model_turns
        )
        if new_milestones:
            trace.record(
                turn=metrics.model_turns,
                event_type="meaningful_progress",
                name="milestone",
                detail=", ".join(sorted(new_milestones))[:400]
            )
        decision = check_lifecycle(
            state=state,
            model_turns=metrics.model_turns,
            progress=progress,
            budget=budget
        )
        metrics.completion_checks += 1
        if checkpoint_store is not None:
            checkpoint_store.save(session=session) # persist ProgressTracker milestones
        if decision.action != LifecycleAction.CONTINUE:
            return finish_agent(
                state=state,
                metrics=metrics,
                trace=trace,
                decision=decision
            )




```


## `src/mini_coding_agent/checkpoint.py`

```python
"""Lesson 25: safe, versioned JSON checkpoints for one coding-agent run.

A checkpoint is an execution cursor, NOT a filesystem backup.
An uncertain in-flight tool call must never be replayed automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .agent_metrics import AgentMetrics
from .agent_trace import AgentTrace, TraceEvent
from .evidence import EvidenceType
from .goal import GoalState
from .planning import (
    AgentState, FailureState, PlanSnapshot, PlanState, PlanStatus,
    TaskStatus, TodoItem, TodoKind, VerificationState, ReplanReason,
)
from .requirements import RequirementState, RequirementKind, TaskRequirement
from .runtime_lifecycle import ProgressTracker
from .verification import VerificationKind, VerificationRecord

SCHEMA_VERSION = 1
SKIP_DIRS = {
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'node_modules', '.mini_agent',
    'dist', 'build',
}
SKIP_FILES = {'.DS_Store'}
MAX_FINGERPRINT_FILES = 10000

_TYPES = {t.__name__: t for t in (
    AgentState, GoalState, PlanState, PlanSnapshot, FailureState,
    VerificationState, TodoItem, TaskRequirement, RequirementState,
    VerificationRecord, AgentMetrics, AgentTrace, TraceEvent, ProgressTracker,
)}
_ENUMS = {t.__name__: t for t in (
    EvidenceType, PlanStatus, TaskStatus, TodoKind, ReplanReason,
    RequirementKind, VerificationKind,
)}


class CheckpointError(RuntimeError):
    """Unusable or incompatible checkpoint; do not silently start over."""


class RecoveryRequired(CheckpointError):
    """A tool may have executed without its result being committed."""


@dataclass
class RunSession:
    state: AgentState
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    trace: AgentTrace = field(default_factory=AgentTrace)
    progress: ProgressTracker = field(default_factory=ProgressTracker)
    # 已经完整结束的历史轮次
    history_turns: list[list[dict[str, Any]]] = field(default_factory=list)
    # Assistant message plus tool responses not yet moved to history_turns.
    # 当前正在执行、尚未提交到历史的轮次
    inflight_turn: list[dict[str, Any]] | None = None
    # A tool is marked pending BEFORE it is invoked, then cleared AFTER its
    # ToolResult and AgentState have both been saved.
    # 当前已准备执行、但结果尚未持久化的工具
    pending_tool: dict[str, Any] | None = None
    # Remember exact rejected operations across a safe resume.
    denied_approvals: set[str] = field(default_factory=set)


_TYPES['RunSession'] = RunSession


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {'$enum': type(value).__name__, '$value': value.value}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            '$dataclass': type(value).__name__,
            '$fields': {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, set):
        return {'$set': [_encode(v) for v in sorted(value, key=str)]}
    if isinstance(value, tuple):
        return {'$tuple': [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value):
            return {k: _encode(v) for k, v in value.items()}
        return {'$pairs': [[_encode(k), _encode(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f'Cannot checkpoint {type(value).__name__}')


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {'$enum', '$value'}:
        enum_cls = _ENUMS.get(value['$enum'])
        if enum_cls is None:
            raise CheckpointError(f'Unknown enum: {value["$enum"]}')
        return enum_cls(value['$value'])
    if set(value) == {'$dataclass', '$fields'}:
        cls = _TYPES.get(value['$dataclass'])
        if cls is None:
            raise CheckpointError(f'Unknown state type: {value["$dataclass"]}')
        return cls(**{k: _decode(v) for k, v in value['$fields'].items()})
    if set(value) == {'$set'}:
        return set(_decode(v) for v in value['$set'])
    if set(value) == {'$tuple'}:
        return tuple(_decode(v) for v in value['$tuple'])
    if set(value) == {'$pairs'}:
        return {_decode(k): _decode(v) for k, v in value['$pairs']}
    return {k: _decode(v) for k, v in value.items()}


def workspace_fingerprint(root: Path) -> str:
    """Hash current non-ignored workspace files, excluding checkpoints.

    Large binary files are read in chunks; this is intentionally conservative
    for the learning project, not optimized for giant monorepos.
    """
    root = root.resolve()
    digest = hashlib.sha256()
    count = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and not (Path(directory) / d).is_symlink())
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if filename in SKIP_FILES or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith('.mini_agent/'):
                continue
            count += 1
            if count > MAX_FINGERPRINT_FILES:
                raise CheckpointError('Too many workspace files for fingerprint')
            digest.update(relative.encode('utf-8'))
            digest.update(b'\0')
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


class CheckpointStore:
    def __init__(self, path: Path, workspace: Path):
        self.path = Path(path).resolve()
        self.workspace = Path(workspace).resolve()

    def save(self, session: RunSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            'schema': SCHEMA_VERSION,
            'workspace': str(self.workspace),
            'workspace_fingerprint': workspace_fingerprint(self.workspace),
            'session': _encode(session),
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
        envelope = {'sha256': hashlib.sha256(body).hexdigest(), 'payload': payload}
        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                             indent=2, allow_nan=False).encode('utf-8')
        tmp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            # Sync the renamed directory entry too (works on POSIX).
            if os.name == 'posix':
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if tmp.exists():
                tmp.unlink()

    def load(self) -> RunSession:
        if not self.path.is_file():
            raise CheckpointError(f'Checkpoint not found: {self.path}')
        try:
            envelope = json.loads(self.path.read_text(encoding='utf-8'))
            payload = envelope['payload']
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False).encode('utf-8')
            if hashlib.sha256(body).hexdigest() != envelope['sha256']:
                raise CheckpointError('Checkpoint checksum mismatch')
            if payload['schema'] != SCHEMA_VERSION:
                raise CheckpointError('Unsupported checkpoint schema')
            if payload['workspace'] != str(self.workspace):
                raise CheckpointError('Checkpoint belongs to another workspace')
            session = _decode(payload['session'])
            if not isinstance(session, RunSession):
                raise CheckpointError('Checkpoint has invalid session data')
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CheckpointError(f'Invalid checkpoint: {exc}') from exc
        if session.pending_tool is not None:
            tool = session.pending_tool
            raise RecoveryRequired(
                f'UNCERTAIN tool {tool.get("name")} '
                f'call_id={tool.get("call_id")}; do not auto-replay. '
                'Inspect workspace and reconcile manually.'
            )
        if session.inflight_turn is not None:
            raise RecoveryRequired(
                'Partial model tool-call turn exists. '
                'Do not auto-replay missing tool calls.'
            )
        current = workspace_fingerprint(self.workspace)
        if current != payload['workspace_fingerprint']:
            raise RecoveryRequired(
                'Workspace changed since checkpoint. '
                'Inspect changes before restoring state or evidence.'
            )
        return session

    def begin_turn(self, session: RunSession, assistant: dict[str, Any]) -> None:
        if session.inflight_turn is not None:
            raise CheckpointError('Cannot begin turn with unfinished tool messages')
        session.inflight_turn = [assistant]
        self.save(session)

    def before_tool(self, session: RunSession, call_id: str,
                    name: str, arguments: dict[str, Any]) -> None:
        if session.inflight_turn is None or session.pending_tool is not None:
            raise CheckpointError('Invalid pending tool transition')
        # Do not store arguments twice; assistant message already contains them.
        session.pending_tool = {'call_id': call_id, 'name': name}
        self.save(session)

    def after_tool(self, session: RunSession, tool_output: dict[str, Any]) -> None:
        if session.pending_tool is None or session.inflight_turn is None:
            raise CheckpointError('No pending tool to commit')
        if tool_output.get('tool_call_id') != session.pending_tool['call_id']:
            raise CheckpointError('Tool response ID does not match pending call')
        session.inflight_turn.append(tool_output)
        session.pending_tool = None
        self.save(session)

    def skip_tool(self, session: RunSession, tool_output: dict[str, Any]) -> None:
        """Record a guard/policy/approval rejection without an execution marker.

        No tool was invoked; `pending_tool` must remain None.
        """
        if session.inflight_turn is None or session.pending_tool is not None:
            raise CheckpointError('Cannot skip a tool while execution is pending')
        session.inflight_turn.append(tool_output)
        self.save(session)

    def finish_turn(self, session: RunSession) -> None:
        if session.pending_tool is not None or session.inflight_turn is None:
            raise CheckpointError('Cannot commit incomplete turn')
        session.history_turns.append(session.inflight_turn)
        session.inflight_turn = None
        self.save(session)

```


## `src/mini_coding_agent/permission.py`

```python
"""工具权限功能，不能让ai 全权限的执行命令"""

from enum import Enum
import shlex

# 权限
class Permission(Enum):
    SAFE = "safe"
    WRITE = "write"
    CONFIRM = "confirm"
    BLOCKED = "blocked"

# 工具权限
TOOL_PERMISSIONS = {
    'list_files': Permission.SAFE,
    'read_file': Permission.SAFE,
    "write_file": Permission.WRITE,
    "replace_text": Permission.WRITE,
    "run_command": Permission.SAFE,
    "set_plan": Permission.SAFE,
    "get_plan": Permission.SAFE,
    "update_task": Permission.SAFE,
    "search_text": Permission.SAFE,
    "set_requirements": Permission.SAFE,
    "set_goal": Permission.SAFE,
    "replan": Permission.SAFE,
    "ask_user": Permission.SAFE,
    "delete_file": Permission.CONFIRM,
}

# 被禁止的shell语法
SHELL_OPERATORS = [
    "&&",
    "||",
    ";",
    "|",
    ">",
    "<",
    "`",
    "$(",
]


def contains_shell_operator(command: str) -> bool:
    """
    判断命令是否包含被禁止的shell命令
    """
    return any(
        operator in command
        for operator in SHELL_OPERATORS
    )

def classify_command(command: str) -> Permission:
    """
    权限判断
    """
    if contains_shell_operator(command=command):
        return Permission.BLOCKED

    try:
        args = shlex.split(command)
    except ValueError:
        return Permission.BLOCKED

    if not args:
        return Permission.BLOCKED

    program = args[0]
    if program == "git":
        return classify_git_command(args=args)

    if program == "uv":
        return classify_uv_command(args=args)

    if program == "pwd":
        return Permission.SAFE

    if program == "ls":
        return Permission.SAFE

    if program == "pytest":
        return Permission.SAFE

    return Permission.BLOCKED

def classify_git_command(
        args: list[str]
) -> Permission:
    if len(args) < 2:
        return Permission.BLOCKED

    subcommand = args[1]

    safe_commands = {
        "status",
        "diff",
        "log",
        "show",
        "branch",
    }

    confirm_commands = {
        "add",
        "commit",
        "checkout",
        "switch",
        "restore",
        "reset",
        "merge",
        "rebase",
        "push",
        "pull",
    }

    if subcommand in safe_commands:
        return Permission.SAFE

    if subcommand in confirm_commands:
        return Permission.CONFIRM

    return Permission.BLOCKED


def classify_uv_command(
        args: list[str]
) -> Permission:
    if len(args) < 2:
        return Permission.BLOCKED

    subcommand = args[1]

    if subcommand == "add":
        return Permission.CONFIRM

    if subcommand == "remove":
        return Permission.CONFIRM

    if subcommand == "sync":
        return Permission.CONFIRM

    if subcommand == "run":
        if len(args) < 3:
            return Permission.BLOCKED

        target = args[2]

        if target in {
            "pytest",
        }:
            return Permission.SAFE

        return Permission.CONFIRM

    return Permission.BLOCKED

def ask_for_confirmation(
        command: str,
        detail: str | None = None,
) -> bool:
    print("\n⚠️ Agent wants to run:")
    print(command)

    if detail:
        print(detail)

    try:
        answer = input(
            "\nDo you want to allow this operation? [y/N]: "
        )
    except (EOFError, KeyboardInterrupt):
        return False

    answer = answer.strip().lower()

    return answer in {"y", "yes"}
```


## `src/mini_coding_agent/tool_result.py`

```python
from dataclasses import dataclass


@dataclass
class ToolResult:
    success: bool

    content: str = ""

    error: str = ""

    return_code: int | None = None

    # Machine-readable refusal distinct from tool execution failure.
    failure_code: str | None = None

    # 新增
    # # 状态已经改变
    # state_changed: bool = False

    # # 已经验证成功
    # verification_succeeded: bool = False

    @classmethod
    def ok(
        cls,
        content: str = "",
        return_code: int | None = None,
        # state_changed: bool = False,
        # verification_succeeded: bool = False
    ) -> "ToolResult":

        return cls(
            success=True,
            content=content,
            return_code=return_code,
            # state_changed=state_changed,
            # verification_succeeded=verification_succeeded
        )

    @classmethod
    def fail(
        cls,
        error: str,
        content: str = "",
        return_code: int | None = None,
        # state_changed: bool = False,
        # verification_succeeded: bool = False
        failure_code: str | None = None,
    ) -> "ToolResult":

        return cls(
            success=False,
            content=content,
            error=error,
            return_code=return_code,
            failure_code=failure_code,
            # state_changed=state_changed,
            # verification_succeeded=verification_succeeded
        )
```


## `src/mini_coding_agent/tool_runner.py`

```python
"""Tool call signature helper and guarded tool dispatcher."""

import json
import subprocess
from dataclasses import asdict

from .permission import (
    Permission,
    TOOL_PERMISSIONS,
)
from .approval import authorize_tool_call, policy_for_tool
from .planning import (
    AgentState,
    set_plan,
    get_plan,
    update_task,
    # record_progress,
    ReplanReason,
    replan,
)
from .evidence import (
    CommandResult,
    format_command_result,
    record_command_evidence,
    mark_workspace_changed,
)
from .tools import (
    list_files,
    read_file,
    search_text,
    ask_user,
    ask_user_choice,
    write_file,
    replace_text,
    run_command,
    delete_file,
)

from .agent_metrics import (
    AgentMetrics,
    is_test_command
)

from .agent_trace import AgentTrace
from .goal import (
    set_goal
)

from .failure_recovery import (
    RecoveryAction,
    clear_failure_streak,
    handle_tool_failure,
    build_failure_result
)

from .verification import record_command_verification

from .tool_result import ToolResult
from .verification import is_code_file
from .requirements import set_requirements

WRITE_TOOLS = {
    "write_file",
    "replace_text"
}


def record_workspace_change(
        state: AgentState,
        path: str,
) -> None:
    '''
    记录workspace发生变化，如果是code_file,记录codex_revison 

        
    1.先记录，在write_file和replace_text下，先记录发生变化code_revision，
    在run_command中执行完验证后，再记录当时的code_revision
    2.在has
    '''
    state.workspace_revision += 1
    state.changed_files.add(path)

    if is_code_file(path):
        state.code_revision += 1

def format_confirmation_detail(
        name: str,
        arguments: dict,
) -> str:
    '''
    构造 y/N 确认时展示给用户的详细信息。

    delete_file 必须显示目标文件路径，否则用户无法判断是否放行。
    '''
    if name == "delete_file":
        return (
            "Target file: "
            f"{arguments.get('path', '<unknown>')}"
        )

    return (
        "Arguments: "
        + json.dumps(arguments, ensure_ascii=False)
    )


def execute_tool(
        name: str,
        arguments: dict,
        state: AgentState,
        metrics: AgentMetrics,
        trace: AgentTrace,
        *,
        preauthorized: bool = False,
) -> ToolResult:
    '''
    真正的调用工具
    '''
    try:
        # 未知工具直接返回，避免被误判为被禁止的工具
        if name not in TOOL_PERMISSIONS:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {name}"
            )

        # Direct callers must still pass the same policy and approval gate.
        # The Agent Loop obtains approval BEFORE Checkpoint.before_tool,
        # then uses preauthorized=True to prevent a second prompt.
        if preauthorized:
            if policy_for_tool(name, arguments) == Permission.BLOCKED:
                return ToolResult.fail(
                    error=f"POLICY_BLOCKED: {name}",
                    failure_code="POLICY_BLOCKED",
                )
        else:
            authorization = authorize_tool_call(name, arguments)
            if not authorization.allowed:
                code = (
                    "USER_DENIED" if authorization.status.value == "denied"
                    else "POLICY_BLOCKED"
                )
                return ToolResult.fail(
                    error=authorization.reason,
                    failure_code=code,
                )

        if name == "set_goal":
            return set_goal(
                state=state,
                **arguments
            )

        if name == "list_files":
            return list_files(**arguments)
             
        if name == "read_file":
            return read_file(**arguments)

        if name == "write_file":
            if (
                name in WRITE_TOOLS
                and metrics.first_write_turn is None
            ):
                metrics.first_write_turn = metrics.model_turns

            result = write_file(**arguments)
            if result.changed:
                # mark_workspace_changed(state)
                record_workspace_change(
                    state=state,
                    path=arguments["path"]
                )

                # record_progress(
                #     state=state,
                #     turn=metrics.model_turns
                # )

                return ToolResult(
                    success=True,
                    content=json.dumps(asdict(result)),
                    error=result.message,
                )
            else:
                return ToolResult(
                    success=False,
                    content=result.message,
                    error=result.message
                )

        if name == "replace_text":
            if (
                name in WRITE_TOOLS
                and metrics.first_write_turn is None
            ):
                metrics.first_write_turn = metrics.model_turns
                
            result = replace_text(**arguments)
            if result.changed:
                # mark_workspace_changed(state)
                record_workspace_change(
                    state=state,
                    path=arguments["path"]
                )
                # record_progress(
                #     state=state,
                #     turn=metrics.model_turns
                # )
                return ToolResult(
                    success=True,
                    content=json.dumps(asdict(result)),
                    error=result.message,
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.message 
                )

        if name == "run_command":
            
            if (
                is_test_command(
                    arguments.get("command", "")
                )
            ):
                metrics.tests_run += 1

            # 执行命令
            result = run_command(
                state=state,
                metrics=metrics,
                approved=True,
                **arguments
            )

            if isinstance(
                result,
                CommandResult
            ):
                record_command_verification(
                    state=state,
                    command=arguments["command"],
                    result=result
                )
                # 先执行write_file或者replace_text,会更新workspace_revision += 1
                # 命令执行完后，
                # 将执行命令的结果记录下来
                # 更新state的evidence，让 state.evidence[EvidenceType.TESTS_PASSED] = state.workspace_revision
                record_command_evidence(
                    state,
                    result
                )

                output = format_command_result(result=result)
                if result.returncode == 0:
                    tool_result = ToolResult.ok(
                        content=output,
                        return_code=result.returncode,
                    )
                    return tool_result
                return ToolResult.fail(
                    error=output,
                    return_code=result.returncode,
                )

            # Timeout/error strings do not establish successful verification.
            return ToolResult(
                success=False,
                error=result,
            )

        if name == "set_plan":

            result = set_plan(
                state=state,
                **arguments,
            )

            if result.success:
                # record_progress(
                #     state=state,
                #     turn=metrics.model_turns
                # )
                if metrics.plan_created_turn is None:
                    metrics.plan_created_turn = (
                        metrics.model_turns
                    )

                metrics.plan_revision = 1

            return result

        if name == "update_task":
            return update_task(
                state=state,
                metrics=metrics,
                **arguments,
            )

        if name == "get_plan":
            return get_plan(state)

        if name == "search_text":
            return search_text(
                **arguments
            )

        if name == "ask_user":
            return ask_user(
                **arguments
            )

        if name == "set_requirements":
            def resolve_target_with_user(
                    target: str,
                    matches: list[str],
            ) -> str | None:
                selected = ask_user_choice(
                    question=(
                        f"Multiple files match the requirement "
                        f"target '{target}'. Which file should "
                        "be changed?"
                    ),
                    options=matches,
                )

                trace.record(
                    turn=metrics.model_turns,
                    event_type="requirement_target_choice",
                    name="set_requirements",
                    detail=(
                        f"target={target}; "
                        f"selected={selected}"
                    ),
                )

                return selected

            result = set_requirements(
                state=state,
                **arguments,
                resolver=resolve_target_with_user,
            )

            if metrics.requirements_created_turn is None:
                metrics.requirements_created_turn = metrics.model_turns
                trace.record(
                    turn=metrics.model_turns,
                    event_type="requirements",
                    name="set_requirements",
                    detail=(
                        f"count="
                        f"{len(state.requirements.items)}"
                    )
                )

                # record_progress(
                #     state=state,
                #     turn=metrics.model_turns
                # )

            return result

        if name == "replan":
            try:
                reason = ReplanReason(
                    arguments["reason"]
                )
            except ValueError:
                return ToolResult.fail(
                    error=(
                        "Invalid replan reason"
                    )
                )

            result = replan(
                state=state,
                items=arguments["items"],
                reason=reason,
                explanation=arguments["explanation"],
                current_turn=metrics.model_turns
            )
            if result.success:
                metrics.replans += 1
                metrics.plan_revision = state.plan.revision
                reason_key = reason.value
                metrics.replans_by_reason[
                    reason_key
                ] = (
                    metrics.replans_by_reason.get(
                        reason_key,0
                    ) + 1
                )

                trace.record(
                    turn=metrics.model_turns,
                    event_type="replan",
                    name=(
                        f"plan_v"
                        f"{state.plan.revision}"
                    ),
                    detail=(
                        f"reason={reason.value};"
                        f"{arguments['explanation']}"
                    )
                )

                # record_progress(
                #     state=state,
                #     turn=metrics.model_turns
                # )

            return  result

        if name == "delete_file":
            result = delete_file(**arguments)
            if not result.success:
                return ToolResult(
                    success=False,
                    error=result.message,
                )

            if result.changed:
                # record_progress(
                #     state=state,
                #     turn=metrics.model_turns
                # )
                # mark_workspace_changed(state)
                record_workspace_change(
                    state=state,
                    path=arguments["path"]
                )

            return ToolResult(
                success=True,
                content=result.message,
            )


        return ToolResult(
            success=False,
            error=f"Unknown tool: {name}"
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            success=False,
            error=(
                "Command timed out "
                "after 30 seconds."
            )
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Tool error: {e}"
        )



def execute_tool_with_recovery(
    name: str,
    arguments: dict,
    state,
    metrics,
    trace,
    current_turn: int,
    *,
    preauthorized: bool = False,
) -> ToolResult:

    '''
    Runtime 的故障恢复入口，在 execute_tool 外层包一层恢复策略。

    流程：
    1. 先执行一次工具；成功则清除连续失败计数并直接返回。
    2. 失败时通过 handle_tool_failure 对错误分类、记录签名并得到
       FailureDecision 和连续失败次数 failure_count，同时写入 trace。
    3. 仅当 decision.auto_retry 为 True（瞬时错误 + 只读安全工具 +
       首次失败）时，Runtime 自动重试一次：
       - 重试成功：记 recovered_failures、清除失败 streak、写 recovered
         trace 并返回重试结果；
       - 重试仍失败：再次 handle_tool_failure，failure_count >= 2 时记
         repeated_failures，并用 build_failure_result 返回带恢复指引的
         失败结果。
    4. 不允许自动重试时，failure_count >= 2 时记 repeated_failures，
       直接返回 build_failure_result 构造的失败结果（含 RUNTIME
       RECOVERY 指引），由模型决定下一步。

    注意：最多只会自动重试一次，不会无限重试。
    '''

    # Permission denial is a policy outcome, not a tool execution or a
    # recoverable failure. Authorize once when this API is called directly.
    if not preauthorized:
        authorization = authorize_tool_call(name, arguments)
        if not authorization.allowed:
            return ToolResult.fail(
                error=authorization.reason,
                failure_code=(
                    "USER_DENIED" if authorization.status.value == "denied"
                    else "POLICY_BLOCKED"
                ),
            )
        preauthorized = True

    # =========================
    # 第一次执行
    # =========================

    metrics.tool_executions += 1

    result = execute_tool(
        name=name,
        arguments=arguments,
        state=state,
        metrics=metrics,
        trace=trace,
        preauthorized=preauthorized,
    )

    # =========================
    # 成功
    # =========================

    if result.success:

        clear_failure_streak(
            state
        )

        return result

    # Explicit denial / policy blocking is NOT an execution failure;
    # never put it through the generic retry or failure-streak mechanism.
    if result.failure_code in {"USER_DENIED", "POLICY_BLOCKED"}:
        return result

    # =========================
    # 第一次失败
    # =========================

    metrics.tool_failures += 1

    decision, failure_count = (
        handle_tool_failure(
            tool_name=name,
            arguments=arguments,
            result=result,
            state=state,
        )
    )

    trace.record(
        turn=current_turn,
        event_type="tool_failure",
        name=name,
        detail=(
            f"kind={decision.kind.value}, "
            f"action={decision.action.value}, "
            f"count={failure_count}, "
            f"error={result.error[:200]}"
        ),
    )

    # =========================
    # Runtime 自动 Retry
    # =========================

    if decision.auto_retry:

        metrics.auto_retries += 1

        trace.record(
            turn=current_turn,
            event_type="retry",
            name=name,
            detail=(
                "Runtime automatic retry"
            ),
        )

        metrics.tool_executions += 1

        retry_result = execute_tool(
            name=name,
            arguments=arguments,
            state=state,
            metrics=metrics,
            trace=trace,
            preauthorized=preauthorized,
        )

        # ---------------------
        # retry 成功
        # ---------------------

        if retry_result.success:

            metrics.recovered_failures += 1

            clear_failure_streak(
                state
            )

            trace.record(
                turn=current_turn,
                event_type="recovered",
                name=name,
                detail=(
                    "Automatic retry succeeded"
                ),
            )

            return retry_result

        # ---------------------
        # retry 仍然失败
        # ---------------------

        metrics.tool_failures += 1

        second_decision, second_count = (
            handle_tool_failure(
                tool_name=name,
                arguments=arguments,
                result=retry_result,
                state=state,
            )
        )

        if second_count >= 2:

            metrics.repeated_failures += 1

        return build_failure_result(
            original_result=retry_result,
            decision=second_decision,
            failure_count=second_count,
        )

    # =========================
    # 不能 Retry
    # =========================

    if failure_count >= 2:

        metrics.repeated_failures += 1

    return build_failure_result(
        original_result=result,
        decision=decision,
        failure_count=failure_count,
    )

```


## `src/mini_coding_agent/tools.py`

```python
"""Tool implementations: filesystem reads/writes and shell execution."""

import shlex
import subprocess
from pathlib import Path
from .tool_result import ToolResult

# 说明：is_code_file（定义于 verification.py，用于按扩展名判断文件是否为代码文件）
# 在本模块的文件写入类工具（write_file / replace_text）的调用链中被使用：
# tool_runner 会先通过 is_code_file(path) 判断目标路径是否为代码文件，
# 再决定是否触发相应的代码验证流程。此处仅添加注释说明该关联，
# 本模块自身不直接定义或导入 is_code_file。

from .config import (
    WORKSPACE,
    DEFAULT_READ_LINES,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
)
from .context import truncate_text
from .evidence import (
    CommandResult,
    FileOperationResult,
)
from .permission import (
    Permission,
    ask_for_confirmation,
)
from .approval import policy_for_tool

from .agent_metrics import (
    AgentMetrics,
    is_test_command
)

from .planning import (
    AgentState,
    # record_progress
)


PROTECTED_COMPONENTS = {".env", ".git", ".venv", ".mini_agent"}


def resolve_path(path: str) -> Path:
    """Keep file tools inside the workspace and away from private internals.

    This does not sandbox `run_command`, which requires a separate OS-level
    security boundary if project content is untrusted.
    """
    root = WORKSPACE.resolve()
    target = (root / path).resolve()

    if target != root and root not in target.parents:
        raise ValueError("Path is outside the workspace")

    relative = target.relative_to(root)
    if any(part in PROTECTED_COMPONENTS for part in relative.parts):
        raise ValueError("Protected workspace path is not accessible")

    return target


def list_files(path: str) -> ToolResult:
    target = resolve_path(path)

    if not target.exists():
        return ToolResult(
            success=False,
            error=f"Direcotory does not exist: {path}"
        )

    if not target.is_dir():
        return ToolResult(
            success=False,
            error=f"Not a directory: {path}"
        )

    result = []
    for child in sorted(target.iterdir()):
        relative = child.relative_to(WORKSPACE)

        if child.is_dir():
            result.append(f"{relative}/")
        else:
            result.append(str(relative))

    value = "\n".join(result) or "empty directory"
    result_str = truncate_text(value)
    return ToolResult(
        success=True,
        content=result_str
    )


def read_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> ToolResult:
    target = resolve_path(path)

    if not target.exists():
        return ToolResult(
            success=False,
            error=f"File does not exist: {path}"
        )

    if not target.is_file():
        return ToolResult(
            success=False,
            error=f"Not a file: {path}"
        )

    try:
        content = target.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError:
        return ToolResult(
            success=False,
            error=(
                f"Cannot read binary or "
                f"non-UTF-8 file: {path}"
            )
        )

    lines = content.splitlines()

    total_lines = len(lines)

    if total_lines == 0:
        return ToolResult(
            success=False,
            error=(
                f"File: {path}\n"
                f"(empty file)"
            )   
        )

    if start_line < 1:
        start_line = 1

    if start_line > total_lines:
        return ToolResult(
            success=False,
            error=(
                f"Start line {start_line} "
                f"is beyond the end of file.\n"
                f"Total lines: {total_lines}"
            )
        )

    if end_line is None:
        end_line = (
            start_line
            + DEFAULT_READ_LINES
            - 1
        )

    end_line = min(
        end_line,
        total_lines,
    )

    requested_lines = (
        end_line
        - start_line
        + 1
    )

    if requested_lines > MAX_READ_LINES:
        end_line = (
            start_line
            + MAX_READ_LINES
            - 1
        )

        end_line = min(
            end_line,
            total_lines,
        )

    selected_lines = lines[
        start_line - 1:end_line
    ]

    numbered_lines = []

    for line_number, line in enumerate(
        selected_lines,
        start=start_line,
    ):
        numbered_lines.append(
            f"{line_number:4}: {line}"
        )

    result = [
        f"File: {path}",
        (
            f"Lines {start_line}-{end_line} "
            f"of {total_lines}"
        ),
        "",
        "\n".join(numbered_lines),
    ]

    if end_line < total_lines:
        result.append("")
        result.append(
            (
                "More lines are available. "
                f"Continue from line "
                f"{end_line + 1} if needed."
            )
        )
    value = "\n".join(result) or "empty file"
    result_str = truncate_text(value)
    return ToolResult(
        success=True,
        content=result_str
    )


def search_text(
    query: str,
    path: str = ".",
) -> ToolResult:
    target = resolve_path(path)

    if not target.exists():
        return ToolResult(
            success=False,
            error=(
                f"Path does not exist: {path}"
            )
        )

    files = []

    if target.is_file():
        files.append(target)

    else:
        for file in target.rglob("*"):
            if file.is_file():
                files.append(file)

    results = []

    for file in files:
        try:
            content = file.read_text(
                encoding="utf-8"
            )
        except (
            UnicodeDecodeError,
            PermissionError,
        ):
            continue

        for line_number, line in enumerate(
            content.splitlines(),
            start=1,
        ):
            if query.lower() in line.lower():
                relative = file.relative_to(
                    WORKSPACE
                )

                results.append(
                    (
                        f"{relative}:"
                        f"{line_number}: "
                        f"{line.strip()}"
                    )
                )

                if (
                    len(results)
                    >= MAX_SEARCH_RESULTS
                ):
                    return ToolResult(
                        success=True,
                        content=(
                            "\n".join(results)
                            + "\n\n"
                            + "[Search results truncated]"
                        )
                    )

    if not results:
        return ToolResult(
            success=False,
                error=(
                f"No matches found for: "
                f"{query}"
            )
        )

    return ToolResult(
        success=True,
        content="\n".join(results)
    )
    


MAX_ASK_USER_ATTEMPTS = 3


def clean_ask_user_options(
        options: list[str],
) -> list[str]:
    """Normalize option strings and drop empty entries."""
    return [
        str(option).strip()
        for option in options
        if str(option).strip()
    ]


def prompt_user_choice(
        question: str,
        options: list[str],
) -> tuple[str | None, str]:
    """Render a numbered question and read one answer.

    Returns ``(selected_option, status)`` where status is one of:

    - ``"selected"``: ``selected_option`` is the chosen option text;
    - ``"zero"``: the user answered 0 / pressed Enter;
    - ``"closed"``: input was closed (EOF / Ctrl-C);
    - ``"exhausted"``: too many invalid answers.
    """
    print("\n🤔 Agent asks:")
    print(question)
    print()

    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")

    print("  0. None of the above / no answer")

    for _ in range(MAX_ASK_USER_ATTEMPTS):
        try:
            answer = input(
                f"\nYour answer [0-{len(options)}]: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return None, "closed"

        # 直接回车视为“没有答案”
        if answer == "":
            answer = "0"

        if answer == "0":
            return None, "zero"

        if answer.isdigit():
            choice = int(answer)

            if 1 <= choice <= len(options):
                return options[choice - 1], "selected"

        print(
            "Invalid answer. Enter a number between "
            f"0 and {len(options)}."
        )

    return None, "exhausted"


def ask_user_choice(
        question: str,
        options: list[str],
) -> str | None:
    """Interactively pick one option and return its text.

    Returns ``None`` when the user answers 0, input is closed, or no
    valid answer is given. This helper is also used by internal
    runtime flows that need to disambiguate a choice without going
    through a model tool call.
    """
    cleaned_options = clean_ask_user_options(options)

    if not cleaned_options:
        return None

    selected, _ = prompt_user_choice(
        question=question,
        options=cleaned_options,
    )

    return selected


def ask_user(
        question: str,
        options: list[str],
) -> ToolResult:
    """Ask the user to choose one of the given options.

    Options are presented with 1-based numbers. The user may answer 0
    to indicate that none of the options apply or that they have no
    answer. The response is returned to the model as tool content.
    """
    cleaned_options = clean_ask_user_options(options)

    if not cleaned_options:
        return ToolResult(
            success=False,
            error=(
                "ask_user requires at least one "
                "non-empty option."
            )
        )

    selected, status = prompt_user_choice(
        question=question,
        options=cleaned_options,
    )

    if status == "selected":
        choice = cleaned_options.index(selected) + 1

        return ToolResult.ok(
            content=(
                f"User selected option {choice}: "
                f"{selected}"
            )
        )

    if status == "closed":
        return ToolResult.ok(
            content=(
                "User did not answer "
                "(input was closed). "
                "Treat this as answer 0 and proceed "
                "using your best judgment."
            )
        )

    if status == "exhausted":
        return ToolResult.ok(
            content=(
                "User did not give a valid answer after "
                f"{MAX_ASK_USER_ATTEMPTS} attempts. "
                "Treat this as answer 0 (no option selected). "
                "Proceed using your best judgment or mark "
                "the work as blocked with a reason."
            )
        )

    return ToolResult.ok(
        content=(
            "User selected 0: none of the options apply. "
            "The user has no answer or asked you to "
            "proceed without choosing an option."
        )
    )


def write_file(path: str, content: str) -> FileOperationResult:
    target = resolve_path(path)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if target.exists():
        old_content = target.read_text(
            encoding="utf-8"
        )

        if old_content == content:
            return FileOperationResult(
                success=True,
                changed=False,
                message=(
                    f"No changes needed: {path}"
                )
            )

    target.write_text(
        content,
        encoding="utf-8"
    )

    # return f"Successfully wrote file: {path}"
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully wrote file: {path}"
        )
    )

def replace_text(
        path: str,
        old_text: str,
        new_text: str,
) -> FileOperationResult:
    target = resolve_path(path)

    if not target.exists():
        # return f"File does not exist: {path}"
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"File does not exist: {path}"
            )
        )

    content = target.read_text(
        encoding="utf-8"
    )

    if old_text not in content:
        # return "Old text was not found in the file"
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                "Old text was not found in the file"
            )
        )

    count = content.count(old_text)

    if count > 1:
        # return (
        #     f"Old text appears {count} times. "
        #     "Please provide more surrounding context "
        #     "so the replacement is unambiguous."
        # )
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Old text appears {count} times. "
                "Please provide more surrounding context "
                "so the replacement is unambiguous."
            )
        )
    
    updated = content.replace(
        old_text,
        new_text,
        1
    )

    if updated == content:
        return FileOperationResult(
            success=True,
            changed=False,
            message=(
                f"No changes needed: {path}"
            )
        )

    target.write_text(
        updated,
        encoding="utf-8"
    )

    # return f"Successfully updated file: {path}"
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully updated file: {path}"
        )
    )

def run_command(
        state: AgentState,
        metrics: AgentMetrics,
        command: str,
    *,
    approved: bool = False,
) -> CommandResult | str:

    permission = policy_for_tool(
        "run_command", {"command": command}
    )

    if permission == Permission.BLOCKED:
        return f"Blocked dangerous command: {command}"

    if permission == Permission.CONFIRM and not approved:
        if not ask_for_confirmation(command=command):
            return "USER_DENIED: user did not approve the command"

    try:
        args = shlex.split(command)
        result = subprocess.run(
            args=args,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30
        )


        # if (
        #     is_test_command(
        #         command
        #     ) and result.returncode == 0
        # ):
        #     record_progress(
        #         state=state,
        #         turn=metrics.model_turns
            # )

        # output = ""
        # if result.stdout:
        #     output += f"STDOUT:\n{result.stdout}\n"

        # if result.stderr:
        #     output += f"STDERR:\n{result.stderr}\n"

        # output += f"EXIT CODE: {result.returncode}"
        return CommandResult(
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode
        )

    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds"

    except Exception as e:
        return f"Command error: {e}"

def delete_file(path: str) -> FileOperationResult:
    target = resolve_path(path)

    if not target.exists():
        return FileOperationResult(
            success=True,
            changed=False,
            message=f"Already absent: {path}",
        )

    if not target.is_file() or target.is_symlink():
        return FileOperationResult(
            success=False,
            changed=False,
            message="Target must be a regular file",
        )

    target.unlink()

    return FileOperationResult(
        success=True,
        changed=True,
        message=f"Deleted file: {path}",
    )
```


## `tests/test_approval_gate.py`

```python
"""Lesson 26: approval != ask_user; denial != execution error."""
from types import SimpleNamespace
import builtins
import pytest

from mini_coding_agent.approval import (
    ApprovalStatus, authorize_tool_call, policy_for_tool,
)
from mini_coding_agent.permission import Permission, ask_for_confirmation
from mini_coding_agent.checkpoint import CheckpointStore, RunSession
from mini_coding_agent.planning import create_agent_state
from mini_coding_agent.agent_metrics import AgentMetrics
from mini_coding_agent.agent_trace import AgentTrace
from mini_coding_agent.tool_runner import execute_tool_with_recovery
from mini_coding_agent import tools as tools_module


@pytest.mark.parametrize('name,args,expected', [
    ('read_file', {'path': 'note.txt'}, Permission.SAFE),
    ('write_file', {'path': 'note.txt', 'content': 'hello'}, Permission.WRITE),
    ('delete_file', {'path': 'note.txt'}, Permission.CONFIRM),
    ('run_command', {'command': 'git diff'}, Permission.SAFE),
    ('run_command', {'command': 'git diff --output=report.txt'}, Permission.CONFIRM),
    ('run_command', {'command': 'pytest -q'}, Permission.CONFIRM),
    ('run_command', {'command': 'uv run pytest'}, Permission.CONFIRM),
    ('run_command', {'command': 'rm -rf /'}, Permission.BLOCKED),
    ('unknown_tool', {}, Permission.BLOCKED),
])
def test_policy_classification(name, args, expected):
    assert policy_for_tool(name, args) == expected


def test_explicit_approval_allows_exact_operation():
    requested = []
    def approve(command, detail):
        requested.append((command, detail))
        return True

    result = authorize_tool_call(
        'delete_file', {'path': 'note.txt'}, confirm=approve,
    )
    assert result.status == ApprovalStatus.APPROVED
    assert result.allowed
    assert requested == [('delete_file', 'Target file: note.txt')]


def test_denial_is_cached_within_one_run_not_reprompted():
    denied = set()
    asked = []
    def refuse(name, detail):
        asked.append((name, detail))
        return False

    first = authorize_tool_call(
        'delete_file', {'path': 'keep.txt'}, denied_signatures=denied, confirm=refuse,
    )
    second = authorize_tool_call(
        'delete_file', {'path': 'keep.txt'}, denied_signatures=denied, confirm=refuse,
    )
    assert first.status == second.status == ApprovalStatus.DENIED
    assert len(asked) == 1
    assert first.signature in denied


def test_new_args_require_new_approval():
    denied = set()
    called = []
    def refuse(name, detail):
        called.append(detail)
        return False
    authorize_tool_call('delete_file', {'path': 'a.txt'}, denied_signatures=denied, confirm=refuse)
    authorize_tool_call('delete_file', {'path': 'b.txt'}, denied_signatures=denied, confirm=refuse)
    assert len(called) == 2


def test_noninteractive_confirmation_defaults_to_deny(monkeypatch):
    def closed(_prompt=''):
        raise EOFError()
    monkeypatch.setattr(builtins, 'input', closed)
    assert ask_for_confirmation('delete_file') is False
    assert authorize_tool_call('delete_file', {'path': 'a.txt'}).status == ApprovalStatus.DENIED


def test_user_denial_skips_generic_failure_recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_module, 'WORKSPACE', tmp_path)
    f = tmp_path / 'keep.txt'
    f.write_text('hello')
    monkeypatch.setattr(builtins, 'input', lambda _='': 'n')
    metrics = AgentMetrics()
    state = create_agent_state('test')
    result = execute_tool_with_recovery(
        'delete_file', {'path': 'keep.txt'},
        state, metrics, AgentTrace(), current_turn=1,
    )
    assert not result.success
    assert result.failure_code == 'USER_DENIED'
    assert metrics.auto_retries == 0
    assert metrics.tool_failures == 0
    assert metrics.tool_executions == 0  # no side-effectful tool dispatched
    assert f.exists()


def test_skipped_tool_checkpoint_is_safe_to_load(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = CheckpointStore(tmp_path / 'checkpoint.json', workspace)
    session = RunSession(state=create_agent_state('test'))
    session.denied_approvals.add('a-signature')
    assistant = {'role': 'assistant', 'tool_calls': [
        {'id': 'call1', 'function': {'name': 'delete_file', 'arguments': '{}'}}
    ]}
    store.begin_turn(session, assistant)
    store.skip_tool(session, {
        'role': 'tool', 'tool_call_id': 'call1', 'content': 'USER_DENIED',
    })
    store.finish_turn(session)
    loaded = store.load()
    assert loaded.denied_approvals == {'a-signature'}
    assert loaded.pending_tool is None
    assert loaded.inflight_turn is None
    assert loaded.history_turns[0][1]['content'] == 'USER_DENIED'


def test_checkpoint_before_tool_keeps_uncertain_marker(tmp_path):
    from mini_coding_agent.checkpoint import RecoveryRequired
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = CheckpointStore(tmp_path / 'checkpoint.json', workspace)
    session = RunSession(state=create_agent_state('test'))
    store.begin_turn(session, {'role': 'assistant', 'tool_calls': []})
    store.before_tool(session, 'call1', 'delete_file', {'path': 'keep.txt'})
    with pytest.raises(RecoveryRequired, match='UNCERTAIN tool'):
        store.load()


def test_agent_denial_saved_without_pending_tool_and_not_reprompted(monkeypatch, tmp_path):
    """Exercise the actual Agent Loop, not just the helper function."""
    import json
    from mini_coding_agent import agent
    from mini_coding_agent.planning import TodoItem

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = workspace / 'keep.txt'
    target.write_text('important')
    monkeypatch.setattr(agent, 'WORKSPACE', workspace)
    monkeypatch.setattr(tools_module, 'WORKSPACE', workspace)
    monkeypatch.setattr(agent, 'MAX_MODEL_TURN', 2)
    monkeypatch.setattr(agent, 'compress_history', lambda **kwargs: None)
    monkeypatch.setattr(agent, 'print_fixed_context_breakdown', lambda **kwargs: None)
    monkeypatch.setattr(agent, 'print_context_debug', lambda **kwargs: None)
    monkeypatch.setattr(agent, 'print_state_debug', lambda *args: None)

    original_create = agent.create_agent_state
    def setup_state(*args, **kwargs):
        state = original_create(*args, **kwargs)
        state.goal.locked = True
        state.requirements.locked = True
        state.plan.revision = 1
        state.plan.items = [TodoItem(id=1, content='Inspect deletion', status='pending')]
        # TaskStatus is a str-valued Enum in this project; use enum instance.
        from mini_coding_agent.planning import TaskStatus
        state.plan.items[0].status = TaskStatus.PENDING
        return state
    monkeypatch.setattr(agent, 'create_agent_state', setup_state)

    class Message:
        content = ''
        def __init__(self, idx):
            self.tool_calls = [SimpleNamespace(
                id=f'call_{idx}',
                function=SimpleNamespace(name='delete_file', arguments=json.dumps({'path': 'keep.txt'})),
            )]
        def model_dump(self, **kwargs):
            return {'role': 'assistant', 'tool_calls': [
                {'id': self.tool_calls[0].id, 'type': 'function', 'function': {
                    'name': 'delete_file', 'arguments': self.tool_calls[0].function.arguments,
                }}
            ]}

    turns = iter([Message(1), Message(2)])
    monkeypatch.setattr(agent, 'call_model', lambda *args, **kwargs: next(turns))
    called = []
    def deny(prompt=''):
        called.append(prompt)
        return 'n'
    monkeypatch.setattr(builtins, 'input', deny)
    checkpoint_path = tmp_path / 'checkpoint.json'
    output = agent.run_agent('test task', checkpoint_path=str(checkpoint_path))
    assert 'Outcome: stopped' in output
    assert len(called) == 1
    assert target.read_text() == 'important'
    loaded = CheckpointStore(checkpoint_path, workspace).load()
    assert len(loaded.history_turns) == 2
    assert loaded.pending_tool is None
    assert len(loaded.denied_approvals) == 1


@pytest.mark.parametrize('path', ['.env', '.git/config', '.venv/pyvenv.cfg', '.mini_agent/state.json'])
def test_sensitive_workspace_paths_cannot_be_read_or_modified(monkeypatch, tmp_path, path):
    from mini_coding_agent.tools import resolve_path
    monkeypatch.setattr(tools_module, 'WORKSPACE', tmp_path)
    with pytest.raises(ValueError, match='Protected workspace path'):
        resolve_path(path)

```
