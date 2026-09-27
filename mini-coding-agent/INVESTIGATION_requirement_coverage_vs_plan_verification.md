# Investigation: do `validate_requirement_coverage` and `validate_plan_verification` duplicate each other?

## Question

Are the two plan-validation functions `validate_requirement_coverage` and
`validate_plan_verification` functionally redundant?

## Answer

**No.** They validate two different dimensions of a plan and can fail
independently of each other. Both should be kept.

## Source evidence

File: `src/mini_coding_agent/planing.py`

Both functions are invoked from the same plan-validation pipeline:

- `validate_plan_verification(...)` — called at line 913
- `validate_requirement_coverage(...)` — called at line 931

### `validate_plan_verification` (lines 975–1018)

```python
def validate_plan_verification(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue]
) -> None:
    # 3. Verification coverage
    plan_evidence = {
        todo.required_evidence
        for todo in todos
        if todo.required_evidence is not None
    }

    verification_evidence_map = {
        "test": EvidenceType.TESTS_PASSED,
        "diff": EvidenceType.DIFF_INSPECTED
    }

    for requirement in state.requirements.items:
        if (requirement.kind != RequirementKind.MUST_VERIFY):
            continue

        expected_evidence = (
            verification_evidence_map.get(requirement.verifier)
        )
        if expected_evidence is None:
            continue

        if expected_evidence not in plan_evidence:
            issues.append(
                PlanValidationIssue(
                    code="missing_verification_step",
                    ...
                )
            )
```

- Reads the **`todo.required_evidence`** field (not `requirement_ids`).
- Considers **only `MUST_VERIFY`** requirements.
- Emits issue code **`missing_verification_step`**.
- Only handles verifiers `test` / `diff`; `build` / `syntax` are explicitly skipped.

### `validate_requirement_coverage` (lines 1120–1153)

```python
def validate_requirement_coverage(
        state: AgentState,
        todos: list[TodoItem],
        issues: list[PlanValidationIssue],
) -> None:
    covered_requirement_ids = set()
    for todo in todos:
        covered_requirement_ids.update(
            todo.requirement_ids
        )

    for requirement in state.requirements.items:
        if requirement.kind not in {
            RequirementKind.MUST_CHANGE,
            RequirementKind.MUST_VERIFY
        }:
            continue

        if requirement.id in covered_requirement_ids:
            continue

        issues.append(
            PlanValidationIssue(
                code="uncovered_requirement",
                ...
            )
        )
```

- Reads the **`todo.requirement_ids`** field (not `required_evidence`).
- Considers **`MUST_CHANGE` and `MUST_VERIFY`** requirements.
- Emits issue code **`uncovered_requirement`**.

## Comparison

| Aspect | `validate_plan_verification` | `validate_requirement_coverage` |
|---|---|---|
| Input field read | `todo.required_evidence` | `todo.requirement_ids` |
| Requirement kinds | only `MUST_VERIFY` | `MUST_CHANGE` + `MUST_VERIFY` |
| Issue code | `missing_verification_step` | `uncovered_requirement` |
| Failure it catches | a todo exists, but no todo supplies the required `tests_passed`/`diff_inspected` evidence | no todo references the requirement at all |

Neither function subsumes the other:

1. A todo references a `MUST_VERIFY` requirement in `requirement_ids` but its
   `required_evidence` is `none` → **coverage passes**, **verification fails**.
2. A todo declares `required_evidence = tests_passed` but the `MUST_VERIFY`
   requirement is never referenced by any todo → **verification passes**,
   **coverage fails**.
3. A `MUST_CHANGE` requirement is never referenced by any todo, while `test`/`diff`
   evidence exists elsewhere → **verification passes** (it ignores `MUST_CHANGE`),
   **coverage fails**.

## Minor overlap

When a `MUST_VERIFY` requirement using verifier `test`/`diff` is both unreferenced
and unsupported by matching evidence, both functions fire for the same requirement.
That is duplicate *reporting* of two independent failures, not duplicated *detection
logic*: each is triggered by a different field.

## Conclusion

The two functions encode distinct concerns — *requirement → plan linkage*
(coverage) versus *plan → evidence provision* (verification). They are
complementary, not redundant.
