"""Browser contracts for the two home editions; never starts generation."""
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('ATOMS_BASE', 'http://127.0.0.1:8088').rstrip('/')
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
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
    assert not errors, errors
    browser.close()
    print('PASS: edition navigation, validation, idea handoff, shortcuts, no-JS form, 3 screen widths.')
    print('Screenshots:', screenshots)
