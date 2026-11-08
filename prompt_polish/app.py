"""Tkinter floating window and orchestration."""
from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass
from tkinter import messagebox, ttk

from . import config, dsh, llm, rules, winapi
from .config import Config

APP_TITLE = "PromptPolish · 提示词优化"
MAX_PREVIEW = 12000


@dataclass
class Snapshot:
    hwnd: int
    title: str
    draft: str


class PromptPolishApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg: Config = config.load_config()
        self.events: queue.Queue = queue.Queue()
        self.hotkey = winapi.GlobalHotkey()
        self.restore_hotkey = winapi.GlobalHotkey()
        self.busy = False
        self.snapshot: Snapshot | None = None
        self.original = ""
        self.optimized = ""
        self.messages: list[dict] = []
        self.applied = False
        self.debug_log = self.cfg.debug_log
        self.pinned = bool(self.cfg.always_on_top)

        root.title(APP_TITLE)
        root.geometry("540x620")
        root.minsize(480, 520)
        root.attributes("-topmost", self.pinned)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self.root.after(600, self._log_geometry)
        self._register_hotkey()
        self.refresh_target()
        self._poll_events()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(background="#f3f5f9")
        style.configure("App.TFrame", background="#f3f5f9")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#f3f5f9", foreground="#17223b", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Subtitle.TLabel", background="#f3f5f9", foreground="#66738b", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#f3f5f9", foreground="#273751", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Meta.TLabel", background="#f3f5f9", foreground="#64748b", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#e8efff", foreground="#2855a2", padding=(10, 8), font=("Microsoft YaHei UI", 9))
        style.configure("Action.TButton", padding=(12, 7), font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", padding=(14, 8), font=("Microsoft YaHei UI", 10, "bold"), background="#315eea", foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton", background=[("disabled", "#aebcdd"), ("active", "#234ac7")], foreground=[("disabled", "#f4f6fc")])
        style.configure("App.TCheckbutton", background="#f3f5f9", foreground="#475569", font=("Microsoft YaHei UI", 9))
        style.map("App.TCheckbutton", background=[("active", "#f3f5f9")])
        outer = ttk.Frame(self.root, padding=(16, 14), style="App.TFrame")
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="PromptPolish", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="把想法整理成清晰、可执行的提示词", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        self.target_var = tk.StringVar(value="目标：正在查找 DeepSeek Harness 窗口…")
        target = ttk.Label(outer, textvariable=self.target_var, style="Meta.TLabel", wraplength=440)
        target.pack(fill="x", anchor="w", pady=(0, 6))

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(outer, textvariable=self.status_var, style="Status.TLabel", wraplength=440)
        self.status_label.pack(fill="x", anchor="w", pady=(0, 12))

        ttk.Label(outer, text="优化结果", style="Section.TLabel").pack(anchor="w", pady=(0, 6))

        text_frame = ttk.Frame(outer, style="Card.TFrame", padding=1)
        text_frame.pack(fill="both", expand=True)
        self.result = tk.Text(
            text_frame,
            height=3,
            wrap="word",
            font=("Microsoft YaHei UI", 10),
            relief="flat",
            borderwidth=0,
            background="#ffffff",
            foreground="#24324a",
            state="disabled",
            padx=12,
            pady=10,
        )
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.result.yview)
        self.result.configure(yscrollcommand=scroll.set)
        self.result.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        ttk.Label(outer, text="继续调整", style="Section.TLabel").pack(anchor="w", pady=(12, 5))
        refine_row = ttk.Frame(outer, style="App.TFrame")
        refine_row.pack(fill="x")
        self.refine_var = tk.StringVar()
        self.refine_entry = ttk.Entry(refine_row, textvariable=self.refine_var)
        self.refine_entry.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.refine_entry.bind("<Return>", lambda _e: self.on_refine())
        self.refine_btn = ttk.Button(refine_row, text="调整", command=self.on_refine, state="disabled", style="Action.TButton")
        self.refine_btn.pack(side="left")

        row = ttk.Frame(outer, style="App.TFrame")
        row.pack(fill="x", pady=(12, 0))
        self.optimize_btn = ttk.Button(row, text="优化提示词", command=self.on_optimize, style="Primary.TButton")
        self.optimize_btn.pack(side="left")
        self.restore_btn = ttk.Button(row, text="恢复原文", command=self.on_restore, state="disabled", style="Action.TButton")
        self.restore_btn.pack(side="left", padx=(6, 0))
        self.copy_btn = ttk.Button(row, text="复制结果", command=self.on_copy, state="disabled", style="Action.TButton")
        self.copy_btn.pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(outer, style="App.TFrame")
        row2.pack(fill="x", pady=(10, 0))
        ttk.Button(row2, text="校准输入框", command=self.on_calibrate, style="Action.TButton").pack(side="left")
        ttk.Button(row2, text="设置", command=self.open_settings, style="Action.TButton").pack(side="left", padx=(6, 0))
        self.pin_var = tk.BooleanVar(value=self.pinned)
        self.pin_btn = ttk.Checkbutton(
            row2, text="固定在最前", variable=self.pin_var, command=self.on_toggle_pin, style="App.TCheckbutton"
        )
        self.pin_btn.pack(side="left", padx=(6, 0))

        self.hotkey_var = tk.StringVar(value=self._hotkey_text())
        ttk.Label(outer, textvariable=self.hotkey_var, style="Meta.TLabel", wraplength=440).pack(
            fill="x", anchor="w", pady=(9, 0)
        )

        self._show_text(
            "准备开始\n\n"
            "在聊天输入框中写下你的想法，然后点击「优化提示词」。\n\n"
            "优化结果会显示在这里。支持安全回填的输入框会自动填入结果，"
            "否则可以手动复制；工具不会自动发送。\n\n"
            "首次使用？请先在「设置」中配置 API Key，并校准输入框。"
        )

    def _hotkey_text(self) -> str:
        return (
            f"模型 {self.cfg.model} · 优化 {self.cfg.hotkey} · "
            f"恢复 {self.cfg.hotkey_restore or '未设置'}"
        )

    def _log_geometry(self) -> None:
        for name, widget in (
            ("optimize", self.optimize_btn),
            ("restore", self.restore_btn),
            ("copy", self.copy_btn),
            ("refine_entry", self.refine_entry),
            ("refine_btn", self.refine_btn),
            ("pin", self.pin_btn),
        ):
            self._log(
                f"ui: {name} x={widget.winfo_rootx()} y={widget.winfo_rooty()} "
                f"w={widget.winfo_width()} h={widget.winfo_height()}"
            )

    def _show_text(self, text: str) -> None:
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text[:MAX_PREVIEW])
        self.result.configure(state="disabled")

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_var.set(text)
        self.status_label.configure(foreground="#a12c2c" if error else "#2855a2", background="#fcecec" if error else "#e8efff")

    # ---------------- events ----------------
    def _post(self, kind: str, payload=None) -> None:
        self.events.put((kind, payload))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, kind: str, payload) -> None:
        if kind == "status":
            self._set_status(str(payload))
        elif kind == "busy":
            self._set_busy(bool(payload))
        elif kind == "applied":
            original, optimized, usage = payload
            self.original, self.optimized = original, optimized
            self.applied = True
            self._show_text(optimized)
            self._set_status(f"已回填{usage}。请检查后手动发送；可在下方继续对话调整。")
            self.copy_btn.configure(state="normal")
            self.restore_btn.configure(state="normal")
            self._set_refine_enabled(True)
        elif kind == "refined":
            optimized, usage = payload
            self.optimized = optimized
            self.applied = True
            self._show_text(optimized)
            self._set_status(f"已按你的要求调整并回填{usage}。")
            self.copy_btn.configure(state="normal")
            self.restore_btn.configure(state="normal")
            self._set_refine_enabled(True)
            self.refine_var.set("")
            self.refine_entry.focus_set()
        elif kind == "manual":
            optimized, reason = payload
            self.optimized = optimized
            self.applied = False
            self._show_text(optimized)
            self._set_status(f"{reason}。结果保留在此，可手动复制。", error=True)
            self.copy_btn.configure(state="normal")
            self.restore_btn.configure(state="disabled")
            self._set_refine_enabled(False)
        elif kind == "restored":
            self.applied = False
            self.messages = []
            self._show_text(self.original)
            self._set_status("已恢复原文。")
            self.restore_btn.configure(state="disabled")
            self.copy_btn.configure(state="normal")
            self._set_refine_enabled(False)
        elif kind == "error":
            self._set_status(str(payload), error=True)
            self._show_text(str(payload))
        elif kind == "nokey":
            self._set_status("未配置 DeepSeek API Key，原文未改动。", error=True)
            self._show_text(
                "未找到 DeepSeek API Key，原文未改动。\n\n"
                "任选一种方式配置（不要把密钥发到聊天里）：\n"
                "1. 设置用户环境变量 DEEPSEEK_API_KEY，然后重启本工具；\n"
                "2. 或点击「设置 → 写入 API Key」，密钥只保存在本机：\n"
                "   %APPDATA%\\PromptPolish\\api_key.txt\n\n"
                "密钥获取：https://platform.deepseek.com/api_keys"
            )
        elif kind == "key_unreadable":
            self._set_status("密钥文件存在，但当前用户读不了（文件权限损坏）。", error=True)
            self._show_text(
                "检测到 API Key 文件，但读取被拒绝。这通常是写入时的权限设置失败导致的。\n\n"
                "在 PowerShell 中执行下面一行即可修复（不会改动密钥内容）：\n"
                "icacls \"$env:APPDATA\\PromptPolish\\api_key.txt\" /reset\n\n"
                "或删除该文件后，在「设置 → 写入 API Key」重新写入。"
            )
        elif kind == "hotkey":
            self.on_optimize()
        elif kind == "restore_hotkey":
            self.on_restore()
        elif kind == "target":
            self.target_var.set(f"目标：{payload}")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.optimize_btn.configure(text="处理中…" if busy else "优化提示词")
        self.optimize_btn.configure(state="disabled" if busy else "normal")
        self._set_refine_enabled((not busy) and self.applied)
        # While operating, drop topmost so the floating window cannot cover the
        # composer click point; restore the user's pin choice afterwards.
        self._apply_pin()

    def _apply_pin(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.pinned) and not self.busy)
        except tk.TclError:
            pass

    def on_toggle_pin(self) -> None:
        self.pinned = bool(self.pin_var.get())
        self._apply_pin()
        self.cfg.always_on_top = self.pinned
        try:
            config.save_config(self.cfg)
        except OSError as exc:
            self._set_status(f"固定状态已切换，但保存配置失败：{exc}", error=True)
            return
        if self.pinned:
            self._set_status("已固定在最前：滚动或切换窗口时仍停留在最上方。")
        else:
            self._set_status("已取消固定：窗口恢复普通行为。")

    def _set_refine_enabled(self, enabled: bool) -> None:
        self.refine_btn.configure(state="normal" if enabled else "disabled")
        self.refine_entry.configure(state="normal" if enabled else "disabled")

    # ---------------- target ----------------
    def refresh_target(self) -> None:
        try:
            target = winapi.find_target_window(self.cfg.window_title_regex)
        except Exception as exc:
            self.target_var.set(f"目标：查找失败（{exc}）")
            return
        if target:
            suffix = "（已最小化）" if target.minimized else ""
            self.target_var.set(f"目标：{target.title}{suffix}")
        else:
            self.target_var.set("目标：未找到 DeepSeek Harness / Codex 窗口")

    # ---------------- hotkey ----------------
    def _register_hotkey(self) -> None:
        try:
            mods, vk = config.parse_hotkey(self.cfg.hotkey)
        except ValueError as exc:
            self._set_status(f"快捷键配置无效：{exc}", error=True)
            return
        ok, err = self.hotkey.register(mods, vk, lambda: self._post("hotkey"))
        if ok:
            self.hotkey_var.set(self._hotkey_text())
        else:
            self.hotkey_var.set("快捷键：注册失败")
            self._set_status(f"快捷键注册失败：{err}", error=True)
        if self.cfg.hotkey_restore:
            try:
                rmods, rvk = config.parse_hotkey(self.cfg.hotkey_restore)
            except ValueError:
                return
            self.restore_hotkey.register(rmods, rvk, lambda: self._post("restore_hotkey"))

    # ---------------- actions ----------------
    def _ensure_key(self) -> bool:
        status = config.api_key_status()
        if status in ("env", "ok"):
            return True
        self._post("key_unreadable" if status == "unreadable" else "nokey")
        return False

    def _usage_text(self, result: llm.LLMResult) -> str:
        if result.prompt_tokens is None:
            return ""
        return f"（{result.prompt_tokens}+{result.completion_tokens or 0} tokens）"

    def on_optimize(self) -> None:
        if self.busy:
            return
        self.refresh_target()
        target = winapi.find_target_window(self.cfg.window_title_regex)
        if not target:
            self._set_status("未找到目标窗口，请先打开 DeepSeek Harness 的聊天窗口。", error=True)
            return
        if not self._ensure_key():
            return
        self._set_busy(True)
        self._set_status("正在读取输入框…")
        threading.Thread(target=self._optimize_worker, args=(target,), daemon=True).start()

    def _optimize_worker(self, target: winapi.WindowInfo) -> None:
        try:
            winapi.set_foreground(target.hwnd)
            time.sleep(0.2)
            self._log("optimize: start")
            read = dsh.read_draft(target, self.cfg)
            self._log(f"optimize: read ok={read.ok} code={read.code} len={read.length}")
            if not read.ok:
                self._post("error", read.message)
                return
            draft = rules.normalize_draft(read.value)
            if not draft:
                self._post("error", "输入框为空，没有可优化的文字。")
                return
            if read.special:
                self._post("error", "检测到附件或特殊引用：" + "；".join(read.special) + "。已停止自动回填，草稿未改动。")
                return
            reasons = rules.detect_special_text(draft)
            if reasons:
                self._post("error", "检测到特殊内容：" + "；".join(reasons) + "。已停止自动回填，草稿未改动。")
                return
            key = config.get_api_key()
            if not key:
                self._post("nokey")
                return
            self.snapshot = Snapshot(hwnd=target.hwnd, title=read.title, draft=draft)
            self._post("status", f"正在调用 DeepSeek（{self.cfg.model}）…")
            result = llm.optimize(draft, self.cfg, key)
            self._log(f"optimize: llm ok len={len(result.text)}")
            self._post("status", "正在校验草稿并回填…")
            applied = dsh.apply_draft(
                target,
                self.cfg,
                expected_title=read.title,
                expected_draft=draft,
                new_text=result.text,
            )
            self._log(f"optimize: apply ok={applied.ok} code={applied.code} len={applied.length}")
            if applied.ok:
                self.messages = [
                    {"role": "system", "content": self.cfg.system_prompt},
                    {"role": "user", "content": rules.build_user_message(draft)},
                    {"role": "assistant", "content": result.text},
                ]
                self._post("applied", (draft, result.text, self._usage_text(result)))
            else:
                self._post("manual", (result.text, applied.message))
        except llm.LLMError as exc:
            self._post("error", exc.message + "；原文未改动。")
        except Exception as exc:  # pragma: no cover - defensive
            self._log_exception("optimize", exc)
            self._post("error", f"意外错误：{exc}；原文未改动。")
        finally:
            self._post("busy", False)

    def on_refine(self) -> None:
        if self.busy:
            return
        instruction = self.refine_var.get().strip()
        if not instruction:
            self._set_status("请先在「继续调整」里写下要求，例如：再精简 30%，补充验收标准。", error=True)
            return
        if not self.applied or not self.optimized or self.snapshot is None:
            self._set_status("请先完成一次优化并回填，再继续调整。", error=True)
            return
        if not self._ensure_key():
            return
        target = winapi.find_target_window(self.cfg.window_title_regex)
        if not target:
            self._set_status("未找到目标窗口，无法继续调整。", error=True)
            return
        self._set_busy(True)
        self._set_status("正在按你的要求调整…")
        threading.Thread(
            target=self._refine_worker, args=(target, instruction), daemon=True
        ).start()

    def _refine_messages(self, instruction: str) -> list[dict]:
        base = [m for m in self.messages[1:] if m.get("role") in ("user", "assistant")]
        if len(base) > 8:
            base = base[-8:]
        return (
            [{"role": "system", "content": self.cfg.system_prompt}]
            + base
            + [{
                "role": "user",
                "content": instruction + "\n\n请直接输出调整后的完整提示词，不要解释。",
            }]
        )

    def _refine_worker(self, target: winapi.WindowInfo, instruction: str) -> None:
        try:
            winapi.set_foreground(target.hwnd)
            time.sleep(0.2)
            read = dsh.read_draft(target, self.cfg)
            self._log(f"refine: read ok={read.ok} code={read.code} len={read.length}")
            if not read.ok:
                self._post("error", read.message)
                return
            snapshot = self.snapshot
            if snapshot is None:
                self._post("error", "没有可继续调整的优化会话。")
                return
            if read.title != snapshot.title:
                self._post("error", "当前聊天已变化，无法继续调整；请重新优化。")
                return
            current = rules.normalize_draft(read.value)
            if current != self.optimized:
                self._post("error", "草稿已被修改，无法基于上一次结果继续调整；请重新点「优化提示词」。")
                return
            key = config.get_api_key()
            if not key:
                self._post("nokey")
                return
            messages = self._refine_messages(instruction)
            result = llm.chat(messages, self.cfg, key)
            self._log(f"refine: llm ok len={len(result.text)}")
            applied = dsh.apply_draft(
                target,
                self.cfg,
                expected_title=snapshot.title,
                expected_draft=self.optimized,
                new_text=result.text,
            )
            self._log(f"refine: apply ok={applied.ok} code={applied.code}")
            if applied.ok:
                self.messages = messages + [{"role": "assistant", "content": result.text}]
                self._post("refined", (result.text, self._usage_text(result)))
            else:
                self._post("manual", (result.text, applied.message))
        except llm.LLMError as exc:
            self._post("error", exc.message + "；草稿未改动。")
        except Exception as exc:  # pragma: no cover - defensive
            self._log_exception("refine", exc)
            self._post("error", f"继续调整失败：{exc}")
        finally:
            self._post("busy", False)

    def on_restore(self) -> None:
        self._log(f"restore: clicked busy={self.busy} applied={self.applied} has_opt={bool(self.optimized)}")
        if self.busy or not self.applied or not self.optimized:
            return
        target = winapi.find_target_window(self.cfg.window_title_regex)
        if not target:
            self._set_status("未找到目标窗口，无法恢复。", error=True)
            return
        self._set_busy(True)
        self._set_status("正在恢复原文…")
        threading.Thread(target=self._restore_worker, args=(target,), daemon=True).start()

    def _restore_worker(self, target: winapi.WindowInfo) -> None:
        try:
            winapi.set_foreground(target.hwnd)
            time.sleep(0.2)
            snapshot = self.snapshot
            if snapshot is None:
                self._post("error", "没有可恢复的快照。")
                return
            res = dsh.apply_draft(
                target,
                self.cfg,
                expected_title=snapshot.title,
                expected_draft=self.optimized,
                new_text=self.original,
            )
            if res.ok:
                self._log("restore: apply ok")
                self._post("restored")
            elif res.code == "draft_changed":
                self._post("error", "草稿在优化后已被修改，未自动恢复。原文已显示，可手动复制。\n\n" + self.original)
            else:
                self._post("error", res.message + "；请检查输入框中的内容。")
        except Exception as exc:  # pragma: no cover
            self._log_exception("restore", exc)
            self._post("error", f"恢复失败：{exc}")
        finally:
            self._post("busy", False)

    def on_copy(self) -> None:
        text = self.optimized or self.original
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("已复制到剪贴板。")

    def on_calibrate(self) -> None:
        target = winapi.find_target_window(self.cfg.window_title_regex)
        if not target:
            messagebox.showwarning(APP_TITLE, "未找到目标窗口，请先打开 DeepSeek Harness 聊天窗口。")
            return
        messagebox.showinfo(
            APP_TITLE,
            "点击“确定”后，请在 5 秒内把鼠标移到 DeepSeek 输入框内部并停住。\n"
            "工具只记录鼠标相对窗口的位置，不会读取任何内容。",
        )
        self.root.withdraw()

        def capture() -> None:
            self.root.deiconify()
            self.root.lift()
            self._apply_pin()
            x, y = winapi.cursor_pos()
            t = winapi.find_target_window(self.cfg.window_title_regex)
            if not t:
                self._set_status("校准失败：目标窗口已消失。", error=True)
                return
            x0, y0, x1, y1 = t.rect
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                self._set_status("校准失败：鼠标不在目标窗口内，请重试。", error=True)
                return
            rel_x = (x - x0) / max(1, x1 - x0)
            rel_y = (y - y0) / max(1, y1 - y0)
            if not (0.30 <= rel_x <= 0.80 and 0.50 <= rel_y <= 0.84):
                self._set_status(
                    "校准点超出输入框文字区域（可能点到工具栏），未保存。请重新校准。", error=True
                )
                return
            self.cfg.click_rel_x = round(rel_x, 4)
            self.cfg.click_rel_y = round(rel_y, 4)
            config.save_config(self.cfg)
            self._set_status(f"校准完成：点击位置 ({rel_x:.2%}, {rel_y:.2%})。")
            self.refresh_target()

        self.root.after(5000, capture)

    # ---------------- settings ----------------
    def open_settings(self) -> None:
        SettingsDialog(self)

    # ---------------- misc ----------------
    def _log(self, message: str) -> None:
        if not self.debug_log:
            return
        try:
            with open(config.log_path(), "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")
        except OSError:
            pass

    def _log_exception(self, where: str, exc: BaseException) -> None:
        try:
            if not self.debug_log:
                return
            with open(config.log_path(), "a", encoding="utf-8") as fh:
                fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}: {exc}\n")
                fh.write(traceback.format_exc())
        except OSError:
            pass

    def _on_close(self) -> None:
        try:
            self.hotkey.unregister()
            self.restore_hotkey.unregister()
        except Exception:
            pass
        self.root.destroy()


class SettingsDialog:
    def __init__(self, app: PromptPolishApp):
        self.app = app
        self.cfg = app.cfg
        self.top = tk.Toplevel(app.root)
        self.top.title("PromptPolish 设置")
        self.top.transient(app.root)
        self.top.attributes("-topmost", True)
        self.top.resizable(False, False)
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self.vars: dict[str, tk.Variable] = {}
        rows = [
            ("api_base_url", "API 地址", str),
            ("model", "模型", str),
            ("timeout_seconds", "超时（秒）", int),
            ("hotkey", "快捷键", str),
            ("hotkey_restore", "恢复快捷键", str),
            ("window_title_regex", "窗口标题匹配（正则）", str),
            ("composer_name_prefix", "输入框名称前缀", str),
            ("click_rel_x", "点击位置 X（0-1）", float),
            ("click_rel_y", "点击位置 Y（0-1）", float),
        ]
        for idx, (key, label, typ) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=idx, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(getattr(self.cfg, key)))
            self.vars[key] = var
            if key == "model":
                ttk.Combobox(
                    frame,
                    textvariable=var,
                    width=32,
                    values=("deepseek-flash", "deepseek-v4-pro", "deepseek-chat"),
                ).grid(row=idx, column=1, sticky="we", pady=3, padx=(8, 0))
            else:
                ttk.Entry(frame, textvariable=var, width=34).grid(
                    row=idx, column=1, sticky="we", pady=3, padx=(8, 0)
                )

        ttk.Label(
            frame,
            text="质量优先选 deepseek-v4-pro（较慢），速度/费用优先选 deepseek-flash。",
            foreground="#666666",
            wraplength=420,
        ).grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(4, 0))

        status = config.api_key_status()
        key_state = {
            "env": "已通过环境变量配置",
            "ok": "已配置（本机文件）",
            "unreadable": "文件存在但无法读取（权限问题）",
            "empty": "文件为空",
        }.get(status, "未配置")
        ttk.Label(frame, text=f"API Key：{key_state}", foreground="#666666").grid(
            row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(6, 2)
        )
        btns = ttk.Frame(frame)
        btns.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttk.Button(btns, text="写入 API Key…", command=self._write_key).pack(side="left")
        ttk.Button(btns, text="打开配置目录", command=self._open_dir).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side="right")
        ttk.Button(btns, text="保存", command=self._save).pack(side="right", padx=(0, 6))

    def _write_key(self) -> None:
        dlg = tk.Toplevel(self.top)
        dlg.title("写入 API Key")
        dlg.transient(self.top)
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        ttk.Label(dlg, text="密钥只保存到本机用户目录，不会写入配置或日志：").pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(dlg, text=str(config.key_path()), foreground="#666666").pack(anchor="w", padx=12)
        var = tk.StringVar()
        entry = ttk.Entry(dlg, textvariable=var, show="*", width=48)
        entry.pack(padx=12, pady=8)
        entry.focus_set()

        def save() -> None:
            value = var.get().strip()
            if not value:
                return
            config.save_api_key(value)
            readable = bool(config.get_api_key())
            dlg.destroy()
            if readable:
                messagebox.showinfo(APP_TITLE, "API Key 已保存，并已验证可读取。")
            else:
                messagebox.showwarning(
                    APP_TITLE,
                    "密钥已写入，但读取验证失败。请对该文件执行 icacls /reset，或删除后重新写入。",
                )

        ttk.Button(dlg, text="保存", command=save).pack(pady=(0, 12))

    def _open_dir(self) -> None:
        try:
            os.startfile(str(config.app_dir()))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"无法打开目录：{exc}")

    def _save(self) -> None:
        try:
            hotkey = str(self.vars["hotkey"].get()).strip()
            mods, vk = config.parse_hotkey(hotkey)
            restore_hotkey = str(self.vars["hotkey_restore"].get()).strip()
            if restore_hotkey:
                config.parse_hotkey(restore_hotkey)
            values = {
                "api_base_url": str(self.vars["api_base_url"].get()).strip(),
                "model": str(self.vars["model"].get()).strip(),
                "timeout_seconds": int(float(self.vars["timeout_seconds"].get())),
                "hotkey": hotkey,
                "hotkey_restore": restore_hotkey,
                "window_title_regex": str(self.vars["window_title_regex"].get()).strip(),
                "composer_name_prefix": str(self.vars["composer_name_prefix"].get()).strip(),
                "click_rel_x": float(self.vars["click_rel_x"].get()),
                "click_rel_y": float(self.vars["click_rel_y"].get()),
            }
            if not values["api_base_url"]:
                raise ValueError("API 地址不能为空")
            if not values["model"]:
                raise ValueError("模型不能为空")
            if not (0.30 <= values["click_rel_x"] <= 0.80):
                raise ValueError("点击位置 X 应在 0.30 到 0.80 之间（输入框文字区域）")
            if not (0.50 <= values["click_rel_y"] <= 0.84):
                raise ValueError("点击位置 Y 应在 0.50 到 0.84 之间，不要落在底部工具栏")
            if values["timeout_seconds"] < 5:
                raise ValueError("超时不能小于 5 秒")
        except (ValueError, TypeError) as exc:
            messagebox.showerror(APP_TITLE, f"设置无效：{exc}")
            return
        for k, v in values.items():
            setattr(self.cfg, k, v)
        config.save_config(self.cfg)
        self.top.destroy()
        self.app.hotkey_var.set(self.app._hotkey_text())
        self.app._register_hotkey()
        self.app.refresh_target()
        self.app._set_status("设置已保存。")


def _single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "PromptPolish_SingleInstance")
    return ctypes.get_last_error() != 183


def main() -> None:
    winapi.enable_dpi_awareness()
    if not _single_instance():
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(APP_TITLE, "PromptPolish 已经在运行。")
            root.destroy()
        except Exception:
            pass
        return
    root = tk.Tk()
    app = PromptPolishApp(root)

    def report(exc_type, exc_value, exc_tb):
        app._log_exception("tk", exc_value)
        app._set_status(f"界面错误：{exc_value}", error=True)

    root.report_callback_exception = report
    root.mainloop()
