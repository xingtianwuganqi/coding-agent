# 第 24 课：Runtime Lifecycle / Termination Protocol — 可运行参考实现

**适用基线：** 2026-10-28 用户上传的 `mini-coding-agent(3).zip`，不是第 22、23 课整合后的未知版本。

## 本包包含

- `src/mini_coding_agent/runtime_lifecycle.py`：**新增**。LifecycleDecision、预算、里程碑跟踪、终止条件。
- `src/mini_coding_agent/agent.py`：**示例修改**。改造主循环，调用模型之前、整批工具之后检查生命周期；结束时汇总结果。保留原工具 Guard、工具分发、历史压缩、Failure Recovery。
- `src/mini_coding_agent/agent_metrics.py`：**示例修改**。增加 run_outcome 与 stop_reason。
- `tests/test_runtime_lifecycle.py`：**新增**。9 个生命周期测试（再加现有 4 个模型重试测试共同跑通 13 个）。
- `lesson24.patch`：基于原始 zip 生成的统一补丁，推荐先 `git apply --check` 检查。

## 合并方法（不要覆盖你更新后的源码）

先在独立 Runner/实验分支验证，然后在用户的项目仓库根目录执行：

```bash
git status --short
git apply --check lesson24.patch
git apply lesson24.patch
uv run pytest -q
```

如果你已经把第 22、23 课的更改集成到了同一批文件，`git apply --check` 可能失败：**不要强行覆盖**，逐段参照补丁移植。`agent.py` 和 metrics 文件为 zip3 基线版本。

## 概念与设计

```
Task Completion：完成条件是否满足？
Progress Tracker：是否出现了首次有效的新里程碑？
Lifecycle Controller：继续、正常结束、阻塞、预算终止？
Agent Loop：负责执行决策、完整保存 tool replies。
```

每轮模型调用之前：检查 COMPLETE > 确认不可恢复 BLOCKED > MAX_TURNS > NO_PROGRESS。
每轮工具调用之后：完整保存工具响应，观察新里程碑，再检查是否结束。

`ProgressTracker` 只有第一次看到一个里程碑才更新 last_meaningful_turn，重复 `git diff`、`pytest`（相同 code_revision）、`update_task`、`replan` 不会按“工具成功”直接重置计时。**这仍是教学版启发式**，可能被新任务名或不同 revision 反复推进；第 27 课应强化对内容/证据的评估，硬调用预算仍然必要。

`BLOCKED` 只有明确输入 `unrecoverable_blocker=True` 才会终止；这版还没有完整的“是否可恢复”裁决器。它不会把单个任务 `BLOCKED` 直接断定整次运行无法完成；若无人恢复且无进展，最终会安全地 `STOPPED`。

本版 Finalize 是**不再调用工具的确定性汇总**，因此模型不会在宣布完成后继续执行工具。只有模型在无工具回合且 Runtime 同时判定 COMPLETE，才允许复用模型回复。更自然的总结可在下阶段加一次 `tools=None` 的受限模型调用及其失败回退。

## 回归验证

在具备完整 Python 依赖的项目环境：

```bash
uv run pytest -q tests/test_runtime_lifecycle.py
uv run pytest -q
```

我们在受限容器中运行原始测试时，由于没有可用的 `openai.OpenAI` 实现，用了**只用于测试导入的 stub**，并没有进行真实模型调用。运行时工作目录设为提取后的项目根目录，得到 **81 passed**（原有测试 + 9 个生命周期新测试）。不能因此宣称真实模型环境已验收。

## 特别注意

1. 原代码 `run_command` 调用外部脚本可能修改文件，但 `workspace_revision` 未必同步更新。生命周期依赖的“当前验证证据”将受此限制。第 26/27 课需增加受控的文件变更追踪。
2. 使用长期运行任务时，`max_no_progress_turns=30` 是教学默认，可根据实际任务调整；这不是正确性保障。
3. 新旧 ToolResult 完整保存后才检查结束，避免一组工具调用中断而缺少部分 Tool Message。
4. `agent_metrics.py` 保留既有 `end_runing()` 方法与现有返回字符串 API；旧测试未做不兼容变更。
5. 应先修复 `plan_validation.py` 中 `TodoKind` 运行时不可用的路径问题，并补相关测试，以免 Runtime 因 Validation 异常中断。
