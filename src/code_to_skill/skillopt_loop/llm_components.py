"""M4 LLM 组件：Reflect（轨迹分析生成 patch）和 Select（编辑排序）。

当 LLM backend 不可用时，自动降级为规则模式。
"""
from __future__ import annotations

import json
import logging

from code_to_skill.model_provider.llm_backend import create_llm_backend, is_llm_available
from code_to_skill.model_provider.types import InteractionRequest
from code_to_skill.model_provider.structured_output import invoke_with_structured_output

from .types import EditOp

logger = logging.getLogger(__name__)

# Patch JSON Schema
PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["append", "replace", "delete", "insert_after"]},
                    "content": {"type": "string"},
                    "target": {"type": "string"},
                    "source_type": {"type": "string", "enum": ["failure", "success"]},
                },
                "required": ["op", "content"]
            }
        }
    },
    "required": ["edits"]
}


def reflect_llm(
    rollout_results: list[dict],
    current_skill: str,
    step_buffer: list[dict] | None = None,
    rejected_edits: list | None = None,
    meta_skill_context: str = "",
    backend: Any = None,
) -> list[dict]:
    """LLM Reflect：分析 rollout 轨迹，生成有意义的 patch。

    Args:
        rollout_results: 阶段 1 的 rollout 结果（含失败原因和预测答案）
        current_skill: 当前 Skill 内容
        step_buffer: 之前步骤的失败模式和 rejected edits
        rejected_edits: 已拒绝的 EditOp 列表（供 StepBufferManager 使用）
        meta_skill_context: Optimizer 侧跨 epoch 记忆（MetaSkill.render()）
        backend: 外部传入的 optimizer backend（不传则自动创建）

    Returns:
        patch dict 列表，每个含 edits 和 reasoning
    """
    if not is_llm_available():
        logger.info("LLM not available for reflect, using rule-based")
        return _rule_based_patches(rollout_results)

    if backend is None:
        backend = create_llm_backend()

    # 构建 step buffer 摘要
    buffer_summary = _build_buffer_summary(step_buffer, rejected_edits)

    # 分失败和成功
    failed = [r for r in rollout_results if r.get("hard", 0) == 0]
    succeeded = [r for r in rollout_results if r.get("hard", 1) == 1]

    patches: list[dict] = []

    # 失败分析
    if failed:
        failure_text = ""
        for r in failed[:8]:  # minibatch size
            failure_text += f"\n### Task {r.get('id', '?')}\n"
            failure_text += f"Failed checks: {r.get('fail_reason', 'unknown')}\n"
            failure_text += f"Answer snippet: {r.get('predicted_answer', '')[:200]}\n"

        system_content = _REFLECT_FAILURE_PROMPT.format(
            current_skill=current_skill[:2000],
            failure_text=failure_text[:2000],
            step_buffer_summary=buffer_summary,
        )
        if meta_skill_context.strip():
            system_content = meta_skill_context + "\n---\n" + system_content

        request = InteractionRequest(
            role="optimizer",
            stage="reflect_failure",
            messages=[{
                "role": "system",
                "content": system_content,
            }],
            max_output_tokens=1024,
            temperature=0.3,
        )

        try:
            response = invoke_with_structured_output(backend, request, target_schema=PATCH_SCHEMA)
            if response.parsed:
                patches.append({
                    "source_type": "failure",
                    "batch_size": len(failed),
                    "reasoning": response.parsed.get("reasoning", ""),
                    "edits": response.parsed.get("edits", []),
                    "failure_summary": [{"type": r.get("fail_reason", "check_missed"), "count": len(failed)} for r in failed[:3]],
                })
        except Exception as e:
            logger.warning("LLM reflect failure analysis failed: %s", e)

    # 成功保留
    if succeeded and len(patches) < 2:
        success_text = "\n".join([f"- {r.get('id', '?')}: PASS" for r in succeeded[:5]])
        success_content = _REFLECT_SUCCESS_PROMPT.format(success_text=success_text[:1500])
        if meta_skill_context.strip():
            success_content = meta_skill_context + "\n---\n" + success_content
        request = InteractionRequest(
            role="optimizer",
            stage="reflect_success",
            messages=[{
                "role": "system",
                "content": success_content,
            }],
            max_output_tokens=512,
            temperature=0.2,
        )
        try:
            response = invoke_with_structured_output(backend, request, target_schema=PATCH_SCHEMA)
            if response.parsed and response.parsed.get("edits"):
                patches.append({
                    "source_type": "success",
                    "batch_size": len(succeeded),
                    "reasoning": response.parsed.get("reasoning", ""),
                    "edits": response.parsed.get("edits", []),
                })
        except Exception as e:
            logger.warning("LLM reflect success analysis failed: %s", e)

    return patches or _rule_based_patches(rollout_results)


def select_edits_llm(
    edits: list[EditOp],
    current_skill: str,
    budget: int = 3,
    backend: Any = None,
) -> list[dict]:
    """LLM Select：对候选编辑排序，按 budget 截断。

    Args:
        edits: 待排序的编辑列表
        current_skill: 当前 Skill 内容
        budget: 最多保留的编辑数
        backend: 外部传入的 optimizer backend（不传则自动创建）

    Returns:
        排序后的编辑列表（含 rank 和 score）
    """
    if not is_llm_available() or len(edits) <= budget:
        return [{"edit": e, "rank": i + 1, "support_count": 1, "score": 1.0}
                for i, e in enumerate(edits[:budget])]

    if backend is None:
        backend = create_llm_backend()

    edit_text = "\n".join([
        f"{i+1}. [{e.op}] target='{e.target}' content='{e.content[:80]}'"
        for i, e in enumerate(edits)
    ])

    request = InteractionRequest(
        role="optimizer",
        stage="select_edits",
        messages=[{
            "role": "system",
            "content": _SELECT_PROMPT.format(
                current_skill=current_skill[:1500],
                edits=edit_text[:2000],
                budget=budget,
            )
        }],
        max_output_tokens=512,
        temperature=0.1,
    )

    try:
        response = invoke_with_structured_output(backend, request)
        if response.parsed:
            ranked = response.parsed
            if isinstance(ranked, list):
                return [
                    {"edit": edits[int(e.get("index", i)) - 1] if e.get("index") and int(e["index"]) <= len(edits) else edits[i],
                     "rank": i + 1, "support_count": e.get("support", 1), "score": e.get("score", 0.5)}
                    for i, e in enumerate(ranked[:budget])
                ]
    except Exception as e:
        logger.warning("LLM select failed: %s", e)

    # 降级：按原始顺序截断
    return [{"edit": e, "rank": i + 1, "support_count": 1, "score": 0.5}
            for i, e in enumerate(edits[:budget])]


def _rule_based_patches(results: list[dict]) -> list[dict]:
    """规则降级（当前实现）。"""
    patches = []
    failed = [r for r in results if r.get("hard", 0) == 0]
    if failed:
        patches.append({
            "source_type": "failure",
            "batch_size": len(failed),
            "failure_summary": [{"type": "check_missed", "count": len(failed)}],
            "edits": [{"op": "append", "content": f"# Verify: {len(failed)} tasks failed, need improvement", "target": "", "source_type": "failure"}],
        })
    return patches


def _build_buffer_summary(
    step_buffer: list[dict] | None,
    rejected_edits: list | None,
) -> str:
    """构建 step buffer 摘要字符串，供 Reflect prompt 使用。"""
    parts: list[str] = []

    if rejected_edits:
        parts.append("Previously REJECTED edits (do NOT propose these again):")
        for i, e in enumerate(rejected_edits[-5:]):  # 最近 5 条
            op = getattr(e, "op", "?")
            content = (getattr(e, "content", "") or "")[:80]
            parts.append(f"  - [{op}] {content}")
        parts.append("")

    if step_buffer:
        failure_types: dict[str, int] = {}
        for buf in step_buffer:
            if isinstance(buf, dict) and buf.get("type") == "failure":
                ft = buf.get("failure_type", buf.get("type", "unknown"))
                failure_types[ft] = failure_types.get(ft, 0) + 1
        if failure_types:
            parts.append("Previously observed failure patterns:")
            for ft, count in sorted(failure_types.items(), key=lambda x: -x[1]):
                parts.append(f"  - {ft}: {count} occurrences")
            parts.append("")

    if not parts:
        return "(no prior buffer information — this is the first step)"

    return "\n".join(parts)


# ── Prompt 模板 ─────────────────────────────────────────────

_REFLECT_FAILURE_PROMPT = """## Task
Analyze the failure cases and propose specific edits to improve the Skill document.

## Current Skill
{current_skill}

## Failure Cases
{failure_text}

## Step Buffer (historical context — DO NOT repeat rejected edits)
{step_buffer_summary}

## Instructions
1. Identify the ROOT CAUSE pattern in the failures (not individual edge cases).
2. Propose 1-3 specific edits to the Skill (append new rules, clarify constraints, add verification steps).
3. Each edit must have: op (append/replace), content (the new text to add), and a brief justification.
4. Check the Step Buffer above — do NOT propose edits that have already been rejected.
5. Target COMMON patterns across multiple failures, not single-task fixes.

CRITICAL: Do NOT remove existing rules unless they are contradictory. Prefer appending new rules.

## Output
Return JSON: {{"reasoning": "...", "edits": [{{"op": "append", "content": "...", "source_type": "failure"}}]}}"""

_REFLECT_SUCCESS_PROMPT = """## Task
Based on the successful cases, propose edits to retain effective rules.

## Successful Cases
{success_text}

## Instructions
If the Skill successfully handled these cases, consider adding a note to preserve the effective patterns.

## Output
Return JSON: {{"reasoning": "...", "edits": []}}"""

_SELECT_PROMPT = """## Task
Rank the following candidate edits by their likely impact on Skill quality.
Select the top {budget} most important edits.

## Current Skill
{current_skill}

## Candidate Edits
{edits}

## Instructions
1. Prioritize edits that address specific failure patterns over vague improvements.
2. Avoid duplicate edits.
3. Consider whether the edit contradicts existing rules.

## Output
Return a JSON array of the selected edits with their original index, support count, and importance score (0-1): [{{"index": 1, "support": 3, "score": 0.9}}]"""
