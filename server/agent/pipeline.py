"""Multi-agent generation pipeline: PM -> Architect -> Engineer -> Reviewer.

Implemented as a generator that yields SSE-style event dicts and returns a
final summary dict (captured via StopIteration.value by the caller).
"""
import json
import re

from agent.llm import chat, provider_available, LLM_PROVIDER

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
    title = (idea or "My App")[:40]
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


def run_pipeline(idea: str, model: str | None = None, refine_code: str | None = None,
                 refine_msg: str | None = None, base_spec: str | None = None,
                 base_arch: str | None = None):
    """Generator yielding event dicts; returns final summary dict."""
    model = model or LLM_PROVIDER
    mock = not provider_available(model)
    if mock:
        yield {"type": "system", "message": f"⚠️ 未检测到可用 LLM（{model}）或缺少 API Key，已切换离线模板模式——全流程仍可跑通。"}

    # 1) PM
    yield {"type": "agent_start", "agent": "PM", "label": "产品经理 · Emma", "icon": "📋"}
    if refine_code and refine_msg and base_spec:
        spec = base_spec
    elif mock:
        spec = _mock_spec(idea)
    else:
        spec, _ = chat(model, [{"role": "system", "content": PM_SYSTEM}, {"role": "user", "content": idea}])
        spec = spec or _mock_spec(idea)
    yield {"type": "agent_output", "agent": "PM", "output": spec}

    # 2) Architect
    yield {"type": "agent_start", "agent": "Architect", "label": "架构师 · Bob", "icon": "🏗️"}
    if refine_code and refine_msg and base_arch:
        arch = base_arch
    elif mock:
        arch = _mock_arch(idea)
    else:
        arch, _ = chat(model, [{"role": "system", "content": ARCH_SYSTEM}, {"role": "user", "content": spec}])
        arch = arch or _mock_arch(idea)
    yield {"type": "agent_output", "agent": "Architect", "output": arch}

    # 3) Engineer
    yield {"type": "agent_start", "agent": "Engineer", "label": "工程师 · Alex", "icon": "⚙️"}
    if refine_code and refine_msg:
        if mock:
            code = refine_code
        else:
            ctx = f"SPEC:\n{spec}\n\nARCH:\n{arch}\n\nCURRENT CODE:\n{refine_code}\n\nUSER REFINEMENT REQUEST:\n{refine_msg}"
            c, _ = chat(model, [{"role": "system", "content": REFINE_SYSTEM}, {"role": "user", "content": ctx}], max_tokens=6000)
            cand = _extract_html(c) if c else ""
            code = cand if _valid_html(cand) else refine_code
    else:
        if mock:
            code = _mock_app(idea)
        else:
            c, _ = chat(model, [{"role": "system", "content": ENG_SYSTEM}, {"role": "user", "content": f"SPEC:\n{spec}\n\nARCH:\n{arch}"}], max_tokens=6000)
            cand = _extract_html(c) if c else ""
            code = cand if _valid_html(cand) else _mock_app(idea)
    yield {"type": "agent_output", "agent": "Engineer", "output": code[:1800]}
    yield {"type": "app_code", "code": code}

    # 4) Reviewer
    yield {"type": "agent_start", "agent": "Reviewer", "label": "评审 · Mike", "icon": "🔍"}
    verdict = "approve"
    review_text = ""
    if mock:
        review_text = "【离线模式】模板应用结构完整、含 localStorage 持久化与基础交互，默认通过。"
    else:
        r, _ = chat(model, [{"role": "system", "content": REV_SYSTEM}, {"role": "user", "content": f"SPEC:\n{spec}\n\nCODE:\n{code}"}], max_tokens=1500)
        rj = _extract_json(r or "")
        review_text = r or "{}"
        verdict = (rj or {}).get("verdict", "approve")
    yield {"type": "agent_output", "agent": "Reviewer", "output": review_text}

    # 5) optional fix loop
    if verdict == "fix" and not mock:
        yield {"type": "agent_start", "agent": "Engineer", "label": "工程师 · Alex（修复）", "icon": "⚙️"}
        patch = (_extract_json(review_text) or {}).get("patch_instructions", "")
        c2, _ = chat(model, [{"role": "system", "content": FIX_SYSTEM}, {"role": "user", "content": f"Fix instructions: {patch}\n\nCURRENT CODE:\n{code}"}], max_tokens=6000)
        if c2:
            cand = _extract_html(c2)
            if _valid_html(cand):
                code = cand
                yield {"type": "app_code", "code": code}
                yield {"type": "agent_output", "agent": "Engineer", "output": code[:1800]}
            else:
                yield {"type": "agent_output", "agent": "Engineer",
                       "output": "（修复返回内容不是完整 HTML，已保留修复前的版本）"}

    return {"spec": spec, "arch": arch, "code": code, "model": model, "mock": mock, "verdict": verdict}
