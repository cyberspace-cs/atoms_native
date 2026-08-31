"""LLMOps 可观测性（对齐企业实践）。

对齐 LLMOps Observability：OpenTelemetry / OpenInference GenAI 语义约定
（span: LLM / AGENT / TOOL / RETRIEVER / GUARDRAIL / EVALUATOR）、
P50/P95/P99 + TTFT 指标、结构化 correlation-id 日志 + PII 脱敏 + prompt hash、
prompt/model 版本化用于回归。

设计：
- 零强依赖：opentelemetry 为可选。缺失时退化为进程内指标聚合（供 /api/metrics
  展示），追踪为 no-op（不报错）。
- 进程内环形缓冲记录每次 agent 调用与每次生成的 TTFT，计算分位数。
- PII 脱敏：邮箱/手机号/卡号/密钥/Bearer 自动打码，用于日志与审计详情。
- prompt hash：sha256[:16]，用于把日志/审计与具体 prompt 关联又不泄露内容。
"""
import hashlib
import re
import time
import uuid
from collections import deque
from contextlib import contextmanager

MAX_RUNS = 5000
_runs: deque = deque(maxlen=MAX_RUNS)      # 每次 agent 调用
_ttfts: deque = deque(maxlen=MAX_RUNS)     # 每次生成的 TTFT（proxy）
_by_agent: dict = {}
_by_model: dict = {}

# OpenTelemetry 可选集成状态
_otel = None
_otel_ready = False


def _try_otel():
    global _otel, _otel_ready
    if _otel_ready:
        return _otel
    try:
        from opentelemetry import trace  # 可选
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.semconv.trace import SpanAttributes
        _otel = {"trace": trace, "TracerProvider": TracerProvider, "SpanAttributes": SpanAttributes}
        _otel_ready = True
        return _otel
    except Exception:
        _otel_ready = True  # 决定「无 OTel」，不再重试
        return None


@contextmanager
def span(name: str, kind: str = "AGENT", attributes: dict | None = None):
    """GenAI 语义约定的 span（LLM/AGENT/TOOL/RETRIEVER/GUARDRAIL/EVALUATOR）。

    无 OTel 时为 no-op 上下文管理器（不影响业务逻辑）。
    """
    ot = _try_otel()
    if ot is None:
        yield None
        return
    tracer = ot["trace"].get_tracer("atoms_native")
    with tracer.start_as_current_span(name) as sp:
        sp.set_attribute("gen_ai.span.kind", kind)
        for k, v in (attributes or {}).items():
            try:
                sp.set_attribute(k, v)
            except Exception:
                pass
        yield sp


def redact(text: str | None) -> str:
    """PII 脱敏：邮箱 / 手机号 / 身份证 / 卡号 / 密钥 / Bearer 打码。"""
    if not text:
        return ""
    t = text
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", t)
    t = re.sub(r"(?<!\d)(1[3-9]\d{9})(?!\d)", "[phone]", t)
    t = re.sub(r"\b\d{15,19}\b", "[card]", t)
    t = re.sub(r"(?i)\bsk-[A-Za-z0-9]{8,}", "[REDACTED]", t)
    t = re.sub(r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|pwd|authorization|key)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{6,}",
               lambda m: m.group(0).split("=")[0].split(":")[0] + "=[REDACTED]", t)
    t = re.sub(r"(?i)Bearer\s+[A-Za-z0-9_\-\.]{6,}", "Bearer [REDACTED]", t)
    return t


def prompt_hash(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def corr_id() -> str:
    return uuid.uuid4().hex[:16]


def record_run(agent: str, model: str, latency_ms: int, tokens: int,
               mock: bool, security_score=None):
    """记录一次 agent 调用（用于延迟/吞吐/成本分位）。"""
    rec = {
        "agent": agent, "model": model, "latency_ms": latency_ms,
        "tokens": tokens, "mock": bool(mock), "security_score": security_score,
        "ts": time.time(),
    }
    _runs.append(rec)
    _by_agent.setdefault(agent, []).append(latency_ms)
    _by_model.setdefault(model, {"n": 0, "tok": 0, "lat": 0, "mock": 0})
    m = _by_model[model]
    m["n"] += 1
    m["tok"] += tokens
    m["lat"] += latency_ms
    m["mock"] += int(bool(mock))


def record_ttft(model: str, ttft_ms: int):
    """记录一次生成的 TTFT（proxy：pipeline 启动到工程师首段输出的墙钟）。"""
    _ttfts.append({"model": model, "ttft_ms": ttft_ms})


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def summary() -> dict:
    """聚合指标：P50/P95/P99 延迟、吞吐(tokens/s)、TTFT 分位、mock 率。"""
    lats = [r["latency_ms"] for r in _runs]
    toks = [r["tokens"] for r in _runs]
    mocks = [r["mock"] for r in _runs]
    ttfts = [t["ttft_ms"] for t in _ttfts]
    total_tok = sum(toks)
    total_s = sum(lats) / 1000.0 or 0
    out = {
        "n_runs": len(_runs),
        "latency_p50_ms": _percentile(lats, 50),
        "latency_p95_ms": _percentile(lats, 95),
        "latency_p99_ms": _percentile(lats, 99),
        "ttft_p50_ms": _percentile(ttfts, 50),
        "ttft_p95_ms": _percentile(ttfts, 95),
        "ttft_p99_ms": _percentile(ttfts, 99),
        "throughput_tps": round(total_tok / total_s, 1) if total_s else 0.0,
        "mock_rate": round(sum(mocks) / len(mocks), 3) if mocks else 0.0,
        "by_agent": {a: {"p95_ms": _percentile(v, 95), "n": len(v)} for a, v in _by_agent.items()},
        "by_model": {m: {"n": d["n"], "avg_lat_ms": round(d["lat"] / d["n"], 1) if d["n"] else 0,
                         "avg_tok": round(d["tok"] / d["n"], 1) if d["n"] else 0,
                         "mock_rate": round(d["mock"] / d["n"], 3)} for m, d in _by_model.items()},
    }
    return out
