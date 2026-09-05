"""Opt-in real generation/refinement check. Never included in offline CI.

Supply provider credentials through the process environment; --run is mandatory.
Uses a disposable database, enforces eight total upstream calls, records actual
provider usage, and checks the generated app inside the real Studio sandbox.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Authorize billable LLM requests')
    parser.add_argument('--model', default='deepseek')
    parser.add_argument('--replay', type=Path, help='Replay a saved run without any LLM calls')
    args = parser.parse_args()
    if not args.run and not args.replay:
        parser.error('--run is required; this check calls a real provider')
    directory = args.replay.resolve() if args.replay else Path(tempfile.mkdtemp(prefix='atoms-real-loop-'))
    source = json.loads((directory / 'report.json').read_text(encoding='utf-8')) if args.replay else None
    os.environ.update(ATOMS_OFFLINE='0', ATOMS_MAX_LLM_CALLS='6', REDIS_URL='',
                      DB_PATH=str(directory / 'loop.db'), AUDIT_WORM_PATH=str(directory / 'audit.log'))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
    from agent import llm
    if not args.replay and not llm.provider_available(args.model):
        raise RuntimeError('Requested provider is not configured')
    import main as server
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright, expect
    calls = []
    real_post = llm.httpx.post
    report = {'model': args.model, 'calls': calls, 'checks': [], 'passed': False}

    def measured_post(*a, **kw):
        if args.replay:
            raise RuntimeError('Replay forbids LLM requests')
        if len(calls) >= 8:
            raise RuntimeError('Real-loop total call budget exhausted')
        entry = {'index': len(calls) + 1, 'model': kw.get('json', {}).get('model')}
        calls.append(entry)
        print('Real LLM request', entry['index'], entry['model'], flush=True)
        started = time.monotonic()
        try:
            response = real_post(*a, **kw)
            entry['http_status'] = response.status_code
            if response.status_code == 200:
                usage = response.json().get('usage', {})
                entry['usage'] = {k: v for k, v in usage.items() if isinstance(v, (int, float))}
            return response
        finally:
            entry['seconds'] = round(time.monotonic() - started, 2)

    llm.httpx.post = measured_post
    idea = '做一个简约计数器。h1 标题为“小计数器”，数字元素 id=count 初始显示0。两个原生按钮文字为“加一”和“重置”，分别加1和归零。无需存储，不引用外部资源，单文件HTML。'
    try:
        with TestClient(server.app) as client, sync_playwright() as playwright:
            if args.replay:
                from database import get_conn
                connection = get_conn()
                project = connection.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()['id']
                token = connection.execute('SELECT token FROM sessions WHERE user_id=(SELECT user_id FROM projects WHERE id=?)', (project,)).fetchone()['token']
                connection.close()
            else:
                token = client.post('/api/auth/register', json={'username': 'loop_' + secrets.token_hex(4), 'password': secrets.token_urlsafe(16)}).json()['token']
            client.headers['Authorization'] = 'Bearer ' + token
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1280, 'height': 900})
            context.add_init_script('if (window === window.top && location.origin === "http://atoms.test") { localStorage.setItem("an_token", ' + json.dumps(token) + '); }')

            def route_request(route):
                request = route.request
                url = urlsplit(request.url)
                if url.hostname != 'atoms.test':
                    route.abort()
                    return
                response = client.request(request.method, url.path + ('?' + url.query if url.query else ''), content=request.post_data_buffer)
                route.fulfill(status=response.status_code, body=response.content,
                              headers={'content-type': response.headers.get('content-type', 'text/plain')})

            context.route('**/*', route_request)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('http://atoms.test/')
            page.locator('#idea').fill(idea)
            page.get_by_role('button', name='开始构建').click()
            expect(page.locator('#idea')).to_have_value(idea)
            report['checks'].append('homepage to Studio idea handoff')
            if not args.replay:
                project = client.post('/api/projects', json={'idea': idea, 'title': 'Real loop counter'}).json()['project']['id']

            def stage(kind, body, title):
                if args.replay:
                    terminal = source[kind]
                    client.post('/api/projects/' + str(project) + '/select-version', json={'version_id': terminal['version_id']}).raise_for_status()
                else:
                    response = client.post('/api/' + kind, json=body)
                    response.raise_for_status()
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                    terminal = events[-1]
                report[kind] = {k: terminal.get(k) for k in ('type', 'status', 'mock', 'call_count', 'version_id', 'security')}
                print(kind, json.dumps(report[kind]), flush=True)
                assert terminal.get('status') == 'success' and terminal.get('mock') is False, kind + ' did not deliver a real approved version'
                data = client.get('/api/projects/' + str(project)).json()
                (directory / (kind + '.html')).write_text(data['current_code'], encoding='utf-8')
                page.goto('http://atoms.test/studio.html?project=' + str(project))
                frame = page.frame_locator('iframe')
                expect(frame.get_by_role('heading', level=1)).to_have_text(title, timeout=15000)
                expect(frame.locator('#count')).to_have_text('0')
                frame.get_by_role('button', name='加一', exact=True).click()
                frame.get_by_role('button', name='加一', exact=True).click()
                expect(frame.locator('#count')).to_have_text('2')
                frame.get_by_role('button', name='重置', exact=True).click()
                expect(frame.locator('#count')).to_have_text('0')
                page.screenshot(path=str(directory / (kind + '.png')), full_page=True)
                report['checks'].append(kind + ': sandbox title, increment twice, reset')
                return data

            initial = stage('generate', {'project_id': project, 'model': args.model}, '小计数器')
            os.environ['ATOMS_MAX_LLM_CALLS'] = str(max(1, 8 - len(calls)))
            refined = stage('refine', {'project_id': project, 'model': args.model,
                                      'message': '只把h1标题改为“今日计数”，保留count元素、加一和重置按钮的功能。'}, '今日计数')
            assert len(refined['versions']) == len(initial['versions']) + (0 if args.replay else 1)
            assert initial['project']['current_version'] != refined['project']['current_version']
            report['browser_errors'] = errors
            assert not errors, 'Browser JavaScript errors: ' + repr(errors)
            report['checks'].append('saved version pointers verified' if args.replay else 'one new refinement version and current pointer updated')
            report['passed'] = True
            browser.close()
    finally:
        llm.httpx.post = real_post
        name = 'replay-report.json' if args.replay else 'report.json'
        (directory / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Report:', directory / name, flush=True)
        print('Total upstream calls:', len(calls), flush=True)


if __name__ == '__main__':
    main()
