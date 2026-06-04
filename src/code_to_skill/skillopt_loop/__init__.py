"""SkillOpt 优化循环（模块 4）。

主训练循环：rollout → reflect → aggregate → select → update → evaluate
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re

from .types import (
    BenchmarkItem, RolloutResult, RawPatch, EditOp,
    MergedPatch, RankedEdit, CandidateSkill,
    StepRecord, HistoryEntry,
)

logger = logging.getLogger(__name__)


# ── Scorer ──────────────────────────────────────────────────

def score_rollout_result(predicted: str, expected_checks: list[str]) -> dict:
    """确定性 scorer：基于 keyword/regex 检查。"""
    passed = 0
    for check in expected_checks:
        if _check_keyword(predicted, check):
            passed += 1

    total = len(expected_checks) if expected_checks else 1
    soft = passed / total
    hard = 1 if soft == 1.0 else 0

    return {"hard": hard, "soft": round(soft, 3), "passed": passed, "total": total}


def _check_keyword(text: str, check: str) -> bool:
    """检查文本中是否包含预期关键词。"""
    lower = text.lower()
    return check.lower() in lower


# ── Updater ──────────────────────────────────────────────────

def apply_edits(skill_content: str, edits: list[EditOp]) -> str:
    """将编辑应用到 Skill 文档。"""
    lines = skill_content.split("\n")

    for edit in edits:
        if edit.op == "append":
            lines.append(edit.content)
        elif edit.op == "replace":
            new_lines = []
            for line in lines:
                if edit.target in line and edit.target:
                    new_lines.append(edit.content)
                else:
                    new_lines.append(line)
            lines = new_lines
        elif edit.op == "delete":
            lines = [l for l in lines if edit.target not in l or not edit.target]
        elif edit.op == "insert_after":
            new_lines = []
            for line in lines:
                new_lines.append(line)
                if edit.target in line and edit.target:
                    new_lines.append(edit.content)
            lines = new_lines

    return "\n".join(lines)


def compute_semantic_hash(content: str) -> str:
    """计算语义 hash（空白归一化后 SHA256）。"""
    normalized = re.sub(r"\s+", " ", content).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


# ── State Manager ────────────────────────────────────────────

def save_runtime_state(output_dir: str, step: int, current_score: float,
                       best_score: float, best_step: int, current_skill: str = ""):
    """保存断点续训状态。"""
    state = {
        "schema_version": "1.0",
        "last_completed_step": step,
        "current_score": current_score,
        "best_score": best_score,
        "best_step": best_step,
        "current_skill_path": current_skill,
        "step_internal": None,
    }
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "runtime_state.json"), "w") as f:
        json.dump(state, f, indent=2)


# ── Training Loop ────────────────────────────────────────────

def run_skillopt_loop(
    initial_skill: str,
    benchmark_items: list[dict],
    output_dir: str,
    num_epochs: int = 3,
    batch_size: int = 20,
    edit_budget: int = 3,
    selection_split_ratio: float = 0.3,
    use_llm_rollout: bool = False,
) -> dict:
    """运行 SkillOpt 优化循环（简化版）。

    当前实现：用确定性 scorer 做 rollout/evaluate，
    用基于规则的 patch 生成替代 LLM reflect。
    完整版需接入 M5 backend。

    Returns:
        {"best_skill": str, "history": list, "best_score": float}
    """
    os.makedirs(output_dir, exist_ok=True)

    # Split: train / selection
    split_idx = int(len(benchmark_items) * (1 - selection_split_ratio))
    train_items = benchmark_items[:split_idx]
    selection_items = benchmark_items[split_idx:]

    current_skill = initial_skill
    best_skill = initial_skill
    current_score = 0.0
    best_score = 0.0
    best_step = 0
    history: list[dict] = []

    # Initial evaluation
    logger.info("[M4] 开始训练: skill=%d chars, train=%d items, selection=%d items, epochs=%d, batch=%d",
                len(initial_skill), len(train_items), len(selection_items), num_epochs, batch_size)
    if selection_items:
        current_score = _evaluate_skill(current_skill, selection_items, use_llm=use_llm_rollout)
        best_score = current_score
        logger.info("[M4] 初始评分: %.3f", current_score)

    step_counter = 0

    for epoch in range(num_epochs):
        logger.info("[M4] === Epoch %d/%d ===", epoch + 1, num_epochs)
        for batch_start in range(0, len(train_items), batch_size):
            batch = train_items[batch_start:batch_start + batch_size]
            step_counter += 1

            # 1. Rollout
            results = _run_rollout(current_skill, batch, use_llm=use_llm_rollout)
            rollout_avg = sum(r["soft"] for r in results) / max(len(results), 1)
            passed = sum(1 for r in results if r["hard"] == 1)
            failed = sum(1 for r in results if r["hard"] == 0)
            logger.info("[M4] step=%d batch=%d/%d | rollout: avg=%.2f passed=%d failed=%d",
                         step_counter, len(batch), len(train_items), rollout_avg, passed, failed)
            for r in results:
                if r["hard"] == 0:
                    logger.info("  ✗ %s: soft=%.2f reason=%s", r["id"], r["soft"], r.get("fail_reason", "")[:60])

            # 2. Reflect（优先 LLM，降级规则）
            from .llm_components import reflect_llm
            patches = reflect_llm(results, current_skill)
            logger.info("[M4] reflect: %d patches", len(patches))

            # 3. Aggregate
            merged = _merge_patches(patches)
            logger.info("[M4] aggregate: %d edits", len(merged.edits))

            # 4. Select（优先 LLM，降级规则）
            from .llm_components import select_edits_llm
            ranked_dicts = select_edits_llm(merged.edits, current_skill, edit_budget)
            ranked = [RankedEdit(**r) if isinstance(r, dict) else r for r in ranked_dicts]
            for i, r in enumerate(ranked):
                logger.info("[M4] select #%d: [%s] %s", i + 1, r.edit.op, r.edit.content[:60])

            # 5. Update
            candidate_content = apply_edits(current_skill, [e.edit for e in ranked])
            candidate_hash = compute_semantic_hash(candidate_content)
            size_delta = len(candidate_content) - len(current_skill)
            logger.info("[M4] update: hash=%s delta=%+d chars", candidate_hash[:8], size_delta)

            # 6. Evaluate
            candidate_score = _evaluate_skill(candidate_content, selection_items, use_llm=use_llm_rollout)
            logger.info("[M4] evaluate: selection_score=%.3f (current=%.3f, best=%.3f)",
                         candidate_score, current_score, best_score)

            # Gate
            action = "reject"
            if candidate_score > best_score:
                action = "accept_new_best"
                best_score = candidate_score
                best_skill = candidate_content
                best_step = step_counter
                logger.info("[M4] gate: ⭐ NEW BEST score=%.3f step=%d", best_score, step_counter)
            elif candidate_score > current_score:
                action = "accept"
                logger.info("[M4] gate: ✓ accepted score=%.3f", candidate_score)
            else:
                logger.info("[M4] gate: ✗ rejected score=%.3f (≤ current=%.3f)", candidate_score, current_score)

            current_score = candidate_score if action != "reject" else current_score
            if action != "reject":
                current_skill = candidate_content

            record = {
                "step": step_counter,
                "epoch": epoch + 1,
                "rollout_score": round(rollout_avg, 3),
                "selection_score": round(candidate_score, 3),
                "gate_action": action,
                "best_score": round(best_score, 3),
                "edit_count": len(ranked),
            }
            history.append(record)

        # Epoch end: save state
        save_runtime_state(output_dir, step_counter, current_score, best_score, best_step)

    # Final
    final = {"best_skill": best_skill, "history": history, "best_score": best_score}

    with open(os.path.join(output_dir, "best_skill.md"), "w") as f:
        f.write(best_skill)
    with open(os.path.join(output_dir, "history.json"), "w") as f:
        json.dump(history, f, indent=2)

    print(f"[M4] SkillOpt 完成: {step_counter} steps, best_score={best_score:.3f}")
    return final


# ── 内部函数 ─────────────────────────────────────────────────

def _run_rollout(skill: str, items: list[dict], use_llm: bool = False) -> list[dict]:
    """Rollout：用 skill 对 benchmark items 生成回答并评分。

    Args:
        skill: 当前 Skill 内容
        items: benchmark item 列表
        use_llm: 是否使用 LLM 生成回答（默认 False 用规则模拟）

    当 use_llm=True 且 LLM 可用时，调用 DeepSeek API 实际回答；
    否则用规则模拟（skill 关键词匹配）。
    """
    results = []

    # 尝试 LLM rollout
    backend = None
    if use_llm:
        try:
            from code_to_skill.model_gateway.llm_backend import is_llm_available, create_llm_backend
            if is_llm_available():
                backend = create_llm_backend()
        except Exception:
            pass

    for item in items:
        checks = item.get("expected_checks", [])
        question = item.get("task_template", item.get("question", ""))

        if backend:
            # 真实 LLM rollout
            from code_to_skill.model_gateway.types import InteractionRequest
            try:
                resp = backend.invoke(InteractionRequest(
                    role="target",
                    stage="rollout",
                    messages=[
                        {"role": "system", "content": f"You are an expert code reviewer. Use this skill:\n\n{skill[:2000]}"},
                        {"role": "user", "content": question[:1000]},
                    ],
                    max_output_tokens=512,
                    temperature=0.3,
                ))
                predicted = resp.content
                fail_reason = ""
            except Exception as e:
                predicted = f"[LLM error: {e}]"
                fail_reason = str(e)[:100]
        else:
            # 规则模拟（当 LLM 不可用或未启用）
            skill_lines = skill.split("\n")
            relevant_lines = []
            for line in skill_lines:
                line_lower = line.lower()
                if any(c.lower() in line_lower for c in checks):
                    relevant_lines.append(line)
            relevant_text = "\n".join(relevant_lines[:10]) if relevant_lines else skill[:300]
            predicted = f"基于以下规则分析：\n{relevant_text}\n\n检查项: {', '.join(checks)}"
            fail_reason = ""

        scores = score_rollout_result(predicted, checks)
        results.append({
            "id": item.get("id", ""),
            "hard": scores["hard"],
            "soft": scores["soft"],
            "predicted_answer": predicted,
            "fail_reason": fail_reason or ("check_missed" if scores["hard"] == 0 else ""),
            "task_type": item.get("task_type", ""),
        })
    return results


def _evaluate_skill(skill: str, items: list[dict], use_llm: bool = False) -> float:
    """在 selection split 上评估 Skill。"""
    if not items:
        return 0.0
    results = _run_rollout(skill, items, use_llm=use_llm)
    return sum(r["soft"] for r in results) / len(results)


def _generate_patches(results: list[dict], skill: str) -> list[dict]:
    """基于规则的 patch 生成（替代 LLM reflect）。"""
    patches = []
    failed = [r for r in results if r["hard"] == 0]
    if failed:
        patches.append({
            "source_type": "failure",
            "batch_size": len(failed),
            "failure_summary": [{"type": "check_missed", "count": len(failed)}],
            "edits": [{"op": "append", "content": "# TODO: 改进规则以覆盖失败场景", "target": "", "source_type": "failure"}],
        })
    return patches


def _merge_patches(patches: list[dict]) -> MergedPatch:
    """简单合并 patches。"""
    edits = []
    for p in patches:
        for e in p.get("edits", []):
            edits.append(EditOp(**e) if isinstance(e, dict) else e)
    return MergedPatch(edits=edits)


def _select_edits(edits: list[EditOp], budget: int) -> list[RankedEdit]:
    """按 budget 截断。"""
    ranked = []
    for i, e in enumerate(edits[:budget]):
        ranked.append(RankedEdit(edit=e, rank=i + 1, support_count=1, score=1.0))
    return ranked
