# 第27课：Agent Evaluation / Regression Testing

该参考包基于 mini-coding-agent(6).zip 的源码生成。**没有包含上传 ZIP 的 .env、Git 仓库、venv 或 API Key。**

包含：
- 新增的 evaluation.py / eval_cli.py
- 新增 tests/test_lesson27_evaluation.py
- calculator 故障 fixture 和独立 oracle
- 经修复的 agent.py、runtime_lifecycle.py
- Git patch（适用于第六版；已通过 check）
- 完整中文教程

完整验证：原有77项 + 新增20项 = 97项 pytest 通过（测试环境采用临时 OpenAI SDK 导入替身，没有请求真实模型）。

用法见完整教程；推荐先在备份副本运行 `git apply --check`，通过后再 `git apply`。
