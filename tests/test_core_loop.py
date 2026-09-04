"""Failure-path contracts. Only the external LLM is replaced; SQLite/API are real."""
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
# Set BEFORE config import: never inherit personal credentials or production data.
TMP = tempfile.TemporaryDirectory(prefix='atoms-core-')
os.environ.update(DEEPSEEK_API_KEY='', OPENROUTER_API_KEY='', OPENAI_COMPAT_API_KEY='',
                  REDIS_URL='', DB_PATH=os.path.join(TMP.name, 'core.db'),
                  AUDIT_WORM_PATH=os.path.join(TMP.name, 'audit.log'))
import config
import database
database.DB_PATH = config.DB_PATH = os.path.join(TMP.name, 'core.db')
import main
from agent import pipeline, llm
from fastapi.testclient import TestClient

BASE = '<!DOCTYPE html><html><body><h1>Original</h1>' + '<p>Keep my notes</p>' * 40 + '</body></html>'
CHANGED = BASE.replace('Original', 'Green')
FIXED = BASE.replace('Original', 'Fixed')
APPROVE = json.dumps({'score': 90, 'issues': [], 'verdict': 'approve', 'patch_instructions': ''})
FIX = json.dumps({'score': 40, 'issues': ['button broken'], 'verdict': 'fix', 'patch_instructions': 'fix button'})


def consume(gen):
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as end:
            return events, end.value


class CoreLoop(unittest.TestCase):
    def setUp(self):
        self.offline = patch.dict(os.environ, {'ATOMS_OFFLINE': '0', 'ATOMS_MAX_LLM_CALLS': '8'})
        self.offline.start()
        self.addCleanup(self.offline.stop)
        self.available = patch.object(pipeline, 'provider_available', return_value=True)
        self.available.start()
        self.addCleanup(self.available.stop)

    def run_case(self, replies, refine=True):
        self.prompts = []
        values = iter(replies)
        def reply(model, messages, **kwargs):
            self.prompts.append(messages)
            value = next(values)
            return value if isinstance(value, tuple) else (value, None)
        args = dict(idea='a notes app', model='deepseek')
        if refine:
            args.update(refine_code=BASE, refine_msg='change title to Green',
                        base_spec='keep notes and edit title', base_arch='single file')
        with patch.object(pipeline, 'chat', side_effect=reply):
            return consume(pipeline.run_pipeline(**args))

    def test_retry_keeps_original_code_and_request(self):
        _, result = self.run_case(['invalid', CHANGED, APPROVE])
        self.assertIn(BASE, str(self.prompts[1]))
        self.assertIn('change title to Green', str(self.prompts[1]))
        self.assertEqual(result['code'], CHANGED)

    def test_invalid_refine_is_failed_not_real_success(self):
        _, result = self.run_case(['invalid', 'invalid', APPROVE])
        self.assertEqual(result.get('status'), 'failed')
        self.assertEqual(result['code'], BASE)
        self.assertEqual(len(self.prompts), 2, 'do not review unchanged old code')

    def test_unchanged_refine_skips_review(self):
        _, result = self.run_case([BASE, APPROVE])
        self.assertEqual(result.get('status'), 'unchanged')
        self.assertEqual(len(self.prompts), 1)

    def test_invalid_review_never_approves(self):
        for review in ['garbage', '{}', '[]', '[1]', '{"verdict":"approve"}',
                       '{"score":999,"issues":[],"verdict":"approve"}',
                       '{"score":true,"issues":[],"verdict":"approve"}']:
            with self.subTest(review=review):
                _, result = self.run_case([CHANGED, review])
                self.assertEqual(result.get('status'), 'failed')
                self.assertEqual(result['code'], BASE)

    def test_fix_is_reviewed_before_delivery(self):
        _, result = self.run_case([CHANGED, FIX, FIXED, APPROVE])
        self.assertEqual(result.get('status'), 'success')
        self.assertEqual(result['verdict'], 'approve')
        self.assertEqual(result['code'], FIXED)
        self.assertEqual(result.get('call_count'), 4)
        self.assertIn(FIXED, str(self.prompts[3]))

    def test_reviewer_sees_refinement_request(self):
        self.run_case([CHANGED, APPROVE])
        self.assertIn('change title to Green', str(self.prompts[1]))

    def test_real_eval_cannot_count_retained_code_as_success(self):
        from evals.runner import run_refine_fix
        with patch.object(pipeline, 'chat', side_effect=[('spec', None), ('arch', None),
                                                        ('bad', None), ('bad', None)]):
            code, mock, _ = run_refine_fix({'idea': 'notes', 'base_code': BASE,
                                           'message': 'change title'}, 'deepseek', False)
        self.assertEqual(code, '')
        self.assertFalse(mock)

    def test_second_rejection_preserves_base(self):
        _, result = self.run_case([CHANGED, FIX, FIXED, FIX])
        self.assertEqual(result.get('status'), 'failed')
        self.assertEqual(result['code'], BASE)
        self.assertEqual(len(self.prompts), 4)

    def test_failed_fix_preserves_base(self):
        _, result = self.run_case([CHANGED, FIX, 'bad', 'bad'])
        self.assertEqual(result.get('status'), 'failed')
        self.assertEqual(result['code'], BASE)

    def test_provider_error_stops_without_format_retry(self):
        _, result = self.run_case([(None, '429 limited'), 'bad', APPROVE])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(result.get('status'), 'failed')

    def test_generation_provider_failure_is_not_offline_success(self):
        _, result = self.run_case([(None, '401 invalid'), 'arch', BASE, APPROVE], refine=False)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(result.get('status'), 'failed')

    def test_generation_normal_and_format_fallback(self):
        _, result = self.run_case(['spec', 'arch', CHANGED, APPROVE], refine=False)
        self.assertEqual(result.get('status'), 'success')
        self.assertEqual(result.get('call_count'), 4)
        _, result = self.run_case(['spec', 'arch', 'bad', 'bad', APPROVE], refine=False)
        self.assertEqual(result.get('status'), 'degraded')
        self.assertTrue(result['mock'])
        self.assertEqual(len(self.prompts), 4)

    def test_budget_stops_before_extra_request(self):
        with patch.dict(os.environ, {'ATOMS_MAX_LLM_CALLS': '2'}):
            _, result = self.run_case([CHANGED, FIX, FIXED, APPROVE])
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(result.get('status'), 'failed')
        self.assertEqual(result.get('call_count'), 2)

    def test_offline_refine_is_explicitly_unchanged(self):
        with patch.object(pipeline, 'provider_available', return_value=False):
            _, result = self.run_case([])
        self.assertEqual(result.get('status'), 'unchanged')
        self.assertEqual(result['code'], BASE)
        self.assertTrue(result['mock'])


class OfflineBoundary(unittest.TestCase):
    def test_offline_blocks_even_configured_provider_at_http_boundary(self):
        provider = {'base_url': 'https://example.invalid', 'key': 'fake-test-key', 'model': 'test'}
        with patch.dict(llm.PROVIDER_CONFIG, {'deepseek': provider}), \
             patch.dict(os.environ, {'ATOMS_OFFLINE': '1'}), \
             patch.object(llm.httpx, 'post', side_effect=AssertionError('Network is forbidden')) as request:
            text, error = llm.chat('deepseek', [])
            self.assertIsNone(text)
            self.assertIsNotNone(error)
            self.assertFalse(llm.provider_available('deepseek'))
            self.assertEqual(request.call_count, 0)


class ApiLoop(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        username = 'test_' + os.urandom(6).hex()
        token = self.client.post('/api/auth/register', json={'username': username, 'password': 'TestPass123'}).json()['token']
        self.headers = {'Authorization': 'Bearer ' + token}
        self.pid = self.client.post('/api/projects', headers=self.headers,
                                   json={'title': 'Test', 'idea': 'notes'}).json()['project']['id']
        self.client.post('/api/generate', headers=self.headers, json={'project_id': self.pid, 'model': 'mock_test'})
        self.before = self.project()

    def tearDown(self):
        self.client.close()
        gc.collect()  # legacy execute helpers retain SQLite handles until collection

    def project(self):
        return self.client.get(f'/api/projects/{self.pid}', headers=self.headers).json()

    def refine(self, replies):
        with patch.object(pipeline, 'provider_available', return_value=True), \
             patch.object(pipeline, 'chat', side_effect=[(r, None) for r in replies]):
            r = self.client.post('/api/refine', headers=self.headers,
                                 json={'project_id': self.pid, 'message': 'change title', 'model': 'deepseek'})
        return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: ')]

    def test_failed_refine_does_not_create_or_select_version(self):
        events = self.refine(['bad', 'bad', APPROVE])
        after = self.project()
        self.assertEqual(len(after['versions']), 1)
        self.assertEqual(after['project']['current_version'], self.before['project']['current_version'])
        self.assertEqual(after['current_code'], self.before['current_code'])
        self.assertEqual(events[-1]['type'], 'error')
        self.assertFalse(any(e['type'] == 'done' for e in events))
        self.assertFalse(any(e['type'] == 'app_code' for e in events))

    def test_commit_failure_rolls_back_version_and_message(self):
        conn = database.get_conn()
        conn.execute(f"CREATE TRIGGER reject_commit BEFORE UPDATE ON projects WHEN NEW.id={self.pid} "
                     "BEGIN SELECT RAISE(ABORT, 'forced transaction failure'); END")
        conn.commit()
        conn.close()
        try:
            events = self.refine([CHANGED, APPROVE])
            self.assertEqual(events[-1]['type'], 'error')
            self.assertFalse(any(e['type'] == 'app_code' for e in events))
            after = self.project()
            self.assertEqual(len(after['versions']), 1)
            self.assertEqual(after['messages'], self.before['messages'])
            self.assertEqual(after['project']['current_version'], self.before['project']['current_version'])
        finally:
            conn = database.get_conn()
            conn.execute('DROP TRIGGER reject_commit')
            conn.commit()
            conn.close()

    def test_audit_failure_after_commit_does_not_claim_generation_failed(self):
        original = main.log_audit
        def failing_audit(user, action, *args, **kwargs):
            if action == 'refine_done':
                raise RuntimeError('audit unavailable')
            return original(user, action, *args, **kwargs)
        with patch.object(main, 'log_audit', side_effect=failing_audit):
            events = self.refine([CHANGED, APPROVE])
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(len(self.project()['versions']), 2)
        self.assertTrue(any(e['type'] == 'system' for e in events))

    def test_success_persists_provenance_once(self):
        events = self.refine([CHANGED, APPROVE])
        after = self.project()
        self.assertEqual(len(after['versions']), 2)
        self.assertEqual(after['current_code'], CHANGED)
        version = after['versions'][-1]
        self.assertEqual(version.get('status'), 'success')
        self.assertEqual(version.get('parent_version'), self.before['project']['current_version'])
        self.assertEqual(version.get('mock'), 0)
        self.assertEqual(version.get('call_count'), 2)
        self.assertEqual(events[-1].get('status'), 'success')

    def test_offline_generation_persists_honest_provenance(self):
        version = self.before['versions'][0]
        self.assertEqual(version.get('status'), 'degraded')
        self.assertEqual(version.get('mock'), 1)
        self.assertEqual(version.get('call_count'), 0)

    def test_unexpected_pipeline_exception_is_terminal_error_and_unlocks(self):
        with patch.object(pipeline, 'run_pipeline', side_effect=RuntimeError('private detail')):
            response = self.client.post('/api/refine', headers=self.headers,
                                        json={'project_id': self.pid, 'message': 'change'})
        self.assertIn('"type": "error"', response.text)
        self.assertNotIn('private detail', response.text)
        self.assertEqual(len(self.project()['versions']), 1)
        events = self.refine([CHANGED, APPROVE])
        self.assertEqual(events[-1]['type'], 'done')

    def test_stale_parent_cannot_overwrite_newer_version(self):
        # A concurrent rollback/selection changes the pointer after generation starts.
        def race_model(*args, **kwargs):
            conn = database.get_conn()
            conn.execute('UPDATE projects SET current_version=NULL WHERE id=?', (self.pid,))
            conn.commit()
            conn.close()
            return CHANGED, None
        with patch.object(pipeline, 'provider_available', return_value=True):
            replies = iter([CHANGED, APPROVE])
            def concurrent_reply(*args, **kwargs):
                value = next(replies)
                if value == CHANGED:
                    race_model()
                return value, None
            with patch.object(pipeline, 'chat', side_effect=concurrent_reply):
                response = self.client.post('/api/refine', headers=self.headers,
                                            json={'project_id': self.pid, 'message': 'change'})
        self.assertIn('"type": "error"', response.text)
        self.assertIsNone(self.project()['project']['current_version'])
        self.assertEqual(len(self.project()['versions']), 1)


if __name__ == '__main__':
    unittest.main()
