"""Tool JSON schemas and blocked-command list."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_goal",
            "description": (
                "Extract and lock the primary "
                "objective of the user's task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "objective": {
                        "type": "string",
                    },
                },
                "required": [
                    "objective"
                ],
            },
        },
    },
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
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "analysis",
                                    "implementation",
                                    "verification",
                                ],
                            },
                            "requirement_ids": {
                                "type": "array",
                                "items": {
                                    "type": "integer",
                                },
                                "uniqueItems": True,
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
                            "kind",
                            "requirement_ids",
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
    },
    {
        "type": "function",
        "function": {
            "name": "set_requirements",
            "description": (
                "Extract and lock the explicit task "
                "requirements from the user's request."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "requirements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "must_change",
                                        "must_not_modify",
                                        "must_verify",
                                        "soft_constraint",
                                    ],
                                },
                                "description": {
                                    "type": "string",
                                },
                                "target": {
                                    "type": [
                                        "string",
                                        "null",
                                    ],
                                },
                                "verifier": {
                                    "type": [
                                        "string",
                                        "null",
                                    ],
                                    "enum": [
                                        "test",
                                        "build",
                                        "syntax",
                                        "diff",
                                        None,
                                    ],
                                },
                                "command_contains": {
                                    "type": [
                                        "string",
                                        "null",
                                    ],
                                },
                            },
                            "required": [
                                "kind",
                                "description",
                            ],
                        },
                    },
                },
                "required": [
                    "requirements"
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replan",
            "description": (
                "Replace the current execution plan "
                "when evidence shows that the current "
                "plan is no longer effective. "
                "The goal and task requirements "
                "must remain unchanged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": [
                            "assumption_invalid",
                            "blocked_task",
                            "repeated_failure",
                            "stagnation",
                            "requirement_conflict",
                        ],
                    },
                    "explanation": {
                        "type": "string",
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "analysis",
                                        "implementation",
                                        "verification",
                                    ],
                                },
                                "requirement_ids": {
                                    "type": "array",
                                    "items": {
                                        "type": "integer",
                                    },
                                    "uniqueItems": True,
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
                                "kind",
                                "requirement_ids",
                                "required_evidence",
                            ],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": [
                    "reason",
                    "explanation",
                    "items",
                ],
            },
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
