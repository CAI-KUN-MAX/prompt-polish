"""Configuration and secret handling for PromptPolish.

The config file and the optional API-key file live under %APPDATA%\\PromptPolish.
No secret is ever written to the repository, to the config file, or to logs.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "PromptPolish"
DEFAULT_TITLE_REGEX = r"DeepSeek Harness|Codex"
DEFAULT_COMPOSER_PREFIX = "发消息或创建任务"
DEFAULT_HOTKEY = "Ctrl+Alt+P"
CONFIG_VERSION = 2

DEFAULT_SYSTEM_PROMPT = """你是资深提示词工程师。把用户草稿改写成可直接交给 AI 执行的高质量提示词，而不是同义句改写或简单排版。

必须遵守：
1. 信息保真：不得新增项目特定事实（字段名、路径、接口、业务规则、技术栈）；不得删除硬性约束。无法从原文确定的项目特定信息用“[待确认：…]”占位；通用工程做法（如分块读取、日志、重试）可以写成要求，但不得冒充用户给定的事实。
2. 结构：按“任务目标 / 输入 / 输出 / 约束 / 验收标准 / 交付物”组织，缺少信息的小节可以省略。
3. 可执行性：把含糊要求改成可检查的验收标准；原文没有的数值阈值写成“[建议确认：…]”。
4. 保留原语言、代码、路径、数字、专有名词。
5. 只输出改写后的提示词正文；不要解释、前言、结语，不要用代码块包裹全文。
6. 占位符控制在必要范围（尽量不超过 5 处），不要把整篇写成问卷。
7. 即使原文已经清楚，也要做结构重排和验收标准补全，不能只改标点。"""


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    override = os.environ.get("PROMPTPOLISH_CONFIG")
    if override:
        return Path(override)
    return app_dir() / "config.json"


def key_path() -> Path:
    override = os.environ.get("PROMPTPOLISH_KEY_FILE")
    if override:
        return Path(override)
    return app_dir() / "api_key.txt"


def log_path() -> Path:
    return app_dir() / "log.txt"


@dataclass
class Config:
    config_version: int = CONFIG_VERSION
    window_title_regex: str = DEFAULT_TITLE_REGEX
    composer_name_prefix: str = DEFAULT_COMPOSER_PREFIX
    click_rel_x: float = 0.53
    click_rel_y: float = 0.78
    hotkey: str = DEFAULT_HOTKEY
    hotkey_restore: str = "Ctrl+Alt+Shift+P"
    api_base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    temperature: float = 0.4
    timeout_seconds: int = 60
    max_input_chars: int = 12000
    always_on_top: bool = True
    debug_log: bool = False
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


def load_config() -> Config:
    cfg = Config()
    p = config_path()
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    try:
        raw_version = int(raw.get("config_version", 1))
    except (TypeError, ValueError):
        raw_version = 1
    known = {f.name: f.type for f in fields(Config)}
    for k, v in raw.items():
        if k not in known:
            continue
        current = getattr(cfg, k)
        try:
            if isinstance(current, bool):
                setattr(cfg, k, bool(v))
            elif isinstance(current, int):
                setattr(cfg, k, int(v))
            elif isinstance(current, float):
                setattr(cfg, k, float(v))
            elif isinstance(current, str):
                setattr(cfg, k, str(v))
        except (TypeError, ValueError):
            continue
    # v2: the old conservative default prompt and lightweight model alias
    # produced near-copy edits. Migrate configs created before the upgrade.
    if raw_version < 2:
        if cfg.model in ("", "deepseek-chat"):
            cfg.model = "deepseek-flash"
        if "资深提示词工程师" not in (cfg.system_prompt or ""):
            cfg.system_prompt = DEFAULT_SYSTEM_PROMPT
        cfg.temperature = 0.4
        cfg.config_version = CONFIG_VERSION
    # A calibration point in the bottom toolbar can hit the model or send
    # controls. Keep it on the composer's text row.
    if not (0.30 <= cfg.click_rel_x <= 0.80):
        cfg.click_rel_x = 0.53
    if not (0.50 <= cfg.click_rel_y <= 0.84):
        cfg.click_rel_y = 0.78
    return cfg


def save_config(cfg: Config) -> None:
    p = config_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def api_key_status() -> str:
    """Return env / ok / missing / empty / unreadable without exposing the key."""
    env = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if env:
        return "env"
    p = key_path()
    if not p.exists():
        return "missing"
    try:
        text = p.read_text(encoding="utf-8-sig").strip()
        return "ok" if text else "empty"
    except PermissionError:
        return "unreadable"
    except OSError:
        return "unreadable"


def get_api_key() -> str:
    """Return the API key from the environment first, then the private key file.

    The value is never printed or logged by this module.
    """
    env = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if env:
        return env
    p = key_path()
    try:
        if p.exists():
            return p.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""
    return ""


def _reset_file_acl(path: Path) -> None:
    """Restore inherited permissions (the user profile is already user-private)."""
    if os.name != "nt":
        return
    try:
        import subprocess

        subprocess.run(
            ["icacls", str(path), "/reset"],
            capture_output=True,
            creationflags=0x08000000,
            timeout=15,
        )
    except Exception:
        pass


def save_api_key(key: str) -> None:
    """Persist a key in the user profile and verify it can be read back.

    The file lives under %APPDATA%, which already inherits a user-private ACL.
    We deliberately do not rewrite the ACL with a username: icacls mangles
    non-ASCII account names and previously locked the file for its own owner.
    """
    p = key_path()
    p.write_text(key.strip() + "\n", encoding="utf-8")
    _reset_file_acl(p)
    try:
        if not p.read_text(encoding="utf-8-sig").strip():
            raise OSError("empty after save")
    except OSError:
        _reset_file_acl(p)


_HOTKEY_MODS = {
    "ctrl": 0x0002,
    "control": 0x0002,
    "alt": 0x0001,
    "shift": 0x0004,
    "win": 0x0008,
    "windows": 0x0008,
}

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}


def parse_hotkey(text: str) -> tuple[int, int]:
    """Parse e.g. 'Ctrl+Alt+P' into (modifiers, virtual-key). Raises ValueError."""
    if not text or not text.strip():
        raise ValueError("快捷键不能为空")
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", text.strip()) if p.strip()]
    if not parts:
        raise ValueError("快捷键格式无效")
    mods = 0
    key = None
    for part in parts:
        if part in _HOTKEY_MODS:
            mods |= _HOTKEY_MODS[part]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            key = 0x70 + int(part[1:]) - 1
        elif part in _NAMED_KEYS:
            key = _NAMED_KEYS[part]
        else:
            raise ValueError(f"无法识别快捷键按键：{part}")
    if key is None:
        raise ValueError("快捷键缺少主按键，例如 Ctrl+Alt+P")
    if key in _HOTKEY_MODS.values():
        raise ValueError("快捷键缺少主按键")
    return mods, key


def format_hotkey(mods: int, vk: int) -> str:
    names = []
    if mods & 0x0002:
        names.append("Ctrl")
    if mods & 0x0001:
        names.append("Alt")
    if mods & 0x0004:
        names.append("Shift")
    if mods & 0x0008:
        names.append("Win")
    if 0x70 <= vk <= 0x87:
        names.append(f"F{vk - 0x70 + 1}")
    elif 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        names.append(chr(vk))
    else:
        for name, code in _NAMED_KEYS.items():
            if code == vk and name.isalpha():
                names.append(name.capitalize())
                break
        else:
            names.append(f"0x{vk:02X}")
    return "+".join(names)
