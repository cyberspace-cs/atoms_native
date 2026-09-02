"""LLM multi-provider registry. OpenAI-compatible chat completions.

Providers:
  - deepseek (default): https://api.deepseek.com/v1  (deepseek-chat / deepseek-reasoner)
  - openai-compatible: configurable base_url + key + model (通义千问 DashScope / Azure / 自建)
"""
import json
import os

import httpx

from config import (
    DEEPSEEK_API_KEY,
    LLM_PROVIDER,
    OPENAI_COMPAT_API_KEY,
    OPENAI_COMPAT_BASE_URL,
    OPENAI_COMPAT_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
)

PROVIDER_CONFIG = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "key": DEEPSEEK_API_KEY,
    },
    "deepseek-reasoner": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-reasoner",
        "key": DEEPSEEK_API_KEY,
    },
    "openrouter": {
        # Free community hub: one key serves DeepSeek V4 Flash free +
        # Nous Hermes free (and 35+ other :free models), no credit card.
        "base_url": "https://openrouter.ai/api/v1",
        "model": OPENROUTER_MODEL,
        "key": OPENROUTER_API_KEY,
    },
    "openai-compatible": {
        "base_url": OPENAI_COMPAT_BASE_URL,
        "model": OPENAI_COMPAT_MODEL,
        "key": OPENAI_COMPAT_API_KEY,
    },
}


def normalize_model(model: str | None):
    if not model:
        return LLM_PROVIDER
    model = model.strip()
    # OpenRouter model ids contain a "/", e.g. "deepseek/deepseek-v4-flash:free"
    # or "nousresearch/hermes-3-llama-3.1-405b:free" — route them to openrouter.
    if "/" in model:
        return "openrouter"
    # alias: qwen -> openai-compatible (configured for DashScope)
    if model in ("qwen", "qwen-plus", "qwen-max") and OPENAI_COMPAT_BASE_URL:
        return "openai-compatible"
    if model in PROVIDER_CONFIG:
        return model
    return LLM_PROVIDER


def provider_available(model: str | None = None) -> bool:
    cfg = PROVIDER_CONFIG.get(normalize_model(model))
    return bool(cfg and cfg.get("key") and cfg.get("base_url"))


def list_models() -> list[str]:
    return [k for k, v in PROVIDER_CONFIG.items() if v.get("key") and v.get("base_url")]


def list_choices() -> list[dict]:
    """Selectable model ids for the frontend picker.

    Ordering follows the take-home priorities:
      1. The four named models requested earlier (hy4 / deepseek-v4-flash /
         GLM 5.3 Flash / DeepSeek V4 Flash Vision Exp) — all verified reachable
         on 2026-08-31 once the OpenRouter account's Allowed-Providers gate was
         relaxed. Paid tier under the configured key.
      2. Zero-cost `:free` options for quick experimentation, led by the
         `z-ai/glm-5.2:free` model the user explicitly asked to call (reachable
         but subject to upstream free-tier throttling -> 429).
      3. Direct DeepSeek key (cheapest, always-on fallback, default selection).
    """
    out = []
    if OPENROUTER_API_KEY:
        # (1) the four named models — priority
        out += [
            {
                "id": "tencent/hy4-preview",
                "label": "Tencent Hy4（混元）· 你点名",
                "free": False,
                "note": "上下文 1M · 输出 64K · 已验证可用 · $0.83/1M in",
            },
            {
                "id": "deepseek/deepseek-v4-flash",
                "label": "DeepSeek V4 Flash · 你点名",
                "free": False,
                "note": "上下文 1M · 输出 384K · 已验证可用 · $0.086/1M in",
            },
            {
                "id": "z-ai/glm-5.3-flash",
                "label": "Z.ai GLM 5.3 Flash · 你点名",
                "free": False,
                "note": "上下文 1.3M · 输出 131K · 已验证可用 · $0.075/1M in",
            },
            {
                "id": "deepseek/deepseek-v4-flash-vision-exp",
                "label": "DeepSeek V4 Flash Vision Exp · 你点名",
                "free": False,
                "note": "视觉实验版 · 输出 384K · 已验证可用",
            },
        ]
        # (2) zero-cost free tier
        out += [
            {
                "id": "z-ai/glm-5.2:free",
                "label": "Z.ai GLM 5.2（免费）· 你让我调用",
                "free": True,
                "note": "256K 上下文 · 已验证可达 · 免费档偶发 429 限流，稍后重试",
            },
            {
                "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "label": "NVIDIA Nemotron 3 Ultra（免费）",
                "free": True,
                "note": "1M 上下文 · Agent 编排/长推理首选免费模型",
            },
            {
                "id": "poolside/laguna-s-2.1:free",
                "label": "Poolside Laguna S 2.1（免费）",
                "free": True,
                "note": "262K · 编码 Agent 强（Terminal-Bench 70.2%）",
            },
            {
                "id": "minimax/minimax-m3:free",
                "label": "MiniMax M3（免费）",
                "free": True,
                "note": "1M 上下文 · 多模态长上下文",
            },
            {
                "id": "cohere/north-mini-code:free",
                "label": "Cohere North Mini Code（免费）",
                "free": True,
                "note": "256K · 代码生成/终端任务",
            },
            {
                "id": "nvidia/nemotron-3-super-120b-a12b:free",
                "label": "NVIDIA Nemotron 3 Super（免费）",
                "free": True,
                "note": "1M 上下文 · 通用+编码",
            },
            {
                "id": "thinkingmachines/inkling:free",
                "label": "Thinking Machines Inkling（免费）",
                "free": True,
                "note": "1M 上下文 · 推理/编码 Agent",
            },
            {
                "id": "openrouter/free",
                "label": "OpenRouter 免费路由器（自动选）",
                "free": True,
                "note": "自动随机选可用免费模型",
            },
        ]
    # (3) direct DeepSeek key fallback (cheapest, always on)
    if DEEPSEEK_API_KEY:
        out.append({
            "id": "deepseek",
            "label": "DeepSeek 直连（key）· 默认",
            "free": False,
            "note": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat") + " · 已验证真生成",
        })
    if not out:
        out.append({"id": "deepseek", "label": "DeepSeek（离线回退）", "free": False, "note": "无可用 key，将走离线模板"})
    return out


def chat(model: str | None, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4000):
    """Returns (text, error). Non-streaming.

    `model` may be a provider key (e.g. "deepseek", "openrouter") or a full
    model id (e.g. "deepseek/deepseek-v4-flash:free"). When a full id is given
    it is used directly for the API call; otherwise the provider's default
    `cfg["model"]` is used.
    """
    cfg = PROVIDER_CONFIG.get(normalize_model(model))
    if not cfg or not cfg.get("key") or not cfg.get("base_url"):
        return None, "no_provider"
    api_model = model if (model and "/" in model) else cfg["model"]
    try:
        resp = httpx.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": api_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        # Reasoning models (e.g. deepseek-v4-flash / deepseek-reasoner) put the
        # answer in reasoning_content and leave content empty — fall back to it.
        text = msg.get("content") or msg.get("reasoning_content") or ""
        return text, None
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:300]
