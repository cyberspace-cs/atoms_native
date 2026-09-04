"""Multi-agent generation pipeline: PM -> Architect -> Engineer -> Reviewer.

Implemented as a generator that yields SSE-style event dicts and returns a
final summary dict (captured via StopIteration.value by the caller).
"""
import html
import json
import os
import re
import time

from agent.llm import chat, provider_available, LLM_PROVIDER
from database import log_agent_run
import security
import observability as obs
import friction


def _tok(s: str) -> int:
    """token 消耗估计（中文≈1字/token，其他≈4字符/token），仅用于效率指标。"""
    if not s:
        return 0
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    return int(cjk + (len(s) - cjk) / 4)

PM_SYSTEM = (
    "你是 Emma，一名资深产品经理 AI。用户会给你一个产品想法，"
    "请产出一份精炼的产品需求规格（中文，250 字以内）：\n"
    "1) 应用名称  2) 核心功能点（3-5 条）  3) 页面/区块  4) 需要持久化的数据字段  "
    "5) 关键约束。只输出规格本身，不要寒暄。"
)
ARCH_SYSTEM = (
    "你是 Bob，一名软件架构师 AI。基于产品规格，为一款【单文件 Web 应用】"
    "（HTML+CSS+JS，无构建步骤，可 localStorage 持久化）设计：\n"
    "1) UI 区块/组件清单  2) 数据模型字段  3) 交互与构建计划（250 字以内）。只输出设计本身。"
)
ENG_SYSTEM = (
    "你是 Alex，一名全栈工程师 AI。请构建一个完整、自包含的【单文件 Web 应用】。\n"
    "硬性要求：\n"
    "- 以 <!DOCTYPE html> 开头的有效 HTML5；内联 <style> 与 <script>；使用原生 JS。\n"
    "- 用 localStorage 做数据持久化；视觉现代、简洁、响应式、有设计感。\n"
    "- 实现规格里的【全部】功能；不要使用需要安装的框架/构建工具。\n"
    "- 轻量 CDN（如 chart.js）仅在确有需要时可用，优先自包含。\n"
    "只返回完整 HTML 文件本身，不要任何解释或代码围栏。"
)
REFINE_SYSTEM = (
    "你是 Alex，全栈工程师 AI。在【现有代码】基础上，根据用户的中文修改请求做【增量修改】，"
    "保持其余功能不变，返回修改后的【完整】单文件 HTML（以 <!DOCTYPE html> 开头）。不要解释。"
)
REV_SYSTEM = (
    "你是 Mike，资深代码评审 AI。请对照规格评审这份单文件 Web 应用，"
    "严格检查：功能是否缺失、JS 是否报错、是否做了持久化。\n"
    "只返回 JSON：{\"score\":0-100,\"issues\":[...],\"verdict\":\"approve\"|\"fix\","
    "\"patch_instructions\":\"若需修复的简短指引\"}"
)
FIX_SYSTEM = (
    "你是 Alex，全栈工程师 AI。按修复指引修改下方【完整】单文件 HTML。\n"
    "必须返回【完整的整个文件】，而不是改动片段、diff 或说明。\n"
    "回复的第一个字符必须是 <!DOCTYPE html>，最后一个字符必须是 </html>。不要任何解释。"
)

# 复杂/超长规格的「先规划后构建」：先把多特征需求拆成清晰模块清单，
# 再让工程师一次性产出覆盖全部模块的完整单文件应用（避免模型被长提示带偏而产出近空）。
PLAN_SYSTEM = (
    "你是资深架构师。用户给出一个功能繁多的产品想法，请把它拆解成【结构化实现计划】，"
    "中文，不超过 400 字：\n"
    "1) 应用名称  2) 必须实现的模块清单（每条一句话，3-8 条）  "
    "3) 页面分区（Header/主区/侧栏/页脚等） 4) 各模块如何用单文件 HTML+JS+localStorage 实现要点。"
    "只输出计划本身，不要写代码。"
)

# 复杂规格下工程师的系统提示（强调「完整、覆盖全部模块、不中途停止、标签闭合」）
ENG_COMPLEX_SYSTEM = (
    "你是 Alex，全栈工程师 AI。请构建一个【完整、自包含】的单文件 Web 应用，"
    "必须【一次性实现下方计划里的全部模块】，不要只做其中一部分。\n"
    "硬性要求：\n"
    "- 以 <!DOCTYPE html> 开头的有效 HTML5；内联 <style> 与 <script>；原生 JS。\n"
    "- 用 localStorage 持久化；视觉现代、简洁、响应式。\n"
    "- 即使功能很多，也要形成一个【完整可运行】的单一 HTML 文件，结尾必须是 </html>。\n"
    "- 不要输出代码片段、不要省略、不要写「此处省略」——必须给出完整文件。\n"
    "只返回完整 HTML 文件本身，不要任何解释或代码围栏。"
)

# 格式强制（Q3 防线：Structured Output）。当模型没有返回合法 HTML 时，用这条
# 修正指令再做一次「只输出 HTML」的尝试，避免直接降级成离线模板。
HTML_ONLY_RETRY = (
    "你的回复不是有效的 HTML 文件。请只输出以 <!DOCTYPE html> 开头、以 </html> 结尾的"
    "完整 HTML 代码，不要任何解释文字、不要 markdown 代码围栏、不要重复规格。"
)


def _eng_user(spec: str, arch: str) -> str:
    """把 SPEC/ARCH 明确标记为「只读上下文」，避免模型把它当成要复述/讨论的任务。"""
    return (
        "你是资深前端工程师。下面【仅作上下文参考】是产品规格(SPEC)与架构设计(ARCH)，"
        "请直接构建符合它们的【完整单文件 HTML 应用】。\n\n"
        "[上下文 SPEC]\n" + (spec or "") + "\n\n[上下文 ARCH]\n" + (arch or "") + "\n\n"
        "[任务] 只输出一个以 <!DOCTYPE html> 开头的完整 HTML 文件，内联 CSS/JS，使用原生 JS 与 "
        "localStorage 持久化。不要任何解释文字、不要 markdown 代码围栏、不要重复上面的规格内容。"
    )


def _strip_fences(t: str) -> str:
    """Strip ```lang ... ``` wrappers (handles missing closing fence too)."""
    if not t:
        return ""
    s = t.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        s = s[nl + 1:] if nl != -1 else ""
    if s.rstrip().endswith("```"):
        s = s.rstrip()[:-3].rstrip()
    return s.strip()


def _extract_html(t: str) -> str:
    """Strip fences AND any chatty prose the model wraps around the HTML.

    Models often reply with "Here is the file..." + a partial/odd fence, so we
    additionally slice from the first <!DOCTYPE (or <html) to the last </html>.
    """
    s = _strip_fences(t)
    low = s.lower()
    if "<!doctype" not in low and "<html" not in low:
        return s
    i = low.find("<!doctype")
    if i < 0:
        i = low.find("<html")
    if i < 0:
        return s
    body = s[i:]
    j = body.lower().rfind("</html>")
    if j >= 0:
        body = body[: j + len("</html>")]
    body = body.rstrip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    return body


def _valid_html(s: str, min_len: int = 500) -> bool:
    """Guard against models returning a fragment/diff instead of a full file."""
    if not s:
        return False
    low = s.lower().lstrip()
    has_open = low.startswith("<!doctype") or "<html" in low
    return has_open and "</html>" in low and len(s) >= min_len


def _extract_json(s: str):
    try:
        return json.loads(s)
    except Exception:  # noqa
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa
                return None
    return None


# ---------- offline template (no API key) ----------
def _mock_spec(idea: str) -> str:
    return (
        f"应用名称：{idea[:24] or 'My App'}\n"
        "核心功能：1) 展示核心信息 2) 可交互操作（计数/切换）3) 本地保存笔记\n"
        "页面区块：Header / 主卡片 / 互动区 / 笔记列表\n"
        "数据字段：notes(text), count(number)\n"
        "约束：单文件、响应式、localStorage 持久化"
    )


def _mock_arch(idea: str) -> str:
    return (
        "UI 区块：顶部标题栏、主功能卡片（计数器+状态切换）、笔记输入与列表\n"
        "数据模型：notes[]（文本+时间）、counter（数字）\n"
        "交互：点击计数、增删笔记、刷新后从 localStorage 恢复"
    )


def _mock_app(idea: str) -> str:
    # idea 为用户自由输入，必须 HTML 转义后再插入模板（adversarial case 会带 <script> 等）
    title = html.escape((idea or "My App")[:40])
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root{{--bg:#0b1020;--card:#141b33;--accent:#5b8cff;--accent2:#9b6bff;--txt:#e8ecff;}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:system-ui,'Segoe UI',sans-serif;background:radial-gradient(1200px 600px at 70% -10%,#1b2550,#0b1020);color:var(--txt);min-height:100vh;padding:24px}}
  .wrap{{max-width:760px;margin:0 auto}}
  header{{display:flex;align-items:center;gap:12px;margin-bottom:18px}}
  .logo{{width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent2))}}
  h1{{font-size:22px;margin:0}}
  .sub{{opacity:.7;font-size:13px}}
  .card{{background:var(--card);border:1px solid #243056;border-radius:16px;padding:20px;margin-bottom:16px}}
  .counter{{font-size:48px;font-weight:800;text-align:center;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}}
  .row{{display:flex;gap:10px;justify-content:center;margin-top:10px}}
  button{{border:0;border-radius:10px;padding:10px 16px;font-size:14px;cursor:pointer;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font-weight:600}}
  button.ghost{{background:#22305c;color:var(--txt)}}
  textarea{{width:100%;height:64px;border-radius:10px;border:1px solid #2a375f;background:#0e1530;color:var(--txt);padding:10px;resize:vertical}}
  .note{{display:flex;justify-content:space-between;align-items:center;background:#0e1530;border:1px solid #243056;border-radius:10px;padding:10px 12px;margin-top:8px}}
  .note small{{opacity:.5}}
  .del{{background:#3a1d2a;color:#ff9bb0}}
  .tag{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;background:#1d2a52;color:#9fb4ff;margin-left:8px}}
</style>
</head>
<body>
<div class="wrap">
  <header><div class="logo"></div><div><h1>{title}<span class="tag">Atoms Native · 离线模板</span></h1><div class="sub">由 AI Agent 团队生成 · 本版本为无 Key 离线模板</div></div></header>
  <div class="card">
    <div class="counter" id="c">0</div>
    <div class="row"><button id="inc">+1</button><button class="ghost" id="dec">-1</button><button class="ghost" id="reset">重置</button></div>
  </div>
  <div class="card">
    <h3 style="margin:0 0 10px">📝 我的笔记</h3>
    <textarea id="ta" placeholder="写点什么，自动保存到本地…"></textarea>
    <div class="row" style="justify-content:flex-start"><button id="add">添加笔记</button></div>
    <div id="list"></div>
  </div>
</div>
<script>
  const $ = s => document.querySelector(s);
  let count = +(localStorage.getItem('cnt')||0);
  const render = ()=>{{ $('#c').textContent=count; localStorage.setItem('cnt',count); }};
  $('#inc').onclick=()=>{{count++;render();}};
  $('#dec').onclick=()=>{{count--;render();}};
  $('#reset').onclick=()=>{{count=0;render();}};
  let notes = JSON.parse(localStorage.getItem('notes')||'[]');
  function draw(){{ $('#list').innerHTML = notes.map((n,i)=>`<div class="note"><span>${{n.t}}</span><span><small>${{n.d}}</small> <button class="del" data-i="${{i}}">删除</button></span></div>`).join('');
    document.querySelectorAll('.del').forEach(b=>b.onclick=()=>{{notes.splice(+b.dataset.i,1);localStorage.setItem('notes',JSON.stringify(notes));draw();}}); }}
  $('#add').onclick=()=>{{ const v=$('#ta').value.trim(); if(!v)return; notes.unshift({{t:v,d:new Date().toLocaleString()}}); localStorage.setItem('notes',JSON.stringify(notes)); $('#ta').value=''; draw(); }};
  render(); draw();
</script>
</body>
</html>"""


class PipelineFailure(Exception):
    """A safe, user-facing terminal failure (never a provider exception body)."""


def run_pipeline(idea: str, model: str | None = None, refine_code: str | None = None,
                 refine_msg: str | None = None, base_spec: str | None = None,
                 base_arch: str | None = None, project_id: int | None = None):
    """Bounded generation; only approved/degraded output is deliverable.

    status is independent of provenance (mock). Failed/unchanged refinements keep
    their input code but MUST NOT be persisted as a new version by callers.
    """
    model = model or LLM_PROVIDER
    mock = not provider_available(model)
    t_start = time.time()
    trace_id = obs.corr_id()
    refining = refine_code is not None
    spec, arch = base_spec or "", base_arch or ""
    calls = 0
    try:
        budget = max(1, min(12, int(os.environ.get("ATOMS_MAX_LLM_CALLS", "8"))))
    except ValueError:
        budget = 8
    yield {"type": "trace", "trace_id": trace_id,
           "prompt_hash": obs.prompt_hash(idea), "model": model}

    def result(status, code, error=None, verdict="unreviewed"):
        return {"spec": spec, "arch": arch, "code": code, "model": model,
                "mock": mock, "status": status, "error": error,
                "call_count": calls, "verdict": verdict,
                "security": security.scan_html(code) if code else {}}

    def _agent(agent: str, messages: list, max_tokens: int = 1500):
        nonlocal calls
        if calls >= budget:
            raise PipelineFailure(f"本轮已达到 {budget} 次模型调用上限，已停止，不再重试。")
        calls += 1
        attrs = {
            "gen_ai.agent.name": agent,
            "gen_ai.request.model": model,
            "gen_ai.prompt.hash": obs.prompt_hash(json.dumps(messages, ensure_ascii=False)),
        }
        with obs.span(agent, kind="AGENT", attributes=attrs):
            t0 = time.time()
            t, e = chat(model, messages, max_tokens=max_tokens)
            dt_ms = int((time.time() - t0) * 1000)
            toks = _tok(json.dumps(messages, ensure_ascii=False)) + _tok(t or "")
            # Provider errors may contain URLs/credentials: only safe summaries persist.
            safe_error = "模型调用失败，已停止本轮任务，请检查服务配置或稍后重试。" if e else None
            log_agent_run(project_id, None, agent, model,
                          json.dumps(messages, ensure_ascii=False)[:2000], (t or "")[:4000],
                          dt_ms, toks, False, safe_error, None)
            obs.record_run(agent, model, dt_ms, toks, False, None)
            if e:
                friction.record(project_id, "llm_error", f"{agent}: {safe_error}", session_id=trace_id)
                raise PipelineFailure(safe_error)
            return t or ""

    def _produce_html(system_prompt: str, user_prompt: str, label: str, max_tokens: int = 16000):
        for attempt in range(2):
            # Retain requirements, current code and requested edits on retry.
            prompt = user_prompt + ("\n\n" + HTML_ONLY_RETRY if attempt else "")
            c = _agent(label, [{"role": "system", "content": system_prompt},
                               {"role": "user", "content": prompt}], max_tokens=max_tokens)
            candidate = _extract_html(c)
            if _valid_html(candidate):
                return candidate
            if attempt == 0:
                friction.record(project_id, "format_retry", f"{label}: 格式修正重试", session_id=trace_id)
        return None

    def review(code):
        r = _agent("Reviewer", [{"role": "system", "content": REV_SYSTEM},
                               {"role": "user", "content": f"SPEC:\n{spec}\n\n本轮请求（优先验收）：\n{refine_msg or idea}\n\nCODE:\n{code}"}], max_tokens=1500)
        data = _extract_json(r)
        if (not isinstance(data, dict) or data.get("verdict") not in ("approve", "fix")
                or type(data.get("score")) not in (int, float) or not 0 <= data["score"] <= 100
                or not isinstance(data.get("issues"), list)
                or not all(isinstance(issue, str) for issue in data["issues"])
                or (data["verdict"] == "fix" and
                    (not isinstance(data.get("patch_instructions"), str) or not data["patch_instructions"].strip()))):
            raise PipelineFailure("评审未返回有效结论，未交付新版本。")
        return r, data

    try:
        if refining and not (refine_msg or "").strip():
            raise PipelineFailure("修改请求不能为空。")
        if mock:
            friction.record(project_id, "mock_mode", "离线模式：未调用真实模型", session_id=trace_id)
            yield {"type": "system", "message": "离线模板模式：本次不会调用真实模型。"}
            if refining:
                return result("unchanged", refine_code, "离线模式无法执行精修，已保留上一版。")
        for agent, label, icon, prompt in (
                ("PM", "产品经理 · Emma", "📋", PM_SYSTEM),
                ("Architect", "架构师 · Bob", "🏗️", ARCH_SYSTEM)):
            yield {"type": "agent_start", "agent": agent, "label": label, "icon": icon}
            value = spec if agent == "PM" else arch
            if not (refining and value):
                value = ((_mock_spec(idea) if agent == "PM" else _mock_arch(idea)) if mock else
                         _agent(agent, [{"role": "system", "content": prompt},
                                        {"role": "user", "content": idea if agent == "PM" else spec}]))
            if not value.strip():
                raise PipelineFailure(f"{agent} 未产出有效上下文，已停止。")
            if agent == "PM":
                spec = value
            else:
                arch = value
            yield {"type": "agent_output", "agent": agent, "output": value}

        yield {"type": "agent_start", "agent": "Engineer", "label": "工程师 · Alex", "icon": "⚙️"}
        if mock:
            code = _mock_app(idea)
        elif refining:
            ctx = f"[SPEC]\n{spec}\n[ARCH]\n{arch}\n[当前代码]\n{refine_code}\n[用户修改请求]\n{refine_msg}"
            code = _produce_html(REFINE_SYSTEM, ctx, "Engineer")
            if code is None:
                raise PipelineFailure("精修未产出完整 HTML，已保留上一版。")
            if code.strip() == refine_code.strip():
                return result("unchanged", refine_code, "模型未产生代码变更，已保留上一版。")
        else:
            ctx = _eng_user(spec, arch)
            complex_spec = _tok(spec) > 1200 or len(idea) > 350
            if complex_spec:
                plan = _agent("Planner", [{"role": "system", "content": PLAN_SYSTEM},
                                          {"role": "user", "content": f"{idea}\n{spec}"}], max_tokens=1200)
                ctx += "\n[实现计划]\n" + plan
            code = _produce_html(ENG_COMPLEX_SYSTEM if complex_spec else ENG_SYSTEM,
                                 ctx, "Engineer", max_tokens=28000 if complex_spec else 16000)
            if code is None:
                mock = True
                code = _mock_app(idea)
                friction.record(project_id, "fell_back", "格式修正失败，明确降级离线模板", session_id=trace_id)
                yield {"type": "system", "message": "生成内容两次格式校验失败，已降级离线模板，本次并非真实生成。"}
        obs.record_ttft(model, int((time.time() - t_start) * 1000))
        yield {"type": "agent_output", "agent": "Engineer", "output": code[:1800]}
        yield {"type": "agent_start", "agent": "Reviewer", "label": "评审 · Mike", "icon": "🔍"}
        verdict = "unreviewed"
        if mock:
            yield {"type": "agent_output", "agent": "Reviewer", "output": "离线模板：未进行真实模型评审。"}
        else:
            for attempt in range(2):
                text, data = review(code)
                yield {"type": "agent_output", "agent": "Reviewer", "output": text}
                verdict = data["verdict"]
                if verdict == "approve":
                    break
                if attempt:
                    raise PipelineFailure("修复后复审仍未通过，已停止，不再循环调用。")
                friction.record(project_id, "review_fix", data["patch_instructions"][:200], session_id=trace_id)
                yield {"type": "agent_start", "agent": "Engineer", "label": "工程师 · Alex（修复）", "icon": "⚙️"}
                ctx = f"[SPEC]\n{spec}\n[用户修改请求]\n{refine_msg or idea}\n[修复指引]\n{data['patch_instructions']}\n[当前代码]\n{code}"
                code = _produce_html(FIX_SYSTEM, ctx, "Engineer-Fix")
                if code is None:
                    friction.record(project_id, "fix_failed", "修复未产出完整 HTML", session_id=trace_id)
                    raise PipelineFailure("修复未产出完整 HTML，未交付新版本。")
                yield {"type": "agent_output", "agent": "Engineer", "output": code[:1800]}
                yield {"type": "agent_start", "agent": "Reviewer", "label": "评审 · Mike（复审）", "icon": "🔍"}
        if refining and code.strip() == refine_code.strip():
            return result("unchanged", refine_code, "修复后代码无变更，已保留上一版。", verdict)
        final = result("degraded" if mock else "success", code, verdict=verdict)
        sec = final["security"]
        yield {"type": "security", "score": sec["score"], "findings": sec["findings"], "summary": security.summarize(sec)}
        # Transport releases app_code only AFTER the atomic version commit.
        yield {"type": "app_code", "code": code}
        return final
    except PipelineFailure as exc:
        friction.record(project_id, "fell_back", str(exc), session_id=trace_id)
        return result("failed", refine_code or "", str(exc))
