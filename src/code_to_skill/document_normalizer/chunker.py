"""Chunk 切分。

按语义完整性切分 blocks 为 DocumentChunk。
"""
from __future__ import annotations

from .types import DocumentChunk

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))
except ImportError:
    def _count_tokens(text: str) -> int:
        return len(text) // 4


def chunk_blocks(
    blocks: list[dict],
    source_id: str,
    max_chunk_tokens: int = 2000,
) -> list[DocumentChunk]:
    """将 blocks 切分为语义完整的 DocumentChunk。"""
    chunks: list[DocumentChunk] = []
    current_text = ""
    current_heading = ""
    chunk_idx = 0
    page = 0

    def flush():
        nonlocal current_text, current_heading, chunk_idx, page
        if not current_text.strip():
            return
        chunk_idx += 1
        tokens = _count_tokens(current_text)
        chunks.append(DocumentChunk(
            chunk_id=f"{source_id}:chunk-{chunk_idx:04d}",
            source_id=source_id,
            heading_path=[current_heading] if current_heading else [],
            text=current_text.strip(),
            page=page,
            token_estimate=tokens,
            content_type=_classify_text(current_text),
        ))
        current_text = ""

    for blk in blocks:
        typ = blk.get("type", "")
        text = blk.get("text", "")
        if typ == "heading":
            flush()
            current_heading = text
            continue

        page = blk.get("page", page)

        if typ == "code_block":
            text = f"```\n{text}\n```"

        # 检查是否需要flush
        additional_tokens = _count_tokens(text)
        current_tokens = _count_tokens(current_text)

        if current_tokens + additional_tokens > max_chunk_tokens and current_text.strip():
            flush()

        current_text += text + "\n"

    flush()
    return chunks


def _classify_text(text: str) -> str:
    """简单启发式内容类型识别。"""
    lower = text.lower()
    if "步骤" in text or "流程" in text or "先" in text[:10] or "step" in lower[:20]:
        return "procedure"
    if "不得" in text or "禁止" in text or "must not" in lower or "严禁" in text:
        return "constraint"
    if "错误码" in text or "error code" in lower:
        return "error_code"
    if "q:" in lower[:5] or "a:" in lower[:5] or "常见问题" in text:
        return "faq"
    if "api" in lower[:5] or "endpoint" in lower[:10] or "request" in lower[:5] or "response" in lower[:5]:
        return "api_contract"
    if "```" in text or "class " in text[:10] or "def " in text[:10] or "function " in text[:10]:
        return "example"
    return "concept"
