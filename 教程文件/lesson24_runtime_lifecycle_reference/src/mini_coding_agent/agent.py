"""Agent loop: drives the model, tools, planning and completion checks."""

import json
import time
from .config import MAX_MODEL_TURN
from .model_api import ModelCallError, call_model
from .context import (
    build_model_input,
    format_runtime_state,
    compress_history,
    print_context_debug,
    print_fixed_context_breakdown,
)
from .planning import (
    AgentState,
    create_agent_state,
    format_plan,
    print_state_debug,
    build_stagnation_feedback,
    build_runtime_instructions
)
from .task_completion import (
    CompletionStatus,
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
    LifecycleAction, LifecycleDecision, RunOutcome, StopReason,
    RuntimeBudget, ProgressTracker, check_lifecycle,
)


def finish_agent(
    *,
    state: AgentState,
    metrics: AgentMetrics,
    trace: AgentTrace,
    decision: LifecycleDecision,
    preferred_text: str = "",
) -> str:
    """统一的单次终止出口：无需模型继续决定能否停止。"""
    metrics.run_outcome = decision.outcome.value if decision.outcome else None
    metrics.stop_reason = decision.reason.value if decision.reason else None
    trace.record(
        turn=metrics.model_turns,
        event_type="lifecycle_end",
        name=metrics.run_outcome or "unknown",
        detail=metrics.stop_reason or "requirements_satisfied",
    )
    metrics.end_runing()
    trace.print_trace()

    # 已通过 Runtime 检查，才允许返回模型此前的总结文字。
    if decision.outcome == RunOutcome.COMPLETE and preferred_text.strip():
        return preferred_text.strip()

    missing = [f"- {r.name}: {r.reason}" for r in decision.report.missing]
    changed = sorted(state.changed_files)
    lines = [
        f"Outcome: {metrics.run_outcome}",
        f"Reason: {metrics.stop_reason or 'requirements_satisfied'}",
        f"Model turns: {metrics.model_turns}",
        "Changed files: " + (", ".join(changed) if changed else "none"),
        "Unmet conditions:",
        *(missing or ["- none"]),
    ]
    return "\n".join(lines)

def run_agent(task: str) -> str:

    state = create_agent_state(
        user_prompt=task
    )

    # 指标对象
    metrics = AgentMetrics()

    # 追踪
    trace = AgentTrace()


    # 本课单独追踪“真实里程碑”，不依赖旧的 record_progress()。
    budget = RuntimeBudget(
        max_model_turns=min(MAX_MODEL_TURN, 120),
        max_no_progress_turns=30,
        warn_no_progress_turns=12,
    )
    progress = ProgressTracker()
    history_turns: list[list] = []

    progress.observe(
        state=state,
        report=check_lifecycle(state, 0, progress, budget).report,
        turn=0,
    )

    while True:
        # 模型调用之前先判定：已完成就不要继续消耗 Token。
        decision = check_lifecycle(state, metrics.model_turns, progress, budget)
        metrics.completion_checks += 1
        if decision.action != LifecycleAction.CONTINUE:
            return finish_agent(state=state, metrics=metrics, trace=trace, decision=decision)

        # 仅警告一次；达到硬阈值则由上面的 Controller 直接终止。
        if progress.should_warn(metrics.model_turns, budget):
            metrics.stagnation_warnings += 1
            state.stagnation_warnings += 1
            history_turns.append([{
                "role": "user",
                "content": build_stagnation_feedback(state),
            }])

        metrics.model_turns += 1
        turn = metrics.model_turns
        print(f"\n --- Agent step {turn} ---")
        trace.record(turn=turn, event_type="model_turn", name="call_model")

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
            failed_report = check_lifecycle(state, metrics.model_turns, progress, budget).report
            decision = LifecycleDecision(
                action=LifecycleAction.TERMINATE,
                report=failed_report,
                outcome=RunOutcome.STOPPED,
                reason=StopReason.MODEL_ERROR,
            )
            finish_agent(state=state, metrics=metrics, trace=trace, decision=decision)
            return f"Agent stopped: {error}"

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
                debug=True,
            )
        except ModelCallError as error:
            failed_report = check_lifecycle(state, metrics.model_turns, progress, budget).report
            decision = LifecycleDecision(
                action=LifecycleAction.TERMINATE,
                report=failed_report,
                outcome=RunOutcome.STOPPED,
                reason=StopReason.MODEL_ERROR,
            )
            finish_agent(state=state, metrics=metrics, trace=trace, decision=decision)
            return f"Agent stopped: {error}"
        # Preserve reasoning_content alongside tool calls for GLM's next turn.
        # input_items.append(message.model_dump(mode="json", exclude_none=True))
        turn_items = [message.model_dump(mode="json", exclude_none=True)]
        tool_calls = message.tool_calls or []

        if not tool_calls:
            # 模型停止调用工具，并不等于任务完成。
            decision = check_lifecycle(state, metrics.model_turns, progress, budget)
            metrics.completion_checks += 1
            trace.record(
                turn=metrics.model_turns,
                event_type="completion_check",
                name="no_tools",
                detail=f"status={decision.report.complete.value}",
            )
            if decision.action != LifecycleAction.CONTINUE:
                return finish_agent(
                    state=state, metrics=metrics, trace=trace,
                    decision=decision, preferred_text=message.content or "",
                )

            metrics.completion_rejections += 1
            history_turns.append(turn_items + [{
                "role": "user",
                "content": build_completion_feedback(decision.report, state),
            }])
            continue

        # -------------------------
        # 5. 执行 Tool Calls
        # -------------------------
        for call in tool_calls:
            print(f"Tool: {call.function.name}")
            print(f"Arguments: {call.function.arguments}")
            
            try:
                name = call.function.name
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")

                metrics.record_tool_call(name)
                # 记录轨迹
                trace.record(
                    turn= metrics.model_turns,
                    event_type="tool",
                    name=name,
                    detail=str(arguments)[:200]
                )
                
                guard_error = guard_tool_call(
                    name=name,
                    arguments=arguments,
                    state=state,
                    metrics=metrics,
                    trace=trace
                )
            
                if guard_error:
                    metrics.blocked_tool_calls += 1
                    trace.record(
                        turn=metrics.model_turns,
                        event_type="blocked",
                        name=name,
                        detail=guard_error,
                    )

                    result = ToolResult.fail(
                        error=guard_error
                    )

                else:
                    recent_error = guard_recent_tool_call(
                        name=name,
                        arguments=arguments,
                        state=state
                    )

                    if recent_error:
                        metrics.blocked_tool_calls += 1
                        trace.record(
                            turn=metrics.model_turns,
                            event_type="blocked_recent",
                            name=name,
                            detail=recent_error,
                        )
                        result = ToolResult.fail(
                            error=recent_error
                        )

                    else:
                        result = execute_tool_with_recovery(
                            name=call.function.name,
                            arguments=arguments,
                            state=state,
                            metrics=metrics,
                            trace=trace,
                            current_turn=metrics.model_turns,
                        )

                tool_output = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": (
                        result.content
                        if result.success
                        else result.error
                    )
                }
                
            except (json.JSONDecodeError, ValueError) as error:
                result = f"Tool arguments error: {error}"

                print(f'result: \n{result}')

                tool_output = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }

            turn_items.append(
                tool_output
            )
        # -------------------------
        # 6. 保存完整的这一轮
        # -------------------------
        history_turns.append(
            turn_items
        )

        # 一批工具全部执行、Tool Response 都保存以后，再统一检查。
        # 由实际状态和有效证据判断进展，set_plan/replan 本身不自动算进展。
        decision = check_lifecycle(state, metrics.model_turns, progress, budget)
        new_milestones = progress.observe(
            state, decision.report, metrics.model_turns
        )
        if new_milestones:
            trace.record(
                turn=metrics.model_turns,
                event_type="meaningful_progress",
                name="milestone",
                detail=", ".join(sorted(new_milestones))[:400],
            )
        decision = check_lifecycle(state, metrics.model_turns, progress, budget)
        metrics.completion_checks += 1
        if decision.action != LifecycleAction.CONTINUE:
            return finish_agent(
                state=state, metrics=metrics, trace=trace, decision=decision
            )
