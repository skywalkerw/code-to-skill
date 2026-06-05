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
    EditOp,
    RankedEdit,
)
from .skill_ops import apply_edits as apply_edits_with_report
from .gate import GateManager
from .scheduler import EditBudgetScheduler
from .step_buffer import StepBufferManager

logger = logging.getLogger(__name__)


from .scoring import score_rollout_result  # re-export for backward compat

# ── Updater helper ──────────────────────────────────────────

def apply_edits(skill_content: str, edits: list[EditOp]) -> str:
    """将编辑应用到 Skill 文档（向后兼容，返回 str）。

    内部使用 skill_ops.apply_edits 的完整报告版本。
    """
    result, _ = apply_edits_with_report(skill_content, edits)
    return result

def _gate_icon(action: str) -> str:
    """门禁动作图标。"""
    return {"accept_new_best": "⭐", "accept": "✓", "reject": "✗"}.get(action, "?")


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
    budget_strategy: str = "constant",
    patience: int = 10,
    gate_metric: str = "hard",
    accumulation: int = 1,
    enable_slow_update: bool = False,
    enable_meta_skill: bool = False,
    test_split_ratio: float = 0.0,
    adapter: Any = None,
) -> dict:
    """运行 SkillOpt 优化循环（完整版）。

    对齐 external/SkillOpt engine/trainer.py 的完整训练流程：
    - BackendManager: optimizer/target 分离
    - EnvAdapter: benchmark 抽象
    - Accumulator: 多批累积
    - Gradient/merge_patches: 分层合并
    - reflect_llm + step_buffer: buffer→reflect 闭环
    - SelectionCache: 语义 hash 缓存
    - Slow update + Meta skill: epoch 级纵向优化
    - Test evaluation: 最终 test split 报告

    Returns:
        {"best_skill": str, "history": list, "best_score": float}
    """
    os.makedirs(output_dir, exist_ok=True)

    # ── Backend 初始化 ─────────────────────────────────────
    from .separation import BackendManager, Accumulator
    backend_mgr = BackendManager.from_env(use_llm=use_llm_rollout)

    # ── Adapter 初始化 ─────────────────────────────────────
    from .envs import DEFAULTAdapter
    if adapter is None:
        adapter = DEFAULTAdapter(use_llm=use_llm_rollout)
    adapter.setup()

    # ── Split: train / selection / test ─────────────────────
    total = len(benchmark_items)
    sel_ratio = selection_split_ratio
    test_ratio = test_split_ratio
    train_ratio = 1.0 - sel_ratio - test_ratio

    sel_start = int(total * train_ratio)
    test_start = int(total * (train_ratio + sel_ratio))

    train_items = benchmark_items[:sel_start]
    selection_items = benchmark_items[sel_start:test_start]
    test_items = benchmark_items[test_start:] if test_ratio > 0 else []

    current_skill = initial_skill
    prev_epoch_skill = initial_skill  # for slow update
    best_skill = initial_skill
    current_hard = 0.0
    current_soft = 0.0
    best_score = 0.0
    best_step = 0
    history: list[dict] = []

    # ── 组件初始化 ─────────────────────────────────────────
    total_steps = num_epochs * max(1, len(train_items) // max(1, batch_size))
    gate = GateManager(patience=patience, metric=gate_metric)  # type: ignore[arg-type]
    scheduler = EditBudgetScheduler(
        initial_budget=edit_budget, min_budget=1,
        total_steps=total_steps, strategy=budget_strategy,
    )
    buffer = StepBufferManager()
    from .cache import SelectionCache
    cache = SelectionCache(
        cache_path=os.path.join(output_dir, "cache", "selection_scores.json")
    )
    from .meta_skill import MetaSkill
    meta_skill = MetaSkill()
    acc = Accumulator(accumulate=accumulation)

    # ── Initial evaluation ──────────────────────────────────
    logger.info("[M4] 开始训练: skill=%d chars, train=%d items, selection=%d items, epochs=%d, batch=%d acc=%d",
                len(initial_skill), len(train_items), len(selection_items),
                num_epochs, batch_size, accumulation)
    if selection_items:
        eval_result = adapter.evaluate(current_skill, selection_items, target_backend=backend_mgr.target)
        current_hard = eval_result["accuracy"]
        current_soft = eval_result["soft"]
        best_score = gate.evaluate(current_hard, current_soft, 0.0, 0.0).candidate_score
        # 不需要实际 gate，只是用 gate metric 投影初始分

        logger.info("[M4] 初始评分: hard=%.3f soft=%.3f acc=%.3f f1=%.3f (best=%.3f [%s])",
                     current_hard, current_soft, eval_result["accuracy"], eval_result["f1"],
                     best_score, gate.metric)

    step_counter = 0

    for epoch in range(num_epochs):
        logger.info("[M4] === Epoch %d/%d ===", epoch + 1, num_epochs)
        for batch_start in range(0, len(train_items), batch_size):
            batch = train_items[batch_start:batch_start + batch_size]
            step_counter += 1

            # 1. Rollout（通过 adapter）
            results = adapter.rollout(current_skill, batch, target_backend=backend_mgr.target)
            acc.add_batch(results)

            # Accumulation：未积累够则继续下一批
            if not acc.ready:
                logger.info("[M4] step=%d batch=%d/%d | accumulated %d/%d",
                             step_counter, len(batch), len(train_items),
                             acc.pending_count, len(batch) * accumulation)
                continue

            all_results = acc.consume()
            rollout_avg = sum(r["soft"] for r in all_results) / max(len(all_results), 1)
            passed = sum(1 for r in all_results if r["hard"] == 1)
            failed = sum(1 for r in all_results if r["hard"] == 0)
            logger.info("[M4] step=%d rollout: avg=%.2f passed=%d failed=%d (from %d accumulated batches)",
                         step_counter, rollout_avg, passed, failed, accumulation)
            for r in all_results:
                if r["hard"] == 0:
                    logger.info("  ✗ %s: soft=%.2f reason=%s", r["id"], r["soft"], r.get("fail_reason", "")[:60])

            # 2. Reflect（通过 adapter prompt + buffer→reflect 闭环）
            from .llm_components import reflect_llm
            rejected_edits = buffer.get_rejected_edits()
            # 构建 step_buffer（从 buffer 中提取该 step 已记录的失败模式）
            step_buf_entries = buffer.get_rejected_edits()  # rejected edits 作为 step buffer 的廉价近似
            step_buffer = [{"type": "rejected_edit", "edit": e} for e in step_buf_entries] if step_buf_entries else None
            patches = reflect_llm(
                all_results, current_skill,
                rejected_edits=rejected_edits,
                step_buffer=step_buffer,
                meta_skill_context=meta_skill.render() if enable_meta_skill else "",
            )
            logger.info("[M4] reflect: %d patches", len(patches))

            # 3. Aggregate（分层 merge）
            from .gradient import merge_patches
            failure_patches = [p for p in patches if p.get("source_type") == "failure"]
            success_patches = [p for p in patches if p.get("source_type") == "success"]
            merged = merge_patches(
                failure_patches, success_patches,
                current_skill=current_skill,
                optimizer_backend=backend_mgr.optimizer,
            )
            logger.info("[M4] aggregate: %d edits", len(merged.edits))

            # 4. Select（LLM 排序 + budget 截断）
            from .llm_components import select_edits_llm
            step_budget = scheduler.step()
            ranked_dicts = select_edits_llm(merged.edits, current_skill, step_budget)
            ranked = [RankedEdit(**r) if isinstance(r, dict) else r for r in ranked_dicts]
            for i, r in enumerate(ranked):
                if buffer.is_edit_redundant(r.edit):
                    logger.info("[M4] select #%d: SKIP (redundant) [%s] %s", i + 1, r.edit.op, r.edit.content[:40])
                    continue
                logger.info("[M4] select #%d: [%s] %s", i + 1, r.edit.op, r.edit.content[:60])

            # 5. Update
            candidate_content, edit_reports = apply_edits_with_report(current_skill, [e.edit for e in ranked])
            candidate_hash = compute_semantic_hash(candidate_content)
            size_delta = len(candidate_content) - len(current_skill)
            logger.info("[M4] update: hash=%s delta=%+d chars", candidate_hash[:8], size_delta)

            # 6. Evaluate（selection cache）
            cached = cache.get(candidate_hash)
            if cached is not None:
                candidate_score = cached["gate_score"]
                logger.info("[M4] evaluate: CACHED soft=%.3f (epoch=%d step=%d)",
                             candidate_score, cached.get("epoch", 0), cached.get("step", 0))
            else:
                eval_result = adapter.evaluate(candidate_content, selection_items, target_backend=backend_mgr.target)
                candidate_score = eval_result["soft"]
                cache.put(candidate_hash, eval_result.get("accuracy", 0.0),
                          candidate_score, candidate_score, epoch + 1, step_counter)
                logger.info("[M4] evaluate: soft=%.3f acc=%.3f f1=%.3f (current=%.3f, best=%.3f)",
                             eval_result["soft"], eval_result.get("accuracy", 0.0),
                             eval_result.get("f1", 0.0), current_score, best_score)

            # Gate
            decision = gate.evaluate(candidate_score, best_score, current_score)
            action = decision.action
            logger.info("[M4] gate: %s reason=%s", _gate_icon(action), decision.reason)

            if action == "accept_new_best":
                best_score = candidate_score
                best_skill = candidate_content
                best_step = step_counter
                for r in ranked:
                    buffer.record_accepted_edit(step_counter, r.edit)
            elif action == "accept":
                for r in ranked:
                    buffer.record_accepted_edit(step_counter, r.edit)
            else:
                for r in ranked:
                    buffer.record_rejected_edit(step_counter, r.edit)

            current_score = candidate_score if action != "reject" else current_score
            if action != "reject":
                current_skill = candidate_content

            # 记录 rollout 中的失败/成功任务
            for r in all_results:
                if r["hard"] == 0:
                    buffer.record_failure(step_counter, r["id"])
                else:
                    buffer.record_success(step_counter, r["id"])

            # 早停检查
            if gate.should_early_stop:
                logger.info("[M4] 早停: %d 次连续 reject", gate._consecutive_rejects)
                break

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

            if gate.should_early_stop:
                break

        # ── Epoch End ───────────────────────────────────────
        # Flush remaining accumulated results
        remaining = acc.flush_remaining()
        if remaining:
            logger.info("[M4] Flushed %d remaining accumulated results at epoch end", len(remaining))

        # Slow Update（epoch >= 2 时启用）
        if enable_slow_update and epoch >= 1:
            logger.info("[M4] === Slow Update epoch %d ===", epoch + 1)
            from .slow_update import run_slow_update, apply_slow_update
            # 从 train 抽 20 条做 comparison
            import random
            samples = random.sample(train_items, min(20, len(train_items)))
            slow_result = run_slow_update(
                prev_epoch_skill, current_skill, samples,
                adapter=adapter,
                optimizer_backend=backend_mgr.optimizer,
            )
            if slow_result["slow_update_content"]:
                current_skill = apply_slow_update(current_skill, slow_result["slow_update_content"])
                best_skill = apply_slow_update(best_skill, slow_result["slow_update_content"])
                logger.info("[M4] Slow update applied: %d chars", len(slow_result["slow_update_content"]))

            # Meta Skill
            if enable_meta_skill:
                logger.info("[M4] === Meta Skill epoch %d ===", epoch + 1)
                meta_skill.update(
                    prev_skill=prev_epoch_skill,
                    curr_skill=current_skill,
                    accepted_edits=buffer.get_accepted_edits(),
                    rejected_edits=rejected_edits,
                    comparison_pairs=slow_result.get("comparison_pairs", {}),
                    optimizer_backend=backend_mgr.optimizer,
                )

        # Save prev_epoch_skill for next epoch's slow update
        prev_epoch_skill = current_skill

        # Epoch-level early stop
        if gate.should_early_stop:
            logger.info("[M4] 早停在 epoch %d/%d", epoch + 1, num_epochs)
            break

        # Epoch end: save state
        save_runtime_state(output_dir, step_counter, current_score, best_score, best_step)
        cache.save()

    # ── Final ───────────────────────────────────────────────
    # Test eval
    test_report = {}
    if test_items:
        from .test_eval import test_evaluate
        test_report = test_evaluate(best_skill, test_items, adapter=adapter,
                                    output_dir=os.path.join(output_dir, "final_eval"))
        logger.info("[M4] Test eval: score=%.3f hard=%.3f n=%d",
                     test_report["test_score"], test_report["test_hard"], test_report["n_items"])

    final = {
        "best_skill": best_skill,
        "history": history,
        "best_score": best_score,
        "test_report": test_report,
    }

    with open(os.path.join(output_dir, "best_skill.md"), "w") as f:
        f.write(best_skill)
    with open(os.path.join(output_dir, "history.json"), "w") as f:
        json.dump(history, f, indent=2)
    if test_report:
        with open(os.path.join(output_dir, "test_report.json"), "w") as f:
            json.dump(test_report, f, indent=2, ensure_ascii=False)
    cache.save()

    logger.info("[M4] SkillOpt 完成: %d steps, best_score=%.3f", step_counter, best_score)
    return final
