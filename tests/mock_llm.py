"""A tiny OpenAI-compatible mock used by the tests (no third-party deps)."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockState:
    def __init__(self) -> None:
        self.mode = "ok"
        self.last_auth = ""
        self.last_body: dict = {}
        self.requests = 0
        self.reply = "优化后的提示词\n\n1. 保留原意\n2. 改善结构"
        self.refined_reply = "精简后的提示词\n\n- 保留原意\n- 结构更紧凑"


STATE = MockState()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # silence
        pass

    def _send(self, code: int, payload: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        STATE.requests += 1
        STATE.last_auth = self.headers.get("Authorization", "")
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            STATE.last_body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            STATE.last_body = {}
        mode = STATE.mode
        if mode == "auth":
            self._send(401, b'{"error":{"message":"invalid api key"}}')
            return
        if mode == "balance":
            self._send(402, b'{"error":{"message":"insufficient balance"}}')
            return
        if mode == "server":
            self._send(500, b'{"error":{"message":"boom"}}')
            return
        if mode == "rate":
            self._send(429, b'{"error":{"message":"slow down"}}')
            return
        if mode == "badjson":
            self._send(200, b"not-json")
            return
        if mode == "empty":
            self._send(200, b'{"choices":[]}')
            return
        if mode == "slow":
            time.sleep(2.5)
        if mode == "slow8":
            time.sleep(8)
        content = STATE.reply
        if mode == "refine":
            messages = STATE.last_body.get("messages") or []
            if len(messages) >= 4:
                content = STATE.refined_reply
        payload = {
            "id": "mock",
            "model": "deepseek-chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        self._send(200, json.dumps(payload).encode("utf-8"))


class MockServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> "MockServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
