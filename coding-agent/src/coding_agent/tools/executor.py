"""Executes a single tool call requested by the model."""

from dataclasses import asdict
import json
from ..permission import Permission
from ..planning import (
    get_plan,
    mark_workspace_changed,
    record_command_evidence,
    set_plan,
    update_task,
)
from ..task_state import (
    AgentState,
    CommandResult,
)
from ..tools.commands import (
    format_command_result,
    run_command,
)
from ..tools.filesystem import (
    list_files,
    read_file,
    replace_text,
    search_text,
    write_file,
    create_directory,
)
from ..tools.schemas import TOOL_PERMISSIONS

# 调用工具
def execute_tool(
        name: str, 
        arguments: dict,
        state: AgentState
) -> str:
    try:

        permission = TOOL_PERMISSIONS.get(name, Permission.BLOCKED)
        if permission == Permission.BLOCKED:
            return f"Tool is blocked: {name}"

        if permission == Permission.CONFIRM:
            print(f'\n⚠️ Agent wants to use tool: {name}')
            print(f'Arguments: {arguments}')
            answer = input("Do you want to allow this tool? (y/n): ").strip().lower()
            if answer not in {'y', 'yes'}:
                return "User denied the tool."

        if name == "set_plan":
            return set_plan(
                state=state,
                **arguments
            )
        elif name == "update_task":
            return update_task(
                state=state,
                **arguments,
            )
        elif name == "get_plan":
            return get_plan(
                state=state
            )
        elif name == "list_files":
            return list_files(**arguments)
        elif name == "read_file":
            return read_file(**arguments)
        elif name == "write_file":
            result = write_file(**arguments)
            if result.changed:
                mark_workspace_changed(state)

            return json.dumps(asdict(result), ensure_ascii=False)
        elif name == "replace_text":
            result = replace_text(**arguments)
            if result.changed:
                mark_workspace_changed(state)
            return json.dumps(asdict(result), ensure_ascii=False)
        elif name == "run_command":
            result = run_command(**arguments)
            if isinstance(result, CommandResult):
                record_command_evidence(state=state, result=result)
                return format_command_result(result=result)
            return result
        elif name == "search_text":
            return search_text(
                **arguments
            )
        elif name == "create_directory":
            result = create_directory(**arguments)
            if result.changed:
                mark_workspace_changed(state=state)

            return json.dumps(
                asdict(result), 
                ensure_ascii=False
            )
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"
