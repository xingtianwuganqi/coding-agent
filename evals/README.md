# Lesson 27: Agent Evaluation Cases

**Separation**: The Agent works in `evals/fixtures/calculator_before/` copied to a new isolated workspace. The evaluator owns `evals/oracles/calculator/test_calculator.py`, *outside* the Agent editable workspace. The Agent must not see or modify the hidden oracle tests.

1. Copy `evals/fixtures/calculator_before/` to a fresh isolated workspace, e.g. `/tmp/mini-agent-eval-001/`. Use a new directory for every run.
2. Start Mini Coding Agent **with that copied fixture as your current working directory** and save its checkpoint **outside** the fixture. Use its task prompt from the case JSON.
3. Once the Agent completes (or stops), evaluate the final state by loading the checkpoint and checking the workspace. Only add `--run-tests` if you trust the external oracle test source.
4. Run each version multiple times from independently reset fixtures. Save one result JSON per run; aggregate into version JSON arrays for regression analysis.

```bash
uv run python -m mini_coding_agent.eval_cli inspect \
  --case evals/cases/calculator_divide_by_zero.json \
  --checkpoint "$HOME/.mini-agent-checkpoints/calculator-001.json" \
  --workspace "/tmp/mini-agent-eval-001" \
  --oracle-root evals/oracles \
  --run-tests \
  --output "$HOME/.mini-agent-evals/calculator-001.json"
```

**Practical note:** The executable is part of the Mini Agent package. When running it from a fixture cwd, you must have installed the package or set its import path. Executing arbitrary external pytest files can execute Python code; use only trusted oracle tests and an isolated subprocess/container. The CLI does not automatically start an Agent or provide a security sandbox.

The `file_contains` oracle is deliberately weak. An independent test checking behavior and side effects is the stronger correctness signal. A 'COMPLETE' label without independent success is a **false complete**.
