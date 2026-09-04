# -*- coding: utf-8 -*-
"""UI 循环守护（loop engineering 第 1.5 层：视觉回归的真浏览器检测）。

背景：2026-09-04 浏览器审计发现 index 首页在中等宽度（~814px）横向溢出
（氛围光晕 right:-120px 撑出文档流，iOS Safari 会忽略 body 的
overflow-x:hidden，手机上页面可左右晃动）。本脚本把「页面不得横向溢出」
和「空态必须可见」编码为永久检查，任何页面改动后跑一遍即知回归。

覆盖：
  1. 7 个页面 × 3 个宽度（1440/814/375）：documentElement 不得横向溢出
  2. discover 搜索空态：无结果时 #empty 可见且回显关键词
  3. portfolio 无效用户空态：0 作品提示可见

用法：先起本地服务，再 ATOMS_BASE=... python tests/ui_loop.py
CI（ci_gate.sh 第 7 步）在 E2E 之后自动执行。
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("ATOMS_BASE", "http://127.0.0.1:8088")
PAGES = ["/", "discover.html", "studio.html", "team.html",
         "plan.html", "portfolio.html", "admin.html"]
WIDTHS = [1440, 814, 375]  # 814 = 审计抓到 index 溢出的宽度，钉住不放

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL.append((name, str(e)[:200]))
        print(f"  ❌ {name}: {str(e)[:160]}")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    # ── 1. 横向溢出检测（7 页 × 3 宽度）─────────────────────────
    print("[1] 横向溢出检测（1440 / 814 / 375）")
    for w in WIDTHS:
        page.set_viewport_size({"width": w, "height": 900})
        for path in PAGES:
            page.goto(f"{BASE}/{path}", wait_until="domcontentloaded")
            page.wait_for_timeout(600)  # 字体/图片/JS 渲染余量
            sw = page.evaluate("document.documentElement.scrollWidth")
            cw = page.evaluate("document.documentElement.clientWidth")
            check(f"{path or '/'} @ {w}px 无横向溢出（scroll {sw} ≤ view {cw}）",
                  lambda sw=sw, cw=cw: (_ for _ in ()).throw(
                      AssertionError(f"scrollWidth={sw} > clientWidth={cw}")) if sw > cw + 1 else None)

    # ── 2. discover 搜索空态 ────────────────────────────────────
    print("[2] discover 搜索空态")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{BASE}/discover.html", wait_until="domcontentloaded")
    page.wait_for_selector(".card")
    page.fill("#q", "绝对不存在的词ui_loop")
    page.wait_for_timeout(1000)  # 防抖 300ms + 请求往返
    empty_visible = page.evaluate(
        "(() => { const e = document.getElementById('empty');"
        " return e && e.style.display !== 'none' && e.offsetParent !== null; })()")
    empty_text = page.evaluate(
        "document.getElementById('empty') ? document.getElementById('empty').textContent : ''")
    check("无结果时空态可见且回显关键词",
          lambda: (_ for _ in ()).throw(
              AssertionError(f"visible={empty_visible}, text={empty_text!r}"))
          if not (empty_visible and "绝对不存在的词ui_loop" in empty_text) else None)

    # ── 3. portfolio 无效用户空态 ───────────────────────────────
    print("[3] portfolio 无效用户空态")
    page.goto(f"{BASE}/portfolio.html?u=ui_loop_nosuchuser", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    body_text = page.evaluate("document.body.innerText")
    check("无效用户显示空态提示（非白屏/报错）",
          lambda: (_ for _ in ()).throw(AssertionError("未见空态文案"))
          if not any(k in body_text for k in ("还没有发布作品", "暂无", "没有")) else None)

    browser.close()

print()
print(f"════ UI 循环守护：{len(PASS)} 通过 / {len(FAIL)} 失败 ════")
sys.exit(1 if FAIL else 0)
