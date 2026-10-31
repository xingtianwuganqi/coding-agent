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

## 3.planning

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
3.MAX_STAGNANT_TURNS = 5：允许的最大停滞轮数（planning.py 常量）
```

### 15.3 核心函数（planning.py）
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

run_agent()
    │
    ├── 模型产生 tool_call
    │
    ▼
ToolRunner / execute_tool()
    │
    ├── Guard
    │
    ├── 真正执行 Tool
    │
    ▼
ToolResult
    │
    ├── success
    │       ↓
    │   正常继续
    │
    └── failure
            ↓
    handle_tool_failure()
            ↓
    classify_failure()
            ↓
    FailureDecision
       │          │
       │          └── 交给模型换策略
       │
       └── Runtime 自动 retry

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
set_plan / replan 的入口处强制校验。planning.py 中为此定义了一组
专门的数据结构与常量。

### 20.1 计划相关的数据结构
```
常量（planning.py 顶部）：
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
在 planning.py 增加 PlanValidationIssue / PlanValidationResult / validate_plan()。
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
TodoItem 新增（planning.py，标注 “# plan validation v2”）：
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
代码位置（planning.py）：
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


## 21. Task State Machine / Plan Execution Invariants 状态机

### 概述
```
Todo（计划条目）就是这一层的"任务"。
它的状态不再是可以随意赋值的字段，而是一个受约束的状态机：
  每次 update_task 都必须先通过 validate_task_transition，
  只有合法迁移才允许提交（Commit），否则返回 ToolResult.fail。
状态机的目的：把"计划执行不变量"从提示词的软约束，
下沉为代码里的硬约束（single in-progress、完成需证据、非法迁移被拒）。
```

### TaskStatus 状态定义
```text
class TaskStatus(Enum):
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    BLOCKED     = "blocked"
```

### 迁移表 TASK_TRANSITIONS（合法边）
```text
PENDING     -> { IN_PROGRESS }
IN_PROGRESS -> { COMPLETED, BLOCKED }
BLOCKED     -> { IN_PROGRESS }
COMPLETED   -> { }                # 终态，不可再迁出

未在上面列出的迁移一律非法，例如：
  PENDING -> COMPLETED            # 必须先 in_progress
  BLOCKED -> COMPLETED            # 必须先回到 in_progress
  COMPLETED -> *                  # 终态不可回退
```

### 校验入口 validate_task_transition（按顺序检查）
```
输入：state(AgentState) / todo(TodoItem) / target(TaskStatus)
返回：TaskTransitionResult(allowed: bool, error: str | None)

1. 禁止同状态更新
   current == target  -> allowed=False
   error = "Task {id} is already {status}."

2. State Machine（迁移表）
   not can_transition_task(current, target)  -> allowed=False
   error = "Invalid task transitons: {current} -> {target}."

3. Single IN_PROGRESS（唯一进行中不变量）
   target == IN_PROGRESS 且 has_other_in_progress_task(state, id) -> allowed=False
   error = "Another task is already in progress."

4. Evidence Guard（完成需证据不变量）
   target == COMPLETED 且 not has_required_task_evidence(state, todo) -> allowed=False
   error = "Task {id} requires evidence '{required_evidence}' before it can be completed."

  全部通过 -> allowed=True
```

### 两个关键不变量及其实现
```text
不变量 A：同一时刻只允许一个 IN_PROGRESS
  - get_in_progress_task(state)         取当前所有 IN_PROGRESS 的 Todo
  - has_other_in_progress_task(state,id) 判断是否存在"非本任务"的 IN_PROGRESS
  - 任何把别的任务置为 IN_PROGRESS 的尝试都会被第 3 步拒绝。

不变量 B：Todo 完成前必须提供所需证据
  - TodoItem.required_evidence: EvidenceType | None（None 表示无证据要求）
  - has_required_task_evidence(state, todo)：
      required 为 None            -> True（无需证据）
      state.evidence 中没有该证据 -> False
      evidence_revision != state.workspace_revision -> False（证据必须是"当前版本"的）
    即证据要满足"存在"且"未过期（版本等于 workspace_revision）"两个条件。
  - 任何证据缺失/过期的 COMPLETED 尝试都会被第 4 步拒绝。
```

### 与 update_task 的集成（准入 -> 提交）
```
update_task(state, metrics, task_id, status, note):
  1. TaskStatus(status) 解析目标状态，非法值 -> ToolResult.fail
  2. 在 state.plan.items 中按 id 查找 todo，找不到 -> ToolResult.fail
  3. 调 validate_task_transition 做准入校验
     not allowed -> ToolResult.fail(error=validation.error)
  4. Commit：todo.status = target_status；note 非空则写回 stripped note
  5. record_progress(...)：迁移成功也算一次真实进展，重置 stagnation 计时
  返回 ToolResult.ok("Task {id} updated: {old} -> {new}.")
```

### 代码位置（src/mini_coding_agent/planning.py）
```text
- TaskStatus(Enum)                     任务四态
- TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]]   合法迁移表
- can_transition_task(current, target)  迁移表查询
- get_in_progress_task(state)           当前进行中的 Todo 列表
- has_other_in_progress_task(state, id) 单 IN_PROGRESS 检查
- has_required_task_evidence(state,todo) 完成所需证据检查（版本比对）
- TaskTransitionResult(allowed, error)  校验结果
- validate_task_transition(state,todo,target)  状态机总入口
- update_task(...)                      接入状态机的提交入口
```

### 相关常量
```text
MAX_STAGNANT_TURNS = 5   连续无进展容忍上限（与 record_progress 配合）
MAX_REPLANS        = 5   重新计划次数上限
MIN_PLAN_ITEMS     = 2   计划最少条目
MAX_PLAN_ITEMS     = 7   计划最多条目
```

### 已修复问题
```text
can_transition_task 已使用 TASK_TRANSITIONS.get(current, set()) 查询迁移表。
has_required_task_evidence 已复用 has_valid_evidence，按 workspace_revision
判断证据是否属于当前工作区版本。
```


## fix：新增 delete_file / ask_user 工具、must_change target 修复、SYSTEM_PROMPT 重构与 guard 流程优化

本次修改集中在五个方面：

1. 新增 `ask_user`，让人机澄清成为一等工具；
2. 修复 `delete_file` 的返回类型、确认信息、版本号和工具注册问题；
3. 修复 `must_change.target` 的路径解析，支持裸文件名补全、目录拒绝、多文件时询问用户；
4. 优化 `guard_tool_call`，恢复“先检索定位文件，再设置 requirements”的流程；
5. 重写 `SYSTEM_PROMPT`，使其与运行时门禁保持一致。

---

### 1. 新增 ask_user 工具（人机澄清）

#### 解决的问题

- 模型遇到无法从用户请求或工作区确定的信息时，只能猜测、直接写代码，或者把任务标为 blocked；
- 没有统一的“向用户提问”通道，问题只能混在普通回复里，用户无法用固定格式回答；
- 内部流程（例如歧义文件选择）需要交互能力，但复用 `ask_user` 会多包一层 `ToolResult`，不方便。

#### 改动位置

| 文件 | 改动 |
| --- | --- |
| `src/mini_coding_agent/tools.py` | 新增 `clean_ask_user_options`、`prompt_user_choice`、`ask_user_choice`、`ask_user` |
| `src/mini_coding_agent/tools_schema.py` | 新增 `ask_user` 的 function schema，要求 2–6 个选项 |
| `src/mini_coding_agent/permission.py` | 将 `ask_user` 标记为 `Permission.SAFE`，无需确认 |
| `src/mini_coding_agent/tool_runner.py` | 增加 `ask_user` 分发分支 |
| `src/mini_coding_agent/guard.py` | `ask_user` 在任意阶段直接放行 |
| `src/mini_coding_agent/prompts.py` | 增加 ask_user 的使用场景、选项格式、回答 0 时的处理规则 |
| `tests/test_ask_user.py` | 新增 10 个测试 |

#### 实现细节

`tools.py` 把交互拆成三层，避免 CLI 逻辑和工具返回类型耦合：

```text
prompt_user_choice(question, options)
    -> (selected_option | None, status)
       status: selected / zero / closed / exhausted

ask_user_choice(question, options)
    -> 选中的选项文本，或 None
       供 runtime 内部流程复用

ask_user(question, options)
    -> ToolResult
       供模型通过 tool call 调用
```

行为约定：

- 选项按 `1..N` 编号展示，最后附带 `0. None of the above / no answer`；
- 直接回车视为 `0`；
- `0` 返回“用户没有选择”，模型应使用安全默认值、换一个问题或标记 blocked；
- `EOFError` / `KeyboardInterrupt` 返回 `closed`，不抛异常；
- 连续 3 次非法输入（`MAX_ASK_USER_ATTEMPTS = 3`）返回 `exhausted`；
- 空选项列表直接返回失败，并提示至少需要一个非空选项。

#### 优化效果

- 模型有了明确、可验证的澄清通道，减少“猜文件、猜实现、猜需求”；
- 用户输入被统一约束为数字，避免自由文本解析；
- `ask_user_choice` 可被 requirements 解析等内部流程直接复用；
- `ask_user` 不修改工作区，因此在 goal / requirements 锁定前也可安全调用。

---

### 2. 修复 delete_file 工具

#### 解决的问题

1. **返回类型不匹配导致运行时崩溃**：`tools.delete_file` 返回 `FileOperationResult`，但 `tool_runner.execute_tool` 的调用方统一按 `ToolResult` 读取 `success` / `error` / `content`，删除成功后会触发属性错误。
2. **确认信息缺少目标路径**：y/N 确认只显示 JSON 参数，用户很难判断到底要删除哪个文件。
3. **未真正删除也推进版本号**：文件本来不存在时 `changed=False`，但旧逻辑仍可能增加 `workspace_revision`，导致已有测试和 diff 证据被无意义地失效。
4. **调试输出污染 CLI**：删除流程中残留 `print`。
5. **未注册为动作工具**：`guard` 的 `ACTION_TOOLS` 未包含 `delete_file`，连续检索计数不会被正确重置。

#### 改动位置

| 文件 | 改动 |
| --- | --- |
| `src/mini_coding_agent/tools.py` | `delete_file` 明确返回 `FileOperationResult`；拒绝目录和符号链接；文件不存在时返回 `changed=False` |
| `src/mini_coding_agent/tool_runner.py` | 将 `FileOperationResult` 包装为 `ToolResult`；仅 `changed=True` 时记录进展和版本变化；新增 `format_confirmation_detail` 显示目标路径 |
| `src/mini_coding_agent/guard.py` | 将 `delete_file` 加入 `ACTION_TOOLS` |
| `src/mini_coding_agent/tools_schema.py` | 提供 `delete_file(path)` 的 schema |
| `src/mini_coding_agent/permission.py` | `delete_file` 归类为 `Permission.CONFIRM` |
| `tests/test_delete_file.py` | 新增 4 个测试 |

#### 关键逻辑

```python
# tool_runner.py
if name == "delete_file":
    result = delete_file(**arguments)

    if not result.success:
        return ToolResult(success=False, error=result.message)

    if result.changed:
        record_progress(...)
        record_workspace_change(state=state, path=arguments["path"])

    return ToolResult(success=True, content=result.message)
```

确认信息：

```python
if name == "delete_file":
    return f"Target file: {arguments.get('path', '<unknown>')}"
```

#### 优化效果

- 删除成功/失败都能稳定返回 `ToolResult`，不再让 `agent.py` 崩溃；
- 用户确认前能看到准确路径，降低误删风险；
- 文件本来不存在时不会推进 `workspace_revision`，避免证据被错误失效；
- 删除动作会正确重置连续检索计数；
- 目录、符号链接被拒绝，删除范围限制为普通文件。

---

### 3. must_change target 修复

#### 解决的问题

- 模型经常传裸文件名（如 `guard.py`），旧逻辑直接拒绝，要求完整路径，导致 requirements 反复校验失败；
- 模型传 `./src/foo.py` 时可能无法匹配；
- 目录路径可以被当成文件 target，永久无法满足；
- 裸文件名在工作区命中多个文件时，旧逻辑只报错，模型只能猜或重试；
- 不存在的文件也会被写入 requirement，导致后续永远无法完成。

#### 改动位置

| 文件 | 改动 |
| --- | --- |
| `src/mini_coding_agent/requirements.py` | 新增 `find_workspace_files_by_name`、`resolve_file_target`；扩展 `validate_requirement_payloads` / `set_requirements` 的 `resolver` 参数 |
| `src/mini_coding_agent/tool_runner.py` | `set_requirements` 注入 `resolve_target_with_user`，歧义时调用 `ask_user_choice` 并记录 trace |
| `src/mini_coding_agent/prompts.py` | 更新 target 规则，要求先检索定位文件，禁止用符号名或猜测路径 |
| `tests/test_requirement_targets.py` | 新增 5 个交互/解析测试 |

#### 解析规则

`normalize_file_target`：

- 去除首尾空格，统一 `\` 为 `/`；
- 循环去掉开头的 `./`；
- 拒绝空 target；
- 拒绝绝对路径、`~`、Windows 盘符；
- 拒绝 `.`、`..` 和空路径段；
- 单段且无扩展名的 target 视为符号名（函数/类），直接拒绝。

`resolve_file_target(target, resolver=None)`：

```text
包含目录的路径
  -> 语法校验通过后直接返回
  -> 如果目标是目录 -> 报错

裸文件名
  -> 在 WORKSPACE 中按文件名递归查找
  -> 跳过 .git / .venv 等目录
  -> 唯一命中 -> 自动补全为工作区相对路径
  -> 无命中   -> 校验失败
  -> 多个命中 -> 调用 resolver
                 resolver 返回合法路径 -> 使用该路径
                 resolver 返回 None 或其他值 -> 报错
```

`tool_runner` 中的 resolver：

```python
def resolve_target_with_user(target, matches):
    selected = ask_user_choice(
        question=(
            f"Multiple files match the requirement target '{target}'. "
            "Which file should be changed?"
        ),
        options=matches,
    )
    trace.record(
        ...,
        event_type="requirement_target_choice",
        detail=f"target={target}; selected={selected}",
    )
    return selected
```

#### 优化效果

- 裸文件名可自动补全，模型不必猜目录；
- `./src/foo.py` 等常见写法被规范化；
- 目录 target 在写入 requirement 前就被拒绝；
- 多文件同名不再直接失败，而是让用户选择，选择结果会写回 requirement；
- 用户选择 `0`、直接回车、输入关闭或连续非法输入时，requirement 不会锁定，错误信息要求模型给出完整相对路径；
- 每次用户选择都会写入 trace，便于排查 requirement 解析过程。

---

### 4. 恢复“先检索定位文件，再设置 requirements”

#### 解决的问题

- requirements 门禁原先在 goal 锁定后拦截所有非 `set_requirements` 工具；
- 当用户只说“修改 guard_tool_call”或某个函数名时，模型无法先定位文件，只能：
  - 猜完整路径；
  - 使用 `target=null`；
  - 直接失败；
- prompt 与实际 guard 行为不一致，模型容易连续撞门禁。

#### 改动位置

`src/mini_coding_agent/guard.py`：

```python
if not state.requirements.locked:
    if name == "set_requirements":
        return None

    # 允许 requirements 锁定前做只读检索
    if name not in RETRIEVAL_TOOLS:
        return (
            "RUNTIME GUARD: task requirements have not been extracted yet. "
            "You may use list_files/read_file/search_text to locate the "
            "relevant files, then call set_requirements. Other task actions "
            "are blocked until requirements are locked."
        )
```

pre-plan 检索预算耗尽时按阶段返回不同提示：

```text
requirements 未锁定:
  enough pre-plan inspection has been performed.
  Call set_requirements now, then create the plan with set_plan.

requirements 已锁定:
  enough pre-plan inspection has been performed.
  Create the todo plan with set_plan.
```

门禁顺序保持为：

```text
ask_user 放行
  -> goal gate
  -> requirements gate
  -> requirement constraints
  -> plan gate
  -> pre-plan inspection budget
  -> replan check
  -> post-plan consecutive retrieval check
```

#### 优化效果

- 模型可以先 `search_text` 定位符号所在文件，再 `read_file` 确认内容，最后写出准确的 `must_change.target`；
- requirements 前后的检索共享同一个 4 次 pre-plan 预算，无法通过“先检索再设 requirements”绕过预算；
- goal 门禁仍然优先，goal 锁定前检索依旧被拦截；
- 预算耗尽时提示会明确指出下一步是 `set_requirements` 还是 `set_plan`，减少无效重试；
- plan 创建后的 3 次连续检索限制和重复检索去重保持不变。

---

### 5. SYSTEM_PROMPT 重构

#### 解决的问题

- 旧 prompt 的工作流是 `goal -> requirements -> inspection`，与 guard 行为矛盾；
- 要求“文件不明确时先调查”，但 requirements 锁定前无法检索，模型无法执行；
- 未说明阶段门禁、检索预算、plan 校验规则、命令白名单、证据失效和完成条件；
- 未覆盖 `delete_file`、`get_plan`、`ask_user` 等工具；
- 多处内容重复且表述不一致。

#### 改动位置

`src/mini_coding_agent/prompts.py`：完整重写 `SYSTEM_PROMPT`，当前约 395 行。

章节结构：

```text
Stage gates
Tool map
Goal
Requirements
Inspection before requirements and planning
Planning
Executing tasks
Inspection rules after planning
Editing rules
Commands
Verification and evidence
Clarifying questions
Failure recovery
Stagnation
Replanning
Runtime feedback
Completion
```

#### 主要补充

1. **工作流顺序** 改为：

   ```text
   goal -> inspection -> requirements -> plan -> execution -> verification -> final report
   ```

2. **阶段门禁**：
   - goal 锁定前只允许 `set_goal` / `ask_user`；
   - requirements 锁定前允许检索和 `set_requirements`；
   - plan 创建前只允许检索和 `set_plan`；
   - 写入、删除、命令、`update_task` 在 plan 前被拒绝。

3. **14 个工具全部覆盖**，并用脚本校验 prompt 中包含所有 schema 工具名。

4. **Requirements 规则**：
   - 优先使用用户给出的路径或检索确认的路径；
   - 用户给符号名时，先检索定位所在文件；
   - `target=null` 仅在合理检索后仍无法确定文件时使用；
   - 多文件同名由 runtime 询问用户。

5. **检索预算**：
   - plan 前最多 4 次检索，requirements 前后共享；
   - plan 后最多 3 次连续检索；
   - 同一 revision 的重复检索被拦截；
   - 明确预算耗尽后的下一步。

6. **Plan 校验规则**：
   - 2–7 项、禁止重复、`kind` / `required_evidence` 取值；
   - `requirement_ids` 必须存在；
   - `must_change` 必须由 implementation 项覆盖；
   - `must_verify` 必须由 verification 项覆盖；
   - `test` / `diff` verifier 对应的证据要求；
   - `must_not_modify` 不得被 plan 引用。

7. **命令白名单与确认策略**：
   - `pwd` / `ls` / `pytest` / `uv run pytest` / 只读 git；
   - git 写操作和 `uv add/remove/sync` 需要 y/N；
   - 其他程序被拒绝，shell 操作符被禁止。

8. **验证与证据**：
   - 编辑完成后跑测试；
   - 测试通过后执行 `git diff`；
   - 证据按 `workspace_revision` / `code_revision` 失效；
   - 禁止声称未记录的验证成功。

9. **恢复、停滞、replan、完成条件**：
   - 只读工具瞬时错误自动重试一次；
   - 5 轮无进展触发停滞提示；
   - replan 的 5 种 reason 和最多 5 次限制；
   - 最终消息必须无 tool call，并经过完成度检查。

#### 优化效果

- prompt 与 guard、plan 校验、命令策略、完成检查保持一致，减少“照着 prompt 做却被拦截”；
- 模型知道每个阶段能做什么、不能做什么，减少无效工具调用；
- 明确要求先定位文件再写 requirement，减少 target 校验失败和猜测；
- 检索预算、证据失效、完成条件被写清楚，减少重复检索和虚假完成；
- 工具说明完整，14 个工具无遗漏。

---

### 6. 测试与验证

新增/更新的测试：

| 文件 | 覆盖内容 |
| --- | --- |
| `tests/test_ask_user.py` | 选项展示、合法选择、0、回车、EOF、非法输入、空选项 |
| `tests/test_delete_file.py` | 成功删除、文件不存在、目录/符号链接拒绝、ToolResult 包装 |
| `tests/test_requirement_targets.py` | 路径规范化、裸文件名补全、目录拒绝、无命中、歧义 resolver、用户交互、多 target 逐个询问 |
| `tests/test_refactor_layout.py` | requirements 前允许检索、动作仍被拦截、goal 前检索被拦截、预算耗尽提示 |

最终验证：

```text
75 passed in 0.79s
```

---

### 7. 总体优化效果

- **安全性**：`ask_user` / `delete_file` 纳入权限体系；删除前显示目标路径；goal / plan 门禁未被绕过。
- **可用性**：模型可以先检索再写 requirements，裸文件名可自动补全，多文件歧义交给用户选择。
- **一致性**：`SYSTEM_PROMPT`、guard、plan 校验、命令策略和完成检查描述一致。
- **可观测性**：requirement 目标选择写入 trace，便于复盘模型为何选择某个文件。
- **稳定性**：`delete_file` 返回类型统一为 `ToolResult`，不再导致运行时崩溃；未发生实际修改时不会错误推进版本号。
- **可维护性**：交互逻辑拆分为 `prompt_user_choice` / `ask_user_choice` / `ask_user`，内部流程和工具调用共用同一套实现。



## 22： Task Dependency / Execution Ordering

本章为计划系统引入**任务间依赖（depends_on）**与**执行顺序控制**。核心思想：允许 LLM 在 `set_plan` 时声明每个任务的前置任务 ID，runtime 在三个层面保证依赖的正确性与可执行性——①计划创建时的静态校验（依赖图合法性）；②状态迁移时的动态门禁（Dependency Guard）；③把依赖与 ready 任务信息暴露给 LLM。涉及模块：`planning.py`（字段与解析）、`tools_schema.py`（schema 描述）、`plan_validation.py`（依赖图校验）、`task_state.py`（未满足依赖判定、ready 计算、状态机 guard）、`runtime_context.py`（向 LLM 呈现依赖）。

### 22.1 TodoItem 中新增 depends_on 字段并接入解析与 schema

- **数据结构**（`planning.py` 第 136-138 行）：`TodoItem` 新增 `depends_on: list[int] = field(default_factory=list)`，注释标注 "22 task dependency 该任务必须等待哪些前置任务"。默认空列表，保证旧计划（无依赖声明）仍可正常构造，向后兼容。
- **参数解析**（`planning.py` `parse_plan_items`，约第 389-416 行）：从 LLM tool arguments 的 `item` 中读取 `depends_on`（缺省 `[]`），并做两道类型校验后才写入 `TodoItem`：
  - `if not isinstance(depends_on, list)` → 返回错误 `"depends_on must be a list"`；
  - `if not all(type(task_id) is int and task_id > 0 for task_id in depends_on)` → 返回错误 `"depends_on must contain positive integer IDs."`；
  - 校验通过后随 `content/kind/requirement_ids/required_evidence` 一并传入 `TodoItem(depends_on=depends_on)`。任一校验失败即 `(None, error)`，整份计划被拒绝。
- **工具 schema**（`tools_schema.py` 第 234-252 行）：`set_plan.items` 子 schema 新增 `depends_on` 属性，类型为 `{"type": "array", "items": {"type": "integer"}, "uniqueItems": True}`，描述为 "IDs of prerequisite tasks that must be completed before this task starts."，并被加入 `required` 列表（`["content", "kind", "requirement_ids", "required_evidence", "depends_on"]`）。配合 `additionalProperties: False`，强制模型每次创建任务都显式给出依赖（可为空数组）。

### 22.2 计划创建时检查依赖（validate_dependency_graph）

#### 22.2.1 validate_dependency_graph 验证任务依赖（`plan_validation.py` 第 441-527 行）

签名 `validate_dependency_graph(todos: list[TodoItem], issues: list[PlanValidationIssue]) -> None`，通过追加 `PlanValidationIssue` 报告问题。先构建 `task_map = {todo.id: todo}` 便于按 ID 查找。校验分两阶段，采用「引用有效性优先于环检测」的策略，用局部标志 `invalid` 串联：

- **第一阶段：依赖引用合法性**（逐条遍历 todos，命中即 `issues.append(...)` 并置 `invalid=True`）：
  - `duplicate_dependency`：`len(todo.depends_on) != len(set(todo.depends_on))`，同一任务重复声明同一依赖；
  - `unknown_dependency`：`dep_id not in task_map`，依赖了计划中不存在的任务 ID；
  - `self_dependency`：`dep_id == todo.id`，任务依赖自身。
  - 第一阶段结束后 `if invalid: return`——存在引用错误时**不再进行环检测**，避免在残缺图上误报。
- **第二阶段：DFS 三色环检测**：维护 `visiting`（当前递归栈/灰色）与 `visited`（已确认无环/黑色）两个集合。`has_cycle(task_id)`：命中 `visited` 返回 False（已证明无环，剪枝）；命中 `visiting` 返回 True（回到栈上节点，构成环）；否则加入 `visiting`，递归检查其 `depends_on`，回溯时移出 `visiting` 并入 `visited`。对 `task_map` 中每个 ID 调用 `has_cycle`，一旦发现环即追加 `dependency_cycle`（消息 "Task {task_id} is part of a dependency cycle."）并 `break`——**只报告第一个环即可**，无需穷举所有环。

#### 22.2.2 在 validate_plan 中调用 validate_dependency_graph

`validate_plan` 聚合各专项校验器（如 `validate_requirement_coverage` 等），在其调用链中加入 `validate_dependency_graph(todos, issues)`（`plan_validation.py` 约第 134 行）。因此上述四类依赖错误码会与其他计划校验问题一起返回给 `set_plan`/`replan`，使含非法依赖图的计划在创建阶段就被拒绝，而不会进入执行期。

### 22.3 Execution Ordering——哪些任务现在可以执行（`task_state.py`）

#### 22.3.1 get_unmet_dependencies：判断还有哪些依赖未完成（第 217-239 行）

签名 `get_unmet_dependencies(state: AgentState, todo: TodoItem) -> list[TodoItem]`（文档语义为返回未完成依赖列表）。实现：先从 `state.plan.items` 收集所有 `status == COMPLETED` 的任务 ID 到集合 `completed_ids`，再返回 `[dep_id for dep_id in todo.depends_on if dep_id not in completed_ids]`——即当前 todo 中尚未完成的前置依赖 ID 列表。返回列表为空即代表依赖全部满足。

#### 22.3.2 get_ready_tasks：获取当前所有 Ready Tasks（第 242-263 行）

签名 `get_ready_tasks(state: AgentState) -> list[TodoItem]`。可开始执行的条件（docstring 列出三条）：①状态为 `PENDING`；②所有依赖任务已完成；③（若声明 required_evidence 需已具备——但当前实现聚焦前两条）。实现：`[todo for todo in state.plan.items if todo.status == PENDING and not get_unmet_dependencies(state, todo)]`。即「pending 且无未满足依赖」的任务集合，供 runtime 展示与调度使用。

### 22.4 将 Dependency Guard 加入到状态机（validate_task_transition）

`validate_task_transition`（`task_state.py` 第 99 行起）按序执行多道门禁，其中新增的 Dependency Guard 位于「单活动任务约束」之后、「证据门禁」之前：

- 前置检查：①禁止同状态更新（`current == target`）；②状态机合法性 `can_transition_task`（依据 `TASK_TRANSITIONS`：`PENDING→{IN_PROGRESS}`、`IN_PROGRESS→{COMPLETED,BLOCKED}`、`BLOCKED→{IN_PROGRESS}`、`COMPLETED→∅`）；③单活动任务约束（迁往 `IN_PROGRESS` 时 `has_other_in_progress_task` 拦截并发进行中任务）。
- **Dependency Guard**（第 167-187 行）：`if target == TaskStatus.IN_PROGRESS:` 调用 `get_unmet_dependencies(state, todo)`，若结果非空则把未完成依赖 ID 转成字符串列表 `unmet_ids`，返回 `TaskTransitionResult(allowed=False, error="Task {id} cannot be started because it has unmet dependencies: {ids}.")`。由此在运行期硬性阻止「依赖未做完就想开工」的迁移。
- 后置 Evidence Guard：迁往 `COMPLETED` 时校验 `has_required_task_evidence`，缺失则拒绝。最终无拦截返回 `allowed=True`。

### 22.5 让 LLM 看见依赖（planning 渲染 + runtime_context）

- **计划文本渲染**（`planning.py` 第 276-284 行）：格式化每条 todo 时，若 `todo.depends_on` 非空，追加 ` [depends on: {逗号分隔ID}]`，使 plan 的可读输出体现依赖关系。
- **运行时上下文**（`runtime_context.py`，导入 `from .task_state import get_ready_tasks`）：`build_active_plan` 遍历 `state.plan.items` 时，若 `todo.depends_on` 非空则在行尾追加 `[depends_on: {deps}]`（第 52-54 行），并计算 `ready_ids = [todo.id for todo in get_ready_tasks(state)]`，在每条后附 `\n READY TASK IDS: {ready_ids}`（第 56-63 行）。这样模型每轮都能看到「哪些任务已解锁、可以开始」，从而做出符合依赖顺序的决策。

### 22.6 编写测试

依赖相关行为应覆盖以下用例（与上述实现一一对应）：

- **解析层**：`parse_plan_items` 对合法 `depends_on` 正确写入；对非 list、含非正整数 ID 分别返回对应错误字符串。
- **校验层**：`validate_dependency_graph` 分别触发 `duplicate_dependency`、`unknown_dependency`、`self_dependency`、`dependency_cycle`（含 A→B→A 互环）；第一阶段有错时短路不报环；`validate_plan` 集成后能整体拒绝非法依赖图。
- **执行排序**：`get_unmet_dependencies` 在有/无未完成依赖下的返回列表；`get_ready_tasks` 仅返回 pending 且依赖全 completed 的任务。
- **状态机 guard**：`validate_task_transition` 在依赖未满足时阻止 `→ IN_PROGRESS` 并给出含 `unmet dependencies` 的错误；依赖满足后放行。
- **上下文呈现**：`build_active_plan` 输出包含 `[depends_on: ...]` 与正确的 `READY TASK IDS`。

注：本次代码库检索未在 `tests/` 目录发现以 `depends_on`/`dependency`/`ready_task` 命名的现成测试文件，上述为应当补齐的测试清单；实际落地时按项目既有 pytest 组织方式添加。

### 本章收获

- **依赖建模**：用最小字段 `depends_on: list[int]` 表达任务前置关系，默认空列表保持向后兼容。
- **分层防护**：静态（创建期依赖图校验）+ 动态（运行期状态机 Dependency Guard）双重保证依赖不被绕过；ready 计算为二者提供统一判据。
- **校验短路设计**：`validate_dependency_graph` 先验引用有效性再做环检测，避免残缺图误报；环检测用三色 DFS + `visited` 剪枝，高效且只报一个环。
- **信息透明**：把依赖与 ready 任务写进 `runtime_context`，让 LLM 基于真实可执行集合规划下一步，减少无效尝试。



##  23. Dependency-aware Replanning / Scheduling

**核心问题**：当某个任务失败、被阻塞，或者执行方案发生变化时，Agent 如何重新规划（replan），同时避免丢失已经完成的工作。

Replan 的本质是"用新计划整体替换旧计划"（`state.plan.items = todos`），如果不做额外处理，旧计划中所有 `COMPLETED` 状态都会随替换丢失——已做完的分析、已写完的代码会被重新列为 `PENDING`，导致重复劳动甚至重复修改文件。本章机制的目标是：**在替换发生时，把新旧计划中语义等价且仍然有效的已完成任务"携带"（carry over）到新计划中，其余任务正常重置**。整体思路是对账（reconciliation）：replan 以后，对比每条任务，将相同的、已完成的任务保留为已完成状态，替换掉未完成的、更新了的任务。

### 23.1 数据模型与 Schema 扩展

**`TodoItem` 新增字段**（`planning.py:140-141`）：

```python
# 23新增：此任务复用了旧计划中的哪个任务
reuse_from_task_id: int | None = None
```

该字段由 LLM 在 replan 时显式声明："新任务 N 复用了旧任务 M 的成果"。默认 `None` 表示全新任务或已变更任务。

**replan tool schema 扩展**（`tools_schema.py:501-533`）：第 22 章为任务引入了 `depends_on`，本章的 replan schema 要求每个 item 必须同时提供两个调度字段，且都列入 `required` 列表（L525-532）：

- `depends_on`：前置任务 ID 列表，**指向新计划的 ID**，而非旧计划（schema 描述 L507-512 明确 "IDs refer to the new plan, not the previous plan"）。这保证新计划自身就是完整、自洽的 DAG。
- `reuse_from_task_id`：`["integer", "null"]` 类型，指向**旧计划**中一个 COMPLETED 任务；schema 描述（L516-522）强调 "The runtime validates reuse before preserving the completed status"——即声明复用不等于复用生效，最终由运行时对账裁决。

两套 ID 空间（新计划 ID vs 旧计划 ID）是本设计的关键区分：`depends_on` 描述新计划内部结构，`reuse_from_task_id` 是跨计划的映射指针。

**解析层**（`parse_plan_items`，`planning.py:409-424`）：从 tool arguments 中读取 `reuse_from_task_id`（缺省为 `None`）并连同 `depends_on` 一起构造 `TodoItem`。注意解析阶段只做类型/取值校验，不做任何复用合法性判断——合法性属于对账职责。

**渲染层**（`format_plan`，`planning.py:279-287`）：有依赖的任务追加 `[depends on: ...]` 标注，让 LLM 在新计划的上下文里继续看到依赖结构；被携带为 COMPLETED 的任务则以 `[v]` 符号呈现，LLM 自然不会再执行它们。

### 23.2 plan_reconciliation 实现 reconcile_completed_tasks

新模块 `plan_reconciliation.py` 只导出一个函数：

```python
def reconcile_completed_tasks(
        state: AgentState,
        new_todos: list[TodoItem],
) -> tuple[list[int], str | None]:
```

返回 `(保留完成状态的新任务 ID 列表, None)` 或 `([], 错误原因)`。模块头部注释（L1-14）给出了保守规则表：旧任务必须存在且为 COMPLETED、任务语义（内容/类型/Requirement/验证要求）不能改变、依赖经 ID 映射后保持一致、前置任务也必须保留完成、证据必须仍有效。

函数约定（L30-43 docstring）：`new_todos` 已通过 DAG Validation；不复制 `IN_PROGRESS` 和 `BLOCKED` 状态；校验失败时调用方**不提交新计划**。`TaskStatus` 采用延迟导入（L45-46）以避免与 `planning.py` 的循环导入。

算法分四个阶段：

**第一阶段：建立显式映射**（L48-75）。构建 `old_by_id`（旧任务字典）与 `old_to_new`（旧 ID → 新 ID）。遍历所有声明了 `reuse_from_task_id` 的新任务，做两种硬校验，任一失败立即返回错误、拒绝整个 replan：
- 引用悬空：`old_id not in old_by_id` → `"Unknown reused task ID"`；
- 一女二嫁：同一旧任务被多个新任务复用 → `"is reused more than once"`。

这两条是**结构性错误**（LLM 的映射声明本身不合法），因此直接失败；而后续的语义/依赖/证据不符只是**不可复用**，静默跳过即可。

**第二阶段：判定复用资格**（L77-121）。对每对 (old, new)，依次检查四道闸门，全部通过才进入 `eligible` 集合：

1. **状态闸门**：`old.status != COMPLETED` → 跳过（呼应"不复制 IN_PROGRESS/BLOCKED"约定）。
2. **语义闸门** `same_definition`（L91-97）：四个维度逐一比对——`normalize_plan_content(old.content) == normalize_plan_content(new.content)`（内容规范化后相等）、`kind` 相同、`requirement_ids` 集合相等、`required_evidence` 相同。任何一项变化都说明任务已被改写，其"完成"不再覆盖新任务的验收标准。
3. **依赖闸门**（L102-115）：把旧任务的 `depends_on`（旧 ID）经 `old_to_new` 映射为新 ID；若某个旧依赖没有被任何新任务复用（映射结果为 `None`），或映射后的依赖集合 ≠ `new.depends_on`，则跳过。含义是：**复用任务的前置结构必须原封不动地搬进新计划**，否则它在新 DAG 里的"已完成"缺乏对应的完成前提。
4. **证据闸门**（L117-119）：若任务要求证据（如 `tests_passed`），调用 `has_valid_evidence(state, ...)` 复核。由于证据与工作区版本绑定（第 17/20 章机制），replan 期间若有文件改动使旧证据失效，完成状态不能被继承。

**第三阶段：依赖闭包迭代**（L123-143）。`eligible` 只是单点合格，还要求"链式成立"：一个任务能保留完成，当其所有前置任务也**已经**确认保留完成。实现为不动点迭代——每轮把"前置已全部进入 `carried`"的 eligible 任务加入 `carried`，直到没有新增为止。若某条依赖链中途有任务失去资格，其后所有下游任务都会被连锁剥夺复用权（即使它们自身语义未变）。这是防止出现"上游没做完、下游却显示已完成"的虚假 DAG 状态。

*注：L132-133 存在一行冗余的重复条件 `todo.id in eligible and todo.id in eligible`，不影响正确性，疑似笔误。*

**第四阶段：赋态**（L145-150）。直接在 `new_todos` 上就地修改：`carried` 中的任务 `status = TaskStatus.COMPLETED`，返回排序后的 ID 列表。这一步是副作用式的——对账结果已写进候选新计划，等待调用方提交。

### 23.3 在 replan 中集成对账与签名判重

`replan`（`planning.py:507-648`）按"先验证、后提交"的顺序组织，对账发生在提交之前：

1. **前置门槛**（L517-561）：`MAX_REPLANS` 次数上限、必须已有 plan、goal/requirements 必须锁定、必须提供非空 `explanation`、新 items 非空。
2. **解析 + DAG 校验**（L563-583）：`parse_plan_items` → `validate_plan`（含第 22 章的依赖图校验）。注释（L571-572）点明原则："新 Plan 验证失败，不能把旧 Plan 搞坏"——此前所有失败路径都不触碰 `state.plan`。
3. **签名判重**（L597-604）：替代早期"仅比 content 列表"的做法（被注释掉的旧代码 L585-596），改用 `plan_signature`（L650-669）对新旧 todos 生成结构化签名元组：每项包含 `(规范化 content, kind.value, sorted(requirement_ids), required_evidence.value, sorted(depends_on))`。**签名相等 ⇒ 拒绝 replan**（"Replacement plan is identical to the current plan."）。相比纯内容比较，签名把依赖结构也纳入判据：只改 depends_on 的重排算有效 replan，而内容相同但打乱顺序的伪 replan 因签名做了 `sorted` 归一仍会被识别——防止 LLM 用无实质变化的"换皮计划"消耗 replan 配额。
4. **调用对账**（L606-615）：`reconcile_completed_tasks(state, new_todos=todos)`。返回 `reconcile_error`（第一阶段的结构性错误）时整个 replan 失败，旧计划原样保留；成功时 `todos` 中合格任务已被就地改为 COMPLETED。
5. **提交与快照**（L617-639）：`deepcopy` 旧 items 存入 `PlanSnapshot`（状态 `SUPERSEDED`，记录 reason+explanation 与被取代的 turn），然后 `revision + 1`、`state.plan.items = todos`、`replan_count += 1`，最后 `clear_failure_streak` 清零连续失败计数、`record_progress` 记一次真实进展（避免刚 replan 就再触发停滞告警）。

返回值报告"revision X was superseded, created revision Y"，历史快照则为后续审计/回溯保留了完整现场。

### 测试要点

- **映射校验**：`reuse_from_task_id` 指向不存在的旧 ID → 报错拒绝；同一旧任务被两个新任务复用 → 报错拒绝。
- **四道闸门**：旧任务非 COMPLETED、content/kind/requirement_ids/evidence 任一变化、依赖集合映射后不一致、证据已失效——四种情形都应静默不携带，任务回到 PENDING。
- **依赖闭包**：A→B→C 均 eligible 但 B 因语义变化失格，则 C 即使自身未变也不能携带（不动点迭代只收 A）。
- **签名判重**：完全同构的 replan（含仅乱序）被拒；仅改 `depends_on` 的计划签名不同、放行。
- **原子性**：对账报错后 `state.plan.revision`、`items`、`history` 均未被修改。
- **提交效果**：成功后 carried 任务在新计划中为 `[v]`，snapshot 状态为 SUPERSEDED，failure streak 清零。

### 本章收获

- **保守对账**：复用完成状态必须同时满足状态、语义、依赖、证据四类条件，宁可重做也不虚假携带——"声明复用"与"允许复用"分离，前者是 LLM 意图，后者是运行时裁决。
- **双 ID 空间**：`depends_on` 面向新计划保证 DAG 自洽，`reuse_from_task_id` 面向旧计划提供映射锚点，两者经 `old_to_new` 桥接后一致性校验，是跨版本状态迁移的通用模式。
- **依赖闭包**：完成状态的继承具有传递约束，用不动点迭代实现"上游不可靠则下游全部作废"，成本低于递归定义且天然处理任意拓扑。
- **签名判重**：用结构化签名（含依赖）代替文本比较来识别"伪 replan"，兼顾了防刷配额与允许纯调度调整两种诉求。
- **失败原子性**：replan 全流程遵循"验证不过不提交"，配合 deepcopy 快照，使旧计划在任意失败路径下完好无损。

## 24: Runtime Lifecycle / Termination Protocol

**核心问题**：Agent 什么时候才算"真正完成"，什么时候必须被强制终止？此前的终止判定分散在多处——依赖 LLM 停止调用工具来触发完成检查、依赖旧的 `record_progress`/`is_stagnating` 做停滞判断——判定口径不一、容易被模型"假装收尾"绕过，也会因无效重试白白消耗 Token。本章把整个生命周期收敛为**一个统一的运行时控制器**：新增 `runtime_lifecycle.py`，用 `check_lifecycle()` 作为唯一的终止闸门，用 `ProgressTracker` 度量"真实进展"，用 `finish_agent()` 作为唯一出口，并把 `run_agent()` 的主循环改造成"每轮先问控制器、再决定是否调用模型"。

整体设计原则有三条：

1. **判定权归运行时，不归 LLM**：模型不再决定何时结束；即使模型停止调用工具并给出总结，也必须通过 `check_lifecycle()` 的验收才允许返回。
2. **完成之前先止损**：在每次模型调用*之前*就检查生命周期（L142-151），已完成或已注定失败的任务不再消耗 Token。
3. **进展 = 新里程碑首次出现**：重复的失败重试、无效的相同动作不算进展，只有状态机上第一次发生的实质变化才刷新进展轮次。

### 24.1 新增 runtime_lifecycle.py：check_lifecycle 作为唯一终止闸门

新模块集中定义生命周期的数据模型与决策函数，`agent.py` 从中导入全部符号（L56-65）：`RuntimeBudget`、`LifecycleAction`、`LifecycleDecision`、`RunOutcome`、`ProgressTracker`、`StopReason`、`check_lifecycle`。

**三个枚举构成决策词汇表**：

- `LifecycleAction`：控制器对当前这一步的裁决——`CONTINUE`（继续跑）、`FINALIZE`（任务完成，可以收尾）、`TERMINATE`（硬性终止）。
- `RunOutcome`：最终结果归类，如 `COMPLETE`（成功完成）与 `STOPPED`（被中止）。
- `StopReason`：中止原因，例如达到最大轮次、无进展预算耗尽、需求冲突无法推进、`MODEL_ERROR`（模型调用异常）等，保证每个终止都有可审计的理由。

**`RuntimeBudget` 把资源上限显式化**（`agent.py:119-123`）：

```python
budget = RuntimeBudget(
    max_model_turns=min(MAX_MODEL_TURN, 120),   # 硬轮次上限
    max_no_progress_turns=30,                   # 连续无进展 → 终止
    warn_no_progress_turns=12,                  # 连续无进展 → 警告一次
)
```

三个阈值分工明确：12 轮无进展只注入一次停滞反馈给模型自我纠正；30 轮无进展则视为死循环，直接终止；`max_model_turns` 兜底防止无限运行。

**`check_lifecycle(state, model_turns, progress, budget)` 的判定优先级**（内部委托 `evaluate_runtime` + `task_completion.evaluate_completion` 聚合完成度）：

1. **所有任务都完成且证据齐备 ⇒ FINALIZE**：调用完成度评估，得到 `CompletionStatus.COMPLETE` 时放行收尾。
2. **存在明确不可处理的阻塞 ⇒ TERMINATE**：完成度评估返回 `BLOCKED`（如任务被标记 blocked、硬性需求无法满足）时立即止损，而不是像旧版那样还允许"blocked 后宽容一次"。
3. **超过最大轮次 ⇒ TERMINATE**。
4. **无进展达到预算 ⇒ TERMINATE**（基于 `ProgressTracker.last_meaningful_turn` 与当前轮次之差）。
5. **其他情况 ⇒ CONTINUE**。

返回值封装在 `LifecycleDecision(action, report, outcome, reason)` 中：`report` 是 `evaluate_completion` 产出的完整报告（含 `missing` 未达标项列表），供反馈注入和最终输出使用。

**`evaluate_completion()`（`task_completion.py`）聚合的状态判定**是 FINALIZE 分支的核心依据，它逐项核验：plan 是否存在、所有非 blocked 的 todo 是否 completed、带 required_evidence 的 todo 是否有对当前工作区版本有效的证据、文件有变更时是否做过 diff 校验、代码有修改时是否有成功的 test/build/syntax 验证、以及全部硬性用户需求是否满足。任一不满足即 `INCOMPLETE`，并把缺口写入 `report.missing`。

### 24.2 新增 ProgressTracker：只记录"第一次出现"的真实进展

旧机制 `record_progress` 的问题：只要某轮做了"看起来不同"的动作就可能刷新进展计数，导致反复试错、反复读取也被当成进展，停滞检测形同虚设。`ProgressTracker` 改用**里程碑集合去重**的思路：

- 内部维护 `seen_milestones` 集合与 `last_meaningful_turn`。
- 每次 `observe(state=..., report=..., turn=...)` 时，从当前状态推导出**已达成的里程碑全集** `milestones`，例如：goal 已锁定、requirements 已锁定、plan 已创建、某个 todo 首次 completed（以 content/kind/requirement_ids 的组合为键，避免同义任务混淆）、test/diff 证据在某个工作区 revision 上首次有效等。
- 计算增量：`new_milestones = milestones - self.seen_milestones`。**只有集合中出现过的新元素**才把 `last_meaningful_turn` 更新到当前轮次；重复观察到同一里程碑（重试、replan 后又回到同样状态）不刷新。
- `should_warn(current_turn, budget)`：当 `current_turn - last_meaningful_turn >= warn_no_progress_turns` 时给出警告信号，供主循环注入停滞反馈。

这样"原地打转"（同样的失败命令、同样的读取、拒绝后再交一版几乎相同的计划）都不会重置无进展计数器，为 check_lifecycle 的 TERMINATE 判定提供了可靠依据。

### 24.3 删除旧 record_progress 逻辑，改用 ProgressTracker.observe

`planning.py` 中的 `record_progress` 在 `agent.py` 的导入里已被注释掉（L21），主循环不再调用它。取而代之的是两处 `observe`：

- **启动时基线**（L125-129）：进入循环前先 `progress.observe(state, check_lifecycle(..., 0, ...).report, turn=0)`，把"任务尚未开始时的既有状态"记入 `seen_milestones`，避免第一轮就把 goal/plan 之类已有事实误判为新进展。
- **循环内持续观察**：每轮工具执行后由主循环驱动 observe，将本轮产生的新里程碑并入集合。

注意：`is_stagnating` / `build_stagnation_feedback` 仍保留导入，但停滞的**判定权**已经转移到 `ProgressTracker.should_warn` + `check_lifecycle` 的无进展预算；旧的基于 `state.last_progress_turn` 的独立停滞分支在 `run_agent()` 中整段注释移除（L170-195）。

### 24.4 统一终止出口 finish_agent()

新增 `finish_agent(state, metrics, trace, decision, preferred_text="")`（`agent.py:67-103`），是所有路径的唯一收口：

1. 把 `decision.outcome` / `decision.reason` 写入 `metrics.run_outcome` / `metrics.stop_reason`，并在 trace 里记录 `lifecycle_end` 事件——每个结局都可追溯。
2. `metrics.end_runing()` 结束计时，`trace.print_trace()` 打印执行轨迹。
3. **只有 `RunOutcome.COMPLETE` 才允许返回模型此前的总结文字**（`preferred_text.strip()`）；这修正了旧版"模型不调工具就把它的话当最终答案"的漏洞。
4. 其余情况输出结构化终止报告：`Outcome` / `Reason` / `Model turns` / `Changed files`（来自 `state.changed_files`）/ `Unmet conditions`（逐条列出 `decision.report.missing` 的 `name: reason`）。

调用点覆盖四类场景：循环入口判定、模型无 tool_calls 后的判定、两处 `call_model` 抛 `ModelCallError` 时构造 `TERMINATE + STOPPED + MODEL_ERROR` 的 decision 再收尾。

### 24.5 改造 run_agent()：先问控制器，再调模型

主循环结构调整为"生命周期检查前置"：

```python
while True:
    # 模型调用之前先判断：已完成就不要继续消耗 Token。
    decision = check_lifecycle(state, metrics.model_turns, progress, budget)
    if decision.action != LifecycleAction.CONTINUE:
        return finish_agent(...)
    # 仅警告一次；达到硬阈值则由上面的 Controller 直接终止。
    if progress.should_warn(metrics.model_turns, budget):
        history_turns.append([{"role": "user",
            "content": build_stagnation_feedback(state)}])
    ...
    message = call_model(...)
```

关键变化：

- **删除旧的停滞判断分支**：原先散落的 `is_stagnating(...)` 大段逻辑被注释移除，其职责由 `ProgressTracker` + `RuntimeBudget.max_no_progress_turns` 接管；警告也只注入一次反馈让模型自我调整。
- **"没有工具调用 ≠ 任务结束"**（L309-337）：模型停止调用工具时，不再直接采信其文本，而是走 Completion Protocol——`check_lifecycle` 重新评估，`completion_checks += 1` 并记录 trace；若 action 仍为 `CONTINUE`，则 `completion_rejections += 1`，把 `build_completion_feedback(decision.report, state)` 作为 user 消息追加进历史，`continue` 逼模型继续干活或补齐证据；只有 FINALIZE/TERMINATE 才经 `finish_agent(preferred_text=message.content)` 真正退出。
- **v1/v2 旧协议整体退役**：代码中保留了被注释的 v1（`get_completion_status` 三态判断）与 v2（`evaluate_completion` + `allow_blocked_final` 宽容 blocked 一次）实现作对照。v3 的区别在于：blocked 不再被"宽容放行"，统一交给控制器按 `StopReason` 终止并输出缺失清单；同时把检查时机从"仅无工具调用时"扩展到"每轮模型调用前"。
- **异常路径也走统一出口**：`compress_history` 与 `call_model` 的 `ModelCallError` 捕获中，先用 `check_lifecycle` 取回当前 report，再手工构造 `LifecycleDecision(TERMINATE, report, RunOutcome.STOPPED, StopReason.MODEL_ERROR)` 交给 `finish_agent`，保证异常终止同样有 outcome/reason/trace 记录。

### 测试要点

- **check_lifecycle 优先级**：全部 todo 完成且证据有效 → FINALIZE；存在 blocked 或未满足硬需求 → TERMINATE；轮次超 `max_model_turns` → TERMINATE；无进展达 `max_no_progress_turns` → TERMINATE；其余 → CONTINUE。
- **ProgressTracker 去重**：同一里程碑（同一 task_id 完成、同一 revision 的 test 证据）重复 observe 不刷新 `last_meaningful_turn`；全新里程碑才刷新；初始 observe(turn=0) 建立基线后，首轮不应虚报进展。
- **should_warn 边界**：恰好 12 轮无进展触发一次警告；警告不等于终止。
- **finish_agent 输出**：COMPLETE 时返回模型原文；非 COMPLETE 时包含 Outcome/Reason/Model turns/Changed files/Unmet conditions 各行，missing 为空时输出 `- none`。
- **无 tool_calls 不等于结束**：模型只回文本时，未完成状态下应收到 completion feedback 并继续循环；`completion_rejections` 计数增加。
- **MODEL_ERROR 路径**：call_model 抛异常时以 `STOPPED + MODEL_ERROR` 终止，trace 中有 `lifecycle_end` 事件。
- **Token 止损**：任务在模型调用前已满足完成条件时，循环入口直接 finalize，不产生额外的模型调用。

### 本章收获

- **单一控制面**：把"完成/阻塞/超轮/停滞"四类终止判定收敛进 `check_lifecycle` 一个函数，用 `(action, outcome, reason)` 三元组表达决策，主循环只剩"问—答—执行"三段式，消除了散落各处的 if-else 终止逻辑。
- **判定权与生成权分离**：LLM 负责产出，运行时负责验收。"不调工具"只是触发验收的信号而非终点，堵住模型提前下班的漏洞。
- **进展的本质是新信息**：用集合差 `milestones - seen_milestones` 定义进展，重试与原地打转天然不计入，比时间窗口式的旧停滞检测更贴近语义。
- **预算分层**：warn（12）给模型自救机会，terminate（30）硬止损，max_turns 全局兜底——软警告在前、硬限制在后的双阈值模式适用于一切防失控循环。
- **统一出口的审计价值**：`finish_agent` 强制每条终止路径都落 outcome/reason/trace/changed files，使"为什么停"成为一等公民信息，方便调试与回放。
- **检查前置省 Token**：在完成条件早已满足的情况下仍调用模型是纯浪费；把 check_lifecycle 放到每轮模型调用之前，是成本与正确性的双赢。



## 25. Checkpoint / Resume / State Persistence

**核心问题**：Agent 跑到一半崩溃、断电或被人为中断后，如何安全地接着跑？本章新增 `checkpoint.py`，把一次运行的全部内存状态（目标、需求、计划、指标、轨迹、进展跟踪器、对话历史）持久化为**带校验和的版本化 JSON**。开篇 docstring（`checkpoint.py:1-5`）立了本模块最重要的两条军规：

1. **Checkpoint 是"执行游标"（execution cursor），不是文件系统备份**——它记录的是"Agent 知道自己走到哪了"，工作区文件本身不在 checkpoint 里保存副本。
2. **不确定的在途工具调用绝不能自动重放**——一个可能已经执行、但结果未落盘的工具（如 `run_command` 有副作用），盲目重放可能造成二次破坏；此时必须要求人工恢复。

### 25.1 RunSession：一次运行需要持久化的全部现场

`@dataclass RunSession`（L56-73）聚合了所有运行时对象：`state: AgentState`、`metrics: AgentMetrics`、`trace: AgentTrace`、`progress: ProgressTracker`，外加三个刻画"对话进行到哪一步"的字段：

```python
history_turns: list[list[dict]]   # 已经完整结束的历史轮次
inflight_turn: list[dict] | None  # Assistant 消息 + 尚未归档的 tool 响应
pending_tool: dict | None         # 已标记准备执行、结果尚未提交持久化的工具
```

三者构成一条严格的状态机：一轮对话从 `begin_turn` 开始（assistant 消息进入 `inflight_turn`），每个工具执行前 `before_tool` 把 `{call_id, name}` 写入 `pending_tool`，工具结果与状态都保存后才清空并归档进 `history_turns`。**每一步转换都立即 `save()`**，因此任何时刻崩溃，磁盘上的 checkpoint 都能准确反映"最后确认安全的位置"。注意 L68 的注释：pending 标志在调用*之前*写入、在 ToolResult 和 AgentState *都*保存之后才清除——宁可多留一个"不确定"标记，也不留下"看似干净实则丢结果"的假象。另外 L64 说明 `pending_tool` 只存 call_id 和 name、不重复存 arguments，因为 assistant 消息里已经有了。

### 25.2 _encode / _decode：类型受限的可逆序列化

JSON 无法直接表达 dataclass、Enum、set、tuple 和非字符串键的 dict。`_encode`（L76-96）用带 `$` 前缀的标签把它们逐一标记：`{'$enum': 类名, '$value': 值}`、`{'$dataclass': 类名, '$fields': {...}}`、`{'$set': [...]}`（按 `str` 排序保证确定性）、`{'$tuple': [...]}`、`{'$pairs': [[k,v],...]}`；遇到未知类型直接 `raise TypeError`——**拒绝静默丢失信息**。`_decode`（L99-120）逆向还原，并依赖两张注册表 `_TYPES`（L37-41，14 个状态 dataclass）与 `_ENUMS`（L42-45，7 个枚举）；反序列化遇到未注册的类名/枚举名即抛 `CheckpointError`，等于一道隐式的兼容性白名单。新模块要接入 checkpoint，必须先把类型登记进这两张表。

### 25.3 workspace_fingerprint：识别"世界变了"

`workspace_fingerprint(root)`（L123-151）对非忽略文件做流式 SHA-256：`os.walk(followlinks=False)`，目录名与文件名分别排序遍历以保证哈希确定性；跳过 `SKIP_DIRS`（`.git`、`.venv`、`__pycache__`、`node_modules`、`.mini_agent` 等，L29-33）与符号链接；每个文件按 1MB 分块读取；超过 `MAX_FINGERPRINT_FILES = 10000` 则拒绝。关键细节：哈希输入包含**相对路径 + `\0` + 内容 + `\0`**，改名和内容变化都会改变指纹；且 `.mini_agent/` 前缀的文件被排除，避免 checkpoint 自身污染指纹（自指问题）。

### 25.4 save()：原子写 + 双层信封

`CheckpointStore.save`（L159-189）的 payload 固定含四个键：`schema`（版本号 `SCHEMA_VERSION = 1`）、`workspace`（绝对路径）、`workspace_fingerprint`、`session`。随后构造**两层 JSON 信封**：先以紧凑形式序列化 payload 并计算其 `sha256`，再把 `{sha256, payload}` 以缩进形式写盘——校验时只需按同样规则重新紧凑序列化比对哈希，即可发现截断或篡改。落盘走完整的 durability 流程：`mkdir(mode=0o700)` → 写临时文件 `*.tmp`（`O_CREAT|O_TRUNC`，权限 `0o600`）→ `flush + os.fsync` → `os.replace` 原子替换 → POSIX 下再 fsync 父目录（L180-186）→ finally 清理残留 tmp。这套"临时文件 + rename + 双 fsync"是防止半写文件的教科书式做法。

### 25.5 load()：五道闸门与 RecoveryRequired

`load`（L191-228）依次检查：文件存在 → 校验和匹配 → schema 版本一致 → `payload['workspace']` 属于当前工作区 → 解码结果是 `RunSession`。任一失败抛 `CheckpointError`（L48-49："不可用或不兼容的 checkpoint，不要悄悄从头开始"）。通过后再进入**危险状态检测**，此时抛的是其子类 `RecoveryRequired`（L52-53："某个工具可能已执行但结果未提交"）：

- `pending_tool is not None`（L210-216）→ 报错信息带上 tool name 与 call_id，明确指示"do not auto-replay，检查工作区并手工对账"；
- `inflight_turn is not None`（L217-221）→ 半个模型工具轮存在，缺失的工具调用不得自动补发；
- 当前工作区指纹 ≠ 存档指纹（L222-227）→ 即使没有半途状态，外部修改过的工作区也可能让旧证据失效，必须先检查差异再决定是否恢复。

两类异常的分工体现设计意图：`CheckpointError` 意味着"这份存档本身坏了"，`RecoveryRequired` 意味着"存档完好，但现场可疑，需要人来判断"——后者绝不返回 session，宁可不 resume 也不带病续跑。

### 25.6 接线：CLI 与 run_agent

`cli.py:24-32` 暴露 `--checkpoint PATH` 与 `--resume`；`--resume` 缺 `--checkpoint` 时直接 `parser.error`，resume 模式以空任务串调 `run_agent("", checkpoint_path=..., resume=True)`。`agent.py:116-131` 中 `run_agent` 接收两个新参数，`resume and not checkpoint_path` 先行拦截，然后构造 `CheckpointStore(Path(checkpoint_path), WORKSPACE) if checkpoint_path else None`——不传路径时整个机制零开销关闭。

### 测试要点

- **往返一致性**：构造含各类枚举/set/tuple 的 RunSession，save→load 后逐字段相等。
- **篡改检测**：手改 checkpoint 任意字节 → sha256 失配 → `CheckpointError`。
- **pending/inflight 拒载**：存档中带 `pending_tool` 或 `inflight_turn` → `load()` 抛 `RecoveryRequired` 且消息含 "do not auto-replay"。
- **指纹漂移**：save 后在工作区新增/改动一个非忽略文件 → `RecoveryRequired('Workspace changed...')`；改动 `.git/` 内文件则不受影响。
- **跨工作区**：同一份 checkpoint 换个 workspace 路径打开 → `CheckpointError('belongs to another workspace')`。
- **原子写**：save 中途异常后不留 `.tmp`，原文件仍是上一版完整内容。
- **未知类型**：塞入未注册 dataclass → encode 时 `TypeError`；伪造 `$dataclass` 名 → decode 时 `CheckpointError`。

### 本章收获

- **游标而非快照**：持久化"我知道做到哪了"比备份文件更轻、更可靠；配合工作区指纹，用极小成本回答了"存档之外的世界变了吗"。
- **不确定即熔断**：在副作用世界里，"不知道工具执行没执行"必须显式建模为 pending 标志并在恢复时拒绝自动重放；错误分级（CheckpointError vs RecoveryRequired）让上层能区分"换一份存档"与"叫人来"。
- **每次状态转换都落盘**：begin_turn/before_tool 各自 save 一次，牺牲一点 IO 换取"任意崩溃点都可判定"，这正是执行游标的正确粒度。
- **注册表白名单反序列化**：类型必须预先登记才能解码，既解决 JSON 类型缺失，又天然防注入与版本漂移。
- **durability 三件套**：tmp 文件 + fsync + 原子 rename（外加目录 fsync），任何需要"绝不留半成品"的持久化都应照此实现。
- **确定性编码可校验**：sort_keys + 紧凑 separators + allow_nan=False 使"重排序列化求哈希"成为无需自定义二进制格式的完整性校验方案。


## 26. Tool Safety / Human-in-the-loop

当 Agent 准备执行一个可能产生危险副作用的操作时，Runtime 怎样判断是否允许、是否需要用户批准，以及批准或拒绝以后该怎样继续？

**核心思想：** 模型可以提出操作，但权限系统决定什么能够执行。Lesson 26 在已有 Guard、Recovery 和 Checkpoint 的基础上建立完整审批链：

```text
Model Tool Call
→ structural and semantic validation (guard)
→ tool permission policy
→ command classification (run_command)
→ user approval when required
→ pending-tool checkpoint (only after approval)
→ execute or skip
→ commit result into history
```

### 26.1 Permission Levels（permission.py）

`Permission(Enum)` 定义四种级别，语义互斥：

- `SAFE`：允许自动执行（只读或低副作用）；
- `WRITE`：修改 workspace，通常有后续验证保护（write_file/replace_text 等）；
- `CONFIRM`：每次都必须用户确认（如 delete_file、git 写操作）；
- `BLOCKED`：策略直接拒绝，不询问用户。

`TOOL_PERMISSIONS` 静态映射每个工具的默认级别。关键设计：`run_command` 本身映射为 SAFE，真正的风险由运行时对命令内容的分类决定——即"工具级权限 + 参数级分类"两层叠加。`set_plan`、`update_task`、`ask_user` 等纯状态工具全部是 SAFE，保证审批不打断规划循环。

### 26.2 Command Policy（classify_command）

`classify_command()` 对每条 shell 命令做静态分析：

1. **Shell 操作符黑名单** `SHELL_OPERATORS = ["&&", "||", ";", "|", ">", "<", "`", "$("]`：任何命中直接 BLOCKED，切断链式执行、管道、重定向和命令替换这些绕过逐条审查的手段；
2. **解析失败即拒绝**：用 `shlex.split()` 拆词，解析异常或空命令一律 BLOCKED（fail-closed）；
3. **按程序分类**：`pwd`/`ls`/`pytest` 直接 SAFE；`git` 与 `uv` 进入子命令分类；其他程序 BLOCKED。

`classify_git_command()` 维护显式集合：safe = `status, diff, log, show, branch`（只读）；confirm = `add, commit, checkout, switch, restore, reset, merge, rebase, push, pull`；其余 BLOCKED。`classify_uv_command()`：`add/remove/sync` → CONFIRM；`uv run <target>` 特判——target 为 `pytest` 时 SAFE，运行其他程序要求确认；未知子命令 BLOCKED。

这里体现的原则是：**安全来自明确分类，而不是猜测命令是否危险**。新增程序/子命令默认落在 BLOCKED，扩展必须显式修改白名单。

### 26.3 Approval Status vs Permission（approval.py）

`approval.py` 把底层 Permission 翻译成上层可审计的审批结果。`ApprovalStatus(Enum)` 五种状态：

```text
AUTO_ALLOWED   获准自动执行
USER_REQUIRED  需要人工确认
APPROVED       用户已批准
DENIED         用户明确拒绝
POLICY_BLOCKED 策略直接禁止
```

`policy_for_tool(name, arguments)` 先查 `TOOL_PERMISSIONS`——未注册的工具名直接返回 BLOCKED（同样 fail-closed）；若工具是 `run_command`，用 `classify_command(arguments["command"])` 的动态分类**覆盖静态映射**，这是 26.2 两层叠加在代码里的汇合点。

`ApprovalDecision` dataclass 携带 `allowed: bool`、`status: ApprovalStatus`、`signature: str`、`reason: str`。ALLOW/BLOCK 的最终判定集中在单一分派处（SAFE/WRITE → AUTO_ALLOWED；CONFIRM → USER_REQUIRED；BLOCKED → POLICY_BLOCKED），以后调整 WRITE 是否需要确认只需改这一处。Permission 表达工具需要什么级别，ApprovalStatus 表达这一次调用最终得到了什么决定，二者不混成一个布尔值。

### 26.4 Stable Signatures and User-Facing Details

`approval_signature(name, arguments)` 生成稳定身份：

```python
raw = json.dumps({"tool": name, "arguments": arguments},
                 sort_keys=True, separators=(",", ":"))
return hashlib.sha256(raw.encode()).hexdigest()
```

`sort_keys=True` 让键顺序不影响哈希。**Signature 记忆的是具体被拒绝的操作，而不是整个工具名**——拒绝一次危险命令不应连带禁用以后合法的 `ls`。trace 中只记录签名前 12 位（agent.py：`detail="signature=" + authorization.signature[:12]`，注释明确 Never log raw command arguments），避免敏感参数进日志。

`approval_detail(name, arguments)` 解决"用户只看一串 JSON 无法判断风险"的问题：`run_command` 展示实际命令并追加 `Command policy: {classify_command(...)}`；`write_file` 展示路径 + 拟写入字节数（不打印内容）；`replace_text` 展示路径 + old/new 文本长度；`delete_file` 展示路径；其余工具退回展示排序后的 keys。人类需要理解"将要发生什么"，而不是阅读内部 JSON。

### 26.5 Authorization Flow（authorize_tool_call）

`authorize_tool_call(name, arguments, denied_signatures)` 是所有 Tool Call 的统一入口，内部先 `policy_for_tool` 再分派三条路径：SAFE/WRITE 直接放行；BLOCKED 直接拒绝（不询问用户）；CONFIRM 进入 `_request_approval(...)`。

`_request_approval` 的顺序经过精心设计：

1. **先查历史拒绝**：`signature in denied_signatures` → 直接 DENIED，不再重复打扰用户，防止模型反复提交同一被拒操作；
2. **再检查交互能力**：`stdin.isatty()` 为 False（CI、非交互环境）→ POLICY_BLOCKED。无人可问时绝不假装用户批准，也不擅自执行 CONFIRM 级操作；
3. **最后才提问**：调用 `ask_for_confirmation(command=f"{name}({arguments})", detail=approval_detail(...))`，接受 `y/yes`（strip+lower 归一化），其他输入、EOF、Ctrl-C 一律视为拒绝。

`permission.py` 的 `ask_for_confirmation()` 是终端交互原语：打印 `⚠️ Agent wants to run:` + 命令 + detail，读取 `[y/N]` 默认拒绝；捕获 `EOFError/KeyboardInterrupt` 返回 False，中断时行为保守。

### 26.6 Denial Is Not Silence

拒绝后系统不结束任务，而是向模型返回结构化错误。`agent.py` 约 658–667 行：`authorization.allowed` 为 False 时 `metrics.blocked_tool_calls += 1`，并以 `ToolResult.fail(error=authorization.reason, failure_code=...)` 回填——`DENIED` → `USER_DENIED`，其余 → `POLICY_BLOCKED`。模型因此知道这是人工决定或策略边界，可调整方案而非原样重试；被拒签名同时加入 `session.denied_approvals` 跨轮记住。

### 26.7 将审批拒绝记录进 Checkpoint

#### 26.7.1 RunSession 新增 denied_approvals

第 26 课新增字段：

```python
denied_approvals: set[str] = field(default_factory=set)
```

它随 checkpoint 一起持久化（`_encode` 支持 set 的 `$set` 序列化，见 25.2），使安全决定能跨越进程重启存活——resume 之后模型也无法绕过上次的人工拒绝。

#### 26.7.2 checkpoint.py 新增 skip_tool

协议约束：模型一次发出多个 Tool Call 时，**每个 tool_call 必须有对应 tool response**，否则下一轮消息序列不完整。若第一个获批、第二个被拒就直接丢弃，`inflight_turn` 会缺响应且 `finish_turn` 因残留状态报错。

`CheckpointStore.skip_tool(session, tool_output)` 的实现细节：前置条件是 `inflight_turn` 非 None 且 `pending_tool` 必须为 None（docstring："No tool was invoked; pending_tool must remain None"），即只能跳过尚未开始执行的调用；动作仅是把拒绝输出 append 进 `inflight_turn` 并 save，不设置任何执行标记。由此被拒绝的 Tool Call 依然进入聊天历史，模型能看到拒绝原因，协议一致性不被破坏。

#### 26.7.3 重构 run_agent 的工具调用流水线

旧版本由 run_agent 自己调 `guard_tool_call` 后再进 execute_tool；新版本改为五段流水线（agent.py 约 600–735 行），三个时序要点：

1. **Guard 在前，审批在后**：guard_error 存在时根本不进入 authorize_tool_call，避免让用户确认结构上非法的调用；
2. **pending_tool 标记延迟到获批之后**：只有 `authorization.allowed` 为真才调 `checkpoint_store.before_tool(session, call.id, name, arguments)` 并置 `execution_started = True`（注释："A checkpoint now means the tool may have actually run"）。被拒调用绝不留待执行标记，恢复时不会被误判为"可能已执行"；随后 `execute_tool_with_recovery(..., preauthorized=True)` 执行，内部不再重复审批；
3. **执行分支与跳过分支分别提交**：轮末按 `execution_started` 走 `after_tool`（校验 call_id 匹配后落结果、清 pending）或 `skip_tool`；整轮结束后 `finish_turn` 固化 history。另外，若工具已开始执行后参数解析才抛异常，代码直接 `raise` 而非当作无害 parse error——保留 pending 标记交给 RecoveryRequired 处理（见 25.5）。

这保证了批准、执行标记、实际运行和 checkpoint 四者的顺序一致。

#### 26.7.4 Path Safety：resolve_path

审批之外还有独立的空间防线（tools.py `resolve_path`）：所有文件路径先 resolve 再校验是否位于 workspace root 内，绝对路径、`~`、盘符、`.`/`..` 越界均拒绝。典型攻击：

```json
{"path": "../../.ssh/id_rsa"}
```

即使 read_file 是 SAFE 工具不需要确认，路径守卫也会拦截越权读取——**权限系统管"要不要问人"，路径守卫管"技术上能不能出圈"**，两者独立生效、互为补充。

### 本章结论

Lesson 26 完成了从"程序守卫"到"人类授权"的最后闭环：

```text
Can it run?          guard / validate
Should we ask?       policy_for_tool + classify_command
What did user say?   authorize_tool_call + denied_approvals
Is history valid?    skip_tool / after_tool + checkpoint
Can it escape?       resolve_path workspace containment
```

至此 Agent 既能在低风险操作上自主推进任务，又始终让高危操作处于可预测、可审计的人类控制之下。



## 27：Agent Evaluation / Regression Testing

怎样证明 Agent 真的完成了任务？以及修改 Agent 代码以后，怎样证明新版本比旧版本更好，而不是引入了新的问题？

本课新增一条**与运行时完全解耦的事后评测通道**：`evaluation.py`（评测内核）+ `eval_cli.py`（命令行入口）。它的核心立场写在模块 docstring 里——*checks workspace artifacts and opt-in tests independently of AgentState's own CompletionReport. It never calls an LLM or executes shell.*（除显式 opt-in 的可信 pytest 外）。也就是说：**Agent 自己说"完成了"不算数，必须由外部观察者用独立证据重新裁决**。

### 27.1 为什么不能相信 Agent 的自评

前面各课里，完成判定来自 Agent 内部（CompletionStatus、验证守卫），但这些都是"考生自己改卷"。真实风险是 **false complete**：模型声称任务完成、runtime 也记录 `run_outcome == "complete"`，但工作区里文件根本没写、内容不对、测试没通过。第 27 课的评测器是一个不共享 Agent 任何状态的第三方：它只读两样东西——checkpoint 里的 `RunSession.metrics`（运行事实）与工作区的实际产物（外部真相），然后把两者对照。

### 27.2 EvalCase：声明式的验收合同

```python
@dataclass(frozen=True)
class EvalCase:
    case_id: str
    task: str
    expected_outcome: str = "complete"      # complete | blocked | stopped
    must_exist: list[str]                   # 必须存在的文件
    must_not_exist: list[str]               # 必须不存在的文件
    file_contains: dict[str, str]           # 文件必须包含的子串
    file_not_contains: dict[str, str]       # 文件必须不含的子串
    pytest_paths: list[str]                 # 可信 oracle 测试（opt-in 执行）
    max_model_turns: int | None             # 效率预算（只告警不判负）
    max_tool_calls: int | None
```

`from_file()` 从 JSON 加载时先做**白名单校验**：`unknown = set(data) - allowed`，出现未知键直接抛 `EvaluationError`——防止一个拼错的字段名（如 `must_exits`）悄悄退化成"没有任何检查的空用例"。`validate()` 中最关键的一条：

```python
if not any((self.must_exist, self.must_not_exist,
            self.file_contains, self.file_not_contains,
            self.pytest_paths)):
    raise EvaluationError("At least one independent oracle check is required")
```

**每个用例必须至少有一个独立 oracle 检查**。只看 `expected_outcome == "complete"` 的用例是被禁止的，因为那等于又回到让 Agent 自证清白。所有路径字段逐条过 `_require_relative_path`（拒绝绝对路径、`..`、空段），与第 26 课的 resolve_path 同一思路。

### 27.3 独立 Oracle：文件检查与可信测试

`_check_files()` 把每条声明翻译成一个 `EvalCheck(name, passed, detail)`，命名规则如 `exists:path`、`absent:path`、`contains:path`、`not_contains:path`。两个细节体现保守设计：

- `must_not_exist` 用 `not target.exists()`（任何类型存在都算失败），而 `must_exist`/内容检查用 `is_file()`（目录不算文件产物）；
- `file_not_contains` **要求文件必须存在且不含目标子串**——文件缺失时判 fail 而不是"当然不含"。对应测试 `test_file_not_contains_requires_existing_file` 锁住了这个语义：删除文件不能成为通过"不包含"检查的后门。

`_run_trusted_tests()` 是唯一被允许执行外部进程的地方，防护层层叠加：

1. 测试文件属于**评测者**而非 Agent：`pytest_paths` 相对独立的 `oracle_root` 解析，且仍受 workspace-containment 检查约束——Agent 无法把自己的假测试放进 oracle 目录；
2. 任一 oracle 文件缺失 → 直接 fail（`Oracle test missing`），绝不静默跳过；
3. 用 `sys.executable -m pytest` 在目标 workspace 下运行，`PYTHONPATH` 前置 workspace，`timeout` 默认 120 秒，超时/OSError 都转成失败的 EvalCheck；
4. 输出只保留 `(stdout + stderr)[-1200:]` 的有界尾部，避免把大日志灌进结果文件。

docstring 还诚实地标注了边界："The path guard prevents traversal; it is NOT a sandbox for malicious Python tests."——路径守卫防越界，不防恶意测试代码本身，所以 oracle 必须是可信的人写的测试。

**未 opt-in 时也不给白**：若用例带 `pytest_paths` 但没传 `--run-tests`，不会跳过该检查，而是生成 `EvalCheck("pytest", False, "Not run: ...")`。"没跑测试"永远不等于"测试通过"（`test_missing_opt_in_tests_never_silently_pass`）。同时 `evaluate_case` 强制 `--run-tests` 必须搭配独立 `--oracle-root`，否则报错。

### 27.4 evaluate_case：三个维度分开记账

```python
external_success = bool(checks) and all(c.passed for c in checks)
outcome_match    = outcome == case.expected_outcome
passed           = external_success and outcome_match
false_complete   = (outcome == "complete" and not external_success)
```

`EvalResult` 不把一切压成一个布尔，而是分别记录：**外部产物对不对**（external_success）、**runtime 结局是否符合预期**（runtime_matches_expected）、**综合是否通过**（passed）、以及最重要的诊断信号 **false_complete**——runtime 声称完成但外部证据不支持。`expected_outcome` 也可以是 `"blocked"` 或 `"stopped"`：如果 Agent 正确地识别出任务不可行而停止，但产物齐全，`passed` 依然要求两者一致（`test_external_success_but_runtime_stopped_is_not_pass`），防止"结果碰巧对、过程完全失控"的运行被当作成功样本。

效率预算刻意与正确性分离：`max_model_turns` / `max_tool_calls` 超限时只往 `efficiency_warnings` 里追加字符串（如 `model_turns 9 > 5`），**不影响 passed**。源码注释解释了原因：budget regressions should not turn a correct file result into a false claim of failure——成本退化是趋势信号，不是对错信号。

指标来源是 checkpoint 中的 `session.metrics`（`AgentMetrics`，agent_metrics.py）：`model_turns`、`tool_calls`、`replans`、`duration`（`end_time` 为 None 即运行未正常收尾时，`duration_seconds` 记 None 而非估算值）、以及第 24 课持久化的最终 `run_outcome`。评测器读的是运行时的客观记录，不是模型的叙述。

### 27.5 SuiteSummary 与回归对比

单个用例之上是套件级统计：

```python
@dataclass(frozen=True)
class SuiteSummary:
    case_count, passed_count, pass_rate
    false_complete_count          # 自评谎报次数
    avg_successful_turns          # 只统计通过用例的平均轮数
```

`avg_successful_turns` 只对 `passed` 的结果取平均——失败运行可能烧了很多轮，把它们混进来会掩盖"正常路径变慢了"的信号。

回归测试的核心是 `compare_results(current, baseline)`：

- 以 `(case_id, run_id)` 为键；**任一侧出现重复键 → 报错**，防止静默覆盖导致对比失真；
- **两侧用例集合必须完全一致**，缺一个用例就抛 `Cannot compare different evaluation case/run sets`——缺失基线不是"免费通过"，而是数据错误（呼应 27.2 的保守哲学）；
- 输出 `regressions`（baseline 通过、current 失败）与 `improvements`（反向），外加双方 summary。

`run_id`（如 01/02/03）支持同一用例多次 trial：LLM 行为随机，单次运行不足以说明问题，多 trial 标签让对比可以按每次尝试配对。

结果的可移植性由 `write_result` / `load_results` 保证：`EvalResult.to_json()`（dataclass asdict）落盘，`load_results` 读回并把 `checks` 字典重建为 `EvalCheck`。roundtrip 测试（`test_result_roundtrip_and_suite_comparison`）确保对比可以在"上次保存的基线 vs 本次新结果"之间进行。

### 27.6 eval_cli.py：四个子命令

CLI 的定位同样明确："Post-run evaluation CLI; it never starts or controls a live Agent."

| 子命令 | 作用 | 关键守护 |
|---|---|---|
| `inspect` | 评测一个已结束的 checkpoint | `--output` 若解析到 workspace 内部会被拒绝（avoid checkpoint drift）；退出码 `0 if result.passed else 1`，可直接接入脚本化回归 |
| `summarize` | 把结果 JSON 数组汇总为 SuiteSummary | — |
| `collect` | 把多个单用例结果合并成数组 | 校验每项都是含 `checks` 的单结果对象 |
| `compare` | current vs baseline 回归对比 | 用例集不一致时报错退出 2 |

典型回归流程：跑 N 个任务各 K 次 trial → `inspect` 逐个产出结果文件（写到 workspace 外）→ `collect` 合并 → 存一份作 baseline；改动 Agent 代码后再走一遍，`compare` 看 regressions/improvements。

评测器的异常处理收口在一个 `except (OSError, ValueError, KeyError, TypeError, CheckpointError)`，统一以退出码 2 + `evaluation error: {exc}` 报告——评测工具自身出错与被评测任务失败（退出码 1）是两个不同的信号，不会混淆。

### 27.7 测试即规格：tests/test_lesson27_evaluation.py

`tests/test_lesson27_evaluation.py` 用 20 个左右的用例把上述语义逐条钉死，代表性的有：

- `test_runtime_complete_but_external_failure_is_false_complete`：谎报检测的正例；
- `test_refuse_paths_outside_workspace` / `test_refuse_symlink_outside_workspace`：参数化传入 `../evil`、绝对路径等不安全输入，并用**符号链接**指向 workspace 外来验证 `_inside_workspace` 是先 resolve 再比较（`is_relative_to`），软链接绕不过 containment；
- `test_case_json_rejects_unknown_keys`：拼写错误的 case JSON 被拒；
- `test_efficiency_budget_is_warning_not_correctness_failure`：超预算仍 passed，只有 warning；
- `test_compare_multiple_trials_by_run_id`：多 trial 配对对比；
- `test_finish_agent_persists_final_outcome` / `test_agent_end_paths_persist_final_checkpoint`：从另一侧保证前提成立——**metrics 里的 `run_outcome` 必须在每条结束路径上被写入最终 checkpoint**，否则评测器读到的是陈旧状态；
- `test_evaluate_runtime_wrapper_uses_tracker_not_old_kwarg`：守住 runtime 包装层与 metrics tracker 的接线不被改坏。

### 本章结论

Lesson 27 给整个课程补上了"如何证明系统有效"的一环：

```text
谁说了算?        外部 oracle，不是 Agent 的 CompletionReport
怎么算完成?      must_exist / contains / absent + 可信 pytest（opt-in）
没说完成却失败?  false_complete 单独计数
完成但太贵?      efficiency_warnings 与 passed 分离
怎么防退化?      baseline/current compare：缺用例=错误，重复键=错误
```

它与第 25 课 checkpoint（提供客观 metrics 与可恢复现场）、第 26 课授权（控制危险操作）正好构成三角：**权限系统控制它能做什么，checkpoint 记录它实际做了什么，评测系统裁决它做的事是否真的有价值**。修改 Agent 代码后不必凭感觉宣称"更好了"——跑一遍套件、compare 一次，regressions 列表就是答案。