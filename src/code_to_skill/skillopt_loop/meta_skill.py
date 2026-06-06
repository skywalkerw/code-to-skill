"""Meta Skill：Optimizer 侧跨 epoch 记忆。

对齐 external/SkillOpt skillopt/optimizer/meta_skill.py。

Meta skill 只存在于 optimizer 侧，不进入部署的 Skill。
它在每次 Reflect/Aggregate/Select 时作为前置 context 注入 optimizer prompt，
帮助 optimizer 记住哪些编辑方向有效、哪些被拒绝过、哪些失败反复出现。
"""
from __future__ import annotations

import logging
from typing import Any

from .token_budgets import get_token_budgets

logger = logging.getLogger(__name__)


class MetaSkill:
    """Optimizer 侧跨 epoch 记忆管理器。

    Usage:
        meta = MetaSkill()
        # 每次 epoch 结束时更新
        meta.update(
            prev_skill=..., curr_skill=...,
            accepted_edits=[...], rejected_edits=[...],
            comparison_pairs={...},
        )
        # Reflect 时注入 prompt
        meta_context = meta.render()  # → str
    """

    def __init__(self):
        self._content: str = ""
        self._epoch_history: list[dict] = []

    @property
    def content(self) -> str:
        return self._content

    @property
    def is_empty(self) -> bool:
        return not self._content.strip()

    def update(
        self,
        prev_skill: str = "",
        curr_skill: str = "",
        accepted_edits: list | None = None,
        rejected_edits: list | None = None,
        comparison_pairs: dict | None = None,
        optimizer_backend: Any = None,
    ) -> None:
        """用本 epoch 经验更新 meta skill。

        Args:
            prev_skill: 上一 epoch 最后 Skill
            curr_skill: 当前 epoch 最后 Skill
            accepted_edits: 本 epoch 中 accepted 的 EditOp 列表
            rejected_edits: 本 epoch 中 rejected 的 EditOp 列表
            comparison_pairs: slow update 产生的 comparison pairs
            optimizer_backend: 用于 LLM 生成 meta skill（可选）
        """
        entry = {
            "accepted_count": len(accepted_edits or []),
            "rejected_count": len(rejected_edits or []),
            "improved": (comparison_pairs or {}).get("improved", 0),
            "regressed": (comparison_pairs or {}).get("regressed", 0),
        }
        self._epoch_history.append(entry)

        if optimizer_backend:
            try:
                self._content = _llm_generate_meta_skill(
                    optimizer_backend,
                    prev_skill, curr_skill,
                    accepted_edits, rejected_edits,
                    comparison_pairs,
                    self._content,
                )
                logger.info("[MetaSkill] Updated via LLM: %d chars", len(self._content))
                return
            except Exception as e:
                logger.warning("LLM meta skill generation failed: %s", e)

        # 降级：规则生成
        self._content = _rule_based_meta_skill(
            accepted_edits, rejected_edits, comparison_pairs
        )
        logger.info("[MetaSkill] Updated via rules: %d chars", len(self._content))

    def render(self) -> str:
        """渲染为可注入 optimizer prompt 的上下文字符串。"""
        if not self._content.strip():
            return "(no meta guidance yet — this is the first epoch)"

        return (
            "## Optimizer Meta Guidance (from previous epochs)\n"
            f"{self._content}\n"
            "---\n"
        )

    def clear(self) -> None:
        self._content = ""
        self._epoch_history.clear()


def _rule_based_meta_skill(
    accepted_edits: list | None,
    rejected_edits: list | None,
    comparison_pairs: dict | None,
) -> str:
    """规则生成 meta skill（LLM 不可用时的降级）。"""
    lines: list[str] = []

    if accepted_edits:
        lines.append(f"Previously ACCEPTED {len(accepted_edits)} edits — preserve these patterns:")
        for e in accepted_edits[-5:]:
            op = getattr(e, "op", "?")
            content = (getattr(e, "content", "") or "")[:80]
            lines.append(f"  - [{op}] {content}")

    if rejected_edits:
        lines.append(f"\nPreviously REJECTED {len(rejected_edits)} edits — avoid these patterns:")
        for e in rejected_edits[-5:]:
            op = getattr(e, "op", "?")
            content = (getattr(e, "content", "") or "")[:80]
            lines.append(f"  - [{op}] {content}")

    if comparison_pairs:
        improved = comparison_pairs.get("improved", 0)
        regressed = comparison_pairs.get("regressed", 0)
        if improved or regressed:
            lines.append(f"\nEpoch transition: +{improved} improved, -{regressed} regressed tasks.")

    if not lines:
        return "No significant patterns observed in this epoch."

    return "\n".join(lines)


def _llm_generate_meta_skill(
    backend: Any,
    prev_skill: str,
    curr_skill: str,
    accepted_edits: list | None,
    rejected_edits: list | None,
    comparison_pairs: dict | None,
    current_meta: str,
) -> str:
    """用 LLM 生成 meta skill。"""
    from code_to_skill.model_provider.types import InteractionRequest

    accepted_summary = _format_edits(accepted_edits or [])
    rejected_summary = _format_edits(rejected_edits or [])

    resp = backend.invoke(InteractionRequest(
        role="optimizer",
        stage="meta_skill",
        messages=[{
            "role": "system",
            "content": _META_SKILL_PROMPT.format(
                current_meta=current_meta or "(none — first update)",
                prev_skill=prev_skill[:800],
                curr_skill=curr_skill[:800],
                accepted_edits=accepted_summary[:1000],
                rejected_edits=rejected_summary[:1000],
                pairs_summary=str(comparison_pairs or {})[:500],
            ),
        }],
        max_output_tokens=get_token_budgets().meta_skill,
        temperature=0.2,
    ))

    return resp.content.strip()


def _format_edits(edits: list) -> str:
    lines = []
    for i, e in enumerate(edits[-10:]):  # 最近 10 条
        op = getattr(e, "op", "?")
        content = (getattr(e, "content", "") or "")[:80]
        lines.append(f"  {i+1}. [{op}] {content}")
    return "\n".join(lines) if lines else "(none)"


_META_SKILL_PROMPT = """## Task
Summarize cross-epoch editing patterns into concise guidance for the optimizer.

## Current Meta Guidance
{current_meta}

## Previous Skill
{prev_skill}

## Current Skill
{curr_skill}

## Accepted Edits (this epoch)
{accepted_edits}

## Rejected Edits (this epoch)
{rejected_edits}

## Epoch Comparison
{pairs_summary}

## Instructions
1. Identify which editing DIRECTIONS worked well (e.g., "adding verification rules").
2. Identify which editing DIRECTIONS did NOT work (e.g., "overly specific examples").
3. Write 3-5 concise bullet points of guidance for the optimizer in future epochs.
4. Focus on patterns and directions, not specific edits.
5. Keep output under 250 tokens.

## Output
Write the meta guidance directly (markdown bullet points, not JSON)."""