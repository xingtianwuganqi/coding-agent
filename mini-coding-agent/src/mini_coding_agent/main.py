import json
import os
from pathlib import Path
from openai import OpenAI
import subprocess

client = OpenAI(
    api_key=os.getenv('ZHIPU_API_KEY', '7eb19ed5b41c4acbb18c68346448ef79.JiIVOlOXKnDkjHpY'),
    base_url='https://open.bigmodel.cn/api/paas/v4/',
)

WORKSPACE = Path.cwd().resolve()

MODEL = 'glm-4.7'

MAX_STEPS = 100

SYSTEM_PROMPT = """
You are a coding agent working inside a software project.

You can inspect files, modify files, and run commands.

Rules:

1. Never assume file contents.
2. Inspect relevant files before modifying them.
3. Stay inside the current workspace.
4. Prefer replace_text for small targeted changes.
5. Use write_file for new files or full rewrites.
6. Never modify unrelated files.
7. Do not run destructive commands.
8. After modifying code, run relevant tests or commands
   when possible to verify the change.
9. If a command fails, inspect the error and try to fix it.
10. Briefly explain the final changes and verification result.
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
        "description": "Read the text content of a file in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the workspace.",
                }
            },
            "required": ["path"],
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


def read_file(path: str) -> str:
    target = resolve_path(path)

    if not target.exists():
        return f"File does not exits: {path}"

    if not target.is_file():
        return f"Not a file: {path}"

    content = target.read_text(encoding="utf-8")

    if len(content) > 20_000:
        return content[:20_000] + "\n\n[truncated]"

    return content

def write_file(path: str, content: str) -> str:
    target = resolve_path(path)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    target.write_text(
        content,
        encoding="utf-8"
    )

    return f"Successfully wrote file: {path}"

def replace_text(
        path: str,
        old_text: str,
        new_text: str,
) -> str:
    target = resolve_path(path)

    if not target.exists():
        return f"File does not exist: {path}"

    content = target.read_text(
        encoding="utf-8"
    )

    if old_text not in content:
        return "Old text was not found in the file"

    count = content.count(old_text)

    if count > 1:
        return (
            f"Old text appears {count} times. "
            "Please provide more surrounding context "
            "so the replacement is unambiguous."
        )
    
    updated = content.replace(
        old_text,
        new_text,
        1
    )

    target.write_text(
        updated,
        encoding="utf-8"
    )

    return f"Successfully updated file: {path}"

def run_command(command: str) -> str:

    normalized = command.strip().lower()

    for blocked in BLOCKED_COMMANDS:
        if blocked in normalized:
            return f"Blocked dangerous command: {command}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=30
        )

        output = ""
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"

        if result.stderr:
            output += f"STDERR:\{result.stderr}\n"

        output += f"EXIT CODE: {result.returncode}"

    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds"

    except Exception as e:
        return f"Command error: {e}"

def execute_tool(name: str, arguments: dict) -> str:
    try:
        if name == "list_files":
            return list_files(**arguments)

        if name == "read_file":
            return read_file(**arguments)

        if name == "write_file":
            return write_file(**arguments)

        if name == "replace_text":
            return replace_text(**arguments)

        if name == "run_command":
            return run_command(**arguments)

        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"


def run_agent(task: str) -> str:
    input_items = [
        {
            "role": "user",
            "content": task,
        }
    ]

    for step in range(MAX_STEPS):
        print(f"\n --- Agent step {step + 1} ---")

        request = {
            "model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + input_items,
            "tools": [
                {"type": "function", "function": {k: v for k, v in tool.items() if k != "type"}}
                for tool in TOOLS
            ],
        }
        print("\n--- Request JSON ---")
        print(json.dumps(request, ensure_ascii=False, indent=2), flush=True)
        response = client.chat.completions.create(**request)

        print("\n--- Response JSON ---")
        print(response.model_dump_json(indent=2), flush=True)
        message = response.choices[0].message
        # Preserve reasoning_content alongside tool calls for GLM's next turn.
        input_items.append(message.model_dump(mode="json", exclude_none=True))
        tool_calls = message.tool_calls or []

        if not tool_calls:
            return message.content or ""

        for call in tool_calls:
            print(f"Tool: {call.function.name}")
            print(f"Arguments: {call.function.arguments}")
            try:
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                result = execute_tool(call.function.name, arguments)
            except (json.JSONDecodeError, ValueError) as error:
                result = f"Tool arguments error: {error}"

            print(f'result: \n{result}')

            input_items.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )

    return "Agent stopped because the maximum number of steps was reached."


def main():
    print("Mini Coding Agent")
    print("Type 'exit' to quit.\n")

    while True:
        task = input("You > ").strip()

        if task in {"exit", "quit"}:
            break

        if not task:
            continue

        result = run_agent(task)

        print("\nAgent >")
        print(result)
