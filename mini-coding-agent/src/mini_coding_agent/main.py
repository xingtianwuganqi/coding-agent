"""Thin entrypoint for the mini coding agent.

The implementation now lives in focused modules:

- ``config``: runtime constants (workspace, limits).
- ``prompts``: the system prompt.
- ``tools_schema``: model-facing tool schemas.
- ``tools``: tool implementations (filesystem + shell).
- ``tool_runner``: guarded tool dispatcher.
- ``agent``: the agent loop.
- ``cli``: interactive command line entrypoint.

This module re-exports the public entrypoint for backwards compatibility.
"""

from .cli import main, read_task
from .agent import run_agent

__all__ = ["main", "read_task", "run_agent"]


if __name__ == "__main__":
    main()
