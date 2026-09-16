"""Single entry point for DeepSeek and Zhipu model requests."""

import os
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

# Change only this value, then restart the agent: "zhipu" or "deepseek".
PROVIDER = "zhipu"

PROVIDERS = {
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-4.7",
        "api_key": os.getenv("ZHIPU_API_KEY", '7eb19ed5b41c4acbb18c68346448ef79.JiIVOlOXKnDkjHpY'),
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key": os.getenv("DEEPSEEK_API_KEY", 'sk-3cb0a38c5ece4d58a6941ddfdee85c5d'),
    },
}


def call_model(
    messages: list[dict],
    *,
    instructions: str | None = None,
    tools: list[dict] | None = None,
    thinking: bool | None = None,
) -> ChatCompletionMessage:
    """Return one assistant message, including tool_calls and reasoning_content."""
    if PROVIDER not in PROVIDERS:
        raise ValueError(f"Unknown model provider: {PROVIDER}")
    settings = PROVIDERS[PROVIDER]
    request = {"model": settings["model"], "messages": list(messages)}
    if instructions:
        request["messages"].insert(0, {"role": "system", "content": instructions})
    if tools:
        request["tools"] = [
            tool if "function" in tool else {
                "type": "function",
                "function": {key: value for key, value in tool.items() if key != "type"},
            }
            for tool in tools
        ]
    if thinking is not None:
        request["extra_body"] = {
            "thinking": {"type": "enabled" if thinking else "disabled"}
        }
    with OpenAI(api_key=settings["api_key"], base_url=settings["base_url"]) as client:
        response = client.chat.completions.create(**request)
    if not response.choices:
        raise RuntimeError(f"{PROVIDER} returned no response choices")
    return response.choices[0].message
