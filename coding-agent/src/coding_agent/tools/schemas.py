"""Tool schemas exposed to the model and their permission levels."""

from ..permission import Permission

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
    },
    {
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
    {
        "type": "function",
        "name": "create_directory",
        "description": (
            "Create a directory inside the workspace. "
            "Parent directories are created automatically if needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Directory path relative to the workspace."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


TOOL_PERMISSIONS = {
    "list_files": Permission.SAFE,
    "read_file": Permission.SAFE,
    "search_text": Permission.SAFE,
    "write_file": Permission.WRITE,
    "replace_text": Permission.WRITE,
    "create_directory": Permission.WRITE,
    "run_command": Permission.SAFE,
    "set_plan": Permission.SAFE,
    "update_task": Permission.SAFE,
    "get_plan": Permission.SAFE,
}
