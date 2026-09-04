# -*- coding: utf-8 -*-
"""真浏览器 E2E 用户旅程（loop engineering 第 1 层防线）。

背景：2026-09-03 的 role 字段 bug（API 测试全绿、真浏览器 admin 被拒）证明
「人肉点页面」不可靠也不可沉淀。本脚本用 Playwright 驱动 Chromium 走完整
用户旅程，断言**页面上真实可见的文本**——前端消费什么，这里就验什么。

旅程：发现页(搜索/排序/空态) → 注册 → studio 生成(离线模板兜底) → 发布
→ 发现页可见新模板 → 另一账号 Remix → 分享链接免登录预览 → 作品集统计。

用法：先起本地服务（python -m uvicorn main:app --port 8088），
再 ATOMS_BASE=http://127.0.0.1:8088 python tests/e2e_journeys.py
CI（ci_gate.sh 第 7 步）会自动完成这两步。
"""
import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("ATOMS_BASE", "http://127.0.0.1:8088")
STAMP = str(int(time.time()))[-7:]  # 每次运行独立账号，可重复执行
USER_A = f"e2e_a_{STAMP}"   # 作者
USER_B = f"e2e_b_{STAMP}"   # Remix 者

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL.append((name, str(e)[:300]))
        print(f"  ❌ {name}: {str(e)[:200]}")
        raise


def run(page):
    # ── 1. 发现页：搜索 / 排序 / 空态 ─────────────────────────
    print("[1] 发现页")
    page.goto(f"{BASE}/discover.html")
    page.wait_for_selector(".card")
    check("发现页渲染模板卡片", lambda: expect(page.locator(".card").first).to_be_visible())

    page.fill("#q", "贪吃蛇")
    page.wait_for_timeout(1200)  # 防抖 300ms + 请求往返
    check("搜索「贪吃蛇」只剩 1 卡", lambda: expect(page.locator(".card")).to_have_count(1))
    check("搜索结果标题正确", lambda: expect(page.locator(".card .title").first)
          .to_contain_text("贪吃蛇"))

    page.fill("#q", "绝对不存在的词xyz")
    page.wait_for_timeout(1200)
    check("无结果空态带关键词回显", lambda: expect(page.locator("#empty"))
          .to_contain_text("绝对不存在的词xyz"))

    page.fill("#q", "")
    page.wait_for_timeout(1200)
    page.click("#sorter button[data-sort=new]")
    page.wait_for_timeout(800)
    check("最新排序后卡片恢复", lambda: expect(page.locator(".card").first).to_be_visible())

    # ── 2. 注册作者账号（studio 页内注册 tab）────────────────
    print("[2] 注册 + 登录")
    page.goto(f"{BASE}/studio.html")
    page.click("[data-tab=register]")
    page.fill("#username", USER_A)
    page.fill("#password", "E2ePass#2026")
    page.click("#authBtn")
    page.wait_for_timeout(1500)
    check("注册成功进入工作台", lambda: expect(page.locator("#main")).to_be_visible(timeout=5000))

    # ── 3. 生成（DeepSeek 不可用时走离线模板，同样落版本）─────
    print("[3] 生成应用")
    page.fill("#idea", f"一个倒计时小工具 {STAMP}")
    page.click("#genBtn")
    # 就绪信号：预览 iframe 被填（app_code 事件已到、state.code 已赋值）。
    # 不能只等按钮 enabled——那只是 DOM 移除 disabled，code 可能还没进 state
    # （2026-09-04 E2E 抓到的真实竞态，doPublish/doShare 已加兜底）。
    for _ in range(60):
        n = page.evaluate(
            "document.getElementById('preview') ? document.getElementById('preview').srcdoc.length : -1")
        if n and n > 100:
            break
        page.wait_for_timeout(1000)
    check("生成完成，预览已渲染", lambda: (_ for _ in ()).throw(
        AssertionError("preview srcdoc 未就绪")) if not (
            page.evaluate("document.getElementById('preview').srcdoc.length") > 100) else None)

    # ── 4. 发布到发现页 ──────────────────────────────────────
    print("[4] 发布")
    page.once("dialog", lambda d: d.accept())  # confirm 防误触（点击前注册）
    page.click("#publishBtn")
    page.wait_for_timeout(2500)
    # 刷新发现页验证落库
    req = urllib.request.Request(f"{BASE}/api/discover")
    items = json.loads(urllib.request.urlopen(req).read())["items"]
    mine = [i for i in items if i["author"] == USER_A]
    check("发布后出现在 /api/discover", lambda: (_ for _ in ()).throw(
        AssertionError(f"author={USER_A} not found")) if not mine else None)

    # ── 5. 发现页搜索作者模板 + 作者名链到作品集 ─────────────
    print("[5] 发现页可见 + 作品集链接")
    page.goto(f"{BASE}/discover.html")
    page.fill("#q", STAMP)  # 模板标题/idea 含时间戳，搜索命中
    page.wait_for_timeout(1200)
    check("搜索能找到自己的模板", lambda: expect(page.locator(".card").first).to_be_visible())
    check("作者名显示正确", lambda: expect(page.locator(".card .author").first)
          .to_contain_text(USER_A))
    page.click(".card .author")
    page.wait_for_timeout(1200)
    check("作者名点击跳到作品集页", lambda: expect(page.locator("#name")).to_have_text(USER_A))
    check("作品集统计被 Remix 为 0", lambda: expect(page.locator("#nUses")).to_have_text("0"))

    # ── 6. 另一账号 Remix（先注册登录，再去作品集点「使用此模板」）──
    print("[6] Remix（另一账号）")
    page.evaluate("localStorage.clear()")
    page.goto(f"{BASE}/studio.html")
    page.click("[data-tab=register]")
    page.fill("#username", USER_B)
    page.fill("#password", "E2ePass#2026")
    page.click("#authBtn")
    page.wait_for_timeout(1500)
    check("USER_B 注册进入工作台", lambda: expect(page.locator("#main")).to_be_visible(timeout=5000))
    # 已登录状态下去作品集点「使用此模板」→ 真正的 Remix
    page.goto(f"{BASE}/portfolio.html?u={USER_A}")
    page.wait_for_selector(".use")
    page.click(".use")
    page.wait_for_timeout(2500)
    req = urllib.request.Request(f"{BASE}/api/discover?author={USER_A}")
    items = json.loads(urllib.request.urlopen(req).read())["items"]
    uses = items[0]["uses"] if items else -1
    check("Remix 后 uses >= 1", lambda: (_ for _ in ()).throw(
        AssertionError(f"uses={uses}")) if uses < 1 else None)
    check("Remix 后跳到工作台新项目", lambda: expect(page.locator("#main")).to_be_visible())

    # ── 7. 分享链接免登录预览 ────────────────────────────────
    print("[7] 分享链接")
    # 用作者 token 生成分享链接（API 层；按钮已由单测覆盖，这里验证端到端可访问性）
    body = json.dumps({"username": USER_A, "password": "E2ePass#2026"}).encode()
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    token = json.loads(urllib.request.urlopen(req).read())["token"]
    # 找作者的第一个项目（项目列表接口）
    req = urllib.request.Request(f"{BASE}/api/projects",
                                 headers={"Authorization": f"Bearer {token}"})
    plist = json.loads(urllib.request.urlopen(req).read())
    projects = plist.get("projects", plist if isinstance(plist, list) else [])
    pid = projects[0]["id"] if isinstance(projects[0], dict) else projects[0]
    req = urllib.request.Request(f"{BASE}/api/projects/{pid}/share", method="POST",
                                 headers={"Authorization": f"Bearer {token}"})
    share_token = json.loads(urllib.request.urlopen(req).read())["token"]
    # 新 context（无 localStorage）免登录访问
    page.goto(f"{BASE}/api/share/{share_token}")
    content = page.content()
    if "<html" not in content.lower() and len(content) < 300:
        raise AssertionError("share page too small")
    PASS.append("分享链接免登录返回 HTML")
    print("  ✅ 分享链接免登录返回 HTML")

    # ── 8. 作品集统计最终态 ──────────────────────────────────
    print("[8] 作品集统计")
    page.goto(f"{BASE}/portfolio.html?u={USER_A}")
    # .stat 节点是静态 HTML（数字初始 0），不能当就绪信号——等异步 fetch 真正
    # 落地（nWorks >= 1，作者已发布过）再读数。2026-09-04 抓到的假就绪竞态。
    for _ in range(30):
        if int(page.locator("#nWorks").inner_text() or "0") >= 1:
            break
        page.wait_for_timeout(500)
    uses_n = int(page.locator("#nUses").inner_text())
    check("作品集被 Remix 数 >= 1", lambda: (_ for _ in ()).throw(
        AssertionError(f"nUses={uses_n}")) if uses_n < 1 else None)
    works_n = int(page.locator("#nWorks").inner_text())
    check("作品数 >= 1", lambda: (_ for _ in ()).throw(
        AssertionError(f"nWorks={works_n}")) if works_n < 1 else None)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            run(page)
            if errors:
                print(f"\n⚠️ 页面 JS 错误：{errors[:3]}")
        finally:
            browser.close()
    print(f"\n{'='*50}")
    print(f"E2E 旅程：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        for n, e in FAIL:
            print(f"  ❌ {n}: {e}")
        sys.exit(1)
    print("ALL GREEN ✅")


if __name__ == "__main__":
    main()
