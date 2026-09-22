"""Mini coding agent package."""

# NOTE: Do not re-export a ``main`` name here. ``mini_coding_agent.main``
# must resolve to the ``main.py`` submodule (the console-script target is
# ``mini_coding_agent.main:main``). Re-exporting the ``cli.main`` function
# would shadow that submodule.
from .agent import run_agent
from .cli import read_task

__all__ = ["read_task", "run_agent"]
