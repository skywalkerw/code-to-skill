"""搜索查询解析 — 对齐 external/codegraph query-parser 子集。

支持前缀过滤：
  kind:class method   → 限定节点类型
  file:**/*.java      → 限定文件路径 glob
  其余 token 作为 FTS 检索词
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedQuery:
    terms: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)

    @property
    def text_query(self) -> str:
        return " ".join(self.terms).strip()


_KIND_PREFIX = re.compile(r"^kind:([\w,]+)$", re.IGNORECASE)
_FILE_PREFIX = re.compile(r"^file:(.+)$", re.IGNORECASE)


def parse_query(raw: str) -> ParsedQuery:
    """解析混合查询字符串。"""
    parsed = ParsedQuery()
    for token in _tokenize(raw):
        m_kind = _KIND_PREFIX.match(token)
        if m_kind:
            parsed.kinds.extend(k.strip().lower() for k in m_kind.group(1).split(",") if k.strip())
            continue
        m_file = _FILE_PREFIX.match(token)
        if m_file:
            parsed.file_patterns.append(m_file.group(1).strip())
            continue
        parsed.terms.append(token)
    return parsed


def _tokenize(raw: str) -> list[str]:
    tokens: list[str] = []
    for part in re.split(r"\s+", raw.strip()):
        if not part:
            continue
        # 引号包裹的短语
        if part.startswith('"') and part.endswith('"') and len(part) > 1:
            tokens.append(part[1:-1])
        else:
            tokens.append(part)
    return tokens
