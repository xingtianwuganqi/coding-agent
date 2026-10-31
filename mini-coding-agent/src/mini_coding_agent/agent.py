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
        final_text = (
            preferred_text.strip()
            or "Outcome: complete\nAll completion requirements satisfied."
        )
        return final_text

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
    
    last_model_content = ""

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
                preferred_text=last_model_content,
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
                preferred_text=last_model_content,
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
            if message.content and message.content.strip():
                last_model_content = message.content
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
                preferred_text=last_model_content,
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
                    state=state,
                    metrics=metrics, 
                    trace=trace,
                    decision=decision, 
                    preferred_text=message.content,
                    session=session,
                    checkpoint_store=checkpoint_store
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
            print(
                "Arguments: "
                + truncate_text(
                    str(call.function.arguments),
                    1000,
                )
            )

            # Important: only an actually dispatched tool gets a write-ahead
            # marker. A Guard/Policy/User rejection has NO tool side effect.
            # 记录执行是否开始
            execution_started = False

            # Durable write-ahead marker BEFORE any tool side effect.
            # 26课approval改造时注释
            # if checkpoint_store is not None:
            #     checkpoint_store.before_tool(session, call.id, call.function.name, {})
            
            try:
                # 1. 解析参数
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")

                metrics.record_tool_call(name)
                # 记录轨迹
                trace.record(
                    turn= metrics.model_turns,
                    event_type="tool_requested",
                    name=name,
                    detail=str(arguments)[:200]
                )

                # 2. 业务规则守卫
                guard_error = guard_tool_call(
                    name=name,
                    arguments=arguments,
                    state=state,
                    metrics=metrics,
                    trace=trace
                )

                if not guard_error:
                    guard_error = guard_recent_tool_call(
                        name=name,
                        arguments=arguments,
                        state=state
                    )
            
                if guard_error:
                    metrics.blocked_tool_calls += 1
                    trace.record(
                        turn=metrics.model_turns,
                        event_type="guard_rejected",
                        name=name,
                        detail=guard_error,
                    )

                    result = ToolResult.fail(
                        error=guard_error
                    )

                else:
                    # 26课改造注释
                    # recent_error = guard_recent_tool_call(
                    #     name=name,
                    #     arguments=arguments,
                    #     state=state
                    # )

                    # if recent_error:
                    #     metrics.blocked_tool_calls += 1
                    #     trace.record(
                    #         turn=metrics.model_turns,
                    #         event_type="blocked_recent",
                    #         name=name,
                    #         detail=recent_error,
                    #     )
                    #     result = ToolResult.fail(
                    #         error=recent_error
                    #     )

                    # else:
                    #     result = execute_tool_with_recovery(
                    #         name=call.function.name,
                    #         arguments=arguments,
                    #         state=state,
                    #         metrics=metrics,
                    #         trace=trace,
                    #         current_turn=metrics.model_turns,
                    #     )
                    # 3. 工具权限和用户审批
                    authorization = authorize_tool_call(
                        name=name,
                        arguments=arguments,
                        denied_signatures=session.denied_approvals
                    )
                    trace.record(
                        turn=metrics.model_turns,
                        event_type="approval_" + authorization.status.value,
                        name=name,
                        # Never log raw command arguments or file contents here.
                        detail="signature=" + authorization.signature[:12]
                    )
                    if not authorization.allowed:
                        metrics.blocked_tool_calls += 1
                        result = ToolResult.fail(
                            error=authorization.reason,
                            failure_code=(
                                "USER_DENIED"
                                if authorization.status == ApprovalStatus.DENIED
                                else "POLICY_BLOCKED"
                            )
                        )
                    else:
                        # A checkpoint now means the tool may have actually
                        # run. Save this marker as close as possible to execute.
                        # 4. 只有获准执行后才保存待执行标记
                        if checkpoint_store is not None:
                            checkpoint_store.before_tool(
                                session, call.id, name, arguments
                            )
                        execution_started = True
                        # 5. 执行工具
                        result = execute_tool_with_recovery(
                            name=call.function.name,
                            arguments=arguments,
                            state=state,
                            metrics=metrics,
                            trace=trace,
                            current_turn=metrics.model_turns,
                            preauthorized=True,
                        )

                # if result.success:
                #     # 推进了任务
                #     if tool_caused_progress(
                #         name=name,
                #         result=result
                #     ):
                #         record_progress(
                #             state=state,
                #             turn=metrics.model_turns
                #         )
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
                # If the tool was already dispatched, it may have modified
                # the workspace before raising. Keep the pending marker and
                # require recovery; never commit it as a harmless parse error.
                if execution_started:
                    raise
                tool_output = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }

            turn_items.append(
                tool_output
            )

            if checkpoint_store is not None:
                if execution_started:
                    # 6. 保存工具执行结果
                    checkpoint_store.after_tool(
                        session=session, 
                        tool_output=tool_output
                    )
                else:
                    checkpoint_store.skip_tool(
                        session=session,
                        tool_output=tool_output
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
                decision=decision,
                preferred_text=last_model_content,
                session=session,
                checkpoint_store=checkpoint_store
            )



