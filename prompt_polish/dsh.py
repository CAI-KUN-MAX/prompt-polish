"""Client for the PowerShell UIA bridge."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .winapi import WindowInfo

BRIDGE = Path(__file__).with_name("uia_bridge.ps1")
CREATE_NO_WINDOW = 0x08000000

MESSAGES = {
    "window_gone": "目标窗口已关闭或重启，未改动草稿",
    "chat_changed": "当前聊天已变化，已停止自动回填",
    "composer_not_found": "无法定位输入框（窗口布局变化或无障碍树未启用），已停止，未改动草稿",
    "foreground_failed": "无法把目标窗口切到前台（可能被其他窗口遮挡），已停止，未点击、未改动草稿",
    "focus_failed": "无法确认焦点在输入框内，已停止，未读取或改动草稿",
    "unsupported": "输入框不支持文本读取，已停止",
    "special": "输入区含附件或特殊引用，已停止自动回填",
    "draft_changed": "草稿在优化期间发生变化，已停止自动回填",
    "empty_result": "优化结果为空，已停止",
    "write_failed": "写入后校验不一致，已尝试恢复原文",
    "timeout": "输入框操作超时，原文未改动",
    "bridge_missing": "缺少自动化桥脚本",
    "bridge_error": "自动化桥执行失败",
    "exception": "自动化桥执行异常",
}

SPECIAL_MESSAGES = {
    "attachment_image": "输入区有图片/附件",
    "mention_popup": "有 @/文件候选弹窗打开",
    "attachment_remove_button": "输入区有附件移除按钮",
    "reference_or_attachment_node": "输入区含引用/附件控件",
}


@dataclass
class BridgeResult:
    ok: bool
    code: str = ""
    message: str = ""
    value: str = ""
    special: list[str] = field(default_factory=list)
    title: str = ""
    length: int = 0
    raw: dict = field(default_factory=dict)


def _powershell() -> str:
    # Windows PowerShell 5.1 is present on every supported Windows build and
    # loads the UIAutomation assemblies without extra modules.
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    flags = CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        creationflags=flags,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _parse(stdout: str) -> dict:
    for line in reversed([ln.strip() for ln in stdout.splitlines() if ln.strip()]):
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    raise ValueError("UIA 桥没有返回有效结果")


def call(
    action: str,
    hwnd: int,
    cfg: Config,
    *,
    expected_title: str = "",
    expected_draft: str = "",
    new_text: str = "",
    timeout: float = 60.0,
) -> BridgeResult:
    if not BRIDGE.exists():
        return BridgeResult(False, "bridge_missing", f"缺少自动化桥脚本：{BRIDGE}")
    args = [
        _powershell(),
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BRIDGE),
        "-Action",
        action,
        "-Hwnd",
        str(int(hwnd)),
        "-RelX",
        f"{float(cfg.click_rel_x):.6f}",
        "-RelY",
        f"{float(cfg.click_rel_y):.6f}",
        "-ComposerPrefix",
        cfg.composer_name_prefix,
    ]
    if expected_title:
        args += ["-ExpectedTitle", expected_title]
    if expected_draft:
        args += ["-ExpectedDraft", expected_draft]
    if new_text:
        args += ["-NewText", new_text]

    try:
        code, out, err = _run(args, timeout)
    except subprocess.TimeoutExpired:
        return BridgeResult(False, "timeout", "输入框操作超时，原文未改动")
    try:
        data = _parse(out)
    except ValueError:
        detail = (err or out or "").strip().splitlines()
        message = detail[-1] if detail else "未知错误"
        return BridgeResult(False, "bridge_error", f"自动化桥执行失败：{message[:300]}")
    result = BridgeResult(
        ok=bool(data.get("ok")),
        code=str(data.get("code") or ""),
        message=str(data.get("message") or ""),
        value=str(data.get("value") or ""),
        special=[str(x) for x in (data.get("special") or [])],
        title=str(data.get("title") or ""),
        length=int(data.get("length") or 0),
        raw=data,
    )
    if result.code in MESSAGES:
        result.message = MESSAGES[result.code]
    if result.special:
        result.special = [SPECIAL_MESSAGES.get(s, s) for s in result.special]
    if not result.ok and not result.message:
        result.message = "操作失败"
    return result


def read_draft(target: WindowInfo, cfg: Config) -> BridgeResult:
    return call("read", target.hwnd, cfg, expected_title=target.title, timeout=40)


def apply_draft(
    target: WindowInfo,
    cfg: Config,
    *,
    expected_title: str,
    expected_draft: str,
    new_text: str,
) -> BridgeResult:
    return call(
        "apply",
        target.hwnd,
        cfg,
        expected_title=expected_title,
        expected_draft=expected_draft,
        new_text=new_text,
        timeout=90,
    )


def status(target: WindowInfo, cfg: Config) -> BridgeResult:
    return call("status", target.hwnd, cfg, timeout=25)


def self_test() -> tuple[bool, str]:
    """Check that the bundled bridge script parses and PowerShell is runnable."""
    try:
        code, out, err = _run(
            [_powershell(), "-NoProfile", "-Command", "Write-Output '{\"ok\":true,\"ping\":true}'"],
            20,
        )
        return code == 0 and '"ping":true' in out.replace(" ", ""), (err or out)[:300]
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, str(exc)
