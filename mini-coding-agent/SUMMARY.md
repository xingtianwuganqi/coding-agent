# 总结

## 1.工具调用 Tool Calling
```
1. list_files
2. read_file
3. write_file
4. replace_text
5. run_command
```

## 2.权限控制 Tool Permission / Human in the loop


### 2.1 工具权限控制
### 2.2 AI 自动执行命令控制
```
1.TOOL_PERMISSION
2.Permission
3.classify_command
4.在调用工具或者执行命令时，提前询问

```
### 2.3 Command Allowlist
```
1.SHELL_OPERATORS
2.classify_git_command、classify_uv_command
2.禁止直接调用shell命令，而是单独调用git、uv命令，然后分开判断git、uv命令，再单独判断权限
```

## 3.planing

### 3.1 先新增TaskStatus、TodoItem、AgentState, AgentState是一次任务的总的状态

### 3.2 新增set_plan,update_task,get_plan的Tool和实现，

### 3.3 Agent loop 中，AI制定计划，通过set_plan转化成Agent runtime中可观察的计划状态,完成计划后，ai调用update_task,更新计划状态

## 4.Task Completion Guard.不再完全相信模型说“任务完成了”，而是由 Runtime 检查 Todo 是否真的全部完成

### 4.1 之前没有工具调用直接结束，现在开始判断任务中是否有未完成的任务

### 4.2定义完成主要看todos中的任务是否都完成了，都完成了，就结束，没有完成或者有禁止的就继续
```
1.CompletionStatus
2.get_completion_status
3. 判断是否结束：
completion_status = get_completion_status(
                state
            )

            if completion_status == CompletionStatus.COMPLETE:
                return message.content or ""

            if (
                completion_status == CompletionStatus.BLOCKED
                and allow_blocked_final == True
            ):
                return message.content or ""


            if completion_status == CompletionStatus.INCOMPLETE:
                unfinished = get_unfinished_tasks(state)

                unfinished_text = "\n".join(
                    f"- {todo.id}. {todo.content}"
                    for todo in unfinished
                )

                input_items.append(
                    {
                        "role": "user",
                        "content": (
                            "You attempted to finish the task, "
                            "but the todo plan is not complete.\n\n"
                            "Remaining tasks:\n"
                            f"{unfinished_text}\n\n"
                            "Continue working on the remaining tasks. "
                            "Do not provide a final answer yet."
                        ),
                    }
                )

                continue

            if completion_status == CompletionStatus.BLOCKED:
                allow_blocked_final = True
                input_items.append(
                    {
                        "role": "user",
                        "content": (
                            "Some tasks are blocked.\n\n"
                            f"{format_plan(state)}\n\n"
                            "Provide a final answer "
                            "explaining completed work "
                            "and blocked tasks."
                        ),
                    }
                )

                continue
```

## 5 Evidence Guard：确保任务是实际完成了，而不只是状态改变

### 5.1 新增EvideceType，修改set_plan,现在set_plan不止返回plan list[str]了，还会携带是否需要tests_passed, 需要test的，最后执行完以后，runtime会验证是否执行成功，returncode是不是0，是0就是命令执行通过了，最后在给llm看
```
1.EvideceType
2.set_plan
3.execute_tool中（record_command_result、format_command_result）
4.update_task()
如果当前状态完成了，
如果这个 Todo 有证据要求：required = todo.required_evidence
但runtime中没有记录required not in state.evidence
直接拒绝

```

### 5.2 Evidence 必须由受信任的 Runtime 根据 Tool 执行结果产生。
```
run_command("uv run pytest")
↓
真实 subprocess
↓
returncode == 0
↓
Runtime 自动记录 TESTS_PASSED
```

## 6.Evidence Invalidation / Dirty State（证据失效 / 脏状态）只要代码发生修改，就删除旧的测试证据和diff证据

### 6.1 state中的evidence有值之后，在write_file和replace_text后，代码发生了变化，要清空掉state中的evidence，让再次进行tests和diff
```
1.invalidate_evidence
2.FileOperationResult
3.write_file和replace_text返回FileOperationResult
4.execute_tool后，如果changed，则要清空state的evidence，下一次会再次验证

```

## 7.Versioned Evidence（带版本的证据）。

### 7.1 给AgentState添加workspace_revision，并且将AgentState中的evidence改成dict，当执行write_file或者replace_text后，会将workspace_revision+=1，在执行完run_command后，会根据命令更新state.evidence[EvidenceType.TESTS_PASSED] = state.workspace_revision,state.evidence[EvidenceType.TESTS_PASSED]和state.workspace_revision相等，就说明当前版本更改经过了验证，如果在update_task时不想带，就告诉llm，没有执行验证命令，需要重新执行run_command,进行验证，验证之后state.evidence[EvidenceType.TESTS_PASSED] = state.workspace_revision

```
1.AgentState:  workspace_revision
2.evidence: dict[EvidenceType: int] = field(default_factory=dict)
3.mark_workspace_changed
4.has_valid_evidence
5.execute_tool中mark_workspace_changed
6.run_command中record_command_result
7.update_task中not has_valid_evidence
```

## 8.Context Management(上下文治理)

### 8.1
| 东西            | 代表什么                  |
| ------------- | --------------------- |
| Workspace     | 真实世界现在是什么样            |
| AgentState    | Runtime 知道任务做到哪、证据是什么 |
| Model Context | 这一轮 LLM 能看到什么         |

Workspace 是事实源，State 是工作记忆，Context 是模型当前看到的材料。

### 8.2 将最近的数据进行保留，比如说保留8个，将，当前的AgentState进行描述，然后在传给llm

```
1.build_model_input(最近的几条数据)
2.format_runtime_state (当前runtime的状态描述)
3.runtime_instructions, runtime中对agent state的拼接和描述
4.
```



## 9.Context Compression / Task Summary（上下文压缩）

### 9.1

1. Recent Context
   最近几轮具体发生了什么

2. Runtime State
   当前做到哪、Evidence、Revision

3. Task Summary
   过去发生过但以后仍然有价值的结论
```
1.task_summary
```

### 9.2 将history_list中前端的内容和state.summary进行合并总结，然后将总结的内容拼接进build_model_input中，发送给llm
```
1.turn_to_text
2.history_to_text
3.summarize_history
4.compress_history
5.build_model_input

```

## 10.Tool Output Truncation / Large File Handling：比如 pytest 输出 5 万字符、read_file 打开一个 1MB 文件时，为什么即使只有最近 6 Turn 也会把 Context 撑爆，以及 Coding Agent 应该怎样按行读取、搜索代码和截断工具输出。

### 10.1 Tool Output Truncation 控制命令输出的大小
```
1.trncate_text、truncate_tail
2.对format_command_result中调用命令返回的结果进行裁剪，

```

### 10.2 Large File Handling 不再一次读取整个大文件，而是按行读取 + 搜索定位
```
1.修改read_file，改为按范围读取
2.新增search_text Tool
```

## 11. Context Budget / Token Budget

### 11.1 动态计算token数量.
规定好总的token数，总token数减去输出token数，安全边界token数，就是输入token数。
输入token数又可以分为静态token数，包括system_prompt,task,tools, summary, runtime_state等，还有recent_state，就是最近的内容，裁剪出来的内容，这个内容是动态的，就是输入token数减去静态token数，就是最近动态的token数，尽可能多的最近动态

```
1.estimate_tokens
2.get_input_token_budget
3.estimate_tools_tokens
4.estimate_fixed_context_tokens
5.estimate_turn_tokens
6.select_recent_turns
7.get_recent_context_budget
8.get_recent_context_budget
9.build_model_input
10.print_context_debug
```


## 12 重构项目

### 12.1遇到的问题，输入重构需求后，ai一直不停的阅读，不列计划，不做工作，陷入阅读循环

### 12.2 解决办法
```
1.调整SYSTEM_PROMPT,让尽快列计划开始执行
2.AgentState中新增pre_plan_inspection_count、consecutive_retrieval_count、recent_tool_calls
3.新增guard_tool_call、tool_signature方法，在execute_tool方法中添加guard_tool_call判断，如果连续调用，或者连续阅读搜索，就会告诉llm,尽快制定计划然后执行

```

## 13.Agent Observability - 运行轨迹与指标

### 13.1 AgentMetrics 数据结构

新增 `src/mini_coding_agent/agent_metrics.py`，用 `@dataclass` 定义 `AgentMetrics`，集中记录一次 agent run 的运行指标：

```
1.model_turns               模型调用轮数
2.tool_calls                工具调用总次数
3.tool_calls_by_name        按工具名分类的调用次数（dict）
4.blocked_tool_calls        被 guard 拦截的工具调用次数（含 guard_tool_call 与 guard_recent_tool_call 两处拦截）
5.context_compressions      上下文压缩次数
6.tests_run                 执行测试的次数
7.plan_created_turn         在第几轮创建计划
8.first_write_turn          在第几轮第一次写文件
9.start_time / end_time     运行起止时间
```

### 13.2 记录方法

```
1.record_tool_call(name)：tool_calls +1，并把次数累加到 tool_calls_by_name
2.finish()：记录 end_time
3.duration 属性：end_time(或当前时间) - start_time
4.print_summary()：打印 Agent Run Metrics，包括轮数、工具调用、工具使用分布、被拦截次数、压缩次数、测试次数、建计划轮次、首次写入轮次和耗时
5.end_runing()：finish() + print_summary() 的组合
6.is_test_command(command)：通过关键字（pytest / unittest / npm test / go test / swift test）判断命令是否为测试命令
```

### 13.3 与运行流程的集成

```
1.agent.run_agent 中创建 metrics = AgentMetrics()
2.while 循环每轮 metrics.model_turns += 1，超过 MAX_MODEL_TURN 时调用 metrics.end_runing() 再返回
3.agent 正常结束/异常退出时都调用 metrics.end_runing() 输出汇总
4.compress_history 接收 metrics，每次压缩 history 时 metrics.context_compressions += 1
5.ToolRunner 的 execute_tool 接收 metrics：
  1) 开头调用 metrics.record_tool_call(name) 记录工具调用
  2) guard_tool_call 或 guard_recent_tool_call 拦截时 metrics.blocked_tool_calls += 1
  3) write_file / replace_text 首次写入时记录 metrics.first_write_turn = metrics.model_turns
  4) set_plan 首次调用时记录 metrics.plan_created_turn = metrics.model_turns
  5) run_command 时用 is_test_command 判断，是测试命令则 metrics.tests_run += 1
6.以上各记录点共同形成可观测的运行轨迹
```


##  14.Agent Run Trace，Agent 运行追踪

### 14.1 数据结构

```
1.TraceEvent：单条跟踪事件
   1) turn：事件发生的轮数（对应 metrics.model_turns）
   2) event_type：事件类型，取值 model_turn / tool / blocked / blocked_recent / compression
   3) name：事件名称，如 call_model 或具体工具名
   4) detail：事件详情，默认空字符串
   5) timestamp：事件时间，未传入时由 __post_init__ 用 datetime.now() 自动生成（精确到秒）
2.AgentTrace：跟踪容器
   1) events：TraceEvent 列表，使用 field(default_factory=list) 初始化
```

### 14.2 AgentTrace 方法

```
1.record(turn, event_type, name, detail)：构造一个 TraceEvent 并追加到 events 中
2.print_trace()：打印 Agent Run Trace
   1) 先输出 "===== Agent Run Trace ====="
   2) 逐条打印 "[Turn {turn}] {EVENT_TYPE} {name}"（event_type 转大写）
   3) detail 非空时另起一行缩进打印
   4) 最后输出 "==========================="
```

### 14.3 与运行流程的集成

```
1.agent.run_agent 中创建 trace = AgentTrace()
2.while 循环每轮先 trace.record(turn=metrics.model_turns, event_type="model_turn", name="call_model")
3.trace 作为参数传给 compress_history 和 execute_tool，在内部继续记录事件
4.退出时统一调用 trace.print_trace()：达到 MAX_MODEL_TURN、任务完成（COMPLETE）、允许 BLOCKED 结束等情况
5.compress_history 接收 trace，压缩 history 时记录 event_type="compression" 事件
6.ToolRunner 的 execute_tool 接收 trace：
   1) 开头记录 event_type="tool" 事件，detail 为 str(arguments)[:200]
   2) guard_tool_call 拦截时记录 event_type="blocked"，detail 为拦截错误信息
   3) guard_recent_tool_call 拦截时记录 event_type="blocked_recent"，detail 为拦截错误信息
7.以上各记录点按轮次串成一条可读的 Agent 运行轨迹，与 AgentMetrics 的数值汇总互补
```

## 15 Progress / Stagnation Detection 进度和停滞检测

### 15.1 将这些作为真正的Progress:
```
成功创建 Plan

Todo 状态发生变化

成功修改文件

workspace_revision 增加

成功完成验证

Todo 被标记 blocked，并给出原因

```

### 15.2 状态字段（AgentState）
```
1.last_progress_turn：最近一次真正取得进展发生在第几个 model turn，初始为 0
2.stagnation_warnings：当前连续停滞的告警计数，取得进展时清零
3.MAX_STAGNANT_TURNS = 5：允许的最大停滞轮数（planing.py 常量）
```

### 15.3 核心函数（planing.py）
```
1.record_progress(state, turn)
   - 记录进展：state.last_progress_turn = turn
   - 同时把 state.stagnation_warnings 重置为 0
   - 调用点：update_task 中 Todo 状态真正变化时；
     tool_runner / tools 中成功写入、完成验证等产生进展的动作处
2.is_stagnating(state, current_turn)
   - 还没有 plan（todos 为空）时直接返回 False
   - stagnant_turns = current_turn - state.last_progress_turn
   - 当 stagnant_turns >= MAX_STAGNANT_TURNS 时判定为停滞
3.build_stagnation_feedback(state)
   - 取出第一个 IN_PROGRESS 的 Todo 作为 current task（无法找到时用占位文本）
   - 返回 RUNTIME NOTICE，提示已停滞、给出当前任务，
     要求用已收集的信息采取下一步具体行动，不要继续无目的调研
```

### 15.4 与运行流程的集成（agent.run_agent）
```
1.每轮开始先调用 is_stagnating(state, current_turn=metrics.model_turns) 判断
2.判定停滞时：
   1) trace.record(event_type="stagnation", name="progress_guard",
      detail=f"last_progress_turn={state.last_progress_turn}")
   2) metrics.stagnation_warnings += 1
   3) build_stagnation_feedback 生成反馈，作为一条 user 消息追加进 history_turns
3.反馈以 user 消息形式注入下一轮上下文，驱动模型停止无效调研、回到具体行动
4.metrics.stagnation_warnings 纳入 AgentMetrics（agent_metrics.py），供运行汇总观测
```


## 16. Failure Recovery & Retry Policy。 故障恢复与重试策略

### 16.1 新增ToolResult，将工具调用execute_tool和其他工具调用返回的数据统一成ToolResult

新增 `tool_result.py`，定义统一的结果数据结构（`@dataclass ToolResult`）：
```
字段：
  success: bool              是否成功
  content: str = ""          正常输出内容
  error: str = ""            错误信息（失败时填充）
  return_code: int | None    命令返回码（可选）

构造方法：
  ToolResult.ok(content, return_code)   成功结果
  ToolResult.fail(error, content, return_code)  失败结果
```
`execute_tool` 及各类工具调用的返回值统一收敛为 `ToolResult`，下游只需判断 `result.success`，失败原因走 `result.error`。

### 16.2 新增failure_recovery，
```
1.FailureKind 错误类型类（str Enum）
   TRANSIENT 短暂错误 / NOT_FOUND 未找到 / INVALID_ARGUMENT 参数错误 /
   PERMISSION 权限错误 / CONFLICT 冲突 / TEST_FAILED 测试失败 /
   COMMAND_FAILED 命令失败 / UNKNOWN 未知
2.RecoveryAction 恢复的行为类（str Enum）
   RETRY 重试 / INSPECT 检查 / CHANGE_STRATEGY 变更策略 / BLOCKED 屏蔽
3.FailureDecision 失败的决策类
   kind + action + reason + auto_retry(默认False)
4.classify_failure 对错误进行分类
   先 raise 拦截 success 的结果；再对 error 文本做小写关键字匹配，
   依次判定 transient → permission → not_found → replace_text 冲突 →
   invalid_argument；最后按 tool_name=run_command 且 is_test_command 区分
   TEST_FAILED / COMMAND_FAILED；都不匹配则 UNKNOWN
5.decide_recovery 对各个类型的错误决定怎样恢复
   failure_count >= 3 → BLOCKED（同一错误反复出现，禁止再重复）
   TRANSIENT：安全工具(SAFE_AUTO_RETRY_TOOLS)且首次 → RETRY(auto_retry=True)，
              否则 CHANGE_STRATEGY
   NOT_FOUND / CONFLICT → INSPECT
   INVALID_ARGUMENT / TEST_FAILED / COMMAND_FAILED / UNKNOWN → CHANGE_STRATEGY
   PERMISSION → BLOCKED
6.make_failure_signature 构建错误签名
   f"{tool_name}:{failure_kind}:{sha1(arguments)[:12]}"
   例：read_file:not_found:a87f153922ab
7.record_failure 记录错误并返回错误个数
   签名与上次相同则 consecutive_count += 1，否则重置为 1
8.clear_failure_streak 清空错误个数，在tool调用成功后清空
   将 last_signature 置 None、consecutive_count 置 0
9.handle_tool_failure，处理错误，并返回决策和错误个数
   内部串联 classify_failure → make_failure_signature → record_failure →
   decide_recovery，返回 (decision, failure_count)
10.build_failure_result 构建失败原因
   在原始 error 后追加 RUNTIME RECOVERY 段（failure_kind / recovery_action /
   repeated_count / instruction），返回 ToolResult.fail(...)
11.execute_tool_with_recovery，故障恢复截止（位于 tool_runner.py）
   这是新增的包装函数，不是“把 run_agent 里的 execute_tool 改名”：
   execute_tool 仍然存在（tool_runner.py），execute_tool_with_recovery 在其
   之上叠加失败分类 / 计数 / 自动重试 / 结果构建；
   run_agent（agent.py）改为调用 execute_tool_with_recovery 来执行工具。
```

### 16.3 详细的故障恢复机制
```
入口：execute_tool_with_recovery(name, arguments, state, metrics, trace, current_turn)

1.第一次执行：metrics.tool_executions += 1，调用 execute_tool
2.成功：clear_failure_streak(state) 清空 failure，直接返回结果
3.第一次失败：
   1) metrics.tool_failures += 1
   2) handle_tool_failure → (decision, failure_count)
   3) trace.record(event_type="tool_failure", detail=kind/action/count/error前200字符)
4.是否自动重试：仅当 decision.auto_retry 为真（TRANSIENT + 安全工具 + 首次）
   1) metrics.auto_retries += 1
   2) trace.record(event_type="retry", detail="Runtime automatic retry")
   3) metrics.tool_executions += 1，再次 execute_tool
5.重试成功：metrics.recovered_failures += 1，clear_failure_streak，
            trace.record(event_type="recovered")，返回 retry_result
6.重试仍失败：metrics.tool_failures += 1，再次 handle_tool_failure 得到
            second_decision/second_count；second_count >= 2 时
            metrics.repeated_failures += 1；返回 build_failure_result
7.不自动重试（auto_retry 为假）：failure_count >= 2 时
            metrics.repeated_failures += 1；返回 build_failure_result
            （决策/签名/计数在 3 步已经算好，此处直接构建失败结果）
```

### 16.4 观测与状态
```
- state.failure（AgentState）保存 last_signature 与 consecutive_count，
  用于跨轮次的“同一错误连续出现”判定
- 涉及指标（AgentMetrics）：
  tool_executions / tool_failures / auto_retries /
  recovered_failures / repeated_failures
- 涉及 trace 事件：tool_failure / retry / recovered
- 失败结果以 ToolResult.fail 返回，附带 RUNTIME RECOVERY 文本，
  作为工具输出注入模型上下文，驱动其检查、改参或换策略
```


## 17. Verification / Task Completion Protocol 验证/任务完成协议

### 17.1 任务完成不在只靠状态变成COMPLETION，而是看是否测试完成，要有证据证明完成了
```
          Task Completion
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
    Plan       Work     Verification
   Complete    Done        Passed
```

### 17.2 版本号与验证记录
```
- AgentState 新增两个版本号：
  - workspace_revision：任何文件修改都 +1（tool_runner 中记录）
  - code_revision：只有修改“代码文件”才 +1
    （write_file / replace_text 时用 is_code_file 判断后缀，
     命中 CODE_EXTENSIONS 才增加）
- AgentState 新增 verification（VerificationState），内部维护
  records: list[VerificationRecord]
- VerificationRecord 字段：
  kind / success / workspace_revision / code_revision / source / detail
  - kind 取值（VerificationKind）：
    diff / test / build / syntax / readback
  - 记录时会同时快照当时的 workspace_revision 与 code_revision，
    即“这次验证针对的是哪一个修订版本”
```

### 17.3 命令如何被记录为验证
```
- run_command 执行后调用 record_command_verification(state, command, result)
- success 由 result.return_code == 0 判定
- 按命令文本分类（命中即记录并返回，按此顺序）：
  1. is_test_command   → TEST
  2. is_diff_command   → DIFF   （命令以 "git diff" 开头）
  3. is_build_command  → BUILD  （go build / npm run build / cargo build 等）
  4. is_syntax_command → SYNTAX （py_compile / compileall）
  5. 都不命中则不记录
```

### 17.4 完成判定 evaluate_completion
```
依次检查以下 requirements，全部满足才算 complete：
1. plan_exists      ：state.todos 非空
2. todos_completed  ：所有 todo 都是 COMPLETED
3. no_blocked_tasks ：不存在 BLOCKED 的 todo
4. diff_verified    ：仅当 state.changed_files 非空时检查
                      has_current_diff_verification(state)
                      即存在成功的 DIFF 记录且其
                      workspace_revision == 当前 workspace_revision
5. code_verified    ：仅当 state.code_revision > 0 时检查
                      has_successful_code_verification(state)
                      即存在 kind ∈ {TEST, BUILD, SYNTAX}、success 为真、
                      且 code_revision == 当前 code_revision 的记录

注：DIFF 记录只用于 diff_verified，不能当作代码验证；
    代码验证必须是 TEST/BUILD/SYNTAX 且成功。
    只要 code_revision 前进（又改了代码），旧的成功验证即失效，
    必须重新跑测试/构建/语法检查。
未满足时通过 build_completion_feedback 生成
"RUNTIME COMPLETION GUARD: ..." 文本反馈给模型。
```

## 18 Task Requirement Extraction 任务需求提取

```
实现文件：src/mini_coding_agent/requirements.py

目的：把用户请求里“明确的要求”抽取成结构化需求，
      记录到 AgentState.requirements 并锁定，
      供上下文提示 / 运行时守卫 / 完成判定使用。
```

### 18.1 需求类型 RequirementKind
```
- must_change      ：用户明确要求某个文件/模块必须被修改
                     （target 可为 None，表示“必须发生文件修改”即可）
- must_not_modify  ：用户明确禁止修改某个文件/目录（必须有 target 路径）
- must_verify      ：用户明确要求某个验证
- soft_constraint  ：runtime 暂时无法机械证明的需求（始终视为满足）

结构：
- TaskRequirement：id / kind / description / target /
                   verifier / command_contains
- RequirementState：items: list[TaskRequirement] + locked: bool
- RequirementCheck：requirement_id / satisfied / reason
```

### 18.2 提取与锁定 set_requirements
```
set_requirements(state, requirements) -> ToolResult
- 若 state.requirements.locked 为真：直接失败
  （"Requirements are already locked and cannot be replaced."，
   需求不可替换、不可再次调用）
- 逐个解析 requirements（list[dict]）：
  1. kind 必须能转成 RequirementKind，否则失败
     "Invalid requirement kind: ..."
  2. description 不能为空，否则失败
     "Requirement description cannot be empty"
  3. must_not_modify 必须带 target，否则失败
     "must_not_modify requires a target path."
     （must_change 的 target 可为空，表示“必须发生文件修改”即可）
  4. must_verify 的 verifier 必须在
     {test, build, syntax, diff} 内，否则失败
     "must_verifier requires verifier to be one of: ..."
  5. id 按列表顺序从 1 开始编号（index + 1）
- 全部合法后：写入 state.requirements.items 并置 locked = True
- 返回 ToolResult.ok("Recorded N requirements.")
注：解析在内存的 parsed 列表中完成，只要有一条非法就立即
    返回失败，state.requirements.items 不会被写入、locked 也不会
    被置为 True（即“整体失败、不落库、不锁定”）。
```

### 18.3 需求上下文 build_requirement_context
```
- 未锁定（locked 为 False）时返回
  "Task requirements have not been extracted yet"
- 已锁定则逐条输出：
  "TASK REQUIREMENTS:" 开头，之后每行
  "{id}. [{kind}] {description} | target=... | verifier=... | command_contains=..."
  （target / verifier / command_contains 有值时才拼接对应片段）
```

### 18.4 路径匹配 path_matches_target
```
- normalize_path：
  str(PurePosixPath(path)) 归一化（如合并多余分隔符、
  去掉结尾 "/"、把 "." 解析掉），再把反斜杠替换为正斜杠，
  最后 lstrip("./")（去掉开头连续的 '.' 与 '/' 字符）
- path_matches_target(path, target)：
  1. 归一化后两者完全相等 → True
  2. 否则 target 是 path 的祖先目录（target in path.parents）→ True
  （即命中目标路径本身或其子路径）
```

### 18.5 运行时守卫 guard_requirement_constraints
```
guard_requirement_constraints(name, arguments, state) -> str | None
- 仅对修改类工具生效（MUTATION_TOOLS）：
  write_file / replace_text / delete_file / rename_file
  （其中 delete_file / rename_file 目前仅在集合中预留，
   实际工具列表如 list_files/read_file/write_file/replace_text/run_command）
- 非修改类工具：直接放行（返回 None）
- 从 arguments 取 "path"；若无 path 参数：直接放行（返回 None）
  （因此 rename_file 的 source 之类参数不会被检查）
- 遍历 must_not_modify 需求（跳过无 target 的），
  若 path_matches_target(path, target) 命中，则返回
  "RUNTIME REQUIREMENT GUARD: modifying '<path>' would violate
   requirement <id>: <description>"
- 命中首个违规即返回该字符串；无命中返回 None
- 用于在模型动手改被禁路径前拦截（只查 must_not_modify）。
```

### 18.6 验证类需求判定 has_required_verification
```
- expected_kind = requirement.verifier
- 倒序扫描 state.verification.records，逐条过滤：
  1. success 为真
  2. record.kind.value == expected_kind
  3. 版本匹配：
     - verifier == "diff"：record.workspace_revision 必须
       == 当前 state.workspace_revision
     - 其它（test/build/syntax）：record.code_revision 必须
       == 当前 state.code_revision
  4. 若需求带 command_contains：将其小写后必须出现在
     record.source（小写）中
- 命中即返回 True，否则 False
（与 §17 的版本失效机制一致：代码又改了，旧验证即失效）
```

### 18.7 单项需求评估 evaluate_requirement
```
evaluate_requirement(state, requirement) -> RequirementCheck
- must_change：
  - target 为空：satisfied = bool(state.changed_files)（发生过任意改动）
    reason = "Workspace change required."
  - 有 target  ：satisfied = 任一 changed_files 命中 path_matches_target
    reason = "Required target '<target>' must be changed"
- must_not_modify：
  satisfied = not 任一 changed_files 命中 path_matches_target(target)
  （即被禁路径没被动过）
  reason = "Forbidden target '<target>' must remain unchanged"
- must_verify：
  satisfied = has_required_verification(...)
  reason 固定为“当前版本还没有通过的验证”一类的提示
  （"Required verification has not passed for the current revision."，
   注意该 reason 无论 satisfied 真假都会带上）
- soft_constraint：
  始终 satisfied = True，仅保留在上下文中提示、不做机械证明
```


## 19 Goal/Plan/Requirement Separation + Replaning

把一次任务的“三件事”拆成彼此独立、职责清晰的状态，避免模型在执行途中
偷换目标或改写约束：
```
- Goal        : 这次到底要达成什么           （最上层的目标，锁一次）
- Plan        : 打算分几步做                  （可执行、可调整的执行策略）
- Requirements: 过程中必须一直满足的约束       （锁定后不可放松）
```
三者的生命周期不同：
- Goal【锁定后不可变】：锁定之后就是整个任务的锚点，不允许被覆盖或弱化。
- Requirements【锁定后不可变】：是“必须保持成立”的约束，replan 也不能删改。
- Plan【允许按规则替换】：只是当前执行策略，当证据表明原策略不再有效时
  可以通过 replan 整体替换。

这样做的好处：模型可以修正“怎么做”（Plan），但改不了“做什么”（Goal）
和“必须守住什么”（Requirements），从而在长任务里保持方向不跑偏。

### 19.1 目标锁定 set_goal
```
set_goal(state, objective) -> ToolResult
1. objective = objective.strip()
2. 若 state.goal.locked 已为 True：
   返回失败 "Goal is already locked and cannot be replaced."
   （Goal 只能设置一次，之后无法覆盖）
3. 若 strip 后 objective 为空：
   返回失败 "Goal cannot be empty."
4. 否则写入 state.goal.objective = objective
   并置 state.goal.locked = True
5. 返回成功 content = "Goal locked: {objective}"
```
要点：Goal 属于“一次性锁定”的字段，set_goal 成功即不可回退，
后续 replan 必须在 goal 已锁定的前提下才被允许。

### 19.2 GoalState / RequirementState 状态
```
@dataclass GoalState:
  original_request: str   # 初始用户请求原文
  objective: str = ""     # 抽取出的目标
  locked: bool = False    # 是否已锁定

RequirementState（见 §18）:
  requirements: list[Requirement]
  locked: bool            # 需求抽取完成后锁定
```
AgentState 同时持有 plan、goal、requirements（requirements 默认
RequirementState()），三者分离存放，互不覆盖。

### 19.3 replan 前置校验
```
replan(state, items, reason, explanation, current_turn) -> ToolResult
按顺序校验，任一条失败即返回，且不改动当前计划：
1. state.plan.replan_count >= MAX_REPLANS：
   失败 “Maximum replanning limit has been reached.
         Do not keep replacing the plan.
         Resolve the current blocker or mark the task blocked.”
   （防止无限重规划，逼模型去解决阻塞或直接标记 blocked）
2. state.plan.revision == 0：尚没有计划
   失败 "No existing plan. Use set_plan first."
3. not state.goal.locked：
   失败 "Cannot replan without a locked goal."
   （Goal 必须已锁定；这也是“Goal 在 replan 中不可变”的体现）
4. not state.requirements.locked：
   失败 "Cannot replan without locked requirements."
   （Requirements 必须已锁定，replan 不得绕过约束）
5. explanation = explanation.strip() 后为空：
   失败 "Replanning requires an explanation."
   （必须说明为什么旧计划不再适用）
```
reason 取 ReplanReason 枚举，如
assumption_invalid / blocked_task / repeated_failure /
stagnation / requirement_conflict 等，用于区分“为什么要重规划”。

### 19.4 replan 新计划校验与替换
```
6. items 为空：失败 "Replacement plan cannot be empty."
7. 逐条把 items 规范化为 TodoItem（先校验，后写入）：
   - 必须是 dict 且 item["content"] 是字符串，
     否则失败 "Plan item must have text content."
   - content = item["content"].strip()，为空则
     失败 "Plan item content cannot be empty."
   - required_evidence 缺省为 "none"；
     非 "none" 时用 EvidenceType(evidence_value) 转换，
     非法值失败 "Invalid evidence type: {evidence_value}"
   - 生成 TodoItem(id=index, content=content,
                   required_evidence=required_evidence)
8. 与当前计划逐条比较 content 列表：
   若与旧计划完全相同，失败
   "Replacement plan is identical to the current plan."
   （不允许“换汤不换药”的空 replan）
9. 全部通过后才真正落地：
   写入新的 todos，递增 replan_count，返回新计划（format_plan）
```
关键设计：**先校验、后变更**。所有失败分支都在修改 state 之前返回，
因此一次失败的 replan 不会破坏当前正在执行的计划。

### 19.5 分离带来的约束保证
```
- Goal 在 replan 中不变：replan 只替换 plan.items，
  永不触碰 state.goal（且要求 goal 已 locked）。
- Requirements 在 replan 中不变：replan 只读 state.requirements.locked，
  不修改任何 requirement；replan 通过 reason=requirement_conflict
  表达“旧计划会违反需求”，而不是去改需求本身。
- Plan 可换但有边界：次数上限（MAX_REPLANS）、非空、内容需变化、
  必须给出解释，避免无意义抖动。
```
```text
模型可以修改“怎么做”（Plan），
但不可以修改“做什么”（Goal）与“必须守住什么”（Requirements）。
```

## 20 v1 Plan Quality / Plan Validation 计划质量和计划验证

计划的“质量”和“合法性”不是靠模型自觉，而是被代码在
set_plan / replan 的入口处强制校验。planing.py 中为此定义了一组
专门的数据结构与常量。

### 20.1 计划相关的数据结构
```
常量（planing.py 顶部）：
- MIN_PLAN_ITEMS = 2    计划不能太碎（至少 2 项）
- MAX_PLAN_ITEMS = 7    计划不能太臃肿（最多 7 项）

PlanStatus(str, Enum)：
  ACTIVE = "ACTIVE" | SUPERSEDED = "superseded" | COMPLETED = "completed"

PlanSnapshot（一份历史计划的快照）：
  revision / items / status / reason / created_turn
  superseded_turn: int | None = None

PlanState（当前计划状态）：
  revision: int = 0                 计划版本号，0 表示“还没有计划”
  items: list[TodoItem]             当前计划
  history: list[PlanSnapshot]       历史计划快照
  replan_count: int = 0             已重规划次数

TodoItem：
  id / content / status(TaskStatus) / note
  required_evidence: EvidenceType | None = None

TaskStatus(Enum)：
  PENDING="pending" | IN_PROGRESS="in_progress"
  COMPLETED="completed" | BLOCKED="blocked"
```

### 20.2 校验结果的两级结构
```
PlanValidationIssue：
  code: str        问题的机器可读代码
  message: str     问题的人类可读描述
  （一个 issue 对应一个具体问题）

PlanValidationResult：
  valid: bool                      整体是否通过
  issues: list[PlanValidationIssue] 所有问题
  （把校验做成“收集问题”的风格：
    不因为第一个错误就中断，
    而是把所有问题一次性返回给模型，
    便于模型一轮内把计划改到位）
```

### 20.3 进入校验的前置条件
```
set_plan 在真正校验 items 之前，先检查三个前置条件：
1. state.goal.locked 为真，否则失败
   "Goal must be locked before creating a plan"
2. state.requirements.locked 为真，否则失败
   "Requirements must be locked before creating a plan"
3. state.plan.revision == 0，否则失败
   "An active plan already exists. Use replan instead of set_plan."
   （已有计划就只能 replan，不能用 set_plan 覆盖）

即：先有锁定的 Goal 和 Requirements，才有资格谈“计划质量”。
```

### 20.4 计划条目的规范化与校验
```
先把 items 里的假值过滤掉：clean_items = [item for item in items if item]
若为空 → 失败 "Plan cannot be empty."

再逐条规范化为 TodoItem（index 从 1 开始）：
- 条目必须是 dict，且 item["content"] 是字符串，
  否则失败 "Plan item must have text content."
- content = item["content"].strip()，为空则失败
  "Plan item content cannot be empty."
- required_evidence 缺省为 "none"；
  非 "none" 时用 EvidenceType(evidence_value) 转换，
  非法值失败 "Invalid evidence type: {evidence_value}"
- 生成 TodoItem(id=index, content=content,
                required_evidence=required_evidence)

校验通过后才写入 state.plan.items / revision，
即“先校验、后落地”，失败的计划不会污染当前状态。
```

### 20.5 required_evidence：把“质量”落到证据上
```
TodoItem.required_evidence 的类型是 EvidenceType（见 evidence.py），
缺省为 None 表示“不强制证据”。

format_plan 输出时，只有 required_evidence 非 None 才追加
  " [requires: {required_evidence.value}]"

对应 AgentState 上的证据记账：
- evidence: dict[EvidenceType, int]      各类型证据的计数
- workspace_revision: int                任何文件修改都会增加
- code_revision: int                     只有代码文件修改才增加

因此“计划质量”在运行时的含义是：
被标记 [requires: ...] 的条目，必须有对应的、且版本不陈旧的
证据才算真正完成（由 has_valid_evidence 判定）。
计划阶段就把 evidence 要求写死，避免完成后才补证。
```

### 20.6 计划质量的三个维度
```
1. 形状（Shape）
   - 非空（Plan cannot be empty.）
   - 条目数量落在 MIN_PLAN_ITEMS=2 与 MAX_PLAN_ITEMS=7 之间
     （既不过碎，也不过臃肿）
   - 每条是带非空文本的 dict
2. 可验证性（Verifiability）
   - 条目可声明 required_evidence（none / tests_passed /
     diff_inspected 等），把“怎么算完成”写进计划本身
   - 证据与 workspace_revision / code_revision 绑定，
     代码一变，旧证据即失效
3. 一致性（Consistency）
   - 计划必须建立在已锁定的 Goal 与 Requirements 之上
   - 已经存在计划时只能用 replan，且 replan 还要求
     “替换计划不能与旧计划完全相同”
     （Replacement plan is identical to the current plan.）
     防止换汤不换药的抖动
```

### 20.7 关键设计取舍
```
- 校验前置（fail fast）：Goal/Requirements 未锁定、已有计划、
  空计划都在解析条目之前就返回失败。
- 收集式校验：PlanValidationResult 携带 issues 列表，
  把多个问题一次性反馈，减少无效往返。
- 计划状态可追溯：PlanState.revision + history(PlanSnapshot)
  + replan_count 让每次计划变更都有版本、有原因(reason)、
  有起止 turn，便于排查“计划为什么变了”。
- 质量靠约束不靠自觉：条目数上下界、证据要求、非重复计划，
  全部由代码强制，而不是提示词里的一句“请写好计划”。
```
```text
计划质量 = 形状规范（2~7 条、非空、带文本）
         + 可验证（required_evidence + 版本化证据）
         + 一致（Goal/Requirements 锁定、replan 有边界）
```

### turn 58步完成

```
在 planing.py 增加 PlanValidationIssue / PlanValidationResult / validate_plan()。
实现 2~7 个 Todo、完全重复 Todo、MUST_VERIFY(test/diff) Coverage 三类检查。
在 set_plan() 提交 state.plan 之前调用 validate_plan()。
在 replan() 保存旧 Plan 和替换新 Plan 之前调用同一个 validate_plan()。
```

## 20 v2 Plan Validation V2 (Plan 是否真正覆盖了Requirements)

v1 的 validate_plan 只检查计划的“形状”（非空、2~7 条、非重复）。
v2 把校验推进了一层：计划不仅要形状规范，还必须**真正把锁定的
Requirements 映射到具体的 Plan 条目上**。为此 TodoItem 增加了两
个字段，validate_plan 也拆成“形状检查 + 四个 Requirements 相关检查”。

### 20v2.1 TodoItem 的 V2 扩展
```
TodoItem 新增（planing.py，标注 “# plan validation v2”）：
- requirement_ids: list[int] = []     该条目覆盖了哪些 Requirement.id
- kind: TodoKind                      该条目的类型

TodoKind(str, Enum)：
  ANALYSIS = "analysis"
  IMPLEMENTATION = "implementation"
  VERIFICATION = "verification"
```

### 20v2.2 V2 校验的结构
```
validate_plan(state, todos) 返回 PlanValidationResult，按顺序收集：
1. 形状检查（沿用 v1）：
   - len(todos) < MIN_PLAN_ITEMS → plan_too_short
   - len(todos) > MAX_PLAN_ITEMS → plan_too_long
   - 重复条目（normalize_plan_content 去空格/小写后比较）
     → duplicate_plan_item
2. validate_plan_verification  → 验证证据覆盖
3. validate_requirement_references → Requirement 引用合法性
4. validate_requirement_mapping    → Requirement.kind 与 TodoKind 对应
5. validate_requirement_coverage   → MUST_CHANGE/MUST_VERIFY 是否被覆盖

仍然是“收集式校验”：把四个子函数拿到的 issues 全部汇总，
valid = not issues 才会通过。
```

### 20v2.3 Requirement 引用合法性（validate_requirement_references）
```
合法 id 集合 = {requirement.id for requirement in state.requirements.items}

遍历每个 todo.requirement_ids，若引用了集合外（不存在）的 id：
  → issue code="unknown_requirement"
     "Todo {todo.id} references unknown requirement {requirement_id}."
即 Todo 不能凭空引用一个不存在的需求。
```

### 20v2.4 Requirement.kind 与 TodoKind 的对应（validate_requirement_mapping）
```
对每个 todo 的每个 requirement_id，查出对应 Requirement，
再按 kind 强制一一对应：
- MUST_CHANGE 必须由 IMPLEMENTATION todo 覆盖，
  否则 → requirement_kind_mismatch
  "Requirement {id} is must_change and must be covered by an
   implementation todo."
- MUST_VERIFY 必须由 VERIFICATION todo 覆盖，
  否则 → requirement_kind_mismatch
  "Requirement {id} is must_verify and must be covered by a
   verification todo."
- MUST_NOT_MODIFY 是运行时约束（Runtime Guard），
  不应被分配到任何 todo，否则 → constraint_requirement_reference
  "Requirement {id} is a runtime constraint and should not be
   assigned to a todo item."

即 kind 语义：
  MUST_CHANGE      -> IMPLEMENTATION
  MUST_VERIFY      -> VERIFICATION
  MUST_NOT_MODIFY  -> Runtime Guard（不允许挂到 todo）
  SOFT_CONSTRAINT  -> Context / Model Responsibility
```

### 20v2.5 验证证据覆盖（validate_plan_verification）
```
收集计划里出现的证据类型：
  plan_evidence = {todo.required_evidence for todo in todos
                   if todo.required_evidence is not None}

对每个 MUST_VERIFY 的 Requirement，用 verifier 反查所需证据：
  verification_evidence_map = { "test": TESTS_PASSED,
                                "diff": DIFF_INSPECTED }
  - verifier 不在表里（如 build / syntax，当前 EvidenceType
    尚未支持 Plan 级验证）→ 跳过，不报错。
  - 表里有但 plan_evidence 不包含该证据
    → issue code="missing_verification_step"
      "Requirement {id} requires '{verifier}' verification, but the
       plan does not include the required verification evidence."

含义：如果需求要求“跑测试/看 diff”来验证，那么计划里必须
真的有一条声明了对应 required_evidence 的条目。
```

### 20v2.6 需求覆盖（validate_requirement_coverage）
```
covered_requirement_ids = 所有 todo.requirement_ids 的并集

只对必须被覆盖的 kind 检查（MUST_CHANGE、MUST_VERIFY）；
MUST_NOT_MODIFY（运行时约束）与 SOFT_CONSTRAINT（软约束）
不要求被 todo 覆盖，直接跳过。

若某个 MUST_CHANGE / MUST_VERIFY 的 requirement.id
不在 covered_requirement_ids 中：
  → issue code="uncovered_requirement"
    "Requirement {id} ({kind}) is not covered by any plan item"

这条是 V2 的核心：每个必须落地的需求都必须有 Todo 认领。
```

### 20v2.7 关键设计取舍
```
- 从“形状”到“语义”：v1 只管计划长什么样，v2 管计划是否
  真正回应了 Requirements，把需求追溯到具体条目。
- 双向对齐：既检查 todo -> requirement（引用存在、kind 对应），
  也检查 requirement -> todo（必须覆盖的都要被认领）。
- 硬约束与软约束分流：MUST_NOT_MODIFY 交给 Runtime Guard，
  SOFT_CONSTRAINT 交给 Context / 模型自觉，不混进计划校验。
- 温和降级：EvidenceType 暂不支持 build / syntax 时跳过而非报错，
  便于后续扩展 verification_evidence_map。
- 复用 v1 的收集式校验与 normalize_plan_content，
  新增检查只是往同一个 issues 列表里追加。
```
```text
Plan Validation V2 把计划校验从“形状规范”
升级为“形状规范 + 需求覆盖”：
  Todo 用 requirement_ids 认领需求、用 kind 声明性质，
  validate_plan 保证
    引用存在、kind 匹配、证据齐备、必须覆盖的需求都有人认领。
```
```text
代码位置（planing.py）：
- TodoItem.requirement_ids / TodoItem.kind
- TodoKind(str, Enum)
- validate_plan(...)                        总入口
- validate_plan_verification(...)           证据覆盖
- validate_requirement_references(...)      引用合法性
- validate_requirement_mapping(...)         kind 对应
- validate_requirement_coverage(...)        必须覆盖检查
```

### 已知问题（代码观察，非本总结结论）
```
validate_requirement_references 与 validate_requirement_mapping
中部分分支误用 PlanValidationResult(code=..., message=...) 而不是
PlanValidationIssue(...)。PlanValidationResult 没有 code/message 参数，
因此一旦命中 unknown_requirement 或 requirement_kind_mismatch /
constraint_requirement_reference 分支就会抛
TypeError: PlanValidationResult.__init__() got an unexpected keyword
argument 'code'，而不是给出可读的校验信息。
（这正是本任务初次 set_plan 时触发的报错。）

