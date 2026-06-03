"""结构恢复：heading tree + 跨页合并。"""
from __future__ import annotations

from .types import DocumentIndex, SectionInfo


def build_structure(blocks: list[dict], source_id: str, title: str = "") -> DocumentIndex:
    """从 blocks 构建 heading tree。"""
    sections: list[SectionInfo] = []
    stack: list[SectionInfo] = []
    sec_counter = 0
    current_chunk_ids: list[str] = []

    for blk in blocks:
        if blk.get("type") == "heading":
            # 上一个 section 结束
            if stack and current_chunk_ids:
                stack[-1].chunk_ids = list(set(stack[-1].chunk_ids + current_chunk_ids))
                current_chunk_ids = []

            level = blk.get("level", 1)
            heading = blk.get("text", "")
            sec_counter += 1
            sec_id = f"{source_id}:sec-{sec_counter:03d}"

            # pop deeper levels
            while stack and stack[-1].level >= level:
                stack.pop()

            parent_id = stack[-1].section_id if stack else "sec-root"
            section = SectionInfo(
                section_id=sec_id,
                heading=heading,
                level=level,
                parent_id=parent_id,
                page_range=[blk.get("page", 0), blk.get("page", 0)],
            )
            sections.append(section)
            stack.append(section)
        else:
            # assign chunk to current section
            chunk_id = f"{source_id}:chunk-{len(current_chunk_ids):03d}"
            current_chunk_ids.append(chunk_id)

    # flush remaining
    if stack and current_chunk_ids:
        stack[-1].chunk_ids = list(set(stack[-1].chunk_ids + current_chunk_ids))

    return DocumentIndex(title=title or source_id, sections=sections)
