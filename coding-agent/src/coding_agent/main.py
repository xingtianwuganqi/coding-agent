import json
from dataclasses import asdict
from pathlib import Path
from openai import OpenAI
import subprocess
from .permission import classify_command, Permission
from .task_state import TaskStatus, TodoItem, AgentState, CompletionStatus, EvidenceType, CommandResult, FileOperationResult
import shlex


client = OpenAI(
    api_key="sk-3cb0a38c5ece4d58a6941ddfdee85c5d",
    base_url="https://api.deepseek.com",
)

WORKSPACE = Path.cwd().resolve()

MODEL = "deepseek-v4-flash"

# 最大模型循环次数
MAX_MODEL_TURNS = 50
# 最大工具调用次数
MAX_TOOL_CALLS = 100
# 最大上下文
MAX_CONTEXT_TURNS = 8
# 保持最近的turns
KEEP_RECENT_TURNS = 6

SYSTEM_PROMPT = """
You are a coding agent working inside a software project.

You can inspect files, modify files, and run commands.

Run only one shell command per tool call.

Do not use shell operators such as &&, ||, pipes,
redirections, or semicolons.

If multiple commands are needed, call run_command
multiple times.

For multi-step coding tasks, create a todo plan before
making changes.

Keep the plan concise and actionable.

Before working on a todo item, mark it as in_progress.

When an item is finished, mark it as completed.

Only one item should normally be in_progress at a time.

If an item cannot be completed, mark it as blocked and
include a short reason.

For very simple tasks that require only one obvious action,
a plan is not necessary.

Rules:

1. Never assume file contents.
2. Inspect relevant files before modifying them.
3. Stay inside the current workspace.
4. Prefer replace_text for small targeted changes.
5. Use write_file for new files or full rewrites.
6. Never modify unrelated files.
7. Do not run destructive commands.
8. After modifying code, run relevant tests when possible.
9. If a command fails, inspect the error and try to fix it.
10. Before finishing, inspect git diff when possible.
11. Some operations may require user approval.
12. If the user denies an operation, do not repeatedly request
    the same operation.
13. Prefer safer alternatives when available.
14. Briefly explain what changed and how it was verified.

When creating a plan:

- Tasks that run the test suite should require
  "tests_passed" evidence.

- Tasks that inspect the final git diff should require
  "diff_inspected" evidence.

- Other tasks should normally use "none".
"""

BLOCKED_COMMANDS = [
    "rm ",
    "sudo ",
    "shutdown",
    "reboot",
    "mkfs",
]

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
                    "description": "Directory path relative to the workspace",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        }
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read the text content of a file in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the workspace",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        }
                
    },
    {
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
                    "description": "File path relative to the workspace",
                },
                "content": {
                    "type": "string",
                    "description": "The complete content to write.",
                }
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }
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
                        "File path relative to the workspace"
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
                }
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        }
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
            "Create or replace the todo plan for "
            "a multi-step coding task. "
            "Use required evidence for tasks that "
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
                            },
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
    },
]

TOOL_PERMISSIONS = {
    "list_files": Permission.SAFE,
    "read_file": Permission.SAFE,
    "write_file": Permission.WRITE,
    "replace_text": Permission.WRITE,
    "run_command": Permission.SAFE,
    "set_plan": Permission.SAFE,
    "update_task": Permission.SAFE,
    "get_plan": Permission.SAFE
}
 

# 解析路径
def resolve_path(path: str) -> Path:
    """Resolve a path relative to the workspace."""
    target = (WORKSPACE / path).resolve()
    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError(f"Path {path} is outside the workspace.")
    return target

# 
def list_files(path: str) -> str:
    target = resolve_path(path)
    if not target.exists():
        return f"Directory does not exist: {path}"

    if not target.is_dir():
        return f"Path is not a directory: {path}"

    result = []

    for child in sorted(target.iterdir()):
        relative = child.relative_to(WORKSPACE)
        if child.is_dir():
            result.append(f"{relative}/")
        else:
            result.append(str(relative))

    return "\n".join(result) or "(empty directory)"

# 读取文件
def read_file(path: str) -> str:
    target = resolve_path(path)
    if not target.exists():
        return f"File does not exist: {path}"
    if not target.is_file():
        return f"Path is not a file: {path}"

    content = target.read_text(encoding="utf-8")

    if len(content) > 20_000:
        return content[:20_000] + "\n\n[truncated]"
    
    return content

# 写入文件
def write_file(path: str, content: str) -> FileOperationResult:
    target = resolve_path(path)
    if not target.parent.exists():
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Parent directory does not exist: {target.parent}"
            )
        )

    target.parent.mkdir(parents=True, exist_ok=True)
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
    target.write_text(content, encoding="utf-8")
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully wrote file: {path}"
        )
    )

# 写入text
def replace_text(path: str, old_text: str, new_text: str) -> FileOperationResult:
    target = resolve_path(path)
    if not target.exists():
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"File does not exist: {path}"
            )
        )
    if not target.is_file():
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Path is not a file: {path}"
            )
        )

    content = target.read_text(encoding="utf-8")
    if old_text not in content:
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Text '{old_text}' not found in file: {path}"
            )
        )

    count = content.count(old_text)
    if count > 1:
        return FileOperationResult(
            success=False,
            changed=False,
            message=(
                f"Old text appears {count} times. "
                "Please provide more surrounding context "
                "so the replacement is unambiguous."
            )
        )

    updated_content = content.replace(old_text, new_text, 1)
    if updated_content == content:
        return FileOperationResult(
            success=True,
            changed=False,
            message=(
                f"No changes needed: {path}"
            )
        )
    target.write_text(updated_content, encoding="utf-8")
    return FileOperationResult(
        success=True,
        changed=True,
        message=(
            f"Successfully updated file: "
            f"{path}"
        )
    )


# 询问是不是要继续
def ask_for_confirmation(command: str) -> bool:
    print("\n⚠️ Agent wants to run:")
    print(command)

    answer = input(
        "\nDo you want to allow this command? [y/N]: "
    )

    answer = answer.strip().lower()

    print(f"DEBUG answer: {answer!r}")

    return answer in {"y", "yes"}

# 运行命令
def run_command(command: str) -> CommandResult | str:

    permission = classify_command(command)
    if permission == Permission.BLOCKED:
        return f"Command is blocked for safety reasons: {command}"

    if permission == Permission.CONFIRM:
        if not ask_for_confirmation(command):
            return "User denied the command."
    
    try:
        args = shlex.split(command)

        result = subprocess.run(
            args,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # output = ""
        # if result.stdout:
        #     output += f"STDOUT:\n{result.stdout.strip()}\n"
        # if result.stderr:
        #     output += f"STDERR:\n{result.stderr.strip()}\n"

        # output += f"Exit code: {result.returncode}"
        # return output.strip()
        return CommandResult(
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode
        )
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."
    except subprocess.CalledProcessError as e:
        return f"Command failed with exit code {e.returncode}:\n{e.stderr.strip()}"
    except Exception as e:
        return f"Command error: {e}"

def format_plan(state: AgentState) -> str:
    """
    格式化plan
    """
    if not state.todos:
        return "No plan."

    symbols = {
        TaskStatus.PENDING: "[]",
        TaskStatus.IN_PROGRESS: "[>]",
        TaskStatus.COMPLETED: "[x]",
        TaskStatus.BLOCKED: "[!]",
    }

    lines = []

    for todo in state.todos:
        line = (
            f"{symbols[todo.status]}"
            f"{todo.id}.{todo.content}"
        )

        if (todo.required_evidence is not None):
            line += (
                " "
                "[requires:"
                f"{todo.required_evidence.value}"
                "]"
            )

        if todo.note:
            line += f" - {todo.note}"

        lines.append(line)

    return "\n".join(lines)

def format_command_result(
        result: CommandResult
) -> str:
    parts = []
    if result.stdout:
        parts.append(
            f"STDOUT:\n{result.stdout.strip()}\n"
        )
    if result.stderr:
        parts.append(
            f"STDERR:\n{result.stderr.strip()}\n"
        )
    parts.append(
        f"EXIT CODE: {result.returncode}"
    )

    return "\n".join(parts)


def update_task(
        state: AgentState,
        task_id: int,
        status: str,
        note: str = ""
) -> str:
    """
    更新任务状态
    """
    try:
        new_status = TaskStatus(status)
    except ValueError:
        return f"Invalid status: {status}"

    for todo in state.todos:
        if todo.id != task_id:
            continue

        if (new_status == TaskStatus.COMPLETED):
            required = (todo.required_evidence)
            if required is not None and not has_valid_evidence(state, required):
                return (
                    f"Cannot complete task "
                    f"{task_id}.\n"
                    f"Required evidence "
                    f"is missing: "
                    f"{required.value}"
                )

        todo.status = new_status

        if note:
            todo.note = note

        result = format_plan(state)
        print("\n--- Current Plan ---")
        print(result)
        return result
        
    return f"Task not found:{task_id}"

# 给Agent一个 set_plan工具
def set_plan(
        state: AgentState,
        items: list[dict]
) -> str:
    todos = []
    for index, item in enumerate(items, start=1):
        content = item["content"]
        evidence_value = item.get("required_evidence","none")
        required_evicence = None
        if evidence_value != "none":
            try:
                required_evicence = EvidenceType(
                    evidence_value
                )
            except ValueError:
                return (
                    f"Invalid evidence type: "
                    f"{evidence_value}"
                )
        todos.append(
            TodoItem(
                id=index,
                content=content,
                required_evidence=required_evicence
            )
        )
    state.todos = todos
    
    return format_plan(state)

def get_plan(state: AgentState) -> str:
    return format_plan(state)

# 记录命令完成的证据
def record_command_evidence(
        state: AgentState,
        result: CommandResult,
) -> None:
    try:
        args = shlex.split(result.command)
    except ValueError:
        return

    evidence_type = None
    if args[:1] == ["pytest"] or args[:3] == ["uv", "run", "pytest"]:
        evidence_type = EvidenceType.TESTS_PASSED
    elif args[:2] == ["git", "diff"]:
        evidence_type = EvidenceType.DIFF_INSPECTED

    if evidence_type is not None:
        if result.returncode == 0:
            state.evidence[evidence_type] = state.workspace_revision
        else:
            state.evidence.pop(evidence_type, None)

# 让evidence失效
def invalidate_evidence(
        state: AgentState,
) -> None:
    state.evidence.clear()

# 改变workspace_revision
def mark_workspace_changed(
        state: AgentState
) -> None:
    state.workspace_revision += 1
    invalidate_evidence(state)
    get_unfinished_tasks(state)

# 判断Evidence是否有效
def has_valid_evidence(
        state: AgentState,
        evidence_type: EvidenceType
) -> bool:
    evidence_revision = state.evidence.get(
        evidence_type
    )

    if evidence_revision is None:
        return False
    return (
        evidence_revision == state.workspace_revision
    )

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
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"
    
# tasks 相关
def get_unfinished_tasks(
        state: AgentState,
) -> list[TodoItem]:
    for todo in state.todos:
        if (todo.status == TaskStatus.COMPLETED
                and todo.required_evidence is not None
                and not has_valid_evidence(state, todo.required_evidence)):
            todo.status = TaskStatus.PENDING
    return [
        todo 
        for todo in state.todos
        if todo.status 
        not in {
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED
        }
    ]

def has_blocked_tasks(
        state: AgentState
) -> bool:
    return any(
        todo.status == TaskStatus.BLOCKED
        for todo in state.todos
    )

def is_task_complete(state: AgentState) -> bool:
    get_unfinished_tasks(state)
    if not state.todos:
        return True

    return all(
        todo.status == TaskStatus.COMPLETED
        for todo in state.todos
    )

def get_completion_status(
        state: AgentState,
) -> CompletionStatus:
    if not state.todos:
        return CompletionStatus.COMPLETE

    unfinished = get_unfinished_tasks(state)
    if unfinished:
        return CompletionStatus.INCOMPLETE

    if has_blocked_tasks(state):
        return CompletionStatus.BLOCKED

    return CompletionStatus.COMPLETE

# 打印出workspacerevision状态
def print_state_debug(
    state: AgentState,
) -> None:
    print("\n--- Runtime State ---")

    print(
        "Workspace revision:",
        state.workspace_revision,
    )

    print("Evidence:")

    if not state.evidence:
        print("  (none)")
        return

    for evidence_type, revision in (
        state.evidence.items()
    ):
        valid = (
            revision
            == state.workspace_revision
        )

        print(
            f"  {evidence_type.value}: "
            f"revision={revision}, "
            f"valid={valid}"
        )

def build_model_input(
        task: str,
        history_turns: list[list],
        state: AgentState
) -> list:
    """
    构建当前模型的上下文
    """
    input_items = [
        {
            "role": "user",
            "content": task,
        }
    ]

    if state.task_summary:
        input_items.append(
            {
                "role": "user",
                "content": (
                    "TASK MEMORY SUMMARY: \n"
                    f"{state.task_summary}"
                )
            }
        )

    for turn in history_turns:
        input_items.extend(turn)

    return input_items

# 格式化runtime的state
def format_runtime_state(
        state: AgentState
) -> str:
    lines = []
    lines.append(
        f"Workspace revision:"
        f"{state.workspace_revision}"
    )
    lines.append("")
    lines.append("Current plan:")
    lines.append(
        format_plan(state)
    )
    lines.append("")
    lines.append("Evidence:")

    if not state.evidence:
        lines.append("(none)")
    else:
        for evidence_type, revision in state.evidence.items():
            valid = (
                revision == state.workspace_revision
            )
            lines.append(
                f"- {evidence_type.value}: "
                f"revision={revision}, "
                f"valid={valid}"
            )

    return "\n".join(lines)

# 将turn转成str
def turn_to_text(
        turn: list
) -> str:
    lines = []
    for item in turn:
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "function_call_output":
                lines.append(
                    "TOOL RESULT:\n"
                    f"{item.get('output','')}"
                )
            elif item.get("role") == "user":
                lines.append(
                    "RUNTIME FEEDBACK:\n"
                    f"{item.get('content','')}"
                )
            continue

        item_type = getattr(
            item, 
            "type",
            None,
        )

        if item_type == "function_call":
            lines.append(
                "TOOL CALL:\n"
                f"{item.name}"
                f"({item.arguments})"
            )
        elif item_type == "message":
            text_parts = []
            for content in item.content:
                text = getattr(
                    content,
                    "text",
                    None,
                )

                if text:
                    text_parts.append(text)

            if text_parts:
                lines.append(
                    "ASSISTANT:\n"
                    + "\n".join(text_parts)
                )

    return "\n\n".join(lines)

# 把多个turn拼起来
def history_to_text(
        turns: list[list]
) -> str:
    parts = []
    for index, turn in enumerate(turns, start=1):
        text = turn_to_text(turn)
        parts.append(
            f"---Old Turn {index} ---\n"
            f"{text}"
        )

    return "\n\n".join(parts)

# 总结历史总结
def summarize_history(
        old_summary: str,
        history_text: str,
) -> str:
    prompt = f"""
    You are maintaining working memory for a coding agent.

    Compress the following old conversation/tool history into
    a concise task summary.

    Keep only information that may matter later, such as:

    - important findings
    - confirmed bug causes
    - user requirements and constraints
    - design decisions
    - files or components already changed
    - important failures or unresolved issues

    Do NOT include:

    - routine tool calls
    - verbose command output
    - details that are no longer useful

    Existing summary:

    {old_summary or "(none)"}

    New history to compress:

    {history_text}

    Return only the updated concise summary.
    """

    response = client.responses.create(
        model=MODEL,
        input=prompt,
    )

    return response.output_text.strip()

def compress_history(
        state: AgentState, 
        history_turns: list[list]
) -> None:
    if (len(history_turns) <= MAX_CONTEXT_TURNS):
        return 

    compress_count = (
        len(history_turns) - KEEP_RECENT_TURNS
    )

    old_turns = history_turns[:compress_count]

    history_text = history_to_text(old_turns)

    new_summary = summarize_history(
        state.task_summary,
        history_text
    )

    state.task_summary = new_summary

    del history_turns[:compress_count]

# 运行agent
def run_agent(task: str) -> str:
    state = AgentState()
    allow_blocked_final = False
    # input_items = [
    #     {
    #         "role": "user",
    #         "content": task,
    #     }
    # ]
    history_turns: list[list] = []
    #-----记忆系统
    # 上下文Context = Recent Turns + Runtime state + Task Summary
    # Recent Turns: 最近几轮具体发生了什么
    # Runtime state: 当前做到哪、Evidence、Revision
    # Task Summary: 过去留下来的结论

    model_turns = 0
    tool_call_count = 0

    while True:
        if model_turns > MAX_MODEL_TURNS:
            return (
                "Agent Stopped:"
                "maxinum model turns reached."
            )
        model_turns += 1
        print(f"\n--- Agent turn {model_turns} ---")
        # 压缩上下文
        compress_history(
            state=state,
            history_turns=history_turns
        )
        # -------------------------
        # 1. 构建有限长度 Context
        # -------------------------
        model_input = build_model_input(
            task=task, 
            history_turns=history_turns,
            state=state
        )

        # 每次调用模型时，动态生成最新的RuntimeState
        # -------------------------
        # 2. 注入最新 Runtime State
        # -------------------------
        runtime_instructions = (
            SYSTEM_PROMPT
            + "\n\n"
            + "CURRENT RUNTIME STATE:\n"
            + format_runtime_state(state)
        )

        response = client.responses.create(
            model=MODEL,
            instructions=runtime_instructions,
            input=model_input,
            tools = TOOLS,
        )
        # response = client.responses.create(
        #     model=MODEL,
        #     instructions=SYSTEM_PROMPT,
        #     input=input_items,
        #     tools=TOOLS,
        # )

        # 模型产出的内容必须保留下来
        # 下一轮 Agent 需要知道之前自己做了什么
        # input_items.extend(response.output)
        # 本轮所有内容先放这里
        turn_items = list(
            response.output
        )
        print(
            json.dumps(
                [item.model_dump(mode="json") for item in response.output],
                ensure_ascii=False,
                indent=2,
            )
        )

        tool_calls = [
            item
            for item in response.output
            if item.type == "function_call"
        ]

        # 判断任务都完成了才结束
        # -------------------------
        # 4. 模型想结束
        # -------------------------

        if not tool_calls:
            completion_status = get_completion_status(
                state=state
            )

            if completion_status == CompletionStatus.COMPLETE:
                return response.output_text

            if completion_status == CompletionStatus.BLOCKED and allow_blocked_final:
                return response.output_text

            if completion_status == CompletionStatus.INCOMPLETE:
                runtime_feedback = {
                    "role": "user",
                    "content": (
                        "You attempted to finish, "
                        "but the task plan is "
                        "not complete.\n\n"
                        f"{format_plan(state)}\n\n"
                        "Continue working on "
                        "the unfinished tasks."
                    )
                }

                turn_items.append(runtime_feedback)
                history_turns.append(turn_items)
                
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

        for call in tool_calls:

            print_state_debug(state)

            tool_call_count += 1
            if tool_call_count > MAX_TOOL_CALLS:
                return (
                    "Agent stopped:"
                    "maximum tool calls reached"
                )
            arguments = json.loads(call.arguments)

            print(f"Tool call: {call.name}")
            print(f'args:{arguments}')

            result = execute_tool(
                call.name, 
                arguments,
                state
            )

            print(f"Tool result: {result}")

            tool_output = {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result,
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
    print('Mini Coding Agent')
    print("输入 :send 发送任务")
    print("输入 exit 后再输入 :send 退出\n")

    while True:
        task = read_task()
        if task in {'exit', 'quit'}:
            break

        if not task:
            continue

        result = run_agent(task)
        print("\n Agent >")
        print(result)
