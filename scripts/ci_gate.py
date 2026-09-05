#!/usr/bin/env python3
"""Single mandatory offline gate for Windows/Linux and GitHub Actions.

Never uses project DB/.env credentials, never kills another server, never skips
browser checks. A missing runtime/dependency is RED, not a partial GREEN.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def offline_env(directory):
    env = dict(os.environ)
    env.update(ATOMS_OFFLINE='1', LLM_PROVIDER='mock_ci_unavailable',
               DEEPSEEK_API_KEY='', OPENROUTER_API_KEY='', OPENAI_COMPAT_API_KEY='',
               REDIS_URL='', DB_PATH=str(directory / 'gate.db'),
               AUDIT_WORM_PATH=str(directory / 'audit.log'),
               PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               HTTP_PROXY='', HTTPS_PROXY='', ALL_PROXY='',
               http_proxy='', https_proxy='', all_proxy='',
               NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    return env


def step(name, command, env, timeout=180):
    print(f'\n>> {name}', flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=timeout)


def main():
    py = sys.executable
    with tempfile.TemporaryDirectory(prefix='atoms-gate-') as tmp:
        directory = Path(tmp)
        env = offline_env(directory)
        checks = [
            ('Compile', [py, '-c', "import compileall; from pathlib import Path; roots=(Path('server'),Path('tests'),Path('scripts')); files=[p for root in roots for p in root.rglob('*.py') if 'venv' not in p.parts and '__pycache__' not in p.parts]; assert all(compileall.compile_file(str(p), quiet=1) for p in files)" ]),
            ('Unit tests', [py, 'tests/unit_tests.py']),
            ('Core failure contracts', [py, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v']),
            ('Gate negative controls', [py, 'tests/gate_test.py']),
            ('Frontend wiring', [py, 'tests/frontend_checks.py']),
            ('SSE behavior', ['node', '--test', 'tests/test_sse.js']),
            ('Regression guard', [py, 'tests/regression_guard.py']),
            ('Browser dependency', [py, '-c', 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); b.close(); p.stop()']),
        ]
        for name, command in checks:
            step(name, command, env)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env['ATOMS_BASE'] = f'http://127.0.0.1:{port}'
        report = str(directory / 'eval.json')
        with open(directory / 'server.log', 'w+', encoding='utf-8') as log:
            server = subprocess.Popen([py, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)],
                                      cwd=ROOT / 'server', env=env, stdout=log, stderr=log)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                ready = False
                for _ in range(60):
                    if server.poll() is not None:
                        break
                    try:
                        with opener.open(env['ATOMS_BASE'] + '/api/models', timeout=1) as response:
                            models = json.load(response)
                        if models.get('mock') is not True or models.get('available') != []:
                            raise RuntimeError('Server is not forcibly offline')
                        ready = True
                        break
                    except (OSError, ValueError):
                        time.sleep(.25)
                if not ready:
                    raise RuntimeError('Offline server failed to start')
                for name, command in [
                    ('HTTP smoke', [py, 'tests/smoke.py']),
                    ('Browser journeys', [py, 'tests/e2e_journeys.py']),
                    ('Homepage editions', [py, 'tests/homepage_journeys.py']),
                    ('UI loop', [py, 'tests/ui_loop.py']),
                    ('Offline eval harness', [py, 'server/evals/runner.py', '--runs', '2', '--model', 'mock_ci_unavailable', '--report', report]),
                    ('Offline eval gate', [py, 'scripts/eval_gate.py', '--expect-mock', '--report', report]),
                ]:
                    step(name, command, env)
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-5000:], file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
    print('\nGREEN: all mandatory offline gates passed. Real LLM calls: 0.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
        print(f'\nRED: {exc}', file=sys.stderr)
        sys.exit(1)
