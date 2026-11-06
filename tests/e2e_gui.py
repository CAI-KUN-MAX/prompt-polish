"""Real end-to-end check: GUI app + real DeepSeek Harness composer + mock LLM.

Run:  python tests/e2e_gui.py
It never sends a chat message and restores/clears the draft afterwards.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompt_polish import config, dsh, winapi  # noqa: E402
from tests.mock_llm import STATE, MockServer  # noqa: E402

ART = ROOT / "artifacts"
ART.mkdir(exist_ok=True)
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
if not PYTHONW.exists():
    PYTHONW = Path(r"D:\python\pythonw.exe")

user32 = ctypes.WinDLL("user32", use_last_error=True)
VK_CONTROL, VK_MENU, VK_F10 = 0x11, 0x12, 0x79
VK_SHIFT, VK_P = 0x10, 0x50
VK_A, VK_V, VK_DELETE = 0x41, 0x56, 0x2E

TEST_DRAFT = (
    "请帮我检查这个登录接口的问题。\n"
    "需要保留原有 API 兼容性，不要改数据库结构。\n"
    "English note: keep it concise and list the risks."
)
MOCK_REPLY = (
    "请检查登录接口的问题，并满足以下约束：\n\n"
    "1. 保留原有 API 兼容性；\n"
    "2. 不修改数据库结构；\n"
    "3. 用英文简要列出主要风险。\n\n"
    "Keep the response concise."
)
REFINED_REPLY = (
    "请检查登录接口：保留 API 兼容性，不改数据库结构，并用英文列出主要风险。\n"
    "保持简洁。"
)


def key(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.05)


def ctrl(vk: int) -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.03)
    key(vk)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.12)


def click(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.15)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.07)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.2)


def hotkey() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    time.sleep(0.02)
    key(VK_F10)
    user32.keybd_event(VK_MENU, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)


def hotkey_restore() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_SHIFT, 0, 0, 0)
    time.sleep(0.02)
    key(VK_P)
    user32.keybd_event(VK_SHIFT, 0, 2, 0)
    user32.keybd_event(VK_MENU, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)


def wait_for(pred, timeout: float, interval: float = 0.5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


def app_window() -> winapi.WindowInfo | None:
    for w in winapi.list_windows():
        if "PromptPolish" in w.title:
            return w
    return None


def set_draft(target: winapi.WindowInfo, cfg: config.Config, text: str) -> None:
    cur = dsh.read_draft(target, cfg)
    assert cur.ok, f"read before set failed: {cur.message}"
    res = dsh.apply_draft(target, cfg, expected_title=cur.title, expected_draft=cur.value, new_text=text)
    assert res.ok, f"set draft failed: {res.code} {res.message}"


def read_draft(target: winapi.WindowInfo, cfg: config.Config) -> str:
    res = dsh.read_draft(target, cfg)
    assert res.ok, f"read failed: {res.code} {res.message}"
    return res.value


def main() -> int:
    winapi.enable_dpi_awareness()
    cfg_path = Path(os.environ["TEMP"]) / "pp_e2e_config.json"
    cfg_data = {
        "window_title_regex": "DeepSeek Harness",
        "click_rel_x": 0.53,
        "click_rel_y": 0.78,
        "hotkey": "Ctrl+Alt+F10",
        "api_base_url": "http://127.0.0.1:1",
        "model": "deepseek-chat",
        "timeout_seconds": 30,
        "debug_log": True,
    }
    target = winapi.find_target_window("DeepSeek Harness")
    if not target:
        print("FAIL: no DeepSeek Harness window")
        return 2

    env = dict(os.environ)
    env["PROMPTPOLISH_CONFIG"] = str(cfg_path)
    env["DEEPSEEK_API_KEY"] = "sk-e2e-test"
    env["PYTHONPATH"] = str(ROOT)

    server = MockServer().__enter__()
    cfg_data["api_base_url"] = server.base_url
    cfg_path.write_text(json.dumps(cfg_data, ensure_ascii=False), encoding="utf-8")
    os.environ["PROMPTPOLISH_CONFIG"] = str(cfg_path)
    cfg = config.load_config()
    assert cfg.api_base_url == server.base_url, "temp config not loaded"

    proc = None
    try:
        try:
            config.log_path().unlink(missing_ok=True)
        except OSError:
            pass
        proc = subprocess.Popen([str(PYTHONW), str(ROOT / "prompt_polish.pyw")], env=env)
        assert wait_for(lambda: app_window() is not None, 15), "app window did not appear"
        STATE.reply = MOCK_REPLY
        STATE.refined_reply = REFINED_REPLY
        time.sleep(1.5)
        app = app_window()
        print("APP:", app.hwnd, app.rect)

        # Platform state
        winapi.set_foreground(target.hwnd)
        set_draft(target, cfg, TEST_DRAFT)
        assert read_draft(target, cfg) == TEST_DRAFT
        print("STEP 1 ok: draft set")

        # --- success path via global hotkey ---
        STATE.mode = "ok"
        STATE.requests = 0
        hotkey()
        assert wait_for(lambda: STATE.requests > 0, 25), "LLM request was not made"
        time.sleep(12)
        shot = ART / "e2e_after_hotkey.png"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing; $b=New-Object System.Drawing.Bitmap(1920,1080); $g=[System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen(0,0,0,0,$b.Size); $b.Save('{shot}',[System.Drawing.Imaging.ImageFormat]::Png)"],
                       capture_output=True)
        assert read_draft(target, cfg) == MOCK_REPLY, "draft was not replaced"
        print("STEP 2 ok: hotkey -> optimize -> replace")

        # --- conversational refinement through the entry + Enter ---
        STATE.mode = "refine"
        STATE.requests = 0
        app = app_window()
        geo = {}
        for line in config.log_path().read_text(encoding="utf-8").splitlines():
            m = re.search(r"ui: (\w+) x=(\d+) y=(\d+) w=(\d+) h=(\d+)", line)
            if m:
                geo[m.group(1)] = tuple(int(m.group(i)) for i in range(2, 6))
        assert "refine_entry" in geo, "refine entry geometry not logged"
        ex, ey, ew, eh = geo["refine_entry"]
        winapi.set_foreground(app.hwnd)
        time.sleep(0.3)
        click(ex + ew // 2, ey + eh // 2)
        old_clip = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True, text=True, encoding="utf-8",
        ).stdout
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Set-Clipboard -Value '请再精简 30%，保留约束和验收标准。'"],
            capture_output=True,
        )
        ctrl(VK_V)
        time.sleep(0.3)
        key(0x0D)
        time.sleep(9)
        assert read_draft(target, cfg) == REFINED_REPLY, "refinement did not replace the draft"
        print("STEP 3 ok: conversational refinement")
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Set-Clipboard -Value '" + old_clip.replace("'", "''") + "'"],
            capture_output=True,
        )
        shot = ART / "e2e_applied.png"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing; $b=New-Object System.Drawing.Bitmap(1920,1080); $g=[System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen(0,0,0,0,$b.Size); $b.Save('{shot}',[System.Drawing.Imaging.ImageFormat]::Png)"],
                       capture_output=True)

        # --- restore via the restore hotkey ---
        winapi.set_foreground(target.hwnd)
        time.sleep(0.3)
        hotkey_restore()
        time.sleep(8)
        assert read_draft(target, cfg) == TEST_DRAFT, "restore did not work"
        print("STEP 4 ok: restore hotkey")

        # --- draft changed while the model request is in flight ---
        STATE.mode = "slow8"
        STATE.requests = 0
        set_draft(target, cfg, TEST_DRAFT)
        hotkey()
        assert wait_for(lambda: STATE.requests > 0, 25), "slow LLM request not made"
        time.sleep(0.3)
        changed = TEST_DRAFT + "\n用户中途补充：只改文档，不改代码。"
        set_draft(target, cfg, changed)
        time.sleep(7.0)
        after = read_draft(target, cfg)
        assert after == changed, f"tool overwrote a changed draft: {after!r}"
        print("STEP 5 ok: changed draft was not overwritten")
        shot = ART / "e2e_changed.png"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing; $b=New-Object System.Drawing.Bitmap(1920,1080); $g=[System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen(0,0,0,0,$b.Size); $b.Save('{shot}',[System.Drawing.Imaging.ImageFormat]::Png)"],
                       capture_output=True)

        # --- model failure keeps the draft ---
        STATE.mode = "server"
        STATE.requests = 0
        set_draft(target, cfg, TEST_DRAFT)
        hotkey()
        assert wait_for(lambda: STATE.requests > 0, 25), "failing LLM request not made"
        time.sleep(2.0)
        assert read_draft(target, cfg) == TEST_DRAFT
        print("STEP 6 ok: model failure kept original")
        shot = ART / "e2e_failure.png"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        f"Add-Type -AssemblyName System.Drawing; $b=New-Object System.Drawing.Bitmap(1920,1080); $g=[System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen(0,0,0,0,$b.Size); $b.Save('{shot}',[System.Drawing.Imaging.ImageFormat]::Png)"],
                       capture_output=True)

        # --- attachment blocks the call ---
        attach_png = ART / "test_attachment.png"
        attach_png.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        ))
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
              "$i=[System.Drawing.Image]::FromFile('" + str(attach_png) + "'); "
              "[System.Windows.Forms.Clipboard]::SetImage($i); Start-Sleep -Seconds 1")
        subprocess.run(["powershell.exe", "-STA", "-NoProfile", "-Command", ps], capture_output=True)
        dsh.read_draft(target, cfg)
        ctrl(VK_V)
        time.sleep(1.2)
        STATE.mode = "ok"
        STATE.requests = 0
        hotkey()
        time.sleep(4.0)
        assert STATE.requests == 0, "LLM was called despite an attachment"
        assert read_draft(target, cfg) == TEST_DRAFT
        print("STEP 7 ok: attachment blocked before the model call")
        # remove the attachment
        ps2 = (
            "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes; "
            "$w=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]" + str(target.hwnd) + "); "
            "$c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty,'yTDIAG_remove'); "
            "$b=$w.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c); "
            "if($b){$b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()}"
        )
        subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", ps2], capture_output=True)
        time.sleep(0.6)

        print("ALL E2E STEPS PASSED")
        return 0
    finally:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            pass
        try:
            server.__exit__(None, None, None)
        except Exception:
            pass
        try:
            # leave an empty composer, never a test draft
            res = dsh.read_draft(target, config.load_config())
            if res.ok and res.value:
                dsh.read_draft(target, config.load_config())
                ctrl(VK_A)
                key(VK_DELETE)
        except Exception as exc:
            print("cleanup warning:", exc)
        try:
            cfg_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            log = config.log_path()
            if log.exists():
                print("---- app log tail ----")
                print("\n".join(log.read_text(encoding="utf-8").splitlines()[-20:]))
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
