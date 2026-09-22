"""Runtime configuration constants for the mini coding agent."""

from pathlib import Path

WORKSPACE = Path.cwd().resolve()

MAX_STEPS = 1000
MAX_MODEL_TURN = 1000

# Default and maximum number of lines returned by the read_file tool.
DEFAULT_READ_LINES = 300
MAX_READ_LINES = 600

# Maximum number of matches returned by the search_text tool.
MAX_SEARCH_RESULTS = 20
