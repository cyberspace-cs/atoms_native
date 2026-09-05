"""Browser contracts for the two home editions; never starts generation.

2026-09-05 测试补全（loop engineering，证据来源）：
- Lighthouse SEO 审计子集（title/meta-description/viewport/可达锚点/描述性链接文本）:
  https://unlighthouse.dev/learn-lighthouse/seo 、https://www.perfmasters.com/learn/seo
- a11y 自动化可靠子集（lang/title/label——WebAIM/Deque 指出自动化整体覆盖 30-57%，
  但这类结构性检查是其强项，适合 CI 回归）:
  https://www.davidmello.com/software-testing/test-automation/playwright-accessibility-testing-axe-lighthouse-limitations
- 网络/控制台卫生（pageerror + requestfailed + response.status>=400；
  注意 HTTP 404 走 response 事件而非 requestfailed，401/403 属鉴权探测应豁免）:
  https://runebook.dev/zh/docs/playwright/api/class-request/request-failure
"""
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.request import urlopen, Request
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('ATOMS_BASE', 'http://127.0.0.1:8088').rstrip('/')
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    console_errors = []
    bad_responses = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda msg: console_errors.append(msg.text) if msg.type == 'error' else None)
    page.on('response', lambda r: bad_responses.append((r.status, r.url))
            if r.url.startswith(BASE) and r.status >= 400 and r.status not in (401, 403) else None)

    def meta_contract(route, expected_title):
        """Lighthouse SEO 子集：title/meta-description/viewport/lang，且两版互不相同。"""
        page.goto(BASE + route)
        title = page.title()
        assert title == expected_title, (route, title)
        desc = page.locator('meta[name="description"]').get_attribute('content')
        assert desc and len(desc.strip()) >= 20, (route, desc)
        assert page.locator('meta[name="viewport"]').count() == 1, route
        assert page.evaluate('document.documentElement.getAttribute("lang")'), route
        return title, desc

    def assert_one_h1(route):
        page.goto(BASE + route)
        assert page.locator('h1').count() == 1, f'{route} 必须恰好一个 h1'
        assert page.locator('a[href]').evaluate_all(
            'els => els.every(e => e.textContent.trim().length > 0)'), f'{route} 存在空文本链接'

    def internal_links_resolve(route):
        """同源 .html 链接逐一 GET 200（Lighthouse http-status-code / crawlable-anchors）。"""
        page.goto(BASE + route)
        hrefs = page.locator('a[href]').evaluate_all(
            'els => els.map(e => e.getAttribute("href"))')
        checked = set()
        for href in hrefs:
            if href.startswith('#') or href.startswith('data:'):
                continue
            url = urljoin(BASE + '/', href).split('#')[0].split('?')[0]
            if not urlparse(url).path.startswith(urlparse(BASE + '/').path) or url in checked:
                continue
            checked.add(url)
            with urlopen(Request(url), timeout=10) as resp:
                assert resp.status == 200, (href, resp.status)

    # ---------- 1. 简约版：SEO/meta + 内容契约 + 跳转链接 ----------
    t1, d1 = meta_contract('/', 'Atoms Native — 把想法变成应用')
    assert_one_h1('/')
    internal_links_resolve('/')
    assert page.locator('form.composer[action="./studio.html"]').count() == 1
    assert page.locator('#idea').get_attribute('required') is not None
    assert page.locator('.ideas a').count() == 4, '简约版应有四个快捷场景'
    shortcut_hrefs = page.locator('.ideas a').evaluate_all('els => els.map(e => e.getAttribute("href"))')
    assert all(h.split('idea=', 1)[-1].strip() for h in shortcut_hrefs), '快捷场景必须带入非空 idea'
    page.locator('a.skip').focus()  # skip link 平时在视口外，键盘元素：聚焦后回车触发（模拟 Tab）
    page.keyboard.press('Enter')
    expect(page.locator('#idea')).to_be_focused()

    # ---------- 2. 详细版：SEO/meta + 内容契约 + 动效真实运行 ----------
    t2, d2 = meta_contract('/overview.html', 'Atoms Native — 详细版 · 了解产品')
    assert t1 != t2 and d1 != d2, '两版 title/description 必须互不相同（Lighthouse 唯一性）'
    assert_one_h1('/overview.html')
    internal_links_resolve('/overview.html')
    assert page.locator('.member-card').count() == 4, 'AI 团队必须四人'
    assert page.locator('#workflow .step').count() == 4, '工作流程必须四步'
    assert page.locator('details').count() == 3, '常见问题必须三条'
    assert page.locator('.stats .stat').count() == 4, '指标卡必须四张'
    page.wait_for_function('document.getElementById("typed").textContent.length > 0', timeout=4000)
    page.wait_for_selector('#termBody .tl', timeout=6000)  # 流程示意终端真的在打字
    page.locator('details summary').first.click()
    assert page.locator('details').first.get_attribute('open') is not None, 'FAQ 点击应展开'

    # ---------- 3. 网络/控制台卫生快照（静态页阶段；401/403 鉴权探测已豁免） ----------
    assert not bad_responses, f'静态页存在 4xx/5xx 资源: {bad_responses}'
    assert not console_errors, f'控制台报错: {console_errors}'
    console_errors.clear(); bad_responses.clear()  # 后续进入 Studio，鉴权 401 不再计入

    # ---------- 4. 原有旅程：切换/校验/带入/快捷场景/无 JS/三屏宽 ----------
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.goto(BASE + '/')
    expect(page.get_by_role('heading', level=1)).to_contain_text('能用的应用')
    page.get_by_role('button', name='开始构建').click()
    assert urlparse(page.url).path in ('', '/'), 'Empty input must stay on home'
    idea = '书单 & 进度 + 100% #中文 / <笔记>'
    page.locator('#idea').fill(idea)
    page.get_by_role('button', name='开始构建').click()
    assert parse_qs(urlparse(page.url).query)['idea'] == [idea]
    expect(page.locator('#idea')).to_have_value(idea)
    page.goto(BASE + '/')
    page.get_by_role('link', name='◷ 专注工具').click()
    expect(page.locator('#idea')).to_have_value('制作一个番茄钟，支持开始暂停、休息提醒和今日专注次数统计')
    assert '番茄钟' in page.locator('#idea').input_value()
    screenshots = Path(tempfile.mkdtemp(prefix='atoms-home-review-'))
    for width in (1440, 814, 375):
        page.set_viewport_size({'width': width, 'height': 1000})
        page.goto(BASE + '/')
        expect(page.get_by_role('link', name='详细版 ↗', exact=True)).to_be_visible()
        page.get_by_role('link', name='详细版 ↗', exact=True).click()
        expect(page.get_by_role('link', name='简约版 ↗')).to_be_visible()
        page.get_by_role('link', name='简约版 ↗').click()
        expect(page.locator('#idea')).to_be_visible()
        for route in ('index.html', 'overview.html'):
            page.goto(BASE + '/' + route)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (route, width)
            page.screenshot(path=str(screenshots / f'{route}-{width}.png'), full_page=True)
    context = browser.new_context(java_script_enabled=False)
    plain = context.new_page()
    plain.goto(BASE + '/')
    plain.locator('#idea').fill(idea)
    plain.get_by_role('button', name='开始构建').click()
    assert parse_qs(urlparse(plain.url).query)['idea'] == [idea]
    context.close()

    # ---------- 5. 全站双主题绑定（2026-09-05）：切换→跨页→刷新→切回 ----------
    page.goto(BASE + '/')
    page.evaluate('localStorage.removeItem("an_theme")')
    page.reload()
    assert page.evaluate('document.documentElement.dataset.theme') == 'light', '未选择时默认浅色'
    assert page.evaluate('localStorage.getItem("an_theme")') is None, '默认态不应写入 localStorage'
    page.click('#themeToggle')
    assert page.evaluate('document.documentElement.dataset.theme') == 'dark', '点击后应变深色'
    assert page.evaluate('localStorage.getItem("an_theme")') == 'dark', '选择应持久化到 localStorage'
    page.get_by_role('link', name='进入 Studio').click()  # 跨页：Studio 必须保持深色（绑定核心）
    page.wait_for_load_state()
    assert page.evaluate('document.documentElement.dataset.theme') == 'dark', '跨页颜色模式绑定失败'
    page.reload()
    assert page.evaluate('document.documentElement.dataset.theme') == 'dark', '刷新后应保持深色'
    page.click('#themeToggle')
    assert page.evaluate('document.documentElement.dataset.theme') == 'light', '切回应回到浅色'
    assert page.evaluate('localStorage.getItem("an_theme")') == 'light'
    page.evaluate('localStorage.removeItem("an_theme")')  # 还原默认态，不影响后续浏览器会话
    assert not console_errors, f'主题旅程控制台报错: {console_errors}'
    assert not bad_responses, f'主题旅程 4xx/5xx: {bad_responses}'

    assert not errors, errors
    browser.close()
    print('PASS: edition navigation, validation, idea handoff, shortcuts, no-JS form, 3 screen widths.')
    print('PASS: SEO/meta contracts, deep content (4 team/4 steps/3 FAQ/4 stats), typewriter+terminal, '
          'FAQ toggle, skip-link focus, internal links 200, console/network hygiene.')
    print('Screenshots:', screenshots)
