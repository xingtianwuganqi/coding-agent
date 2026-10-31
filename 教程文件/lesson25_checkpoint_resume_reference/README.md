# Mini Coding Agent Lesson 25 - Checkpoint / Resume

Code based on the user's `mini-coding-agent(4).zip` snapshot.

## Files
- `src/mini_coding_agent/checkpoint.py`: safe versioned JSON checkpoint and recovery guard
- `src/mini_coding_agent/agent.py`: complete modified Agent Loop (including lifecycle and tool checkpoint hooks)
- `src/mini_coding_agent/cli.py`: `--checkpoint` / `--resume` CLI support
- `tests/test_checkpoint.py`: 10 regression and integration tests
- `lesson25_checkpoint_resume.patch`: patch against the uploaded v4 archive

## Usage (run from the workspace root)
Use a checkpoint path outside the Agent's editable workspace if practical:

```sh
uv run mini-coding-agent --checkpoint "$HOME/.mini-agent-checkpoints/task-001.json"
uv run mini-coding-agent --checkpoint "$HOME/.mini-agent-checkpoints/task-001.json" --resume
uv run pytest -q
```

IMPORTANT: use a separate checkpoint file for each run. Existing checkpoints are never overwritten
by a new task; use --resume, or choose another file path. An uncertain tool call or external
workspace edit intentionally raises RecoveryRequired. Inspect the workspace and reconcile it
manually; there is NO automatic retry of the uncertain operation.

A checkpoint does NOT back up project files. Checksum detects accidental corruption, not
adversarial tampering. Existing data can contain user prompts, model history and file contents;
store it outside Git, with restrictive filesystem permissions.

Tests on this assistant's container used a temporary OpenAI module stub (no real network calls),
because the openai SDK is unavailable here. The reference tests passed but LIVE API behavior has
not been verified. The script `test_stubs` is intentionally NOT shipped with the code package.
