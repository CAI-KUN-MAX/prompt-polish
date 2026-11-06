from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from prompt_polish import config, llm, rules
from tests.mock_llm import STATE, MockServer


class RulesTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(rules.normalize_draft("a\r\nb\r\n"), "a\nb")
        self.assertEqual(rules.normalize_draft("\n\n"), "")
        self.assertEqual(rules.normalize_draft(None), "")

    def test_plain_prompt_has_no_special(self) -> None:
        text = "请把这段文字改写得更清楚，保留原意。\nSecond paragraph in English."
        self.assertEqual(rules.detect_special_text(text), [])

    def test_mention(self) -> None:
        reasons = rules.detect_special_text("请参考 @src/main.py 的实现")
        self.assertTrue(any("@ 提及" in r for r in reasons))

    def test_slash_command(self) -> None:
        reasons = rules.detect_special_text("请执行\n/compact")
        self.assertTrue(any("指令" in r for r in reasons))

    def test_file_marker(self) -> None:
        reasons = rules.detect_special_text("see file:///C:/secret.txt")
        self.assertTrue(any("文件" in r for r in reasons))

    def test_email_is_not_a_mention(self) -> None:
        self.assertEqual(rules.detect_special_text("联系 me@example.com 即可"), [])

    def test_code_fence_stripped(self) -> None:
        self.assertEqual(rules.strip_code_fence("```\nabc\n```"), "abc")

    def test_build_user_message_contains_draft(self) -> None:
        self.assertIn("原始草稿", rules.build_user_message("原始草稿"))


class HotkeyTests(unittest.TestCase):
    def test_parse_default(self) -> None:
        mods, vk = config.parse_hotkey("Ctrl+Alt+P")
        self.assertEqual(mods, 0x0002 | 0x0001)
        self.assertEqual(vk, ord("P"))

    def test_parse_function_key(self) -> None:
        mods, vk = config.parse_hotkey("ctrl+shift+f9")
        self.assertEqual(mods, 0x0002 | 0x0004)
        self.assertEqual(vk, 0x78)

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            config.parse_hotkey("Ctrl+Alt")
        with self.assertRaises(ValueError):
            config.parse_hotkey("Hyper+Q")

    def test_round_trip(self) -> None:
        mods, vk = config.parse_hotkey("Ctrl+Alt+P")
        self.assertEqual(config.format_hotkey(mods, vk), "Ctrl+Alt+P")


class LLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer().__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.__exit__(None, None, None)

    def setUp(self) -> None:
        STATE.mode = "ok"
        STATE.requests = 0
        self.cfg = config.Config(api_base_url=self.server.base_url, timeout_seconds=10, model="deepseek-chat")

    def test_success(self) -> None:
        result = llm.optimize("原始草稿", self.cfg, "sk-test")
        self.assertIn("优化后的提示词", result.text)
        self.assertEqual(result.model, "deepseek-chat")
        self.assertEqual(STATE.last_auth, "Bearer sk-test")
        self.assertEqual(STATE.last_body["model"], "deepseek-chat")
        self.assertEqual(STATE.last_body["messages"][0]["role"], "system")

    def test_auth_error(self) -> None:
        STATE.mode = "auth"
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", self.cfg, "sk-bad")
        self.assertEqual(ctx.exception.code, "auth")
        self.assertNotIn("sk-bad", str(ctx.exception))

    def test_server_error(self) -> None:
        STATE.mode = "server"
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", self.cfg, "sk-test")
        self.assertEqual(ctx.exception.code, "server")

    def test_rate_limit(self) -> None:
        STATE.mode = "rate"
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", self.cfg, "sk-test")
        self.assertEqual(ctx.exception.code, "rate_limit")

    def test_bad_json(self) -> None:
        STATE.mode = "badjson"
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", self.cfg, "sk-test")
        self.assertEqual(ctx.exception.code, "bad_response")

    def test_empty_choices(self) -> None:
        STATE.mode = "empty"
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", self.cfg, "sk-test")
        self.assertEqual(ctx.exception.code, "bad_response")

    def test_timeout(self) -> None:
        STATE.mode = "slow"
        cfg = config.Config(api_base_url=self.server.base_url, timeout_seconds=1)
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", cfg, "sk-test")
        self.assertEqual(ctx.exception.code, "timeout")

    def test_network_error(self) -> None:
        cfg = config.Config(api_base_url="http://nonexistent.invalid", timeout_seconds=3)
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", cfg, "sk-test")
        self.assertIn(ctx.exception.code, ("network", "timeout"))

    def test_missing_key(self) -> None:
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x", self.cfg, "")
        self.assertEqual(ctx.exception.code, "no_key")

    def test_multiturn_chat(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "original"},
            {"role": "assistant", "content": "first result"},
            {"role": "user", "content": "make it shorter"},
        ]
        result = llm.chat(messages, self.cfg, "sk-test")
        self.assertTrue(result.text)
        self.assertEqual(len(STATE.last_body["messages"]), 4)
        self.assertEqual(STATE.last_body["messages"][-1]["content"], "make it shorter")

    def test_too_long(self) -> None:
        cfg = config.Config(api_base_url=self.server.base_url, max_input_chars=100)
        with self.assertRaises(llm.LLMError) as ctx:
            llm.optimize("x" * 101, cfg, "sk-test")
        self.assertEqual(ctx.exception.code, "too_long")


class ConfigMigrationTests(unittest.TestCase):
    def test_v1_config_migrates_model_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text(
                json.dumps(
                    {
                        "model": "deepseek-chat",
                        "temperature": 0.3,
                        "system_prompt": "你只做最小必要修改。",
                        "hotkey": "Ctrl+Alt+P",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            old = os.environ.get("PROMPTPOLISH_CONFIG")
            os.environ["PROMPTPOLISH_CONFIG"] = str(p)
            try:
                cfg = config.load_config()
            finally:
                if old is None:
                    os.environ.pop("PROMPTPOLISH_CONFIG", None)
                else:
                    os.environ["PROMPTPOLISH_CONFIG"] = old
            self.assertEqual(cfg.model, "deepseek-flash")
            self.assertIn("资深提示词工程师", cfg.system_prompt)
            self.assertEqual(cfg.temperature, 0.4)
            self.assertEqual(cfg.config_version, config.CONFIG_VERSION)

    def test_api_key_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "key.txt"
            old = os.environ.get("PROMPTPOLISH_KEY_FILE")
            os.environ["PROMPTPOLISH_KEY_FILE"] = str(p)
            try:
                self.assertEqual(config.api_key_status(), "missing")
                p.write_text("sk-test\n", encoding="utf-8")
                self.assertEqual(config.api_key_status(), "ok")
                self.assertEqual(config.get_api_key(), "sk-test")
                p.write_text("\n", encoding="utf-8")
                self.assertEqual(config.api_key_status(), "empty")
            finally:
                if old is None:
                    os.environ.pop("PROMPTPOLISH_KEY_FILE", None)
                else:
                    os.environ["PROMPTPOLISH_KEY_FILE"] = old


if __name__ == "__main__":
    unittest.main()
