"""Tool call signature helper and guarded tool dispatcher."""

import json
import subprocess
from dataclasses import asdict

from .permission import (
    Permission,
    TOOL_PERMISSIONS,
    ask_for_confirmation,
)
from .planing import (
    AgentState,
    set_plan,
    get_plan,
    update_task,
    record_progress
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
    write_file,
    replace_text,
    run_command,
)

from .agent_metrics import (
    AgentMetrics,
    is_test_command
)

from .agent_trace import AgentTrace

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

def execute_tool(
        name: str,
        arguments: dict,
        state: AgentState,
        metrics: AgentMetrics,
        trace: AgentTrace,
) -> ToolResult:

    try:
        # 未知工具直接返回，避免被误判为被禁止的工具
        if name not in TOOL_PERMISSIONS:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {name}"
            )

        # 调用工具前对权限进行询问
        permission = TOOL_PERMISSIONS[name]

        if permission == Permission.BLOCKED:
            return ToolResult(
                success=False,
                error=f"Tool is blocked: {name}"
            )
        
        if permission == Permission.CONFIRM:
            if not ask_for_confirmation(name):
                return ToolResult(
                    success=False,
                    error="User denied the tool call."
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
                record_progress(
                    state=state,
                    turn=metrics.model_turns
                )
                # mark_workspace_changed(state)
                record_workspace_change(
                    state=state,
                    path=arguments["path"]
                )

                record_progress(
                    state=state,
                    turn=metrics.model_turns
                )

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
                record_progress(
                    state=state,
                    turn=metrics.model_turns
                )
                # mark_workspace_changed(state)
                record_workspace_change(
                    state=state,
                    path=arguments["path"]
                )
                record_progress(
                    state=state,
                    turn=metrics.model_turns
                )
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
                record_progress(
                    state=state,
                    turn=metrics.model_turns
                )
                if metrics.plan_created_turn is None:
                    metrics.plan_created_turn = (
                        metrics.model_turns
                    )

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

        if name == "set_requirements":
            result = set_requirements(
                state=state,
                **arguments
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

            return result

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
) -> ToolResult:

    # =========================
    # 第一次执行
    # =========================

    metrics.tool_executions += 1

    result = execute_tool(
        name=name,
        arguments=arguments,
        state=state,
        metrics=metrics,
        trace=trace
    )

    # =========================
    # 成功
    # =========================

    if result.success:

        clear_failure_streak(
            state
        )

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
            trace=trace
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
