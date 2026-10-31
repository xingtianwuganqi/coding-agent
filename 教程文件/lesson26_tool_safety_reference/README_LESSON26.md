# 第 26 课 — Tool Safety / Human-in-the-loop

基础代码：上传的 `mini-coding-agent(5).zip`（第 25 课已集成）。

包含：
- `approval.py` 新模块：集中工具策略，用户明确批准，拒绝去重；
- `agent.py` 参考集成：Guard -> Approval -> Checkpoint.before_tool -> Execute -> after_tool，拒绝走 skip_tool；
- `checkpoint.py`：在 RunSession 持久化 `denied_approvals`，新增 `skip_tool()`；
- `tool_runner.py`：直接调用仍要审批，`preauthorized` 参数避免重复确认，权限拒绝不走 Recovery；
- `tools.py`：命令安全检查与敏感路径保护；
- `permission.py`：EOF/Ctrl-C 视为拒绝；
- `tool_result.py`：结构化失败码；
- `test_approval_gate.py`：21 项测试（含模拟 Agent Loop）。

## 在项目根目录集成

先保存一个 Git 提交或独立 Runner 副本，再检查补丁：

```bash
git apply --check lesson26_tool_safety.patch
git apply lesson26_tool_safety.patch
uv run pytest -q
```

注意：补丁针对上传的第 5 版源码。若你在本地做了额外修改，建议逐文件查看 diff 并合并。

## 安全边界

这只是教学级工具策略，不是操作系统沙箱。项目测试和 `run_command`
可能执行任意项目代码；对于不可信代码，必须通过外部沙箱/容器/权限隔离。
不应把受限命令字符串判断当成完整的安全保障。

## 测试说明

本地使用临时 OpenAI SDK 导入替身（没有进行真实模型 API 调用）测试：
98 passed = 原有 77 + 新增 21。参考 ZIP 不包含 SDK 替身或任何 `.env` 文件。
