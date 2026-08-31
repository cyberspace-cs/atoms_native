"""Race Mode: run the same spec across multiple LLM models in parallel, score each, rank.

Per-model generation is parallelized with threads so total latency ~= the
slowest single model, not the sum.
"""
import concurrent.futures as cf
import json
import time

from agent.llm import chat, provider_available, LLM_PROVIDER
from agent.pipeline import (
    PM_SYSTEM, ARCH_SYSTEM, ENG_SYSTEM, REV_SYSTEM, _extract_html, _extract_json,
    _valid_html,
    _mock_spec, _mock_arch, _mock_app,
)
from database import log_agent_run


def _tok(s: str) -> int:
    if not s:
        return 0
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    return int(cjk + (len(s) - cjk) / 4)


def _build_spec_arch(idea: str, models: list[str]):
    spec_model = LLM_PROVIDER if provider_available(LLM_PROVIDER) else (models[0] if models else None)
    if provider_available(spec_model):
        spec, _ = chat(spec_model, [{"role": "system", "content": PM_SYSTEM}, {"role": "user", "content": idea}])
        spec = spec or _mock_spec(idea)
        arch, _ = chat(spec_model, [{"role": "system", "content": ARCH_SYSTEM}, {"role": "user", "content": spec}])
        arch = arch or _mock_arch(idea)
    else:
        spec, arch = _mock_spec(idea), _mock_arch(idea)
    return spec, arch


def _gen_one(model: str, idea: str, spec: str, arch: str, project_id=None):
    """Run engineer+reviewer for one model. Returns (events, candidate)."""
    events = []
    if provider_available(model):
        t0 = time.time()
        c, e1 = chat(model, [{"role": "system", "content": ENG_SYSTEM},
                             {"role": "user", "content": f"SPEC:\n{spec}\n\nARCH:\n{arch}"}], max_tokens=5000)
        dt = int((time.time() - t0) * 1000)
        log_agent_run(project_id, None, "Race-Engineer", model,
                      f"SPEC:{spec[:1000]}", (c or "")[:4000], dt,
                      _tok(spec) + _tok(c or ""), bool(e1), e1, None)
        cand = _extract_html(c) if c else ""
        code = cand if _valid_html(cand) else _mock_app(idea)
    else:
        code = _mock_app(idea)
    events.append(("agent_start", {"agent": "Engineer", "label": f"工程师 · {model}", "icon": "⚙️"}))
    events.append(("app_code", {"code": code, "model": model}))
    if provider_available(model):
        t0 = time.time()
        r, e2 = chat(model, [{"role": "system", "content": REV_SYSTEM},
                             {"role": "user", "content": f"SPEC:\n{spec}\n\nCODE:\n{code}"}], max_tokens=1500)
        dt = int((time.time() - t0) * 1000)
        rj = _extract_json(r or "")
        score = (rj or {}).get("score", 50)
        issues = (rj or {}).get("issues", [])
        log_agent_run(project_id, None, "Race-Reviewer", model,
                      f"CODE:{code[:1000]}", (r or "")[:4000], dt,
                      _tok(code) + _tok(r or ""), bool(e2), e2, None)
    else:
        score, issues = 60, ["离线模板，未参与真实评审"]
    candidate = {"model": model, "code": code, "score": score, "issues": issues}
    events.append(("agent_output", {"agent": "Reviewer",
                 "output": f"模型 {model} 评分：{score}/100\n问题：{', '.join(issues) if issues else '无明显问题'}",
                 "model": model}))
    return events, candidate


def run_race(idea: str, models: list[str], project_id=None):
    """Generator yielding per-model events; returns {candidates, spec, arch}."""
    # Keep the original (possibly full OpenRouter) model ids so chat() targets
    # the specific free model; provider resolution happens inside chat().
    spec, arch = _build_spec_arch(idea, models)
    yield {"type": "spec", "spec": spec, "arch": arch}
    candidates = []
    with cf.ThreadPoolExecutor(max_workers=max(1, len(models))) as ex:
        futs = {ex.submit(_gen_one, m, idea, spec, arch, project_id): m for m in models}
        for fut in cf.as_completed(futs):
            events, cand = fut.result()
            for etype, payload in events:
                yield {"type": etype, **payload}
            candidates.append(cand)
    candidates.sort(key=lambda x: -x["score"])
    yield {"type": "race_done", "winner": candidates[0]["model"] if candidates else None}
    return {"candidates": candidates, "spec": spec, "arch": arch}
