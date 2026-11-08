from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from prompt_polish import config, dsh, llm


class BridgeSafetyTests(unittest.TestCase):
    def test_drafts_use_stdin_not_command_line(self):
        draft = '中文 "quoted"\n' + 'x' * 40000
        with patch.object(dsh, '_run', return_value=(0, '{"ok":true}', '')) as run:
            result = dsh.call('apply', 1, config.Config(), expected_draft=draft, new_text='新结果')
        self.assertTrue(result.ok)
        args, _, payload = run.call_args.args
        self.assertNotIn(draft, args)
        self.assertEqual(json.loads(payload), {'expected_draft': draft, 'new_text': '新结果'})

    def test_apply_timeout_does_not_claim_unchanged(self):
        with patch.object(dsh, '_run', side_effect=subprocess.TimeoutExpired('bridge', 1)):
            result = dsh.call('apply', 1, config.Config())
        self.assertEqual(result.code, 'timeout')
        self.assertIn('状态未知', result.message)


class EndpointSafetyTests(unittest.TestCase):
    def test_insecure_remote_endpoint_rejected(self):
        with self.assertRaises(llm.LLMError):
            llm.endpoint('http://example.com/v1')

    def test_local_http_allowed(self):
        self.assertEqual(llm.endpoint('http://127.0.0.1:1234/v1'), 'http://127.0.0.1:1234/v1/chat/completions')

    def test_credentials_and_query_rejected(self):
        for url in ('https://user:pass@example.com', 'https://example.com/v1?token=x'):
            with self.subTest(url=url), self.assertRaises(llm.LLMError):
                llm.endpoint(url)

    def test_conversation_budget_rejected_before_request(self):
        cfg = config.Config(max_input_chars=100)
        with self.assertRaises(llm.LLMError) as error:
            llm.chat([{'role': 'user', 'content': 'x' * 401}], cfg, 'test-key')
        self.assertEqual(error.exception.code, 'too_long')

    def test_redirects_are_not_followed(self):
        handler = llm._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'http://example.com'))
