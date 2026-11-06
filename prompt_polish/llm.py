"""DeepSeek (OpenAI-compatible) chat-completions client using only the stdlib."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Config
from .rules import build_user_message, strip_code_fence


class LLMError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("config", "API 地址为空")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/chat/completions"


def _complete(messages: list[dict], cfg: Config, api_key: str) -> LLMResult:
    if not api_key:
        raise LLMError("no_key", "未配置 DeepSeek API Key")
    if not messages:
        raise LLMError("empty", "没有可发送的对话内容")
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": float(cfg.temperature),
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint(cfg.api_base_url),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "PromptPolish/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=max(1, int(cfg.timeout_seconds))) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        code = exc.code
        detail = ""
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
            detail = str((data.get("error") or {}).get("message") or "")[:200]
        except Exception:
            pass
        if code in (401, 403):
            raise LLMError("auth", "DeepSeek 鉴权失败（HTTP %d），请检查 API Key 是否有效" % code)
        if code == 402:
            raise LLMError("balance", "DeepSeek 账户余额不足（HTTP 402）")
        if code == 429:
            raise LLMError("rate_limit", "请求过于频繁或额度受限（HTTP 429），请稍后重试")
        if 500 <= code:
            raise LLMError("server", f"DeepSeek 服务暂时不可用（HTTP {code}），请稍后重试")
        raise LLMError("http", f"模型请求失败（HTTP {code}）" + (f"：{detail}" if detail else ""))
    except (socket.timeout, TimeoutError):
        raise LLMError("timeout", f"模型请求超时（{cfg.timeout_seconds} 秒），原文未改动")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise LLMError("timeout", f"模型请求超时（{cfg.timeout_seconds} 秒），原文未改动")
        raise LLMError("network", f"网络连接失败：{reason}")
    except OSError as exc:
        raise LLMError("network", f"网络连接失败：{exc}")

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise LLMError("bad_response", "模型返回内容无法解析")
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("bad_response", "模型没有返回可用内容")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("bad_response", "模型返回了空内容")
    text = strip_code_fence(content)
    if not text.strip():
        raise LLMError("bad_response", "模型返回了空内容")
    usage = data.get("usage") or {}
    return LLMResult(
        text=text,
        model=str(data.get("model") or cfg.model),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )


def chat(messages: list[dict], cfg: Config, api_key: str) -> LLMResult:
    """Multi-turn refinement: send the whole conversation to the model."""
    return _complete(messages, cfg, api_key)


def optimize(draft: str, cfg: Config, api_key: str) -> LLMResult:
    if not draft.strip():
        raise LLMError("empty", "草稿为空")
    if len(draft) > max(100, int(cfg.max_input_chars)):
        raise LLMError(
            "too_long",
            f"草稿过长（{len(draft)} 字符，上限 {cfg.max_input_chars}），为避免截断已取消",
        )
    messages = [
        {"role": "system", "content": cfg.system_prompt},
        {"role": "user", "content": build_user_message(draft)},
    ]
    return _complete(messages, cfg, api_key)
