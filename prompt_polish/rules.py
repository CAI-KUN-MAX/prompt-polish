"""Pure text rules: normalization, special-content detection, prompt building."""
from __future__ import annotations

import re

# Mentions: @ at start of text or after whitespace, followed by a token.
_MENTION_RE = re.compile(r"(?<![\w.])@[^\s@]{1,120}")
# Slash command / skill reference: a line starting with "/name".
_SLASH_RE = re.compile(r"(?m)^\s*/[A-Za-z][\w.-]{1,60}\s*$")
# Explicit file / image / attachment markers.
_FILE_MARKERS = ("file://", "attachment:", "![", "<img", "data:image/", "sandbox:/")
_SKILL_MARKERS = ("skill:", "skills/", ".dsh/skills")


def normalize_draft(text: str | None) -> str:
    """Normalize line endings and trim only outer blank lines."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return ""
    return text.strip("\n")


def is_empty(text: str | None) -> bool:
    return not (text or "").strip()


def detect_special_text(text: str) -> list[str]:
    """Return human-readable reasons why a plain-text draft may not be safe to replace.

    These are conservative heuristics. The UI layer also inspects the composer's
    accessibility subtree for attachment chips and open mention popups.
    """
    reasons: list[str] = []
    if _MENTION_RE.search(text):
        reasons.append("草稿包含 @ 提及/引用，无法确认替换后仍原样保留")
    if _SLASH_RE.search(text):
        reasons.append("草稿包含以 / 开头的指令或技能引用")
    low = text.lower()
    if any(m in low for m in _FILE_MARKERS):
        reasons.append("草稿包含文件或图片引用标记")
    if any(m in low for m in _SKILL_MARKERS):
        reasons.append("草稿包含技能路径引用")
    return reasons


def strip_code_fence(text: str) -> str:
    """Remove a single wrapping ```...``` fence if the model added one."""
    s = text.strip()
    if s.startswith("```") and s.rstrip().endswith("```"):
        lines = s.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip("\n")
    return s


def build_user_message(draft: str) -> str:
    return (
        "请润色下面这段提示词。只输出润色后的正文，不要解释。\n"
        "<<<PROMPT\n"
        f"{draft}\n"
        "PROMPT>>>"
    )


def short_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
