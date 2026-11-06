"""Thin Win32 helpers: window discovery, DPI, foreground, global hotkey.

No third-party packages are used.
"""
from __future__ import annotations

import ctypes
import re
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

SW_RESTORE = 9
SW_MINIMIZE = 6
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
ERROR_ALREADY_EXISTS = 183


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str
    rect: tuple[int, int, int, int]
    minimized: bool

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])


def enable_dpi_awareness() -> None:
    try:
        # PER_MONITOR_AWARE_V2 = -4
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def _process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def get_window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    try:
        if dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        ) == 0:
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return (0, 0, 0, 0)


def list_windows() -> list[WindowInfo]:
    results: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = get_window_title(hwnd)
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = get_window_rect(hwnd)
        if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
            return True
        results.append(
            WindowInfo(
                hwnd=hwnd,
                title=title,
                pid=pid.value,
                process=_process_name(pid.value),
                rect=rect,
                minimized=is_minimized(hwnd),
            )
        )
        return True

    user32.EnumWindows(callback, 0)
    return results


def find_target_window(title_regex: str) -> WindowInfo | None:
    try:
        pattern = re.compile(title_regex, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(title_regex), re.IGNORECASE)
    best = None
    for w in list_windows():
        if not pattern.search(w.title):
            continue
        if w.width < 400 or w.height < 300:
            continue
        # Prefer the real app over browser tabs / tool windows.
        if "DeepSeek Harness" in w.process:
            return w
        if best is None:
            best = w
    return best


def restore_window(hwnd: int) -> None:
    if is_minimized(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)


def set_foreground(hwnd: int) -> bool:
    restore_window(hwnd)
    if user32.SetForegroundWindow(hwnd):
        return True
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if fg_thread and fg_thread != my_thread:
            attached = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
        ok = bool(user32.SetForegroundWindow(hwnd))
        user32.BringWindowToTop(hwnd)
        return ok
    finally:
        if attached:
            user32.AttachThreadInput(my_thread, fg_thread, False)


def foreground_window() -> int:
    return int(user32.GetForegroundWindow())


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class GlobalHotkey:
    """RegisterHotKey on a dedicated thread; invokes callback from that thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._callback: Callable[[], None] | None = None
        self._mods = 0
        self._vk = 0
        self._ready = threading.Event()
        self._ok = False
        self._error = ""
        self._lock = threading.Lock()

    def register(self, mods: int, vk: int, callback: Callable[[], None]) -> tuple[bool, str]:
        self.unregister()
        self._mods, self._vk, self._callback = mods, vk, callback
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, name="PromptPolishHotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        return self._ok, self._error

    def _loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        mods = self._mods | MOD_NOREPEAT
        ok = bool(user32.RegisterHotKey(None, 1, mods, self._vk))
        if not ok:
            err = ctypes.get_last_error()
            self._error = {
                1409: "快捷键已被其他程序占用",
            }.get(err, f"注册快捷键失败（错误码 {err}）")
            self._ready.set()
            return
        self._ok = True
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY and msg.wParam == 1:
                    try:
                        if self._callback:
                            self._callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, 1)

    def unregister(self) -> None:
        with self._lock:
            thread = self._thread
            if thread and thread.is_alive() and self._thread_id:
                user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
                thread.join(timeout=2)
            self._thread = None
            self._thread_id = 0
            self._callback = None
            self._ok = False
