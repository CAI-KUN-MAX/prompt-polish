"""Real-window check for the "固定在最前" button.

Run: python tests/e2e_pin.py
Uses a temporary config, so it never changes your saved settings.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompt_polish import config, winapi  # noqa: E402
from prompt_polish.app import PromptPolishApp  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetAncestor.restype = ctypes.c_void_p
GA_ROOT = 2
WS_EX_TOPMOST = 0x00000008


def is_topmost(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(ctypes.c_void_p(hwnd), -20) & WS_EX_TOPMOST)


def click(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.12)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.25)


def main() -> int:
    winapi.enable_dpi_awareness()
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "config_version": 2,
                    "always_on_top": True,
                    "hotkey": "Ctrl+Alt+F11",
                    "hotkey_restore": "Ctrl+Alt+Shift+F11",
                    "window_title_regex": "DeepSeek Harness",
                }
            ),
            encoding="utf-8",
        )
        os.environ["PROMPTPOLISH_CONFIG"] = str(cfg_path)

        root = tk.Tk()
        app = PromptPolishApp(root)
        root.update()
        time.sleep(0.4)
        root.update()
        hwnd = user32.GetAncestor(ctypes.c_void_p(root.winfo_id()), GA_ROOT)

        assert app.pinned is True
        assert is_topmost(hwnd), "window should start pinned"
        print("PASS initial pinned")

        btn = app.pin_btn
        x = btn.winfo_rootx() + btn.winfo_width() // 2
        y = btn.winfo_rooty() + btn.winfo_height() // 2

        click(x, y)
        root.update()
        time.sleep(0.3)
        assert app.pinned is False
        assert not is_topmost(hwnd), "unpin should clear WS_EX_TOPMOST"
        assert json.loads(cfg_path.read_text(encoding="utf-8"))["always_on_top"] is False
        print("PASS click unpin + persisted")

        click(x, y)
        root.update()
        time.sleep(0.3)
        assert app.pinned is True
        assert is_topmost(hwnd), "pin should set WS_EX_TOPMOST"
        assert json.loads(cfg_path.read_text(encoding="utf-8"))["always_on_top"] is True
        print("PASS click pin + persisted")

        app._set_busy(True)
        root.update()
        assert not is_topmost(hwnd), "busy should temporarily drop topmost"
        app._set_busy(False)
        root.update()
        assert is_topmost(hwnd), "topmost should return after busy"
        print("PASS busy drop/restore")

        target = winapi.find_target_window("DeepSeek Harness")
        if target:
            user32.SetForegroundWindow(ctypes.c_void_p(target.hwnd))
            time.sleep(0.2)
            user32.SendMessageW(ctypes.c_void_p(target.hwnd), 0x020A, (120 << 16) & 0xFFFFFFFF, 0)
            time.sleep(0.4)
            assert is_topmost(hwnd), "topmost must survive DSH scrolling"
            print("PASS topmost survives DSH scroll")

        app.hotkey.unregister()
        app.restore_hotkey.unregister()
        root.destroy()
    print("ALL PIN CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
