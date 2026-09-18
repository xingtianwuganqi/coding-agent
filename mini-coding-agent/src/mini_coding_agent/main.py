import json
from pathlib import Path
from .model_api import call_model
import subprocess
from dataclasses import asdict
from .permission import (
    Permission,
    TOOL_PERMISSIONS,
    classify_command,
    ask_for_confirmation,
)

from .planing import (
    AgentState,
    TodoItem,
    set_plan,
    get_plan,
    update_task,
    format_plan,
    print_state_debug,
)

from .task_complation import (
    CompletionStatus,
    get_completion_status,
    get_unfinished_tasks,
)

from .evidence import (
    CommandResult,
    FileOperationResult,
    format_command_result,
    record_command_evidence,
    mark_workspace_changed,

)

from .context import (

    build_model_input,
    format_runtime_state,
    compress_history,
    print_context_debug,
    print_fixed_context_breakdown,
)

import shlex

WORKSPACE = Path.cwd().resolve()


MAX_STEPS = 100
MAX_MODEL_TURN = 100
# 当前保留的最近的多少条数据
# 前6个内容会进行总结

# 单次 Tool Result 最多给 LLM 大约 12000 个字符。

# 默认读取行数
DEFAULT_READ_LINES = 20
# 最大阅读行数
MAX_READ_LINES = 40
# 最大搜索结果
MAX_SEARCH_RESULTS = 5




SYSTEM_PROMPT = """
You are a coding agent working inside a software project.

Your job is to inspect the workspace, plan when necessary,
modify code safely, verify the result, and complete the user's task
with minimal unrelated changes.

The workspace is the source of truth.
Never rely on memory or summaries when exact current file contents
are required. Re-read only the specific files or ranges needed.

# Core workflow

For non-trivial coding tasks, follow this general workflow:

1. Understand the user's goal.
2. Inspect only the relevant project structure and code.
3. Create a concise todo plan.
4. Execute the plan one task at a time.
5. Verify changes with appropriate tests or commands.
6. Inspect the final git diff when possible.
7. Finish only after the task is actually complete.

Do not spend excessive time inspecting the project.
Once you have enough information to make the next safe change,
move to implementation.

For large refactors, do not try to read or memorize the entire
codebase before making changes.

Instead use:

plan -> locate relevant code -> read specific ranges -> modify ->
verify -> continue with the next responsibility.

# Planning

For multi-step tasks, create a todo plan before making changes.

Plans must be concise, actionable, and focused on the user's goal.

Before working on a todo item:
- mark it as "in_progress".

When the item is actually finished:
- mark it as "completed".

Normally only one todo item should be "in_progress" at a time.

If an item cannot currently be completed:
- mark it as "blocked";
- include a short reason.

For simple tasks with one obvious action, a plan is optional.

When creating todo items:

- Test verification tasks should require:
  "tests_passed"

- Final git diff inspection tasks should require:
  "diff_inspected"

- Other tasks should normally require:
  "none"

Do not mark a task completed before performing the work required
to complete it.

# Inspection and retrieval

Never assume file contents.

Inspect relevant files before modifying them.

For large files:
- prefer search_text to locate relevant symbols or text;
- read only the relevant line range;
- expand the range only when more surrounding context is required;
- do not repeatedly read the same content without a specific reason.

Do not re-inspect the whole project merely for reassurance.

Once an inspection or planning task is complete, move to the next
todo item unless a specific missing fact blocks implementation.

If exact current code is needed, retrieve it from the workspace
instead of relying on an old summary.

# File modifications

Stay inside the current workspace.

Prefer replace_text for small, precise changes.

Use write_file when:
- creating a new file; or
- a full rewrite is clearly appropriate.

Do not modify unrelated files.

Keep changes as small and focused as reasonably possible.

For refactoring tasks:
- preserve existing behavior unless the user requested a behavior change;
- separate code by responsibility;
- avoid unnecessary abstraction;
- avoid splitting code into many tiny modules without a clear benefit.

# Commands

Run only one shell command per run_command tool call.

Do not use shell operators such as:

&&
||
;
|
>
<
$()
backticks

If multiple commands are required, call run_command separately
for each command.

Do not run destructive commands.

Some commands may require user approval.

If the user denies an operation:
- do not repeatedly request the same operation;
- use a safer alternative if one exists;
- otherwise explain the limitation.

# Verification

After modifying code, run relevant tests or validation commands
when possible.

If a test or command fails:
1. inspect the failure;
2. identify the likely cause;
3. make a focused correction;
4. run the relevant verification again.

Do not claim tests passed unless the runtime has produced valid
test evidence.

Before finishing a coding task, inspect the final git diff when
possible.

After inspecting the diff:
- confirm that the changes are relevant to the task;
- check for accidental or unrelated edits;
- correct unnecessary changes before finishing.

# Progress

Prefer making concrete progress over repeated inspection.

Do not repeatedly:
- read the same files;
- inspect the same project structure;
- run the same command;
- reconsider an already-decided architecture;

unless new evidence or a specific failure gives a reason to do so.

If enough information is available to implement the current todo,
begin implementation.

# Completion

Do not provide a final answer while required todo items remain
pending or in progress.

If work is blocked and cannot proceed, clearly explain:
- what was completed;
- what remains blocked;
- why it is blocked.

When the task is complete, briefly report:
- what changed;
- how it was verified;
- any important limitation or blocked item.
"""

TOOLS = [
    {
        "type": "function",
        "name": "list_files",
        "description": "List files and directories inside a directory in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to the workspace.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": (
            "Read a range of lines from a text file "
            "inside the workspace. "
            "Use line ranges for large files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path relative "
                        "to the workspace."
                    ),
                },
                "start_line": {
                    "type": "integer",
                    "description": (
                        "First line to read. "
                        "Line numbers start at 1."
                    ),
                },
                "end_line": {
                    "type": "integer",
                    "description": (
                        "Last line to read."
                    ),
                },
            },
            "required": [
                "path",
            ],
            "additionalProperties": False,
        },
    },{
        "type": "function",
        "name": "write_file",
        "description": (
            "Create or overwrite a text file "
            "inside the workspace."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path relative to the workspace."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The complete content to write."
                    ),
                },
            },
            "required": [
                "path",
                "content",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "replace_text",
        "description": (
            "Replace one exact piece of text "
            "inside an existing file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path relative to the workspace."
                    ),
                },
                "old_text": {
                    "type": "string",
                    "description": (
                        "Exact existing text to replace."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": (
                        "New text that replaces old_text."
                    ),
                },
            },
            "required": [
                "path",
                "old_text",
                "new_text",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "run_command",
        "description": (
            "Run a shell command inside the current workspace. "
            "Use this to run tests, inspect git diff, "
            "or execute project commands."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command to execute "
                        "inside the workspace."
                    ),
                }
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    }, 
    {
        "type": "function",
        "name": "set_plan",
        "description": (
            "Create a todo plan for a multi-step task. "
            "Use evidence requirements for tasks that "
            "must be verified by the runtime."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                            },
                            "required_evidence": {
                                "type": "string",
                                "enum": [
                                    "none",
                                    "tests_passed",
                                    "diff_inspected",
                                ],
                            }
                        },
                        "required": [
                            "content",
                            "required_evidence",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "update_task",
        "description": (
            "Update the status of one todo item."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                },
                "status": {
                    "type": "string",
                    "enum": [
                        "pending",
                        "in_progress",
                        "completed",
                        "blocked",
                    ],
                },
                "note": {
                    "type": "string",
                },
            },
            "required": [
                "task_id",
                "status",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_plan",
        "description": (
            "View the current todo plan and task statuses."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },{
        "type": "function",
        "name": "search_text",
        "description": (
            "Search for text inside files in the workspace. "
            "Returns file paths, line numbers, "
            "and matching lines."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Text to search for."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to search. "
                        "Defaults to the workspace."
                    ),
                },
            },
            "required": [
                "query",
            ],
            "additionalProperties": False,
        },
    }
]

BLOCKED_COMMANDS = [
    "rm ",
    "sudo ",
    "shutdown",
    "reboot",
    "mkfs",
]

def resolve_path(path: str) -> Path:
    target = (WORKSPACE / path).resolve()

    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError("Path is outside the workspace")

    return target


def list_files(path: str) -> str:
    target = resolve_path(path)

    if not target.exists():
        return f"Direcotory does not exist: {path}"

    if not target.is_dir():
        return f"Not a directory: {path}"

    result = []
    for child in sorted(target.iterdir()):
        relative = child.relative_to(WORKSPACE)

        if child.is_dir():
            result.append(f"{relative}/")
        else:
            result.append(str(relative))

    return "\n".join(result) or "empty directory"


def read_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    target = resolve_path(path)

    if not target.exists():
        return (
            f"File does not exist: {path}"
        )

    if not target.is_file():
        return (
            f"Not a file: {path}"
        )

    try:
        content = target.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError:
        return (
            f"Cannot read binary or "
            f"non-UTF-8 file: {path}"
        )

    lines = content.splitlines()

    total_lines = len(lines)

    if total_lines == 0:
        return (
            f"File: {path}\n"
            f"(empty file)"
        )

    if start_line < 1:
        start_line = 1

    if start_line > total_lines:
        return (
            f"Start line {start_line} "
            f"is beyond the end of file.\n"
            f"Total lines: {total_lines}"
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

    return "\n".join(result)


def search_text(
    query: str,
    path: str = ".",
) -> str:
    target = resolve_path(path)

    if not target.exists():
        return (
            f"Path does not exist: {path}"
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
                    return (
                        "\n".join(results)
                        + "\n\n"
                        + "[Search results truncated]"
                    )

    if not results:
        return (
            f"No matches found for: "
            f"{query}"
        )

    return "\n".join(results)
    


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

def run_command(command: str) -> CommandResult | str:

    permission = classify_command(command=command)

    if permission == Permission.BLOCKED:
        return f"Blocked dangerous command: {command}"

    if permission == Permission.CONFIRM:
        if not ask_for_confirmation(command=command):
            return "User deinied the command"

    try:
        args = shlex.split(command)
        result = subprocess.run(
            args=args,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30
        )

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

def execute_tool(
        name: str,
        arguments: dict,
        state: AgentState
) -> str:

    try:
        # 调用工具前对权限进行询问
        permission = TOOL_PERMISSIONS.get(
            name,
            Permission.BLOCKED
        )

        if permission == Permission.BLOCKED:
            return f"Tool is blocked: {name}"
        
        if permission == Permission.CONFIRM:
            if not ask_for_confirmation(name):
                            return "User denied the tool call."
            
        if name == "list_files":
            return list_files(**arguments)

        if name == "read_file":
            return read_file(**arguments)

        if name == "write_file":
            result = write_file(**arguments)
            if result.changed:
                mark_workspace_changed(state)
            return json.dumps(asdict(result))

        if name == "replace_text":
            result = replace_text(**arguments)
            if result.changed:
                mark_workspace_changed(state)
            return json.dumps(asdict(result))
        if name == "run_command":
            # return run_command(**arguments)
            # 执行命令
            result = run_command(
                **arguments
            )
            if isinstance(
                result,
                CommandResult
            ):
                # 先执行write_file或者replace_text,会更新workspace_revision += 1
                # 命令执行完后，
                # 将执行命令的结果记录下来
                # 更新state的evidence，让 state.evidence[EvidenceType.TESTS_PASSED] = state.workspace_revision
                record_command_evidence(
                    state,
                    result
                )

                return format_command_result(
                    result=result
                )

            # Timeout/error strings do not establish successful verification.
            return result

        if name == "set_plan":
            return set_plan(
                state=state,
                **arguments,
            )

        if name == "update_task":
            return update_task(
                state=state,
                **arguments,
            )

        if name == "get_plan":
            return get_plan(state)

        if name == "search_text":
            return search_text(
                **arguments
            )
        

        return f"Unknown tool: {name}"
    except subprocess.TimeoutExpired:
        return (
            "Command timed out "
            "after 30 seconds."
        )
    except Exception as e:
        return f"Tool error: {e}"


def run_agent(task: str) -> str:

    state = AgentState()

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
        if turn >= MAX_MODEL_TURN:
            return (
                "Agent stopped because the maximum "
                "number of model turns was reached."
            )
        print(f"\n --- Agent step {turn} ---")

        # 先压缩旧history，会在内部进行删除
        compress_history(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            task=task,
            state=state,
            history_turns=history_turns
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
        )

        print_state_debug(state)

        message = call_model(
            model_input,
            instructions=runtime_instructions,
            tools=TOOLS,
            debug=True,
        )
        # Preserve reasoning_content alongside tool calls for GLM's next turn.
        # input_items.append(message.model_dump(mode="json", exclude_none=True))
        turn_items = [message.model_dump(mode="json", exclude_none=True)]
        tool_calls = message.tool_calls or []

        if not tool_calls:
            # return message.content or ""
            completion_status = get_completion_status(
                state
            )

            if completion_status == CompletionStatus.COMPLETE:
                return message.content or ""

            if (
                completion_status == CompletionStatus.BLOCKED
                and allow_blocked_final == True
            ):
                return message.content or ""


            if completion_status == CompletionStatus.INCOMPLETE:
                # unfinished = get_unfinished_tasks(state)

                # unfinished_text = "\n".join(
                #     f"- {todo.id}. {todo.content}"
                #     for todo in unfinished
                # )

                runtime_feedback = {
                    "role": "user",
                    "content": (
                        "You attempted to finish "
                        "the task, but the plan "
                        "is not complete.\n\n"
                        f"{format_plan(state)}\n\n"
                        "Continue working on "
                        "the unfinished tasks."
                    ),
                }

                turn_items.append(runtime_feedback)
                history_turns.append(
                    turn_items
                )

                continue

            if completion_status == CompletionStatus.BLOCKED:
                allow_blocked_final = True
                runtime_feedback = {
                    "role": "user",
                    "content": (
                        "Some tasks are blocked.\n\n"
                        f"{format_plan(state)}\n\n"
                        "Provide a final answer "
                        "explaining completed work "
                        "and blocked tasks."
                    ),
                }
                turn_items.append(
                    runtime_feedback
                )

                history_turns.append(
                    turn_items
                )

                continue

        # -------------------------
        # 5. 执行 Tool Calls
        # -------------------------
        for call in tool_calls:
            print(f"Tool: {call.function.name}")
            print(f"Arguments: {call.function.arguments}")
            try:
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                result = execute_tool(call.function.name, arguments, state)
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

def read_task() -> str:
    print("You > ", end="", flush=True)

    lines = []

    while True:
        line = input()

        if line.strip() == ":send":
            break

        lines.append(line)

    return "\n".join(lines).strip()

def main():
    print("Mini Coding Agent")
    print("输入 :send 发送任务")
    print("输入 exit 后再输入 :send 退出\n")

    while True:
        task = read_task()

        if task in {"exit", "quit"}:
            break

        if not task:
            continue

        result = run_agent(task)

        print("\nAgent >")
        print(result)
