"""从 atom 文本提取对齐/种子用词（通用启发式，无领域词表）。"""
from __future__ import annotations

import re

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "must", "not", "have",
    "will", "been", "were", "was", "are", "has", "had", "can", "should",
    "在", "中", "的", "和", "或", "是", "将", "为", "所", "以", "及", "等",
    "发现", "实现", "定义", "包含", "操作", "逻辑", "文档", "必须", "不得",
    "check", "before", "after", "with", "from", "into", "when", "that",
})


def extract_alignment_tokens(text: str) -> set[str]:
    """从 claim/action 等文本提取可用于跨来源对齐的术语 token。"""
    if not text:
        return set()

    tokens: set[str] = set()
    lower = text.lower()

    for match in re.finditer(r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b", text):
        name = match.group(1)
        tokens.add(name.lower())
        for part in re.findall(r"[A-Z]?[a-z]+", name):
            part_l = part.lower()
            if len(part_l) >= 3 and part_l not in _STOPWORDS:
                tokens.add(part_l)

    for match in re.finditer(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", lower):
        for part in match.group(0).split("_"):
            if len(part) >= 3 and part not in _STOPWORDS:
                tokens.add(part)

    for match in re.finditer(r"\b[a-z]{4,}\b", lower):
        word = match.group(0)
        if word not in _STOPWORDS:
            tokens.add(word)

    for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        phrase = match.group(0)
        if phrase not in _STOPWORDS:
            tokens.add(phrase)

    return tokens


def extract_seed_check_tokens(*texts: str, limit: int = 5) -> list[str]:
    """从 atom 文本补充 benchmark seed 的 expected_checks（保留原 checks 优先）。"""
    seen: set[str] = set()
    out: list[str] = []

    for text in texts:
        for token in sorted(extract_alignment_tokens(text), key=len, reverse=True):
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
            if len(out) >= limit:
                return out
    return out
