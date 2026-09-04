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
# C1 两处 streamPost 调用（generate/refine）都必须 finally setBusy(false)
# （race 已于 2026-09-04 下线，不再有第三处）
stream_calls = len(re.findall(r"await streamPost\(", app_js))
finally_busy = len(re.findall(r"finally \{ setBusy\(false\); \}", app_js))
check("C1 两处 SSE 调用都有 finally 解除 busy（流断/无 done 事件不卡死）",
      stream_calls == 2 and finally_busy == 2, f"streamPost={stream_calls}, finally={finally_busy}")

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
for cls in (".status.busy", ".agent.running", ".toast.show"):
    check(f"C4 样式类存在: {cls}", cls in css)

# C5 busy 期间必须连带禁用 publish/share（生成未落库时点分享 404 / 发布半成品
#    ——2026-09-04 E2E 抓到的真实竞态）
busy_line = re.search(r"\[([^\]]*)\]\.forEach\(\(id\)", app_js)
busy_ids = busy_line.group(1) if busy_line else ""
check("C5 setBusy 连带禁用 publishBtn/shareBtn",
      all(f'"{i}"' in busy_line.group(0) for i in ("genBtn", "refineBtn", "publishBtn", "shareBtn")),
      f"setBusy 列表: {busy_ids}")

# ---------- D. 结构守卫（防编辑器旧缓冲把结构改回去；空白容忍，容忍格式化折行） ----------
check("D1 studio.html 主区无 team-card（团队详情移至 team.html）", "team-card" not in studio)
# D2（2026-09-04 反转）：Race 模式不对用户开放——前端任何页面不得出现 Race 入口
race_leftovers = []
for page in ("studio.html", "index.html", "team.html"):
    body = (PUBLIC / page).read_text(encoding="utf-8")
    if re.search(r"race-card|raceBtn|race-banner|api/race|Race Mode", body, flags=re.I):
        race_leftovers.append(page)
check("D2 前端页面无任何 Race 入口（Race 不对用户开放）", not race_leftovers,
      f"残留: {race_leftovers}")
team_html = (PUBLIC / "team.html").read_text(encoding="utf-8")
check("D3 team.html 存在且含四位成员卡", team_html.count('class="member-card"') == 4)
check("D4 studio.html 有 AI 团队入口链接", 'href="./team.html"' in studio)

# ---------- E. admin.html 管理端守卫 ----------
admin = (PUBLIC / "admin.html").read_text(encoding="utf-8")
check("E1 admin.html 三 Tab 存在（看板/用户/审计）",
      all(f'data-tab="{t}"' in admin for t in ("dash", "users", "audit")))
check("E2 admin.html 登录框与无权限卡存在",
      'id="auth"' in admin and 'id="noaccess"' in admin)
check("E3 admin.html 只用相对路径 ./api（子路径部署）",
      '"/api' not in admin and "'/api" not in admin and "'/admin/users'" in admin.replace("./api", ""))
check("E4 admin.html 链完整性徽章元素存在", 'id="chainbox"' in admin and "chain_intact" in admin)
check("E5 admin.html 角色切换调用 set-role",
      "/admin/set-role" in admin and "onchange" in admin)

# ---------- F. UI/UX a11y 守卫（loop engineering 2026-09-04，ui-ux-pro-max 标准） ----------
# F1 每个页面生效的 CSS（内联 style 或所链接的 .css 文件）都要有键盘焦点样式
#    + 尊重减弱动效偏好
ALL_PAGES = ("index.html", "discover.html", "studio.html", "team.html",
             "plan.html", "portfolio.html", "admin.html")
for page in ALL_PAGES:
    body = (PUBLIC / page).read_text(encoding="utf-8")
    css_all = body
    for link in re.findall(r'href="\./([a-z_]+\.css)"', body):
        p = PUBLIC / link
        if p.exists():
            css_all += p.read_text(encoding="utf-8")
    check(f"F1 {page} 生效 CSS 含 :focus-visible 与 prefers-reduced-motion",
          ":focus-visible" in css_all and "prefers-reduced-motion" in css_all)
# F2 index 氛围光晕必须在源头裁剪（iOS Safari 忽略 body 的 overflow-x:hidden，
#    曾导致手机上首页可左右晃动——2026-09-04 浏览器审计发现）
landing = (PUBLIC / "landing.css").read_text(encoding="utf-8")
hero_rule = re.search(r"\.hero\s*\{[^}]*\}", landing)
check("F2 landing.css html 与 .hero 均 overflow-x: clip（防移动端横向漂移）",
      "overflow-x: clip" in landing and bool(hero_rule)
      and re.search(r"overflow-x:\s*clip", hero_rule.group(0)))
# F3 低对比度灰色禁用（#475569 在 12px 文本上仅 2.6:1，低于 4.5 标准）
check("F3 landing.css 无低对比度灰 #475569/#64748b",
      "#475569" not in landing and "#64748b" not in landing)

# ---------- G. 发现页做细守卫（2026-09-04 模板扩充：对标 NoCode 品类） ----------
discover = (PUBLIC / "discover.html").read_text(encoding="utf-8")
check("G1 发现页可试玩角标：CSS 类与渲染逻辑同时存在",
      ".playable" in discover and "可试玩" in discover,
      "has_sample 模板必须在封面渲染「可试玩」角标")
check("G2 发现页分类 chips 带计数",
      "renderChips" in discover
      and re.search(r"filter\([^)]*category[^)]*\)\.length", discover) is not None,
      "分类 chip 应显示该分类下的模板数量")
check("G3 发现页示例链接在新窗口打开且隔离",
      'rel="noopener"' in discover and "/sample" in discover)

# ---------- 汇总 ----------
print()
if FAILS:
    print(f"❌ {len(FAILS)}/{CHECKS} 项失败: {FAILS}")
    sys.exit(1)
print(f"✅ 前端接线检查 {CHECKS} 项全部通过。")
