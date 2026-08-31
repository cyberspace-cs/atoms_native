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
)

PROVIDER_CONFIG = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key": DEEPSEEK_API_KEY,
    },
    "deepseek-reasoner": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-reasoner",
        "key": DEEPSEEK_API_KEY,
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


def chat(model: str | None, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4000):
    """Returns (text, error). Non-streaming."""
    cfg = PROVIDER_CONFIG.get(normalize_model(model))
    if not cfg or not cfg.get("key") or not cfg.get("base_url"):
        return None, "no_provider"
    try:
        resp = httpx.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], None
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:300]
