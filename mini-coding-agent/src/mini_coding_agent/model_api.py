"""Single entry point for DeepSeek and Zhipu model requests."""

import os
import json
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

# Change only this value, then restart the agent: "zhipu" or "deepseek".
PROVIDER = "deepseek"

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
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3.8-27b",
        "api_key": os.getenv("GROQ_API", "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"),
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.5-flash",
        "api_key": os.getenv("GEMINI_API", "AQ.Ab8RN6KlEI4u25V8VD2gY34xZ2MXmgVHrrw8-iUSdAZavNz7xQ")
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen3.8-27b:free",
        "api_key": os.getenv("OPENROUTER_API", "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")
    }
}


def call_model(
    messages: list[dict],
    *,
    instructions: str | None = None,
    tools: list[dict] | None = None,
    thinking: bool | None = None,
    debug: bool = False,
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
    # if debug:
    #     print("\n--- Request JSON ---")
    #     print(json.dumps(request, ensure_ascii=False, indent=2), flush=True)
    with OpenAI(api_key=settings["api_key"], base_url=settings["base_url"]) as client:
        response = client.chat.completions.create(**request)
    if debug:
        print("\n--- Response JSON ---")
        print(response.model_dump_json(indent=2), flush=True)
    if not response.choices:
        raise RuntimeError(f"{PROVIDER} returned no response choices")
    return response.choices[0].message
