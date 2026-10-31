# Mini Coding Agent 后续学习计划（第 22～27 课）

> 学习目标：完成 Mini Coding Agent 的**教学版核心架构**，形成对 Coding Agent 的任务调度、执行控制、状态持久化、安全与质量评估的系统理解，然后进入 MCP 学习。
>
> 学习策略：**原理优先、少量手写、借助 OpenCode 完成跨模块工程修改、通过固定任务验证**。不再把“手工抄完所有代码”当作学习成果。

## 一、当前起点与完成边界

当前 Mini Coding Agent 已经具备以下基础能力（以 2026-10-28 讨论的 `mini-coding-agent(3).zip` 为起点）：

- Agent Loop、模型 API 调用、Tool Calling、文件工具与基础权限控制。
- Goal / Requirement / Plan 分离，以及 Plan Validation 和 Task State Machine。
- Context Compression、Verification、Completion Guard、Failure Recovery、Replanning。
- `ask_user`、`delete_file`、运行指标及执行日志。

**当前课程起点：第 22 课。** 预计还需要 **6 课**完成教学版核心架构；这是学习范围规划，不代表生产级产品已经完成。此前约 75%～80% 的进度估计仅用于教学安排，不代表代码质量评分。

### 开课前的短修复（不占用新课）

在继续第 22 课前，先核查和修复下列问题：

1. `plan_validation.py` 中 `TodoKind` 仅置于 `TYPE_CHECKING` 导入，却在运行时被调用的潜在 `NameError`；为 `validate_requirement_mapping()` 的不同 Requirement 类型补充覆盖测试。
2. 检查 `PlanValidationResult.issues` 默认值变更是否影响旧调用方。
3. 确认测试运行使用的是刚修改过的工作区代码，而非另一份 Runner 或旧安装包。

**原则：** 只修明确可复现的错误；不因此展开大规模重构。

## 二、课程总览

| 课次 | 主题 | 重点模块（以实际源码为准） | 本课交付物 |
|---|---|---|---|
| 第 22 课 | Task Dependency / Execution Ordering | `planning.py`、`plan_validation.py`、`task_state.py`、`agent.py` | 依赖 DAG 校验与 Ready Task 选择 |
| 第 23 课 | Dependency-aware Replanning / Scheduling | `replanning.py`、`planning.py`、`task_state.py` | 失败、阻塞、重规划时的依赖维护 |
| 第 24 课 | Runtime Lifecycle / Termination Protocol | `agent.py`、`task_completion.py`、`stagnation.py` | 明确的完成／阻塞／停止协议 |
| 第 25 课 | Checkpoint / Resume / State Persistence | `AgentState`、`context.py` 等 | 最小可用的状态持久化与恢复 Demo |
| 第 26 课 | Tool Safety / Human-in-the-loop | `permission.py`、`tool_runner.py`、`ask_user` | 工具权限、副作用与人工确认测试 |
| 第 27 课 | Agent Evaluation / Regression Testing | `agent_metrics.py`、`agent_trace.py`、`tests/` | 固定评测任务集与质量报告 |

## 三、逐课学习目标与验收标准

### 第 22 课：Task Dependency / Execution Ordering

**目标：** 从线性的 Todo 列表进化为具有依赖关系的任务图，让 Runtime 而非模型随意决定“哪个任务现在能做”。

**核心知识：**

- 使用 `depends_on: list[int]` 表达前置任务。
- 校验依赖 ID 是否存在、是否自依赖、是否形成环（DAG Validation）。
- 区分 `PENDING`、`IN_PROGRESS`、`COMPLETED`、`BLOCKED`；定义 `get_ready_tasks()`。
- 验证任务状态迁移是否满足依赖约束；明确串行优先，不急于实现并行。
- 理清 `set_plan()`、`validate_plan()`、`update_task()`、Agent Loop 各自的责任。

**实践：** 设计“分析模块 → 拆分文件与检查引用 → 运行测试”的任务图，并实现依赖合法性校验与就绪任务筛选。

**验收：** 不存在的依赖、循环依赖、提前启动任务均被阻止；合法任务能按依赖顺序执行；有针对性的自动化测试。

### 第 23 课：Dependency-aware Replanning / Scheduling

**目标：** Replan 不只是重写 Todo 列表，而要在失败或范围变化后保持依赖与执行证据的一致性。

**核心知识：**

- 任务失败后下游任务应如何等待、阻塞或解除阻塞。
- Replan 时保留已完成任务及其有效验证证据。
- 重新映射任务 ID 与依赖关系；避免悬空依赖和循环。
- 区分“新增信息导致的合理 Replan”与“无进展的重复 Replan”。

**实践：** 构建中途失败后重新规划的场景，对比修改前后的 Plan DAG。

**验收：** 已完成的成果不会被错误清零；受阻的后续任务不会提前执行；无效 Replan 被拒绝并留下可诊断原因。

### 第 24 课：Runtime Lifecycle / Termination Protocol

**目标：** 避免出现“代码基本改完，Agent 却反复测试、反复更新任务、执行几百步不退出”的情况。

**核心知识：**

- 定义 `COMPLETE`、`INCOMPLETE`、`BLOCKED`、`STOPPED` 等清晰终态／中间态。
- 区分 Todo 完成、Requirement 满足、Verification Evidence 有效三种条件。
- 定义真实进展：目标满足程度、代码变更、有效验证证据；不将单纯 `update_task` 视为实质进展。
- 设计无进展预算、最大步数、失败恢复与终止优先级。
- 在 Runtime 层执行完成判定，模型负责提供信息而非最终裁决。

**实践：** 重放循环场景：目标已实现但模型继续重复 `git diff`、`pytest` 或状态更新。

**验收：** 成功、不可继续、无进展、预算耗尽均能正确停止；返回结构化原因，而不无限继续。

### 第 25 课：Checkpoint / Resume / State Persistence

**目标：** 让 Agent 在进程中断后，能够恢复任务，而不是从头开始或误用过期状态。

**核心知识：**

- 哪些状态需要保存：Goal、Requirements、Plan、Todo 状态、预算、工作区修订、验证证据及简要历史。
- JSON 等简单持久化结构、原子写入、版本号及恢复兼容性。
- 恢复时如何检测源文件变化、外部修改及失效证据。
- 不自动重放有副作用的工具操作；区分已执行、执行中与未知结果。

**实践：** 任务运行中保存 Checkpoint → 退出程序 → 重新加载 → 继续执行。

**验收：** 重启后能恢复合理进度；源码变更后重新验证证据；不会未经确认再次删除或写入文件。

### 第 26 课：Tool Safety / Human-in-the-loop

**目标：** 把工具执行从“可以调用”升级为“权限明确、可确认、可审计、可恢复”。

**核心知识：**

- 只读工具、写入工具、破坏性工具的权限分类。
- `delete_file` 的工作区路径约束、符号链接风险、存在性与删除确认。
- `ask_user` 的使用时机：需求不明确、关键决策需要用户选择、危险操作确认。
- `run_command` 的副作用识别：外部脚本可能修改文件，并影响 `workspace_revision` 与 Verification Evidence。
- 用户拒绝、超时、中断的明确处理策略。

**实践：** 测试删除工作区文件、越界路径、用户拒绝操作、脚本侧写文件等场景。

**验收：** 危险操作需要授权；拒绝后不再重复尝试绕过；工作区变更可追踪；行为日志可复盘。

### 第 27 课：Agent Evaluation / Regression Testing

**目标：** 通过可重复的实验判断一次 Agent 修改是否真正改善了成功率、效率和可靠性。

**核心知识：**

- 区分任务成功率、测试通过率、模型声明完成、Runtime 判定完成。
- 评估 `model_turns`、`tool_calls`、重复调用、失败调用、Token、耗时、成本与恢复次数。
- 使用相同任务、相同初始代码、相同模型配置进行对比；理解模型随机性。
- 建立 Failure Taxonomy：工具失败、规划失败、需求误判、验证缺失、终止失败等。
- 如何回归测试 Agent 核心机制与真实 Coding Task。

**固定回归任务集（初版）：**

1. 修改一个简单函数并运行测试。
2. 新建文件并更新引用。
3. 拆分模块但保持旧接口兼容。
4. 合并模块并删除旧文件。
5. 制造测试失败并要求 Agent 修复或明确报告阻塞。
6. 构造依赖任务、权限拒绝或中途恢复场景。

**验收：** 每次关键代码调整都有可复现的对照结果；不再仅凭“步数变少”判断优化成功；能够生成单次质量报告。

## 四、每节课的统一学习流程

1. **原理**：先讲概念、为什么需要、与已有模块的关系。
2. **架构与调用链**：明确数据结构、谁调用谁、状态如何变化。
3. **最小 Demo**：自己实现关键算法；必要时手写 30～100 行核心代码。
4. **工程实现**：借助 OpenCode 处理跨文件修改、类型定义、注册、测试等重复工作；人工 Review 差异。
5. **测试与日志**：先跑单元测试，再跑固定 Coding Task，记录行为和结果。
6. **知识复盘**：能够不看代码解释核心原理、关键边界及失败场景。

**学习节奏：** 每课以理解和验收为结束条件，不因次要代码风格问题延长课程。可以先做最小实现，将非核心优化记录到 Backlog。

## 五、Mini Coding Agent 核心架构的毕业标准

完成第 27 课后，逐项检查：

- [ ] 可以表达任务依赖，检测无效依赖和循环，并禁止错误执行顺序。
- [ ] 失败、阻塞和 Replanning 不会破坏已完成结果或依赖关系。
- [ ] 成功、阻塞、无进展、预算耗尽时都能正确终止并说明原因。
- [ ] 中断后能够恢复关键任务状态，且不会误用过期验证证据。
- [ ] 工具权限、用户确认、副作用和拒绝流程有可执行测试。
- [ ] 有固定回归任务集，能够评价成功率、质量、成本和效率。
- [ ] 至少成功完成一项真实的模块重构，并检查变更范围和测试覆盖率。

> **完成边界：** 教学版核心架构完成，并不要求与 OpenCode 功能对等，也不要求立即实现容器沙箱、分布式任务、并行执行或生产级服务治理。

## 六、完成后进入 MCP：建议的下一阶段

**原则：完成以上 6 课后，正式进入 MCP；不再无限期停留在 Mini Coding Agent 上。**

MCP 的学习顺序建议为：

1. **MCP 基础与架构**：Host、Client、Server、Transport、Tools、Resources、Prompts。
2. **独立 MCP Server Demo**：用 Python 暴露文件读取、搜索等最小工具。
3. **MCP Client 集成**：让 Mini Coding Agent 通过 MCP Client 发现和调用工具，比较本地 Tool Runner 与 MCP 的区别。
4. **MCP 安全与工程化**：权限、鉴权、连接生命周期、错误处理与工具结果管理。

随后再学习 RAG、Memory、Workflow、Multi-Agent 和主流 Agent 框架（如 OpenAI Agents SDK、LangGraph）。Mini Coding Agent 届时可作为实验载体，而不是所有知识点都必须直接集成进去。

## 七、学习记录模板

每节课结束后，追加以下记录：

```markdown
### 第 XX 课学习记录
- 日期：
- 本课原理（用自己的话说明）：
- 修改的模块与调用链：
- 关键设计选择及原因：
- 单元测试结果：
- 固定任务测试结果：
- 遇到的 Bug 与修复：
- 仍不理解的内容：
- 是否达到本课验收标准：是 / 否
```

**下一步：从第 22 课 Task Dependency / Execution Ordering 开始。**
