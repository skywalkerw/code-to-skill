"""Gradient 模块：分层 patch 合并。

对齐 external/SkillOpt skillopt/gradient/aggregate.py。

Hierarchical merge 流程：
1. 分别合并 failure patches → merged_failure
2. 分别合并 success patches → merged_success
3. 最终合并（failure 优先）→ final merged patch

当前仓库当前只有一个简单的 patching 拼接，升级为支持 LLM 或规则分层合并。
"""
from __future__ import annotations

import logging
from typing import Any

from ..types import EditOp, MergedPatch

logger = logging.getLogger(__name__)


def merge_patches(
    failure_patches: list[dict],
    success_patches: list[dict],
    current_skill: str = "",
    optimizer_backend: Any = None,
) -> MergedPatch:
    """分层合并 failure 和 success patches。

    Args:
        failure_patches: 失败分析产生的 patch 列表
        success_patches: 成功分析产生的 patch 列表
        current_skill: 当前 Skill（供 LLM merge 时参考）
        optimizer_backend: optimizer 后端（LLM 可用时做智能合并）

    Returns:
        MergedPatch with merged edits
    """
    # Step 1: 收集所有 edits
    failure_edits: list[EditOp] = []
    for p in failure_patches:
        for e in p.get("edits", []):
            edit = EditOp(**e) if isinstance(e, dict) else e
            edit.source_type = "failure"
            failure_edits.append(edit)

    success_edits: list[EditOp] = []
    for p in success_patches:
        for e in p.get("edits", []):
            edit = EditOp(**e) if isinstance(e, dict) else e
            edit.source_type = "success"
            success_edits.append(edit)

    # Step 2: 去重（相同 op + content 的编辑只保留一个）
    failure_edits = _deduplicate_edits(failure_edits)
    success_edits = _deduplicate_edits(success_edits)

    # Step 3: LLM 分层合并（如果 backend 可用 + edits 足够多）
    if optimizer_backend and (len(failure_edits) + len(success_edits)) > 3:
        try:
            return _llm_hierarchical_merge(
                failure_edits, success_edits, current_skill, optimizer_backend
            )
        except Exception as e:
            logger.warning("LLM hierarchical merge failed, falling back to simple merge: %s", e)

    # Step 4: 降级 — 简单拼接（failure 优先）
    all_edits = failure_edits + success_edits
    return MergedPatch(
        edits=all_edits,
        reasoning=f"Merged {len(failure_edits)} failure + {len(success_edits)} success edits (simple merge)",
    )


def _deduplicate_edits(edits: list[EditOp]) -> list[EditOp]:
    """去重：相同 op + 相同 content 前 100 字符的编辑只保留一个。"""
    seen: set[str] = set()
    unique: list[EditOp] = []
    for e in edits:
        key = f"{e.op}:{(e.content or '')[:100].strip()}"
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _llm_hierarchical_merge(
    failure_edits: list[EditOp],
    success_edits: list[EditOp],
    current_skill: str,
    backend: Any,
) -> MergedPatch:
    """用 LLM 做分层合并。

    1. 先分别 merge failure + success（两组独立调用）
    2. 再 final merge（failure 优先）
    """
    from code_to_skill.model_gateway.types import InteractionRequest
    from code_to_skill.model_gateway.structured_output import invoke_with_structured_output
    from ..json_utils import safe_json_parse

    # Merge failure edits
    failure_text = _format_edits(failure_edits)
    failure_merged = failure_edits
    if len(failure_edits) > 1:
        resp = backend.invoke(InteractionRequest(
            role="optimizer",
            stage="merge_failure",
            messages=[{
                "role": "system",
                "content": _MERGE_PROMPT_TEMPLATE.format(
                    edit_type="failure",
                    current_skill=current_skill[:1000],
                    edits=failure_text[:2000],
                ),
            }],
            max_output_tokens=512,
            temperature=0.2,
        ))
        parsed = safe_json_parse(resp.content)
        if parsed and isinstance(parsed, dict):
            failure_merged = _parse_edits_from_response(parsed, failure_edits)

    # Merge success edits
    success_text = _format_edits(success_edits)
    success_merged = success_edits
    if len(success_edits) > 1:
        resp = backend.invoke(InteractionRequest(
            role="optimizer",
            stage="merge_success",
            messages=[{
                "role": "system",
                "content": _MERGE_PROMPT_TEMPLATE.format(
                    edit_type="success",
                    current_skill=current_skill[:1000],
                    edits=success_text[:2000],
                ),
            }],
            max_output_tokens=512,
            temperature=0.2,
        ))
        parsed = safe_json_parse(resp.content)
        if parsed and isinstance(parsed, dict):
            success_merged = _parse_edits_from_response(parsed, success_edits)

    # Final merge (failure priority)
    final = failure_merged + success_merged
    return MergedPatch(
        edits=final,
        reasoning=(
            f"Hierarchical merge: {len(failure_edits)}→{len(failure_merged)} failure, "
            f"{len(success_edits)}→{len(success_merged)} success "
            f"(total: {len(final)})"
        ),
    )


def _format_edits(edits: list[EditOp]) -> str:
    """格式化编辑列表供 prompt 使用。"""
    lines = []
    for i, e in enumerate(edits):
        lines.append(f"{i+1}. [{e.op}] content=\"{e.content[:80]}\"")
    return "\n".join(lines)


def _parse_edits_from_response(parsed: dict, originals: list[EditOp]) -> list[EditOp]:
    """从 LLM 响应中解析编辑列表。"""
    edits_data = parsed.get("edits", [])
    result: list[EditOp] = []
    for ed in edits_data:
        if isinstance(ed, dict):
            result.append(EditOp(**ed))
        elif isinstance(ed, EditOp):
            result.append(ed)
    return result if result else originals  # fallback to originals


_MERGE_PROMPT_TEMPLATE = """## Task
Merge and deduplicate {edit_type}-driven skill edits. Remove redundant, contradictory, and overly specific edits.

## Current Skill
{current_skill}

## Candidate Edits
{edits}

## Instructions
1. Remove exact duplicates.
2. Merge edits with overlapping intent into a single, more general edit.
3. Remove edits that contradict existing or higher-priority edits.
4. Keep edits concise and generalizable (not instance-specific).
5. Preserve the edit order: make the most important edit first.

## Output
Return JSON: {{"reasoning": "...", "edits": [{{"op": "append", "content": "..."}}]}}"""