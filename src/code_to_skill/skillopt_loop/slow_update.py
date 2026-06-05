"""Epoch 级 Slow Update。

对齐 external/SkillOpt skillopt/optimizer/slow_update.py。

Slow update 是跨 epoch 的纵向优化：
1. 取上一 epoch 最后 Skill 和当前 epoch 最后 Skill
2. 从 train split 抽样同一批任务
3. 分别用两个 Skill rollout → 构建 comparison pairs
4. Optimizer 分析 improved / regressed / persistent_fail / stable_success
5. 产出 slow_update_content 写入受保护区域 <!-- SLOW_UPDATE_START -->...<!-- SLOW_UPDATE_END -->

论文消融实验：去掉 slow update 后 SpreadsheetBench 从 77.5 掉到 55.0（-22.5 分）。
"""
from __future__ import annotations

import logging
from typing import Any

from .skill_ops import SLOW_UPDATE_START, SLOW_UPDATE_END

logger = logging.getLogger(__name__)


def run_slow_update(
    prev_skill: str,
    curr_skill: str,
    sampled_items: list[dict],
    adapter: Any = None,
    optimizer_backend: Any = None,
    evaluate_fn: Any = None,
) -> dict:
    """执行 epoch 级 Slow Update。

    Args:
        prev_skill: 上一 epoch 最后的 Skill（S_{e-1}）
        curr_skill: 当前 epoch 最后的 Skill（S_e）
        sampled_items: 从 train split 抽样的任务列表（默认 20 条）
        adapter: EnvAdapter 实例（用于 rollout）
        optimizer_backend: optimizer 后端
        evaluate_fn: 评分函数 (skill, items) → float

    Returns:
        {
            "slow_update_content": str,  # 写入保护区域的内容
            "comparison_pairs": dict,    # improved/regressed/persistent_fail/stable_success
            "action": str,               # 是否建议应用
        }
    """
    if not sampled_items:
        logger.info("[SlowUpdate] No sampled items, skipping")
        return {"slow_update_content": "", "comparison_pairs": {}, "action": "skip"}

    if not adapter:
        logger.info("[SlowUpdate] No adapter available, using rule-based comparison")
        return _rule_based_slow_update(prev_skill, curr_skill, sampled_items)

    # Step 1: Double rollout — 同一批任务分别用两个 Skill
    logger.info("[SlowUpdate] Evaluating %d items with prev_skill and curr_skill", len(sampled_items))
    prev_results = adapter.rollout(prev_skill, sampled_items)
    curr_results = adapter.rollout(curr_skill, sampled_items)

    # Step 2: Build comparison pairs
    pairs = _build_comparison_pairs(prev_results, curr_results)
    logger.info(
        "[SlowUpdate] Comparison: improved=%d regressed=%d persistent_fail=%d stable_success=%d",
        len(pairs["improved"]), len(pairs["regressed"]),
        len(pairs["persistent_fail"]), len(pairs["stable_success"]),
    )

    # Step 3: Optimizer analysis (LLM if available)
    if optimizer_backend:
        try:
            return _llm_slow_update(
                pairs, prev_skill, curr_skill, optimizer_backend
            )
        except Exception as e:
            logger.warning("LLM slow update failed, using rule-based: %s", e)

    return _rule_based_slow_update(prev_skill, curr_skill, sampled_items)


# ── 应用 Slow Update 到 Skill ──────────────────────────────

def apply_slow_update(skill: str, slow_update_content: str) -> str:
    """将 slow update 内容写入 Skill 的受保护区域。

    如果已有保护区域，替换其内容；否则在末尾创建。
    """
    start = SLOW_UPDATE_START
    end = SLOW_UPDATE_END

    if start in skill and end in skill:
        # 替换已有区域
        before = skill[:skill.index(start) + len(start)]
        after = skill[skill.index(end):]
        return before + "\n" + slow_update_content.strip() + "\n" + after
    else:
        # 创建新区域
        marker = f"\n\n{start}\n{slow_update_content.strip()}\n{end}\n"
        return skill.rstrip() + marker


# ── Comparison Pairs 构建 ──────────────────────────────────

def _build_comparison_pairs(
    prev_results: list[dict],
    curr_results: list[dict],
) -> dict[str, list[dict]]:
    """对 prev/curr rollout 结果逐一对比，生成 comparison pairs。"""
    pairs: dict[str, list[dict]] = {
        "improved": [],
        "regressed": [],
        "persistent_fail": [],
        "stable_success": [],
    }

    # 按 id 匹配
    curr_map = {r["id"]: r for r in curr_results}
    for pr in prev_results:
        cr = curr_map.get(pr["id"])
        if cr is None:
            continue

        prev_hard = pr.get("hard", 0)
        curr_hard = cr.get("hard", 0)

        if prev_hard == 0 and curr_hard == 1:
            pairs["improved"].append({"id": pr["id"], "prev": pr, "curr": cr})
        elif prev_hard == 1 and curr_hard == 0:
            pairs["regressed"].append({"id": pr["id"], "prev": pr, "curr": cr})
        elif prev_hard == 0 and curr_hard == 0:
            pairs["persistent_fail"].append({"id": pr["id"], "prev": pr, "curr": cr})
        else:
            pairs["stable_success"].append({"id": pr["id"], "prev": pr, "curr": cr})

    return pairs


# ── LLM Slow Update ─────────────────────────────────────────

def _llm_slow_update(
    pairs: dict,
    prev_skill: str,
    curr_skill: str,
    backend: Any,
) -> dict:
    """用 optimizer LLM 分析 comparison pairs，输出 longitudinal guidance。"""
    from code_to_skill.model_gateway.types import InteractionRequest

    summary = _format_pairs_for_prompt(pairs)

    resp = backend.invoke(InteractionRequest(
        role="optimizer",
        stage="slow_update",
        messages=[{
            "role": "system",
            "content": _SLOW_UPDATE_PROMPT.format(
                prev_skill=prev_skill[:1000],
                curr_skill=curr_skill[:1000],
                pairs_summary=summary[:1500],
            ),
        }],
        max_output_tokens=512,
        temperature=0.2,
    ))

    content = resp.content.strip()
    return {
        "slow_update_content": content,
        "comparison_pairs": {
            "improved": len(pairs["improved"]),
            "regressed": len(pairs["regressed"]),
            "persistent_fail": len(pairs["persistent_fail"]),
            "stable_success": len(pairs["stable_success"]),
        },
        "action": "suggested",
    }


def _rule_based_slow_update(
    prev_skill: str,
    curr_skill: str,
    sampled_items: list[dict],
) -> dict:
    """规则降级：当 LLM 不可用时，按简单变化做 slow update。"""
    # 简单规则：如果两个 Skill 相同，不做 slow update
    if prev_skill.strip() == curr_skill.strip():
        return {"slow_update_content": "", "comparison_pairs": {}, "action": "skip_no_change"}

    # 否则记录 epoch 变化摘要
    content = (
        f"## Epoch Transition Summary\n"
        f"Skill evolved from {len(prev_skill)} → {len(curr_skill)} chars.\n"
        f"Evaluated on {len(sampled_items)} sampled tasks.\n"
        f"Review the differences for regression and improvement patterns."
    )
    return {
        "slow_update_content": content,
        "comparison_pairs": {},
        "action": "rule_based",
    }


def _format_pairs_for_prompt(pairs: dict) -> str:
    """将 comparison pairs 格式化为 prompt 文本。"""
    lines = []
    if pairs["improved"]:
        lines.append(f"## Improved ({len(pairs['improved'])} tasks)")
        for p in pairs["improved"][:5]:
            lines.append(f"- {p['id']}: FAIL → PASS")

    if pairs["regressed"]:
        lines.append(f"## Regressed ({len(pairs['regressed'])} tasks)")
        for p in pairs["regressed"][:5]:
            lines.append(f"- {p['id']}: PASS → FAIL")

    if pairs["persistent_fail"]:
        lines.append(f"## Persistent Failures ({len(pairs['persistent_fail'])} tasks)")
        for p in pairs["persistent_fail"][:5]:
            lines.append(f"- {p['id']}: still FAIL")

    if pairs["stable_success"]:
        lines.append(f"## Stable Success ({len(pairs['stable_success'])} tasks)")
        lines.append(f"  {len(pairs['stable_success'])} tasks consistently passed")

    return "\n".join(lines) if lines else "(no comparison data)"


_SLOW_UPDATE_PROMPT = """## Task
Analyze how the skill changed between two epochs and write a concise longitudinal guidance block.

## Previous Skill (end of last epoch)
{prev_skill}

## Current Skill (end of this epoch)
{curr_skill}

## Comparison on Sampled Tasks
{pairs_summary}

## Instructions
1. Identify what improved: which rules or patterns caused the improvements?
2. Identify what regressed: which changes may have harmed performance?
3. Write 2-5 concise bullet points of longitudinal guidance.
4. Focus on durable domain lessons, not batch-specific fixes.
5. Keep the output under 300 tokens. Markdown format is OK.

## Output
Write the guidance block directly (not JSON — just the markdown text)."""