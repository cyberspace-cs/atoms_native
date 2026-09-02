#!/usr/bin/env python3
"""前端测试层：静态接线检查（纯 stdlib，无浏览器依赖）。

覆盖三类真实问题：
  A. DOM 接线   —— app.js 引用的每个 $("id") 必须在 studio.html 中真实存在，
                   防止「按钮/面板改了 id 但 JS 悄悄失效」
  B. API 接线   —— 前端 fetch 的每个 /api/ 路径必须能匹配后端路由，
                   防止「前端先上线、后端没跟上」的 404
  C. 回归守卫   —— 今天修过的「一直显示生成任务」类 bug 用断言钉死：
                   三处 streamPost 调用必须 finally 解除 busy；error 事件必须停表

用法：python tests/frontend_checks.py   （exit 0 = 全过）
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
FAILS = []
CHECKS = 0


def check(name: str, ok: bool, detail: str = ""):
    global CHECKS
    CHECKS += 1
    print(f"{'✅' if ok else '❌'} {name}" + (f" —— {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def ids_in_html(html: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html))


def js_dynamic_ids(js: str) -> set[str]:
    """app.js 内 JS 生成的 HTML 字符串里的 id（不在静态 HTML 中属正常）。"""
    return set(re.findall(r'id=\\?"([a-zA-Z][\w-]*)\\?"', js))


def js_id_refs(js: str) -> set[str]:
    return set(re.findall(r'\$\("([^"]+)"\)', js))


def routes_in_main(main_py: str) -> set[str]:
    return {re.sub(r"\{[^}]+\}", "*", m) for m in
            re.findall(r'@app\.(?:get|post|put|delete)\("([^"]+)"\)', main_py)}


def js_api_paths(js: str) -> set[str]:
    """app.js 里的 API 字面量；字符串拼接产生的动态尾部按前缀处理。"""
    raw = set(re.findall(r'["\'](\.?/api/[^"\']*)["\']', js))
    out = set()
    for p in raw:
        p = p.removeprefix("./")
        if not p.startswith("/"):
            p = "/" + p
        # 以 / 结尾或含 + 拼接的视为前缀
        out.add(p.rstrip("/") if "${" in p or p.endswith("/") else p)
    return out


def path_matches(route_set: set[str], path: str) -> bool:
    for r in route_set:
        rx = re.escape(r).replace(r"\*", "[^/]+")
        if re.fullmatch(rx, path) or path.startswith(r.rstrip("*")):
            return True
    return False


# ---------- A. DOM 接线 ----------
studio = (PUBLIC / "studio.html").read_text(encoding="utf-8")
app_js = (PUBLIC / "app.js").read_text(encoding="utf-8")
html_ids = ids_in_html(studio) | js_dynamic_ids(app_js)
missing = sorted(i for i in js_id_refs(app_js) if i not in html_ids)
check("A1 app.js 引用的所有 id 在 studio.html 中存在", not missing, f"缺失: {missing}")

inline_ids = set(re.findall(r"getElementById\('([^']+)'\)", studio))
missing_inline = sorted(i for i in inline_ids if i not in html_ids)
check("A2 studio.html 内联脚本引用的 id 存在", not missing_inline, f"缺失: {missing_inline}")

check("A3 生成按钮存在且可被禁用",
      'id="genBtn"' in studio and "genBtn" in app_js)
check("A4 预览 iframe 带 sandbox 隔离",
      re.search(r'<iframe[^>]+sandbox="[^"]+"', studio) is not None)
check("A5 预览 iframe 有可访问标题", 'title="preview"' in studio)

# ---------- B. API 接线 ----------
main_py = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
routes = routes_in_main(main_py)
for page in ("studio", "discover", "plan", "admin", "index"):
    f = PUBLIC / f"{page}.html"
    if not f.exists():
        continue
    txt = f.read_text(encoding="utf-8")
    paths = js_api_paths(txt)
    bad = [p for p in paths if not path_matches(routes, p)]
    check(f"B {page}.html 的 API 路径全部可匹配后端路由", not bad, f"未匹配: {bad} | 路由数={len(routes)}")

js_paths = js_api_paths(app_js)
bad_js = [p for p in js_paths if not path_matches(routes, p)]
check("B2 app.js 的 API 路径全部可匹配后端路由", not bad_js, f"未匹配: {bad_js}")

# ---------- C. 回归守卫：busy 状态必须总能解除 ----------
# C1 三处 streamPost 调用（generate/refine/race）都必须 finally setBusy(false)
stream_calls = len(re.findall(r"await streamPost\(", app_js))
finally_busy = len(re.findall(r"finally \{ setBusy\(false\); \}", app_js))
check("C1 三处 SSE 调用都有 finally 解除 busy（流断/无 done 事件不卡死）",
      stream_calls == 3 and finally_busy == 3, f"streamPost={stream_calls}, finally={finally_busy}")

# C2 error 事件处理必须停表（setBusy(false)），否则「一直显示生成任务」复发
err_block = re.search(r'case "error":.*?break;', app_js, flags=re.S)
check("C2 SSE error 事件会解除 busy 并停掉 running 动画",
      bool(err_block) and "setBusy(false)" in err_block.group(0)
      and "markAllStopped" in err_block.group(0))

# C3 markAllStopped 必须清除 .agent.running（含 spec 卡）
helper = re.search(r"function markAllStopped\(\).*?\n  \}", app_js, flags=re.S)
check("C3 markAllStopped 清除 running 态", bool(helper)
      and '.agent.running' in helper.group(0))

# C4 关键 CSS 类存在（busy 状态徽章 / running 圆点 / toast 显示）
css = (PUBLIC / "styles.css").read_text(encoding="utf-8")
for cls in (".status.busy", ".agent.running", ".toast.show", "details.race-card"):
    check(f"C4 样式类存在: {cls}", cls in css)

# ---------- D. 结构守卫（防编辑器旧缓冲把结构改回去；空白容忍，容忍格式化折行） ----------
check("D1 studio.html 主区无 team-card（团队详情移至 team.html）", "team-card" not in studio)
check("D2 studio.html Race 为 details 折叠面板",
      bool(re.search(r'<details\s+class="race-card', studio))
      and "raceBtn" in studio)
team_html = (PUBLIC / "team.html").read_text(encoding="utf-8")
check("D3 team.html 存在且含四位成员卡", team_html.count('class="member-card"') == 4)
check("D4 studio.html 有 AI 团队入口链接", 'href="./team.html"' in studio)

# ---------- 汇总 ----------
print()
if FAILS:
    print(f"❌ {len(FAILS)}/{CHECKS} 项失败: {FAILS}")
    sys.exit(1)
print(f"✅ 前端接线检查 {CHECKS} 项全部通过。")
