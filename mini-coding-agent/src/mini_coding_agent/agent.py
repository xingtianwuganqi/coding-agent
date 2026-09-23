"""Agent loop: drives the model, tools, planning and completion checks."""

import json
import time
from .config import MAX_MODEL_TURN
from .model_api import call_model
from .context import (
    build_model_input,
    format_runtime_state,
    compress_history,
    print_context_debug,
    print_fixed_context_breakdown,
)
from .planing import (
    AgentState,
    format_plan,
    print_state_debug,
    record_progress,
    tool_caused_progress,
    is_stagnating,
    build_stagnation_feedback
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

from .requirements import build_requirement_context

from .guard import (
    RETRIEVAL_TOOLS,
    guard_tool_call,
    guard_recent_tool_call,
)

NEED_SLEEP: bool = True

def run_agent(task: str) -> str:

    state = AgentState()

    # 指标对象
    metrics = AgentMetrics()

    # 追踪
    trace = AgentTrace()


    allow_blocked_final = False
    
    # input_items = [
    #     {
    #         "role": "user",
    #         "content": task,
    #     }
    # ]

    history_turns : list[list] = []

    turn = 0

    while True:
        turn += 1
        metrics.model_turns += 1

        trace.record(
            turn=metrics.model_turns,
            event_type="model_turn",
            name="call_model",
        )

        if turn >= MAX_MODEL_TURN:
            metrics.end_runing()
            trace.print_trace()
            return (
                "Agent stopped because the maximum "
                "number of model turns was reached."
            )
        print(f"\n --- Agent step {turn} ---")

        # 构建是否停滞
        if is_stagnating(
            state=state,
            current_turn=metrics.model_turns
        ):
            trace.record(
                turn=metrics.model_turns,
                event_type="stagnation",
                name="progress_guard",
                detail=(
                    f"last_progress_turn="
                    f"{state.last_progress_turn}"
                ),
            )
            metrics.stagnation_warnings += 1
            stagnation_feedback = build_stagnation_feedback(
                state=state
            )
            history_turns.append(
                [
                    {
                        "role": "user",
                        "content": stagnation_feedback
                    }
                ]
            )

        # 先压缩旧history，会在内部进行删除
        compress_history(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            state=state,
            history_turns=history_turns,
            metrics=metrics,
            trace=trace
        )

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

        requirements_context = build_requirement_context(
            state=state
        )

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
            + "\n\n"
            + requirements_context
        )

        print_state_debug(state)

        message = call_model(
            model_input,
            instructions=runtime_instructions,
            tools=TOOLS,
            thinking=False,
            debug=True,
        )
        # Preserve reasoning_content alongside tool calls for GLM's next turn.
        # input_items.append(message.model_dump(mode="json", exclude_none=True))
        turn_items = [message.model_dump(mode="json", exclude_none=True)]
        tool_calls = message.tool_calls or []

        if not tool_calls:
            # Completion Protocol
            report = evaluate_completion(state=state)
            metrics.completion_checks += 1
            missing_names = [
                item.name
                for item in report.missing
            ]
            trace.record(
                turn=metrics.model_turns,
                event_type="completion_check",
                name="completion_protocol",
                detail=(
                    f"completion={report.complete}"
                    f"missing={missing_names}"
                )
            )

            # 真正完成
            if report.complete == CompletionStatus.COMPLETE:
                metrics.end_runing()
                trace.print_trace()
                return message.content or ""

            if  report.complete == CompletionStatus.BLOCKED and allow_blocked_final:
                metrics.end_runing()
                trace.print_trace()
                return message.content or ""


            if report.complete == CompletionStatus.INCOMPLETE:

                metrics.completion_rejections += 1

                feed_back = build_completion_feedback(
                    report=report,
                    state=state
                )

                turn_items.append(
                    {
                        'role': 'user',
                        'content': feed_back
                    }
                )

                history_turns.append(
                    turn_items
                )

                continue

            if report.complete == CompletionStatus.BLOCKED:
                allow_blocked_final = True
                feed_back = build_completion_feedback(
                    report=report,
                    state=state
                )
                
                turn_items.append(
                    {
                        'role': 'user',
                        'content': feed_back
                    }
                )

                history_turns.append(
                    turn_items
                )

                continue

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

                if result.success:
                    # 推进了任务
                    if tool_caused_progress(
                        name=name,
                        result=result
                    ):
                        record_progress(
                            state=state,
                            turn=metrics.model_turns
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
        # if NEED_SLEEP:
        #     time.sleep(30)
