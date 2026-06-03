"""清洗与脱敏。"""
from __future__ import annotations

import re

# 脱敏模式
_REDACT_PATTERNS = [
    (re.compile(r'sk-[A-Za-z0-9]{32,}'), "<REDACTED_API_KEY>"),
    (re.compile(r'(?:api[_-]?key|apikey|secret|token|password)\s*[:=]\s*["\']?([^"\'&\s]+)', re.I),
     lambda m: f"{m.group(0).split('=')[0].split(':')[0]}=<REDACTED>"),
    (re.compile(r'(?:Bearer|Basic)\s+[A-Za-z0-9+/=_-]{20,}'), "<REDACTED_AUTH>"),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), "<REDACTED_EMAIL>"),
    # 数据库连接串
    (re.compile(r'(?:jdbc|mysql|postgresql|mongodb)://[^\s"\']+', re.I), "<REDACTED_DB_URL>"),
]


def clean_text(text: str) -> str:
    """清洗文本：统一空白，保留代码块原始格式。"""
    # 统一全角/半角空格
    text = text.replace("\u3000", " ")
    # 删除重复空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def redact_text(text: str) -> tuple[str, int]:
    """脱敏处理。返回 (脱敏后文本, 脱敏次数)。"""
    count = 0
    for pat, repl in _REDACT_PATTERNS:
        if callable(repl):
            new_text, n = pat.subn(repl, text)
        else:
            new_text, n = pat.subn(repl, text)
        count += n
        text = new_text
    return text, count


def normalize_blocks(blocks: list[dict]) -> list[dict]:
    """对 blocks 做清洗和脱敏。"""
    cleaned: list[dict] = []
    for blk in blocks:
        if "text" in blk:
            txt, redacted = redact_text(blk["text"])
            txt = clean_text(txt)
            blk = {**blk, "text": txt}
            if redacted:
                blk.setdefault("flags", []).append("redacted")
        cleaned.append(blk)
    return cleaned
