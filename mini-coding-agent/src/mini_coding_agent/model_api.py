"""Single entry point for DeepSeek and Zhipu model requests."""

import os
import json
from pathlib import Path
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage


def _load_local_env() -> None:
    """Load project-local API keys without overriding exported variables."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()

# Change only this value, then restart the agent: "zhipu" or "deepseek".
PROVIDER = "deepseek"

PROVIDERS = {
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-4.7",
        "api_key": os.getenv("ZHIPU_API_KEY"),
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3.8-27b",
        "api_key": os.getenv("GROQ_API"),
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.5-flash",
        "api_key": os.getenv("GEMINI_API")
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen3.8-27b:free",
        "api_key": os.getenv("OPENROUTER_API")
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
    if not settings["api_key"]:
        raise RuntimeError(
            f"Missing API key for {PROVIDER}. Set the required value in .env."
        )
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
